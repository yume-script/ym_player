"""Plex 라이브러리에 새로 들어온 동영상 -> 텔레그램 채널에 자동으로 올리기 (설정 > 자동 올리기).

규칙 하나 = Plex 소스의 라이브러리(섹션) 하나 -> 텔레그램 소스(로그인한 내 계정)의 채널 하나.
- 몇 분마다(기본 10분) 그 라이브러리를 "추가된 순"으로 훑어 새 항목(영화 / 에피소드)을 찾는다.
- 규칙을 처음 켤 때는 지금 있는 것은 올리지 않고 기준만 잡는다 (그 뒤로 들어온 것부터).
- 파일은 Plex 서버가 이 컴퓨터에 있어 원본 경로가 보이면 그대로, 아니면 Plex에서 임시 폴더로 받은 뒤 올리고 지운다.
- 한 번에 하나씩 올린다. 실패하면 다음 확인 때 다시(3번까지). 올린 것은 기억해 두어 다시 켜도 두 번 올리지 않는다
  (data/plex_to_tg.json).
- 캡션 첫 줄은 "작품 S01E02 - 제목" / "영화 (2026)" 모양이라 YMPlayer 텔레그램 목록에서 제목·회차 묶기가 그대로 된다.
- 텔레그램 일반 계정은 파일 하나 2GB(프리미엄 4GB)까지 - 넘는 파일은 ffmpeg로 시간을 나눠(각 조각이 따로 재생되게)
  "이름 part1.mkv", "이름 part2.mkv"…로 올린다. YMPlayer는 이 조각들을 한 묶음으로 이어서 튼다.
- 자막 기다리기(규칙마다, 기본 켬): Plex 항목에 자막(내장이든 외부든)이 하나도 없으면 올리지 않고 기다렸다가
  생기면 올린다 (기다릴 날 수를 정하면 그 뒤엔 자막 없이). 외부 자막 파일은 영상 뒤에 같은 이름으로 같이 올려
  YMPlayer가 영상에 자동으로 붙인다 (나눈 영상이면 자막도 조각 시간에 맞춰 자른다).
- 자막 입혀 인코딩(규칙마다, 기본 켬): 영상 안에 자막이 없고 외부 자막만 있으면(자막을 기다리다 생긴 경우 등) 그 자막을
  화면에 입혀 H.264 mp4로 다시 인코딩해서 올린다 - 텔레그램 앱에서도 자막이 보인다 (한국어 자막을 먼저 고름).
- 올린 영상마다 Plex의 작품 정보(포스터·배경·줄거리·장르·출연·회차 제목/줄거리/스틸)를 텔레그램 색인에 같이 남겨
  (telegram_src.plex_meta_put) YMPlayer의 텔레그램 화면이 TMDB 없이도 Plex처럼 보인다.

설정은 config.json의 "plex_to_tg": {interval, max_gb, rules: [{id, enabled, plex, section, section_title, tg, channel,
channel_title, caption, wait_subs, wait_days, burn_subs}]}
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

from . import config_store, telegram_src, transcode

log = logging.getLogger("ym_player.plex_to_tg")

MAX_RULES = 20
MAX_TRIES = 3
KEEP_DONE = 5000
KEEP_HISTORY = 50
SCAN_SIZE = 50
DEFAULT_CAPTION = "{name}\n\n{summary}"
STREAMABLE = {".mp4", ".m4v", ".mov"}   # 텔레그램 앱에서 받기 전에 바로 재생되는 형식
SUB_EXT = {"srt": "srt", "subrip": "srt", "ass": "ass", "ssa": "ass", "smi": "smi", "sami": "smi", "vtt": "vtt", "webvtt": "vtt"}
MAX_PARTS = 10
PART_LIMIT = 1000 * 1024 * 1024   # 텔레그램 한도 1GB 단위 (2GB = 2000MiB = 4000조각 x 512KB) - max_gb를 곱해 쓴다


_KO_FONT = None


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
                "wait_subs": bool(r.get("wait_subs", True)), "wait_days": num(r.get("wait_days"), 0, 365, 0),
                "burn_subs": bool(r.get("burn_subs", True))}
        if not (rule["plex"] and rule["section"] and rule["tg"] and re.fullmatch(r"-?\d+", rule["channel"])):
            raise ValueError("규칙마다 Plex 라이브러리와 텔레그램 채널을 골라 주세요.")
        rules.append(rule)
    if len(rules) > MAX_RULES:
        raise ValueError(f"규칙은 {MAX_RULES}개까지 만들 수 있습니다.")
    return {"interval": num(raw.get("interval"), 2, 1440, 10), "max_gb": num(raw.get("max_gb"), 1, 4, 2), "rules": rules}


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


def item_fields(m, section_title=""):
    """Plex 메타데이터 -> 캡션에 넣을 값들"""
    kind = m.get("type", "")
    title = str(m.get("title") or "")
    year = str(m.get("year") or "")
    if kind == "episode":
        show = str(m.get("grandparentTitle") or "")
        s, e = m.get("parentIndex"), m.get("index")
        code = f"S{int(s):02d}E{int(e):02d}" if isinstance(s, int) and isinstance(e, int) else ""
        name = " ".join(x for x in (show, code) if x) + (f" - {title}" if title else "")
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
    def __init__(self, plex_json, plex_src, tg_for, http):
        """plex_json(src, path, params) -> dict, plex_src(id) -> (cfg, src), tg_for(id) -> TelegramSource, http() -> ClientSession"""
        self.plex_json, self.plex_src, self.tg_for, self.http = plex_json, plex_src, tg_for, http
        self.queue = []          # [{rule, key, title, tries}]
        self.current = None      # {title, phase, done, total}
        self.last_check = 0
        self.error = ""
        self._wake = asyncio.Event()

    def wake(self):
        self._wake.set()

    def status(self):
        st = load_state()
        return {"queue": [{"title": j["title"], "rule": j["rule"]} for j in self.queue], "current": self.current,
                "last_check": self.last_check, "error": self.error, "history": st["history"][:KEEP_HISTORY],
                "rules": {rid: {"baseline": r.get("baseline", 0), "count": len(r.get("done", []))} for rid, r in st["rules"].items()},
                "waiting": [{"title": w.get("title", k), "since": w.get("since", 0), "rule": rid}
                            for rid, r in st["rules"].items() for k, w in (r.get("waiting") or {}).items()]}

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
                await self.run_queue(st)
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
        for rule in st["rules"]:
            if not rule["enabled"]:
                continue
            items = await self._recent(rule)
            rs = state["rules"].setdefault(rule["id"], {"done": [], "fails": {}})
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
            for m in sorted(items, key=lambda x: int(x.get("addedAt") or 0)):
                key = str(m["ratingKey"])
                if int(m.get("addedAt") or 0) <= rs["baseline"] or key in done or (rule["id"], key) in queued:
                    continue
                if rs.get("fails", {}).get(key, 0) >= MAX_TRIES:
                    continue
                self.queue.append({"rule": rule["id"], "key": key, "title": item_fields(m)["name"]})
                queued.add((rule["id"], key))
        self.last_check = int(time.time())
        save_state(state)

    async def test_latest(self, rule_id):
        """규칙 시험: 가장 최근 항목 하나를 (올린 적이 있어도) 올린다"""
        rule = next((r for r in load_settings()["rules"] if r["id"] == rule_id), None)
        if not rule:
            raise ValueError("없는 규칙입니다. 먼저 저장해 주세요.")
        items = await self._recent(rule)
        if not items:
            raise ValueError("이 라이브러리에 동영상이 없습니다.")
        m = items[0]
        self.queue.append({"rule": rule["id"], "key": str(m["ratingKey"]), "title": item_fields(m)["name"], "test": True})
        self.wake()
        return item_fields(m)["name"]

    async def run_queue(self, st):
        rules = {r["id"]: r for r in st["rules"]}
        while self.queue:
            job = self.queue[0]
            rule = rules.get(job["rule"])
            ok, note, waiting = False, "", False
            if rule:
                try:
                    note = await self.upload(rule, job["key"], st["max_gb"], test=job.get("test"))
                    ok = True
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
            self.queue.pop(0)
            self.current = None
            state = load_state()
            rs = state["rules"].setdefault(job["rule"], {"done": [], "fails": {}})
            if waiting:   # 자막이 생길 때까지 (기록에는 남기지 않고 상태의 "자막 기다리는 중"에)
                rs.setdefault("waiting", {}).setdefault(job["key"], {"title": job["title"], "since": int(time.time())})
                save_state(state)
                continue
            (rs.get("waiting") or {}).pop(job["key"], None)
            fails = rs.setdefault("fails", {})
            if ok and note.startswith("건너뜀"):   # 너무 큰 파일 - 다시 시도하지 않는다
                ok = False
                fails[job["key"]] = MAX_TRIES
            elif ok:
                rs["done"] = [*rs.get("done", []), job["key"]][-KEEP_DONE:]
                fails.pop(job["key"], None)
            elif not job.get("test"):
                fails[job["key"]] = fails.get(job["key"], 0) + 1
            state["history"] = [{"title": job["title"], "ok": ok, "note": note, "at": int(time.time()),
                                 "channel": (rule or {}).get("channel_title", "")}, *state["history"]][:KEEP_HISTORY]
            save_state(state)

    async def upload(self, rule, key, max_gb, test=False):
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
        if rule["wait_subs"] and not subs and not test:
            w = (load_state()["rules"].get(rule["id"], {}).get("waiting") or {}).get(key)
            if not rule["wait_days"] or not w or time.time() - w.get("since", time.time()) < rule["wait_days"] * 86400:
                raise Waiting()
        size = int(part.get("size") or 0)
        limit = int(max_gb * PART_LIMIT * 0.97)
        duration = (m.get("duration") or media.get("duration") or 0) / 1000
        if size > limit and not transcode.ffmpeg_path():
            return f"건너뜀: 파일이 {size / 1024 ** 3:.1f}GB인데 나눌 ffmpeg가 없습니다."
        if size > limit and duration <= 0:
            return f"건너뜀: 파일이 {size / 1024 ** 3:.1f}GB인데 길이를 몰라 나눌 수 없습니다."
        name = os.path.basename(str(part.get("file") or "")) or f"{key}.{part.get('container') or 'mp4'}"
        name = re.sub(r'[\\/:*?"<>|]', "_", name)
        stem, ext = os.path.splitext(name)
        tg = self.tg_for(rule["tg"])
        client = await tg.authorized_client()
        peer = await tg.peer(int(rule["channel"]))
        self.current = {"title": fields["name"], "phase": "준비 중", "done": 0, "total": size}
        tmp = os.path.join(config_store.DATA_DIR, "plex_to_tg_tmp", secrets.token_hex(6))
        os.makedirs(tmp, exist_ok=True)
        msgs = []
        try:
            local = str(part.get("file") or "")
            if not (local and os.path.isfile(local) and os.access(local, os.R_OK)):   # Plex가 다른 컴퓨터면 받아 온다
                local = os.path.join(tmp, name)
                await self._download(cfg, src, part["key"], local, fields["name"], size)
            # 외부 자막 파일 (내장 자막은 영상 안에 그대로 들어 있다)
            sub_files = []
            for x in subs:
                sext = SUB_EXT.get(str(x.get("codec") or x.get("format") or "").lower())
                if x.get("key") and sext:
                    lang = re.sub(r"[^A-Za-z-]", "", str(x.get("languageCode") or x.get("languageTag") or ""))[:8]
                    path = os.path.join(tmp, f"sub{len(sub_files)}.{sext}")
                    if await self._fetch(cfg, src, x["key"], path):
                        sub_files.append((path, lang, sext))
            burned = False
            embedded = any(not x.get("key") for x in subs)
            if rule["burn_subs"] and sub_files and not embedded:   # 영상 안에 자막이 없으면 외부 자막을 입혀 다시 인코딩
                best = next((f for f in sub_files if f[1].lower() in ("kor", "ko", "ko-kr")), sub_files[0])
                out = os.path.join(tmp, f"{stem}.burn.mp4")
                await self._burn(local, best[0], out, duration, fields["name"])
                local, name, ext, burned = out, f"{stem}.mp4", ".mp4", True
                sub_files = []   # 화면에 들어갔으니 따로 올리지 않는다
            thumb = await self._art(cfg, src, m.get("thumb") or m.get("grandparentThumb") or m.get("parentThumb"), 320, tmp)
            thumb = thumb if thumb and os.path.getsize(thumb) <= 200 * 1024 else None
            # 나누기 (각 조각이 따로 재생되게 시간으로)
            pieces = [(local, 0, duration)]
            if os.path.getsize(local) > limit:
                pieces = await self._split(local, os.path.join(tmp, stem), ext, duration, limit, fields["name"])
            caption = make_caption(rule["caption"], fields)
            from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo
            stream = ext.lower() in STREAMABLE
            for i, (path, start, dur) in enumerate(pieces, 1):
                many = len(pieces) > 1
                vname = f"{stem} part{i}{ext}" if many else name
                cap = caption
                if many:   # 첫 줄(제목)은 그대로 두고 그 아래에 (1/2)
                    first, _, rest = caption.partition("\n")
                    cap = f"{first}\n({i}/{len(pieces)})" + (f"\n{rest}" if rest else "")
                attrs = [DocumentAttributeVideo(duration=int(dur), w=int(media.get("width") or 0), h=int(media.get("height") or 0),
                                                supports_streaming=stream),
                         DocumentAttributeFilename(vname)]
                self.current = {"title": fields["name"] + (f" ({i}/{len(pieces)})" if many else ""),
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
            await self._save_meta(rule, cfg, src, m, [x for x in msgs if x], tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        log.info("자동 올리기: %s -> %s", fields["name"], rule["channel_title"] or rule["channel"])
        notes = []
        if burned:
            notes.append("자막 입혀 인코딩" + ("" if has_korean_font() else
                         " (서버에 한글 글꼴이 없어 글자가 네모로 보일 수 있음 - sudo apt install fonts-nanum)"))
        if len(msgs) > 1:
            notes.append(f"{len(msgs)}개로 나눠 올림")
        if not subs:
            notes.append("자막 없음")
        return ", ".join(notes)

    async def _ffmpeg(self, *args):
        proc = await asyncio.create_subprocess_exec(transcode.ffmpeg_path(), "-v", "error", "-y", *args,
                                                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _out, err = await proc.communicate()
        return proc.returncode == 0, err.decode("utf-8", "replace")[-300:]

    async def _burn(self, src_path, sub_path, out, duration, title):
        """자막을 화면에 입혀 H.264/AAC mp4로 (진행률은 ffmpeg -progress로)"""
        with open(sub_path, "rb") as f:   # 우리말 자막은 CP949인 경우가 많다 -> UTF-8로 맞춰 둔다 (libass는 UTF-8을 기대)
            raw = f.read()
        for enc in ("utf-8-sig", "cp949", "utf-16"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", "replace")
        sub_utf8 = os.path.splitext(sub_path)[0] + ".utf8" + os.path.splitext(sub_path)[1]
        with open(sub_utf8, "w", encoding="utf-8") as f:
            f.write(text)
        args = [transcode.ffmpeg_path(), "-v", "error", "-y", "-nostats", "-progress", "pipe:1", "-i", os.path.abspath(src_path),
                "-map", "0:v:0", "-map", "0:a?", "-vf", f"subtitles=filename={os.path.basename(sub_utf8)}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", os.path.abspath(out)]
        self.current = {"title": title, "phase": "자막 입혀 인코딩 중", "done": 0, "total": int(duration * 1000) or 0}
        # 자막 파일은 그 폴더에서 이름만으로 (필터 안에서 경로의 : ' 등을 이스케이프하지 않게 - 이름은 subN.utf8.srt 꼴)
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                    cwd=os.path.dirname(sub_utf8))
        errs = []

        async def read_err():
            async for line in proc.stderr:
                errs.append(line.decode("utf-8", "replace"))
                del errs[:-20]
        err_task = asyncio.ensure_future(read_err())
        async for line in proc.stdout:   # out_time_ms=12345678 (이름과 달리 마이크로초)
            k, _, v = line.decode("utf-8", "replace").strip().partition("=")
            if k == "out_time_ms" and v.isdigit() and self.current:
                self.current["done"] = int(v) // 1000
        await proc.wait()
        await err_task
        if proc.returncode != 0 or not os.path.exists(out):
            raise RuntimeError(f"자막 입혀 인코딩 실패: {''.join(errs).strip()[-300:]}")

    async def _split(self, src_path, base, ext, duration, limit, title):
        """시간으로 n조각 [(경로, 시작초, 길이초)] - 조각이 한도를 넘으면 더 잘게 다시"""
        n = max(2, math.ceil(os.path.getsize(src_path) / (limit * 0.9)))
        while n <= MAX_PARTS:
            seg = duration / n
            out = []
            for i in range(n):
                path = f"{base} part{i + 1}{ext}"
                self.current = {"title": title, "phase": f"{n}개로 나누는 중 ({i + 1}/{n})", "done": i, "total": n}
                args = ["-ss", f"{i * seg:.3f}", "-i", src_path] + (["-t", f"{seg:.3f}"] if i < n - 1 else []) + \
                       ["-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero", path]
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
                    "seasons": []}
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
