"""소스 설정 저장소 - ym_player/data/config.json (git에 안 올라감).

소스 하나는 이렇게 생겼다:
    {"id": "abc123", "type": "plex", "name": "우리집 Plex", "enabled": true,
     "url": "http://192.168.0.10:32400", "token": "...", "verify_ssl": true}
    {"id": "def456", "type": "iptv", "name": "개인", "enabled": true,
     "m3u": "http://.../playlist.m3u", "epg": "http://.../epg.xml.gz"}

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

SOURCE_TYPES = ("plex", "iptv")
_SECRET_FIELDS = ("token",)
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
        out.append(pub)
    return {"client_id": cfg.get("client_id"), "sources": out}


def _clean_source(raw, old):
    stype = raw.get("type")
    if stype not in SOURCE_TYPES:
        raise ValueError(f"알 수 없는 소스 종류: {stype}")
    src = {
        "id": str(raw.get("id") or secrets.token_hex(4)),
        "type": stype,
        "name": str(raw.get("name") or ("Plex" if stype == "plex" else "IPTV")).strip(),
        "enabled": bool(raw.get("enabled", True)),
    }
    if stype == "plex":
        src["url"] = str(raw.get("url") or "").strip().rstrip("/")
        if not src["url"]:
            raise ValueError(f"[{src['name']}] Plex 서버 주소가 비어 있습니다.")
        token = str(raw.get("token") or "").strip()
        src["token"] = token or (old or {}).get("token", "")
        src["verify_ssl"] = bool(raw.get("verify_ssl", True))
    else:
        src["m3u"] = str(raw.get("m3u") or "").strip()
        src["epg"] = str(raw.get("epg") or "").strip()
        if not src["m3u"]:
            raise ValueError(f"[{src['name']}] M3U 주소가 비어 있습니다.")
    return src


def replace_sources(cfg, raw_sources):
    """화면에서 보낸 소스 목록으로 통째로 바꾼다. 토큰 칸이 비면 기존 토큰 유지."""
    if not isinstance(raw_sources, list):
        raise ValueError("sources는 목록이어야 합니다.")
    old_by_id = {s.get("id"): s for s in cfg.get("sources", [])}
    cfg["sources"] = [_clean_source(r, old_by_id.get(r.get("id"))) for r in raw_sources if isinstance(r, dict)]
    return cfg
