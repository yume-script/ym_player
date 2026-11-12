"""공중파 라디오 소스 - 한국 지상파·지역 라디오를 지역별 채널로.

채널 목록과 방송 주소는 넣어 두지 않는다. 공개 저장소 BSofDeath/radio.bsod.kr (https://github.com/BSofDeath/radio.bsod.kr)에서
그때그때 받아 읽는다 (하루 보관, 못 받으면 이전 것). 저장소가 채널을 더하거나 빼거나 주소를 고치면 다음 날까지는 따라가고,
관리자는 방송국 화면의 [채널 새로 받기]로 바로 다시 받는다 (기억해 둔 방송 주소도 같이 버린다):
  index.html                     지역(수도권·부산…)과 그 지역의 채널 (stn / ch / city)
  functions/api/template/*.js    방송사별 주소 표 - 고정 주소(HLS .m3u8 등) 또는 방송사에 물어 주소를 받는 곳
주소가 접속할 때마다 바뀌는 방송(KBS·MBC·SBS 등 - 토큰이 붙고 시간이 지나면 못 씀)은 그 저장소 README가 적어 둔 대로
이 서버가 방송사에 직접 물어 받는다 (URL_TTL 동안 기억). radio.bsod.kr 사이트 자체는 거치지 않는다.
  KBS       https://cfpwwwapi.kbs.co.kr/api/v1/landing/live/channel_code/<코드>  → channel_item[0].service_url
  MBC·SBS   방송사 주소를 열면 본문이 곧 방송 주소
  부산MBC   오늘 날짜(yymmdd)를 글자로 바꾼 값이 주소에 들어간다
  그 밖     본문(글·JSON·HTML)에서 방송 주소를 찾는다
"""
import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

from aiohttp import ClientTimeout

from . import config_store

REPO = "BSofDeath/radio.bsod.kr"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/"
TEMPLATES = ("kbs", "mbc", "sbs", "ebs", "tbs", "tbn", "cbs", "febc", "bbs", "cpbc", "wbs", "kugak", "afn", "community", "common")
LIST_TTL = 24 * 3600
RETRY_TTL = 600            # 목록을 못 받았으면 이만큼 뒤에 다시
URL_TTL = 20 * 60          # 방송사에 물어 받은 주소를 기억하는 시간
KBS_API = "https://cfpwwwapi.kbs.co.kr/api/v1/landing/live/channel_code/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_STREAM_RE = re.compile(r"https?://[^\s'\"<>\\)]+")
_DATECODE = str.maketrans("0123456789", "qhsagbjcpd")
_KST = timezone(timedelta(hours=9))


# ───────── 저장소 읽기 ─────────
def parse_index(html):
    """index.html → (지역 [(id, 이름)], 채널 [{"region", "name", "stn", "ch", "city"}])"""
    regions = re.findall(r'<option\s+value="([\w-]+)"\s*>([^<]+)</option>', html)
    labels = dict(regions)
    channels = []
    for block in re.finditer(r'<div\s+id="([\w-]+)"\s+class="channelContainer[^"]*"\s*>(.*?)</div>', html, re.S):
        region = block.group(1)
        if region not in labels:
            continue
        for args, name in re.findall(r"changeSource\(\{([^}]*)\}\)\"\s*>([^<]+)</a>", block.group(2)):
            got = dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", args))
            if got.get("stn"):
                channels.append({"region": region, "name": " ".join(name.split()), "stn": got["stn"], "ch": got.get("ch"), "city": got.get("city")})
    return [(r, " ".join(n.split())) for r, n in regions], channels


def _strip_comments(js):
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return "\n".join(line for line in js.split("\n") if not line.lstrip().startswith("//"))


def _field(body, key):
    m = re.search(r"\b%s\s*:\s*(?:\"([^\"]*)\"|'([^']*)'|null)" % key, body)
    return (m.group(1) or m.group(2)) if m and (m.group(1) or m.group(2)) else None


def parse_template(js):
    """방송사 주소 표(JS) → [{"name", "station", "channel", "city", "url" 또는 "how": {...}}]"""
    js = _strip_comments(js)
    marks = [m.start() for m in re.finditer(r"\{\s*name\s*:\s*\"", js)]
    out = []
    for i, at in enumerate(marks):
        body = js[at:marks[i + 1] if i + 1 < len(marks) else len(js)]
        if "streamUrl" not in body:
            continue
        head, tail = body.split("streamUrl", 1)
        entry = {"name": _field(head, "name") or ""}
        for key in ("station", "channel", "city"):   # 적혀 있는 것만 (null로 적힌 것과 아예 없는 것은 다르다 - 저장소의 찾는 순서와 같게)
            if re.search(r"\b%s\s*:" % key, head):
                entry[key] = _field(head, key)
        static = re.match(r"\s*:\s*\"([^\"]+)\"", tail)
        if static:
            entry["url"] = static.group(1)
        else:
            how = _how(tail)
            if not how:
                continue
            entry["how"] = how
        out.append(entry)
    return out


def _how(code):
    """주소를 만드는 함수 본문 → 어떻게 받을지 (모르는 모양이면 None)"""
    urls = re.findall(r"[\"'`](https?://[^\"'`\s$]+)[\"'`]", code)
    if "datecode" in code.lower():   # 날짜 글자가 들어가는 주소 (부산MBC)
        m = re.search(r"\"(https?://[^\"]+)\"\s*\+[^\"]+\+\s*\"([^\"]+)\"", code)
        return {"kind": "datecode", "prefix": m.group(1), "suffix": m.group(2)} if m else None
    if "<ip>" in code:               # 서버 목록에서 주소를 골라 만드는 곳 (AFN)
        api = re.search(r"fetch\(\s*\"(https?://[^\"]+)\"", code)
        tpl = re.search(r"`https://\$\{[^}]*?\"([\w.-]+)\"\s*\}(/[^`]+)`", code)
        return {"kind": "ip", "api": api.group(1), "host": tpl.group(1), "path": tpl.group(2)} if api and tpl else None
    direct = re.search(r"fetch\(\s*\"(https?://[^\"]+)\"", code)
    if direct:                       # 방송사에 물어 받는 곳. 못 받으면 뒤에 적힌 고정 주소
        rest = [u for u in urls if u != direct.group(1)]
        return {"kind": "ask", "api": direct.group(1), "fallback": rest[-1] if rest else ""}
    if "fetch(" in code and urls:    # 물어볼 주소를 고르는 곳 (보이는 라디오 ? A : B) - 뒤의 것이 보통 라디오
        return {"kind": "ask", "api": urls[-1], "fallback": ""}
    if urls:                         # 고정 주소 둘 중 하나 - 뒤의 것이 보통 라디오
        return {"kind": "fixed", "url": urls[-1]}
    return None


def parse_kbs(js):
    """kbs.js의 채널·지역 코드 표 → 주소 표 (KBS는 전부 방송사에 물어 받는다)"""
    rows = re.findall(r"\{\s*name:\s*\"([^\"]+)\",\s*code:\s*\"([\w]+)\",\s*param:\s*\"(\w+)\"\s*\}", _strip_comments(js))
    chans = [(n, c, p) for n, c, p in rows if not c.endswith("_")]
    cities = [(n, c, p) for n, c, p in rows if c.endswith("_")]
    proxy = re.search(r"`(https?://[^`$]+)\$\{", js)
    alt = proxy.group(1) if proxy else ""
    out = []
    for cname, ccode, cparam in chans:
        out.append({"name": f"KBS {cname}", "channel": cparam, "city": None,
                    "how": {"kind": "ask", "api": KBS_API + ccode, "alt": alt + ccode if alt else "", "fallback": ""}})
        for yname, ycode, yparam in cities:
            out.append({"name": f"{yname} {cname}", "channel": cparam, "city": yparam,
                        "how": {"kind": "ask", "api": KBS_API + ycode + ccode, "alt": alt + ycode + ccode if alt else "", "fallback": ""}})
    return out


def find_entry(tables, stn, ch, city):
    """저장소의 getUrl과 같은 순서로 찾는다"""
    urls = tables.get(stn) or tables.get("common") or []
    def has(e, key, value):
        return key in e and e[key] == value
    tests = (lambda e: has(e, "station", stn),
             lambda e: has(e, "channel", ch) and has(e, "city", city),
             lambda e: has(e, "channel", ch) and has(e, "city", None),
             lambda e: has(e, "channel", ch),
             lambda e: has(e, "city", city))
    for test in tests:
        for e in urls:
            if test(e):
                return e
    return None


def build(index_html, templates):
    """받은 파일들 → {"regions": [...], "stations": [{id, key, name, region, group, url 또는 how}]}"""
    regions, channels = parse_index(index_html)
    tables = {}
    for stn, js in templates.items():
        tables[stn] = parse_kbs(js) if stn == "kbs" else parse_template(js)
    labels = dict(regions)
    stations, missing = [], []
    for c in channels:
        e = find_entry(tables, c["stn"], c["ch"], c["city"])
        if not e:
            missing.append(c["name"])
            continue
        key = ".".join(x or "-" for x in (c["stn"], c["ch"], c["city"]))
        st = {"id": f"{c['region']}.{key}", "key": key, "name": c["name"], "region": c["region"], "group": labels[c["region"]]}
        if e.get("url"):
            st["url"] = e["url"]
        else:
            st["how"] = e["how"]
        stations.append(st)
    return {"regions": [{"id": r, "name": n} for r, n in regions], "stations": stations, "missing": missing}


# ───────── 받아 두기 ─────────
def _cache_path():
    return os.path.join(config_store.DATA_DIR, "kradio_cache.json")


def _load_cache():
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(data):
    try:
        os.makedirs(config_store.DATA_DIR, exist_ok=True)
        tmp = _cache_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _cache_path())
    except OSError:
        pass


class Directory:
    """채널 목록(하루 보관)과 방송 주소(잠깐 보관)"""

    def __init__(self, base=RAW):
        self.base = base
        self.data = None
        self.error = ""
        self.tried = 0.0
        self.lock = asyncio.Lock()
        self.urls = {}    # key -> (주소, 만료 시각)
        self.changed = None   # 마지막으로 새로 받았을 때 달라진 채널 {"added": [이름], "removed": [이름]}

    async def _text(self, http, url):
        async with http.get(url, headers={"User-Agent": UA}, timeout=ClientTimeout(total=20)) as r:
            if r.status >= 400:
                raise RuntimeError(f"HTTP {r.status} ({url.rsplit('/', 1)[-1]})")
            return (await r.read()).decode("utf-8", "replace")

    async def stations(self, http, force=False):
        """{"regions", "stations", "at", "error"} - 못 받으면 이전 목록 (그것도 없으면 빈 목록 + error)"""
        async with self.lock:
            now = time.time()
            if self.data is None:
                self.data = await asyncio.to_thread(_load_cache) or None
            fresh = self.data and now - self.data.get("at", 0) < LIST_TTL
            if (fresh and not force) or (not force and now - self.tried < RETRY_TTL):
                return self._public()
            self.tried = now
            before = {x["key"]: x["name"] for x in (self.data or {}).get("stations") or []}
            self.changed = None
            try:
                names = ["index.html"] + [f"functions/api/template/{t}.js" for t in TEMPLATES]
                texts = await asyncio.gather(*(self._text(http, self.base + n) for n in names))
                built = build(texts[0], dict(zip(TEMPLATES, texts[1:])))
                if not built["stations"]:
                    raise RuntimeError("저장소에서 채널을 하나도 읽지 못했습니다 (형식이 바뀐 듯)")
                self.data = dict(built, at=now)
                self.error = ""
                self.urls.clear()   # 주소 표가 바뀌었을 수 있다 - 기억해 둔 방송 주소도 버린다
                after = {x["key"]: x["name"] for x in built["stations"]}
                self.changed = {"added": sorted(after[k] for k in after.keys() - before.keys())[:30],
                                "removed": sorted(before[k] for k in before.keys() - after.keys())[:30]} if before else None
                await asyncio.to_thread(_save_cache, self.data)
            except Exception as e:  # noqa: BLE001
                self.error = f"채널 목록을 받지 못했습니다: {e}"
            return self._public()

    def _public(self):
        d = self.data or {}
        return {"regions": d.get("regions") or [], "at": d.get("at", 0), "error": self.error, "changed": self.changed,
                "stations": [{"id": s["id"], "name": s["name"], "region": s["region"], "group": s["group"], "hls": is_hls(s)}
                             for s in d.get("stations") or []]}

    def station(self, sid):
        return next((s for s in (self.data or {}).get("stations") or [] if s["id"] == sid), None)

    async def resolve(self, http, sid, force=False):
        """방송 주소. 모르는 채널이면 KeyError, 못 받으면 RuntimeError"""
        st = self.station(sid)
        if not st:
            raise KeyError(sid)
        if st.get("url"):
            return st["url"]
        how = st["how"]
        if how["kind"] == "fixed":
            return how["url"]
        if how["kind"] == "datecode":
            return how["prefix"] + datetime.now(_KST).strftime("%y%m%d").translate(_DATECODE) + how["suffix"]
        hit = self.urls.get(st["key"])
        if hit and hit[1] > time.time() and not force:
            return hit[0]
        url = ""
        for api in (how["api"], how.get("alt") or ""):
            if not api or url:
                continue
            try:
                body = await self._text(http, api)
                url = (f"https://{pick_ip(body) or how['host']}{how['path']}" if how["kind"] == "ip" else pick_stream(body))
            except Exception:  # noqa: BLE001
                url = ""
        url = url or how.get("fallback") or (f"https://{how['host']}{how['path']}" if how["kind"] == "ip" else "")
        if not url:
            raise RuntimeError("방송사에서 방송 주소를 받지 못했습니다.")
        self.urls[st["key"]] = (url, time.time() + URL_TTL)
        return url


def is_hls(st):
    """HLS(.m3u8)로 나오는 채널인지 (방송사에 물어 받는 곳은 HLS, 서버를 골라 붙는 곳은 아님)"""
    how = st.get("how") or {}
    url = st.get("url") or how.get("url") or how.get("suffix") or ""
    return ".m3u8" in url.lower() if url else how.get("kind") == "ask"


def pick_ip(xml):
    ips = re.findall(r"<ip>(.*?)</ip>", xml)
    return ips[-1].strip() if ips else ""


def pick_stream(body):
    """방송사 응답(주소 한 줄 / JSON / HTML·JSONP)에서 방송 주소"""
    text = body.strip()
    if re.fullmatch(r"https?://\S+", text):
        return text
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if data is not None:
        found = []

        def walk(x):
            if isinstance(x, dict):
                for k in ("service_url", "mediaurl"):
                    if isinstance(x.get(k), str) and x[k].startswith("http"):
                        found.append(x[k])
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
            elif isinstance(x, str) and x.startswith("http") and ".m3u8" in x:
                found.append(x)
        walk(data)
        return found[0] if found else ""
    hits = [u for u in _STREAM_RE.findall(text) if ".m3u8" in u]
    return hits[0] if hits else ""
