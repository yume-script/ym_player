"""브런치 연재 소스: 브런치(brunch.co.kr)의 브런치북 오리지널 연재를 요일별로 보고, 글을 YMPlayer 안에서 읽는다.
(yume-script/ym_brunch BookOasis 플러그인을 옮김 - 소리 내어 읽기는 브라우저 음성만, 비공식 Edge 음성 서버는 쓰지 않음)

- 목록: 브런치 화면이 쓰는 공개 API (로그인 없음)
    GET https://api.brunch.co.kr/v2/serial-brunchbook/all?dayOfWeek=MONDAY&orderKeyword=PUBLISH_TIME&serialStatus=ONGOING
    (완결: ?serialStatus=COMPLETE) -> data.list(20개씩), data.moreList, data.nextUrl
- 글: https://brunch.co.kr/@@{userId}/{articleNo} 페이지에서
    <script id="ARTICLE_DATA"> JSON(제목·작가) + <div id="ArticleView"> 본문(허용한 태그만 남김) + list_magazine(앞뒤 회차)
  멤버십(유료) 글은 페이지에 있는 앞부분만 보이고 원문 링크를 안내한다 (유료 부분을 우회하지 않는다).
- 브라우저에서 바로 부르면 CORS에 막혀서 서버가 대신 받는다. 정해진 브런치 주소만 부른다 (화면이 넘긴 다음 페이지 주소도 검사).
비공식(화면용) API라 브런치가 바뀌면 깨질 수 있다 - 그때는 API_URL / _item() / fetch_article()만 고치면 된다.
"""
import json
import os
import re
import time
from html import escape, unescape
from html.parser import HTMLParser
from urllib.parse import urlencode

from aiohttp import ClientTimeout

# (MP_TEST_BRUNCH: 화면 자동 테스트가 이 컴퓨터의 가짜 브런치를 쓰게 할 때만)
_TEST = os.environ.get("MP_TEST_BRUNCH", "").rstrip("/")
API_URL = f"{_TEST}/api" if _TEST else "https://api.brunch.co.kr/v2/serial-brunchbook/all"
WEB_URL = _TEST or "https://brunch.co.kr"
DAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
SORTS = ("PUBLISH_TIME", "DONATION_TOTAL_AMOUNT", "LIKE_COUNT")
# 브라우저와 같은 모양으로 (봇처럼 보이는 이름이면 다른 곳으로 넘기는 경우가 있어서)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
_HOSTS = ("brunch.co.kr", "api.brunch.co.kr", "www.brunch.co.kr")   # 넘겨줘도(리다이렉트) 따라가는 곳은 브런치 주소만
TIMEOUT = ClientTimeout(total=15)
LIST_CACHE_SEC = 300
ARTICLE_CACHE_SEC = 600
_ID_RE = re.compile(r"^[A-Za-z0-9]{1,40}$")


class BrunchError(Exception):
    pass


def https(url):
    """브런치 이미지 주소가 http://로 와서 https 페이지에서 막힌다 (CDN은 https도 받음)"""
    if isinstance(url, str) and url.startswith("http://"):
        return "https://" + url[len("http://"):]
    if isinstance(url, str) and url.startswith("//"):
        return "https:" + url
    return url if isinstance(url, str) else ""


def item(raw):
    user_id = str(raw.get("userId") or "")
    no = raw.get("articleNo")
    ok = bool(_ID_RE.match(user_id)) and str(no or "").isdigit()
    return {
        "book_title": raw.get("magazineTitle") or "",
        "author": raw.get("userName") or "",
        "author_image": https(raw.get("userImage")),
        "title": raw.get("articleTitle") or "",
        "subtitle": raw.get("articleSubTitle") or "",
        "summary": (raw.get("articleContentSummary") or "").strip(),
        "image": https(raw.get("articleImage")),
        "url": f"{WEB_URL}/@@{user_id}/{no}" if ok else "",
        "user_id": user_id if ok else "",
        "article_no": int(no) if ok else 0,
        "publish_time": raw.get("publishTime") or 0,
        "like_count": raw.get("likeCount") or 0,
        "donation_amount": raw.get("donationTotalAmount") or 0,
        "status": raw.get("serialStatus") or "",
        "is_new": bool(raw.get("new")),
    }


def list_url(day, sort, next_url=None):
    """요일(MONDAY..SUNDAY) 또는 COMPLETE(완결) -> 부를 주소. next_url은 직전 응답의 nextUrl (브런치 API 주소만)"""
    if next_url:
        if not str(next_url).startswith(API_URL + "?"):
            raise ValueError("잘못된 다음 페이지 주소입니다.")
        return str(next_url)
    if day == "COMPLETE":
        return API_URL + "?" + urlencode({"serialStatus": "COMPLETE"})
    if day not in DAYS:
        raise ValueError(f"알 수 없는 요일: {day}")
    return API_URL + "?" + urlencode({"dayOfWeek": day, "orderKeyword": sort if sort in SORTS else SORTS[0],
                                      "serialStatus": "ONGOING"})


def parse_list(body):
    if not isinstance(body, dict) or body.get("code") != 200 or not isinstance(body.get("data"), dict):
        raise BrunchError((body or {}).get("title") or (body or {}).get("desc") or "브런치 응답 오류")
    data = body["data"]
    return {"items": [item(x) for x in (data.get("list") or []) if isinstance(x, dict)],
            "more": bool(data.get("moreList")), "next_url": data.get("nextUrl") or ""}


# ── 글 본문 ──
_ALLOWED = {"p", "br", "b", "strong", "i", "em", "u", "s", "strike", "del", "sub", "sup",
            "h1", "h2", "h3", "h4", "blockquote", "ul", "ol", "li", "hr", "a", "img", "div",
            "figure", "figcaption", "table", "thead", "tbody", "tr", "td", "th", "small"}
_VOID = {"br", "hr", "img"}
_DROP = {"script", "style", "noscript", "button", "svg", "iframe", "object", "embed", "form",
         "input", "select", "textarea", "video", "audio", "template"}
_KEEP_CLASS = re.compile(r"^(item_type_\w+|blockquote_type\d|hr_type_\d|img_align_\w+)$")


class _Sanitizer(HTMLParser):
    """허용한 태그만, 속성은 href/src/class(브런치 블록 종류)만. 화면은 이 결과를 innerHTML로 넣는다"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.drop = [], [], 0

    def handle_starttag(self, tag, attrs):
        if self.drop:
            self.drop += tag in _DROP
            return
        if tag in _DROP:
            self.drop = 1
            return
        if tag not in _ALLOWED:
            return
        a = dict(attrs)
        parts = [tag]
        classes = [c for c in (a.get("class") or "").split() if _KEEP_CLASS.match(c)]
        if classes:
            parts.append('class="%s"' % escape(" ".join("bb-" + c for c in classes)))
        if tag == "a":
            href = https(a.get("href") or "")
            if href.startswith("/"):
                href = WEB_URL + href
            if href.startswith("https://"):
                parts.append('href="%s" target="_blank" rel="noopener noreferrer"' % escape(href))
        elif tag == "img":
            src = https(a.get("src") or a.get("data-src") or "")
            if not src.startswith("https://"):
                return
            parts.append('src="%s" loading="lazy" referrerpolicy="no-referrer" alt="%s"' % (escape(src), escape(a.get("alt") or "")))
        self.out.append("<%s>" % " ".join(parts))
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and self.stack and self.stack[-1] == tag:
            self.stack.pop()
            self.out.append("</%s>" % tag)

    def handle_endtag(self, tag):
        if self.drop:
            self.drop -= tag in _DROP
            return
        if tag in _VOID or tag not in self.stack:
            return
        while self.stack:
            t = self.stack.pop()
            self.out.append("</%s>" % t)
            if t == tag:
                break

    def handle_data(self, data):
        if not self.drop:
            self.out.append(escape(data, quote=False))

    def result(self):
        self.close()
        while self.stack:
            self.out.append("</%s>" % self.stack.pop())
        return "".join(self.out)


def sanitize(html_text):
    p = _Sanitizer()
    p.feed(html_text or "")
    return p.result()


def _between(text, start_marker, end_markers):
    i = text.find(start_marker)
    if i < 0:
        return ""
    i = text.find(">", i) + 1
    ends = [j for j in (text.find(m, i) for m in end_markers) if j >= 0]
    return text[i:min(ends)] if ends else text[i:]


_EP_RE = re.compile(r'<li class="(item_magazine[^"]*)"[^>]*>(.*?)</li>', re.S)


def _episodes(page, current_no=0):
    """같은 브런치북의 회차들 (링크가 있는 것만 - 발행 예정은 뺌). 지금 글은 current (표시가 없으면 글 번호로)"""
    block = _between(page, 'class="list_magazine"', ["</ul>"])
    eps = []
    for cls, li in _EP_RE.findall(block):
        num = re.search(r'class="txt_num">(?:\s*<span[^>]*>.*?</span>)?\s*([^<]*?)\s*<', li, re.S)
        title = re.search(r'class="txt_magazine">([^<]*)<', li)
        link = re.search(r'href="/@@([A-Za-z0-9]+)/(\d+)"', li)
        if not link:
            continue
        eps.append({"num": num.group(1).strip() if num else "", "title": unescape(title.group(1).strip()) if title else "",
                    "user_id": link.group(1), "article_no": int(link.group(2)),
                    "current": "magazine_current" in cls or "magazine_current" in li})
    if eps and not any(e["current"] for e in eps):
        for e in eps:
            e["current"] = e["article_no"] == current_no
    return eps


def article_url(user_id, article_no):
    user_id = str(user_id or "")
    if not _ID_RE.match(user_id) or not str(article_no or "").isdigit():
        raise ValueError("잘못된 글 주소입니다.")
    return f"{WEB_URL}/@@{user_id}/{int(article_no)}"


def parse_article(page, url):
    m = re.search(r'<script type="application/json" id="ARTICLE_DATA">(.*?)</script>', page, re.S)
    try:
        meta = json.loads(m.group(1)) if m else {}
    except ValueError:
        meta = {}
    if not meta:
        raise BrunchError("글을 찾을 수 없습니다 (삭제됐거나 비공개일 수 있음).")
    view = re.search(r'<div id="ArticleView" class="([^"]*)"', page)
    align = next((a for a in ("center", "right", "left") if view and "text_align_" + a in view.group(1)), "")
    body = _between(page, 'id="ArticleView"', ['<div class="info_keyword"', 'class="info_article magazine_article"'])
    book = re.search(r'class="link_magazinetit[^"]*"[^>]*>\s*(?:<span class="tag_state">[^<]*</span>)?\s*([^<]*?)\s*</a>', page)
    return {
        "title": meta.get("title") or "",
        "subtitle": meta.get("subTitle") or "",
        "author": meta.get("authorName") or "",
        "publish_time": meta.get("publishTime") or 0,
        "like_count": meta.get("likeCount") or 0,
        "book_title": unescape(book.group(1)) if book else "",
        "cover": https(meta.get("articleImageUrl")),
        "url": url,
        "align": align,
        "restricted": bool(meta.get("isMembershipContent") or meta.get("isRestrict")),
        "body": sanitize(body),
        "episodes": _episodes(page, int(url.rsplit("/", 1)[-1]) if url.rsplit("/", 1)[-1].isdigit() else 0),
    }


class Brunch:
    """목록·글을 받아 잠시 기억한다. http() -> aiohttp ClientSession"""

    def __init__(self, http):
        self.http = http
        self._cache = {}

    def _hit(self, key, ttl):
        hit = self._cache.get(key)
        return hit[1] if hit and time.time() - hit[0] < ttl else None

    def _keep(self, key, value):
        if len(self._cache) > 300:
            self._cache.clear()
        self._cache[key] = (time.time(), value)

    async def _get(self, url, accept):
        from urllib.parse import urljoin, urlparse
        try:
            for _hop in range(5):   # 리다이렉트는 브런치 주소 안에서만 직접 따라간다
                async with self.http().get(url, headers={"User-Agent": UA, "Accept": accept, "Referer": WEB_URL + "/",
                                                         "Accept-Language": "ko-KR,ko;q=0.9"},
                                           timeout=TIMEOUT, allow_redirects=False) as r:
                    if r.status in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                        nxt = urljoin(url, r.headers["Location"])
                        host = urlparse(nxt).hostname or ""
                        if not (host in _HOSTS or (_TEST and nxt.startswith(_TEST))):
                            raise BrunchError(f"브런치가 다른 곳으로 넘겼습니다 ({host or nxt[:80]})")
                        url = nxt
                        continue
                    if r.status != 200:
                        raise BrunchError(f"브런치 응답 HTTP {r.status}")
                    return await r.text(encoding="utf-8", errors="replace")
            raise BrunchError("브런치가 너무 여러 번 다른 주소로 넘겼습니다.")
        except BrunchError:
            raise
        except Exception as e:
            raise BrunchError(f"브런치에 연결하지 못했습니다: {e}") from None

    async def list(self, day, sort="", next_url=None):
        url = list_url(day, sort, next_url)
        hit = self._hit(url, LIST_CACHE_SEC)
        if hit is None:
            try:
                body = json.loads(await self._get(url, "application/json"))
            except ValueError:
                raise BrunchError("브런치 응답을 읽지 못했습니다.") from None
            hit = parse_list(body)
            self._keep(url, hit)
        return hit

    async def article(self, user_id, article_no):
        url = article_url(user_id, article_no)
        hit = self._hit(url, ARTICLE_CACHE_SEC)
        if hit is None:
            hit = parse_article(await self._get(url, "text/html"), url)
            self._keep(url, hit)
        return hit
