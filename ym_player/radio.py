"""인터넷 라디오 - 지금 나오는 곡 알아내기와 앨범 표지 찾기.

곡 정보: Icecast/SHOUTcast 스트림은 요청 머리에 `Icy-MetaData: 1`을 넣으면 응답 머리 `icy-metaint`(N)를 주고,
오디오 N바이트마다 [길이 1바이트(x16)] + "StreamTitle='가수 - 제목';" 꼴의 정보를 끼워 보낸다.
첫 정보 블록만 읽고 연결을 끊는다 (방송을 계속 받지 않음).

앨범 표지: iTunes 검색 API(https://itunes.apple.com/search)에서 가수·제목이 가장 잘 맞는 곡의 표지.
"""
import re
import unicodedata
from difflib import SequenceMatcher

ICY_MAX_METAINT = 256 * 1024   # 이보다 띄엄띄엄 정보를 주는 방송은 읽지 않는다 (너무 많이 받게 됨)
_TITLE_RE = re.compile(r"StreamTitle='(.*?)';", re.S)


def parse_icy_block(raw: bytes):
    """메타데이터 블록 -> "가수 - 제목" 문자열 (없으면 "")."""
    raw = raw.rstrip(b"\0")
    if not raw:
        return ""
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    m = _TITLE_RE.search(text)
    return (m.group(1) if m else "").strip()


def split_title(value):
    """"가수 - 제목" -> {"artist", "title", "text"}. 가수가 없으면 artist=""."""
    value = (value or "").strip()
    artist, sep, title = value.partition(" - ")
    if not sep:
        artist, title = "", value
    return {"artist": artist.strip(), "title": title.strip(), "text": value}


def looks_like_station_text(now, station):
    """곡이 아니라 방송국 이름/주소/광고 문구인지 (이럴 땐 표지를 찾지 않는다)."""
    text = (now.get("text") or "").lower()
    if not text:
        return True
    host = re.sub(r"^https?://(www\.)?", "", (station.get("homepage") or "").lower()).split("/")[0]
    if host and host in text:
        return True
    if text.startswith(("http://", "https://", "www.")):
        return True
    norm = lambda v: re.sub(r"[^0-9a-z]+", "", (v or "").lower())
    return bool(norm(station.get("name"))) and norm(now.get("text")) == norm(station.get("name"))


def _norm(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = re.sub(r"\(.*?\)|\[.*?\]", " ", value)          # (Live), [Remastered] 같은 꼬리
    return re.sub(r"[\W_]+", "", value)


def _similar(a, b):
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.85
    return SequenceMatcher(None, a, b).ratio()


def pick_artwork(artist, title, results):
    """iTunes 검색 결과에서 가장 잘 맞는 곡의 표지 주소 (600px). 충분히 맞는 게 없으면 ""."""
    want_t, want_a = _norm(title), _norm(artist)
    best, best_score = "", 0.0
    for r in results or []:
        art = r.get("artworkUrl100") or ""
        if not art:
            continue
        score = _similar(want_t, _norm(r.get("trackName"))) * 0.65
        score += (_similar(want_a, _norm(r.get("artistName"))) if want_a else 0.5) * 0.35
        if score > best_score:
            best, best_score = art, score
    if best_score < 0.6:
        return ""
    return re.sub(r"/\d+x\d+(bb)?\.(jpg|png)$", r"/600x600bb.\2", best)
