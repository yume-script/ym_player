"""한국 드라마·예능 작품 정보 보강 - 한국어 위키백과 (키 없이, 무료).

TMDB·AniList에서 못 찾은 TV 작품(드라마·예능)만 위키백과에서 찾는다: 검색(action=query&list=search) -> 문서 요약(REST
page/summary)의 첫 문단과 대표 그림. 문서 이름이 찾는 이름과 비슷하고(tmdb.MIN_SIM, 괄호 꼬리 "(드라마)" 등은 떼고 비교)
요약에 방송·드라마·예능 같은 말이 있는 문서만 받는다 (동음이의 문서·인물 문서 등은 빼고).
위키백과 글은 CC BY-SA 4.0 - 화면에 출처(문서 링크)를 함께 보인다 (상세 화면의 "정보 출처").
돌려주는 정보는 tmdb.Tmdb.details와 같은 모양에 "source": "wikipedia" (TMDB 번호가 없어 시즌 회차 정보는 없음).
"""
import os
import re
from difflib import SequenceMatcher
from urllib.parse import quote

from aiohttp import ClientTimeout

from . import __version__
from .tmdb import MIN_SIM, _norm

API = os.environ.get("MP_WIKI_BASE", "https://ko.wikipedia.org")   # 테스트에서 가짜 서버로
# 위키미디어 API 규칙: 알아볼 수 있는 User-Agent (개인 정보는 넣지 않는다)
HEADERS = {"User-Agent": f"YMPlayer/{__version__} (self-hosted media player)", "Accept": "application/json"}
_TAIL = re.compile(r"\s*\((?:[^()]*(?:드라마|예능|텔레비전|TV|방송|프로그램|시리즈|시트콤|애니메이션)[^()]*|\d{4}년?(?: [^()]*)?)\)\s*$")
_TV_WORDS = re.compile(r"드라마|예능|방송|프로그램|시리즈|시트콤|리얼리티|버라이어티|토크쇼|오디션|서바이벌|편성|채널|넷플릭스|티빙|웨이브|쿠팡플레이")


def bare(title):
    """'무빙 (드라마)' -> '무빙', '신병 (2022년 드라마)' -> '신병'"""
    return _TAIL.sub("", str(title or "")).strip()


def sim(page_title, want):
    a, b = _norm(bare(page_title)), _norm(want)
    if not a or not b:
        return 0.0
    s = SequenceMatcher(None, a, b).ratio()
    if min(len(a), len(b)) >= 2 and (a in b or b in a):
        s = max(s, 0.75)
    return s


def looks_tv(summary):
    """요약이 TV 작품 문서인가 (동음이의·인물·지명 문서 등은 아님)"""
    if not summary or summary.get("type") == "disambiguation":
        return False
    text = f"{summary.get('description') or ''} {summary.get('extract') or ''} {summary.get('title') or ''}"
    return bool(_TV_WORDS.search(text))


def to_meta(summary, kind="tv"):
    s = summary or {}
    pic = (s.get("originalimage") or {}).get("source") or (s.get("thumbnail") or {}).get("source") or ""
    url = ((s.get("content_urls") or {}).get("desktop") or {}).get("page") or f"{API}/wiki/{quote(str(s.get('title') or ''))}"
    year = re.search(r"(?<!\d)((?:19|20)\d{2})년", s.get("extract") or "")
    return {
        "id": None, "source": "wikipedia", "url": url, "kind": kind,
        "title": bare(s.get("title")), "original": "", "year": year.group(1) if year else "",
        "overview": str(s.get("extract") or "").strip(), "tagline": "", "genres": [], "rating": None, "runtime": None,
        "poster": pic, "backdrop": "", "directors": [], "cast": [], "seasons": [],
    }


async def _get(http, url, params=None):
    async with http.get(url, params=params, headers=HEADERS, timeout=ClientTimeout(total=15)) as r:
        if r.status == 404:
            return None
        if r.status == 429:
            raise RuntimeError("위키백과 요청 제한 (잠시 뒤 다시)")
        if r.status >= 400:
            raise RuntimeError(f"위키백과 HTTP {r.status}")
        return await r.json(content_type=None)


async def search(http, query):
    data = await _get(http, f"{API}/w/api.php", {"action": "query", "list": "search", "srsearch": query, "srlimit": "6",
                                                 "format": "json", "utf8": "1", "srnamespace": "0"}) or {}
    return [x.get("title") for x in ((data.get("query") or {}).get("search") or []) if x.get("title")]


async def summary(http, title):
    return await _get(http, f"{API}/api/rest_v1/page/summary/{quote(title.replace(' ', '_'), safe='')}")


async def find(http, titles, kind="tv"):
    """여러 이름으로 찾아 맞는 TV 작품 문서 하나의 정보 (없으면 None). 이름이 가장 비슷한 문서부터 요약을 본다"""
    for t in list(dict.fromkeys(t for t in titles if t))[:3]:
        pages = await search(http, t)
        ranked = sorted(((sim(p, t), p) for p in pages), reverse=True)
        for score, page in ranked[:3]:
            if score < MIN_SIM:
                break
            s = await summary(http, page)
            if looks_tv(s):
                return to_meta(s, kind)
    return None
