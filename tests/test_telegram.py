"""텔레그램 소스 - 메시지 판별, Range 해석, 그리고 가짜 Telethon 클라이언트로 HTTP 흐름 전체.

진짜 텔레그램에는 붙지 않는다. TelegramSource.get_client를 가짜 클라이언트로 바꿔서
로그인(코드 -> 2단계 비밀번호) / 채널 / 색인(오래된 쪽 채우기) / 스트림 Range / 썸네일 / 이어보기를 확인한다.
"""
import asyncio
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

    async def download_media(self, msg, file=bytes, thumb=None):
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
    """512KB 경계로 받아 앞/뒤 조각은 기억 - 어떤 Range든 내용이 같고, 두 번째 요청은 텔레그램에 안 간다."""
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
        calls.clear()
        assert await read(10, B * 2) == data[10:10 + B * 2]           # 앞쪽 - 기억해 둔 것
        assert await read(size - 5000, 5000) == data[-5000:]          # 끝쪽(mp4 moov) - 기억해 둔 것
        assert calls == []
        assert len([k for k in tg._blocks]) == telegram_src.HEAD_BLOCKS + telegram_src.TAIL_BLOCKS

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
