"""재생 현황 /api/activity - 재생 요청만 세기, 조각은 45초, 긴 연결은 열려 있는 동안, 토큰은 이 경로만."""
import asyncio

import pytest
from aiohttp import web

from ym_player import activity, auth, config_store

pytest.importorskip("aiohttp")


def test_classify():
    c = lambda p, q=None: activity.classify(p, q or {})
    assert c("/api/stream") == ("live", "")
    assert c("/api/tg/tg/stream/-100/5") == ("telegram", "-100/5")
    assert c("/api/tg/tg/transcode/7/5") == ("telegram", "7/5")
    assert c("/api/tg-hls/abc123/seg3.ts") == ("telegram", "hls/abc123")
    assert c("/api/plex/px/library/parts/42/1700/file.mkv") == ("plex", "part/42")
    assert c("/api/plex/px/video/:/transcode/universal/session/S1/base/00001.ts") == ("plex", "transcode/S1")
    assert c("/api/plex/px/video/:/transcode/universal/start.m3u8", {"session": "S2"}) == ("plex", "transcode/S2")
    for p in ("/api/tg/tg/thumb/1/2", "/api/tg/tg/videos", "/api/iptv/x/epg", "/api/plex/px/photo/:/transcode",
              "/api/plex/px/library/sections", "/api/tg/tg/image/1/2", "/api/tg/tg/book/1/2/3", "/api/activity"):
        assert c(p) is None, p
    assert c("/api/tg/tg/stream/1/2", {"doc": "1"}) is None                       # 책 보기 (PDF/EPUB)
    assert c("/api/plex/px/video/:/transcode/universal/stop", {"session": "S2"}) is None


def test_window(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(activity.time, "monotonic", lambda: now[0])
    a = activity.Activity()
    seg = ("telegram", "u", "hls/x")
    a.end(seg, a.begin(seg))                     # 조각 하나 받음
    a.end(seg, a.begin(seg))                     # 같은 스트림의 다음 조각 -> 하나로
    live = ("live", "u", "")
    started = a.begin(live)                      # mpegts: 연결이 열려 있음
    assert a.snapshot() == {"playing": 2, "by": {"plex": 0, "telegram": 1, "live": 1}}
    now[0] += 44
    assert a.snapshot()["by"]["telegram"] == 1   # 마지막 조각 뒤 45초 안
    now[0] += 2
    assert a.snapshot() == {"playing": 1, "by": {"plex": 0, "telegram": 0, "live": 1}}
    a.end(live, started)                         # 46초 열려 있던 긴 연결이 끝남 -> 바로 빠짐
    assert a.snapshot()["playing"] == 0
    bad = ("plex", "u", "part/1")
    a.end(bad, a.begin(bad), ok=False)           # 실패한 요청은 안 남긴다
    assert a.snapshot()["playing"] == 0 and a.streams == {}


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MP_USER", raising=False)
    monkeypatch.delenv("MP_PASSWORD", raising=False)
    monkeypatch.setenv("MP_UPDATE_CHECK", "0")
    monkeypatch.setenv("MP_ACTIVITY_TOKEN", "s3cret-token")
    auth._fails.clear()
    auth.create_user("boss", "secret1", "admin")
    auth.create_user("kid", "secret2", "user")
    return tmp_path


def test_activity_api(data, monkeypatch):
    from aiohttp import CookieJar
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server
    release = asyncio.Event()

    async def fake_stream(request):          # mpegts 중계처럼 끝나지 않는 스트림
        resp = web.StreamResponse()
        await resp.prepare(request)
        await resp.write(b"x" * 188)
        await release.wait()
        return resp

    async def fake_hls(request):             # HLS 조각
        return web.Response(body=b"seg")

    async def fake_plex(request):
        return web.Response(body=b"img")

    monkeypatch.setattr(server, "stream_proxy", fake_stream)
    monkeypatch.setattr(server, "tg_hls_file", fake_hls)
    monkeypatch.setattr(server, "plex_proxy", fake_plex)
    tok = {"X-Activity-Token": "s3cret-token"}

    async def main():
        c = TestClient(TestServer(server.create_app(require_auth=True)), cookie_jar=CookieJar(unsafe=True))
        await c.start_server()
        try:
            r = await c.get("/api/activity", headers=tok)
            assert r.status == 200 and await r.json() == {"success": True, "playing": 0, "by": {"plex": 0, "telegram": 0, "live": 0}}
            assert (await c.get("/api/activity", headers={"X-Activity-Token": "wrong"})).status == 401
            assert (await c.get("/api/activity")).status == 401
            assert (await c.get("/api/config", headers=tok)).status == 401          # 토큰으로 다른 API는 안 열림
            assert (await c.get("/api/stream", headers=tok)).status == 401
            await c.post("/api/auth/login", json={"username": "kid", "password": "secret2"})
            assert (await c.get("/api/activity")).status == 403                     # 일반 사용자는 못 봄
            streaming = asyncio.ensure_future(c.get("/api/stream?url=http://x/live.ts"))
            for _ in range(50):
                if (await (await c.get("/api/activity", headers=tok)).json())["by"]["live"]:
                    break
                await asyncio.sleep(0.02)
            assert (await c.get("/api/tg-hls/job1/seg1.ts")).status == 200
            assert (await c.get("/api/tg-hls/job1/seg2.ts")).status == 200
            await c.get("/api/plex/px/photo/:/transcode?url=/t")                   # 썸네일은 안 셈
            d = await (await c.get("/api/activity", headers=tok)).json()
            assert d == {"success": True, "playing": 2, "by": {"plex": 0, "telegram": 1, "live": 1}}
            assert "kid" not in str(d) and "job1" not in str(d)
            await c.post("/api/auth/logout")
            await c.post("/api/auth/login", json={"username": "boss", "password": "secret1"})
            assert (await (await c.get("/api/activity")).json())["playing"] == 2      # 관리자는 로그인으로도
            release.set()
            r = await streaming
            await r.read()
        finally:
            await c.close()
    asyncio.run(main())
