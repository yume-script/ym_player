"""Plex 라이브러리에 새로 들어온 동영상 -> 텔레그램 채널에 자동으로 올리기 (설정 > 자동 올리기).

규칙 하나 = Plex 소스의 라이브러리(섹션) 하나 -> 텔레그램 소스(로그인한 내 계정)의 채널 하나.
- 몇 분마다(기본 10분) 그 라이브러리를 "추가된 순"으로 훑어 새 항목(영화 / 에피소드)을 찾는다.
- 규칙을 처음 켤 때는 지금 있는 것은 올리지 않고 기준만 잡는다 (그 뒤로 들어온 것부터).
- 파일은 Plex 서버가 이 컴퓨터에 있어 원본 경로가 보이면 그대로, 아니면 Plex에서 임시 폴더로 받은 뒤 올리고 지운다.
- 한 번에 하나씩 올린다. 실패하면 다음 확인 때 다시(3번까지). 올린 것은 기억해 두어 다시 켜도 두 번 올리지 않는다
  (data/plex_to_tg.json).
- 캡션 첫 줄은 "작품 S01E02 - 제목" / "영화 (2026)" 모양이라 YMPlayer 텔레그램 목록에서 제목·회차 묶기가 그대로 된다.
- 텔레그램 일반 계정은 파일 하나 2GB(프리미엄 4GB)까지 - 넘는 파일은 ffmpeg로 시간을 나눠(각 조각이 따로 재생되게)
  "이름_0000.mp4", "이름_0001.mp4"…로 올린다 (0부터, 4자리). YMPlayer는 이 조각들을 한 묶음으로 이어서 튼다.
- 자막 기다리기(규칙마다, 기본 켬): Plex 항목에 자막(내장이든 외부든)이 하나도 없으면 올리지 않고 기다렸다가
  생기면 올린다 (기다릴 날 수를 정하면 그 뒤엔 자막 없이). 외부 자막 파일은 영상 뒤에 같은 이름으로 같이 올려
  YMPlayer가 영상에 자동으로 붙인다 (나눈 영상이면 자막도 조각 시간에 맞춰 자른다).
- 자막 입혀 인코딩(규칙마다, 기본 켬): 영상 안에 자막이 없고 외부 자막만 있으면(자막을 기다리다 생긴 경우 등) 그 자막을
  화면에 입혀 H.264 mp4로 다시 인코딩해서 올린다 - 텔레그램 앱에서도 자막이 보인다 (한국어 자막을 먼저 고름).
- 720p로 줄이기(규칙마다 높이, 기본 720): 그보다 높은 영상은 용량과 상관없이 줄여 H.264 mp4로 올린다 (자막 입히기와 한 번에).
- 텔레그램 앱에서 바로 재생되게: 올리는 영상은 언제나 MP4(H.264 영상 + AAC 소리, 머리에 색인 - faststart)다. MKV·AVI·WMV나
  HEVC·AC3·DTS·MP3 소리는 앱에서 받기만 되고 재생이 안 되거나 소리가 끊기므로 필요한 만큼만 다시 담거나 인코딩한다
  (이미 H.264/AAC mp4면 그대로). '동영상'(미디어)으로 보내 미리보기 그림이 붙고 받기 전에 바로 재생된다 (파일로 보내지 않는다).
  한도(일반 2GB / 프리미엄 4GB)를 넘으면 나눈 조각도 각각 바로 재생되는 mp4다.
- 네트워크 드라이브(rclone mount 등)에 있는 원본은 그대로 읽으며 인코딩하지 않고, 먼저 이 서버(임시 폴더)로 복사한 뒤 인코딩한다
  (읽기가 느리고 끊겨서). 옆에 있는 자막 파일도 같이 복사한다. 임시 폴더에 자리가 모자라면 올리지 않고 알린다.
- 올린 영상마다 Plex의 작품 정보(포스터·배경·줄거리·장르·출연·회차 제목/줄거리/스틸)를 텔레그램 색인에 같이 남겨
  (telegram_src.plex_meta_put) YMPlayer의 텔레그램 화면이 TMDB 없이도 Plex처럼 보인다.

- 폴더 규칙(kind "folder"): Plex 대신 rclone 드라이브(mount 폴더 또는 rclone 원격)에 새로 올라온 파일을 올린다 (folder_watch.py) -
  텔레그램에서 바로 재생되는 H.264/AAC mp4로(필요할 때만 인코딩), 큰 파일은 720p로 줄이고, 자막 파일은 화면에 입힌다.

설정은 config.json의 "plex_to_tg": {interval, max_gb, rules: [{id, enabled, plex, section, section_title, tg, channel,
channel_title, caption, wait_subs, wait_hours, burn_subs, kind, folder, height}]}
(예전 wait_days는 읽을 때 wait_hours = wait_days x 24로 바꾼다)
"""
import asyncio
import hashlib
import json
import math
import logging
import os
import re
import secrets
import shutil
import threading
import time
import urllib.parse

from aiohttp import ClientTimeout

from . import config_store, folder_watch, telegram_src, transcode

log = logging.getLogger("ym_player.plex_to_tg")

MAX_RULES = 20
MAX_TRIES = 3          # 실패하면 다시 시도하는 횟수 (처음 한 번 + 3번)
RETRY_AFTER = (10 * 60, 30 * 60, 2 * 3600)   # 몇 번째 다시 시도를 얼마 뒤에
GIVE_UP = 99           # fails 값: 다시 하지 않음 (직접 뺌, 너무 큰 파일)
SHOT_RETRY_SEC = 6 * 3600   # 장면 뽑기에 실패한 항목을 다시 해 보기까지
KEEP_DONE = 5000
KEEP_HISTORY = 50
SCAN_SIZE = 50
DEFAULT_CAPTION = "{name}\n\n{summary}"
DEFAULT_WAIT_HOURS = 12   # 새 규칙의 자막 기다리기 기본값 (자막을 영상에 입힌 하드섭은 자동으로 알 수 없어서 끝없이 기다리지 않게)
STREAMABLE = {".mp4", ".m4v", ".mov"}   # 텔레그램 앱에서 받기 전에 바로 재생되는 형식
SUB_EXT = {"srt": "srt", "subrip": "srt", "ass": "ass", "ssa": "ass", "smi": "smi", "sami": "smi", "vtt": "vtt", "webvtt": "vtt"}
MAX_PARTS = 10
PART_LIMIT = 1000 * 1024 * 1024   # 텔레그램 한도 1GB 단위 (2GB = 2000MiB = 4000조각 x 512KB) - max_gb를 곱해 쓴다


def part_name(stem, index, ext):
    """나눠 올리는 조각의 이름: "Hope.2026.1080p-KyoGo" 0 ".mp4" -> "Hope.2026.1080p-KyoGo_0000.mp4" (0부터, 4자리)"""
    return f"{stem}_{index:04d}{ext}"


_KO_FONT = None
PAUSE_CHECK_SEC = 2   # 재생 중인지 이만큼마다 보고 인코딩을 멈추거나 잇는다


def _lowest_priority():
    """(ffmpeg 자식 프로세스에서) CPU 우선순위를 가장 낮게"""
    try:
        os.nice(19)
    except OSError:
        pass


def _signal(proc, name):
    import signal
    sig = getattr(signal, name, None)
    if sig is None or proc.returncode is not None:
        return False
    try:
        proc.send_signal(sig)
        return True
    except (ProcessLookupError, OSError):
        return False



def has_korean_font():
    """libass가 쓸 한글 글꼴이 있나 (fc-list가 없으면 있다고 친다)"""
    global _KO_FONT
    if _KO_FONT is None:
        import subprocess
        try:
            out = subprocess.run(["fc-list", ":lang=ko", "family"], capture_output=True, text=True, timeout=10).stdout
            _KO_FONT = bool(out.strip())
        except (OSError, subprocess.SubprocessError):
            _KO_FONT = True
    return _KO_FONT


_LANG = {"ko": "ko", "kor": "ko", "korean": "ko", "kr": "ko", "en": "en", "eng": "en", "english": "en",
         "ja": "ja", "jpn": "ja", "jp": "ja", "zh": "zh", "chi": "zh", "zho": "zh", "chs": "chs", "cht": "cht"}


def sub_lang(code):
    """자막 파일 이름 꼬리에 붙일 언어 (YMPlayer가 영상 이름과 짝지을 때 떼어 내는 것만 - 그 밖에는 붙이지 않음)"""
    c = str(code or "").strip().lower().replace("_", "-")
    return _LANG.get(c) or _LANG.get(c.split("-")[0], "")


def sidecar_subs(video_path):
    """영상 파일 옆의 자막 파일들 [(경로, 언어, 확장자)] - Plex가 아직 못 읽었어도 (같은 이름 + .ko.srt / .smi 등)"""
    folder, base = os.path.split(video_path)
    stem = os.path.splitext(base)[0]
    out = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for n in names:
        root, ext = os.path.splitext(n)
        sext = SUB_EXT.get(ext[1:].lower())
        if not sext or not (root == stem or root.startswith(stem + ".")):
            continue
        tail = root[len(stem) + 1:] if root != stem else ""
        out.append((os.path.join(folder, n), sub_lang(tail.split(".")[-1]) if tail else "", sext))
    return out


SHOT_AT = 600      # 자막을 사람이 판단할 장면: 10분 지점 (더 짧은 영상은 가운데)


def _shot_path(rule_id, key):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", f"{rule_id}_{key}")[:80]
    return os.path.join(config_store.DATA_DIR, "plex_to_tg_shots", f"{safe}.jpg")


def drop_shot(rule_id, key):
    try:
        os.remove(_shot_path(rule_id, key))
    except OSError:
        pass


class Waiting(Exception):
    """자막이 아직 없음 - 나중에 다시"""
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
def clean_settings(raw):
    raw = raw if isinstance(raw, dict) else {}

    def num(v, lo, hi, default):
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            return default
    rules = []
    for r in raw.get("rules") or []:
        if not isinstance(r, dict):
            continue
        rule = {"id": str(r.get("id") or secrets.token_hex(4))[:16], "enabled": bool(r.get("enabled", True)),
                "plex": str(r.get("plex") or ""), "section": str(r.get("section") or ""),
                "section_title": str(r.get("section_title") or "")[:100], "tg": str(r.get("tg") or ""),
                "channel": str(r.get("channel") or "").strip(), "channel_title": str(r.get("channel_title") or "")[:100],
                "caption": str(r.get("caption") or DEFAULT_CAPTION)[:1000],
                "wait_subs": bool(r.get("wait_subs", True)),
                # 자막을 몇 시간까지 기다릴지 (0이면 계속). 예전 설정의 일 단위(wait_days)는 시간으로 바꿔 읽는다
                "wait_hours": num(r["wait_hours"] if "wait_hours" in r else
                                  (int(r.get("wait_days") or 0) * 24 if "wait_days" in r else DEFAULT_WAIT_HOURS), 0, 8760, DEFAULT_WAIT_HOURS),
                "burn_subs": bool(r.get("burn_subs", True)),
                # 폴더 규칙 (rclone 드라이브 등): kind "folder", folder = 절대 경로 또는 rclone "원격:경로"
                "kind": "folder" if r.get("kind") == "folder" else "plex",
                "folder": str(r.get("folder") or "").strip()[:500],
                # height보다 높은 영상은 용량과 상관없이 height로 줄여 올린다 (0이면 줄이지 않음) - Plex·폴더 규칙 모두
                "height": num(r.get("height"), 0, 2160, 720)}
        if rule["height"] not in (0, 480, 720, 1080):
            rule["height"] = 720
        if rule["kind"] == "folder":
            rule["folder"] = folder_watch.clean_folder(rule["folder"])
            rule["plex"], rule["section"] = "", ""
            rule["section_title"] = rule["folder"].rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1] or rule["folder"]
            if not (rule["tg"] and re.fullmatch(r"-?\d+", rule["channel"])):
                raise ValueError("규칙마다 텔레그램 채널을 골라 주세요.")
        elif not (rule["plex"] and rule["section"] and rule["tg"] and re.fullmatch(r"-?\d+", rule["channel"])):
            raise ValueError("규칙마다 Plex 라이브러리와 텔레그램 채널을 골라 주세요.")
        rules.append(rule)
    if len(rules) > MAX_RULES:
        raise ValueError(f"규칙은 {MAX_RULES}개까지 만들 수 있습니다.")
    # max_gb: 0 = 자동 (올리는 텔레그램 계정이 프리미엄이면 4GB, 아니면 2GB), 2/4 = 그 한도로 고정.
    # 예전 설정의 2(그때 기본값)는 자동으로 읽는다 (일반 계정이면 똑같이 2GB) - 새 화면에서 저장하면 max_gb_v가 붙어 그대로
    max_gb = num(raw.get("max_gb"), 0, 4, 0)
    if max_gb not in (0, 2, 4) or (max_gb == 2 and not raw.get("max_gb_v")):
        max_gb = 0
    # 인코더: auto(되는 하드웨어 인코더 NVIDIA > Intel > VAAPI, 없으면 CPU) / cpu / nvenc / qsv / vaapi.
    # cpu_preset: CPU(libx264)로 할 때 빠르기 - 빠를수록 같은 화질에 파일이 조금 커진다
    encoder = raw.get("encoder") if raw.get("encoder") in folder_watch.ENCODERS else "auto"
    preset = raw.get("cpu_preset") if raw.get("cpu_preset") in folder_watch.CPU_PRESETS else "superfast"
    # 올리기(받기·인코딩·올리기)를 할 시간대 "1-7"(1시~7시) - 비우면 언제나. 새 항목 확인은 늘 한다
    hours = str(raw.get("hours") or "").strip()
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", hours)
    hours = f"{int(m.group(1))}-{int(m.group(2))}" if m and int(m.group(1)) < 24 and int(m.group(2)) <= 24 and m.group(1) != m.group(2) else ""
    return {"interval": num(raw.get("interval"), 2, 1440, 10), "max_gb": max_gb, "max_gb_v": 1, "encoder": encoder,
            "cpu_preset": preset, "hours": hours, "rules": rules}


def in_hours(hours, now=None):
    """지금이 올리기 시간대인지 ("" = 언제나, "1-7" = 1시~7시 전, "22-6" = 밤 10시~아침 6시 전)"""
    if not hours:
        return True
    a, b = (int(x) for x in hours.split("-"))
    h = time.localtime(now).tm_hour
    return a <= h < b if a < b else (h >= a or h < b)


def load_settings():
    try:
        return clean_settings(config_store.load().get("plex_to_tg"))
    except ValueError:
        return clean_settings({})


# ---------------------------------------------------------------------------
# 상태 파일 (올린 것 기억)
# ---------------------------------------------------------------------------
def _state_path():
    return os.path.join(config_store.DATA_DIR, "plex_to_tg.json")


def load_state():
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            st = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        st = {}
    st = st if isinstance(st, dict) else {}
    st.setdefault("rules", {})
    st.setdefault("history", [])
    if not st.get("retry_v"):   # 예전 기록: 실패 3번으로 포기한 것은 다시 3번 시도하게 (직접 뺀 것·너무 큰 파일은 그대로 포기)
        skipped = {(h.get("key")) for h in st["history"] if str(h.get("note") or "").startswith("건너뜀")}
        titles = {h.get("key"): h.get("title") for h in reversed(st["history"])}
        for rs in st["rules"].values():
            fails = rs.get("fails") or {}
            for key, n in list(fails.items()):
                if key in skipped or n >= GIVE_UP:
                    fails[key] = GIVE_UP
                    continue
                if n >= MAX_TRIES:
                    fails[key] = 0
                rs.setdefault("retry", {}).setdefault(key, {"title": titles.get(key) or key, "at": 0})
        st["retry_v"] = 1
    return st


def save_state(st):
    with _lock:
        os.makedirs(config_store.DATA_DIR, exist_ok=True)
        tmp = _state_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, _state_path())


# ---------------------------------------------------------------------------
# 캡션
# ---------------------------------------------------------------------------
class _Blank(dict):
    def __missing__(self, key):
        return ""


def _plain_title(title, show):
    """에피소드 제목이 쓸모없으면(파일 이름 그대로, 작품 이름 반복, "Episode 4") 빈 칸"""
    t = title.strip()
    squash = lambda x: re.sub(r"[\s._\-:~]+", "", x).casefold()
    if not t or (show and squash(t).startswith(squash(show)[:12])):
        return ""
    if re.search(r"\d{3,4}p|\bS\d+E\d+|\.E\d+\.", t, re.I) or re.fullmatch(r"(?:episode|ep\.?|에피소드)\s*\d+|제?\s*\d+\s*화", t, re.I):
        return ""
    return t


def item_fields(m, section_title=""):
    """Plex 메타데이터 -> 캡션에 넣을 값들"""
    kind = m.get("type", "")
    title = str(m.get("title") or "")
    year = str(m.get("year") or "")
    if kind == "episode":
        show = str(m.get("grandparentTitle") or "")
        s, e = m.get("parentIndex"), m.get("index")
        code = f"S{int(s):02d}E{int(e):02d}" if isinstance(s, int) and isinstance(e, int) else ""
        ep_title = _plain_title(title, show)
        name = " ".join(x for x in (show, code) if x) + (f" - {ep_title}" if ep_title else "")
    else:
        show, s, e, code = "", "", "", ""
        name = f"{title} ({year})" if year else title
    part = (((m.get("Media") or [{}])[0].get("Part") or [{}])[0])
    genres = [g.get("tag", "") for g in m.get("Genre") or [] if g.get("tag")]
    return _Blank(name=name, title=title, year=year, show=show, season=s if s is not None else "", episode=e if e is not None else "",
                  code=code, summary=str(m.get("summary") or "").strip(), library=section_title,
                  filename=os.path.basename(str(part.get("file") or "")), genres=", ".join(genres),
                  tags=" ".join("#" + re.sub(r"\s+", "_", g) for g in genres))


def make_caption(template, fields):
    try:
        text = str(template or DEFAULT_CAPTION).format_map(fields)
    except (ValueError, IndexError, AttributeError):   # 중괄호를 잘못 쓴 틀
        text = DEFAULT_CAPTION.format_map(fields)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:1020] + ("…" if len(text) > 1020 else "")


# ---------------------------------------------------------------------------
# 자막 시간 옮기기 (나눈 영상의 조각마다)
# ---------------------------------------------------------------------------
_SRT_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


def _srt_sec(m):
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4).ljust(3, "0")) / 1000


def _srt_fmt(t):
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def shift_srt(text, start, dur):
    """SRT에서 start~start+dur에 걸치는 줄만 남기고 start만큼 당긴다 (번호는 다시 매김)"""
    out, n = [], 0
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = block.splitlines()
        i = next((k for k, ln in enumerate(lines) if "-->" in ln), None)
        if i is None:
            continue
        times = _SRT_TIME.findall(lines[i])
        ts = [m for m in _SRT_TIME.finditer(lines[i])]
        if len(times) < 2:
            continue
        a, b = _srt_sec(ts[0]), _srt_sec(ts[1])
        if b <= start or a >= start + dur:
            continue
        n += 1
        out.append("\n".join([str(n), f"{_srt_fmt(a - start)} --> {_srt_fmt(min(b, start + dur) - start)}", *lines[i + 1:]]))
    return "\n\n".join(out) + "\n" if out else ""


def _ass_sec(v):
    h, m, sec = v.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


def _ass_fmt(t):
    cs = int(round(max(0.0, t) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def shift_ass(text, start, dur):
    """ASS의 Dialogue 줄만 시간을 옮긴다 (머리·스타일은 그대로)"""
    out = []
    for ln in text.splitlines():
        if ln.startswith("Dialogue:"):
            head, rest = ln.split(":", 1)
            f = rest.split(",", 9)
            try:
                a, b = _ass_sec(f[1]), _ass_sec(f[2])
            except (IndexError, ValueError):
                continue
            if b <= start or a >= start + dur:
                continue
            f[1], f[2] = _ass_fmt(a - start), _ass_fmt(min(b, start + dur) - start)
            ln = head + ":" + ",".join(f)
        out.append(ln)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# 돌리기
# ---------------------------------------------------------------------------
class Mirror:
    def __init__(self, plex_json, plex_src, tg_for, http, playing=None):
        """plex_json(src, path, params) -> dict, plex_src(id) -> (cfg, src), tg_for(id) -> TelegramSource, http() -> ClientSession,
        playing() -> 지금 누가 서버 변환(재생)으로 보고 있나 - 그동안은 올리기 인코딩을 멈춰 재생이 끊기지 않게"""
        self.plex_json, self.plex_src, self.tg_for, self.http = plex_json, plex_src, tg_for, http
        self.playing = playing or (lambda: False)
        self.queue = []          # [{rule, key, title, tries}]
        self.current = None      # {title, phase, done, total}
        self.current_job = None  # {rule, key} - 지금 올리는 것
        self.subs_label = ""     # 마지막으로 올린 것의 자막 (기록의 "자막" 칸)
        self.last_check = 0
        self.error = ""
        self.off_hours = ""      # 올리기 시간대가 아니라 쉬는 중이면 그 시간대 ("1-7")
        self._wake = asyncio.Event()
        self._shot_task = None
        self._gb_cache = {}      # 텔레그램 소스 id -> (시각, 파일 한도 GB) - 프리미엄 계정인지
        self._head_info = {}     # (rclone 원격, 파일) -> 앞부분 ffprobe 결과 (자막 트랙 확인용)
        self._shot_fail = {}     # (규칙, 항목) -> (시각, 오류) - 장면 뽑기에 실패한 것 (SHOT_RETRY_SEC 뒤에 다시)

    _job_task = None      # 지금 올리는 작업 (취소용)
    _cancel_req = None    # 직접 취소한 (규칙, 키)

    def cancelled(self):
        """[스레드에서도] 지금 작업을 취소했는지 - 서버로 복사하는 중이면 복사를 멈춘다"""
        return bool(self._cancel_req and self.current_job and
                    self._cancel_req == (self.current_job.get("rule"), self.current_job.get("key")))

    def wake(self):
        self._wake.set()

    def status(self):
        st = load_state()
        return {"queue": [{"title": j["title"], "rule": j["rule"], "key": j["key"]} for j in self.queue],
                "current": {**self.current, **(self.current_job or {})} if self.current else None,
                "last_check": self.last_check, "error": self.error, "off_hours": self.off_hours, "history": st["history"][:KEEP_HISTORY],
                "rules": {rid: {"baseline": r.get("baseline", 0), "count": len(r.get("done", []))} for rid, r in st["rules"].items()},
                "waiting": [{"title": w.get("title", k), "since": w.get("since", 0), "rule": rid, "key": k,
                             "shot": self._shot_mtime(rid, k), "shot_error": (self._shot_fail.get((rid, k)) or (0, ""))[1]}
                            for rid, r in st["rules"].items() for k, w in (r.get("waiting") or {}).items()],
                "retry": [{"title": x.get("title", k), "at": x.get("at", 0), "tries": r.get("fails", {}).get(k, 0), "rule": rid, "key": k}
                          for rid, r in st["rules"].items() for k, x in (r.get("retry") or {}).items()]}

    @staticmethod
    def _shot_mtime(rule_id, key):
        try:
            return int(os.path.getmtime(_shot_path(rule_id, key)))
        except OSError:
            return 0

    def shot_file(self, rule_id, key):
        """미리 만들어 둔 장면 파일 (없으면 None - 화면 요청에서는 만들지 않는다)"""
        path = _shot_path(rule_id, key)
        return path if os.path.exists(path) else None

    def prepare_shots(self):
        """자막 기다리는 항목의 장면을 뒤에서 하나씩 미리 만든다 (이미 돌고 있으면 그대로)"""
        if self._shot_task and not self._shot_task.done():
            return self._shot_task
        self._shot_task = asyncio.ensure_future(self._prepare_shots())
        return self._shot_task

    async def _prepare_shots(self):
        while True:
            todo = [(rid, k) for rid, r in load_state()["rules"].items() for k in (r.get("waiting") or {})
                    if not os.path.exists(_shot_path(rid, k))
                    and time.time() - self._shot_fail.get((rid, k), (0, ""))[0] > SHOT_RETRY_SEC]
            if not todo:
                return
            rid, key = todo[0]
            try:
                await self.screenshot(rid, key)
                self._shot_fail.pop((rid, key), None)
            except Exception as e:   # 이 항목은 SHOT_RETRY_SEC 뒤에 다시 (다른 항목은 계속)
                self._shot_fail[(rid, key)] = (time.time(), str(e)[:200])
                log.info("자동 올리기 장면 뽑기 실패 (%s): %s", key, e)

    async def loop(self):
        await asyncio.sleep(5)
        while True:
            st = load_settings()
            if any(r["enabled"] for r in st["rules"]):
                try:
                    await self.check_all(st)
                    self.error = ""
                except Exception as e:
                    log.warning("자동 올리기 확인 실패: %s", e)
                    self.error = str(e)
                self.off_hours = "" if in_hours(st["hours"]) else st["hours"]
                if not self.off_hours:
                    await self.run_queue(st)
                self.prepare_shots()   # 자막 기다리는 항목의 장면을 뒤에서 미리
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), st["interval"] * 60)
            except asyncio.TimeoutError:
                pass

    async def _recent(self, rule):
        cfg, src = self.plex_src(rule["plex"])
        if not src:
            raise RuntimeError("Plex 소스를 찾을 수 없습니다.")
        secs = await self.plex_json(cfg, src, "/library/sections")
        sec = next((d for d in (secs.get("MediaContainer") or {}).get("Directory") or [] if str(d.get("key")) == rule["section"]), None)
        if not sec:
            raise RuntimeError(f"Plex 라이브러리({rule['section_title'] or rule['section']})가 없습니다.")
        ptype = {"movie": 1, "show": 4}.get(sec.get("type"))
        if not ptype:
            raise RuntimeError("영화나 TV 라이브러리만 올릴 수 있습니다.")
        data = await self.plex_json(cfg, src, f"/library/sections/{rule['section']}/all",
                                    {"type": ptype, "sort": "addedAt:desc", "X-Plex-Container-Start": 0, "X-Plex-Container-Size": SCAN_SIZE})
        return [m for m in (data.get("MediaContainer") or {}).get("Metadata") or [] if m.get("ratingKey")]

    async def check_all(self, st=None):
        st = st or load_settings()
        state = load_state()
        queued = {(j["rule"], j["key"]) for j in self.queue}
        errors = []
        for rule in st["rules"]:
            if not rule["enabled"]:
                continue
            rs = state["rules"].setdefault(rule["id"], {"done": [], "fails": {}})
            if rule["kind"] == "folder":
                try:
                    files = await folder_watch.scan(rule["folder"])
                except folder_watch.FolderError as e:   # 이 폴더만 못 봄 - 다른 규칙은 계속
                    errors.append(f"{rule['section_title']}: {e}")
                    continue
                if "baseline" not in rs:      # 처음: 지금 있는 파일은 올리지 않는다 (이름을 기억해 둠)
                    if not files:
                        # 비어 보이면 기준을 잡지 않는다 - 마운트가 아직 안 붙었거나 목록을 못 받은 것일 수 있고, 빈 기준을 잡으면
                        # 다음 확인 때 폴더 전체가 '새 파일'로 줄에 들어간다. 정말 빈 폴더면 첫 파일이 생길 때 기준을 잡는다
                        errors.append(f"{rule['section_title']}: 폴더가 비어 보여 아직 기준을 잡지 않았습니다 (마운트·경로 확인)")
                        continue
                    rs["baseline"] = int(time.time())
                    rs["known"] = [folder_watch.key_hash(f["key"]) for f in files]
                    rs["sizes"] = {}
                    continue
            else:
                items = await self._recent(rule)
                newest = max((int(m.get("addedAt") or 0) for m in items), default=0)
                if "baseline" not in rs:          # 처음: 지금 있는 것은 올리지 않는다
                    rs["baseline"] = newest or int(time.time())
                    continue
            done = set(rs.get("done", []))
            for key, w in list(rs.get("waiting", {}).items()):   # 자막을 기다리던 것 (목록 앞쪽에서 밀려났어도)
                if key in done:
                    rs["waiting"].pop(key, None)
                elif (rule["id"], key) not in queued:
                    self.queue.append({"rule": rule["id"], "key": key, "title": w.get("title", key)})
                    queued.add((rule["id"], key))
            now = time.time()
            for key, r in list((rs.get("retry") or {}).items()):   # 실패해서 다시 해 볼 차례가 된 것
                if key in done:
                    rs["retry"].pop(key, None)
                elif r.get("at", 0) <= now and (rule["id"], key) not in queued:
                    self.queue.append({"rule": rule["id"], "key": key, "title": r.get("title", key)})
                    queued.add((rule["id"], key))
            if rule["kind"] == "folder":
                self._queue_files(rule, rs, files, done, queued)
                continue
            for m in sorted(items, key=lambda x: int(x.get("addedAt") or 0)):
                key = str(m["ratingKey"])
                if int(m.get("addedAt") or 0) <= rs["baseline"] or key in done or (rule["id"], key) in queued:
                    continue
                if key in rs.get("fails", {}):   # 실패한 적이 있으면 retry(정해진 시각)로만 다시
                    continue
                self.queue.append({"rule": rule["id"], "key": key, "title": item_fields(m)["name"]})
                queued.add((rule["id"], key))
        self.last_check = int(time.time())
        save_state(state)
        if errors:
            raise RuntimeError(" / ".join(errors))

    def _queue_files(self, rule, rs, files, done, queued):
        """폴더 규칙: 처음 보는 파일 중 다 올라온 것(크기가 그대로이거나 수정한 지 STABLE_SEC 지남)을 줄에"""
        known = set(rs.get("known") or [])
        sizes, now, fresh = rs.setdefault("sizes", {}), time.time(), {}
        for f in sorted(files, key=lambda x: x["mtime"]):
            key = f["key"]
            if folder_watch.key_hash(key) in known or key in done or (rule["id"], key) in queued or key in rs.get("fails", {}):
                continue
            if sizes.get(key) == f["size"] or now - f["mtime"] >= folder_watch.STABLE_SEC:
                self.queue.append({"rule": rule["id"], "key": key, "title": folder_watch.file_fields(key)["name"]})
                queued.add((rule["id"], key))
            else:   # 아직 올라오는 중일 수 있다 - 다음 확인 때 크기가 같으면
                fresh[key] = f["size"]
        rs["sizes"] = fresh

    async def test_latest(self, rule_id):
        """규칙 시험: 가장 최근 항목 하나를 (올린 적이 있어도) 올린다"""
        rule = next((r for r in load_settings()["rules"] if r["id"] == rule_id), None)
        if not rule:
            raise ValueError("없는 규칙입니다. 먼저 저장해 주세요.")
        if rule["kind"] == "folder":
            try:
                files = await folder_watch.scan(rule["folder"])
            except folder_watch.FolderError as e:
                raise ValueError(str(e))
            if not files:
                raise ValueError("이 폴더에 동영상이 없습니다.")
            f = max(files, key=lambda x: x["mtime"])
            title = folder_watch.file_fields(f["key"])["name"]
            self.queue.append({"rule": rule["id"], "key": f["key"], "title": title, "test": True})
            self.wake()
            return title
        items = await self._recent(rule)
        if not items:
            raise ValueError("이 라이브러리에 동영상이 없습니다.")
        m = items[0]
        self.queue.append({"rule": rule["id"], "key": str(m["ratingKey"]), "title": item_fields(m)["name"], "test": True})
        self.wake()
        return item_fields(m)["name"]

    async def screenshot(self, rule_id, key):
        """자막을 기다리는 항목의 10분 지점 장면(JPEG 경로) - 자막을 영상에 입힌 하드섭인지 사람이 보고 판단하게.
        prepare_shots가 뒤에서 하나씩 ffmpeg로 뽑아 data/plex_to_tg_shots/에 두고(화면은 만든 파일만 가져감),
        항목이 대기에서 빠지면 지운다"""
        path = _shot_path(rule_id, key)
        if os.path.exists(path):
            return path
        rule = next((r for r in load_settings()["rules"] if r["id"] == rule_id), None)
        if not rule or not transcode.ffmpeg_path():
            raise ValueError("장면을 뽑을 수 없습니다 (규칙이 없거나 서버에 ffmpeg가 없음).")
        if rule["kind"] == "folder":
            if folder_watch.is_remote(rule["folder"]):
                raise ValueError("rclone 원격 파일은 장면을 뽑지 않습니다 (드라이브를 mount해서 폴더로 적으면 됩니다).")
            local = folder_watch.local_path(rule["folder"], key)
            dur = folder_watch.duration(await folder_watch.probe(local))
            at = SHOT_AT if not dur or dur > SHOT_AT + 5 else dur / 2
            return await self._grab(["-ss", f"{at:.0f}", "-i", local], path)
        cfg, src = self.plex_src(rule["plex"])
        if not src:
            raise ValueError("Plex 소스를 찾을 수 없습니다.")
        data = await self.plex_json(cfg, src, f"/library/metadata/{key}")
        m = ((data.get("MediaContainer") or {}).get("Metadata") or [None])[0]
        if not m:
            raise ValueError("Plex에서 항목을 찾지 못했습니다.")
        media = (m.get("Media") or [{}])[0]
        part = (media.get("Part") or [{}])[0]
        duration = (m.get("duration") or media.get("duration") or 0) / 1000
        at = SHOT_AT if not duration or duration > SHOT_AT + 5 else duration / 2
        local = str(part.get("file") or "")
        args = ["-ss", f"{at:.0f}"]
        if local and os.path.isfile(local) and os.access(local, os.R_OK):
            args += ["-i", local]
        elif part.get("key"):   # Plex가 다른 컴퓨터면 Plex 주소로 (그 지점 근처만 Range로 받는다)
            args += ["-headers", f"X-Plex-Token: {src.get('token', '')}\r\n", "-i", f"{src['url']}{part['key']}"]
        else:
            raise ValueError("Plex 항목에 파일 정보가 없습니다.")
        return await self._grab(args, path)

    async def _grab(self, args, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ok, err = await self._ffmpeg(*args, "-frames:v", "1", "-vf", "scale='min(960,iw)':-2", "-q:v", "4", path + ".tmp.jpg")
        if not ok or not os.path.exists(path + ".tmp.jpg"):
            raise ValueError(f"장면을 뽑지 못했습니다: {err.strip()[-200:]}")
        os.replace(path + ".tmp.jpg", path)
        return path

    def force_item(self, rule_id, key):
        """자막 기다리는 항목을 기다리지 않고 지금 올린다 (자막을 영상에 입힌 하드섭처럼 자동으로 알 수 없는 경우)"""
        state = load_state()
        w = (state["rules"].get(rule_id, {}).get("waiting") or {}).pop(key, None)
        if w is None:
            raise ValueError("자막을 기다리는 항목이 아닙니다.")
        save_state(state)
        drop_shot(rule_id, key)
        job = next((j for j in self.queue if j["rule"] == rule_id and j["key"] == key), None)
        if job:
            job["force"] = True
        else:
            self.queue.append({"rule": rule_id, "key": key, "title": w.get("title", key), "force": True})
        self.wake()
        return w.get("title", key)

    def drop_item(self, rule_id, key):
        """자막 기다리는 항목을 올리지 않고 뺀다 (다시 들어오지 않게)"""
        state = load_state()
        rs = state["rules"].get(rule_id, {})
        w = (rs.get("waiting") or {}).pop(key, None)
        if w is None:
            raise ValueError("자막을 기다리는 항목이 아닙니다.")
        rs.setdefault("fails", {})[key] = GIVE_UP
        (rs.get("retry") or {}).pop(key, None)
        drop_shot(rule_id, key)
        state["history"] = [{"title": w.get("title", key), "ok": False, "note": "건너뜀: 직접 뺌", "at": int(time.time()), "key": key,
                             "subs": "", "channel": ""}, *state["history"]][:KEEP_HISTORY]
        save_state(state)
        running = self.queue[0] if self.current_job and self.queue else None   # 지금 올리는 중인 것은 건드리지 않는다
        self.queue = [j for j in self.queue if j is running or not (j["rule"] == rule_id and j["key"] == key)]
        return w.get("title", key)

    def _forget(self, state, rule_id, key, title, note):
        """다시 올리지 않게 표시하고 기록에 남긴다 (폴더 규칙은 그 파일 이름을 기억해 다시 줄에 넣지 않는다)"""
        rs = state["rules"].setdefault(rule_id, {"done": [], "fails": {}})
        rs.setdefault("fails", {})[key] = GIVE_UP
        (rs.get("retry") or {}).pop(key, None)
        (rs.get("waiting") or {}).pop(key, None)
        if "known" in rs:   # 폴더 규칙
            h = folder_watch.key_hash(key)
            if h not in rs["known"]:
                rs["known"].append(h)
        drop_shot(rule_id, key)
        state["history"] = [{"title": title, "ok": False, "note": note, "at": int(time.time()), "key": key,
                             "subs": "", "channel": ""}, *state["history"]][:KEEP_HISTORY]

    def cancel_item(self, rule_id, key):
        """올릴 차례·자막 기다림·다시 시도 대기·지금 올리는 중인 항목 하나를 올리지 않는다 (다시 들어오지 않게)"""
        state = load_state()
        rs = state["rules"].get(rule_id) or {}
        job = next((j for j in self.queue if j["rule"] == rule_id and j["key"] == key), None)
        title = (job or {}).get("title") or ((rs.get("waiting") or {}).get(key) or {}).get("title") \
            or ((rs.get("retry") or {}).get(key) or {}).get("title")
        running = bool(self.current_job and self.current_job.get("rule") == rule_id and self.current_job.get("key") == key)
        if not title and not running:
            raise ValueError("진행 목록에 없는 항목입니다 (이미 끝났거나 빠졌을 수 있음).")
        title = title or key
        if running:   # 지금 올리는 중: 그 작업을 멈춘다 (받기·복사·인코딩·올리기 어디에서든) - 기록은 run_queue가 남긴다
            self._cancel_req = (rule_id, key)
            if self._job_task and not self._job_task.done():
                self._job_task.cancel()
            return title
        self._forget(state, rule_id, key, title, "건너뜀: 직접 취소")
        save_state(state)
        self.queue = [j for j in self.queue if not (j["rule"] == rule_id and j["key"] == key)]
        return title

    def cancel_all(self, rule_id=None):
        """진행 목록 전체(rule_id가 있으면 그 규칙만)를 취소한다 - 지금 올리는 중인 것도. 취소한 수를 돌려준다"""
        state = load_state()
        items = {}
        for j in self.queue:
            if rule_id in (None, j["rule"]):
                items[(j["rule"], j["key"])] = j["title"]
        for rid, rs in state["rules"].items():
            if rule_id not in (None, rid):
                continue
            for k, w in list((rs.get("waiting") or {}).items()) + list((rs.get("retry") or {}).items()):
                items.setdefault((rid, k), w.get("title", k))
        cur = (self.current_job or {}).get("rule"), (self.current_job or {}).get("key")
        n = 0
        for (rid, k), title in items.items():
            if (rid, k) == cur:
                continue
            self._forget(state, rid, k, title, "건너뜀: 직접 취소 (전체 취소)")
            n += 1
        save_state(state)
        keep = [j for j in self.queue if (j["rule"], j["key"]) == cur]
        self.queue = keep + [j for j in self.queue if j not in keep and rule_id not in (None, j["rule"])]
        if cur[0] and rule_id in (None, cur[0]):
            self.cancel_item(*cur)
            n += 1
        return n

    async def run_queue(self, st):
        rules = {r["id"]: r for r in st["rules"]}
        while self.queue:
            if not in_hours(st["hours"]):   # 정한 시간대가 끝나면 남은 것은 다음 시간대에
                self.off_hours = st["hours"]
                break
            job = self.queue[0]
            rule = rules.get(job["rule"])
            ok, note, waiting = False, "", False
            self.current_job, self.subs_label = {"rule": job["rule"], "key": job["key"]}, ""
            cancelled = False
            self._cancel_req = None
            if rule:
                try:
                    self._job_task = asyncio.ensure_future(self.upload(rule, job["key"], st["max_gb"], test=job.get("test") or job.get("force")))
                    note = await self._job_task
                    ok = True
                except asyncio.CancelledError:
                    if self._cancel_req != (job["rule"], job["key"]):   # 직접 취소가 아니면 (서버 끄기 등) 그대로 멈춘다
                        if self._job_task and not self._job_task.done():
                            self._job_task.cancel()
                        raise
                    cancelled = True
                except Waiting:
                    waiting = True
                except Exception as e:
                    wait = getattr(e, "seconds", None) if type(e).__name__ == "FloodWaitError" else None
                    if wait:   # 텔레그램이 기다리라고 함 - 그만큼 쉬고 같은 것을 다시
                        self.current = {"title": job["title"], "phase": f"텔레그램 요청 제한 - {wait}초 기다리는 중", "done": 0, "total": 0}
                        await asyncio.sleep(min(int(wait) + 1, 3600))
                        continue
                    note = str(e) or type(e).__name__
                    log.warning("자동 올리기 실패 (%s): %s", job["title"], note)
            self._job_task = None
            if self.queue and self.queue[0] is job:
                self.queue.pop(0)
            else:
                self.queue = [j for j in self.queue if j is not job]
            self.current = self.current_job = None
            state = load_state()
            if cancelled:   # 올리던 중에 직접 취소
                self._forget(state, job["rule"], job["key"], job["title"], "건너뜀: 올리던 중 직접 취소")
                save_state(state)
                self._cancel_req = None
                continue
            rs = state["rules"].setdefault(job["rule"], {"done": [], "fails": {}})
            if waiting:   # 자막이 생길 때까지 (기록에는 남기지 않고 상태의 "자막 기다리는 중"에)
                rs.setdefault("waiting", {}).setdefault(job["key"], {"title": job["title"], "since": int(time.time())})
                save_state(state)
                continue
            if (rs.get("waiting") or {}).pop(job["key"], None) is not None:
                drop_shot(job["rule"], job["key"])   # 대기에서 빠졌으면 판단용 장면도
            fails = rs.setdefault("fails", {})
            retry = rs.setdefault("retry", {})
            if ok and note.startswith("건너뜀"):   # 너무 큰 파일 - 다시 시도하지 않는다
                ok = False
                fails[job["key"]] = GIVE_UP
                retry.pop(job["key"], None)
            elif ok:
                rs["done"] = [*rs.get("done", []), job["key"]][-KEEP_DONE:]
                if (rule or {}).get("kind") == "folder":   # 폴더 규칙은 올린 파일 이름을 따로 끝까지 기억 (done은 최근 것만)
                    rs.setdefault("known", []).append(folder_watch.key_hash(job["key"]))
                fails.pop(job["key"], None)
                retry.pop(job["key"], None)
            elif not job.get("test"):
                # 실패: 10분 -> 30분 -> 2시간 뒤에 다시 (3번까지). Plex 최근 목록에서 밀려나도 retry에 남아 있어서 다시 한다
                n = fails[job["key"]] = min(fails.get(job["key"], 0), MAX_TRIES) + 1
                if n <= MAX_TRIES:
                    wait = RETRY_AFTER[min(n, len(RETRY_AFTER)) - 1]
                    retry[job["key"]] = {"title": job["title"], "at": int(time.time()) + wait}
                    note += f" (다시 시도 {n}/{MAX_TRIES}: {wait // 60}분 뒤)"
                else:
                    retry.pop(job["key"], None)
                    note += f" (다시 시도 {MAX_TRIES}번 모두 실패 - 그만둠)"
            state["history"] = [{"title": job["title"], "ok": ok, "note": note, "at": int(time.time()), "key": job["key"],
                                 "subs": self.subs_label if ok else "", "channel": (rule or {}).get("channel_title", "")},
                                *state["history"]][:KEEP_HISTORY]
            save_state(state)

    async def _account_gb(self, tg_id, client):
        """올리는 텔레그램 계정의 파일 한도(GB): 프리미엄 4, 일반 2 (한 시간 기억)"""
        hit = self._gb_cache.get(tg_id)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
        try:
            me = await client.get_me()
            gb = 4 if getattr(me, "premium", False) else 2
        except Exception as e:   # 모르면 일반 계정 한도로 (작게 나누는 쪽이 안전)
            log.info("텔레그램 계정 종류를 알 수 없어 2GB 한도로 나눕니다: %s", e)
            gb = 2
        self._gb_cache[tg_id] = (time.time(), gb)
        return gb

    async def upload(self, rule, key, max_gb, test=False):
        if rule.get("kind") == "folder":
            return await self.upload_file(rule, key, max_gb, test)
        cfg, src = self.plex_src(rule["plex"])
        data = await self.plex_json(cfg, src, f"/library/metadata/{key}")
        m = ((data.get("MediaContainer") or {}).get("Metadata") or [None])[0]
        if not m:
            raise RuntimeError("Plex에서 항목을 찾지 못했습니다 (지워졌을 수 있음).")
        media = (m.get("Media") or [{}])[0]
        part = (media.get("Part") or [{}])[0]
        if not part.get("key"):
            raise RuntimeError("Plex 항목에 파일 정보가 없습니다.")
        fields = item_fields(m, rule["section_title"])
        subs = [x for x in part.get("Stream") or [] if x.get("streamType") == 3]
        local_file = str(part.get("file") or "")
        local_ok = bool(local_file and os.path.isfile(local_file) and os.access(local_file, os.R_OK))
        side = sidecar_subs(local_file) if local_ok else []   # Plex가 아직 못 읽은 옆 자막 파일도 자막으로 친다
        # 파일 안의 자막 트랙(소프트 자막)은 Plex 정보에 없을 수 있어 파일을 직접 열어 본다 (ffprobe, 머리만 읽음)
        soft = await self._probe_subs(local_file if local_ok else f"{src['url']}{part['key']}",
                                      None if local_ok else src.get("token"))
        if soft is None:   # ffprobe가 없거나 못 열었으면 Plex 정보로
            soft = [x for x in subs if not x.get("key")]
        if rule["wait_subs"] and not subs and not side and not soft and not test:
            w = (load_state()["rules"].get(rule["id"], {}).get("waiting") or {}).get(key)
            if not rule["wait_hours"] or not w or time.time() - w.get("since", time.time()) < rule["wait_hours"] * 3600:
                raise Waiting()
        size = int(part.get("size") or 0)
        tg = self.tg_for(rule["tg"])
        client = await tg.authorized_client()
        max_gb = max_gb or await self._account_gb(rule["tg"], client)   # 자동: 그 계정이 프리미엄이면 4GB, 아니면 2GB
        limit = int(max_gb * PART_LIMIT * 0.97)
        duration = (m.get("duration") or media.get("duration") or 0) / 1000
        if size > limit and not transcode.ffmpeg_path():
            return f"건너뜀: 파일이 {size / 1024 ** 3:.1f}GB인데 나눌 ffmpeg가 없습니다."
        if size > limit and duration <= 0:
            return f"건너뜀: 파일이 {size / 1024 ** 3:.1f}GB인데 길이를 몰라 나눌 수 없습니다."
        name = os.path.basename(str(part.get("file") or "")) or f"{key}.{part.get('container') or 'mp4'}"
        name = re.sub(r'[\\/:*?"<>|]', "_", name)
        stem, ext = os.path.splitext(name)
        peer = await tg.peer(int(rule["channel"]))
        self.current = {"title": fields["name"], "phase": "준비 중", "done": 0, "total": size}
        tmp = os.path.join(config_store.DATA_DIR, "plex_to_tg_tmp", secrets.token_hex(6))
        os.makedirs(tmp, exist_ok=True)
        msgs = []
        try:
            local = local_file
            copied = False
            if not local_ok:   # Plex가 다른 컴퓨터면 받아 온다
                local = os.path.join(tmp, name)
                await self._download(cfg, src, part["key"], local, fields["name"], size)
            elif folder_watch.on_network_drive(local_file):   # rclone mount 등: 서버로 복사해 놓고 인코딩 (자막 파일도)
                local, side = await self._bring_local(local_file, side, tmp, fields["name"])
                copied = True
            # 외부 자막 파일 (내장 자막은 영상 안에 그대로 들어 있다)
            sub_files, seen, sub_fail = [], set(), 0
            for x in subs:
                sext = SUB_EXT.get(str(x.get("codec") or x.get("format") or "").lower())
                if x.get("key") and sext:
                    path = os.path.join(tmp, f"sub{len(sub_files)}.{sext}")
                    if await self._fetch(cfg, src, x["key"], path):
                        with open(path, "rb") as f:
                            seen.add(hashlib.sha1(f.read()).hexdigest())
                        sub_files.append((path, sub_lang(x.get("languageCode") or x.get("languageTag")), sext))
                    else:
                        sub_fail += 1
                        log.warning("자동 올리기: Plex 자막 %s를 받지 못했습니다 (%s)", x.get("key"), fields["name"])
            for path, lang, sext in side:   # 옆 자막 파일 (Plex에서 받은 것과 내용이 같으면 빼고)
                with open(path, "rb") as f:
                    h = hashlib.sha1(f.read()).hexdigest()
                if h not in seen:
                    seen.add(h)
                    sub_files.append((path, lang, sext))
            burned, burn_skip = False, ""
            embedded = bool(soft)
            if rule["burn_subs"] and sub_files and not embedded and not has_korean_font():
                burn_skip = "한글 글꼴이 없어 자막을 입히지 않고 파일로 올림 (sudo apt install fonts-nanum)"
            burn_name = None
            if rule["burn_subs"] and sub_files and not embedded and has_korean_font():   # 영상 안에 자막이 없으면 외부 자막을 입힌다
                best = next((f for f in sub_files if f[1].lower() in ("kor", "ko", "ko-kr")), sub_files[0])
                burn_name = os.path.basename(self.utf8_sub(best[0], os.path.join(tmp, f"burn.{best[2]}")))
            # 정한 높이(기본 720p)보다 큰 영상은 용량과 상관없이 줄인다 - 자막 입히기와 한 번에 인코딩
            info = await folder_watch.probe(local)
            vh = int(((folder_watch.streams(info, "video") or [{}])[0]).get("height") or 0)
            scale = bool(rule["height"] and vh > rule["height"])
            width, height = int(media.get("width") or 0), int(media.get("height") or 0)
            enc_used, remux_notes = "", []
            # mp4로 바꾸면 파일 안의 자막 트랙(소프트 자막)은 따라오지 못한다 → 글자 자막이면 꺼내서 화면에 입히거나
            # (자막 입히기를 켰고 한글 글꼴이 있을 때) 자막 파일로 같이 올린다. 그림 자막(PGS 등)은 옮길 수 없다
            soft_lost = False
            will_convert = bool(info) and transcode.ffmpeg_path() and \
                folder_watch.encode_plan(info, ext, rule["height"], burn_name, None) is not None
            if embedded and will_convert and not burn_name:
                inside = folder_watch.text_subs(info)
                raw = None
                if inside:
                    idx, ilang, isext = inside[0]   # 한국어 먼저 (text_subs가 그렇게 늘어놓는다)
                    raw = os.path.join(tmp, f"inside.{isext}")
                    ok, err = await self._ffmpeg("-i", local, "-map", f"0:{idx}", "-c:s", "ass" if isext == "ass" else "srt", raw)
                    if not ok or not os.path.exists(raw) or os.path.getsize(raw) == 0:
                        log.info("자동 올리기: 파일 안의 자막을 꺼내지 못했습니다 (%s): %s", fields["name"], err.strip()[-200:])
                        raw = None
                if raw and rule["burn_subs"] and has_korean_font():
                    burn_name = os.path.basename(self.utf8_sub(raw, os.path.join(tmp, f"burn.{isext}")))
                elif raw:
                    sub_files.append((raw, sub_lang(ilang), isext))
                else:
                    soft_lost = True
            # 텔레그램 앱에서 바로 재생되게 언제나 MP4(H.264 + AAC)로 - 이미 그런 파일이면 손대지 않는다 (plan None)
            out = os.path.join(tmp, f"{stem}.enc.mp4")
            plan = await self._encode_plan(local, lambda enc: folder_watch.encode_plan(info, ext, rule["height"], burn_name, enc),
                                           out, duration, fields["name"], tmp) if transcode.ffmpeg_path() and info else None
            if plan:
                enc_used = folder_watch.ENCODER_NAMES.get(plan.encoder, "")
                remux_notes = [n for n in plan[1] if n not in ("자막 입힘",) and "p로 줄임" not in n]
                if copied or not local_ok:   # 서버로 가져온 원본은 바로 지운다 (디스크)
                    try:
                        os.remove(local)
                    except OSError:
                        pass
                local, name, ext = out, f"{stem}.mp4", ".mp4"
                burned, scaled = bool(burn_name), scale
                v = (folder_watch.streams(await folder_watch.probe(local), "video") or [{}])[0]
                width, height = int(v.get("width") or width), int(v.get("height") or height)
                if burned:
                    sub_files = []   # 화면에 들어갔으니 따로 올리지 않는다
            else:
                scaled = False
            thumb = await self._art(cfg, src, m.get("thumb") or m.get("grandparentThumb") or m.get("parentThumb"), 320, tmp)
            thumb = thumb if thumb and os.path.getsize(thumb) <= 200 * 1024 else None
            # 나누기 (각 조각이 따로 재생되게 시간으로)
            pieces = [(local, 0, duration)]
            if os.path.getsize(local) > limit:
                pieces = await self._split(local, os.path.join(tmp, stem), ext, duration, limit, fields["name"])
            caption = make_caption(rule["caption"], fields)
            sent_subs = await self._send_pieces(client, peer, pieces, name, caption, width, height, thumb, sub_files,
                                                fields["name"], tmp, msgs)
            await self._save_meta(rule, cfg, src, m, [x for x in msgs if x], tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        log.info("자동 올리기: %s -> %s", fields["name"], rule["channel_title"] or rule["channel"])
        self.subs_label = ("화면에 입힘" if burned else f"파일 {sent_subs}개" if sent_subs
                           else "옮기지 못함" if soft_lost else "소프트 자막" if embedded else "없음")
        notes = []
        if copied:
            notes.append("서버로 복사 후 처리")
        if burned:
            notes.append("자막 입혀 인코딩")
        if scaled:
            notes.append(f"{rule['height']}p로 줄임")
        notes += remux_notes
        if ext.lower() not in STREAMABLE:
            notes.append("mp4가 아니라 앱에서 바로 재생되지 않을 수 있음 (ffmpeg 없음)")
        if enc_used:
            notes.append(f"인코더 {enc_used}")
        if burn_skip:
            notes.append(burn_skip)
        if len(msgs) > 1:
            notes.append(f"{len(msgs)}개로 나눠 올림 ({max_gb}GB 한도)")
        if sent_subs:
            notes.append(f"자막 파일 {sent_subs}개 첨부")
        elif soft_lost:
            notes.append("파일 안의 자막이 그림 자막이라 mp4로 옮기지 못함")
        elif embedded and not burned:
            notes.append("내장 자막")
        if sub_fail:
            notes.append(f"Plex 자막 {sub_fail}개를 받지 못함")
        if not subs and not side and not soft:
            notes.append("자막 없음")
        return ", ".join(notes)

    async def upload_file(self, rule, key, max_gb, test=False):
        """폴더 규칙(rclone 드라이브 등)의 파일 하나: 텔레그램에서 바로 재생되게 H.264/AAC mp4로 (필요할 때만 인코딩),
        크면 720p로 줄이고, 자막 파일이 있으면 화면에 입혀서 올린다"""
        if not transcode.ffmpeg_path():
            raise RuntimeError("서버에 ffmpeg가 없어 인코딩할 수 없습니다.")
        folder = rule["folder"]
        remote = folder_watch.is_remote(folder)
        copied = False
        fields = folder_watch.file_fields(key, rule["section_title"])
        name = re.sub(r'[\\/:*?"<>|]', "_", os.path.basename(key)) or "video.mp4"
        stem, ext = os.path.splitext(name)
        self.current = {"title": fields["name"], "phase": "준비 중", "done": 0, "total": 0}
        tmp = os.path.join(config_store.DATA_DIR, "plex_to_tg_tmp", secrets.token_hex(6))
        os.makedirs(tmp, exist_ok=True)
        msgs = []
        try:
            # 1) 자막: 옆 자막 파일, 없으면 파일 안의 자막 트랙 (원격이면 앞부분만 받아 본다)
            if remote:
                side = await folder_watch.remote_subs(folder, key)
                info = self._head_info.get((folder, key))
                if info is None:   # 자막을 기다리는 동안 확인할 때마다 다시 받지 않게 기억 (파일 안의 트랙은 바뀌지 않는다)
                    head = os.path.join(tmp, "head" + ext)
                    info = await folder_watch.probe(head) if await folder_watch.fetch_head(folder, key, head) else {}
                    self._head_info = {**dict(list(self._head_info.items())[-200:]), (folder, key): info}
            else:
                local = folder_watch.local_path(folder, key)
                if not os.path.isfile(local):
                    raise RuntimeError("파일이 없습니다 (지워졌거나 옮겨짐).")
                side = sidecar_subs(local)
                info = await folder_watch.probe(local)
            any_inside = bool(folder_watch.streams(info, "subtitle"))
            if rule["wait_subs"] and not side and not any_inside and not test:
                w = (load_state()["rules"].get(rule["id"], {}).get("waiting") or {}).get(key)
                if not rule["wait_hours"] or not w or time.time() - w.get("since", time.time()) < rule["wait_hours"] * 3600:
                    raise Waiting()
            tg = self.tg_for(rule["tg"])
            client = await tg.authorized_client()
            max_gb = max_gb or await self._account_gb(rule["tg"], client)
            limit = int(max_gb * PART_LIMIT * 0.97)
            peer = await tg.peer(int(rule["channel"]))
            # 2) 원격이면 통째로 받기 (자막 파일도)
            sub_files = []
            if remote:
                local = os.path.join(tmp, name)
                self.current = {"title": fields["name"], "phase": "rclone에서 받는 중", "done": 0, "total": 0}

                def got(n):
                    if self.current:
                        self.current["done"] = n
                await folder_watch.fetch(folder, key, local, got)
                info = await folder_watch.probe(local)
                for i, (rel, lang, sext) in enumerate(side):
                    dest = os.path.join(tmp, f"side{i}.{sext}")
                    await folder_watch.fetch(folder, rel, dest)
                    sub_files.append((dest, sub_lang(lang), sext))
            else:
                sub_files = [(pth, lang, sext) for pth, lang, sext in side]
                if folder_watch.on_network_drive(local):   # rclone mount 폴더: 서버로 복사해 놓고 인코딩 (자막 파일도)
                    local, sub_files = await self._bring_local(local, sub_files, tmp, fields["name"])
                    copied = True
            if not info:
                raise RuntimeError("영상 정보를 읽지 못했습니다 (ffprobe).")
            duration = folder_watch.duration(info)
            # 3) 화면에 입힐 자막: 옆 자막 파일(한국어 먼저), 없으면 파일 안의 글자 자막 트랙
            burn, burn_skip, inside = None, "", folder_watch.text_subs(info)
            if rule["burn_subs"] and (sub_files or inside):
                if not has_korean_font():
                    burn_skip = "한글 글꼴이 없어 자막을 입히지 않고 파일로 올림 (sudo apt install fonts-nanum)"
                elif sub_files:
                    best = next((f for f in sub_files if f[1].lower() in ("kor", "ko", "ko-kr")), sub_files[0])
                    burn = os.path.basename(self.utf8_sub(best[0], os.path.join(tmp, f"burn.{best[2]}")))
                else:
                    idx, _lang, sext = inside[0]
                    raw = os.path.join(tmp, f"inside.{sext}")
                    ok, err = await self._ffmpeg("-i", local, "-map", f"0:{idx}", "-c:s", "ass" if sext == "ass" else "srt", raw)
                    if ok and os.path.getsize(raw) > 0:
                        burn = os.path.basename(self.utf8_sub(raw, os.path.join(tmp, f"burn.{sext}")))
                    else:
                        log.info("자동 올리기: 파일 안의 자막을 꺼내지 못했습니다 (%s): %s", fields["name"], err.strip()[-200:])
            # 4) 인코딩 (필요할 때만)
            out = os.path.join(tmp, f"{stem}.mp4")
            plan = await self._encode_plan(local, lambda enc: folder_watch.encode_plan(info, ext, rule["height"], burn, enc),
                                           out, duration, fields["name"], tmp)
            notes = []
            if plan:
                notes = list(plan[1]) + ([f"인코더 {folder_watch.ENCODER_NAMES[plan.encoder]}"] if plan.encoder in folder_watch.ENCODER_NAMES else [])
                if remote or copied:   # 받은(복사한) 원본은 바로 지운다 (디스크)
                    os.remove(local)
                local, name, ext = out, f"{stem}.mp4", ".mp4"
                info = await folder_watch.probe(local) or info
            if burn:
                sub_files = []   # 화면에 들어갔으니 따로 올리지 않는다
            v = (folder_watch.streams(info, "video") or [{}])[0]
            # 5) 나누기·미리보기 그림
            pieces = [(local, 0, duration)]
            if os.path.getsize(local) > limit:
                if duration <= 0:
                    raise RuntimeError("파일이 한도보다 큰데 길이를 몰라 나눌 수 없습니다.")
                pieces = await self._split(local, os.path.join(tmp, stem), ext, duration, limit, fields["name"])
            thumb = os.path.join(tmp, "thumb.jpg")
            ok, _err = await self._ffmpeg("-ss", f"{min(60.0, duration * 0.1):.1f}", "-i", local, "-frames:v", "1",
                                          "-vf", "scale=320:-2", "-q:v", "5", thumb)
            thumb = thumb if ok and os.path.exists(thumb) and os.path.getsize(thumb) <= 200 * 1024 else None
            caption = make_caption(rule["caption"], fields)
            sent_subs = await self._send_pieces(client, peer, pieces, name, caption, int(v.get("width") or 0), int(v.get("height") or 0),
                                                thumb, sub_files, fields["name"], tmp, msgs)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        log.info("자동 올리기(폴더): %s -> %s", key, rule["channel_title"] or rule["channel"])
        self.subs_label = "화면에 입힘" if burn else f"파일 {sent_subs}개" if sent_subs else "없음"
        if not plan:
            notes = ["그대로 올림 (바로 재생되는 mp4)"]
        if copied:
            notes.insert(0, "서버로 복사 후 처리")
        if burn_skip:
            notes.append(burn_skip)
        if len(msgs) > 1:
            notes.append(f"{len(msgs)}개로 나눠 올림 ({max_gb}GB 한도)")
        if sent_subs:
            notes.append(f"자막 파일 {sent_subs}개 첨부")
        if not side and not any_inside:
            notes.append("자막 없음")
        return ", ".join(notes)

    async def _send_pieces(self, client, peer, pieces, name, caption, width, height, thumb, sub_files, title, tmp, msgs):
        """영상 조각들을 차례로 올리고(나눴으면 이름 끝에 _0000, _0001…, 캡션에 (i/n)) 그 뒤에 자막 파일. 올린 메시지 id는 msgs에.
        돌려주는 값: 올린 자막 파일 수"""
        from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo
        stem, ext = os.path.splitext(name)
        stream = ext.lower() in STREAMABLE
        sent_subs = 0
        for i, (path, start, dur) in enumerate(pieces, 1):
            many = len(pieces) > 1
            vname = part_name(stem, i - 1, ext) if many else name
            cap = caption
            if many:   # 첫 줄(제목)은 그대로 두고 그 아래에 (1/2)
                first, _, rest = caption.partition("\n")
                cap = f"{first}\n({i}/{len(pieces)})" + (f"\n{rest}" if rest else "")
            attrs = [DocumentAttributeVideo(duration=int(dur), w=width, h=height, supports_streaming=stream),
                     DocumentAttributeFilename(vname)]
            self.current = {"title": title + (f" ({i}/{len(pieces)})" if many else ""),
                            "phase": "텔레그램에 올리는 중", "done": 0, "total": os.path.getsize(path)}

            def progress(done, total):
                if self.current:
                    self.current.update(done=int(done), total=int(total))
            sent = await client.send_file(peer, path, caption=cap[:1024], thumb=thumb, attributes=attrs,
                                          supports_streaming=stream, force_document=False, progress_callback=progress)
            msgs.append(getattr(sent, "id", None))
            for spath, lang, sext in sub_files:   # 그 조각의 자막 (나눴으면 조각 시간만큼 잘라서)
                out = spath
                if many:
                    out = await self._cut_sub(spath, os.path.join(tmp, f"cut{i}_{os.path.splitext(os.path.basename(spath))[0]}"),
                                              start, dur, sext)
                    if not out:
                        continue
                sname = f"{os.path.splitext(vname)[0]}{'.' + lang if lang else ''}.{'srt' if many and sext == 'smi' else sext}"
                await client.send_file(peer, out, force_document=True, attributes=[DocumentAttributeFilename(sname)])
                sent_subs += 1
        return sent_subs

    async def _probe_subs(self, target, token=None):
        """파일 안의 자막 트랙 [{codec, lang}] (없으면 []). ffprobe가 없거나 못 열면 None"""
        fp = transcode.ffprobe_path()
        if not fp or not target:
            return None
        args = [fp, "-v", "error", "-select_streams", "s", "-show_entries", "stream=index,codec_name:stream_tags=language", "-of", "json"]
        if token:   # Plex에서 바로 (파일 머리만 읽는다)
            args += ["-headers", f"X-Plex-Token: {token}\r\n"]
        try:
            proc = await asyncio.create_subprocess_exec(*args, target, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _err = await asyncio.wait_for(proc.communicate(), 120)
        except (OSError, asyncio.TimeoutError):
            try:
                proc.kill()
            except Exception:
                pass
            return None
        if proc.returncode != 0:
            return None
        try:
            streams = json.loads(out.decode("utf-8", "replace") or "{}").get("streams") or []
        except ValueError:
            return None
        return [{"codec": x.get("codec_name", ""), "lang": (x.get("tags") or {}).get("language", "")} for x in streams]

    async def _bring_local(self, src_path, subs, tmp, title):
        """네트워크 드라이브(rclone mount 등)의 원본과 옆 자막 파일을 이 서버(tmp)로 복사한다.
        돌려주는 값: (복사한 영상 경로, [(자막 경로, 언어, 확장자)]). 자리가 모자라면 RuntimeError"""
        size = os.path.getsize(src_path)
        free = shutil.disk_usage(tmp).free
        if free < size * 2.2 + 512 * 1024 * 1024:   # 원본 + 인코딩한 것 + 나눌 때 여유
            raise RuntimeError(f"서버 임시 폴더에 자리가 모자랍니다 (원본 {size / 1024 ** 3:.1f}GB, 남은 자리 {free / 1024 ** 3:.1f}GB) - "
                               "복사한 뒤 인코딩하려면 원본의 두 배쯤 필요합니다.")
        dest = os.path.join(tmp, "src" + os.path.splitext(src_path)[1])
        self.current = {"title": title, "phase": "서버로 복사하는 중", "done": 0, "total": size}

        def got(n):
            if self.cancelled():   # 복사하는 스레드는 작업 취소로 멈추지 않으므로 직접
                raise RuntimeError("직접 취소")
            if self.current:
                self.current["done"] = n
        await asyncio.to_thread(folder_watch.copy_file, src_path, dest, got)
        if os.path.getsize(dest) != size:
            raise RuntimeError("서버로 복사한 파일의 크기가 원본과 다릅니다 (드라이브 연결 확인).")
        out = []
        for i, (spath, lang, sext) in enumerate(subs):   # 자막 파일도 같이
            sdest = os.path.join(tmp, f"srcsub{i}.{sext}")
            try:
                await asyncio.to_thread(shutil.copyfile, spath, sdest)
                out.append((sdest, lang, sext))
            except OSError as e:
                log.warning("자동 올리기: 자막 파일을 복사하지 못했습니다 (%s): %s", spath, e)
        return dest, out

    async def _ffmpeg(self, *args):
        proc = await asyncio.create_subprocess_exec(transcode.ffmpeg_path(), "-v", "error", "-y", *args,
                                                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _out, err = await proc.communicate()
        return proc.returncode == 0, err.decode("utf-8", "replace")[-300:]

    @staticmethod
    def utf8_sub(sub_path, dest):
        """자막 파일을 UTF-8로 맞춰 dest에 (우리말 자막은 CP949인 경우가 많은데 libass는 UTF-8을 기대)"""
        with open(sub_path, "rb") as f:
            raw = f.read()
        for enc in ("utf-8-sig", "cp949", "utf-16"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", "replace")
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
        return dest

    async def _encode_plan(self, src_path, make, out, duration, title, cwd):
        """make(인코더) -> folder_watch.Plan 대로 인코딩 (손댈 것이 없으면 None). 설정의 인코더(자동이면 되는 하드웨어)로 하고,
        하드웨어 인코더가 실패하면 그 인코더는 빼고 CPU로 한 번 더"""
        st = load_settings()
        enc = await folder_watch.pick_encoder(st["encoder"], st["cpu_preset"])
        plan = make(enc)
        if plan is None:
            return None
        for attempt in (1, 2):
            label = ", ".join(plan[1]) + (f" · {folder_watch.ENCODER_NAMES[plan.encoder]}" if plan.encoder in folder_watch.ENCODER_NAMES else "")
            try:
                await self._encode(src_path, plan[0], out, duration, title, f"인코딩 중 ({label})", cwd, pre=plan.pre)
                return plan
            except RuntimeError as e:
                if attempt == 2 or plan.encoder in ("cpu", "copy"):
                    raise
                log.warning("자동 올리기: %s 인코더 실패 - CPU로 다시 합니다: %s", plan.encoder, e)
                folder_watch._HW_OK[plan.encoder] = False
                plan = make({"name": "cpu", "preset": st["cpu_preset"]})

    async def _encode(self, src_path, args, out, duration, title, phase, cwd, pre=()):
        """ffmpeg (pre) -i src args out (진행률·속도는 ffmpeg -progress로 self.current에)"""
        cmd = [transcode.ffmpeg_path(), "-v", "error", "-y", "-nostats", "-progress", "pipe:1", *pre, "-i", os.path.abspath(src_path),
               *args, os.path.abspath(out)]
        self.current = {"title": title, "phase": phase, "done": 0, "total": int(duration * 1000) or 0}
        # 올리기 인코딩은 뒤 작업 - CPU·디스크를 가장 낮은 우선순위로 (재생용 서버 변환이 먼저)
        ionice = shutil.which("ionice")
        if ionice:
            cmd = [ionice, "-c", "3", *cmd]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd,
                                                    preexec_fn=_lowest_priority)
        errs = []
        pause_task = asyncio.ensure_future(self._pause_while_playing(proc, phase))

        async def read_err():
            async for line in proc.stderr:
                errs.append(line.decode("utf-8", "replace"))
                del errs[:-20]
        err_task = asyncio.ensure_future(read_err())
        try:
            async for line in proc.stdout:   # out_time_ms=12345678 (이름과 달리 마이크로초)
                k, _, v = line.decode("utf-8", "replace").strip().partition("=")
                if k == "out_time_ms" and v.isdigit() and self.current:
                    self.current["done"] = int(v) // 1000
                elif k == "speed" and self.current and v.strip() not in ("", "N/A"):   # "2.35x" - 재생 속도의 몇 배로 인코딩하는지
                    self.current["speed"] = v.strip()
            await proc.wait()
        finally:
            pause_task.cancel()
            if proc.returncode is None:   # 취소됨 (서버 끄기 등) - ffmpeg를 남기지 않는다
                _signal(proc, "SIGCONT")
                proc.kill()
                await proc.wait()
        await err_task
        if proc.returncode != 0 or not os.path.exists(out):
            raise RuntimeError(f"{phase.replace(' 중', '')} 실패: {''.join(errs).strip()[-300:]}")

    async def _pause_while_playing(self, proc, phase):
        """누가 서버 변환으로 재생하는 동안(아이폰·아이패드 Safari는 mkv·HEVC 등을 서버 변환으로 본다) 인코딩 ffmpeg를 잠시 멈춘다.
        CPU를 다 쓰는 인코딩이 돌면 재생용 변환이 첫 조각을 제때 못 만들어 재생이 실패한다"""
        paused = False
        try:
            while proc.returncode is None:
                busy = bool(self.playing())
                if busy != paused and _signal(proc, "SIGSTOP" if busy else "SIGCONT"):
                    paused = busy
                    if self.current:
                        self.current["phase"] = "재생 중이라 잠시 멈춤 (재생이 끝나면 이어서)" if busy else phase
                await asyncio.sleep(PAUSE_CHECK_SEC)
        finally:
            if paused:
                _signal(proc, "SIGCONT")

    async def _split(self, src_path, base, ext, duration, limit, title):
        """시간으로 n조각 [(경로, 시작초, 길이초)]. 한도의 몇 배인지만큼 같은 길이로 나눈다 - 한도의 2배까지는 반씩(50%),
        3배까지는 3분의 1씩. 장면마다 용량이 달라 한 조각이라도 한도를 넘으면 하나 더 잘게 다시"""
        n = max(2, math.ceil(os.path.getsize(src_path) / limit))
        while n <= MAX_PARTS:
            seg = duration / n
            out = []
            for i in range(n):
                path = part_name(base, i, ext)
                self.current = {"title": title, "phase": f"{n}개로 나누는 중 ({i + 1}/{n})", "done": i, "total": n}
                args = ["-ss", f"{i * seg:.3f}", "-i", src_path] + (["-t", f"{seg:.3f}"] if i < n - 1 else []) + \
                       ["-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero"] + \
                       (["-movflags", "+faststart"] if ext.lower() in STREAMABLE else []) + [path]   # 조각도 받기 전에 바로 재생되게
                ok, err = await self._ffmpeg(*args)
                if not ok:
                    raise RuntimeError(f"ffmpeg로 나누지 못했습니다: {err.strip()}")
                out.append((path, i * seg, seg if i < n - 1 else duration - i * seg))
            if all(os.path.getsize(p) <= limit for p, _s, _d in out):
                return out
            for p, _s, _d in out:
                os.remove(p)
            n += 1
        raise RuntimeError(f"{MAX_PARTS}개로 나눠도 한도를 넘습니다.")

    async def _cut_sub(self, src_path, out_base, start, dur, sext):
        """자막을 조각 시간(start~start+dur)만 남기고 0초부터 다시. ASS는 ASS로, 나머지는 SRT로.
        (ffmpeg의 자막 -ss/-t는 자막 파일에서 믿을 수 없어서 시간은 직접 옮긴다). 만든 파일 경로, 실패하면 None"""
        path = src_path
        if sext in ("smi", "vtt"):   # 먼저 SRT로
            path = f"{out_base}.full.srt"
            ok, _err = await self._ffmpeg("-i", src_path, "-c:s", "srt", path)
            if not ok:
                return None
        with open(path, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8-sig", "replace") if not raw.startswith(b"\xff\xfe") else raw.decode("utf-16")
        ext = "ass" if sext == "ass" else "srt"
        out = f"{out_base}.{ext}"
        cut = shift_ass(text, start, dur) if ext == "ass" else shift_srt(text, start, dur)
        if not cut.strip():
            return None
        with open(out, "w", encoding="utf-8") as f:
            f.write(cut)
        return out

    async def _fetch(self, cfg, src, path, dest):
        """Plex의 작은 파일(자막 등)을 받아 dest에. 실패하면 False"""
        from .server import plex_headers
        try:
            async with self.http().get(f"{src['url']}{path}", headers=plex_headers(cfg, src.get("token")),
                                       ssl=None if src.get("verify_ssl", True) else False, timeout=ClientTimeout(total=60)) as r:
                if r.status != 200:
                    return False
                data = await r.read()
        except Exception:
            return False
        if not data:
            return False
        with open(dest, "wb") as f:
            f.write(data)
        return True

    async def _save_meta(self, rule, cfg, src, m, msgs, tmp):
        """올린 메시지들에 Plex 작품 정보를 붙여 둔다 (텔레그램 화면이 TMDB 없이도 포스터·줄거리·회차 정보를 보여 주게)"""
        if not msgs:
            return
        try:
            show = m
            if m.get("type") == "episode" and m.get("grandparentRatingKey"):
                d = await self.plex_json(cfg, src, f"/library/metadata/{m['grandparentRatingKey']}")
                show = ((d.get("MediaContainer") or {}).get("Metadata") or [m])[0]
            art_dir = os.path.join(config_store.DATA_DIR, "telegram_thumbs", rule["tg"], "plex")
            os.makedirs(art_dir, exist_ok=True)

            async def keep(path, width):
                if not path:
                    return ""
                name = hashlib.sha1(f"{src['id']}:{path}:{width}".encode()).hexdigest()[:16] + ".jpg"
                dest = os.path.join(art_dir, name)
                if not os.path.exists(dest):
                    got = await self._art(cfg, src, path, width, tmp)
                    if not got:
                        return ""
                    shutil.move(got, dest)
                return f"/api/tg/{urllib.parse.quote(rule['tg'])}/plex-art/{name}"
            tv = m.get("type") == "episode"
            meta = {"id": None, "source": "plex", "kind": "tv" if tv else "movie",
                    "title": str(show.get("title") or ""), "original": str(show.get("originalTitle") or ""),
                    "year": str(show.get("year") or ""), "overview": str(show.get("summary") or ""),
                    "tagline": str(show.get("tagline") or ""),
                    "genres": [g.get("tag") for g in show.get("Genre") or [] if g.get("tag")],
                    "rating": round(float(show.get("audienceRating") or show.get("rating") or 0), 1) or None,
                    "runtime": round((m.get("duration") or 0) / 60000) or None,
                    "poster": await keep(show.get("thumb"), 500), "backdrop": await keep(show.get("art"), 1280),
                    "directors": [d.get("tag") for d in (m.get("Director") or show.get("Director") or []) if d.get("tag")][:3],
                    "cast": [{"name": r.get("tag", ""), "role": r.get("role", ""),
                              "thumb": r["thumb"] if str(r.get("thumb", "")).startswith("http") else ""}
                             for r in (show.get("Role") or m.get("Role") or [])[:15]],
                    "seasons": [],
                    # 이 영상의 원래 Plex 항목 - Plex에서 재생할 때 텔레그램 쪽을 먼저 틀 수 있게 (telegram_src.find_plex_copy)
                    "plex_src": src["id"], "plex_key": str(m.get("ratingKey") or "")}
            ep = None
            if tv:
                ep = {"season": m.get("parentIndex"), "ep": m.get("index"), "name": str(m.get("title") or ""),
                      "overview": str(m.get("summary") or ""), "still": await keep(m.get("thumb"), 300),
                      "air_date": str(m.get("originallyAvailableAt") or "")}
            telegram_src.plex_meta_put(rule["tg"], int(rule["channel"]), msgs, meta, ep)
        except Exception as e:   # 작품 정보는 덤 - 실패해도 올린 것은 그대로
            log.warning("자동 올리기 작품 정보 저장 실패: %s", e)

    async def _download(self, cfg, src, part_key, path, title, size):
        from .server import plex_headers   # (순환 import를 피해 여기서)
        self.current = {"title": title, "phase": "Plex에서 받는 중", "done": 0, "total": size}
        url = f"{src['url']}{part_key}"
        async with self.http().get(url, params={"download": "1"}, headers=plex_headers(cfg, src.get("token")),
                                   ssl=None if src.get("verify_ssl", True) else False,
                                   timeout=ClientTimeout(total=None, sock_read=120)) as r:
            if r.status >= 400:
                raise RuntimeError(f"Plex에서 파일을 받지 못했습니다 (HTTP {r.status}).")
            got = 0
            with open(path, "wb") as f:
                async for chunk in r.content.iter_chunked(1024 * 1024):
                    await asyncio.to_thread(f.write, chunk)
                    got += len(chunk)
                    self.current["done"] = got
        if size and got != size:
            raise RuntimeError("Plex에서 파일을 끝까지 받지 못했습니다.")

    async def _art(self, cfg, src, path, width, tmp):
        """Plex 그림을 폭 width JPEG로 줄여 받아 임시 파일 경로. 없거나 못 받으면 None"""
        from .server import plex_headers
        if not path:
            return None
        url = f"{src['url']}/photo/:/transcode?" + urllib.parse.urlencode(
            {"width": width, "height": width * 2, "minSize": 1, "upscale": 0, "format": "jpeg", "url": path})
        try:
            async with self.http().get(url, headers=plex_headers(cfg, src.get("token")), timeout=ClientTimeout(total=30),
                                       ssl=None if src.get("verify_ssl", True) else False) as r:
                if r.status != 200:
                    return None
                data = await r.read()
        except Exception:
            return None
        if not data:
            return None
        dest = os.path.join(tmp, f"art_{secrets.token_hex(4)}.jpg")
        with open(dest, "wb") as f:
            f.write(data)
        return dest
