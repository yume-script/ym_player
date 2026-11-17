"""MKV 등을 MP4 파일로 바꿔 두고 원본처럼 틀기 (mp4cache) - HLS 대신"""
import asyncio
import shutil
import subprocess

import pytest

from ym_player import config_store, mp4cache, transcode


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    mp4cache._JOBS.clear()
    return tmp_path


def test_mode_from_decision():
    assert mp4cache.mode_from_decision({"mode": "remux"}) == ("remux", True)
    assert mp4cache.mode_from_decision({"mode": "audio", "audio": "ac3"}) == ("audio", False)
    assert mp4cache.mode_from_decision({"mode": "transcode", "video": "hevc yuv420p10le", "audio": "aac"}) == (None, False)
    assert mp4cache.mode_from_decision({"mode": "transcode", "video": "hevc yuv420p10le", "audio": "aac"}, safari=True) == ("hevc", True)
    assert mp4cache.mode_from_decision({"mode": "transcode", "video": "vp9"}, safari=True) == (None, False)
    assert mp4cache.mode_from_decision({"mode": "direct"}) == (None, False)
    a, b = mp4cache.make_key("plex", 1), mp4cache.make_key("plex", 1)
    assert a == b and len(a) == 24 and mp4cache.make_key("plex", 2) != a


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg 없음")
def test_mkv_to_mp4_file(data, tmp_path):
    src = tmp_path / "in.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "ac3", str(src)], check=True)

    async def main():
        info = await transcode.probe(str(src))
        mode, copy_audio = mp4cache.mode_from_decision(transcode.decide(info))
        assert (mode, copy_audio) == ("audio", False)
        key = mp4cache.make_key("t", 1)
        job = await mp4cache.ensure(key, str(src), mode, 3.0, copy_audio)
        assert job.state == "running"
        await job.task
        assert job.state == "done" and job.public()["url"] == f"/api/mp4/file/{key}.mp4"
        out = await transcode.probe(mp4cache.path_of(key))
        assert "mp4" in out["format"]["format_name"]
        assert {s["codec_name"] for s in out["streams"]} == {"h264", "aac"}
        again = await mp4cache.ensure(key, str(src), mode, 3.0)   # 다시 열면 만든 것을 그대로
        assert again.state == "done"
        mp4cache._JOBS.clear()
        assert mp4cache.job(key).state == "done"                  # 서버를 다시 켜도 파일이 있으면
        assert mp4cache.evict(limit_gb=0) == 1                   # 한도를 넘으면 오래 안 쓴 것부터 지움
        assert mp4cache.job(key) is None
    asyncio.run(main())
