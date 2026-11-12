"""BookOasis 소스: 설정 정리, 이어 줄 주소, 로그인해서 중계 (가짜 BookOasis 서버)"""
import asyncio

import pytest
from aiohttp import web

from ym_player import auth, bookoasis, config_store


def test_settings_and_rules():
    with pytest.raises(ValueError):
        bookoasis.clean_settings({"url": "ftp://x", "username": "u"}, None)
    with pytest.raises(ValueError):
        bookoasis.clean_settings({"url": "http://bo.example", "username": " "}, None)
    s = bookoasis.clean_settings({"url": "http://bo.example:5050/ ", "username": "ymp", "password": "pw"}, None)
    assert s["url"] == "http://bo.example:5050" and s["sessions"] == ["general", "audiobook", "video"]
    kept = bookoasis.clean_settings({"url": "http://bo.example", "username": "ymp", "password": "", "sessions": ["video", "adult", "x"]}, s)
    assert kept["password"] == "pw" and kept["sessions"] == ["video", "adult"]

    assert bookoasis.allowed("api/media/list") and bookoasis.allowed("api/media/videos/3/episodes/9/stream")
    assert bookoasis.allowed("api/media/videos/3/episodes/9/hls-segment/seg_001.ts") and bookoasis.allowed("covers/가 나(1).webp")
    for bad in ("api/admin/users", "api/media/detail/edit", "api/media/books/3/delete", "logout", "api/media/progress", "covers/a?x=1"):
        assert not bookoasis.allowed(bad), bad
    assert bookoasis.session_of("api/media/list", "type=adult&page=1") == "adult"
    assert bookoasis.session_of("api/media/stream", "db_type=adult&book_id=1") == "adult"
    assert bookoasis.session_of("api/media/list", "") == "general" and bookoasis.session_of("covers/a.jpg", "") == ""
    assert bookoasis.session_of("api/media/videos/1/cover", "") == "video"
    assert bookoasis.rewrite_playlist("#EXTM3U\n/api/media/videos/1/episodes/2/hls-segment/a.ts\n", "bo") == \
        "#EXTM3U\n/api/bo/bo/api/media/videos/1/episodes/2/hls-segment/a.ts\n"

    assert bookoasis.progress_request({"kind": "book", "book_id": "7", "page_idx": 3, "total_pages": 10}) == (
        "api/media/progress", {"db_type": "general", "book_id": 7, "page_idx": 3, "total_pages": 10, "flush_immediately": False}, "general")
    tail, body, sess = bookoasis.progress_request({"kind": "video", "id": 4, "episode": 9, "time": 12.5, "done": True})
    assert tail == "api/media/videos/4/progress" and body["current_episode_id"] == 9 and body["is_completed"] and sess == "video"
    assert bookoasis.progress_request({"kind": "audio", "id": 2, "track": 5})[0] == "api/media/audiobooks/2/progress"
    for bad in ({"kind": "book"}, {"kind": "x"}, {"kind": "video", "id": "../1", "episode": 1}, {"kind": "book", "book_id": 1, "db_type": "video"}):
        with pytest.raises(ValueError):
            bookoasis.progress_request(bad)


def test_config_hides_password(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    src = config_store._clean_source({"id": "bo", "type": "bookoasis", "url": "http://bo.example", "username": "ymp", "password": "pw"}, None)
    assert src["name"] == "BookOasis" and src["password"] == "pw"
    cfg = config_store.load()
    cfg["sources"] = [src]
    pub = config_store.public_config(cfg)["sources"][0]
    assert "password" not in pub and pub["has_password"] and pub["username"] == "ymp"
    with pytest.raises(ValueError):
        config_store._clean_source({"id": "bo2", "type": "bookoasis", "url": "", "username": "u"}, None)


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


def test_bookoasis_proxy(data):
    from aiohttp import CookieJar
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server
    video = bytes(range(256)) * 8
    state = {"logins": 0, "valid": set(), "progress": [], "ua": []}

    def authed(request):
        return request.cookies.get("session") in state["valid"]

    async def login(request):
        body = await request.json()
        if body.get("username") != "ymp" or body.get("password") != "pw":
            return web.json_response({"success": False, "error": "아이디 또는 비밀번호가 틀립니다"}, status=401)
        state["logins"] += 1
        sid = f"s{state['logins']}"
        state["valid"] = {sid}
        resp = web.json_response({"success": True, "role": "user", "is_default_password": 0})
        resp.set_cookie("session", sid)
        return resp

    def need(handler):
        async def wrapped(request):
            if not authed(request):
                return web.json_response({"success": False}, status=401)
            return await handler(request)
        return wrapped

    async def libraries(request):
        return web.json_response({"success": True, "libraries": [{"id": 1, "name": "만화"}], "groups": [], "kinds": []})

    async def listing(request):
        return web.json_response({"success": True, "series": [{"series_key": "k", "series_name": f"작품-{request.query.get('type')}"}], "has_more": False})

    async def stream(request):
        state["ua"].append(request.headers.get("User-Agent", ""))
        if "Safari" in request.headers.get("User-Agent", ""):
            return web.Response(text="#EXTM3U\n/api/media/videos/1/episodes/2/hls-segment/a.ts\n", content_type="application/vnd.apple.mpegurl")
        rng = request.headers.get("Range", "")
        if rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            a, b = int(a), int(b or len(video) - 1)
            return web.Response(status=206, body=video[a:b + 1], headers={"Content-Range": f"bytes {a}-{b}/{len(video)}", "Accept-Ranges": "bytes", "Content-Type": "video/mp4"})
        return web.Response(body=video, content_type="video/mp4")

    async def cover(request):
        return web.Response(body=b"img:" + request.match_info["name"].encode(), content_type="image/webp")

    async def progress(request):
        state["progress"].append((request.path, await request.json()))
        return web.json_response({"success": True})

    async def main():
        up = web.Application()
        up.router.add_post("/login", login)
        up.router.add_get("/api/media/libraries", need(libraries))
        up.router.add_get("/api/media/list", need(listing))
        up.router.add_get("/api/media/videos/1/episodes/2/stream", need(stream))
        up.router.add_get("/covers/{name}", cover)
        up.router.add_post("/api/media/progress", need(progress))
        up.router.add_post("/api/media/videos/1/progress", need(progress))
        upstream = TestServer(up)
        await upstream.start_server()
        base = f"http://127.0.0.1:{upstream.port}"
        cfg = config_store.load()
        cfg["sources"] = [config_store._clean_source({"id": "bo", "type": "bookoasis", "url": base, "username": "ymp", "password": "pw",
                                                      "sessions": ["general", "video"]}, None)]
        config_store.save(cfg)
        c = TestClient(TestServer(server.create_app(require_auth=True)), cookie_jar=CookieJar(unsafe=True))
        await c.start_server()
        try:
            assert (await c.get("/api/bo/bo/api/media/list?type=general")).status in (401, 302, 403)   # YMPlayer 로그인 전
            await c.post("/api/auth/login", json={"username": "boss", "password": "secret1"})
            pub = (await (await c.get("/api/config")).json())["sources"][0]
            assert pub["type"] == "bookoasis" and "password" not in pub and pub["has_password"]

            d = await (await c.get("/api/bo/bo/api/media/list?type=general&page=1")).json()
            assert d["series"][0]["series_name"] == "작품-general" and state["logins"] == 1
            await c.get("/api/bo/bo/api/media/list?type=video")
            assert state["logins"] == 1                                              # 로그인은 한 번
            state["valid"] = set()                                                   # BookOasis 세션이 끊기면 다시 로그인
            assert (await c.get("/api/bo/bo/api/media/list?type=general")).status == 200 and state["logins"] == 2

            assert (await c.get("/api/bo/bo/api/media/list?type=adult")).status == 403        # 고르지 않은 서재
            assert (await c.get("/api/bo/bo/api/media/list?type=audiobook")).status == 403
            assert (await c.get("/api/bo/bo/api/admin/users")).status == 404                  # 이어 주지 않는 주소
            assert (await c.get("/api/bo/nope/api/media/list")).status == 404

            r = await c.get("/api/bo/bo/api/media/videos/1/episodes/2/stream", headers={"Range": "bytes=5-14"})
            assert r.status == 206 and await r.read() == video[5:15] and r.headers["Content-Range"] == f"bytes 5-14/{len(video)}"
            r = await c.get("/api/bo/bo/api/media/videos/1/episodes/2/stream?ua=safari")
            assert "mpegurl" in r.headers["Content-Type"] and "/api/bo/bo/api/media/videos/1/episodes/2/hls-segment/a.ts" in await r.text()
            assert "Safari" in state["ua"][-1] and "Safari" not in state["ua"][0]
            r = await c.get("/api/bo/bo/covers/%EA%B0%80%20(1).webp")
            assert await r.read() == "img:가 (1).webp".encode() and "max-age" in r.headers["Cache-Control"]

            assert (await (await c.post("/api/bo/bo/progress", json={"kind": "book", "book_id": 7, "page_idx": 3, "total_pages": 10})).json())["success"]
            assert (await (await c.post("/api/bo/bo/progress", json={"kind": "video", "id": 1, "episode": 2, "time": 30})).json())["success"]
            assert state["progress"][0] == ("/api/media/progress", {"db_type": "general", "book_id": 7, "page_idx": 3, "total_pages": 10, "flush_immediately": False})
            assert state["progress"][1][0] == "/api/media/videos/1/progress" and state["progress"][1][1]["current_time"] == 30
            assert (await c.post("/api/bo/bo/progress", json={"kind": "audio", "id": 1, "track": 2})).status == 403   # 오디오북은 안 고름
            assert (await c.post("/api/bo/bo/progress", json={"kind": "nope"})).status == 400

            # 연결 확인 (비밀번호를 비우면 저장된 것, 틀리면 알려 준다)
            t = await (await c.post("/api/admin/bookoasis/test", json={"id": "bo", "url": base, "username": "ymp", "password": "", "sessions": ["general"]})).json()
            assert t["success"] and t["libraries"] == {"general": 1}
            r = await c.post("/api/admin/bookoasis/test", json={"url": base, "username": "ymp", "password": "wrong"})
            assert r.status == 400 and "로그인 실패" in (await r.json())["error"]
        finally:
            await c.close()
            await upstream.close()

    asyncio.run(main())
