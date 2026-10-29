"""도로 CCTV (국가교통정보센터 오픈 API) - 응답 읽기, 설정 검사, 서버 API(목록·재생 넘기기·인증키 숨기기)."""
import asyncio
from urllib.parse import parse_qs, urlparse

import pytest
from aiohttp import web

from ym_player import auth, cctv, config_store

pytest.importorskip("aiohttp")

SAMPLE = {"response": {"coordtype": 1, "datacount": 3, "data": [
    {"roadsectionid": "", "coordx": 127.0391, "coordy": 37.4622, "cctvresolution": "", "filecreatetime": "", "cctvtype": 1,
     "cctvformat": "HLS", "cctvname": "[경부선] 서울", "cctvurl": "http://cctv.example/ex/1/playlist.m3u8?token=a"},
    {"coordx": 127.11, "coordy": 37.3, "cctvformat": "HLS", "cctvname": "[영동선] 마성터널", "cctvurl": "http://cctv.example/ex/2/playlist.m3u8"},
    {"coordx": 127.11, "coordy": 37.3, "cctvformat": "HLS", "cctvname": "[영동선] 마성터널", "cctvurl": "http://cctv.example/ex/2/dup.m3u8"},
    {"coordx": 127.2, "coordy": 37.2, "cctvname": "주소 없음", "cctvurl": ""},
]}}


def test_parse_cams():
    cams = cctv.parse_cams(SAMPLE, "ex")
    assert [c["name"] for c in cams] == ["[경부선] 서울", "[영동선] 마성터널"]     # 주소 없는 것·같은 곳은 빼고
    assert cams[0]["group"] == "경부선" and cams[0]["road"] == "ex" and cams[0]["format"] == "HLS"
    assert cams[0]["id"] == cctv.cam_id("[경부선] 서울", 127.0391, 37.4622) and len(cams[0]["id"]) == 10
    one = {"response": {"datacount": 1, "data": {"cctvname": "국도1호선 수원", "cctvurl": "https://x/1.m3u8", "coordx": "127", "coordy": "37"}}}
    got = cctv.parse_cams(one, "its")
    assert len(got) == 1 and got[0]["group"] == "국도" and got[0]["x"] == 127.0          # data가 dict 하나여도
    assert cctv.parse_cams({"response": {"datacount": 0, "data": []}}, "ex") == []
    assert "url" not in cctv.public_cam(cams[0])
    assert cctv.api_error({"resultCode": "99", "resultMsg": "등록되지 않은 인증키입니다"}) == "등록되지 않은 인증키입니다"


def test_settings():
    st = cctv.clean_settings({"api_key": " k1 ", "roads": ["ex", "zz"], "region": "seoul", "cctv_type": "4"})
    assert st == {"api_key": "k1", "roads": ["ex"], "region": "seoul", "cctv_type": "4"}
    assert cctv.clean_settings({"api_key": ""}, {"api_key": "old"})["api_key"] == "old"   # 빈 칸이면 기존 키
    assert cctv.clean_settings({"region": "nowhere", "roads": []}) == {"roads": ["ex", "its"], "region": "all", "cctv_type": "1"}
    assert cctv.clean_settings({"region": "custom", "box": "126.9,127.1, 37.4 37.6"})["box"] == "126.9, 127.1, 37.4, 37.6"
    with pytest.raises(ValueError):
        cctv.clean_settings({"region": "custom", "box": "1,2,3"})
    p = cctv.request_params({"api_key": "k", "region": "seoul", "cctv_type": "1"}, "its")
    assert p["type"] == "its" and p["minX"] == "126.76" and p["getType"] == "json" and p["apiKey"] == "k"


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MP_USER", raising=False)
    monkeypatch.delenv("MP_PASSWORD", raising=False)
    monkeypatch.setenv("MP_UPDATE_CHECK", "0")
    auth._fails.clear()
    auth.create_user("boss", "secret1", "admin")
    auth.create_user("kid", "secret2", "user")
    return tmp_path


def test_cctv_api(data, monkeypatch):
    from aiohttp import CookieJar
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server
    calls = []

    async def api(request):
        q = request.query
        calls.append(dict(q))
        if q.get("apiKey") != "good":
            return web.json_response({"resultCode": "30", "resultMsg": "등록되지 않은 인증키입니다"})
        if q["type"] == "its":
            return web.json_response({"response": {"datacount": 1, "data": {
                "cctvname": "국도1호선 수원", "cctvurl": "http://cctv.example/its/9.m3u8", "coordx": 127.0, "coordy": 37.2}}})
        return web.json_response(SAMPLE)

    async def main():
        up = web.Application()
        up.router.add_get("/cctvInfo", api)
        upstream = TestServer(up)
        await upstream.start_server()
        monkeypatch.setattr(cctv, "API", f"http://127.0.0.1:{upstream.port}/cctvInfo")
        cfg = config_store.load()
        cfg["sources"] = [config_store._clean_source({"id": "cc", "type": "cctv", "api_key": "good", "region": "seoul"}, None)]
        config_store.save(cfg)
        c = TestClient(TestServer(server.create_app(require_auth=True)), cookie_jar=CookieJar(unsafe=True))
        await c.start_server()
        try:
            await c.post("/api/auth/login", json={"username": "kid", "password": "secret2"})
            pub = await (await c.get("/api/config")).json()
            assert "api_key" not in pub["sources"][0] and "good" not in str(pub)            # 인증키는 브라우저로 안 나감
            d = await (await c.get("/api/cctv/cc/cams")).json()
            names = [x["name"] for x in d["cams"]]
            assert names == ["[경부선] 서울", "[영동선] 마성터널", "국도1호선 수원"] and d["error"] == ""   # 고속도로 먼저
            assert all("url" not in x for x in d["cams"])
            assert {x["type"] for x in calls} == {"ex", "its"} and calls[0]["minX"] == "126.76"
            n = len(calls)
            await c.get("/api/cctv/cc/cams")
            assert len(calls) == n                                                          # 몇 분은 캐시
            cid = d["cams"][0]["id"]
            r = await c.get(f"/api/cctv/cc/live/{cid}", allow_redirects=False)
            assert r.status == 302
            loc = urlparse(r.headers["Location"])
            assert loc.path == "/api/stream" and parse_qs(loc.query)["url"] == ["http://cctv.example/ex/1/playlist.m3u8?token=a"]
            assert (await c.get("/api/cctv/cc/live/0123456789", allow_redirects=False)).status == 404

            # 인증키가 틀리면 API가 준 말을 그대로
            await c.post("/api/auth/logout")
            await c.post("/api/auth/login", json={"username": "boss", "password": "secret1"})
            cfg = config_store.load()
            cfg["sources"][0]["api_key"] = "bad"
            config_store.save(cfg)
            d = await (await c.get("/api/cctv/cc/cams?refresh=1")).json()
            assert "등록되지 않은 인증키" in d["error"]
            st = await (await c.get("/api/admin/status")).json()
            row = st["sources"][0]
            assert row["type"] == "cctv" and row["ok"] is False
        finally:
            await c.close()
            await upstream.close()

    asyncio.run(main())
