"""느린 요청 고치기: IPTV 채널 목록·편성표는 받아 둔 것 먼저(뒤에서 새로, 서버를 다시 켜도 디스크에서),
라디오 곡 정보는 느린 방송국을 기다리지 않음."""
import asyncio
import time

import pytest

from ym_player import config_store

pytest.importorskip("aiohttp")

M3U = "#EXTM3U\n#EXTINF:-1,KBS1\nhttp://x/1.m3u8\n"


def test_iptv_list_served_from_memory_and_disk(monkeypatch, tmp_path):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "WARM_DELAY_SEC", 3600)   # (이 테스트에서는 미리 준비를 안 함)
    hits = {"n": 0, "delay": 0.0, "name": "KBS1"}

    async def m3u(request):
        hits["n"] += 1
        await asyncio.sleep(hits["delay"])
        return web.Response(text=M3U.replace("KBS1", hits["name"]))

    async def run():
        origin = web.Application()
        origin.router.add_get("/list.m3u", m3u)
        osrv = TestServer(origin)
        await osrv.start_server()
        base = str(osrv.make_url("")).rstrip("/")
        cfg = config_store.load()
        config_store.replace_sources(cfg, [{"id": "tv", "type": "iptv", "name": "TV", "m3u": f"{base}/list.m3u"}])
        config_store.save(cfg)

        async def names(c, **params):
            return [x["name"] for x in (await (await c.get("/api/iptv/tv/channels", params=params)).json())["channels"]]
        c = TestClient(TestServer(server.create_app(require_auth=False)))
        await c.start_server()
        try:
            assert await names(c) == ["KBS1"] and hits["n"] == 1        # 처음 한 번은 기다린다
            assert await names(c) == ["KBS1"] and hits["n"] == 1        # 기억한 것
            # 오래됐으면: 받아 둔 것을 바로 주고(원본이 느려도) 뒤에서 새로
            app = c.server.app
            key = next(iter(app[server.CHANNEL_CACHE]))
            at, data = app[server.CHANNEL_CACHE][key]
            app[server.CHANNEL_CACHE][key] = (at - server.CHANNEL_CACHE_SEC - 1, data)
            hits.update(delay=1.0, name="KBS2")
            t0 = time.monotonic()
            assert await names(c) == ["KBS1"] and time.monotonic() - t0 < 0.5
            await asyncio.sleep(1.3)
            assert await names(c) == ["KBS2"] and hits["n"] == 2        # 뒤에서 새로 받은 것
            hits["delay"] = 0
            assert await names(c, refresh="1") == ["KBS2"] and hits["n"] == 3   # [새로 고침]은 바로 받는다
        finally:
            await c.close()
        # 서버를 다시 켜도: 디스크에 남은 것을 바로 (원본에 다시 묻지 않음)
        c = TestClient(TestServer(server.create_app(require_auth=False)))
        await c.start_server()
        try:
            hits["name"] = "KBS3"
            assert await names(c) == ["KBS2"] and hits["n"] == 3
        finally:
            await c.close()
            await osrv.close()
    asyncio.run(run())


def test_radio_now_does_not_wait_for_slow_station(monkeypatch, tmp_path):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "RADIO_NOW_WAIT", 0.5)

    def icy_body(text):
        meta = f"StreamTitle='{text}';".encode()
        meta += b"\0" * (-len(meta) % 16)
        return b"\xff" * 100 + bytes([len(meta) // 16]) + meta

    async def fast(request):
        return web.Response(body=icy_body("A - Fast"), headers={"icy-metaint": "100"})

    async def slow(request):
        await asyncio.sleep(1.5)
        return web.Response(body=icy_body("B - Slow"), headers={"icy-metaint": "100"})

    async def no_art(app, artist, title):
        return ""

    async def run():
        origin = web.Application()
        origin.router.add_get("/fast", fast)
        origin.router.add_get("/slow", slow)
        osrv = TestServer(origin)
        await osrv.start_server()
        base = str(osrv.make_url("")).rstrip("/")
        cfg = config_store.load()
        config_store.replace_sources(cfg, [{"id": "rd", "type": "radio", "name": "라디오", "stations": [
            {"name": "빠름", "url": f"{base}/fast"}, {"name": "느림", "url": f"{base}/slow"}]}])
        config_store.save(cfg)
        monkeypatch.setattr(server, "_radio_art", no_art)
        c = TestClient(TestServer(server.create_app(require_auth=False)))
        await c.start_server()
        try:
            t0 = time.monotonic()
            now = (await (await c.get("/api/radio/rd/now")).json())["now"]
            assert time.monotonic() - t0 < 1.2                            # 느린 방송국을 다 기다리지 않음
            assert now["빠름"]["text"] == "A - Fast" and now["느림"].get("pending")
            await asyncio.sleep(1.5)
            now = (await (await c.get("/api/radio/rd/now")).json())["now"]
            assert now["느림"]["text"] == "B - Slow"                      # 뒤에서 마저 받은 것
        finally:
            await c.close()
            await osrv.close()
    asyncio.run(run())
