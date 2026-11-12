"""BookOasis 소스 - 서재(BookOasis)의 책·오디오북·영상을 YMPlayer에서 찾아 읽고 듣고 본다.

연결: 설정에 넣은 BookOasis 주소와 계정으로 이 서버가 로그인(POST /login)해서 세션 쿠키를 들고 있고,
브라우저의 요청(/api/bo/{소스}/…)을 BookOasis의 정해진 읽기 주소로만 이어 준다 (Plex 소스와 같은 방식 - 계정은 브라우저로 안 나간다).
  목록   /api/media/libraries · /api/media/list · /api/media/detail · /api/media/recently-added · /api/media/history
  책     /api/media/books/{id}/reader-info · /api/media/stream(쪽 그림) · /api/media/pdf · /api/media/epub…(본문) · 원본 받기(/download - EPUB 보기용)
  오디오 /api/media/audiobooks/{id}/tracks/{트랙}/stream · cover
  영상   /api/media/videos/{id}/episodes/{화}/stream · hls-segment · subtitle · cover
  표지   /covers/…
보던 위치는 BookOasis에 그 계정으로 저장한다 (POST /api/bo/{소스}/progress - 책/오디오북/영상).
  → YMPlayer 사용자가 여럿이어도 BookOasis에는 한 계정의 기록으로 남는다.
볼 수 있는 세션(일반·오디오북·영상·성인)은 소스 설정에서 고른다. 고르지 않은 세션은 주소로 직접 불러도 막는다.
"""
import asyncio
import re
import time
from urllib.parse import parse_qs, quote

from aiohttp import ClientSession, ClientTimeout, CookieJar

SESSIONS = ("general", "audiobook", "video", "adult")
SESSION_LABEL = {"general": "책", "audiobook": "오디오북", "video": "영상", "adult": "성인 서재"}
UA = "YMPlayer (BookOasis source)"
LOGIN_TTL = 6 * 3600          # 이만큼 지나면 다시 로그인해 둔다 (세션이 살아 있어도)
# 이어 줄 수 있는 읽기 주소 (GET)
ALLOW_GET = [re.compile(p) for p in (
    r"^api/media/(libraries|list|list-totals|detail|card-info|recently-added|history|next-book|progress-state|tags|genres)$",
    r"^api/media/books/\d+/(info|reader-info|download)$",
    r"^api/media/(stream|pdf|txt|epub|epub/meta|epub/chapter|epub/chapters|epub-image)$",
    r"^api/media/audiobooks/\d+/(cover|progress)$",
    r"^api/media/audiobooks/\d+/tracks/\d+/(stream|cover|lyrics|transcode-status)$",
    r"^api/media/videos$",
    r"^api/media/videos/\d+(/cover|/progress)?$",
    r"^api/media/videos/\d+/episodes/\d+/(stream|subtitle)$",
    r"^api/media/videos/\d+/episodes/\d+/hls-segment/[\w.\-]+$",
    r"^covers/[^?#\\]+$",
)]


def clean_settings(raw, old):
    """소스 설정: 주소·계정(비밀번호는 비워 두면 기존 값), 보여 줄 세션"""
    url = str(raw.get("url") or "").strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+", url):
        raise ValueError("BookOasis 주소는 http:// 또는 https://로 시작해야 합니다.")
    username = str(raw.get("username") or "").strip()[:80]
    if not username:
        raise ValueError("BookOasis 계정 이름이 비어 있습니다.")
    password = str(raw.get("password") or "")[:200] or (old or {}).get("password", "")
    sessions = [s for s in SESSIONS if s in (raw.get("sessions") or ["general", "audiobook", "video"])]
    return {"url": url, "username": username, "password": password, "sessions": sessions or ["general"],
            "verify_ssl": bool(raw.get("verify_ssl", True))}


def allowed(tail):
    return any(p.match(tail) for p in ALLOW_GET)


def session_of(tail, query):
    """이 요청이 어느 세션의 것인지 (모르면 "") - 고르지 않은 세션을 막는 데 쓴다"""
    q = {k: v[0] for k, v in parse_qs(query or "").items() if v}
    if tail.startswith("api/media/audiobooks/"):
        return "audiobook"
    if tail.startswith("api/media/videos"):
        return "video"
    return q.get("type") or q.get("db_type") or ("general" if tail.startswith("api/media/") else "")


class LoginError(Exception):
    pass


class Client:
    """소스 하나의 BookOasis 연결 (쿠키를 든 세션 하나, 로그인은 필요할 때)"""

    def __init__(self, src):
        self.key = (src["url"], src["username"], src.get("password", ""), bool(src.get("verify_ssl", True)))
        self.base = src["url"]
        self.username, self.password = src["username"], src.get("password", "")
        self.ssl = None if src.get("verify_ssl", True) else False
        self.http = None
        self.logged_at = 0.0
        self.lock = asyncio.Lock()

    async def _session(self):
        if self.http is None or self.http.closed:
            self.http = ClientSession(cookie_jar=CookieJar(unsafe=True), auto_decompress=True, headers={"User-Agent": UA})
        return self.http

    async def login(self, force=False):
        async with self.lock:
            if not force and self.logged_at and time.time() - self.logged_at < LOGIN_TTL:
                return
            http = await self._session()
            try:
                async with http.post(self.base + "/login", json={"username": self.username, "password": self.password, "remember_me": True},
                                     ssl=self.ssl, timeout=ClientTimeout(total=20)) as r:
                    try:
                        data = await r.json(content_type=None)
                    except Exception:  # noqa: BLE001
                        data = {}
                    if r.status != 200 or not data.get("success"):
                        raise LoginError(str(data.get("error") or f"HTTP {r.status}")[:200])
                    if data.get("is_default_password"):
                        raise LoginError("이 계정은 기본 비밀번호 상태입니다. BookOasis에서 비밀번호를 먼저 바꾸세요.")
            except LoginError:
                self.logged_at = 0.0
                raise
            except Exception as e:  # noqa: BLE001
                self.logged_at = 0.0
                raise LoginError(f"BookOasis에 연결하지 못했습니다: {type(e).__name__}: {e}")
            self.logged_at = time.time()

    async def request(self, method, tail, query="", headers=None, json=None, stream_timeout=False):
        """BookOasis에 요청 (로그인이 풀렸으면 한 번 다시 로그인). 응답은 호출한 쪽이 release 한다."""
        await self.login()
        http = await self._session()
        url = f"{self.base}/{quote(tail, safe='/')}" + (f"?{query}" if query else "")
        timeout = ClientTimeout(total=None, sock_connect=15, sock_read=120) if stream_timeout else ClientTimeout(total=60)
        for attempt in (0, 1):
            resp = await http.request(method, url, headers=headers, json=json, ssl=self.ssl, timeout=timeout, allow_redirects=False)
            if resp.status in (401, 302) and attempt == 0:   # 세션이 끊김 (API는 401, 화면 주소는 로그인으로 넘김)
                resp.release()
                await self.login(force=True)
                continue
            return resp
        return resp

    async def close(self):
        if self.http is not None and not self.http.closed:
            await self.http.close()


class Clients:
    """소스 id → Client (설정이 바뀌면 새로 만든다)"""

    def __init__(self):
        self.items = {}

    def of(self, src):
        c = self.items.get(src["id"])
        key = (src["url"], src["username"], src.get("password", ""), bool(src.get("verify_ssl", True)))
        if c is None or c.key != key:
            if c is not None:
                asyncio.ensure_future(c.close())
            c = self.items[src["id"]] = Client(src)
        return c

    async def close(self):
        for c in self.items.values():
            await c.close()
        self.items.clear()


def rewrite_playlist(text, sid):
    """HLS 목록 안의 BookOasis 주소(/api/media/…)를 이 서버의 중계 주소로"""
    return re.sub(r"(?m)^/api/media/", f"/api/bo/{sid}/api/media/", text)


def progress_request(body):
    """브라우저가 보낸 보던 위치 → (BookOasis 주소, 보낼 내용, 세션). 모르는 모양이면 ValueError"""
    kind = str(body.get("kind") or "")
    try:
        if kind == "book":
            db = str(body.get("db_type") or "general")
            if db not in ("general", "adult"):
                raise ValueError
            return "api/media/progress", {"db_type": db, "book_id": int(body["book_id"]), "page_idx": max(0, int(body.get("page_idx") or 0)),
                                         "total_pages": max(1, int(body.get("total_pages") or 1)), "flush_immediately": bool(body.get("flush"))}, db
        if kind == "audio":
            return (f"api/media/audiobooks/{int(body['id'])}/progress",
                    {"current_track_id": int(body["track"]), "current_time": max(0.0, float(body.get("time") or 0)),
                     "playback_rate": float(body.get("rate") or 1.0), "is_completed": bool(body.get("done"))}, "audiobook")
        if kind == "video":
            return (f"api/media/videos/{int(body['id'])}/progress",
                    {"current_episode_id": int(body["episode"]), "current_time": max(0.0, float(body.get("time") or 0)),
                     "playback_rate": float(body.get("rate") or 1.0), "is_completed": bool(body.get("done"))}, "video")
    except (KeyError, TypeError, ValueError):
        pass
    raise ValueError("보던 위치 형식이 맞지 않습니다.")

