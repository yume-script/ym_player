"""IPTV 쪽 순수 함수 모음 - M3U 재생목록, XMLTV 편성표, HLS 재생목록 주소 바꾸기.

네트워크나 aiohttp에 기대지 않아서 테스트에서 그대로 부를 수 있다.
북오아시스 M3U 플레이어 플러그인(script.js)의 parseM3U/parseXMLTV를 서버 쪽으로 옮긴 것이다.
편성표를 서버에서 미리 풀어두면 수십 MB짜리 XML을 브라우저가 통째로 받지 않아도 된다.
"""
import gzip
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urljoin

_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
_XMLTV_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})?(?:\s*([+-])(\d{2}):?(\d{2}))?")
_URI_ATTR_RE = re.compile(r'URI="([^"]+)"')

# 오프셋이 안 적힌 XMLTV 시간은 한국 방송 기준(KST)으로 본다 - 원래 플러그인과 같은 규칙
DEFAULT_TZ = timezone(timedelta(hours=9))


def normalize_name(name):
    """채널명 비교용: 공백을 다 빼고 소문자로 ("MBC ", "m b c" -> "mbc")."""
    return re.sub(r"\s+", "", str(name or "")).lower()


def maybe_gunzip(data: bytes) -> bytes:
    """.xml.gz 편성표가 흔해서, gzip 머리(1f 8b)가 보이면 풀어준다."""
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    return data


def decode_text(data: bytes) -> str:
    data = maybe_gunzip(data)
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _clean_logo(value):
    """tvg-logo="None"(파이썬 None을 그대로 찍은 재생목록) 같은 값이나 상대 주소는 버린다.
    그대로 두면 브라우저가 이 앱 기준 주소(/None)로 받으려 해서 404만 쌓인다."""
    value = (value or "").strip()
    if value.lower() in ("none", "null", "undefined", "nan"):
        return ""
    return value if value.startswith(("http://", "https://", "data:image/")) else ""


def parse_m3u(text, source_name="IPTV", source_id=""):
    """#EXTINF 줄과 바로 다음 주소 줄을 묶어 채널 목록으로 만든다.

    tvg-id가 없으면 '소스_채널명'으로 임시 id를 만든다(EPG는 이름으로 다시 찾는다).
    #EXTVLCOPT의 user-agent/referrer는 프록시가 그대로 실어 보낼 수 있게 남긴다.
    """
    channels = []
    cur = None
    opts = {}
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            attrs = dict((k.lower(), v) for k, v in _ATTR_RE.findall(line))
            name = line.rsplit(",", 1)[1].strip() if "," in line else ""
            name = name or attrs.get("tvg-name") or "이름 없는 채널"
            cur = {
                "tvg_id": attrs.get("tvg-id", "").strip(),
                "name": name,
                "logo": _clean_logo(attrs.get("tvg-logo", "")),
                "group": attrs.get("group-title", "") or "기타",
                "chno": attrs.get("tvg-chno", ""),
            }
            opts = {}
        elif line.startswith("#EXTVLCOPT:") and cur is not None:
            key, _, val = line[len("#EXTVLCOPT:"):].partition("=")
            key = key.strip().lower()
            if key == "http-user-agent":
                opts["ua"] = val.strip()
            elif key == "http-referrer":
                opts["referer"] = val.strip()
        elif not line.startswith("#") and cur is not None:
            cur["url"] = line
            cur.update(opts)
            cur["source"] = source_name
            cur["source_id"] = source_id
            cur["id"] = f"{source_id}:{cur['tvg_id'] or cur['name']}"
            channels.append(cur)
            cur = None
            opts = {}
    return channels


def merge_channels(lists):
    """여러 M3U에서 읽은 채널 목록을 하나로. 앞 목록이 우선이다.

    뒤 목록의 채널이 앞 목록에 이미 있으면(같은 tvg-id, 공백·대소문자 빼고 같은 이름, 또는 같은 스트림 주소)
    따로 늘어놓지 않고 그 주소를 alts(예비 주소)로 붙여, 첫 주소가 안 나오면 재생기가 차례로 시도하게 한다.
    한 목록 안의 채널끼리는 합치지 않는다 (목록 하나만 넣으면 예전과 똑같이 보이게).
    """
    merged, by_url, by_tvg, by_name = [], {}, {}, {}
    for channels in lists:
        added = []
        for ch in channels:
            url = ch.get("url", "")
            tvg = ch.get("tvg_id", "").strip().lower()
            name = normalize_name(ch.get("name"))
            hit = (tvg and by_tvg.get(tvg)) or (name and by_name.get(name)) or by_url.get(url)
            if hit is None:
                hit = dict(ch, alts=[])
                merged.append(hit)
                added.append(hit)
                continue
            if url != hit["url"] and all(url != a["url"] for a in hit["alts"]):
                hit["alts"].append({k: ch[k] for k in ("url", "ua", "referer") if ch.get(k)})
            if not hit.get("logo") and ch.get("logo"):
                hit["logo"] = ch["logo"]
            if not hit.get("tvg_id") and ch.get("tvg_id"):
                hit["tvg_id"] = ch["tvg_id"]  # 편성표 매칭용 (id는 그대로 둔다 - 즐겨찾기가 안 깨지게)
        for hit in added:  # 다음 목록부터 찾을 수 있게
            by_url.setdefault(hit["url"], hit)
            if hit["tvg_id"]:
                by_tvg.setdefault(hit["tvg_id"].strip().lower(), hit)
            by_name.setdefault(normalize_name(hit["name"]), hit)
    return merged


def merge_epg(parsed_list):
    """parse_xmltv 결과 여러 개를 하나로. 같은 채널이 여러 편성표에 있으면 앞 편성표 것을 쓴다."""
    out = {"names": {}, "icons": {}, "programmes": {}}
    for parsed in parsed_list:
        for key in ("names", "icons", "programmes"):
            for k, v in (parsed.get(key) or {}).items():
                out[key].setdefault(k, v)
    return out


def parse_xmltv_date(value):
    """'20261014093000 +0900' -> 유닉스 초. 오프셋이 없으면 KST로 본다. 못 읽으면 None."""
    m = _XMLTV_DATE_RE.match(str(value or "").strip())
    if not m:
        return None
    y, mo, d, h, mi, s, sign, oh, om = m.groups()
    if sign:
        mins = int(oh) * 60 + int(om)
        tz = timezone(timedelta(minutes=-mins if sign == "-" else mins))
    else:
        tz = DEFAULT_TZ
    dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), tzinfo=tz)
    return int(dt.timestamp())


def _text(el, tag):
    child = el.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def parse_xmltv(data, window_start=None, window_end=None):
    """XMLTV를 {'names': {정규화이름: 채널id}, 'programmes': {채널id: [[시작, 끝, 제목, 설명, 분류]]}}로.

    window_start/window_end(유닉스 초)를 주면 그 사이에 걸친 방송만 남긴다(응답 크기 줄이기).
    iterparse로 한 요소씩 읽고 바로 버려서 큰 파일도 메모리를 많이 안 쓴다.
    """
    import io

    if isinstance(data, str):
        data = data.encode("utf-8")
    data = maybe_gunzip(data)
    names = {}
    icons = {}
    programmes = {}
    try:
        for _, el in ET.iterparse(io.BytesIO(data), events=("end",)):
            if el.tag == "channel":
                cid = el.get("id")
                if cid:
                    for dn in el.findall("display-name"):
                        key = normalize_name(dn.text)
                        if key and key not in names:
                            names[key] = cid
                    icon = el.find("icon")
                    if icon is not None and icon.get("src"):
                        icons[cid] = icon.get("src")
                el.clear()
            elif el.tag == "programme":
                cid = el.get("channel")
                start = parse_xmltv_date(el.get("start"))
                stop = parse_xmltv_date(el.get("stop")) if el.get("stop") else None
                if cid and start is not None:
                    end = stop or start + 3600
                    if (window_end is None or start < window_end) and (window_start is None or end > window_start):
                        programmes.setdefault(cid, []).append([
                            start, stop, _text(el, "title") or "제목 없음",
                            _text(el, "desc")[:500], _text(el, "category"),
                        ])
                el.clear()
    except ET.ParseError as e:
        if not programmes:
            raise ValueError(f"XMLTV 파싱 실패 (응답이 잘렸거나 형식이 다릅니다): {e}") from e
    for lst in programmes.values():
        lst.sort(key=lambda p: p[0])
    return {"names": names, "icons": icons, "programmes": programmes}


def rewrite_m3u8(text, base_url, make_url):
    """HLS 재생목록 안의 주소(세그먼트/하위 목록/키)를 make_url(절대주소)로 바꾼다.

    상대 주소는 base_url 기준으로 먼저 절대주소로 만든다. make_url은 보통 서버의
    /api/stream?url=... 프록시 주소를 돌려준다.
    """
    out = []
    for line in str(text).splitlines():
        s = line.strip()
        if not s:
            out.append(line)
        elif s.startswith("#"):
            if 'URI="' in s:
                line = _URI_ATTR_RE.sub(lambda m: f'URI="{make_url(urljoin(base_url, m.group(1)))}"', line)
            out.append(line)
        else:
            out.append(make_url(urljoin(base_url, s)))
    return "\n".join(out) + "\n"


def looks_like_m3u8(content_type, url, head: bytes):
    ct = (content_type or "").lower()
    if "mpegurl" in ct:
        return True
    path = url.split("?", 1)[0].lower()
    if path.endswith(".m3u8"):
        return True
    return head.lstrip()[:7] == b"#EXTM3U"


def looks_like_mpd(content_type, url, head: bytes):
    """MPEG-DASH 매니페스트(.mpd)일 것 같으면 True (끝까지 읽어서 <MPD가 있는지 다시 본다)."""
    if "dash+xml" in (content_type or "").lower():
        return True
    if url.split("?", 1)[0].lower().endswith(".mpd"):
        return True
    return head.lstrip()[:4] in (b"<MPD", b"<?xm")


_MPD_OPEN_RE = re.compile(r"<MPD\b[^>]*>", re.S)
_BASEURL_RE = re.compile(r"(<BaseURL\b[^>]*>)(.*?)(</BaseURL>)", re.S)


def rewrite_mpd(text, base_url):
    """중계로 받은 MPD의 기준 주소(BaseURL)를 원본 서버의 절대 주소로.

    재생기는 매니페스트를 /api/stream?url=... 로 받기 때문에 조각 주소(상대 주소)를 그 기준으로 풀면 틀린다.
    MPD 바로 아래 BaseURL을 원본 기준 절대 주소로 바꾸고(없으면 넣고), 그 아래 단계의 상대 BaseURL은 그대로 둔다.
    재생기(dash.js)는 이렇게 나온 원본 절대 주소를 다시 중계 주소로 감싸서 요청한다.
    """
    m = _MPD_OPEN_RE.search(text)
    if not m:
        return text
    first_period = text.find("<Period", m.end())
    top = _BASEURL_RE.search(text, m.end(), first_period if first_period >= 0 else len(text))
    if top:
        absolute = urljoin(base_url, top.group(2).strip())
        return text[:top.start(2)] + _xml_escape(absolute) + text[top.end(2):]
    return text[:m.end()] + f"<BaseURL>{_xml_escape(base_url)}</BaseURL>" + text[m.end():]


def _xml_escape(value):
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def stream_proxy_url(target, ua="", referer=""):
    url = "/api/stream?url=" + quote(target, safe="")
    if ua:
        url += "&ua=" + quote(ua, safe="")
    if referer:
        url += "&ref=" + quote(referer, safe="")
    return url
