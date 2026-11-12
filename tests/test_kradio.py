"""공중파 라디오 소스: 저장소 파일 읽기, 채널 짝짓기, 방송 주소 받기 (가짜 저장소·가짜 방송사)"""
import asyncio

import pytest
from aiohttp import web

from ym_player import auth, config_store, kradio

INDEX = """
<select id="citySelector"><option value="seoul">수도권</option><option value="busan">부산·울산·경남</option></select>
<div id="seoul" class="channelContainer visible">
  <a class="channel" href="javascript:changeSource({stn:'kbs', ch:'1fm'})">KBS 1FM</a>
  <a class="channel" href="javascript:changeSource({stn:'mbc', ch:'sfm'})">MBC 표준FM</a>
  <a class="channel" href="javascript:changeSource({stn:'sbs', ch:'powerfm'})">SBS 파워FM</a>
  <a class="channel" href="javascript:changeSource({stn:'ytn'})">YTN 라디오</a>
  <a class="channel" href="javascript:changeSource({stn:'ifm'})">iFM 경인방송</a>
  <a class="channel" href="javascript:changeSource({stn:'afn', city:'humphreys'})">AFN FM Humphreys</a>
  <a class="channel" href="javascript:changeSource({stn:'nowhere'})">없는 방송</a>
</div>
<div id="busan" class="channelContainer">
  <a class="channel" href="javascript:changeSource({stn:'kbs', ch:'1radio', city:'busan'})">KBS부산 1라디오</a>
  <a class="channel" href="javascript:changeSource({stn:'mbc', ch:'sfm', city:'busan'})">부산MBC 표준FM</a>
  <a class="channel" href="javascript:changeSource({stn:'mbc', ch:'sfm', city:'ulsan'})">울산MBC 표준FM</a>
  <a class="channel" href="javascript:changeSource({stn:'ytn'})">YTN 라디오</a>
</div>
"""
KBS = """
const channelCodes = [
    { name: "1라디오", code: "21", param: "1radio" },
    { name: "1FM", code: "24", param: "1fm" },
];
const cityCodes = [
    { name: "KBS부산", code: "10_", param: "busan" },
];
const createApiUrl = ({ channelCode, cityCode = null }) => { return `https://kbs-proxy.example/${cityCode || ""}${channelCode}`; };
"""
MBC = """
export const mbcUrls = [
  /* MBC 표준FM */
  { name: "부산MBC 표준FM", channel: "sfm", city: "busan", streamUrl: async () => {
    return "https://brook.example/live/AM_onairstream." + await todayBSMBCdatecode() + "/playlist.m3u8"
  } },
  // { name: "지운 방송", channel: "sfm", city: "ulsan", streamUrl: "https://old.example/x.m3u8" },
  { name: "울산MBC 표준FM", channel: "sfm", city: "ulsan", streamUrl: "https://ulsan.example/STDFM/playlist.m3u8" },
  { name: "MBC 표준FM", channel: "sfm", city: null, streamUrl: async () => {
    try {
      const response = await fetch("{base}/mbc?channel=sfm");
      const url = await response.text();
      return url;
    } catch (e) { return null; }} },  // default
];
"""
SBS = """
export const sbsUrls = [
  { name: "SBS 파워FM", channel: "powerfm", city: null, streamUrl: async (bora = null) => {
    try {
      const endpoint = bora == "true"
      ? "{base}/sbs/visual"
      : "{base}/sbs/power";
      const response = await fetch(endpoint);
      return await response.text();
    } catch (e) { return null; }} },
];
"""
COMMON = """
export const commonUrls = [
    { name: "YTN 라디오", station: "ytn", streamUrl: "https://ytn.example/radio/playlist.m3u8" },
    { name: "iFM 경인방송", station: "ifm", streamUrl: async (bora = null) => {
    try {
      const url = bora == "true"
      ? "https://ifm.example/bora1/playlist.m3u8"
      : "https://ifm.example/aod1/playlist.m3u8";
      return url;
    } catch (e) { return null; }} },
];
"""
AFN = """
export const afnUrls = [
    { name: "AFN FM Humphreys", city: "humphreys", streamUrl: async () => {
        const response = await fetch("{base}/afn?station=AFNP_OSN");
        const text = await response.text();
        const urls = [...text.matchAll(/<ip>(.*?)<\\/ip>/g)].map(match => match[1]);
        return `https://${urls.reverse()[0] || "14613.live.example"}/AFNP_OSNAAC.aac`;
    }},
];
"""


def _templates(base=""):
    t = {n: "" for n in kradio.TEMPLATES}
    t.update(kbs=KBS, mbc=MBC.replace("{base}", base), sbs=SBS.replace("{base}", base), common=COMMON, afn=AFN.replace("{base}", base))
    return t


def test_build():
    regions, channels = kradio.parse_index(INDEX)
    assert regions == [("seoul", "수도권"), ("busan", "부산·울산·경남")] and len(channels) == 11
    b = kradio.build(INDEX, _templates("http://api.example"))
    by = {s["id"]: s for s in b["stations"]}
    assert b["missing"] == ["없는 방송"] and len(by) == 10
    assert by["seoul.ytn.-.-"]["url"] == "https://ytn.example/radio/playlist.m3u8" and by["busan.ytn.-.-"]["group"] == "부산·울산·경남"
    assert by["busan.mbc.sfm.ulsan"]["url"] == "https://ulsan.example/STDFM/playlist.m3u8"            # 지운(//) 줄은 안 읽는다
    assert by["seoul.mbc.sfm.-"]["how"] == {"kind": "ask", "api": "http://api.example/mbc?channel=sfm", "fallback": ""}
    assert by["seoul.sbs.powerfm.-"]["how"]["api"] == "http://api.example/sbs/power"                    # 보이는 라디오가 아닌 쪽
    assert by["seoul.ifm.-.-"]["how"] == {"kind": "fixed", "url": "https://ifm.example/aod1/playlist.m3u8"}
    assert by["busan.mbc.sfm.busan"]["how"] == {"kind": "datecode", "prefix": "https://brook.example/live/AM_onairstream.", "suffix": "/playlist.m3u8"}
    assert by["seoul.afn.-.humphreys"]["how"]["host"] == "14613.live.example" and by["seoul.afn.-.humphreys"]["how"]["path"] == "/AFNP_OSNAAC.aac"
    assert by["seoul.kbs.1fm.-"]["how"]["api"] == kradio.KBS_API + "24" and by["seoul.kbs.1fm.-"]["how"]["alt"] == "https://kbs-proxy.example/24"
    assert by["busan.kbs.1radio.busan"]["how"]["api"] == kradio.KBS_API + "10_21"


def test_logos_setting():
    src = config_store._clean_source({"id": "kr", "type": "kradio", "logos": [
        {"name": " MBC  FM4U ", "url": "https://img.example/fm4u.png"}, {"name": "KBS 1FM", "url": "javascript:alert(1)"},
        {"name": "", "url": "https://x/y.png"}, "bad", {"name": "MBC FM4U", "url": "http://img.example/new.png"}]}, None)
    assert src["logos"] == [{"name": "MBC FM4U", "url": "http://img.example/new.png"}]
    src = config_store._clean_source({"id": "kr", "type": "kradio", "names": [
        {"from": " 광주MBC  FM4U ", "to": "광주MBC FM4U 91.5"}, {"from": "KBS 1FM", "to": "KBS 1FM"}, {"from": "", "to": "x"},
        {"from": "EBS FM", "to": " "}, "bad", {"from": "광주MBC FM4U", "to": "광주 FM4U"}]}, None)
    assert src["names"] == [{"from": "광주MBC FM4U", "to": "광주 FM4U"}]


def test_pick_stream():
    assert kradio.pick_stream(" https://a.example/x.m3u8?token=1 \n") == "https://a.example/x.m3u8?token=1"
    assert kradio.pick_stream('{"ret":0,"channel_item":[{"service_url":"https://k.example/1fm.m3u8?Policy=x"}]}') == "https://k.example/1fm.m3u8?Policy=x"
    assert kradio.pick_stream('{"onair":{"source":{"mediasource":{"mediaurl":"https://c.example/live"}}}}') == "https://c.example/live"
    assert kradio.pick_stream("<script>src: 'https://e.example/fm/playlist.m3u8?Policy=y', x</script>") == "https://e.example/fm/playlist.m3u8?Policy=y"
    assert kradio.pick_stream('__boraplay({\n\t"BoraURL":\n"https://b.example/bora/playlist.m3u8?t=1"\n\t}\n);') == "https://b.example/bora/playlist.m3u8?t=1"
    assert kradio.pick_stream("<html>nothing</html>") == "" and kradio.pick_ip("<a><ip>1.example</ip><ip>2.example</ip></a>") == "2.example"


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


def test_kradio_api(data, monkeypatch):
    from aiohttp import CookieJar
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server
    hits = {"mbc": 0, "repo": 0, "down": False}

    async def main():
        up = web.Application()

        async def repo_file(request):
            hits["repo"] += 1
            if hits["down"]:
                return web.Response(status=500)
            name = request.match_info["name"]
            base = f"http://127.0.0.1:{request.url.port}"
            if name == "index.html":
                return web.Response(text=INDEX, content_type="text/html")
            return web.Response(text=_templates(base)[name.rsplit("/", 1)[-1][:-3]], content_type="text/plain")

        async def mbc(request):
            hits["mbc"] += 1
            return web.Response(text=f"https://mbc.example/sfm/playlist.m3u8?token={hits['mbc']}", content_type="text/html")

        async def sbs(request):
            return web.Response(status=503)

        async def afn(request):
            return web.Response(text="<x><ip>1.live.example</ip><ip>2.live.example</ip></x>", content_type="text/xml")

        up.router.add_get("/repo/{name:.+}", repo_file)
        up.router.add_get("/mbc", mbc)
        up.router.add_get("/sbs/power", sbs)
        up.router.add_get("/afn", afn)
        upstream = TestServer(up)
        await upstream.start_server()
        cfg = config_store.load()
        cfg["sources"] = [config_store._clean_source({"id": "kr", "type": "kradio"}, None)]
        assert cfg["sources"][0]["name"] == "공중파 라디오"
        config_store.save(cfg)
        app = server.create_app(require_auth=True)
        app[server.KRADIO].base = f"http://127.0.0.1:{upstream.port}/repo/"
        c = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
        await c.start_server()
        try:
            await c.post("/api/auth/login", json={"username": "boss", "password": "secret1"})
            d = await (await c.get("/api/kradio/kr/stations")).json()
            assert [r["name"] for r in d["regions"]] == ["수도권", "부산·울산·경남"] and len(d["stations"]) == 10 and not d["error"]
            assert "url" not in d["stations"][0] and "how" not in d["stations"][0]
            hls = {x["id"]: x["hls"] for x in d["stations"]}
            assert hls["seoul.kbs.1fm.-"] and hls["busan.mbc.sfm.busan"] and hls["seoul.ifm.-.-"] and not hls["seoul.afn.-.humphreys"]
            n = hits["repo"]
            await c.get("/api/kradio/kr/stations")
            assert hits["repo"] == n                                                          # 하루 보관

            async def live(sid, q=""):
                r = await c.get(f"/api/kradio/kr/live/{sid}{q}", allow_redirects=False)
                return r.status, r.headers.get("Location", "")
            from urllib.parse import parse_qs, urlsplit
            status, loc = await live("seoul.ytn.-.-")                                         # 기본은 서버 중계로
            assert status == 302 and loc.startswith("/api/stream?") and parse_qs(urlsplit(loc).query)["url"] == ["https://ytn.example/radio/playlist.m3u8"]
            assert (await live("seoul.ytn.-.-", "?direct=1"))[1] == "https://ytn.example/radio/playlist.m3u8"
            assert (await live("seoul.mbc.sfm.-", "?direct=1"))[1].endswith("token=1")
            assert (await live("seoul.mbc.sfm.-", "?direct=1"))[1].endswith("token=1")        # 잠깐 기억
            assert (await live("seoul.mbc.sfm.-", "?direct=1&retry=1"))[1].endswith("token=2")   # 다시 받기
            assert (await live("seoul.afn.-.humphreys", "?direct=1"))[1] == "https://2.live.example/AFNP_OSNAAC.aac"
            code = __import__("datetime").datetime.now(kradio._KST).strftime("%y%m%d").translate(kradio._DATECODE)
            assert (await live("busan.mbc.sfm.busan", "?direct=1"))[1] == f"https://brook.example/live/AM_onairstream.{code}/playlist.m3u8"
            r = await c.get("/api/kradio/kr/live/seoul.sbs.powerfm.-", allow_redirects=False)     # 방송사가 안 줌
            assert r.status == 502 and "받지 못했습니다" in (await r.json())["error"]
            assert (await live("seoul.none.-.-"))[0] == 404
            assert (await c.get("/api/kradio/nope/stations")).status == 404

            hits["down"] = True                                                               # 저장소가 안 되면 이전 목록 + 알림
            d = await (await c.get("/api/kradio/kr/stations?refresh=1")).json()
            assert len(d["stations"]) == 10 and "받지 못했습니다" in d["error"]
            assert len(kradio._load_cache()["stations"]) == 10                                # 디스크에도 남아 다시 시작해도 쓴다
        finally:
            await c.close()
            await upstream.close()

    asyncio.run(main())
