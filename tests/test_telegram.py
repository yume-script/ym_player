"""텔레그램 소스 - 메시지 판별, Range 해석, 그리고 가짜 Telethon 클라이언트로 HTTP 흐름 전체.

진짜 텔레그램에는 붙지 않는다. TelegramSource.get_client를 가짜 클라이언트로 바꿔서
로그인(코드 -> 2단계 비밀번호) / 채널 / 색인(오래된 쪽 채우기) / 스트림 Range / 썸네일 / 이어보기를 확인한다.
"""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ym_player import config_store, telegram_src

telethon = pytest.importorskip("telethon")


# ----- 가짜 Telethon 객체 (클래스 이름으로 판별하므로 이름을 맞춘다) -----
class DocumentAttributeVideo:
    def __init__(self, duration=0, w=1280, h=720, round_message=False):
        self.duration, self.w, self.h, self.round_message = duration, w, h, round_message


class DocumentAttributeFilename:
    def __init__(self, file_name):
        self.file_name = file_name


class DocumentAttributeAnimated:
    pass


class Channel:
    def __init__(self, cid, title, broadcast=True):
        self.id, self.title, self.broadcast, self.access_hash, self.username = cid, title, broadcast, cid * 7, ""


def video_msg(mid, size=3000, caption="", name="clip.mp4", thumbs=True, mime="video/mp4", attrs=None):
    doc = SimpleNamespace(mime_type=mime, size=size, thumbs=[1] if thumbs else [],
                          attributes=attrs if attrs is not None else [DocumentAttributeVideo(120), DocumentAttributeFilename(name)])
    return SimpleNamespace(id=mid, document=doc, photo=None, message=caption,
                           date=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=mid))


def text_msg(mid):
    return SimpleNamespace(id=mid, document=None, photo=None, message="글", date=None)


def test_message_to_video_filters():
    v = telegram_src.message_to_video(video_msg(5, caption="첫 줄 제목\n설명"))
    assert v["title"] == "첫 줄 제목" and v["duration"] == 120 and v["height"] == 720 and v["has_thumb"] == 1
    assert telegram_src.message_to_video(video_msg(6, caption=""))["title"] == "clip"  # 캡션 없으면 파일명
    assert telegram_src.message_to_video(text_msg(7)) is None
    assert telegram_src.message_to_video(video_msg(8, attrs=[DocumentAttributeAnimated(), DocumentAttributeVideo(3)])) is None
    assert telegram_src.message_to_video(video_msg(9, attrs=[DocumentAttributeVideo(3, round_message=True)])) is None
    assert telegram_src.message_to_video(video_msg(10, mime="application/octet-stream", attrs=[DocumentAttributeFilename("a.mkv")]))
    assert telegram_src.message_to_video(video_msg(11, mime="image/gif", attrs=[DocumentAttributeFilename("a.gif")])) is None
    assert telegram_src.browser_mime("video/x-matroska") == "video/webm"


def test_parse_range():
    assert telegram_src.parse_range(None, 100) == (0, 99, False)
    assert telegram_src.parse_range("bytes=10-19", 100) == (10, 19, True)
    assert telegram_src.parse_range("bytes=90-", 100) == (90, 99, True)
    assert telegram_src.parse_range("bytes=-5", 100) == (95, 99, True)
    assert telegram_src.parse_range("bytes=50-500", 100) == (50, 99, True)
    for bad in ("bytes=100-", "bytes=5-1", "bytes=0-1,4-5", "items=0-1", "bytes=-"):
        with pytest.raises(ValueError):
            telegram_src.parse_range(bad, 100)


class FakeClient:
    """채널 777: 메시지 1..1000, 3의 배수만 동영상. 파일 내용은 결정적인 바이트열."""

    def __init__(self):
        self.connected = False
        self.authorized = False
        self.needs_password = True
        self.dialogs = [SimpleNamespace(entity=Channel(777, "영화 채널"), name="영화 채널"),
                        SimpleNamespace(entity=Channel(888, "수다방", broadcast=False), name="수다방"),
                        SimpleNamespace(entity=SimpleNamespace(id=1, title="친구"), name="친구")]
        self.msgs = {i: (video_msg(i, size=4096 * 3 + i, caption=f"영상 {i}") if i % 3 == 0 else text_msg(i)) for i in range(1, 1001)}
        self.logged_out = False
        self.scans = []

    def is_connected(self):
        return self.connected

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return SimpleNamespace(first_name="유메", last_name="", username="yume")

    async def send_code_request(self, phone):
        self.phone = phone
        return SimpleNamespace(phone_code_hash="HASH")

    async def sign_in(self, phone=None, code=None, *, password=None, phone_code_hash=None):
        from telethon.errors import PasswordHashInvalidError, PhoneCodeInvalidError, SessionPasswordNeededError
        if password is not None:
            if password != "pw":
                raise PasswordHashInvalidError(request=None)
            self.authorized = True
            return
        if code != "12345" or phone_code_hash != "HASH":
            raise PhoneCodeInvalidError(request=None)
        if self.needs_password:
            raise SessionPasswordNeededError(request=None)
        self.authorized = True

    async def log_out(self):
        self.logged_out = True
        self.authorized = False

    async def iter_dialogs(self):
        for d in self.dialogs:
            yield d

    async def iter_messages(self, peer, min_id=0, offset_id=0, limit=None):
        assert peer.channel_id == 777 and peer.access_hash == 777 * 7
        self.scans.append((min_id, offset_id, limit))
        ids = sorted((i for i in self.msgs if i > min_id and (not offset_id or i < offset_id)), reverse=True)
        for i in ids[:limit] if limit else ids:
            yield self.msgs[i]

    async def get_messages(self, peer, ids):
        return self.msgs.get(ids)

    def iter_download(self, doc, offset=0, request_size=0, chunk_size=0, file_size=None):
        data = getattr(doc, "data", None) or bytes((i * 7) % 256 for i in range(doc.size))

        async def gen():
            pos = offset
            while pos < len(data):
                yield data[pos:pos + 5000]  # 원하는 길이보다 크게도 주는지 서버가 잘라야 한다
                pos += 5000
        return gen()

    async def download_media(self, msg, file=bytes, thumb=None, progress_callback=None):
        data = getattr(getattr(msg, "document", None), "data", None)
        if data is not None and thumb is None and file is not bytes:   # 웹툰 압축 파일처럼 파일로 받는 것
            with open(file, "wb") as f:
                f.write(data)
            if progress_callback:
                progress_callback(len(data), len(data))
            return file
        return b"\xff\xd8JPEG" + str(msg.id).encode()


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    fake = FakeClient()

    async def get_client(self):
        if not self.api_id or not self.api_hash:
            raise telegram_src.TgError("API 필요")
        if not fake.connected:
            await fake.connect()
        self.client = fake
        return fake

    monkeypatch.setattr(telegram_src.TelegramSource, "get_client", get_client)
    from ym_player import media_cache
    media_cache.STORE.configure(None)  # 이 테스트의 데이터 폴더 기준으로 (앞 테스트의 조각이 남지 않게)
    cfg = config_store.load()
    config_store.replace_sources(cfg, [{"id": "tg", "type": "telegram", "name": "텔레", "api_id": "1234",
                                        "api_hash": "SECRET", "phone": "+82 10-1234-5678",
                                        "channels": [{"id": 777, "title": "영화 채널"}]}])
    config_store.save(cfg)
    return fake


def run(coro_fn):
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server

    async def main():
        client = TestClient(TestServer(server.create_app(require_auth=False)))
        await client.start_server()
        try:
            await coro_fn(client)
        finally:
            await client.close()
    asyncio.run(main())


def test_login_channels_index_stream_progress(env):
    async def flow(c):
        # api_hash는 브라우저로 안 나간다
        pub = await (await c.get("/api/config")).json()
        assert "SECRET" not in str(pub) and pub["sources"][0]["has_api_hash"]

        st = await (await c.get("/api/tg/tg/status")).json()
        assert st["authorized"] is False
        r = await c.get("/api/tg/tg/channels")
        assert r.status == 400 and "로그인" in (await r.json())["error"]

        r = await (await c.post("/api/tg/tg/code", json={})).json()
        assert r["code_sent"] and env.phone == "+821012345678"  # 공백/하이픈 정리
        r = await c.post("/api/tg/tg/sign-in", json={"code": "999"})
        assert r.status == 400 and "틀렸" in (await r.json())["error"]
        r = await (await c.post("/api/tg/tg/sign-in", json={"code": "123 45"})).json()
        assert r["password_required"] is True
        r = await c.post("/api/tg/tg/sign-in", json={"password": "nope"})
        assert r.status == 400
        r = await (await c.post("/api/tg/tg/sign-in", json={"password": "pw"})).json()
        assert r["authorized"] and r["name"] == "유메"

        chans = (await (await c.get("/api/tg/tg/channels")).json())["channels"]
        assert [(x["title"], x["kind"]) for x in chans] == [("수다방", "group"), ("영화 채널", "channel")]  # 개인 대화는 빠짐
        assert "access_hash" not in chans[0]

        # 첫 페이지: 최신 400개를 훑어 133개 색인, 40개 반환, 더 있음
        page = await (await c.get("/api/tg/tg/videos?channel=777&limit=40")).json()
        assert len(page["items"]) == 40 and page["more"]
        assert page["items"][0]["msg"] == 999 and page["items"][0]["title"] == "영상 999"
        # 색인 범위(601~1000) 밖까지 넘기면 오래된 쪽을 더 훑는다
        far = await (await c.get("/api/tg/tg/videos?channel=777&offset=150&limit=40")).json()
        assert len(far["items"]) == 40 and far["items"][0]["msg"] == 999 - 150 * 3
        assert any(s[1] for s in env.scans)  # offset_id로 과거 스캔
        # 끝까지
        last = await (await c.get("/api/tg/tg/videos?channel=777&offset=320&limit=40")).json()
        assert len(last["items"]) == 13 and last["more"] is False  # 3,6,...,999 = 333개
        # 검색
        found = await (await c.get("/api/tg/tg/videos?q=영상 99")).json()
        assert {it["msg"] for it in found["items"]} >= {99, 990, 993, 996, 999}
        # 고르지 않은 채널은 막는다
        assert (await c.get("/api/tg/tg/video/888/3")).status == 404
        nothing = await (await c.get("/api/tg/tg/videos?channel=888")).json()
        assert nothing["items"] == []

        # 스트림: 전체 / Range / 범위 밖
        size = 4096 * 3 + 999
        whole = await c.get("/api/tg/tg/stream/777/999")
        body = await whole.read()
        assert whole.status == 200 and len(body) == size and whole.headers["Accept-Ranges"] == "bytes"
        expect = bytes((i * 7) % 256 for i in range(size))
        assert body == expect
        part = await c.get("/api/tg/tg/stream/777/999", headers={"Range": "bytes=5000-5099"})
        assert part.status == 206 and part.headers["Content-Range"] == f"bytes 5000-5099/{size}"
        assert await part.read() == expect[5000:5100]
        assert (await c.get("/api/tg/tg/stream/777/999", headers={"Range": f"bytes={size}-"})).status == 416

        # 썸네일 (두 번째는 디스크 캐시)
        t = await c.get("/api/tg/tg/thumb/777/999")
        assert t.status == 200 and (await t.read()).startswith(b"\xff\xd8")
        env.download_media = None
        assert (await c.get("/api/tg/tg/thumb/777/999")).status == 200

        # 이어보기
        await c.post("/api/tg/tg/progress", json={"channel": 777, "msg": 999, "position": 60, "duration": 120})
        home = await (await c.get("/api/tg/tg/home")).json()
        assert [v["msg"] for v in home["continue"]] == [999] and home["channels"][0]["items"]
        item = (await (await c.get("/api/tg/tg/video/777/999")).json())["item"]
        assert item["position"] == 60 and item["watched"] is False
        await c.post("/api/tg/tg/progress", json={"channel": 777, "msg": 999, "position": 119, "duration": 120})
        item = (await (await c.get("/api/tg/tg/video/777/999")).json())["item"]
        assert item["watched"] is True and item["position"] == 0
        assert (await (await c.get("/api/tg/tg/home")).json())["continue"] == []

        # 로그아웃
        await c.post("/api/tg/tg/logout")
        assert env.logged_out and (await (await c.get("/api/tg/tg/status")).json())["authorized"] is False

    run(flow)


def test_deleting_source_logs_out_and_forgets_index(env):
    async def flow(c):
        env.authorized = True
        await c.get("/api/tg/tg/videos?channel=777&limit=5")
        assert telegram_src.query_videos("tg", [777])[1] > 0
        r = await c.put("/api/config", json={"sources": []})
        assert r.status == 200
        for _ in range(20):
            await asyncio.sleep(0.01)
            if env.logged_out:
                break
        assert env.logged_out
        assert telegram_src.query_videos("tg", [777])[1] == 0

    run(flow)


def test_config_telegram_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    cfg = config_store.load()
    config_store.replace_sources(cfg, [{"id": "t", "type": "telegram", "api_id": " 99 ", "api_hash": "H",
                                        "channels": [{"id": "-100", "title": "a"}, {"id": "x"}, "junk", {"id": 5}]}])
    src = cfg["sources"][0]
    assert src["api_id"] == "99" and src["name"] == "Telegram"
    assert src["channels"] == [{"id": -100, "title": "a"}, {"id": 5, "title": "5"}]
    # 빈 api_hash로 저장하면 기존 값 유지
    config_store.replace_sources(cfg, [{"id": "t", "type": "telegram", "api_id": "99", "api_hash": ""}])
    assert cfg["sources"][0]["api_hash"] == "H"


def _source():
    return telegram_src.TelegramSource(config_store.get_source(config_store.load(), "tg"))


def test_cached_list_does_not_wait_for_telegram(env):
    """이미 색인한 채널은 텔레그램이 느려도 DB에서 바로 돌려주고, 새 영상은 뒤에서 받는다."""
    env.authorized = True

    async def main():
        tg = _source()
        first = await tg.videos([777], limit=10)
        assert first["items"][0]["msg"] == 999 and first["syncing"] is False  # 첫 색인은 기다림

        # 새 영상이 올라왔고 텔레그램 응답이 느려졌다
        env.msgs[1002] = video_msg(1002, caption="새 영상")
        slow = env.iter_messages

        async def slow_iter(*a, **k):
            await asyncio.sleep(0.5)
            async for m in slow(*a, **k):
                yield m
        env.iter_messages = slow_iter
        telegram_src.save_sync_state("tg", 777, latest_at=0)  # 2분 지난 것처럼

        loop = asyncio.get_running_loop()
        t0 = loop.time()
        cached = await tg.videos([777], limit=10)
        assert loop.time() - t0 < 0.3, "느린 텔레그램을 기다리면 안 된다"
        assert cached["syncing"] is True and cached["items"][0]["msg"] == 999
        await tg._tasks[("latest", 777)]
        fresh = await tg.videos([777], limit=10)
        assert fresh["items"][0]["msg"] == 1002 and fresh["syncing"] is False
        # 뒤 작업을 끝까지 정리
        await asyncio.gather(*[t for t in tg._tasks.values() if not t.done()], return_exceptions=True)

    asyncio.run(main())


def test_older_pages_are_prefetched_in_background(env):
    env.authorized = True

    async def main():
        tg = _source()
        await tg.videos([777], limit=40)          # 최신 400개(133개 영상) 색인
        assert telegram_src.sync_state("tg", 777)["oldest"] == 601
        page = await tg.videos([777], offset=60, limit=40)  # 이 페이지는 꽉 차지만 뒤에 남은 게 33개 < 80
        assert len(page["items"]) == 40 and telegram_src.sync_state("tg", 777)["oldest"] == 601  # 기다리진 않음
        await tg._tasks[("older", 777)]  # 다음 페이지 몫은 뒤에서
        assert telegram_src.sync_state("tg", 777)["oldest"] == 201
        await asyncio.gather(*[t for t in tg._tasks.values() if not t.done()], return_exceptions=True)

    asyncio.run(main())


def test_thumbnails_reuse_scanned_messages_and_dedupe(env):
    env.authorized = True
    calls = {"get": 0, "download": 0}
    orig_get, orig_dl = env.get_messages, env.download_media

    async def counted_get(peer, ids):
        calls["get"] += 1
        return await orig_get(peer, ids)

    async def counted_dl(msg, file=bytes, thumb=None):
        calls["download"] += 1
        await asyncio.sleep(0.05)
        return await orig_dl(msg, file=file, thumb=thumb)

    env.get_messages, env.download_media = counted_get, counted_dl

    async def main():
        tg = _source()
        await tg.videos([777], limit=10, fill=False)
        prefetch = [t for k, t in tg._tasks.items() if k[0] == "thumbs"]
        assert prefetch, "새로 색인한 영상의 썸네일을 뒤에서 미리 받아야 한다"
        await asyncio.gather(*prefetch)
        assert calls["download"] == telegram_src.THUMB_PREFETCH and calls["get"] == 0  # 훑을 때 받은 메시지 재사용
        # 미리 받은 건 디스크에서, 동시에 같은 걸 달라고 하면 한 번만 받는다
        before = calls["download"]
        await tg.thumbnail(777, 999)
        # 603: 훑을 때 받은 메시지지만 미리 받은 60개에는 없음
        paths = await asyncio.gather(*(tg.thumbnail(777, 603) for _ in range(5)))
        assert len(set(paths)) == 1 and calls["download"] == before + 1
        assert telegram_src.THUMB_CONCURRENCY >= 1 and calls["get"] == 0

    asyncio.run(main())


def test_status_reports_crypto_speedup(env):
    async def main():
        st = await _source().status()
        assert st["fast_crypto"] == telegram_src.fast_crypto()

    asyncio.run(main())


# ----- 서버 변환 (ffmpeg) -----
def _probe(fmt, *streams):
    return {"format": {"format_name": fmt}, "streams": [dict(codec_type=t, codec_name=c, **kw) for t, c, kw in streams]}


def test_transcode_decide():
    from ym_player import transcode
    mp4 = "mov,mp4,m4a,3gp,3g2,mj2"
    d = transcode.decide
    assert d(_probe(mp4, ("video", "h264", {"pix_fmt": "yuv420p"}), ("audio", "aac", {})))["mode"] == "direct"
    assert d(_probe("matroska,webm", ("video", "vp9", {}), ("audio", "opus", {})))["mode"] == "direct"
    assert d(_probe("matroska,webm", ("video", "h264", {"pix_fmt": "yuv420p"}), ("audio", "aac", {})))["mode"] == "remux"
    assert d(_probe("avi", ("video", "h264", {"pix_fmt": "yuv420p"}), ("audio", "mp3", {})))["mode"] == "remux"
    assert d(_probe("matroska,webm", ("video", "h264", {"pix_fmt": "yuv420p"}), ("audio", "ac3", {})))["mode"] == "audio"
    assert d(_probe(mp4, ("video", "h264", {"pix_fmt": "yuv420p"}), ("audio", "eac3", {})))["mode"] == "audio"
    assert d(_probe(mp4, ("video", "hevc", {"pix_fmt": "yuv420p10le"}), ("audio", "aac", {})))["mode"] == "transcode"
    assert d(_probe(mp4, ("video", "h264", {"pix_fmt": "yuv420p10le"}), ("audio", "aac", {})))["mode"] == "transcode"
    assert d(_probe("matroska,webm", ("video", "mpeg4", {}), ("audio", "dts", {})))["mode"] == "transcode"
    args = transcode.build_args("http://x/y", "audio", 12.5)
    assert args[args.index("-ss") + 1] == "12.500" and args.index("-ss") < args.index("-i")
    assert args[args.index("-c:v") + 1] == "copy" and args[args.index("-c:a") + 1] == "aac"
    assert "-ss" not in transcode.build_args("http://x/y", "remux", 0)
    with pytest.raises(ValueError):
        transcode.build_args("http://x/y", "direct")


def test_transcode_stream_end_to_end(env, tmp_path):
    import shutil
    import subprocess

    from ym_player import transcode
    if not transcode.available():
        pytest.skip("ffmpeg 없음")
    src = tmp_path / "in.mkv"
    subprocess.run([shutil.which("ffmpeg"), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "6", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-g", "25", "-c:a", "ac3", str(src)], check=True)
    data = src.read_bytes()
    doc = env.msgs[999].document
    doc.size, doc.data, doc.mime_type = len(data), data, "video/x-matroska"

    async def flow(c):
        env.authorized = True
        await c.get("/api/tg/tg/videos?channel=777&limit=5")  # 색인 (크기는 위에서 바꾼 값)
        item = (await (await c.get("/api/tg/tg/video/777/999?probe=1")).json())["item"]
        assert item["play"]["mode"] == "audio" and item["play"]["audio"] == "ac3"
        r = await c.get("/api/tg/tg/transcode/777/999", params={"start": "2", "mode": item["play"]["mode"]})
        body = await r.read()
        assert r.status == 200 and r.headers["Content-Type"] == "video/mp4" and body[4:8] == b"ftyp"
        out = tmp_path / "out.mp4"
        out.write_bytes(body)
        info = await transcode.probe(str(out))
        assert {s["codec_name"] for s in info["streams"]} == {"h264", "aac"}
        assert float(info["format"]["duration"]) < 5.5  # 2초부터
        # 내부 주소 토큰은 변환이 끝나면 지워진다
        from ym_player import server
        assert c.server.app[server.TG_INTERNAL] == {}
        assert (await c.get("/internal/tg/nope")).status == 404

    run(flow)


def test_block_cache_serves_same_bytes_and_reuses_head_tail(env):
    """512KB 경계로 받아 디스크에 남김 - 어떤 Range든 내용이 같고, 두 번째부터는 텔레그램에 안 간다."""
    from ym_player import media_cache
    size = telegram_src.STREAM_CHUNK * 10 + 1234
    data = bytes((i * 31) % 251 for i in range(size))
    doc = env.msgs[999].document
    doc.size, doc.data = size, data
    calls = []
    orig = env.iter_download

    def counting(doc_, offset=0, **kw):
        calls.append(offset)
        return orig(doc_, offset=offset, **kw)
    env.iter_download = counting
    media_cache.STORE.configure({"ahead_min": 0})  # 미리 받기 없이 읽은 것만

    async def main():
        env.authorized = True
        env.connected = True
        tg = telegram_src.TelegramSource({"id": "tg", "api_id": "1", "api_hash": "h", "channels": [{"id": 777}]})
        B = telegram_src.STREAM_CHUNK

        async def read(start, length):
            out = b""
            async for chunk in tg.iter_cached(777, 999, start, length, size):
                out += chunk
            return out
        for start, length in [(0, 100), (B - 10, 30), (3, B * 2 + 5), (size - 700, 700), (B * 5 + 7, B * 2), (0, size)]:
            assert await read(start, length) == data[start:start + length], (start, length)
        assert all(off % B == 0 for off in calls)  # 경계에 맞춘 요청만
        assert sorted(set(calls)) == [i * B for i in range(11)] and len(calls) == 11  # 조각마다 한 번씩만
        calls.clear()
        media_cache.STORE.ram.clear()  # 메모리를 비워도 디스크에서
        assert await read(0, size) == data
        assert calls == []
        st = media_cache.STORE.stats()
        assert st["bytes"] == size and st["files"] == 1

    asyncio.run(main())


def test_prefetch_ahead_and_eviction(env, tmp_path):
    from ym_player import media_cache
    B = media_cache.BLOCK
    store = media_cache.STORE
    store.configure({"dir": str(tmp_path / "c"), "max_gb": 0.5, "ahead_min": 1})
    fetched = []

    async def fetch(bi):
        fetched.append(bi)
        await asyncio.sleep(0.01)
        return bytes([bi % 256]) * B

    async def main():
        st = media_cache.streamer("t:a", B * 40, fetch, bytes_per_sec=B * 10 / 60)  # 1분 = 10조각 -> 최소 8
        out = b""
        async for piece in st.iter_range(0, 10):
            out += piece
        assert out == b"\x00" * 10
        n = st.ahead_blocks()
        assert n == 11  # 1분 x 10조각/분, 올림
        for _ in range(50):
            await asyncio.sleep(0.02)
            if len(fetched) >= n + 1:
                break
        await asyncio.sleep(0.05)
        assert sorted(fetched) == list(range(n + 1))   # 읽은 0번 + 앞쪽 n개를 뒤에서, 더는 안 받음
        # 탐색하면 그 위치에서 다시 앞쪽을 채운다
        async for _ in st.iter_range(B * 30, 10):
            pass
        for _ in range(50):
            await asyncio.sleep(0.02)
            if 39 in fetched:
                break
        assert set(range(30, 40)) <= set(fetched)
        # 용량이 넘치면 오래 안 본 영상부터 통째로 지운다
        store.settings["max_gb"] = (B * 25) / 1024 ** 3
        other = media_cache.streamer("t:b", B * 30, fetch)
        store.settings["ahead_min"] = 0
        async for _ in other.iter_range(0, B * 30):
            pass
        # 먼저 본 t:a는 지워지고, 지금 받는 t:b는 (그것만으로 한도를 넘어도) 남긴다
        assert store.stats()["files"] == 1 and store.usage == B * 30
        assert not store.has_cached_sync("t:a", 35) or ("t:a", 35) in store.ram

    asyncio.run(main())


def test_probe_is_shared_and_remembered(env, monkeypatch):
    from ym_player import transcode
    monkeypatch.setattr(transcode, "available", lambda: True)
    seen = []

    async def fake_probe(url, timeout=45):
        seen.append(url)
        await asyncio.sleep(0.2)
        return _probe("matroska,webm", ("video", "hevc", {"pix_fmt": "yuv420p10le"}), ("audio", "ac3", {}))
    monkeypatch.setattr(transcode, "probe", fake_probe)

    async def flow(c):
        env.authorized = True
        await c.get("/api/tg/tg/videos?channel=777&limit=5")
        # 상세 화면의 미리 확인 + 바로 [재생] 두 번 -> ffprobe는 한 번
        assert (await c.post("/api/tg/tg/probe/777/999")).status == 200
        items = await asyncio.gather(*(c.get("/api/tg/tg/video/777/999?probe=1") for _ in range(2)))
        plays = [(await r.json())["item"]["play"]["mode"] for r in items]
        assert plays == ["transcode", "transcode"] and len(seen) == 1
        # DB에 남아서 서버를 다시 켜도 다시 안 본다
        assert telegram_src.get_probe("tg", 777, 999)["mode"] == "transcode"
        await c.get("/api/tg/tg/video/777/999?probe=1")
        assert len(seen) == 1
        assert (await c.post("/api/tg/tg/probe/777/12345")).status == 404

    run(flow)


def test_user_channel_access(env):
    """사용자마다 볼 수 있는 텔레그램 채널 - 설정 목록, 영상 목록, 홈, 영상/스트림/이어보기를 서버가 막는다."""
    from aiohttp import CookieJar
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import auth, server
    cfg = config_store.load()
    cfg["sources"][0]["channels"] = [{"id": 777, "title": "영화 채널"}, {"id": 888, "title": "수다방"}]
    config_store.save(cfg)
    env.authorized = True

    async def main():
        client = TestClient(TestServer(server.create_app()), cookie_jar=CookieJar(unsafe=True))
        await client.start_server()
        c = client
        try:
            auth.create_user("admin", "adminpw", "admin")
            auth.create_user("kid", "kidpass", "user")
            auth.update_user("kid", access={"sources": ["tg"], "libraries": {"tg": ["888"]}})
            # 관리자가 먼저 777을 색인해 둔다
            await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
            assert len((await (await c.get("/api/tg/tg/videos?channel=777&limit=5")).json())["items"]) == 5
            await c.post("/api/auth/logout")
            c.session.cookie_jar.clear()

            await c.post("/api/auth/login", json={"username": "kid", "password": "kidpass"})
            src = (await (await c.get("/api/config")).json())["sources"][0]
            assert [ch["id"] for ch in src["channels"]] == [888]
            assert (await (await c.get("/api/tg/tg/videos?channel=777")).json())["items"] == []
            assert all(it["channel"] != 777 for it in (await (await c.get("/api/tg/tg/videos?q=영상")).json())["items"])
            home = await (await c.get("/api/tg/tg/home")).json()
            assert all(r["channel"] != 777 for r in home["channels"]) and home["continue"] == []
            for path in ("video/777/999", "stream/777/999", "thumb/777/999", "transcode/777/999"):
                assert (await c.get(f"/api/tg/tg/{path}")).status == 404, path
            assert (await c.post("/api/tg/tg/probe/777/999")).status == 404
            r = await c.post("/api/tg/tg/progress", json={"channel": 777, "msg": 999, "position": 10})
            assert r.status == 404
            # 랜덤 재생도 볼 수 없는 채널에서는 뽑지 않는다
            for q in ("", "?channel=777"):
                item = (await (await c.get(f"/api/tg/tg/random{q}")).json())["item"]
                assert item is None or item["channel"] == 888, q
        finally:
            await client.close()

    asyncio.run(main())


def test_fix_text_repairs_broken_titles():
    import unicodedata
    t = "영화 제목 1080p"
    b = t.encode("utf-8")
    browser_cp1252 = "".join(bytes([x]).decode("cp1252", "ignore") or chr(x) for x in b)
    for broken in (b.decode("latin-1"), browser_cp1252, t.encode("cp949").decode("latin-1"),
                   "%EC%98%81%ED%99%94%20%EC%A0%9C%EB%AA%A9%201080p", unicodedata.normalize("NFD", t)):
        assert telegram_src.fix_text(broken) == t, repr(broken)
    # 메일 머리글 방식 (RFC 2047) - 제대로 된 모양, ?가 공백으로 바뀐 모양, 여러 조각, 잘린 것
    name = "개와 늑대의 시간 2.E38.261014.1080p"
    b64 = base64.b64encode(name.encode()).decode()
    assert telegram_src.fix_text(f"=?utf-8?B?{b64}?=.mkv") == name + ".mkv"
    assert telegram_src.fix_text(f"= utf-8 B {b64} =") == name
    assert telegram_src.fix_text(f"= utf-8 B {b64}") == name
    half = base64.b64encode("개와 늑대의 ".encode()).decode(), base64.b64encode("시간".encode()).decode()
    assert telegram_src.fix_text(f"=?UTF-8?B?{half[0]}?= =?UTF-8?B?{half[1]}?=") == "개와 늑대의 시간"
    assert telegram_src.fix_text(f"=?euc-kr?B?{base64.b64encode('영화'.encode('cp949')).decode()}?=") == "영화"
    assert telegram_src.fix_text("=?utf-8?Q?=EC=98=81=ED=99=94_1?=") == "영화 1"
    assert telegram_src.fix_text("= utf-8 B 6rCc7JmAIOuKkeuMgOydm") == "개와 늑대"  # 잘려서 반쪽 난 끝 글자는 버림
    for fine in ("Café Amélie (2001)", "🎬 영화 ✨", "100% 진짜 50% 할인", "“Hello” – it’s ok", ""):
        assert telegram_src.fix_text(fine) == fine
    assert telegram_src.fix_text("a = b = c") == "a = b = c"
    # 색인할 때 파일명/캡션에 적용
    v = telegram_src.message_to_video(video_msg(12, caption="", name=b.decode("latin-1") + ".mp4"))
    assert v["title"] == t and v["file_name"] == t + ".mp4"


def test_existing_index_titles_are_repaired_once(monkeypatch, tmp_path):
    import sqlite3
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    broken = "영화".encode("utf-8").decode("latin-1")
    conn = sqlite3.connect(str(tmp_path / "telegram.db"))
    conn.executescript(telegram_src._SCHEMA)
    conn.execute("INSERT INTO videos(source,channel,msg,title,caption,file_name,mime,duration,size,width,height,date,has_thumb)"
                 " VALUES('tg',1,2,?,?,?,'video/mp4',1,1,1,1,'',0)", (broken, broken, broken + ".mkv"))
    conn.commit()
    conn.close()
    telegram_src._migrated.discard(str(tmp_path))
    row = telegram_src.get_video("tg", 1, 2)
    assert row["title"] == "영화" and row["file_name"] == "영화.mkv"
    with telegram_src.db() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 13


def test_split_parts_are_grouped(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    telegram_src._migrated.discard(str(tmp_path))
    base = "Hope.2026.1080p.AMZN.WEB-DL.DDP5.1.H.264-KyoGo"
    rows = [(10, f"{base}_0001", f"{base}_0001.mkv"), (9, f"{base}_0000", f"{base}_0000.mkv"),
            (11, "새 영화 소개", f"{base}_0002.mkv"),     # 제목이 캡션이어도 파일 이름으로 묶는다
            (12, "Hope.2026 리뷰", "Hope.2026.mkv"),      # 연도는 조각 번호가 아님
            (13, "Other_0001", "Other_0001.mkv")]
    with telegram_src.db() as conn:
        conn.executemany(
            "INSERT INTO videos(source,channel,msg,title,caption,file_name,mime,duration,size,width,height,date,has_thumb)"
            " VALUES('tg',1,?,?,'',?,'video/mp4',60,1,1,1,'',0)", rows)
    telegram_src.save_progress("tg", 1, 9, 60, 60, watched=True)
    parts = telegram_src.video_parts("tg", 1, 10)
    assert [(p["msg"], p["part"]) for p in parts] == [(9, 1), (10, 2), (11, 3)]
    assert parts[0]["watched"] and not parts[1]["watched"]
    assert telegram_src.video_parts("tg", 1, 12) == []
    assert telegram_src.video_parts("tg", 1, 13) == []      # 하나뿐이면 묶음이 아님
    assert telegram_src.split_part("Movie.part2.mp4") == ("movie", 2)
    assert telegram_src.split_part("Movie.2026") is None


def test_hls_transcode_for_safari(env, tmp_path):
    import os
    import shutil
    import subprocess

    from ym_player import server, transcode
    if not transcode.available():
        pytest.skip("ffmpeg 없음")
    src = tmp_path / "in.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "10", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-g", "25", "-c:a", "libmp3lame", str(src)], check=True)
    data = src.read_bytes()
    doc = env.msgs[999].document
    doc.size, doc.data, doc.mime_type = len(data), data, "video/mp4"

    async def flow(c):
        env.authorized = True
        await c.get("/api/tg/tg/videos?channel=777&limit=5")
        item = (await (await c.get("/api/tg/tg/video/777/999?probe=1")).json())["item"]
        assert item["play"]["mode"] == "direct" and item["play"]["audio"] == "mp3"
        # Safari: MP3 소리는 AAC로 바꿔 HLS로
        r = await c.post("/api/tg/tg/hls/777/999", params={"start": "0", "mode": "audio"})
        j = await r.json()
        assert r.status == 200 and j["url"].startswith("/api/tg-hls/")
        playlist = await (await c.get(j["url"])).text()
        assert "#EXT-X-START:TIME-OFFSET=0" in playlist and "#EXTINF" in playlist
        base = j["url"].rsplit("/", 1)[0]
        init = await (await c.get(f"{base}/init.mp4")).read()
        seg = await c.get(f"{base}/seg00000.m4s")
        assert init[4:8] == b"ftyp" and seg.status == 200 and len(await seg.read()) > 1000
        assert (await c.get(f"{base}/../../etc")).status == 404
        assert (await c.get(f"{base}/evil.txt")).status == 404
        out = tmp_path / "init_plus_seg.mp4"
        out.write_bytes(init + (await (await c.get(f"{base}/seg00000.m4s")).read()))
        info = await transcode.probe(str(out))
        assert {s["codec_name"] for s in info["streams"]} == {"h264", "aac"}
        # 새 변환을 시작하면 이전 것은 끄고 파일도 지운다
        app = c.server.app
        old = next(iter(app[server.HLS_JOBS].values()))
        r2 = await (await c.post("/api/tg/tg/hls/777/999", params={"start": "4", "mode": "audio"})).json()
        assert r2["url"] != j["url"] and len(app[server.HLS_JOBS]) == 1
        assert not os.path.exists(old.dir) and (await c.get(j["url"])).status == 404

    run(flow)


def test_photos_are_indexed_and_old_index_rescans(monkeypatch, tmp_path):
    import sqlite3
    from types import SimpleNamespace
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    # 사진 메시지 / 이미지 파일 / 영상 / 스티커
    photo = SimpleNamespace(sizes=[SimpleNamespace(type="m", w=320, h=240, size=20000),
                                   SimpleNamespace(type="y", w=1280, h=960, sizes=[100, 90000])])
    p = SimpleNamespace(id=5, photo=photo, document=None, message="바다 사진\n설명", date=None)
    item = telegram_src.message_to_media(p)
    assert item["kind"] == "image" and (item["width"], item["height"], item["size"]) == (1280, 960, 90000)
    assert item["title"] == "바다 사진" and telegram_src.message_to_video(p) is None
    png = video_msg(6, mime="image/png", name="map.png", attrs=[DocumentAttributeFilename("map.png")])
    assert telegram_src.message_to_media(png)["kind"] == "image"
    vid = video_msg(7)
    assert telegram_src.message_to_media(vid)["kind"] == "video"
    sticker = video_msg(8, mime="image/webp", attrs=[type("DocumentAttributeSticker", (), {})()])
    assert telegram_src.message_to_media(sticker) is None

    # kind 칸이 없던 예전 색인: 칸을 더하고 (기존 행은 video), 채널을 처음부터 다시 훑게 한다
    conn = sqlite3.connect(str(tmp_path / "telegram.db"))
    conn.executescript(telegram_src._SCHEMA.replace("    kind TEXT DEFAULT 'video', PRIMARY KEY", "    PRIMARY KEY"))
    conn.execute("INSERT INTO videos(source,channel,msg,title,caption,file_name,mime,duration,size,width,height,date,has_thumb)"
                 " VALUES('tg',1,2,'영상','','a.mp4','video/mp4',1,1,1,1,'',0)")
    conn.execute("INSERT INTO sync_state(source,channel,newest,oldest,complete) VALUES('tg',1,99,1,1)")
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()
    telegram_src._migrated.discard(str(tmp_path))
    assert telegram_src.get_video("tg", 1, 2)["kind"] == "video"
    assert telegram_src.sync_state("tg", 1)["newest"] == 0
    telegram_src.upsert_videos("tg", 1, [{**item, "msg": 3}])
    assert [r["msg"] for r in telegram_src.query_videos("tg", [1], kind="image")[0]] == [3]
    assert [r["msg"] for r in telegram_src.query_videos("tg", [1], kind="video")[0]] == [2]


def test_image_endpoint_and_kind_filter(env):
    from types import SimpleNamespace
    photo = SimpleNamespace(sizes=[SimpleNamespace(type="y", w=800, h=600, size=5000)])
    env.msgs[1000] = SimpleNamespace(id=1000, photo=photo, document=None, message="사진 하나",
                                     date=datetime(2026, 1, 2, tzinfo=timezone.utc))

    async def flow(c):
        env.authorized = True
        imgs = (await (await c.get("/api/tg/tg/videos?channel=777&limit=5&kind=image")).json())["items"]
        assert [(i["msg"], i["kind"], i["title"]) for i in imgs] == [(1000, "image", "사진 하나")]
        vids = (await (await c.get("/api/tg/tg/videos?channel=777&limit=5&kind=video")).json())["items"]
        assert vids and all(v["kind"] == "video" for v in vids)
        home = await (await c.get("/api/tg/tg/home")).json()
        assert all(v["kind"] == "video" for row in home["channels"] for v in row["items"])
        r = await c.get("/api/tg/tg/image/777/1000")
        assert r.status == 200 and r.content_type == "image/jpeg" and (await r.read()).startswith(b"\xff\xd8")
        assert (await c.get("/api/tg/tg/image/777/999")).status == 404  # 영상은 뷰어로 안 연다
        assert (await c.get("/api/tg/tg/image/888/1000")).status == 404  # 고르지 않은 채널

    run(flow)


def test_random_video_skips_photos_watched_and_middle_parts(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    telegram_src._migrated.discard(str(tmp_path))
    rows = [(1, "Movie_0000", "Movie_0000.mkv", "video"), (2, "Movie_0001", "Movie_0001.mkv", "video"),
            (3, "사진", "p.jpg", "image"), (4, "본 영화", "seen.mp4", "video"), (5, "다른 채널", "x.mp4", "video")]
    with telegram_src.db() as conn:
        conn.executemany(
            "INSERT INTO videos(source,channel,msg,title,caption,file_name,mime,duration,size,width,height,date,has_thumb,kind)"
            " VALUES('tg',?,?,?,'',?,'video/mp4',60,1,1,1,'',0,?)",
            [(2 if m == 5 else 1, m, t, f, k) for m, t, f, k in rows])
    telegram_src.save_progress("tg", 1, 4, 60, 60, watched=True)
    seen = {telegram_src.random_video("tg", [1])["msg"] for _ in range(60)}
    assert seen == {1, 4}                     # 사진 X, 중간 조각(2) -> 첫 조각(1)
    assert {telegram_src.random_video("tg", [1], unwatched=True)["msg"] for _ in range(20)} == {1}
    assert telegram_src.random_video("tg", [1], exclude=[(1, 1), (1, 2)])["msg"] == 4
    # 전부 뺄 목록이어도 아무거나 하나는
    assert telegram_src.random_video("tg", [1], unwatched=True, exclude=[(1, 1), (1, 2)])["msg"] == 1
    assert telegram_src.random_video("tg", [2])["msg"] == 5
    assert telegram_src.random_video("tg", []) is None


def _insert(rows, channel=1):
    """rows: (msg, kind, title, file_name, 분)"""
    with telegram_src.db() as conn:
        conn.executemany(
            "INSERT INTO videos(source,channel,msg,title,caption,file_name,mime,duration,size,width,height,date,has_thumb,kind)"
            " VALUES('tg',?,?,?,'',?,'video/mp4',60,1,1,1,?,0,?)",
            [(channel, m, t, f, f"2026-10-14T{10 + mi // 60:02d}:{mi % 60:02d}:00+00:00", k) for m, k, t, f, mi in rows])


def test_posters_are_paired_and_hidden_from_all(monkeypatch, tmp_path):
    """스크린샷 모양: 포스터 사진 -> 그 회차 영상, 영화는 "호프 [2026]" 사진 -> Hope.2026 조각 둘."""
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    telegram_src._migrated.discard(str(tmp_path))
    hope = "Hope.2026.1080p.AMZN.WEB-DL.DDP5.1.H.264-KyoGo"
    _insert([
        (10, "image", "로또 1등도 출근합니다 S01E04 [2026]", "", 1),
        (11, "video", "로또_1등도_출근합니다_S01E04_1080p", "로또_1등도_출근합니다_S01E04_1080p.mkv", 2),
        (12, "image", "로또 1등도 출근합니다 S01E05 [2026]", "", 3),
        (13, "video", "로또 1등도 출근합니다.E05.260924.1080p", "x.mkv", 4),
        (14, "image", "고양이 사진", "", 5),                                   # 짝 없는 사진은 그대로
        (15, "video", "로또 1등도 출근합니다.E06.260924.1080p", "y.mkv", 6),   # E05 포스터와는 회차가 달라 짝 아님
        (16, "image", "호프 [2026]", "", 7),
        (17, "video", f"{hope}_0000", f"{hope}_0000.mkv", 8),
        (18, "video", f"{hope}_0001", f"{hope}_0001.mkv", 9),
        (19, "image", "다른 영화 [2019]", "", 50),                             # 30분 넘게 떨어짐
        (20, "video", "Other.2019.1080p", "Other.2019.1080p.mkv", 95),
        (21, "image", "로또 1등도 출근합니다 S01E07 [2026]", "", 100),
        (22, "video", "로또 1등도 출근합니다.E07.261001.1080p", "z.mkv", 200),  # 큰 파일이라 100분 뒤에 올라옴
        # 우리말 제목 포스터 + 원제 영상: 이름은 달라도 회차가 같고 바로 옆이면 (실제 로그)
        (23, "image", "성난 사람들 S02E03 [2026]", "", 300),
        (24, "video", "BEEF_S02E03_The_Increasing_Flimsiness_of_Any_Certainties_Abo", "BEEF_S02E03_x.mkv", 300),
        (25, "image", "성난 사람들 S02E04 [2026]", "", 301),
        (26, "video", "BEEF_S02E05_I_Am_Killing_My_Flesh", "BEEF_S02E05_x.mkv", 302),   # 회차가 다르면 짝 아님
        (27, "image", "다른 드라마 S01E08", "", 400),
        (28, "video", "Another_S01E08_1080p", "Another_S01E08.mkv", 500),               # 회차는 같아도 100분 떨어짐
    ])
    telegram_src.link_posters("tg", 1)
    rows, total = telegram_src.query_videos("tg", [1], limit=50)
    by = {r["msg"]: r for r in rows}
    assert {11: 10, 13: 12, 17: 16, 18: 16, 22: 21, 24: 23} == {m: r["poster"] for m, r in by.items() if r["poster"]}
    # 포스터 사진(10,12,16,21,23)은 전체에서 빠짐
    assert set(by) == {11, 13, 14, 15, 17, 18, 19, 20, 22, 24, 25, 26, 27, 28} and total == 14
    photos, _ = telegram_src.query_videos("tg", [1], kind="image", limit=50)
    assert {r["msg"] for r in photos} == {10, 12, 14, 16, 19, 21, 23, 25, 27}   # 사진 보기에는 다 나옴
    assert telegram_src.get_video("tg", 1, 13)["poster"] == 12
    assert telegram_src._poster_score(telegram_src._media_key("영화 3 포스터"), telegram_src._media_key("영화 2")) == 0
    assert telegram_src._poster_score(telegram_src._media_key("로또 1등도 출근합니다"), telegram_src._media_key("로또 1등도 출근함")) > 0


def test_channel_kind_limits_lists(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    telegram_src._migrated.discard(str(tmp_path))
    _insert([(1, "image", "사진", "", 1), (2, "video", "영상", "a.mp4", 30)], channel=1)
    _insert([(1, "image", "사진2", "", 1), (2, "video", "영상2", "b.mp4", 30)], channel=2)
    kinds = {1: "video", 2: "image"}
    rows, total = telegram_src.query_videos("tg", [1, 2], limit=50, kinds=kinds)
    assert {(r["channel"], r["kind"]) for r in rows} == {(1, "video"), (2, "image")} and total == 2
    rows, _ = telegram_src.query_videos("tg", [1], kind="image", limit=50, kinds=kinds)
    assert rows == []                                                       # 영상만 채널은 사진 보기도 비어 있음
    assert telegram_src.random_video("tg", [2], kinds=kinds) is None        # 사진만 채널은 랜덤 재생에서 빠짐
    assert telegram_src.random_video("tg", [1, 2], kinds=kinds)["channel"] == 1
    # 설정 저장: kind는 video/image만 남긴다
    src = config_store._clean_source({"id": "t", "type": "telegram", "name": "t", "channels": [
        {"id": 1, "title": "a", "kind": "video"}, {"id": 2, "title": "b", "kind": "bogus"}]}, None)
    assert src["channels"] == [{"id": 1, "title": "a", "kind": "video"}, {"id": 2, "title": "b"}]
    src = config_store._clean_source({"id": "t", "type": "telegram", "name": "t", "channels": [
        {"id": 1, "title": "a", "ep_order": "desc"}, {"id": 2, "title": "b", "ep_order": "sideways"}]}, None)
    assert src["channels"] == [{"id": 1, "title": "a", "ep_order": "desc"}, {"id": 2, "title": "b"}]


def test_subtitle_files_are_indexed_linked_and_hidden(monkeypatch, tmp_path):
    from ym_player import subtitles
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    telegram_src._migrated.discard(str(tmp_path))
    # 자막 파일 메시지 -> kind subtitle (영상/사진이 아님)
    sub = video_msg(5, size=2000, name="Movie.2026.1080p.WEB-DL.ko.srt", mime="application/x-subrip",
                    attrs=[DocumentAttributeFilename("Movie.2026.1080p.WEB-DL.ko.srt")])
    item = telegram_src.message_to_media(sub)
    assert item["kind"] == "subtitle" and item["file_name"].endswith(".srt")
    rows = [(1, "video", "Movie 2026", "Movie.2026.1080p.WEB-DL.mkv"),
            (2, "image", "포스터", ""),
            (3, "video", "쇼.E05.261001.1080p", "Show.E05.mkv"),
            (4, "subtitle", "쇼 5화", "쇼 5화.smi"),                       # 이름은 달라도 회차가 같고 바로 옆
            (5, "subtitle", "Movie.2026.1080p.WEB-DL.ko", "Movie.2026.1080p.WEB-DL.ko.srt"),  # 파일 이름이 같으면 멀어도
            (6, "subtitle", "Movie.2026.1080p.WEB-DL.en", "Movie.2026.1080p.WEB-DL.en.srt"),
            (7, "subtitle", "아무 자막", "random.ass")]                    # 짝 없음
    with telegram_src.db() as conn:
        conn.executemany(
            "INSERT INTO videos(source,channel,msg,title,caption,file_name,mime,duration,size,width,height,date,has_thumb,kind)"
            " VALUES('tg',1,?,?,'',?,'video/mp4',60,1,1,1,'2026-10-14T10:00:00+00:00',0,?)",
            [(m, t, f, k) for m, k, t, f in rows])
    assert telegram_src.link_subs("tg", 1) == 3
    assert [s["msg"] for s in telegram_src.video_subs("tg", 1, 1)] == [5, 6]
    assert [s["msg"] for s in telegram_src.video_subs("tg", 1, 3)] == [4]
    listed, total = telegram_src.query_videos("tg", [1], limit=50)
    assert {r["kind"] for r in listed} == {"video", "image"} and total == 3   # 자막 파일은 목록에 없음
    # 서버 변환 스트림용: 자막 시각을 당긴다
    vtt = subtitles.to_vtt(b"1\n00:00:05,000 --> 00:00:07,000\nA\n\n2\n00:01:05,000 --> 00:01:07,500\nB\n", "srt")
    shifted = subtitles.shift_vtt(vtt, 60)
    assert "A" not in shifted.split("\n") and "00:00:05.000 --> 00:00:07.500" in shifted and "B" in shifted


def test_series_grouping_and_next_episode(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    telegram_src._migrated.discard(str(tmp_path))
    rows = [(10, "image", "로또 1등도 출근합니다 S01E04 [2026]", ""),
            (11, "video", "로또_1등도_출근합니다_S01E04_1080p", "로또_1등도_출근합니다_S01E04_1080p.mkv"),
            (12, "image", "로또 1등도 출근합니다 S01E05 [2026]", ""),
            (13, "video", "로또 1등도 출근합니다.E05.260924.1080p", "a.mkv"),
            (14, "video", "Hope.2026.1080p_0000", "Hope.2026.1080p_0000.mkv"),        # 회차 없음 -> 묶지 않음
            (15, "video", "단막극 E01", "b.mp4"),                                     # 한 편뿐 -> 묶지 않음
            (16, "video", "로또 1등도 출근합니다.E06.261001.1080p", "c.mkv")]
    with telegram_src.db() as conn:
        conn.executemany(
            "INSERT INTO videos(source,channel,msg,title,caption,file_name,mime,duration,size,width,height,date,has_thumb,kind)"
            " VALUES('tg',1,?,?,'',?,'video/mp4',60,1,1,1,?,0,?)",
            [(m, t, f, f"2026-10-14T10:{m:02d}:00+00:00", k) for m, k, t, f in rows])
    telegram_src.link_posters("tg", 1)
    assert telegram_src.link_series("tg", 1) == 1
    telegram_src.save_progress("tg", 1, 11, 60, 60, watched=True)
    grouped, total = telegram_src.query_videos("tg", [1], limit=50, group=True)
    by = {r["msg"]: r for r in grouped}
    assert set(by) == {14, 15, 16} and total == 3                 # 시리즈는 최근 회차(16) 하나로
    assert by[16]["series"] == {"key": "로또1등도출근합니다", "title": "로또 1등도 출근합니다", "count": 3, "unwatched": 2}
    assert "series" not in by[14]
    flat, _ = telegram_src.query_videos("tg", [1], limit=50)      # 묶지 않으면 회차가 다 보임
    assert {11, 13, 16} <= {r["msg"] for r in flat}
    data = telegram_src.series_episodes("tg", 1, "로또1등도출근합니다")
    assert [(e["msg"], e["ep"]) for e in data["episodes"]] == [(11, 4), (13, 5), (16, 6)]
    assert data["episodes"][0]["watched"] and data["episodes"][1]["poster"] == 12
    assert telegram_src.series_of("tg", 1, 13)["next"]["msg"] == 16
    assert telegram_src.series_of("tg", 1, 16)["next"] is None and telegram_src.series_of("tg", 1, 14) is None


def test_three_digit_season_is_grouped(monkeypatch, tmp_path):
    """'킬러들의_쇼핑몰_S102E08_잘들어_…' - 시즌을 세 자리로 적고 회차 뒤에 부제가 붙은 이름 (실제 채널)"""
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    telegram_src._migrated.discard(str(tmp_path))
    assert telegram_src._episode_info("킬러들의_쇼핑몰_S102E08_잘들어_정신차려_2160p", "") == ("킬러들의쇼핑몰", 2, 8)
    assert telegram_src._display_title("킬러들의_쇼핑몰_S102E08_잘들어_정신차려_2160p") == "킬러들의 쇼핑몰"
    rows = [(m, "video", f"킬러들의_쇼핑몰_S102E0{m}_부제_{m}_2160p", f"x{m}.mkv") for m in range(1, 9)]
    with telegram_src.db() as conn:
        conn.executemany(
            "INSERT INTO videos(source,channel,msg,title,caption,file_name,mime,duration,size,width,height,date,has_thumb,kind)"
            " VALUES('tg',1,?,?,'',?,'video/mp4',60,1,1,1,'2026-08-14T10:00:00+00:00',0,?)",
            [(m, t, f, k) for m, k, t, f in rows])
    assert telegram_src.link_series("tg", 1) == 1
    grouped, total = telegram_src.query_videos("tg", [1], limit=50, group=True)
    assert total == 1 and grouped[0]["series"]["count"] == 8 and grouped[0]["series"]["title"] == "킬러들의 쇼핑몰"
    assert [e["ep"] for e in telegram_src.series_episodes("tg", 1, "킬러들의쇼핑몰")["episodes"]] == list(range(1, 9))


def test_tmdb_find_details_and_targets(monkeypatch, tmp_path):
    from ym_player import tmdb
    calls = []

    async def fake_get(self, path, **params):
        calls.append((path, params.get("query"), params.get("language")))
        if path == "/search/tv":
            return {"results": [{"id": 9, "name": "다른 쇼", "first_air_date": "2020-01-01"},
                                {"id": 7, "name": "킬러들의 쇼핑몰", "original_name": "Killer's Shopping Mall", "first_air_date": "2024-01-17"}]}
        if path == "/search/movie":
            return {"results": [] if params.get("query") == "호프" else [{"id": 5, "title": "호프", "original_title": "Hope", "release_date": "2026-07-01"}]}
        if path == "/tv/7":
            return {"id": 7, "name": "킬러들의 쇼핑몰", "overview": "줄거리", "first_air_date": "2024-01-17", "poster_path": "/p.jpg",
                    "genres": [{"name": "액션"}], "vote_average": 8.12, "created_by": [{"name": "이권"}],
                    "credits": {"cast": [{"name": "이동욱", "character": "정진만", "profile_path": "/a.jpg"}]}, "seasons": [{"season_number": 1}, {"season_number": 2}]}
        if path == "/tv/7/season/2":
            return {"episodes": [{"episode_number": 8, "name": "잘 들어", "overview": "8화 줄거리", "still_path": "/s.jpg"}]}
        if path == "/movie/5":
            return {"id": 5, "title": "호프", "release_date": "2026-07-01", "runtime": 160, "credits": {"crew": [{"job": "Director", "name": "나홍진"}]}}
        return None
    monkeypatch.setattr(tmdb.Tmdb, "_get", fake_get)

    async def run():
        api = tmdb.Tmdb(None, "k" * 32)
        assert await api.find("tv", ["킬러들의 쇼핑몰"]) == 7               # 이름이 맞는 것 (인기순 첫 결과가 아니라)
        assert await api.find("movie", ["호프", "Hope"], "2026") == 5      # 우리말로 못 찾으면 다음 이름으로
        d = await api.details("tv", 7)
        assert d["title"] == "킬러들의 쇼핑몰" and d["rating"] == 8.1 and d["directors"] == ["이권"]
        assert d["poster"].endswith("/w500/p.jpg") and d["cast"][0]["role"] == "정진만"
        assert (await api.season(7, 2))[8]["name"] == "잘 들어"
        assert (await api.details("movie", 5))["directors"] == ["나홍진"]
    asyncio.run(run())

    # 무엇을 찾을지: 시리즈 회차 -> tv, 그 밖 -> movie (우리말 포스터 제목 먼저)
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    telegram_src._migrated.discard(str(tmp_path))
    rows = [(1, "image", "호프 [2026]", ""), (2, "video", "Hope.2026.1080p.AMZN.WEB-DL", "Hope.2026.1080p.AMZN.WEB-DL.mkv"),
            (3, "video", "킬러들의_쇼핑몰_S102E07_x", "a.mkv"), (4, "video", "킬러들의_쇼핑몰_S102E08_y", "b.mkv")]
    with telegram_src.db() as conn:
        conn.executemany(
            "INSERT INTO videos(source,channel,msg,title,caption,file_name,mime,duration,size,width,height,date,has_thumb,kind)"
            " VALUES('tg',1,?,?,'',?,'video/mp4',60,1,1,1,'2026-08-14T10:00:00+00:00',0,?)", [(m, t, f, k) for m, k, t, f in rows])
    telegram_src.link_posters("tg", 1)
    telegram_src.link_series("tg", 1)
    movie = telegram_src.meta_target("tg", 1, msg=2)
    assert movie["kind"] == "movie" and movie["titles"][0] == "호프" and "Hope" in movie["titles"] and movie["year"] == "2026"
    ep = telegram_src.meta_target("tg", 1, msg=4)
    assert (ep["kind"], ep["mkey"], ep["season"], ep["ep"]) == ("tv", "tv:킬러들의쇼핑몰", 2, 8)
    assert telegram_src.meta_target("tg", 1, series_key="킬러들의쇼핑몰")["mkey"] == ep["mkey"]
    telegram_src.meta_put("tg", ep["mkey"], "tv", 7, {"title": "킬러들의 쇼핑몰"})
    assert telegram_src.meta_get("tg", ep["mkey"])["data"]["title"] == "킬러들의 쇼핑몰"


def _zip_bytes(names):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, b"\xff\xd8" + n.encode())
    # "@@"로 적은 이름을 cp949 "가"(2바이트)로 - 옛 압축 프로그램처럼 UTF-8 표시 없이 우리말 이름
    return buf.getvalue().replace(b"@@", "가".encode("cp949"))


def test_webtoon_archives_are_indexed_paged_and_only_in_webtoon_channels(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    telegram_src._migrated.discard(str(tmp_path))
    book = telegram_src.message_to_media(video_msg(5, mime="application/zip", caption="",
                                                    attrs=[DocumentAttributeFilename("나 혼자만 레벨업 12화.zip")]))
    assert book["kind"] == "book" and book["title"] == "나 혼자만 레벨업 12화"
    assert telegram_src.message_to_media(video_msg(6, mime="application/octet-stream", caption="",
                                                   attrs=[DocumentAttributeFilename("a.cbz")]))["kind"] == "book"
    assert telegram_src.message_to_media(video_msg(7, mime="application/x-rar", caption="",
                                                   attrs=[DocumentAttributeFilename("a.rar")])) is None
    # 쪽 순서: 숫자는 크기순, 폴더 안도, 맥 찌꺼기와 그림이 아닌 파일은 빼고. UTF-8 표시 없는 우리말 이름도
    path = tmp_path / "b.zip"
    path.write_bytes(_zip_bytes(["10.jpg", "2.jpg", "1.png", "readme.txt", "__MACOSX/._1.png", "sub/3.webp",
                                 "@@_01.jpg"]))
    names = [n for _i, n in telegram_src.book_pages(str(path))]
    assert names == ["1.png", "2.jpg", "10.jpg", "sub/3.webp", "가_01.jpg"]
    data, mime = telegram_src.book_page(str(path), 1)
    assert data == b"\xff\xd82.jpg" and mime == "image/jpeg"
    with pytest.raises(telegram_src.TgError):
        telegram_src.book_page(str(path), 9)
    # 웹툰 채널에서만 압축 파일이 보이고, 회차는 작품으로 묶인다 (같은 이름의 영상과는 따로)
    _insert([(1, "book", "나 혼자만 레벨업 1화", "나 혼자만 레벨업 1화.zip", 1),
             (2, "book", "나 혼자만 레벨업 2화", "나 혼자만 레벨업 2화.zip", 2),
             (3, "book", "나 혼자만 레벨업 3화", "나 혼자만 레벨업 3화.zip", 3)], channel=1)
    _insert([(1, "book", "자막 묶음", "subs.zip", 1), (2, "video", "영상", "a.mp4", 2)], channel=2)
    telegram_src.link_series("tg", 1)
    kinds = {1: "webtoon"}
    rows, total = telegram_src.query_videos("tg", [1, 2], limit=50, kinds=kinds, group=True)
    assert {(r["channel"], r["msg"]) for r in rows} == {(1, 3), (2, 2)} and total == 2
    rep = next(r for r in rows if r["channel"] == 1)
    assert rep["series"]["key"] == "b:나혼자만레벨업" and rep["series"]["count"] == 3
    s = telegram_src.series_of("tg", 1, 2)
    assert s["prev"]["msg"] == 1 and s["next"]["msg"] == 3
    assert telegram_src.random_video("tg", [1], kinds=kinds) is None
    # 보던 쪽 (쪽 수는 duration) -> 이어보기에도
    telegram_src.save_progress("tg", 1, 2, 4.5, 20)
    assert telegram_src.get_video("tg", 1, 2)["duration"] == 20
    cont = telegram_src.continue_watching("tg", [1])
    assert [(c["msg"], c["position"], c["duration"]) for c in cont] == [(2, 4.5, 20)]
    src = config_store._clean_source({"id": "t", "type": "telegram", "name": "t", "channels": [
        {"id": 1, "title": "a", "kind": "webtoon"}]}, None)
    assert src["channels"] == [{"id": 1, "title": "a", "kind": "webtoon"}]


def test_book_endpoint_downloads_then_serves_pages(env):
    zipdata = _zip_bytes(["001.jpg", "002.jpg", "003.jpg"])
    doc = SimpleNamespace(mime_type="application/zip", size=len(zipdata), thumbs=[], data=zipdata,
                          attributes=[DocumentAttributeFilename("웹툰 1화.zip")])
    env.msgs[1001] = SimpleNamespace(id=1001, document=doc, photo=None, message="",
                                     date=datetime(2026, 1, 3, tzinfo=timezone.utc))
    cfg = config_store.load()
    cfg["sources"][0]["channels"] = [{"id": 777, "title": "웹툰", "kind": "webtoon"}]
    config_store.save(cfg)

    async def flow(c):
        env.authorized = True
        items = (await (await c.get("/api/tg/tg/videos?channel=777&limit=5")).json())["items"]
        assert [(i["msg"], i["kind"]) for i in items] == [(1001, "book")]
        home = await (await c.get("/api/tg/tg/home")).json()
        assert [v["msg"] for row in home["channels"] for v in row["items"]] == [1001]
        for _ in range(50):
            data = await (await c.get("/api/tg/tg/book/777/1001")).json()
            if data["ready"]:
                break
            await asyncio.sleep(0.02)
        assert data["pages"] == ["001.jpg", "002.jpg", "003.jpg"] and data["item"]["title"] == "웹툰 1화"
        r = await c.get("/api/tg/tg/book/777/1001/2")
        assert r.status == 200 and r.content_type == "image/jpeg" and await r.read() == b"\xff\xd8003.jpg"
        assert (await c.get("/api/tg/tg/book/777/1001/3")).status == 404
        assert (await c.get("/api/tg/tg/book/777/999")).status == 404     # 영상은 웹툰으로 안 연다
        # 첫 쪽이 카드 그림으로 (뒤에서)
        for _ in range(50):
            items = (await (await c.get("/api/tg/tg/videos?channel=777&limit=5")).json())["items"]
            if items[0]["has_thumb"]:
                break
            await asyncio.sleep(0.02)
        assert items[0]["has_thumb"] == 1
        r = await c.get("/api/tg/tg/thumb/777/1001")
        assert await r.read() == b"\xff\xd8001.jpg"

    run(flow)


def test_prefetch_scans_once_and_bursts_at_start(env, tmp_path, monkeypatch):
    """미리 받기: 확인한 조각은 다시 디스크를 뒤지지 않고(조각 수에 비례), 시작/탐색 직후엔 더 많이 동시에"""
    from ym_player import media_cache
    B = media_cache.BLOCK
    store = media_cache.STORE
    store.configure({"dir": str(tmp_path / "c"), "max_gb": 1, "ahead_min": 1})
    checks, live, peak = [], [0], [0]
    orig = store.has_cached_sync
    monkeypatch.setattr(store, "has_cached_sync", lambda key, bi: (checks.append(bi), orig(key, bi))[1])

    async def fetch(bi):
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        await asyncio.sleep(0.02)
        live[0] -= 1
        return bytes([bi % 256]) * B

    async def main():
        st = media_cache.streamer("t:burst", B * 120, fetch, bytes_per_sec=B * 60 / 60, concurrency=3, burst=6)
        n = st.ahead_blocks()
        async for _ in st.iter_range(0, 10):
            pass
        for _ in range(100):
            await asyncio.sleep(0.02)
            if st._ahead_task.done():
                break
        assert peak[0] >= 5                       # 처음 몇 조각은 몰아서
        first = len(checks)
        assert first <= n + 2                     # 앞쪽 n조각을 한 번씩만 확인
        # 재생이 앞으로 가며 조각을 하나씩 읽어도, 이미 확인한 곳은 다시 확인하지 않는다
        for bi in range(1, 20):
            async for _ in st.iter_range(B * bi, 10):
                pass
            await asyncio.sleep(0.01)
        for _ in range(100):
            await asyncio.sleep(0.02)
            if st._ahead_task.done():
                break
        assert len(checks) - first <= 19 + 2      # 새로 앞쪽에 생긴 조각만
        assert all(store.has_cached_sync("t:burst", bi) for bi in range(0, 20 + n - 1))

    asyncio.run(main())


def test_probe_head_warms_first_blocks(env):
    from ym_player import media_cache

    async def flow(c):
        env.authorized = True
        await c.get("/api/tg/tg/videos?channel=777&limit=5")
        r = await c.post("/api/tg/tg/probe/777/999?head=1")
        assert r.status == 200
        key = f"tg:tg:777:999:{4096 * 3 + 999}"
        for _ in range(50):
            if media_cache.STORE.has_cached_sync(key, 0):
                break
            await asyncio.sleep(0.02)
        assert media_cache.STORE.has_cached_sync(key, 0)
        assert (await c.post("/api/tg/tg/probe/777/998?head=1")).status == 404   # 영상이 아닌 메시지

    run(flow)


def test_special_episodes_go_last(monkeypatch, tmp_path):
    """특별편·외전·후기·공지는 작품에 붙되 회차 목록 맨 뒤로, 이어 보기도 본편 다음에"""
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    telegram_src._migrated.discard(str(tmp_path))
    assert telegram_src._special_info("나 혼자만 레벨업 특별편", "") == ("나혼자만레벨업", 1, 0)
    assert telegram_src._special_info("나 혼자만 레벨업 외전 3화", "")[2] == 3
    assert telegram_src._special_info("특별수사대 3화", "") is None          # 작품 이름 맨 앞의 "특별"은 특별회차가 아님
    assert telegram_src._special_info("나 혼자만 레벨업 12화", "") is None
    _insert([(1, "book", "나 혼자만 레벨업 1화", "a1.zip", 1),
             (2, "book", "나 혼자만 레벨업 특별편", "sp.zip", 2),        # 본편 사이에 올라온 특별편
             (3, "book", "나 혼자만 레벨업 2화", "a2.zip", 3),
             (4, "book", "나 혼자만 레벨업 휴재 공지", "n.zip", 4),
             (5, "book", "나 혼자만 레벨업 3화", "a3.zip", 5),
             (6, "book", "다른 웹툰 후기", "x.zip", 6)], channel=1)          # 작품이 없는 특별회차는 따로
    telegram_src.link_series("tg", 1)
    eps = telegram_src.series_episodes("tg", 1, "b:나혼자만레벨업")["episodes"]
    assert [(e["msg"], e["special"]) for e in eps] == [(1, False), (3, False), (5, False), (2, True), (4, True)]
    assert telegram_src.series_of("tg", 1, 3)["next"]["msg"] == 5
    assert telegram_src.series_of("tg", 1, 5)["next"]["msg"] == 2          # 본편 끝 -> 특별회차
    assert telegram_src.series_of("tg", 1, 2)["next"]["msg"] == 4 and telegram_src.series_of("tg", 1, 4)["next"] is None
    assert telegram_src.series_of("tg", 1, 2)["prev"]["msg"] == 5 and telegram_src.series_of("tg", 1, 4)["prev"]["msg"] == 2
    rows, _ = telegram_src.query_videos("tg", [1], limit=50, kinds={1: "webtoon"}, group=True)
    by = {r["msg"]: r for r in rows}
    assert set(by) == {5, 6} and by[5]["series"]["count"] == 5            # 대표는 본편 최신 회차


def test_book_opens_from_index_without_full_download(env, monkeypatch):
    """큰 압축 파일도 목차(맨 끝)만 받아 쪽 목록을 주고, 보는 쪽만 받는다. ?warm=1은 미리 받기만"""
    import io
    import os as _os
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for i in range(1, 31):
            z.writestr(f"{i:03d}.jpg", b"\xff\xd8" + _os.urandom(400 * 1024))   # 30쪽 x 400KB = 약 12MB
    data = buf.getvalue()
    doc = SimpleNamespace(mime_type="application/zip", size=len(data), thumbs=[1], data=data,
                          attributes=[DocumentAttributeFilename("큰 웹툰 1화.zip")])
    env.msgs[1001] = SimpleNamespace(id=1001, document=doc, photo=None, message="", date=datetime(2026, 1, 3, tzinfo=timezone.utc))
    doc2 = SimpleNamespace(**{**doc.__dict__, "attributes": [DocumentAttributeFilename("큰 웹툰 2화.zip")]})
    env.msgs[1002] = SimpleNamespace(id=1002, document=doc2, photo=None, message="", date=datetime(2026, 1, 4, tzinfo=timezone.utc))
    cfg = config_store.load()
    cfg["sources"][0]["channels"] = [{"id": 777, "title": "웹툰", "kind": "webtoon"}]
    config_store.save(cfg)
    fetched = []
    orig = env.iter_download

    def counting(doc, offset=0, **kw):
        fetched.append((getattr(doc, "attributes")[0].file_name, offset))
        return orig(doc, offset=offset, **kw)
    monkeypatch.setattr(env, "iter_download", counting)

    async def flow(c):
        env.authorized = True
        await c.get("/api/tg/tg/videos?channel=777&limit=5")
        data1 = await (await c.get("/api/tg/tg/book/777/1001")).json()
        assert data1["ready"] and len(data1["pages"]) == 30
        blocks = len([f for f in fetched if f[0] == "큰 웹툰 1화.zip"])
        assert blocks <= 3                                 # 12MB 중 끝부분 조각만 (512KB 단위)
        r = await c.get("/api/tg/tg/book/777/1001/14")
        body = await r.read()
        assert r.status == 200 and len(body) == 400 * 1024 + 2
        # 그 쪽이 있는 부분부터 받는다 (그 뒤는 앞쪽 미리 받기)
        offsets = [o for name, o in fetched[blocks:] if name == "큰 웹툰 1화.zip"]
        assert offsets and min(o for o in offsets if o > 0) <= 14 * 400 * 1024 + 512 * 1024
        # 다음 화 미리 받기: 바로 답하고 뒤에서 목차와 앞쪽을
        r = await c.get("/api/tg/tg/book/777/1002?warm=1")
        assert r.status == 200 and (await r.json())["success"]
        for _ in range(100):
            if (777, 1002) in env_tg(c)._zips:
                break
            await asyncio.sleep(0.02)
        assert (777, 1002) in env_tg(c)._zips

    def env_tg(c):
        from ym_player import server
        return c.server.app[server.TELEGRAM]["tg"]

    run(flow)


YAML_SAMPLE = """title: 나 혼자만 레벨업
author:
  - 추공
  - 장성락
genre: [액션, 판타지]
status: 완결
platform: 카카오페이지
description: |
  10년 전, 다른 차원과 이쪽 세계를 이어 주는 통로 '게이트'가 열리고
  평범한 이들 중 각성한 사람들이 생겨났다.
cover: https://example.com/cover.jpg
연재요일: 목요일
번역: 없음
"""


def test_meta_yaml_parse_and_mini_parser(monkeypatch):
    m = telegram_src.parse_meta(YAML_SAMPLE.encode())
    assert m["title"] == "나 혼자만 레벨업" and m["authors"] == ["추공", "장성락"] and m["genres"] == ["액션", "판타지"]
    assert m["status"] == "완결" and m["platform"] == "카카오페이지" and m["day"] == "목요일"
    assert m["summary"].startswith("10년 전") and m["cover"] == "https://example.com/cover.jpg"
    assert ["번역", "없음"] in m["extra"]
    # PyYAML이 없어도 같은 결과 (간단한 내장 파서)
    import builtins
    real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", lambda name, *a, **k: (_ for _ in ()).throw(ImportError()) if name == "yaml" else real_import(name, *a, **k))
    m2 = telegram_src.parse_meta(YAML_SAMPLE.encode())
    assert m2["authors"] == m["authors"] and m2["genres"] == m["genres"] and m2["summary"].startswith("10년 전") and m2["day"] == "목요일"
    with pytest.raises(telegram_src.TgError):
        telegram_src.parse_meta(b"- 1\n- 2\n")


def test_series_shows_yaml_file_meta(env):
    def doc_msg(mid, name, data, mime):
        doc = SimpleNamespace(mime_type=mime, size=len(data), thumbs=[], data=data, attributes=[DocumentAttributeFilename(name)])
        return SimpleNamespace(id=mid, document=doc, photo=None, message="", date=datetime(2026, 1, 3, tzinfo=timezone.utc) + timedelta(minutes=mid))
    for i, mid in enumerate((1001, 1002, 1003), start=1):
        env.msgs[mid] = doc_msg(mid, f"나 혼자만 레벨업 {i}화.zip", _zip_bytes(["001.jpg"]), "application/zip")
    env.msgs[1004] = doc_msg(1004, "나 혼자만 레벨업.yaml", YAML_SAMPLE.encode(), "application/x-yaml")

    async def subtitle_bytes(self, channel, msg):   # 작은 파일 받기 (가짜 텔레그램은 download_media가 그림을 줌)
        return env.msgs[msg].document.data
    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(telegram_src.TelegramSource, "subtitle_bytes", subtitle_bytes)
    cfg = config_store.load()
    cfg["sources"][0]["channels"] = [{"id": 777, "title": "웹툰", "kind": "webtoon"}]
    config_store.save(cfg)

    async def flow(c):
        env.authorized = True
        items = (await (await c.get("/api/tg/tg/videos?channel=777&limit=10&group=1")).json())["items"]
        assert [i.get("kind") for i in items] == ["book"]                     # yaml 파일은 목록에 안 나옴
        key = items[0]["series"]["key"]
        data = await (await c.get(f"/api/tg/tg/series/777?key={key}")).json()
        fm = data["file_meta"]
        assert fm["title"] == "나 혼자만 레벨업" and fm["authors"] == ["추공", "장성락"] and fm["status"] == "완결"

    try:
        run(flow)
    finally:
        mp.undo()


def test_yaml_matches_hashtag_series_without_id(monkeypatch, tmp_path):
    """회차 캡션이 "#천하제일_곤륜객잔_63646168 12화"(키에 작품 ID가 붙음)여도 "천하제일 곤륜객잔.yaml"을 찾는다 (Kavita 꼴)"""
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    telegram_src._migrated.discard(str(tmp_path))
    _insert([(1, "book", "#천하제일_곤륜객잔_63646168 1화", "001.zip", 1),
             (2, "book", "#천하제일_곤륜객잔_63646168 2화", "002.zip", 2),
             (3, "meta", "천하제일 곤륜객잔", "천하제일 곤륜객잔.yaml", 3),
             (4, "meta", "다른 작품", "다른 작품.yaml", 4)], channel=1)
    with telegram_src.db() as conn:
        conn.execute("UPDATE videos SET caption=? WHERE msg=3", ("#천하제일_곤륜객잔_63646168\n작품 정보",))
    telegram_src.link_series("tg", 1)
    eps = telegram_src.series_episodes("tg", 1, "b:천하제일곤륜객잔63646168")
    assert eps and eps["title"] == "천하제일 곤륜객잔"                       # 보이는 이름에서 #과 작품 ID는 뺀다
    assert telegram_src.series_meta_file("tg", 1, "b:천하제일곤륜객잔63646168") == 3
    assert telegram_src.series_meta_file("tg", 1, "b:없는작품") is None
    kavita = """name: 천하제일 곤륜객잔
localizedName: 천하제일 곤륜객잔
summary: 곤륜산 아래 객잔 이야기
genres:
  - 무협
writers: [작가A]
publicationStatus: Ongoing
ageRating: 15
releaseYear: 2023
webLinks:
  - https://webtoon.kakao.com/content/63646168
"""
    m = telegram_src.parse_meta(kavita.encode())
    assert m["title"] == "천하제일 곤륜객잔" and m["authors"] == ["작가A"] and m["status"] == "연재중"
    assert m["age"] == "15" and m["year"] == "2023" and m["url"].startswith("https://webtoon.kakao.com")


DOWNLOADER_YAML = """action:
    all_file_is_special: false
    code: "KKP63646168"
    first_cover: false
files:
    "천하제일 곤륜객잔 0001화#105.zip":
        cover: "/9j/4AAQSkZJRgABAgAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        page: 105
        wordcount: 0
    "천하제일 곤륜객잔 0002화#118.zip":
        cover: "FIRST"
        page: 118
        wordcount: 0
meta:
    Age Rating: "3"
    Collections: ""
    Day: "02"
    Genres: "무협"
    Language: "ko"
    Month: "03"
    Name: "천하제일 곤륜객잔"
    Person Character: ""
    Person Publisher: "카카오페이지"
    Person Writers: "김자까, 김준태, 백보"
    Publication Status: "0"
    Release Date: "20240302"
    Summary: "[매주 일요일 연재]\\n\\n“누구시라고요?”\\n“운룡일세.”"
    Tags: "카카오페이지,무협"
    Web Links: "https://page.kakao.com/content/63646168"
    Writer: ""
    Year: "2024"
search:
-   Day: "02"
    Month: "03"
    Publication Status: "0"
    Release Date: "20240302"
    Year: "2024"
    author: "김자까, 김준태, 백보"
    code: "KKP63646168"
    description: "다른 설명"
    genre: "무협"
    illustrator: ""
    link: "https://page.kakao.com/content/63646168"
    poster_url: "https://page-images.kakaoentcdn.com/download/resource?kid=abc"
    publisher: "카카오페이지"
    score: 100
    tag: "카카오페이지,무협"
    title: "천하제일 곤륜객잔"
"""


def test_downloader_yaml_meta_and_search():
    m = telegram_src.parse_meta(DOWNLOADER_YAML.encode())
    assert m["title"] == "천하제일 곤륜객잔" and m["authors"] == ["김자까", "김준태", "백보"]
    assert m["genres"] == ["무협"] and m["tags"] == ["카카오페이지", "무협"] and m["platform"] == "카카오페이지"
    assert m["status"] == "연재중" and m["age"] == "전체 이용가" and m["year"] == "2024-03-02" and m["language"] == "한국어"
    assert m["summary"].startswith("[매주 일요일 연재]")                  # meta가 먼저 (search의 설명은 빈 칸일 때만)
    assert m["cover"].startswith("https://page-images.kakaoentcdn.com")   # search의 poster_url
    assert m["url"] == "https://page.kakao.com/content/63646168"
    assert m["day"] == "일요일"                                            # "Day: 02"(날짜)가 아니라 줄거리의 "[매주 일요일 연재]"에서
    keys = {k for k, _v in m["extra"]}
    assert not keys & {"action", "files", "score", "code", "Month", "Day", "Collections"}
    # 표지 주소가 없으면 files의 첫 회차 표지(base64)를
    m2 = telegram_src.parse_meta(DOWNLOADER_YAML.replace("    poster_url:", "    poster_x:").encode())
    assert m2["cover"].startswith("data:image/jpeg;base64,/9j/")


def test_series_card_uses_yaml_cover(env):
    import base64 as b64
    jpg = b"\xff\xd8\xff\xe0" + b"COVER" * 60
    yaml_text = ('meta:\n    Name: "나 혼자만 레벨업"\nfiles:\n    "나 혼자만 레벨업 0001화#3.zip":\n        cover: "'
                 + b64.b64encode(jpg).decode() + '"\n').encode()

    def doc_msg(mid, name, data, mime):
        doc = SimpleNamespace(mime_type=mime, size=len(data), thumbs=[], data=data, attributes=[DocumentAttributeFilename(name)])
        return SimpleNamespace(id=mid, document=doc, photo=None, message="", date=datetime(2026, 1, 3, tzinfo=timezone.utc) + timedelta(minutes=mid))
    for i, mid in enumerate((1001, 1002), start=1):
        env.msgs[mid] = doc_msg(mid, f"나 혼자만 레벨업 {i}화.zip", _zip_bytes(["001.jpg"]), "application/zip")
    env.msgs[1003] = doc_msg(1003, "나 혼자만 레벨업.yaml", yaml_text, "application/x-yaml")

    async def subtitle_bytes(self, channel, msg):
        return env.msgs[msg].document.data
    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(telegram_src.TelegramSource, "subtitle_bytes", subtitle_bytes)
    cfg = config_store.load()
    cfg["sources"][0]["channels"] = [{"id": 777, "title": "웹툰", "kind": "webtoon"}]
    config_store.save(cfg)

    async def flow(c):
        env.authorized = True
        items = (await (await c.get("/api/tg/tg/videos?channel=777&limit=10&group=1")).json())["items"]
        s = items[0]["series"]
        assert s["meta"] == 1003
        r = await c.get(f"/api/tg/tg/series-cover/777?key={s['key']}")
        assert r.status == 200 and r.content_type == "image/jpeg" and await r.read() == jpg
        assert (await c.get("/api/tg/tg/series-cover/777?key=b:없는작품")).status == 404

    try:
        run(flow)
    finally:
        mp.undo()


def test_shrink_image_makes_small_jpeg_and_crops_tall_pages():
    import shutil as _sh
    if not (_sh.which("ffmpeg") or __import__("importlib").util.find_spec("PIL")):
        pytest.skip("ffmpeg/Pillow 없음")
    import io
    import struct
    import zlib
    # 1000x6000 PNG (긴 웹툰 쪽 흉내) - 줄무늬라 압축이 덜 되게
    w, h = 1000, 6000
    rows = b"".join(b"\x00" + bytes((x * 7 + y) % 256 for x in range(w)) * 3 for y in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows, 1)) + chunk(b"IEND", b"")
    out = asyncio.run(telegram_src.shrink_image(png, width=360, max_ratio=1.5))
    assert out.startswith(b"\xff\xd8") and len(out) < len(png) and len(out) < 200 * 1024
    # 크기 확인 (JPEG SOF0: 높이, 폭)
    i = out.index(b"\xff\xc0")
    hh, ww = struct.unpack(">HH", out[i + 5:i + 9])
    assert ww == 360 and hh == 540                                  # 위쪽 1000x1500만 잘라 360x540
    assert asyncio.run(telegram_src.shrink_image(b"not an image")) == b"not an image"   # 못 줄이면 그대로


def test_series_cover_downloads_whole_image(env):
    """표지 주소의 그림을 끝까지 받는다 (조각으로 오는 큰 그림이 앞부분만 저장되던 문제)"""
    import struct
    import zlib
    from aiohttp import web as _web
    from aiohttp.test_utils import TestServer
    import os as _os
    w, h = 600, 900
    rows = b"".join(b"\x00" + _os.urandom(w * 3) for _y in range(h))        # 압축이 안 되게 (약 1.6MB)
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows, 1)) + chunk(b"IEND", b"")

    async def cover(request):
        resp = _web.StreamResponse(headers={"Content-Type": "image/png", "Content-Length": str(len(png))})
        await resp.prepare(request)
        for i in range(0, len(png), 50_000):          # 조각으로 천천히
            await resp.write(png[i:i + 50_000])
            await asyncio.sleep(0.01)
        return resp

    def doc_msg(mid, name, data, mime):
        doc = SimpleNamespace(mime_type=mime, size=len(data), thumbs=[], data=data, attributes=[DocumentAttributeFilename(name)])
        return SimpleNamespace(id=mid, document=doc, photo=None, message="", date=datetime(2026, 1, 3, tzinfo=timezone.utc) + timedelta(minutes=mid))
    for i, mid in enumerate((1001, 1002), start=1):
        env.msgs[mid] = doc_msg(mid, f"나 혼자만 레벨업 {i}화.zip", _zip_bytes(["001.jpg"]), "application/zip")

    async def subtitle_bytes(self, channel, msg):
        return env.msgs[msg].document.data
    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(telegram_src.TelegramSource, "subtitle_bytes", subtitle_bytes)
    cfg = config_store.load()
    cfg["sources"][0]["channels"] = [{"id": 777, "title": "웹툰", "kind": "webtoon"}]
    config_store.save(cfg)

    async def flow(c):
        app = _web.Application()
        app.router.add_get("/cover.png", cover)
        origin = TestServer(app)
        await origin.start_server()
        try:
            url = str(origin.make_url("/cover.png"))
            env.msgs[1003] = doc_msg(1003, "나 혼자만 레벨업.yaml", f'search:\n-   title: 나 혼자만 레벨업\n    poster_url: "{url}"\n'.encode(), "application/x-yaml")
            env.authorized = True
            items = (await (await c.get("/api/tg/tg/videos?channel=777&limit=10&group=1")).json())["items"]
            key = items[0]["series"]["key"]
            r = await c.get(f"/api/tg/tg/series-cover/777?key={key}")
            body = await r.read()
            assert r.status == 200 and len(body) > 5000
            if body.startswith(b"\xff\xd8"):                   # 줄인 JPEG면 끝 표시(EOI)까지 있어야
                assert body.rstrip(b"\x00").endswith(b"\xff\xd9")
            else:                                             # 못 줄였으면 원본 그대로 (잘리지 않고)
                assert body == png
        finally:
            await origin.close()

    try:
        run(flow)
    finally:
        mp.undo()
