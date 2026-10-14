"""자막 변환(SRT/SMI/ASS/VTT -> WebVTT)과 /api/plex-sub 중계."""
import asyncio

import pytest

from ym_player import config_store, subtitles

SRT = (
    "1\r\n00:00:01,500 --> 00:00:03,000\r\n<font color=red>안녕</font> & <i>반가워</i>\r\n\r\n"
    "2\r\n00:00:04,000 --> 00:00:06,250\r\n{\\an8}둘째\r\n줄\r\n"
)

SMI = """<SAMI><HEAD><STYLE>.KRCC{} .ENCC{}</STYLE></HEAD><BODY>
<SYNC Start=1000><P Class=ENCC>Hello
<SYNC Start=1000><P Class=KRCC>안녕하세요<br>반갑습니다
<SYNC Start=3000><P Class=KRCC>&nbsp;
<SYNC Start=3000><P Class=ENCC>&nbsp;
<SYNC Start=5000><P Class=KRCC><b>마지막</b> &lt;끝&gt;
</BODY></SAMI>"""

ASS = """[Script Info]
Title: x

[V4+ Styles]
Format: Name, Fontname
Style: Default,Arial

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Comment: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,주석은 무시
Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,나중 대사
Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,{\\i1}첫 줄\\N둘째, 쉼표 포함
"""


def cues(vtt):
    """WEBVTT 본문을 [(시작-끝, 대사)]로."""
    blocks = vtt.strip().split("\n\n")
    assert blocks[0] == "WEBVTT"
    out = []
    for b in blocks[1:]:
        lines = b.split("\n")
        out.append((lines[0], "\n".join(lines[1:])))
    return out


def test_srt_times_and_markup():
    assert cues(subtitles.to_vtt(SRT.encode("utf-8"))) == [
        ("00:00:01.500 --> 00:00:03.000", "안녕 &amp; <i>반가워</i>"),
        ("00:00:04.000 --> 00:00:06.250", "둘째\n줄"),
    ]


def test_smi_cp949_picks_korean_class_and_keeps_literal_brackets():
    got = cues(subtitles.to_vtt(SMI.encode("cp949")))
    assert got == [
        # &nbsp; SYNC가 앞 대사의 끝, 마지막 대사는 5초
        ("00:00:01.000 --> 00:00:03.000", "안녕하세요\n반갑습니다"),
        ("00:00:05.000 --> 00:00:10.000", "<b>마지막</b> &lt;끝&gt;"),
    ]
    english = cues(subtitles.smi_to_vtt(SMI, prefer_class="ENCC"))
    assert english == [("00:00:01.000 --> 00:00:03.000", "Hello")]


def test_ass_dialogue_with_commas_sorted_and_overrides_removed():
    assert cues(subtitles.to_vtt(ASS.encode("utf-8"), "ass")) == [
        ("00:00:01.000 --> 00:00:02.500", "첫 줄\n둘째, 쉼표 포함"),
        ("00:00:03.000 --> 00:00:04.000", "나중 대사"),
    ]


def test_vtt_passthrough_and_utf16():
    vtt = "WEBVTT\n\n00:01.000 --> 00:02.000\nhi\n"
    assert subtitles.to_vtt(vtt.encode("utf-16")) == vtt
    # Plex가 vtt라고 했는데 머리가 없으면 붙여 준다
    assert subtitles.to_vtt(b"00:01.000 --> 00:02.000\nhi\n", "vtt").startswith("WEBVTT\n\n")


def test_detects_format_from_content_when_codec_unknown():
    assert "안녕하세요" in subtitles.to_vtt(SMI.encode("utf-8"), "")
    assert "나중 대사" in subtitles.to_vtt(ASS.encode("utf-8"), "")


def test_plex_subtitle_endpoint(monkeypatch, tmp_path):
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from ym_player import server

    monkeypatch.setattr(config_store, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "DATA_DIR", str(tmp_path))
    seen = {}

    async def stream(request):
        seen["token"] = request.headers.get("X-Plex-Token")
        return web.Response(body=SMI.encode("cp949"), content_type="application/octet-stream")

    async def run():
        origin = web.Application()
        origin.router.add_get("/library/streams/{id}", stream)
        origin_srv = TestServer(origin)
        await origin_srv.start_server()
        cfg = config_store.load()
        config_store.replace_sources(cfg, [
            {"id": "px", "type": "plex", "name": "집", "url": str(origin_srv.make_url("")).rstrip("/"), "token": "TOK"}])
        config_store.save(cfg)
        client = TestClient(TestServer(server.create_app()))
        await client.start_server()
        try:
            r = await client.get("/api/plex-sub/px", params={"path": "/library/streams/31", "codec": "smi"})
            assert r.status == 200 and r.content_type == "text/vtt"
            body = await r.text()
            assert body.startswith("WEBVTT") and "안녕하세요" in body
            assert seen["token"] == "TOK"
            # 자막 스트림 경로가 아니면 막는다
            assert (await client.get("/api/plex-sub/px", params={"path": "/library/sections"})).status == 400
            assert (await client.get("/api/plex-sub/px", params={"path": "/library/streams/../sections"})).status == 400
            assert (await client.get("/api/plex-sub/nope", params={"path": "/library/streams/1"})).status == 404
        finally:
            await client.close()
            await origin_srv.close()

    asyncio.run(run())
