"""빠르게 보이게: 응답 압축(gzip)과 느린 요청 측정.

- compress_middleware: 브라우저가 gzip을 받으면 JSON·HTML·글자 응답(1KB 넘는 것)을 압축해서 보낸다.
  밖(LTE)에서 열 때 받는 양이 보통 70% 정도 준다. 영상·그림·스트림(Range, 변환)은 그대로.
- static_gzip: 지문이 붙은 화면 파일(/s/{지문}/js/app.js 등)은 압축본을 한 번 만들어 기억해 두고 준다 (요청마다 압축하지 않게).
- Timings: 경로(라우트)마다 걸린 시간을 모아 설정 > 상태의 "느린 요청 TOP 10"으로 보여 준다
  (스트림처럼 오래 이어지는 응답은 빼고, 응답을 다 만들 때까지 걸린 시간만).
"""
import gzip
import os
import time

from aiohttp import web

MIN_BYTES = 1024
_TEXT_TYPES = ("application/json", "application/javascript", "text/", "image/svg+xml", "application/xml",
               "application/vnd.apple.mpegurl", "application/manifest+json")
_STATIC_TYPES = {".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".html": "text/html",
                 ".json": "application/json", ".webmanifest": "application/manifest+json", ".mjs": "application/javascript"}


def accepts_gzip(request):
    return "gzip" in request.headers.get("Accept-Encoding", "").lower()


def compressible(resp):
    ctype = (resp.content_type or "").lower()
    return (type(resp) is web.Response and not resp.prepared and resp.body is not None
            and isinstance(resp.body, (bytes, bytearray)) and len(resp.body) >= MIN_BYTES
            and "Content-Encoding" not in resp.headers and ctype.startswith(_TEXT_TYPES))


class Timings:
    """라우트마다 {횟수, 합계ms, 최대ms, 최대였던 때, 1초 넘은 횟수}"""

    def __init__(self):
        self.by_route = {}
        self.since = time.time()

    def add(self, route, ms):
        r = self.by_route.get(route)
        if r is None:
            if len(self.by_route) > 500:   # (경로가 너무 많아지면 처음부터)
                self.by_route.clear()
            r = self.by_route[route] = {"route": route, "count": 0, "total_ms": 0.0, "max_ms": 0.0, "max_at": 0, "slow": 0}
        r["count"] += 1
        r["total_ms"] += ms
        if ms > r["max_ms"]:
            r["max_ms"], r["max_at"] = ms, int(time.time())
        if ms >= 1000:
            r["slow"] += 1

    def top(self, n=10):
        rows = sorted(self.by_route.values(), key=lambda r: r["total_ms"] / r["count"], reverse=True)[:n]
        return [{"route": r["route"], "count": r["count"], "avg_ms": round(r["total_ms"] / r["count"]),
                 "max_ms": round(r["max_ms"]), "max_at": r["max_at"], "slow": r["slow"]} for r in rows]


def _route_name(request):
    try:
        res = request.match_info.route.resource
        name = res.canonical if res is not None else request.path
    except Exception:
        name = request.path
    return f"{request.method} {name}"


# 측정에서 뺄 것: 화면 파일·그림·영상 (크기나 회선에 따라 걸리는 시간이라 서버가 느린 것과 다르다)
_SKIP_PREFIX = ("/s/", "/static/", "/brand/")


def middleware(timings):
    @web.middleware
    async def compress_and_time(request, handler):
        t0 = time.monotonic()
        resp = await handler(request)
        try:
            if compressible(resp) and accepts_gzip(request):
                resp.enable_compression(web.ContentCoding.gzip)
                resp.headers["Vary"] = "Accept-Encoding"
            # 스트림(Range·변환·HLS 조각)은 이미 다 보낸 뒤라 시간이 재생 길이 - 빼고 만든 시간만
            if not getattr(resp, "prepared", False) and not request.path.startswith(_SKIP_PREFIX):
                timings.add(_route_name(request), (time.monotonic() - t0) * 1000)
        except Exception:
            pass
        return resp
    return compress_and_time


_GZ = {}   # 파일 경로 -> (수정 시각, 크기, 압축본)


def static_gzip(request, path, headers):
    """화면 파일 압축본 응답 (압축할 종류가 아니거나 브라우저가 gzip을 안 받으면 None)"""
    ctype = _STATIC_TYPES.get(os.path.splitext(path)[1].lower())
    if not ctype or not accepts_gzip(request):
        return None
    st = os.stat(path)
    hit = _GZ.get(path)
    if not hit or hit[0] != st.st_mtime or hit[1] != st.st_size:
        with open(path, "rb") as f:
            raw = f.read()
        if len(raw) < MIN_BYTES:
            return None
        hit = _GZ[path] = (st.st_mtime, st.st_size, gzip.compress(raw, 6))
        if len(_GZ) > 300:
            _GZ.pop(next(iter(_GZ)))
    charset = "utf-8" if ctype.startswith(("text/", "application/javascript", "application/json")) else None
    return web.Response(body=hit[2], content_type=ctype, charset=charset,
                        headers={**headers, "Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
