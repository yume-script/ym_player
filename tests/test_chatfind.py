"""IPTV 채팅 패널 주소 찾기 - 공지 채널 -> 사이트 -> 채팅 주소, 기억, 내부 주소 막기, 설정 검사."""
import asyncio

import pytest
from aiohttp import web

from ym_player import chatfind, config_store

pytest.importorskip("aiohttp")


def test_parse():
    page = '<p>오늘 주소</p><a href="https://t.me/x">tg</a><a href="https://old.example/live?x=1">옛 주소</a>' \
           '<a href="https://new.example/a/b">새 주소</a><a href="https://old.example/">다시 옛 주소</a>'
    assert chatfind.notice_sites(page, "https://t.me/s/notice") == ["https://old.example/", "https://new.example/"]   # 최신(아래)부터, 텔레그램 빼고
    site = '<iframe src="https://bizchataicloud.com/?token=abc&amp;room=1"></iframe><a href="https://evil.example/?bizchataicloud.com">x</a>'
    assert chatfind.find_chat_url(site, "https://new.example/", "bizchataicloud.com") == "https://bizchataicloud.com/?token=abc&room=1"
    assert chatfind.find_chat_url('<script>var u="https://chat.bizchataicloud.com/r?t=9"</script>', "https://a/", "bizchataicloud.com") == \
        "https://chat.bizchataicloud.com/r?t=9"                                                                       # 하위 호스트도
    assert chatfind.find_chat_url('<iframe src="https://evil.example/?h=bizchataicloud.com">', "https://a/", "bizchataicloud.com") == ""
    assert chatfind._blocked("10.0.0.1") and chatfind._blocked("127.0.0.1") and chatfind._blocked("::ffff:192.168.0.1")
    assert not chatfind._blocked("8.8.8.8")


def test_settings():
    clean = lambda **kw: config_store._clean_source({"type": "iptv", "m3u": "http://x/a.m3u", **kw}, None)
    s = clean(chat_notice="https://t.me/s/n", chat_site="https://site.example/", chat_host="https://BizChatAiCloud.com/path")
    assert s["chat_host"] == "bizchataicloud.com" and s["chat_notice"] == "https://t.me/s/n"
    for bad in ({"chat_site": "ftp://x"}, {"chat_notice": "javascript:x"}, {"chat_host": "a b"}):
        with pytest.raises(ValueError):
            clean(**bad)


def test_find_and_block(monkeypatch):
    hits = {"notice": 0, "site": 0}

    async def notice(request):
        hits["notice"] += 1
        b = f"http://127.0.0.1:{request.url.port}"
        return web.Response(text=f'<a href="https://t.me/x">텔레그램</a><a href="{b}/live/today?x=1">새 주소</a>', content_type="text/html")

    async def site(request):
        hits["site"] += 1
        return web.Response(text=f'<iframe src="http://127.0.0.1:{request.url.port}/chat?token=t{hits["site"]}"></iframe>', content_type="text/html")

    async def to_private(request):
        raise web.HTTPFound("http://10.255.255.1/")

    async def main():
        from aiohttp.test_utils import TestServer
        app = web.Application()
        app.router.add_get("/s/notice", notice)
        app.router.add_get("/", site)          # (공지의 링크는 사이트 첫 화면으로 바꿔서 연다)
        app.router.add_get("/site/", site)
        app.router.add_get("/redir", to_private)
        srv = TestServer(app)
        await srv.start_server()
        base = f"http://127.0.0.1:{srv.port}"
        f = chatfind.ChatFinder()
        try:
            # 내부 주소는 막는다 (IP 숫자, 이름, 리다이렉트 모두)
            monkeypatch.setattr(chatfind, "ALLOW_PRIVATE", False)
            for url in (f"{base}/site/", f"http://localhost:{srv.port}/site/"):
                with pytest.raises(chatfind.ChatFindError, match="내부"):
                    await f._get(url)
            assert hits["site"] == 0
            real = chatfind._blocked                                     # 리다이렉트 목적지도 다시 검사: 가짜 서버(127.0.0.1)는 두고 10.x만 막아서
            monkeypatch.setattr(chatfind, "_blocked", lambda ip: str(ip).startswith("10.") and real(ip))
            with pytest.raises(chatfind.ChatFindError, match="내부"):
                await f._get(f"{base}/redir")
            monkeypatch.setattr(chatfind, "_blocked", real)
            monkeypatch.setattr(chatfind, "ALLOW_PRIVATE", True)        # (가짜 서버가 이 컴퓨터라 여기서만 허용)
            src = {"id": "tv", "chat_notice": f"{base}/s/notice", "chat_site": "", "chat_host": "127.0.0.1"}
            url = await f.find(src)
            assert url == f"{base}/chat?token=t1"
            assert await f.find(src) == url and hits["notice"] == 1      # 10분 기억
            assert await f.find(src, force=True) == f"{base}/chat?token=t2"   # 새로고침: 다시 찾기
            assert await f.find({"id": "x", "chat_url": "https://fixed/"}) == "https://fixed/"
            with pytest.raises(chatfind.ChatFindError, match="찾지 못했"):
                await f.find({"id": "y", "chat_site": f"{base}/site/", "chat_host": "bizchataicloud.com"})
        finally:
            await f.close()
            await srv.close()
    asyncio.run(main())
