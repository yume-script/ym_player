# -*- coding: utf-8 -*-
"""자동 올리기: 텔레그램이 Telethon이 모르는 형식을 섞어 답할 때(TypeNotFoundError) 이미 올라갔으니 보낸 것으로 (두 번 올라가지 않게)"""
import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ym_player import plex_to_tg  # noqa: E402


class TypeNotFoundError(Exception):
    pass


class Test(unittest.TestCase):
    def test_sent_anyway(self):
        async def boom():
            raise TypeNotFoundError("Could not find a matching Constructor ID for the TLObject ... d49f34c6")

        async def other():
            raise ValueError("x")

        async def ok():
            return "msg"
        self.assertIsNone(asyncio.run(plex_to_tg.sent_anyway(boom(), "a.mp4")))
        self.assertEqual(asyncio.run(plex_to_tg.sent_anyway(ok(), "a.mp4")), "msg")
        with self.assertRaises(ValueError):
            asyncio.run(plex_to_tg.sent_anyway(other(), "a.mp4"))


if __name__ == "__main__":
    unittest.main()
