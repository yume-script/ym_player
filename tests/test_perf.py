"""빠르게 보이게: 응답 압축(gzip), 화면 파일 압축본, 느린 요청 측정 (perf.py)."""
import asyncio
import gzip
import os

import pytest

from ym_player import auth, config_store, perf

pytest.importorskip("aiohttp")


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


def test_timings_top():
    t = perf.Timings()
    for ms in (100, 300):
        t.add("GET /api/a", ms)
    t.add("GET /api/b", 2500)
    top = t.top(5)
    assert [r["route"] for r in top] == ["GET /api/b", "GET /api/a"]
    assert top[0]["slow"] == 1 and top[1]["avg_ms"] == 200 and top[1]["max_ms"] == 300 and top[1]["count"] == 2


def test_compression_and_slow_list(data):
    from aiohttp import CookieJar
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server

    async def main():
        app = server.create_app(require_auth=True)
        c = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
        await c.start_server()
        try:
            gz = {"Accept-Encoding": "gzip"}
            await c.post("/api/auth/login", json={"username": "boss", "password": "secret1"})
            # 화면(index.html)·큰 JSON은 압축, 작은 응답은 그대로
            r = await c.get("/", headers=gz)
            assert r.headers.get("Content-Encoding") == "gzip" and "<html" in (await r.text()).lower()
            r = await c.get("/api/config", headers=gz)
            small = len(await r.read()) < perf.MIN_BYTES
            assert (r.headers.get("Content-Encoding") == "gzip") != small
            r = await c.get("/api/auth/state", headers=gz)
            assert "Content-Encoding" not in r.headers                       # 작은 응답
            r = await c.get("/", headers={"Accept-Encoding": "identity"})
            assert "Content-Encoding" not in r.headers                       # gzip을 안 받는 브라우저
            # 지문 붙은 화면 파일: 압축본 + 오래 캐시, 풀면 원래 파일과 같다
            token = app[server.STATIC_TOKEN]
            r = await c.get(f"/s/{token}/js/app.js", headers=gz, auto_decompress=False)
            body = await r.read()
            assert r.headers["Content-Encoding"] == "gzip" and "immutable" in r.headers["Cache-Control"]
            assert r.headers["Content-Type"].startswith("application/javascript")
            with open(os.path.join(server.WEB_DIR, "js", "app.js"), "rb") as f:
                assert gzip.decompress(body) == f.read()
            assert len(body) < os.path.getsize(os.path.join(server.WEB_DIR, "js", "app.js")) / 2
            r = await c.get(f"/s/{token}/js/app.js", headers={"Accept-Encoding": "identity"})
            assert r.headers.get("Content-Encoding") is None                  # (gzip을 안 받으면 원본 파일)
            # 설정 > 상태의 느린 요청: 라우트 이름으로 모이고 화면 파일은 빠진다
            st = await (await c.get("/api/admin/status")).json()
            routes = {x["route"] for x in st["slow"]}
            assert "GET /api/config" in routes and not any("/s/" in x for x in routes) and st["slow_since"] > 0
        finally:
            await c.close()
    asyncio.run(main())


def test_pwa_manifest_and_service_worker(data):
    """앱 설치: manifest(이름·아이콘)와 서비스 워커는 로그인 없이 열리고, 화면에 연결돼 있다"""
    import json

    from aiohttp import CookieJar
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server

    async def main():
        app = server.create_app(require_auth=True)
        c = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
        await c.start_server()
        try:
            r = await c.get("/manifest.webmanifest")
            m = json.loads(await r.text())
            assert r.status == 200 and r.content_type == "application/manifest+json"
            assert m["display"] == "standalone" and m["start_url"] == "/" and m["name"]
            assert {i["sizes"] for i in m["icons"]} == {"192x192", "512x512"}
            for icon in m["icons"]:
                assert (await c.get(icon["src"])).status == 200            # 아이콘 파일이 실제로 있다
            r = await c.get("/sw.js")
            js = await r.text()
            assert r.status == 200 and r.content_type == "application/javascript"
            assert f"/s/{app[server.STATIC_TOKEN]}/" in js and "caches" in js
            assert (await c.get("/api/config")).status == 401                   # (다른 것은 여전히 로그인 필요)
            await c.post("/api/auth/login", json={"username": "boss", "password": "secret1"})
            page = await (await c.get("/")).text()
            assert 'rel="manifest"' in page and f"/s/{app[server.STATIC_TOKEN]}/img/apple-touch-icon.png" in page and "__APPLE_ICON__" not in page
            login = await (await c.get("/login")).text()
            assert 'rel="manifest"' in login
        finally:
            await c.close()
    asyncio.run(main())
