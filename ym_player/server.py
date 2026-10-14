"""Yume Player 서버 - Plex 스타일 웹 화면 + Plex/IPTV 중계.

실행:  python -m ym_player            (기본 http://127.0.0.1:8899)
환경변수:
    MP_HOST / MP_PORT        듣는 주소/포트 (기본 127.0.0.1 / 8899)
    MP_USER / MP_PASSWORD    둘 다 넣으면 HTTP 기본 인증을 건다 (LAN/외부에 열 때 꼭)
    MP_DATA_DIR              설정 폴더 (기본 ym_player/data)

하는 일:
- /                       web/index.html (단일 페이지 앱)
- /api/config             소스 목록 조회/저장 (Plex 토큰은 브라우저로 안 내보냄)
- /api/iptv/{id}/channels M3U를 받아 채널 목록 JSON으로
- /api/iptv/{id}/epg      XMLTV(.gz 포함)를 받아 지금-3시간 ~ +24시간 편성표 JSON으로
- /api/stream?url=        CORS/혼합콘텐츠로 막힌 스트림 중계 (.m3u8 안 주소까지 바꿔줌)
- /api/plex/{id}/...      Plex 서버 API/이미지/스트림 중계 - 토큰은 서버가 붙인다
- /api/plex-sub/{id}      Plex 외부 자막(srt/smi/ass/vtt)을 WebVTT로 바꿔 줌
- /api/plex-auth/...      plex.tv PIN 로그인 (토큰/서버 목록 받아오기)
"""
import asyncio
import base64
import hmac
import logging
import os
import time
from urllib.parse import urlencode

from aiohttp import ClientSession, ClientTimeout, web
from yarl import URL

from . import config_store, iptv, subtitles

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


# ---------------------------------------------------------------------------
# 공통
# ---------------------------------------------------------------------------
def _json_error(status, message):
    return web.json_response({"success": False, "error": message}, status=status)


@web.middleware
async def basic_auth_middleware(request, handler):
    user, pw = os.environ.get("MP_USER"), os.environ.get("MP_PASSWORD")
    if user and pw:
        header = request.headers.get("Authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                got_user, _, got_pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                ok = hmac.compare_digest(got_user, user) and hmac.compare_digest(got_pw, pw)
            except (ValueError, UnicodeDecodeError):
                ok = False
        if not ok:
            return web.Response(status=401, headers={"WWW-Authenticate": f'Basic realm="{PRODUCT}"'})
    return await handler(request)


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
async def get_config(request):
    return web.json_response(config_store.public_config(config_store.load()))


async def put_config(request):
    try:
        body = await request.json()
        cfg = config_store.replace_sources(config_store.load(), body.get("sources"))
    except ValueError as e:
        return _json_error(400, str(e))
    config_store.save(cfg)
    request.app[CHANNEL_CACHE].clear()
    request.app[EPG_CACHE].clear()
    return web.json_response(config_store.public_config(cfg))


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
# 앱
# ---------------------------------------------------------------------------
async def index(request):
    return web.FileResponse(os.path.join(WEB_DIR, "index.html"),
                            headers={"Cache-Control": "no-cache"})


async def _on_startup(app):
    app[HTTP] = ClientSession(auto_decompress=True)


async def _on_cleanup(app):
    await app[HTTP].close()


def create_app():
    app = web.Application(middlewares=[basic_auth_middleware], client_max_size=8 * 1024 * 1024)
    app[CHANNEL_CACHE] = {}
    app[EPG_CACHE] = {}
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)

    app.router.add_get("/", index)
    app.router.add_get("/api/config", get_config)
    app.router.add_put("/api/config", put_config)
    app.router.add_get("/api/iptv/{sid}/channels", iptv_channels)
    app.router.add_get("/api/iptv/{sid}/epg", iptv_epg)
    app.router.add_get("/api/stream", stream_proxy)
    app.router.add_post("/api/plex-auth/pin", plex_pin_create)
    app.router.add_get("/api/plex-auth/pin/{pin_id}", plex_pin_check)
    app.router.add_get("/api/plex-sub/{sid}", plex_subtitle)
    app.router.add_route("*", "/api/plex/{sid}/{tail:.*}", plex_proxy)
    app.router.add_static("/static/", WEB_DIR, show_index=False)
    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.environ.get("MP_HOST", "127.0.0.1")
    port = int(os.environ.get("MP_PORT", "8899"))
    if host not in ("127.0.0.1", "localhost", "::1") and not (os.environ.get("MP_USER") and os.environ.get("MP_PASSWORD")):
        log.warning("MP_HOST=%s 로 열지만 MP_USER/MP_PASSWORD가 없습니다 - 같은 네트워크의 누구나 "
                    "스트림 중계/설정 변경을 할 수 있습니다.", host)
    web.run_app(create_app(), host=host, port=port, print=lambda *_: log.info("Yume Player: http://%s:%d", host, port))


if __name__ == "__main__":
    main()
