"""Plex 새 동영상 -> 텔레그램 채널 자동 올리기: 기준 잡기, 새 항목만, 받아서 올리기, 캡션, 한도 넘으면 건너뛰기, 실패 다시."""
import asyncio

import pytest

from ym_player import config_store, plex_to_tg

pytest.importorskip("aiohttp")
pytest.importorskip("telethon")


def test_caption_fields():
    ep = {"type": "episode", "title": "시작", "grandparentTitle": "호프", "parentIndex": 1, "index": 2, "summary": "줄거리",
          "Genre": [{"tag": "드라마"}, {"tag": "SF 판타지"}], "Media": [{"Part": [{"file": "/m/Hope.S01E02.mkv"}]}]}
    f = plex_to_tg.item_fields(ep, "드라마")
    assert f["name"] == "호프 S01E02 - 시작" and f["filename"] == "Hope.S01E02.mkv" and f["tags"] == "#드라마 #SF_판타지"
    assert plex_to_tg.make_caption("{name}\n\n{summary}\n{없는칸}", f) == "호프 S01E02 - 시작\n\n줄거리"
    assert plex_to_tg.make_caption("{name", f) == "호프 S01E02 - 시작\n\n줄거리"      # 틀을 잘못 쓰면 기본 틀로
    assert plex_to_tg.item_fields({"type": "movie", "title": "호프", "year": 2026})["name"] == "호프 (2026)"
    with pytest.raises(ValueError):
        plex_to_tg.clean_settings({"rules": [{"plex": "px", "section": "1", "tg": "tg", "channel": "abc"}]})


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    return tmp_path


class FakeTg:
    def __init__(self):
        self.sent = []
        self.fail = 0

    async def authorized_client(self):
        return self

    async def peer(self, channel):
        return ("peer", channel)

    async def send_file(self, peer, path, caption="", thumb=None, attributes=None, progress_callback=None, **kw):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("일시 오류")
        with open(path, "rb") as f:
            body = f.read()
        if progress_callback:
            progress_callback(len(body), len(body))
        thumb_data = open(thumb, "rb").read() if thumb else None
        self.sent.append({"peer": peer, "name": path.rsplit("/", 1)[-1], "body": body, "caption": caption, "thumb": thumb_data,
                          "attrs": [type(a).__name__ for a in attributes or []], "stream": kw.get("supports_streaming")})


def test_mirror_uploads_only_new_items(data, tmp_path):
    from aiohttp import ClientSession, web
    from aiohttp.test_utils import TestServer

    local = tmp_path / "Hope.2026.mp4"
    local.write_bytes(b"LOCAL-MOVIE")
    items = [{"ratingKey": "1", "type": "movie", "title": "옛 영화", "year": 2020, "addedAt": 100,
              "Media": [{"Part": [{"key": "/library/parts/1/file.mkv", "file": "/nowhere/old.mkv", "size": 3}]}]}]
    meta = {}

    def add(rk, title, added, file, size, **kw):
        m = {"ratingKey": rk, "type": "movie", "title": title, "year": 2026, "addedAt": added, "summary": "새 영화", "thumb": "/t/1",
             "duration": 60000, "Media": [{"width": 1920, "height": 1080, "Part": [{"key": f"/library/parts/{rk}/file", "file": file, "size": size}]}], **kw}
        items.insert(0, m)
        meta[rk] = m

    async def sections(request):
        return web.json_response({"MediaContainer": {"Directory": [{"key": "1", "title": "영화", "type": "movie"}]}})

    async def all_items(request):
        assert request.query["sort"] == "addedAt:desc" and request.query["type"] == "1"
        return web.json_response({"MediaContainer": {"Metadata": items}})

    async def metadata(request):
        return web.json_response({"MediaContainer": {"Metadata": [meta[request.match_info["rk"]]]}})

    async def part(request):
        assert request.headers.get("X-Plex-Token") == "TOKEN"
        return web.Response(body=b"REMOTE-EPISODE-BYTES")

    async def photo(request):
        return web.Response(body=b"\xff\xd8thumb", content_type="image/jpeg")

    plex = web.Application()
    plex.router.add_get("/library/sections", sections)
    plex.router.add_get("/library/sections/1/all", all_items)
    plex.router.add_get("/library/metadata/{rk}", metadata)
    plex.router.add_get("/library/parts/{rk}/file", part)
    plex.router.add_get("/photo/:/transcode", photo)

    async def main():
        srv = TestServer(plex)
        await srv.start_server()
        http = ClientSession()
        src = {"id": "px", "type": "plex", "url": str(srv.make_url("")).rstrip("/"), "token": "TOKEN"}
        cfg = {"client_id": "c", "sources": [src]}
        tg = FakeTg()

        async def plex_json(c, s, path, params=None):
            async with http.get(s["url"] + path, params=params) as r:
                return await r.json()
        mirror = plex_to_tg.Mirror(plex_json, lambda sid: (cfg, src if sid == "px" else None), lambda sid: tg, lambda: http)
        config_store.save({**cfg, "plex_to_tg": {"rules": [{"id": "r1", "plex": "px", "section": "1", "section_title": "영화",
                                                             "tg": "tg", "channel": "-100123", "channel_title": "내 채널"}]}})
        try:
            st = plex_to_tg.load_settings()
            await mirror.check_all(st)                  # 처음: 기준만 (옛 영화는 안 올림)
            assert mirror.queue == [] and plex_to_tg.load_state()["rules"]["r1"]["baseline"] == 100
            add("2", "호프", 200, str(local), len(b"LOCAL-MOVIE"))                     # 이 컴퓨터에 있는 파일
            add("3", "먼 영화", 300, "/nowhere/far.mkv", len(b"REMOTE-EPISODE-BYTES"))  # Plex에서 받아 와야 하는 파일
            add("4", "큰 영화", 400, "/nowhere/big.mkv", 3 * 1024 ** 3)                 # 2GB 넘음
            await mirror.check_all(st)
            assert [j["key"] for j in mirror.queue] == ["2", "3", "4"]
            tg.fail = 1                                  # 첫 번째는 한 번 실패
            await mirror.run_queue(st)
            assert [s["name"] for s in tg.sent] == ["far.mkv"]
            far = tg.sent[0]
            assert far["body"] == b"REMOTE-EPISODE-BYTES" and far["peer"] == ("peer", -100123) and far["thumb"] == b"\xff\xd8thumb"
            assert far["caption"] == "먼 영화 (2026)\n\n새 영화" and far["attrs"] == ["DocumentAttributeVideo", "DocumentAttributeFilename"]
            assert far["stream"] is False
            hist = plex_to_tg.load_state()["history"]
            assert [(h["title"], h["ok"]) for h in hist] == [("큰 영화 (2026)", False), ("먼 영화 (2026)", True), ("호프 (2026)", False)]
            assert hist[0]["note"].startswith("건너뜀")
            # 다음 확인: 실패한 호프만 다시, 올린 것·건너뛴 것은 다시 안 함
            await mirror.check_all(st)
            assert [j["key"] for j in mirror.queue] == ["2"]
            await mirror.run_queue(st)
            assert tg.sent[-1]["name"] == "Hope.2026.mp4" and tg.sent[-1]["body"] == b"LOCAL-MOVIE" and tg.sent[-1]["stream"] is True
            await mirror.check_all(st)
            assert mirror.queue == []
            # 시험: 가장 최근 것 하나 (올린 적 있어도)
            assert await mirror.test_latest("r1") == "큰 영화 (2026)"
            assert not any(p for p in __import__("os").listdir(data / "plex_to_tg_tmp"))   # 임시 파일은 지움
        finally:
            await http.close()
            await srv.close()
    asyncio.run(main())


def test_admin_api(data, monkeypatch):
    from aiohttp import CookieJar
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import auth, server
    monkeypatch.delenv("MP_USER", raising=False)
    monkeypatch.delenv("MP_PASSWORD", raising=False)
    auth._fails.clear()
    auth.create_user("boss", "secret1", "admin")
    auth.create_user("kid", "secret2", "user")

    async def main():
        c = TestClient(TestServer(server.create_app(require_auth=True)), cookie_jar=CookieJar(unsafe=True))
        await c.start_server()
        try:
            await c.post("/api/auth/login", json={"username": "boss", "password": "secret1"})
            r = await c.put("/api/admin/plex-tg", json={"interval": 1, "rules": [{"plex": "px", "section": "1", "tg": "tg", "channel": "-100"}]})
            body = await r.json()
            assert r.status == 200 and body["settings"]["interval"] == 2 and body["settings"]["rules"][0]["id"]
            assert (await c.put("/api/admin/plex-tg", json={"rules": [{"plex": "px"}]})).status == 400
            assert (await (await c.get("/api/admin/plex-tg/options")).json())["plex"] == []
            assert (await c.post("/api/admin/plex-tg/test", json={"rule": "zz"})).status == 400
            await c.post("/api/auth/logout")
            await c.post("/api/auth/login", json={"username": "kid", "password": "secret2"})
            assert (await c.get("/api/admin/plex-tg")).status == 403
        finally:
            await c.close()
    asyncio.run(main())
