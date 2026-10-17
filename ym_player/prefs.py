"""사용자마다 따로 두는 작은 설정 (data/prefs.json) - IPTV 즐겨찾기 채널, 화면 테마.

{"사용자명(소문자)": {"fav_channels": ["채널id", ...], "theme": "light", "accent": "blue", "seen_version": "1.1.2"}}
theme/accent가 ""(또는 없음)이면 관리자가 정한 기본값을 따른다.
브라우저(localStorage)에만 두면 기기마다 달라서, 로그인한 사람 기준으로 서버에 둔다.
"""
import json
import os
import re
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


# 화면 테마와 강조색 (web/css/app.css의 :root[data-theme=...] / [data-accent=...]와 같은 이름)
THEMES = ("dark", "light", "black", "navy", "auto")
ACCENTS = ("amber", "blue", "green", "purple", "red", "pink", "teal")


def _choice(options):
    def check(value):
        value = str(value or "")
        if value and value not in options:
            raise ValueError(f"알 수 없는 값: {value}")
        return value
    return check


def _version(value):
    """마지막으로 "새로워진 점"을 본 버전 ("1.1.2" 꼴, 없으면 "")"""
    value = str(value or "")
    if value and not re.fullmatch(r"\d{1,4}(\.\d{1,4}){0,3}", value):
        raise ValueError(f"버전 형식이 아닙니다: {value}")
    return value


def _iso_time(value):
    """텔레그램 "새로 올라온 영상"을 마지막으로 본 시각 (ISO, 영상 date와 같은 꼴)"""
    value = str(value or "")
    if value and (len(value) > 40 or not re.fullmatch(r"\d{4}-\d\d-\d\dT[0-9:.+\-Z]*", value)):
        raise ValueError(f"시각 형식이 아닙니다: {value}")
    return value


# 받아 주는 키와 검사 함수
KEYS = {"fav_channels": _str_list, "theme": _choice(THEMES), "accent": _choice(ACCENTS), "seen_version": _version, "tg_seen": _iso_time}


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
            mine = out[str(user).lower()] = {}
            for k, v in items.items():
                try:
                    if k in KEYS:
                        mine[k] = KEYS[k](v)
                except ValueError:
                    pass  # 다른 버전에서 만든 값 등은 버린다
    with _lock:
        _save_all(out)
