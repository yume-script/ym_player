"""IPTV 채팅 패널의 채팅 주소 찾기 - 채팅방 주소(토큰)가 자주 바뀌는 곳을 위해.

IPTV 소스 설정 (config_store):
    chat_url     채팅 페이지 주소를 직접 (있으면 이것만 쓴다 - 찾지 않음)
    chat_site    채팅이 붙어 있는 사이트 페이지 주소 (그 HTML에서 채팅 주소를 찾는다)
    chat_notice  사이트 주소를 공지하는 텔레그램 공개 채널 (https://t.me/s/채널) - 최신 글의 링크부터 사이트 후보로
    chat_host    채팅 주소에 들어 있어야 하는 호스트 (예: bizchataicloud.com) - 이 호스트의 주소만 채팅으로 띄운다

찾은 채팅 주소는 브라우저가 바로 iframe으로 연다 (서버는 채팅을 중계하지 않음). 사이트의 영상은 받지 않는다 -
페이지 HTML에서 채팅 주소 한 줄만 찾는다.
SSRF: 서버가 받는 주소는 http(s)만, 접속할 때 DNS로 얻은 주소가 사설·루프백·링크로컬·예약 주소면 거절한다
(리다이렉트도 같은 연결기를 거치므로 매번 검사 - 확인과 접속 사이에 DNS가 바뀌는 rebinding도 막힌다).
"""
import asyncio
import html
import ipaddress
import json
import os
import re
import threading
import time

from aiohttp import ClientSession, ClientTimeout, TCPConnector
from aiohttp.abc import AbstractResolver
from aiohttp.resolver import DefaultResolver
from yarl import URL

CACHE_SEC = 10 * 60
MAX_PAGE = 2 * 1024 * 1024
MAX_SITES = 6            # 공지에서 찾은 사이트 후보를 몇 개까지 열어 볼지 (최신 글부터)
ALLOW_PRIVATE = os.environ.get("MP_CHAT_ALLOW_PRIVATE") == "1"   # 테스트에서만 켠다 (로컬 가짜 서버)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
_TG_HOSTS = ("t.me", "telegram.me", "telegram.org", "telesco.pe", "cdn-telegram.org")


class ChatFindError(Exception):
    pass


# ---------------------------------------------------------------------------
# 보내 온 채팅 주소 - 서버가 사이트를 열 수 없을 때(망에서 막힘 등) 관리자가 자기 브라우저로 사이트를 열고
# 북마크 [채팅 주소 보내기]로 보낸 것 (data/chat_sent.json: {소스 id: {"url", "at"}})
# ---------------------------------------------------------------------------
_sent_lock = threading.Lock()


def _sent_path():
    from . import config_store
    return os.path.join(config_store.DATA_DIR, "chat_sent.json")


def sent_all():
    try:
        with open(_sent_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def sent_url(sid):
    """(주소, 보낸 시각) 또는 None"""
    x = sent_all().get(sid)
    return (x["url"], float(x.get("at") or 0)) if isinstance(x, dict) and x.get("url") else None


def check_sent(url, chat_host):
    """보내 온 주소가 쓸 수 있는 채팅 주소인지 (http(s)이고 설정한 채팅 호스트) - 아니면 ChatFindError"""
    try:
        u = URL(str(url or "").strip())
    except (ValueError, TypeError):
        raise ChatFindError("주소 형식이 아닙니다") from None
    if u.scheme not in ("http", "https") or not u.host:
        raise ChatFindError("http(s) 주소만 받습니다")
    if not chat_host or not _host_match(u.host.lower(), chat_host):
        raise ChatFindError(f"설정한 채팅 호스트({chat_host or '없음'})의 주소가 아닙니다")
    return str(u)[:2000]


def save_sent(sid, url):
    from . import config_store
    with _sent_lock:
        data = sent_all()
        data[sid] = {"url": url, "at": time.time()}
        os.makedirs(config_store.DATA_DIR, exist_ok=True)
        tmp = _sent_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _sent_path())


def _blocked(ip):
    ip = ipaddress.ip_address(ip)
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
            or ip.is_unspecified or (ip.version == 6 and ip.ipv4_mapped and _blocked(ip.ipv4_mapped)))


class PublicResolver(AbstractResolver):
    """공개 주소만 돌려주는 DNS 해석기 (내부 주소로 가는 요청을 접속 단계에서 막는다)"""

    def __init__(self):
        self._inner = DefaultResolver()

    async def resolve(self, host, port=0, family=0):
        infos = await self._inner.resolve(host, port, family)
        if not ALLOW_PRIVATE:
            infos = [i for i in infos if not _blocked(i["host"])]
            if not infos:
                raise OSError(1, "내부·사설 네트워크 주소라 열 수 없습니다")   # (aiohttp가 strerror를 오류 문구에 쓴다)
        return infos

    async def close(self):
        await self._inner.close()


def host_of(url):
    try:
        return (URL(url).host or "").lower()
    except (ValueError, TypeError):
        return ""


def _host_match(host, want):
    want = want.lower().lstrip(".")
    return host == want or host.endswith("." + want)


def find_chat_url(page, base, chat_host):
    """사이트 HTML -> 채팅 주소 (chat_host가 들어간 http(s) 주소, iframe src 우선). 없으면 ''"""
    text = html.unescape(page or "")
    cands = re.findall(r"""<iframe\b[^>]*\bsrc\s*=\s*["']([^"']+)""", text, re.I)
    cands += re.findall(r"""https?://[^\s"'<>\\]+""", text)
    for c in cands:
        try:
            u = URL(base).join(URL(c.strip()))
        except (ValueError, TypeError):
            continue
        if u.scheme in ("http", "https") and u.host and _host_match(u.host.lower(), chat_host):
            return str(u)
    return ""


def notice_sites(page, base):
    """텔레그램 공개 채널 페이지(t.me/s/…) -> 공지된 사이트 주소들 (최신 글이 먼저, 텔레그램 자체 링크는 빼고)"""
    out = []
    for href in re.findall(r"""href\s*=\s*["']([^"']+)""", html.unescape(page or ""), re.I):
        try:
            u = URL(base).join(URL(href.strip()))
        except (ValueError, TypeError):
            continue
        host = (u.host or "").lower()
        if u.scheme not in ("http", "https") or not host or any(_host_match(host, t) for t in _TG_HOSTS):
            continue
        site = str(u.with_path("/").with_query(None).with_fragment(None))
        if site in out:
            out.remove(site)
        out.append(site)          # 페이지 아래쪽(최신 글)일수록 뒤로
    return list(reversed(out))


class ChatFinder:
    def __init__(self):
        self.cache = {}     # 소스 id -> (시각, 설정 키, 채팅 주소, 오류)
        self._locks = {}
        self._http = None

    def _session(self):
        if self._http is None or self._http.closed:
            self._http = ClientSession(connector=TCPConnector(resolver=PublicResolver(), ttl_dns_cache=60),
                                       headers={"User-Agent": UA, "Accept": "text/html,*/*"})
        return self._http

    async def close(self):
        if self._http is not None and not self._http.closed:
            await self._http.close()

    @staticmethod
    def _check(u):
        """http(s)이고, 주소가 IP 숫자면 공개 주소인지 (이름은 PublicResolver가 접속할 때 검사)"""
        if u.scheme not in ("http", "https") or not u.host:
            raise ChatFindError("http(s) 주소만 열 수 있습니다")
        try:
            literal = ipaddress.ip_address(u.host.strip("[]"))
        except ValueError:
            return
        if _blocked(literal) and not ALLOW_PRIVATE:   # (IP 숫자 주소는 aiohttp가 DNS 해석기를 거치지 않는다)
            raise ChatFindError(f"{u.host}: 내부·사설 네트워크 주소라 열 수 없습니다")

    async def _get(self, url):
        """페이지 (최종 주소, 글). 리다이렉트는 한 단계씩 직접 따라가며 매번 검사"""
        try:
            u = URL(str(url).strip())
        except (ValueError, TypeError):
            raise ChatFindError(f"주소 형식이 아닙니다: {url}") from None
        try:
            for _ in range(6):
                self._check(u)
                async with self._session().get(u, timeout=ClientTimeout(total=12), allow_redirects=False) as r:
                    if r.status in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                        u = u.join(URL(r.headers["Location"]))
                        continue
                    if r.status >= 400:
                        raise ChatFindError(f"{u.host} 응답 HTTP {r.status}")
                    data = await r.content.read(MAX_PAGE)
                    return str(u), data.decode(r.charset or "utf-8", "replace")
            raise ChatFindError(f"{u.host}: 리다이렉트가 너무 많습니다")
        except ChatFindError:
            raise
        except Exception as e:   # 연결 실패, 내부 주소 거절(PublicResolver) 등
            raise ChatFindError(f"{u.host or url}: {e}") from None

    async def find(self, src, force=False):
        """채팅 주소 (못 찾으면 ChatFindError). 직접 넣은 주소 > (보내 온 것과 찾은 것 중 새것) > 찾기.
        찾은 것은 10분 기억, force(새로고침)면 다시 찾고 실패하면 보내 온 것으로"""
        if src.get("chat_url"):
            return src["chat_url"]
        chat_host = (src.get("chat_host") or "").strip()
        sent = sent_url(src["id"]) if chat_host else None
        if sent:
            try:
                sent = (check_sent(sent[0], chat_host), sent[1])   # (호스트 설정을 바꿨으면 예전 것은 버린다)
            except ChatFindError:
                sent = None
        if not chat_host:
            raise ChatFindError("채팅 주소의 호스트가 설정되지 않았습니다")
        if not (src.get("chat_site") or src.get("chat_notice")):
            if sent:
                return sent[0]
            raise ChatFindError("아직 채팅 주소가 없습니다 - 설정의 북마크 [채팅 주소 보내기]로 보내 주세요")
        key = (src.get("chat_site"), src.get("chat_notice"), chat_host)
        hit = self.cache.get(src["id"])
        fresh = hit and hit[1] == key and time.time() - hit[0] < CACHE_SEC
        if not force:
            if sent and (not fresh or not hit[2] or sent[1] >= hit[0]):
                return sent[0]           # 보내 온 것이 더 새것이거나, 찾은 것이 없을 때
            if fresh:
                if hit[2]:
                    return hit[2]
                raise ChatFindError(hit[3])
        try:
            return await self._find_locked(src, chat_host, key, force)
        except ChatFindError:
            if sent:
                return sent[0]           # (서버가 사이트를 못 열어도 보내 온 주소로)
            raise

    async def _find_locked(self, src, chat_host, key, force):
        lock = self._locks.setdefault(src["id"], asyncio.Lock())
        async with lock:
            hit = self.cache.get(src["id"])
            if hit and hit[1] == key and not force and time.time() - hit[0] < 5:   # 방금 다른 요청이 찾았으면
                if hit[2]:
                    return hit[2]
                raise ChatFindError(hit[3])
            try:
                url = await self._search(src, chat_host)
                self.cache[src["id"]] = (time.time(), key, url, "")
                return url
            except ChatFindError as e:
                self.cache[src["id"]] = (time.time(), key, "", str(e))
                raise

    async def _search(self, src, chat_host):
        sites, errors = [], []
        if src.get("chat_notice"):
            try:
                base, page = await self._get(src["chat_notice"])
                sites += notice_sites(page, base)[:MAX_SITES]
            except ChatFindError as e:
                errors.append(f"공지 채널: {e}")
        if src.get("chat_site") and src["chat_site"] not in sites:
            sites.append(src["chat_site"])          # 공지에서 못 찾으면 직접 넣은 사이트 주소
        for site in sites:
            try:
                base, page = await self._get(site)
            except ChatFindError as e:
                errors.append(str(e))
                continue
            url = find_chat_url(page, base, chat_host)
            if url:
                return url
        raise ChatFindError(" / ".join(errors) or f"사이트에서 {chat_host} 채팅 주소를 찾지 못했습니다")
