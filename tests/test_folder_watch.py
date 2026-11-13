"""자동 올리기 "폴더" 규칙 (rclone 드라이브): 설정 검사, 파일 이름 -> 캡션, 인코딩 계획, 새 파일만 올리기, 자막 입히기, rclone 원격."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from ym_player import config_store, folder_watch, plex_to_tg

pytest.importorskip("aiohttp")
pytest.importorskip("telethon")

from test_plex_to_tg import SRT, FakeTg  # noqa: E402


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path / "data"))
    os.makedirs(tmp_path / "data")
    return tmp_path


def test_settings_and_names():
    rule = plex_to_tg.clean_settings({"rules": [{"kind": "folder", "folder": "/mnt/gdrive/영화/", "tg": "tg", "channel": "-100"}]})["rules"][0]
    assert rule["kind"] == "folder" and rule["folder"] == "/mnt/gdrive/영화" and rule["section_title"] == "영화"
    assert rule["height"] == 720 and rule["plex"] == "" and "shrink_gb" not in rule
    remote = plex_to_tg.clean_settings({"rules": [{"kind": "folder", "folder": "gdrive:드라마", "tg": "tg", "channel": "-1", "height": 333}]})["rules"][0]
    assert remote["folder"] == "gdrive:드라마" and folder_watch.is_remote("gdrive:드라마") and remote["height"] == 720
    assert not folder_watch.is_remote("/mnt/a:b")
    for bad in ("", "영화", "-x:y", "relative/path"):
        with pytest.raises(ValueError):
            plex_to_tg.clean_settings({"rules": [{"kind": "folder", "folder": bad, "tg": "tg", "channel": "-1"}]})
    old = plex_to_tg.clean_settings({"rules": [{"plex": "px", "section": "1", "tg": "tg", "channel": "-1"}]})["rules"][0]
    assert old["kind"] == "plex"                                        # 예전 규칙은 Plex 그대로

    f = folder_watch.file_fields("드라마/Hope.S01E02.1080p.WEB-DL.H264.mkv", "드라마")
    assert f["name"] == "Hope S01E02" and f["season"] == 1 and f["episode"] == 2 and f["filename"] == "Hope.S01E02.1080p.WEB-DL.H264.mkv"
    assert folder_watch.file_fields("영화 제목.2024.1080p.BluRay.mkv")["name"] == "영화 제목 (2024)"
    assert folder_watch.file_fields("나 혼자 산다.E567.241010.720p-NEXT.mp4")["name"] == "나 혼자 산다 E567"
    assert folder_watch.file_fields("그냥영상.mp4")["name"] == "그냥영상"
    assert plex_to_tg.make_caption("{name}\n\n{summary}", f) == "Hope S01E02"
    assert folder_watch._parse_time("2026-11-10T01:02:03.123456789+09:00") == folder_watch._parse_time("2026-11-09T16:02:03Z")
    assert folder_watch._parse_time("모름") == 0


def _info(vcodec="h264", pix="yuv420p", height=1080, acodec="aac", profile="High"):
    st = [{"codec_type": "video", "codec_name": vcodec, "pix_fmt": pix, "height": height, "width": height * 16 // 9, "profile": profile}]
    if acodec:
        st.append({"codec_type": "audio", "codec_name": acodec})
    return {"streams": st, "format": {"duration": "100.0"}}


def test_encode_plan():
    plan = folder_watch.encode_plan
    assert plan(_info(height=720), ".mp4", 720) is None                            # 바로 재생되는 720p mp4 -> 그대로
    args, notes = plan(_info(height=720), ".mkv", 720)                             # mkv -> mp4로 다시 담기만
    assert args[args.index("-c:v") + 1] == "copy" and args[args.index("-c:a") + 1] == "copy" and notes == ["mp4로 다시 담음"]
    args, notes = plan(_info(), ".mp4", 720)                                        # 1080p는 용량과 상관없이 720p로
    assert "scale=-2:720" in args[args.index("-vf") + 1] and args[args.index("-c:v") + 1] == "libx264" and "720p로 줄임" in notes
    assert args[args.index("-c:a") + 1] == "copy"
    assert plan(_info(), ".mp4", 0) is None                                         # 원본 그대로
    args, notes = plan(_info(vcodec="hevc", acodec="eac3", height=720), ".mkv", 720)   # HEVC/E-AC3 -> H.264/AAC
    assert args[args.index("-c:v") + 1] == "libx264" and args[args.index("-c:a") + 1] == "aac" and "-vf" not in args
    args, _n = plan(_info(pix="yuv420p10le", profile="High 10", height=720), ".mp4", 720)   # 10비트 H.264도 다시
    assert args[args.index("-c:v") + 1] == "libx264"
    args, notes = plan(_info(height=480), ".mp4", 720, "burn.srt")                  # 자막 입히기
    assert args[args.index("-vf") + 1] == "subtitles=filename=burn.srt" and "자막 입힘" in notes
    args, _n = plan(_info(), ".mkv", 720, "burn.srt")
    assert args[args.index("-vf") + 1] == "scale=-2:720,subtitles=filename=burn.srt"    # 줄인 뒤에 자막 (글자 크기가 화면에 맞게)
    assert "-movflags" in args and "+faststart" in args
    with pytest.raises(folder_watch.FolderError):
        plan({"streams": [{"codec_type": "audio", "codec_name": "aac"}]}, ".mp4", 720)


def _make(path, seconds=3, size="320x240", vcodec="libx264", audio=True, color=None):
    src = f"color=c={color}:size={size}:rate=25:duration={seconds}" if color else f"testsrc=size={size}:rate=25:duration={seconds}"
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    args += ["-c:v", vcodec, "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac" if str(path).endswith(".mp4") else "mp3", "-shortest"]
    subprocess.run(args + [str(path)], check=True)


def _probe(body, tmp_path):
    p = tmp_path / f"probe{time.time_ns()}.mp4"
    p.write_bytes(body)
    return json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(p)],
                                     capture_output=True, text=True).stdout)


def test_folder_rule_uploads_new_files(data, tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg 없음")
    from ym_player import telegram_src
    telegram_src._migrated.discard(str(data / "data"))
    monkeypatch.setattr(plex_to_tg, "_KO_FONT", True)
    drive = tmp_path / "drive"
    (drive / "드라마").mkdir(parents=True)
    _make(drive / "옛날.mp4")
    cfg = config_store.load()
    cfg["plex_to_tg"] = plex_to_tg.clean_settings({"rules": [{"id": "f", "kind": "folder", "folder": str(drive), "tg": "tg", "channel": "-100",
                                                               "wait_subs": False}]})
    config_store.save(cfg)
    tg = FakeTg()
    mirror = plex_to_tg.Mirror(None, None, lambda sid: tg, lambda: None)

    async def main():
        st = plex_to_tg.load_settings()
        await mirror.check_all(st)                                         # 처음: 기준만 (옛날.mp4는 안 올림)
        assert mirror.queue == [] and plex_to_tg.load_state()["rules"]["f"]["known"]
        # 새 파일: mkv(H.264+MP3) + 같은 이름의 SRT (CP949), 이미 바로 재생되는 mp4 하나
        ep = drive / "드라마" / "Hope.S01E02.720p.mkv"
        _make(ep, color="gray")
        (drive / "드라마" / "Hope.S01E02.720p.ko.srt").write_bytes(SRT.encode("cp949"))
        ready = drive / "Ready.2026.mp4"
        _make(ready)
        old = time.time() - 3600
        os.utime(ep, (old, old))                                           # 오래전에 다 올라온 것
        await mirror.check_all(st)
        assert [j["key"] for j in mirror.queue] == [os.path.join("드라마", "Hope.S01E02.720p.mkv")]   # Ready는 방금 생겨 다음 확인 때
        await mirror.check_all(st)                                         # 크기가 그대로 -> 다 올라온 것으로
        assert [j["title"] for j in mirror.queue] == ["Hope S01E02", "Ready (2026)"]
        await mirror.run_queue(st)
        hist = plex_to_tg.load_state()["history"]
        assert [h["ok"] for h in hist] == [True, True], hist
        assert [x["name"] for x in tg.sent] == ["Hope.S01E02.720p.mp4", "Ready.2026.mp4"]
        assert all(x["stream"] is True and x["thumb"] for x in tg.sent)
        assert tg.sent[0]["caption"] == "Hope S01E02"
        burned = _probe(tg.sent[0]["body"], tmp_path)
        assert [s["codec_name"] for s in burned["streams"]] == ["h264", "aac"]   # 자막은 화면에, 소리는 AAC로 (텔레그램 앱은 MP3 소리를 못 낼 때가 있다)
        assert "자막 입힘" in hist[1]["note"] and hist[1]["subs"] == "화면에 입힘"
        assert tg.sent[1]["body"] == ready.read_bytes() and hist[0]["note"].startswith("그대로 올림")
        assert not os.listdir(data / "data" / "plex_to_tg_tmp")             # 임시 파일은 지움
        await mirror.check_all(st)                                         # 올린 것은 다시 안 올린다
        assert mirror.queue == []
    asyncio.run(main())


def test_folder_rule_waits_for_subs_and_scales(data, tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg 없음")
    drive = tmp_path / "drive"
    drive.mkdir()
    tg = FakeTg()
    mirror = plex_to_tg.Mirror(None, None, lambda sid: tg, lambda: None)
    big = drive / "Big.Movie.2025.mkv"
    _make(big, size="640x480", vcodec="libx264")
    rule = plex_to_tg.clean_settings({"rules": [{"id": "w", "kind": "folder", "folder": str(drive), "tg": "tg", "channel": "-1",
                                                  "height": 480}]})["rules"][0]
    assert rule["wait_subs"] is True

    async def main():
        with pytest.raises(plex_to_tg.Waiting):                            # 자막이 없으면 기다린다
            await mirror.upload(rule, "Big.Movie.2025.mkv", 2)
        note = await mirror.upload(rule, "Big.Movie.2025.mkv", 2, test=True)   # 시험(또는 지금 올리기)은 기다리지 않음
        assert "자막 없음" in note and tg.sent[0]["name"] == "Big.Movie.2025.mp4"
        v = [s for s in _probe(tg.sent[0]["body"], tmp_path)["streams"] if s["codec_type"] == "video"][0]
        assert v["codec_name"] == "h264" and v["height"] == 480                     # 줄일 높이와 같으면 그대로 (480p 이하)
        rule["height"] = 720
        _make(drive / "Wide.mkv", size="1280x960")
        tg.sent.clear()
        note = await mirror.upload(rule, "Wide.mkv", 2, test=True)
        v = [s for s in _probe(tg.sent[0]["body"], tmp_path)["streams"] if s["codec_type"] == "video"][0]
        assert v["height"] == 720 and v["width"] == 960 and "720p로 줄임" in note
        with pytest.raises(folder_watch.FolderError):                       # 폴더 밖은 안 된다
            await mirror.upload(rule, "../x.mkv", 2, test=True)
    asyncio.run(main())


FAKE_RCLONE = r'''
import json, os, shutil, sys
root = os.environ["FAKE_RCLONE_ROOT"]
args = [a for a in sys.argv[1:] if a != "--"]
if "--config" in args:
    i = args.index("--config")
    with open(os.environ["FAKE_RCLONE_LOG"], "a") as f:
        f.write(args[i + 1] + "\n")
    del args[i:i + 2]
def real(remote):
    assert remote.startswith("fake:"), remote
    return os.path.join(root, remote[5:].lstrip("/"))
cmd = args[0]
if cmd == "listremotes":
    print("fake:")
elif cmd == "lsjson":
    target = real(args[-1])
    rec = "-R" in args
    out = []
    for dp, dn, fn in os.walk(target):
        if "--dirs-only" in args:
            print(json.dumps([{"Path": d, "Name": d, "IsDir": True} for d in sorted(dn)]))
            sys.exit(0)
        for n in fn:
            p = os.path.join(dp, n)
            out.append({"Path": os.path.relpath(p, target), "Name": n, "Size": os.path.getsize(p),
                        "ModTime": "2020-01-01T00:00:00.000000000Z", "IsDir": False})
        if not rec:
            break
    print(json.dumps(out))
elif cmd == "copyto":
    shutil.copy(real(args[1]), args[2])
elif cmd == "cat":
    n = int(args[args.index("--count") + 1])
    with open(real(args[-1]), "rb") as f:
        sys.stdout.buffer.write(f.read(n))
'''


def test_rclone_remote(data, tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg 없음")
    monkeypatch.setattr(plex_to_tg, "_KO_FONT", True)
    root = tmp_path / "remote"
    (root / "영화").mkdir(parents=True)
    script = tmp_path / "rclone"
    script.write_text(f"#!{sys.executable}\n" + FAKE_RCLONE)
    script.chmod(0o755)
    monkeypatch.setenv("MP_RCLONE", str(script))
    monkeypatch.setenv("FAKE_RCLONE_ROOT", str(root))
    monkeypatch.setenv("FAKE_RCLONE_LOG", str(tmp_path / "rclone.log"))
    _make(root / "영화" / "Old.mkv")
    cfg = config_store.load()
    cfg["plex_to_tg"] = plex_to_tg.clean_settings({"rules": [{"id": "r", "kind": "folder", "folder": "fake:영화", "tg": "tg", "channel": "-1"}]})
    config_store.save(cfg)
    tg = FakeTg()
    mirror = plex_to_tg.Mirror(None, None, lambda sid: tg, lambda: None)

    async def main():
        st = plex_to_tg.load_settings()
        await mirror.check_all(st)
        assert mirror.queue == []
        _make(root / "영화" / "New.Film.2026.mkv", color="gray")
        (root / "영화" / "New.Film.2026.srt").write_text(SRT, encoding="utf-8")
        await mirror.check_all(st)                                        # 원격 시각이 오래돼 바로 줄에
        assert [j["title"] for j in mirror.queue] == ["New Film (2026)"]
        await mirror.run_queue(st)
        hist = plex_to_tg.load_state()["history"]
        assert hist[0]["ok"], hist
        assert [x["name"] for x in tg.sent] == ["New.Film.2026.mp4"] and hist[0]["subs"] == "화면에 입힘"
        with pytest.raises(ValueError):
            await mirror.screenshot("r", "New.Film.2026.mkv")
        title = await mirror.test_latest("r")                            # (가짜 rclone은 시각이 다 같다)
        assert title in ("New Film (2026)", "Old") and mirror.queue[-1]["test"]
    asyncio.run(main())


def test_options_lists_remotes(data, monkeypatch, tmp_path):
    from aiohttp import CookieJar
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import auth, server
    script = tmp_path / "rclone"
    script.write_text(f"#!{sys.executable}\n" + FAKE_RCLONE)
    script.chmod(0o755)
    monkeypatch.setenv("MP_RCLONE", str(script))
    monkeypatch.setenv("FAKE_RCLONE_ROOT", str(tmp_path))
    conf = tmp_path / "rclone.conf"
    conf.write_text("[fake]\ntype = local\n")
    monkeypatch.setenv("MP_RCLONE_CONFIG", str(conf))
    monkeypatch.setenv("FAKE_RCLONE_LOG", str(tmp_path / "rclone.log"))
    (tmp_path / "영화" / "한국").mkdir(parents=True)
    (tmp_path / "영화" / ".숨김").mkdir()
    monkeypatch.delenv("MP_USER", raising=False)
    monkeypatch.delenv("MP_PASSWORD", raising=False)
    monkeypatch.setenv("MP_UPDATE_CHECK", "0")
    auth._fails.clear()
    auth.create_user("boss", "secret1", "admin")

    async def main():
        c = TestClient(TestServer(server.create_app(require_auth=True)), cookie_jar=CookieJar(unsafe=True))
        await c.start_server()
        try:
            await c.post("/api/auth/login", json={"username": "boss", "password": "secret1"})
            d = await (await c.get("/api/admin/plex-tg/options")).json()
            assert d["rclone"] is True and d["remotes"] == ["fake:"] and d["rclone_note"] == ""
            assert str(conf) in (tmp_path / "rclone.log").read_text()          # 다른 계정의 rclone 설정도 --config로
            d = await (await c.get("/api/admin/plex-tg/folders")).json()     # 폴더 고르기: 처음은 드라이브 목록
            assert d["remotes"] == ["fake:"] and isinstance(d["mounts"], list)
            d = await (await c.get("/api/admin/plex-tg/folders", params={"path": "fake:영화"})).json()
            assert d["dirs"] == ["한국"]                                       # 숨김 폴더는 빼고
            d = await (await c.get("/api/admin/plex-tg/folders", params={"path": str(tmp_path / "영화")})).json()
            assert d["dirs"] == ["한국"]
            r = await c.get("/api/admin/plex-tg/folders", params={"path": str(tmp_path / "없음")})
            assert r.status == 400 and "열 수 없습니다" in (await r.json())["error"]
            r = await c.put("/api/admin/plex-tg", json={"rules": [{"kind": "folder", "folder": "fake:영화", "tg": "t", "channel": "-1"}]})
            assert r.status == 200 and (await r.json())["settings"]["rules"][0]["kind"] == "folder"
            r = await c.put("/api/admin/plex-tg", json={"rules": [{"kind": "folder", "folder": "영화", "tg": "t", "channel": "-1"}]})
            assert r.status == 400
        finally:
            await c.close()
    asyncio.run(main())


def test_rclone_mounts_and_config(tmp_path, monkeypatch):
    mounts = tmp_path / "mounts"
    mounts.write_text("proc /proc proc rw 0 0\n"
                      "gdrive: /mnt/gdrive fuse.rclone rw,nosuid,nodev,user_id=0 0 0\n"
                      "내드라이브:영화 /mnt/my\\040drive fuse.rclone rw 0 0\n"
                      "/dev/sda1 / ext4 rw 0 0\n")
    assert folder_watch.rclone_mounts(str(mounts)) == [{"remote": "gdrive:", "path": "/mnt/gdrive"},
                                                         {"remote": "내드라이브:영화", "path": "/mnt/my drive"}]
    assert folder_watch.rclone_mounts(str(tmp_path / "없음")) == []
    conf = tmp_path / "r.conf"
    conf.write_text("")
    monkeypatch.setenv("MP_RCLONE_CONFIG", str(conf))
    monkeypatch.setenv("MP_RCLONE", sys.executable)
    assert folder_watch.rclone_cmd("listremotes") == [sys.executable, "--config", str(conf), "listremotes"]


def test_encoders_and_fallback(data, tmp_path, monkeypatch):
    st = plex_to_tg.clean_settings({})
    assert st["encoder"] == "auto" and st["cpu_preset"] == "superfast"
    assert plex_to_tg.clean_settings({"encoder": "nvenc", "cpu_preset": "ultrafast"})["encoder"] == "nvenc"
    assert plex_to_tg.clean_settings({"encoder": "rm -rf", "cpu_preset": "slow"})["encoder"] == "auto"
    _pre, vf, codec = folder_watch.video_encoder({"name": "nvenc"}, 23, ["scale=-2:720"])
    assert codec[codec.index("-c:v") + 1] == "h264_nvenc" and vf[-1] == "format=yuv420p"
    pre, vf, codec = folder_watch.video_encoder({"name": "vaapi"}, 23, ["scale=-2:720"])
    assert pre[0] == "-vaapi_device" and vf[-1] == "hwupload" and "h264_vaapi" in codec
    pre, _vf, codec = folder_watch.video_encoder({"name": "qsv"}, 23, [])
    assert "-init_hw_device" in pre and "h264_qsv" in codec
    _pre, _vf, codec = folder_watch.video_encoder({"name": "cpu", "preset": "ultrafast"}, 23, [])
    assert codec[codec.index("-preset") + 1] == "ultrafast"
    p = folder_watch.encode_plan(_info(), ".mp4", 720, None, {"name": "nvenc"})
    assert p.encoder == "nvenc" and "h264_nvenc" in p[0]
    assert folder_watch.encode_plan(_info(height=720), ".mkv", 720, None, {"name": "nvenc"}).encoder == "copy"   # 다시 담기만은 인코더 안 씀

    async def works(name):
        return name == "vaapi"
    monkeypatch.setattr(folder_watch, "hw_works", works)
    assert asyncio.run(folder_watch.pick_encoder("auto"))["name"] == "vaapi"     # 되는 것을 골라
    assert asyncio.run(folder_watch.pick_encoder("nvenc"))["name"] == "cpu"      # 안 되면 CPU
    assert asyncio.run(folder_watch.pick_encoder("cpu", "veryfast")) == {"name": "cpu", "preset": "veryfast"}


def test_hw_encoder_failure_falls_back_to_cpu(data, tmp_path, monkeypatch):
    """하드웨어 인코더가 실제로 실패하면 CPU로 다시 해서 올린다"""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg 없음")
    drive = tmp_path / "drive"
    drive.mkdir()
    _make(drive / "Wide.mkv", size="1280x960", seconds=1)
    tg = FakeTg()
    mirror = plex_to_tg.Mirror(None, None, lambda sid: tg, lambda: None)
    rule = plex_to_tg.clean_settings({"rules": [{"id": "w", "kind": "folder", "folder": str(drive), "tg": "tg", "channel": "-1",
                                                  "wait_subs": False}]})["rules"][0]

    async def fake_pick(setting="auto", preset="superfast"):
        return {"name": "vaapi", "preset": preset, "device": str(tmp_path / "없는장치")}
    monkeypatch.setattr(folder_watch, "pick_encoder", fake_pick)
    seen = []
    real = mirror._encode

    async def spy(*a, **kw):
        seen.append(dict(mirror.current or {}))
        return await real(*a, **kw)
    monkeypatch.setattr(mirror, "_encode", spy)
    note = asyncio.run(mirror.upload(rule, "Wide.mkv", 2))
    assert "인코더 CPU" in note and "720p로 줄임" in note
    assert len(seen) == 2 and folder_watch._HW_OK.get("vaapi") is False
    folder_watch._HW_OK.pop("vaapi", None)
    v = [s for s in _probe(tg.sent[0]["body"], tmp_path)["streams"] if s["codec_type"] == "video"][0]
    assert v["height"] == 720


def test_encode_pauses_while_someone_plays(data, tmp_path, monkeypatch):
    """누가 서버 변환으로 재생하는 동안(아이폰 Safari 등) 올리기 인코딩은 멈췄다가 끝나면 잇는다"""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg 없음")
    src = tmp_path / "a.mkv"
    _make(src, seconds=2)
    playing = {"on": True}
    mirror = plex_to_tg.Mirror(None, None, None, lambda: None, lambda: playing["on"])
    monkeypatch.setattr(plex_to_tg, "PAUSE_CHECK_SEC", 0.05)
    out = tmp_path / "out.mp4"

    async def main():
        task = asyncio.ensure_future(mirror._encode(str(src), ["-c:v", "libx264", "-preset", "ultrafast"], str(out), 2, "t",
                                                    "인코딩 중", str(tmp_path)))
        await asyncio.sleep(1.5)
        assert not task.done() and mirror.current["phase"].startswith("재생 중이라 잠시 멈춤")   # 멈춰 있다
        playing["on"] = False
        await asyncio.wait_for(task, 60)                                        # 재생이 끝나면 이어서 끝까지
        assert out.exists() and mirror.current["phase"] == "인코딩 중"
    asyncio.run(main())


def test_network_drive_detection(tmp_path):
    mounts = tmp_path / "mounts"
    mounts.write_text("/dev/sda1 / ext4 rw 0 0\n"
                      "gdrive: /mnt/gds2 fuse.rclone rw 0 0\n"
                      "/dev/sdb1 /mnt/gds2/local ext4 rw 0 0\n"
                      "nas:/vol /mnt/nas\\040share nfs4 rw 0 0\n", encoding="utf-8")
    on = lambda p: folder_watch.on_network_drive(p, str(mounts))   # noqa: E731
    assert on("/mnt/gds2/GDRIVE/VIDEO/a.mkv") and on("/mnt/nas share/b.mkv")
    assert not on("/home/user/a.mkv") and not on("/mnt/gds2/local/c.mkv")      # 가장 길게 맞는 마운트로 본다
    assert not folder_watch.on_network_drive("/x", str(tmp_path / "missing"))


def test_mount_folder_is_copied_to_server_before_encoding(data, tmp_path, monkeypatch):
    """rclone mount 폴더의 파일: 그대로 읽으며 인코딩하지 않고 서버(임시 폴더)로 복사한 뒤 인코딩한다 - 자막 파일도 같이"""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg 없음")
    from ym_player import telegram_src
    telegram_src._migrated.discard(str(data / "data"))
    monkeypatch.setattr(plex_to_tg, "_KO_FONT", True)
    drive = tmp_path / "mnt"
    drive.mkdir()
    ep = drive / "Hope.S01E03.mkv"
    _make(ep, color="gray")
    (drive / "Hope.S01E03.ko.srt").write_bytes(SRT.encode("cp949"))
    monkeypatch.setattr(folder_watch, "on_network_drive", lambda p, *a: str(p).startswith(str(drive)))
    read_from = []
    real_copy = folder_watch.copy_file
    monkeypatch.setattr(folder_watch, "copy_file", lambda src, dest, progress=None: (read_from.append((src, dest)), real_copy(src, dest, progress))[1])
    tg = FakeTg()
    mirror = plex_to_tg.Mirror(None, None, lambda sid: tg, lambda: None)
    encoded_from = []
    real_encode = mirror._encode

    async def spy(src_path, *a, **k):
        encoded_from.append(src_path)
        return await real_encode(src_path, *a, **k)
    monkeypatch.setattr(mirror, "_encode", spy)
    rule = plex_to_tg.clean_settings({"rules": [{"id": "f", "kind": "folder", "folder": str(drive), "tg": "tg", "channel": "-100",
                                                 "wait_subs": False}]})["rules"][0]

    async def main():
        note = await mirror.upload(rule, "Hope.S01E03.mkv", 2)
        assert note.startswith("서버로 복사 후 처리") and "자막 입힘" in note
        assert read_from and read_from[0][0] == str(ep) and not read_from[0][1].startswith(str(drive))   # 영상을 임시 폴더로
        assert encoded_from and not encoded_from[0].startswith(str(drive))                                # 인코딩은 복사본으로
        assert [x["name"] for x in tg.sent] == ["Hope.S01E03.mp4"] and tg.sent[0]["stream"] is True
        got = _probe(tg.sent[0]["body"], tmp_path)
        assert [s["codec_name"] for s in got["streams"]] == ["h264", "aac"]
        assert not os.listdir(data / "data" / "plex_to_tg_tmp")                                           # 복사본·임시 파일은 지움
        # 임시 폴더에 자리가 모자라면 올리지 않고 알린다
        monkeypatch.setattr(shutil, "disk_usage", lambda p: type("U", (), {"free": 1024, "total": 1, "used": 1})())
        with pytest.raises(RuntimeError, match="자리가 모자랍니다"):
            await mirror.upload(rule, "Hope.S01E03.mkv", 2)
    asyncio.run(main())


def test_match_subtitle_files_loosely():
    m = folder_watch.match_subs
    v = "Hope.2026.1080p.AMZN.WEB-DL.DDP5.1.H.264-KyoGo.mkv"
    # 같은 이름 (대소문자 무시) - 있으면 이것만
    assert m(v, [v, "hope.2026.1080p.amzn.web-dl.ddp5.1.h.264-kyogo.ko.SRT", "other.srt"]) == [
        ("hope.2026.1080p.amzn.web-dl.ddp5.1.h.264-kyogo.ko.SRT", "ko", "srt")]
    # 이름이 다르지만 폴더에 영상이 하나뿐이면 그 폴더의 자막 (릴리스 이름이 다른 자막)
    assert m(v, [v, "Hope.2026.1080p.WEB-DL.kor.smi", "readme.txt"]) == [("Hope.2026.1080p.WEB-DL.kor.smi", "kor", "smi")]
    assert m(v, [v, "Other.Movie.mkv", "Hope.2026.srt"]) == []                  # 영상이 여럿이면 엉뚱한 것을 붙이지 않는다
    # 회차가 있으면 같은 회차 자막
    ep = "Show.S01E02.1080p.WEB-DL-GRP.mkv"
    names = [ep, "Show.S01E01.1080p.WEB-DL-GRP.mkv", "Show.S01E02.WEB.ko.srt", "Show.S01E01.WEB.ko.srt"]
    assert m(ep, names) == [("Show.S01E02.WEB.ko.srt", "ko", "srt")]
    assert m("Show.S01E03.mkv", ["Show.S01E03.mkv", "Show.S01E02.ko.srt"]) == []   # 다른 회차 자막은 안 붙인다
