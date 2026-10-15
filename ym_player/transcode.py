"""브라우저가 못 여는 영상을 서버에서 ffmpeg로 바꿔 보내기 (지금은 텔레그램 영상).

1) ffprobe로 컨테이너/코덱을 본다 (probe)
2) 어떻게 보낼지 정한다 (decide)
     direct    : 브라우저가 그대로 연다 (mp4+H.264+AAC 등) - 지금처럼 Range 스트림
     remux     : 코덱은 되는데 그릇만 안 맞음 (mkv/avi/ts 안의 H.264+AAC) - 다시 압축하지 않고 mp4로 포장만
     audio     : 영상은 되는데 음성이 안 됨 (AC-3/DTS/FLAC 등) - 영상은 그대로, 음성만 AAC로
     transcode : 영상 코덱이 안 됨 (HEVC, MPEG-4, 10비트 H.264 등) - H.264로 다시 압축 (CPU 많이 씀)
3) ffmpeg가 조각난 MP4(fMP4)를 stdout으로 흘리고 서버가 그대로 브라우저로 보낸다 (build_args)
   탐색은 재생기가 ?start=초 로 새로 요청한다 (조각난 MP4는 Range 탐색이 안 됨).

환경변수: MP_FFMPEG / MP_FFPROBE (실행 파일 경로, 기본은 PATH에서 찾음),
         MP_MAX_TRANSCODES (동시에 돌릴 ffmpeg 수, 기본 3)
"""
import asyncio
import json
import os
import shutil

MODES = ("direct", "remux", "audio", "transcode")

# <video>가 그대로 여는 조합 (Chrome/Edge 기준, 넓게 잡지 않는다)
_MP4_VIDEO = {"h264", "vp9", "av1"}
_MP4_AUDIO = {"aac", "mp3", "opus", "flac"}
_WEBM_VIDEO = {"vp8", "vp9", "av1"}
_WEBM_AUDIO = {"opus", "vorbis"}
# 다시 압축하지 않고 mp4에 그대로 담을 수 있는 것
_COPY_VIDEO = {"h264"}
_COPY_AUDIO = {"aac", "mp3"}
_8BIT = {"yuv420p", "yuvj420p", ""}


def ffmpeg_path():
    return os.environ.get("MP_FFMPEG") or shutil.which("ffmpeg")


def ffprobe_path():
    return os.environ.get("MP_FFPROBE") or shutil.which("ffprobe")


def available():
    return bool(ffmpeg_path() and ffprobe_path())


def max_jobs():
    try:
        return max(1, int(os.environ.get("MP_MAX_TRANSCODES", "3")))
    except ValueError:
        return 3


async def probe(url, timeout=45):
    """ffprobe 결과 JSON (format + streams). 실패하면 RuntimeError."""
    proc = await asyncio.create_subprocess_exec(
        ffprobe_path(), "-v", "error", "-show_entries",
        "format=format_name,duration:stream=index,codec_type,codec_name,profile,pix_fmt,width,height,channels",
        "-of", "json", url,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("ffprobe 시간 초과")
    if proc.returncode != 0:
        raise RuntimeError((err.decode("utf-8", "replace").strip().splitlines() or ["ffprobe 실패"])[-1][:300])
    return json.loads(out.decode("utf-8", "replace") or "{}")


def _first(streams, kind):
    return next((s for s in streams if s.get("codec_type") == kind), None)


def decide(info):
    """ffprobe 결과로 보낼 방법을 정한다. 반환 {"mode", "container", "video", "audio", "reason"}."""
    streams = (info or {}).get("streams") or []
    fmt = ((info or {}).get("format") or {}).get("format_name", "")
    v, a = _first(streams, "video"), _first(streams, "audio")
    vc = (v or {}).get("codec_name", "") or ""
    ac = (a or {}).get("codec_name", "") or ""
    pix = (v or {}).get("pix_fmt", "") or ""
    if "mp4" in fmt or "mov" in fmt:
        container = "mp4"
    elif "matroska" in fmt or "webm" in fmt:
        container = "mkv"
    else:
        container = fmt.split(",")[0] or "?"
    out = {"container": container, "video": vc + (f" {pix}" if pix and pix not in _8BIT else ""), "audio": ac}

    v_ok8 = pix in _8BIT
    if not v:
        return {**out, "mode": "transcode", "reason": "영상 트랙이 없습니다"}
    if container == "mp4" and vc in _MP4_VIDEO and (vc != "h264" or v_ok8) and (not a or ac in _MP4_AUDIO):
        return {**out, "mode": "direct", "reason": "브라우저가 그대로 엽니다"}
    if container == "mkv" and vc in _WEBM_VIDEO and (not a or ac in _WEBM_AUDIO):
        return {**out, "mode": "direct", "reason": "WebM 형식이라 그대로 엽니다"}
    v_copy = vc in _COPY_VIDEO and v_ok8
    if v_copy and (not a or ac in _COPY_AUDIO):
        return {**out, "mode": "remux", "reason": f"{container} 그릇만 mp4로 바꿉니다 (다시 압축 안 함)"}
    if v_copy:
        return {**out, "mode": "audio", "reason": f"음성({ac})만 AAC로 바꿉니다"}
    return {**out, "mode": "transcode", "reason": f"영상({out['video']})을 H.264로 다시 압축합니다"}


def build_args(url, mode, start=0.0):
    """ffmpeg 명령. 결과는 stdout으로 나오는 조각난 MP4."""
    if mode not in ("remux", "audio", "transcode"):
        raise ValueError(f"알 수 없는 변환 방법: {mode}")
    args = [ffmpeg_path() or "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if start and start > 0:
        args += ["-ss", f"{float(start):.3f}"]
    args += ["-i", url, "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-map_metadata", "-1"]
    if mode == "transcode":
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
                 "-vf", "scale='min(1920,iw)':-2", "-g", "60", "-maxrate", "10M", "-bufsize", "20M"]
    else:
        args += ["-c:v", "copy"]
    if mode == "remux":
        args += ["-c:a", "copy"]
    else:
        args += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    args += ["-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"]
    return args
