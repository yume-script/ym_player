"""MCP(Model Context Protocol) 서버 - Claude 같은 AI 앱이 YMPlayer를 도구로 쓰게 한다.

주소: POST /mcp (헤더 Authorization: Bearer <토큰>) 또는 POST /mcp/<토큰> (헤더를 못 넣는 앱용).
방식은 MCP "Streamable HTTP"의 가장 단순한 꼴: 요청 하나에 JSON 응답 하나 (세션·SSE 없음).
토큰은 관리자가 설정 > MCP에서 사용자마다 만든다. 그 사용자가 볼 수 있는 소스·라이브러리만 보인다.
토큰 원문은 만들 때 한 번만 보여 주고 서버에는 SHA-256만 남긴다 (data/mcp_tokens.json, 권한 600).

여기는 토큰 저장소와 JSON-RPC 처리만. 도구가 실제로 하는 일은 server.py(_mcp_*)에 있다.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from . import __version__, config_store

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
TOKEN_PREFIX = "ymp_"
MAX_TOKENS = 50
_TOUCH_SEC = 600          # 마지막 사용 시각은 10분에 한 번만 파일에 적는다
_lock = threading.RLock()


class McpError(Exception):
    """JSON-RPC 오류 (code, message)"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# 토큰 저장소
# ---------------------------------------------------------------------------
def _path():
    return os.path.join(config_store.DATA_DIR, "mcp_tokens.json")


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load_tokens():
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save(tokens):
    os.makedirs(config_store.DATA_DIR, exist_ok=True)
    tmp = _path() + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _path())


def public_token(t):
    return {k: t.get(k) for k in ("id", "name", "user", "created", "last_used", "hint")}


def list_tokens():
    return [public_token(t) for t in load_tokens()]


def create_token(name, user):
    """(토큰 원문, 공개 정보). 원문은 이때만 알 수 있다."""
    name = str(name or "").strip()[:60] or "MCP"
    with _lock:
        tokens = load_tokens()
        if len(tokens) >= MAX_TOKENS:
            raise ValueError(f"토큰은 {MAX_TOKENS}개까지 만들 수 있습니다. 안 쓰는 것을 지워 주세요.")
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        rec = {"id": secrets.token_hex(6), "name": name, "user": str(user), "hash": _hash(raw),
               "hint": raw[-4:], "created": int(time.time()), "last_used": 0}
        tokens.append(rec)
        _save(tokens)
    return raw, public_token(rec)


def delete_token(tid):
    with _lock:
        tokens = load_tokens()
        left = [t for t in tokens if t.get("id") != tid]
        if len(left) == len(tokens):
            return False
        _save(left)
        return True


def forget_user(username):
    """사용자를 지우면 그 사람의 토큰도"""
    with _lock:
        tokens = load_tokens()
        left = [t for t in tokens if str(t.get("user", "")).lower() != str(username).lower()]
        if len(left) != len(tokens):
            _save(left)


def find_token(raw):
    """토큰 원문 -> 저장된 기록 (없으면 None). 마지막 사용 시각도 적는다."""
    if not raw or not raw.startswith(TOKEN_PREFIX) or len(raw) > 200:
        return None
    digest = _hash(raw)
    with _lock:
        tokens = load_tokens()
        for t in tokens:
            if hmac.compare_digest(str(t.get("hash", "")), digest):
                if time.time() - (t.get("last_used") or 0) > _TOUCH_SEC:
                    t["last_used"] = int(time.time())
                    _save(tokens)
                return t
    return None


# ---------------------------------------------------------------------------
# 도구 목록 (inputSchema는 JSON Schema)
# ---------------------------------------------------------------------------
def _schema(props=None, required=()):
    return {"type": "object", "properties": props or {}, "required": list(required), "additionalProperties": False}


_LIMIT = {"type": "integer", "minimum": 1, "maximum": 50, "description": "최대 개수 (기본 10)"}
_RO = {"readOnlyHint": True, "openWorldHint": False}

TOOLS = [
    {"name": "get_status", "title": "YMPlayer 상태",
     "description": "YMPlayer 서버 버전, 새 버전이 있는지, 이 토큰으로 볼 수 있는 소스(Plex/IPTV/텔레그램/라디오) 목록과 화면 주소를 알려 준다.",
     "inputSchema": _schema(), "annotations": _RO},
    {"name": "list_libraries", "title": "라이브러리 목록",
     "description": "소스마다 볼 수 있는 라이브러리: Plex 섹션, 텔레그램 채널(영상/사진/웹툰), 라디오 방송국 분류, IPTV.",
     "inputSchema": _schema(), "annotations": _RO},
    {"name": "search", "title": "찾기",
     "description": "제목으로 영화·드라마·에피소드(Plex), 텔레그램 영상·웹툰, 라디오 방송국을 한꺼번에 찾는다. 결과마다 YMPlayer에서 바로 열 수 있는 주소(url)가 있다.",
     "inputSchema": _schema({"query": {"type": "string", "description": "찾을 말 (제목 일부)"}, "limit": _LIMIT}, ["query"]),
     "annotations": _RO},
    {"name": "continue_watching", "title": "이어보기",
     "description": "보다 만 것들 (Plex 이어보기/다음 에피소드, 텔레그램 영상·웹툰). 토큰 주인의 기록 기준.",
     "inputSchema": _schema({"limit": _LIMIT}), "annotations": _RO},
    {"name": "recently_added", "title": "최근 추가",
     "description": "새로 들어온 것 (Plex 최근 추가, 텔레그램 채널의 새 영상·웹툰).",
     "inputSchema": _schema({"limit": _LIMIT, "source": {"type": "string", "description": "소스 id (없으면 전부)"}}),
     "annotations": _RO},
    {"name": "radio_now_playing", "title": "라디오 지금 나오는 곡",
     "description": "인터넷 라디오 방송국에서 지금 나오는 곡(가수 - 제목). query로 방송국 이름을 좁힐 수 있다.",
     "inputSchema": _schema({"query": {"type": "string", "description": "방송국 이름이나 분류 일부 (없으면 앞에서부터)"},
                             "limit": _LIMIT}), "annotations": {**_RO, "openWorldHint": True}},
]


# ---------------------------------------------------------------------------
# JSON-RPC
# ---------------------------------------------------------------------------
def _result(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


async def handle(msg, call_tool):
    """JSON-RPC 메시지 하나 -> 응답 dict (알림이면 None). call_tool(name, args) -> 결과 객체(JSON으로 보냄)"""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
    mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if "id" not in msg:          # 알림 (notifications/initialized 따위) - 답하지 않는다
        return None
    try:
        if method == "initialize":
            want = str(params.get("protocolVersion") or "")
            return _result(mid, {
                "protocolVersion": want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "ymplayer", "title": "YMPlayer", "version": __version__},
                "instructions": "YMPlayer(개인 미디어 서버: Plex, IPTV, 텔레그램 채널, 인터넷 라디오)를 둘러보는 도구입니다. "
                                "찾은 항목의 url을 사용자에게 주면 YMPlayer 화면에서 바로 열립니다.",
            })
        if method == "ping":
            return _result(mid, {})
        if method == "tools/list":
            return _result(mid, {"tools": TOOLS})
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if not any(t["name"] == name for t in TOOLS):
                raise McpError(-32602, f"없는 도구입니다: {name}")
            if not isinstance(args, dict):
                raise McpError(-32602, "arguments는 객체여야 합니다.")
            try:
                data = await call_tool(name, args)
            except McpError:
                raise
            except Exception as e:  # 도구 실행 오류는 프로토콜 오류가 아니라 결과로 (모델이 읽고 다시 시도할 수 있게)
                return _result(mid, {"content": [{"type": "text", "text": f"오류: {e}"}], "isError": True})
            return _result(mid, {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}],
                                 "structuredContent": data if isinstance(data, dict) else {"items": data}})
        if method in ("resources/list", "prompts/list"):
            return _result(mid, {method.split("/")[0]: []})
        raise McpError(-32601, f"Method not found: {method}")
    except McpError as e:
        return _error(mid, e.code, str(e))
