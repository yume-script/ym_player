"""디스코드 새 글 -> 아래쪽 흐르는 글: 설정 다듬기, 글 다듬기, 가짜 디스코드 서버로 받아 오기, /api/ticker."""
import asyncio

import pytest

from ym_player import auth, config_store, discord_feed

pytest.importorskip("aiohttp")


def test_clean_settings_keeps_token_and_parses_channels():
    st = discord_feed.clean_settings({"enabled": True, "token": "Bot abc.def", "interval": 3, "repeat": 9,
                                      "channels": "https://discord.com/channels/111111111111111111/222222222222222222\n333333333333333333, x"})
    assert st == {"enabled": True, "channels": ["222222222222222222", "333333333333333333"], "interval": 10, "repeat": 5,
                  "speed": 90, "token": "abc.def"}
    again = discord_feed.clean_settings({"enabled": False, "token": "", "channels": []}, st)
    assert again["token"] == "abc.def" and again["channels"] == [] and not again["enabled"]
    assert "token" not in discord_feed.public_settings(st) and discord_feed.public_settings(st)["has_token"]
    with pytest.raises(ValueError):
        discord_feed.clean_settings({"channels": [str(10 ** 17 + i) for i in range(11)]})


def test_message_text():
    m = {"content": "**안녕** <@42> <#7> <:pepe:123> <@&9> [링크](https://x.y) `코드`\n둘째 줄",
         "mentions": [{"id": "42", "global_name": "유메"}], "attachments": [{"content_type": "image/png"}, {"filename": "a.zip"}]}
    assert discord_feed.message_text(m, {"7": "공지"}) == "안녕 @유메 #공지 :pepe: @역할 링크 코드 둘째 줄 [사진] [파일 a.zip]"
    assert "MESSAGE CONTENT" in discord_feed.message_text({"content": "", "type": 0})
    assert discord_feed.message_text({"content": "", "embeds": [{"title": "새 영상", "description": "설명"}]}) == "새 영상 - 설명"


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MP_USER", raising=False)
    monkeypatch.delenv("MP_PASSWORD", raising=False)
    monkeypatch.setenv("MP_UPDATE_CHECK", "0")
    auth._fails.clear()
    auth.create_user("boss", "secret1", "admin")
    auth.create_user("kid", "secret2", "user")
    return tmp_path


def test_feed_polls_fake_discord_and_ticker_serves_new_messages(data, monkeypatch):
    from aiohttp import CookieJar, web
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server

    CH = "222222222222222222"
    msgs = [{"id": "100", "type": 0, "content": "예전 글", "author": {"username": "old"}}]
    seen = []

    async def channel(request):
        seen.append(request.headers.get("Authorization"))
        if request.match_info["cid"] != CH:
            return web.json_response({"message": "Missing Access"}, status=403)
        return web.json_response({"id": CH, "name": "알림"})

    async def messages(request):
        after = int(request.query.get("after", 0))
        got = sorted((m for m in msgs if int(m["id"]) > after), key=lambda m: -int(m["id"]))
        return web.json_response(got[:int(request.query.get("limit", 50))])

    fake = web.Application()
    fake.router.add_get("/channels/{cid}", channel)
    fake.router.add_get("/channels/{cid}/messages", messages)

    async def main():
        dsrv = TestServer(fake)
        await dsrv.start_server()
        monkeypatch.setattr(discord_feed, "API", str(dsrv.make_url("")).rstrip("/"))
        app = server.create_app(require_auth=True)
        c = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
        await c.start_server()
        try:
            await c.post("/api/auth/login", json={"username": "boss", "password": "secret1"})
            r = await c.put("/api/admin/discord", json={"enabled": True, "token": "TOKEN", "channels": [CH, "999999999999999999"], "interval": 10})
            body = await r.json()
            assert body["settings"]["has_token"] and "token" not in body["settings"]
            assert body["status"]["channels"][CH]["name"] == "#알림"
            assert "볼 수 없습니다" in body["status"]["channels"]["999999999999999999"]["error"]
            assert seen[0] == "Bot TOKEN"
            first = await (await c.get("/api/ticker")).json()
            assert first["enabled"] and first["items"] == []          # 예전 글은 흘리지 않음
            msgs.append({"id": "101", "type": 0, "content": "새 글 <@5>", "author": {"username": "u", "global_name": "유메"},
                         "mentions": [{"id": "5", "username": "kid"}]})
            msgs.append({"id": "102", "type": 7, "content": "", "author": {"username": "j"}})   # 입장 알림은 빼고
            app[server.DISCORD].reconfigure()
            for _ in range(50):
                d = await (await c.get(f"/api/ticker?after={first['latest']}&boot={first['boot']}")).json()
                if d["items"]:
                    break
                await asyncio.sleep(0.05)
            assert [(x["channel"], x["author"], x["text"]) for x in d["items"]] == [("#알림", "유메", "새 글 @kid")]
            # 다음엔 그 뒤 것만, 서버를 다시 켠 것처럼 boot가 다르면 처음처럼
            again = await (await c.get(f"/api/ticker?after={d['latest']}&boot={d['boot']}")).json()
            assert again["items"] == []
            assert len((await (await c.get(f"/api/ticker?after={d['latest']}&boot=1")).json())["items"]) == 1
            await c.post("/api/admin/discord/test")
            assert (await (await c.get(f"/api/ticker?after={d['latest']}&boot={d['boot']}")).json())["items"][0]["channel"] == "#시험"
            # 일반 사용자는 설정을 못 보고, 흐르는 글은 받는다
            await c.post("/api/auth/logout")
            await c.post("/api/auth/login", json={"username": "kid", "password": "secret2"})
            assert (await c.get("/api/admin/discord")).status == 403
            assert (await c.get("/api/ticker")).status == 200
            assert "TOKEN" not in (await (await c.get("/api/config")).text())
        finally:
            await c.close()
            await dsrv.close()
    asyncio.run(main())
