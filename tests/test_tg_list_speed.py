"""텔레그램 목록 속도: WAL(쓰는 중에도 읽기), 여러 채널 상태 한 번에, 제목 해석 기억, BL 숨김 판단 재사용"""
import sqlite3
import threading
import time

from ym_player import config_store, telegram_src


def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    telegram_src._migrated.discard(str(tmp_path))


def test_wal_read_while_writing(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    with telegram_src.db() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    telegram_src.save_sync_state("s", 1, newest=5)
    writer = sqlite3.connect(str(tmp_path / "telegram.db"))
    writer.execute("BEGIN EXCLUSIVE")   # 짝짓기 같은 큰 쓰기가 잡고 있는 동안
    writer.execute("DELETE FROM sync_state")
    t0 = time.time()
    st = telegram_src.sync_state("s", 1)   # 읽기는 기다리지 않고 (커밋 전) 예전 값을 본다
    assert time.time() - t0 < 1 and st["newest"] == 5
    writer.rollback()
    writer.close()


def test_sync_states_many(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    telegram_src.save_sync_state("s", 1, newest=7, complete=1)
    got = telegram_src.sync_states("s", [1, 2])
    assert got[1]["newest"] == 7 and got[1]["complete"] == 1
    assert got[2] == {"newest": 0, "oldest": 0, "complete": 0, "latest_at": 0}
    assert telegram_src.sync_states("s", []) == {}


def test_parsers_cached():
    telegram_src._media_key.cache_clear()
    a = telegram_src._media_key("로또 1등도 출근합니다.E09.261008.1080p")
    b = telegram_src._media_key("로또 1등도 출근합니다.E09.261008.1080p")
    assert a == b and a[1] == 9
    assert telegram_src._media_key.cache_info().hits >= 1


def test_bl_checker_reused_until_data_changes(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    calls = []
    real = telegram_src._bl_checker

    def counting(*a):
        calls.append(1)
        return real(*a)
    monkeypatch.setattr(telegram_src, "_bl_checker", counting)
    telegram_src._BL_CACHE.clear()
    with telegram_src.db() as conn:
        telegram_src._bl_function(conn, "s", [1])
        telegram_src._bl_function(conn, "s", [1])
        assert len(calls) == 1
        conn.execute("INSERT INTO videos(source,channel,msg,title,kind) VALUES('s',1,10,'x','book')")
        telegram_src._bl_function(conn, "s", [1])   # 새 회차가 들어오면 다시 계산
        assert len(calls) == 2


def test_relink_one_channel_at_a_time():
    assert isinstance(telegram_src._RELINK_LOCK, type(threading.Lock()))
