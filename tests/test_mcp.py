"""MCP 서버 - 토큰, JSON-RPC(initialize / tools/list / tools/call), 사용자 제한, 접속 기록 가리기."""
import asyncio
import logging

import pytest

from ym_player import auth, config_store, mcp, telegram_src

pytest.importorskip("aiohttp")


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MP_USER", raising=False)
    monkeypatch.delenv("MP_PASSWORD", raising=False)
    monkeypatch.setenv("MP_UPDATE_CHECK", "0")
    telegram_src._migrated.discard(str(tmp_path))
    auth._fails.clear()
    auth.create_user("boss", "secret1", "admin")
    auth.create_user("kid", "secret2", "user")
    cfg = config_store.load()
    config_store.replace_sources(cfg, [
        {"id": "tg", "type": "telegram", "name": "텔레", "api_id": "1", "api_hash": "h",
         "channels": [{"id": 1, "title": "영화 채널"}, {"id": 2, "title": "웹툰 채널", "kind": "webtoon"}]},
        {"id": "px", "type": "plex", "name": "플렉스", "url": "http://plex.invalid:32400", "token": "T"},
        {"id": "rd", "type": "radio", "name": "라디오", "stations": [
            {"name": "재즈 FM", "url": "http://r.invalid/jazz", "group": "재즈"},
            {"name": "클래식 FM", "url": "http://r.invalid/classic", "group": "클래식"}]},
    ])
    config_store.save(cfg)
    with telegram_src.db() as conn:
        conn.executemany(
            "INSERT INTO videos(source,channel,msg,title,caption,file_name,mime,duration,size,width,height,date,has_thumb,kind)"
            " VALUES('tg',?,?,?,'',?,'video/mp4',?,1,1,1,?,0,?)",
            [(1, 10, "호프 2026", "Hope.2026.mkv", 6000, "2026-10-01T00:00:00+00:00", "video"),
             (1, 11, "다른 영화", "b.mkv", 5400, "2026-10-02T00:00:00+00:00", "video"),
             (2, 20, "호프 웹툰 1화", "호프 웹툰 1화.zip", 0, "2026-10-03T00:00:00+00:00", "book")])
    telegram_src.save_progress("tg", 1, 10, 1200, 6000, user="boss")
    return tmp_path


def run(flow, monkeypatch):
    from aiohttp import CookieJar
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server

    async def fake_plex(app, cfg, src, path, params=None):
        meta = [{"ratingKey": "101", "type": "movie", "title": "호프", "year": 2026, "librarySectionID": 1, "librarySectionTitle": "영화"},
                {"ratingKey": "202", "type": "episode", "title": "1화", "grandparentTitle": "호프 드라마", "parentIndex": 1,
                 "index": 1, "librarySectionID": 2, "librarySectionTitle": "드라마", "viewOffset": 300, "duration": 1200}]
        if path == "/library/sections":
            return {"MediaContainer": {"Directory": [{"key": "1", "title": "영화", "type": "movie"},
                                                     {"key": "2", "title": "드라마", "type": "show"}]}}
        if path == "/hubs/search":
            assert params["query"] == "호프"
            return {"MediaContainer": {"Hub": [{"type": "mixed", "Metadata": meta}]}}
        return {"MediaContainer": {"Metadata": meta}}

    async def fake_now(app, station):
        return {"text": f"Artist - {station['name']} Song", "artist": "Artist", "title": f"{station['name']} Song"}

    monkeypatch.setattr(server, "_plex_json", fake_plex)
    monkeypatch.setattr(server, "_radio_now", fake_now)

    async def main():
        app = server.create_app(require_auth=True)
        client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
        await client.start_server()
        try:
            await flow(client)
        finally:
            await client.close()
    asyncio.run(main())


async def rpc(c, token, method, params=None, mid=1, path=None):
    body = {"jsonrpc": "2.0", "method": method, **({"id": mid} if mid is not None else {}), **({"params": params} if params else {})}
    headers = {} if path else {"Authorization": f"Bearer {token}"}
    r = await c.post(path or "/mcp", json=body, headers=headers)
    return r.status, (await r.json() if r.status == 200 else None)


async def tool(c, token, name, args=None):
    status, resp = await rpc(c, token, "tools/call", {"name": name, "arguments": args or {}})
    assert status == 200 and "result" in resp, resp
    return resp["result"]["structuredContent"]


def test_token_store(data):
    raw, rec = mcp.create_token("내 Claude", "boss")
    assert raw.startswith("ymp_") and rec["hint"] == raw[-4:] and rec["user"] == "boss"
    assert raw not in (data / "mcp_tokens.json").read_text()        # 원문은 저장하지 않음
    assert mcp.find_token(raw)["id"] == rec["id"]
    assert mcp.find_token(raw + "x") is None and mcp.find_token("") is None
    mcp.forget_user("BOSS")
    assert mcp.find_token(raw) is None


def test_mcp_protocol_tools_and_limits(data, monkeypatch):
    async def flow(c):
        # 관리자가 화면에서 토큰을 만든다 (사용자 kid 몫도)
        r = await c.post("/api/auth/login", json={"username": "boss", "password": "secret1"})
        assert r.status == 200
        made = await (await c.post("/api/admin/mcp", json={"name": "Claude", "user": "boss"})).json()
        token = made["token"]
        assert made["url"].endswith("/mcp") and made["url_with_token"].endswith(f"/mcp/{token}")
        kid_token = (await (await c.post("/api/admin/mcp", json={"name": "아이", "user": "kid"})).json())["token"]
        listed = (await (await c.get("/api/admin/mcp")).json())["tokens"]
        assert {t["user"] for t in listed} == {"boss", "kid"} and all("hash" not in t for t in listed)
        await c.post("/api/auth/logout")

        # 토큰이 없거나 틀리면 401 (로그인 쿠키로는 안 됨)
        assert (await rpc(c, "", "ping"))[0] == 401
        assert (await rpc(c, "ymp_wrong", "ping"))[0] == 401
        assert (await c.get("/mcp", headers={"Authorization": f"Bearer {token}"})).status == 405

        status, resp = await rpc(c, token, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                                          "clientInfo": {"name": "t", "version": "1"}})
        assert resp["result"]["protocolVersion"] == "2025-03-26" and resp["result"]["serverInfo"]["name"] == "ymplayer"
        r = await c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                         headers={"Authorization": f"Bearer {token}"})
        assert r.status == 202
        _, resp = await rpc(c, token, "tools/list")
        assert {t["name"] for t in resp["result"]["tools"]} == {"get_status", "list_libraries", "search", "continue_watching",
                                                                 "recently_added", "radio_now_playing"}
        _, resp = await rpc(c, token, "nope/x")
        assert resp["error"]["code"] == -32601
        # 주소에 토큰을 넣는 방식도
        status, resp = await rpc(c, None, "ping", path=f"/mcp/{token}")
        assert status == 200 and resp["result"] == {}

        st = await tool(c, token, "get_status")
        assert st["user"] == "boss" and {s["id"] for s in st["sources"]} == {"tg", "px", "rd"}
        found = await tool(c, token, "search", {"query": "호프"})
        got = {(x["source"], x["type"], x["title"]) for x in found["results"]}
        assert ("텔레", "video", "호프 2026") in got and ("텔레", "webtoon", "호프 웹툰 1화") in got
        assert ("플렉스", "movie", "호프") in got and ("플렉스", "episode", "호프 드라마 S1E1 1화") in got
        hope = next(x for x in found["results"] if x["title"] == "호프 2026")
        assert hope["url"].endswith("/#/item/tg/1:10") and hope["progress"] == 0.2
        cont = await tool(c, token, "continue_watching")
        assert [x["title"] for x in cont["items"] if x["source"] == "텔레"] == ["호프 2026"]
        libs = await tool(c, token, "list_libraries")
        assert {(x["source"], x["library"]) for x in libs["libraries"]} >= {("플렉스", "영화"), ("텔레", "웹툰 채널"), ("라디오", "재즈")}
        radio = await tool(c, token, "radio_now_playing", {"query": "재즈"})
        assert [(s["title"], s["now_playing"]) for s in radio["stations"]] == [("재즈 FM", "Artist - 재즈 FM Song")]
        recent = await tool(c, token, "recently_added", {"source": "tg", "limit": 5})
        assert recent["items"][0]["title"] == "호프 웹툰 1화"
        _, resp = await rpc(c, token, "tools/call", {"name": "search", "arguments": {"query": ""}})
        assert resp["error"]["code"] == -32602

        # 볼 수 있는 범위가 정해진 사용자: 텔레그램 영화 채널과 Plex 드라마만
        auth.update_user("kid", access={"sources": ["tg", "px"], "libraries": {"tg": ["1"], "px": ["2"]}}, acting="boss")
        found = await tool(c, kid_token, "search", {"query": "호프"})
        assert {(x["source"], x["title"]) for x in found["results"]} == {("텔레", "호프 2026"), ("플렉스", "호프 드라마 S1E1 1화")}
        st = await tool(c, kid_token, "get_status")
        assert {s["id"] for s in st["sources"]} == {"tg", "px"}
        cont = await tool(c, kid_token, "continue_watching")   # kid은 텔레그램 기록 없음, Plex는 드라마만
        assert [(x["source"], x["title"]) for x in cont["items"]] == [("플렉스", "호프 드라마 S1E1 1화")]
    run(flow, monkeypatch)


def test_access_log_hides_token():
    from ym_player import server  # noqa: F401 - 필터를 붙인다
    rec = logging.LogRecord("aiohttp.access", logging.INFO, "", 0, '1.2.3.4 "POST /mcp/ymp_abcDEF-12_x HTTP/1.1" 200', None, None)
    for f in logging.getLogger("aiohttp.access").filters:
        f.filter(rec)
    assert "ymp_abc" not in rec.msg and "/mcp/***" in rec.msg
