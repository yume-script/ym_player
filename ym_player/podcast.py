"""팟캐스트 소스 - RSS 피드를 읽어 프로그램·에피소드 목록을 만들고, 사용자마다 들은 위치를 기억한다.

소스 설정: {"id", "type": "podcast", "name", "enabled", "feeds": [{"id", "url", "title"}]}
    feed id는 주소에서 만든다 (주소가 같으면 id도 같아서 들은 위치가 안 깨진다).
피드: 서버가 받아서 메모리와 data/podcast_cache/에 둔다 (FEED_TTL마다 다시 받음, 서버를 다시 켜도 바로 보이게).
들은 위치: data/podcast.db (사용자, 소스, 피드, 에피소드) -> 위치·길이·다 들음·마지막으로 들은 시각
에피소드 id는 guid(없으면 오디오 주소)에서 만든다.
오디오는 /api/podcast/{sid}/audio/{feed}/{ep}로 서버가 중계한다 (피드에 적힌 주소만 - 아무 주소나 중계하지 않음).
프로그램 찾기는 iTunes 검색 API (https://itunes.apple.com/search?media=podcast).
"""
import email.utils
import hashlib
import html
import json
import os
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from datetime import timezone

from . import config_store

MAX_FEEDS = 300
MAX_EPISODES = 1000           # 피드 하나에서 기억할 에피소드 수 (최신 것부터)
FEED_TTL = 30 * 60
CACHE_VERSION = 2          # 피드 읽는 방법이 바뀌면 올린다 -> 서버에 남은 예전 캐시는 버리고 다시 받는다
FEED_MAX_BYTES = 15 * 1024 * 1024
SUMMARY_MAX = 4000

NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media": "http://search.yahoo.com/mrss/",
    "atom": "http://www.w3.org/2005/Atom",
    "googleplay": "http://www.google.com/schemas/play-podcasts/1.0",
}
# 피드마다 네임스페이스 주소를 조금씩 다르게 적는다 (대소문자·https·끝의 /) -> 읽기 전에 위 주소로 맞춘다
_NS_FIX = [(re.compile(rb"""(["'])\s*https?://www\.itunes\.com/dtds/podcast-1\.0\.dtd/?\s*(["'])""", re.I), NS["itunes"]),
           (re.compile(rb"""(["'])\s*https?://purl\.org/rss/1\.0/modules/content/?\s*(["'])""", re.I), NS["content"]),
           (re.compile(rb"""(["'])\s*https?://search\.yahoo\.com/mrss/?\s*(["'])""", re.I), NS["media"]),
           (re.compile(rb"""(["'])\s*https?://www\.google\.com/schemas/play-podcasts/1\.0/?\s*(["'])""", re.I), NS["googleplay"])]


def feed_id(url):
    return hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:10]


def clean_feeds(value):
    """설정의 피드 목록 검사. http(s) 주소만, 같은 주소는 하나만"""
    out, seen = [], set()
    for f in value if isinstance(value, list) else []:
        if isinstance(f, str):
            f = {"url": f}
        if not isinstance(f, dict):
            continue
        url = str(f.get("url") or "").strip()
        if not url.startswith(("http://", "https://")) or len(url) > 1000:
            continue
        fid = feed_id(url)
        if fid in seen:
            continue
        seen.add(fid)
        out.append({"id": fid, "url": url, "title": str(f.get("title") or "").strip()[:200]})
    return out[:MAX_FEEDS]


# ---------------------------------------------------------------------------
# RSS 읽기
# ---------------------------------------------------------------------------
def _text(el, path):
    x = el.find(path, NS)
    return (x.text or "").strip() if x is not None and x.text else ""


def strip_html(value):
    """설명 HTML -> 읽을 수 있는 글 (줄바꿈은 살린다)"""
    value = str(value or "")
    value = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n\s*\n+", "\n\n", value)
    return "\n".join(line.strip() for line in value.split("\n")).strip()[:SUMMARY_MAX]


def parse_duration(value):
    """itunes:duration ("3600", "1:02:03", "62:03") -> 초 (모르면 0)"""
    value = str(value or "").strip()
    if not value:
        return 0
    try:
        if ":" in value:
            sec = 0
            for part in value.split(":"):
                sec = sec * 60 + float(part or 0)
            return int(sec)
        return int(float(value))
    except ValueError:
        return 0


def parse_date(value):
    """pubDate (RFC 822) -> 유닉스 시각 (모르면 0)"""
    value = str(value or "").strip()
    if not value:
        return 0
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return 0
    if d is None:
        return 0
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp())


def _image(el):
    x = el.find("itunes:image", NS)
    if x is not None and x.get("href"):
        return x.get("href").strip()
    url = _text(el, "image/url")
    if url:
        return url
    x = el.find("media:thumbnail", NS)
    if x is not None and x.get("url"):
        return x.get("url").strip()
    x = el.find("googleplay:image", NS)
    return (x.get("href") or "").strip() if x is not None else ""


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_xml(data):
    """네임스페이스 주소를 맞추고, 선언 없이 쓴 접두어(itunes: 등)는 선언을 넣어서 읽는다"""
    for rx, uri in _NS_FIX:
        data = rx.sub(lambda m: m.group(1) + uri.encode() + m.group(2), data)
    for _ in range(len(NS) + 1):
        try:
            return ET.fromstring(data)
        except ET.ParseError as e:
            m = re.search(r"unbound prefix", str(e))
            missing = [p for p in NS if (f"<{p}:".encode() in data or f"</{p}:".encode() in data)
                       and f"xmlns:{p}=".encode() not in data]
            if not m or not missing:
                raise ValueError(f"RSS 형식이 아닙니다 ({e})") from None
            decl = f' xmlns:{missing[0]}="{NS[missing[0]]}"'.encode()
            data = re.sub(rb"<(rss|feed)\b", lambda x: x.group(0) + decl, data, count=1)
    raise ValueError("RSS 형식이 아닙니다")


def parse_feed(data):
    """RSS bytes -> {"title", "author", "summary", "image", "link", "serial", "episodes": [...]}.
    에피소드: {"id", "title", "date", "duration", "url", "mime", "size", "image", "summary", "season", "episode", "link"}
    오디오(enclosure)가 없는 항목은 뺀다. 최신 것부터."""
    root = _parse_xml(data)
    ch = root.find("channel")
    if ch is None:
        if root.tag.endswith("html") or b"<html" in data[:2000].lower():
            raise ValueError("RSS 대신 웹 페이지(HTML)가 왔습니다. 팟캐스트 RSS 주소를 넣어 주세요.")
        raise ValueError("RSS 형식이 아닙니다 (<channel> 없음)")
    show = {
        "title": _text(ch, "title")[:200] or "이름 없는 팟캐스트",
        "author": (_text(ch, "itunes:author") or _text(ch, "managingEditor"))[:200],
        "summary": strip_html(_text(ch, "itunes:summary") or _text(ch, "description")),
        "image": _image(ch),
        "link": _text(ch, "link")[:500],
        "serial": _text(ch, "itunes:type").lower() == "serial",
    }
    eps, seen = [], set()
    for it in ch.findall("item"):
        enc = it.find("enclosure")
        url = (enc.get("url") or "").strip() if enc is not None else ""
        if not url.startswith(("http://", "https://")):
            continue
        guid = _text(it, "guid") or url
        eid = hashlib.sha1(guid.encode("utf-8")).hexdigest()[:12]
        if eid in seen:
            continue
        seen.add(eid)
        mime = (enc.get("type") or "").strip().lower()
        eps.append({
            "id": eid,
            "title": _text(it, "title")[:300] or _text(it, "itunes:title")[:300] or "제목 없음",
            "date": parse_date(_text(it, "pubDate")),
            "duration": parse_duration(_text(it, "itunes:duration")),
            "url": url[:2000],
            "mime": mime,
            "video": mime.startswith("video/") or bool(re.search(r"\.(mp4|m4v|mov)(\?|$)", url, re.I)),
            "size": _int(enc.get("length")) or 0,
            "image": _image(it),
            "summary": strip_html(_text(it, "content:encoded") or _text(it, "description") or _text(it, "itunes:summary")),
            "season": _int(_text(it, "itunes:season")),
            "episode": _int(_text(it, "itunes:episode")),
            "link": _text(it, "link")[:500],
        })
    eps.sort(key=lambda e: e["date"], reverse=True)
    show["episodes"] = eps[:MAX_EPISODES]
    return show


class FeedHTTPError(Exception):
    """피드 주소가 오류 상태 코드를 돌려줌 - 알아보기 쉬운 말로"""

    def __init__(self, status):
        self.status = status
        if status in (404, 410):
            text = (f"RSS 주소에서 HTTP {status}(없는 페이지)가 왔습니다. 프로그램이 RSS 주소를 옮겼거나 방송을 끝냈을 수 있습니다. "
                    "프로그램 홈페이지나 팟빵 등에서 지금 쓰는 RSS 주소를 확인해 넣어 주세요.")
        elif status in (401, 403):
            text = f"RSS 서버가 거절했습니다 (HTTP {status}). 로그인이 필요한 유료·비공개 피드이거나 서버 접속을 막는 곳입니다."
        elif status == 429:
            text = "RSS 서버가 요청이 너무 잦다고 합니다 (HTTP 429). 잠시 뒤 다시 해 주세요."
        else:
            text = f"RSS 서버 응답 HTTP {status}"
        super().__init__(text)


# ---------------------------------------------------------------------------
# RSS 주소 찾기 (웹 페이지 주소를 넣었거나, RSS 주소가 옮겨져 404일 때)
# ---------------------------------------------------------------------------
_LINK_RE = re.compile(r"<link\b[^>]*>", re.I)
_A_RE = re.compile(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*>", re.I)
_ATTR_RE = re.compile(r"([a-zA-Z_:-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")


def discover_feeds(page, base_url):
    """웹 페이지 HTML -> RSS 주소 후보 (먼저 <link rel=alternate type=application/rss+xml>, 다음 rss·feed가 든 링크)"""
    from urllib.parse import urljoin
    text = page.decode("utf-8", "replace") if isinstance(page, bytes) else str(page or "")
    out = []

    def add(href):
        href = html.unescape(href or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            return
        url = urljoin(base_url, href)
        if url.startswith(("http://", "https://")) and url not in out and url != base_url:
            out.append(url)
    for tag in _LINK_RE.findall(text):
        attrs = {m.group(1).lower(): (m.group(2) if m.group(2) is not None else m.group(3)) for m in _ATTR_RE.finditer(tag)}
        if "alternate" in (attrs.get("rel") or "").lower() and re.search(r"(rss|atom)\+xml", attrs.get("type") or "", re.I):
            add(attrs.get("href"))
    for href in _A_RE.findall(text):
        if re.search(r"(^|[/._?=-])(rss|feed)([/._?=-]|$)", href, re.I):
            add(href)
    return out[:5]


def page_candidates(url):
    """RSS를 못 받았을 때 RSS 링크를 찾아볼 웹 페이지들: 그 주소 자체, 팟빵이면 채널 페이지"""
    out = [url]
    if "podbbang.com" in url:
        m = re.search(r"(?:[?&](?:id|channel)=|/(?:ch|channels|channel)/(?:rss/)?)(\d{3,})", url)
        if m:
            out.append(f"https://www.podbbang.com/channels/{m.group(1)}")
    return out


# ---------------------------------------------------------------------------
# 피드 캐시 (메모리 + 파일)
# ---------------------------------------------------------------------------
def _cache_dir():
    return os.path.join(config_store.DATA_DIR, "podcast_cache")


class FeedCache:
    def __init__(self):
        self.mem = {}   # 주소 -> {"at", "feed", "error"}
        self._loaded = set()

    def _path(self, url):
        return os.path.join(_cache_dir(), f"{feed_id(url)}.json")

    def get(self, url):
        """메모리(없으면 파일)에 있는 것. {"at", "feed", "error"} 또는 None"""
        if url not in self.mem and url not in self._loaded:
            self._loaded.add(url)
            try:
                with open(self._path(url), encoding="utf-8") as f:
                    got = json.load(f)
                if isinstance(got, dict) and isinstance(got.get("feed"), dict) and got.get("v") == CACHE_VERSION:
                    self.mem[url] = {"at": float(got.get("at") or 0), "feed": got["feed"], "error": ""}
            except (OSError, ValueError):
                pass
        return self.mem.get(url)

    def fresh(self, url):
        e = self.get(url)
        return bool(e and time.time() - e["at"] < FEED_TTL)

    def put(self, url, feed):
        self.mem[url] = {"at": time.time(), "feed": feed, "error": ""}
        try:
            os.makedirs(_cache_dir(), exist_ok=True)
            tmp = self._path(url) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"v": CACHE_VERSION, "at": self.mem[url]["at"], "feed": feed}, f, ensure_ascii=False)
            os.replace(tmp, self._path(url))
        except OSError:
            pass

    def fail(self, url, error):
        """받기 실패 - 예전에 받은 것이 있으면 그대로 쓰고, 잠깐(5분) 뒤에 다시"""
        e = self.get(url)
        if e:
            e["error"] = error
            e["at"] = time.time() - FEED_TTL + 5 * 60
        else:
            self.mem[url] = {"at": time.time() - FEED_TTL + 5 * 60, "feed": None, "error": error}

    def forget_except(self, urls):
        keep = {feed_id(u) for u in urls}
        for url in list(self.mem):
            if feed_id(url) not in keep:
                del self.mem[url]
        try:
            for name in os.listdir(_cache_dir()):
                if name.endswith(".json") and name[:-5] not in keep:
                    os.remove(os.path.join(_cache_dir(), name))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 들은 위치 (data/podcast.db)
# ---------------------------------------------------------------------------
_db_lock = threading.Lock()


def _db():
    os.makedirs(config_store.DATA_DIR, exist_ok=True)
    con = sqlite3.connect(os.path.join(config_store.DATA_DIR, "podcast.db"), timeout=10)
    con.execute("""CREATE TABLE IF NOT EXISTS progress (
        user TEXT NOT NULL, sid TEXT NOT NULL, feed TEXT NOT NULL, ep TEXT NOT NULL,
        position REAL NOT NULL DEFAULT 0, duration REAL NOT NULL DEFAULT 0,
        watched INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (user, sid, feed, ep))""")
    return con


def progress_of(user, sid, feeds=None):
    """{(feed, ep): {"position", "duration", "watched", "updated"}}. feeds가 있으면 그 피드만"""
    with _db_lock:
        con = _db()
        try:
            rows = con.execute("SELECT feed, ep, position, duration, watched, updated FROM progress WHERE user=? AND sid=?",
                               (user, sid)).fetchall()
        finally:
            con.close()
    want = set(feeds) if feeds is not None else None
    return {(f, e): {"position": p, "duration": d, "watched": bool(w), "updated": u}
            for f, e, p, d, w, u in rows if want is None or f in want}


def save_progress(user, sid, feed, ep, position, duration=0, watched=None):
    """들은 위치 저장. 끝 무렵(남은 시간이 30초와 길이의 5% 중 작은 것 이하)까지 들었으면 다 들은 것으로"""
    position = max(0.0, float(position or 0))
    duration = max(0.0, float(duration or 0))
    if watched is None:
        watched = bool(duration and duration - position <= min(30.0, duration * 0.05))
        if watched:
            position = 0.0
    elif watched:
        position = 0.0
    with _db_lock:
        con = _db()
        try:
            old = con.execute("SELECT duration FROM progress WHERE user=? AND sid=? AND feed=? AND ep=?",
                              (user, sid, feed, ep)).fetchone()
            if old and not duration:
                duration = old[0]
            con.execute("""INSERT INTO progress (user, sid, feed, ep, position, duration, watched, updated)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(user, sid, feed, ep) DO UPDATE SET
                             position=excluded.position, duration=excluded.duration,
                             watched=excluded.watched, updated=excluded.updated""",
                        (user, sid, feed, ep, position, duration, int(bool(watched)), time.time()))
            con.commit()
        finally:
            con.close()


def set_watched_many(user, sid, feed, eps, watched):
    with _db_lock:
        con = _db()
        try:
            now = time.time()
            con.executemany("""INSERT INTO progress (user, sid, feed, ep, position, duration, watched, updated)
                               VALUES (?, ?, ?, ?, 0, 0, ?, ?)
                               ON CONFLICT(user, sid, feed, ep) DO UPDATE SET
                                 position=0, watched=excluded.watched, updated=excluded.updated""",
                            [(user, sid, feed, ep, int(bool(watched)), now) for ep in eps])
            con.commit()
        finally:
            con.close()


def forget_source(sid):
    with _db_lock:
        con = _db()
        try:
            con.execute("DELETE FROM progress WHERE sid=?", (sid,))
            con.commit()
        finally:
            con.close()


# ---------------------------------------------------------------------------
# 브라우저로 보낼 모양
# ---------------------------------------------------------------------------
def public_episode(fid, ep, prog=None, show=None):
    """오디오 원래 주소는 내보내지 않는다 (서버 중계 주소로만)"""
    p = (prog or {}).get((fid, ep["id"])) or {}
    out = {k: ep[k] for k in ("id", "title", "date", "duration", "mime", "video", "size", "image", "summary", "season", "episode", "link")
           if k in ep}
    out.update(feed=fid, position=p.get("position", 0), watched=p.get("watched", False), updated=p.get("updated", 0))
    if p.get("duration") and not out.get("duration"):
        out["duration"] = int(p["duration"])
    if show:
        out["show"] = show.get("title", "")
        out["show_image"] = show.get("image", "")
    return out


def public_show(f, feed, prog=None, error=""):
    eps = (feed or {}).get("episodes") or []
    unplayed = sum(1 for e in eps if not (prog or {}).get((f["id"], e["id"]), {}).get("watched"))
    return {
        "id": f["id"], "url": f["url"],
        "title": (feed or {}).get("title") or f.get("title") or f["url"],
        "author": (feed or {}).get("author", ""), "image": (feed or {}).get("image", ""),
        "summary": (feed or {}).get("summary", ""), "link": (feed or {}).get("link", ""),
        "serial": bool((feed or {}).get("serial")), "count": len(eps),
        "latest": eps[0]["date"] if eps else 0, "unplayed": unplayed, "error": error, "loaded": feed is not None,
    }


def pick_search(results):
    """iTunes 검색 결과 -> [{"title", "author", "url", "image", "genre", "count"}] (RSS 주소가 있는 것만)"""
    out = []
    for r in results or []:
        url = str(r.get("feedUrl") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        out.append({"title": r.get("collectionName") or r.get("trackName") or "", "author": r.get("artistName") or "",
                    "url": url, "image": r.get("artworkUrl600") or r.get("artworkUrl100") or "",
                    "genre": r.get("primaryGenreName") or "", "count": r.get("trackCount") or 0})
    return out
