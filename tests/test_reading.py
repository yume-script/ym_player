"""읽을거리 소스: 브런치 연재(brunch.py) / 만개의레시피(recipe.py) - 주소 만들기·검사, 응답·페이지 읽기, 본문 정리, 서버 API."""
import asyncio
import json

import pytest

from ym_player import auth, brunch, config_store, recipe

pytest.importorskip("aiohttp")

LIST_BODY = {"code": 200, "data": {"moreList": True, "nextUrl": brunch.API_URL + "?dayOfWeek=MONDAY&page=2", "list": [
    {"userId": "a1B2", "articleNo": 17, "magazineTitle": "고양이와 살기", "userName": "냥집사", "userImage": "http://img1.daumcdn.net/u.jpg",
     "articleTitle": "3화 첫눈", "articleContentSummary": " 눈이 왔다 ", "articleImage": "//img1.daumcdn.net/a.jpg",
     "publishTime": 1762700000000, "likeCount": 12, "donationTotalAmount": 3000, "serialStatus": "ONGOING", "new": True},
    {"userId": "../evil", "articleNo": 1, "magazineTitle": "이상한 주소"},
]}}

ARTICLE_PAGE = """<html><head>
<script type="application/json" id="ARTICLE_DATA">{"title": "3화 첫눈", "subTitle": "겨울", "authorName": "냥집사",
 "publishTime": 1762700000000, "likeCount": 12, "articleImageUrl": "http://img1.daumcdn.net/c.jpg",
 "magazineAddressUri": "/brunchbook/cat", "isMembershipContent": false}</script></head><body>
<a class="link_magazinetit" href="/brunchbook/cat"><span class="tag_state">연재중</span> 고양이와 살기 </a>
<div id="ArticleView" class="text_align_left">
 <div class="wrap_item item_type_text"><p>눈이 <b>왔다</b>.<script>alert(1)</script></p>
 <p onclick="x()">고양이가 <a href="javascript:alert(1)">창</a>을 봤다. <a href="/@@a1B2/16">지난 화</a></p>
 <img src="http://img1.daumcdn.net/p.jpg" onerror="steal()"><iframe src="https://evil"></iframe>
 <blockquote class="blockquote_type1">인용</blockquote><hr class="hr_type_2"></div>
</div><div class="info_keyword">태그</div>
<ul class="list_magazine">
 <li class="item_magazine"><a href="/@@a1B2/16"><span class="txt_num">2화</span><span class="txt_magazine">밤 산책</span></a></li>
 <li class="item_magazine magazine_current"><a href="/@@a1B2/17"><span class="txt_num">3화</span><span class="txt_magazine">첫눈</span></a></li>
 <li class="item_magazine"><span class="txt_num">4화</span><span class="txt_magazine">다음 화</span><span class="info_tobe">발행 예정</span></li>
</ul></body></html>"""


def test_brunch_urls_and_list():
    assert brunch.list_url("MONDAY", "LIKE_COUNT").endswith("dayOfWeek=MONDAY&orderKeyword=LIKE_COUNT&serialStatus=ONGOING")
    assert "orderKeyword=PUBLISH_TIME" in brunch.list_url("FRIDAY", "DROP TABLE")     # 모르는 정렬은 최신순
    assert brunch.list_url("COMPLETE", "") == brunch.API_URL + "?serialStatus=COMPLETE"
    assert brunch.list_url("", "", brunch.API_URL + "?page=2") == brunch.API_URL + "?page=2"
    for bad in ("https://evil.example/?x", "http://127.0.0.1/"):
        with pytest.raises(ValueError):
            brunch.list_url("MONDAY", "", bad)                                        # 다음 페이지는 브런치 API 주소만
    with pytest.raises(ValueError):
        brunch.list_url("FUNDAY", "")
    d = brunch.parse_list(LIST_BODY)
    a, b = d["items"]
    assert a["url"] == "https://brunch.co.kr/@@a1B2/17" and a["image"] == "https://img1.daumcdn.net/a.jpg"
    assert a["author_image"].startswith("https://") and a["summary"] == "눈이 왔다" and a["is_new"]
    assert b["user_id"] == "" and b["url"] == ""                                       # 이상한 아이디는 열 수 없게
    assert d["more"] and d["next_url"].startswith(brunch.API_URL)
    with pytest.raises(brunch.BrunchError):
        brunch.parse_list({"code": 500, "title": "점검 중"})


def test_brunch_article_is_sanitized():
    a = brunch.parse_article(ARTICLE_PAGE, "https://brunch.co.kr/@@a1B2/17")
    assert a["title"] == "3화 첫눈" and a["author"] == "냥집사" and a["book_title"] == "고양이와 살기"
    assert a["cover"] == "https://img1.daumcdn.net/c.jpg" and a["align"] == "left" and not a["restricted"]
    body = a["body"]
    for bad in ("<script", "alert(1)", "onclick", "onerror", "javascript:", "<iframe", "evil", "태그"):
        assert bad not in body, bad
    assert "<b>왔다</b>" in body and 'href="https://brunch.co.kr/@@a1B2/16"' in body
    assert 'src="https://img1.daumcdn.net/p.jpg"' in body and 'referrerpolicy="no-referrer"' in body
    assert 'class="bb-blockquote_type1"' in body and "<hr class=\"bb-hr_type_2\">" in body
    assert [(e["num"], e["article_no"], e["current"]) for e in a["episodes"]] == [("2화", 16, False), ("3화", 17, True)]   # 발행 예정은 빼고
    with pytest.raises(brunch.BrunchError):
        brunch.parse_article("<html>없음</html>", "u")
    for bad in (("../x", 1), ("ab", "1; drop"), ("", 3)):
        with pytest.raises(ValueError):
            brunch.article_url(*bad)
    locked = ARTICLE_PAGE.replace('"isMembershipContent": false', '"isMembershipContent": true')
    assert brunch.parse_article(locked, "u")["restricted"] is True


RANK_PAGE = """<ul class="common_sp_list_ul">
<li class="common_sp_list_li"><div class="common_sp_thumb"><a href="/recipe/6903394" class="common_sp_link">
 <img src="https://recipe1.ezmember.co.kr/cache/recipe/2019/03/a.jpg" alt="김치찌개"><img src="https://recipe1.ezmember.co.kr/img/icon_vod.png"></a></div>
 <div class="common_sp_caption"><div class="common_sp_caption_tit line2">백종원 김치찌개 &amp; 계란말이</div>
 <div class="common_sp_caption_rv_name">요리왕</div></div></li>
<li class="common_sp_list_li"><a href="/recipe/6903394"><img src="x.jpg"></a></li>
<li class="common_sp_list_li"><a href="https://www.10000recipe.com/recipe/123"><img data-src="//recipe1.ezmember.co.kr/b.jpg" src="/img/blank.gif" alt="된장국"></a></li>
<li><a href="/recipe/999">그림 없는 링크</a></li>
</ul>"""

RECIPE_PAGE = """<html><head><script type="application/ld+json">{"@context": "http://schema.org", "@type": "Recipe",
 "name": "백종원 김치찌개", "description": "칼칼한 찌개", "image": ["https://recipe1.ezmember.co.kr/main.jpg"],
 "author": {"@type": "Person", "name": "요리왕"}, "recipeIngredient": ["김치 1/4포기"],
 "recipeInstructions": [{"@type": "HowToStep", "text": "ld 순서"}]}</script></head><body>
<div class="view2_summary st3"><h3>백종원 김치찌개 &amp; 밥</h3>
<div class="view2_summary_info"><span class="view2_summary_info1">2인분</span><span class="view2_summary_info2">30분 이내</span><span class="view2_summary_info3">초급</span></div></div>
<div id="divConfirmedMaterialArea"><ul>
 <li><div class="ingre_list_name"><a href="#">김치</a></div><span class="ingre_list_ea">1/4포기</span></li>
 <li><div class="ingre_list_name">돼지고기 <script>x</script></div><span class="ingre_list_ea">200g</span></li>
</ul></div>
<div class="view_step">
<div id="stepDiv1" class="view_step_cont media step1"><div id="stepdescr1" class="media-body">김치를 <b>썬다</b>.</div>
 <div id="stepimg1"><img src="https://recipe1.ezmember.co.kr/s1.jpg"></div></div>
<div id="stepDiv2" class="view_step_cont media step2"><div id="stepdescr2" class="media-body">끓인다.</div></div>
<div class="view_step_tip">팁</div></div></body></html>"""


def test_recipe_urls_and_parsing():
    assert recipe.list_url() == recipe.BASE + "/recipe/list.html?order=reco&page=1"   # 랭킹 페이지는 순위가 스크립트로 와서 목록 페이지로
    assert recipe.list_url("0", "", 2) == recipe.BASE + "/recipe/list.html?cat4=63&page=2"
    assert recipe.list_url("99") == recipe.list_url()                                  # 모르는 분류는 추천순
    assert recipe.list_url("cat4=1&x", "") == recipe.list_url()
    assert recipe.list_url("", "김치 찌개&x=1") == recipe.BASE + "/recipe/list.html?q=%EA%B9%80%EC%B9%98+%EC%B0%8C%EA%B0%9C%26x%3D1&page=1"
    items = recipe.parse_list(RANK_PAGE)
    assert [(x["id"], x["title"], x["by"]) for x in items] == [("6903394", "백종원 김치찌개 & 계란말이", "요리왕"), ("123", "된장국", "")]
    assert items[0]["image"].endswith("/a.jpg") and items[1]["image"] == "https://recipe1.ezmember.co.kr/b.jpg"   # data-src 먼저
    r = recipe.parse_recipe(RECIPE_PAGE, "6903394")
    assert r["title"] == "백종원 김치찌개 & 밥" and r["intro"] == "칼칼한 찌개" and r["by"] == "요리왕"
    assert r["info"] == ["2인분", "30분 이내", "초급"] and r["image"] == "https://recipe1.ezmember.co.kr/main.jpg"
    assert r["ingredients"] == ["김치 1/4포기", "돼지고기 200g"]                          # 스크립트 내용은 빼고
    assert [s["text"] for s in r["steps"]] == ["김치를 썬다.", "끓인다."]
    assert r["steps"][0]["image"] == "https://recipe1.ezmember.co.kr/s1.jpg" and r["steps"][1]["image"] == ""
    # 화면 HTML이 바뀌어도 ld+json으로
    ld_only = RECIPE_PAGE.split("<body>")[0] + "<body></body></html>"
    r2 = recipe.parse_recipe(ld_only, "1")
    assert r2["title"] == "백종원 김치찌개" and r2["ingredients"] == ["김치 1/4포기"] and r2["steps"][0]["text"] == "ld 순서"
    with pytest.raises(recipe.RecipeError):
        recipe.parse_recipe("<html></html>", "1")


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MP_USER", raising=False)
    monkeypatch.delenv("MP_PASSWORD", raising=False)
    monkeypatch.setenv("MP_UPDATE_CHECK", "0")
    auth._fails.clear()
    auth.create_user("boss", "secret1", "admin")
    return tmp_path


def test_reading_api(data, monkeypatch):
    from aiohttp import CookieJar
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server
    asked = []

    async def brunch_get(self, url, accept):
        asked.append(url)
        if url.startswith(brunch.API_URL):
            return json.dumps(LIST_BODY)
        return ARTICLE_PAGE

    async def recipe_get(self, url):
        asked.append(url)
        return RECIPE_PAGE if "/recipe/6903394" in url else RANK_PAGE
    monkeypatch.setattr(brunch.Brunch, "_get", brunch_get)
    monkeypatch.setattr(recipe.Recipes, "_get", recipe_get)
    cfg = config_store.load()
    cfg["sources"] = [config_store._clean_source({"id": "br", "type": "brunch"}, None),
                      config_store._clean_source({"id": "rc", "type": "recipe"}, None)]
    assert cfg["sources"][0]["name"] == "브런치 연재" and cfg["sources"][1]["name"] == "만개의레시피"
    config_store.save(cfg)

    async def main():
        c = TestClient(TestServer(server.create_app(require_auth=True)), cookie_jar=CookieJar(unsafe=True))
        await c.start_server()
        try:
            assert (await c.get("/api/brunch/br/list?day=MONDAY")).status == 401                 # 로그인 필요
            await c.post("/api/auth/login", json={"username": "boss", "password": "secret1"})
            d = await (await c.get("/api/brunch/br/list?day=MONDAY&sort=LIKE_COUNT")).json()
            assert d["items"][0]["title"] == "3화 첫눈" and d["more"]
            n = len(asked)
            await c.get("/api/brunch/br/list?day=MONDAY&sort=LIKE_COUNT")
            assert len(asked) == n                                                                 # 5분은 기억
            r = await c.get("/api/brunch/br/list", params={"day": "MONDAY", "next": "http://127.0.0.1:1/x"})
            assert r.status == 400
            a = (await (await c.get("/api/brunch/br/article/a1B2/17")).json())["article"]
            assert a["title"] == "3화 첫눈" and "<script" not in a["body"]
            assert (await c.get("/api/brunch/br/article/a1B2/abc")).status == 404
            assert (await c.get("/api/brunch/rc/list?day=MONDAY")).status == 404                 # 종류가 다른 소스
            d = await (await c.get("/api/recipe/rc/list")).json()
            assert [x["id"] for x in d["items"]] == ["6903394", "123"] and "밑반찬" in d["categories"]
            await c.get("/api/recipe/rc/list", params={"q": "김치"})
            assert any("q=%EA%B9%80%EC%B9%98" in u for u in asked)
            r = (await (await c.get("/api/recipe/rc/item/6903394")).json())["recipe"]
            assert r["title"].startswith("백종원 김치찌개") and r["steps"]
            st = await (await c.get("/api/admin/status")).json()
            rows = {s["type"]: s for s in st["sources"]}
            assert rows["brunch"]["ok"] is True and rows["recipe"]["ok"] is True

            async def down(self, url, accept="text/html"):
                raise brunch.BrunchError("브런치에 연결하지 못했습니다: 테스트")
            monkeypatch.setattr(brunch.Brunch, "_get", down)
            # 아직 받은 적 없는 요일로 (상태 확인이 오늘 요일 목록을 받아 기억해 두므로 - 금요일에 돌리면 FRIDAY가 이미 있다)
            import datetime as _dt
            unseen = [d for d in ("SATURDAY", "SUNDAY") if d != ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")[_dt.datetime.now().weekday()]][0]
            r = await c.get(f"/api/brunch/br/list?day={unseen}")
            assert r.status == 502 and "연결하지 못했습니다" in (await r.json())["error"]
        finally:
            await c.close()
    asyncio.run(main())


class _Resp:
    def __init__(self, status, body=b"", location=None, charset="utf-8"):
        self.status, self._body, self.charset = status, body, charset
        self.headers = {"Location": location} if location else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def text(self, encoding="utf-8", errors="strict"):
        return self._body.decode(encoding, errors)

    async def read(self):
        return self._body


class _Http:
    def __init__(self, routes):
        self.routes, self.asked = routes, []

    def get(self, url, **kw):
        assert kw.get("allow_redirects") is False   # 리다이렉트는 직접 (주소 확인하며)
        self.asked.append(url)
        return self.routes[url]


def test_redirects_followed_only_inside_the_site():
    url = brunch.list_url("MONDAY", "")
    moved = brunch.API_URL + "?dayOfWeek=MONDAY&moved=1"
    http = _Http({url: _Resp(302, location=moved), moved: _Resp(200, json.dumps(LIST_BODY).encode())})
    d = asyncio.run(brunch.Brunch(lambda: http).list("MONDAY"))
    assert d["items"][0]["title"] == "3화 첫눈" and http.asked == [url, moved]
    # 처음에 카카오 로그인 확인으로 넘기면 따라가지 않고 같은 주소를 다시 (쿠키가 생겨 두 번째는 됨)
    class Bounce(_Http):
        def get(self, u, **kw):
            self.asked.append(u)
            if len(self.asked) == 1:
                return _Resp(302, location="https://kauth.kakao.com/oauth/authorize?x=1")
            return _Resp(200, json.dumps(LIST_BODY).encode())
    once = Bounce({})
    assert asyncio.run(brunch.Brunch(lambda: once).list("MONDAY"))["items"] and once.asked == [url, url]
    out = _Http({url: _Resp(302, location="https://login.example.com/?next=x")})
    with pytest.raises(brunch.BrunchError, match="다른 곳으로"):   # 계속 넘기면 (세 번) 오류
        asyncio.run(brunch.Brunch(lambda: out).list("MONDAY"))
    assert out.asked == [url, url, url]
    # 레시피: 리다이렉트 + EUC-KR 페이지, 목록이 비면 받은 페이지를 알려 준다
    rank = recipe.list_url()
    page = RANK_PAGE.encode("cp949", "replace")
    http = _Http({rank: _Resp(301, location="/recipe/list.html?order=reco&page=1&x=1"),
                  recipe.BASE + "/recipe/list.html?order=reco&page=1&x=1": _Resp(200, page, charset="euc-kr")})
    items = asyncio.run(recipe.Recipes(lambda: http).list())
    assert items[0]["title"].startswith("백종원 김치찌개")
    empty = _Http({rank: _Resp(200, "<html><title>점검 중</title><body>잠시 후</body></html>".encode())})
    with pytest.raises(recipe.RecipeError, match="점검 중"):
        asyncio.run(recipe.Recipes(lambda: empty).list())


def test_recipe_list_variants():
    """따옴표가 다르거나 게으른 그림(data-original)·자리 채움 그림이어도 읽는다"""
    page = """<li><a href='/recipe/111?from=rank'><img src='/img/blank.gif' data-original='https://recipe1.ezmember.co.kr/x.jpg' alt='잡채'></a>
    <p class='common_sp_caption_tit'>명절 잡채</p></li>
    <li><a href=/recipe/222><span class="common_sp_caption_tit">사진 없는 레시피</span></a></li>
    <li><a href="/recipe/333">그냥 링크</a></li>"""
    items = recipe.parse_list(page)
    assert [(x["id"], x["title"], x["image"]) for x in items] == [
        ("111", "명절 잡채", "https://recipe1.ezmember.co.kr/x.jpg"), ("222", "사진 없는 레시피", "")]
