"""사용자 계정과 로그인 세션.

- 사용자: data/users.json  [{"username", "role": "admin"|"user", "salt", "hash", "iterations", "pw_ver"}]
  비밀번호는 PBKDF2-SHA256(20만 번)으로만 저장한다.
- 세션: 서명한 쿠키 하나 (ymp_session). 내용 = 사용자명|만료시각|pw_ver, 서명 = HMAC-SHA256(data/secret.key).
  비밀번호를 바꾸거나 사용자를 지우면 pw_ver가 바뀌거나 사용자가 없어져서 그 사람의 기존 쿠키는 바로 무효.
- 처음 실행: 사용자가 하나도 없으면
    * MP_USER/MP_PASSWORD 환경변수가 있으면 그걸로 관리자를 만든다 (예전 기본 인증 설정을 그대로 옮김)
    * 없으면 로그인 화면이 "관리자 계정 만들기"로 바뀐다
- 로그인 실패가 IP마다 10분에 10번을 넘으면 잠시 막는다.
- 볼 수 있는 범위(access): None이면 전부. {"sources": [소스id...], "libraries": {소스id: [key...]}}
  key는 Plex면 라이브러리 섹션 key, 텔레그램이면 채널 id. libraries에 없는 소스는 그 소스의 전부.
  관리자는 항상 전부.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

from . import config_store

COOKIE = "ymp_session"
SESSION_SEC = 12 * 3600            # "로그인 유지"를 안 켰을 때 (브라우저를 닫아도 서버 쪽 만료는 12시간)
REMEMBER_SEC = 30 * 24 * 3600      # "로그인 유지"
ITERATIONS = 200_000
ROLES = ("admin", "user")
FAIL_WINDOW_SEC = 600
FAIL_LIMIT = 10
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@가-힣-]{2,32}$")

_lock = threading.Lock()
_fails = {}  # ip -> [실패 시각...]


class AuthError(Exception):
    """화면에 그대로 보여줄 수 있는 오류."""


def _path(name):
    return os.path.join(config_store.DATA_DIR, name)


def _write_private(path, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def secret_key():
    path = _path("secret.key")
    with _lock:
        try:
            with open(path, "rb") as f:
                key = f.read()
            if len(key) >= 32:
                return key
        except FileNotFoundError:
            pass
        key = secrets.token_bytes(32)
        _write_private(path, key)
        return key


# ---------------------------------------------------------------------------
# 사용자 저장소
# ---------------------------------------------------------------------------
def load_users():
    try:
        with open(_path("users.json"), "r", encoding="utf-8") as f:
            users = json.load(f)
        return users if isinstance(users, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def save_users(users):
    with _lock:
        _write_private(_path("users.json"), json.dumps(users, ensure_ascii=False, indent=2).encode("utf-8"))


def find_user(username):
    username = str(username or "").strip().lower()
    return next((u for u in load_users() if u["username"].lower() == username), None)


def public_user(u):
    return {"username": u["username"], "role": u["role"], "access": u.get("access")}


def clean_access(raw):
    """화면에서 온 access를 믿지 않고 정리. None/빈 값이면 전부 허용."""
    if not isinstance(raw, dict):
        return None
    sources = raw.get("sources")
    if not isinstance(sources, list):
        return None
    libs = raw.get("libraries") if isinstance(raw.get("libraries"), dict) else {}
    out_sources = [str(s) for s in sources if str(s).strip()]
    out_libs = {str(k): sorted({str(x) for x in v}) for k, v in libs.items()
                if str(k) in out_sources and isinstance(v, list)}
    return {"sources": out_sources, "libraries": out_libs}


def can_source(user, sid):
    if not user or user.get("role") == "admin":
        return True
    access = user.get("access")
    return access is None or str(sid) in access.get("sources", [])


def allowed_libraries(user, sid):
    """None = 이 소스의 라이브러리 전부, set = 이 섹션 key들만."""
    if not user or user.get("role") == "admin":
        return None
    access = user.get("access") or {}
    libs = (access.get("libraries") or {}).get(str(sid))
    return None if libs is None else set(map(str, libs))


def _hash(password, salt, iterations=ITERATIONS):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def _check_password_rules(password):
    if len(password or "") < 6:
        raise AuthError("비밀번호는 6자 이상이어야 합니다.")


def _new_secret_fields(password):
    salt = secrets.token_bytes(16)
    return {"salt": salt.hex(), "hash": _hash(password, salt), "iterations": ITERATIONS,
            "pw_ver": secrets.token_hex(4)}


def create_user(username, password, role="user"):
    username = str(username or "").strip()
    if not USERNAME_RE.match(username):
        raise AuthError("아이디는 2~32자의 영문/숫자/한글과 _ . @ - 만 쓸 수 있습니다.")
    if role not in ROLES:
        raise AuthError("알 수 없는 권한입니다.")
    _check_password_rules(password)
    users = load_users()
    if any(u["username"].lower() == username.lower() for u in users):
        raise AuthError("이미 있는 아이디입니다.")
    user = {"username": username, "role": role, **_new_secret_fields(password), "created": int(time.time())}
    users.append(user)
    save_users(users)
    return public_user(user)


def update_user(username, password=None, role=None, acting=None, access=False):
    """비밀번호/권한 변경. 마지막 관리자를 일반 사용자로 내리는 건 막는다."""
    users = load_users()
    user = next((u for u in users if u["username"].lower() == str(username).lower()), None)
    if not user:
        raise AuthError("없는 사용자입니다.")
    if role is not None:
        if role not in ROLES:
            raise AuthError("알 수 없는 권한입니다.")
        if user["role"] == "admin" and role != "admin" and sum(u["role"] == "admin" for u in users) <= 1:
            raise AuthError("관리자가 한 명은 있어야 합니다.")
        user["role"] = role
    if password is not None:
        _check_password_rules(password)
        user.update(_new_secret_fields(password))  # pw_ver가 바뀌어 다른 기기의 기존 로그인은 끊긴다
    if access is not False:
        user["access"] = clean_access(access)
    save_users(users)
    return public_user(user)


def clean_backup_users(raw):
    """백업 파일의 사용자 목록 검사 (해시 그대로 옮김). 관리자가 한 명은 있어야 한다."""
    if not isinstance(raw, list) or not raw:
        raise AuthError("사용자 목록이 없습니다.")
    out, seen = [], set()
    for u in raw:
        if not isinstance(u, dict):
            raise AuthError("사용자 항목 모양이 틀렸습니다.")
        name = str(u.get("username") or "")
        if not USERNAME_RE.match(name) or name.lower() in seen:
            raise AuthError(f"아이디가 틀렸거나 겹칩니다: {name}")
        if u.get("role") not in ROLES:
            raise AuthError(f"{name}: 알 수 없는 권한")
        try:
            bytes.fromhex(str(u["salt"]))
            int(str(u["hash"]), 16)
            iterations = int(u.get("iterations") or ITERATIONS)
        except (KeyError, ValueError):
            raise AuthError(f"{name}: 비밀번호 해시가 없습니다.")
        seen.add(name.lower())
        out.append({"username": name, "role": u["role"], "salt": str(u["salt"]), "hash": str(u["hash"]),
                    "iterations": iterations, "pw_ver": str(u.get("pw_ver") or secrets.token_hex(4)),
                    "created": u.get("created"), "access": clean_access(u.get("access"))})
    if not any(u["role"] == "admin" for u in out):
        raise AuthError("관리자가 한 명은 있어야 합니다.")
    return out


def delete_user(username, acting=None):
    users = load_users()
    user = next((u for u in users if u["username"].lower() == str(username).lower()), None)
    if not user:
        raise AuthError("없는 사용자입니다.")
    if acting and acting.lower() == user["username"].lower():
        raise AuthError("자기 자신은 지울 수 없습니다.")
    if user["role"] == "admin" and sum(u["role"] == "admin" for u in users) <= 1:
        raise AuthError("관리자가 한 명은 있어야 합니다.")
    save_users([u for u in users if u is not user])


def verify_password(username, password):
    user = find_user(username)
    if not user:
        # 없는 아이디도 같은 시간이 걸리게 (아이디 존재 여부를 시간으로 알 수 없게)
        _hash(password or "", b"\0" * 16)
        return None
    expect = _hash(password or "", bytes.fromhex(user["salt"]), int(user.get("iterations") or ITERATIONS))
    return user if hmac.compare_digest(expect, user["hash"]) else None


def bootstrap_from_env():
    """사용자가 없고 MP_USER/MP_PASSWORD가 있으면 그 계정을 관리자로 만든다 (예전 기본 인증에서 옮겨오기)."""
    if load_users():
        return None
    user, pw = os.environ.get("MP_USER"), os.environ.get("MP_PASSWORD")
    if user and pw:
        try:
            return create_user(user, pw, "admin")
        except AuthError:
            return None
    return None


def needs_setup():
    return not load_users()


# ---------------------------------------------------------------------------
# 세션 쿠키
# ---------------------------------------------------------------------------
def _b64(data: bytes):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(user, remember=False):
    expires = int(time.time()) + (REMEMBER_SEC if remember else SESSION_SEC)
    body = f"{user['username']}|{expires}|{user['pw_ver']}".encode("utf-8")
    sig = hmac.new(secret_key(), body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(sig)}"


def user_from_token(token):
    """올바르고 만료 안 됐고 비밀번호가 바뀌지 않은 쿠키면 사용자, 아니면 None."""
    try:
        body_b64, sig_b64 = str(token or "").split(".", 1)
        body = _unb64(body_b64)
        if not hmac.compare_digest(hmac.new(secret_key(), body, hashlib.sha256).digest(), _unb64(sig_b64)):
            return None
        username, expires, pw_ver = body.decode("utf-8").split("|")
        if int(expires) < time.time():
            return None
    except (ValueError, UnicodeDecodeError):
        return None
    user = find_user(username)
    if not user or not hmac.compare_digest(user["pw_ver"], pw_ver):
        return None
    return user


# ---------------------------------------------------------------------------
# 로그인 실패 제한
# ---------------------------------------------------------------------------
def throttled(ip):
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW_SEC]
    _fails[ip] = recent
    return len(recent) >= FAIL_LIMIT


def record_failure(ip):
    _fails.setdefault(ip, []).append(time.time())


def clear_failures(ip):
    _fails.pop(ip, None)
