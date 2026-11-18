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

- 텔레그램 계정마다 따로 올린다: 같은 계정의 항목은 차례로, 다른 계정(A·B·C)의 항목은 동시에. 한 계정이 텔레그램 요청 제한
  (FloodWait)에 걸리면 그 계정만 기다리고 다른 계정은 계속 올린다. 인코딩은 서버 부하를 생각해 동시에 encode_slots개까지
  (기본 1 - 다른 계정 것은 앞 인코딩이 끝날 때까지 "인코딩 차례 기다리는 중"). 기록·성공/실패 수는 계정마다.
- 드라마 회차 중복 막기: 작품 이름 + 시즌 + 회차가 같으면(ST/SW 같은 태그, mp4/mkv, 해상도와 상관없이) 이미 올린 것으로 보고
  건너뛴다 - 자동 올리기로 올린 것과 그 채널에 이미 있는 영상(텔레그램 색인) 모두. 건너뛴 까닭은 기록에 남는다.
- 취소·건너뛴 항목 다시 올리기: 최근 7일 / 30일 / 전체를 골라 다시 줄에 넣는다 (그사이 같은 회차가 올라갔으면 빼고 까닭을 알려 줌).
- 폴더 규칙(rclone)은 Plex 정보가 없어서, 올린 뒤 파일 이름으로 TMDB에서 작품을 찾아 텔레그램 화면의 작품 정보 저장소(meta 표,
  telegram_src.meta_put)에 미리 넣는다 - 목록 카드에 포스터가 바로 나온다. 같은 드라마의 회차는 처음 찾은 것을 그대로 쓴다.
  못 찾거나 실패하면 1시간 -> 6시간 -> 하루 -> 3일 뒤에 다시, 그래도 못 찾으면 "포스터 못 찾음" 목록에 남겨 직접 검색해 고른다.
  예전에 올린 것은 [포스터 복구]로 한꺼번에 찾는다.

- 처리 순서 (항목마다): 1) 새 영상 발견 -> 2) 채널 중복 확인(작품 이름·파일 이름·시즌·회차 - 중복이면 "건너뜀: 중복"으로 기록)
  -> 3) 제외 정규식 확인(설정 exclude - 맞으면 "건너뜀: 제외"로 기록) -> 4) 소프트 자막(파일 안 자막)이 있으면 바로 올림
  -> 5) 자막 파일이 있어야 하면 정한 시간(분)만큼 기다렸다가 -> 6) 놀고 있는 텔레그램 계정이 가져가 올린다.
- 계정 여럿 (규칙의 tg + tg_alt): 놀고 있는 계정이 진행 목록의 앞에서부터 가져간다. 한 계정에 문제가 생기면(로그아웃·세션 만료·
  채널 권한 없음·요청 제한·연결 끊김) 그 항목을 다른 계정에 넘기고, 문제가 생긴 계정은 잠시 쉰다. 화면에서 직접 다른 계정을
  고를 수도 있다 (pin). 진행 목록의 순서는 화면에서 바꿀 수 있다 (위로·아래로·맨 앞).
- 기록마다 걸린 시간(elapsed)과 단계별 시간(times: 받기·인코딩·나누기·올리기·대기)을 남긴다.

설정은 config.json의 "plex_to_tg": {interval, max_gb, encode_slots, exclude, rules: [{id, enabled, plex, section, section_title, tg, tg_alt,
channel, channel_title, caption, wait_subs, wait_min, burn_subs, kind, folder, height}]}
(예전 wait_days / wait_hours는 읽을 때 wait_min(분)으로 바꾼다)
"""
import asyncio
import contextvars
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
import unicodedata
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
KEEP_HISTORY = 50      # 계정마다 이만큼 (기록은 계정별로 나눠 본다)
KEEP_SKIPPED = 2000    # 규칙마다 취소·건너뛴 항목 기억 (다시 올리기용)
POSTER_RETRY = (3600, 6 * 3600, 24 * 3600, 3 * 24 * 3600)   # TMDB 포스터를 못 찾았을 때 다시 찾기까지
AUTO_TMDB_SEC = 10 * 60     # 텔레그램 채널의 작품 정보(TMDB)가 없는 영상을 이만큼마다 찾아 둔다 (목록 카드 포스터)
AUTO_TMDB_MAX = 150         # 한 번에 찾는 작품 수
AUTO_TMDB_MISS = 86400      # 못 찾은 작품은 하루 뒤에 다시 ([목록 새로 고침]은 바로 다시)
SCAN_SIZE = 50
DEFAULT_CAPTION = "{name}\n\n{summary}"
DEFAULT_WAIT_MIN = 720   # 새 규칙의 자막 기다리기 기본값(분) (자막을 영상에 입힌 하드섭은 자동으로 알 수 없어서 끝없이 기다리지 않게)
DEFAULT_EXCLUDE = ["S101", "S201"]   # 파일 이름·제목에 이 정규식이 맞으면 올리지 않는다 (설정에서 바꿈)
MAX_EXCLUDE = 30
MAX_ENCODE_SLOTS = 4
ACCOUNT_REST = 30 * 60   # 로그인·세션 문제가 생긴 계정을 쉬게 하는 시간 (그동안 다른 계정이 올림)
STREAMABLE = {".mp4", ".m4v", ".mov"}   # 텔레그램 앱에서 받기 전에 바로 재생되는 형식
SUB_EXT = {"srt": "srt", "subrip": "srt", "ass": "ass", "ssa": "ass", "smi": "smi", "sami": "smi", "vtt": "vtt", "webvtt": "vtt"}
MAX_PARTS = 10
PART_LIMIT = 1000 * 1024 * 1024   # 텔레그램 한도 1GB 단위 (2GB = 2000MiB = 4000조각 x 512KB) - max_gb를 곱해 쓴다


def file_sig(key, size):
    """폴더 규칙의 같은 파일 표시: 파일 이름(폴더 빼고, 대소문자 무시) + 크기 - 다른 폴더로 옮겨도 같다"""
    return f"{os.path.basename(str(key)).casefold()}|{int(size or 0)}"


def part_name(stem, index, ext):
    """나눠 올리는 조각의 이름: "Hope.2026.1080p-KyoGo" 0 ".mp4" -> "Hope.2026.1080p-KyoGo_0000.mp4" (0부터, 4자리)"""
    return f"{stem}_{index:04d}{ext}"


_KO_FONT = None
PAUSE_CHECK_SEC = 2   # 재생 중인지 이만큼마다 보고 인코딩을 멈추거나 잇는다


def _lowest_priority():
    """(ffmpeg 자식 프로세스에서) CPU 우선순위를 가장 낮게 (리눅스·맥)"""
    try:
        os.nice(19)
    except (OSError, AttributeError):
        pass


def low_priority_kwargs():
    """낮은 우선순위로 자식 프로세스를 띄우는 인자 - 윈도우는 preexec_fn을 못 써서 우선순위 클래스로"""
    if os.name == "nt":
        import subprocess
        return {"creationflags": getattr(subprocess, "IDLE_PRIORITY_CLASS", 0x40)}
    return {"preexec_fn": _lowest_priority}


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
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [(os.path.join(folder, n), sub_lang(lang) if lang else "", SUB_EXT.get(sext, sext))
            for n, lang, sext in folder_watch.match_subs(base, names)]


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
                # 자막을 몇 분까지 기다릴지 (0이면 계속). 예전 설정의 시간(wait_hours)·일(wait_days)은 분으로 바꿔 읽는다
                "wait_min": num(_wait_min(r), 0, 525600, DEFAULT_WAIT_MIN),
                # 이 규칙을 같이 올릴 수 있는 다른 텔레그램 계정 (그 채널에 글을 쓸 수 있어야 함) - 놀고 있는 계정이 가져가고,
                # 한 계정에 문제가 생기면 다른 계정이 이어 올린다
                "tg_alt": [],
                "burn_subs": bool(r.get("burn_subs", True)),
                # 폴더 규칙 (rclone 드라이브 등): kind "folder", folder = 절대 경로 또는 rclone "원격:경로"
                "kind": "folder" if r.get("kind") == "folder" else "plex",
                "folder": str(r.get("folder") or "").strip()[:500],
                # height보다 높은 영상은 용량과 상관없이 height로 줄여 올린다 (0이면 줄이지 않음) - Plex·폴더 규칙 모두
                "height": num(r.get("height"), 0, 2160, 720)}
        if rule["height"] not in (0, 480, 720, 1080):
            rule["height"] = 720
        alt = r.get("tg_alt") if isinstance(r.get("tg_alt"), list) else []
        rule["tg_alt"] = list(dict.fromkeys(str(a)[:16] for a in alt if a and str(a) != rule["tg"]))[:10]
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
    # 인코딩을 동시에 몇 개까지 (계정이 여럿이면 올리기는 동시에 해도 인코딩은 CPU를 많이 써서 이만큼만) - 기본 1
    slots = num(raw.get("encode_slots"), 1, MAX_ENCODE_SLOTS, 1)
    return {"interval": num(raw.get("interval"), 2, 1440, 10), "max_gb": max_gb, "max_gb_v": 1, "encoder": encoder,
            "cpu_preset": preset, "hours": hours, "encode_slots": slots, "exclude": clean_exclude(raw.get("exclude")), "rules": rules}


def _wait_min(r):
    """자막 기다리는 시간(분) - 새 설정 wait_min, 예전 wait_hours(시간)·wait_days(일)"""
    try:
        if "wait_min" in r:
            return int(r["wait_min"])
        if "wait_hours" in r:
            return int(r["wait_hours"]) * 60
        if "wait_days" in r:
            return int(r.get("wait_days") or 0) * 1440
    except (TypeError, ValueError):
        pass
    return DEFAULT_WAIT_MIN


def clean_exclude(raw):
    """제외 정규식 목록 (설정에 없으면 기본값 S101·S201). 줄바꿈으로 나눈 글도 받는다. 틀린 정규식이면 ValueError"""
    if raw is None:
        return list(DEFAULT_EXCLUDE)
    if isinstance(raw, str):
        raw = raw.splitlines()
    out = []
    for x in raw if isinstance(raw, list) else []:
        x = str(x or "").strip()[:200]
        if not x or x in out:
            continue
        try:
            re.compile(x)
        except re.error as e:
            raise ValueError(f"제외 정규식이 틀렸습니다: {x} ({e})")
        out.append(x)
    if len(out) > MAX_EXCLUDE:
        raise ValueError(f"제외 정규식은 {MAX_EXCLUDE}개까지 넣을 수 있습니다.")
    return out


def excluded_by(patterns, *texts):
    """제외 정규식 중 파일 이름·제목·경로에 맞는 첫 정규식 (없으면 "") - 예: "(?i)(^|[\\/])featurettes[\\/]"는 Featurettes 폴더 아래"""
    for pat in patterns or []:
        try:
            rx = re.compile(pat)
        except re.error:
            continue
        if any(t and rx.search(str(t)) for t in texts):
            return pat
    return ""


def phase_kind(phase):
    """진행 단계 문구 -> 시간을 모을 칸 (받기·인코딩·나누기·올리기·대기·준비)"""
    p = str(phase or "")
    if "요청 제한" in p or "차례 기다리는" in p or "멈춤" in p:
        return "대기"
    if "받는" in p or "복사" in p:
        return "받기"
    if "인코딩" in p:
        return "인코딩"
    if "나누는" in p:
        return "나누기"
    if "올리는" in p:
        return "올리기"
    return "준비"


# 계정 탓인 오류 (텔레그램 예외 이름) - 다른 계정에 넘긴다. 앞의 것은 계정 전체(잠시 쉼), 뒤의 것은 그 채널만
_ACCOUNT_DOWN = {"AuthKeyUnregisteredError", "AuthKeyDuplicatedError", "AuthKeyInvalidError", "SessionRevokedError",
                 "SessionExpiredError", "SessionPasswordNeededError", "UserDeactivatedError", "UserDeactivatedBanError",
                 "PhoneNumberBannedError", "TgError", "ConnectionError", "ConnectionResetError", "ConnectionRefusedError"}
_ACCOUNT_CHANNEL = {"ChatWriteForbiddenError", "ChannelPrivateError", "ChatAdminRequiredError", "UserBannedInChannelError",
                    "ChannelInvalidError", "ChannelLost", "ChatForbiddenError", "PeerIdInvalidError"}


def account_problem(e):
    """(계정을 쉬게 할 초, 까닭) - 계정 탓이 아니면 None. 요청 제한(FloodWait)은 그 초만큼"""
    name = type(e).__name__
    if name in ("FloodWaitError", "FloodPremiumWaitError", "SlowModeWaitError"):
        sec = int(getattr(e, "seconds", 60) or 60) + 1
        return sec, f"텔레그램 요청 제한 {sec}초"
    if name == "PeerFloodError":
        return 3600, "텔레그램 요청 제한 (PeerFlood)"
    if name in _ACCOUNT_DOWN:
        return (300 if "Connection" in name else ACCOUNT_REST), f"계정 문제 ({str(e) or name})"[:200]
    if name in _ACCOUNT_CHANNEL:
        return 0, f"이 계정은 채널에 올릴 수 없음 ({str(e) or name})"[:200]
    return None


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


def _queue_path():
    return os.path.join(config_store.DATA_DIR, "plex_to_tg_queue.json")


QUEUE_FIELDS = ("rule", "key", "title", "sig", "test", "force", "again", "pin", "avoid", "added")


def load_queue():
    """서버를 다시 켜기 전의 올릴 차례 목록 (지금 있는 규칙의 것만) - 예전엔 메모리에만 두어 다시 켜면 목록이 사라졌다
    (Plex 규칙은 최근 SCAN_SIZE개만 다시 보므로 그보다 뒤에 있던 것은 다시 들어오지도 않았다)"""
    try:
        with open(_queue_path(), "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    try:
        rules = {r["id"] for r in load_settings()["rules"]}
    except Exception:  # noqa: BLE001 - 설정을 못 읽으면 걸러내지 않는다 (run_queue가 없는 규칙은 건너뜀)
        rules = None
    out, seen = [], set()
    for j in raw if isinstance(raw, list) else []:
        if not isinstance(j, dict) or not j.get("rule") or not j.get("key"):
            continue
        k = (str(j["rule"]), str(j["key"]))
        if k in seen or (rules is not None and k[0] not in rules):
            continue
        seen.add(k)
        out.append({f: j[f] for f in QUEUE_FIELDS if f in j})
    return out


def save_queue(queue):
    with _lock:
        os.makedirs(config_store.DATA_DIR, exist_ok=True)
        tmp = _queue_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([{k: j[k] for k in QUEUE_FIELDS if k in j} for j in queue], f, ensure_ascii=False)
        os.replace(tmp, _queue_path())


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


def _name_key(text):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", str(text or "")).casefold())


def episode_key(fields):
    """드라마 회차 표시 (작품 이름 키, 시즌, 회차) - 회차가 아니면 None.
    텔레그램 색인과 같은 방법(파일 이름·캡션에서 읽음)으로 - ST/SW 태그, mp4/mkv, 해상도는 보지 않는다"""
    finder = getattr(telegram_src, "_episode_info", None)
    if finder:
        info = finder(fields.get("name") or "", fields.get("filename") or "")
        if info:
            return info
    if fields.get("show") and fields.get("episode") not in ("", None):
        try:
            return _name_key(fields["show"]), int(fields.get("season") or 1) % 100, int(fields["episode"])
        except (TypeError, ValueError):
            return None
    return None


def ep_label(epk):
    return f"{epk[0]}|{epk[1]}|{epk[2]}" if epk else ""


def trim_history(hist):
    """기록은 계정마다 KEEP_HISTORY개까지 (새것이 앞)"""
    seen, out = {}, []
    for h in hist:
        k = h.get("tg", "")
        seen[k] = seen.get(k, 0) + 1
        if seen[k] <= KEEP_HISTORY:
            out.append(h)
    return out


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
class Lane:
    """텔레그램 계정 하나의 올리기 줄 - 같은 계정은 차례로, 다른 계정은 저마다의 줄로 동시에"""

    def __init__(self, tg):
        self.tg = tg
        self.current = None       # {title, phase, done, total}
        self.current_job = None   # {rule, key}
        self.job = None           # 지금 올리는 줄의 항목
        self.job_task = None      # 지금 올리는 작업 (취소용)
        self.cur_sig = ""         # 지금 올리는 파일의 이름+크기
        self.cur_ep = None        # 지금 올리는 회차 (작품 키, 시즌, 회차)
        self.cancel_req = None    # 직접 취소한 (규칙, 키)
        self.subs_label = ""
        self.flood_until = 0      # 텔레그램 요청 제한으로 이때까지 쉼
        self.started = 0          # 지금 항목을 시작한 때
        self.times = {}           # 지금 항목의 단계별 걸린 초 {받기, 인코딩, 나누기, 올리기, 대기, 준비}
        self.phase_kind = ""      # 지금 단계 칸과 그 시작 시각
        self.phase_at = 0
        self.last_done = None     # 마지막으로 끝낸 항목 {title, ok, at, elapsed}

    def begin(self):
        self.started, self.times, self.phase_kind, self.phase_at = time.time(), {}, "", 0

    def mark(self, phase):
        """단계가 바뀌면 앞 단계의 시간을 모은다 (phase None = 끝)"""
        now = time.time()
        kind = phase_kind(phase) if phase is not None else ""
        if kind == self.phase_kind:
            return
        if self.phase_kind and self.phase_at:
            self.times[self.phase_kind] = self.times.get(self.phase_kind, 0) + (now - self.phase_at)
        self.phase_kind, self.phase_at = kind, now

    def summary(self):
        """(걸린 초, {단계: 초}) - 1초 밑은 뺀다"""
        self.mark(None)
        return (int(time.time() - self.started) if self.started else 0,
                {k: int(v) for k, v in self.times.items() if v >= 1})


_LANE = contextvars.ContextVar("plex_to_tg_lane", default=None)


def _lane_attr(name):
    """지금 돌고 있는 계정 줄(_LANE)의 값 - 줄 밖(시험·화면 요청)에서는 기본 줄"""
    def get(self):
        return getattr(_LANE.get() or self._main, name)

    def put(self, value):
        setattr(_LANE.get() or self._main, name, value)
    return property(get, put)


def _current_attr():
    """self.current - 넣을 때 그 줄의 단계 시간도 잰다"""
    def get(self):
        return (_LANE.get() or self._main).current

    def put(self, value):
        lane = _LANE.get() or self._main
        lane.current = value
        if value:
            lane.mark(value.get("phase"))
    return property(get, put)


class Mirror:
    current = _current_attr()
    current_job = _lane_attr("current_job")
    subs_label = _lane_attr("subs_label")
    _job = _lane_attr("job")
    _job_task = _lane_attr("job_task")
    _cur_sig = _lane_attr("cur_sig")
    _cur_ep = _lane_attr("cur_ep")
    _cancel_req = _lane_attr("cancel_req")

    def __init__(self, plex_json, plex_src, tg_for, http, playing=None):
        """plex_json(src, path, params) -> dict, plex_src(id) -> (cfg, src), tg_for(id) -> TelegramSource, http() -> ClientSession,
        playing() -> 지금 누가 서버 변환(재생)으로 보고 있나 - 그동안은 올리기 인코딩을 멈춰 재생이 끊기지 않게"""
        self.plex_json, self.plex_src, self.tg_for, self.http = plex_json, plex_src, tg_for, http
        self.playing = playing or (lambda: False)
        self._main = Lane("")    # 계정 줄 밖에서 부를 때 (시험·화면 요청)
        self.lanes = {}          # 텔레그램 소스 id -> Lane (지금 도는 줄)
        self._lane_tasks = {}    # 텔레그램 소스 id -> asyncio.Task
        self._taken = set()      # 줄이 잡은 항목 id(job) - 다른 줄이 다시 잡지 않게
        self._queue_event = asyncio.Event()
        self._encoding = 0       # 지금 돌고 있는 인코딩 수 (encode_slots까지)
        self._encode_free = asyncio.Event()
        self._runner = None      # run_queue 작업 (loop가 뒤에서 돌린다)
        self.poster_job = None   # 포스터 복구 진행 {"done", "total", "found", "missing", "running", "error"}
        self._poster_task = None
        self.queue = load_queue()   # [{rule, key, title, sig?, test?, force?, again?}] - 파일에도 적어 서버를 다시 켜도 이어 간다
        self.last_check = 0
        self.error = ""
        self.off_hours = ""      # 올리기 시간대가 아니라 쉬는 중이면 그 시간대 ("1-7")
        self._wake = asyncio.Event()
        self._shot_task = None
        self._gb_cache = {}      # 텔레그램 소스 id -> (시각, 파일 한도 GB) - 프리미엄 계정인지
        self._head_info = {}     # (rclone 원격, 파일) -> 앞부분 ffprobe 결과 (자막 트랙 확인용)
        self._shot_fail = {}     # (규칙, 항목) -> (시각, 오류) - 장면 뽑기에 실패한 것 (SHOT_RETRY_SEC 뒤에 다시)
        self._down = {}          # 텔레그램 소스 id -> (이때까지 쉼, 까닭) - 문제가 생긴 계정 (그동안 다른 계정이 올림)
        self._auto_tmdb_at = 0   # 마지막으로 채널들의 작품 정보를 찾아 둔 때
        self._tmdb_done = {}     # (소스, 채널) -> 다 찾아 본 때의 채널 모양 (영상 수, 마지막 메시지) - 그대로면 다시 훑지 않음

    def cancelled(self):
        """[스레드에서도] 지금 작업을 취소했는지 - 서버로 복사하는 중이면 복사를 멈춘다"""
        return bool(self._cancel_req and self.current_job and
                    self._cancel_req == (self.current_job.get("rule"), self.current_job.get("key")))

    def wake(self):
        self._wake.set()

    def _queue_changed(self):
        now = int(time.time())
        for j in self.queue:   # 줄에 들어온 때 (화면에서 기다린 시간을 보여 준다)
            j.setdefault("added", now)
        self._queue_event.set()   # run_queue가 새 계정의 줄을 바로 띄우게
        try:
            save_queue(self.queue)
        except OSError as e:   # 못 적어도 올리기는 계속 (다시 켜면 목록만 다시 찾는다)
            log.warning("자동 올리기 목록을 저장하지 못했습니다: %s", e)

    def _running(self):
        """지금 올리는 중인 줄들 [(Lane)]"""
        return [ln for ln in self.lanes.values() if ln.current_job]

    def _set_phase(self, text):
        """지금 단계 문구만 바꾼다 (단계 시간도 같이)"""
        if self.current:
            self.current["phase"] = text
            (_LANE.get() or self._main).mark(text)

    # ----- 계정 고르기 (놀고 있는 계정 / 문제 생긴 계정 넘기기) -----
    def _is_down(self, tg):
        d = self._down.get(tg)
        if d and d[0] > time.time():
            return d[1]
        self._down.pop(tg, None)
        return ""

    @staticmethod
    def _accounts_for(rule, job):
        """이 항목을 올릴 수 있는 계정들 (앞이 먼저). 직접 고른 계정(pin)이 있으면 그것만, 문제가 있었던 계정(avoid)은 빼되
        모두 빠지면 처음 목록 그대로"""
        if not rule:
            return [""]
        if job.get("pin"):
            return [job["pin"]]
        allx = [rule["tg"], *[a for a in rule.get("tg_alt") or [] if a != rule["tg"]]]
        avoid = set(job.get("avoid") or [])
        return [a for a in allx if a not in avoid] or allx

    def _lane_of(self, job):
        """지금 그 항목을 잡고 있는 계정 (안 잡혔으면 "")"""
        return next((ln.tg for ln in self.lanes.values() if ln.job is job and ln.current_job), "")

    def status(self):
        st = load_state()
        settings = load_settings()
        rules = {r["id"]: r for r in settings["rules"]}
        now = time.time()
        lanes = []
        for ln in self.lanes.values():
            if not (ln.current or ln.flood_until > now):
                continue
            cur = None
            if ln.current:
                cur = {**ln.current, **(ln.current_job or {}), "started": int(ln.started), "elapsed": int(now - ln.started) if ln.started else 0,
                       "phase_since": int(ln.phase_at), "times": {k: int(v) for k, v in ln.times.items() if v >= 1}}
            lanes.append({"tg": ln.tg, "current": cur, "flood_until": int(ln.flood_until) if ln.flood_until > now else 0})
        first = next((x["current"] for x in lanes if x["current"]), None)
        # 계정마다: 지금 하는 것, 기다리는 수(그 계정이 가져갈 수 있는 것), 성공·실패 수, 쉬는 중이면 까닭
        accounts = {}
        queue = []
        for n, j in enumerate(self.queue):
            rule = rules.get(j["rule"])
            cands = self._accounts_for(rule, j)
            running = self._lane_of(j) if id(j) in self._taken else ""
            if not running:   # 올리는 중인 것은 계정의 current로 보인다
                for a in cands:
                    accounts.setdefault(a, {"queued": 0})["queued"] += 1
            queue.append({"title": j["title"], "rule": j["rule"], "key": j["key"], "tg": running or cands[0], "tgs": cands,
                          "pin": j.get("pin", ""), "avoid": j.get("avoid") or [], "running": bool(running), "pos": n,
                          "added": j.get("added", 0)})
        for tg, a in (st.get("accounts") or {}).items():
            accounts.setdefault(tg, {"queued": 0}).update(ok=a.get("ok", 0), fail=a.get("fail", 0))
        for r in settings["rules"]:
            for tg in [r["tg"], *r.get("tg_alt", [])]:
                accounts.setdefault(tg, {"queued": 0})
        for tg, a in accounts.items():
            ln = self.lanes.get(tg)
            a["current"] = ({"title": ln.current.get("title", ""), "phase": ln.current.get("phase", ""),
                             "elapsed": int(now - ln.started) if ln.started else 0} if ln and ln.current else None)
            a["down"] = self._is_down(tg)
            a["down_until"] = int(self._down[tg][0]) if a["down"] else 0
            a["last"] = ln.last_done if ln else None
        return {"queue": queue,
                "current": first, "lanes": lanes, "accounts": accounts, "encode_slots": settings["encode_slots"],
                "encoding": self._encoding,
                "poster": self.poster_job, "poster_missing": self._poster_missing(st),
                "skipped": sum(len(r.get("skipped") or {}) + sum(1 for k, n in (r.get("fails") or {}).items()
                                                                  if n >= GIVE_UP and k not in (r.get("skipped") or {}))
                               for r in st["rules"].values()),
                "last_check": self.last_check, "error": self.error, "off_hours": self.off_hours, "history": trim_history(st["history"]),
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
        try:
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
                    # 올리기는 뒤에서 (계정마다 줄) - 올리는 동안에도 새 항목 확인은 정해진 간격대로 한다
                    if not self.off_hours and (self._runner is None or self._runner.done()):
                        self._runner = asyncio.ensure_future(self.run_queue(st))
                    self.prepare_shots()   # 자막 기다리는 항목의 장면을 뒤에서 미리
                try:
                    await self._poster_retries()
                    await self.auto_tmdb()
                except Exception as e:  # noqa: BLE001 - 포스터는 덤
                    log.info("자동 올리기 포스터 다시 찾기 실패: %s", e)
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), st["interval"] * 60)
                except asyncio.TimeoutError:
                    pass
        finally:   # 서버를 끄면 돌던 줄도 멈춘다 (반쯤 올린 것은 다음에 켤 때 지우고 다시)
            for t in [self._runner, *self._lane_tasks.values(), self._poster_task]:
                if t and not t.done():
                    t.cancel()

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
        errors = []
        # 1) 바깥(폴더·Plex)에서 목록부터 받는다 - 받는 동안 올리는 줄이 상태 파일을 고칠 수 있어서, 상태는 다 받은 뒤에 한 번에 읽고 쓴다
        got = {}
        for rule in st["rules"]:
            if not rule["enabled"]:
                continue
            try:
                got[rule["id"]] = await folder_watch.scan(rule["folder"]) if rule["kind"] == "folder" else await self._recent(rule)
            except folder_watch.FolderError as e:   # 이 폴더만 못 봄 - 다른 규칙은 계속
                errors.append(f"{rule['section_title']}: {e}")
            except Exception as e:
                if rule["kind"] == "folder":
                    errors.append(f"{rule['section_title']}: {e}")
                    continue
                raise
        state = load_state()
        queued = {(j["rule"], j["key"]) for j in self.queue}
        for rule in st["rules"]:
            if rule["id"] not in got:
                continue
            rs = state["rules"].setdefault(rule["id"], {"done": [], "fails": {}})
            if rule["kind"] == "folder":
                files = got[rule["id"]]
                if "baseline" not in rs:      # 처음: 지금 있는 파일은 올리지 않는다 (이름을 기억해 둠)
                    if not files:
                        # 비어 보이면 기준을 잡지 않는다 - 마운트가 아직 안 붙었거나 목록을 못 받은 것일 수 있고, 빈 기준을 잡으면
                        # 다음 확인 때 폴더 전체가 '새 파일'로 줄에 들어간다. 정말 빈 폴더면 첫 파일이 생길 때 기준을 잡는다
                        errors.append(f"{rule['section_title']}: 폴더가 비어 보여 아직 기준을 잡지 않았습니다 (마운트·경로 확인)")
                        continue
                    rs["baseline"] = int(time.time())
                    rs["known"] = [folder_watch.key_hash(f["key"]) for f in files]
                    rs["sigs"] = [file_sig(f["key"], f["size"]) for f in files]
                    rs["sizes"] = {}
                    continue
            else:
                items = got[rule["id"]]
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
        self._queue_changed()
        if errors:
            raise RuntimeError(" / ".join(errors))

    def _queue_files(self, rule, rs, files, done, queued):
        """폴더 규칙: 처음 보는 파일 중 다 올라온 것(크기가 그대로이거나 수정한 지 STABLE_SEC 지남)을 줄에"""
        known = set(rs.get("known") or [])
        sigs = set(rs.get("sigs") or [])
        sizes, now, fresh = rs.setdefault("sizes", {}), time.time(), {}
        for f in sorted(files, key=lambda x: x["mtime"]):
            key = f["key"]
            if folder_watch.key_hash(key) in known or key in done or (rule["id"], key) in queued or key in rs.get("fails", {}):
                continue
            sig = file_sig(key, f["size"])
            if sig in sigs:   # 이미 올린(또는 처음부터 있던) 파일을 다른 폴더로 옮겼거나 경로만 바뀐 것 - 다시 올리지 않는다
                rs.setdefault("known", []).append(folder_watch.key_hash(key))
                continue
            if any((j["rule"] == rule["id"] and j.get("sig") == sig) for j in self.queue):   # 같은 파일이 두 경로에 - 하나만
                continue
            if sizes.get(key) == f["size"] or now - f["mtime"] >= folder_watch.STABLE_SEC:
                self.queue.append({"rule": rule["id"], "key": key, "title": folder_watch.file_fields(key)["name"], "sig": sig})
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
            self._queue_changed()
            self.wake()
            return title
        items = await self._recent(rule)
        if not items:
            raise ValueError("이 라이브러리에 동영상이 없습니다.")
        m = items[0]
        self.queue.append({"rule": rule["id"], "key": str(m["ratingKey"]), "title": item_fields(m)["name"], "test": True})
        self._queue_changed()
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
        self._queue_changed()
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
        self._remember_skip(rs, key, w.get("title", key), "건너뜀: 직접 뺌")
        state["history"] = trim_history([{"title": w.get("title", key), "ok": False, "note": "건너뜀: 직접 뺌", "at": int(time.time()),
                                          "key": key, "subs": "", "channel": "", "tg": self._tg_of(rule_id)}, *state["history"]])
        save_state(state)
        # 지금 올리는 중인 것은 건드리지 않는다
        self.queue = [j for j in self.queue if id(j) in self._taken or not (j["rule"] == rule_id and j["key"] == key)]
        self._queue_changed()
        return w.get("title", key)

    def _tg_of(self, rule_id):
        return next((r["tg"] for r in load_settings()["rules"] if r["id"] == rule_id), "")

    @staticmethod
    def _remember_skip(rs, key, title, note, sig=""):
        """취소·건너뛴 항목을 기억해 둔다 (나중에 [다시 올리기]로 고를 수 있게) - 규칙마다 KEEP_SKIPPED개까지"""
        sk = rs.setdefault("skipped", {})
        sk.pop(key, None)
        sk[key] = {"title": title, "at": int(time.time()), "note": note, **({"sig": sig} if sig else {})}
        while len(sk) > KEEP_SKIPPED:
            sk.pop(next(iter(sk)))

    def _forget(self, state, rule_id, key, title, note, sig=""):
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
        self._remember_skip(rs, key, title, note, sig)
        state["history"] = trim_history([{"title": title, "ok": False, "note": note, "at": int(time.time()), "key": key,
                                          "subs": "", "channel": "", "tg": self._tg_of(rule_id)}, *state["history"]])

    def cancel_item(self, rule_id, key):
        """올릴 차례·자막 기다림·다시 시도 대기·지금 올리는 중인 항목 하나를 올리지 않는다 (다시 들어오지 않게)"""
        state = load_state()
        rs = state["rules"].get(rule_id) or {}
        job = next((j for j in self.queue if j["rule"] == rule_id and j["key"] == key), None)
        title = (job or {}).get("title") or ((rs.get("waiting") or {}).get(key) or {}).get("title") \
            or ((rs.get("retry") or {}).get(key) or {}).get("title")
        lane = next((ln for ln in self._running() if ln.current_job.get("rule") == rule_id and ln.current_job.get("key") == key), None)
        if not title and not lane:
            raise ValueError("진행 목록에 없는 항목입니다 (이미 끝났거나 빠졌을 수 있음).")
        title = title or key
        if lane:   # 지금 올리는 중: 그 계정 줄의 작업을 멈춘다 (받기·복사·인코딩·올리기 어디에서든) - 기록은 그 줄이 남긴다
            lane.cancel_req = (rule_id, key)
            if lane.job_task and not lane.job_task.done():
                lane.job_task.cancel()
            return title
        self._forget(state, rule_id, key, title, "건너뜀: 직접 취소", (job or {}).get("sig", ""))
        save_state(state)
        self.queue = [j for j in self.queue if not (j["rule"] == rule_id and j["key"] == key)]
        self._queue_changed()
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
        running = {(ln.current_job.get("rule"), ln.current_job.get("key")) for ln in self._running()}
        sigs = {(j["rule"], j["key"]): j.get("sig", "") for j in self.queue}
        n = 0
        for (rid, k), title in items.items():
            if (rid, k) in running:
                continue
            self._forget(state, rid, k, title, "건너뜀: 직접 취소 (전체 취소)", sigs.get((rid, k), ""))
            n += 1
        save_state(state)
        keep = [j for j in self.queue if (j["rule"], j["key"]) in running]
        self.queue = keep + [j for j in self.queue if j not in keep and rule_id not in (None, j["rule"])]
        self._queue_changed()
        for cur in running:   # 지금 올리는 것들 (계정마다 하나씩일 수 있다)
            if rule_id in (None, cur[0]):
                self.cancel_item(*cur)
                n += 1
        return n

    # ----- 드라마 회차 중복 막기 -----
    def _already_ep(self, rule, fields):
        """같은 작품·시즌·회차를 이 채널에 이미 올렸으면 그 까닭 (아니면 "") - 태그·확장자·해상도는 보지 않는다.
        지금 올리는 회차로 표시해 둔다 (다른 계정 줄이 같은 회차를 동시에 올리지 않게)"""
        epk = episode_key(fields)
        self._cur_ep = epk
        if not epk:
            return ""
        title = ((load_state().get("sent_eps") or {}).get(str(rule["channel"])) or {}).get(ep_label(epk))
        if title:
            return f"같은 회차를 이미 올림 ({title})"
        lookup = getattr(telegram_src, "channel_episode", None)
        if lookup:
            try:
                title = lookup(rule["tg"], int(rule["channel"]), *epk)
            except Exception:  # noqa: BLE001 - 색인을 못 보면 자동 올리기 기록만으로
                title = ""
            if title:
                return f"같은 회차가 이미 채널에 있음 ({title})"
        me = _LANE.get()
        for ln in self.lanes.values():
            if ln is not me and ln.cur_ep == epk and ln.current_job:
                return "같은 회차를 다른 계정이 지금 올리는 중"
        return ""

    def _remember_ep(self, state, rule, title):
        epk = self._cur_ep
        if not epk or not rule:
            return
        ch = state.setdefault("sent_eps", {}).setdefault(str(rule["channel"]), {})
        ch[ep_label(epk)] = title
        while len(ch) > KEEP_DONE:
            ch.pop(next(iter(ch)))

    # ----- 취소·건너뛴 항목 다시 올리기 -----
    def requeue_skipped(self, days=None, rule_id=None):
        """최근 days일(None이면 전체) 동안 취소·건너뛴 항목을 다시 줄에 넣는다. 이미 올린 같은 파일·같은 회차는 빼고 까닭을 알려 준다.
        돌려주는 값: {"queued": [제목], "excluded": [{"title", "reason"}]}"""
        st = load_settings()
        rules = {r["id"]: r for r in st["rules"]}
        state = load_state()
        cutoff = time.time() - days * 86400 if days else 0
        queued_keys = {(j["rule"], j["key"]) for j in self.queue}
        added, excluded = [], []
        for rid, rs in state["rules"].items():
            rule = rules.get(rid)
            if not rule or rule_id not in (None, rid):
                continue
            skipped = dict(rs.get("skipped") or {})
            if not days:   # 전체: 예전(이 기능 전)에 건너뛴 것도 - 제목·시각 기록이 없다
                for k, n in (rs.get("fails") or {}).items():
                    if n >= GIVE_UP and k not in skipped:
                        title = folder_watch.file_fields(k)["name"] if rule["kind"] == "folder" else k
                        skipped[k] = {"title": title, "at": 0, "note": ""}
            sent = ((state.get("sent_eps") or {}).get(str(rule["channel"])) or {})
            for key, x in skipped.items():
                if (x.get("at") or 0) < cutoff or (rid, key) in queued_keys:
                    continue
                title = x.get("title") or key
                reason = ""
                if key in set(rs.get("done") or []):
                    reason = "이미 올림"
                elif x.get("sig") and (state.get("sent_sigs") or {}).get(str(rule["channel"]), {}).get(x["sig"]):
                    reason = "같은 파일을 이미 올림"
                else:
                    fields = folder_watch.file_fields(key) if rule["kind"] == "folder" else {"name": title, "filename": ""}
                    epk = episode_key(fields)
                    if epk and sent.get(ep_label(epk)):
                        reason = f"같은 회차를 이미 올림 ({sent[ep_label(epk)]})"
                    elif epk and getattr(telegram_src, "channel_episode", None):
                        try:
                            got = telegram_src.channel_episode(rule["tg"], int(rule["channel"]), *epk)
                        except Exception:  # noqa: BLE001
                            got = ""
                        if got:
                            reason = f"같은 회차가 이미 채널에 있음 ({got})"
                if reason:
                    excluded.append({"title": title, "reason": reason})
                    continue
                (rs.get("fails") or {}).pop(key, None)
                (rs.get("retry") or {}).pop(key, None)
                (rs.get("skipped") or {}).pop(key, None)
                self.queue.append({"rule": rid, "key": key, "title": title, "again": True, **({"sig": x["sig"]} if x.get("sig") else {})})
                queued_keys.add((rid, key))
                added.append(title)
        save_state(state)
        self._queue_changed()
        self.wake()
        return {"queued": added, "excluded": excluded}

    def _wait_subs(self, rule, key):
        """자막이 없을 때: 기다린 지 wait_min분이 안 됐으면 Waiting (0분이면 생길 때까지 계속)"""
        w = (load_state()["rules"].get(rule["id"], {}).get("waiting") or {}).get(key)
        if not rule["wait_min"] or not w or time.time() - w.get("since", time.time()) < rule["wait_min"] * 60:
            raise Waiting()

    async def _duplicate(self, rule, fields, file_name):
        """채널 중복이면 그 까닭 (아니면 ""): 같은 파일(이름+크기) -> 채널에 같은 파일 이름 -> 같은 작품·시즌·회차"""
        dup = self._already_sent(rule, self._cur_sig)
        if dup:
            return f"이 채널에 이미 올린 파일 ({dup})"
        refresh = getattr(telegram_src, "refresh_channel", None)   # 채널 목록을 먼저 새로 읽는다 (BookOasis Assistant)
        if refresh:
            try:
                await refresh(self.tg_for(rule["tg"]), int(rule["channel"]))
            except Exception as e:  # noqa: BLE001 - 못 읽으면 지금까지 읽은 목록·자동 올리기 기록으로
                log.info("자동 올리기: 채널 목록을 새로 읽지 못했습니다 (%s): %s", rule["channel_title"] or rule["channel"], e)
        same = getattr(telegram_src, "channel_file", None)
        if same and file_name:
            try:
                got = same(rule["tg"], int(rule["channel"]), file_name)
            except Exception:  # noqa: BLE001
                got = ""
            if got:
                return f"같은 파일 이름이 이미 채널에 있음 ({got})"
        return self._already_ep(rule, fields)

    def _already_sent(self, rule, sig):
        """같은 채널에 같은 파일(이름+크기)을 이미 올렸는지 - 규칙이 둘이거나(Plex 규칙과 폴더 규칙이 같은 파일을 봄)
        같은 파일이 다른 항목으로 다시 들어온 경우. 올렸으면 그 기록 문구, 아니면 """""
        if not sig:
            return ""
        got = (load_state().get("sent_sigs") or {}).get(str(rule["channel"])) or {}
        return got.get(sig, "")

    def _remember_sent(self, rule, sig, title):
        if not sig:
            return
        state = load_state()
        ch = state.setdefault("sent_sigs", {}).setdefault(str(rule["channel"]), {})
        ch[sig] = title
        while len(ch) > KEEP_DONE:
            ch.pop(next(iter(ch)))
        save_state(state)

    def _note_sent(self, msg_id):
        """지금 올리는 항목이 채널에 보낸 메시지 id를 상태에 바로 적는다 (sending) - 다 올리기 전에 실패하거나 취소되거나
        서버가 다시 켜지면 그 메시지들을 지우고 처음부터 다시 올린다 (반쪽짜리가 채널에 남아 겹치지 않게)"""
        job = self.current_job or {}
        if not msg_id or not job:
            return
        state = load_state()
        rs = state["rules"].setdefault(job["rule"], {"done": [], "fails": {}})
        rs.setdefault("sending", {}).setdefault(job["key"], []).append(int(msg_id))
        save_state(state)

    async def _drop_partial(self, rule, key):
        """그 항목이 지난번에 반쯤 올리다 만 메시지들을 채널에서 지운다 (없으면 아무것도 안 함). 지운 수"""
        state = load_state()
        ids = ((state["rules"].get(rule["id"]) or {}).get("sending") or {}).get(key) or []
        if not ids:
            return 0
        try:
            tg = self.tg_for(rule["tg"])
            client = await tg.authorized_client()
            peer = await tg.peer(int(rule["channel"]))
            await client.delete_messages(peer, ids)
        except Exception as e:  # noqa: BLE001 - 못 지워도 다음에 다시 (sending은 그대로 둔다)
            log.warning("자동 올리기: 반쯤 올린 메시지 %s개를 지우지 못했습니다 (%s): %s", len(ids), key, e)
            return 0
        state = load_state()
        ((state["rules"].get(rule["id"]) or {}).get("sending") or {}).pop(key, None)
        save_state(state)
        log.info("자동 올리기: 반쯤 올린 메시지 %s개를 지웠습니다 (%s)", len(ids), key)
        return len(ids)

    async def run_queue(self, st):
        """올릴 차례를 텔레그램 계정마다 따로 처리한다 - 같은 계정은 차례로, 다른 계정은 동시에.
        줄이 다 끝날 때까지(또는 올리기 시간대가 끝날 때까지) 돌고, 도는 동안 새 계정의 항목이 들어오면 그 줄도 띄운다."""
        rules = {r["id"]: r for r in st["rules"]}
        try:
            while True:
                if not in_hours(st["hours"]):   # 정한 시간대가 끝나면 남은 것은 다음 시간대에
                    self.off_hours = st["hours"]
                for tg in self._waiting_accounts(rules):
                    t = self._lane_tasks.get(tg)
                    if (t is None or t.done()) and in_hours(st["hours"]):
                        self._lane_tasks[tg] = asyncio.ensure_future(self._run_lane(tg, st))
                running = [t for t in self._lane_tasks.values() if not t.done()]
                if not running:
                    if any(self._is_down(tg) == "" for tg in self._waiting_accounts(rules, include_down=True)) \
                            or not self._waiting_accounts(rules, include_down=True):
                        break
                    # 남은 항목의 계정이 모두 쉬는 중 - 가장 먼저 풀리는 때까지 기다렸다가 다시
                    until = min((d[0] for d in self._down.values()), default=time.time() + 60)
                    self._queue_event.clear()
                    try:
                        await asyncio.wait_for(self._queue_event.wait(), max(5, min(600, until - time.time() + 1)))
                    except asyncio.TimeoutError:
                        pass
                    continue
                self._queue_event.clear()
                ev = asyncio.ensure_future(self._queue_event.wait())
                try:
                    # 쉬던 계정이 풀리면 그 줄도 띄우게 1분마다 다시 본다
                    await asyncio.wait([*running, ev], return_when=asyncio.FIRST_COMPLETED, timeout=60)
                finally:
                    ev.cancel()
                for t in running:   # 줄이 예외로 끝났으면 로그만 (다른 줄은 계속)
                    if t.done() and not t.cancelled() and t.exception():
                        log.warning("자동 올리기 줄 오류: %s", t.exception())
        except asyncio.CancelledError:   # 서버 끄기 - 돌던 줄도 멈춘다
            for t in self._lane_tasks.values():
                if not t.done():
                    t.cancel()
            raise

    def _waiting_accounts(self, rules, include_down=False):
        """아직 아무 줄도 잡지 않은 항목을 가져갈 수 있는 계정들 (규칙이 없어진 항목은 "" - 그 줄이 실패로 정리한다).
        쉬는 중인 계정은 뺀다 (include_down이면 넣음)"""
        out = []
        for j in self.queue:
            if id(j) in self._taken:
                continue
            for a in self._accounts_for(rules.get(j["rule"]), j):
                if a not in out and (include_down or not self._is_down(a)):
                    out.append(a)
        return out

    def _next_job(self, tg, rules):
        """이 계정이 가져갈 다음 항목 - 진행 목록의 앞에서부터 (그 계정이 올릴 수 있는 것 중)"""
        return next((j for j in self.queue if id(j) not in self._taken and tg in self._accounts_for(rules.get(j["rule"]), j)), None)

    async def _run_lane(self, tg, st):
        lane = self.lanes.setdefault(tg, Lane(tg))
        _LANE.set(lane)   # 이 작업(과 그 안에서 만든 작업·스레드)의 self.current 등은 이 계정 줄의 것
        rules = {r["id"]: r for r in st["rules"]}
        try:
            while in_hours(st["hours"]) and not self._is_down(tg):
                job = self._next_job(tg, rules)
                if job is None:
                    break
                self._taken.add(id(job))
                try:
                    await self._run_job(lane, job, rules.get(job["rule"]), st)
                finally:
                    self._taken.discard(id(job))
        finally:
            lane.current = lane.current_job = lane.job = None
            lane.cur_ep = None

    async def _run_job(self, lane, job, rule, st):
        ok, note, waiting = False, "", False
        lane.begin()
        if rule and lane.tg and lane.tg != rule["tg"]:   # 다른 계정이 이어 올림 - 그 계정으로 (채널은 같음)
            rule = {**rule, "tg": lane.tg}
        while True:   # 텔레그램 요청 제한이면 이 계정만 기다렸다가 같은 것을 처음부터 다시
            lane.current_job, lane.subs_label = {"rule": job["rule"], "key": job["key"], "tg": lane.tg}, ""
            lane.job, lane.cur_sig, lane.cur_ep = job, "", None
            cancelled = False
            lane.cancel_req = None
            if rule:
                await self._drop_partial(rule, job["key"])   # 지난번(서버가 다시 켜지기 전 등)에 반쯤 올린 것
            if not rule:
                break
            try:
                lane.job_task = asyncio.ensure_future(self.upload(rule, job["key"], st["max_gb"], test=job.get("test") or job.get("force")))
                note = await lane.job_task
                ok = True
            except asyncio.CancelledError:
                if lane.cancel_req != (job["rule"], job["key"]):   # 직접 취소가 아니면 (서버 끄기 등) 그대로 멈춘다
                    if lane.job_task and not lane.job_task.done():
                        lane.job_task.cancel()
                    raise
                cancelled = True
            except Waiting:
                waiting = True
            except Exception as e:
                problem = account_problem(e)
                if problem and self._hand_over(lane, job, rule, problem):   # 다른 계정이 이어 올린다
                    lane.job_task = None
                    await self._drop_partial(rule, job["key"])
                    lane.current = lane.current_job = None
                    lane.cur_ep = None
                    return
                wait = getattr(e, "seconds", None) if type(e).__name__ == "FloodWaitError" else None
                if wait:   # 텔레그램이 이 계정에 기다리라고 함 - 이 줄만 쉰다 (다른 계정 줄은 그대로 올린다)
                    wait = min(int(wait) + 1, 3600)
                    lane.flood_until = time.time() + wait
                    lane.current = {"title": job["title"], "phase": f"텔레그램 요청 제한 - {wait}초 기다리는 중 (이 계정만)", "done": 0, "total": 0}
                    log.info("자동 올리기: 텔레그램 요청 제한 %s초 (계정 %s) - 이 계정만 기다립니다", wait, lane.tg)
                    lane.job_task = None
                    await asyncio.sleep(wait)
                    lane.flood_until = 0
                    await self._drop_partial(rule, job["key"])
                    continue
                note = str(e) or type(e).__name__
                log.warning("자동 올리기 실패 (%s): %s", job["title"], note)
            break
        lane.job_task = None
        if rule and not ok:   # 실패·취소·자막 기다림: 반쯤 올린 것은 지운다 (다시 올릴 때 겹치지 않게)
            await self._drop_partial(rule, job["key"])
        self.queue = [j for j in self.queue if j is not job]
        self._queue_changed()
        elapsed, times = lane.summary()
        lane.current = lane.current_job = None
        state = load_state()
        tg = (rule or {}).get("tg", "")
        if cancelled:   # 올리던 중에 직접 취소
            self._forget(state, job["rule"], job["key"], job["title"], "건너뜀: 올리던 중 직접 취소", job.get("sig", ""))
            save_state(state)
            lane.cancel_req = None
            lane.cur_ep = None
            return
        rs = state["rules"].setdefault(job["rule"], {"done": [], "fails": {}})
        if waiting:   # 자막이 생길 때까지 (기록에는 남기지 않고 상태의 "자막 기다리는 중"에)
            rs.setdefault("waiting", {}).setdefault(job["key"], {"title": job["title"], "since": int(time.time())})
            save_state(state)
            lane.cur_ep = None
            return
        if (rs.get("waiting") or {}).pop(job["key"], None) is not None:
            drop_shot(job["rule"], job["key"])   # 대기에서 빠졌으면 판단용 장면도
        fails = rs.setdefault("fails", {})
        retry = rs.setdefault("retry", {})
        if ok and note.startswith("건너뜀"):   # 너무 큰 파일 / 이미 올린 파일·회차 - 다시 시도하지 않는다 ([다시 올리기]로 고를 수 있게 기억)
            ok = False
            fails[job["key"]] = GIVE_UP
            retry.pop(job["key"], None)
            self._remember_skip(rs, job["key"], job["title"], note, job.get("sig", ""))
        elif ok:
            (rs.get("sending") or {}).pop(job["key"], None)   # 다 올렸다 - 지울 것 없음
            if lane.cur_sig and rule and not job.get("test"):
                ch = state.setdefault("sent_sigs", {}).setdefault(str(rule["channel"]), {})
                ch[lane.cur_sig] = job["title"]
            if not job.get("test"):
                self._remember_ep(state, rule, job["title"])
            rs["done"] = [*rs.get("done", []), job["key"]][-KEEP_DONE:]
            if (rule or {}).get("kind") == "folder":   # 폴더 규칙은 올린 파일 이름을 따로 끝까지 기억 (done은 최근 것만)
                rs.setdefault("known", []).append(folder_watch.key_hash(job["key"]))
                if job.get("sig"):   # 이름+크기도 (옮겨도 다시 올리지 않게)
                    rs.setdefault("sigs", []).append(job["sig"])
            fails.pop(job["key"], None)
            retry.pop(job["key"], None)
            (rs.get("skipped") or {}).pop(job["key"], None)
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
        acct = state.setdefault("accounts", {}).setdefault(tg, {"ok": 0, "fail": 0})
        acct["ok" if ok else "fail"] = acct.get("ok" if ok else "fail", 0) + 1
        state["history"] = trim_history([{"title": job["title"], "ok": ok, "note": note, "at": int(time.time()), "key": job["key"],
                                          "subs": lane.subs_label if ok else "", "channel": (rule or {}).get("channel_title", ""),
                                          "tg": tg, "rule": job["rule"], "elapsed": elapsed, "times": times,
                                          "started": int(lane.started), "queued_at": job.get("added", 0),
                                          **({"again": True} if job.get("again") else {}),
                                          **({"handover": job["avoid"]} if job.get("avoid") else {})}, *state["history"]])
        save_state(state)
        lane.last_done = {"title": job["title"], "ok": ok, "at": int(time.time()), "elapsed": elapsed}
        lane.cur_ep = None

    def _hand_over(self, lane, job, rule, problem):
        """계정 탓인 오류: 이 계정을 (필요하면) 쉬게 하고 그 항목을 다른 계정에 넘긴다. 넘길 계정이 없으면 False (보통 실패로)"""
        rest, why = problem
        if rest:
            self._down[lane.tg] = (time.time() + rest, why)
        if job.get("pin") == lane.tg:
            job.pop("pin", None)   # 직접 고른 계정이 문제면 규칙의 계정들로
        rules = {r["id"]: r for r in load_settings()["rules"]}
        base = rules.get(job["rule"])
        if not base:
            return False
        avoid = list(dict.fromkeys([*(job.get("avoid") or []), lane.tg]))
        left = [a for a in [base["tg"], *base.get("tg_alt", [])] if a not in avoid]
        if not left:
            job.pop("avoid", None)   # 다 써 봤다 - 다음에는 처음부터 다시 고른다
            return False
        job["avoid"] = avoid
        self._queue_changed()   # 자리는 그대로 - 다른 계정 줄이 가져간다
        log.info("자동 올리기: %s - 계정 %s에서 %s → 다른 계정(%s)에 넘김", job["title"], lane.tg, why, ", ".join(left))
        state = load_state()
        state["history"] = trim_history([{"title": job["title"], "ok": False, "note": f"다른 계정에 넘김: {why}", "at": int(time.time()),
                                          "key": job["key"], "subs": "", "channel": (rule or {}).get("channel_title", ""),
                                          "tg": lane.tg, "rule": job["rule"], "handed": True,
                                          "elapsed": int(time.time() - lane.started) if lane.started else 0}, *state["history"]])
        save_state(state)
        lane.cancel_req = None
        return True

    # ----- 진행 목록 손보기 (순서 바꾸기 · 계정 고르기) -----
    def move_item(self, rule_id, key, where):
        """올릴 차례인 항목의 자리를 바꾼다: up / down / top / bottom (지금 올리는 중인 것은 그대로)"""
        i = next((n for n, j in enumerate(self.queue) if j["rule"] == rule_id and j["key"] == key), None)
        if i is None:
            raise ValueError("진행 목록에 없는 항목입니다.")
        job = self.queue[i]
        if id(job) in self._taken:
            raise ValueError("지금 올리는 중인 항목은 옮길 수 없습니다.")
        free = [n for n, j in enumerate(self.queue) if id(j) not in self._taken]   # 옮길 수 있는 자리들
        pos = free.index(i)
        target = {"up": max(0, pos - 1), "down": min(len(free) - 1, pos + 1), "top": 0, "bottom": len(free) - 1}.get(where)
        if target is None:
            raise ValueError("where는 up, down, top, bottom 중 하나입니다.")
        if target != pos:
            rest = [j for j in self.queue if j is not job]
            anchor = self.queue[free[target]]
            k = rest.index(anchor) + (1 if target > pos else 0)
            self.queue = rest[:k] + [job] + rest[k:]
            self._queue_changed()
        return job["title"]

    def assign_item(self, rule_id, key, tg):
        """그 항목을 올릴 계정을 직접 고른다 (tg ""이면 규칙대로 - 놀고 있는 계정). 다시 시도 대기 중인 것이면 바로 줄에"""
        job = next((j for j in self.queue if j["rule"] == rule_id and j["key"] == key), None)
        if job is not None and id(job) in self._taken:
            raise ValueError("지금 올리는 중인 항목입니다 - 취소한 뒤 다시 올리거나 끝나기를 기다려 주세요.")
        if job is None:
            state = load_state()
            rs = state["rules"].get(rule_id) or {}
            r = (rs.get("retry") or {}).get(key)
            if r is None:
                raise ValueError("진행 목록에 없는 항목입니다.")
            r["at"] = 0
            save_state(state)
            job = {"rule": rule_id, "key": key, "title": r.get("title", key)}
            self.queue.append(job)
        if tg:
            job["pin"] = tg
            job["avoid"] = [a for a in job.get("avoid") or [] if a != tg]
            self._down.pop(tg, None)   # 직접 고른 계정은 쉬던 중이어도 바로 써 본다
        else:
            job.pop("pin", None)
        self._queue_changed()
        self.wake()
        return job["title"]

    async def _encode_slot(self):
        """인코딩 차례 - 동시에 encode_slots개까지 (계정 줄이 여럿이어도 CPU를 다 쓰지 않게)"""
        while self._encoding >= load_settings()["encode_slots"]:
            self._set_phase(f"인코딩 차례 기다리는 중 (동시에 {load_settings()['encode_slots']}개까지 - 앞 인코딩이 끝나면)")
            self._encode_free.clear()
            try:
                await asyncio.wait_for(self._encode_free.wait(), 5)
            except asyncio.TimeoutError:
                pass
        self._encoding += 1

    def _encode_done(self):
        self._encoding = max(0, self._encoding - 1)
        self._encode_free.set()

    # ----- TMDB 포스터 (폴더 규칙 - Plex 정보가 없는 rclone 업로드) -----
    @staticmethod
    def _tmdb_key(tg_id):
        get = getattr(config_store, "get_source", None)
        if not get:
            return ""
        cfg = config_store.load()
        key = str((get(cfg, tg_id) or {}).get("tmdb_key") or "").strip()
        if not key:   # 계정이 여럿이면 TMDB 키는 한 곳에만 넣어도 되게 (다른 텔레그램 소스의 키)
            key = next((str(s.get("tmdb_key")).strip() for s in cfg.get("sources", []) if s.get("type") == "telegram" and s.get("tmdb_key")), "")
        return key

    @staticmethod
    def _poster_on():
        """텔레그램 화면의 작품 정보 저장소가 있을 때만 (BookOasis Assistant에는 없다)"""
        return getattr(telegram_src, "KEEP_PLEX_META", True) and hasattr(telegram_src, "meta_target_for")

    async def _lookup_poster(self, tg_id, tgt, force=False):
        """작품 하나를 TMDB에서 찾아 텔레그램 화면의 작품 정보 저장소(meta 표)에 넣는다.
        돌려주는 값: "ok" / "cached"(이미 찾아 둠 - 같은 드라마의 다른 회차, 직접 고른 것) / 못 찾은 까닭"""
        hit = telegram_src.meta_get(tg_id, tgt["mkey"])
        if hit and (hit["manual"] or (hit["data"] and not force)):
            return "cached"
        key = self._tmdb_key(tg_id)
        if not key:
            return "TMDB 키 없음 (설정 > 소스 > 텔레그램)"
        from . import tmdb
        api = tmdb.Tmdb(self.http(), key)
        try:
            tid = await api.find(tgt["kind"], tgt["titles"], tgt.get("year") or "")
            data = await api.details(tgt["kind"], tid) if tid else None
        except Exception as e:  # noqa: BLE001 - 연결 실패·키 오류 등은 나중에 다시
            return f"TMDB 오류: {e}"[:200]
        if not data:
            return "TMDB에서 못 찾음"
        telegram_src.meta_put(tg_id, tgt["mkey"], tgt["kind"], tid, data)
        return "ok" if data.get("poster") else "TMDB에 포스터 없음"

    def _poster_note(self, tg_id, tgt, title, err):
        """못 찾은 포스터를 기억해 POSTER_RETRY 간격으로 다시 찾는다 (다 해도 못 찾으면 '포스터 못 찾음'에 남겨 직접 고르게)"""
        state = load_state()
        posters = state.setdefault("posters", {})
        k = f"{tg_id}|{tgt['mkey']}"
        p = posters.get(k) or {"tg": tg_id, "mkey": tgt["mkey"], "kind": tgt["kind"], "titles": tgt.get("titles") or [],
                               "year": tgt.get("year") or "", "title": title, "tries": 0}
        p["tries"] = p.get("tries", 0) + 1
        p["error"] = err
        p["gave_up"] = p["tries"] > len(POSTER_RETRY)
        p["at"] = 0 if p["gave_up"] else int(time.time()) + POSTER_RETRY[p["tries"] - 1]
        posters[k] = p
        while len(posters) > 1000:
            posters.pop(next(iter(posters)))
        save_state(state)

    def _poster_found(self, tg_id, mkey):
        state = load_state()
        if (state.get("posters") or {}).pop(f"{tg_id}|{mkey}", None) is not None:
            save_state(state)

    @staticmethod
    def _poster_missing(state):
        return [{k: p.get(k) for k in ("tg", "mkey", "kind", "title", "titles", "year", "tries", "error", "at", "gave_up")}
                for p in list((state.get("posters") or {}).values())[-100:]]

    async def _find_poster(self, rule, title, file_name):
        """올린 영상의 작품 포스터를 찾아 둔다. 기록에 붙일 짧은 문구 (BookOasis Assistant 등 저장소가 없으면 "")"""
        if not self._poster_on():
            return ""
        tgt = telegram_src.meta_target_for(title, file_name)
        if not tgt:
            return ""
        res = await self._lookup_poster(rule["tg"], tgt)
        if res in ("ok", "cached"):
            self._poster_found(rule["tg"], tgt["mkey"])
            return "포스터 찾음 (TMDB)" if res == "ok" else ""
        self._poster_note(rule["tg"], tgt, title, res)
        return f"포스터 못 찾음 ({res}) - 나중에 다시 찾음"

    async def _poster_retries(self):
        """다시 찾을 차례가 된 포스터 (loop가 확인할 때마다, 한 번에 20개까지)"""
        if not self._poster_on():
            return
        now = time.time()
        due = [p for p in (load_state().get("posters") or {}).values() if not p.get("gave_up") and (p.get("at") or 0) <= now][:20]
        for p in due:
            res = await self._lookup_poster(p["tg"], p)
            if res in ("ok", "cached"):
                self._poster_found(p["tg"], p["mkey"])
            else:
                self._poster_note(p["tg"], p, p.get("title") or p["mkey"], res)

    async def auto_tmdb(self, force=False):
        """텔레그램 채널에 들어온 영상 중 작품 정보(TMDB)가 아직 없는 것을 찾아 둔다 - 예전엔 영상을 한 번 열어 보거나
        [정보 고치기]를 해야 목록 카드에 포스터가 붙었다 (다른 프로그램·자동 올리기로 올린 mp4 등). 찾은 수"""
        if not self._poster_on() or not hasattr(telegram_src, "tmdb_targets"):
            return 0
        now = time.time()
        if not force and now - self._auto_tmdb_at < AUTO_TMDB_SEC:
            return 0
        self._auto_tmdb_at = now
        found = tried = 0
        for s in config_store.load().get("sources", []):
            if s.get("type") != "telegram" or not self._tmdb_key(s.get("id")):
                continue
            try:
                chans = [int(c) for c in getattr(self.tg_for(s["id"]), "channels", None) or []]
            except Exception:  # noqa: BLE001 - 지운 소스 등
                continue
            for ch in chans:
                sig = await asyncio.to_thread(telegram_src.channel_sig, s["id"], ch) if hasattr(telegram_src, "channel_sig") else None
                if sig and self._tmdb_done.get((s["id"], ch)) == sig and not force:
                    continue
                left = False
                for t in await asyncio.to_thread(telegram_src.tmdb_targets, s["id"], ch):
                    # 찾았어도 포스터가 없으면(포스터 없는 항목으로 맞춰졌거나 그때 그림이 없었음) 하루에 한 번 다시
                    if t["manual"] or (t["have"] and t.get("poster", True)) or (t["fetched"] and now - t["fetched"] < AUTO_TMDB_MISS):
                        continue
                    if tried >= AUTO_TMDB_MAX:
                        left = True
                        break
                    tried += 1
                    res = await self._lookup_poster(s["id"], t, force=t["have"])
                    if res == "ok":
                        found += 1
                    elif res == "TMDB에서 못 찾음":   # 못 찾은 것도 적어 두어 AUTO_TMDB_MISS 뒤에 다시
                        telegram_src.meta_put(s["id"], t["mkey"], t["kind"], None, None)
                    elif res.startswith("TMDB 오류"):
                        left = True
                        break
                    await asyncio.sleep(0.25)   # TMDB에 너무 빨리 묻지 않게
                if not left and sig:
                    self._tmdb_done[(s["id"], ch)] = sig
                if tried >= AUTO_TMDB_MAX:
                    self._auto_tmdb_at = now - AUTO_TMDB_SEC + 120   # 남은 것은 2분 뒤에 이어서
                    return found
        if found:
            log.info("텔레그램 작품 정보: %d개를 TMDB에서 찾아 두었습니다", found)
        return found

    async def fill_channel_tmdb(self, sid, channel, limit=150, budget=50.0):
        """[목록 새로 고침]: 이 채널에서 작품 정보(TMDB)가 없는 영상을 지금 찾는다 - 예전에 못 찾은 것도 다시.
        budget초 안에서 limit개까지. 돌려주는 값: {"found", "tried", "left", "missing": [못 찾은 제목], "error"}"""
        out = {"found": 0, "tried": 0, "left": 0, "missing": [], "error": ""}
        if not self._poster_on() or not hasattr(telegram_src, "tmdb_targets"):
            return out
        if not self._tmdb_key(sid):
            out["error"] = "TMDB 키가 없습니다 (설정 > 소스 > 텔레그램)."
            return out
        # 아직 못 찾은 것 + 찾았지만 포스터가 없는 것 (직접 고른 것은 그대로)
        todo = [t for t in await asyncio.to_thread(telegram_src.tmdb_targets, sid, int(channel))
                if not t["manual"] and not (t["have"] and t.get("poster", True))]
        end = time.time() + budget
        for i, t in enumerate(todo):
            if out["tried"] >= limit or time.time() > end:
                out["left"] = len(todo) - i
                break
            out["tried"] += 1
            res = await self._lookup_poster(sid, t, force=True)
            if res in ("ok", "cached"):
                out["found"] += 1
            elif res == "TMDB에서 못 찾음":
                telegram_src.meta_put(sid, t["mkey"], t["kind"], None, None)
                out["missing"].append(" / ".join(x for x in t.get("titles") or [] if x) or t["mkey"])
            elif res == "TMDB에 포스터 없음":
                out["missing"].append((" / ".join(x for x in t.get("titles") or [] if x) or t["mkey"]) + " (포스터 없음)")
            else:   # 키 오류·연결 실패 - 더 해도 같다
                out["error"] = res
                out["left"] = len(todo) - i - 1
                break
            await asyncio.sleep(0.2)
        self._tmdb_done.pop((sid, int(channel)), None)
        if out["found"]:
            log.info("텔레그램 작품 정보 (새로 고침, 채널 %s): %d개 찾음, 못 찾음 %d", channel, out["found"], len(out["missing"]))
        return out

    def start_poster_backfill(self, rule_id=None):
        """[포스터 복구] 폴더 규칙 채널에서 포스터(작품 정보)가 없는 영상의 작품을 한꺼번에 찾는다 (뒤에서). 진행은 status의 poster"""
        if not self._poster_on():
            raise ValueError("이 프로그램에는 텔레그램 화면이 없어 포스터를 둘 곳이 없습니다.")
        if self._poster_task and not self._poster_task.done():
            return self.poster_job
        chans = sorted({(r["tg"], int(r["channel"])) for r in load_settings()["rules"]
                        if r["kind"] == "folder" and rule_id in (None, r["id"])})
        if not chans:
            raise ValueError("rclone(폴더) 규칙이 없습니다.")
        self.poster_job = {"running": True, "done": 0, "total": 0, "found": 0, "missing": 0, "error": ""}
        self._poster_task = asyncio.ensure_future(self._poster_backfill(chans))
        return self.poster_job

    async def _poster_backfill(self, chans):
        job = self.poster_job
        try:
            todo = []
            for tg_id, ch in chans:
                for t in await asyncio.to_thread(telegram_src.tmdb_targets, tg_id, ch):
                    if not t["have"] and not t["manual"]:
                        todo.append((tg_id, t))
            job["total"] = len(todo)
            for tg_id, t in todo:
                res = await self._lookup_poster(tg_id, t, force=True)
                if res in ("ok", "cached"):
                    job["found"] += 1
                    self._poster_found(tg_id, t["mkey"])
                else:
                    job["missing"] += 1
                    self._poster_note(tg_id, t, (t.get("titles") or [t["mkey"]])[0], res)
                    if res.startswith("TMDB 키 없음"):
                        job["error"] = res
                        break
                job["done"] += 1
                await asyncio.sleep(0.25)   # TMDB에 너무 빨리 묻지 않게
        except Exception as e:  # noqa: BLE001
            job["error"] = str(e)[:200]
            log.warning("자동 올리기 포스터 복구 실패: %s", e)
        finally:
            job["running"] = False

    async def search_poster(self, tg_id, kind, query, year=""):
        """[포스터 고르기] TMDB 검색 결과 (가벼운 모양)"""
        key = self._tmdb_key(tg_id)
        if not key:
            raise ValueError("TMDB 키가 없습니다 (설정 > 소스 > 텔레그램).")
        from . import tmdb
        try:
            res = await tmdb.Tmdb(self.http(), key).search("tv" if kind == "tv" else "movie", query, year)
        except tmdb.TmdbError as e:
            raise ValueError(str(e))
        return [{k: v for k, v in r.items() if k != "_raw"} for r in res[:20]]

    async def set_poster(self, tg_id, mkey, kind, tmdb_id):
        """[포스터 고르기] 고른 작품으로 정한다 (직접 고른 것 - 자동으로 다시 찾지 않음). tmdb_id 0이면 '정보 없음'으로"""
        kind = "tv" if kind == "tv" else "movie"
        data = None
        if tmdb_id:
            from . import tmdb
            try:
                data = await tmdb.Tmdb(self.http(), self._tmdb_key(tg_id)).details(kind, int(tmdb_id))
            except tmdb.TmdbError as e:
                raise ValueError(str(e))
        telegram_src.meta_put(tg_id, mkey, kind, int(tmdb_id) or None, data, manual=True)
        self._poster_found(tg_id, mkey)
        return data

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
        size = int(part.get("size") or 0)
        # 1) 중복: 같은 파일(이름+크기) / 채널에 같은 파일 이름 / 같은 작품·시즌·회차 (태그·확장자·해상도 무관)
        self._cur_sig = file_sig(part.get("file") or "", size) if part.get("file") and size else ""
        dup = "" if test else await self._duplicate(rule, fields, os.path.basename(local_file))
        if dup:
            return f"건너뜀: 중복 - {dup}"
        # 2) 제외 정규식 (파일 이름·제목)
        # (파일 이름·제목·전체 경로 - 경로로 "Featurettes 폴더 아래" 같은 것도 뺄 수 있게)
        pat = "" if test else excluded_by(load_settings()["exclude"], os.path.basename(local_file), fields["name"], fields["title"],
                                          local_file, str(part.get("file") or ""))
        if pat:
            return f"건너뜀: 제외 - 제외 정규식 '{pat}'에 맞음"
        local_ok = bool(local_file and os.path.isfile(local_file) and os.access(local_file, os.R_OK))
        side = sidecar_subs(local_file) if local_ok else []   # Plex가 아직 못 읽은 옆 자막 파일도 자막으로 친다
        # 3) 소프트 자막: 파일 안의 자막 트랙은 Plex 정보에 없을 수 있어 파일을 직접 열어 본다 (ffprobe, 머리만 읽음) - 있으면 바로 올림
        soft = await self._probe_subs(local_file if local_ok else f"{src['url']}{part['key']}",
                                      None if local_ok else src.get("token"))
        if soft is None:   # ffprobe가 없거나 못 열었으면 Plex 정보로
            soft = [x for x in subs if not x.get("key")]
        # 4) 자막 파일을 기다린다 (정한 분만큼 - 0이면 생길 때까지)
        if rule["wait_subs"] and not subs and not side and not soft and not test:
            self._wait_subs(rule, key)
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
            burned, burn_skip, soft_lost = False, "", False
            embedded = bool(soft)
            # 정한 높이(기본 720p)보다 큰 영상은 용량과 상관없이 줄인다 - 자막 입히기와 한 번에 인코딩
            info = await folder_watch.probe(local)
            vh = int(((folder_watch.streams(info, "video") or [{}])[0]).get("height") or 0)
            scale = bool(rule["height"] and vh > rule["height"])
            width, height = int(media.get("width") or 0), int(media.get("height") or 0)
            enc_used, remux_notes = "", []
            inside = folder_watch.text_subs(info) if info else []
            # 자막 입히기: 텔레그램 앱은 영상 안의 자막 트랙(소프트 자막)을 보여 주지 않는다 - 영상 안에 자막이 있어도
            # 자막 파일이나 글자 자막이 있으면 화면에 입힌다 (한국어 먼저). 전에는 영상 안에 아무 자막(영어·그림 자막 포함)이
            # 있으면 옆의 한국어 자막 파일을 입히지 않았다
            burn_name = None
            if rule["burn_subs"] and (sub_files or inside) and transcode.ffmpeg_path():
                if not has_korean_font():
                    burn_skip = "한글 글꼴이 없어 자막을 입히지 않고 파일로 올림 (sudo apt install fonts-nanum)"
                else:
                    burn_name = await self._burn_source(local, sub_files, inside, tmp, fields["name"])
            # mp4로 바꾸면 파일 안의 자막 트랙은 따라오지 못한다 - 입히지 않으면 글자 자막은 꺼내서 파일로 같이 올린다
            will_convert = bool(info) and transcode.ffmpeg_path() and \
                folder_watch.encode_plan(info, ext, rule["height"], burn_name, None) is not None
            if embedded and will_convert and not burn_name and not sub_files:
                for idx, ilang, isext in inside[:1]:
                    raw = os.path.join(tmp, f"inside.{isext}")
                    ok, _err = await self._ffmpeg("-i", local, "-map", f"0:{idx}", "-c:s", "ass" if isext == "ass" else "srt", raw)
                    if ok and os.path.exists(raw) and os.path.getsize(raw) > 0:
                        sub_files.append((raw, sub_lang(ilang), isext))
                soft_lost = not sub_files
            # 텔레그램 앱에서 바로 재생되게 언제나 MP4(H.264 + AAC)로 - 이미 그런 파일이면 손대지 않는다 (plan None)
            out = os.path.join(tmp, f"{stem}.enc.mp4")
            plan, did_burn = None, False
            if transcode.ffmpeg_path() and info:
                plan, did_burn, why = await self._encode_burn(
                    local, lambda enc, b: folder_watch.encode_plan(info, ext, rule["height"], b, enc), out, duration, fields["name"], tmp, burn_name)
                burn_skip = burn_skip or why
            if plan:
                enc_used = folder_watch.ENCODER_NAMES.get(plan.encoder, "")
                remux_notes = [n for n in plan[1] if n not in ("자막 입힘",) and "p로 줄임" not in n]
                if copied or not local_ok:   # 서버로 가져온 원본은 바로 지운다 (디스크)
                    try:
                        os.remove(local)
                    except OSError:
                        pass
                local, name, ext = out, f"{stem}.mp4", ".mp4"
                burned, scaled = did_burn, scale
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
        folder = rule["folder"]
        remote = folder_watch.is_remote(folder)
        copied = False
        self._cur_sig = (self._job or {}).get("sig") or ""
        fields = folder_watch.file_fields(key, rule["section_title"])
        # 1) 중복 (같은 파일 / 채널에 같은 파일 이름 / 같은 작품·시즌·회차) -> 2) 제외 정규식
        dup = "" if test else await self._duplicate(rule, fields, os.path.basename(key))
        if dup:
            return f"건너뜀: 중복 - {dup}"
        # (파일 이름·제목·폴더 안 경로·전체 경로 - 경로로 "Featurettes 폴더 아래" 같은 것도 뺄 수 있게)
        pat = "" if test else excluded_by(load_settings()["exclude"], os.path.basename(key), fields["name"], key,
                                          folder_watch._join_remote(folder, key) if folder_watch.is_remote(folder)
                                          else os.path.join(folder, key))
        if pat:
            return f"건너뜀: 제외 - 제외 정규식 '{pat}'에 맞음"
        if not transcode.ffmpeg_path():   # (중복·제외 확인은 ffmpeg 없이도 - 처리 순서대로)
            raise RuntimeError("서버에 ffmpeg가 없어 인코딩할 수 없습니다.")
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
            # 3) 소프트 자막이 있으면 바로, 4) 없으면 자막 파일을 정한 분만큼 기다린다
            if rule["wait_subs"] and not side and not any_inside and not test:
                self._wait_subs(rule, key)
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
            # 3) 화면에 입힐 자막: 한국어 먼저 (자막 파일 > 파일 안의 글자 자막 > 다른 언어)
            burn, burn_skip, inside = None, "", folder_watch.text_subs(info)
            if rule["burn_subs"] and (sub_files or inside):
                if not has_korean_font():
                    burn_skip = "한글 글꼴이 없어 자막을 입히지 않고 파일로 올림 (sudo apt install fonts-nanum)"
                else:
                    burn = await self._burn_source(local, sub_files, inside, tmp, fields["name"])
            # 4) 인코딩 (필요할 때만)
            out = os.path.join(tmp, f"{stem}.mp4")
            plan, did_burn, why = await self._encode_burn(
                local, lambda enc, b: folder_watch.encode_plan(info, ext, rule["height"], b, enc), out, duration, fields["name"], tmp, burn)
            burn_skip = burn_skip or why
            burn = burn if did_burn else None
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
        # Plex 정보가 없으니 TMDB에서 작품을 찾아 둔다 (목록 카드의 포스터) - 같은 드라마는 처음 찾은 것을 그대로
        poster = await self._find_poster(rule, fields["name"], os.path.basename(key))
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
        if poster:
            notes.append(poster)
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
            self._note_sent(getattr(sent, "id", None))
            for spath, lang, sext in sub_files:   # 그 조각의 자막 (나눴으면 조각 시간만큼 잘라서)
                out = spath
                if many:
                    out = await self._cut_sub(spath, os.path.join(tmp, f"cut{i}_{os.path.splitext(os.path.basename(spath))[0]}"),
                                              start, dur, sext)
                    if not out:
                        continue
                sname = f"{os.path.splitext(vname)[0]}{'.' + lang if lang else ''}.{'srt' if many and sext == 'smi' else sext}"
                sub_msg = await client.send_file(peer, out, force_document=True, attributes=[DocumentAttributeFilename(sname)])
                self._note_sent(getattr(sub_msg, "id", None))
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

    async def _burn_source(self, local, sub_files, inside, tmp, title):
        """화면에 입힐 자막 파일 이름 (tmp 안, UTF-8). 한국어를 먼저: 한국어 자막 파일 > 영상 안의 한국어 글자 자막 >
        다른 자막 파일 > 영상 안의 다른 글자 자막. 못 고르면 None"""
        def ko(lang):
            return str(lang or "").lower() in ("ko", "kor", "korean", "kr", "ko-kr")
        order = [("file", f) for f in sub_files if ko(f[1])] + [("in", t) for t in inside if ko(t[1])] + \
                [("file", f) for f in sub_files if not ko(f[1])] + [("in", t) for t in inside if not ko(t[1])]
        for kind, x in order:
            if kind == "file":
                return os.path.basename(self.utf8_sub(x[0], os.path.join(tmp, f"burn.{x[2]}")))
            idx, _lang, isext = x
            raw = os.path.join(tmp, f"inside{idx}.{isext}")
            ok, err = await self._ffmpeg("-i", local, "-map", f"0:{idx}", "-c:s", "ass" if isext == "ass" else "srt", raw)
            if ok and os.path.exists(raw) and os.path.getsize(raw) > 0:
                return os.path.basename(self.utf8_sub(raw, os.path.join(tmp, f"burn.{isext}")))
            log.info("자동 올리기: 파일 안의 자막을 꺼내지 못했습니다 (%s): %s", title, err.strip()[-200:])
        return None

    async def _encode_burn(self, local, make, out, duration, title, tmp, burn_name):
        """자막을 입혀 인코딩 - 자막 때문에 실패하면(깨진 자막 등) 자막 없이 한 번 더. 돌려주는 값: (plan, 입혔는지, 못 입힌 까닭)"""
        try:
            return await self._encode_plan(local, lambda enc: make(enc, burn_name), out, duration, title, tmp), bool(burn_name), ""
        except RuntimeError as e:
            if not burn_name:
                raise
            log.warning("자동 올리기: 자막을 입혀 인코딩하지 못해 자막 없이 다시 합니다 (%s): %s", title, e)
            plan = await self._encode_plan(local, lambda enc: make(enc, None), out, duration, title, tmp)
            return plan, False, "자막을 입히지 못해 파일로 올림 (자막 파일 확인)"

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
        """ffmpeg (pre) -i src args out (진행률·속도는 ffmpeg -progress로 self.current에). 동시에 encode_slots개까지"""
        await self._encode_slot()
        try:
            await self._encode_run(src_path, args, out, duration, title, phase, cwd, pre)
        finally:
            self._encode_done()

    async def _encode_run(self, src_path, args, out, duration, title, phase, cwd, pre=()):
        cmd = [transcode.ffmpeg_path(), "-v", "error", "-y", "-nostats", "-progress", "pipe:1", *pre, "-i", os.path.abspath(src_path),
               *args, os.path.abspath(out)]
        self.current = {"title": title, "phase": phase, "done": 0, "total": int(duration * 1000) or 0}
        # 올리기 인코딩은 뒤 작업 - CPU·디스크를 가장 낮은 우선순위로 (재생용 서버 변환이 먼저)
        ionice = shutil.which("ionice") if os.name != "nt" else None
        if ionice:
            cmd = [ionice, "-c", "3", *cmd]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd,
                                                    **low_priority_kwargs())
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
                    self._set_phase("재생 중이라 잠시 멈춤 (재생이 끝나면 이어서)" if busy else phase)
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
