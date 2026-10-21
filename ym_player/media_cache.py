"""동영상 조각 캐시 + 앞쪽 미리 받기 (텔레그램, Plex 원본 재생).

파일을 512KB 조각(block)으로 나눠 다룬다.
- 본 조각은 디스크(<캐시 폴더>/<키 해시>/<번호>.blk)에 남긴다. 다시 보거나 이미 본 곳으로 탐색하면 원본에 안 간다.
  용량이 넘치면 가장 오래 안 본 영상부터 통째로 지운다.
- 재생 중에는 지금 위치보다 몇 분 앞까지를 뒤에서 여러 조각씩 동시에 받아 둔다. 텔레그램은 요청 하나에
  왕복 시간이 걸려서, 한 줄로 받는 것보다 동시에 받는 쪽이 훨씬 빠르고 끊김도 줄어든다.
- 같은 조각을 여러 요청(브라우저, ffprobe, ffmpeg, 미리 받기)이 동시에 원하면 한 번만 받는다.
- 캐시를 꺼도 미리 받은 조각은 메모리(최근 것 일부)에 두고 같은 방식으로 쓴다.

캐시 키: "tg:<소스>:<채널>:<메시지>", "plex:<소스>:<파트 경로>" - 소스를 지우면 그 접두어로 지운다.
"""
import asyncio
import hashlib
import json
import logging
import os
import shutil
import threading
import time
from collections import OrderedDict

from . import config_store

log = logging.getLogger("ym_player.cache")

BLOCK = 512 * 1024
RAM_BLOCKS = 256            # 메모리에 둘 최근 조각 (약 128MB)
STREAMERS_MAX = 64          # 기억해 둘 파일별 상태
TOUCH_EVERY_SEC = 60

DEFAULTS = {"enabled": True, "dir": "", "max_gb": 20.0, "ahead_min": 3}


def clean_settings(raw):
    """설정 화면에서 온 캐시 설정 정리."""
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    out["enabled"] = bool(raw.get("enabled", DEFAULTS["enabled"]))
    out["dir"] = str(raw.get("dir") or "").strip()
    try:
        out["max_gb"] = min(10000.0, max(0.5, float(raw.get("max_gb", DEFAULTS["max_gb"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["ahead_min"] = min(30, max(0, int(raw.get("ahead_min", DEFAULTS["ahead_min"]))))
    except (TypeError, ValueError):
        pass
    return out


class BlockStore:
    def __init__(self):
        self.settings = dict(DEFAULTS)
        self.ram = OrderedDict()        # (키, 번호) -> bytes
        self.inflight = {}              # (키, 번호) -> Future
        self.entries = None             # 해시 -> {"bytes", "last"} (처음 쓸 때 디스크를 훑어서 채움)
        self.usage = 0
        self._touched = {}
        self._root = None
        self._lock = threading.Lock()   # 디스크 쓰기/지우기는 여러 스레드에서 오므로

    # ----- 설정 -----
    def configure(self, settings):
        self.settings = clean_settings(settings)
        if self.root != self._root:
            # 다른 폴더(또는 다른 데이터 폴더)면 처음부터 다시 센다. 메모리 조각도 그 폴더 것이 아니다
            self._root = self.root
            self.entries = None
            self.ram.clear()

    @property
    def enabled(self):
        return self.settings["enabled"]

    @property
    def root(self):
        return self.settings["dir"] or os.path.join(config_store.DATA_DIR, "cache")

    @property
    def max_bytes(self):
        return int(self.settings["max_gb"] * 1024 ** 3)

    # ----- 디스크 -----
    @staticmethod
    def _hash(key):
        return hashlib.sha1(key.encode("utf-8")).hexdigest()

    def _dir(self, key):
        h = self._hash(key)
        return os.path.join(self.root, h[:2], h)

    def _scan(self):
        """처음 한 번 캐시 폴더를 훑어 영상별 용량과 마지막 사용 시각을 센다."""
        entries, total = {}, 0
        if os.path.isdir(self.root):
            for top in os.scandir(self.root):
                if not top.is_dir():
                    continue
                for ent in os.scandir(top.path):
                    if not ent.is_dir():
                        continue
                    size = sum(f.stat().st_size for f in os.scandir(ent.path) if f.name.endswith(".blk"))
                    entries[ent.name] = {"bytes": size, "last": ent.stat().st_mtime}
                    total += size
        self.entries, self.usage = entries, total

    def _ensure_scanned(self):
        if self.entries is None or self._root != self.root:
            self._root = self.root
            self._scan()

    def _read_disk(self, key, bi):
        try:
            with open(os.path.join(self._dir(key), f"{bi}.blk"), "rb") as f:
                return f.read()
        except OSError:
            return None

    def _write_disk(self, key, bi, data, meta):
        with self._lock:
            self._write_disk_locked(key, bi, data, meta)

    def _write_disk_locked(self, key, bi, data, meta):
        self._ensure_scanned()
        d = self._dir(key)
        path = os.path.join(d, f"{bi}.blk")
        if os.path.exists(path):
            return
        os.makedirs(d, exist_ok=True)
        info = os.path.join(d, "info.json")
        if not os.path.exists(info):
            with open(info, "w", encoding="utf-8") as f:
                json.dump({"key": key, **(meta or {})}, f, ensure_ascii=False)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        h = os.path.basename(d)
        ent = self.entries.setdefault(h, {"bytes": 0, "last": time.time()})
        ent["bytes"] += len(data)
        ent["last"] = time.time()
        self.usage += len(data)
        if self.usage > self.max_bytes:
            self._evict(keep=h)

    def _evict(self, keep=None):
        """가장 오래 안 본 영상부터 통째로 지워서 최대 용량의 90%까지 줄인다."""
        target = int(self.max_bytes * 0.9)
        for h, ent in sorted(self.entries.items(), key=lambda kv: kv[1]["last"]):
            if self.usage <= target:
                break
            if h == keep:
                continue
            shutil.rmtree(os.path.join(self.root, h[:2], h), ignore_errors=True)
            self.usage -= ent["bytes"]
            self.entries.pop(h, None)

    def _touch(self, key):
        """마지막 사용 시각 (지울 순서용). 자주 쓰지 않게 1분에 한 번만."""
        now = time.time()
        if now - self._touched.get(key, 0) < TOUCH_EVERY_SEC:
            return
        self._touched[key] = now
        h = self._hash(key)
        if self.entries is not None and h in self.entries:
            self.entries[h]["last"] = now
            try:
                os.utime(self._dir(key))
            except OSError:
                pass

    # ----- 조각 -----
    def _ram_put(self, key, bi, data):
        self.ram[(key, bi)] = data
        self.ram.move_to_end((key, bi))
        while len(self.ram) > RAM_BLOCKS:
            self.ram.popitem(last=False)

    async def lookup(self, key, bi):
        hit = self.ram.get((key, bi))
        if hit is not None:
            self.ram.move_to_end((key, bi))
            return hit
        if self.enabled:
            data = await asyncio.to_thread(self._read_disk, key, bi)
            if data is not None:
                self._ram_put(key, bi, data)
                await asyncio.to_thread(self._touch, key)
                return data
        return None

    async def get(self, key, bi, fetch, meta=None):
        """조각 하나. 캐시에 없으면 fetch(bi)로 받아서 남긴다. 같은 조각을 동시에 원하면 한 번만 받는다."""
        data = await self.lookup(key, bi)
        if data is not None:
            return data
        fut = self.inflight.get((key, bi))
        if fut is None:
            fut = self.inflight[(key, bi)] = asyncio.ensure_future(self._fetch(key, bi, fetch, meta))
            fut.add_done_callback(lambda _f: self.inflight.pop((key, bi), None))
        return await asyncio.shield(fut)

    async def _fetch(self, key, bi, fetch, meta):
        data = await fetch(bi)
        self._ram_put(key, bi, data)
        if self.enabled:
            try:
                await asyncio.to_thread(self._write_disk, key, bi, data, meta)
            except OSError as e:
                log.warning("캐시에 쓰지 못했습니다 (%s): %s", self.root, e)
        return data

    def has_cached_sync(self, key, bi):
        return (key, bi) in self.ram or (self.enabled and os.path.exists(os.path.join(self._dir(key), f"{bi}.blk")))

    # ----- 관리 -----
    def stats(self):
        with self._lock:
            self._ensure_scanned()
        return {"dir": self.root, "bytes": self.usage, "files": len(self.entries), "max_bytes": self.max_bytes,
                "ram_blocks": len(self.ram)}

    def clear(self):
        self.ram.clear()
        with self._lock:
            shutil.rmtree(self.root, ignore_errors=True)
            self.entries, self.usage = {}, 0

    def drop_prefix(self, prefix):
        """소스를 지웠을 때 그 소스의 캐시를 지운다."""
        for k in [k for k in self.ram if k[0].startswith(prefix)]:
            self.ram.pop(k, None)
        with self._lock:
            self._drop_prefix_locked(prefix)

    def _drop_prefix_locked(self, prefix):
        self._ensure_scanned()
        for h in list(self.entries):
            d = os.path.join(self.root, h[:2], h)
            try:
                with open(os.path.join(d, "info.json"), encoding="utf-8") as f:
                    key = json.load(f).get("key", "")
            except (OSError, ValueError):
                continue
            if key.startswith(prefix):
                shutil.rmtree(d, ignore_errors=True)
                self.usage -= self.entries.pop(h)["bytes"]


STORE = BlockStore()


class Streamer:
    """파일 하나의 읽기 + 앞쪽 미리 받기."""

    BURST_BLOCKS = 8                    # 재생 시작/탐색 직후 이만큼은 더 많이 동시에 받는다 (첫 화면을 빨리)
    HIGH_BPS = 2.5 * 1024 * 1024        # 약 20Mbps 넘는 영상(4K 등)은 평소에도 하나 더

    def __init__(self, key, size, fetch, meta=None, bytes_per_sec=0, concurrency=3, burst=0, limiter=None):
        self.key, self.size, self.fetch, self.meta = key, int(size), fetch, meta or {}
        self.bps = bytes_per_sec
        self.concurrency = concurrency + (1 if bytes_per_sec > self.HIGH_BPS else 0)
        self.burst = max(burst, self.concurrency)
        self.limiter = limiter          # 원본 쪽 동시 요청 제한 (텔레그램 전체 공용 등)
        self.last_block = (self.size - 1) // BLOCK
        self._ahead_task = None
        self._anchor = 0
        # 미리 받기에서 [_scan_from, _scan_pos) 조각은 이미 있거나 받는 중인 것으로 확인함 -> 다시 디스크를 뒤지지 않는다
        self._scan_from = self._scan_pos = 0
        self._burst_from = 0            # 이 조각부터 BURST_BLOCKS개는 burst만큼 동시에

    def ahead_blocks(self):
        minutes = STORE.settings["ahead_min"]
        if not minutes:
            return 0
        bps = self.bps or 1024 * 1024  # 모르면 약 8Mbps로 친다
        want = int(bps * minutes * 60)
        return max(8, min(256, want // BLOCK + 1))   # 4MB ~ 128MB

    async def _fetch_limited(self, bi):
        if self.limiter is None:
            return await self.fetch(bi)
        async with self.limiter:
            return await self.fetch(bi)

    async def block(self, bi):
        return await STORE.get(self.key, bi, self._fetch_limited, self.meta)

    async def iter_range(self, start, length):
        """[start, start+length) 바이트를 조각 경계와 상관없이."""
        end = min(self.size, start + length)
        pos = start
        while pos < end:
            bi = pos // BLOCK
            self._prefetch_from(bi + 1)
            data = await self.block(bi)
            piece = data[pos - bi * BLOCK:end - bi * BLOCK]
            if not piece:
                raise IOError("원본에서 조각을 받지 못했습니다.")
            yield piece
            pos += len(piece)

    def _prefetch_from(self, bi):
        """bi부터 앞쪽 N개 조각을 뒤에서 동시에 받아 둔다. 탐색하면 기준점이 바뀌고 새로 채운다."""
        n = self.ahead_blocks()
        if not n or bi > self.last_block:
            return
        if not self._scan_from <= bi <= self._scan_pos:
            # 확인해 둔 구간 밖으로 탐색(또는 처음) -> 거기서부터 다시 확인하고, 처음 몇 조각은 몰아서 받는다
            self._scan_from = self._scan_pos = bi
            self._burst_from = bi
        else:
            self._scan_from = bi            # 재생이 앞으로 간 만큼 - 그 사이는 이미 확인함
        self._anchor = bi
        if self._ahead_task is None or self._ahead_task.done():
            self._ahead_task = asyncio.ensure_future(self._ahead_loop())

    def _limit(self, bi):
        return self.burst if bi < self._burst_from + self.BURST_BLOCKS else self.concurrency

    async def _ahead_loop(self):
        running = set()
        try:
            while True:
                stop = min(self.last_block, self._anchor + self.ahead_blocks() - 1)
                todo = None
                while self._scan_pos <= stop:
                    bi = self._scan_pos
                    self._scan_pos += 1
                    if (self.key, bi) in STORE.inflight:
                        continue
                    if not await asyncio.to_thread(STORE.has_cached_sync, self.key, bi):
                        todo = bi
                        break
                if todo is None and not running:
                    return  # 앞쪽이 다 차 있다 - 다음 읽기 때 다시 (확인한 곳 다음부터)
                if todo is not None:
                    running.add(asyncio.ensure_future(self.block(todo)))
                if todo is None or len(running) >= self._limit(todo):
                    done, running = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                    for t in done:
                        if t.exception() is not None:
                            log.info("미리 받기 중단 (%s): %s", self.key, t.exception())
                            for r in running:
                                r.cancel()
                            self._scan_pos = self._anchor   # 실패한 조각을 다음에 다시
                            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.info("미리 받기 중단 (%s): %s", self.key, e)


_streamers = OrderedDict()


def streamer(key, size, fetch, **kw):
    """같은 파일이면 같은 Streamer (미리 받기 기준점을 공유)."""
    s = _streamers.get(key)
    if s is None or s.size != int(size):
        s = _streamers[key] = Streamer(key, size, fetch, **kw)
    else:
        s.fetch = fetch  # 새 연결/토큰으로
    _streamers.move_to_end(key)
    while len(_streamers) > STREAMERS_MAX:
        _streamers.popitem(last=False)
    return s
