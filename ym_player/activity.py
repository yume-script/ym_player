"""재생 현황 - 지금 재생 중인 스트림 수만 센다 (GET /api/activity, 부하 조절 봇용).

재생으로 세는 요청 (썸네일·목록·편성표 따위는 세지 않는다):
    live      /api/stream                                   (IPTV·라디오 중계)
    telegram  /api/tg/{sid}/stream/…, /api/tg/{sid}/transcode/…, /api/tg-hls/… (책 보기의 ?doc=1 받기는 빼고)
    plex      /api/plex/{sid}/…/library/parts/…, /api/plex/{sid}/video|music/:/transcode/… (photo/:/transcode 썸네일은 빼고)
스트림 하나 = (종류, 누가, 무엇을). 같은 영상의 Range 조각·HLS 조각 요청은 한 스트림으로 묶인다.
재생 중으로 보는 기준:
    - 연결이 끝나지 않은 긴 스트림(mpegts 등)은 연결이 열려 있는 동안
    - 조각으로 받는 재생(HLS, 조각 받기)은 마지막 요청이 끝난 뒤 WINDOW(45초) 동안
      (WINDOW보다 오래 열려 있던 연결이 끝나면 긴 스트림이 끝난 것으로 보고 바로 뺀다)
버벅임 (재생기가 POST /api/activity/report로 알린다 - 서버는 버벅임을 볼 수 없어서):
    stall   재생 중에 버퍼가 바닥나 멈춘 횟수 (처음 불러오기·탐색 직후는 빼고)
    retry   끊겨서 다시 연결한 횟수 (자동 재연결, 서버 중계로 바꾸기, 변환 다시 시작, 미디어 오류 복구)
최근 EVENT_WINDOW(5분) 동안의 횟수를 종류별로 내보낸다.
제목·사용자 이름·채널명은 내보내지 않는다 (숫자만).
"""
import collections
import re
import time

from aiohttp import web

WINDOW = 45
KINDS = ("plex", "telegram", "live")
EVENTS = ("stall", "retry")
EVENT_WINDOW = 300
REPORT_LIMIT = 30      # 사용자 한 명이 1분에 알릴 수 있는 횟수 (잘못된 화면이 숫자를 부풀리지 않게)

_TG_RE = re.compile(r"^/api/tg/[^/]+/(stream|transcode)/(-?\d+)/(\d+)$")
_TG_HLS_RE = re.compile(r"^/api/tg-hls/([^/]+)/")
_PLEX_PART_RE = re.compile(r"^/api/plex/[^/]+/(?:.*/)?library/parts/(\d+)")
_PLEX_TRANSCODE_RE = re.compile(r"^/api/plex/[^/]+/(?:video|music|audio)/:/transcode/")   # (photo/:/transcode는 썸네일)
_PLEX_SESSION_RE = re.compile(r"/session/([^/]+)/")


def classify(path, query):
    """(종류, 무엇을) 또는 재생 요청이 아니면 None"""
    if path == "/api/stream":
        return "live", ""          # 한 사람이 동시에 보는 라이브는 하나로 (HLS 중계는 조각마다 주소가 달라서)
    m = _TG_RE.match(path)
    if m and not query.get("doc"):   # (책 보기의 PDF/EPUB 받기는 ?doc=1 - 재생이 아님)
        return "telegram", f"{m.group(2)}/{m.group(3)}"
    m = _TG_HLS_RE.match(path)
    if m:
        return "telegram", f"hls/{m.group(1)}"
    if path.startswith("/api/plex/"):
        m = _PLEX_PART_RE.match(path)
        if m:
            return "plex", f"part/{m.group(1)}"
        if _PLEX_TRANSCODE_RE.match(path) and not re.search(r"/(?:stop|ping)$", path):   # (멈춤·살아 있음 알림은 재생이 아님)
            m = _PLEX_SESSION_RE.search(path)
            return "plex", f"transcode/{(m.group(1) if m else '') or query.get('session', '')}"
    return None


class Activity:
    def __init__(self):
        self.streams = {}   # (종류, 누가, 무엇을) -> {"open": 열린 연결 수, "last": 조각 요청이 끝난 시각}
        self.events = {(e, k): collections.deque() for e in EVENTS for k in KINDS}   # 버벅임 시각들
        self.reports = {}   # 누가 -> 최근 1분 알림 시각들

    def begin(self, key):
        e = self.streams.setdefault(key, {"open": 0, "last": 0.0})
        e["open"] += 1
        return time.monotonic()

    def end(self, key, started, ok=True):
        e = self.streams.get(key)
        if not e:
            return
        e["open"] = max(0, e["open"] - 1)
        now = time.monotonic()
        # 짧은 요청(조각)이면 WINDOW 동안 재생 중으로, 오래 열려 있던 연결이 끝났거나 실패했으면 바로 끝
        e["last"] = now if ok and now - started < WINDOW else 0.0

    def report(self, who, event, kind):
        """재생기가 알린 버벅임 하나. 받아들였으면 True (모르는 종류이거나 너무 잦으면 False)"""
        if event not in EVENTS or kind not in KINDS:
            return False
        now = time.monotonic()
        mine = self.reports.setdefault(who, collections.deque())
        while mine and now - mine[0] > 60:
            mine.popleft()
        if len(mine) >= REPORT_LIMIT:
            return False
        mine.append(now)
        self.events[(event, kind)].append(now)
        return True

    def _counts(self, event, now):
        out = {}
        for k in KINDS:
            q = self.events[(event, k)]
            while q and now - q[0] > EVENT_WINDOW:
                q.popleft()
            out[k] = len(q)
        return out

    def snapshot(self):
        now = time.monotonic()
        by = dict.fromkeys(KINDS, 0)
        for key, e in list(self.streams.items()):
            if e["open"] > 0 or now - e["last"] < WINDOW:
                by[key[0]] += 1
            else:
                del self.streams[key]
        for who in [w for w, q in self.reports.items() if not q or now - q[-1] > 60]:
            del self.reports[who]
        return {"playing": sum(by.values()), "by": by, "stalls": self._counts("stall", now),
                "retries": self._counts("retry", now), "window": EVENT_WINDOW}


def middleware(activity, who):
    """재생 요청이면 시작·끝을 기록하는 미들웨어. who(request) -> 누가 (사용자 이름, 없으면 접속 주소)"""
    @web.middleware
    async def track(request, handler):
        hit = classify(request.path, request.query) if request.method == "GET" else None
        if not hit:
            return await handler(request)
        key = (hit[0], who(request), hit[1])
        started = activity.begin(key)
        ok = False
        try:
            resp = await handler(request)   # 스트림 응답은 다 보낼 때까지 여기서 기다린다
            ok = getattr(resp, "status", 200) < 400
            return resp
        finally:
            activity.end(key, started, ok)
    return track
