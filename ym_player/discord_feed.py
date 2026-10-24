"""디스코드 채널의 새 글 -> 화면 아래쪽에 흐르는 글(ticker.js).

관리자가 설정 > 디스코드에서 봇 토큰과 채널 ID를 넣으면, 서버가 몇십 초마다 디스코드 REST API로
그 채널들의 새 메시지를 받아 메모리에 최근 것만 쥐고 있다. 브라우저는 /api/ticker로 새 것만 받아 간다.

- 봇은 그 채널을 볼 수 있는 서버에 초대되어 있어야 한다 (채널 보기 + 메시지 기록 보기 권한).
- 글 내용을 읽으려면 Discord Developer Portal > Bot에서 MESSAGE CONTENT INTENT를 켜야 한다
  (안 켜면 내용이 빈 채로 와서 "[내용을 읽을 권한 없음]"으로 보인다).
- 처음 켤 때 이미 있던 글은 보여 주지 않는다 (그때부터 새로 올라온 것만).
- 봇 토큰은 브라우저로 내보내지 않는다 (has_token만). 빈 칸으로 저장하면 기존 토큰 유지.

설정은 config.json의 "discord": {enabled, token, channels: ["채널ID", ...], interval, repeat, speed}
"""
import asyncio
import collections
import logging
import re
import time
from datetime import datetime

from aiohttp import ClientError, ClientTimeout

from . import config_store

log = logging.getLogger("ym_player.discord")

API = "https://discord.com/api/v10"
MAX_CHANNELS = 10
KEEP = 200                 # 메모리에 쥐고 있을 최근 글 수
RECENT_SEC = 10 * 60       # 화면을 새로 연 사람에게 보여 줄 "방금 올라온 글" 범위
TEXT_MAX = 400
DEFAULTS = {"enabled": False, "channels": [], "interval": 20, "repeat": 2, "speed": 90}


def clean_settings(raw, old=None):
    raw = raw if isinstance(raw, dict) else {}
    old = old if isinstance(old, dict) else {}
    chans = raw.get("channels", old.get("channels", []))
    if isinstance(chans, str):
        chans = re.split(r"[\s,]+", chans)
    ids = []
    for c in chans or []:
        m = re.search(r"(\d{15,22})\s*$", str(c).strip().rstrip("/"))   # 채널 링크(…/서버ID/채널ID)를 붙여 넣어도
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    if len(ids) > MAX_CHANNELS:
        raise ValueError(f"디스코드 채널은 {MAX_CHANNELS}개까지 넣을 수 있습니다.")

    def num(key, lo, hi):
        try:
            return max(lo, min(hi, int(raw.get(key, old.get(key, DEFAULTS[key])))))
        except (TypeError, ValueError):
            return DEFAULTS[key]
    token = str(raw.get("token") or "").strip()
    if token.lower().startswith("bot "):
        token = token[4:].strip()
    out = {"enabled": bool(raw.get("enabled", old.get("enabled", False))), "channels": ids,
           "interval": num("interval", 10, 600), "repeat": num("repeat", 1, 5), "speed": num("speed", 30, 300)}
    token = token[:200] or old.get("token", "")
    if token:
        out["token"] = token
    return out


def public_settings(st):
    st = clean_settings(st)
    return {**{k: v for k, v in st.items() if k != "token"}, "has_token": bool(st.get("token"))}


# ---------------------------------------------------------------------------
# 글 다듬기 (디스코드 표기 -> 읽을 수 있는 글)
# ---------------------------------------------------------------------------
_MD_RE = re.compile(r"(\*\*\*|\*\*|__|~~|\|\||`{1,3})")


def _when(ts, style):
    try:
        d = datetime.fromtimestamp(int(ts)).astimezone()
    except (ValueError, OverflowError, OSError):
        return ""
    return d.strftime("%H:%M") if style in ("t", "T") else d.strftime("%Y-%m-%d %H:%M")


def message_text(m, channel_names=None):
    """디스코드 메시지 dict -> 한 줄 글"""
    names = {u["id"]: (u.get("global_name") or u.get("username") or "") for u in m.get("mentions") or [] if u.get("id")}
    text = str(m.get("content") or "")
    text = re.sub(r"<@!?(\d+)>", lambda x: "@" + (names.get(x.group(1)) or "누군가"), text)
    text = re.sub(r"<#(\d+)>", lambda x: "#" + ((channel_names or {}).get(x.group(1)) or "채널"), text)
    text = re.sub(r"<@&\d+>", "@역할", text)
    text = re.sub(r"<a?:(\w+):\d+>", r":\1:", text)
    text = re.sub(r"<t:(-?\d+)(?::([tTdDfFR]))?>", lambda x: _when(x.group(1), x.group(2) or "f"), text)
    text = re.sub(r"<(https?://[^>\s]+)>", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1", text)   # [글](주소) -> 글
    text = re.sub(r"^\s*(#{1,3}|-#|>{1,3})\s+", "", text, flags=re.M)     # 머리글·작은 글·인용 표시
    text = _MD_RE.sub("", text)
    extra = []
    for e in m.get("embeds") or []:
        part = " - ".join(x for x in (str(e.get("title") or "").strip(), str(e.get("description") or "").strip()) if x)
        if part and part not in text:
            extra.append(part)
    files = m.get("attachments") or []
    if files:
        pics = sum(1 for a in files if str(a.get("content_type") or "").startswith("image/"))
        other = [a.get("filename") or "파일" for a in files if not str(a.get("content_type") or "").startswith("image/")]
        if pics:
            extra.append(f"[사진{f' {pics}장' if pics > 1 else ''}]")
        extra += [f"[파일 {n}]" for n in other[:3]]
    if m.get("sticker_items"):
        extra.append(f"[스티커 {m['sticker_items'][0].get('name', '')}]".replace(" ]", "]"))
    text = " ".join(x for x in [text, *extra] if x)
    text = re.sub(r"\s+", " ", text).strip()
    if not text and m.get("type", 0) in (0, 19):
        text = "[내용을 읽을 권한 없음 - 봇의 MESSAGE CONTENT INTENT를 켜 주세요]"
    return text[:TEXT_MAX] + ("…" if len(text) > TEXT_MAX else "")


def _author(m):
    a = m.get("author") or {}
    member = m.get("member") or {}
    return str(member.get("nick") or a.get("global_name") or a.get("username") or "")[:60]


# ---------------------------------------------------------------------------
# 받아 오기
# ---------------------------------------------------------------------------
class DiscordError(Exception):
    pass


class Feed:
    def __init__(self):
        self.items = collections.deque(maxlen=KEEP)   # {seq, channel, author, text, ts}
        self.seq = 0
        self.boot = int(time.time() * 1000)   # 서버를 다시 켜면 seq가 처음부터 -> 브라우저가 알아채게
        self.last = {}          # 채널 ID -> 마지막으로 본 메시지 ID (처음 한 번은 "지금"으로 맞춤)
        self.names = {}         # 채널 ID -> "#이름"
        self.status = {"ok": False, "error": "", "checked": 0, "channels": {}}
        self._wake = asyncio.Event()
        self._key = None

    def settings(self):
        return clean_settings(config_store.load().get("discord"))

    def reconfigure(self):
        """설정을 바꾸면 바로 다시 (채널이 바뀌면 그 채널은 처음부터 "지금"으로)"""
        self._wake.set()

    async def loop(self, http):
        while True:
            st = self.settings()
            key = (st.get("token"), tuple(st["channels"]))
            if key != self._key:   # 토큰·채널이 바뀌면 기억을 비운다
                self._key = key
                self.last = {c: v for c, v in self.last.items() if c in st["channels"]}
                self.status = {"ok": False, "error": "", "checked": 0, "channels": {}}
            if st["enabled"] and st.get("token") and st["channels"]:
                try:
                    await self.poll(http, st)
                    self.status.update(ok=True, error="")
                except DiscordError as e:
                    self.status.update(ok=False, error=str(e))
                except Exception as e:   # 인터넷 끊김 따위 - 다음 차례에 다시
                    log.warning("디스코드 확인 실패: %s", e)
                    self.status.update(ok=False, error=f"디스코드에 연결하지 못했습니다: {e}")
                self.status["checked"] = int(time.time())
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), st["interval"] if st["enabled"] else 3600)
            except asyncio.TimeoutError:
                pass

    async def _get(self, http, st, path, params=None):
        headers = {"Authorization": f"Bot {st['token']}", "User-Agent": "YMPlayer (https://github.com/yume-script/ym_player, 1)"}
        for _ in range(3):
            async with http.get(API + path, params=params, headers=headers, timeout=ClientTimeout(total=20)) as r:
                if r.status == 429:   # 너무 자주 - 기다리라는 만큼
                    body = await r.json(content_type=None)
                    await asyncio.sleep(min(30.0, float((body or {}).get("retry_after") or 2)))
                    continue
                if r.status == 401:
                    raise DiscordError("봇 토큰이 맞지 않습니다 (401). Developer Portal > Bot에서 토큰을 다시 복사해 넣어 주세요.")
                if r.status in (403, 404):
                    return r.status, None
                if r.status != 200:
                    raise ClientError(f"HTTP {r.status}")
                return 200, await r.json(content_type=None)
        raise ClientError("요청이 너무 많다고 합니다 (429)")

    async def poll(self, http, st):
        for cid in st["channels"]:
            ch = self.status["channels"].setdefault(cid, {"name": "", "error": ""})
            if cid not in self.names:
                status, info = await self._get(http, st, f"/channels/{cid}")
                if status != 200:
                    ch["error"] = ("봇이 이 채널을 볼 수 없습니다 (봇을 그 서버에 초대하고 채널 보기·메시지 기록 보기 권한을 주세요)"
                                   if status == 403 else "없는 채널입니다 (채널 ID 확인)")
                    continue
                self.names[cid] = f"#{info.get('name') or cid}"
            ch["name"] = self.names[cid]
            if cid not in self.last:   # 처음: 지금 있는 마지막 글부터 (예전 글은 흘리지 않음)
                status, msgs = await self._get(http, st, f"/channels/{cid}/messages", {"limit": 1})
                if status != 200:
                    ch["error"] = "메시지 기록을 볼 권한이 없습니다 (봇에게 메시지 기록 보기 권한을 주세요)"
                    continue
                self.last[cid] = msgs[0]["id"] if msgs else "0"
                ch["error"] = ""
                continue
            status, msgs = await self._get(http, st, f"/channels/{cid}/messages", {"after": self.last[cid], "limit": 50})
            if status != 200:
                ch["error"] = "메시지 기록을 볼 권한이 없습니다 (봇에게 메시지 기록 보기 권한을 주세요)"
                continue
            ch["error"] = ""
            for m in sorted(msgs or [], key=lambda x: int(x["id"])):
                self.last[cid] = m["id"]
                if m.get("type", 0) not in (0, 19, 20):   # 보통 글, 답글, 명령 결과만 (입장·고정 알림은 빼고)
                    continue
                text = message_text(m, {c: n[1:] for c, n in self.names.items()})
                if text:
                    self.add(self.names[cid], _author(m), text)

    def add(self, channel, author, text, ts=None):
        self.seq += 1
        self.items.append({"seq": self.seq, "channel": channel, "author": author, "text": text, "ts": ts or time.time()})

    def since(self, after=None):
        """after(seq)보다 새 글. after가 없으면 방금(10분 안) 올라온 것 몇 개"""
        if after is None:
            now = time.time()
            out = [x for x in self.items if now - x["ts"] <= RECENT_SEC][-5:]
        else:
            out = [x for x in self.items if x["seq"] > after][-50:]
        return out
