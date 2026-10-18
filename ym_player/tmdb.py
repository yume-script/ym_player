"""텔레그램 영상에 입힐 작품 정보 - TMDB(The Movie Database) API.

파일 이름에서 뽑은 작품 이름·연도(·회차)로 영화나 TV 시리즈를 찾고, 한국어(ko-KR) 줄거리·포스터·배경·장르·출연진·
평점과 시즌의 회차 제목·줄거리·장면 사진을 가져온다. 키는 텔레그램 소스 설정의 tmdb_key (themoviedb.org에서 무료 발급).
v3 API 키(32자)와 v4 읽기 토큰(긴 JWT) 둘 다 받는다. 그림은 image.tmdb.org 주소를 그대로 브라우저에 준다.
"""
import os
import re
import unicodedata
from difflib import SequenceMatcher

from aiohttp import ClientTimeout

API = os.environ.get("MP_TMDB_BASE", "https://api.themoviedb.org/3")   # 테스트에서 가짜 서버로
IMG = "https://image.tmdb.org/t/p"
LANG = "ko-KR"


class TmdbError(Exception):
    pass


def img(path, size="w500"):
    return f"{IMG}/{size}{path}" if path else ""


def _norm(v):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", str(v or "")).casefold())


def best_match(results, title, year="", kind="movie"):
    """검색 결과 중 이름(번역·원제)이 가장 비슷하고 연도가 맞는 것. 비슷한 게 없으면 첫 결과(인기순)."""
    want = _norm(title)
    best, best_score = None, -1.0
    for i, r in enumerate(results or []):
        names = [r.get("title"), r.get("original_title")] if kind == "movie" else [r.get("name"), r.get("original_name")]
        sim = max((SequenceMatcher(None, want, _norm(n)).ratio() for n in names if n), default=0.0)
        date = r.get("release_date" if kind == "movie" else "first_air_date") or ""
        score = sim + (0.3 if year and date[:4] == str(year) else 0) - 0.02 * i
        if score > best_score:
            best, best_score = r, score
    return best


class Tmdb:
    def __init__(self, http, key):
        self.http, self.key = http, (key or "").strip()

    async def _get(self, path, **params):
        if not self.key:
            raise TmdbError("TMDB API 키가 없습니다. 설정 > 소스 > 텔레그램에서 넣어 주세요.")
        headers = {"Accept": "application/json"}
        if len(self.key) > 40 and "." in self.key:     # v4 읽기 토큰
            headers["Authorization"] = f"Bearer {self.key}"
        else:
            params["api_key"] = self.key
        params.setdefault("language", LANG)
        async with self.http.get(f"{API}{path}", params={k: str(v) for k, v in params.items() if v not in (None, "")},
                                 headers=headers, timeout=ClientTimeout(total=15)) as r:
            if r.status == 401:
                raise TmdbError("TMDB API 키가 맞지 않습니다.")
            if r.status == 404:
                return None
            if r.status >= 400:
                raise TmdbError(f"TMDB HTTP {r.status}")
            return await r.json(content_type=None)

    async def search(self, kind, query, year=""):
        """kind: movie | tv -> 결과 목록 (가벼운 모양)"""
        params = {"query": query, "include_adult": "false"}
        if year:
            params["year" if kind == "movie" else "first_air_date_year"] = year
        data = await self._get(f"/search/{kind}", **params) or {}
        out = []
        for r in data.get("results") or []:
            out.append({"id": r.get("id"), "kind": kind,
                        "title": r.get("title") or r.get("name") or "",
                        "original": r.get("original_title") or r.get("original_name") or "",
                        "year": (r.get("release_date") or r.get("first_air_date") or "")[:4],
                        "poster": img(r.get("poster_path"), "w185"), "overview": r.get("overview") or "",
                        "_raw": r})
        return out

    async def find(self, kind, titles, year=""):
        """여러 이름(우리말 포스터 제목, 파일 이름...)으로 차례로 찾아 가장 잘 맞는 것 하나. 없으면 None"""
        for t in [t for t in titles if t]:
            for y in ([year, ""] if year else [""]):
                res = await self.search(kind, t, y)
                if res:
                    hit = best_match([r["_raw"] for r in res], t, year, kind)
                    return hit and hit.get("id")
        return None

    async def details(self, kind, tmdb_id):
        """작품 정보 (화면에 쓰는 모양). 없으면 None"""
        d = await self._get(f"/{kind}/{int(tmdb_id)}", append_to_response="credits")
        if not d:
            return None
        credits = d.get("credits") or {}
        directors = [c.get("name") for c in credits.get("crew") or [] if c.get("job") == "Director"][:3]
        if kind == "tv":
            directors = [c.get("name") for c in d.get("created_by") or []][:3] or directors
        return {
            "id": d.get("id"), "kind": kind,
            "title": d.get("title") or d.get("name") or "",
            "original": d.get("original_title") or d.get("original_name") or "",
            "year": (d.get("release_date") or d.get("first_air_date") or "")[:4],
            "overview": d.get("overview") or "", "tagline": d.get("tagline") or "",
            "genres": [g.get("name") for g in d.get("genres") or [] if g.get("name")],
            "rating": round(float(d.get("vote_average") or 0), 1) or None,
            "runtime": d.get("runtime") or ((d.get("episode_run_time") or [None])[0]),
            "poster": img(d.get("poster_path"), "w500"), "backdrop": img(d.get("backdrop_path"), "w1280"),
            "directors": directors,
            "cast": [{"name": c.get("name"), "role": c.get("character") or "", "thumb": img(c.get("profile_path"), "w185")}
                     for c in (credits.get("cast") or [])[:15]],
            "seasons": [s.get("season_number") for s in d.get("seasons") or [] if s.get("season_number") is not None],
        }

    async def season(self, tmdb_id, season):
        """{회차번호: {name, overview, still, air_date}}"""
        d = await self._get(f"/tv/{int(tmdb_id)}/season/{int(season)}")
        if not d:
            return {}
        return {int(e.get("episode_number")): {"name": e.get("name") or "", "overview": e.get("overview") or "",
                                               "still": img(e.get("still_path"), "w300"), "air_date": e.get("air_date") or ""}
                for e in d.get("episodes") or [] if e.get("episode_number") is not None}
