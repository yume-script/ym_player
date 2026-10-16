"""YMPlayer(ym_player) - M3U/XMLTV 파싱, HLS 주소 바꾸기, 설정 저장, 서버 중계."""
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
        async def origin_mpd(request):
            return web.Response(
                text='<MPD type="static"><Period><AdaptationSet><SegmentTemplate media="v-$Number$.m4s"/></AdaptationSet></Period></MPD>',
                content_type="application/dash+xml")
        origin.router.add_get("/dash/manifest.mpd", origin_mpd)
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

            # DASH: 중계로 받은 MPD는 원본 기준 절대 BaseURL이 들어간다
            r = await client.get("/api/stream", params={"url": f"{base}/dash/manifest.mpd"})
            assert r.content_type == "application/dash+xml"
            assert f"<BaseURL>{base}/dash/manifest.mpd</BaseURL><Period>" in await r.text()

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


def test_parse_m3u_drops_bogus_logos():
    text = ("#EXTM3U\n"
            '#EXTINF:-1 tvg-logo="None",A\nhttp://x/a.m3u8\n'
            '#EXTINF:-1 tvg-logo="logo.png",B\nhttp://x/b.m3u8\n'
            '#EXTINF:-1 tvg-logo=" https://cdn/c.png ",C\nhttp://x/c.mpd\n')
    logos = [c["logo"] for c in iptv.parse_m3u(text)]
    assert logos == ["", "", "https://cdn/c.png"]


def test_rewrite_mpd_makes_base_absolute():
    base = "https://cdn.example/live/ch1/manifest.mpd?token=a&b=1"
    mpd = '<?xml version="1.0"?>\n<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="dynamic"><Period id="1"><BaseURL>v/</BaseURL></Period></MPD>'
    out = iptv.rewrite_mpd(mpd, base)
    assert '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="dynamic"><BaseURL>https://cdn.example/live/ch1/manifest.mpd?token=a&amp;b=1</BaseURL><Period' in out
    assert "<BaseURL>v/</BaseURL>" in out  # 아래 단계 상대 주소는 그대로 (위 BaseURL 기준으로 풀림)
    top = '<MPD><BaseURL>../seg/</BaseURL><Period/></MPD>'
    assert iptv.rewrite_mpd(top, base) == '<MPD><BaseURL>https://cdn.example/live/seg/</BaseURL><Period/></MPD>'
    assert iptv.rewrite_mpd("not xml", base) == "not xml"
    assert iptv.looks_like_mpd("application/dash+xml", "http://x/a", b"")
    assert iptv.looks_like_mpd("", "http://x/a.mpd?x=1", b"")
    assert not iptv.looks_like_mpd("video/mp2t", "http://x/a.ts", b"G@")


def test_plex_part_goes_through_block_cache(monkeypatch, tmp_path):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import media_cache, server

    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    B = media_cache.BLOCK
    movie = tmp_path / "movie.mkv"
    data = bytes((i * 13) % 251 for i in range(B * 3 + 777))
    movie.write_bytes(data)
    hits = []

    async def part(request):
        hits.append(request.headers.get("Range"))
        assert request.headers.get("X-Plex-Token") == "TOK"
        return web.FileResponse(movie)

    async def run():
        origin = web.Application()
        origin.router.add_get("/library/parts/5/1700000000/movie.mkv", part)
        osrv = TestServer(origin)
        await osrv.start_server()
        cfg = config_store.load()
        config_store.replace_sources(cfg, [{"id": "px", "type": "plex", "name": "집",
                                            "url": str(osrv.make_url("")).rstrip("/"), "token": "TOK"}])
        cfg["cache"] = {"enabled": True, "ahead_min": 0}
        config_store.save(cfg)
        client = TestClient(TestServer(server.create_app(require_auth=False)))
        await client.start_server()
        try:
            url = "/api/plex/px/library/parts/5/1700000000/movie.mkv"
            r = await client.get(url, headers={"Range": f"bytes={B - 5}-{B + 9}"})
            assert r.status == 206 and r.headers["Content-Range"] == f"bytes {B - 5}-{B + 9}/{len(data)}"
            assert await r.read() == data[B - 5:B + 10]
            r = await client.get(url)
            assert r.status == 200 and await r.read() == data
            n = len(hits)  # 크기 확인 1번 + 조각 4개 (조각마다 한 번)
            assert n == 5
            media_cache.STORE.ram.clear()
            r = await client.get(url, headers={"Range": "bytes=0-99"})
            assert await r.read() == data[:100] and len(hits) == n   # 디스크 캐시에서
            st = await (await client.get("/api/admin/cache")).json()
            assert st["stats"]["bytes"] == len(data) and st["settings"]["ahead_min"] == 0
            r = await client.put("/api/admin/cache", json={"enabled": True, "max_gb": 5, "ahead_min": 2})
            assert (await r.json())["settings"]["max_gb"] == 5.0
            assert config_store.load()["cache"]["ahead_min"] == 2
            r = await client.delete("/api/admin/cache")
            assert (await r.json())["stats"]["bytes"] == 0
            # 캐시를 끄면 예전처럼 그냥 중계
            await client.put("/api/admin/cache", json={"enabled": False})
            before = len(hits)
            r = await client.get(url, headers={"Range": "bytes=0-9"})
            assert await r.read() == data[:10] and len(hits) == before + 1
        finally:
            await client.close()
            await osrv.close()

    asyncio.run(run())


def test_merge_channels_dedupes_and_keeps_alternates():
    a = iptv.parse_m3u('#EXTM3U\n#EXTINF:-1 tvg-id="kbs1",KBS1\nhttp://a/kbs1.m3u8\n'
                       '#EXTINF:-1,YTN\nhttp://a/ytn.m3u8\n', "TV", "s")
    b = iptv.parse_m3u('#EXTM3U\n#EXTINF:-1 tvg-logo="http://b/logo.png",kbs 1\n#EXTVLCOPT:http-referrer=http://b/\nhttp://b/kbs1.m3u8\n'
                       '#EXTINF:-1,YTN 사본\nhttp://a/ytn.m3u8\n'          # 같은 주소 -> 버림
                       '#EXTINF:-1 tvg-id="KBS1",KBS1 HD\nhttp://c/kbs1.m3u8\n'  # 같은 tvg-id -> 예비 주소
                       '#EXTINF:-1,MBC\nhttp://b/mbc.m3u8\n', "TV", "s")
    merged = iptv.merge_channels([a, b])
    assert [c["name"] for c in merged] == ["KBS1", "YTN", "MBC"]
    # 한 목록 안에서는 합치지 않는다
    same = iptv.parse_m3u('#EXTINF:-1,A\nhttp://x/1\n#EXTINF:-1,B\nhttp://x/1\n', "TV", "s")
    assert len(iptv.merge_channels([same])) == 2
    kbs = merged[0]
    assert kbs["url"] == "http://a/kbs1.m3u8" and kbs["id"] == "s:kbs1"
    assert kbs["alts"] == [{"url": "http://b/kbs1.m3u8", "referer": "http://b/"}, {"url": "http://c/kbs1.m3u8"}]
    assert kbs["logo"] == "http://b/logo.png"     # 앞 목록에 로고가 없으면 뒤 목록 것
    assert merged[1]["alts"] == [] and len({c["id"] for c in merged}) == 3


def test_merge_epg_and_url_list():
    one = {"names": {"kbs1": "K"}, "icons": {}, "programmes": {"K": [[1, 2, "a", "", ""]]}}
    two = {"names": {"kbs1": "X", "mbc": "M"}, "icons": {"M": "i"}, "programmes": {"K": [[9, 9, "b", "", ""]], "M": []}}
    out = iptv.merge_epg([one, two])
    assert out["names"] == {"kbs1": "K", "mbc": "M"} and out["programmes"]["K"][0][2] == "a" and "M" in out["programmes"]
    assert config_store.url_list(" http://a \n\n# 메모\nhttp://b\nhttp://a\n") == ["http://a", "http://b"]
    assert config_store.url_list(["http://a", " "]) == ["http://a"]


def test_server_merges_several_m3u(monkeypatch, tmp_path):
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server

    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))

    def text(body):
        async def handler(request):
            return web.Response(text=body)
        return handler

    async def run():
        origin = web.Application()
        origin.router.add_get("/a.m3u", text("#EXTM3U\n#EXTINF:-1,KBS1\nhttp://a/1.m3u8\n"))
        origin.router.add_get("/b.m3u", text("#EXTM3U\n#EXTINF:-1,KBS1\nhttp://b/1.m3u8\n#EXTINF:-1,MBC\nhttp://b/2.m3u8\n"))
        origin.router.add_get("/epg1.xml", text(XMLTV))
        origin_srv = TestServer(origin)
        await origin_srv.start_server()
        base = str(origin_srv.make_url("")).rstrip("/")
        cfg = config_store.load()
        config_store.replace_sources(cfg, [{"id": "tv", "type": "iptv", "name": "합침",
                                            "m3u": f"{base}/a.m3u\n{base}/dead.m3u\n{base}/b.m3u",
                                            "epg": f"{base}/epg1.xml\n{base}/dead.xml"}])
        config_store.save(cfg)
        client = TestClient(TestServer(server.create_app(require_auth=False)))
        await client.start_server()
        try:
            body = await (await client.get("/api/iptv/tv/channels")).json()
            assert [c["name"] for c in body["channels"]] == ["KBS1", "MBC"]
            assert body["channels"][0]["alts"] == [{"url": "http://b/1.m3u8"}]
            assert len(body["errors"]) == 1 and "dead.m3u" in body["errors"][0]
            epg = await (await client.get("/api/iptv/tv/epg")).json()
            assert epg["success"] and epg["names"]["ytn뉴스"] == "ytn.news" and len(epg["errors"]) == 1
        finally:
            await client.close()
            await origin_srv.close()

    asyncio.run(run())


def test_radio_helpers():
    from ym_player import radio
    assert radio.parse_icy_block(b"StreamTitle='Miles Davis - So What';StreamUrl='';\0\0") == "Miles Davis - So What"
    assert radio.parse_icy_block("StreamTitle='김광석 - 바람이 불어오는 곳';".encode("utf-8")) == "김광석 - 바람이 불어오는 곳"
    assert radio.split_title("Miles Davis - So What") == {"artist": "Miles Davis", "title": "So What", "text": "Miles Davis - So What"}
    assert radio.split_title("Station ID")["artist"] == ""
    st = {"name": "Jazz24", "homepage": "https://www.jazz24.org/"}
    assert radio.looks_like_station_text(radio.split_title("Jazz24"), st)
    assert radio.looks_like_station_text(radio.split_title("Listen at jazz24.org"), st)
    assert not radio.looks_like_station_text(radio.split_title("Bill Evans - Peace Piece"), st)
    results = [{"artistName": "Someone Else", "trackName": "Other", "artworkUrl100": "https://a/x/100x100bb.jpg"},
               {"artistName": "Bill Evans", "trackName": "Peace Piece (Remastered)", "artworkUrl100": "https://a/y/100x100bb.jpg"}]
    assert radio.pick_artwork("Bill Evans", "Peace Piece", results) == "https://a/y/600x600bb.jpg"
    assert radio.pick_artwork("Nobody", "Nothing at all", results[:1]) == ""


def test_radio_source_and_now_playing(monkeypatch, tmp_path):
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server

    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    # 기본 방송국 / 잘못된 주소는 버림 / 다 버려지면 오류
    cfg = config_store.replace_sources({"client_id": "x", "sources": []}, [{"type": "radio", "name": "라디오"}])
    assert [s["id"] for s in cfg["sources"][0]["stations"]] == ["relaxingjazz", "radio-swiss-jazz", "jazz24"]
    with pytest.raises(ValueError):
        config_store.replace_sources(cfg, [{"type": "radio", "stations": [{"name": "x", "url": "ftp://x"}]}])

    async def icy(request):
        assert request.headers.get("Icy-MetaData") == "1"
        meta = b"StreamTitle='Bill Evans - Peace Piece';"
        meta += b"\0" * (-len(meta) % 16)
        body = b"\xff" * 1000 + bytes([len(meta) // 16]) + meta + b"\xff" * 1000
        return web.Response(body=body, headers={"icy-metaint": "1000", "Content-Type": "audio/mpeg"})

    async def plain(request):
        return web.Response(body=b"\xff" * 5000, headers={"Content-Type": "audio/mpeg"})

    async def run():
        origin = web.Application()
        origin.router.add_get("/live", icy)
        origin.router.add_get("/plain", plain)
        osrv = TestServer(origin)
        await osrv.start_server()
        base = str(osrv.make_url("")).rstrip("/")
        cfg = config_store.load()
        config_store.replace_sources(cfg, [{"id": "rd", "type": "radio", "name": "라디오", "stations": [
            {"name": "재즈", "url": f"{base}/live"}, {"name": "조용한 방송", "url": f"{base}/plain"}]}])
        config_store.save(cfg)
        monkeypatch.setattr(server, "_radio_art", _fake_art)
        client = TestClient(TestServer(server.create_app(require_auth=False)))
        await client.start_server()
        try:
            j = await (await client.get("/api/radio/rd/now")).json()
            assert j["now"]["재즈"]["text"] == "Bill Evans - Peace Piece" and j["now"]["재즈"]["art"] == "ART"
            assert j["now"]["조용한-방송"]["text"] == "" and j["now"]["조용한-방송"]["art"] == ""
            assert (await client.get("/api/radio/nope/now")).status == 404
        finally:
            await client.close()
            await osrv.close()

    async def _fake_art(app, artist, title):
        return "ART" if (artist, title) == ("Bill Evans", "Peace Piece") else ""

    asyncio.run(run())
