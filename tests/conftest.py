"""저장소 루트를 import 경로에 넣어 `import ym_player`가 설치 없이 되게 한다."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 화면(브라우저) 테스트(tests/e2e)는 YMP_E2E=1일 때만 - Playwright·Chromium이 필요하고 오래 걸려서 (CI의 e2e 작업)
if not os.environ.get("YMP_E2E"):
    collect_ignore = ["e2e"]

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_anilist(monkeypatch):
    """테스트는 진짜 AniList에 묻지 않는다 (작품 정보 보강 - 결과 없음). AniList를 보는 테스트는 search를 다시 바꾼다"""
    from ym_player import anilist

    async def nothing(http, query):
        return []
    monkeypatch.setattr(anilist, "search", nothing)
