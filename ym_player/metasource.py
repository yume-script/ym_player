"""TMDB에서 못 찾은 작품 정보를 다른 곳에서: 애니는 AniList, 그래도 없으면 TV 작품(한국 드라마·예능)은 한국어 위키백과.

돌려주는 값: (정보 또는 None, 실패했나) - 실패(요청 제한·연결)는 '못 찾음'으로 적지 않고 다음에 다시 물어보게.
"""
import logging

from . import anilist, wiki

log = logging.getLogger("ym_player.metasource")


async def fallback(http, target):
    failed = False
    try:
        data = await anilist.find(http, target["titles"], target.get("year") or "", target["kind"])
    except Exception as e:  # noqa: BLE001
        log.info("AniList 실패 (%s): %s", target.get("mkey"), e)
        data, failed = None, True
    if data:
        return data, False
    if target.get("kind") == "tv":   # 한국 드라마·예능 (영화는 TMDB가 충분하다)
        try:
            data = await wiki.find(http, target["titles"], "tv")
        except Exception as e:  # noqa: BLE001
            log.info("위키백과 실패 (%s): %s", target.get("mkey"), e)
            data, failed = None, True
        if data:
            return data, False
    return None, failed
