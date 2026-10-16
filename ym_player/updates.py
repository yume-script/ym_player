"""GitHub에 새 버전이 올라왔는지 확인 (관리자 알림용).

저장소의 기본 가지(main)에 있는 ym_player/__init__.py의 __version__과 이 서버의 __version__을 비교한다.
새 버전이면 web/js/credits.js의 CHANGELOG에서 그 사이에 바뀐 것(version이 붙은 줄)도 같이 읽어 온다.
공개 저장소라 토큰 없이 raw.githubusercontent.com에서 파일만 받는다 (업데이트 자체는 관리자가 git pull로).

MP_UPDATE_CHECK=0 이면 확인하지 않는다.
"""
import asyncio
import json
import logging
import os
import re
import time

from aiohttp import ClientTimeout

from . import __version__

log = logging.getLogger("ym_player.updates")

REPO = "yume-script/ym_player"
BRANCH = "main"
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
CHECK_EVERY_SEC = 6 * 3600
FIRST_DELAY_SEC = 60            # 서버를 켜자마자 말고 조금 뒤에
INSTALL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_VERSION_RE = re.compile(r"""^__version__\s*=\s*["']([0-9][0-9.]*)["']""", re.M)
_CHANGELOG_RE = re.compile(r"export const CHANGELOG\s*=\s*(\[.*?\n\]);", re.S)


def enabled():
    return os.environ.get("MP_UPDATE_CHECK", "1").strip().lower() not in ("0", "false", "no", "off")


def parse_version(text):
    m = _VERSION_RE.search(text or "")
    return m.group(1) if m else ""


def version_key(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return ()


def is_newer(remote, local=__version__):
    return bool(version_key(remote)) and version_key(remote) > version_key(local)


def parse_changelog(text):
    """credits.js -> CHANGELOG 목록 (JSON 꼴로 적혀 있음). 못 읽으면 []."""
    m = _CHANGELOG_RE.search(text or "")
    if not m:
        return []
    try:
        items = json.loads(re.sub(r",\s*([\]}])", r"\1", m.group(1)))  # 끝에 남은 쉼표 허용
    except ValueError:
        return []
    return [e for e in items if isinstance(e, dict)]


def changes_between(changelog, local, remote):
    """local보다 새롭고 remote까지인 줄들 (새것이 위)."""
    out = []
    for e in changelog:
        v = str(e.get("version") or "")
        if v and is_newer(v, local) and not is_newer(v, remote):
            out.append({"pr": e.get("pr"), "version": v, "date": str(e.get("date") or ""), "title": str(e.get("title") or "")})
    return out


def update_command():
    return f"cd {INSTALL_DIR} && git pull && sudo systemctl restart ym_player"


class UpdateChecker:
    """마지막 확인 결과를 들고 있다가 /api/admin/update에 돌려준다."""

    def __init__(self):
        self.latest = ""
        self.changes = []
        self.checked_at = 0
        self.error = ""
        self._lock = asyncio.Lock()

    def info(self):
        return {"enabled": enabled(), "current": __version__, "latest": self.latest,
                "newer": is_newer(self.latest), "changes": self.changes if is_newer(self.latest) else [],
                "checked_at": int(self.checked_at), "error": self.error, "repo": f"https://github.com/{REPO}",
                "command": update_command()}

    async def _get(self, http, path):
        async with http.get(f"{RAW}/{path}", timeout=ClientTimeout(total=15),
                            headers={"Cache-Control": "no-cache"}) as r:
            if r.status != 200:
                raise RuntimeError(f"GitHub HTTP {r.status} ({path})")
            return await r.text()

    async def check(self, http):
        async with self._lock:
            try:
                latest = parse_version(await self._get(http, "ym_player/__init__.py"))
                if not latest:
                    raise RuntimeError("GitHub의 __init__.py에서 버전을 찾지 못했습니다.")
                changes = []
                if is_newer(latest):
                    try:
                        changes = changes_between(parse_changelog(await self._get(http, "ym_player/web/js/credits.js")),
                                                  __version__, latest)
                    except Exception as e:  # 변경 이력은 없어도 버전 알림은 한다
                        log.info("GitHub 변경 이력을 읽지 못했습니다: %s", e)
                if is_newer(latest) and latest != self.latest:
                    log.info("GitHub에 새 버전 %s이(가) 있습니다 (이 서버 %s).", latest, __version__)
                self.latest, self.changes, self.error = latest, changes, ""
            except Exception as e:
                self.error = str(e) or e.__class__.__name__
                log.info("업데이트 확인 실패: %s", self.error)
            self.checked_at = time.time()
        return self.info()

    async def loop(self, http):
        await asyncio.sleep(FIRST_DELAY_SEC)
        while True:
            if enabled():
                await self.check(http)
            await asyncio.sleep(CHECK_EVERY_SEC)
