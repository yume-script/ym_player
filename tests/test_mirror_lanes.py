"""자동 올리기: 계정별 줄(같은 계정은 차례로, 다른 계정은 동시에, FloodWait는 그 계정만), 동시 인코딩 수,
드라마 회차 중복 막기, 취소·건너뛴 항목 다시 올리기, rclone 업로드의 TMDB 포스터."""
import asyncio
import time

import pytest

from ym_player import config_store, plex_to_tg, telegram_src

pytest.importorskip("aiohttp")


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    telegram_src._migrated.discard(str(tmp_path))
    telegram_src._EP_CACHE.clear()
    (tmp_path / "drive").mkdir()
    cfg = {"client_id": "c", "sources": []}
    config_store.replace_sources(cfg, [
        {"id": "tgA", "type": "telegram", "name": "계정A", "api_id": "1", "api_hash": "h", "tmdb_key": "k" * 32, "channels": []},
        {"id": "tgB", "type": "telegram", "name": "계정B", "api_id": "1", "api_hash": "h", "channels": []}])
    folder = str(tmp_path / "drive")
    cfg["plex_to_tg"] = {"rules": [
        {"id": "a", "kind": "folder", "folder": folder, "tg": "tgA", "channel": "-100", "channel_title": "A채널"},
        {"id": "b", "kind": "folder", "folder": folder, "tg": "tgB", "channel": "-200", "channel_title": "B채널"}]}
    config_store.save(cfg)
    return tmp_path


def _mirror():
    return plex_to_tg.Mirror(None, lambda _id: (None, None), lambda _id: None, lambda: None)


class FloodWaitError(Exception):   # telethon의 것과 이름만 같으면 된다 (run_queue는 이름과 seconds로 본다)
    def __init__(self, seconds):
        super().__init__(f"wait {seconds}")
        self.seconds = seconds


def test_accounts_run_side_by_side(data, monkeypatch):
    log = []

    async def main():
        mirror = _mirror()
        flooded = {"A1": 1}

        async def fake_upload(rule, key, max_gb, test=False):
            log.append(("start", key, time.monotonic()))
            if flooded.get(key):
                flooded[key] -= 1
                raise FloodWaitError(1)      # 계정 A만 1초 쉰다
            await asyncio.sleep(0.3)
            log.append(("end", key, time.monotonic()))
            return "ok"
        monkeypatch.setattr(mirror, "upload", fake_upload)
        monkeypatch.setattr(mirror, "_drop_partial", lambda *a: asyncio.sleep(0))
        mirror.queue = [{"rule": "a", "key": "A1", "title": "A1"}, {"rule": "a", "key": "A2", "title": "A2"},
                        {"rule": "b", "key": "B1", "title": "B1"}, {"rule": "b", "key": "B2", "title": "B2"}]
        await asyncio.wait_for(mirror.run_queue(plex_to_tg.load_settings()), 10)
        assert mirror.queue == []
    asyncio.run(main())
    t = {(k, key): at for k, key, at in log}
    starts = [key for k, key, _ in log if k == "start"]
    # 같은 계정은 차례로: A1(제한 후 다시) -> A2, B1 -> B2
    assert t[("start", "A2")] >= t[("end", "A1")] and t[("start", "B2")] >= t[("end", "B1")]
    # 다른 계정은 동시에, 그리고 A가 요청 제한으로 쉬는 동안 B는 끝까지 올렸다
    assert starts[:2] in (["A1", "B1"], ["B1", "A1"])
    assert t[("end", "B2")] < t[("end", "A1")]
    st = plex_to_tg.load_state()
    assert st["accounts"]["tgA"]["ok"] == 2 and st["accounts"]["tgB"]["ok"] == 2
    assert {h["tg"] for h in st["history"]} == {"tgA", "tgB"}


def test_encode_slots(data):
    async def main():
        mirror = _mirror()
        order = []

        async def enc(name):
            await mirror._encode_slot()
            order.append(("in", name))
            await asyncio.sleep(0.2)
            order.append(("out", name))
            mirror._encode_done()
        await asyncio.gather(enc("x"), enc("y"))
        return order
    order = asyncio.run(main())
    assert order[0][0] == "in" and order[1] == ("out", order[0][1])   # 기본 1개 - 하나가 끝나야 다음


def test_history_per_account():
    hist = [{"tg": "A", "n": i} for i in range(80)] + [{"tg": "B", "n": i} for i in range(10)]
    out = plex_to_tg.trim_history(hist)
    assert sum(1 for h in out if h["tg"] == "A") == plex_to_tg.KEEP_HISTORY and sum(1 for h in out if h["tg"] == "B") == 10


def test_episode_key_ignores_tags():
    f = plex_to_tg.folder_watch.file_fields
    a = plex_to_tg.episode_key(f("드라마/작품명.S01E05.1080p.WEB-DL.ST.mkv"))
    b = plex_to_tg.episode_key(f("작품명.S01E05.720p.SW.mp4"))
    c = plex_to_tg.episode_key(f("작품명.E05.2160p.mkv"))
    assert a == b == c and a[1:] == (1, 5)
    assert plex_to_tg.episode_key(f("작품명.S01E06.mkv")) != a
    assert plex_to_tg.episode_key(f("영화 제목 (2024).mkv")) is None


def test_episode_dup_skips(data, monkeypatch):
    with telegram_src.db() as conn:   # 채널에 이미 있던 영상 (자동 올리기 밖에서 올린 것)
        conn.execute("INSERT INTO videos(source,channel,msg,title,caption,file_name,mime,duration,size,width,height,date,has_thumb,kind)"
                     " VALUES('tgA',-100,1,'작품명 S01E03','','작품명.S01E03.1080p.mkv','video/mp4',60,1,1,1,'2026',0,'video')")
    mirror = _mirror()
    rule = {"tg": "tgA", "channel": "-100"}
    f = plex_to_tg.folder_watch.file_fields
    assert "이미 채널에 있음" in mirror._already_ep(rule, f("작품명.S01E03.720p.SW.mp4"))
    assert mirror._already_ep(rule, f("작품명.S01E04.mp4")) == ""
    state = plex_to_tg.load_state()
    mirror._remember_ep(state, rule, "작품명 S01E04")      # 자동 올리기로 올린 것
    plex_to_tg.save_state(state)
    assert "이미 올림" in mirror._already_ep(rule, f("작품명.S01E04.1080p.ST.mkv"))
    assert mirror._already_ep({"tg": "tgB", "channel": "-200"}, f("작품명.S01E04.mkv")) == ""   # 다른 채널은 따로


def test_requeue_skipped(data):
    mirror = _mirror()
    state = plex_to_tg.load_state()
    now = int(time.time())
    rs = state["rules"].setdefault("a", {"done": [], "fails": {}})
    rs["fails"] = {"x/new.mkv": plex_to_tg.GIVE_UP, "x/old.mkv": plex_to_tg.GIVE_UP, "x/작품.S01E02.mkv": plex_to_tg.GIVE_UP,
                   "x/legacy.mkv": plex_to_tg.GIVE_UP}
    rs["skipped"] = {"x/new.mkv": {"title": "new", "at": now - 86400, "note": "건너뜀: 직접 취소"},
                     "x/old.mkv": {"title": "old", "at": now - 20 * 86400, "note": "건너뜀: 직접 취소"},
                     "x/작품.S01E02.mkv": {"title": "작품 S01E02", "at": now - 3600, "note": "건너뜀: 직접 취소"}}
    state["sent_eps"] = {"-100": {plex_to_tg.ep_label(plex_to_tg.episode_key(plex_to_tg.folder_watch.file_fields("작품.S01E02.1080p.mkv"))): "작품 S01E02"}}
    plex_to_tg.save_state(state)
    r = mirror.requeue_skipped(7)
    assert r["queued"] == ["new"] and [x["title"] for x in r["excluded"]] == ["작품 S01E02"]
    assert "같은 회차" in r["excluded"][0]["reason"]
    assert [j["key"] for j in mirror.queue] == ["x/new.mkv"] and mirror.queue[0]["again"]
    assert "x/new.mkv" not in plex_to_tg.load_state()["rules"]["a"]["fails"]
    r = mirror.requeue_skipped(None)                         # 전체: 오래된 것과 예전(기록 없는) 것도
    assert sorted(r["queued"]) == ["legacy", "old"]
    assert mirror.requeue_skipped(30)["queued"] == []        # 이미 줄에 있는 것은 또 넣지 않음


def test_cancel_remembers_skipped(data):
    mirror = _mirror()
    mirror.queue = [{"rule": "a", "key": "x/c.mkv", "title": "c", "sig": "c.mkv|1"}]
    mirror.cancel_item("a", "x/c.mkv")
    sk = plex_to_tg.load_state()["rules"]["a"]["skipped"]["x/c.mkv"]
    assert sk["title"] == "c" and sk["sig"] == "c.mkv|1" and sk["note"] == "건너뜀: 직접 취소"


def test_rclone_poster_from_tmdb(data, monkeypatch):
    from ym_player import tmdb
    calls = {"find": 0}

    class FakeTmdb:
        def __init__(self, http, key):
            self.key = key

        async def find(self, kind, titles, year=""):
            calls["find"] += 1
            if calls.get("down"):
                raise tmdb.TmdbError("연결 안 됨")
            return 42 if "작품명" in titles else None

        async def details(self, kind, tid):
            return {"id": tid, "kind": kind, "title": "작품명", "poster": "https://image.tmdb.org/t/p/w500/p.jpg"}

        async def search(self, kind, q, year=""):
            return [{"id": 7, "kind": kind, "title": "고른 작품", "poster": ""}]
    monkeypatch.setattr(tmdb, "Tmdb", FakeTmdb)

    async def main():
        mirror = _mirror()
        rule = {"tg": "tgA", "channel": "-100"}
        f = plex_to_tg.folder_watch.file_fields
        n1 = await mirror._find_poster(rule, f("작품명.S01E01.mkv")["name"], "작품명.S01E01.mkv")
        n2 = await mirror._find_poster(rule, f("작품명.S01E02.SW.mp4")["name"], "작품명.S01E02.SW.mp4")
        assert n1 == "포스터 찾음 (TMDB)" and n2 == "" and calls["find"] == 1   # 같은 드라마는 처음 찾은 것을 그대로
        # 못 찾음 -> 기억했다가 다시 (이번엔 찾게)
        calls["down"] = True
        n3 = await mirror._find_poster(rule, "다른작품 S01E01", "다른작품.S01E01.mkv")
        assert n3.startswith("포스터 못 찾음 (TMDB 오류")
        miss = mirror._poster_missing(plex_to_tg.load_state())
        assert len(miss) == 1 and miss[0]["tries"] == 1 and miss[0]["at"] > time.time()
        state = plex_to_tg.load_state()
        for p in state["posters"].values():
            p["at"] = 0
        plex_to_tg.save_state(state)
        calls["down"] = False
        await mirror._poster_retries()
        assert len(mirror._poster_missing(plex_to_tg.load_state())) == 1   # TMDB에 없음 -> 다음 차례로
        # 직접 고르기
        mk = miss[0]["mkey"]
        assert (await mirror.search_poster("tgA", "tv", "다른작품"))[0]["id"] == 7
        await mirror.set_poster("tgA", mk, "tv", 7)
        assert mirror._poster_missing(plex_to_tg.load_state()) == []
        assert telegram_src.meta_get("tgA", mk)["manual"]
        # B 계정은 TMDB 키가 없지만 A 계정의 키를 같이 쓴다 (작품명이면 찾음)
        assert await mirror._find_poster({"tg": "tgB", "channel": "-200"}, "작품명 S01E01", "작품명.S01E01.mkv") == "포스터 찾음 (TMDB)"
    asyncio.run(main())
    # 목록 카드: Plex 정보가 없는 영상도 찾아 둔 TMDB 포스터로
    with telegram_src.db() as conn:
        conn.execute("INSERT INTO videos(source,channel,msg,title,caption,file_name,mime,duration,size,width,height,date,has_thumb,kind)"
                     " VALUES('tgA',-100,9,'작품명 S01E01','','작품명.S01E01.mkv','video/mp4',60,1,1,1,'2026',0,'video')")
        got = telegram_src._plex_posters(conn, "tgA", [(-100, 9)])
    assert got == {(-100, 9): "https://image.tmdb.org/t/p/w342/p.jpg"}


def test_poster_backfill(data, monkeypatch):
    from ym_player import tmdb

    class FakeTmdb:
        def __init__(self, http, key):
            pass

        async def find(self, kind, titles, year=""):
            return 1 if any("호프" in t for t in titles) else None

        async def details(self, kind, tid):
            return {"id": tid, "kind": kind, "title": "호프", "poster": "https://image.tmdb.org/t/p/w500/h.jpg"}
    monkeypatch.setattr(tmdb, "Tmdb", FakeTmdb)
    with telegram_src.db() as conn:
        conn.executemany("INSERT INTO videos(source,channel,msg,title,caption,file_name,mime,duration,size,width,height,date,has_thumb,kind)"
                         " VALUES('tgA',-100,?,?,'',?,'video/mp4',60,1,1,1,'2026',0,'video')",
                         [(1, "호프 S01E01", "Hope.S01E01.mkv"), (2, "호프 S01E02", "Hope.S01E02.mkv"), (3, "없는영화 (2020)", "x.mkv")])
    telegram_src.link_series("tgA", -100)

    async def main():
        mirror = _mirror()
        mirror.start_poster_backfill()
        await asyncio.wait_for(mirror._poster_task, 10)
        return mirror.poster_job
    job = asyncio.run(main())
    assert job["total"] == 2 and job["found"] == 1 and job["missing"] == 1 and not job["running"]   # 드라마 회차 둘은 작품 하나


def test_second_telegram_account_shares_api(data):
    """텔레그램 계정을 더 넣을 때 API ID/Hash를 비워 두면 먼저 넣은 계정의 값을 같이 쓴다 (세션 파일은 소스마다 따로)"""
    cfg = config_store.load()
    raw = [{"id": "tgA", "keep": True},
           {"type": "telegram", "name": "둘째", "api_id": "", "api_hash": "", "channels": []},
           {"type": "telegram", "name": "다른 앱", "api_id": "999", "api_hash": "", "channels": []}]
    cfg["sources"] = [{**s, "api_hash": "h"} for s in cfg["sources"]]
    config_store.replace_sources(cfg, raw)
    tg = [s for s in cfg["sources"] if s["type"] == "telegram"]
    assert (tg[1]["api_id"], tg[1]["api_hash"]) == ("1", "h")      # 비워 두면 같이 씀
    assert (tg[2]["api_id"], tg[2]["api_hash"]) == ("999", "")      # 다른 API ID를 적었으면 남의 Hash를 붙이지 않음
    assert tg[0]["id"] != tg[1]["id"]
    assert telegram_src.TelegramSource(tg[0]).session_path != telegram_src.TelegramSource(tg[1]).session_path
    # TMDB 키는 한 계정에만 넣어도 rclone 포스터에 같이 씀
    assert plex_to_tg.Mirror._tmdb_key("tgB") == "k" * 32
