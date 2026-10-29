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
    # 에피소드 제목이 파일 이름·작품 이름 반복·"Episode 4"면 붙이지 않는다
    ep = lambda title, show: plex_to_tg.item_fields({"type": "episode", "title": title, "grandparentTitle": show, "parentIndex": 1, "index": 4})["name"]
    assert ep("다이아몬드 에이스 actII part 2.E16.261026.1080p-KL", "다이아몬드 에이스 actII -Second Season- 2쿨") == "다이아몬드 에이스 actII -Second Season- 2쿨 S01E04"
    assert ep("Episode 4", "유루유리") == "유루유리 S01E04" and ep("4화", "유루유리") == "유루유리 S01E04"
    assert ep("새로운 시작", "호프") == "호프 S01E04 - 새로운 시작"
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
        self.next_id = 500

    async def authorized_client(self):
        return self

    async def peer(self, channel):
        return ("peer", channel)

    async def send_file(self, peer, path, caption="", thumb=None, attributes=None, progress_callback=None, **kw):
        from types import SimpleNamespace
        if self.fail:
            self.fail -= 1
            raise RuntimeError("일시 오류")
        with open(path, "rb") as f:
            body = f.read()
        if progress_callback:
            progress_callback(len(body), len(body))
        names = [a.file_name for a in attributes or [] if type(a).__name__ == "DocumentAttributeFilename"]
        self.sent.append({"peer": peer, "name": names[0] if names else path.rsplit("/", 1)[-1], "body": body, "caption": caption,
                          "thumb": open(thumb, "rb").read() if thumb else None, "doc": kw.get("force_document"),
                          "attrs": [type(a).__name__ for a in attributes or []], "stream": kw.get("supports_streaming")})
        self.next_id += 1
        return SimpleNamespace(id=self.next_id)


def _video(path, seconds=6):
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seconds}",
                    "-c:v", "libx264", "-g", "25", "-pix_fmt", "yuv420p", str(path)], check=True)


SRT = "1\n00:00:01,000 --> 00:00:02,000\n첫 자막\n\n2\n00:00:04,500 --> 00:00:05,500\n뒤 자막\n"


def test_mirror_uploads_new_items(data, tmp_path, monkeypatch):
    import shutil as _sh
    from aiohttp import ClientSession, web
    from aiohttp.test_utils import TestServer

    from ym_player import telegram_src
    if not _sh.which("ffmpeg"):
        pytest.skip("ffmpeg 없음")
    telegram_src._migrated.discard(str(data))
    local = tmp_path / "Hope.2026.mp4"
    _video(local, 2)
    big = tmp_path / "Big.mkv"
    _video(big, 6)
    monkeypatch.setattr(plex_to_tg, "PART_LIMIT", int(big.stat().st_size * 0.75 / 0.97))   # max_gb=1이면 한도가 파일의 75% -> 2조각
    items = [{"ratingKey": "1", "type": "movie", "title": "옛 영화", "year": 2020, "addedAt": 100,
              "Media": [{"Part": [{"key": "/library/parts/1/file.mkv", "file": "/nowhere/old.mkv", "size": 3}]}]}]
    meta = {}

    def add(rk, title, added, file, size, streams=(), **kw):
        m = {"ratingKey": rk, "type": "movie", "title": title, "year": 2026, "addedAt": added, "summary": "새 영화", "thumb": "/t/1",
             "art": "/a/1", "duration": 6000, "Genre": [{"tag": "드라마"}], "Role": [{"tag": "배우", "role": "주인공", "thumb": "https://img/x.jpg"}],
             "Media": [{"width": 320, "height": 240, "Part": [{"key": f"/library/parts/{rk}/file", "file": file, "size": size,
                                                                "Stream": list(streams)}]}], **kw}
        items.insert(0, m)
        meta[rk] = m
        return m

    async def sections(request):
        return web.json_response({"MediaContainer": {"Directory": [{"key": "1", "title": "영화", "type": "movie"}]}})

    async def all_items(request):
        assert request.query["sort"] == "addedAt:desc" and request.query["type"] == "1"
        return web.json_response({"MediaContainer": {"Metadata": items}})

    async def metadata(request):
        return web.json_response({"MediaContainer": {"Metadata": [meta[request.match_info["rk"]]]}})

    async def part(request):
        assert request.headers.get("X-Plex-Token") == "TOKEN"
        return web.Response(body=big.read_bytes())

    async def stream(request):
        return web.Response(text=SRT)

    async def photo(request):
        return web.Response(body=b"\xff\xd8thumb", content_type="image/jpeg")

    plex = web.Application()
    plex.router.add_get("/library/sections", sections)
    plex.router.add_get("/library/sections/1/all", all_items)
    plex.router.add_get("/library/metadata/{rk}", metadata)
    plex.router.add_get("/library/parts/{rk}/file", part)
    plex.router.add_get("/library/streams/{sid}", stream)
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
        config_store.save({**cfg, "plex_to_tg": {"max_gb": 1, "rules": [{"id": "r1", "plex": "px", "section": "1", "section_title": "영화",
                                                                         "tg": "tg", "channel": "-100123", "channel_title": "내 채널", "burn_subs": False}]}})
        try:
            st = plex_to_tg.load_settings()
            assert st["rules"][0]["wait_subs"] is True and st["rules"][0]["burn_subs"] is False
            await mirror.check_all(st)                  # 처음: 기준만 (옛 영화는 안 올림)
            assert mirror.queue == [] and plex_to_tg.load_state()["rules"]["r1"]["baseline"] == 100
            small = local.stat().st_size
            add("2", "호프", 200, str(local), small, [{"streamType": 3, "codec": "srt", "index": 2}])   # 내장 자막, 이 컴퓨터의 파일
            nosub = add("3", "자막 없는 영화", 300, str(local), small)                                     # 자막이 아직 없음
            add("4", "큰 영화", 400, "/nowhere/Big.mkv", big.stat().st_size,                               # Plex에서 받아 와서 나눠야 함
                [{"streamType": 3, "codec": "srt", "key": "/library/streams/9", "languageCode": "kor"}])
            await mirror.check_all(st)
            assert [j["key"] for j in mirror.queue] == ["2", "3", "4"]
            tg.fail = 1                                  # 첫 번째는 한 번 실패
            await mirror.run_queue(st)
            names = [x["name"] for x in tg.sent]
            assert names == ["Big part1.mkv", "Big part1.ko.srt", "Big part2.mkv", "Big part2.ko.srt"], names
            p1, s1, p2, s2 = tg.sent
            assert p1["caption"] == "큰 영화 (2026)\n(1/2)\n\n새 영화" and p1["thumb"] == b"\xff\xd8thumb" and p1["stream"] is False
            assert s1["doc"] and "첫 자막" in s1["body"].decode() and "뒤 자막" not in s1["body"].decode()
            assert "뒤 자막" in s2["body"].decode() and "00:00:01,500" in s2["body"].decode()   # 뒤 조각 자막은 3초 당겨짐
            assert len(p1["body"]) + len(p2["body"]) >= big.stat().st_size * 0.9
            state = plex_to_tg.load_state()
            assert list(state["rules"]["r1"]["waiting"]) == ["3"]
            assert [(h["title"], h["ok"], h["note"]) for h in state["history"]] == [("큰 영화 (2026)", True, "2개로 나눠 올림, 자막 파일 2개 첨부"), ("호프 (2026)", False, "일시 오류")]
            # 올린 두 조각 메시지에 Plex 작품 정보
            m, eps = telegram_src.plex_meta_for("tg", -100123, msg=501)
            assert m["title"] == "큰 영화" and m["genres"] == ["드라마"] and m["cast"][0]["thumb"] == "https://img/x.jpg"
            assert m["poster"].startswith("/api/tg/tg/plex-art/") and telegram_src.plex_meta_for("tg", -100123, msg=503)
            art = data / "telegram_thumbs" / "tg" / "plex" / m["poster"].rsplit("/", 1)[1]
            assert art.read_bytes() == b"\xff\xd8thumb"
            # 다음 확인: 실패한 호프 다시, 자막 없는 영화는 계속 기다림
            await mirror.check_all(st)
            assert sorted(j["key"] for j in mirror.queue) == ["2", "3"]
            await mirror.run_queue(st)
            assert tg.sent[-1]["name"] == "Hope.2026.mp4" and tg.sent[-1]["stream"] is True
            assert list(plex_to_tg.load_state()["rules"]["r1"]["waiting"]) == ["3"]
            # 자막이 생기면 올림
            nosub["Media"][0]["Part"][0]["Stream"] = [{"streamType": 3, "codec": "ass", "index": 3}]
            await mirror.check_all(st)
            await mirror.run_queue(st)
            assert tg.sent[-1]["name"] == "Hope.2026.mp4" and plex_to_tg.load_state()["rules"]["r1"]["waiting"] == {}
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
            assert (await c.post("/api/admin/plex-tg/item", json={"rule": "r", "key": "1", "action": "boom"})).status == 400
            assert (await c.post("/api/admin/plex-tg/item", json={"rule": "r", "key": "1", "action": "upload"})).status == 400
            await c.post("/api/auth/logout")
            await c.post("/api/auth/login", json={"username": "kid", "password": "secret2"})
            assert (await c.get("/api/admin/plex-tg")).status == 403
        finally:
            await c.close()
    asyncio.run(main())


def test_shift_subtitles():
    srt = "1\n00:00:01,000 --> 00:00:02,000\n첫\n\n2\n00:00:59,000 --> 00:01:02,500\n걸침\n\n3\n00:02:00,000 --> 00:02:01,000\n뒤\n"
    cut = plex_to_tg.shift_srt(srt, 60, 30)
    assert cut == "1\n00:00:00,000 --> 00:00:02,500\n걸침\n"
    ass = "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" \
          "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,첫, 쉼표\nDialogue: 0,0:01:05.50,0:01:07.00,Default,,0,0,0,,뒤\n"
    out = plex_to_tg.shift_ass(ass, 60, 60)
    assert "첫" not in out and "Dialogue: 0,0:00:05.50,0:00:07.00,Default,,0,0,0,,뒤" in out and out.startswith("[Events]")


def test_tg_meta_uses_plex_info_without_tmdb(data, monkeypatch):
    import sys
    sys.path.insert(0, __import__("os").path.dirname(__file__))
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server, telegram_src
    telegram_src._migrated.discard(str(data))
    cfg = config_store.load()
    config_store.replace_sources(cfg, [{"id": "tg", "type": "telegram", "name": "텔레", "api_id": "1", "api_hash": "h",
                                        "channels": [{"id": 777, "title": "드라마"}]}])
    config_store.save(cfg)
    with telegram_src.db() as conn:
        conn.executemany(
            "INSERT INTO videos(source,channel,msg,title,caption,file_name,mime,duration,size,width,height,date,has_thumb,kind)"
            " VALUES('tg',777,?,?,'',?,'video/mp4',60,1,1,1,?,0,'video')",
            [(1, "호프 S01E01 - 시작", "Hope.S01E01.mkv", "2026-10-01T00:00:00+00:00"),
             (2, "호프 S01E02 - 다음", "Hope.S01E02.mkv", "2026-10-02T00:00:00+00:00")])
    telegram_src.link_series("tg", 777)
    show = {"kind": "tv", "title": "호프", "overview": "작품 줄거리", "poster": "/api/tg/tg/plex-art/0123456789abcdef.jpg", "genres": ["SF"]}
    telegram_src.plex_meta_put("tg", 777, [1], show, {"season": 1, "ep": 1, "name": "시작", "overview": "1화 줄거리", "still": ""})
    telegram_src.plex_meta_put("tg", 777, [2], show, {"season": 1, "ep": 2, "name": "다음", "overview": "2화 줄거리", "still": ""})
    art = data / "telegram_thumbs" / "tg" / "plex"
    art.mkdir(parents=True)
    (art / "0123456789abcdef.jpg").write_bytes(b"\xff\xd8P")

    async def main():
        c = TestClient(TestServer(server.create_app(require_auth=False)))
        await c.start_server()
        try:
            key = telegram_src.series_of("tg", 777, 1)["key"]
            d = await (await c.get(f"/api/tg/tg/meta/777?series={key}")).json()
            assert d["meta"]["overview"] == "작품 줄거리" and d["episodes"]["1"]["2"]["name"] == "다음" and d["meta"]["seasons"] == [1]
            d = await (await c.get("/api/tg/tg/meta/777/1")).json()
            assert d["meta"]["title"] == "호프" and d["target"]["ep"] == 1
            rows, _ = telegram_src.query_videos("tg", [777], group=True)
            assert rows[0]["series"]["plex_poster"] == show["poster"]
            assert telegram_src.query_videos("tg", [777])[0][0]["plex_poster"] == show["poster"]
            r = await c.get("/api/tg/tg/plex-art/0123456789abcdef.jpg")
            assert r.status == 200 and await r.read() == b"\xff\xd8P"
            assert (await c.get("/api/tg/tg/plex-art/0123456789abcdeg.jpg")).status == 404
        finally:
            await c.close()
    asyncio.run(main())


def test_burn_external_subtitle(data, tmp_path, monkeypatch):
    """영상 안에 자막이 없고 외부 자막(CP949 SRT)만 있으면 화면에 입혀 mp4로 다시 인코딩해서 올린다"""
    import json as _json
    import shutil as _sh
    import subprocess

    from ym_player import telegram_src
    if not _sh.which("ffmpeg"):
        pytest.skip("ffmpeg 없음")
    telegram_src._migrated.discard(str(data))
    src_video = tmp_path / "Ep.S01E01.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:size=320x240:rate=25:duration=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src_video)], check=True)   # 단색이라 글자가 있으면 바로 티가 난다
    sub = tmp_path / "ep.srt"
    sub.write_bytes(SRT.encode("cp949"))
    m = {"ratingKey": "7", "type": "movie", "title": "자막 영화", "year": 2026, "addedAt": 10, "duration": 3000,
         "Media": [{"width": 320, "height": 240, "Part": [{"key": "/p/7", "file": str(src_video), "size": src_video.stat().st_size,
                                                            "Stream": [{"streamType": 3, "codec": "srt", "key": "/s/1", "languageCode": "kor"}]}]}]}
    tg = FakeTg()

    async def plex_json(c, s, path, params=None):
        return {"MediaContainer": {"Metadata": [m]}}

    async def main():
        mirror = plex_to_tg.Mirror(plex_json, lambda sid: ({"client_id": "c"}, {"id": "px", "url": "http://x"}), lambda sid: tg, lambda: None)

        async def fetch(cfg, src, path, dest):
            _sh.copy(sub, dest)
            return True
        monkeypatch.setattr(mirror, "_fetch", fetch)
        monkeypatch.setattr(mirror, "_art", lambda *a: asyncio.sleep(0))
        rule = plex_to_tg.clean_settings({"rules": [{"id": "r", "plex": "px", "section": "1", "tg": "tg", "channel": "-1"}]})["rules"][0]
        assert rule["burn_subs"] is True
        note = await mirror.upload(rule, "7", 2)
        monkeypatch.setattr(plex_to_tg, "_KO_FONT", True)
        assert note.startswith("자막 입혀 인코딩")
        assert [x["name"] for x in tg.sent] == ["Ep.S01E01.mp4"] and tg.sent[0]["stream"] is True
        out = tmp_path / "out.mp4"
        out.write_bytes(tg.sent[0]["body"])
        info = _json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)],
                                          capture_output=True, text=True).stdout)
        assert [s["codec_name"] for s in info["streams"]] == ["h264"]
        # 자막이 있는 1~2초 프레임은 원본과 달라야 한다 (화면에 글자)
        def frame(path, t):
            return subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                                   "-pix_fmt", "gray", "-"], capture_output=True).stdout
        def bottom_diff(t):   # 자막이 그려지는 아래쪽 (320x240 중 아래 50줄)만 비교
            a, b = frame(src_video, t), frame(out, t)
            return sum(abs(a[i] - b[i]) for i in range(320 * 180, 320 * 230)) / (320 * 50)
        with_sub, without = bottom_diff(1.5), bottom_diff(2.5)
        assert with_sub > 0.5 and without < 0.2, (with_sub, without)
    asyncio.run(main())


def test_sidecar_subtitles_and_no_font(data, tmp_path, monkeypatch):
    """Plex가 아직 못 읽은 옆 자막 파일도 붙이고(기다림도 풀림), 한글 글꼴이 없으면 입히지 않고 파일로"""
    import shutil as _sh
    if not _sh.which("ffmpeg"):
        pytest.skip("ffmpeg 없음")
    video = tmp_path / "Show.S01E02.mkv"
    _video(video, 2)
    (tmp_path / "Show.S01E02.ko-KR.srt").write_text(SRT, encoding="utf-8")
    (tmp_path / "Show.S01E02.smi").write_text("<SAMI><BODY><SYNC Start=1000><P>자막</SAMI>", encoding="utf-8")
    (tmp_path / "Other.srt").write_text(SRT, encoding="utf-8")
    assert sorted((p.rsplit("/", 1)[1], l, e) for p, l, e in plex_to_tg.sidecar_subs(str(video))) == \
        [("Show.S01E02.ko-KR.srt", "ko", "srt"), ("Show.S01E02.smi", "", "smi")]
    assert plex_to_tg.sub_lang("ko-KR") == "ko" and plex_to_tg.sub_lang("spa") == ""
    m = {"ratingKey": "9", "type": "episode", "title": "둘째", "grandparentTitle": "쇼", "parentIndex": 1, "index": 2, "addedAt": 10,
         "duration": 2000, "Media": [{"Part": [{"key": "/p/9", "file": str(video), "size": video.stat().st_size, "Stream": []}]}]}
    tg = FakeTg()

    async def plex_json(c, s, path, params=None):
        return {"MediaContainer": {"Metadata": [m]}}

    async def main():
        mirror = plex_to_tg.Mirror(plex_json, lambda sid: ({"client_id": "c"}, {"id": "px", "url": "http://x"}), lambda sid: tg, lambda: None)
        monkeypatch.setattr(mirror, "_art", lambda *a: asyncio.sleep(0))
        monkeypatch.setattr(mirror, "_save_meta", lambda *a: asyncio.sleep(0))
        monkeypatch.setattr(plex_to_tg, "_KO_FONT", False)
        rule = plex_to_tg.clean_settings({"rules": [{"id": "r", "plex": "px", "section": "1", "tg": "tg", "channel": "-1"}]})["rules"][0]
        note = await mirror.upload(rule, "9", 2)      # Plex에는 자막이 없지만 옆 파일이 있으니 기다리지 않는다
        assert [x["name"] for x in tg.sent] == ["Show.S01E02.mkv", "Show.S01E02.ko.srt", "Show.S01E02.smi"]
        assert "한글 글꼴이 없어" in note and "자막 파일 2개 첨부" in note
    asyncio.run(main())


def test_soft_subtitle_inside_file_counts(data, tmp_path, monkeypatch):
    """Plex 정보에 자막이 없어도 파일 안에 자막 트랙(소프트 자막)이 있으면 기다리지 않고, 입히지도 않고 그대로 올린다"""
    import shutil as _sh
    import subprocess
    if not (_sh.which("ffmpeg") and _sh.which("ffprobe")):
        pytest.skip("ffmpeg 없음")
    plain = tmp_path / "plain.mkv"
    _video(plain, 2)
    srt = tmp_path / "s.srt"
    srt.write_text(SRT, encoding="utf-8")
    soft = tmp_path / "Anime.S01E03.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(plain), "-i", str(srt), "-map", "0", "-map", "1", "-c", "copy",
                    "-c:s", "ass", "-metadata:s:s:0", "language=kor", str(soft)], check=True)
    items = {"soft": soft, "plain": plain}
    tg = FakeTg()
    cur = {}

    async def plex_json(c, s, path, params=None):
        f = items[cur["k"]]
        return {"MediaContainer": {"Metadata": [{"ratingKey": cur["k"], "type": "episode", "title": "Episode 3", "grandparentTitle": "애니",
                "parentIndex": 1, "index": 3, "duration": 2000,
                "Media": [{"Part": [{"key": "/p/1", "file": str(f), "size": f.stat().st_size, "Stream": []}]}]}]}}

    async def main():
        mirror = plex_to_tg.Mirror(plex_json, lambda sid: ({"client_id": "c"}, {"id": "px", "url": "http://x"}), lambda sid: tg, lambda: None)
        monkeypatch.setattr(mirror, "_art", lambda *a: asyncio.sleep(0))
        monkeypatch.setattr(mirror, "_save_meta", lambda *a: asyncio.sleep(0))
        monkeypatch.setattr(plex_to_tg, "_KO_FONT", True)
        assert await mirror._probe_subs(str(soft)) == [{"codec": "ass", "lang": "kor"}]
        assert await mirror._probe_subs(str(plain)) == []
        rule = plex_to_tg.clean_settings({"rules": [{"id": "r", "plex": "px", "section": "1", "tg": "tg", "channel": "-1"}]})["rules"][0]
        cur["k"] = "plain"
        with pytest.raises(plex_to_tg.Waiting):      # 자막이 정말 없으면 기다림
            await mirror.upload(rule, "plain", 2)
        cur["k"] = "soft"
        note = await mirror.upload(rule, "soft", 2)
        assert [x["name"] for x in tg.sent] == ["Anime.S01E03.mkv"] and note == "내장 자막" and mirror.subs_label == "소프트 자막"
        assert tg.sent[0]["caption"].startswith("애니 S01E03")
    asyncio.run(main())


def test_wait_hours_force_and_drop(data, tmp_path, monkeypatch):
    """자막 기다리기는 시간 단위(예전 일 단위는 x24), 하드섭처럼 알 수 없는 것은 [지금 올리기] / [빼기]"""
    base = {"plex": "px", "section": "1", "tg": "tg", "channel": "-1"}
    rules = plex_to_tg.clean_settings({"rules": [{**base, "id": "a"}, {**base, "id": "b", "wait_days": 2},
                                                 {**base, "id": "c", "wait_hours": 3}, {**base, "id": "d", "wait_days": 0}]})["rules"]
    assert [r["wait_hours"] for r in rules] == [12, 48, 3, 0]
    import shutil as _sh
    if not _sh.which("ffmpeg"):
        pytest.skip("ffmpeg 없음")
    video = tmp_path / "Hard.S01E01.mp4"
    _video(video, 2)                                  # 자막이 그림에 들어 있다고 치는 하드섭 (파일에는 자막 트랙 없음)
    m = {"ratingKey": "5", "type": "episode", "title": "", "grandparentTitle": "하드", "parentIndex": 1, "index": 1, "addedAt": 50,
         "duration": 2000, "Media": [{"Part": [{"key": "/p/5", "file": str(video), "size": video.stat().st_size, "Stream": []}]}]}
    tg = FakeTg()

    async def plex_json(c, s, path, params=None):
        if path == "/library/sections":
            return {"MediaContainer": {"Directory": [{"key": "1", "title": "애니", "type": "show"}]}}
        return {"MediaContainer": {"Metadata": [m]}}
    config_store.save({"client_id": "c", "sources": [], "plex_to_tg": {"rules": [{**base, "id": "r", "wait_hours": 0}]}})

    async def main():
        mirror = plex_to_tg.Mirror(plex_json, lambda sid: ({"client_id": "c"}, {"id": "px", "url": "http://x"}), lambda sid: tg, lambda: None)
        monkeypatch.setattr(mirror, "_art", lambda *a: asyncio.sleep(0))
        monkeypatch.setattr(mirror, "_save_meta", lambda *a: asyncio.sleep(0))
        st = plex_to_tg.load_settings()
        state = plex_to_tg.load_state()
        state["rules"]["r"] = {"baseline": 10, "done": [], "fails": {}}
        plex_to_tg.save_state(state)
        await mirror.check_all(st)
        await mirror.run_queue(st)
        assert tg.sent == [] and list(plex_to_tg.load_state()["rules"]["r"]["waiting"]) == ["5"]   # 0시간 = 계속 기다림
        with pytest.raises(ValueError):
            mirror.force_item("r", "nope")
        assert mirror.force_item("r", "5") == "하드 S01E01"
        await mirror.run_queue(st)
        assert [x["name"] for x in tg.sent] == ["Hard.S01E01.mp4"]
        state = plex_to_tg.load_state()
        assert state["rules"]["r"]["waiting"] == {} and "5" in state["rules"]["r"]["done"] and state["history"][0]["ok"]
        # 빼기: 다시 들어오지 않는다
        m["ratingKey"], m["addedAt"] = "6", 60
        await mirror.check_all(st)
        await mirror.run_queue(st)
        assert list(plex_to_tg.load_state()["rules"]["r"]["waiting"]) == ["6"]
        assert mirror.drop_item("r", "6") == "하드 S01E01"
        await mirror.check_all(st)
        assert mirror.queue == [] and plex_to_tg.load_state()["history"][0]["note"] == "건너뜀: 직접 뺌"
        # 시간이 지나면 자막 없이 올림
        st["rules"][0]["wait_hours"] = 1
        m["ratingKey"], m["addedAt"] = "7", 70
        await mirror.check_all(st)
        await mirror.run_queue(st)
        state = plex_to_tg.load_state()
        state["rules"]["r"]["waiting"]["7"]["since"] -= 3601
        plex_to_tg.save_state(state)
        await mirror.check_all(st)
        await mirror.run_queue(st)
        assert len(tg.sent) == 2 and plex_to_tg.load_state()["rules"]["r"]["waiting"] == {}
    asyncio.run(main())


def test_screenshot_for_waiting_item(data, tmp_path, monkeypatch):
    """자막 기다리는 항목의 10분 지점 장면 (짧은 영상은 가운데) - 처음 한 번 뽑아 두고, 대기에서 빠지면 지움"""
    import shutil as _sh
    import subprocess
    if not _sh.which("ffmpeg"):
        pytest.skip("ffmpeg 없음")
    video = tmp_path / "Hard.S01E02.mp4"
    _video(video, 4)
    m = {"ratingKey": "8", "type": "episode", "title": "", "grandparentTitle": "하드", "parentIndex": 1, "index": 2, "duration": 4000,
         "Media": [{"Part": [{"key": "/p/8", "file": str(video), "size": video.stat().st_size, "Stream": []}]}]}
    calls = []

    async def plex_json(c, s, path, params=None):
        calls.append(path)
        return {"MediaContainer": {"Metadata": [m]}}
    config_store.save({"client_id": "c", "sources": [], "plex_to_tg": {"rules": [{"id": "r", "plex": "px", "section": "1", "tg": "tg", "channel": "-1"}]}})

    async def main():
        mirror = plex_to_tg.Mirror(plex_json, lambda sid: ({"client_id": "c"}, {"id": "px", "url": "http://x"}), lambda sid: None, lambda: None)
        path = await mirror.screenshot("r", "8")
        assert open(path, "rb").read(2) == b"\xff\xd8"
        w = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
        assert int(w) == 320                                       # 원본보다 키우지 않는다 (큰 영상만 960으로 줄임)
        n = len(calls)
        assert await mirror.screenshot("r", "8") == path and len(calls) == n   # 두 번째는 저장해 둔 것
        with pytest.raises(ValueError):
            await mirror.screenshot("없는규칙", "8")
        state = plex_to_tg.load_state()
        state["rules"]["r"] = {"done": [], "fails": {}, "waiting": {"8": {"title": "하드 S01E02", "since": 1}}}
        plex_to_tg.save_state(state)
        mirror.drop_item("r", "8")
        assert not __import__("os").path.exists(path)
    asyncio.run(main())
