"""팟캐스트 소스 - RSS 읽기, 설정 검사, 들은 위치, 서버 API(목록·상세·홈·중계·사용자별 범위)."""
import asyncio

import pytest
from aiohttp import web

from ym_player import auth, config_store, podcast

pytest.importorskip("aiohttp")

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
  <title>테스트 라디오쇼</title>
  <itunes:author>진행자</itunes:author>
  <description><![CDATA[<p>소개 <b>글</b></p><p>둘째 줄&amp;끝</p>]]></description>
  <itunes:image href="https://img.example/show.jpg"/>
  <itunes:type>serial</itunes:type>
  <link>https://example.com/show</link>
  <item>
    <title>1화 시작</title>
    <guid>ep-1</guid>
    <pubDate>Mon, 05 Oct 2026 09:00:00 +0900</pubDate>
    <itunes:duration>1:02:03</itunes:duration>
    <itunes:episode>1</itunes:episode>
    <enclosure url="{base}/a1.mp3" type="audio/mpeg" length="1000"/>
  </item>
  <item>
    <title>2화 계속</title>
    <guid>ep-2</guid>
    <pubDate>Mon, 12 Oct 2026 09:00:00 +0900</pubDate>
    <itunes:duration>600</itunes:duration>
    <content:encoded><![CDATA[<p>2화 설명</p>]]></content:encoded>
    <itunes:image href="https://img.example/ep2.jpg"/>
    <enclosure url="{base}/a2.mp3" type="audio/mpeg" length="1000"/>
  </item>
  <item>
    <title>오디오 없는 공지</title>
    <guid>note</guid>
  </item>
  <item>
    <title>같은 guid</title>
    <guid>ep-2</guid>
    <enclosure url="{base}/dup.mp3" type="audio/mpeg"/>
  </item>
</channel>
</rss>"""


def test_parse_feed():
    show = podcast.parse_feed(RSS.replace("{base}", "https://cdn.example").encode())
    assert show["title"] == "테스트 라디오쇼" and show["author"] == "진행자" and show["serial"]
    assert show["summary"] == "소개 글\n둘째 줄&끝"
    assert show["image"] == "https://img.example/show.jpg"
    eps = show["episodes"]
    assert [e["title"] for e in eps] == ["2화 계속", "1화 시작"]        # 오디오 없는 것·같은 guid는 빼고, 최신부터
    assert eps[1]["duration"] == 3723 and eps[1]["episode"] == 1 and eps[0]["duration"] == 600
    assert eps[0]["summary"] == "2화 설명" and eps[0]["image"] == "https://img.example/ep2.jpg"
    assert eps[0]["date"] > eps[1]["date"] > 0
    with pytest.raises(ValueError):
        podcast.parse_feed(b"<html><body>hi</body></html>")
    with pytest.raises(ValueError):
        podcast.parse_feed(b"not xml")


def test_helpers():
    assert podcast.parse_duration("62:03") == 3723
    assert podcast.parse_duration("1:00:00") == 3600 and podcast.parse_duration("90") == 90 and podcast.parse_duration("x") == 0
    feeds = podcast.clean_feeds([{"url": " https://a.example/rss "}, "https://a.example/rss", {"url": "ftp://x"}, {"url": "http://b.example/f", "title": "B"}])
    assert [f["url"] for f in feeds] == ["https://a.example/rss", "http://b.example/f"]
    assert feeds[0]["id"] == podcast.feed_id("https://a.example/rss") and len(feeds[0]["id"]) == 10
    src = config_store._clean_source({"type": "podcast", "feeds": ["https://a.example/rss"]}, None)
    assert src["name"] == "팟캐스트" and src["feeds"][0]["url"] == "https://a.example/rss"


def test_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    podcast.save_progress("u", "s", "f", "e1", 100, 600)
    podcast.save_progress("u", "s", "f", "e2", 590, 600)          # 남은 10초 -> 다 들음
    podcast.save_progress("u", "s", "f", "e4", 58, 90)            # 짧은 에피소드는 남은 5%(4.5초)까지
    assert not podcast.progress_of("u", "s")[("f", "e4")]["watched"]
    podcast.save_progress("u", "s", "f", "e4", 87, 90)
    assert podcast.progress_of("u", "s")[("f", "e4")]["watched"]
    podcast.save_progress("v", "s", "f", "e1", 5, 600)
    p = podcast.progress_of("u", "s")
    assert p[("f", "e1")]["position"] == 100 and not p[("f", "e1")]["watched"]
    assert p[("f", "e2")]["watched"] and p[("f", "e2")]["position"] == 0
    podcast.save_progress("u", "s", "f", "e1", 120)                 # 길이를 안 보내면 예전 길이 그대로
    assert podcast.progress_of("u", "s")[("f", "e1")]["duration"] == 600
    podcast.set_watched_many("u", "s", "f", ["e1", "e3"], True)
    assert all(x["watched"] for x in podcast.progress_of("u", "s").values())
    assert len(podcast.progress_of("v", "s")) == 1                  # 사용자마다 따로
    podcast.forget_source("s")
    assert podcast.progress_of("u", "s") == {}


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


def test_podcast_api(data):
    from aiohttp import CookieJar
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server
    audio = bytes(range(256)) * 4
    hits = {"feed": 0}

    async def feed(request):
        hits["feed"] += 1
        return web.Response(text=RSS.replace("{base}", f"http://127.0.0.1:{request.url.port}"), content_type="application/rss+xml")

    async def picky_feed(request):           # 브라우저 UA는 404로 거절하고 팟캐스트 앱 UA만 받아 주는 곳
        if "YMPlayer" not in request.headers.get("User-Agent", ""):
            return web.Response(status=404, text="not found")
        return await feed(request)

    async def gone_feed(request):
        return web.Response(status=404, text="not found")

    async def bad_feed(request):
        return web.Response(text="<html>nope</html>", content_type="text/html")

    async def mp3(request):
        rng = request.headers.get("Range", "")
        if rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            a, b = int(a), int(b or len(audio) - 1)
            return web.Response(status=206, body=audio[a:b + 1], headers={
                "Content-Range": f"bytes {a}-{b}/{len(audio)}", "Accept-Ranges": "bytes", "Content-Type": "application/octet-stream"})
        return web.Response(body=audio, content_type="application/octet-stream")

    async def main():
        up = web.Application()
        up.router.add_get("/feed.xml", feed)
        up.router.add_get("/bad.xml", bad_feed)
        up.router.add_get("/picky.xml", picky_feed)
        up.router.add_get("/gone.xml", gone_feed)
        up.router.add_get("/{name}.mp3", mp3)
        upstream = TestServer(up)
        await upstream.start_server()
        base = f"http://127.0.0.1:{upstream.port}"
        url, bad = f"{base}/feed.xml", f"{base}/bad.xml"
        fid, bid = podcast.feed_id(url), podcast.feed_id(bad)
        cfg = config_store.load()
        cfg["sources"] = [config_store._clean_source({"id": "pd", "type": "podcast", "feeds": [url, bad]}, None)]
        config_store.save(cfg)
        c = TestClient(TestServer(server.create_app(require_auth=True)), cookie_jar=CookieJar(unsafe=True))
        await c.start_server()
        try:
            await c.post("/api/auth/login", json={"username": "boss", "password": "secret1"})
            shows = (await (await c.get("/api/podcast/pd/shows")).json())["shows"]
            assert [s["title"] for s in shows] == ["테스트 라디오쇼", bad]
            assert shows[0]["count"] == 2 and shows[0]["unplayed"] == 2 and shows[0]["serial"]
            assert not shows[1]["loaded"] and "HTML" in shows[1]["error"]

            d = await (await c.get(f"/api/podcast/pd/show/{fid}")).json()
            eps = d["episodes"]
            assert [e["title"] for e in eps] == ["2화 계속", "1화 시작"] and "url" not in eps[0]   # 원래 주소는 안 내보냄
            e1, e2 = eps[1]["id"], eps[0]["id"]

            # 오디오 중계: Range 그대로, octet-stream이면 피드의 형식으로
            r = await c.get(f"/api/podcast/pd/audio/{fid}/{e1}", headers={"Range": "bytes=10-19"})
            assert r.status == 206 and await r.read() == audio[10:20] and r.headers["Content-Type"] == "audio/mpeg"
            assert r.headers["Content-Range"] == f"bytes 10-19/{len(audio)}"
            assert (await c.get(f"/api/podcast/pd/audio/{fid}/0123456789ab")).status == 404

            # 들은 위치 -> 이어 듣기, 다 들으면 안 들은 수가 준다
            assert (await (await c.post("/api/podcast/pd/progress", json={"feed": fid, "ep": e1, "position": 300, "duration": 3723})).json())["success"]
            assert (await c.post("/api/podcast/pd/progress", json={"feed": fid, "ep": "../x", "position": 1})).status == 400
            home = await (await c.get("/api/podcast/pd/home")).json()
            assert [x["id"] for x in home["continue"]] == [e1] and home["continue"][0]["position"] == 300
            assert home["continue"][0]["show"] == "테스트 라디오쇼"
            ep = await (await c.get(f"/api/podcast/pd/episode/{fid}/{e1}")).json()
            assert ep["newer"]["id"] == e2 and ep["older"] is None and ep["episode"]["position"] == 300
            await c.post("/api/podcast/pd/progress", json={"feed": fid, "ep": e2, "position": 600, "duration": 600})
            assert (await (await c.get("/api/podcast/pd/shows")).json())["shows"][0]["unplayed"] == 1
            await c.post("/api/podcast/pd/watched", json={"feed": fid, "watched": True})
            assert (await (await c.get("/api/podcast/pd/shows")).json())["shows"][0]["unplayed"] == 0

            # 캐시: 다시 물어도 피드를 또 받지 않고, refresh=1이면 받는다
            n = hits["feed"]
            await c.get(f"/api/podcast/pd/show/{fid}")
            assert hits["feed"] == n
            await c.get(f"/api/podcast/pd/show/{fid}?refresh=1")
            assert hits["feed"] == n + 1

            # 관리자: 추가 전 확인
            r = await (await c.post("/api/admin/podcast/probe", json={"url": url})).json()
            assert r["title"] == "테스트 라디오쇼" and r["count"] == 2 and r["id"] == fid
            assert (await c.post("/api/admin/podcast/probe", json={"url": bad})).status == 400
            assert (await c.post("/api/admin/podcast/probe", json={"url": "file:///etc/passwd"})).status == 400
            r = await (await c.post("/api/admin/podcast/probe", json={"url": f"{base}/picky.xml"})).json()
            assert r["title"] == "테스트 라디오쇼"                              # 거절하면 팟캐스트 앱처럼 한 번 더
            r = await c.post("/api/admin/podcast/probe", json={"url": f"{base}/gone.xml"})
            assert r.status == 400 and "RSS 주소에서 HTTP 404" in (await r.json())["error"]   # 없는 주소는 알아보기 쉬운 말로

            # 일반 사용자: 들은 위치는 따로, 허락된 프로그램만
            await c.post("/api/auth/logout")
            await c.post("/api/auth/login", json={"username": "kid", "password": "secret2"})
            assert (await c.get("/api/admin/podcast/search?q=x")).status == 403
            assert (await (await c.get("/api/podcast/pd/shows")).json())["shows"][0]["unplayed"] == 2
            auth.update_user("kid", access={"sources": ["pd"], "libraries": {"pd": [bid]}})
            assert [s["id"] for s in (await (await c.get("/api/podcast/pd/shows")).json())["shows"]] == [bid]
            assert (await c.get(f"/api/podcast/pd/show/{fid}")).status == 404
            assert (await c.get(f"/api/podcast/pd/audio/{fid}/{e1}")).status == 404
            cfgv = await (await c.get("/api/config")).json()
            assert [f["id"] for f in cfgv["sources"][0]["feeds"]] == [bid]
        finally:
            await c.close()
            await upstream.close()

    asyncio.run(main())
