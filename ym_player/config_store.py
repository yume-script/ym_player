"""소스 설정 저장소 - ym_player/data/config.json (git에 안 올라감).

소스 하나는 이렇게 생겼다:
    {"id": "abc123", "type": "plex", "name": "우리집 Plex", "enabled": true,
     "url": "http://192.168.0.10:32400", "token": "...", "verify_ssl": true,
     "hidden_libraries": ["3"]}       <- 가져오지 않을 Plex 라이브러리(섹션 key). 새 라이브러리는 기본으로 보임
    {"id": "tg1", "type": "telegram", "name": "텔레그램", "enabled": true, "api_id": "123456",
     "api_hash": "...", "phone": "+8210...", "channels": [{"id": 1234567890, "title": "영화 채널", "kind": "video"}]}
    {"id": "def456", "type": "iptv", "name": "개인", "enabled": true,
     "m3u": "http://.../playlist.m3u", "epg": "http://.../epg.xml.gz"}
        m3u/epg에는 주소를 한 줄에 하나씩 여러 개 넣을 수 있다 (합쳐서 한 채널 목록/편성표로 보여 줌)

Plex 토큰은 브라우저로 내보내지 않는다(public_config가 has_token만 알려준다). 화면에서
저장할 때 토큰 칸을 비워 보내면 기존 토큰을 그대로 둔다.
"""
import json
import os
import secrets
import threading
import uuid

DATA_DIR = os.environ.get("MP_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")

SOURCE_TYPES = ("plex", "iptv", "telegram", "radio")
_SECRET_FIELDS = ("token", "api_hash", "tmdb_key")
_lock = threading.Lock()


def _default():
    return {"client_id": str(uuid.uuid4()), "sources": []}


def load():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        cfg = None
    if not isinstance(cfg, dict):
        cfg = _default()
        save(cfg)
    cfg.setdefault("client_id", str(uuid.uuid4()))
    cfg.setdefault("sources", [])
    return cfg


def save(cfg):
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_FILE)


def get_source(cfg, source_id):
    for s in cfg.get("sources", []):
        if s.get("id") == source_id:
            return s
    return None


def public_config(cfg):
    """브라우저로 내보낼 모양 - 비밀값은 빼고 has_token만."""
    out = []
    for s in cfg.get("sources", []):
        pub = {k: v for k, v in s.items() if k not in _SECRET_FIELDS}
        pub["has_token"] = bool(s.get("token"))
        pub["has_api_hash"] = bool(s.get("api_hash"))
        pub["has_tmdb_key"] = bool(s.get("tmdb_key"))
        out.append(pub)
    return {"client_id": cfg.get("client_id"), "sources": out}


def _clean_source(raw, old):
    stype = raw.get("type")
    if stype not in SOURCE_TYPES:
        raise ValueError(f"알 수 없는 소스 종류: {stype}")
    src = {
        "id": str(raw.get("id") or secrets.token_hex(4)),
        "type": stype,
        "name": str(raw.get("name") or {"plex": "Plex", "iptv": "IPTV", "telegram": "Telegram", "radio": "라디오"}[stype]).strip(),
        "enabled": bool(raw.get("enabled", True)),
    }
    if stype == "plex":
        src["url"] = str(raw.get("url") or "").strip().rstrip("/")
        if not src["url"]:
            raise ValueError(f"[{src['name']}] Plex 서버 주소가 비어 있습니다.")
        token = str(raw.get("token") or "").strip()
        src["token"] = token or (old or {}).get("token", "")
        src["verify_ssl"] = bool(raw.get("verify_ssl", True))
        hidden = raw.get("hidden_libraries") or []
        src["hidden_libraries"] = sorted({str(k) for k in hidden if str(k).strip()}) if isinstance(hidden, list) else []
    elif stype == "telegram":
        # api_hash는 Plex 토큰처럼 브라우저로 안 내보내고, 빈 칸으로 저장하면 기존 값 유지
        src["api_id"] = str(raw.get("api_id") or "").strip()
        api_hash = str(raw.get("api_hash") or "").strip()
        src["api_hash"] = api_hash or (old or {}).get("api_hash", "")
        # 작품 정보(TMDB) API 키 - 비밀값이라 브라우저로 안 내보내고, 빈 칸이면 기존 값 유지
        tmdb_key = str(raw.get("tmdb_key") or "").strip()[:400]
        if tmdb_key or (old or {}).get("tmdb_key"):
            src["tmdb_key"] = tmdb_key or (old or {}).get("tmdb_key", "")
        src["phone"] = str(raw.get("phone") or "").strip()
        channels = []
        for c in raw.get("channels") or []:
            if isinstance(c, dict) and str(c.get("id", "")).lstrip("-").isdigit():
                ch = {"id": int(c["id"]), "title": str(c.get("title") or c["id"])[:200]}
                if c.get("kind") in ("video", "image"):   # 영상만 / 사진만 (없으면 둘 다)
                    ch["kind"] = c["kind"]
                if c.get("ep_order") in ("asc", "desc"):  # 시리즈 회차 목록 순서 (없으면 1화부터)
                    ch["ep_order"] = c["ep_order"]
                channels.append(ch)
        src["channels"] = channels
    elif stype == "radio":
        stations = raw.get("stations")
        # 새로 추가하면서 비워 두면 기본 방송국
        src["stations"] = clean_stations(stations if stations else DEFAULT_STATIONS)
        if not src["stations"]:
            raise ValueError(f"[{src['name']}] 방송국이 하나도 없습니다.")
    else:
        src["m3u"] = "\n".join(url_list(raw.get("m3u")))
        src["epg"] = "\n".join(url_list(raw.get("epg")))
        if not src["m3u"]:
            raise ValueError(f"[{src['name']}] M3U 주소가 비어 있습니다.")
    return src


# 인터넷 라디오 기본 방송국 (방송국이 공개한 스트림 주소). 설정에서 바꾸거나 더할 수 있다.
DEFAULT_STATIONS = [
    {"name": "RelaxingJazz", "subtitle": "Smooth Jazz", "url": "https://443-1.autopo.st/171/stream/3/",
     "homepage": "https://relaxingjazz.com/"},
    {"name": "Radio Swiss Jazz", "subtitle": "Jazz · Blues · Soul", "url": "https://stream.srg-ssr.ch/srgssr/rsj/mp3/128",
     "homepage": "https://www.radioswissjazz.ch/"},
    {"name": "Jazz24", "subtitle": "Classic & Modern Jazz", "url": "https://knkx-live-a.edge.audiocdn.com/6285_128k",
     "homepage": "https://www.jazz24.org/"},
]


MAX_STATIONS = 500   # 한국 지역 라디오(194개)를 다 넣어도 넉넉하게


def clean_stations(value):
    """라디오 방송국 목록 검사. 이름과 http(s) 스트림 주소가 있어야 하고, id는 이름에서 만든다 (즐겨찾기가 안 깨지게)."""
    import re as _re
    out, seen = [], set()
    for st in value if isinstance(value, list) else []:
        if not isinstance(st, dict):
            continue
        name = str(st.get("name") or "").strip()[:80]
        url = str(st.get("url") or "").strip()
        if not name or not url.startswith(("http://", "https://")):
            continue
        sid = str(st.get("id") or "").strip() or (_re.sub(r"[\W_]+", "-", name.lower()).strip("-") or "station")
        base, n = sid[:40], 2
        while sid in seen:
            sid = f"{base}-{n}"
            n += 1
        seen.add(sid)
        home = str(st.get("homepage") or "").strip()
        item = {"id": sid, "name": name, "subtitle": str(st.get("subtitle") or "").strip()[:80], "url": url[:500],
                "homepage": home[:300] if home.startswith(("http://", "https://")) else ""}
        group = str(st.get("group") or "").strip()[:40]   # 분류 (지역 등) - 방송국 화면에서 골라 보기
        if group:
            item["group"] = group
        out.append(item)
    return out[:MAX_STATIONS]


def url_list(value):
    """M3U/EPG 칸 값 -> 주소 목록. 한 줄에 하나(목록으로 와도 됨), 빈 줄과 '#' 메모 줄과 같은 주소는 뺀다."""
    lines = value if isinstance(value, list) else str(value or "").splitlines()
    out = []
    for line in lines:
        line = str(line or "").strip()
        if line and not line.startswith("#") and line not in out:
            out.append(line)
    return out


def replace_sources(cfg, raw_sources):
    """화면에서 보낸 소스 목록으로 통째로 바꾼다. 토큰 칸이 비면 기존 토큰 유지."""
    if not isinstance(raw_sources, list):
        raise ValueError("sources는 목록이어야 합니다.")
    old_by_id = {s.get("id"): s for s in cfg.get("sources", [])}
    cfg["sources"] = [_clean_source(r, old_by_id.get(r.get("id"))) for r in raw_sources if isinstance(r, dict)]
    return cfg
