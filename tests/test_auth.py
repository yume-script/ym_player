"""로그인/다중 사용자 - 첫 관리자 만들기, 로그인·쿠키, 관리자만 되는 것, 일반 사용자 제한."""
import asyncio

import pytest

from ym_player import auth, config_store

pytest.importorskip("aiohttp")


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MP_USER", raising=False)
    monkeypatch.delenv("MP_PASSWORD", raising=False)
    auth._fails.clear()
    return tmp_path


def run(flow, require_auth=True):
    from aiohttp import CookieJar
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server

    async def main():
        app = server.create_app(require_auth=require_auth)
        # 테스트 서버는 127.0.0.1(IP 주소)이라 기본 쿠키 저장소가 쿠키를 버린다 -> unsafe=True
        client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
        await client.start_server()
        try:
            await flow(client, app)
        finally:
            await client.close()
    asyncio.run(main())


def test_password_store_and_token(data):
    u = auth.create_user("yume", "secret1", "admin")
    assert u == {"username": "yume", "role": "admin", "access": None}
    raw = auth.load_users()[0]
    assert "secret1" not in str(raw) and len(raw["hash"]) == 64  # 해시만 저장
    assert auth.verify_password("YUME", "secret1")["username"] == "yume"  # 아이디는 대소문자 무시
    assert auth.verify_password("yume", "wrong") is None and auth.verify_password("nobody", "x") is None

    token = auth.make_token(raw)
    assert auth.user_from_token(token)["username"] == "yume"
    assert auth.user_from_token(token[:-2] + "xx") is None          # 서명 위조
    assert auth.user_from_token("garbage") is None
    auth.update_user("yume", password="newpass")
    assert auth.user_from_token(token) is None                      # 비밀번호 바꾸면 기존 쿠키 무효

    with pytest.raises(auth.AuthError):
        auth.create_user("yume", "another", "user")                 # 중복
    with pytest.raises(auth.AuthError):
        auth.create_user("x", "secret1")                            # 아이디 너무 짧음
    with pytest.raises(auth.AuthError):
        auth.create_user("kim", "123")                              # 비밀번호 너무 짧음
    with pytest.raises(auth.AuthError):
        auth.update_user("yume", role="user")                       # 마지막 관리자
    with pytest.raises(auth.AuthError):
        auth.delete_user("yume")


def test_env_bootstrap(data, monkeypatch):
    monkeypatch.setenv("MP_USER", "boss")
    monkeypatch.setenv("MP_PASSWORD", "bosspass")
    assert auth.bootstrap_from_env() == {"username": "boss", "role": "admin", "access": None}
    assert auth.bootstrap_from_env() is None  # 이미 사용자가 있으면 아무것도 안 함


def test_setup_login_and_permissions(data):
    async def flow(c, app):
        # 로그인 전: 화면은 /login으로, API는 401, 로그인 화면/정적 파일은 열림
        r = await c.get("/", allow_redirects=False)
        assert r.status == 302 and r.headers["Location"] == "/login"
        assert (await c.get("/api/config")).status == 401
        assert (await c.get("/login")).status == 200
        assert (await c.get("/static/js/login.js")).status == 200
        st = await (await c.get("/api/auth/state")).json()
        assert st["setup"] is True and st["user"] is None

        # 첫 관리자 만들기 -> 바로 로그인됨. 두 번째 setup은 막힘
        r = await c.post("/api/auth/setup", json={"username": "admin", "password": "adminpw", "remember": True})
        assert r.status == 200 and auth.COOKIE in r.cookies
        assert "HttpOnly" in r.headers["Set-Cookie"] and "SameSite=Lax" in r.headers["Set-Cookie"]
        assert (await c.post("/api/auth/setup", json={"username": "evil", "password": "evilpw"})).status == 403
        me = await (await c.get("/api/auth/me")).json()
        assert me["user"] == {"username": "admin", "role": "admin", "access": None}

        # 관리자: 소스 저장, 사용자 추가
        r = await c.put("/api/config", json={"sources": [
            {"id": "tv", "type": "iptv", "name": "개인", "m3u": "http://nas/list.m3u?apikey=KEY", "epg": "http://nas/epg.xml"},
            {"id": "px", "type": "plex", "name": "집", "url": "http://plex:32400", "token": "TOK", "hidden_libraries": ["3"]},
            {"id": "tg", "type": "telegram", "name": "텔레", "api_id": "111", "api_hash": "H", "channels": [{"id": 7, "title": "채널"}]},
        ]})
        assert r.status == 200
        r = await c.post("/api/users", json={"username": "kim", "password": "kimpass", "role": "user"})
        assert r.status == 200
        users = (await (await c.get("/api/users")).json())["users"]
        assert {u["username"]: u["role"] for u in users} == {"admin": "admin", "kim": "user"}

        # 로그아웃 -> 401
        await c.post("/api/auth/logout")
        c.session.cookie_jar.clear()
        assert (await c.get("/api/auth/me")).status == 401

        # 일반 사용자로 로그인 (틀린 비밀번호 먼저)
        assert (await c.post("/api/auth/login", json={"username": "kim", "password": "nope"})).status == 401
        r = await c.post("/api/auth/login", json={"username": "kim", "password": "kimpass"})
        assert r.status == 200 and "Max-Age" not in r.headers["Set-Cookie"]  # 로그인 유지 안 하면 세션 쿠키

        # 설정은 볼 수 있는 것만: 주소/키/API ID 없음
        cfg = await (await c.get("/api/config")).json()
        text = str(cfg)
        for secret in ("KEY", "nas", "plex:32400", "111", "TOK"):
            assert secret not in text
        by_id = {s["id"]: s for s in cfg["sources"]}
        assert by_id["tv"]["has_epg"] is True and by_id["px"]["hidden_libraries"] == ["3"]
        assert by_id["tg"]["channels"] == [{"id": 7, "title": "채널"}]

        # 관리자 전용은 전부 403
        assert (await c.put("/api/config", json={"sources": []})).status == 403
        assert (await c.get("/api/users")).status == 403
        assert (await c.post("/api/users", json={"username": "lee", "password": "leepass"})).status == 403
        assert (await c.delete("/api/users/admin")).status == 403
        assert (await c.post("/api/plex-auth/pin")).status == 403
        assert (await c.get("/api/tg/tg/status")).status == 403
        assert (await c.post("/api/tg/tg/code", json={})).status == 403
        assert (await c.get("/api/tg/tg/channels")).status == 403
        # Plex 중계: 일반 사용자는 읽기와 자막 선택 저장만
        assert (await c.delete("/api/plex/px/library/metadata/1")).status == 403
        assert (await c.post("/api/plex/px/library/sections/1/refresh")).status == 403
        assert (await c.put("/api/plex/px/:/prefs")).status == 403

        # 내 비밀번호 변경
        assert (await c.post("/api/auth/password", json={"current": "bad", "new": "kimpass2"})).status == 400
        r = await c.post("/api/auth/password", json={"current": "kimpass", "new": "kimpass2"})
        assert r.status == 200
        assert (await c.get("/api/auth/me")).status == 200  # 이 기기는 새 쿠키로 계속 로그인

    run(flow)


def test_admin_can_manage_users_and_stale_sessions_die(data):
    async def flow(c, app):
        await c.post("/api/auth/setup", json={"username": "admin", "password": "adminpw"})
        await c.post("/api/users", json={"username": "kim", "password": "kimpass"})
        kim = auth.find_user("kim")
        kim_cookie = auth.make_token(kim)

        assert (await c.delete("/api/users/admin")).status == 400       # 자기 자신
        assert (await c.put("/api/users/admin", json={"role": "user"})).status == 400  # 마지막 관리자
        r = await c.put("/api/users/kim", json={"role": "admin"})
        assert (await r.json())["user"]["role"] == "admin"
        await c.put("/api/users/kim", json={"password": "reset99"})     # 관리자가 비밀번호 초기화
        assert auth.user_from_token(kim_cookie) is None                 # kim의 기존 로그인은 끊김
        await c.delete("/api/users/kim")
        assert auth.find_user("kim") is None

    run(flow)


def test_login_throttle(data):
    async def flow(c, app):
        auth.create_user("admin", "adminpw", "admin")
        for _ in range(auth.FAIL_LIMIT):
            await c.post("/api/auth/login", json={"username": "admin", "password": "x"})
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        assert r.status == 429  # 맞는 비밀번호여도 잠시 막힘

    auth.FAIL_LIMIT, old = 3, auth.FAIL_LIMIT
    try:
        run(flow)
    finally:
        auth.FAIL_LIMIT = old


def test_access_clean_and_checks():
    assert auth.clean_access(None) is None and auth.clean_access({"sources": "x"}) is None
    acc = auth.clean_access({"sources": ["px", "tv", ""], "libraries": {"px": [2, "1", "1"], "gone": ["9"]}})
    assert acc == {"sources": ["px", "tv"], "libraries": {"px": ["1", "2"]}}
    kid = {"role": "user", "access": acc}
    assert auth.can_source(kid, "px") and not auth.can_source(kid, "tg")
    assert auth.allowed_libraries(kid, "px") == {"1", "2"} and auth.allowed_libraries(kid, "tv") is None
    admin = {"role": "admin", "access": acc}
    assert auth.can_source(admin, "tg") and auth.allowed_libraries(admin, "px") is None


def _fake_plex():
    """섹션 1(만화), 2(성인) 두 라이브러리를 가진 가짜 Plex."""
    from aiohttp import web

    def meta(rk, sec, **kw):
        return {"ratingKey": str(rk), "librarySectionID": sec, "title": f"t{rk}", **kw}

    items = {
        "10": meta(10, 1, Media=[{"Part": [{"id": 100, "key": "/library/parts/100/1/a.mp4",
                                           "Stream": [{"id": 1000, "streamType": 3}]}]}]),
        "20": meta(20, 2, Media=[{"Part": [{"id": 200, "key": "/library/parts/200/1/b.mp4"}]}]),
    }

    async def handle(request):
        path = request.path
        if path == "/library/sections":
            return web.json_response({"MediaContainer": {"Directory": [
                {"key": "1", "type": "movie", "title": "만화"}, {"key": "2", "type": "movie", "title": "성인"}]}})
        if path == "/hubs":
            return web.json_response({"MediaContainer": {"Hub": [
                {"title": "최근", "Metadata": [items["10"], items["20"]]},
                {"title": "성인만", "Metadata": [items["20"]]}]}})
        if path.startswith("/library/metadata/"):
            rk = path.split("/")[3]
            if rk not in items:
                return web.json_response({}, status=404)
            if path.endswith("/thumb/1"):
                return web.Response(body=b"IMG", content_type="image/jpeg")
            return web.json_response({"MediaContainer": {"Metadata": [items[rk]]}})
        if path.startswith("/library/parts/") or path.startswith("/library/streams/") or path.startswith("/photo/"):
            return web.Response(body=b"DATA", content_type="application/octet-stream")
        if path.startswith("/library/sections/"):
            return web.json_response({"MediaContainer": {"librarySectionID": int(path.split("/")[3]), "Metadata": []}})
        if path.startswith("/video/:/transcode/universal/") or path == "/:/timeline":
            return web.Response(text="ok")
        return web.Response(status=404)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    return app


def test_user_access_limits_sources_and_plex_libraries(data):
    from aiohttp.test_utils import TestServer

    async def flow(c, app):
        origin = TestServer(_fake_plex())
        await origin.start_server()
        try:
            await c.post("/api/auth/setup", json={"username": "admin", "password": "adminpw"})
            await c.put("/api/config", json={"sources": [
                {"id": "px", "type": "plex", "name": "집", "url": str(origin.make_url("")).rstrip("/"), "token": "T"},
                {"id": "tv", "type": "iptv", "name": "TV", "m3u": "http://127.0.0.1:9/x.m3u"},
                {"id": "tg", "type": "telegram", "name": "텔레", "api_id": "1", "api_hash": "H"},
            ]})
            await c.post("/api/users", json={"username": "kid", "password": "kidpass"})
            r = await c.put("/api/users/kid", json={"access": {"sources": ["px"], "libraries": {"px": ["1"]}}})
            assert (await r.json())["user"]["access"] == {"sources": ["px"], "libraries": {"px": ["1"]}}
            # 비밀번호만 바꾸는 요청은 access를 건드리지 않는다
            await c.put("/api/users/kid", json={"password": "kidpass"})
            assert auth.find_user("kid")["access"]["sources"] == ["px"]

            await c.post("/api/auth/logout")
            c.session.cookie_jar.clear()
            await c.post("/api/auth/login", json={"username": "kid", "password": "kidpass"})

            cfg = await (await c.get("/api/config")).json()
            assert [s["id"] for s in cfg["sources"]] == ["px"]
            assert cfg["sources"][0]["allowed_libraries"] == ["1"]
            assert (await c.get("/api/iptv/tv/channels")).status == 404
            assert (await c.get("/api/tg/tg/videos")).status == 404

            j = await (await c.get("/api/plex/px/library/sections")).json()
            assert [d["key"] for d in j["MediaContainer"]["Directory"]] == ["1"]
            j = await (await c.get("/api/plex/px/hubs")).json()
            hubs = j["MediaContainer"]["Hub"]
            assert [h["title"] for h in hubs] == ["최근"] and [m["ratingKey"] for m in hubs[0]["Metadata"]] == ["10"]

            assert (await c.get("/api/plex/px/library/sections/1/all")).status == 200
            assert (await c.get("/api/plex/px/library/sections/2/all")).status == 403
            assert (await c.get("/api/plex/px/hubs/sections/2")).status == 403
            assert (await c.get("/api/plex/px/library/metadata/10")).status == 200
            assert (await c.get("/api/plex/px/library/metadata/20")).status == 403
            assert (await c.get("/api/plex/px/library/metadata/20/thumb/1")).status == 403
            assert (await c.get("/api/plex/px/photo/:/transcode", params={"url": "/library/metadata/10/thumb/1"})).status == 200
            assert (await c.get("/api/plex/px/photo/:/transcode", params={"url": "/library/metadata/20/thumb/1"})).status == 403
            # 원본 파일/자막은 보여준 항목의 것만
            assert (await c.get("/api/plex/px/library/parts/100/1/a.mp4")).status == 200
            assert (await c.get("/api/plex/px/library/parts/200/1/b.mp4")).status == 403
            assert (await c.get("/api/plex-sub/px", params={"path": "/library/streams/1000"})).status == 200
            assert (await c.get("/api/plex-sub/px", params={"path": "/library/streams/1001"})).status == 403
            tc = "/api/plex/px/video/:/transcode/universal/start.m3u8"
            assert (await c.get(tc, params={"path": "/library/metadata/10"})).status == 200
            assert (await c.get(tc, params={"path": "/library/metadata/20"})).status == 403
            assert (await c.get("/api/plex/px/:/timeline", params={"ratingKey": "20", "key": "/library/metadata/20"})).status == 403
            assert (await c.get("/api/plex/px/:/timeline", params={"ratingKey": "10", "key": "/library/metadata/10"})).status == 200
            # 정해 둔 길 밖은 막힘
            assert (await c.get("/api/plex/px/library/all")).status == 403
            assert (await c.get("/api/plex/px/status/sessions")).status == 403
        finally:
            await origin.close()

    run(flow)


def test_prefs_are_per_user(data):
    async def flow(c, app):
        await c.post("/api/auth/setup", json={"username": "admin", "password": "adminpw"})
        await c.post("/api/users", json={"username": "kim", "password": "kimpass"})
        assert (await (await c.get("/api/me/prefs")).json())["prefs"] == {}
        r = await c.put("/api/me/prefs", json={"fav_channels": ["tv:1", "tv:2", "tv:1"]})
        assert (await r.json())["prefs"] == {"fav_channels": ["tv:1", "tv:2"]}
        assert (await c.put("/api/me/prefs", json={"evil": 1})).status == 400
        assert (await c.put("/api/me/prefs", json={"fav_channels": "x"})).status == 400

        await c.post("/api/auth/logout")
        c.session.cookie_jar.clear()
        await c.post("/api/auth/login", json={"username": "kim", "password": "kimpass"})
        assert (await (await c.get("/api/me/prefs")).json())["prefs"] == {}  # 남의 즐겨찾기는 안 보임
        await c.put("/api/me/prefs", json={"fav_channels": ["tv:9"]})
        from ym_player import prefs
        assert prefs.get("ADMIN")["fav_channels"] == ["tv:1", "tv:2"]
        assert prefs.get("kim")["fav_channels"] == ["tv:9"]

    run(flow)


def test_backup_restore_and_status(data):
    from aiohttp.test_utils import TestServer

    async def flow(c, app):
        origin = TestServer(_fake_plex())
        await origin.start_server()
        try:
            await c.post("/api/auth/setup", json={"username": "admin", "password": "adminpw"})
            await c.post("/api/users", json={"username": "kim", "password": "kimpass"})
            await c.put("/api/config", json={"sources": [
                {"id": "px", "type": "plex", "name": "집", "url": str(origin.make_url("")).rstrip("/"), "token": "T"},
                {"id": "tv", "type": "iptv", "name": "TV", "m3u": "http://127.0.0.1:9/x.m3u"},
            ]})
            await c.put("/api/me/prefs", json={"fav_channels": ["tv:1"]})

            st = await (await c.get("/api/admin/status")).json()
            by_id = {s["id"]: s for s in st["sources"]}
            assert by_id["px"]["ok"] is False  # 가짜 Plex에는 /identity가 없다
            assert by_id["tv"]["ok"] is False and st["server"]["users"] == 2

            r = await c.get("/api/admin/backup")
            assert r.status == 200 and "attachment" in r.headers["Content-Disposition"]
            backup = await r.json()
            assert backup["format"] == "ym_player-backup" and backup["config"]["sources"][0]["token"] == "T"
            assert {u["username"] for u in backup["users"]} == {"admin", "kim"}

            # 바꿔 놓고 복원하면 돌아온다
            await c.put("/api/config", json={"sources": []})
            await c.delete("/api/users/kim")
            assert (await c.post("/api/admin/restore", json={"format": "nope"})).status == 400
            bad = {**backup, "users": [{**u, "role": "user"} for u in backup["users"]]}
            assert (await c.post("/api/admin/restore", json=bad)).status == 400  # 관리자 없음
            r = await c.post("/api/admin/restore", json=backup)
            res = await r.json()
            assert r.status == 200 and res["still_logged_in"] and res["users"] == 2
            assert config_store.get_source(config_store.load(), "px")["token"] == "T"
            assert auth.verify_password("kim", "kimpass")
            assert (await (await c.get("/api/me/prefs")).json())["prefs"]["fav_channels"] == ["tv:1"]

            # 일반 사용자는 못 함
            await c.post("/api/auth/logout")
            c.session.cookie_jar.clear()
            await c.post("/api/auth/login", json={"username": "kim", "password": "kimpass"})
            for path in ("/api/admin/status", "/api/admin/backup"):
                assert (await c.get(path)).status == 403
            assert (await c.post("/api/admin/restore", json=backup)).status == 403
        finally:
            await origin.close()

    run(flow)


def test_branding_title_and_icon(data):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

    async def flow(c, app):
        # 로그인 전에도 이름/아이콘은 보인다 (로그인 화면이 씀)
        b = await (await c.get("/api/branding")).json()
        assert b["title"] == "Yume Player" and b["icon_url"] is None
        assert "<title>로그인 · Yume Player</title>" in await (await c.get("/login")).text()
        assert (await c.get("/brand/icon")).status == 404

        await c.post("/api/auth/setup", json={"username": "admin", "password": "adminpw"})
        r = await c.put("/api/admin/branding", json={"title": "우리집 <극장>"})
        assert (await r.json())["title"] == "우리집 <극장>"
        assert (await c.put("/api/admin/branding", json={"title": "x" * 41})).status == 400
        page = await (await c.get("/")).text()
        assert "<title>우리집 &lt;극장&gt;</title>" in page and '<span class="brand-name">우리집 &lt;극장&gt;</span>' in page
        assert "__BRAND" not in page

        assert (await c.put("/api/admin/branding/icon", data=b"<svg onload=alert(1)>")).status == 400
        assert (await c.put("/api/admin/branding/icon", data=b"x" * (600 * 1024))).status == 400
        r = await c.put("/api/admin/branding/icon", data=png)
        icon = (await r.json())["icon_url"]
        assert icon.startswith("/brand/icon?v=")
        r = await c.get("/brand/icon")
        assert r.status == 200 and r.content_type == "image/png" and await r.read() == png
        page = await (await c.get("/")).text()
        assert 'class="brand-mark has-img"' in page and f'<link rel="icon" href="{icon}">' in page

        # 일반 사용자는 못 바꾼다
        await c.post("/api/users", json={"username": "kim", "password": "kimpass"})
        await c.post("/api/auth/logout")
        c.session.cookie_jar.clear()
        await c.post("/api/auth/login", json={"username": "kim", "password": "kimpass"})
        assert (await c.put("/api/admin/branding", json={"title": "해킹"})).status == 403
        assert (await c.delete("/api/admin/branding/icon")).status == 403
        assert (await (await c.get("/api/branding")).json())["title"] == "우리집 <극장>"

        # 백업에 이름과 아이콘이 들어가고, 복원하면 돌아온다
        await c.post("/api/auth/logout")
        c.session.cookie_jar.clear()
        await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        backup = await (await c.get("/api/admin/backup")).json()
        assert backup["brand_icon"] and backup["config"]["branding"]["title"] == "우리집 <극장>"
        await c.delete("/api/admin/branding/icon")
        await c.put("/api/admin/branding", json={"title": ""})
        assert (await c.post("/api/admin/restore", json={**backup, "brand_icon": "!!"})).status == 400
        assert (await c.post("/api/admin/restore", json=backup)).status == 200
        b = await (await c.get("/api/branding")).json()
        assert b["title"] == "우리집 <극장>" and b["icon_url"]
        assert await (await c.get("/brand/icon")).read() == png

    run(flow)
