"""사용자마다 따로 두는 작은 설정 (data/prefs.json) - 지금은 IPTV 즐겨찾기 채널.

{"사용자명(소문자)": {"fav_channels": ["채널id", ...]}}
브라우저(localStorage)에만 두면 기기마다 달라서, 로그인한 사람 기준으로 서버에 둔다.
"""
import json
import os
import threading

from . import config_store

_lock = threading.Lock()
MAX_ITEMS = 5000
MAX_LEN = 500


def _str_list(value):
    if not isinstance(value, list):
        raise ValueError("목록이어야 합니다.")
    out = []
    for v in value[:MAX_ITEMS]:
        v = str(v)
        if v and len(v) <= MAX_LEN and v not in out:
            out.append(v)
    return out


# 받아 주는 키와 검사 함수
KEYS = {"fav_channels": _str_list}


def _path():
    return os.path.join(config_store.DATA_DIR, "prefs.json")


def _load_all():
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_all(data):
    os.makedirs(config_store.DATA_DIR, exist_ok=True)
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, _path())


def get(user):
    return dict(_load_all().get(str(user).lower(), {}))


def update(user, changes):
    """알려진 키만 검사해서 덮어쓴다. 모르는 키는 ValueError."""
    if not isinstance(changes, dict):
        raise ValueError("JSON 객체여야 합니다.")
    clean = {}
    for k, v in changes.items():
        if k not in KEYS:
            raise ValueError(f"알 수 없는 항목: {k}")
        clean[k] = KEYS[k](v)
    with _lock:
        data = _load_all()
        mine = data.setdefault(str(user).lower(), {})
        mine.update(clean)
        _save_all(data)
        return dict(mine)


def forget(user):
    with _lock:
        data = _load_all()
        if data.pop(str(user).lower(), None) is not None:
            _save_all(data)


def load_all():
    return _load_all()


def replace_all(data):
    """백업 복원용. 사용자마다 알려진 키만 검사해서 남긴다."""
    out = {}
    for user, items in (data or {}).items():
        if isinstance(items, dict):
            out[str(user).lower()] = {k: KEYS[k](v) for k, v in items.items() if k in KEYS}
    with _lock:
        _save_all(out)
