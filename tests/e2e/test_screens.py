"""화면 기본 흐름 - 로그인, 홈, 설정 탭, 라디오·팟캐스트 재생, Plex -> 텔레그램 고르기 창, 휴대폰 크기.

Plex·텔레그램은 서버가 없으니 브라우저에서 그 API 응답을 흉내 낸다 (page.route). 나머지는 실제 서버 그대로.
"""
import pytest

from .conftest import ADMIN, js_modules, new_context, player_state, set_sources, wait_playing


def test_login_page(browser, server):
    ctx = new_context(browser)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(server + "/")
    assert "/login" in pg.url                                   # 로그인 안 했으면 로그인 화면으로
    pg.fill("input[name=username], #username", ADMIN[0])
    pg.fill("input[type=password]", ADMIN[1])
    pg.keyboard.press("Enter")
    pg.wait_for_url(lambda u: "/login" not in u, timeout=10000)
    pg.wait_for_selector(".page.home, .empty", timeout=10000)
    ctx.close()
    assert not errors, errors


def test_settings_tabs(page, server):
    page.goto(server + "/#/settings")
    page.wait_for_selector(".src-tabs")
    tabs = page.locator(".tabs:not(.src-tabs) > button.tab, .tabs:not(.src-tabs) > .tab")
    names = tabs.all_inner_texts()
    assert len(names) >= 8, names
    for i in range(len(names)):                                 # 모든 탭이 오류 없이 그려지는지
        tabs.nth(i).click()
        page.wait_for_timeout(400)
    tabs.nth(0).click()                                         # 소스 탭으로 돌아가서
    page.wait_for_timeout(300)
    for t in page.locator(".src-tabs .tab").all():              # 소스 종류 하위 탭도
        t.click()
        page.wait_for_timeout(150)


def test_radio_and_podcast(page, server, upstream):
    set_sources(page, server, [
        {"type": "radio", "name": "E2E 라디오", "enabled": True,
         "stations": [{"name": "테스트 방송", "url": f"{upstream.base}/live.wav", "subtitle": "흐르는 소리"}]},
        {"type": "podcast", "name": "E2E 팟캐스트", "enabled": True, "feeds": [{"url": f"{upstream.base}/feed.xml"}]},
    ])
    page.goto(server + "/#/home")
    page.wait_for_selector(".home-slot .card", timeout=15000)
    text = page.inner_text(".page.home")
    assert "테스트 방송" in text and "E2E 팟캐스트" in text

    # 라디오: 채널 카드를 누르면 라이브 재생
    page.click(".card:has-text('테스트 방송')")
    st = wait_playing(page)
    assert st["src"].startswith("/api/stream")

    # 팟캐스트: 프로그램 화면 -> 다음에 들을 회 재생, 소리만이어도 재생 속도 버튼
    js_modules(page)
    page.evaluate("window._pl.stop()")
    page.goto(server + "/#/home")
    page.click(".card:has(.card-title:text-is('E2E 팟캐스트'))")   # (에피소드 카드 부제에도 프로그램 이름이 있어서 제목으로)
    page.wait_for_selector(".detail-actions .btn-accent")
    assert "에피소드 2개" in page.inner_text(".detail-info")
    page.click(".detail-actions .btn-accent")
    st = wait_playing(page)
    assert "/api/podcast/" in st["src"] and "/audio/" in st["src"]
    assert not page.locator(".rate-btn").first.evaluate("e => e.classList.contains('hidden')")
    page.evaluate("window._pl.stop()")


PLEX_META = {"MediaContainer": {"Metadata": [{
    "ratingKey": "500", "type": "movie", "title": "듄", "year": 2021, "duration": 6000,
    # (원본 그대로 틀 수 있는 형식이라고 해 두고, 받는 내용은 소리 파일 - 브라우저가 내용을 보고 연다)
    "Media": [{"container": "webm", "videoCodec": "vp9", "audioCodec": "opus",
               "Part": [{"id": 1, "key": "/library/parts/1/file.webm", "container": "webm", "Stream": []}]}]}]}}


@pytest.fixture
def plex_tg(page, server, upstream):
    """Plex 소스 + 텔레그램 소스. Plex·텔레그램 API는 흉내, 영상 대신 소리 파일"""
    set_sources(page, server, [
        {"id": "px", "type": "plex", "name": "우리집 Plex", "url": "http://127.0.0.1:9", "token": "t", "enabled": True},
        {"id": "tg", "type": "telegram", "name": "텔레", "api_id": "1", "api_hash": "h", "enabled": True,
         "channels": [{"id": 777, "title": "영화 채널"}]}])
    wav = open(upstream.wav, "rb").read()

    def route(r):
        u = r.request.url
        if "/api/plex/px/library/metadata/500" in u:
            return r.fulfill(json=PLEX_META)
        if "/api/plex-tg/px/500" in u:
            return r.fulfill(json={"success": True, "found": True, "tg": "tg", "channel": 777, "msg": 10, "title": "듄.wav", "how": "제목이 같음"})
        if "/api/tg/tg/video/777/10" in u:
            return r.fulfill(json={"success": True, "item": {"channel": 777, "msg": 10, "title": "듄.wav", "duration": 6,
                                                             "position": 0, "play": {"mode": "direct"}, "subs": [], "parts": []}})
        if "/api/tg/tg/stream/777/10" in u or "/api/plex/px/library/parts/" in u:
            return r.fulfill(body=wav, content_type="audio/wav")
        if "/api/tg/tg/progress" in u or "/api/plex/px/:/" in u or "/api/tg/tg/probe" in u:
            return r.fulfill(json={"success": True})
        return r.continue_()
    page.route("**/api/**", route)
    page.goto(server + "/#/home")
    page.wait_for_timeout(1000)
    js_modules(page)
    return page


def _play_plex(page):
    # (void: 고르기 창이 떠 있는 동안 playItem이 끝나지 않으므로 기다리지 않는다)
    page.evaluate("void window._pl.playItem(window._reg.byId('px'), '500')")


def test_plex_pick_source(plex_tg):
    page = plex_tg
    page.evaluate("localStorage.removeItem('yp_plex_tg_choice')")
    # Plex를 고르면 Plex로
    _play_plex(page)
    page.wait_for_selector(".source-ask")
    assert "텔레그램에도 있습니다" in page.inner_text(".source-ask")
    page.click(".source-pick:has-text('Plex로 재생')")
    st = wait_playing(page)
    assert "/api/plex/px/library/parts/" in st["src"]
    page.evaluate("window._pl.stop()")
    # 텔레그램 + 다음부터 묻지 않기
    _play_plex(page)
    page.wait_for_selector(".source-ask")
    page.check(".source-ask input[type=checkbox]")
    page.click(".source-pick:has-text('텔레그램으로 재생')")
    st = wait_playing(page)
    assert st["src"] == "/api/tg/tg/stream/777/10" and st["engine"].startswith("텔레그램")
    page.evaluate("window._pl.stop()")
    # 기억한 대로 창 없이 텔레그램
    _play_plex(page)
    st = wait_playing(page)
    assert st["src"] == "/api/tg/tg/stream/777/10" and page.locator(".source-ask").count() == 0
    page.evaluate("window._pl.stop()")


def test_plex_pick_cancel(plex_tg):
    page = plex_tg
    page.evaluate("localStorage.setItem('yp_plex_tg_choice', JSON.stringify('ask'))")
    _play_plex(page)
    page.wait_for_selector(".source-ask")
    page.click(".modal .icon-btn[title='닫기']")                # 창을 닫으면 재생하지 않음
    page.wait_for_timeout(1500)
    assert player_state(page)["src"] == ""


def test_phone_layout(browser, server, page):
    ctx = new_context(browser, viewport={"width": 390, "height": 844}, storage_state=page.context.storage_state())
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(server + "/#/home")
    pg.wait_for_selector(".page.home, .empty", timeout=10000)
    width = pg.evaluate("document.documentElement.scrollWidth")
    assert width <= 390 + 2, f"가로로 넘침: {width}px"            # 휴대폰 화면에서 옆으로 밀리지 않게
    ctx.close()
    assert not errors, errors


def test_iptv_chat_panel(page, server, upstream):
    """IPTV 채팅 패널: 접두어로 시작하는 채널에서 오른쪽에 채팅 페이지, 닫기 -> [채팅 열기], 다른 채널에서는 숨김"""
    set_sources(page, server, [{"id": "tv", "type": "iptv", "name": "E2E TV", "enabled": True, "m3u": f"{upstream.base}/list.m3u",
                                "chat_url": f"{upstream.base}/chat.html", "chat_prefix": "[스포츠]"}])
    page.goto(server + "/#/live/tv")
    page.wait_for_selector("text=[스포츠] 축구 중계", timeout=15000)
    js_modules(page)
    play = """(name) => { const p = window._reg.byId('tv');
        return p.loadChannels().then((chs) => { const ch = chs.find((c) => c.name === name); window._pl.playChannel(p, ch, chs.map((c) => ({provider: p, channel: c}))); }); }"""
    page.evaluate(play, "[스포츠] 축구 중계")
    page.wait_for_selector(".chat-panel:not(.hidden)", timeout=10000)
    frame = page.frame_locator(".chat-frame")
    assert frame.locator("#hello").inner_text(timeout=10000) == "채팅 페이지"
    assert page.evaluate("document.querySelector('.player').classList.contains('chat-open')")
    page.click(".chat-panel button[title='채팅 닫기']")              # 닫기 -> [채팅 열기]
    assert page.locator(".chat-panel").evaluate("e => e.classList.contains('hidden')")
    page.click(".chat-open-btn")
    assert not page.locator(".chat-panel").evaluate("e => e.classList.contains('hidden')")
    page.click(".chat-panel button[title='채팅 새로고침']")
    assert frame.locator("#hello").inner_text(timeout=10000) == "채팅 페이지"
    page.evaluate(play, "뉴스 채널")                                   # 접두어가 아닌 채널: 패널도 버튼도 없음
    page.wait_for_timeout(800)
    assert page.locator(".chat-panel").evaluate("e => e.classList.contains('hidden')")
    assert page.locator(".chat-open-btn").evaluate("e => e.classList.contains('hidden')")
    page.evaluate("window._pl.stop()")


def test_iptv_chat_found_from_notice(page, server, upstream):
    """채팅 주소가 바뀌는 곳: 공지 채널 -> 사이트 -> 채팅 주소를 서버가 찾아 띄운다 (브라우저가 채팅을 바로 엶)"""
    set_sources(page, server, [{"id": "tv2", "type": "iptv", "name": "E2E TV2", "enabled": True, "m3u": f"{upstream.base}/list.m3u",
                                "chat_notice": f"{upstream.base}/notice", "chat_host": "127.0.0.1", "chat_prefix": "[스포츠]"}])
    page.goto(server + "/#/live/tv2")
    page.wait_for_selector("text=[스포츠] 축구 중계", timeout=15000)
    js_modules(page)
    page.evaluate("""() => { const p = window._reg.byId('tv2');
        return p.loadChannels().then((chs) => window._pl.playChannel(p, chs[0], chs.map((c) => ({provider: p, channel: c})))); }""")
    page.wait_for_selector(".chat-panel:not(.hidden)", timeout=10000)
    frame = page.frame_locator(".chat-frame")
    assert frame.locator("#hello").inner_text(timeout=10000) == "채팅 페이지"
    assert page.evaluate("document.querySelector('.chat-frame').src").endswith("/chat.html?token=abc")
    page.click(".chat-panel button[title='채팅 새로고침']")              # 다시 찾기
    assert frame.locator("#hello").inner_text(timeout=10000) == "채팅 페이지"
    page.evaluate("window._pl.stop()")


def test_chat_bookmarklet(page, server, upstream):
    """서버가 사이트를 못 열 때: 설정의 북마크 [채팅 주소 보내기]를 사이트 페이지에서 누르면 채팅 주소가 YMPlayer에 저장되고 채널에 뜬다"""
    set_sources(page, server, [{"id": "tv3", "type": "iptv", "name": "E2E TV3", "enabled": True, "m3u": f"{upstream.base}/list.m3u",
                                "chat_host": "127.0.0.1", "chat_prefix": "[스포츠]"}])
    page.goto(server + "/#/settings")
    page.wait_for_selector(".src-tabs")
    page.click(".src-tabs .tab:has-text('IPTV')")
    href = page.locator("a.chat-bookmarklet").first.get_attribute("href")
    assert href.startswith("javascript:")
    code = __import__("urllib.parse").parse.unquote(href[len("javascript:"):])
    site = page.context.new_page()                                     # "내 브라우저로 연 사이트 페이지"
    site.goto(upstream.base + "/")
    site.evaluate("window.open = (u) => { window.__opened = u; }")
    site.evaluate(code)
    opened = site.evaluate("window.__opened")
    assert opened.startswith(server + "/#/chat-set?sid=tv3&url=") and "chat.html%3Ftoken%3Dabc" in opened
    site.close()
    page.goto(opened)
    page.wait_for_selector(".chat-set-ok", timeout=10000)
    page.goto(server + "/#/live/tv3")
    page.wait_for_selector("text=[스포츠] 축구 중계", timeout=15000)
    js_modules(page)
    page.evaluate("""() => { const p = window._reg.byId('tv3');
        return p.loadChannels().then((chs) => window._pl.playChannel(p, chs[0], chs.map((c) => ({provider: p, channel: c})))); }""")
    page.wait_for_selector(".chat-panel:not(.hidden)", timeout=10000)
    assert page.frame_locator(".chat-frame").locator("#hello").inner_text(timeout=10000) == "채팅 페이지"
    page.evaluate("window._pl.stop()")


def test_mirror_folder_rule(page, server):
    """자동 올리기: 규칙을 "rclone 드라이브 · 폴더"로 바꾸면 폴더 칸과 720p 줄이기 칸이 보이고 Plex 칸은 숨는다.
    설정의 [자동 올리기] 탭은 숨겨 두었다(1.2.21, HIDE_MIRROR - BookOasis Assistant로 옮김). 화면은 주소(?tab=mirror)로 연다"""
    page.goto(server + "/#/settings")
    page.wait_for_selector(".src-tabs")
    assert page.locator(".tabs:not(.src-tabs) > .tab:has-text('자동 올리기')").count() == 0   # 탭은 숨김
    page.goto(server + "/#/settings?tab=mirror")
    page.wait_for_selector("button:has-text('규칙 추가')")
    page.click("button:has-text('규칙 추가')")
    rule = page.locator(".mirror-rule").last
    assert rule.locator(".mirror-folder").is_hidden() and rule.locator(".mirror-height").input_value() == "720"   # Plex도 720p로
    rule.locator(".mirror-kind").select_option("folder")
    assert rule.locator(".mirror-folder").is_visible() and rule.locator(".mirror-enc").is_visible()
    assert rule.locator(".mirror-height").input_value() == "720"
    assert "자막 파일(SRT 등)" in rule.inner_text()
    # [고르기]: 드라이브 목록(못 찾으면 이 컴퓨터 /mnt, /)에서 하위 폴더로 들어가 [이 폴더로]
    rule.locator(".mirror-pick-folder").click()
    page.wait_for_selector(".folder-pick-item:has-text('/')")
    page.locator(".folder-pick-item").filter(has_text="이 컴퓨터").last.click()      # "/"
    page.wait_for_selector(".folder-pick-path")
    assert page.inner_text(".folder-pick-path") == "/"
    page.locator(".folder-pick-item:has-text('tmp')").first.click()
    page.wait_for_selector(".folder-pick-path:has-text('/tmp')")
    page.click(".modal button:has-text('이 폴더로')")
    assert rule.locator(".mirror-folder").input_value() == "/tmp"
    rule.locator(".mirror-kind").select_option("plex")
    assert rule.locator(".mirror-folder").is_hidden()


def test_brunch_and_recipe_reader(page, server):
    """브런치 연재·만개의레시피 소스: 홈 카드 -> 글 읽기 화면 (본문, 앞 회차, 원문 링크), 레시피 재료·순서"""
    set_sources(page, server, [{"type": "brunch", "name": "브런치 연재", "enabled": True},
                               {"type": "recipe", "name": "만개의레시피", "enabled": True}])
    page.goto(server + "/#/home")
    page.wait_for_selector(".card:has-text('고양이와 살기')", timeout=15000)
    page.click(".card:has-text('고양이와 살기')")
    page.wait_for_selector(".article-reader .article-title")
    assert page.inner_text(".article-title") == "3화 첫눈"
    body = page.inner_text(".article-body")
    assert "왔다" in body and "alert" not in body and "null" not in page.inner_text(".article-page")
    assert page.locator(".article-reader script").count() == 0
    assert page.locator(".article-nav-btn:has-text('이전')").count() == 1        # 2화로
    assert page.get_attribute(".article-reader .doc-tools a", "href").endswith("/@@a1B2/17")
    page.keyboard.press("Escape")
    page.wait_for_selector(".article-reader", state="detached")
    # 레시피
    page.wait_for_selector(".card:has-text('백종원 김치찌개')")
    page.click(".card:has-text('백종원 김치찌개')")
    page.wait_for_selector(".article-reader .recipe-step")
    assert page.locator(".recipe-ing li").all_inner_texts() == ["김치 1/4포기", "돼지고기 200g"]
    assert page.locator(".recipe-step").count() == 2 and "2인분" in page.inner_text(".article-meta")
    assert "null" not in page.inner_text(".article-page")
    page.click(".article-reader .reader-bar .icon-btn")
    page.wait_for_selector(".article-reader", state="detached")
    # 라이브러리: 분류 칩 대신 [분류] 고르기
    page.goto(server + "/#/library/" + page.evaluate("fetch('/api/config').then(r => r.json()).then(c => c.sources.find(s => s.type === 'recipe').id)") + "/recipes")
    page.wait_for_selector(".page.library .card")
    assert "밑반찬" in page.inner_text(".page.library select")
    # 검색: 멈추거나 Enter를 누르면 사이트 검색(q=)으로 다시 받고, 주소에도 남는다. 검색 중에는 분류를 쓰지 않는다
    with page.expect_request(lambda r: "/api/recipe/" in r.url and "q=%EA%B9%80%EC%B9%98" in r.url):
        page.fill(".lib-search", "김치")
        page.press(".lib-search", "Enter")
    page.wait_for_selector(".page.library .card")
    assert "q=%EA%B9%80%EC%B9%98" in page.url or "q=김치" in page.url
    assert page.locator(".page.library select").first.is_disabled()
    with page.expect_request(lambda r: "/api/recipe/" in r.url and "cat=" in r.url):
        page.fill(".lib-search", "")
        page.press(".lib-search", "Enter")
    assert not page.locator(".page.library select").first.is_disabled()
    # 전체 검색 화면: 제목이 한 줄로, 결과 줄이 그 아래 (위 검색창의 .search 모양을 물려받아 세로로 찌그러지던 것)
    page.goto(server + "/#/search?q=" + "김치")
    page.wait_for_selector(".search-page .card")
    head = page.locator(".search-page .page-head h1").bounding_box()
    row = page.locator(".search-page .card").first.bounding_box()
    assert head["height"] < 60 and head["width"] > 100 and row["y"] > head["y"] + head["height"] - 1


def test_single_library_source_opens_from_nav(page, server):
    """메뉴가 하나뿐인 소스(레시피·브런치·IPTV 등)는 사이드바에서 소스 이름을 한 번 누르면 바로 그 화면"""
    set_sources(page, server, [{"type": "brunch", "name": "브런치 연재", "enabled": True},
                               {"type": "recipe", "name": "만개의레시피", "enabled": True}])
    page.goto(server + "/#/home")
    page.wait_for_selector(".nav-group.nav-single .nav-single-link:has-text('만개의레시피')")
    assert page.locator(".nav-group .nav-toggle").count() == 0          # 펼치는 머리줄 없음
    page.click(".nav-single-link:has-text('만개의레시피')")
    page.wait_for_selector(".page.library h1:has-text('만개의레시피')")
    assert "/recipes" in page.url
    assert page.locator(".nav-single-link.active:has-text('만개의레시피')").count() == 1
    page.click(".nav-single-link:has-text('브런치 연재')")
    page.wait_for_selector(".page.library h1:has-text('브런치 연재')")


def test_nav_head_icons_and_library_crumb(page, server, upstream):
    """하위 메뉴가 있는 소스 머리줄에 소스 종류 아이콘, 라이브러리 제목은 "소스 › 라이브러리" (이름이 같으면 한 번만)"""
    set_sources(page, server, [{"type": "podcast", "name": "팟캐스트", "enabled": True, "feeds": [{"url": f"{upstream.base}/feed.xml"}]},
                               {"type": "recipe", "name": "만개의레시피", "enabled": True}])
    page.goto(server + "/#/home")
    page.wait_for_selector(".nav-toggle .nav-head-icon.fa-podcast")
    page.goto(server + "/" + page.get_attribute(".nav-link:has-text('새 에피소드')", "href"))
    page.wait_for_selector(".page.library h1 .crumb-src")
    assert page.inner_text(".page.library h1").replace("\n", " ").split() == ["팟캐스트", "›", "새", "에피소드"]
    page.click(".nav-single-link:has-text('만개의레시피')")
    page.wait_for_selector(".page.library h1 .crumb-title:has-text('만개의레시피')")
    assert page.locator(".page.library h1 .crumb-src").count() == 0           # 소스 이름과 같으면 한 번만


def test_home_sources_hide_and_lazy(page, server, upstream):
    """홈에 보일 소스 고르기: 끈 소스는 홈에 줄이 없고 요청도 안 함 (사이드바에는 그대로). 다시 켜면 돌아온다"""
    set_sources(page, server, [{"type": "brunch", "name": "브런치 연재", "enabled": True},
                               {"type": "recipe", "name": "만개의레시피", "enabled": True}])
    asked = []
    page.on("request", lambda r: asked.append(r.url) if "/api/brunch/" in r.url else None)
    page.goto(server + "/#/home")
    page.wait_for_selector(".card:has-text('고양이와 살기')")
    page.click(".home-foot button")
    page.locator(".home-src-row:has-text('브런치 연재') input").uncheck()
    page.click(".modal button:has-text('저장')")
    page.wait_for_selector(".home-foot button:has-text('1개 숨김')")
    asked.clear()
    page.reload()
    page.wait_for_selector(".card:has-text('백종원 김치찌개')")
    page.wait_for_timeout(500)
    assert page.locator(".home-slot .card:has-text('고양이와 살기')").count() == 0 and not asked   # 요청도 안 함
    assert page.locator(".nav-single-link:has-text('브런치 연재')").count() == 1                     # 사이드바에는 그대로
    page.click(".home-foot button")
    page.locator(".home-src-row:has-text('브런치 연재') input").check()
    page.click(".modal button:has-text('저장')")
    page.wait_for_selector(".home-slot .card:has-text('고양이와 살기')")


def test_service_worker_caches_app_files(page, server):
    """앱 설치(PWA): 서비스 워커가 등록되고, 다시 열면 화면 파일(/s/…)을 기기에 저장해 둔 것으로 꺼낸다"""
    page.goto(server + "/#/home")
    page.wait_for_function("navigator.serviceWorker && navigator.serviceWorker.controller !== undefined")
    page.evaluate("navigator.serviceWorker.ready")
    page.reload()                                            # 이제 서비스 워커가 이 페이지를 맡는다
    page.wait_for_function("!!navigator.serviceWorker.controller")
    from_sw = []
    page.on("response", lambda r: from_sw.append(r.url) if "/s/" in r.url and r.from_service_worker else None)
    page.reload()
    page.wait_for_selector(".nav-link")
    assert any(u.endswith("/js/app.js") for u in from_sw), from_sw
    assert page.evaluate("caches.keys()")[0].startswith("ymp-")


def test_data_saver_switch(page, server):
    """데이터 절약 (이 브라우저): 자동/켜기/끄기 - 켜면 Plex 기본 화질 720p, 텔레그램 서버 변환 720p, 다음 회차 미리 받기 안 함"""
    page.goto(server + "/#/home")
    page.wait_for_selector(".nav-link")
    base = page.evaluate("[...document.scripts].map(s => s.src).find(s => s.includes('/js/app.js')).replace('app.js', '')")
    get = lambda: page.evaluate(f"import('{base}util.js').then(m => m.dataSaver())")
    page.evaluate("localStorage.setItem('yp_data_saver', JSON.stringify('on'))")
    assert get() is True
    page.evaluate("localStorage.setItem('yp_data_saver', JSON.stringify('off'))")
    assert get() is False
    page.evaluate(f"import('{base}theme.js').then(m => m.openThemeDialog())")
    page.wait_for_selector(".modal select option[value='on']", state="attached")
    assert "데이터 절약" in page.inner_text(".modal")


def test_split_movie_resume_and_part_list(page, server):
    """나눠 올린 영화: [재생]은 보다 만 조각부터, 상세 화면에 "(1/3) 재생"처럼 조각마다 줄, 고른 조각은 그대로, [처음부터]는 첫 조각"""
    set_sources(page, server, [{"id": "tg", "type": "telegram", "name": "텔레", "api_id": "1", "api_hash": "h", "enabled": True,
                                "channels": [{"id": 777, "title": "영화 > 최신"}]}])
    parts = [{"msg": 10, "part": 1, "title": "쥬라기 (1/3)", "duration": 2700, "position": 0, "watched": True, "size": 1, "mime": "video/mp4"},
             {"msg": 11, "part": 2, "title": "쥬라기 (2/3)", "duration": 2700, "position": 600, "watched": False, "size": 1, "mime": "video/mp4"},
             {"msg": 12, "part": 3, "title": "쥬라기 (3/3)", "duration": 2700, "position": 0, "watched": False, "size": 1, "mime": "video/mp4"}]

    def route(r):
        u = r.request.url
        for p in parts:
            if f"/api/tg/tg/video/777/{p['msg']}" in u:
                return r.fulfill(json={"success": True, "item": {"channel": 777, "msg": p["msg"], "title": "쥬라기 월드", "duration": 2700,
                                                                 "position": p["position"], "watched": p["watched"], "file_name": f"j_{p['msg']}.mp4",
                                                                 "play": {"mode": "direct"}, "subs": [], "parts": parts, "caption": ""}})
        if "/api/tg/tg/meta/" in u:
            return r.fulfill(json={"success": True, "meta": None})
        return r.continue_()
    page.route("**/api/tg/**", route)
    page.goto(server + "/#/home")
    page.wait_for_timeout(800)
    js_modules(page)
    tg = "window._reg.byId('tg')"
    assert page.evaluate(f"{tg}.confirmPlay('777:10', {{}})") == "777:11"            # 목록 카드: 보다 만 2번째부터
    assert page.evaluate(f"{tg}.confirmPlay('777:10:p', {{}})") == "777:10"          # 조각 목록에서 고른 1번째는 그대로
    assert page.evaluate(f"{tg}.confirmPlay('777:11', {{fromStart: true}})") == "777:10"   # [처음부터]는 첫 조각
    d = page.evaluate(f"{tg}.getDetail('777:10').then(d => ({{id: d.id, off: d.viewOffset, kids: d.children.map(c => [c.id, c.raw.index, c.raw.title])}}))")
    assert d["id"] == "777:11" and d["off"] == 600000
    assert [k[1] for k in d["kids"]] == ["(1/3)", "(2/3)", "(3/3)"] and d["kids"][0][0] == "777:10:p"
    assert "다 봄" in d["kids"][0][2] and "이어 볼 곳" in d["kids"][1][2]
    # 화면: 상세에 조각 줄 3개
    page.goto(server + "/#/item/tg/777%3A10")
    page.wait_for_selector(".track-row")
    assert page.locator(".track-row").count() == 3 and "(2/3)" in page.inner_text(".track-list")


def test_telegram_list_title_and_lines(page, server):
    """텔레그램 목록: 영화 제목은 찾아 둔 작품 제목, 둘째 줄에 채널 위치 없음, 셋째 줄(격자)은 파일 이름 대신 등록일"""
    set_sources(page, server, [{"id": "tg", "type": "telegram", "name": "텔레", "api_id": "1", "api_hash": "h", "enabled": True,
                                "channels": [{"id": 777, "title": "영화 > 최신"}]}])
    items = [{"channel": 777, "msg": 10, "kind": "video", "title": "The Naked Gun (2025)", "meta_title": "총알 탄 사나이",
              "file_name": "(1/2) The Naked Gun (2025).mp4", "date": "2026-11-19T01:00:00+00:00", "duration": 5160, "size": 1,
              "parts_count": 2, "position": 0, "watched": False, "caption": "", "poster": 0},
             {"channel": 777, "msg": 9, "kind": "video", "title": "얼굴 FHD", "file_name": "face.mp4",
              "date": "2026-11-18T01:00:00+00:00", "duration": 6180, "size": 1, "position": 0, "watched": False, "caption": "", "poster": 0}]

    def route(r):
        u = r.request.url
        if "/api/tg/tg/videos" in u:
            return r.fulfill(json={"success": True, "items": items, "total": len(items)})
        return r.continue_()
    page.route("**/api/tg/**", route)
    page.goto(server + "/#/library/tg/777")
    page.wait_for_selector(".card-title")
    titles = page.locator(".card-title").all_inner_texts()
    assert titles[:2] == ["총알 탄 사나이", "얼굴 FHD"]
    subs = page.locator(".card-sub").all_inner_texts()
    assert subs[0] == "1시간 26분 · 2개 파일" and "영화 > 최신" not in " ".join(subs)
    assert page.locator(".card-file").first.inner_text() == "2026-11-19 등록"


def test_telegram_music_album(page, server):
    """음악 채널: 앨범 카드(정사각형, 아티스트·곡 수) -> 앨범 상세(트랙 순 목록, 작곡·작사) -> 곡 재생은 오디오 화면, 다음 곡 이어서"""
    set_sources(page, server, [{"id": "tg", "type": "telegram", "name": "텔레", "api_id": "1", "api_hash": "h", "enabled": True,
                                "channels": [{"id": 777, "title": "음악", "kind": "music"}]}])
    cap = "🎵 {t}\n아티스트: 아이유\n앨범: Palette (2017)\n트랙: {n}/10\n작곡: 김제휘\n작사: 아이유\n앨범ID: abc"

    def track(msg, t, n):
        return {"channel": 777, "msg": msg, "kind": "audio", "title": t, "caption": cap.format(t=t, n=n), "file_name": f"{n:02d} {t}.flac",
                "date": "2026-11-19T01:00:00+00:00", "duration": 215, "size": 1, "position": 0, "watched": False, "poster": 0,
                "has_thumb": 1, "season": 1, "ep": n, "special": False}
    album = {**track(3, "셋째", 3), "series": {"key": "m:abc", "title": "Palette", "count": 3, "unwatched": 3,
                                                "album": {"artist": "아이유", "year": "2017", "genre": ""}}}
    eps = [track(2, "첫째", 1), track(1, "둘째", 2), track(3, "셋째", 3)]

    def route(r):
        u = r.request.url
        if "/api/tg/tg/videos" in u:
            return r.fulfill(json={"success": True, "items": [album], "total": 1})
        if "/api/tg/tg/series/777" in u:
            return r.fulfill(json={"success": True, "key": "m:abc", "title": "Palette", "channel": 777, "episodes": eps})
        if "/api/tg/tg/video/777/2" in u:
            return r.fulfill(json={"success": True, "item": {**eps[0], "series": {"key": "m:abc", "title": "Palette",
                                                                                     "next": {"msg": 1, "title": "둘째"}}}})
        if "/api/tg/tg/thumb/" in u:
            return r.fulfill(status=404, body="")
        return r.continue_()
    page.route("**/api/tg/**", route)
    page.goto(server + "/#/library/tg/777")
    page.wait_for_selector(".card-title")
    assert page.locator(".card-title").first.inner_text() == "Palette"
    assert page.locator(".card-sub").first.inner_text() == "아이유 · 3곡 · 2017"
    page.goto(server + "/#/item/tg/" + "series%3A777%3Am%3Aabc")
    page.wait_for_selector(".track-row")
    rows = page.locator(".track-row").all_inner_texts()
    assert len(rows) == 3 and "첫째" in rows[0] and "셋째" in rows[2] and "3:35" in rows[0]
    facts = page.inner_text(".detail-facts")
    assert "김제휘" in facts and "아이유" in facts
    js_modules(page)
    pb = page.evaluate("window._reg.byId('tg').getPlayback('777:2').then(p => "
                       "({audio: p.audioOnly, url: p.url, title: p.title, sub: p.subtitle, q: p.queue.map(x => x.id), i: p.queueIndex}))")
    assert pb == {"audio": True, "url": "/api/tg/tg/stream/777/2", "title": "첫째", "sub": "아이유 · Palette",
                  "q": ["777:2", "777:1", "777:3"], "i": 0}   # 다음 곡은 앨범 곡 목록에서 (트랙 순)


def test_music_queue_and_artist_view(page, server):
    """음악: 곡 목록의 다음 곡 고르기(반복 안 함·전체·한 곡, 섞어 듣기), 앨범 곡의 재생 목록, 아티스트 보기·상세"""
    set_sources(page, server, [{"id": "tg", "type": "telegram", "name": "텔레", "api_id": "1", "api_hash": "h", "enabled": True,
                                "channels": [{"id": 777, "title": "음악", "kind": "music"}]}])
    cap = "🎵 {t}\n아티스트: 아이유\n앨범: Palette (2017)\n트랙: {n}/3\n앨범ID: abc"

    def track(msg, t, n):
        return {"channel": 777, "msg": msg, "kind": "audio", "title": t, "caption": cap.format(t=t, n=n), "file_name": f"{n}.flac",
                "date": "2026-11-19T01:00:00+00:00", "duration": 200, "size": 1, "position": 0, "watched": False, "poster": 0,
                "has_thumb": 0, "season": 1, "ep": n, "special": False}
    eps = [track(1, "첫째", 1), track(2, "둘째", 2), track(3, "셋째", 3)]
    album = {**eps[2], "series": {"key": "m:abc", "title": "Palette", "count": 3, "unwatched": 3, "album": {"artist": "아이유", "year": "2017"}}}

    def route(r):
        u = r.request.url
        if "/api/tg/tg/artists/777?name=" in u:
            return r.fulfill(json={"success": True, "name": "아이유", "albums": [album], "tracks": eps})
        if "/api/tg/tg/artists/777" in u:
            return r.fulfill(json={"success": True, "items": [{"name": "아이유", "albums": 1, "tracks": 3, "cover": 0, "latest": ""}]})
        if "/api/tg/tg/series/777" in u:
            return r.fulfill(json={"success": True, "key": "m:abc", "title": "Palette", "channel": 777, "episodes": eps})
        if "/api/tg/tg/video/777/2" in u:
            return r.fulfill(json={"success": True, "item": {**eps[1], "series": {"key": "m:abc", "title": "Palette", "next": None}}})
        return r.continue_()
    page.route("**/api/tg/**", route)
    page.goto(server + "/#/home")
    page.wait_for_timeout(800)
    js_modules(page)
    # 앨범 곡 재생: 곡 목록은 앨범 (가운데 곡이 지금)
    pb = page.evaluate("window._reg.byId('tg').getPlayback('777:2').then(p => ({q: p.queue.map(x => x.id), i: p.queueIndex, next: p.getNext}))")
    assert pb == {"q": ["777:1", "777:2", "777:3"], "i": 1, "next": None}
    pick = page.evaluate("""Promise.resolve(window._pl).then((player) => {
        const q = ['a', 'b', 'c'].map(id => ({pid: 'tg', id, title: id}));
        const at = (i) => ({queue: q, queueIndex: i});
        const r = {};
        player.music = {shuffle: false, repeat: 'off', played: [], sig: ''};
        r.off = [player.musicPick(at(0), 1, true), player.musicPick(at(2), 1, true), player.musicPick(at(0), -1, false)];
        player.music.repeat = 'all'; r.all = player.musicPick(at(2), 1, true);
        player.music.repeat = 'one'; r.one = [player.musicPick(at(1), 1, true), player.musicPick(at(1), 1, false)];
        player.music = {shuffle: true, repeat: 'off', played: [], sig: ''};
        const seen = [0]; let cur = 0;
        for (let k = 0; k < 2; k++) { cur = player.musicPick(at(cur), 1, true); seen.push(cur); }
        r.shuffleAll = seen.slice().sort().join(',');
        r.shuffleEnd = player.musicPick(at(cur), 1, true);
        player.music.repeat = 'all'; r.shuffleRepeat = player.musicPick(at(cur), 1, true) !== cur;
        return r;
    })""")
    assert pick["off"] == [1, -1, 2] and pick["all"] == 0 and pick["one"] == [1, 2]
    assert pick["shuffleAll"] == "0,1,2" and pick["shuffleEnd"] == -1 and pick["shuffleRepeat"] is True
    # 곡을 틀면 재생기에 이전·다음 곡·섞어 듣기·반복 단추
    page.evaluate("window._pl.playItem(window._reg.byId('tg'), '777:2')")
    page.wait_for_selector(".player.is-music")
    assert page.locator(".player .music-only:visible").count() == 4
    page.evaluate("window._pl.stop()")
    # 아티스트 보기
    items = page.evaluate("window._reg.byId('tg').getLibraryItems('777', {kind: 'artist'}).then(r => r.items.map(i => [i.id, i.title, i.subtitle]))")
    assert items == [["artist:777:아이유", "아이유", "앨범 1 · 3곡"]]
    page.goto(server + "/#/item/tg/" + "artist%3A777%3A%EC%95%84%EC%9D%B4%EC%9C%A0")
    page.wait_for_selector(".detail h1")
    assert page.inner_text(".detail h1") == "아이유"
    assert page.locator("button:has-text('전체 재생')").count() == 1 and page.locator("button:has-text('섞어 듣기')").count() == 1
    assert "Palette" in page.inner_text(".page.detail")
