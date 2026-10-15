"""Yume Player 서버 - Plex 스타일 웹 화면 + Plex/IPTV 중계.

실행:  python -m ym_player            (기본 http://127.0.0.1:8899)
환경변수:
    MP_HOST / MP_PORT        듣는 주소/포트 (기본 127.0.0.1 / 8899)
    MP_USER / MP_PASSWORD    사용자가 아직 없을 때 이 아이디/비밀번호로 첫 관리자를 만든다
                             (없으면 첫 접속 화면에서 관리자 계정을 만든다 - auth.py)
    MP_DATA_DIR              설정 폴더 (기본 ym_player/data)

하는 일:
- /                       web/index.html (단일 페이지 앱) - 로그인해야 열림, 아니면 /login
- /login, /api/auth/...   로그인 화면, 로그인/로그아웃/첫 관리자 만들기/비밀번호 변경
- /api/users              사용자 관리 (관리자만)
- /api/config             소스 목록 조회/저장 (Plex 토큰은 브라우저로 안 내보냄)
- /api/iptv/{id}/channels M3U를 받아 채널 목록 JSON으로
- /api/iptv/{id}/epg      XMLTV(.gz 포함)를 받아 지금-3시간 ~ +24시간 편성표 JSON으로
- /api/stream?url=        CORS/혼합콘텐츠로 막힌 스트림 중계 (.m3u8 안 주소까지 바꿔줌)
- /api/plex/{id}/...      Plex 서버 API/이미지/스트림 중계 - 토큰은 서버가 붙인다
- /api/plex-sub/{id}      Plex 외부 자막(srt/smi/ass/vtt)을 WebVTT로 바꿔 줌
- /api/tg/{id}/...        텔레그램 로그인/채널/영상 목록/스트림/썸네일/이어보기 (telegram_src.py)
- /api/plex-auth/...      plex.tv PIN 로그인 (토큰/서버 목록 받아오기)
"""
import asyncio
import logging
import re
import os
import time
from urllib.parse import urlencode

from aiohttp import ClientSession, ClientTimeout, web
from yarl import URL

from . import auth, config_store, iptv, subtitles, telegram_src

log = logging.getLogger("ym_player")

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
PRODUCT = "Yume Player"
TEXT_MAX_BYTES = 80 * 1024 * 1024  # 통합 EPG가 수십 MB인 경우가 있어 넉넉히
CHANNEL_CACHE_SEC = 10 * 60
EPG_CACHE_SEC = 30 * 60
EPG_PAST_SEC = 3 * 3600
EPG_FUTURE_SEC = 24 * 3600
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

# 응답으로 그대로 넘겨줄 헤더 (나머지 hop-by-hop/압축 헤더는 버린다)
_PASS_RESP_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges",
                      "Cache-Control", "Last-Modified", "ETag")
_PASS_REQ_HEADERS = ("Range", "Accept", "Accept-Language", "Content-Type", "If-None-Match", "If-Modified-Since")


HTTP = web.AppKey("http", ClientSession)
CHANNEL_CACHE = web.AppKey("channel_cache", dict)
EPG_CACHE = web.AppKey("epg_cache", dict)
TELEGRAM = web.AppKey("telegram", dict)
REQUIRE_AUTH = web.AppKey("require_auth", bool)  # 소스 id -> telegram_src.TelegramSource (연결을 계속 유지)


# ---------------------------------------------------------------------------
# 공통
# ---------------------------------------------------------------------------
def _json_error(status, message):
    return web.json_response({"success": False, "error": message}, status=status)


# 로그인 없이 열리는 경로 (로그인 화면과 그 화면이 쓰는 것만)
_PUBLIC_PATHS = {"/login", "/api/auth/state", "/api/auth/login", "/api/auth/setup"}


@web.middleware
async def auth_middleware(request, handler):
    """로그인한 사용자를 request["user"]에 넣는다. 안 했으면 API는 401, 화면은 /login으로."""
    if not request.app[REQUIRE_AUTH]:
        request["user"] = {"username": "", "role": "admin"}
        return await handler(request)
    user = auth.user_from_token(request.cookies.get(auth.COOKIE))
    if user:
        request["user"] = auth.public_user(user)
        return await handler(request)
    if request.path in _PUBLIC_PATHS or request.path.startswith("/static/"):
        return await handler(request)
    if request.path.startswith("/api/"):
        return web.json_response({"success": False, "error": "로그인이 필요합니다.", "login": True}, status=401)
    raise web.HTTPFound("/login")


def is_admin(request):
    return request.get("user", {}).get("role") == "admin"


def admin_only(handler):
    async def wrapped(request, *args):
        if not is_admin(request):
            return _json_error(403, "관리자만 할 수 있습니다.")
        return await handler(request, *args)
    return wrapped


def username(request):
    return request.get("user", {}).get("username", "")


async def _fetch_bytes(session, url, timeout=30, headers=None, max_bytes=TEXT_MAX_BYTES):
    hdrs = {"User-Agent": DEFAULT_UA, "Accept-Encoding": "gzip, deflate"}
    hdrs.update(headers or {})
    async with session.get(url, headers=hdrs, timeout=ClientTimeout(total=timeout)) as resp:
        if resp.status >= 400:
            raise web.HTTPBadGateway(text=f"원본 서버 응답 HTTP {resp.status}")
        buf = bytearray()
        async for chunk in resp.content.iter_chunked(64 * 1024):
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise web.HTTPRequestEntityTooLarge(max_size=max_bytes, actual_size=len(buf))
        return bytes(buf)


def _resp_headers(upstream):
    headers = {k: upstream.headers[k] for k in _PASS_RESP_HEADERS if k in upstream.headers}
    if upstream.headers.get("Content-Encoding"):
        # aiohttp가 압축을 풀어서 넘겨주므로 원래 길이는 더 이상 맞지 않는다
        headers.pop("Content-Length", None)
    return headers


async def _pipe_response(request, upstream):
    """원본 응답을 버퍼링 없이 그대로 흘려보낸다 (Range 재생/라이브 스트림)."""
    headers = _resp_headers(upstream)
    resp = web.StreamResponse(status=upstream.status, headers=headers)
    await resp.prepare(request)
    try:
        async for chunk in upstream.content.iter_chunked(64 * 1024):
            await resp.write(chunk)
    except (ConnectionResetError, asyncio.CancelledError):
        # 브라우저가 채널을 바꾸거나 탐색(seek)하면 연결을 끊는다 - 정상 상황
        pass
    return resp


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
# 관리자가 아닌 사용자에게는 서버 주소/M3U 주소(키가 들어 있기도 함)/API ID 등을 안 보여준다
_USER_VISIBLE = ("id", "type", "name", "enabled", "channels", "hidden_libraries")


async def get_config(request):
    pub = config_store.public_config(config_store.load())
    if not is_admin(request):
        pub["sources"] = [{**{k: s[k] for k in _USER_VISIBLE if k in s}, "has_epg": bool(s.get("epg"))}
                          for s in pub["sources"]]
    return web.json_response(pub)


@admin_only
async def put_config(request):
    try:
        body = await request.json()
        cfg = config_store.replace_sources(config_store.load(), body.get("sources"))
    except ValueError as e:
        return _json_error(400, str(e))
    old_ids = {s["id"] for s in config_store.load().get("sources", []) if s.get("type") == "telegram"}
    config_store.save(cfg)
    request.app[CHANNEL_CACHE].clear()
    request.app[EPG_CACHE].clear()
    # 지운 텔레그램 소스는 로그아웃(텔레그램의 "활성 세션"에서도 빠지게)하고 색인도 지운다
    new_ids = {s["id"] for s in cfg["sources"] if s.get("type") == "telegram"}
    for sid in old_ids - new_ids:
        tg = request.app[TELEGRAM].pop(sid, None) or telegram_src.TelegramSource({"id": sid})
        asyncio.ensure_future(_forget_telegram(tg))
    return web.json_response(config_store.public_config(cfg))


async def _forget_telegram(tg):
    try:
        await tg.logout()
    except Exception as e:
        log.warning("텔레그램 소스 %s 로그아웃 실패(세션 파일만 지움): %s", tg.sid, e)
        for suffix in ("", "-journal"):
            try:
                os.remove(tg.session_path + suffix)
            except OSError:
                pass
    telegram_src.forget_source(tg.sid)


def _source_or_404(source_id, stype):
    src = config_store.get_source(config_store.load(), source_id)
    if not src or src.get("type") != stype:
        raise web.HTTPNotFound(text="소스를 찾을 수 없습니다.")
    return src


# ---------------------------------------------------------------------------
# IPTV
# ---------------------------------------------------------------------------
async def iptv_channels(request):
    src = _source_or_404(request.match_info["sid"], "iptv")
    cache = request.app[CHANNEL_CACHE]
    key = (src["id"], src["m3u"])
    hit = cache.get(key)
    if hit and time.time() - hit[0] < CHANNEL_CACHE_SEC and not request.query.get("refresh"):
        return web.json_response(hit[1])
    try:
        raw = await _fetch_bytes(request.app[HTTP], src["m3u"], timeout=20)
    except web.HTTPException as e:
        return _json_error(502, f"M3U 받기 실패: {e.text or e.reason}")
    except Exception as e:  # 네트워크 오류 종류가 많아서 넓게 잡는다
        return _json_error(502, f"M3U 받기 실패: {type(e).__name__}: {e}")
    channels = iptv.parse_m3u(iptv.decode_text(raw), src["name"], src["id"])
    data = {"success": True, "channels": channels, "has_epg": bool(src.get("epg"))}
    cache[key] = (time.time(), data)
    return web.json_response(data)


async def iptv_epg(request):
    src = _source_or_404(request.match_info["sid"], "iptv")
    if not src.get("epg"):
        return web.json_response({"success": True, "names": {}, "icons": {}, "programmes": {}})
    cache = request.app[EPG_CACHE]
    key = (src["id"], src["epg"])
    hit = cache.get(key)
    if hit and time.time() - hit[0] < EPG_CACHE_SEC and not request.query.get("refresh"):
        return web.json_response(hit[1])
    try:
        raw = await _fetch_bytes(request.app[HTTP], src["epg"], timeout=60)
        now = int(time.time())
        # 큰 XML 파싱은 CPU를 오래 잡으니 이벤트 루프 밖에서
        parsed = await asyncio.get_running_loop().run_in_executor(
            None, iptv.parse_xmltv, raw, now - EPG_PAST_SEC, now + EPG_FUTURE_SEC)
    except web.HTTPException as e:
        return _json_error(502, f"EPG 받기 실패: {e.text or e.reason}")
    except Exception as e:
        return _json_error(502, f"EPG 처리 실패: {type(e).__name__}: {e}")
    data = {"success": True, **parsed}
    cache[key] = (time.time(), data)
    return web.json_response(data)


async def stream_proxy(request):
    target = request.query.get("url", "")
    if not target.startswith(("http://", "https://")):
        return _json_error(400, "http(s) 주소만 중계합니다.")
    ua = request.query.get("ua", "")
    referer = request.query.get("ref", "")
    headers = {"User-Agent": ua or DEFAULT_UA}
    if referer:
        headers["Referer"] = referer
    if "Range" in request.headers:
        headers["Range"] = request.headers["Range"]

    session = request.app[HTTP]
    try:
        upstream = await session.get(target, headers=headers, allow_redirects=True,
                                     timeout=ClientTimeout(total=None, sock_connect=10, sock_read=30))
    except Exception as e:
        return _json_error(502, f"스트림 연결 실패: {type(e).__name__}: {e}")

    try:
        head = await upstream.content.read(7) if upstream.status < 400 else b""
        if upstream.status < 400 and iptv.looks_like_m3u8(upstream.headers.get("Content-Type"), str(upstream.url), head):
            rest = await upstream.content.read(8 * 1024 * 1024)
            text = iptv.decode_text(head + rest)
            body = iptv.rewrite_m3u8(text, str(upstream.url), lambda u: iptv.stream_proxy_url(u, ua, referer))
            return web.Response(text=body, content_type="application/vnd.apple.mpegurl",
                                headers={"Cache-Control": "no-cache"})

        resp = web.StreamResponse(status=upstream.status, headers=_resp_headers(upstream))
        await resp.prepare(request)
        try:
            if head:
                await resp.write(head)
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                await resp.write(chunk)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return resp
    finally:
        upstream.release()


# ---------------------------------------------------------------------------
# Plex
# ---------------------------------------------------------------------------
def plex_headers(cfg, token=None):
    h = {
        "X-Plex-Product": PRODUCT,
        "X-Plex-Version": "1.0",
        "X-Plex-Client-Identifier": cfg["client_id"],
        "X-Plex-Platform": "Chrome",
        "X-Plex-Device": "Web",
        "X-Plex-Device-Name": PRODUCT,
    }
    if token:
        h["X-Plex-Token"] = token
    return h


async def plex_proxy(request):
    """/api/plex/{sid}/<경로>?<쿼리> -> <Plex 서버>/<경로>?<쿼리> (토큰은 여기서 붙임).

    Plex 트랜스코더의 HLS 재생목록은 상대 주소라, 브라우저가 이 프록시 경로 기준으로
    그대로 풀어서 다시 여기로 요청한다 - 따로 주소를 바꿀 필요가 없다.
    """
    cfg = config_store.load()
    src = config_store.get_source(cfg, request.match_info["sid"])
    if not src or src.get("type") != "plex":
        return _json_error(404, "Plex 소스를 찾을 수 없습니다.")
    if not is_admin(request) and not _plex_allowed_for_user(request):
        return _json_error(403, "이 Plex 요청은 관리자만 할 수 있습니다.")
    # 이미 인코딩된 원래 모양 그대로 넘긴다 (match_info/query_string은 디코딩된 값이라
    # /library/parts/.../파일명.mkv 의 %20 같은 게 깨진다)
    raw_path = request.rel_url.raw_path
    prefix = f"/api/plex/{src['id']}/"
    tail = raw_path[len(prefix):] if raw_path.startswith(prefix) else request.match_info["tail"]
    url = f"{src['url']}/{tail}"
    if request.rel_url.raw_query_string:
        url += "?" + request.rel_url.raw_query_string

    headers = plex_headers(cfg, src.get("token"))
    for k in _PASS_REQ_HEADERS:
        if k in request.headers:
            headers[k] = request.headers[k]
    body = await request.read() if request.can_read_body else None

    try:
        upstream = await request.app[HTTP].request(
            request.method, URL(url, encoded=True), headers=headers, data=body,
            ssl=None if src.get("verify_ssl", True) else False,
            timeout=ClientTimeout(total=None, sock_connect=10, sock_read=60))
    except Exception as e:
        return _json_error(502, f"Plex 서버 연결 실패: {type(e).__name__}: {e}")
    try:
        return await _pipe_response(request, upstream)
    finally:
        upstream.release()


async def plex_subtitle(request):
    """Plex 외부 자막 파일(/library/streams/{id})을 받아 WebVTT로 바꿔 준다 (<track>용).

    ?path=/library/streams/123&codec=srt  - codec은 Plex가 알려준 형식(없으면 내용으로 판단)
    """
    cfg = config_store.load()
    src = config_store.get_source(cfg, request.match_info["sid"])
    if not src or src.get("type") != "plex":
        return _json_error(404, "Plex 소스를 찾을 수 없습니다.")
    path = request.query.get("path", "")
    # 아무 Plex 경로나 받아오는 통로가 되지 않게 자막 스트림 경로만 허용
    if not path.startswith("/library/streams/") or ".." in path:
        return _json_error(400, "자막 스트림 경로가 아닙니다.")
    try:
        async with request.app[HTTP].get(
                f"{src['url']}{path}", headers=plex_headers(cfg, src.get("token")),
                ssl=None if src.get("verify_ssl", True) else False,
                timeout=ClientTimeout(total=30)) as r:
            if r.status >= 400:
                return _json_error(502, f"Plex 자막 받기 실패 (HTTP {r.status})")
            data = await r.content.read(20 * 1024 * 1024)
    except Exception as e:
        return _json_error(502, f"Plex 서버 연결 실패: {type(e).__name__}: {e}")
    vtt = subtitles.to_vtt(data, request.query.get("codec", ""))
    return web.Response(text=vtt, content_type="text/vtt", charset="utf-8",
                        headers={"Cache-Control": "private, max-age=3600"})


_PLEX_USER_PUT = re.compile(r"^/api/plex/[^/]+/library/parts/\d+$")


def _plex_allowed_for_user(request):
    """일반 사용자는 서버 토큰으로 읽기(GET)만. 쓰기는 재생기가 쓰는 자막 선택 저장(PUT parts)뿐."""
    if request.method in ("GET", "HEAD"):
        return True
    return request.method == "PUT" and bool(_PLEX_USER_PUT.match(request.path))


@admin_only
async def plex_pin_create(request):
    cfg = config_store.load()
    async with request.app[HTTP].post("https://plex.tv/api/v2/pins?strong=true",
                                        headers={**plex_headers(cfg), "Accept": "application/json"}) as r:
        if r.status >= 400:
            return _json_error(502, f"plex.tv PIN 발급 실패 (HTTP {r.status})")
        pin = await r.json()
    auth_url = "https://app.plex.tv/auth#?" + urlencode({
        "clientID": cfg["client_id"], "code": pin["code"],
        "context[device][product]": PRODUCT,
    })
    return web.json_response({"success": True, "id": pin["id"], "auth_url": auth_url})


@admin_only
async def plex_pin_check(request):
    """PIN이 승인됐으면 계정 토큰으로 서버 목록(각 서버 접속 주소 + 서버용 토큰)을 돌려준다."""
    cfg = config_store.load()
    http = request.app[HTTP]
    hdrs = {**plex_headers(cfg), "Accept": "application/json"}
    async with http.get(f"https://plex.tv/api/v2/pins/{int(request.match_info['pin_id'])}", headers=hdrs) as r:
        if r.status >= 400:
            return _json_error(502, f"PIN 확인 실패 (HTTP {r.status})")
        pin = await r.json()
    token = pin.get("authToken")
    if not token:
        return web.json_response({"success": True, "done": False})

    async with http.get("https://plex.tv/api/v2/resources?includeHttps=1&includeRelay=1",
                        headers={**hdrs, "X-Plex-Token": token}) as r:
        resources = await r.json() if r.status < 400 else []
    servers = []
    for res in resources:
        if "server" not in (res.get("provides") or ""):
            continue
        conns = sorted(res.get("connections") or [],
                       key=lambda c: (c.get("relay", False), not c.get("local", False)))
        servers.append({
            "name": res.get("name"),
            "token": res.get("accessToken") or token,
            "owned": res.get("owned", False),
            "connections": [{"uri": c.get("uri"), "local": c.get("local", False),
                             "relay": c.get("relay", False)} for c in conns],
        })
    return web.json_response({"success": True, "done": True, "servers": servers})


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def _telegram(request):
    """설정의 텔레그램 소스에 맞는 연결 객체. API ID/Hash가 바뀌었으면 새로 만든다."""
    src = config_store.get_source(config_store.load(), request.match_info["sid"])
    if not src or src.get("type") != "telegram":
        raise web.HTTPNotFound(text="텔레그램 소스를 찾을 수 없습니다.")
    pool = request.app[TELEGRAM]
    tg = pool.get(src["id"])
    if tg is None or not tg.matches(src):
        if tg is not None:
            asyncio.ensure_future(tg.close())
        tg = pool[src["id"]] = telegram_src.TelegramSource(src)
    tg.channels = [int(c["id"]) for c in src.get("channels") or []]
    tg.phone = tg.phone if tg._code_hash else str(src.get("phone") or "").strip()
    return tg


def telegram_api(handler):
    """TgError는 화면에 그대로, FloodWait는 몇 초 기다리라고, 나머지는 502로."""
    async def wrapped(request):
        try:
            return await handler(request, _telegram(request))
        except web.HTTPException:
            raise
        except telegram_src.TgError as e:
            return _json_error(400, str(e))
        except Exception as e:
            if type(e).__name__ == "FloodWaitError":
                return _json_error(429, f"텔레그램 요청 제한에 걸렸습니다. {getattr(e, 'seconds', '?')}초 뒤에 다시 시도하세요.")
            log.exception("telegram 요청 실패")
            return _json_error(502, f"텔레그램 처리 실패: {type(e).__name__}: {e}")
    return wrapped


async def _json_body(request):
    try:
        body = await request.json()
    except ValueError:
        body = {}
    return body if isinstance(body, dict) else {}


@admin_only
@telegram_api
async def tg_status(request, tg):
    return web.json_response({"success": True, **await tg.status()})


@admin_only
@telegram_api
async def tg_code(request, tg):
    body = await _json_body(request)
    return web.json_response({"success": True, **await tg.send_code(str(body.get("phone") or ""))})


@admin_only
@telegram_api
async def tg_sign_in(request, tg):
    body = await _json_body(request)
    return web.json_response({"success": True, **await tg.sign_in(str(body.get("code") or ""), str(body.get("password") or ""))})


@admin_only
@telegram_api
async def tg_logout(request, tg):
    await tg.logout()
    return web.json_response({"success": True, "authorized": False})


@admin_only
@telegram_api
async def tg_channels(request, tg):
    return web.json_response({"success": True, "channels": await tg.list_channels()})


def _int_arg(request, name, default=0, lo=0, hi=10 ** 12):
    try:
        return max(lo, min(hi, int(request.query.get(name, default))))
    except (TypeError, ValueError):
        return default


@telegram_api
async def tg_videos(request, tg):
    channel = request.query.get("channel")
    channels = [int(channel)] if channel and channel.lstrip("-").isdigit() else list(tg.channels)
    data = await tg.videos(channels, offset=_int_arg(request, "offset"), limit=_int_arg(request, "limit", 40, 1, 200),
                           q=request.query.get("q", "").strip(), sort=request.query.get("sort", "latest"),
                           user=username(request))
    return web.json_response({"success": True, **data})


@telegram_api
async def tg_home(request, tg):
    """홈 줄: 이어보기 + 채널마다 최신 영상 몇 개. 채널들을 동시에, 오래된 쪽 채우기 없이."""
    async def one(ch):
        try:
            data = await tg.videos([ch], limit=12, fill=False, user=username(request))
        except telegram_src.TgError:
            raise
        except Exception as e:
            log.warning("telegram 홈 %s 실패: %s", ch, e)
            return None
        return {"channel": ch, "items": data["items"]}

    rows = [r for r in await asyncio.gather(*(one(ch) for ch in tg.channels)) if r]
    return web.json_response({"success": True,
                              "continue": telegram_src.continue_watching(tg.sid, tg.channels, user=username(request)),
                              "channels": rows})


def _ids(request, tg):
    channel, msg = int(request.match_info["channel"]), int(request.match_info["msg"])
    if channel not in tg.channels:
        raise web.HTTPNotFound(text="설정에서 고르지 않은 채널입니다.")
    return channel, msg


@telegram_api
async def tg_video(request, tg):
    channel, msg = _ids(request, tg)
    row = telegram_src.get_video(tg.sid, channel, msg, user=username(request))
    if not row:
        return _json_error(404, "색인에 없는 영상입니다.")
    return web.json_response({"success": True, "item": row})


@telegram_api
async def tg_stream(request, tg):
    channel, msg = _ids(request, tg)
    row = telegram_src.get_video(tg.sid, channel, msg)
    if not row or not row["size"]:
        return _json_error(404, "색인에 없는 영상입니다.")
    size = int(row["size"])
    try:
        start, end, partial = telegram_src.parse_range(request.headers.get("Range"), size)
    except ValueError:
        return web.Response(status=416, headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})
    await tg.authorized_client()  # 로그인 안 됐으면 응답을 시작하기 전에 JSON 오류로
    length = end - start + 1
    headers = {"Content-Type": telegram_src.browser_mime(row["mime"]), "Accept-Ranges": "bytes",
               "Content-Length": str(length), "Cache-Control": "no-store"}
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    resp = web.StreamResponse(status=206 if partial else 200, headers=headers)
    await resp.prepare(request)
    try:
        async for chunk in tg.iter_range(channel, msg, start, length):
            await resp.write(chunk)
    except (ConnectionResetError, asyncio.CancelledError):
        pass  # 브라우저가 탐색하거나 닫으면 연결을 끊는다 - 정상
    except Exception as e:
        log.warning("telegram 스트림 %s/%s 중단: %s", channel, msg, e)
    return resp


@telegram_api
async def tg_thumb(request, tg):
    channel, msg = _ids(request, tg)
    try:
        path = await tg.thumbnail(channel, msg)
    except telegram_src.TgError as e:
        return _json_error(404, str(e))
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400", "Content-Type": "image/jpeg"})


@telegram_api
async def tg_progress(request, tg):
    body = await _json_body(request)
    try:
        channel, msg = int(body.get("channel")), int(body.get("msg"))
    except (TypeError, ValueError):
        return _json_error(400, "channel/msg가 없습니다.")
    if channel not in tg.channels:
        return _json_error(404, "설정에서 고르지 않은 채널입니다.")
    watched = body.get("watched")
    telegram_src.save_progress(tg.sid, channel, msg, body.get("position"), body.get("duration"),
                               None if watched is None else bool(watched), user=username(request))
    return web.json_response({"success": True})


# ---------------------------------------------------------------------------
# 로그인 / 사용자
# ---------------------------------------------------------------------------
def _set_session(request, resp, user, remember):
    resp.set_cookie(auth.COOKIE, auth.make_token(user, remember), httponly=True, samesite="Lax",
                    secure=request.secure, path="/", max_age=auth.REMEMBER_SEC if remember else None)
    return resp


async def login_page(request):
    return web.FileResponse(os.path.join(WEB_DIR, "login.html"), headers={"Cache-Control": "no-cache"})


async def auth_state(request):
    user = auth.user_from_token(request.cookies.get(auth.COOKIE))
    return web.json_response({"success": True, "setup": auth.needs_setup(),
                              "user": auth.public_user(user) if user else None})


async def auth_login(request):
    ip = request.remote or "?"
    if auth.throttled(ip):
        return _json_error(429, "로그인 실패가 너무 많습니다. 10분 뒤에 다시 시도하세요.")
    body = await _json_body(request)
    user = auth.verify_password(body.get("username"), str(body.get("password") or ""))
    if not user:
        auth.record_failure(ip)
        await asyncio.sleep(0.5)  # 무작위 대입을 느리게
        return _json_error(401, "아이디 또는 비밀번호가 틀렸습니다.")
    auth.clear_failures(ip)
    return _set_session(request, web.json_response({"success": True, "user": auth.public_user(user)}),
                        user, bool(body.get("remember")))


async def auth_setup(request):
    """사용자가 하나도 없을 때만: 첫 관리자 만들기."""
    if not auth.needs_setup():
        return _json_error(403, "이미 관리자 계정이 있습니다.")
    body = await _json_body(request)
    try:
        auth.create_user(body.get("username"), str(body.get("password") or ""), "admin")
    except auth.AuthError as e:
        return _json_error(400, str(e))
    user = auth.find_user(body.get("username"))
    return _set_session(request, web.json_response({"success": True, "user": auth.public_user(user)}),
                        user, bool(body.get("remember")))


async def auth_logout(request):
    resp = web.json_response({"success": True})
    resp.del_cookie(auth.COOKIE, path="/")
    return resp


async def auth_me(request):
    return web.json_response({"success": True, "user": request.get("user")})


async def auth_password(request):
    """내 비밀번호 바꾸기 (지금 비밀번호 확인). 바꾸면 다른 기기의 로그인은 끊기고 이 기기는 새 쿠키."""
    body = await _json_body(request)
    name = username(request)
    if not auth.verify_password(name, str(body.get("current") or "")):
        return _json_error(400, "지금 비밀번호가 틀렸습니다.")
    try:
        auth.update_user(name, password=str(body.get("new") or ""))
    except auth.AuthError as e:
        return _json_error(400, str(e))
    return _set_session(request, web.json_response({"success": True}), auth.find_user(name), True)


@admin_only
async def users_list(request):
    return web.json_response({"success": True, "users": [auth.public_user(u) for u in auth.load_users()]})


@admin_only
async def users_create(request):
    body = await _json_body(request)
    try:
        user = auth.create_user(body.get("username"), str(body.get("password") or ""), body.get("role") or "user")
    except auth.AuthError as e:
        return _json_error(400, str(e))
    return web.json_response({"success": True, "user": user})


@admin_only
async def users_update(request):
    body = await _json_body(request)
    try:
        user = auth.update_user(request.match_info["name"],
                                password=str(body["password"]) if body.get("password") else None,
                                role=body.get("role"), acting=username(request))
    except auth.AuthError as e:
        return _json_error(400, str(e))
    return web.json_response({"success": True, "user": user})


@admin_only
async def users_delete(request):
    try:
        auth.delete_user(request.match_info["name"], acting=username(request))
    except auth.AuthError as e:
        return _json_error(400, str(e))
    return web.json_response({"success": True})


# ---------------------------------------------------------------------------
# 앱
# ---------------------------------------------------------------------------
async def index(request):
    return web.FileResponse(os.path.join(WEB_DIR, "index.html"),
                            headers={"Cache-Control": "no-cache"})


async def _on_startup(app):
    app[HTTP] = ClientSession(auto_decompress=True)
    if app[REQUIRE_AUTH]:
        made = auth.bootstrap_from_env()
        if made:
            log.info("MP_USER로 첫 관리자 '%s'를 만들었습니다.", made["username"])
        admins = [u["username"] for u in auth.load_users() if u["role"] == "admin"]
        telegram_src.MIGRATE_USER = admins[0] if admins else ""


async def _on_cleanup(app):
    await app[HTTP].close()
    for tg in list(app[TELEGRAM].values()):
        try:
            await tg.close()
        except Exception:
            pass


def create_app(require_auth=True):
    """require_auth=False는 테스트용 (모든 요청을 관리자로 본다)."""
    app = web.Application(middlewares=[auth_middleware], client_max_size=8 * 1024 * 1024)
    app[REQUIRE_AUTH] = require_auth
    app[CHANNEL_CACHE] = {}
    app[EPG_CACHE] = {}
    app[TELEGRAM] = {}
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)

    app.router.add_get("/", index)
    app.router.add_get("/login", login_page)
    app.router.add_get("/api/auth/state", auth_state)
    app.router.add_post("/api/auth/login", auth_login)
    app.router.add_post("/api/auth/setup", auth_setup)
    app.router.add_post("/api/auth/logout", auth_logout)
    app.router.add_get("/api/auth/me", auth_me)
    app.router.add_post("/api/auth/password", auth_password)
    app.router.add_get("/api/users", users_list)
    app.router.add_post("/api/users", users_create)
    app.router.add_put("/api/users/{name}", users_update)
    app.router.add_delete("/api/users/{name}", users_delete)
    app.router.add_get("/api/config", get_config)
    app.router.add_put("/api/config", put_config)
    app.router.add_get("/api/iptv/{sid}/channels", iptv_channels)
    app.router.add_get("/api/iptv/{sid}/epg", iptv_epg)
    app.router.add_get("/api/stream", stream_proxy)
    app.router.add_post("/api/plex-auth/pin", plex_pin_create)
    app.router.add_get("/api/plex-auth/pin/{pin_id}", plex_pin_check)
    app.router.add_get("/api/plex-sub/{sid}", plex_subtitle)
    app.router.add_get("/api/tg/{sid}/status", tg_status)
    app.router.add_post("/api/tg/{sid}/code", tg_code)
    app.router.add_post("/api/tg/{sid}/sign-in", tg_sign_in)
    app.router.add_post("/api/tg/{sid}/logout", tg_logout)
    app.router.add_get("/api/tg/{sid}/channels", tg_channels)
    app.router.add_get("/api/tg/{sid}/videos", tg_videos)
    app.router.add_get("/api/tg/{sid}/home", tg_home)
    app.router.add_get(r"/api/tg/{sid}/video/{channel:-?\d+}/{msg:\d+}", tg_video)
    app.router.add_get(r"/api/tg/{sid}/stream/{channel:-?\d+}/{msg:\d+}", tg_stream)
    app.router.add_get(r"/api/tg/{sid}/thumb/{channel:-?\d+}/{msg:\d+}", tg_thumb)
    app.router.add_post("/api/tg/{sid}/progress", tg_progress)
    app.router.add_route("*", "/api/plex/{sid}/{tail:.*}", plex_proxy)
    app.router.add_static("/static/", WEB_DIR, show_index=False)
    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.environ.get("MP_HOST", "127.0.0.1")
    port = int(os.environ.get("MP_PORT", "8899"))
    if host not in ("127.0.0.1", "localhost", "::1") and auth.needs_setup() and not (
            os.environ.get("MP_USER") and os.environ.get("MP_PASSWORD")):
        log.warning("MP_HOST=%s 로 열었고 아직 사용자가 없습니다 - 가장 먼저 접속한 사람이 관리자 계정을 만들게 됩니다. "
                    "바로 접속해서 관리자를 만들거나 MP_USER/MP_PASSWORD를 넣고 시작하세요.", host)
    web.run_app(create_app(), host=host, port=port, print=lambda *_: log.info("Yume Player: http://%s:%d", host, port))


if __name__ == "__main__":
    main()
