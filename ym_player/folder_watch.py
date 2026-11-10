"""자동 올리기의 "폴더" 규칙: rclone 드라이브(또는 이 컴퓨터의 폴더)에 새로 올라온 동영상 찾기 + 텔레그램용 인코딩 계획.

폴더 칸에는 둘 중 하나를 적는다.
- 이 컴퓨터의 폴더 (절대 경로, 예: /mnt/gdrive/영화 - rclone mount로 붙인 드라이브도 여기)
- rclone 원격 "이름:경로" (예: gdrive:영화) - 서버에 rclone이 설치·설정돼 있어야 한다. 목록은 rclone lsjson,
  파일은 rclone copyto로 임시 폴더에 받아서 올린다.

인코딩(encode_plan): 텔레그램 앱·브라우저에서 바로 재생되게 H.264(yuv420p) + AAC를 mp4(faststart)에 담는다.
- 이미 그 모양이면 그대로 (mp4가 아니면 다시 담기만 - 화질 그대로)
- 파일이 정한 크기보다 크고 720p보다 높으면 720p로 줄여 다시 인코딩 (용량 낮추기)
- 자막 파일(SRT 등)이 있으면 화면에 입혀 인코딩 - 텔레그램 앱에서도 자막이 보인다
"""
import asyncio
import hashlib
import json
import os
import re
import shutil

from . import transcode

VIDEO_EXT = {".mp4", ".m4v", ".mkv", ".avi", ".mov", ".ts", ".m2ts", ".webm", ".wmv", ".flv", ".mpg", ".mpeg"}
SUB_EXT = {"srt": "srt", "ass": "ass", "ssa": "ass", "smi": "smi", "vtt": "vtt"}
STABLE_SEC = 300          # 수정 시각이 이만큼 지났거나, 지난 확인 때와 크기가 같으면 다 올라온 것으로 본다
MAX_FILES = 200000
_REMOTE_RE = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_. -]*:.*")
TEXT_SUBS = {"subrip": "srt", "srt": "srt", "ass": "ass", "ssa": "ass", "mov_text": "srt", "webvtt": "srt", "text": "srt"}


class FolderError(Exception):
    pass


def is_remote(folder):
    """rclone 원격("이름:경로")인지. 이 컴퓨터의 폴더는 /로 시작한다"""
    folder = str(folder or "")
    return not folder.startswith("/") and bool(_REMOTE_RE.fullmatch(folder))


def clean_folder(folder):
    """설정 검사: 절대 경로 또는 rclone 원격. 아니면 ValueError"""
    folder = str(folder or "").strip()
    if not folder:
        raise ValueError("폴더 규칙에는 폴더(또는 rclone 원격 이름:경로)를 적어 주세요.")
    if "\x00" in folder or "\n" in folder:
        raise ValueError("폴더 이름에 쓸 수 없는 글자가 있습니다.")
    if folder.startswith("/"):
        return folder.rstrip("/") or "/"
    if is_remote(folder):
        return folder
    raise ValueError("폴더는 /로 시작하는 경로(예: /mnt/gdrive/영화)나 rclone 원격(예: gdrive:영화)으로 적어 주세요.")


def key_hash(key):
    return hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:16]


def rclone_path():
    return shutil.which(os.environ.get("MP_RCLONE") or "rclone")


def _join_remote(folder, rel):
    return f"{folder.rstrip('/')}/{rel}" if not folder.endswith(":") else f"{folder}{rel}"


def local_path(folder, key):
    """이 컴퓨터 폴더 안의 파일 경로 (폴더 밖을 가리키면 FolderError)"""
    root = os.path.realpath(folder)
    path = os.path.realpath(os.path.join(root, key))
    if path != root and not path.startswith(root.rstrip("/") + "/"):
        raise FolderError("폴더 밖의 파일입니다.")
    return path


async def _run(*args, timeout=600):
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise FolderError(f"{os.path.basename(args[0])}이(가) {timeout}초 안에 끝나지 않았습니다.")
    return proc.returncode, out, err.decode("utf-8", "replace")


def _scan_local(folder):
    if not os.path.isdir(folder):
        raise FolderError(f"폴더가 없습니다 (드라이브가 붙어 있는지 확인): {folder}")
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for n in files:
            if n.startswith(".") or os.path.splitext(n)[1].lower() not in VIDEO_EXT:
                continue
            p = os.path.join(root, n)
            try:
                s = os.stat(p)
            except OSError:
                continue
            out.append({"key": os.path.relpath(p, folder), "size": s.st_size, "mtime": int(s.st_mtime)})
            if len(out) >= MAX_FILES:
                return out
    return out


def _parse_time(v):
    """rclone 시각 "2026-11-10T01:02:03.123456789+09:00" / "...Z" -> 유닉스 초 (못 읽으면 0)"""
    from datetime import datetime, timezone
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:\d\d)?$", str(v or "").strip())
    if not m:
        return 0
    tz = m.group(2) or "Z"
    dt = datetime.fromisoformat(m.group(1) + ("+00:00" if tz == "Z" else tz))
    return int(dt.astimezone(timezone.utc).timestamp())


async def _lsjson(target, recursive=True):
    rc = rclone_path()
    if not rc:
        raise FolderError("서버에 rclone이 없습니다 (rclone 원격을 쓰려면 rclone을 설치·설정해 주세요).")
    args = [rc, "lsjson", "--files-only", "--no-mimetype"] + (["-R"] if recursive else []) + ["--", target]
    code, out, err = await _run(*args, timeout=900)
    if code != 0:
        raise FolderError(f"rclone 목록을 못 받았습니다: {err.strip()[-300:]}")
    try:
        rows = json.loads(out.decode("utf-8", "replace") or "[]")
    except ValueError:
        raise FolderError("rclone 목록을 읽지 못했습니다.")
    return rows if isinstance(rows, list) else []


async def scan(folder):
    """폴더 안의 동영상 [{key(폴더 기준 상대 경로), size, mtime}]"""
    if not is_remote(folder):
        return await asyncio.to_thread(_scan_local, folder)
    out = []
    for r in await _lsjson(folder):
        path = str(r.get("Path") or "")
        if not path or any(x.startswith(".") for x in path.split("/")) or os.path.splitext(path)[1].lower() not in VIDEO_EXT:
            continue
        out.append({"key": path, "size": int(r.get("Size") or 0), "mtime": _parse_time(r.get("ModTime"))})
    return out[:MAX_FILES]


def _sub_match(stem, name):
    root, ext = os.path.splitext(name)
    sext = SUB_EXT.get(ext[1:].lower())
    if not sext or not (root == stem or root.startswith(stem + ".")):
        return None
    tail = root[len(stem) + 1:] if root != stem else ""
    return tail.split(".")[-1] if tail else "", sext


async def remote_subs(folder, key):
    """rclone 원격에서 영상 옆의 자막 파일 [(상대 경로, 언어 꼬리, 확장자)]"""
    d, base = os.path.split(key)
    stem = os.path.splitext(base)[0]
    target = _join_remote(folder, d) if d else folder
    out = []
    for r in await _lsjson(target, recursive=False):
        name = str(r.get("Name") or r.get("Path") or "")
        hit = _sub_match(stem, name)
        if hit:
            out.append((f"{d}/{name}" if d else name, hit[0], hit[1]))
    return sorted(out)


async def fetch(folder, key, dest, progress=None):
    """rclone 원격의 파일 하나를 dest로 받는다 (progress(받은 바이트)는 1초마다)"""
    rc = rclone_path()
    if not rc:
        raise FolderError("서버에 rclone이 없습니다.")
    proc = await asyncio.create_subprocess_exec(rc, "copyto", "--", _join_remote(folder, key), dest,
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    task = asyncio.ensure_future(proc.communicate())
    folder_of = os.path.dirname(dest)
    while not task.done():
        await asyncio.wait([task], timeout=1)
        if progress:   # rclone은 받는 동안 이름 뒤에 .partial 등을 붙이기도 해서 임시 폴더의 그 이름으로 시작하는 파일을 본다
            try:
                got = sum(os.path.getsize(os.path.join(folder_of, n)) for n in os.listdir(folder_of)
                          if n.startswith(os.path.basename(dest)))
            except OSError:
                got = 0
            progress(got)
    _out, err = task.result()
    if proc.returncode != 0 or not os.path.exists(dest):
        raise FolderError(f"rclone으로 받지 못했습니다: {err.decode('utf-8', 'replace').strip()[-300:]}")


async def fetch_head(folder, key, dest, count=32 * 1024 * 1024):
    """원격 파일의 앞부분만 (안에 든 자막 트랙을 ffprobe로 보려고 - mkv는 트랙 정보가 앞에 있다)"""
    rc = rclone_path()
    if not rc:
        return False
    try:
        code, out, _err = await _run(rc, "cat", "--count", str(count), "--", _join_remote(folder, key), timeout=600)
    except FolderError:
        return False
    if code != 0 or not out:
        return False
    with open(dest, "wb") as f:
        f.write(out)
    return True


# ---------------------------------------------------------------------------
# 파일 이름 -> 캡션 값
# ---------------------------------------------------------------------------
_EP_RE = re.compile(r"(?<![A-Za-z0-9])S(\d{1,3})[\s._-]*E(\d{1,4})(?![0-9])", re.I)
_EONLY_RE = re.compile(r"(?<![A-Za-z0-9])E(?:P)?(\d{1,4})(?![0-9])", re.I)
_YEAR_RE = re.compile(r"(?<![0-9])(19\d{2}|20\d{2})(?![0-9])")
_JUNK_RE = re.compile(r"(?<![A-Za-z0-9])(?:\d{3,4}p|\d{6}|WEB-?DL|WEBRip|BluRay|BDRip|HDTV|x26[45]|H\.?26[45]|HEVC|AAC\d?(?:\.\d)?|"
                      r"DDP?\d?(?:\.\d)?|10bit|NF|AMZN|DSNP)(?![A-Za-z0-9]).*$", re.I)


def file_fields(key, library=""):
    """파일 이름 -> 캡션에 넣을 값 (Plex 규칙의 item_fields와 같은 칸)"""
    base = os.path.basename(key)
    stem = os.path.splitext(base)[0]
    text = re.sub(r"[._]+", " ", stem).strip()
    se = _EP_RE.search(text)
    season = episode = ""
    if se:
        season, episode = int(se.group(1)), int(se.group(2))
        cut = se.start()
    else:
        e = _EONLY_RE.search(text)
        if e:
            episode = int(e.group(1))
        cut = e.start() if e else None
    y = _YEAR_RE.search(text)
    year = y.group(1) if y else ""
    if cut is None:
        cut = y.start() if y and y.start() > 0 else None
    title = text[:cut] if cut else _JUNK_RE.sub("", text)
    title = re.sub(r"[\s\-\[(]+$", "", title).strip() or text
    code = (f"S{season:02d}E{episode:02d}" if season != "" else f"E{episode:02d}") if episode != "" else ""
    if code:
        name, show = f"{title} {code}", title
    else:
        name, show = (f"{title} ({year})" if year else title), ""
    from .plex_to_tg import _Blank
    return _Blank(name=name, title=title, year=year, show=show, season=season, episode=episode, code=code, summary="",
                  library=library, filename=base, genres="", tags="")


# ---------------------------------------------------------------------------
# 인코딩
# ---------------------------------------------------------------------------
async def probe(path):
    """ffprobe 결과 {"streams": [...], "format": {...}} (못 읽으면 {})"""
    fp = transcode.ffprobe_path()
    if not fp:
        return {}
    try:
        code, out, _err = await _run(fp, "-v", "error", "-show_streams", "-show_format", "-of", "json", path, timeout=180)
    except FolderError:
        return {}
    if code != 0:
        return {}
    try:
        return json.loads(out.decode("utf-8", "replace") or "{}")
    except ValueError:
        return {}


def streams(info, kind):
    return [s for s in info.get("streams") or [] if s.get("codec_type") == kind]


def text_subs(info):
    """파일 안의 글자 자막 트랙 [(스트림 번호, 언어, 확장자)] - 한국어를 앞으로"""
    out = []
    for s in streams(info, "subtitle"):
        ext = TEXT_SUBS.get(str(s.get("codec_name") or "").lower())
        if ext:
            out.append((int(s.get("index", 0)), str((s.get("tags") or {}).get("language") or ""), ext))
    return sorted(out, key=lambda x: x[1].lower() not in ("kor", "ko"))


def duration(info):
    for v in [(info.get("format") or {}).get("duration")] + [s.get("duration") for s in streams(info, "video")]:
        try:
            if float(v) > 0:
                return float(v)
        except (TypeError, ValueError):
            pass
    return 0.0


def encode_plan(info, size, ext, shrink_bytes, height_cap, burn_sub=None):
    """텔레그램에서 바로 재생되게 할 ffmpeg 인자 (출력 파일 앞까지) - 손댈 것이 없으면 None.
    shrink_bytes: 파일이 이보다 크면 height_cap보다 높은 영상을 줄인다 (0이면 늘). burn_sub: 화면에 입힐 자막 파일 이름
    (ffmpeg를 그 폴더에서 돌린다). 돌려주는 값: (인자, 설명들)"""
    v = (streams(info, "video") or [None])[0]
    a = (streams(info, "audio") or [None])[0]
    if not v:
        raise FolderError("영상 트랙이 없는 파일입니다.")
    h = int(v.get("height") or 0)
    scale = height_cap and h > height_cap and (not shrink_bytes or size > shrink_bytes)
    v_ok = (str(v.get("codec_name")) == "h264" and str(v.get("pix_fmt")) in ("yuv420p", "yuvj420p")
            and "10" not in str(v.get("profile") or "") and "4:4" not in str(v.get("profile") or "")
            and not scale and not burn_sub)
    a_ok = a is None or str(a.get("codec_name")) in ("aac", "mp3")
    if v_ok and a_ok and ext.lower() in (".mp4", ".m4v"):
        return None
    notes = []
    args = ["-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]
    if v_ok:
        args += ["-c:v", "copy"]
    else:
        vf = []
        if scale:
            vf.append(f"scale=-2:{height_cap}")
            notes.append(f"{height_cap}p로 줄임")
        if burn_sub:
            vf.append(f"subtitles=filename={burn_sub}")
            notes.append("자막 입힘")
        if not scale and not burn_sub:
            notes.append("H.264로 인코딩")
        args += (["-vf", ",".join(vf)] if vf else []) + [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23" if scale else "21", "-profile:v", "high", "-level:v", "4.1",
            "-pix_fmt", "yuv420p"]
    if a is None:
        pass
    elif a_ok:
        args += ["-c:a", "copy"]
    else:
        args += ["-c:a", "aac", "-ac", "2", "-b:a", "192k"]
        notes.append("소리 AAC로")
    if v_ok and a_ok:
        notes.append("mp4로 다시 담음")
    return args + ["-movflags", "+faststart"], notes
