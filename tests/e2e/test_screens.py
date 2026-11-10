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
    """자동 올리기: 규칙을 "rclone 드라이브 · 폴더"로 바꾸면 폴더 칸과 720p 줄이기 칸이 보이고 Plex 칸은 숨는다"""
    page.goto(server + "/#/settings")
    page.wait_for_selector(".src-tabs")
    page.click(".tabs:not(.src-tabs) > .tab:has-text('자동 올리기')")
    page.click("button:has-text('규칙 추가')")
    rule = page.locator(".mirror-rule").last
    assert rule.locator(".mirror-folder").is_hidden() and rule.locator(".mirror-enc").is_hidden()
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
