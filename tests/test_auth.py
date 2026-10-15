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
    assert u == {"username": "yume", "role": "admin"}
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
    assert auth.bootstrap_from_env() == {"username": "boss", "role": "admin"}
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
        assert me["user"] == {"username": "admin", "role": "admin"}

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
