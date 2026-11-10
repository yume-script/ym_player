"""만개의레시피 소스: 만개의레시피(10000recipe.com)의 인기 랭킹·분류·검색과 레시피 읽기 (재료·만드는 순서).
(yume-script/recipe_10000 BookOasis 플러그인을 옮김 - 그 플러그인은 브라우저가 프록시로 페이지를 받아 읽었는데,
 여기서는 서버가 정해진 만개의레시피 주소만 받아 필요한 값만 뽑아서 넘긴다)

- 랭킹: /ranking/home_new.html?rtype=r&dtype=d   분류·검색: /recipe/list.html?cat4=63&page=2 / ?q=김치
- 레시피: /recipe/{번호} -> ld+json(Recipe: 이름·재료·순서) + 화면 HTML(재료 칸, 순서 사진, 요약 정보)
화면은 글자만 받아 textContent로 그린다 (HTML을 그대로 넣지 않는다). 사이트 구조가 바뀌면 parse_list / parse_recipe만 고친다.
"""
import json
import os
import re
import time
from html import unescape
from urllib.parse import urlencode

from aiohttp import ClientTimeout

BASE = os.environ.get("MP_TEST_RECIPE", "").rstrip("/") or "https://www.10000recipe.com"   # (MP_TEST_RECIPE: 화면 자동 테스트 전용)
UA = "Mozilla/5.0 (YMPlayer recipe source)"
TIMEOUT = ClientTimeout(total=15)
LIST_CACHE_SEC = 600
RECIPE_CACHE_SEC = 3600
# 분류 칩 (이름, 목록 주소 조건) - 화면은 이름의 번호(key)만 넘긴다
CATEGORIES = [("밑반찬", "cat4=63"), ("메인반찬", "cat4=56"), ("국/탕", "cat4=54"), ("찌개", "cat4=55"),
              ("밥/죽/떡", "cat4=52"), ("면/만두", "cat4=53"), ("초스피드", "cat2=18"), ("도시락", "cat2=15"),
              ("야식", "cat2=45"), ("디저트", "cat4=60"), ("다이어트", "cat2=21")]


class RecipeError(Exception):
    pass


def _clean(s):
    """HTML 조각 -> 글자만 (스크립트·스타일 내용은 버리고, 태그 자리 빈칸은 문장부호 앞에서 붙인다)"""
    s = re.sub(r"<(script|style)\b.*?</\1\s*>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s))).strip()
    return re.sub(r" ([.,!?)])", r"\1", s)


def _abs(u):
    u = unescape(u or "").strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return BASE + u
    if u.startswith("http://"):
        return "https://" + u[7:]
    return u if u.startswith("https://") else ""


def list_url(cat="", q="", page=1):
    """cat: CATEGORIES 번호(문자열) / q: 검색어 / 둘 다 없으면 오늘의 랭킹"""
    page = max(1, min(50, int(page or 1)))
    if q:
        return f"{BASE}/recipe/list.html?" + urlencode({"q": str(q)[:50], "page": page})
    if str(cat).isdigit() and int(cat) < len(CATEGORIES):
        return f"{BASE}/recipe/list.html?{CATEGORIES[int(cat)][1]}&page={page}"
    return f"{BASE}/ranking/home_new.html?rtype=r&dtype=d"


_LINK_RE = re.compile(r'href="(?:https?://www\.10000recipe\.com)?/recipe/(\d+)"')
_IMG_RE = re.compile(r"<img\b[^>]*>", re.I)


def _attr(tag, name):
    m = re.search(r'\b%s="([^"]*)"' % name, tag)
    return m.group(1) if m else ""


def parse_list(page):
    """목록 페이지 -> [{id, title, by, image}] (같은 레시피는 한 번)"""
    seen, out = set(), []
    for m in _LINK_RE.finditer(page):
        rid = m.group(1)
        if rid in seen:
            continue
        start = page.rfind("<li", 0, m.start())
        end = page.find("</li>", m.end())
        box = page[start if start >= 0 else m.start():end if end >= 0 else m.end() + 3000]
        imgs = [t for t in _IMG_RE.findall(box) if "icon_vod" not in t and "icon_" not in _attr(t, "src")]
        if not imgs:
            continue
        img = imgs[0]
        t = re.search(r'class="common_sp_caption_tit[^"]*"[^>]*>(.*?)</div>', box, re.S)
        w = re.search(r'class="common_sp_caption_rv_name[^"]*"[^>]*>(.*?)</div>', box, re.S)
        title = _clean(t.group(1)) if t else _clean(_attr(img, "alt"))
        seen.add(rid)
        out.append({"id": rid, "title": title or f"레시피 {rid}", "by": _clean(w.group(1)) if w else "",
                    "image": _abs(_attr(img, "data-src") or _attr(img, "src"))})
    return out


def _ld_recipe(page):
    for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for x in data if isinstance(data, list) else [data]:
            if isinstance(x, dict) and x.get("@type") == "Recipe":
                return x
    return {}


def _class_text(page, cls):
    m = re.search(r'class="%s[^"]*"[^>]*>(.*?)</(?:span|div|p|h3)>' % re.escape(cls), page, re.S)
    return _clean(m.group(1)) if m else ""


def parse_recipe(page, rid):
    ld = _ld_recipe(page)
    # 재료: 화면의 재료 칸 (이름 + 양), 없으면 ld+json
    ing = []
    area = re.search(r'id="divConfirmedMaterialArea"(.*?)(?:<div class="view_step"|id="obx_recipe_step_start"|$)', page, re.S)
    if area:
        for li in re.findall(r"<li[^>]*>(.*?)</li>", area.group(1), re.S):
            n = re.search(r'class="ingre_list_name[^"]*"[^>]*>(.*?)</div>', li, re.S)
            a = re.search(r'class="ingre_list_ea[^"]*"[^>]*>(.*?)</span>', li, re.S)
            text = (_clean(n.group(1)) + " " + (_clean(a.group(1)) if a else "")).strip() if n else _clean(li)
            if text:
                ing.append(text)
    if not ing:
        ing = [_clean(x) for x in ld.get("recipeIngredient") or [] if _clean(str(x))]
    # 만드는 순서: 화면의 순서 칸 (글 + 사진), 없으면 ld+json
    steps = []
    for blk in re.findall(r'<div[^>]+class="view_step_cont[^"]*"[^>]*>(.*?)(?=<div[^>]+class="view_step_cont|<div class="view_step_tip|$)',
                          page, re.S)[:60]:
        body = re.search(r'class="media-body"[^>]*>(.*?)</div>', blk, re.S)
        img = _IMG_RE.search(blk)
        text = _clean(body.group(1) if body else blk)
        if text:
            steps.append({"text": text[:2000], "image": _abs(_attr(img.group(0), "src")) if img else ""})
    if not steps:
        for s in ld.get("recipeInstructions") or []:
            text = _clean(s.get("text") if isinstance(s, dict) else str(s))
            if text:
                steps.append({"text": text[:2000], "image": _abs((s.get("image") if isinstance(s, dict) else "") or "")})
    title = _class_text(page, "view2_summary") or ""
    t = re.search(r'<div class="view2_summary[^"]*">\s*<h3>(.*?)</h3>', page, re.S)
    title = _clean(t.group(1)) if t else (ld.get("name") or title)
    info = [x for x in (_class_text(page, f"view2_summary_info{i}") for i in (1, 2, 3)) if x]
    image = ld.get("image")
    image = image[0] if isinstance(image, list) and image else image
    author = ld.get("author")
    if not title and not ing and not steps:
        raise RecipeError("레시피를 읽지 못했습니다 (지워졌거나 사이트 구조가 바뀌었을 수 있습니다).")
    return {"id": rid, "title": title or f"레시피 {rid}", "intro": _clean(ld.get("description") or ""),
            "info": info, "image": _abs(image if isinstance(image, str) else ""),
            "by": _clean(author.get("name")) if isinstance(author, dict) else "",
            "ingredients": ing[:80], "steps": steps, "url": f"{BASE}/recipe/{rid}"}


class Recipes:
    def __init__(self, http):
        self.http = http
        self._cache = {}

    def _hit(self, key, ttl):
        hit = self._cache.get(key)
        return hit[1] if hit and time.time() - hit[0] < ttl else None

    def _keep(self, key, value):
        if len(self._cache) > 500:
            self._cache.clear()
        self._cache[key] = (time.time(), value)

    async def _get(self, url):
        try:
            async with self.http().get(url, headers={"User-Agent": UA, "Accept": "text/html", "Referer": BASE + "/"},
                                       timeout=TIMEOUT, allow_redirects=False) as r:
                if r.status != 200:
                    raise RecipeError(f"만개의레시피 응답 HTTP {r.status}")
                return await r.text(encoding="utf-8", errors="replace")
        except RecipeError:
            raise
        except Exception as e:
            raise RecipeError(f"만개의레시피에 연결하지 못했습니다: {e}") from None

    async def list(self, cat="", q="", page=1):
        url = list_url(cat, q, page)
        hit = self._hit(url, LIST_CACHE_SEC)
        if hit is None:
            hit = parse_list(await self._get(url))
            self._keep(url, hit)
        return hit

    async def recipe(self, rid):
        rid = str(rid or "")
        if not rid.isdigit() or len(rid) > 12:
            raise ValueError("잘못된 레시피 번호입니다.")
        hit = self._hit(rid, RECIPE_CACHE_SEC)
        if hit is None:
            hit = parse_recipe(await self._get(f"{BASE}/recipe/{rid}"), rid)
            self._keep(rid, hit)
        return hit
