"""YMPlayer 서버 - Plex 스타일 웹 화면 + Plex/IPTV 중계.

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
- /api/me/prefs           내 즐겨찾기 채널 등 사용자별 작은 설정 (prefs.py)
- /api/admin/...          상태 점검, 백업 내려받기/복원, 동영상 캐시 설정/비우기, 화면 이름/아이콘 (관리자만)
- /api/branding, /brand/icon  화면 이름과 아이콘 (로그인 화면도 쓰므로 로그인 없이)
- /api/config             소스 목록 조회/저장 (Plex 토큰은 브라우저로 안 내보냄)
- /api/iptv/{id}/channels M3U를 받아 채널 목록 JSON으로
- /api/iptv/{id}/epg      XMLTV(.gz 포함)를 받아 지금-3시간 ~ +24시간 편성표 JSON으로
- /api/stream?url=        CORS/혼합콘텐츠로 막힌 스트림 중계 (.m3u8 안 주소까지 바꿔줌)
- /api/plex/{id}/...      Plex 서버 API/이미지/스트림 중계 - 토큰은 서버가 붙인다
- /api/plex-sub/{id}      Plex 외부 자막(srt/smi/ass/vtt)을 WebVTT로 바꿔 줌
- /api/tg/{id}/...        텔레그램 로그인/채널/영상 목록/스트림/썸네일/이어보기 (telegram_src.py),
                          브라우저가 못 여는 영상은 ffmpeg 서버 변환 (transcode.py)
- /api/plex-auth/...      plex.tv PIN 로그인 (토큰/서버 목록 받아오기)
"""
import asyncio
import base64
import binascii
import collections
import hashlib
import hmac
import html
import json
import logging
import re
import os
import platform
import secrets
import shutil
import time
from urllib.parse import quote, urlencode

from aiohttp import ClientSession, ClientTimeout, web
from yarl import URL

from . import __version__, activity, auth, config_store, discord_feed, iptv, mcp, media_cache, plex_to_tg, prefs, radio, subtitles, telegram_src, tmdb, transcode, updates

log = logging.getLogger("ym_player")

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
PRODUCT = "YMPlayer"
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


class _RecentWarnings(logging.Handler):
    """상태 화면에 보여줄 최근 경고/오류 몇 개 (메모리에만)."""

    def __init__(self, size=60):
        super().__init__(logging.WARNING)
        self.items = collections.deque(maxlen=size)

    def emit(self, record):
        try:
            self.items.append({"time": record.created, "level": record.levelname, "logger": record.name,
                               "message": record.getMessage()[:500]})
        except Exception:
            pass


RECENT = _RecentWarnings()
STARTED = time.time()

HTTP = web.AppKey("http", ClientSession)
CHANNEL_CACHE = web.AppKey("channel_cache", dict)
EPG_CACHE = web.AppKey("epg_cache", dict)
TELEGRAM = web.AppKey("telegram", dict)
REQUIRE_AUTH = web.AppKey("require_auth", bool)
TG_INTERNAL = web.AppKey("tg_internal", dict)   # ffmpeg용 내부 주소 토큰 -> (소스id, 채널, 메시지, 만료)
TRANSCODES = web.AppKey("transcodes", dict)     # 사용자명 -> 돌고 있는 ffmpeg 프로세스
PLEX_FILES = web.AppKey("plex_files", dict)  # 캐시 키 -> (크기, Content-Type) - Plex 원본 파일
HLS_JOBS = web.AppKey("hls_jobs", dict)  # HLS 변환 id -> HlsJob (Safari용 서버 변환)
HLS_SWEEPER = web.AppKey("hls_sweeper", asyncio.Task)
UPDATES = web.AppKey("updates", updates.UpdateChecker)
UPDATE_TASK = web.AppKey("update_task", asyncio.Task)
DISCORD = web.AppKey("discord", discord_feed.Feed)   # 디스코드 채널 새 글 -> 아래쪽 흐르는 글
DISCORD_TASK = web.AppKey("discord_task", asyncio.Task)
ACTIVITY = web.AppKey("activity", activity.Activity)   # 지금 재생 중인 스트림 수 (/api/activity)
MIRROR = web.AppKey("mirror", plex_to_tg.Mirror)   # Plex 새 동영상 -> 텔레그램 채널 자동 올리기
MIRROR_TASK = web.AppKey("mirror_task", asyncio.Task)
PROBES = web.AppKey("probes", dict)  # (소스id, 채널, 메시지) -> 돌고 있는 ffprobe (같은 영상을 겹쳐 보지 않게)  # 소스 id -> telegram_src.TelegramSource (연결을 계속 유지)


# ---------------------------------------------------------------------------
# 공통
# ---------------------------------------------------------------------------
def _json_error(status, message):
    return web.json_response({"success": False, "error": message}, status=status)


# 로그인 없이 열리는 경로 (로그인 화면과 그 화면이 쓰는 것만)
_PUBLIC_PATHS = {"/login", "/api/auth/state", "/api/auth/login", "/api/auth/setup", "/api/branding", "/brand/icon"}


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
    # 재생 현황(/api/activity)은 이 경로만 MP_ACTIVITY_TOKEN으로도 (다른 API는 이 토큰으로 안 열린다)
    if request.path == "/api/activity" and _activity_token_ok(request):
        request["activity_token"] = True
        return await handler(request)
    # /mcp는 쿠키가 아니라 MCP 토큰으로 (mcp_endpoint가 확인)
    if request.path in _PUBLIC_PATHS or request.path.startswith(("/static/", "/s/", "/internal/tg/", "/mcp/")) or request.path == "/mcp":
        return await handler(request)
    if request.path.startswith("/api/"):
        return web.json_response({"success": False, "error": "로그인이 필요합니다.", "login": True}, status=401)
    raise web.HTTPFound("/login")


def _activity_token_ok(request):
    want = os.environ.get("MP_ACTIVITY_TOKEN", "")
    got = request.headers.get("X-Activity-Token", "")
    return bool(want) and hmac.compare_digest(got.encode("utf-8"), want.encode("utf-8"))


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


def require_source(request, sid):
    """이 사용자에게 허락되지 않은 소스면 404 (있다는 것도 모르게)."""
    if not auth.can_source(request.get("user"), sid):
        raise web.HTTPNotFound(text="소스를 찾을 수 없습니다.")


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
    except (ConnectionError, asyncio.CancelledError):
        # 브라우저가 채널을 바꾸거나 탐색(seek)하면 연결을 끊는다 - 정상 상황
        pass
    return resp


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
# 관리자가 아닌 사용자에게는 서버 주소/M3U 주소(키가 들어 있기도 함)/API ID 등을 안 보여준다
_USER_VISIBLE = ("id", "type", "name", "enabled", "channels", "hidden_libraries", "stations")


async def get_config(request):
    pub = config_store.public_config(config_store.load())
    if not is_admin(request):
        user = request.get("user")
        out = []
        for s in pub["sources"]:
            if not auth.can_source(user, s["id"]):
                continue
            item = {**{k: s[k] for k in _USER_VISIBLE if k in s}, "has_epg": bool(s.get("epg"))}
            allowed = auth.allowed_libraries(user, s["id"])
            if s["type"] == "plex" and allowed is not None:
                item["allowed_libraries"] = sorted(allowed)
            if s["type"] == "telegram" and allowed is not None:
                item["channels"] = [c for c in item.get("channels") or [] if str(c["id"]) in allowed]
            if s["type"] == "radio" and allowed is not None:  # 라디오는 방송국 id
                item["stations"] = [st for st in item.get("stations") or [] if st["id"] in allowed]
            out.append(item)
        pub["sources"] = out
    return web.json_response(pub)


@admin_only
async def put_config(request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("설정 형식이 아닙니다.")
        cfg = config_store.replace_sources(config_store.load(), body.get("sources"))
    except ValueError as e:
        return _json_error(400, str(e))
    try:
        return await _save_config(request, cfg)
    except OSError as e:
        log.exception("설정 저장 실패")
        return _json_error(500, f"설정 파일을 쓰지 못했습니다 ({e.strerror or e}: {getattr(e, 'filename', '') or config_store.CONFIG_FILE}). "
                                "data 폴더의 권한(주인)을 확인해 주세요.")
    except Exception as e:  # 화면에 원인이 보이게 (빈 "HTTP 500" 대신)
        log.exception("설정 저장 실패")
        return _json_error(500, f"설정을 저장하지 못했습니다: {type(e).__name__}: {e}")


async def _save_config(request, cfg):
    old_ids = {s["id"] for s in config_store.load().get("sources", []) if s.get("type") == "telegram"}
    old_plex = {s["id"] for s in config_store.load().get("sources", []) if s.get("type") == "plex"}
    config_store.save(cfg)
    request.app[CHANNEL_CACHE].clear()
    request.app[EPG_CACHE].clear()
    # 지운 Plex 소스의 동영상 캐시는 지운다
    for sid in old_plex - {s["id"] for s in cfg["sources"] if s.get("type") == "plex"}:
        await asyncio.to_thread(media_cache.STORE.drop_prefix, f"plex:{sid}:")
    # 지운 텔레그램 소스는 로그아웃(텔레그램의 "활성 세션"에서도 빠지게)하고 색인·캐시도 지운다
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


def _source_or_404(source_id, stype, request=None):
    if request is not None:
        require_source(request, source_id)
    src = config_store.get_source(config_store.load(), source_id)
    if not src or src.get("type") != stype:
        raise web.HTTPNotFound(text="소스를 찾을 수 없습니다.")
    return src


# ---------------------------------------------------------------------------
# IPTV
# ---------------------------------------------------------------------------
async def _fetch_all(request, urls, timeout):
    """주소 여러 개를 동시에 받는다. 반환 [(주소, bytes 또는 예외)] (순서 유지)."""
    async def one(url):
        try:
            return url, await _fetch_bytes(request.app[HTTP], url, timeout=timeout)
        except Exception as e:  # 네트워크 오류 종류가 많아서 넓게 잡는다
            return url, e
    return await asyncio.gather(*(one(u) for u in urls))


def _fetch_error(e):
    if isinstance(e, web.HTTPException):
        return e.text or e.reason
    return f"{type(e).__name__}: {e}"


def _short_url(url):
    """오류 메시지에 쓸 주소 (apikey 같은 쿼리는 빼고)."""
    return url.split("?", 1)[0][-80:]


async def _load_m3u(request, src, timeout=20):
    """소스의 M3U 주소들을 받아 합친 채널 목록과 목록별 결과. 하나도 못 받으면 RuntimeError."""
    urls = config_store.url_list(src["m3u"])
    lists, report = [], []
    for url, got in await _fetch_all(request, urls, timeout):
        if isinstance(got, Exception):
            report.append({"url": _short_url(url), "ok": False, "error": _fetch_error(got)})
            continue
        text = iptv.decode_text(got)
        chans = iptv.parse_m3u(text, src["name"], src["id"])
        note = ""
        if not chans and text.lstrip()[:80].lower().startswith(("<!doctype", "<html")):
            note = "로그인 화면(HTML)이 온 것 같습니다. apikey를 확인하세요"
        lists.append(chans)
        report.append({"url": _short_url(url), "ok": bool(chans), "count": len(chans), "error": note or ("" if chans else "채널 0개")})
    if not lists:
        raise RuntimeError(" / ".join(r["error"] for r in report) or "M3U 주소가 없습니다")
    return iptv.merge_channels(lists), report


async def iptv_channels(request):
    src = _source_or_404(request.match_info["sid"], "iptv", request)
    cache = request.app[CHANNEL_CACHE]
    key = (src["id"], src["m3u"])
    hit = cache.get(key)
    if hit and time.time() - hit[0] < CHANNEL_CACHE_SEC and not request.query.get("refresh"):
        return web.json_response(hit[1])
    try:
        channels, report = await _load_m3u(request, src)
    except RuntimeError as e:
        return _json_error(502, f"M3U 받기 실패: {e}")
    errors = [f"{r['url']}: {r['error']}" for r in report if not r["ok"]]
    data = {"success": True, "channels": channels, "has_epg": bool(src.get("epg")), "lists": report, "errors": errors}
    cache[key] = (time.time(), data)
    return web.json_response(data)


async def iptv_epg(request):
    src = _source_or_404(request.match_info["sid"], "iptv", request)
    urls = config_store.url_list(src.get("epg"))
    if not urls:
        return web.json_response({"success": True, "names": {}, "icons": {}, "programmes": {}})
    cache = request.app[EPG_CACHE]
    key = (src["id"], src["epg"])
    hit = cache.get(key)
    if hit and time.time() - hit[0] < EPG_CACHE_SEC and not request.query.get("refresh"):
        return web.json_response(hit[1])
    now = int(time.time())
    parsed, errors = [], []
    for url, got in await _fetch_all(request, urls, 60):
        if isinstance(got, Exception):
            errors.append(f"{_short_url(url)}: {_fetch_error(got)}")
            continue
        try:
            # 큰 XML 파싱은 CPU를 오래 잡으니 이벤트 루프 밖에서
            parsed.append(await asyncio.get_running_loop().run_in_executor(
                None, iptv.parse_xmltv, got, now - EPG_PAST_SEC, now + EPG_FUTURE_SEC))
        except Exception as e:
            errors.append(f"{_short_url(url)}: {type(e).__name__}: {e}")
    if not parsed:
        return _json_error(502, f"EPG 받기 실패: {' / '.join(errors)}")
    data = {"success": True, **iptv.merge_epg(parsed), "errors": errors}
    cache[key] = (time.time(), data)
    return web.json_response(data)


# ---------------------------------------------------------------------------
# 인터넷 라디오 (지금 나오는 곡 + 앨범 표지). 소리는 브라우저가 스트림을 바로(막히면 /api/stream 중계로) 받는다
# ---------------------------------------------------------------------------
RADIO_NOW = web.AppKey("radio_now", dict)   # 스트림 주소 -> (시각, 곡 dict)
RADIO_ART = web.AppKey("radio_art", dict)   # "가수|제목" -> (시각, 표지 주소)
RADIO_NOW_SEC = 15
RADIO_ART_SEC, RADIO_ART_MISS_SEC = 3600, 600


async def _radio_icy(session, url):
    """스트림의 첫 곡 정보 블록만 읽는다."""
    headers = {"User-Agent": DEFAULT_UA, "Icy-MetaData": "1"}
    async with session.get(url, headers=headers, timeout=ClientTimeout(total=6, sock_read=5)) as resp:
        if resp.status >= 400:
            return ""
        metaint = int(resp.headers.get("icy-metaint") or 0)
        if not 0 < metaint <= radio.ICY_MAX_METAINT:
            return ""
        await resp.content.readexactly(metaint)
        size = (await resp.content.readexactly(1))[0] * 16
        return radio.parse_icy_block(await resp.content.readexactly(size)) if size else ""


async def _radio_art(app, artist, title):
    key = f"{artist}|{title}".lower()
    hit = app[RADIO_ART].get(key)
    if hit and time.time() - hit[0] < (RADIO_ART_SEC if hit[1] else RADIO_ART_MISS_SEC):
        return hit[1]
    art = ""
    try:
        params = {"term": f"{artist} {title}".strip(), "entity": "song", "limit": "8", "media": "music"}
        async with app[HTTP].get("https://itunes.apple.com/search", params=params,
                                 timeout=ClientTimeout(total=5)) as resp:
            if resp.status < 400:
                art = radio.pick_artwork(artist, title, (await resp.json(content_type=None)).get("results"))
    except Exception as e:
        log.info("라디오 앨범 표지 찾기 실패 (%s - %s): %s", artist, title, e)
        return ""
    if len(app[RADIO_ART]) > 2000:
        app[RADIO_ART].clear()
    app[RADIO_ART][key] = (time.time(), art)
    return art


async def _radio_now(app, station):
    cache = app[RADIO_NOW]
    hit = cache.get(station["url"])
    if hit and time.time() - hit[0] < RADIO_NOW_SEC:
        return hit[1]
    try:
        text = await _radio_icy(app[HTTP], station["url"])
    except Exception as e:  # 방송국마다 사정이 달라서 넓게 (ICY 200 같은 옛 응답, 끊김)
        log.debug("라디오 곡 정보 %s 실패: %s", station["name"], e)
        text = hit[1]["text"] if hit else ""
    now = radio.split_title(text)
    now["art"] = ""
    if not radio.looks_like_station_text(now, station) and now["title"]:
        now["art"] = await _radio_art(app, now["artist"], now["title"])
    if not hit or hit[1]["text"] != now["text"]:
        now["since"] = time.time()
    else:
        now["since"] = hit[1].get("since", time.time())
    cache[station["url"]] = (time.time(), now)
    return now


RADIO_NOW_MAX = 60   # 한 번에 곡 정보를 알아보는 방송국 수 (방송국이 많으면 화면에 보이는 것만 ?ids=로)


async def radio_now(request):
    """GET /api/radio/{sid}/now[?ids=a,b] -> 방송국마다 지금 나오는 곡 (15초 캐시).
    방송국이 수백 개일 수 있어서 ids(화면에 보이는 것·재생 중인 것)만, 없으면 앞에서 RADIO_NOW_MAX개만."""
    src = _source_or_404(request.match_info["sid"], "radio", request)
    stations = src.get("stations") or []
    allowed = auth.allowed_libraries(request.get("user"), src["id"])
    if allowed is not None:
        stations = [st for st in stations if st["id"] in allowed]
    ids = [x for x in request.query.get("ids", "").split(",") if x]
    if ids:
        want = set(ids)
        stations = [st for st in stations if st["id"] in want]
    stations = stations[:RADIO_NOW_MAX]
    nows = await asyncio.gather(*(_radio_now(request.app, st) for st in stations))
    return web.json_response({"success": True, "now": {st["id"]: n for st, n in zip(stations, nows)}})


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
        if upstream.status < 400 and iptv.looks_like_mpd(upstream.headers.get("Content-Type"), str(upstream.url), head):
            rest = await upstream.content.read(8 * 1024 * 1024)
            text = iptv.decode_text(head + rest)
            if "<MPD" in text:
                return web.Response(text=iptv.rewrite_mpd(text, str(upstream.url)), content_type="application/dash+xml",
                                    headers={"Cache-Control": "no-cache"})
            head = head + rest  # MPD가 아니었다 - 읽은 만큼 그대로 넘긴다

        resp = web.StreamResponse(status=upstream.status, headers=_resp_headers(upstream))
        await resp.prepare(request)
        try:
            if head:
                await resp.write(head)
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                await resp.write(chunk)
        except (ConnectionError, asyncio.CancelledError):
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
    require_source(request, src["id"])
    if not is_admin(request) and not _plex_allowed_for_user(request):
        return _json_error(403, "이 Plex 요청은 관리자만 할 수 있습니다.")
    allowed = auth.allowed_libraries(request.get("user"), src["id"])
    if allowed is not None:
        return await _plex_restricted(request, cfg, src, allowed)
    if _RE_PLEX_PART_FILE.match("/" + request.match_info["tail"]):
        cached = await _plex_part_cached(request, cfg, src)
        if cached is not None:
            return cached
    upstream = await _plex_upstream(request, cfg, src)
    if isinstance(upstream, web.Response):
        return upstream
    try:
        return await _pipe_response(request, upstream)
    finally:
        upstream.release()


_RE_PLEX_PART_FILE = re.compile(r"^/library/parts/\d+/\d+/[^/]+$")


def _plex_tail(request, src):
    raw_path = request.rel_url.raw_path
    prefix = f"/api/plex/{src['id']}/"
    return raw_path[len(prefix):] if raw_path.startswith(prefix) else request.match_info["tail"]


async def _plex_part_cached(request, cfg, src):
    """Plex 원본 파일(/library/parts/{id}/{시각}/파일)을 조각 캐시와 미리 받기를 거쳐 보낸다.
    경로의 {시각}은 파일이 바뀌면 달라지므로 그대로 캐시 키로 쓴다. Range를 못 하는 서버면 None (그냥 중계)."""
    if request.method not in ("GET", "HEAD") or not media_cache.STORE.enabled:
        return None
    tail = _plex_tail(request, src)
    url = f"{src['url']}/{tail}"
    headers = plex_headers(cfg, src.get("token"))
    ssl = None if src.get("verify_ssl", True) else False
    http = request.app[HTTP]
    key = f"plex:{src['id']}:{tail.split('?')[0]}"
    info = request.app[PLEX_FILES].get(key)
    if info is None:
        try:
            async with http.get(URL(url, encoded=True), headers={**headers, "Range": "bytes=0-0"}, ssl=ssl,
                                timeout=ClientTimeout(total=20)) as r:
                m = re.match(r"bytes \d+-\d+/(\d+)", r.headers.get("Content-Range", ""))
                if r.status != 206 or not m:
                    return None
                info = (int(m.group(1)), r.headers.get("Content-Type") or "application/octet-stream")
        except Exception as e:
            return _json_error(502, f"Plex 서버 연결 실패: {type(e).__name__}: {e}")
        request.app[PLEX_FILES][key] = info
    size, ctype = info

    async def fetch(bi):
        a = bi * media_cache.BLOCK
        b = min(size, a + media_cache.BLOCK) - 1
        async with http.get(URL(url, encoded=True), headers={**headers, "Range": f"bytes={a}-{b}"}, ssl=ssl,
                            timeout=ClientTimeout(total=120, sock_connect=10)) as r:
            if r.status != 206:
                raise IOError(f"Plex 조각 요청 HTTP {r.status}")
            data = await r.read()
        if len(data) != b - a + 1:
            raise IOError("Plex에서 조각을 다 받지 못했습니다.")
        return data

    st = media_cache.streamer(key, size, fetch, meta={"source": src["id"], "title": tail.rsplit("/", 1)[-1]},
                              concurrency=4)
    return await _range_response(request, size, ctype, st.iter_range, f"Plex 파일 {tail}")


async def _plex_upstream(request, cfg, src, accept=None):
    """요청을 그대로 Plex로 보내고 원본 응답을 돌려준다 (연결 실패면 502 응답). 호출한 쪽이 release."""
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
    if accept:
        headers["Accept"] = accept
    body = await request.read() if request.can_read_body else None
    try:
        return await request.app[HTTP].request(
            request.method, URL(url, encoded=True), headers=headers, data=body,
            ssl=None if src.get("verify_ssl", True) else False,
            timeout=ClientTimeout(total=None, sock_connect=10, sock_read=60))
    except Exception as e:
        return _json_error(502, f"Plex 서버 연결 실패: {type(e).__name__}: {e}")


# ---- 라이브러리 제한이 있는 사용자 ------------------------------------------------
# 허락된 라이브러리(섹션)의 것만 보이게 한다. 경로마다 정해 둔 것만 통과(나머지는 403):
#   - 섹션 목록/허브/검색/이어보기 응답은 JSON으로 받아 허락된 섹션 항목만 남긴다
#   - /library/metadata/{rk}, 이미지 변환, 트랜스코드 시작, timeline/scrobble 은 rk의 섹션을 확인
#   - 원본 파일(/library/parts/{id})과 자막(/library/streams/{id})은 이 사용자에게 보여준
#     항목 응답에서 본 id만 (id가 순서대로라 하나씩 넣어 보는 걸 막는다)
PLEX_SECTIONS = web.AppKey("plex_sections", dict)  # (소스id, "rk"|"part"|"stream", id) -> 섹션 key
_PLEX_SECTIONS_MAX = 200_000
_RE_PLEX_SECTION = re.compile(r"^/(?:library|hubs)/sections/([^/]+)(?:/|$)")
_RE_PLEX_META = re.compile(r"^/library/metadata/(\d+)(?:/|$|\?)")
_RE_PLEX_PART = re.compile(r"^/library/parts/(\d+)(?:/|$)")
_RE_PLEX_TRANSCODE = re.compile(r"^/(?:video|audio|music)/:/transcode/universal/")
_PLEX_FILTER_PATHS = {"/hubs", "/hubs/search", "/hubs/home/continueWatching", "/hubs/continueWatching",
                      "/hubs/home/onDeck", "/hubs/home/recentlyAdded", "/library/onDeck", "/library/recentlyAdded"}
_PLEX_PINGS = {"/:/timeline", "/:/scrobble", "/:/unscrobble"}


def _remember_sections(cache, sid, data):
    """Plex JSON 응답 안 항목들의 섹션을 기억하고, 허락 여부 판단 없이 그대로 둔다."""
    if len(cache) > _PLEX_SECTIONS_MAX:
        cache.clear()
    mc = data.get("MediaContainer") if isinstance(data, dict) else None
    if not isinstance(mc, dict):
        return
    for m in _plex_items(mc):
        sec = m.get("librarySectionID", mc.get("librarySectionID"))
        if sec is not None:
            _remember_item(cache, sid, m, str(sec))


def _plex_items(mc):
    for m in mc.get("Metadata") or []:
        if isinstance(m, dict):
            yield m
    for hub in mc.get("Hub") or []:
        for m in (hub.get("Metadata") or []) if isinstance(hub, dict) else []:
            if isinstance(m, dict):
                yield m


def _remember_item(cache, sid, m, sec):
    for k in ("ratingKey", "parentRatingKey", "grandparentRatingKey"):
        if m.get(k) is not None:
            cache[(sid, "rk", str(m[k]))] = sec
    for media in m.get("Media") or []:
        for part in media.get("Part") or []:
            if part.get("id") is not None:
                cache[(sid, "part", str(part["id"]))] = sec
            for st in part.get("Stream") or []:
                if st.get("id") is not None:
                    cache[(sid, "stream", str(st["id"]))] = sec
    deck = (m.get("OnDeck") or {}).get("Metadata")
    if isinstance(deck, dict):
        _remember_item(cache, sid, deck, sec)


def filter_plex_json(data, allowed, sections_list=False):
    """허락된 섹션 항목만 남긴다. 섹션을 알 수 없는 항목도 뺀다."""
    mc = data.get("MediaContainer") if isinstance(data, dict) else None
    if not isinstance(mc, dict):
        return data
    if sections_list:
        mc["Directory"] = [d for d in mc.get("Directory") or [] if str(d.get("key")) in allowed]
        mc["size"] = len(mc["Directory"])
        return data
    default = mc.get("librarySectionID")

    def ok(m):
        sec = m.get("librarySectionID", default)
        return sec is not None and str(sec) in allowed

    if "Metadata" in mc:
        mc["Metadata"] = [m for m in mc.get("Metadata") or [] if isinstance(m, dict) and ok(m)]
    if "Hub" in mc:
        hubs = []
        for hub in mc.get("Hub") or []:
            if not isinstance(hub, dict):
                continue
            if "Metadata" in hub:
                hub["Metadata"] = [m for m in hub.get("Metadata") or [] if isinstance(m, dict) and ok(m)]
                hub["size"] = len(hub["Metadata"])
                if not hub["Metadata"]:
                    continue
            elif str(hub.get("librarySectionID", "")) not in allowed:
                continue
            hubs.append(hub)
        mc["Hub"] = hubs
    return data


async def _plex_rk_section(request, cfg, src, rk):
    cache = request.app[PLEX_SECTIONS]
    hit = cache.get((src["id"], "rk", rk))
    if hit is not None:
        return hit
    try:
        async with request.app[HTTP].get(
                f"{src['url']}/library/metadata/{rk}",
                headers={**plex_headers(cfg, src.get("token")), "Accept": "application/json"},
                ssl=None if src.get("verify_ssl", True) else False, timeout=ClientTimeout(total=20)) as r:
            if r.status >= 400:
                return None
            data = await r.json(content_type=None)
    except Exception:
        return None
    _remember_sections(cache, src["id"], data)
    return cache.get((src["id"], "rk", rk))


def _rk_from(value):
    m = _RE_PLEX_META.match(value or "")
    if m:
        return m.group(1)
    return value if (value or "").isdigit() else None


async def _plex_restricted(request, cfg, src, allowed):
    sid = src["id"]
    path = "/" + request.match_info["tail"]
    q = request.query
    cache = request.app[PLEX_SECTIONS]
    denied = _json_error(403, "볼 수 없는 라이브러리입니다.")
    filtered, sections_list = False, False

    async def rk_ok(rk):
        return rk is not None and await _plex_rk_section(request, cfg, src, rk) in allowed

    if path == "/library/sections":
        filtered = sections_list = True
    elif _RE_PLEX_SECTION.match(path):
        if _RE_PLEX_SECTION.match(path).group(1) not in allowed:
            return denied
        filtered = True
    elif _RE_PLEX_META.match(path):
        if not await rk_ok(_RE_PLEX_META.match(path).group(1)):
            return denied
        filtered = True
    elif _RE_PLEX_PART.match(path):
        if cache.get((sid, "part", _RE_PLEX_PART.match(path).group(1))) not in allowed:
            return denied
        if _RE_PLEX_PART_FILE.match(path):
            cached = await _plex_part_cached(request, cfg, src)
            if cached is not None:
                return cached
    elif path in _PLEX_FILTER_PATHS:
        filtered = True
    elif path == "/photo/:/transcode":
        url = q.get("url", "")
        if not url.startswith(("http://", "https://")) and not await rk_ok(_rk_from(url)):
            return denied
    elif _RE_PLEX_TRANSCODE.match(path):
        # 시작/결정 요청에는 path=/library/metadata/{rk}, 그 뒤 세션 조각 요청은 추측할 수 없는 세션 id
        if "path" in q and not await rk_ok(_rk_from(q["path"])):
            return denied
    elif path in _PLEX_PINGS:
        if not await rk_ok(_rk_from(q.get("ratingKey") or q.get("key"))):
            return denied
    else:
        return denied

    upstream = await _plex_upstream(request, cfg, src, accept="application/json" if filtered else None)
    if isinstance(upstream, web.Response):
        return upstream
    try:
        if not filtered or upstream.status >= 400 or "json" not in upstream.headers.get("Content-Type", ""):
            return await _pipe_response(request, upstream)
        raw = await upstream.content.read(TEXT_MAX_BYTES)
        try:
            data = json.loads(raw)
        except ValueError:
            return _json_error(502, "Plex 응답을 읽을 수 없습니다.")
        _remember_sections(cache, sid, data)
        return web.json_response(filter_plex_json(data, allowed, sections_list),
                                 headers={"Cache-Control": "no-store"})
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
    require_source(request, src["id"])
    allowed = auth.allowed_libraries(request.get("user"), src["id"])
    stream_id = path[len("/library/streams/"):].split("/")[0].split(".")[0]
    if allowed is not None and request.app[PLEX_SECTIONS].get((src["id"], "stream", stream_id)) not in allowed:
        return _json_error(403, "볼 수 없는 라이브러리입니다.")
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
    require_source(request, src["id"])
    return _tg_source(request.app, src)


def _tg_source(app, src):
    pool = app[TELEGRAM]
    tg = pool.get(src["id"])
    if tg is None or not tg.matches(src):
        if tg is not None:
            asyncio.ensure_future(tg.close())
        tg = pool[src["id"]] = telegram_src.TelegramSource(src)
    tg.set_channels(src)
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


def _user_channels(request, tg):
    """설정에서 고른 채널 중 이 사용자가 볼 수 있는 것 (관리자/제한 없음이면 전부)."""
    allowed = auth.allowed_libraries(request.get("user"), tg.sid)
    return [c for c in tg.channels if allowed is None or str(c) in allowed]


@telegram_api
async def tg_videos(request, tg):
    channel = request.query.get("channel")
    mine = _user_channels(request, tg)
    if channel and channel.lstrip("-").isdigit():
        channels = [int(channel)] if int(channel) in mine else []
    else:
        channels = mine
    data = await tg.videos(channels, offset=_int_arg(request, "offset"), limit=_int_arg(request, "limit", 40, 1, 200),
                           q=request.query.get("q", "").strip(), sort=request.query.get("sort", "latest"),
                           user=username(request), kind=request.query.get("kind", ""),
                           group=request.query.get("group") == "1")
    return web.json_response({"success": True, **data})


@telegram_api
async def tg_random(request, tg):
    """랜덤 재생할 영상 하나. channel(없으면 볼 수 있는 채널 전부), unwatched=1, exclude="채널:메시지,..." """
    channel = request.query.get("channel", "")
    mine = _user_channels(request, tg)
    if channel.lstrip("-").isdigit():
        channels = [int(channel)] if int(channel) in mine else []
    else:
        channels = mine
    exclude = []
    for part in request.query.get("exclude", "").split(",")[:50]:
        c, _, m = part.partition(":")
        if c.lstrip("-").isdigit() and m.isdigit():
            exclude.append((int(c), int(m)))
    item = telegram_src.random_video(tg.sid, channels, user=username(request),
                                     unwatched=request.query.get("unwatched") == "1", exclude=exclude, kinds=tg.kinds)
    return web.json_response({"success": True, "item": item})


# 홈 채널 줄에 보일 것 (채널 종류마다). 정하지 않은 채널은 영상과 책 (사진은 홈에 안 보임)
_HOME_KIND = {"webtoon": "book,doc", "doc": "doc", "video": "video", "image": "video"}


@telegram_api
async def tg_home(request, tg):
    """홈 줄: 이어보기 + 채널마다 최신 영상 몇 개. 채널들을 동시에, 오래된 쪽 채우기 없이."""
    async def one(ch):
        try:
            data = await tg.videos([ch], limit=12, fill=False, user=username(request),
                                   kind=_HOME_KIND.get(tg.kinds.get(ch, ""), "video,doc"), group=True)
        except telegram_src.TgError:
            raise
        except Exception as e:
            log.warning("telegram 홈 %s 실패: %s", ch, e)
            return None
        return {"channel": ch, "items": data["items"]}

    mine = _user_channels(request, tg)
    rows = [r for r in await asyncio.gather(*(one(ch) for ch in mine)) if r]
    # 채널이 여럿이면 채널들을 합친 "새로 올라온 영상" 줄도 (사진만 보는 채널은 빼고)
    recent = []
    vid_channels = [c for c in mine if tg.kinds.get(c, "") not in ("image", "webtoon", "doc")]
    if len(vid_channels) > 1:
        recent = (await tg.videos(vid_channels, limit=30, fill=False, user=username(request), kind="video"))["items"]
    return web.json_response({"success": True,
                              "continue": telegram_src.continue_watching(tg.sid, mine, user=username(request)),
                              "channels": rows, "recent": recent})


def _ids(request, tg):
    channel, msg = int(request.match_info["channel"]), int(request.match_info["msg"])
    if channel not in _user_channels(request, tg):
        raise web.HTTPNotFound(text="설정에서 고르지 않은 채널입니다.")
    return channel, msg


@telegram_api
async def tg_video(request, tg):
    """?probe=1 이면 어떻게 재생할지(play: direct/remux/audio/transcode)도 같이."""
    channel, msg = _ids(request, tg)
    row = telegram_src.get_video(tg.sid, channel, msg, user=username(request))
    if not row:
        return _json_error(404, "색인에 없는 영상입니다.")
    row = {**row, "parts": telegram_src.video_parts(tg.sid, channel, msg, user=username(request)),
           "subs": telegram_src.video_subs(tg.sid, channel, msg),
           "series": telegram_src.series_of(tg.sid, channel, msg)}
    if request.query.get("probe"):
        await tg.authorized_client()
        row = {**row, "play": await _tg_decision(request, tg, channel, msg)}
    return web.json_response({"success": True, "item": row})


# ----- 작품 정보 (TMDB) -----
META_FRESH_SEC = 30 * 86400      # 찾은 정보는 30일 동안 그대로
META_MISS_SEC = 7 * 86400        # 못 찾았으면 7일 뒤 다시
SEASON_FRESH_SEC = 7 * 86400


def _tmdb_for(request, tg):
    src = config_store.get_source(config_store.load(), tg.sid) or {}
    return tmdb.Tmdb(request.app[HTTP], src.get("tmdb_key", ""))


async def _tg_meta(request, tg, target, refresh=False):
    """target(meta_target) -> 저장한 정보 또는 새로 찾은 정보. 키가 없으면 None"""
    api = _tmdb_for(request, tg)
    if not api.key or not target:
        return None
    hit = telegram_src.meta_get(tg.sid, target["mkey"])
    if hit and not refresh:
        age = time.time() - (hit["fetched"] or 0)
        if hit["manual"] or (hit["data"] and age < META_FRESH_SEC) or (not hit["data"] and age < META_MISS_SEC):
            return hit["data"]
    tmdb_id = hit["tmdb_id"] if hit and hit["manual"] else None
    tmdb_id = tmdb_id or await api.find(target["kind"], target["titles"], target["year"])
    data = await api.details(target["kind"], tmdb_id) if tmdb_id else None
    telegram_src.meta_put(tg.sid, target["mkey"], target["kind"], tmdb_id, data, manual=bool(hit and hit["manual"]))
    return data


async def _tg_seasons(request, tg, meta, seasons):
    """{시즌: {회차: 정보}} - TV 작품의 고른 시즌들"""
    out = {}
    if not meta or meta.get("kind") != "tv":
        return out
    api = _tmdb_for(request, tg)
    for s in sorted({int(x) for x in seasons if x is not None}):
        data, fetched = telegram_src.season_get(meta["id"], s)
        if data is None or time.time() - fetched > SEASON_FRESH_SEC:
            try:
                data = await api.season(meta["id"], s)
            except tmdb.TmdbError:
                data = data or {}
            telegram_src.season_put(meta["id"], s, data)
        out[str(s)] = data
    return out


@telegram_api
async def tg_meta(request, tg):
    """GET /api/tg/{sid}/meta/{channel}/{msg} (영상) 또는 /meta/{channel}?series=키 -> {meta, episodes}"""
    channel = int(request.match_info["channel"])
    if channel not in _user_channels(request, tg):
        return _json_error(404, "설정에서 고르지 않은 채널입니다.")
    msg = request.match_info.get("msg")
    target = telegram_src.meta_target(tg.sid, channel, msg=int(msg) if msg else None,
                                      series_key=None if msg else request.query.get("series", ""))
    # Plex -> 텔레그램 자동 올리기로 올린 영상이면 그때 붙여 둔 Plex 작품 정보 (TMDB 키가 없어도)
    plex = telegram_src.plex_meta_for(tg.sid, channel, msg=int(msg) if msg else None,
                                      series_key=None if msg else request.query.get("series", ""))
    if plex:
        meta, episodes = plex
        if target and target.get("season") is None and meta.get("kind") == "tv" and msg:   # 회차를 이름에서 못 읽었으면 Plex 값으로
            ep = next((e for eps in episodes.values() for e in eps.values()), None)
            if ep:
                target = {**target, "season": ep.get("season"), "ep": ep.get("ep")}
        return web.json_response({"success": True, "meta": meta, "episodes": episodes,
                                  "target": {k: target[k] for k in ("kind", "season", "ep")} if target else None})
    if not _tmdb_for(request, tg).key:
        return web.json_response({"success": True, "meta": None, "reason": "nokey"})
    try:
        meta = await _tg_meta(request, tg, target, refresh=request.query.get("refresh") == "1" and is_admin(request))
    except tmdb.TmdbError as e:
        return web.json_response({"success": True, "meta": None, "error": str(e)})
    except Exception as e:  # TMDB에 못 붙어도 화면은 나오게
        log.warning("TMDB 실패: %s", e)
        return web.json_response({"success": True, "meta": None, "error": f"{type(e).__name__}: {e}"})
    seasons = []
    if target and target.get("season") is not None:
        seasons = [target["season"]]
    elif msg is None and meta and meta.get("kind") == "tv":
        seasons = [s for s in request.query.get("seasons", "").split(",") if s.isdigit()]
    episodes = await _tg_seasons(request, tg, meta, seasons) if meta else {}
    return web.json_response({"success": True, "meta": meta, "episodes": episodes,
                              "target": {k: target[k] for k in ("kind", "season", "ep")} if target else None})


async def tg_plex_art(request):
    """자동 올리기가 받아 둔 Plex 포스터·배경·스틸 (data/telegram_thumbs/{소스}/plex/)"""
    sid, name = request.match_info["sid"], request.match_info["name"]
    src = config_store.get_source(config_store.load(), sid)
    if not src or src.get("type") != "telegram":
        raise web.HTTPNotFound()
    require_source(request, sid)
    path = os.path.join(config_store.DATA_DIR, "telegram_thumbs", sid, "plex", name)
    if not os.path.isfile(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=604800", "Content-Type": "image/jpeg"})


@telegram_api
async def tg_meta_search(request, tg):
    """[정보 고치기] 검색: ?kind=movie|tv&q=이름&year= (관리자)"""
    if not is_admin(request):
        return _json_error(403, "관리자만 할 수 있습니다.")
    kind = request.query.get("kind") if request.query.get("kind") in ("movie", "tv") else "movie"
    try:
        res = await _tmdb_for(request, tg).search(kind, request.query.get("q", "").strip(), request.query.get("year", ""))
    except tmdb.TmdbError as e:
        return _json_error(400, str(e))
    return web.json_response({"success": True, "results": [{k: v for k, v in r.items() if k != "_raw"} for r in res[:20]]})


@telegram_api
async def tg_meta_fix(request, tg):
    """[정보 고치기] 고른 작품으로 정하기 (관리자). {channel, msg | series, kind, tmdb_id(0이면 정보 없음)}"""
    if not is_admin(request):
        return _json_error(403, "관리자만 할 수 있습니다.")
    body = await _json_body(request)
    channel = int(body.get("channel") or 0)
    if channel not in _user_channels(request, tg):
        return _json_error(404, "설정에서 고르지 않은 채널입니다.")
    target = telegram_src.meta_target(tg.sid, channel, msg=int(body["msg"]) if body.get("msg") else None,
                                      series_key=None if body.get("msg") else str(body.get("series") or ""))
    if not target:
        return _json_error(404, "영상을 찾을 수 없습니다.")
    kind = body.get("kind") if body.get("kind") in ("movie", "tv") else target["kind"]
    tmdb_id = int(body.get("tmdb_id") or 0) or None
    try:
        data = await _tmdb_for(request, tg).details(kind, tmdb_id) if tmdb_id else None
    except tmdb.TmdbError as e:
        return _json_error(400, str(e))
    telegram_src.meta_put(tg.sid, target["mkey"], kind, tmdb_id, data, manual=True)
    return web.json_response({"success": True, "meta": data})


@telegram_api
async def tg_series(request, tg):
    """시리즈 회차 목록 GET /api/tg/{sid}/series/{channel}?key=작품이름키"""
    channel = int(request.match_info["channel"])
    if channel not in _user_channels(request, tg):
        return _json_error(404, "설정에서 고르지 않은 채널입니다.")
    data = telegram_src.series_episodes(tg.sid, channel, request.query.get("key", ""), user=username(request))
    if not data:
        return _json_error(404, "시리즈를 찾을 수 없습니다.")
    # 작품 정보 파일(웹툰명.yaml)이 채널에 있으면 같이 (작아서 한 번 받아 두고 다시 쓴다)
    meta = None
    mmsg = telegram_src.series_meta_file(tg.sid, channel, data["key"])
    if mmsg:
        try:
            meta = await tg.file_meta(channel, mmsg)
        except Exception as e:
            log.info("telegram 작품 정보 파일 %s/%s 읽기 실패: %s", channel, mmsg, e)
            meta = {"error": str(e)}
    return web.json_response({"success": True, **data, "file_meta": meta})


@telegram_api
async def tg_series_cover(request, tg):
    """작품 카드/상세 그림 GET /api/tg/{sid}/series-cover/{channel}?key=작품키 - 작품 정보 파일의 표지 (서버가 한 번 받아 둠).
    없으면 그 작품 회차의 썸네일로"""
    channel = int(request.match_info["channel"])
    if channel not in _user_channels(request, tg):
        return _json_error(404, "설정에서 고르지 않은 채널입니다.")
    key = request.query.get("key", "")
    path = await tg.series_cover(request.app[HTTP], channel, key)
    if not path:
        eps = (telegram_src.series_episodes(tg.sid, channel, key) or {}).get("episodes") or []
        for ep in reversed(eps):
            if ep.get("has_thumb"):
                try:
                    path = await tg.thumbnail(channel, ep["msg"])
                    break
                except telegram_src.TgError:
                    continue
    if not path:
        return _json_error(404, "표지가 없습니다.")
    with open(path, "rb") as f:
        head = f.read(16)
    ctype = "image/png" if head.startswith(b"\x89PNG") else "image/webp" if head[8:12] == b"WEBP" else \
        "image/gif" if head.startswith(b"GIF") else "image/jpeg"
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400", "Content-Type": ctype,
                                           "X-Content-Type-Options": "nosniff"})


@telegram_api
async def tg_subtitle(request, tg):
    """영상 옆에 올린 자막 파일 -> WebVTT (<track>용). ?shift=초 - 서버 변환 스트림처럼 그 위치가 0초인 경우"""
    channel, msg = _ids(request, tg)
    row = telegram_src.get_video(tg.sid, channel, msg)
    if not row or row.get("kind") != "subtitle":
        return _json_error(404, "색인에 없는 자막입니다.")
    try:
        data = await tg.subtitle_bytes(channel, msg)
    except telegram_src.TgError as e:
        return _json_error(404, str(e))
    ext = os.path.splitext(row.get("file_name") or "")[1].lstrip(".").lower()
    try:
        shift = max(0.0, float(request.query.get("shift") or 0))
    except ValueError:
        shift = 0.0
    vtt = subtitles.shift_vtt(subtitles.to_vtt(data, ext), shift)
    return web.Response(text=vtt, content_type="text/vtt", charset="utf-8", headers={"Cache-Control": "private, max-age=3600"})


@telegram_api
async def tg_stream(request, tg):
    channel, msg = _ids(request, tg)
    return await _tg_range_response(request, tg, channel, msg)


async def _tg_range_response(request, tg, channel, msg):
    row = telegram_src.get_video(tg.sid, channel, msg)
    if not row or not row["size"]:
        return _json_error(404, "색인에 없는 영상입니다.")
    await tg.authorized_client()  # 로그인 안 됐으면 응답을 시작하기 전에 JSON 오류로
    size = int(row["size"])
    return await _range_response(
        request, size, telegram_src.browser_mime(row["mime"]),
        lambda start, length: tg.iter_cached(channel, msg, start, length, size, duration=row.get("duration") or 0),
        f"telegram 스트림 {channel}/{msg}")


async def _range_response(request, size, content_type, open_range, label):
    """Range 요청 하나에 답한다. open_range(start, length)는 바이트 조각을 내는 async iterator."""
    try:
        start, end, partial = telegram_src.parse_range(request.headers.get("Range"), size)
    except ValueError:
        return web.Response(status=416, headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})
    length = end - start + 1
    headers = {"Content-Type": content_type, "Accept-Ranges": "bytes",
               "Content-Length": str(length), "Cache-Control": "no-store"}
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    resp = web.StreamResponse(status=206 if partial else 200, headers=headers)
    await resp.prepare(request)
    if request.method == "HEAD":
        return resp
    # Safari(아이폰/아이패드)는 Chrome과 달리 작은 구간을 여러 번 나눠 요청한다. 그쪽 재생 문제를 볼 수 있게
    # Safari 요청만 구간/첫 바이트까지 걸린 시간/보낸 양을 남긴다.
    ua = request.headers.get("User-Agent", "")
    trace = "Safari" in ua and "Chrome" not in ua and "CriOS" not in ua
    t0, ttfb, sent = time.monotonic(), None, 0
    try:
        async for chunk in open_range(start, length):
            if ttfb is None:
                ttfb = time.monotonic() - t0
            await resp.write(chunk)
            sent += len(chunk)
    except (ConnectionError, asyncio.CancelledError):
        pass  # 브라우저가 탐색하거나 닫으면 연결을 끊는다 - 정상
    except Exception as e:
        log.warning("%s 중단: %s", label, e)
    finally:
        if trace:
            log.info("Safari %s: %s-%s/%s 첫 바이트 %.2f초, %d/%d바이트 %.2f초", label, start, end, size,
                     ttfb if ttfb is not None else -1, sent, length, time.monotonic() - t0)
    return resp


# ---- 서버 변환 (ffmpeg) -----------------------------------------------------------
# ffmpeg/ffprobe는 이 서버의 내부 주소(/internal/tg/{토큰})로 텔레그램 파일을 Range로 읽는다.
# 토큰은 추측할 수 없는 값이고 변환이 끝나면(또는 몇 시간 뒤) 지운다. 로그인 없이 열리는 유일한 영상 주소.
_INTERNAL_TTL = 6 * 3600


def _internal_url(request, tg, channel, msg):
    now = time.time()
    pool = request.app[TG_INTERNAL]
    for k in [k for k, v in pool.items() if v[3] < now]:
        pool.pop(k, None)
    token = secrets.token_urlsafe(24)
    pool[token] = (tg.sid, channel, msg, now + _INTERNAL_TTL)
    # 브라우저가 들어온 바로 그 주소/포트로 (MP_HOST를 LAN 주소 하나로 열어도 맞게)
    host, port = request.transport.get_extra_info("sockname")[:2]
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    host = f"[{host}]" if ":" in host else host
    return token, f"http://{host}:{port}/internal/tg/{token}"


async def internal_tg(request):
    item = request.app[TG_INTERNAL].get(request.match_info["token"])
    if not item or item[3] < time.time():
        return web.Response(status=404)
    sid, channel, msg, _ = item
    tg = request.app[TELEGRAM].get(sid)
    if tg is None:
        return web.Response(status=404)
    return await _tg_range_response(request, tg, channel, msg)


async def _tg_decision(request, tg, channel, msg):
    """ffprobe로 이 영상을 어떻게 보낼지. 결과는 DB에 남겨 두고(파일은 안 바뀜), 같은 영상을 동시에
    물어보면(상세 화면이 미리 물어본 직후 [재생]) 돌고 있는 것을 같이 기다린다. ffmpeg가 없으면 원본 그대로."""
    if not transcode.available():
        return {"mode": "direct", "available": False, "reason": "서버에 ffmpeg가 없습니다"}
    saved = telegram_src.get_probe(tg.sid, channel, msg)
    if saved:
        return saved
    key = (tg.sid, channel, msg)
    running = request.app[PROBES].get(key)
    if running is None:
        # 내부 주소는 지금 만든다 (미리 확인 요청은 응답이 먼저 끝나서 나중엔 연결 정보가 없다)
        token, url = _internal_url(request, tg, channel, msg)
        app = request.app
        running = app[PROBES][key] = asyncio.ensure_future(_tg_probe(app, tg, channel, msg, token, url))
        running.add_done_callback(lambda _f: app[PROBES].pop(key, None))
    # 기다리던 브라우저가 떠나도 판단은 끝까지 해서 남겨 둔다
    return await asyncio.shield(running)


async def _tg_probe(app, tg, channel, msg, token, url):
    t0 = time.monotonic()
    try:
        info = await transcode.probe(url)
        result = {**transcode.decide(info), "available": True}
    except Exception as e:
        log.warning("telegram %s/%s ffprobe 실패: %s", channel, msg, e)
        return {"mode": "direct", "available": True, "reason": f"형식을 알아내지 못했습니다: {e}"}
    finally:
        app[TG_INTERNAL].pop(token, None)
    log.info("telegram %s/%s 형식 확인 %.1f초: %s (%s/%s/%s, 음성 트랙 %s개, 음성 시작 차이 %+.3f초)", channel, msg,
             time.monotonic() - t0, result["mode"], result.get("container"), result.get("video"), result.get("audio"),
             result.get("audio_tracks", "?"), result.get("av_offset", 0.0))
    telegram_src.save_probe(tg.sid, channel, msg, result)
    return result


@telegram_api
async def tg_probe(request, tg):
    """미리 형식 확인 (상세 화면을 열거나 재생 버튼에 마우스를 올리면). 결과는 기억만 하고 바로 돌려준다.
    ?head=1 이면 앞부분 몇 MB도 받아 둔다 (재생 중인 영상이 끝나 갈 때 다음 회차/조각을)."""
    channel, msg = _ids(request, tg)
    row = telegram_src.get_video(tg.sid, channel, msg)
    if not row or row.get("kind") != "video":
        return _json_error(404, "색인에 없는 영상입니다.")
    need_probe = transcode.available() and not telegram_src.get_probe(tg.sid, channel, msg)
    if need_probe or request.query.get("head"):
        await tg.authorized_client()
    if need_probe:
        asyncio.ensure_future(_tg_decision(request, tg, channel, msg))
    if request.query.get("head"):
        async def head():
            try:
                await tg.warm_head(channel, msg, int(row["size"]), row.get("duration") or 0)
            except Exception as e:  # 미리 받기라 실패해도 재생 때 다시 받으면 된다
                log.info("telegram %s/%s 앞부분 미리 받기 실패: %s", channel, msg, e)
        asyncio.ensure_future(head())
    return web.json_response({"success": True})


# ---- Safari용 서버 변환 (HLS) ------------------------------------------------------
# Safari(아이폰/아이패드)는 길이를 모르는 스트림(tg_transcode)을 <video>로 열지 못하고, MP4 안의 MP3 소리는
# 처음 몇 초를 못 낸다. 그래서 Safari에서는 같은 ffmpeg 변환을 HLS(fMP4 조각 + 재생목록)로 data/hls_tmp에 쓰고
# 그 파일을 내준다. 탐색은 fMP4 스트림처럼 재생기가 ?start=초 로 새 변환을 시작한다.
HLS_IDLE_SEC = 120
_HLS_NAME_RE = re.compile(r"^(index\.m3u8|init\.mp4|seg\d{5}\.m4s)$")


class HlsJob:
    def __init__(self, job_id, owner, proc, folder, token, label):
        self.id, self.owner, self.proc, self.dir, self.token, self.label = job_id, owner, proc, folder, token, label
        self.last = time.monotonic()
        self.errors = []
        self.err_task = None

    def playlist(self):
        try:
            with open(os.path.join(self.dir, "index.m3u8"), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""


def _hls_root():
    return os.path.join(config_store.DATA_DIR, "hls_tmp")


async def _hls_stop(app, job):
    app[HLS_JOBS].pop(job.id, None)
    if job.proc.returncode is None:
        job.proc.kill()
    await job.proc.wait()
    if job.err_task:
        job.err_task.cancel()
    app[TG_INTERNAL].pop(job.token, None)
    jobs = app[TRANSCODES]
    for k, v in list(jobs.items()):
        if v is job.proc:
            jobs.pop(k, None)
    await asyncio.to_thread(shutil.rmtree, job.dir, True)


async def _hls_sweeper(app):
    """아무도 안 받아 가는 HLS 변환(창을 닫았거나 다른 영상으로 감)을 끄고 파일을 지운다."""
    while True:
        await asyncio.sleep(30)
        now = time.monotonic()
        for job in list(app[HLS_JOBS].values()):
            if now - job.last > HLS_IDLE_SEC:
                log.info("%s HLS 변환 정리 (%d초 동안 요청 없음)", job.label, HLS_IDLE_SEC)
                await _hls_stop(app, job)


@telegram_api
async def tg_hls_start(request, tg):
    """POST /api/tg/{sid}/hls/{c}/{m}?start=초&mode=remux|audio|transcode -> {"url": 재생목록 주소}."""
    channel, msg = _ids(request, tg)
    if not transcode.available():
        return _json_error(501, "서버에 ffmpeg가 없습니다. sudo apt install ffmpeg 후 다시 시작하세요.")
    if not telegram_src.get_video(tg.sid, channel, msg):
        return _json_error(404, "색인에 없는 영상입니다.")
    mode = request.query.get("mode") or (await _tg_decision(request, tg, channel, msg))["mode"]
    if mode == "direct":
        mode = "remux"
    try:
        start = max(0.0, float(request.query.get("start") or 0))
    except ValueError:
        start = 0.0
    await tg.authorized_client()

    app, me = request.app, username(request) or "-"
    # 한 사람이 보는 영상은 하나 - 이전 변환(스트림이든 HLS든)은 끈다
    old = app[TRANSCODES].pop(me, None)
    if old is not None and old.returncode is None:
        old.kill()
    for job in [j for j in app[HLS_JOBS].values() if j.owner == me]:
        await _hls_stop(app, job)
    if sum(p.returncode is None for p in app[TRANSCODES].values()) >= transcode.max_jobs():
        return _json_error(503, "서버 변환이 이미 여러 개 돌고 있습니다. 잠시 뒤에 다시 시도하세요.")

    job_id = secrets.token_urlsafe(12)
    folder = os.path.join(_hls_root(), job_id)
    os.makedirs(folder, exist_ok=True)
    token, url = _internal_url(request, tg, channel, msg)
    try:
        args = transcode.build_hls_args(url, mode, start, folder)
    except ValueError as e:
        app[TG_INTERNAL].pop(token, None)
        await asyncio.to_thread(shutil.rmtree, folder, True)
        return _json_error(400, str(e))
    proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    label = f"telegram {channel}/{msg}"
    job = HlsJob(job_id, me, proc, folder, token, label)
    app[TRANSCODES][me] = proc
    app[HLS_JOBS][job_id] = job

    async def read_err():
        async for line in proc.stderr:
            job.errors.append(line.decode("utf-8", "replace").strip())
            del job.errors[:-5]
    job.err_task = asyncio.ensure_future(read_err())
    log.info("%s HLS 변환 시작 (%s, %.0f초부터, %s)", label, mode, start, me)

    # 첫 조각이 나올 때까지 기다렸다가 주소를 준다 (재생기가 빈 재생목록을 받고 실패하지 않게)
    deadline = time.monotonic() + 45
    while "#EXTINF" not in job.playlist():
        if proc.returncode is not None or time.monotonic() > deadline:
            reason = job.errors[-1] if job.errors else (
                f"ffmpeg 종료 코드 {proc.returncode}" if proc.returncode is not None else "첫 조각을 만들지 못했습니다 (시간 초과)")
            log.warning("%s HLS 변환 실패: %s", label, reason)
            await _hls_stop(app, job)
            return _json_error(502, f"서버 변환 실패: {reason}")
        await asyncio.sleep(0.2)
    job.last = time.monotonic()
    return web.json_response({"success": True, "mode": mode, "url": f"/api/tg-hls/{job_id}/index.m3u8"})


async def tg_hls_file(request):
    """HLS 변환의 재생목록/조각. 변환을 시작한 사람만 받을 수 있다."""
    job = request.app[HLS_JOBS].get(request.match_info["job"])
    name = request.match_info["name"]
    if not job or not _HLS_NAME_RE.match(name) or (request.app[REQUIRE_AUTH] and job.owner != (username(request) or "-")):
        return web.Response(status=404)
    job.last = time.monotonic()
    if name == "index.m3u8":
        text = job.playlist()
        # 계속 늘어나는(EVENT) 재생목록이라도 맨 앞부터 재생하게
        if "#EXT-X-START" not in text:
            text = text.replace("#EXTM3U\n", "#EXTM3U\n#EXT-X-START:TIME-OFFSET=0,PRECISE=YES\n", 1)
        return web.Response(text=text, content_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store"})
    # 다 쓰인 조각만 (재생목록에 올라간 것). 아직이면 조금 기다린다
    for _ in range(100):
        if name in job.playlist() or job.proc.returncode is not None:
            break
        await asyncio.sleep(0.15)
    path = os.path.join(job.dir, name)
    if not os.path.exists(path):
        return web.Response(status=404)
    return web.FileResponse(path, headers={"Content-Type": "video/mp4" if name.endswith(".mp4") else "video/iso.segment",
                                           "Cache-Control": "no-store"})


@telegram_api
async def tg_transcode(request, tg):
    """GET /api/tg/{sid}/transcode/{c}/{m}?start=초&mode=remux|audio|transcode -> 조각난 MP4 스트림."""
    channel, msg = _ids(request, tg)
    if not transcode.available():
        return _json_error(501, "서버에 ffmpeg가 없습니다. sudo apt install ffmpeg 후 다시 시작하세요.")
    if not telegram_src.get_video(tg.sid, channel, msg):
        return _json_error(404, "색인에 없는 영상입니다.")
    mode = request.query.get("mode") or (await _tg_decision(request, tg, channel, msg))["mode"]
    if mode == "direct":
        mode = "remux"
    try:
        start = max(0.0, float(request.query.get("start") or 0))
    except ValueError:
        start = 0.0
    await tg.authorized_client()

    jobs = request.app[TRANSCODES]
    me = username(request) or "-"
    old = jobs.pop(me, None)  # 한 사람이 보는 영상은 하나 - 탐색하면 이전 변환은 끈다
    if old is not None and old.returncode is None:
        old.kill()
    if sum(p.returncode is None for p in jobs.values()) >= transcode.max_jobs():
        return _json_error(503, "서버 변환이 이미 여러 개 돌고 있습니다. 잠시 뒤에 다시 시도하세요.")

    token, url = _internal_url(request, tg, channel, msg)
    try:
        args = transcode.build_args(url, mode, start)
    except ValueError as e:
        request.app[TG_INTERNAL].pop(token, None)
        return _json_error(400, str(e))
    proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    jobs[me] = proc
    log.info("telegram %s/%s 서버 변환 시작 (%s, %.0f초부터, %s)", channel, msg, mode, start, me)
    errors = []

    async def read_err():
        async for line in proc.stderr:
            errors.append(line.decode("utf-8", "replace").strip())
            del errors[:-5]
    err_task = asyncio.ensure_future(read_err())

    resp = web.StreamResponse(status=200, headers={"Content-Type": "video/mp4", "Cache-Control": "no-store",
                                                   "X-Transcode-Mode": mode})
    try:
        first = await proc.stdout.read(64 * 1024)
        if not first:
            await proc.wait()
            await err_task
            msg_ = errors[-1] if errors else f"ffmpeg 종료 코드 {proc.returncode}"
            log.warning("telegram %s/%s 서버 변환 실패: %s", channel, msg, msg_)
            return _json_error(502, f"서버 변환 실패: {msg_}")
        await resp.prepare(request)
        await resp.write(first)
        while True:
            chunk = await proc.stdout.read(256 * 1024)
            if not chunk:
                break
            await resp.write(chunk)
    except (ConnectionError, asyncio.CancelledError):
        pass  # 브라우저가 탐색하거나 닫았다
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        err_task.cancel()
        request.app[TG_INTERNAL].pop(token, None)
        if jobs.get(me) is proc:
            jobs.pop(me, None)
        if proc.returncode not in (0, -9) and errors:
            log.warning("telegram %s/%s 서버 변환 중단: %s", channel, msg, errors[-1])
    return resp


@telegram_api
async def tg_thumb(request, tg):
    channel, msg = _ids(request, tg)
    try:
        path = await tg.thumbnail(channel, msg)
    except telegram_src.TgError as e:
        return _json_error(404, str(e))
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400", "Content-Type": "image/jpeg"})


_IMAGE_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
                ".bmp": "image/bmp", ".avif": "image/avif", ".heic": "image/heic", ".heif": "image/heif"}


@telegram_api
async def tg_image(request, tg):
    """사진 원본 (뷰어). 처음 한 번 텔레그램에서 받아 data/telegram_images에 둔다."""
    channel, msg = _ids(request, tg)
    row = telegram_src.get_video(tg.sid, channel, msg)
    if not row or row.get("kind") != "image":
        return _json_error(404, "색인에 없는 사진입니다.")
    try:
        path = await tg.image(channel, msg, row)
    except telegram_src.TgError as e:
        return _json_error(404, str(e))
    ctype = _IMAGE_TYPES.get(os.path.splitext(path)[1], "image/jpeg")
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400", "Content-Type": ctype,
                                           "X-Content-Type-Options": "nosniff"})


@telegram_api
async def tg_book(request, tg):
    """웹툰 압축 파일 GET /api/tg/{sid}/book/{c}/{m} -> {ready, pages: [이름...], item, series}.
    파일을 통째로 받지 않고 목차(파일 맨 끝)만 읽어 바로 답한다. ?warm=1 이면 목차와 앞쪽 몇 장을 뒤에서 미리 받기만 (다음 화)"""
    channel, msg = _ids(request, tg)
    row = telegram_src.get_video(tg.sid, channel, msg, user=username(request))
    if not row or row.get("kind") != "book":
        return _json_error(404, "색인에 없는 웹툰 파일입니다.")
    if request.query.get("warm"):
        await tg.authorized_client()
        asyncio.ensure_future(tg.book_warm(channel, msg, row))
        return web.json_response({"success": True})
    try:
        _zf, pages = await tg.book_open(channel, msg, row)
    except telegram_src.TgError as e:
        return _json_error(502, str(e))
    return web.json_response({"success": True, "ready": True, "pages": [name for _info, name in pages], "item": row,
                              "series": telegram_src.series_of(tg.sid, channel, msg)})


@telegram_api
async def tg_book_page(request, tg):
    """웹툰 한 쪽 GET /api/tg/{sid}/book/{c}/{m}/{n} (n은 0부터) - 그 그림 부분만 텔레그램에서 받아 푼다"""
    channel, msg = _ids(request, tg)
    row = telegram_src.get_video(tg.sid, channel, msg)
    if not row or row.get("kind") != "book":
        return _json_error(404, "색인에 없는 웹툰 파일입니다.")
    try:
        data, ctype = await tg.book_page(channel, msg, row, int(request.match_info["n"]))
    except telegram_src.TgError as e:
        return _json_error(404, str(e))
    return web.Response(body=data, headers={"Content-Type": ctype, "Cache-Control": "private, max-age=604800, immutable",
                                            "X-Content-Type-Options": "nosniff"})


@telegram_api
async def tg_progress(request, tg):
    body = await _json_body(request)
    try:
        channel, msg = int(body.get("channel")), int(body.get("msg"))
    except (TypeError, ValueError):
        return _json_error(400, "channel/msg가 없습니다.")
    if channel not in _user_channels(request, tg):
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
    return _render_page("login.html", request.app[STATIC_TOKEN])


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


async def prefs_get(request):
    return web.json_response({"success": True, "prefs": prefs.get(username(request))})


async def prefs_put(request):
    try:
        mine = prefs.update(username(request), await _json_body(request))
    except ValueError as e:
        return _json_error(400, str(e))
    return web.json_response({"success": True, "prefs": mine})


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
                                role=body.get("role"), acting=username(request),
                                access=body["access"] if "access" in body else False)
    except auth.AuthError as e:
        return _json_error(400, str(e))
    return web.json_response({"success": True, "user": user})


@admin_only
async def users_delete(request):
    try:
        auth.delete_user(request.match_info["name"], acting=username(request))
    except auth.AuthError as e:
        return _json_error(400, str(e))
    prefs.forget(request.match_info["name"])
    mcp.forget_user(request.match_info["name"])
    return web.json_response({"success": True})


# ---------------------------------------------------------------------------
# MCP - AI 앱(Claude 등)이 쓰는 도구 (mcp.py가 JSON-RPC와 토큰을 맡고, 여기는 도구가 하는 일)
# 토큰 주인(사용자)이 볼 수 있는 소스·라이브러리만. 읽기만 한다 (재생 기록을 바꾸거나 설정을 건드리지 않음).
# ---------------------------------------------------------------------------
MCP_LIMIT_MAX = 50


def _public_base(request):
    """화면 주소 (리버스 프록시 뒤면 X-Forwarded-* 를 따름)"""
    proto = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip() or "http"
    host = request.headers.get("X-Forwarded-Host", request.host).split(",")[0].strip()
    return f"{proto}://{host}"


def _item_url(base, sid, item_id):
    return f"{base}/#/item/{quote(str(sid), safe='')}/{quote(str(item_id), safe=':')}"


def _mcp_limit(args, default=10):
    try:
        return max(1, min(MCP_LIMIT_MAX, int(args.get("limit") or default)))
    except (TypeError, ValueError):
        return default


def _mcp_sources(user, stype=None):
    return [s for s in config_store.load()["sources"]
            if auth.can_source(user, s["id"]) and (stype is None or s["type"] == stype)]


def _mcp_tg_channels(user, src):
    allowed = auth.allowed_libraries(user, src["id"])
    return [c for c in src.get("channels") or [] if allowed is None or str(c["id"]) in allowed]


def _mcp_tg_item(base, src, row):
    titles = {int(c["id"]): c.get("title", "") for c in src.get("channels") or []}
    kind = {"book": "webtoon", "image": "photo", "doc": "ebook"}.get(row.get("kind"), "video")
    out = {"source": src["name"], "channel": titles.get(row["channel"], ""), "date": (row.get("date") or "")[:10]}
    s = row.get("series")
    if s:
        out.update(type=f"{kind}_series", title=s["title"], episodes=s["count"], unwatched=s["unwatched"],
                   url=_item_url(base, src["id"], f"series:{row['channel']}:{s['key']}"))
        return out
    out.update(type=kind, title=row.get("title", ""), url=_item_url(base, src["id"], f"{row['channel']}:{row['msg']}"))
    if row.get("duration"):
        if kind == "webtoon":
            out["pages"] = int(row["duration"])
        else:
            out["minutes"] = round(row["duration"] / 60)
        if row.get("position"):
            out["progress"] = round(min(1.0, row["position"] / row["duration"]), 2)
    if row.get("watched"):
        out["watched"] = True
    return out


async def _plex_json(app, cfg, src, path, params=None):
    hdrs = {**plex_headers(cfg, src.get("token")), "Accept": "application/json"}
    async with app[HTTP].get(f"{src['url']}{path}", params=params or {}, headers=hdrs,
                             ssl=None if src.get("verify_ssl", True) else False, timeout=ClientTimeout(total=15)) as r:
        if r.status >= 400:
            raise RuntimeError(f"Plex HTTP {r.status}")
        return await r.json(content_type=None)


def _plex_visible(data, user, src):
    """허락된 섹션 + 숨기지 않은 섹션의 항목만 [Metadata...]"""
    allowed = auth.allowed_libraries(user, src["id"])
    if allowed is not None:
        data = filter_plex_json(data, allowed)
    mc = (data or {}).get("MediaContainer") or {}
    items = list(mc.get("Metadata") or [])
    for hub in mc.get("Hub") or []:
        items += hub.get("Metadata") or []
    hidden = set(map(str, src.get("hidden_libraries") or []))
    default = mc.get("librarySectionID")
    return [m for m in items if isinstance(m, dict) and str(m.get("librarySectionID", default)) not in hidden]


def _mcp_plex_item(base, src, m):
    kind = m.get("type", "")
    title = m.get("title", "")
    if kind == "episode":
        title = f"{m.get('grandparentTitle', '')} S{m.get('parentIndex', '?')}E{m.get('index', '?')} {title}".strip()
    elif kind == "season":
        title = f"{m.get('parentTitle', '')} {title}".strip()
    elif kind in ("track", "album"):
        title = f"{m.get('grandparentTitle') or m.get('parentTitle', '')} - {title}".strip(" -")
    out = {"source": src["name"], "type": kind, "title": title, "url": _item_url(base, src["id"], m.get("ratingKey", ""))}
    if m.get("year"):
        out["year"] = m["year"]
    if m.get("librarySectionTitle"):
        out["library"] = m["librarySectionTitle"]
    if m.get("duration") and m.get("viewOffset"):
        out["progress"] = round(m["viewOffset"] / m["duration"], 2)
    if m.get("summary"):
        out["summary"] = m["summary"][:200]
    return out


async def _mcp_each(sources, fn):
    """소스마다 fn(src)를 동시에. 실패한 소스는 errors로 (하나가 안 돼도 나머지는 보여 줌)"""
    async def one(src):
        try:
            return src, await fn(src), None
        except Exception as e:
            return src, [], f"{src['name']}: {e}"
    got = await asyncio.gather(*(one(s) for s in sources))
    items = [it for _s, part, _e in got for it in part]
    errors = [e for _s, _p, e in got if e]
    return items, errors


async def _mcp_call(request, user, name, args):
    app, base, me = request.app, _public_base(request), user["username"]
    cfg = config_store.load()
    limit = _mcp_limit(args)
    if name == "get_status":
        up = app[UPDATES].info()
        return {"version": __version__, "update_available": up.get("newer", False), "latest_version": up.get("latest", ""),
                "user": me, "role": user["role"], "url": base,
                "sources": [{"id": s["id"], "type": s["type"], "name": s["name"]} for s in _mcp_sources(user)]}
    if name == "list_libraries":
        async def libs(src):
            if src["type"] == "plex":
                data = await _plex_json(app, cfg, src, "/library/sections")
                allowed = auth.allowed_libraries(user, src["id"])
                hidden = set(map(str, src.get("hidden_libraries") or []))
                dirs = ((data.get("MediaContainer") or {}).get("Directory")) or []
                return [{"source": src["name"], "library": d.get("title"), "type": d.get("type"),
                         "url": f"{base}/#/library/{quote(src['id'], safe='')}/{d.get('key')}"}
                        for d in dirs if str(d.get("key")) not in hidden and (allowed is None or str(d.get("key")) in allowed)]
            if src["type"] == "telegram":
                kinds = {"video": "영상", "image": "사진", "webtoon": "웹툰", "doc": "책(PDF·EPUB)"}
                return [{"source": src["name"], "library": c.get("title"), "type": "telegram:" + (c.get("kind") or "video+photo"),
                         "kind": kinds.get(c.get("kind"), "영상+사진"),
                         "url": f"{base}/#/library/{quote(src['id'], safe='')}/{c['id']}"} for c in _mcp_tg_channels(user, src)]
            if src["type"] == "radio":
                stations = _mcp_stations(user, src)
                groups = collections.Counter(st.get("group") or "" for st in stations)
                return [{"source": src["name"], "library": g or "방송국", "type": "radio", "stations": n,
                         "url": f"{base}/#/library/{quote(src['id'], safe='')}/stations"} for g, n in groups.items()]
            return [{"source": src["name"], "library": "라이브 TV", "type": src["type"],
                     "url": f"{base}/#/live/{quote(src['id'], safe='')}"}]
        items, errors = await _mcp_each(_mcp_sources(user), libs)
        return {"libraries": items, "errors": errors}
    if name == "search":
        query = str(args.get("query") or "").strip()
        if not query:
            raise mcp.McpError(-32602, "query가 비었습니다.")
        q = query.casefold()

        async def find(src):
            if src["type"] == "plex":
                data = await _plex_json(app, cfg, src, "/hubs/search", {"query": query, "limit": limit})
                return [_mcp_plex_item(base, src, m) for m in _plex_visible(data, user, src)
                        if m.get("type") in ("movie", "show", "season", "episode", "artist", "album", "track")][:limit]
            if src["type"] == "telegram":
                chans = [int(c["id"]) for c in _mcp_tg_channels(user, src)]
                rows, _total = telegram_src.query_videos(src["id"], chans, limit=limit, q=query, user=me, kinds=_tg_kinds(src))
                return [_mcp_tg_item(base, src, r) for r in rows]
            if src["type"] == "radio":
                hits = [st for st in _mcp_stations(user, src)
                        if q in f"{st['name']} {st.get('subtitle', '')} {st.get('group', '')}".casefold()][:limit]
                return [_mcp_station(base, src, st) for st in hits]
            return []
        items, errors = await _mcp_each(_mcp_sources(user), find)
        return {"query": query, "results": items, "errors": errors}
    if name == "continue_watching":
        async def cont(src):
            if src["type"] == "plex":
                data = await _plex_json(app, cfg, src, "/library/onDeck", {"X-Plex-Container-Start": 0, "X-Plex-Container-Size": limit})
                return [_mcp_plex_item(base, src, m) for m in _plex_visible(data, user, src)][:limit]
            if src["type"] == "telegram":
                chans = [int(c["id"]) for c in _mcp_tg_channels(user, src)]
                return [_mcp_tg_item(base, src, r) for r in telegram_src.continue_watching(src["id"], chans, limit=limit, user=me)]
            return []
        items, errors = await _mcp_each(_mcp_sources(user), cont)
        return {"items": items[:limit * 2], "errors": errors}
    if name == "recently_added":
        only = str(args.get("source") or "")

        async def recent(src):
            if src["type"] == "plex":
                data = await _plex_json(app, cfg, src, "/library/recentlyAdded",
                                        {"X-Plex-Container-Start": 0, "X-Plex-Container-Size": limit})
                return [_mcp_plex_item(base, src, m) for m in _plex_visible(data, user, src)][:limit]
            if src["type"] == "telegram":
                chans = [int(c["id"]) for c in _mcp_tg_channels(user, src)]
                rows, _total = telegram_src.query_videos(src["id"], chans, limit=limit, user=me, kinds=_tg_kinds(src), group=True)
                return [_mcp_tg_item(base, src, r) for r in rows if r.get("kind") != "image"]
            return []
        items, errors = await _mcp_each([s for s in _mcp_sources(user) if not only or s["id"] == only], recent)
        return {"items": items, "errors": errors}
    if name == "radio_now_playing":
        q = str(args.get("query") or "").strip().casefold()
        picked = []
        for src in _mcp_sources(user, "radio"):
            for st in _mcp_stations(user, src):
                if not q or q in f"{st['name']} {st.get('subtitle', '')} {st.get('group', '')}".casefold():
                    picked.append((src, st))
        picked = picked[:limit]
        nows = await asyncio.gather(*(_radio_now(app, st) for _src, st in picked))
        return {"stations": [{**_mcp_station(base, src, st), "now_playing": n.get("text", ""),
                              "artist": n.get("artist", ""), "song": n.get("title", "")}
                             for (src, st), n in zip(picked, nows)]}
    raise mcp.McpError(-32602, f"없는 도구입니다: {name}")


def _tg_kinds(src):
    return {int(c["id"]): c["kind"] for c in src.get("channels") or [] if c.get("kind") in telegram_src.CHANNEL_KINDS}


def _mcp_stations(user, src):
    allowed = auth.allowed_libraries(user, src["id"])
    return [st for st in src.get("stations") or [] if allowed is None or st["id"] in allowed]


def _mcp_station(base, src, st):
    return {"source": src["name"], "type": "radio", "title": st["name"], "group": st.get("group", ""),
            "url": f"{base}/#/library/{quote(src['id'], safe='')}/stations"}


async def mcp_endpoint(request):
    """POST /mcp (Authorization: Bearer 토큰) · POST /mcp/{token}. GET은 SSE를 안 쓰므로 405 (MCP 규칙)."""
    if request.method != "POST":
        return web.Response(status=405, headers={"Allow": "POST"})
    raw = request.match_info.get("token") or ""
    authz = request.headers.get("Authorization", "")
    if not raw and authz.lower().startswith("bearer "):
        raw = authz[7:].strip()
    rec = mcp.find_token(raw)
    user = auth.find_user(rec["user"]) if rec else None
    if not user:
        return web.json_response({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "MCP 토큰이 맞지 않습니다."}},
                                 status=401, headers={"WWW-Authenticate": 'Bearer realm="YMPlayer"'})
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status=400)
    pub = auth.public_user(user)

    async def call(name, args):
        return await _mcp_call(request, pub, name, args)
    if isinstance(body, list):   # 예전 규격의 묶음 요청
        out = [r for r in [await mcp.handle(m, call) for m in body] if r is not None]
        return web.json_response(out) if out else web.Response(status=202)
    resp = await mcp.handle(body, call)
    return web.json_response(resp) if resp is not None else web.Response(status=202)


@admin_only
async def admin_mcp_list(request):
    return web.json_response({"success": True, "tokens": mcp.list_tokens(), "url": f"{_public_base(request)}/mcp"})


@admin_only
async def admin_mcp_create(request):
    body = await _json_body(request)
    user = auth.find_user(body.get("user") or username(request))
    if not user:
        return _json_error(400, "그런 사용자가 없습니다.")
    try:
        raw, rec = mcp.create_token(body.get("name"), user["username"])
    except ValueError as e:
        return _json_error(400, str(e))
    base = _public_base(request)
    return web.json_response({"success": True, "token": raw, "item": rec, "url": f"{base}/mcp", "url_with_token": f"{base}/mcp/{raw}"})


@admin_only
async def admin_mcp_delete(request):
    if not mcp.delete_token(request.match_info["tid"]):
        return _json_error(404, "없는 토큰입니다.")
    return web.json_response({"success": True})


class _HideMcpToken(logging.Filter):
    """접속 기록에 /mcp/<토큰> 주소가 그대로 남지 않게"""
    _re = re.compile(r"/mcp/[A-Za-z0-9_\-]+")

    def filter(self, record):
        if isinstance(record.msg, str) and "/mcp/" in record.msg:
            record.msg = self._re.sub("/mcp/***", record.msg)
        return True


logging.getLogger("aiohttp.access").addFilter(_HideMcpToken())


# ---------------------------------------------------------------------------
# 상태 점검 / 백업·복원 (관리자)
# ---------------------------------------------------------------------------
async def _timed(coro):
    t0 = time.monotonic()
    try:
        ok, detail = await coro
    except asyncio.TimeoutError:
        ok, detail = False, "시간 초과"
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    return ok, detail, int((time.monotonic() - t0) * 1000)


async def _check_plex(request, cfg, src):
    hdrs = {**plex_headers(cfg, src.get("token")), "Accept": "application/json"}
    ssl = None if src.get("verify_ssl", True) else False
    http = request.app[HTTP]
    async with http.get(f"{src['url']}/identity", headers=hdrs, ssl=ssl, timeout=ClientTimeout(total=8)) as r:
        if r.status >= 400:
            return False, f"HTTP {r.status}"
        version = ((await r.json(content_type=None)).get("MediaContainer") or {}).get("version", "?")
    async with http.get(f"{src['url']}/library/sections", headers=hdrs, ssl=ssl, timeout=ClientTimeout(total=8)) as r:
        if r.status == 401:
            return False, f"Plex {version} - 토큰이 맞지 않습니다 (HTTP 401)"
        if r.status >= 400:
            return False, f"Plex {version} - 라이브러리 목록 HTTP {r.status}"
        dirs = ((await r.json(content_type=None)).get("MediaContainer") or {}).get("Directory") or []
    hidden = len(set(src.get("hidden_libraries") or []) & {str(d.get("key")) for d in dirs})
    return True, f"Plex {version} · 라이브러리 {len(dirs)}개" + (f" (숨김 {hidden})" if hidden else "")


async def _check_radio(request, src):
    stations = src.get("stations") or []
    nows = await asyncio.gather(*(_radio_now(request.app, st) for st in stations))
    with_info = sum(bool(n["text"]) for n in nows)
    return True, f"방송국 {len(stations)}개 · 곡 정보 받음 {with_info}개"


async def _check_iptv(request, src):
    try:
        channels, report = await _load_m3u(request, src, timeout=15)
    except RuntimeError as e:
        return False, str(e)
    if not channels:
        return False, "채널이 0개입니다" + "".join(f" - {r['error']}" for r in report if r["error"])
    note = f"채널 {len(channels)}개" + (" · EPG 있음" if src.get("epg") else "")
    if len(report) > 1:
        ok = sum(r["ok"] for r in report)
        note += f" · M3U {ok}/{len(report)}개 받음"
        bad = [f"{r['url']}: {r['error']}" for r in report if not r["ok"]]
        if bad:
            note += " (" + "; ".join(bad) + ")"
    return True, note


async def _check_telegram(request, src):
    pool = request.app[TELEGRAM]
    tg = pool.get(src["id"])
    if tg is None or not tg.matches(src):
        tg = pool[src["id"]] = telegram_src.TelegramSource(src)
    tg.set_channels(src)
    st = await asyncio.wait_for(tg.status(), 15)
    counts = telegram_src.index_counts(src["id"])
    total = sum(counts.values())
    if not st.get("authorized"):
        return False, st.get("error") or "로그인이 필요합니다 (설정 > 소스에서 로그인)"
    note = f"{st.get('name') or '로그인됨'} · 채널 {len(tg.channels)}개 · 색인한 영상 {total}개"
    if not st.get("fast_crypto"):
        note += " · cryptg 없음(느림)"
    return True, note


@admin_only
async def admin_status(request):
    cfg = config_store.load()

    async def one(src):
        if not src.get("enabled", True):
            return {"id": src["id"], "name": src["name"], "type": src["type"], "enabled": False,
                    "ok": None, "detail": "꺼 둠", "ms": 0}
        if src["type"] == "plex":
            coro = _check_plex(request, cfg, src)
        elif src["type"] == "iptv":
            coro = _check_iptv(request, src)
        elif src["type"] == "radio":
            coro = _check_radio(request, src)
        else:
            coro = _check_telegram(request, src)
        ok, detail, ms = await _timed(coro)
        return {"id": src["id"], "name": src["name"], "type": src["type"], "enabled": True,
                "ok": ok, "detail": detail, "ms": ms}

    sources = await asyncio.gather(*(one(s) for s in cfg["sources"]))
    users = auth.load_users()
    return web.json_response({"success": True, "server": {
        "version": __version__, "python": platform.python_version(), "uptime": int(time.time() - STARTED),
        "data_dir": config_store.DATA_DIR, "users": len(users),
        "admins": sum(u["role"] == "admin" for u in users),
        "cryptg": telegram_src.fast_crypto(), "telethon": telegram_src.available(),
        "cache": {**await asyncio.to_thread(media_cache.STORE.stats), "enabled": media_cache.STORE.enabled},
        "ffmpeg": transcode.available(), "transcoding": sum(p.returncode is None for p in request.app[TRANSCODES].values()),
    }, "sources": sources, "warnings": list(reversed(RECENT.items))})


@admin_only
async def admin_update_get(request):
    """GitHub에 새 버전이 있는지 (마지막으로 확인한 결과, 6시간마다 다시 봄)"""
    return web.json_response({"success": True, **request.app[UPDATES].info()})


@admin_only
async def admin_update_check(request):
    """지금 바로 GitHub 확인 ([지금 확인] 버튼)"""
    return web.json_response({"success": True, **await request.app[UPDATES].check(request.app[HTTP])})


@admin_only
async def admin_cache_get(request):
    cfg = config_store.load()
    return web.json_response({"success": True, "settings": media_cache.clean_settings(cfg.get("cache")),
                              "stats": await asyncio.to_thread(media_cache.STORE.stats)})


@admin_only
async def admin_cache_put(request):
    body = await _json_body(request)
    settings = media_cache.clean_settings(body)
    if settings["dir"]:
        try:
            os.makedirs(settings["dir"], exist_ok=True)
            probe_file = os.path.join(settings["dir"], ".write-test")
            with open(probe_file, "w") as f:
                f.write("ok")
            os.remove(probe_file)
        except OSError as e:
            return _json_error(400, f"캐시 폴더에 쓸 수 없습니다: {e}")
    cfg = config_store.load()
    cfg["cache"] = settings
    config_store.save(cfg)
    media_cache.STORE.configure(settings)
    return web.json_response({"success": True, "settings": settings,
                              "stats": await asyncio.to_thread(media_cache.STORE.stats)})


@admin_only
async def admin_cache_clear(request):
    await asyncio.to_thread(media_cache.STORE.clear)
    request.app[PLEX_FILES].clear()
    log.warning("관리자 %s가 동영상 캐시를 비웠습니다", username(request))
    return web.json_response({"success": True, "stats": await asyncio.to_thread(media_cache.STORE.stats)})


# ---- Plex 새 동영상 -> 텔레그램 채널 자동 올리기 -------------------------------------
def _make_mirror(app):
    def plex_src(sid):
        cfg = config_store.load()
        src = config_store.get_source(cfg, sid)
        return cfg, (src if src and src.get("type") == "plex" else None)

    def tg_for(sid):
        src = config_store.get_source(config_store.load(), sid)
        if not src or src.get("type") != "telegram":
            raise RuntimeError("텔레그램 소스를 찾을 수 없습니다.")
        return _tg_source(app, src)

    async def plex_json(cfg, src, path, params=None):
        return await _plex_json(app, cfg, src, path, params)
    return plex_to_tg.Mirror(plex_json, plex_src, tg_for, lambda: app[HTTP])


def _mirror_state(request):
    return {"success": True, "settings": plex_to_tg.load_settings(), "status": request.app[MIRROR].status()}


@admin_only
async def admin_mirror_get(request):
    return web.json_response(_mirror_state(request))


@admin_only
async def admin_mirror_put(request):
    body = await _json_body(request)
    cfg = config_store.load()
    try:
        cfg["plex_to_tg"] = plex_to_tg.clean_settings(body)
    except ValueError as e:
        return _json_error(400, str(e))
    config_store.save(cfg)
    request.app[MIRROR].wake()   # 새 규칙은 바로 기준을 잡게
    return web.json_response(_mirror_state(request))


@admin_only
async def admin_mirror_options(request):
    """규칙 고르기용: Plex 소스마다 영화·TV 라이브러리, 텔레그램 소스 목록 (채널은 /api/tg/{id}/channels)"""
    cfg = config_store.load()
    plex, tgs = [], []
    for src in cfg.get("sources", []):
        if src.get("type") == "plex":
            item = {"id": src["id"], "name": src.get("name", ""), "sections": [], "error": ""}
            try:
                data = await _plex_json(request.app, cfg, src, "/library/sections")
                item["sections"] = [{"key": str(d.get("key")), "title": d.get("title", ""), "type": d.get("type")}
                                    for d in (data.get("MediaContainer") or {}).get("Directory") or [] if d.get("type") in ("movie", "show")]
            except Exception as e:
                item["error"] = f"Plex에 연결하지 못했습니다: {e}"
            plex.append(item)
        elif src.get("type") == "telegram":
            tgs.append({"id": src["id"], "name": src.get("name", "")})
    return web.json_response({"success": True, "plex": plex, "telegram": tgs})


@admin_only
async def admin_mirror_check(request):
    request.app[MIRROR].wake()
    return web.json_response({"success": True})


@admin_only
async def admin_mirror_item(request):
    """자막 기다리는 항목 하나: {rule, key, action: "upload"(기다리지 않고 지금 올리기) | "drop"(빼기)}"""
    body = await _json_body(request)
    mirror, rule, key = request.app[MIRROR], str(body.get("rule") or ""), str(body.get("key") or "")
    try:
        if body.get("action") == "upload":
            title = mirror.force_item(rule, key)
        elif body.get("action") == "drop":
            title = mirror.drop_item(rule, key)
        else:
            return _json_error(400, "action은 upload 또는 drop입니다.")
    except ValueError as e:
        return _json_error(400, str(e))
    return web.json_response({"success": True, "title": title})


@admin_only
async def admin_mirror_test(request):
    body = await _json_body(request)
    try:
        title = await request.app[MIRROR].test_latest(str(body.get("rule") or ""))
    except Exception as e:
        return _json_error(400, str(e))
    return web.json_response({"success": True, "title": title})


# ---- 재생 현황 (부하 조절 봇용 - 숫자만) --------------------------------------------
async def activity_get(request):
    """GET /api/activity -> {"success", "playing", "by": {"plex", "telegram", "live"}}
    X-Activity-Token(= MP_ACTIVITY_TOKEN)으로, 또는 관리자 로그인으로"""
    if not (request.get("activity_token") or _activity_token_ok(request) or is_admin(request)):
        return _json_error(403, "관리자만 볼 수 있습니다.")
    return web.json_response({"success": True, **request.app[ACTIVITY].snapshot()})


async def activity_report(request):
    """POST /api/activity/report {event: stall|retry, kind: plex|telegram|live} - 재생기가 버벅임을 알린다 (로그인한 사용자)"""
    body = await _json_body(request)
    ok = request.app[ACTIVITY].report(username(request) or (request.remote or ""), str(body.get("event") or ""), str(body.get("kind") or ""))
    return web.json_response({"success": ok})


# ---- 공통 설정 (모든 사용자에게 적용) ------------------------------------------------
def _apply_general(app, raw):
    st = config_store.clean_general(raw)
    telegram_src.HIDE_BL = st["hide_bl_gl"]
    if st["hide_bl_gl"]:   # 작품 정보 파일(웹툰명.yaml)의 장르를 아직 안 봤으면 뒤에서 읽어 둔다
        for tg in list(app[TELEGRAM].values()):
            tg.background(("meta_flags",), tg.scan_meta_flags)
    return st


@admin_only
async def admin_general_get(request):
    return web.json_response({"success": True, "settings": config_store.clean_general(config_store.load().get("general"))})


@admin_only
async def admin_general_put(request):
    body = await _json_body(request)
    cfg = config_store.load()
    cfg["general"] = config_store.clean_general({**(cfg.get("general") or {}), **body})
    config_store.save(cfg)
    return web.json_response({"success": True, "settings": _apply_general(request.app, cfg["general"])})


# ---- 디스코드 채널 새 글 (아래쪽 흐르는 글) -------------------------------------------
async def ticker_get(request):
    """?after=마지막으로 받은 seq&boot=서버 켠 시각. 처음(after 없음)이면 방금 올라온 것 몇 개"""
    feed = request.app[DISCORD]
    st = discord_feed.clean_settings(config_store.load().get("discord"))
    after = request.query.get("after")
    if request.query.get("boot") != str(feed.boot) or not str(after or "").isdigit():
        after = None
    items = feed.since(int(after) if after is not None else None)   # 꺼져 있으면 받아 온 글이 없다 (관리자의 시험 글만)
    return web.json_response({"success": True, "enabled": st["enabled"], "boot": feed.boot, "latest": feed.seq,
                              "items": items, "repeat": st["repeat"], "speed": st["speed"]})


def _discord_state(request):
    feed = request.app[DISCORD]
    return {"success": True, "settings": discord_feed.public_settings(config_store.load().get("discord")), "status": feed.status}


@admin_only
async def admin_discord_get(request):
    return web.json_response(_discord_state(request))


@admin_only
async def admin_discord_put(request):
    body = await _json_body(request)
    cfg = config_store.load()
    try:
        cfg["discord"] = discord_feed.clean_settings(body, cfg.get("discord"))
    except ValueError as e:
        return _json_error(400, str(e))
    config_store.save(cfg)
    request.app[DISCORD].reconfigure()
    await asyncio.sleep(1.5)   # 바로 한 번 확인해 본 결과(채널 이름·오류)를 같이 돌려주려고
    return web.json_response(_discord_state(request))


@admin_only
async def admin_discord_test(request):
    """흐르는 글이 어떻게 보이는지 시험 글 하나"""
    request.app[DISCORD].add("#시험", username(request), "흐르는 글 시험입니다. 디스코드 채널에 새 글이 올라오면 이렇게 보입니다.")
    return web.json_response({"success": True})


# ---- 화면 이름/아이콘 (관리자가 바꿈) --------------------------------------------
# 이름은 config.json의 branding.title, 아이콘은 data/brand_icon.<확장자> (그림 파일만 - SVG는 스크립트를 품을 수 있어 받지 않음)
DEFAULT_TITLE = PRODUCT
BRAND_ICON_MAX = 512 * 1024
_ICON_TYPES = {  # 파일 머리 -> (확장자, Content-Type)
    b"\x89PNG\r\n\x1a\n": ("png", "image/png"),
    b"\xff\xd8\xff": ("jpg", "image/jpeg"),
    b"GIF87a": ("gif", "image/gif"),
    b"GIF89a": ("gif", "image/gif"),
    b"\x00\x00\x01\x00": ("ico", "image/x-icon"),
}


def _icon_kind(data):
    for magic, kind in _ICON_TYPES.items():
        if data.startswith(magic):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ("webp", "image/webp")
    return None


def _brand_icon_path():
    for ext, _ct in set(_ICON_TYPES.values()) | {("webp", "image/webp")}:
        path = os.path.join(config_store.DATA_DIR, f"brand_icon.{ext}")
        if os.path.exists(path):
            return path
    return None


def branding_info():
    b = config_store.load().get("branding") or {}
    icon = _brand_icon_path()
    version = int(os.path.getmtime(icon)) if icon else 0
    title = b.get("title") or ""
    if title == "Yume Player":  # 예전 이름을 그대로 저장해 둔 경우는 새 기본 이름으로
        title = ""
    return {"title": title or DEFAULT_TITLE, "icon_url": f"/brand/icon?v={version}" if icon else None,
            "theme": b.get("theme") if b.get("theme") in prefs.THEMES else "dark",
            "accent": b.get("accent") if b.get("accent") in prefs.ACCENTS else "amber",
            "show_version": b.get("show_version", True) is not False, "version": __version__}


def _clean_branding(raw, old=None):
    """branding 설정 검사. 보낸 항목만 바꾸고 나머지는 old 그대로. 잘못된 값은 ValueError."""
    out = dict(old or {})
    if "title" in raw:
        title = str(raw.get("title") or "").strip()
        if len(title) > 40:
            raise ValueError("이름은 40자까지 쓸 수 있습니다.")
        out["title"] = title
    if "show_version" in raw:
        out["show_version"] = bool(raw.get("show_version"))
    for key, options in (("theme", prefs.THEMES), ("accent", prefs.ACCENTS)):
        if key in raw:
            value = str(raw.get(key) or "")
            if value and value not in options:
                raise ValueError(f"알 수 없는 {key}: {value}")
            out[key] = value
    return out


async def branding_get(request):
    return web.json_response({"success": True, **branding_info()}, headers={"Cache-Control": "no-cache"})


async def brand_icon(request):
    path = _brand_icon_path()
    if not path:
        return web.Response(status=404)
    ext = path.rsplit(".", 1)[1]
    ctype = next(ct for e, ct in list(_ICON_TYPES.values()) + [("webp", "image/webp")] if e == ext)
    return web.FileResponse(path, headers={"Content-Type": ctype, "Cache-Control": "public, max-age=86400",
                                           "X-Content-Type-Options": "nosniff"})


@admin_only
async def branding_put(request):
    body = await _json_body(request)
    cfg = config_store.load()
    try:
        cfg["branding"] = _clean_branding(body, cfg.get("branding"))
    except ValueError as e:
        return _json_error(400, str(e))
    config_store.save(cfg)
    return web.json_response({"success": True, **branding_info()})


@admin_only
async def branding_icon_put(request):
    data = await request.content.read(BRAND_ICON_MAX + 1)
    if len(data) > BRAND_ICON_MAX:
        return _json_error(400, "아이콘 파일은 512KB까지 올릴 수 있습니다.")
    kind = _icon_kind(data)
    if not kind:
        return _json_error(400, "PNG, JPG, GIF, WebP, ICO 그림만 올릴 수 있습니다.")
    old = _brand_icon_path()
    if old:
        os.remove(old)
    os.makedirs(config_store.DATA_DIR, exist_ok=True)
    with open(os.path.join(config_store.DATA_DIR, f"brand_icon.{kind[0]}"), "wb") as f:
        f.write(data)
    return web.json_response({"success": True, **branding_info()})


@admin_only
async def branding_icon_delete(request):
    old = _brand_icon_path()
    if old:
        os.remove(old)
    return web.json_response({"success": True, **branding_info()})


BACKUP_FORMAT = "ym_player-backup"


@admin_only
async def admin_backup(request):
    """설정(토큰 포함) + 사용자(비밀번호 해시) + 사용자별 즐겨찾기. 텔레그램 로그인/색인은 안 들어감."""
    data = {"format": BACKUP_FORMAT, "version": 1, "created": int(time.time()),
            "config": config_store.load(), "users": auth.load_users(), "prefs": prefs.load_all()}
    icon = _brand_icon_path()
    if icon:
        with open(icon, "rb") as f:
            data["brand_icon"] = base64.b64encode(f.read()).decode()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return web.json_response(data, dumps=lambda o: json.dumps(o, ensure_ascii=False, indent=2), headers={
        "Content-Disposition": f'attachment; filename="ym_player-backup-{stamp}.json"', "Cache-Control": "no-store"})


@admin_only
async def admin_restore(request):
    body = await _json_body(request)
    if body.get("format") != BACKUP_FORMAT:
        return _json_error(400, "YMPlayer 백업 파일이 아닙니다.")
    try:
        raw_cfg = body.get("config") or {}
        cfg = config_store.replace_sources({"client_id": str(raw_cfg.get("client_id") or ""), "sources": []},
                                           raw_cfg.get("sources") or [])
        if not cfg["client_id"]:
            cfg["client_id"] = config_store.load()["client_id"]
        if "cache" in raw_cfg:
            cfg["cache"] = media_cache.clean_settings(raw_cfg.get("cache"))
        if isinstance(raw_cfg.get("plex_to_tg"), dict):
            cfg["plex_to_tg"] = plex_to_tg.clean_settings(raw_cfg["plex_to_tg"])
        if isinstance(raw_cfg.get("general"), dict):
            cfg["general"] = config_store.clean_general(raw_cfg["general"])
        if isinstance(raw_cfg.get("discord"), dict):
            cfg["discord"] = discord_feed.clean_settings(raw_cfg["discord"])
        if isinstance(raw_cfg.get("branding"), dict):
            raw_b = raw_cfg["branding"]
            cfg["branding"] = _clean_branding({"title": str(raw_b.get("title") or "")[:40],
                                               **{k: raw_b[k] for k in ("theme", "accent", "show_version") if k in raw_b}})
        users = auth.clean_backup_users(body.get("users"))
        icon = base64.b64decode(body["brand_icon"], validate=True) if body.get("brand_icon") else None
        if icon is not None and (len(icon) > BRAND_ICON_MAX or not _icon_kind(icon)):
            raise ValueError("아이콘 그림이 올바르지 않습니다.")
    except (ValueError, auth.AuthError, binascii.Error) as e:
        return _json_error(400, f"백업 내용이 올바르지 않습니다: {e}")
    old_tg = {s["id"] for s in config_store.load().get("sources", []) if s.get("type") == "telegram"}
    config_store.save(cfg)
    media_cache.STORE.configure(cfg.get("cache"))
    request.app[DISCORD].reconfigure()
    _apply_general(request.app, cfg.get("general"))
    auth.save_users(users)
    prefs.replace_all(body.get("prefs") if isinstance(body.get("prefs"), dict) else {})
    old_icon = _brand_icon_path()
    if old_icon:
        os.remove(old_icon)
    if icon is not None:
        with open(os.path.join(config_store.DATA_DIR, f"brand_icon.{_icon_kind(icon)[0]}"), "wb") as f:
            f.write(icon)
    request.app[CHANNEL_CACHE].clear()
    request.app[EPG_CACHE].clear()
    request.app[PLEX_SECTIONS].clear()
    for sid in old_tg | {s["id"] for s in cfg["sources"] if s.get("type") == "telegram"}:
        tg = request.app[TELEGRAM].pop(sid, None)
        if tg is not None:
            asyncio.ensure_future(tg.close())  # 바뀐 API ID로 다시 연결되게 (세션 파일은 그대로)
    log.warning("관리자 %s가 백업을 복원했습니다 (소스 %d개, 사용자 %d명)", username(request), len(cfg["sources"]), len(users))
    me = auth.find_user(username(request))
    return web.json_response({"success": True, "sources": len(cfg["sources"]), "users": len(users),
                              "still_logged_in": bool(me and me.get("role") == "admin")})


# ---------------------------------------------------------------------------
# 앱
# ---------------------------------------------------------------------------
async def index(request):
    return _render_page("index.html", request.app[STATIC_TOKEN])


# 기본 아이콘 (초승달 + 재생). 화면 안에서는 색을 강조색 변수로 칠해 테마를 따르고, 브라우저 탭은 주황 고정
DEFAULT_FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
                   "%3Crect width='64' height='64' rx='15' fill='%23e5a00d'/%3E"
                   "%3Ccircle cx='27' cy='33' r='19' fill='%23141517'/%3E"
                   "%3Ccircle cx='37' cy='26' r='15.5' fill='%23e5a00d'/%3E"
                   "%3Cpath d='M33 19.5v15l12.5-7.5z' fill='%23141517'/%3E%3C/svg%3E")
LOGO_SVG = ('<svg class="logo" viewBox="0 0 64 64" aria-hidden="true">'
            '<rect class="logo-bg" width="64" height="64" rx="15"/>'
            '<circle class="logo-ink" cx="27" cy="33" r="19"/>'
            '<circle class="logo-bg" cx="37" cy="26" r="15.5"/>'
            '<path class="logo-ink" d="M33 19.5v15l12.5-7.5z"/></svg>')


def version_short(version=__version__):
    """배지에 쓸 버전 ("1.1.1" 그대로 - 작은 수정도 보이게)."""
    return version


def brand_mark_html(icon_url, show_version=True):
    inner = f'<img src="{html.escape(icon_url)}" alt="">' if icon_url else LOGO_SVG
    badge = f'<span class="brand-ver">{html.escape(version_short())}</span>' if show_version else ""
    return f'<span class="brand-mark{" has-img" if icon_url else ""}">{inner}{badge}</span>'


def static_token():
    """화면 파일(web/) 전체의 지문. 파일이 바뀌면(업데이트) 값이 바뀐다."""
    h = hashlib.sha1()
    for root, _dirs, files in sorted(os.walk(WEB_DIR)):
        for fn in sorted(files):
            path = os.path.join(root, fn)
            st = os.stat(path)
            h.update(f"{os.path.relpath(path, WEB_DIR)}:{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()[:10]


STATIC_TOKEN = web.AppKey("static_token", str)


async def versioned_static(request):
    """/s/{지문}/js/app.js -> web/js/app.js. 주소에 지문이 들어 있어 파일이 바뀌면 주소도 바뀌므로 오래 캐시해도 된다.
    (예전엔 /static/만 있고 캐시 지시가 없어서, Safari가 업데이트 뒤에도 옛 화면 코드를 며칠씩 썼다)"""
    rel = request.match_info["path"]
    path = os.path.realpath(os.path.join(WEB_DIR, rel))
    if not path.startswith(os.path.realpath(WEB_DIR) + os.sep) or not os.path.isfile(path):
        return web.Response(status=404)
    current = request.match_info["token"] == request.app[STATIC_TOKEN]
    return web.FileResponse(path, headers={
        "Cache-Control": "public, max-age=31536000, immutable" if current else "no-cache"})


async def _static_no_cache(request, response):
    """옛 주소(/static/)로 받는 파일은 매번 바뀌었는지 확인하게 (바뀌지 않았으면 304라 가볍다)."""
    if request.path.startswith("/static/") and "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"


def _render_page(name, token=""):
    """index.html/login.html에 관리자가 정한 이름과 아이콘을 넣어서 (처음 그릴 때부터 바른 이름이 보이게).
    화면 파일 주소는 지문이 들어간 /s/{지문}/로 바꿔서, 업데이트하면 브라우저가 새 파일을 받게 한다."""
    with open(os.path.join(WEB_DIR, name), encoding="utf-8") as f:
        page = f.read()
    if token:
        page = page.replace('"/static/', f'"/s/{token}/')
    b = branding_info()
    page = (page.replace("__BRAND_TITLE__", html.escape(b["title"]))
                .replace("__BRAND_FAVICON__", html.escape(b["icon_url"] or DEFAULT_FAVICON))
                .replace("__BRAND_MARK__", brand_mark_html(b["icon_url"], b["show_version"]))
                .replace("__THEME__", b["theme"]).replace("__ACCENT__", b["accent"])
                .replace("__VERSION__", html.escape(__version__)))
    return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-cache"})


async def _on_startup(app):
    app[HTTP] = ClientSession(auto_decompress=True)
    media_cache.STORE.configure(config_store.load().get("cache"))
    _apply_general(app, config_store.load().get("general"))
    shutil.rmtree(_hls_root(), ignore_errors=True)  # 지난번에 남은 HLS 변환 파일
    app[HLS_SWEEPER] = asyncio.ensure_future(_hls_sweeper(app))
    app[UPDATE_TASK] = asyncio.ensure_future(app[UPDATES].loop(app[HTTP]))
    app[DISCORD_TASK] = asyncio.ensure_future(app[DISCORD].loop(app[HTTP]))
    app[MIRROR_TASK] = asyncio.ensure_future(app[MIRROR].loop())
    if app[REQUIRE_AUTH]:
        made = auth.bootstrap_from_env()
        if made:
            log.info("MP_USER로 첫 관리자 '%s'를 만들었습니다.", made["username"])
        admins = [u["username"] for u in auth.load_users() if u["role"] == "admin"]
        telegram_src.MIGRATE_USER = admins[0] if admins else ""


async def _on_cleanup(app):
    for task in (app.get(HLS_SWEEPER), app.get(UPDATE_TASK), app.get(DISCORD_TASK), app.get(MIRROR_TASK)):
        if task:
            task.cancel()
    for job in list(app[HLS_JOBS].values()):
        await _hls_stop(app, job)
    for proc in list(app[TRANSCODES].values()):
        if proc.returncode is None:
            proc.kill()
    await app[HTTP].close()
    for tg in list(app[TELEGRAM].values()):
        try:
            await tg.close()
        except Exception:
            pass


def create_app(require_auth=True):
    """require_auth=False는 테스트용 (모든 요청을 관리자로 본다)."""
    tracker = activity.Activity()
    # 재생 현황은 로그인한 다음에 센다 (누가 = 사용자 이름, 로그인을 안 쓰면 접속 주소)
    app = web.Application(middlewares=[auth_middleware, activity.middleware(tracker, lambda r: username(r) or (r.remote or ""))],
                          client_max_size=8 * 1024 * 1024)
    app[ACTIVITY] = tracker
    root = logging.getLogger()
    if RECENT not in root.handlers:
        root.addHandler(RECENT)
    app[REQUIRE_AUTH] = require_auth
    app[CHANNEL_CACHE] = {}
    app[EPG_CACHE] = {}
    app[TELEGRAM] = {}
    app[PLEX_SECTIONS] = {}
    app[TG_INTERNAL] = {}
    app[TRANSCODES] = {}
    app[PROBES] = {}
    app[HLS_JOBS] = {}
    app[UPDATES] = updates.UpdateChecker()
    app[DISCORD] = discord_feed.Feed()
    app[MIRROR] = _make_mirror(app)
    app[RADIO_NOW] = {}
    app[RADIO_ART] = {}
    app[PLEX_FILES] = {}
    app[STATIC_TOKEN] = static_token()
    app.on_response_prepare.append(_static_no_cache)
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
    app.router.add_get("/api/me/prefs", prefs_get)
    app.router.add_put("/api/me/prefs", prefs_put)
    app.router.add_get("/api/branding", branding_get)
    app.router.add_get("/brand/icon", brand_icon)
    app.router.add_put("/api/admin/branding", branding_put)
    app.router.add_put("/api/admin/branding/icon", branding_icon_put)
    app.router.add_delete("/api/admin/branding/icon", branding_icon_delete)
    app.router.add_get("/api/admin/status", admin_status)
    app.router.add_get("/api/admin/backup", admin_backup)
    app.router.add_get("/api/admin/update", admin_update_get)
    app.router.add_get("/api/admin/mcp", admin_mcp_list)
    app.router.add_post("/api/admin/mcp", admin_mcp_create)
    app.router.add_delete("/api/admin/mcp/{tid}", admin_mcp_delete)
    app.router.add_route("*", "/mcp", mcp_endpoint)
    app.router.add_route("*", "/mcp/{token}", mcp_endpoint)
    app.router.add_post("/api/admin/update/check", admin_update_check)
    app.router.add_get("/api/admin/cache", admin_cache_get)
    app.router.add_get("/api/ticker", ticker_get)
    app.router.add_get("/api/admin/general", admin_general_get)
    app.router.add_get("/api/activity", activity_get)
    app.router.add_post("/api/activity/report", activity_report)
    app.router.add_get("/api/admin/plex-tg", admin_mirror_get)
    app.router.add_put("/api/admin/plex-tg", admin_mirror_put)
    app.router.add_get("/api/admin/plex-tg/options", admin_mirror_options)
    app.router.add_post("/api/admin/plex-tg/check", admin_mirror_check)
    app.router.add_post("/api/admin/plex-tg/test", admin_mirror_test)
    app.router.add_post("/api/admin/plex-tg/item", admin_mirror_item)
    app.router.add_put("/api/admin/general", admin_general_put)
    app.router.add_get("/api/admin/discord", admin_discord_get)
    app.router.add_put("/api/admin/discord", admin_discord_put)
    app.router.add_post("/api/admin/discord/test", admin_discord_test)
    app.router.add_put("/api/admin/cache", admin_cache_put)
    app.router.add_delete("/api/admin/cache", admin_cache_clear)
    app.router.add_post("/api/admin/restore", admin_restore)
    app.router.add_get("/api/users", users_list)
    app.router.add_post("/api/users", users_create)
    app.router.add_put("/api/users/{name}", users_update)
    app.router.add_delete("/api/users/{name}", users_delete)
    app.router.add_get("/api/config", get_config)
    app.router.add_put("/api/config", put_config)
    app.router.add_get("/api/iptv/{sid}/channels", iptv_channels)
    app.router.add_get("/api/iptv/{sid}/epg", iptv_epg)
    app.router.add_get("/api/stream", stream_proxy)
    app.router.add_get("/api/radio/{sid}/now", radio_now)
    app.router.add_post("/api/plex-auth/pin", plex_pin_create)
    app.router.add_get("/api/plex-auth/pin/{pin_id}", plex_pin_check)
    app.router.add_get("/api/plex-sub/{sid}", plex_subtitle)
    app.router.add_get("/api/tg/{sid}/status", tg_status)
    app.router.add_post("/api/tg/{sid}/code", tg_code)
    app.router.add_post("/api/tg/{sid}/sign-in", tg_sign_in)
    app.router.add_post("/api/tg/{sid}/logout", tg_logout)
    app.router.add_get("/api/tg/{sid}/channels", tg_channels)
    app.router.add_get("/api/tg/{sid}/videos", tg_videos)
    app.router.add_get("/api/tg/{sid}/random", tg_random)
    app.router.add_get("/api/tg/{sid}/home", tg_home)
    app.router.add_get(r"/api/tg/{sid}/video/{channel:-?\d+}/{msg:\d+}", tg_video)
    app.router.add_get(r"/api/tg/{sid}/stream/{channel:-?\d+}/{msg:\d+}", tg_stream)
    app.router.add_get(r"/api/tg/{sid}/transcode/{channel:-?\d+}/{msg:\d+}", tg_transcode)
    app.router.add_post(r"/api/tg/{sid}/hls/{channel:-?\d+}/{msg:\d+}", tg_hls_start)
    app.router.add_get("/api/tg-hls/{job}/{name}", tg_hls_file)
    app.router.add_post(r"/api/tg/{sid}/probe/{channel:-?\d+}/{msg:\d+}", tg_probe)
    app.router.add_get("/internal/tg/{token}", internal_tg)
    app.router.add_get(r"/api/tg/{sid}/thumb/{channel:-?\d+}/{msg:\d+}", tg_thumb)
    app.router.add_get(r"/api/tg/{sid}/image/{channel:-?\d+}/{msg:\d+}", tg_image)
    app.router.add_get(r"/api/tg/{sid}/sub/{channel:-?\d+}/{msg:\d+}", tg_subtitle)
    app.router.add_get(r"/api/tg/{sid}/series/{channel:-?\d+}", tg_series)
    app.router.add_get(r"/api/tg/{sid}/series-cover/{channel:-?\d+}", tg_series_cover)
    app.router.add_get(r"/api/tg/{sid}/book/{channel:-?\d+}/{msg:\d+}", tg_book)
    app.router.add_get(r"/api/tg/{sid}/book/{channel:-?\d+}/{msg:\d+}/{n:\d+}", tg_book_page)
    app.router.add_get(r"/api/tg/{sid}/meta/{channel:-?\d+}/{msg:\d+}", tg_meta)
    app.router.add_get(r"/api/tg/{sid}/meta/{channel:-?\d+}", tg_meta)
    app.router.add_get("/api/tg/{sid}/meta-search", tg_meta_search)
    app.router.add_get(r"/api/tg/{sid}/plex-art/{name:[0-9a-f]{16}\.jpg}", tg_plex_art)
    app.router.add_post("/api/tg/{sid}/meta-fix", tg_meta_fix)
    app.router.add_post("/api/tg/{sid}/progress", tg_progress)
    app.router.add_route("*", "/api/plex/{sid}/{tail:.*}", plex_proxy)
    app.router.add_get("/s/{token}/{path:.+}", versioned_static)
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
    web.run_app(create_app(), host=host, port=port, print=lambda *_: log.info("YMPlayer %s: http://%s:%d", __version__, host, port))


if __name__ == "__main__":
    main()
