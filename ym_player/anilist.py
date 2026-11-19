"""애니 작품 정보 보강 - AniList GraphQL API (키 없이, 무료).

TMDB에서 못 찾은(또는 TMDB 키가 없는) 텔레그램 영상을 AniList에서 한 번 더 찾는다. AniList는 애니만 있으므로 애니가 아닌
작품은 이름이 비슷한 결과가 없어 맞춰지지 않는다 (tmdb.MIN_SIM과 같은 기준). 이름은 로마자·영어·일본어·별칭(synonyms -
한국어 제목이 들어 있는 일이 많다) 모두와 비교하고, 화면 제목은 한글 별칭 > 영어 > 로마자 순으로 고른다.
돌려주는 정보는 tmdb.Tmdb.details와 같은 모양에 "source": "anilist" (TMDB 번호가 없어 시즌 회차 정보는 없음).
"""
import os
import re
from difflib import SequenceMatcher

from aiohttp import ClientTimeout

from .tmdb import MIN_SIM, MIN_SIM_YEAR, _norm

API = os.environ.get("MP_ANILIST_URL", "https://graphql.anilist.co")   # 테스트에서 가짜 서버로
_HANGUL = re.compile(r"[가-힣]")
_QUERY = """query ($s: String) {
  Page(perPage: 8) {
    media(search: $s, type: ANIME, sort: SEARCH_MATCH) {
      id siteUrl format episodes duration seasonYear startDate { year }
      title { romaji english native } synonyms
      description(asHtml: false) genres averageScore
      coverImage { extraLarge large } bannerImage
      studios(isMain: true) { nodes { name } }
    }
  }
}"""
GENRES = {"Action": "액션", "Adventure": "모험", "Comedy": "코미디", "Drama": "드라마", "Ecchi": "에치", "Fantasy": "판타지",
          "Horror": "공포", "Mahou Shoujo": "마법소녀", "Mecha": "메카", "Music": "음악", "Mystery": "미스터리",
          "Psychological": "심리", "Romance": "로맨스", "Sci-Fi": "SF", "Slice of Life": "일상", "Sports": "스포츠",
          "Supernatural": "초자연", "Thriller": "스릴러"}


def _names(m):
    t = m.get("title") or {}
    return [x for x in [t.get("romaji"), t.get("english"), t.get("native"), *(m.get("synonyms") or [])] if x]


def _year(m):
    return str(m.get("seasonYear") or (m.get("startDate") or {}).get("year") or "")


def sim(m, title):
    """찾는 이름과 작품 이름들(로마자·영어·일본어·별칭) 중 가장 비슷한 정도 0~1 (한쪽이 다른 쪽을 품으면 0.75 이상)"""
    want, best = _norm(title), 0.0
    for n in _names(m):
        n = _norm(n)
        if not n or not want:
            continue
        s = SequenceMatcher(None, want, n).ratio()
        if min(len(want), len(n)) >= 2 and (want in n or n in want):
            s = max(s, 0.75)
        best = max(best, s)
    return best


def pick(results, title, year=""):
    """받아들일 만한 결과 중 가장 잘 맞는 것 (이름이 비슷하지 않으면 None - 엉뚱한 포스터보다 없는 게 낫다)"""
    best, best_score = None, -1.0
    for i, m in enumerate(results or []):
        s = sim(m, title)
        same_year = bool(year) and _year(m) == str(year)
        if not (s >= MIN_SIM or (same_year and s >= MIN_SIM_YEAR)):
            continue
        score = s + (0.3 if same_year else 0) - 0.02 * i
        if score > best_score:
            best, best_score = m, score
    return best


def to_meta(m, kind=""):
    """AniList 작품 -> 화면에 쓰는 작품 정보 (tmdb.Tmdb.details와 같은 칸)"""
    t = m.get("title") or {}
    korean = next((s for s in m.get("synonyms") or [] if _HANGUL.search(s)), "")
    desc = re.sub(r"<[^>]+>", "", str(m.get("description") or "")).replace("\r", "").strip()
    desc = re.sub(r"\n{3,}", "\n\n", desc)
    cover = m.get("coverImage") or {}
    fmt = str(m.get("format") or "")
    return {
        "id": None, "source": "anilist", "anilist_id": m.get("id"), "url": m.get("siteUrl") or "",
        "kind": kind or ("movie" if fmt == "MOVIE" else "tv"),
        "title": korean or t.get("english") or t.get("romaji") or t.get("native") or "",
        "original": t.get("native") or t.get("romaji") or "",
        "year": _year(m), "overview": desc, "tagline": "",
        "genres": [GENRES.get(g, g) for g in m.get("genres") or []],
        "rating": round((m.get("averageScore") or 0) / 10, 1) or None,
        "runtime": m.get("duration") or None,
        "poster": cover.get("extraLarge") or cover.get("large") or "", "backdrop": m.get("bannerImage") or "",
        "directors": [n.get("name") for n in ((m.get("studios") or {}).get("nodes") or []) if n.get("name")][:3],
        "cast": [], "seasons": [], "episodes_total": m.get("episodes") or None,
    }


async def search(http, query):
    async with http.post(API, json={"query": _QUERY, "variables": {"s": query}}, timeout=ClientTimeout(total=15),
                         headers={"Accept": "application/json"}) as r:
        if r.status == 429:
            raise RuntimeError("AniList 요청 제한 (잠시 뒤 다시)")
        if r.status >= 400:
            raise RuntimeError(f"AniList HTTP {r.status}")
        data = await r.json(content_type=None)
    return ((data or {}).get("data") or {}).get("Page", {}).get("media") or []


async def find(http, titles, year="", kind=""):
    """여러 이름으로 찾아 가장 잘 맞는 작품 정보 하나 (없으면 None). 이름이 거의 같으면 바로 그것으로"""
    best, best_score = None, -1.0
    for t in list(dict.fromkeys(t for t in titles if t))[:4]:   # (요청 제한이 빡빡해서 이름 넷까지)
        res = await search(http, t)
        hit = pick(res, t, year)
        if not hit:
            continue
        score = sim(hit, t) + (0.3 if year and _year(hit) == str(year) else 0)
        if score > best_score:
            best, best_score = hit, score
        if score >= 0.9:
            break
    return to_meta(best, kind) if best else None
