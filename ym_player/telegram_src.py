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
import base64
import io
import binascii
import json
import logging
import os
import quopri
import re
import shutil
import sqlite3
import time
import unicodedata
import zipfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

from . import config_store, media_cache

log = logging.getLogger("ym_player.telegram")

STREAM_CHUNK = 512 * 1024
LATEST_SYNC_SEC = 120
SCAN_BATCH = 400          # 한 번에 훑는 메시지 수 (동영상이 아닌 글/사진도 섞여 있다)
OLDER_BATCHES_PER_PAGE = 3
MSG_CACHE_SIZE = 3000     # 훑으면서 받은 메시지 객체를 기억 -> 썸네일/재생 때 다시 요청하지 않음
THUMB_CONCURRENCY = 4     # 썸네일 동시 다운로드 수 (텔레그램 요청 제한에 안 걸리게)
THUMB_PREFETCH = 60       # 새로 색인한 영상 중 미리 받아 둘 썸네일 수 (채널마다, 한 번 훑을 때)
DOWNLOAD_CONCURRENCY = 8  # 텔레그램에 동시에 거는 파일 조각 요청 (재생 + 미리 받기, 모든 영상 합쳐서)
WARM_HEAD_BYTES = 4 * 1024 * 1024   # 다음 회차를 미리 준비할 때 받아 둘 앞부분


def fast_crypto():
    """cryptg(C 암호화 모듈)가 있으면 Telethon이 알아서 쓴다. 없으면 순수 파이썬 AES라 큰 파일이 느리다."""
    import importlib.util
    return importlib.util.find_spec("cryptg") is not None


def available():
    """Telethon이 설치돼 있는지."""
    import importlib.util
    return importlib.util.find_spec("telethon") is not None

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


_PCT_RE = re.compile(r"%[0-9A-Fa-f]{2}")
_HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
# cp1252가 0x80~0x9F 자리에 넣는 글자들 (깨진 글자 안에 섞여 있어도 원래 바이트로 되돌릴 수 있다)
_CP1252_EXTRA = set(bytes(range(0x80, 0xA0)).decode("cp1252", errors="ignore"))


# 메일 머리글 방식 인코딩(RFC 2047): =?utf-8?B?6rCc7JmA...?=  (파일명에 그대로 남아 오는 경우가 있다)
_EW_RE = re.compile(r"=\?([\w.-]+)\?([bq])\?([^?]*)\?=", re.I)
# 파일명에 ?를 못 써서 공백/_로 바뀐 모양: "= utf-8 B 6rCc7JmA... ="
_EW_LOOSE_RE = re.compile(r"=[\s?_]*(utf-?8|euc-?kr|cp949|ks_c_5601-1987)[\s?_]+([bq])[\s?_]+"
                          r"([A-Za-z0-9+/=_-]+)(?:[\s?_]*=(?![A-Za-z0-9]))?", re.I)
_EW_GAP_RE = re.compile(r"\x00\s*\x00")


def _decode_word(charset, kind, payload):
    charset = {"utf8": "utf-8", "euckr": "euc-kr", "ks_c_5601-1987": "cp949"}.get(charset.lower().replace("-", ""), charset)
    try:
        if kind.upper() == "B":
            p = payload.replace("-", "+").replace("_", "/").rstrip("=")
            if len(p) % 4 == 1:  # 파일명이 잘려 남은 한 글자는 바이트가 못 된다
                p = p[:-1]
            raw = base64.b64decode(p + "=" * (-len(p) % 4))
        else:
            raw = quopri.decodestring(payload.replace("_", " ").encode("ascii", "ignore"), header=True)
        text = raw.decode(charset, errors="ignore")  # 파일명이 잘려 끝 글자가 반쯤이면 그것만 버린다
    except (LookupError, ValueError, binascii.Error):
        return None
    return text or None


def _decode_words(s):
    """=?utf-8?B?...?= (와 ?가 빠진 모양)를 풀어서 글자로. 이어진 조각 사이의 공백은 없앤다 (RFC 2047)."""
    if "=" not in s:
        return s

    def sub(m):
        text = _decode_word(m.group(1), m.group(2), m.group(3))
        return m.group(0) if text is None else f"\x00{text}\x00"
    out = _EW_RE.sub(sub, s)
    out = _EW_LOOSE_RE.sub(sub, out)
    return _EW_GAP_RE.sub("", out).replace("\x00", "")


def fix_text(value):
    """텔레그램 제목/파일명을 사람이 읽을 수 있는 UTF-8 글자로.

    - "%EC%98%81%ED%99%94" 같은 주소 인코딩 -> 풀기
    - UTF-8/CP949(EUC-KR) 바이트를 Latin-1로 잘못 읽어 깨진 글자("ì˜í™”", "¿µÈ­") -> 원래대로
    - macOS에서 올린 파일명의 풀어 쓴 한글(NFD, ㅇㅕㅇㅎㅘ처럼 보임) -> 모아 쓴 한글(NFC)
    - 메일 머리글 방식 "=?utf-8?B?6rCc7JmA...?=" (?가 공백으로 바뀐 "= utf-8 B ..." 포함) -> 풀기
    """
    s = str(value or "")
    if not s:
        return s
    s = _decode_words(s)
    if len(_PCT_RE.findall(s)) >= 2:
        try:
            s = unquote(s, errors="strict")
        except UnicodeDecodeError:
            pass
    if any(0x80 <= ord(c) <= 0xFF or c in _CP1252_EXTRA for c in s) and all(ord(c) < 0x100 or c in _CP1252_EXTRA for c in s):
        # 글자 하나씩 원래 바이트로: cp1252 특수 글자(˜ ™ …)는 그 바이트, 나머지는 Latin-1 그대로
        raw = bytes(c.encode("cp1252")[0] if c in _CP1252_EXTRA else ord(c) for c in s)
        if raw:
            try:
                s = raw.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    fixed = raw.decode("cp949")
                    if _HANGUL_RE.search(fixed):  # 진짜 한글이 나올 때만 (é 같은 라틴 글자는 그대로)
                        s = fixed
                except UnicodeDecodeError:
                    pass
    return unicodedata.normalize("NFC", s)


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".heic", ".heif"}
SUB_EXTS = {".srt", ".smi", ".sami", ".ass", ".ssa", ".vtt"}   # 영상 옆에 올린 자막 파일 (영상에 자동으로 붙임)
SUB_MAX_BYTES = 5 * 1024 * 1024
IMAGE_MAX_BYTES = 40 * 1024 * 1024   # 뷰어로 열 이미지 파일 크기 한도 (그보다 크면 썸네일만)
# 웹툰/만화 압축 파일 (그림 묶음). 채널 종류를 "웹툰"으로 정한 채널에서만 목록에 보인다
BOOK_EXTS = {".zip", ".cbz"}
BOOK_MIMES = {"application/zip", "application/x-zip-compressed", "application/x-cbz", "application/vnd.comicbook+zip"}
BOOK_MAX_BYTES = 1024 * 1024 * 1024
# 작품 정보 파일: 채널에 "웹툰명.yaml"(.yml)로 올린 것 -> 같은 이름의 작품(시리즈) 상세 화면에 보여 준다 (목록에는 안 나옴)
META_EXTS = {".yaml", ".yml"}
META_MAX_BYTES = 1024 * 1024


def _caption_title(msg, file_name, fallback):
    caption = fix_text(getattr(msg, "message", "") or "").strip()
    first_line = caption.splitlines()[0].strip() if caption else ""
    return caption, (first_line or os.path.splitext(file_name)[0] or fallback)[:300]


def _photo_item(msg, photo):
    """사진 메시지 (텔레그램이 압축해서 올린 사진). 가장 큰 크기를 원본으로 친다."""
    best_w = best_h = best_size = 0
    for size in getattr(photo, "sizes", None) or []:
        w, h = int(getattr(size, "w", 0) or 0), int(getattr(size, "h", 0) or 0)
        nbytes = getattr(size, "size", 0) or max(getattr(size, "sizes", None) or [0])
        if w * h >= best_w * best_h:
            best_w, best_h, best_size = w, h, int(nbytes or 0)
    caption, title = _caption_title(msg, "", "사진")
    date = getattr(msg, "date", None)
    return {"msg": int(msg.id), "kind": "image", "title": title, "caption": caption[:4000], "file_name": "",
            "mime": "image/jpeg", "duration": 0, "size": best_size, "width": best_w, "height": best_h,
            "date": date.isoformat() if date else "", "has_thumb": 1}


def message_to_media(msg):
    """색인할 메시지면 dict (kind = video | image), 아니면 None.
    움짤/스티커/둥근 영상 메시지는 뺀다. 사진은 압축 사진과 이미지 파일(문서) 둘 다."""
    photo = getattr(msg, "photo", None)
    if photo is not None:
        return _photo_item(msg, photo)
    doc = getattr(msg, "document", None) or getattr(msg, "video", None)
    if doc is None:
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
            file_name = fix_text(attr.file_name)
    ext = os.path.splitext(file_name.lower())[1]
    if not is_video and ext in SUB_EXTS and int(getattr(doc, "size", 0) or 0) <= SUB_MAX_BYTES:
        date = getattr(msg, "date", None)
        return {"msg": int(msg.id), "kind": "subtitle", "title": os.path.splitext(file_name)[0][:300],
                "caption": fix_text(getattr(msg, "message", "") or "")[:4000], "file_name": file_name,
                "mime": mime or "text/plain", "duration": 0, "size": int(getattr(doc, "size", 0) or 0),
                "width": 0, "height": 0, "date": date.isoformat() if date else "", "has_thumb": 0}
    is_image = not is_video and ((mime.startswith("image/") and mime != "image/svg+xml") or ext in IMAGE_EXTS)
    if is_image:
        for attr in getattr(doc, "attributes", None) or []:
            if _attr_name(attr) == "DocumentAttributeImageSize":
                width, height = int(getattr(attr, "w", 0) or 0), int(getattr(attr, "h", 0) or 0)
        caption, title = _caption_title(msg, file_name, "이미지")
        date = getattr(msg, "date", None)
        return {"msg": int(msg.id), "kind": "image", "title": title, "caption": caption[:4000], "file_name": file_name,
                "mime": mime or "image/jpeg", "duration": 0, "size": int(getattr(doc, "size", 0) or 0),
                "width": width, "height": height, "date": date.isoformat() if date else "",
                "has_thumb": 1 if (getattr(doc, "thumbs", None) or []) else 0}
    if not is_video and ext in META_EXTS and int(getattr(doc, "size", 0) or 0) <= META_MAX_BYTES:
        date = getattr(msg, "date", None)
        return {"msg": int(msg.id), "kind": "meta", "title": os.path.splitext(file_name)[0][:300],
                "caption": fix_text(getattr(msg, "message", "") or "")[:4000], "file_name": file_name,
                "mime": mime or "application/yaml", "duration": 0, "size": int(getattr(doc, "size", 0) or 0),
                "width": 0, "height": 0, "date": date.isoformat() if date else "", "has_thumb": 0}
    if not is_video and (ext in BOOK_EXTS or (not ext and mime in BOOK_MIMES)):
        caption, title = _caption_title(msg, file_name, "웹툰")
        date = getattr(msg, "date", None)
        return {"msg": int(msg.id), "kind": "book", "title": title, "caption": caption[:4000], "file_name": file_name,
                "mime": mime or "application/zip", "duration": 0, "size": int(getattr(doc, "size", 0) or 0),
                "width": 0, "height": 0, "date": date.isoformat() if date else "",
                "has_thumb": 1 if (getattr(doc, "thumbs", None) or []) else 0}
    if not (is_video or mime.startswith("video/") or ext in VIDEO_EXTS):
        return None
    caption, title = _caption_title(msg, file_name, "제목 없음")
    date = getattr(msg, "date", None)
    return {
        "msg": int(msg.id),
        "kind": "video",
        "title": title,
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


def message_to_video(msg):
    """동영상 메시지면 색인용 dict, 아니면 None (예전 이름 - 사진은 None)."""
    item = message_to_media(msg)
    return item if item and item["kind"] == "video" else None


def browser_mime(mime):
    return MIME_FOR_BROWSER.get((mime or "").lower(), mime or "video/mp4")


# ---------------------------------------------------------------------------
# SQLite 색인
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS videos(
    source TEXT, channel INTEGER, msg INTEGER, title TEXT, caption TEXT, file_name TEXT, mime TEXT,
    duration INTEGER, size INTEGER, width INTEGER, height INTEGER, date TEXT, has_thumb INTEGER,
    kind TEXT DEFAULT 'video', PRIMARY KEY(source, channel, msg));
CREATE INDEX IF NOT EXISTS videos_by_date ON videos(source, channel, msg DESC);
CREATE TABLE IF NOT EXISTS sync_state(
    source TEXT, channel INTEGER, newest INTEGER DEFAULT 0, oldest INTEGER DEFAULT 0,
    complete INTEGER DEFAULT 0, latest_at REAL DEFAULT 0, PRIMARY KEY(source, channel));
CREATE TABLE IF NOT EXISTS peers(source TEXT, channel INTEGER, access_hash TEXT, title TEXT,
    PRIMARY KEY(source, channel));
CREATE TABLE IF NOT EXISTS progress(source TEXT, channel INTEGER, msg INTEGER, position REAL,
    duration REAL, watched INTEGER DEFAULT 0, updated REAL, PRIMARY KEY(source, channel, msg));
CREATE TABLE IF NOT EXISTS probes(source TEXT, channel INTEGER, msg INTEGER, data TEXT,
    PRIMARY KEY(source, channel, msg));
CREATE TABLE IF NOT EXISTS user_progress(source TEXT, user TEXT, channel INTEGER, msg INTEGER, position REAL,
    duration REAL, watched INTEGER DEFAULT 0, updated REAL, PRIMARY KEY(source, user, channel, msg));
CREATE TABLE IF NOT EXISTS posters(source TEXT, channel INTEGER, video INTEGER, image INTEGER,
    PRIMARY KEY(source, channel, video));
CREATE INDEX IF NOT EXISTS posters_by_image ON posters(source, channel, image);
CREATE TABLE IF NOT EXISTS subs(source TEXT, channel INTEGER, video INTEGER, sub INTEGER,
    PRIMARY KEY(source, channel, sub));
CREATE TABLE IF NOT EXISTS series(source TEXT, channel INTEGER, video INTEGER, skey TEXT, title TEXT,
    season INTEGER, ep INTEGER, rep INTEGER DEFAULT 0, special INTEGER DEFAULT 0, PRIMARY KEY(source, channel, video));
CREATE INDEX IF NOT EXISTS series_by_key ON series(source, channel, skey);
CREATE TABLE IF NOT EXISTS meta(source TEXT, mkey TEXT, kind TEXT, tmdb_id INTEGER, data TEXT, fetched REAL,
    manual INTEGER DEFAULT 0, PRIMARY KEY(source, mkey));
CREATE TABLE IF NOT EXISTS meta_season(tmdb_id INTEGER, season INTEGER, data TEXT, fetched REAL, PRIMARY KEY(tmdb_id, season));
"""
# 이어보기는 사용자마다 따로 (user_progress). 예전 progress(사용자 구분 없음)는 처음 한 번 MIGRATE_USER 몫으로 옮긴다.
MIGRATE_USER = ""
_migrated = set()


def db():
    os.makedirs(config_store.DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(os.path.join(config_store.DATA_DIR, "telegram.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    path = config_store.DATA_DIR
    if path not in _migrated:
        _migrated.add(path)
        if MIGRATE_USER and not conn.execute("SELECT 1 FROM user_progress LIMIT 1").fetchone():
            with conn:
                conn.execute("""INSERT OR IGNORE INTO user_progress(source,user,channel,msg,position,duration,watched,updated)
                                SELECT source,?,channel,msg,position,duration,watched,updated FROM progress""",
                             (MIGRATE_USER,))
        if conn.execute("PRAGMA user_version").fetchone()[0] < 2:
            _fix_indexed_text(conn)
            conn.execute("PRAGMA user_version = 2")
        if conn.execute("PRAGMA user_version").fetchone()[0] < 3:
            # 사진도 색인한다 (kind). 예전에 훑은 채널은 사진을 건너뛰었으니 처음부터 다시 훑게 한다
            if "kind" not in {r[1] for r in conn.execute("PRAGMA table_info(videos)")}:
                conn.execute("ALTER TABLE videos ADD COLUMN kind TEXT DEFAULT 'video'")
            with conn:
                conn.execute("DELETE FROM sync_state")
            conn.execute("PRAGMA user_version = 3")
        if conn.execute("PRAGMA user_version").fetchone()[0] < 6:
            # 영상 옆에 올린 포스터 사진 찾기 (예전에 색인한 채널도 한 번. 5: 시간 기준을 늘려, 6: 회차만 같아도)
            for sid, ch in conn.execute("SELECT DISTINCT source, channel FROM videos").fetchall():
                _link_posters(conn, sid, ch)
            conn.execute("PRAGMA user_version = 6")
        if conn.execute("PRAGMA user_version").fetchone()[0] < 7:
            # 자막 파일도 색인한다 -> 예전에 훑은 채널은 자막을 건너뛰었으니 처음부터 다시 훑게
            with conn:
                conn.execute("DELETE FROM sync_state")
            conn.execute("PRAGMA user_version = 7")
        if conn.execute("PRAGMA user_version").fetchone()[0] < 8:
            # 회차 영상을 시리즈로 묶기 (예전에 색인한 채널도 한 번)
            for sid, ch in conn.execute("SELECT DISTINCT source, channel FROM videos").fetchall():
                _link_series(conn, sid, ch)
            conn.execute("PRAGMA user_version = 8")
        if conn.execute("PRAGMA user_version").fetchone()[0] < 9:
            # 회차 인식 규칙이 바뀜 (S102E08 같은 세 자리 시즌) -> 포스터·자막·시리즈를 한 번 다시
            for sid, ch in conn.execute("SELECT DISTINCT source, channel FROM videos").fetchall():
                _link_posters(conn, sid, ch)
                _link_subs(conn, sid, ch)
                _link_series(conn, sid, ch)
            conn.execute("PRAGMA user_version = 9")
        if conn.execute("PRAGMA user_version").fetchone()[0] < 10:
            # 웹툰 압축 파일(zip/cbz)도 색인한다 -> 예전에 훑은 채널은 압축 파일을 건너뛰었으니 처음부터 다시 훑게
            with conn:
                conn.execute("DELETE FROM sync_state")
            conn.execute("PRAGMA user_version = 10")
        if conn.execute("PRAGMA user_version").fetchone()[0] < 11:
            # 특별회차(특별편·외전·번외·후기·공지…)를 작품에 붙이고 회차 목록 맨 뒤로 -> 시리즈를 한 번 다시 묶는다
            if "special" not in {r[1] for r in conn.execute("PRAGMA table_info(series)")}:
                conn.execute("ALTER TABLE series ADD COLUMN special INTEGER DEFAULT 0")
            for sid, ch in conn.execute("SELECT DISTINCT source, channel FROM videos").fetchall():
                _link_series(conn, sid, ch)
            conn.execute("PRAGMA user_version = 11")
        if conn.execute("PRAGMA user_version").fetchone()[0] < 12:
            # 작품 정보 파일(웹툰명.yaml)도 색인한다 -> 예전에 훑은 채널은 그 파일을 건너뛰었으니 처음부터 다시 훑게
            with conn:
                conn.execute("DELETE FROM sync_state")
            conn.execute("PRAGMA user_version = 12")
    return conn


def _fix_indexed_text(conn):
    """예전에 깨진 채로 색인된 제목/설명/파일명을 한 번 고친다 (fix_text가 생기기 전 색인)."""
    rows = conn.execute("SELECT source, channel, msg, title, caption, file_name FROM videos").fetchall()
    changes = []
    for r in rows:
        new = (fix_text(r["title"]), fix_text(r["caption"]), fix_text(r["file_name"]))
        if new != (r["title"], r["caption"], r["file_name"]):
            changes.append((*new, r["source"], r["channel"], r["msg"]))
    if changes:
        with conn:
            conn.executemany("UPDATE videos SET title=?, caption=?, file_name=? WHERE source=? AND channel=? AND msg=?", changes)
        log.info("텔레그램 색인의 깨진 제목 %d개를 고쳤습니다", len(changes))


def upsert_videos(sid, channel, items):
    if not items:
        return
    with db() as conn:
        conn.executemany(
            """INSERT INTO videos(source,channel,msg,title,caption,file_name,mime,duration,size,width,height,date,has_thumb,kind)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source,channel,msg) DO UPDATE SET title=excluded.title, caption=excluded.caption,
                 file_name=excluded.file_name, mime=excluded.mime, duration=excluded.duration, size=excluded.size,
                 width=excluded.width, height=excluded.height, date=excluded.date, has_thumb=excluded.has_thumb,
                 kind=excluded.kind""",
            [(sid, channel, it["msg"], it["title"], it["caption"], it["file_name"], it["mime"], it["duration"],
              it["size"], it["width"], it["height"], it["date"], it["has_thumb"], it.get("kind", "video")) for it in items])


# ---------------------------------------------------------------------------
# 포스터 짝짓기: "포스터 사진 -> 그 영상 파일" 순서로 올리는 채널이 많다.
# 영상 바로 앞(또는 뒤) 몇 개 안에, 몇 분 안에 올라온 사진이 같은 작품·회차면 그 영상의 포스터로 본다.
# 포스터가 된 사진은 [전체] 목록에서 빠지고(사진 보기에는 나옴), 영상 카드의 썸네일/상세 포스터로 쓰인다.
# ---------------------------------------------------------------------------
# 텔레그램은 파일을 다 올린 시각을 메시지 시각으로 잡는다 -> 몇 GB짜리 회차는 포스터보다 한참 뒤 시각이 된다.
# 작품 이름/회차가 맞으면 12시간까지, 연도만 맞으면(이름이 다를 때) 30분 안만.
POSTER_WINDOW_SEC = 12 * 3600
POSTER_NEAR_SEC = 30 * 60
POSTER_BEFORE = 4    # 영상 앞쪽으로 볼 메시지 수 (나눠진 조각들 사이에 끼어 있어도 찾게)
POSTER_AFTER = 2     # 영상 뒤에 포스터를 올리는 채널도 있다

_B, _E = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"   # 밑줄(_)도 구분자로 ("이름_S01E04_1080p")
# 시즌은 세 자리도 ("킬러들의_쇼핑몰_S102E08" - 업로더가 시즌 2를 S102로 적음 -> 뒤 두 자리가 시즌)
_EP_RE = re.compile(_B + r"S(\d{1,3})[\s._-]*E(\d{1,4})" + _E + "|" + _B + r"EP?(\d{1,4})" + _E
                    + r"|(\d{1,4})\s*(?:화|회)(?![가-힣A-Za-z0-9])", re.I)
_YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_CUT_RE = re.compile(_B + r"S\d{1,3}[\s._-]*E\d|" + _B + r"EP?\d{1,4}" + _E + r"|\d{1,4}\s*(?:화|회)(?![가-힣A-Za-z0-9])"
                     r"|(?<!\d)(?:19|20)\d{2}(?!\d)|" + _B + r"\d{3,4}p" + _E + r"|[\[(]", re.I)


def _media_key(title, file_name=""):
    """제목/파일명 -> (작품 이름, 회차, 연도). "로또 1등도 출근합니다.E09.261008.1080p" -> ("로또1등도출근합니다", 9, "")"""
    best = ("", None, "")
    for text in (title or "", os.path.splitext(file_name or "")[0]):
        if not text:
            continue
        m = _EP_RE.search(text)
        ep = int(next(g for g in m.groups()[1:] if g)) if m else None   # S01E09 -> 9 (시즌 번호는 안 봄)
        y = _YEAR_RE.search(text)
        cut = _CUT_RE.search(text)
        name = text[:cut.start()] if cut and cut.start() > 0 else text
        name = re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", name).casefold())
        key = (name, ep, y.group(1) if y else "")
        if not best[0] or (ep is not None and best[1] is None):
            best = (best[0] or key[0], key[1] if key[1] is not None else best[1], best[2] or key[2])
    return best


def _poster_score(img, vid):
    """0이면 짝 아님. 작품 이름이 맞으면 높게(3~4). 이름이 달라도 - 우리말 제목 / 원제 - 회차가 같거나(성난 사람들 S02E03 /
    BEEF_S02E03, 1.5) 연도가 같으면(호프 [2026] / Hope.2026, 1) 낮게 (이 둘은 바로 옆·30분 안일 때만 짝)."""
    (iname, iep, iyear), (vname, vep, vyear) = img, vid
    if iep is not None and vep is not None and iep != vep:
        return 0
    # 비슷한 이름은 숫자가 같을 때만 ("영화2" / "영화3"은 다른 작품)
    if iname and vname and (iname in vname or vname in iname
                            or (re.findall(r"\d+", iname) == re.findall(r"\d+", vname) and _similar(iname, vname) >= 0.6)):
        return 3 + (1 if iep is not None and iep == vep else 0)
    if iep is not None and iep == vep:
        return 1.5
    if iyear and iyear == vyear and iep is None:
        return 1
    return 0


def _similar(a, b):
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def _ts(date):
    try:
        from datetime import datetime
        return datetime.fromisoformat(date).timestamp()
    except (TypeError, ValueError):
        return None


def _link_posters(conn, sid, channel):
    rows = conn.execute("SELECT msg, kind, title, file_name, date FROM videos WHERE source=? AND channel=? "
                        "AND kind IN ('video','image') ORDER BY msg", (sid, channel)).fetchall()
    keys = [_media_key(r["title"], r["file_name"]) for r in rows]
    times = [_ts(r["date"]) for r in rows]
    links = []
    for i, r in enumerate(rows):
        if r["kind"] != "video":
            continue
        best, best_score = None, 0
        for j in [*range(i - 1, i - 1 - POSTER_BEFORE, -1), *range(i + 1, i + 1 + POSTER_AFTER)]:
            if not 0 <= j < len(rows) or rows[j]["kind"] != "image":
                continue
            gap = abs(times[i] - times[j]) if times[i] is not None and times[j] is not None else 0
            if gap > POSTER_WINDOW_SEC:
                continue
            score = _poster_score(keys[j], keys[i])
            if score < 3 and (abs(i - j) > 2 or gap > POSTER_NEAR_SEC):    # 이름이 다르면 아주 가까울 때만
                continue
            score -= 0.01 * abs(i - j) + (0.005 if j > i else 0)   # 같으면 가깝고 앞에 있는 것
            if score > best_score:
                best, best_score = rows[j]["msg"], score
        if best is not None:
            links.append((sid, channel, r["msg"], best))
    with conn:
        conn.execute("DELETE FROM posters WHERE source=? AND channel=?", (sid, channel))
        conn.executemany("INSERT INTO posters(source,channel,video,image) VALUES(?,?,?,?)", links)
    _log_posters(rows, keys, links, channel)
    return len(links)


def _log_posters(rows, keys, links, channel):
    """짝짓기 결과를 서버 로그에 (짝을 못 찾은 사진 몇 개는 옆 영상 제목과 같이 - 규칙을 고칠 때 보려고)"""
    images = [i for i, r in enumerate(rows) if r["kind"] == "image"]
    videos = sum(r["kind"] == "video" for r in rows)
    if not images or not videos:
        return
    used = {img for *_, img in links}
    log.info("텔레그램 포스터 짝짓기: 채널 %s 영상 %d개 중 %d개에 포스터 (사진 %d개 중 %d개 사용)",
             channel, videos, len(links), len(images), len(used))
    shown = 0
    for i in images:
        if rows[i]["msg"] in used or shown >= 3:
            continue
        near = next((rows[j] for j in (i + 1, i - 1, i + 2) if 0 <= j < len(rows) and rows[j]["kind"] == "video"), None)
        if near is None:
            continue
        shown += 1
        log.info("  짝 없음: 사진 %r %s %s / 옆 영상 %r %s %s", rows[i]["title"][:60], keys[i], rows[i]["date"],
                 near["title"][:60], _media_key(near["title"], near["file_name"]), near["date"])


def link_posters(sid, channel):
    with db() as conn:
        return _link_posters(conn, sid, channel)


# ---------------------------------------------------------------------------
# 자막 짝짓기: 영상 옆에 올린 자막 파일(.srt/.smi/.ass...)을 그 영상의 자막으로.
#  1) 파일 이름이 같으면 (언어 꼬리 .ko/.kor/.en 따위는 빼고) 거리와 상관없이
#  2) 아니면 앞뒤 3개 메시지 안의 영상 중 작품 이름이나 회차가 맞는 것 (포스터 짝짓기와 같은 규칙)
# 영상 하나에 자막 여러 개(한국어/영어)도 된다.
# ---------------------------------------------------------------------------
SUB_NEAR = 3
_SUB_LANG_TAIL = re.compile(r"[._ -](?:ko|kor|korean|kr|en|eng|english|jp|jpn|ja|chs|cht|zh|한글|한국어|영어)$", re.I)


def _stem(name):
    stem = os.path.splitext(name or "")[0].strip()
    return _SUB_LANG_TAIL.sub("", stem).casefold()


def _link_subs(conn, sid, channel):
    rows = conn.execute("SELECT msg, kind, title, file_name FROM videos WHERE source=? AND channel=? "
                        "AND kind IN ('video','subtitle') ORDER BY msg", (sid, channel)).fetchall()
    by_stem = {}
    for r in rows:
        if r["kind"] == "video" and r["file_name"]:
            by_stem.setdefault(_stem(r["file_name"]), r["msg"])
    links = []
    for i, r in enumerate(rows):
        if r["kind"] != "subtitle":
            continue
        video = by_stem.get(_stem(r["file_name"]))
        if video is None:
            key = _media_key(r["title"], r["file_name"])
            best = 0
            for j in range(max(0, i - SUB_NEAR), min(len(rows), i + SUB_NEAR + 1)):
                if rows[j]["kind"] != "video":
                    continue
                score = _poster_score(key, _media_key(rows[j]["title"], rows[j]["file_name"])) - 0.01 * abs(i - j)
                if score >= 1.4 and score > best:
                    video, best = rows[j]["msg"], score
        if video is not None:
            links.append((sid, channel, video, r["msg"]))
    with conn:
        conn.execute("DELETE FROM subs WHERE source=? AND channel=?", (sid, channel))
        conn.executemany("INSERT INTO subs(source,channel,video,sub) VALUES(?,?,?,?)", links)
    return len(links)


def link_subs(sid, channel):
    with db() as conn:
        return _link_subs(conn, sid, channel)


# ---------------------------------------------------------------------------
# 시리즈(회차) 묶기: 같은 채널에서 작품 이름이 같고 회차(S01E09 / E09 / 9화)가 있는 영상이 2편 이상이면 한 시리즈.
# 목록에서는 가장 최근 회차 하나(rep)만 시리즈 카드로 보이고, 누르면 회차 목록. 회차가 끝나면 다음 회차를 이어서.
# ---------------------------------------------------------------------------
_SEASON_RE = re.compile(_B + r"S(\d{1,3})[\s._-]*E\d|시즌\s*(\d{1,2})", re.I)


def _episode_info(title, file_name):
    """(작품 이름 키, 시즌, 회차) - 회차가 없으면 None"""
    name, ep, _year = _media_key(title, file_name)
    if not name or ep is None:
        return None
    season = 1
    for text in (title or "", file_name or ""):
        m = _SEASON_RE.search(text)
        if m:
            season = int(m.group(1) or m.group(2)) % 100   # S102 -> 2
            break
    return name, season, ep


# 특별회차: 작품 이름 뒤에 이런 말이 붙은 것 ("나 혼자만 레벨업 특별편", "… 외전 3화", "… 후기", "… 휴재 공지").
# 본편 회차 목록 맨 뒤로 보내고, 이어 보기도 본편을 다 본 다음에. 이름 맨 앞에 오면(작품 이름의 일부) 특별회차로 보지 않는다
_SPECIAL_RE = re.compile(r"특별\s*(?:편|회|화|판|호)?|외전|번외|후기|공지|휴재|휴간|스페셜|(?<![A-Za-z])(?:SP|special|extra|side\s*story)(?![A-Za-z])"
                         r"|작가\s*의?\s*말|Q\s*&\s*A", re.I)


def _special_info(title, file_name):
    """특별회차면 (작품 이름 키, 시즌, 번호 또는 0), 아니면 None"""
    for text in (title or "", os.path.splitext(file_name or "")[0]):
        m = _SPECIAL_RE.search(text)
        if not m or m.start() == 0:
            continue
        name, _ep, _year = _media_key(text[:m.start()])
        if not name:
            continue
        _n, ep, _y = _media_key(text[m.start():])
        season = 1
        sm = _SEASON_RE.search(text)
        if sm:
            season = int(sm.group(1) or sm.group(2)) % 100
        return name, season, ep or 0
    return None


def _display_title(text):
    """'로또_1등도_출근합니다_S01E04_1080p' / '로또 1등도 출근합니다 S01E09 [2026]' -> '로또 1등도 출근합니다'"""
    text = os.path.splitext(text or "")[0] if re.search(r"\.(mkv|mp4|avi|ts|mov|webm|m4v|zip|cbz)$", text or "", re.I) else (text or "")
    text = text.lstrip("#").strip()   # 해시태그로 붙인 이름 ("#천하제일_곤륜객잔_63646168 12화")
    cut = _CUT_RE.search(text)
    head = text[:cut.start()] if cut and cut.start() > 0 else text
    head = re.sub(r"[._]+", " ", head)
    head = re.sub(r"[\s\-]*\(?\d{6,}\)?\s*$", "", head)   # 끝의 작품 ID (63646168)
    return re.sub(r"[\s\-\[(]+$", "", head).strip()


def _link_series(conn, sid, channel):
    rows = conn.execute("SELECT msg, kind, title, file_name FROM videos WHERE source=? AND channel=? AND kind IN ('video','book') "
                        "ORDER BY msg", (sid, channel)).fetchall()
    posters = {p["video"]: p["image"] for p in conn.execute(
        "SELECT video, image FROM posters WHERE source=? AND channel=?", (sid, channel)).fetchall()}
    groups, specials = {}, []
    for r in rows:
        prefix = "b:" if r["kind"] == "book" else ""   # 웹툰(압축 파일)은 같은 이름의 영상과 섞이지 않게 키 앞에 "b:"
        sp = _special_info(r["title"], r["file_name"])
        if sp:
            specials.append((prefix + sp[0], r, sp[1], sp[2]))
            continue
        info = _episode_info(r["title"], r["file_name"])
        if info:
            groups.setdefault(prefix + info[0], []).append((r, info[1], info[2]))
    links = []
    for key, eps in groups.items():
        if len({(s, e) for _, s, e in eps}) < 2:   # 회차 하나뿐이면 묶지 않는다
            continue
        rep = max(r["msg"] for r, _, _ in eps)
        # 이름은 포스터 사진 제목(띄어쓰기가 살아 있는 경우가 많음) -> 영상 제목 -> 파일 이름 순으로
        title = ""
        for r, _, _ in sorted(eps, key=lambda x: -x[0]["msg"]):
            img = posters.get(r["msg"])
            if img:
                prow = conn.execute("SELECT title FROM videos WHERE source=? AND channel=? AND msg=?", (sid, channel, img)).fetchone()
                title = _display_title(prow["title"]) if prow else ""
                if title:
                    break
        if not title:
            cands = [_display_title(r["title"]) for r, _, _ in eps] + [_display_title(r["file_name"]) for r, _, _ in eps]
            cands = [c for c in cands if c and re.search(r"[^\W\d_]", c)]   # "001" 같은 번호뿐인 파일 이름은 빼고
            title = max(cands, key=lambda c: (c.count(" "), -len(c))) if cands else key
        for r, season, ep in eps:
            links.append((sid, channel, r["msg"], key, title, season, ep, int(r["msg"] == rep), 0))
        # 같은 작품의 특별회차는 본편 뒤에
        for skey, r, season, ep in specials:
            if skey == key:
                links.append((sid, channel, r["msg"], key, title, season, ep, 0, 1))
    with conn:
        conn.execute("DELETE FROM series WHERE source=? AND channel=?", (sid, channel))
        conn.executemany("INSERT INTO series(source,channel,video,skey,title,season,ep,rep,special) VALUES(?,?,?,?,?,?,?,?,?)", links)
    return len({x[3] for x in links})


def link_series(sid, channel):
    with db() as conn:
        return _link_series(conn, sid, channel)


def _series_map(conn, sid, rows, user=""):
    """목록의 대표 회차 -> 시리즈 정보 {key, title, count, unwatched}"""
    reps = {(r["channel"], r["msg"]) for r in rows if r["kind"] in ("video", "book")}
    if not reps:
        return {}
    chans = {c for c, _ in reps}
    marks = ",".join("?" * len(chans))
    srows = conn.execute(f"SELECT s.channel, s.video, s.skey, s.title, s.rep, v.has_thumb FROM series s LEFT JOIN videos v "
                         f"ON v.source=s.source AND v.channel=s.channel AND v.msg=s.video WHERE s.source=? AND s.channel IN ({marks})",
                         [sid, *chans]).fetchall()
    keys = {(s["channel"], s["skey"]) for s in srows if s["rep"] and (s["channel"], s["video"]) in reps}
    if not keys:
        return {}
    watched = {(p["channel"], p["msg"]) for p in conn.execute(
        "SELECT channel, msg FROM user_progress WHERE source=? AND user=? AND watched=1", (sid, user)).fetchall()}
    info = {}
    for s in srows:
        k = (s["channel"], s["skey"])
        if k not in keys:
            continue
        d = info.setdefault(k, {"key": s["skey"], "title": s["title"], "count": 0, "unwatched": 0, "rep": None})
        d["count"] += 1
        d["unwatched"] += (s["channel"], s["video"]) not in watched
        if s["rep"]:
            d["rep"] = s["video"]
        if s["has_thumb"] and s["video"] > d.get("cover", 0):
            d["cover"] = s["video"]   # 대표 회차에 그림이 없을 때 카드에 쓸 회차 (웹툰은 한 번 열어 본 회차만 그림이 있음)
    return {(c, d["rep"]): {k: v for k, v in d.items() if k != "rep"} for (c, _), d in info.items()}


def series_episodes(sid, channel, key, user=""):
    """시리즈의 회차들 (시즌, 회차 순, 특별회차는 맨 뒤). 없으면 None"""
    with db() as conn:
        srows = conn.execute("SELECT video, title, season, ep, special FROM series WHERE source=? AND channel=? AND skey=? "
                             "ORDER BY special, season, ep, video", (sid, channel, key)).fetchall()
        if not srows:
            return None
        rows = {r["msg"]: r for r in conn.execute(
            f"SELECT * FROM videos WHERE source=? AND channel=? AND msg IN ({','.join('?' * len(srows))})",
            [sid, channel, *[s["video"] for s in srows]]).fetchall()}
        vids = list(rows.values())
        prog, posters = _progress_map(conn, sid, vids, user), _poster_map(conn, sid, vids)
    eps = [{**_row(rows[s["video"]], prog, posters), "season": s["season"], "ep": s["ep"], "special": bool(s["special"])}
           for s in srows if s["video"] in rows]
    return {"key": key, "title": srows[0]["title"], "channel": channel, "episodes": eps}


def series_of(sid, channel, msg):
    """이 영상이 시리즈의 회차면 {key, title, season, ep, next: {msg, title} 또는 None}"""
    with db() as conn:
        me = conn.execute("SELECT skey, title, season, ep, special FROM series WHERE source=? AND channel=? AND video=?",
                          (sid, channel, msg)).fetchone()
        if not me:
            return None
        # 순서: 본편(시즌, 회차) 다음 특별회차. 본편은 같은 회차를 두 번 올린 것을 건너뛰고, 특별회차는 번호가 없을 수 있어 메시지 순으로
        order = "(s.special, s.season, s.ep, CASE WHEN s.special THEN s.video ELSE 0 END)"
        mine = (me["special"], me["season"], me["ep"], int(msg) if me["special"] else 0)
        base = ("SELECT s.video, v.title FROM series s JOIN videos v ON v.source=s.source AND v.channel=s.channel AND v.msg=s.video "
                "WHERE s.source=? AND s.channel=? AND s.skey=? AND ")
        nxt = conn.execute(base + f"{order} > (?, ?, ?, ?) ORDER BY s.special, s.season, s.ep, s.video LIMIT 1",
                           (sid, channel, me["skey"], *mine)).fetchone()
        prv = conn.execute(base + f"{order} < (?, ?, ?, ?) ORDER BY s.special DESC, s.season DESC, s.ep DESC, s.video DESC LIMIT 1",
                           (sid, channel, me["skey"], *mine)).fetchone()
    return {"key": me["skey"], "title": me["title"], "season": me["season"], "ep": me["ep"], "special": bool(me["special"]),
            "next": {"msg": nxt["video"], "title": nxt["title"]} if nxt else None,
            "prev": {"msg": prv["video"], "title": prv["title"]} if prv else None}


_ID_TAIL_RE = re.compile(r"\d{6,}$")   # 이름 키 끝의 작품 ID ("천하제일곤륜객잔63646168")


def series_meta_file(sid, channel, key):
    """작품(시리즈 키)과 이름이 같은 작품 정보 파일(웹툰명.yaml)의 메시지 번호 - 여러 개면 가장 최근 것. 없으면 None.
    파일 이름은 작품 ID를 뺀 이름("천하제일 곤륜객잔.yaml"), 캡션 첫 줄은 회차와 같은 해시태그("#천하제일_곤륜객잔_63646168")
    라서 둘 다 보고, 끝의 작품 ID는 있든 없든 같은 작품으로 본다"""
    name = key[2:] if key.startswith("b:") else key
    want = {name, _ID_TAIL_RE.sub("", name)}
    with db() as conn:
        rows = conn.execute("SELECT msg, title, caption, file_name FROM videos WHERE source=? AND channel=? AND kind='meta' ORDER BY msg DESC",
                            (sid, channel)).fetchall()
    exact = loose = None
    for r in rows:
        texts = [os.path.splitext(r["file_name"] or "")[0], r["title"] or ""]
        texts += re.findall(r"#(\S+)", r["caption"] or "")[:3]          # 캡션의 해시태그
        for text in texts:
            n, _ep, _year = _media_key(text)
            if not n:
                continue
            if n == name:
                exact = exact or r["msg"]
            elif n in want or _ID_TAIL_RE.sub("", n) in want:
                loose = loose or r["msg"]
    return exact or loose


_META_KEYS = {   # 파일마다 이름이 달라서 흔한 이름들을 모아 본다 (소문자, 띄어쓰기·_·- 무시)
    "title": ("title", "name", "localizedname", "seriesname", "series", "제목", "작품명", "작품", "웹툰명"),
    "authors": ("author", "authors", "writer", "writers", "personwriters", "artist", "artists", "illustrator",
                "penciller", "pencillers", "personpenciller", "작가", "글", "그림", "글그림", "저자"),
    "summary": ("summary", "description", "desc", "synopsis", "overview", "story", "줄거리", "소개", "작품소개", "설명"),
    "genres": ("genre", "genres", "category", "장르"),
    "tags": ("tag", "tags", "keyword", "keywords", "태그", "키워드"),
    "status": ("status", "state", "publicationstatus", "상태", "연재상태", "완결"),
    "platform": ("platform", "publisher", "publishers", "personpublisher", "site", "service", "연재처", "플랫폼", "출판사"),
    "day": ("day", "days", "weekday", "요일", "연재요일"),
    "age": ("age", "rating", "agerating", "연령", "등급", "연령등급", "이용가"),
    "cover": ("cover", "coverimage", "coverurl", "posterurl", "poster", "thumbnail", "thumb", "image", "표지", "썸네일"),
    "url": ("url", "link", "links", "weblink", "weblinks", "homepage", "링크", "주소"),
    "year": ("releasedate", "year", "releaseyear", "started", "start", "연재시작", "연도"),
    "language": ("language", "lang", "언어"),
}
# 보여 주지 않는 키 (다운로더 내부 정보 등)
_META_SKIP = {"action", "files", "score", "code", "month", "collections", "personcharacter", "personlocation", "personteam"}
# Kavita 숫자 코드
_KAVITA_STATUS = {"0": "연재중", "1": "휴재", "2": "완결", "3": "연재 중단", "4": "완결"}
_KAVITA_AGE = {"2": "유아", "3": "전체 이용가", "4": "전체 이용가", "5": "10세 이상", "6": "12세 이상", "7": "전체 이용가",
               "8": "15세 이상", "9": "15세 이상", "10": "17세 이상", "11": "17세 이상", "12": "18세 이상", "13": "18세 이상",
               "14": "18세 이상"}
_STATUS_WORDS = {"ongoing": "연재중", "hiatus": "휴재", "completed": "완결", "complete": "완결", "ended": "완결",
                 "cancelled": "연재 중단", "canceled": "연재 중단"}
_LANGS = {"ko": "한국어", "kr": "한국어", "en": "영어", "ja": "일본어", "jp": "일본어", "zh": "중국어"}


def _norm_key(k):
    return re.sub(r"[\s_\-]+", "", str(k)).casefold()


def _mini_yaml(text):
    """PyYAML이 없을 때: "키: 값" / 다음 줄들의 "- 항목" 목록 / 여러 줄 값(| >)만 읽는 작은 파서"""
    out, key, block = {}, None, None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if block is not None and (raw.startswith((" ", "\t")) or not line.strip()):
            out[key] = (out[key] + "\n" if out[key] else "") + line.strip()
            continue
        block = None
        m = re.match(r"^\s*-\s*(.*)$", line)
        if m and key is not None:
            if not isinstance(out.get(key), list):
                out[key] = [] if out.get(key) in ("", None) else [out[key]]
            out[key].append(m.group(1).strip().strip("'\""))
            continue
        m = re.match(r"^([^:#][^:]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1).strip().strip("'\""), m.group(2).strip()
            if val in ("|", ">", "|-", ">-"):
                out[key], block = "", key
            elif val.startswith("[") and val.endswith("]"):
                out[key] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
            else:
                out[key] = val.strip("'\"")
    return out


def parse_meta(data):
    """작품 정보 파일 내용 -> {title, authors[], summary, genres[], tags[], status, platform, day, age, cover, url, year,
    language, extra[[키, 값]]}.
    평평한 "키: 값" 모양 말고도, 웹툰 다운로더가 만드는 모양 {meta: {Kavita 정보}, search: [{검색 결과}], files: {...}}도 읽는다
    (meta를 먼저, 빈 칸은 search로 채움. files의 첫 회차 표지(base64)는 표지 주소가 없을 때 표지로)"""
    text = data.decode("utf-8-sig", "replace") if isinstance(data, bytes) else str(data)
    try:
        import yaml
        raw = yaml.safe_load(text)
    except ImportError:
        raw = _mini_yaml(text)
    except Exception as e:
        raise TgError(f"작품 정보 파일(YAML)을 읽지 못했습니다: {e}") from e
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        raw = raw[0]
    if not isinstance(raw, dict) or not raw:
        raise TgError("작품 정보 파일(YAML)이 '키: 값' 모양이 아닙니다.")
    sources, files = [], None
    if isinstance(raw.get("meta"), dict) or isinstance(raw.get("search"), (list, dict)):
        if isinstance(raw.get("meta"), dict):
            sources.append(raw["meta"])
        srch = raw.get("search")
        if isinstance(srch, list):
            sources += [x for x in srch[:1] if isinstance(x, dict)]
        elif isinstance(srch, dict):
            sources.append(srch)
        files = raw.get("files") if isinstance(raw.get("files"), dict) else None
    else:
        sources.append(raw)
    lookup = {}
    for field, names in _META_KEYS.items():
        for n in names:
            lookup.setdefault(_norm_key(n), field)

    def as_list(v):
        if isinstance(v, (list, tuple)):
            return [str(x).strip() for x in v if str(x).strip()]
        if isinstance(v, dict):
            return [f"{k}: {x}" for k, x in v.items()]
        return [x.strip() for x in re.split(r"[,/·]", str(v)) if x.strip()] if v not in (None, "") else []

    def as_text(v):
        return ", ".join(as_list(v)) if isinstance(v, (list, tuple, dict)) else str(v).strip()

    out, extra_keys = {"extra": []}, set()
    for src in sources:
        for k, v in src.items():
            nk = _norm_key(k)
            if v in (None, "", [], {}) or str(v).strip() == "" or nk in _META_SKIP:
                continue
            field = lookup.get(nk)
            if field == "day" and re.fullmatch(r"\d{1,2}", str(v).strip()):
                continue   # Kavita의 "Day: 02"는 연재 요일이 아니라 날짜
            if field in ("authors", "genres", "tags"):
                cur = out.setdefault(field, [])
                cur += [x for x in as_list(v) if x not in cur]
            elif field and field not in out:
                out[field] = as_text(v)
            elif not field and nk not in extra_keys and len(out["extra"]) < 20 and not isinstance(v, (list, dict)):
                extra_keys.add(nk)
                out["extra"].append([str(k)[:40], as_text(v)[:500]])
    # 숫자 코드·형식 다듬기
    st = out.get("status", "")
    out["status"] = _KAVITA_STATUS.get(st, _STATUS_WORDS.get(st.casefold(), st)) if st else ""
    if out.get("age"):
        out["age"] = _KAVITA_AGE.get(out["age"], "" if out["age"] in ("0", "1", "-1") else out["age"])
    yr = out.get("year", "")
    if re.fullmatch(r"\d{8}", yr):
        out["year"] = f"{yr[:4]}-{yr[4:6]}-{yr[6:]}"
    if out.get("language"):
        out["language"] = _LANGS.get(out["language"].casefold(), out["language"])
    if out.get("cover") and not re.match(r"^https?://", out["cover"]):
        out.pop("cover")   # 주소가 아니면 (파일 이름 등) 쓰지 않는다
    if not out.get("cover") and files:
        # 첫 회차 표지가 base64로 들어 있으면 (다른 회차는 "FIRST" 같은 표시)
        for _name, info in sorted(files.items()):
            b64 = info.get("cover") if isinstance(info, dict) else None
            if isinstance(b64, str) and len(b64) > 200 and len(b64) < 700_000 and re.fullmatch(r"[A-Za-z0-9+/=\s]+", b64):
                kind = "png" if b64.startswith("iVBOR") else "webp" if b64.startswith("UklGR") else "jpeg"
                out["cover"] = f"data:image/{kind};base64," + re.sub(r"\s+", "", b64)
                break
    if out.get("url") and not re.match(r"^https?://", out["url"]):
        out["extra"].append(["주소", out.pop("url")])
    out = {k: v for k, v in out.items() if v not in ("", None) or k == "extra"}
    out["summary"] = str(out.get("summary") or "")[:5000]
    if not out.get("day"):
        # 줄거리 첫머리의 "[매주 일요일 연재]" 같은 안내에서 연재 요일
        m = re.search(r"매주\s*((?:[월화수목금토일](?:요일)?[,·\s]*)+)", out["summary"][:200])
        if m:
            out["day"] = m.group(1).strip(" ,·")
    return out


# ---------------------------------------------------------------------------
# 작품 정보(TMDB) - 무엇을 찾을지 정하고, 찾은 결과를 저장한다 (tmdb.py가 실제 검색).
# mkey: 시리즈/회차 영상은 "tv:{작품 이름 키}", 그 밖의 영상은 "movie:{이름 키}:{연도}". 같은 소스 안에서 채널끼리 같이 쓴다.
# ---------------------------------------------------------------------------
def meta_target(sid, channel, msg=None, series_key=None):
    """{mkey, kind, titles[], year, season, ep} - 찾을 이름은 우리말 포스터 제목을 먼저"""
    with db() as conn:
        if series_key is not None:
            s = conn.execute("SELECT title FROM series WHERE source=? AND channel=? AND skey=? LIMIT 1",
                             (sid, channel, series_key)).fetchone()
            if not s:
                return None
            return {"mkey": f"tv:{series_key}", "kind": "tv", "titles": [s["title"]], "year": "", "season": None, "ep": None}
        r = conn.execute("SELECT * FROM videos WHERE source=? AND channel=? AND msg=?", (sid, channel, msg)).fetchone()
        if not r or r["kind"] != "video":
            return None
        srow = conn.execute("SELECT skey, title, season, ep FROM series WHERE source=? AND channel=? AND video=?",
                            (sid, channel, msg)).fetchone()
        prow = conn.execute("SELECT v.title FROM posters p JOIN videos v ON v.source=p.source AND v.channel=p.channel "
                            "AND v.msg=p.image WHERE p.source=? AND p.channel=? AND p.video=?", (sid, channel, msg)).fetchone()
    poster_title = _display_title(prow["title"]) if prow else ""
    if srow:
        return {"mkey": f"tv:{srow['skey']}", "kind": "tv", "titles": [srow["title"], poster_title],
                "year": "", "season": srow["season"], "ep": srow["ep"]}
    name, ep, year = _media_key(r["title"], r["file_name"])
    titles = [poster_title, _display_title(r["title"]), _display_title(r["file_name"])]
    titles = [t for i, t in enumerate(titles) if t and t not in titles[:i]]
    if not name:
        return None
    info = _episode_info(r["title"], r["file_name"])
    if info:
        return {"mkey": f"tv:{info[0]}", "kind": "tv", "titles": titles, "year": "", "season": info[1], "ep": info[2]}
    return {"mkey": f"movie:{name}:{year}", "kind": "movie", "titles": titles, "year": year, "season": None, "ep": None}


def meta_get(sid, mkey):
    with db() as conn:
        r = conn.execute("SELECT * FROM meta WHERE source=? AND mkey=?", (sid, mkey)).fetchone()
    if not r:
        return None
    return {"kind": r["kind"], "tmdb_id": r["tmdb_id"], "data": json.loads(r["data"]) if r["data"] else None,
            "fetched": r["fetched"], "manual": bool(r["manual"])}


def meta_put(sid, mkey, kind, tmdb_id, data, manual=False):
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO meta(source,mkey,kind,tmdb_id,data,fetched,manual) VALUES(?,?,?,?,?,?,?)",
                     (sid, mkey, kind, tmdb_id, json.dumps(data, ensure_ascii=False) if data else None, time.time(), int(manual)))


def season_get(tmdb_id, season):
    with db() as conn:
        r = conn.execute("SELECT data, fetched FROM meta_season WHERE tmdb_id=? AND season=?", (tmdb_id, season)).fetchone()
    return (json.loads(r["data"]), r["fetched"]) if r else (None, 0)


def season_put(tmdb_id, season, data):
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO meta_season(tmdb_id,season,data,fetched) VALUES(?,?,?,?)",
                     (tmdb_id, season, json.dumps(data, ensure_ascii=False), time.time()))


def video_subs(sid, channel, msg):
    """이 영상에 붙은 자막 파일들 [{msg, name}] (메시지 순서)"""
    with db() as conn:
        rows = conn.execute("SELECT v.msg, v.file_name FROM subs s JOIN videos v ON v.source=s.source AND v.channel=s.channel "
                            "AND v.msg=s.sub WHERE s.source=? AND s.channel=? AND s.video=? ORDER BY v.msg",
                            (sid, channel, msg)).fetchall()
    return [{"msg": r["msg"], "name": r["file_name"]} for r in rows]


def _poster_map(conn, sid, rows):
    """영상 (채널, 메시지) -> 포스터 사진 메시지"""
    chans = {r["channel"] for r in rows if r["kind"] == "video"}
    if not chans:
        return {}
    marks = ",".join("?" * len(chans))
    got = conn.execute(f"SELECT channel, video, image FROM posters WHERE source=? AND channel IN ({marks})",
                       [sid, *chans]).fetchall()
    return {(p["channel"], p["video"]): p["image"] for p in got}


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


def _row(r, prog, posters=None):
    d = dict(r)
    p = prog.get((d["channel"], d["msg"]))
    d["position"] = p["position"] if p else 0
    d["watched"] = bool(p and p["watched"])
    d["poster"] = (posters or {}).get((d["channel"], d["msg"]), 0)
    if d.get("kind") == "book":   # 웹툰: 보던 위치는 쪽, duration은 쪽 수 (처음 열어 봐야 앎)
        d["duration"] = p["duration"] if p else 0
    return d


def _progress_map(conn, sid, rows, user=""):
    keys = {(r["channel"], r["msg"]) for r in rows}
    if not keys:
        return {}
    got = conn.execute("SELECT channel,msg,position,duration,watched FROM user_progress WHERE source=? AND user=?",
                       (sid, user)).fetchall()
    return {(p["channel"], p["msg"]): dict(p) for p in got if (p["channel"], p["msg"]) in keys}


CHANNEL_KINDS = ("video", "image", "webtoon")


def _channel_clause(channels, kinds, col=""):
    """채널마다 정한 종류를 지키는 WHERE 조각. kinds: {채널: "video"|"image"|"webtoon"|""}
    웹툰 압축 파일(book)은 "웹툰" 채널에서만, 웹툰 채널에서는 압축 파일만 (다른 채널의 zip은 자막 묶음 따위일 수 있음)"""
    groups = {}
    for c in channels:
        groups.setdefault((kinds or {}).get(c, "") if (kinds or {}).get(c, "") in CHANNEL_KINDS else "", []).append(c)
    parts, args = [], []
    for k, chans in groups.items():
        marks = ",".join("?" * len(chans))
        if k == "webtoon":
            parts.append(f"({col}channel IN ({marks}) AND {col}kind='book')")
        elif k:
            parts.append(f"({col}channel IN ({marks}) AND {col}kind=?)")
        else:
            parts.append(f"({col}channel IN ({marks}) AND {col}kind!='book')")
        args += [*chans, *([k] if k and k != "webtoon" else [])]
    return "(" + " OR ".join(parts) + ")", args


def query_videos(sid, channels, offset=0, limit=40, q="", sort="latest", user="", kind="", kinds=None, group=False):
    """kind: "video" / "image" / "book" / ""(전부 - 영상의 포스터로 짝지은 사진은 뺌). kinds: 채널마다 정한 종류"""
    if not channels:
        return [], 0
    clause, cargs = _channel_clause(channels, kinds)
    where = f"source=? AND {clause}"
    args = [sid, *cargs]
    if kind in ("video", "image", "book"):
        where += " AND kind=?"
        args.append(kind)
    else:
        where += " AND kind IN ('video','image','book')"   # 자막 파일은 목록에 안 보임 (영상에 붙음)
        where += (" AND NOT (kind='image' AND EXISTS (SELECT 1 FROM posters p WHERE p.source=videos.source"
                  " AND p.channel=videos.channel AND p.image=videos.msg))")
    if group and kind != "image":
        # 시리즈는 가장 최근 회차 하나만 (나머지 회차는 시리즈 화면에서)
        where += (" AND NOT EXISTS (SELECT 1 FROM series s WHERE s.source=videos.source AND s.channel=videos.channel"
                  " AND s.video=videos.msg AND s.rep=0)")
    if q:
        where += " AND (title LIKE ? OR caption LIKE ? OR file_name LIKE ?)"
        like = f"%{q}%"
        args += [like, like, like]
    order = {"latest": "date DESC, msg DESC", "oldest": "date ASC, msg ASC", "title": "title COLLATE NOCASE ASC"}.get(sort, "date DESC, msg DESC")
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM videos WHERE {where}", args).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM videos WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                            [*args, int(limit), int(offset)]).fetchall()
        prog = _progress_map(conn, sid, rows, user)
        posters = _poster_map(conn, sid, rows)
        series = _series_map(conn, sid, rows, user) if group else {}
    out = [_row(r, prog, posters) for r in rows]
    for d in out:
        s = series.get((d["channel"], d["msg"]))
        if s:
            d["series"] = s
    return out, total


# 큰 영상을 여러 파일로 나눠 올린 경우: "이름_0000", "이름_0001" / "이름.001" / "이름 part2" / "이름 CD2"
# (연도 "Movie.2026"과 헷갈리지 않게 숫자만 있는 꼬리는 0으로 시작하는 3~4자리만 본다)
_PART_RE = re.compile(r"^(.*?)[\s._-]*(?:(?:part|pt|cd|disc|disk)[\s._-]*0*(\d{1,3})|[._-](0\d{2,3}))$", re.I)
_VIDEO_EXT = {".mp4", ".mkv", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".webm", ".m4v", ".flv", ".mpg", ".mpeg"}


def split_part(name):
    """"Hope.2026.1080p-KyoGo_0001.mkv" -> ("hope.2026.1080p-kyogo", 1). 조각 이름이 아니면 None."""
    stem = (name or "").strip()
    root, ext = os.path.splitext(stem)
    if ext.lower() in _VIDEO_EXT:
        stem = root
    m = _PART_RE.match(stem)
    if not m or len(m.group(1)) < 3:
        return None
    return m.group(1).casefold(), int(m.group(2) or m.group(3))


def _part_key(r):
    return split_part(r["file_name"]) or split_part(r["title"])


def video_parts(sid, channel, msg, user=""):
    """같은 채널에서 이 영상과 한 묶음인 조각들 (번호순). 조각이 아니거나 하나뿐이면 []."""
    with db() as conn:
        r = conn.execute("SELECT * FROM videos WHERE source=? AND channel=? AND msg=?", (sid, channel, msg)).fetchone()
        key = r and r["kind"] == "video" and _part_key(r)
        if not key:
            return []
        like = key[0].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = conn.execute(
            "SELECT * FROM videos WHERE source=? AND channel=? AND kind='video' AND (file_name LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\') "
            "ORDER BY msg LIMIT 300", (sid, channel, like, like)).fetchall()
        rows = [x for x in rows if (_part_key(x) or ("",))[0] == key[0]]
        prog = _progress_map(conn, sid, rows, user)
    best = {}
    for x in rows:  # 같은 번호가 두 번 올라왔으면 나중 것
        best[_part_key(x)[1]] = x
    if len(best) < 2:
        return []
    out = []
    for i, num in enumerate(sorted(best)):
        d = _row(best[num], prog)
        out.append({"msg": d["msg"], "part": i + 1, "title": d["title"], "duration": d["duration"],
                    "position": d["position"], "watched": d["watched"]})
    return out


def random_video(sid, channels, user="", unwatched=False, exclude=(), kinds=None):
    """랜덤 재생: 고른 채널들의 영상 중 하나 (사진 제외). 없으면 None.

    unwatched=True면 다 본 영상은 뺀다. exclude는 방금 틀었던 (채널, 메시지)들 (연달아 같은 게 안 나오게).
    나눠 올린 영상의 중간 조각이 뽑히면 그 묶음의 첫 조각으로 바꾼다 (다음 조각은 이어서 재생됨).
    """
    channels = [c for c in channels if (kinds or {}).get(c, "") not in ("image", "webtoon")]  # 사진/웹툰 채널은 빼고
    if not channels:
        return None
    marks = ",".join("?" * len(channels))
    where = f"v.source=? AND v.channel IN ({marks}) AND v.kind='video'"
    args = [user, sid, *channels]
    if unwatched:
        where += " AND COALESCE(p.watched, 0)=0"
    skip = {(int(c), int(m)) for c, m in exclude}
    with db() as conn:
        rows = conn.execute(
            "SELECT v.* FROM videos v LEFT JOIN user_progress p ON p.source=v.source AND p.user=? "
            f"AND p.channel=v.channel AND p.msg=v.msg WHERE {where} ORDER BY RANDOM() LIMIT ?",
            [*args, len(skip) + 1]).fetchall()
    # 뺄 목록이 전부라면 (영상이 몇 개뿐) 그중에서라도
    pick = next((r for r in rows if (r["channel"], r["msg"]) not in skip), rows[0] if rows else None)
    if pick is None:
        return None
    parts = video_parts(sid, pick["channel"], pick["msg"], user=user)
    if parts and parts[0]["msg"] != pick["msg"]:
        return get_video(sid, pick["channel"], parts[0]["msg"], user=user)
    return get_video(sid, pick["channel"], pick["msg"], user=user)


def get_video(sid, channel, msg, user=""):
    with db() as conn:
        r = conn.execute("SELECT * FROM videos WHERE source=? AND channel=? AND msg=?", (sid, channel, msg)).fetchone()
        if not r:
            return None
        return _row(r, _progress_map(conn, sid, [r], user), _poster_map(conn, sid, [r]))


def continue_watching(sid, channels, limit=20, user=""):
    if not channels:
        return []
    marks = ",".join("?" * len(channels))
    with db() as conn:
        rows = conn.execute(
            f"""SELECT v.*, p.position, p.watched, p.updated, p.duration AS pages FROM user_progress p JOIN videos v
                ON v.source=p.source AND v.channel=p.channel AND v.msg=p.msg
                WHERE p.source=? AND p.user=? AND p.channel IN ({marks}) AND p.watched=0
                  AND (p.position > 30 OR (v.kind='book' AND p.position > 0))
                ORDER BY p.updated DESC LIMIT ?""", [sid, user, *channels, limit]).fetchall()
        posters = _poster_map(conn, sid, rows)
    out = []
    for r in rows:
        d = {**dict(r), "poster": posters.get((r["channel"], r["msg"]), 0)}
        pages = d.pop("pages", 0)
        if d["kind"] == "book":
            d["duration"] = pages
        out.append(d)
    return out


def save_progress(sid, channel, msg, position, duration, watched=None, user=""):
    position, duration = float(position or 0), float(duration or 0)
    if watched is None:
        watched = 1 if duration and position >= duration * 0.95 else 0
    with db() as conn:
        conn.execute(
            """INSERT INTO user_progress(source,user,channel,msg,position,duration,watched,updated) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(source,user,channel,msg) DO UPDATE SET position=excluded.position, duration=excluded.duration,
                 watched=excluded.watched, updated=excluded.updated""",
            (sid, user, channel, msg, 0 if watched else position, duration, int(bool(watched)), time.time()))


def get_probe(sid, channel, msg):
    """서버 변환 판단(ffprobe) 결과 - 파일은 안 바뀌므로 한 번 알아내면 계속 쓴다."""
    with db() as conn:
        r = conn.execute("SELECT data FROM probes WHERE source=? AND channel=? AND msg=?", (sid, channel, msg)).fetchone()
    if not r:
        return None
    data = json.loads(r[0])
    # 예전 판단에는 음성 트랙 정보가 없다 -> 한 번 다시 확인 (실패 결과는 그대로 둔다)
    if data.get("container") and "audio_tracks" not in data:
        return None
    return data


def save_probe(sid, channel, msg, data):
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO probes(source,channel,msg,data) VALUES(?,?,?,?)",
                     (sid, channel, msg, json.dumps(data, ensure_ascii=False)))


def index_counts(sid):
    """채널마다 색인한 영상 수 (상태 화면용)."""
    with db() as conn:
        rows = conn.execute("SELECT channel, COUNT(*) FROM videos WHERE source=? GROUP BY channel", (sid,)).fetchall()
    return {int(r[0]): int(r[1]) for r in rows}


def forget_source(sid):
    """소스를 지우면 그 색인/진행 기록도 지운다 (세션 파일은 TelegramSource.logout이)."""
    media_cache.STORE.drop_prefix(f"tg:{sid}:")
    with db() as conn:
        for table in ("videos", "sync_state", "peers", "progress", "user_progress", "probes", "posters", "subs", "series", "meta"):
            conn.execute(f"DELETE FROM {table} WHERE source=?", (sid,))
    shutil.rmtree(os.path.join(config_store.DATA_DIR, "telegram_images", sid), ignore_errors=True)
    shutil.rmtree(os.path.join(config_store.DATA_DIR, "telegram_books", sid), ignore_errors=True)


# ---------------------------------------------------------------------------
# 웹툰 압축 파일 (zip/cbz) - 안의 그림을 이름순(숫자는 숫자 크기로)으로 한 쪽씩
# ---------------------------------------------------------------------------
BOOK_PAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
                   ".gif": "image/gif", ".bmp": "image/bmp", ".avif": "image/avif"}
BOOK_PAGE_MAX = 50 * 1024 * 1024     # 쪽 하나 (압축을 풀었을 때) 한도


def _zip_name(info):
    """zip 안의 파일 이름. UTF-8 표시가 없는 이름은 cp437로 읽혀 있어서 우리말(cp949)로 다시 읽어 본다"""
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("cp949")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return unicodedata.normalize("NFC", name)


def _natural_key(name):
    return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", name.casefold()) if t]


def zip_pages(zf):
    """압축 파일 안의 그림 [(ZipInfo, 이름)] - 맥 찌꺼기(__MACOSX, ._파일)와 그림이 아닌 파일은 빼고 이름순"""
    pages = []
    for info in zf.infolist():
        name = _zip_name(info)
        base = name.rsplit("/", 1)[-1]
        if info.is_dir() or name.startswith("__MACOSX/") or base.startswith("."):
            continue
        if os.path.splitext(base.lower())[1] in BOOK_PAGE_TYPES:
            pages.append((info, name))
    pages.sort(key=lambda p: _natural_key(p[1]))
    return pages


def zip_read(zf, pages, n):
    """n번째 쪽 (bytes, mime)"""
    if not 0 <= n < len(pages):
        raise TgError("없는 쪽입니다.")
    info, name = pages[n]
    if info.file_size > BOOK_PAGE_MAX:
        raise TgError("그림이 너무 큽니다.")
    try:
        data = zf.read(info)
    except (RuntimeError, NotImplementedError, zipfile.BadZipFile) as e:   # 암호가 걸렸거나 모르는 압축 방식
        raise TgError(f"이 압축 파일의 그림을 풀지 못했습니다 ({e}).") from e
    return data, BOOK_PAGE_TYPES[os.path.splitext(name.lower())[1]]


def _open_zip(fileobj):
    try:
        return zipfile.ZipFile(fileobj)
    except (zipfile.BadZipFile, OSError) as e:
        raise TgError(f"압축 파일을 열지 못했습니다 ({e}). zip/cbz만 볼 수 있습니다.") from e


def book_pages(path):
    """(디스크의 압축 파일) 쪽 이름 목록 [(순번, 이름)]"""
    with _open_zip(path) as zf:
        return [(i, name) for i, (_info, name) in enumerate(zip_pages(zf))]


def book_page(path, n):
    with _open_zip(path) as zf:
        return zip_read(zf, zip_pages(zf), n)


# zipfile 작업은 전용 스레드에서: RemoteFile.read가 스레드를 잡은 채 이벤트 루프의 조각 받기를 기다리는데,
# 조각 캐시의 디스크 읽기도 기본 스레드 풀(asyncio.to_thread)을 쓰므로 같은 풀이면 서로 기다리다 멈출 수 있다
_ZIP_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ymp-zip")


async def _zip_job(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(_ZIP_POOL, fn, *args)


class RemoteFile(io.RawIOBase):
    """텔레그램 파일을 디스크 파일처럼 (seek/read) - zipfile이 목차(파일 맨 끝)와 필요한 그림 부분만 읽게.
    읽기는 media_cache 조각(512KB)으로 받고(받은 조각은 캐시에 남음), 앞쪽은 여러 조각씩 동시에 미리 받는다.
    zipfile은 동기 코드라 스레드에서 돌리고, 조각 받기는 이벤트 루프에 맡긴다."""

    def __init__(self, streamer, loop):
        super().__init__()
        self.st, self.loop, self.pos = streamer, loop, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.st.size + offset
        self.pos = max(0, self.pos)
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.st.size - self.pos
        n = max(0, min(n, self.st.size - self.pos))
        if not n:
            return b""
        data = asyncio.run_coroutine_threadsafe(self._get(self.pos, n), self.loop).result(300)
        self.pos += len(data)
        return data

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)

    async def _get(self, pos, n):
        buf = bytearray()
        async for piece in self.st.iter_range(pos, n):
            buf += piece
        return bytes(buf)


def set_has_thumb(sid, channel, msg):
    with db() as conn:
        conn.execute("UPDATE videos SET has_thumb=1 WHERE source=? AND channel=? AND msg=?", (sid, channel, msg))


# ---------------------------------------------------------------------------
# Telethon 연결
# ---------------------------------------------------------------------------
class TelegramSource:
    def __init__(self, src):
        self.sid = src["id"]
        self.api_id = str(src.get("api_id") or "").strip()
        self.api_hash = str(src.get("api_hash") or "").strip()
        self.phone = str(src.get("phone") or "").strip()
        self.set_channels(src)
        self.client = None
        self._connect_lock = asyncio.Lock()
        self._sync_locks = {}
        self._code_hash = None
        self._msgs = OrderedDict()  # (channel, msg) -> Message (썸네일/Range 요청마다 메시지를 다시 받지 않게)
        self._tasks = {}            # 뒤에서 도는 작업 (같은 일을 겹쳐 돌리지 않게 이름으로)
        self._thumb_sem = asyncio.Semaphore(THUMB_CONCURRENCY)
        self._thumb_inflight = {}
        self._dl_sem = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)  # 이 계정으로 동시에 받는 파일 조각 수
        self._zips = OrderedDict()  # (channel, msg) -> (열어 둔 ZipFile, 쪽 목록) - 웹툰 압축 파일
        self._zip_opening = {}      # (channel, msg) -> 여는 중 (목차 읽기, 같은 파일은 한 번만)
        if not fast_crypto():
            log.warning("cryptg가 없어 텔레그램 암호화를 순수 파이썬으로 처리합니다 (느림): pip install cryptg")

    def set_channels(self, src):
        """설정에서 고른 채널과 채널마다 정한 종류 (영상만 "video" / 사진만 "image" / 웹툰 "webtoon" / 영상+사진 "")"""
        chans = src.get("channels") or []
        self.channels = [int(c["id"]) for c in chans]
        self.kinds = {int(c["id"]): c.get("kind", "") for c in chans if c.get("kind") in CHANNEL_KINDS}

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
                return {"authorized": False, "phone": self.phone, "fast_crypto": fast_crypto()}
            me = await client.get_me()
            name = " ".join(x for x in (getattr(me, "first_name", ""), getattr(me, "last_name", "")) if x)
            return {"authorized": True, "name": name or getattr(me, "username", "") or "", "phone": self.phone,
                    "fast_crypto": fast_crypto()}
        except TgError as e:
            return {"authorized": False, "error": str(e), "phone": self.phone, "fast_crypto": fast_crypto()}

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
            items.append({"id": int(ent.id), "title": fix_text(getattr(ent, "title", "") or dialog.name),
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

    # ----- 뒤에서 돌리기 -----
    def background(self, key, make_coro):
        """같은 key의 작업이 이미 돌고 있으면 새로 띄우지 않는다. 실패는 로그만."""
        task = self._tasks.get(key)
        if task and not task.done():
            return task

        async def runner():
            try:
                await make_coro()
            except Exception as e:
                log.warning("telegram 뒤 작업 %s 실패: %s", key, e)

        task = self._tasks[key] = asyncio.ensure_future(runner())
        return task

    # ----- 색인 -----
    def _lock(self, channel):
        return self._sync_locks.setdefault(channel, asyncio.Lock())

    async def _scan(self, channel, **kwargs):
        client = await self.authorized_client()
        peer = await self.peer(channel)
        items, ids = [], []
        async for msg in client.iter_messages(peer, **kwargs):
            ids.append(int(msg.id))
            item = message_to_media(msg)
            if item:
                items.append(item)
                self._remember(channel, msg)
        upsert_videos(self.sid, channel, items)
        if items:
            link_posters(self.sid, channel)
            link_subs(self.sid, channel)
            link_series(self.sid, channel)
        # 새로 찾은 영상의 썸네일은 화면이 달라고 하기 전에 뒤에서 미리 받아 둔다
        wanted = [it["msg"] for it in items if it["has_thumb"]][:THUMB_PREFETCH]
        if wanted:
            self.background(("thumbs", channel, wanted[0]), lambda: self.prefetch_thumbs(channel, wanted))
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

    async def videos(self, channels, offset=0, limit=40, q="", sort="latest", fill=True, user="", kind="", group=False):
        """목록. 이미 색인한 채널은 DB에서 바로 돌려주고 새 영상 받기는 뒤에서 한다 (기다리지 않음).

        - 처음 보는 채널만 첫 SCAN_BATCH개를 받을 때까지 기다린다 (보여줄 게 없으니까)
        - 채널 하나를 최신순으로 내려 보다가 색인이 모자라면 그때만 오래된 쪽을 기다려서 채우고,
          다음 페이지 몫은 뒤에서 미리 훑어 둔다 -> 다음 스크롤은 바로 나온다
        - fill=False(홈 줄)면 오래된 쪽은 건드리지 않는다
        """
        channels = [c for c in channels if c in self.channels] if self.channels else []
        syncing = False
        for c in channels:
            st = sync_state(self.sid, c)
            if not st["newest"]:
                try:
                    await self.sync_latest(c)
                except TgError:
                    raise
                except Exception as e:  # 네트워크 문제여도 다른 채널은 보여준다
                    log.warning("telegram 첫 색인 %s 실패: %s", c, e)
            elif time.time() - st["latest_at"] >= LATEST_SYNC_SEC:
                self.background(("latest", c), lambda c=c: self.sync_latest(c))
                syncing = True
        rows, total = query_videos(self.sid, channels, offset, limit, q, sort, user, kind, self.kinds, group)
        if fill and len(channels) == 1 and not q and sort == "latest":
            ch = channels[0]
            for _ in range(OLDER_BATCHES_PER_PAGE):
                if len(rows) >= limit or not await self.sync_older(ch):
                    break
                rows, total = query_videos(self.sid, channels, offset, limit, q, sort, user, kind, self.kinds, group)
            ahead = total - (offset + len(rows))
            if ahead < limit * 2 and not sync_state(self.sid, ch)["complete"]:
                self.background(("older", ch), lambda: self.sync_older(ch))
        complete = all(sync_state(self.sid, c)["complete"] for c in channels)
        return {"items": rows, "total": total, "more": (offset + len(rows) < total) or not complete,
                "syncing": syncing}

    # ----- 파일 -----
    def _remember(self, channel, msg):
        if (getattr(msg, "document", None) or getattr(msg, "video", None) or getattr(msg, "photo", None)) is None:
            return
        key = (channel, int(msg.id))
        self._msgs[key] = msg
        self._msgs.move_to_end(key)
        while len(self._msgs) > MSG_CACHE_SIZE:
            self._msgs.popitem(last=False)

    async def _message(self, channel, msg, fresh=False):
        key = (channel, int(msg))
        if not fresh and key in self._msgs:
            self._msgs.move_to_end(key)
            return self._msgs[key]
        client = await self.authorized_client()
        m = await client.get_messages(await self.peer(channel), ids=int(msg))
        if not m:
            raise TgError("텔레그램에서 이 메시지를 찾을 수 없습니다 (지워졌을 수 있음).")
        self._remember(channel, m)
        return m

    async def _document(self, channel, msg, fresh=False):
        m = await self._message(channel, msg, fresh=fresh)
        doc = getattr(m, "document", None) or getattr(m, "video", None)
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

    async def iter_cached(self, channel, msg, start, length, size, duration=0):
        """iter_range와 같지만 media_cache를 거친다: 512KB 조각으로 받아 디스크에 남기고(캐시를 켰으면),
        재생 중에는 앞쪽 몇 분을 여러 조각씩 동시에 미리 받는다. 경계에 맞춘 요청이라 Telethon이 앞부분을
        버리려고 더 받는 일도 없다."""
        st = self._streamer(channel, msg, size, duration)
        async for piece in st.iter_range(start, length):
            yield piece

    async def warm_head(self, channel, msg, size, duration=0, nbytes=WARM_HEAD_BYTES):
        """앞부분 몇 MB만 받아 캐시에 둔다 (곧 이어 볼 다음 회차). 그 뒤쪽 미리 받기는 하지 않는다"""
        st = self._streamer(channel, msg, size, duration)
        last = min(st.last_block, max(0, (min(int(size), nbytes) - 1) // media_cache.BLOCK))
        await asyncio.gather(*(st.block(bi) for bi in range(last + 1)))

    def _streamer(self, channel, msg, size, duration=0):
        async def fetch(bi):
            off = bi * media_cache.BLOCK
            want = min(media_cache.BLOCK, size - off)
            buf = bytearray()
            async for chunk in self.iter_range(channel, msg, off, want):
                buf += chunk
            if len(buf) != want:
                raise TgError("텔레그램에서 파일 조각을 다 받지 못했습니다.")
            return bytes(buf)

        # 크기까지 키에 넣는다 - 메시지를 고쳐 다른 파일로 바꾸면 다른 캐시
        st = media_cache.streamer(f"tg:{self.sid}:{int(channel)}:{int(msg)}:{int(size)}", size, fetch,
                                  meta={"source": self.sid, "title": f"{channel}/{msg}"},
                                  bytes_per_sec=(size / duration) if duration else 0,
                                  concurrency=3, burst=6, limiter=self._dl_sem)
        return st

    def _thumb_path(self, channel, msg):
        return os.path.join(config_store.DATA_DIR, "telegram_thumbs", self.sid, f"{int(channel)}_{int(msg)}.jpg")

    def _sub_path(self, channel, msg):
        return os.path.join(config_store.DATA_DIR, "telegram_subs", self.sid, f"{int(channel)}_{int(msg)}.bin")

    async def subtitle_bytes(self, channel, msg):
        """자막 파일 내용 (작아서 통째로 받고 data/telegram_subs/에 둔다)"""
        path = self._sub_path(channel, msg)
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
        client = await self.authorized_client()
        m = await self._message(channel, msg)
        data = await client.download_media(m, file=bytes)
        if not data:
            raise TgError("자막 파일을 받지 못했습니다.")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
        return data

    async def thumbnail(self, channel, msg):
        """썸네일 파일 경로. 같은 썸네일을 여러 요청이 동시에 달라고 해도 한 번만 받는다."""
        path = self._thumb_path(channel, msg)
        if os.path.exists(path):
            return path
        key = (channel, int(msg))
        fut = self._thumb_inflight.get(key)
        if fut is None:
            fut = self._thumb_inflight[key] = asyncio.ensure_future(self._fetch_thumb(channel, msg, path))
            fut.add_done_callback(lambda _f: self._thumb_inflight.pop(key, None))
        return await asyncio.shield(fut)

    async def _fetch_thumb(self, channel, msg, path):
        async with self._thumb_sem:
            if os.path.exists(path):
                return path
            client = await self.authorized_client()
            m = await self._message(channel, msg)
            data = await client.download_media(m, file=bytes, thumb=-1)
            if not data:
                raise TgError("미리보기 이미지가 없습니다.")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            return path

    # ----- 사진 원본 (뷰어) -----
    IMAGE_CACHE_MAX = 1024 * 1024 * 1024  # 받아 둔 사진 원본은 합쳐서 1GB까지 (넘으면 오래 안 본 것부터 지움)

    def _image_dir(self):
        return os.path.join(config_store.DATA_DIR, "telegram_images", self.sid)

    async def image(self, channel, msg, row):
        """사진 원본 파일 경로. 처음 한 번 텔레그램에서 받아 디스크에 두고, 같은 사진을 동시에 달라고 해도 한 번만."""
        ext = {"image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "image/bmp": ".bmp",
               "image/avif": ".avif", "image/heic": ".heic", "image/heif": ".heif"}.get(row.get("mime"), ".jpg")
        path = os.path.join(self._image_dir(), f"{int(channel)}_{int(msg)}{ext}")
        if os.path.exists(path):
            os.utime(path)  # 최근에 본 사진 (캐시 정리 순서)
            return path
        if int(row.get("size") or 0) > IMAGE_MAX_BYTES:
            raise TgError(f"이미지 파일이 너무 큽니다 ({int(row['size']) // (1024 * 1024)}MB). 텔레그램 앱에서 열어 주세요.")
        key = ("img", channel, int(msg))
        fut = self._thumb_inflight.get(key)
        if fut is None:
            fut = self._thumb_inflight[key] = asyncio.ensure_future(self._fetch_image(channel, msg, path))
            fut.add_done_callback(lambda _f: self._thumb_inflight.pop(key, None))
        return await asyncio.shield(fut)

    async def _fetch_image(self, channel, msg, path):
        from telethon.errors import FileReferenceExpiredError
        client = await self.authorized_client()
        async with self._dl_sem:
            try:
                data = await client.download_media(await self._message(channel, msg), file=bytes)
            except FileReferenceExpiredError:
                data = await client.download_media(await self._message(channel, msg, fresh=True), file=bytes)
        if not data:
            raise TgError("텔레그램에서 이미지를 받지 못했습니다.")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        await asyncio.to_thread(self._trim_images)
        return path

    def _trim_images(self):
        folder = self._image_dir()
        try:
            files = [(e.stat().st_mtime, e.stat().st_size, e.path) for e in os.scandir(folder) if e.is_file()]
        except OSError:
            return
        total = sum(f[1] for f in files)
        for _mtime, size, path in sorted(files):
            if total <= self.IMAGE_CACHE_MAX:
                break
            try:
                os.remove(path)
                total -= size
            except OSError:
                pass

    # ----- 웹툰 압축 파일 -----
    # 통째로 받지 않는다: 파일 맨 끝의 목차만 먼저 받아 쪽 목록을 만들고, 보는 쪽의 그림 부분만 받는다 (첫 쪽이 1~2초).
    # 받는 것은 media_cache 조각이라 디스크 캐시에 남고, 앞쪽은 여러 조각씩 동시에 미리 받는다 (영상 재생과 같은 길).
    ZIPS_OPEN_MAX = 12

    def book_path(self, channel, msg):
        """예전 방식으로 통째로 받아 둔 파일 (있으면 그것을 씀)"""
        return os.path.join(config_store.DATA_DIR, "telegram_books", self.sid, f"{int(channel)}_{int(msg)}.zip")

    async def book_open(self, channel, msg, row):
        """(ZipFile, 쪽 목록 [(ZipInfo, 이름)])"""
        key = (int(channel), int(msg))
        hit = self._zips.get(key)
        if hit:
            self._zips.move_to_end(key)
            return hit
        fut = self._zip_opening.get(key)
        if fut is None:
            fut = self._zip_opening[key] = asyncio.ensure_future(self._book_open(channel, msg, row))
            fut.add_done_callback(lambda _f: self._zip_opening.pop(key, None))
        return await asyncio.shield(fut)

    async def _book_open(self, channel, msg, row):
        key = (int(channel), int(msg))
        path = self.book_path(channel, msg)
        if os.path.exists(path):
            zf = await _zip_job(_open_zip, path)
        else:
            size = int(row.get("size") or 0)
            if size > BOOK_MAX_BYTES:
                raise TgError(f"압축 파일이 너무 큽니다 ({size // (1024 * 1024)}MB). 텔레그램 앱에서 받아 주세요.")
            if size <= 0:
                raise TgError("압축 파일 크기를 알 수 없습니다.")
            await self.authorized_client()
            remote = RemoteFile(self._streamer(channel, msg, size), asyncio.get_running_loop())
            zf = await _zip_job(_open_zip, remote)
        pages = zip_pages(zf)
        if not pages:
            zf.close()
            raise TgError("압축 파일 안에 그림(jpg/png/webp/gif)이 없습니다.")
        self._zips[key] = (zf, pages)
        while len(self._zips) > self.ZIPS_OPEN_MAX:
            _k, (old, _p) = self._zips.popitem(last=False)
            old.close()
        if not os.path.exists(self._thumb_path(channel, msg)):
            asyncio.ensure_future(self._book_cover(channel, msg, row))
        return zf, pages

    async def book_page(self, channel, msg, row, n):
        zf, pages = await self.book_open(channel, msg, row)
        return await _zip_job(zip_read, zf, pages, n)

    async def book_warm(self, channel, msg, row, first=3):
        """곧 볼 다음 화: 목차와 앞쪽 몇 장을 미리 받아 둔다"""
        try:
            zf, pages = await self.book_open(channel, msg, row)
            for n in range(min(first, len(pages))):
                await _zip_job(zip_read, zf, pages, n)
        except Exception as e:  # 미리 받기라 실패해도 볼 때 다시
            log.info("telegram 웹툰 %s/%s 미리 받기 실패: %s", channel, msg, e)

    async def _book_cover(self, channel, msg, row):
        """썸네일이 없는 압축 파일은 첫 쪽을 카드 그림으로"""
        thumb = self._thumb_path(channel, msg)
        try:
            data, _mime = await self.book_page(channel, msg, row, 0)
        except Exception:
            return
        if len(data) > 4 * 1024 * 1024 or os.path.exists(thumb):
            return
        os.makedirs(os.path.dirname(thumb), exist_ok=True)
        with open(thumb + ".tmp", "wb") as f:
            f.write(data)
        os.replace(thumb + ".tmp", thumb)
        set_has_thumb(self.sid, channel, msg)

    async def prefetch_thumbs(self, channel, msgs):
        for mid in msgs:
            if not os.path.exists(self._thumb_path(channel, mid)):
                try:
                    await self.thumbnail(channel, mid)
                except Exception as e:  # 하나 실패해도 나머지는 계속
                    log.debug("telegram 썸네일 미리 받기 %s/%s 실패: %s", channel, mid, e)
