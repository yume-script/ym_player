"""화면(브라우저) 테스트 공통 준비 - 실제 YMPlayer 서버 + 가짜 외부 서버 + 헤드리스 Chromium.

- YMPlayer: `python -m ym_player`를 빈 데이터 폴더로 띄운다 (관리자 admin / adminpw).
- 가짜 외부 서버: 팟캐스트 RSS(/feed.xml), 소리 파일(/a.wav, Range 지원), 라디오처럼 끝없이 흐르는 소리(/live.wav).
  소리는 파이썬이 만든 WAV라 ffmpeg 없이 되고, 오픈소스 Chromium도 바로 재생한다.
- 브라우저: Playwright Chromium. PW_CHROMIUM(실행 파일 경로)이 있으면 그것으로.
- CDN(hls.js, Font Awesome 등): 그대로 받는다. 인터넷이 없는 곳에서는 YMP_E2E_VENDOR(파일 이름 -> 로컬 경로를 찾을 폴더)로 대신한다.
- 각 테스트가 끝날 때 화면의 JavaScript 오류(pageerror)가 없어야 한다.

YMP_E2E=1일 때만 돈다 (tests/conftest.py). CI의 e2e 작업, 또는 로컬에서:
    pip install playwright && python -m playwright install chromium
    YMP_E2E=1 python -m pytest -v tests/e2e
"""
import asyncio
import io
import math
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import wave

import pytest

import playwright.sync_api as playwright_sync  # noqa: E402  (YMP_E2E=1일 때만 이 폴더를 모은다 - tests/conftest.py)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tests"))   # (가짜 사이트 페이지는 tests/test_reading.py의 것을 같이 쓴다)
ADMIN = ("admin", "adminpw")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wav_bytes(seconds=6, freq=440, rate=8000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate)))
                               for i in range(seconds * rate)))
    return buf.getvalue()


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/DTDs/Podcast-1.0.dtd">
<channel>
  <title>E2E 팟캐스트</title>
  <itunes:author>테스트 방송국</itunes:author>
  <description>화면 테스트용</description>
  <item><title>1화 - 시작</title><guid>e1</guid><pubDate>Mon, 19 Oct 2026 09:00:00 +0900</pubDate>
    <itunes:duration>0:06</itunes:duration><enclosure url="{base}/a.wav" type="audio/wav" length="96044"/></item>
  <item><title>2화 - 다음</title><guid>e2</guid><pubDate>Mon, 26 Oct 2026 09:00:00 +0900</pubDate>
    <itunes:duration>0:06</itunes:duration><enclosure url="{base}/a.wav?2" type="audio/wav" length="96044"/></item>
</channel></rss>"""


class Upstream:
    """가짜 외부 서버 (별도 스레드의 aiohttp)"""

    def __init__(self, tmp):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.wav = os.path.join(tmp, "a.wav")
        with open(self.wav, "wb") as f:
            f.write(wav_bytes())
        self._ready = threading.Event()
        self._loop = None
        threading.Thread(target=self._run, daemon=True).start()
        if not self._ready.wait(10):
            raise RuntimeError("가짜 외부 서버가 뜨지 않았습니다")

    def _run(self):
        from aiohttp import web

        async def feed(request):
            return web.Response(text=FEED.replace("{base}", self.base), content_type="application/rss+xml")

        async def wav(request):
            return web.FileResponse(self.wav, headers={"Content-Type": "audio/wav"})

        async def live(request):   # 끝없이 흐르는 라디오 흉내 (WAV 머리 + 같은 소리 반복)
            resp = web.StreamResponse(headers={"Content-Type": "audio/wav"})
            await resp.prepare(request)
            data = wav_bytes(seconds=2)
            head, body = data[:44], data[44:]
            head = head[:4] + struct.pack("<I", 0x7FFFFFF0) + head[8:40] + struct.pack("<I", 0x7FFFFFC0)
            try:
                await resp.write(head)
                for _ in range(300):
                    await resp.write(body)
                    await asyncio.sleep(1.5)
            except (ConnectionError, asyncio.CancelledError):
                pass
            return resp

        async def m3u(request):   # IPTV 재생목록: 채팅 패널 접두어가 붙은 채널과 아닌 채널
            return web.Response(text=f"#EXTM3U\n#EXTINF:-1 group-title=\"스포츠\",[스포츠] 축구 중계\n{self.base}/live.wav\n"
                                     f"#EXTINF:-1 group-title=\"뉴스\",뉴스 채널\n{self.base}/live.wav?2\n", content_type="audio/x-mpegurl")

        async def chat(request):  # 공개 채팅 페이지 흉내
            return web.Response(text="<!doctype html><title>chat</title><p id=hello>채팅 페이지</p>", content_type="text/html")

        async def notice(request):   # 사이트 주소를 공지하는 텔레그램 공개 채널 흉내 (t.me/s/…)
            return web.Response(text=f'<a href="https://t.me/x">텔레그램</a><a href="{self.base}/today">오늘 주소</a>', content_type="text/html")

        async def site(request):     # 채팅이 붙어 있는 사이트 첫 화면
            return web.Response(text=f'<div>영상 자리</div><iframe src="{self.base}/chat.html?token=abc"></iframe>', content_type="text/html")

        async def brunch(request):   # 가짜 브런치: 연재 목록 API / 글 페이지
            from test_reading import ARTICLE_PAGE, LIST_BODY
            if request.match_info["tail"] == "api":
                return web.json_response(LIST_BODY)
            return web.Response(text=ARTICLE_PAGE, content_type="text/html")

        async def recipe10000(request):   # 가짜 만개의레시피: 랭킹·목록 / 레시피 페이지
            from test_reading import RANK_PAGE, RECIPE_PAGE
            tail = request.match_info["tail"]
            return web.Response(text=RECIPE_PAGE if tail.startswith("recipe/") and tail[7:].isdigit() else RANK_PAGE,
                                content_type="text/html")

        async def main():
            app = web.Application()
            app.router.add_get("/brunch/{tail:.*}", brunch)
            app.router.add_get("/recipe10000/{tail:.*}", recipe10000)
            app.router.add_get("/feed.xml", feed)
            app.router.add_get("/a.wav", wav)
            app.router.add_get("/live.wav", live)
            app.router.add_get("/list.m3u", m3u)
            app.router.add_get("/chat.html", chat)
            app.router.add_get("/notice", notice)
            app.router.add_get("/", site)
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", self.port).start()
            self._ready.set()
            await asyncio.Event().wait()

        self._loop = asyncio.new_event_loop()
        try:
            self._loop.run_until_complete(main())
        except RuntimeError:
            pass


@pytest.fixture(scope="session")
def upstream(tmp_path_factory):
    return Upstream(str(tmp_path_factory.mktemp("upstream")))


@pytest.fixture(scope="session")
def server(tmp_path_factory, upstream):
    """실제 YMPlayer 서버 (빈 데이터 폴더). 반환: 주소 (http://127.0.0.1:포트)"""
    data = tmp_path_factory.mktemp("ymp_data")
    port = free_port()
    env = {**os.environ, "MP_HOST": "127.0.0.1", "MP_PORT": str(port), "MP_DATA_DIR": str(data),
           "MP_USER": ADMIN[0], "MP_PASSWORD": ADMIN[1], "MP_UPDATE_CHECK": "0", "PYTHONUNBUFFERED": "1",
           "MP_CHAT_ALLOW_PRIVATE": "1",   # (채팅 주소 찾기가 이 컴퓨터의 가짜 서버를 열 수 있게)
           "MP_TEST_BRUNCH": f"{upstream.base}/brunch", "MP_TEST_RECIPE": f"{upstream.base}/recipe10000"}
    log = open(os.path.join(str(data), "server.log"), "w")
    proc = subprocess.Popen([sys.executable, "-m", "ym_player"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"YMPlayer가 바로 꺼졌습니다:\n{open(log.name).read()}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("YMPlayer가 30초 안에 뜨지 않았습니다")
    yield base
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()


def _vendor_route(vendor):
    """인터넷이 없는 곳: CDN 파일을 YMP_E2E_VENDOR 아래에서 이름으로 찾아 준다 (없으면 끊음)"""
    files = {}
    for dirpath, _dirs, names in os.walk(vendor):
        built = os.sep + "dist" in dirpath or os.sep + "build" in dirpath or os.sep + "css" in dirpath or "webfonts" in dirpath
        for n in names:
            if built or n not in files:   # 같은 이름이면 배포용(dist/build) 파일을 (src/의 모듈 원본 말고)
                files[n] = os.path.join(dirpath, n)

    def handler(route):
        name = route.request.url.split("?")[0].rsplit("/", 1)[-1]
        if name == "mpegts.min.js" and "mpegts.js" in files:
            name = "mpegts.js"
        path = files.get(name)
        if not path:
            return route.abort()
        ct = "text/css" if name.endswith(".css") else "application/javascript" if name.endswith((".js", ".mjs")) else None
        return route.fulfill(path=path, content_type=ct, headers={"Access-Control-Allow-Origin": "*"})
    return handler


@pytest.fixture(scope="session")
def browser():
    with playwright_sync.sync_playwright() as p:
        exe = os.environ.get("PW_CHROMIUM") or None
        b = p.chromium.launch(executable_path=exe, args=["--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


def new_context(browser, **kw):
    """브라우저 창 (인터넷이 없는 곳이면 CDN을 YMP_E2E_VENDOR에서)"""
    ctx = browser.new_context(**{"viewport": {"width": 1280, "height": 800}, **kw})
    vendor = os.environ.get("YMP_E2E_VENDOR")
    if vendor:
        handler = _vendor_route(vendor)
        for host in ("https://cdn.jsdelivr.net/**", "https://cdnjs.cloudflare.com/**"):
            ctx.route(host, handler)
        ctx.route("https://fonts.googleapis.com/**", lambda r: r.abort())
    return ctx


@pytest.fixture
def page(browser, server):
    """로그인한 관리자 화면. 테스트가 끝날 때 JavaScript 오류가 없어야 한다"""
    ctx = new_context(browser)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(server + "/login")
    r = pg.request.post(server + "/api/auth/login", data={"username": ADMIN[0], "password": ADMIN[1]})
    assert r.ok, r.text()
    pg.errors = errors
    yield pg
    ctx.close()
    assert not errors, f"화면 JavaScript 오류: {errors}"


def set_sources(page, server, sources):
    """설정 > 소스를 API로 바꾼다 (화면 테스트의 준비 단계)"""
    import json
    r = page.request.put(server + "/api/config", data=json.dumps({"sources": sources}), headers={"Content-Type": "application/json"})
    assert r.ok, r.text()
    return r.json()


def js_modules(page):
    """화면의 player / registry 모듈을 window._pl / window._reg로 꺼내 둔다"""
    page.evaluate("""(async () => {
        const base = [...document.scripts].map(s => s.src).find(s => s.includes('/js/app.js')).replace('app.js', '');
        window._pl = (await import(base + 'player.js')).player;
        window._reg = (await import(base + 'providers/index.js')).registry;
    })()""")


def player_state(page):
    return page.evaluate("""(() => { const v = document.querySelector('.player video');
        return {t: v.currentTime, paused: v.paused, src: (v.currentSrc || '').replace(location.origin, ''),
                engine: (document.querySelector('.engine-badge') || {}).textContent || '',
                title: (document.querySelector('.player-title') || {}).textContent || ''}; })()""")


def wait_playing(page, timeout=15):
    """재생이 시작되어 시간이 흐를 때까지"""
    deadline = time.time() + timeout
    st = None
    while time.time() < deadline:
        st = player_state(page)
        if st["t"] > 0.5 and not st["paused"]:
            return st
        page.wait_for_timeout(300)
    raise AssertionError(f"재생이 시작되지 않았습니다: {st}")
