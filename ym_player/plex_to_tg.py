"""Plex 라이브러리에 새로 들어온 동영상 -> 텔레그램 채널에 자동으로 올리기 (설정 > 자동 올리기).

규칙 하나 = Plex 소스의 라이브러리(섹션) 하나 -> 텔레그램 소스(로그인한 내 계정)의 채널 하나.
- 몇 분마다(기본 10분) 그 라이브러리를 "추가된 순"으로 훑어 새 항목(영화 / 에피소드)을 찾는다.
- 규칙을 처음 켤 때는 지금 있는 것은 올리지 않고 기준만 잡는다 (그 뒤로 들어온 것부터).
- 파일은 Plex 서버가 이 컴퓨터에 있어 원본 경로가 보이면 그대로, 아니면 Plex에서 임시 폴더로 받은 뒤 올리고 지운다.
- 한 번에 하나씩 올린다. 실패하면 다음 확인 때 다시(3번까지). 올린 것은 기억해 두어 다시 켜도 두 번 올리지 않는다
  (data/plex_to_tg.json).
- 캡션 첫 줄은 "작품 S01E02 - 제목" / "영화 (2026)" 모양이라 YMPlayer 텔레그램 목록에서 제목·회차 묶기가 그대로 된다.
- 텔레그램 일반 계정은 파일 하나 2GB(프리미엄 4GB)까지 - 넘는 파일은 건너뛰고 기록에 남긴다.

설정은 config.json의 "plex_to_tg": {interval, max_gb, rules: [{id, enabled, plex, section, section_title, tg, channel,
channel_title, caption}]}
"""
import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
import urllib.parse

from aiohttp import ClientTimeout

from . import config_store

log = logging.getLogger("ym_player.plex_to_tg")

MAX_RULES = 20
MAX_TRIES = 3
KEEP_DONE = 5000
KEEP_HISTORY = 50
SCAN_SIZE = 50
DEFAULT_CAPTION = "{name}\n\n{summary}"
STREAMABLE = {".mp4", ".m4v", ".mov"}   # 텔레그램 앱에서 받기 전에 바로 재생되는 형식
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
                "caption": str(r.get("caption") or DEFAULT_CAPTION)[:1000]}
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
                "rules": {rid: {"baseline": r.get("baseline", 0), "count": len(r.get("done", []))} for rid, r in st["rules"].items()}}

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
            for m in sorted(items, key=lambda x: int(x.get("addedAt") or 0)):
                key = str(m["ratingKey"])
                if int(m.get("addedAt") or 0) <= rs["baseline"] or key in done or (rule["id"], key) in queued:
                    continue
                if rs.get("fails", {}).get(key, 0) >= MAX_TRIES:
                    continue
                self.queue.append({"rule": rule["id"], "key": key, "title": item_fields(m)["name"]})
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
            ok, note = False, ""
            if rule:
                try:
                    note = await self.upload(rule, job["key"], st["max_gb"])
                    ok = True
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

    async def upload(self, rule, key, max_gb):
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
        size = int(part.get("size") or 0)
        if size > max_gb * 1024 ** 3:
            return f"건너뜀: 파일이 {size / 1024 ** 3:.1f}GB라 텔레그램 한도({max_gb}GB)를 넘습니다."
        name = os.path.basename(str(part.get("file") or "")) or f"{key}.{part.get('container') or 'mp4'}"
        tg = self.tg_for(rule["tg"])
        client = await tg.authorized_client()
        peer = await tg.peer(int(rule["channel"]))
        self.current = {"title": fields["name"], "phase": "준비 중", "done": 0, "total": size}
        tmp = os.path.join(config_store.DATA_DIR, "plex_to_tg_tmp", secrets.token_hex(6))
        os.makedirs(tmp, exist_ok=True)
        try:
            local = str(part.get("file") or "")
            if not (local and os.path.isfile(local) and os.access(local, os.R_OK)):   # Plex가 다른 컴퓨터면 받아 온다
                local = os.path.join(tmp, re.sub(r'[\\/:*?"<>|]', "_", name))
                await self._download(cfg, src, part["key"], local, fields["name"], size)
            thumb = await self._thumb(cfg, src, m, tmp)
            from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo
            ext = os.path.splitext(name)[1].lower()
            attrs = [DocumentAttributeVideo(duration=int((m.get("duration") or media.get("duration") or 0) / 1000),
                                            w=int(media.get("width") or 0), h=int(media.get("height") or 0),
                                            supports_streaming=ext in STREAMABLE),
                     DocumentAttributeFilename(name)]
            self.current = {"title": fields["name"], "phase": "텔레그램에 올리는 중", "done": 0, "total": os.path.getsize(local)}

            def progress(done, total):
                if self.current:
                    self.current.update(done=int(done), total=int(total))
            await client.send_file(peer, local, caption=make_caption(rule["caption"], fields), thumb=thumb,
                                   attributes=attrs, supports_streaming=ext in STREAMABLE, force_document=False,
                                   progress_callback=progress)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        log.info("자동 올리기: %s -> %s", fields["name"], rule["channel_title"] or rule["channel"])
        return ""

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

    async def _thumb(self, cfg, src, m, tmp):
        """텔레그램 미리보기 그림 (320px JPEG). 없거나 못 받으면 None (텔레그램이 알아서 만든다)"""
        from .server import plex_headers
        thumb = m.get("thumb") or m.get("grandparentThumb") or m.get("parentThumb")
        if not thumb:
            return None
        url = f"{src['url']}/photo/:/transcode?" + urllib.parse.urlencode(
            {"width": 320, "height": 320, "minSize": 1, "upscale": 0, "format": "jpeg", "url": thumb})
        try:
            async with self.http().get(url, headers=plex_headers(cfg, src.get("token")), timeout=ClientTimeout(total=30),
                                       ssl=None if src.get("verify_ssl", True) else False) as r:
                if r.status != 200:
                    return None
                data = await r.read()
        except Exception:
            return None
        if not data or len(data) > 200 * 1024:
            return None
        path = os.path.join(tmp, "thumb.jpg")
        with open(path, "wb") as f:
            f.write(data)
        return path
