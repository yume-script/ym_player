"""텔레그램 채널 동영상 소스 (Telethon).

북오아시스 bookoasis_telegram_video 플러그인에서 동영상 부분만 옮겨 왔다. 그 플러그인은 Flask 위에서
요청마다 이벤트 루프를 새로 만들어 Telethon을 돌렸는데, 여기 서버(aiohttp)는 원래 asyncio라
소스마다 TelegramClient 하나를 계속 연결해 두고 같이 쓴다.

- 로그인: 전화번호 -> 인증 코드 -> (2단계 인증이면) 비밀번호. 세션은 data/telegram_<소스id>.session
- 채널 목록: 내가 들어가 있는 채널/그룹 (설정에서 골라서 저장)
- 색인: 채널의 동영상 메시지를 SQLite(data/telegram.db)에 기록. 화면을 열 때 최신 것을 먼저
  (LATEST_SYNC_SEC마다 한 번), 목록을 끝까지 내리면 더 오래된 것을 SCAN_BATCH개씩 채운다
- 재생: 브라우저의 Range 요청을 iter_download(offset=...)로 그대로 이어 준다 (탐색 가능)
- 썸네일: 메시지의 작은 미리보기를 한 번만 받아 data/telegram_thumbs/에 저장
- 이어보기: 재생 위치를 이 서버의 SQLite에 저장 (텔레그램엔 시청 기록이 없어서)
"""
import asyncio
import logging
import os
import re
import sqlite3
import time
from collections import OrderedDict

from . import config_store

log = logging.getLogger("ym_player.telegram")

STREAM_CHUNK = 512 * 1024
LATEST_SYNC_SEC = 120
SCAN_BATCH = 400          # 한 번에 훑는 메시지 수 (동영상이 아닌 글/사진도 섞여 있다)
OLDER_BATCHES_PER_PAGE = 3
DOC_CACHE_SIZE = 64

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".mpeg", ".mpg", ".ts"}
# 브라우저 <video>에 넘길 Content-Type. mkv는 Chrome의 webm(Matroska) 파서가 대개 읽어서 webm으로 알린다
MIME_FOR_BROWSER = {"video/x-matroska": "video/webm", "video/quicktime": "video/mp4", "video/x-m4v": "video/mp4"}


class TgError(Exception):
    """화면에 그대로 보여줄 수 있는 오류."""


# ---------------------------------------------------------------------------
# 순수 함수 (테스트 대상)
# ---------------------------------------------------------------------------
def parse_range(value, size):
    """bytes=a-b 하나만. 반환 (start, end, partial). 잘못되면 ValueError (-> 416)."""
    if size <= 0:
        raise ValueError("empty media")
    raw = str(value or "").strip()
    if not raw:
        return 0, size - 1, False
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", raw)
    if not m or "," in raw or (not m.group(1) and not m.group(2)):
        raise ValueError("invalid Range")
    left, right = m.groups()
    if not left:
        start, end = max(0, size - int(right)), size - 1
    else:
        start = int(left)
        end = min(int(right), size - 1) if right else size - 1
        if start >= size or end < start:
            raise ValueError("Range out of bounds")
    return start, end, True


def _attr_name(attr):
    return attr.__class__.__name__


def message_to_video(msg):
    """동영상 메시지면 색인용 dict, 아니면 None. (움짤/스티커/둥근 영상 메시지는 뺀다)"""
    doc = getattr(msg, "document", None) or getattr(msg, "video", None)
    if doc is None or getattr(msg, "photo", None) is not None:
        return None
    mime = str(getattr(doc, "mime_type", "") or "").lower()
    file_name, duration, width, height, is_video = "", 0, 0, 0, False
    for attr in getattr(doc, "attributes", None) or []:
        name = _attr_name(attr)
        if name in ("DocumentAttributeAnimated", "DocumentAttributeSticker"):
            return None
        if name == "DocumentAttributeVideo":
            if getattr(attr, "round_message", False):
                return None
            is_video = True
            duration = max(duration, int(getattr(attr, "duration", 0) or 0))
            width, height = int(getattr(attr, "w", 0) or 0), int(getattr(attr, "h", 0) or 0)
        if getattr(attr, "file_name", None):
            file_name = str(attr.file_name)
    ext = os.path.splitext(file_name.lower())[1]
    if mime == "image/gif" or ext == ".gif":
        return None
    if not (is_video or mime.startswith("video/") or ext in VIDEO_EXTS):
        return None
    caption = str(getattr(msg, "message", "") or "").strip()
    first_line = caption.splitlines()[0].strip() if caption else ""
    date = getattr(msg, "date", None)
    return {
        "msg": int(msg.id),
        "title": (first_line or os.path.splitext(file_name)[0] or "제목 없음")[:300],
        "caption": caption[:4000],
        "file_name": file_name,
        "mime": mime or "video/mp4",
        "duration": duration,
        "size": int(getattr(doc, "size", 0) or 0),
        "width": width,
        "height": height,
        "date": date.isoformat() if date else "",
        "has_thumb": 1 if (getattr(doc, "thumbs", None) or []) else 0,
    }


def browser_mime(mime):
    return MIME_FOR_BROWSER.get((mime or "").lower(), mime or "video/mp4")


# ---------------------------------------------------------------------------
# SQLite 색인
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS videos(
    source TEXT, channel INTEGER, msg INTEGER, title TEXT, caption TEXT, file_name TEXT, mime TEXT,
    duration INTEGER, size INTEGER, width INTEGER, height INTEGER, date TEXT, has_thumb INTEGER,
    PRIMARY KEY(source, channel, msg));
CREATE INDEX IF NOT EXISTS videos_by_date ON videos(source, channel, msg DESC);
CREATE TABLE IF NOT EXISTS sync_state(
    source TEXT, channel INTEGER, newest INTEGER DEFAULT 0, oldest INTEGER DEFAULT 0,
    complete INTEGER DEFAULT 0, latest_at REAL DEFAULT 0, PRIMARY KEY(source, channel));
CREATE TABLE IF NOT EXISTS peers(source TEXT, channel INTEGER, access_hash TEXT, title TEXT,
    PRIMARY KEY(source, channel));
CREATE TABLE IF NOT EXISTS progress(source TEXT, channel INTEGER, msg INTEGER, position REAL,
    duration REAL, watched INTEGER DEFAULT 0, updated REAL, PRIMARY KEY(source, channel, msg));
"""


def db():
    os.makedirs(config_store.DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(os.path.join(config_store.DATA_DIR, "telegram.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def upsert_videos(sid, channel, items):
    if not items:
        return
    with db() as conn:
        conn.executemany(
            """INSERT INTO videos(source,channel,msg,title,caption,file_name,mime,duration,size,width,height,date,has_thumb)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source,channel,msg) DO UPDATE SET title=excluded.title, caption=excluded.caption,
                 file_name=excluded.file_name, mime=excluded.mime, duration=excluded.duration, size=excluded.size,
                 width=excluded.width, height=excluded.height, date=excluded.date, has_thumb=excluded.has_thumb""",
            [(sid, channel, it["msg"], it["title"], it["caption"], it["file_name"], it["mime"], it["duration"],
              it["size"], it["width"], it["height"], it["date"], it["has_thumb"]) for it in items])


def sync_state(sid, channel):
    with db() as conn:
        row = conn.execute("SELECT * FROM sync_state WHERE source=? AND channel=?", (sid, channel)).fetchone()
    return dict(row) if row else {"newest": 0, "oldest": 0, "complete": 0, "latest_at": 0}


def save_sync_state(sid, channel, **values):
    state = {**sync_state(sid, channel), **values}
    with db() as conn:
        conn.execute(
            """INSERT INTO sync_state(source,channel,newest,oldest,complete,latest_at) VALUES(?,?,?,?,?,?)
               ON CONFLICT(source,channel) DO UPDATE SET newest=excluded.newest, oldest=excluded.oldest,
                 complete=excluded.complete, latest_at=excluded.latest_at""",
            (sid, channel, state["newest"], state["oldest"], state["complete"], state["latest_at"]))


def _row(r, prog):
    d = dict(r)
    p = prog.get((d["channel"], d["msg"]))
    d["position"] = p["position"] if p else 0
    d["watched"] = bool(p and p["watched"])
    return d


def _progress_map(conn, sid, rows):
    keys = {(r["channel"], r["msg"]) for r in rows}
    if not keys:
        return {}
    got = conn.execute("SELECT channel,msg,position,duration,watched FROM progress WHERE source=?", (sid,)).fetchall()
    return {(p["channel"], p["msg"]): dict(p) for p in got if (p["channel"], p["msg"]) in keys}


def query_videos(sid, channels, offset=0, limit=40, q="", sort="latest"):
    if not channels:
        return [], 0
    marks = ",".join("?" * len(channels))
    where = f"source=? AND channel IN ({marks})"
    args = [sid, *channels]
    if q:
        where += " AND (title LIKE ? OR caption LIKE ? OR file_name LIKE ?)"
        like = f"%{q}%"
        args += [like, like, like]
    order = {"latest": "date DESC, msg DESC", "oldest": "date ASC, msg ASC", "title": "title COLLATE NOCASE ASC"}.get(sort, "date DESC, msg DESC")
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM videos WHERE {where}", args).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM videos WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                            [*args, int(limit), int(offset)]).fetchall()
        prog = _progress_map(conn, sid, rows)
    return [_row(r, prog) for r in rows], total


def get_video(sid, channel, msg):
    with db() as conn:
        r = conn.execute("SELECT * FROM videos WHERE source=? AND channel=? AND msg=?", (sid, channel, msg)).fetchone()
        if not r:
            return None
        return _row(r, _progress_map(conn, sid, [r]))


def continue_watching(sid, channels, limit=20):
    if not channels:
        return []
    marks = ",".join("?" * len(channels))
    with db() as conn:
        rows = conn.execute(
            f"""SELECT v.*, p.position, p.watched FROM progress p JOIN videos v
                ON v.source=p.source AND v.channel=p.channel AND v.msg=p.msg
                WHERE p.source=? AND p.channel IN ({marks}) AND p.watched=0 AND p.position > 30
                ORDER BY p.updated DESC LIMIT ?""", [sid, *channels, limit]).fetchall()
    return [dict(r) for r in rows]


def save_progress(sid, channel, msg, position, duration, watched=None):
    position, duration = float(position or 0), float(duration or 0)
    if watched is None:
        watched = 1 if duration and position >= duration * 0.95 else 0
    with db() as conn:
        conn.execute(
            """INSERT INTO progress(source,channel,msg,position,duration,watched,updated) VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(source,channel,msg) DO UPDATE SET position=excluded.position, duration=excluded.duration,
                 watched=excluded.watched, updated=excluded.updated""",
            (sid, channel, msg, 0 if watched else position, duration, int(bool(watched)), time.time()))


def forget_source(sid):
    """소스를 지우면 그 색인/진행 기록도 지운다 (세션 파일은 TelegramSource.logout이)."""
    with db() as conn:
        for table in ("videos", "sync_state", "peers", "progress"):
            conn.execute(f"DELETE FROM {table} WHERE source=?", (sid,))


# ---------------------------------------------------------------------------
# Telethon 연결
# ---------------------------------------------------------------------------
class TelegramSource:
    def __init__(self, src):
        self.sid = src["id"]
        self.api_id = str(src.get("api_id") or "").strip()
        self.api_hash = str(src.get("api_hash") or "").strip()
        self.phone = str(src.get("phone") or "").strip()
        self.channels = [int(c["id"]) for c in src.get("channels") or []]
        self.client = None
        self._connect_lock = asyncio.Lock()
        self._sync_locks = {}
        self._code_hash = None
        self._docs = OrderedDict()  # (channel, msg) -> Document (Range 요청마다 메시지를 다시 받지 않게)

    def matches(self, src):
        return (str(src.get("api_id") or "").strip(), str(src.get("api_hash") or "").strip()) == (self.api_id, self.api_hash)

    @property
    def session_path(self):
        return os.path.join(config_store.DATA_DIR, f"telegram_{self.sid}.session")

    async def get_client(self):
        if not self.api_id or not self.api_hash:
            raise TgError("설정에서 Telegram API ID와 API Hash를 넣고 저장해 주세요 (my.telegram.org에서 발급).")
        if not self.api_id.isdigit():
            raise TgError("API ID는 숫자여야 합니다.")
        try:
            from telethon import TelegramClient
        except ImportError as e:
            raise TgError("서버에 telethon이 설치되어 있지 않습니다: pip install -r requirements.txt") from e
        async with self._connect_lock:
            if self.client is None:
                os.makedirs(config_store.DATA_DIR, exist_ok=True)
                self.client = TelegramClient(self.session_path, int(self.api_id), self.api_hash, receive_updates=False)
            if not self.client.is_connected():
                await self.client.connect()
                try:
                    os.chmod(self.session_path, 0o600)  # 세션 파일 = 계정 열쇠
                except OSError:
                    pass
        return self.client

    async def authorized_client(self):
        client = await self.get_client()
        if not await client.is_user_authorized():
            raise TgError("Telegram 로그인이 필요합니다. 설정에서 로그인해 주세요.")
        return client

    async def close(self):
        if self.client is not None and self.client.is_connected():
            await self.client.disconnect()

    # ----- 로그인 -----
    async def status(self):
        try:
            client = await self.get_client()
            if not await client.is_user_authorized():
                return {"authorized": False, "phone": self.phone}
            me = await client.get_me()
            name = " ".join(x for x in (getattr(me, "first_name", ""), getattr(me, "last_name", "")) if x)
            return {"authorized": True, "name": name or getattr(me, "username", "") or "", "phone": self.phone}
        except TgError as e:
            return {"authorized": False, "error": str(e), "phone": self.phone}

    async def send_code(self, phone=""):
        phone = re.sub(r"[\s-]", "", phone or self.phone)
        if not phone:
            raise TgError("전화번호를 넣어 주세요 (국가번호 포함, 예: +821012345678).")
        client = await self.get_client()
        sent = await client.send_code_request(phone)
        self.phone, self._code_hash = phone, sent.phone_code_hash
        return {"code_sent": True, "phone": phone}

    async def sign_in(self, code="", password=""):
        from telethon.errors import (PasswordHashInvalidError, PhoneCodeExpiredError, PhoneCodeInvalidError,
                                     SessionPasswordNeededError)
        client = await self.get_client()
        try:
            if password:
                await client.sign_in(password=password)
            else:
                if not self._code_hash:
                    raise TgError("먼저 [인증 코드 받기]를 눌러 주세요.")
                await client.sign_in(phone=self.phone, code=re.sub(r"\s", "", code), phone_code_hash=self._code_hash)
        except SessionPasswordNeededError:
            return {"password_required": True}
        except PhoneCodeInvalidError:
            raise TgError("인증 코드가 틀렸습니다.")
        except PhoneCodeExpiredError:
            raise TgError("인증 코드가 만료됐습니다. 다시 받아 주세요.")
        except PasswordHashInvalidError:
            raise TgError("2단계 인증 비밀번호가 틀렸습니다.")
        self._code_hash = None
        return await self.status()

    async def logout(self):
        try:
            client = await self.get_client()
            if await client.is_user_authorized():
                await client.log_out()
        finally:
            await self.close()
            self.client = None
            for suffix in ("", "-journal"):
                try:
                    os.remove(self.session_path + suffix)
                except OSError:
                    pass

    # ----- 채널 -----
    async def list_channels(self):
        client = await self.authorized_client()
        items = []
        async for dialog in client.iter_dialogs():
            ent = dialog.entity
            if _attr_name(ent) != "Channel":
                continue
            items.append({"id": int(ent.id), "title": str(getattr(ent, "title", "") or dialog.name),
                          "username": str(getattr(ent, "username", "") or ""),
                          "kind": "channel" if getattr(ent, "broadcast", False) else "group",
                          "access_hash": str(getattr(ent, "access_hash", 0) or 0)})
        with db() as conn:
            conn.executemany(
                """INSERT INTO peers(source,channel,access_hash,title) VALUES(?,?,?,?)
                   ON CONFLICT(source,channel) DO UPDATE SET access_hash=excluded.access_hash, title=excluded.title""",
                [(self.sid, c["id"], c["access_hash"], c["title"]) for c in items])
        items.sort(key=lambda c: c["title"].lower())
        return [{k: v for k, v in c.items() if k != "access_hash"} for c in items]

    async def peer(self, channel):
        from telethon.tl.types import InputPeerChannel
        with db() as conn:
            row = conn.execute("SELECT access_hash FROM peers WHERE source=? AND channel=?", (self.sid, channel)).fetchone()
        if not row or not int(row["access_hash"] or 0):
            await self.list_channels()
            with db() as conn:
                row = conn.execute("SELECT access_hash FROM peers WHERE source=? AND channel=?", (self.sid, channel)).fetchone()
        if not row:
            raise TgError("이 채널을 찾을 수 없습니다 (채널에서 나갔거나 접근할 수 없음).")
        return InputPeerChannel(int(channel), int(row["access_hash"]))

    # ----- 색인 -----
    def _lock(self, channel):
        return self._sync_locks.setdefault(channel, asyncio.Lock())

    async def _scan(self, channel, **kwargs):
        client = await self.authorized_client()
        peer = await self.peer(channel)
        items, ids = [], []
        async for msg in client.iter_messages(peer, **kwargs):
            ids.append(int(msg.id))
            item = message_to_video(msg)
            if item:
                items.append(item)
                self._remember_doc(channel, msg)
        upsert_videos(self.sid, channel, items)
        return items, ids

    async def sync_latest(self, channel, force=False):
        """새로 올라온 것 받기 (LATEST_SYNC_SEC 안에 이미 했으면 건너뜀)."""
        async with self._lock(channel):
            st = sync_state(self.sid, channel)
            if not force and time.time() - st["latest_at"] < LATEST_SYNC_SEC:
                return
            if st["newest"]:
                _, ids = await self._scan(channel, min_id=st["newest"], limit=None)
                save_sync_state(self.sid, channel, newest=max(ids + [st["newest"]]), latest_at=time.time())
            else:
                _, ids = await self._scan(channel, limit=SCAN_BATCH)
                save_sync_state(self.sid, channel, newest=max(ids or [0]), oldest=min(ids or [0]),
                                complete=int(len(ids) < SCAN_BATCH), latest_at=time.time())

    async def sync_older(self, channel):
        """더 오래된 메시지 SCAN_BATCH개 훑기. 끝까지 갔으면 complete."""
        async with self._lock(channel):
            st = sync_state(self.sid, channel)
            if st["complete"] or not st["oldest"]:
                return False
            _, ids = await self._scan(channel, offset_id=st["oldest"], limit=SCAN_BATCH)
            save_sync_state(self.sid, channel, oldest=min(ids + [st["oldest"]]), complete=int(len(ids) < SCAN_BATCH))
            return bool(ids)

    async def videos(self, channels, offset=0, limit=40, q="", sort="latest"):
        """목록. 채널 하나를 최신순으로 볼 때는 모자라면 오래된 쪽을 더 훑어서 채운다."""
        channels = [c for c in channels if c in self.channels] if self.channels else []
        for c in channels:
            try:
                await self.sync_latest(c)
            except TgError:
                raise
            except Exception as e:  # 네트워크 문제여도 이미 색인된 건 보여준다
                log.warning("telegram sync_latest %s 실패: %s", c, e)
        rows, total = query_videos(self.sid, channels, offset, limit, q, sort)
        if len(channels) == 1 and not q and sort == "latest":
            for _ in range(OLDER_BATCHES_PER_PAGE):
                if len(rows) >= limit or not await self.sync_older(channels[0]):
                    break
                rows, total = query_videos(self.sid, channels, offset, limit, q, sort)
        complete = all(sync_state(self.sid, c)["complete"] for c in channels)
        return {"items": rows, "total": total, "more": (offset + len(rows) < total) or not complete}

    # ----- 파일 -----
    def _remember_doc(self, channel, msg):
        doc = getattr(msg, "document", None) or getattr(msg, "video", None)
        if doc is None:
            return
        self._docs[(channel, int(msg.id))] = doc
        self._docs.move_to_end((channel, int(msg.id)))
        while len(self._docs) > DOC_CACHE_SIZE:
            self._docs.popitem(last=False)

    async def _message(self, channel, msg):
        client = await self.authorized_client()
        m = await client.get_messages(await self.peer(channel), ids=int(msg))
        if not m:
            raise TgError("텔레그램에서 이 메시지를 찾을 수 없습니다 (지워졌을 수 있음).")
        self._remember_doc(channel, m)
        return m

    async def _document(self, channel, msg, fresh=False):
        key = (channel, int(msg))
        if fresh or key not in self._docs:
            await self._message(channel, msg)
        doc = self._docs.get(key)
        if doc is None:
            raise TgError("이 메시지에 동영상 파일이 없습니다.")
        return doc

    async def iter_range(self, channel, msg, start, length):
        """[start, start+length) 바이트를 조각으로. 파일 참조가 만료되면 메시지를 다시 받아 한 번 더."""
        from telethon.errors import FileReferenceExpiredError
        client = await self.authorized_client()
        sent, fresh = 0, False
        while True:
            doc = await self._document(channel, msg, fresh=fresh)
            try:
                async for chunk in client.iter_download(doc, offset=start + sent, request_size=STREAM_CHUNK,
                                                        chunk_size=STREAM_CHUNK, file_size=int(doc.size)):
                    chunk = bytes(chunk[:length - sent])
                    sent += len(chunk)
                    yield chunk
                    if sent >= length:
                        return
                return
            except FileReferenceExpiredError:
                if fresh:
                    raise
                fresh = True

    async def thumbnail(self, channel, msg):
        folder = os.path.join(config_store.DATA_DIR, "telegram_thumbs", self.sid)
        path = os.path.join(folder, f"{int(channel)}_{int(msg)}.jpg")
        if os.path.exists(path):
            return path
        client = await self.authorized_client()
        m = await self._message(channel, msg)
        data = await client.download_media(m, file=bytes, thumb=-1)
        if not data:
            raise TgError("미리보기 이미지가 없습니다.")
        os.makedirs(folder, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return path
