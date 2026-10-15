"""Yume Player(ym_player) - M3U/XMLTV 파싱, HLS 주소 바꾸기, 설정 저장, 서버 중계."""
import asyncio
import gzip
import os
import tempfile

import pytest

os.environ.setdefault("MP_DATA_DIR", tempfile.mkdtemp(prefix="mp_test_"))

from ym_player import config_store, iptv  # noqa: E402

M3U = """#EXTM3U x-tvg-url="http://epg.example/epg.xml"
#EXTINF:-1 tvg-id="KBS1.kr" tvg-logo="http://logo/kbs1.png" group-title="지상파" tvg-chno="9",KBS1
http://stream.example/kbs1.m3u8
#EXTINF:-1 group-title="뉴스",YTN 뉴스
#EXTVLCOPT:http-user-agent=MyUA/1.0
#EXTVLCOPT:http-referrer=http://ref.example/
http://stream.example/ytn.ts

#EXTINF:-1,주소 없는 채널
#EXTINF:-1 tvg-name="이름만",
http://stream.example/noname
"""

XMLTV = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="KBS1.kr"><display-name>KBS 1TV</display-name><icon src="http://icon/kbs"/></channel>
  <channel id="ytn.news"><display-name>YTN 뉴스</display-name></channel>
  <programme start="20261014090000 +0900" stop="20261014100000 +0900" channel="KBS1.kr">
    <title>아침 뉴스</title><desc>설명</desc><category>뉴스</category>
  </programme>
  <programme start="20261014080000" stop="20261014090000" channel="KBS1.kr"><title>새벽</title></programme>
  <programme start="20261014100000 +0000" channel="ytn.news"><title>YTN 24</title></programme>
</tv>
"""


def test_parse_m3u_reads_attributes_and_vlc_options():
    chans = iptv.parse_m3u(M3U, "개인", "s1")
    assert [c["name"] for c in chans] == ["KBS1", "YTN 뉴스", "이름만"]
    kbs, ytn, noname = chans
    assert kbs["tvg_id"] == "KBS1.kr" and kbs["logo"] == "http://logo/kbs1.png"
    assert kbs["group"] == "지상파" and kbs["chno"] == "9" and kbs["id"] == "s1:KBS1.kr"
    # tvg-id가 없으면 소스:이름으로 id를 만든다
    assert ytn["id"] == "s1:YTN 뉴스" and ytn["group"] == "뉴스"
    assert ytn["ua"] == "MyUA/1.0" and ytn["referer"] == "http://ref.example/"
    assert "ua" not in kbs
    # 주소 줄이 없는 #EXTINF는 다음 #EXTINF가 덮어쓴다
    assert noname["url"] == "http://stream.example/noname"


def test_xmltv_date_offsets():
    assert iptv.parse_xmltv_date("20261014090000 +0900") == iptv.parse_xmltv_date("20261014000000 +0000")
    # 오프셋이 없으면 KST
    assert iptv.parse_xmltv_date("20261014090000") == iptv.parse_xmltv_date("20261014090000 +0900")
    assert iptv.parse_xmltv_date("20261014090000 -0500") - iptv.parse_xmltv_date("20261014090000 +0000") == 5 * 3600
    assert iptv.parse_xmltv_date("garbage") is None


def test_parse_xmltv_sorts_maps_names_and_windows():
    data = iptv.parse_xmltv(XMLTV)
    kbs = data["programmes"]["KBS1.kr"]
    assert [p[2] for p in kbs] == ["새벽", "아침 뉴스"]  # 시작 시각 순
    assert kbs[1][3] == "설명" and kbs[1][4] == "뉴스"
    assert data["names"]["kbs1tv"] == "KBS1.kr"
    assert data["icons"]["KBS1.kr"] == "http://icon/kbs"
    assert data["programmes"]["ytn.news"][0][1] is None  # stop 없는 방송

    nine = iptv.parse_xmltv_date("20261014090000")
    windowed = iptv.parse_xmltv(XMLTV, window_start=nine, window_end=nine + 1800)
    assert [p[2] for p in windowed["programmes"]["KBS1.kr"]] == ["아침 뉴스"]


def test_parse_xmltv_accepts_gzip_and_rejects_garbage():
    data = iptv.parse_xmltv(gzip.compress(XMLTV.encode("utf-8")))
    assert "KBS1.kr" in data["programmes"]
    with pytest.raises(ValueError):
        iptv.parse_xmltv(b"<html><body>404 Not Found")


def test_rewrite_m3u8_makes_every_uri_absolute_through_proxy():
    text = "\n".join([
        "#EXTM3U",
        '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"',
        "#EXTINF:6.0,",
        "seg/001.ts?x=1",
        "#EXTINF:6.0,",
        "https://cdn.example/abs/002.ts",
        '#EXT-X-MEDIA:TYPE=AUDIO,URI="../audio/index.m3u8"',
    ])
    out = iptv.rewrite_m3u8(text, "http://host.example/live/ch1/index.m3u8",
                            lambda u: iptv.stream_proxy_url(u, "UA X"))
    lines = out.splitlines()
    assert lines[0] == "#EXTM3U"
    assert 'URI="/api/stream?url=http%3A%2F%2Fhost.example%2Flive%2Fch1%2Fkey.bin&ua=UA%20X"' in lines[1]
    assert lines[3] == "/api/stream?url=http%3A%2F%2Fhost.example%2Flive%2Fch1%2Fseg%2F001.ts%3Fx%3D1&ua=UA%20X"
    assert lines[5].startswith("/api/stream?url=https%3A%2F%2Fcdn.example%2Fabs%2F002.ts")
    assert "http%3A%2F%2Fhost.example%2Flive%2Faudio%2Findex.m3u8" in lines[6]


def test_looks_like_m3u8():
    assert iptv.looks_like_m3u8("application/vnd.apple.mpegurl", "http://x/a", b"")
    assert iptv.looks_like_m3u8("text/plain", "http://x/a.m3u8?t=1", b"")
    assert iptv.looks_like_m3u8("application/octet-stream", "http://x/play", b"#EXTM3U")
    assert not iptv.looks_like_m3u8("video/mp2t", "http://x/a.ts", b"\x47\x40\x00")


def test_config_keeps_token_when_blank_and_hides_it(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    cfg = config_store.load()
    assert cfg["client_id"] and cfg["sources"] == []

    config_store.replace_sources(cfg, [
        {"id": "p1", "type": "plex", "name": "집", "url": "http://plex:32400/", "token": "SECRET"},
        {"type": "iptv", "name": "공개", "m3u": "http://m3u", "enabled": False},
    ])
    config_store.save(cfg)
    assert cfg["sources"][0]["url"] == "http://plex:32400"  # 끝 / 제거

    pub = config_store.public_config(config_store.load())
    assert "token" not in pub["sources"][0] and pub["sources"][0]["has_token"] is True
    assert pub["sources"][1]["enabled"] is False and pub["sources"][1]["id"]

    # 화면이 토큰 칸을 비워 보내면 기존 토큰 유지
    cfg = config_store.load()
    config_store.replace_sources(cfg, [{"id": "p1", "type": "plex", "name": "집2", "url": "http://plex:32400", "token": ""}])
    assert cfg["sources"][0]["token"] == "SECRET" and cfg["sources"][0]["name"] == "집2"

    with pytest.raises(ValueError):
        config_store.replace_sources(cfg, [{"type": "iptv", "name": "빈 주소", "m3u": ""}])
    with pytest.raises(ValueError):
        config_store.replace_sources(cfg, [{"type": "jellyfin", "name": "?"}])


def test_server_proxies_m3u_epg_streams_and_plex(monkeypatch, tmp_path):
    """가짜 원본 서버(M3U/EPG/HLS/Plex)를 띄워 놓고 실제 HTTP로 중계를 확인한다."""
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from yarl import URL

    from ym_player import server

    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    seen = {}

    async def origin_m3u(request):
        return web.Response(text=M3U)

    async def origin_epg(request):
        return web.Response(body=gzip.compress(XMLTV.encode("utf-8")), content_type="application/octet-stream")

    async def origin_hls(request):
        seen["ua"] = request.headers.get("User-Agent")
        return web.Response(text="#EXTM3U\n#EXTINF:6,\nseg1.ts\n", content_type="application/vnd.apple.mpegurl")

    async def origin_seg(request):
        return web.Response(body=b"\x47" * 1000, content_type="video/mp2t")

    async def origin_plex(request):
        seen["plex_token"] = request.headers.get("X-Plex-Token")
        seen["plex_path"] = request.rel_url.raw_path
        seen["plex_query"] = request.rel_url.raw_query_string
        return web.json_response({"MediaContainer": {"Directory": [{"key": "1", "title": "영화", "type": "movie"}]}})

    async def run():
        origin = web.Application()
        origin.router.add_get("/list.m3u", origin_m3u)
        origin.router.add_get("/epg.xml.gz", origin_epg)
        origin.router.add_get("/live/index.m3u8", origin_hls)
        origin.router.add_get("/live/seg1.ts", origin_seg)
        origin.router.add_route("*", "/{tail:.*}", origin_plex)
        origin_srv = TestServer(origin)
        await origin_srv.start_server()
        base = str(origin_srv.make_url("")).rstrip("/")

        cfg = config_store.load()
        config_store.replace_sources(cfg, [
            {"id": "tv", "type": "iptv", "name": "개인", "m3u": f"{base}/list.m3u", "epg": f"{base}/epg.xml.gz"},
            {"id": "px", "type": "plex", "name": "집", "url": base, "token": "TOK"},
        ])
        config_store.save(cfg)

        client = TestClient(TestServer(server.create_app(require_auth=False)))
        await client.start_server()
        try:
            r = await client.get("/api/config")
            body = await r.json()
            assert "TOK" not in await r.text() and body["sources"][1]["has_token"]

            r = await client.get("/api/iptv/tv/channels")
            chans = (await r.json())["channels"]
            assert [c["name"] for c in chans][:2] == ["KBS1", "YTN 뉴스"]

            # 지금 시각 기준 창으로 잘라내므로 2026-10-14 방송은 테스트 날짜에 따라 비어 있을 수 있다 - 이름표만 확인
            r = await client.get("/api/iptv/tv/epg")
            epg = await r.json()
            assert epg["success"] and epg["names"]["ytn뉴스"] == "ytn.news"

            r = await client.get("/api/stream", params={"url": f"{base}/live/index.m3u8", "ua": "TestUA"})
            text = await r.text()
            assert seen["ua"] == "TestUA"
            seg_line = text.strip().splitlines()[-1]
            assert seg_line.startswith("/api/stream?url=") and "seg1.ts" in seg_line and "ua=TestUA" in seg_line
            r = await client.get(seg_line)
            assert (await r.read()) == b"\x47" * 1000

            r = await client.get("/api/stream", params={"url": "file:///etc/passwd"})
            assert r.status == 400

            r = await client.get("/api/plex/px/library/sections", headers={"Accept": "application/json"})
            assert (await r.json())["MediaContainer"]["Directory"][0]["title"] == "영화"
            assert seen["plex_token"] == "TOK"
            # 인코딩된 경로/쿼리는 그대로 넘어가야 한다
            await client.get(URL("/api/plex/px/library/parts/1/my%20file.mkv?q=a%26b&width=10", encoded=True))
            assert seen["plex_path"] == "/library/parts/1/my%20file.mkv"
            assert seen["plex_query"] == "q=a%26b&width=10"

            r = await client.get("/api/plex/nope/library/sections")
            assert r.status == 404
        finally:
            await client.close()
            await origin_srv.close()

    asyncio.run(run())
