"""MKV·TS 등을 다시 압축하지 않고 MP4 파일로 바꿔 두고(머리에 색인 - faststart) 원본처럼 Range로 보내기.

HLS(서버 변환 조각)로 보내면 아이패드 Safari에서 끊기는 일이 잦아서(특히 Plex 변환), 그릇만 바꾸면 되는 영상은
먼저 MP4 파일을 만들어 두고 그 파일을 그대로 튼다 - 탐색도 브라우저가 Range로 해서 매끄럽다.

  remux : H.264 + AAC/MP3 - 영상·소리 모두 그대로
  audio : H.264 + 다른 소리(AC-3, DTS, FLAC …) - 소리만 AAC로 (빠름)
  hevc  : HEVC(H.265) - Safari는 MP4 안의 HEVC(hvc1)를 그대로 튼다 (영상은 그대로, 소리는 AAC가 아니면 AAC로)
영상을 다시 압축해야 하는 것(VP9·MPEG-4 등, Safari가 아닌 브라우저의 HEVC)은 여기서 하지 않는다 -> 예전처럼 HLS/스트림.

만든 파일은 DATA_DIR/mp4cache/에 두고 다시 열면 그대로 쓴다. 다 합쳐 MP_MP4_CACHE_GB(기본 20GB)를 넘으면 오래 안 쓴
것부터 지운다. MP_MP4_MAX_GB(기본 12GB)보다 큰 원본은 MP4로 만들지 않는다 (디스크·기다리는 시간).
"""
import asyncio
import hashlib
import logging
import os
import re
import time

from . import config_store, transcode

log = logging.getLogger("ym_player.mp4cache")
MODES = ("remux", "audio", "hevc")
_HEVC = {"hevc", "h265"}
_JOBS = {}            # key -> Job
_KEY_RE = re.compile(r"^[0-9a-f]{24}$")


def _gb(name, default):
    try:
        return max(0.5, float(os.environ.get(name) or default))
    except ValueError:
        return default


def cache_gb():
    return _gb("MP_MP4_CACHE_GB", 20)


def max_source_gb():
    return _gb("MP_MP4_MAX_GB", 12)


def root():
    return os.path.join(config_store.DATA_DIR, "mp4cache")


def _secret():
    """키에 섞는 이 서버만의 값 (키를 짐작해 다른 라이브러리의 파일을 받지 못하게)"""
    path = os.path.join(root(), ".secret")
    try:
        with open(path, encoding="utf-8") as f:
            v = f.read().strip()
        if v:
            return v
    except OSError:
        pass
    import secrets
    v = secrets.token_hex(16)
    os.makedirs(root(), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(v)
    return v


def make_key(*parts):
    return hashlib.sha1("|".join(str(p) for p in (_secret(), *parts)).encode("utf-8")).hexdigest()[:24]


def path_of(key):
    if not _KEY_RE.match(str(key or "")):
        raise ValueError("잘못된 키입니다.")
    return os.path.join(root(), f"{key}.mp4")


def mp4_mode(info, safari=False):
    """ffprobe 결과 -> MP4 파일로 만들 방법 (remux / audio / hevc) 또는 None (다시 압축해야 함, 또는 이미 MP4라 그대로 틀면 됨)"""
    d = transcode.decide(info)
    if d["mode"] in ("remux", "audio"):
        return d["mode"]
    streams = (info or {}).get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if safari and v and str(v.get("codec_name") or "").lower() in _HEVC:
        return "hevc"
    return None


def mode_from_decision(d, safari=False):
    """transcode.decide()의 결과(또는 텔레그램 재생 판단) -> (방법, 소리 그대로인지). 만들지 않으면 (None, False)"""
    d = d or {}
    if d.get("mode") in ("remux", "audio"):
        return d["mode"], d["mode"] == "remux"
    video = str(d.get("video") or "").split(" ")[0].lower()
    if safari and d.get("mode") == "transcode" and video in _HEVC:
        return "hevc", str(d.get("audio") or "").lower() in ("aac", "")
    return None, False


def build_args(url, mode, out, copy_audio=False):
    """ffmpeg: 영상 그대로(-c:v copy), 소리는 그대로 또는 AAC, 자막·데이터는 빼고, 머리에 색인(faststart)"""
    if mode not in MODES:
        raise ValueError(f"알 수 없는 방법: {mode}")
    args = [transcode.ffmpeg_path() or "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-nostats", "-progress", "pipe:1", "-i", url,
            "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-map_metadata", "-1", "-c:v", "copy",
            "-tag:v", "hvc1" if mode == "hevc" else "avc1"]
    if mode == "remux" or (mode == "hevc" and copy_audio):
        args += ["-c:a", "copy"]
    else:
        args += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    return args + ["-movflags", "+faststart", "-f", "mp4", out]


class Job:
    def __init__(self, key, mode, duration=0.0):
        self.key, self.mode, self.duration = key, mode, float(duration or 0)
        self.state = "running"        # running / done / error
        self.done_sec = 0.0
        self.error = ""
        self.proc = None
        self.started = time.time()
        self.finished = 0.0
        self.task = None

    def public(self):
        pct = 1.0 if self.state == "done" else (min(0.99, self.done_sec / self.duration) if self.duration else 0.0)
        left = 0
        if self.state == "running" and 0.02 < pct < 1:
            spent = time.time() - self.started
            left = int(spent / pct - spent)
        return {"key": self.key, "state": self.state, "mode": self.mode, "progress": round(pct, 3), "left": left,
                "error": self.error, "url": f"/api/mp4/file/{self.key}.mp4" if self.state == "done" else ""}


def job(key):
    """돌고 있거나 끝난 작업 (서버를 다시 켜서 작업은 없어도 파일이 있으면 끝난 것으로)"""
    j = _JOBS.get(key)
    if j:
        return j
    try:
        p = path_of(key)
    except ValueError:
        return None
    if os.path.isfile(p):
        j = Job(key, "")
        j.state = "done"
        return j
    return None


async def ensure(key, url, mode, duration=0.0, copy_audio=False):
    """그 MP4가 있으면 바로 끝난 작업, 만들고 있으면 그 작업, 없으면 만들기 시작"""
    final = path_of(key)
    if os.path.isfile(final):
        try:
            os.utime(final)   # 최근에 쓴 것 (지우기 순서)
        except OSError:
            pass
        j = _JOBS.get(key) or Job(key, mode, duration)
        j.state = "done"
        _JOBS[key] = j
        return j
    j = _JOBS.get(key)
    if j and j.state == "running":
        return j
    os.makedirs(root(), exist_ok=True)
    j = _JOBS[key] = Job(key, mode, duration)
    tmp = final + ".part"
    j.proc = await asyncio.create_subprocess_exec(*build_args(url, mode, tmp, copy_audio), stdin=asyncio.subprocess.DEVNULL,
                                                  stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    j.task = asyncio.ensure_future(_watch(j, tmp, final))
    log.info("MP4로 바꾸기 시작 (%s, %s)", key, mode)
    return j


async def _watch(j, tmp, final):
    errs = []

    async def read_err():
        async for line in j.proc.stderr:
            errs.append(line.decode("utf-8", "replace").strip())
            del errs[:-5]
    err_task = asyncio.ensure_future(read_err())
    try:
        async for line in j.proc.stdout:   # -progress: out_time_ms=마이크로초
            k, _, v = line.decode("utf-8", "replace").strip().partition("=")
            if k == "out_time_ms" and v.isdigit():
                j.done_sec = int(v) / 1e6
        await j.proc.wait()
        await err_task
    except asyncio.CancelledError:
        if j.proc.returncode is None:
            j.proc.kill()
        j.state, j.error = "error", "그만둠"
        try:
            os.remove(tmp)   # 만들다 만 파일은 남기지 않는다
        except OSError:
            pass
        raise
    if j.proc.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
        os.replace(tmp, final)
        j.state, j.finished = "done", time.time()
        log.info("MP4로 바꿈 (%s, %.0f초 걸림, %.1fMB)", j.key, j.finished - j.started, os.path.getsize(final) / 1048576)
        await asyncio.to_thread(evict)
    else:
        j.state = "error"
        j.error = (errs[-1] if errs else f"ffmpeg 종료 코드 {j.proc.returncode}")[:300]
        log.warning("MP4로 바꾸지 못함 (%s): %s", j.key, j.error)
        try:
            os.remove(tmp)
        except OSError:
            pass


def sweep_partial():
    """만들다 만 MP4(.part) 지우기 - 서버를 끄거나 죽어서 남은 것 (서버를 켤 때, 만드는 중인 것은 그대로). 지운 수"""
    try:
        names = [n for n in os.listdir(root()) if n.endswith(".part")]
    except OSError:
        return 0
    n = 0
    for name in names:
        key = name.split(".", 1)[0]
        if _JOBS.get(key) and _JOBS[key].state == "running":
            continue
        try:
            os.remove(os.path.join(root(), name))
            n += 1
        except OSError:
            pass
    return n


def cancel(key):
    j = _JOBS.get(key)
    if j and j.state == "running" and j.proc and j.proc.returncode is None:
        j.proc.kill()


def evict(limit_gb=None):
    """다 합쳐 limit_gb를 넘으면 오래 안 쓴 것부터 지운다 (만드는 중인 것은 그대로). 지운 수"""
    limit = (limit_gb if limit_gb is not None else cache_gb()) * 1024 ** 3
    try:
        names = [n for n in os.listdir(root()) if n.endswith(".mp4")]
    except OSError:
        return 0
    files = []
    for n in names:
        p = os.path.join(root(), n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        files.append((max(st.st_atime, st.st_mtime), st.st_size, p, n[:-4]))
    total, removed = sum(f[1] for f in files), 0
    for _t, size, p, key in sorted(files):
        if total <= limit:
            break
        if (_JOBS.get(key) and _JOBS[key].state == "running"):
            continue
        try:
            os.remove(p)
            total -= size
            removed += 1
            _JOBS.pop(key, None)
        except OSError:
            pass
    return removed
