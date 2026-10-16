"""GitHub 새 버전 확인 (updates.py, /api/admin/update)."""
import asyncio
import os

from test_auth import data, run  # noqa: F401  (pytest fixture)

from ym_player import __version__, updates

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def test_parse_real_files():
    assert updates.parse_version(_read("ym_player/__init__.py")) == __version__
    log = updates.parse_changelog(_read("ym_player/web/js/credits.js"))
    assert log and log[0]["version"] == __version__          # 맨 위 줄 = 지금 버전 (PR마다 같이 올림)
    assert all("pr" in e and "title" in e for e in log)


def test_version_compare_and_changes():
    assert updates.is_newer("1.1.10", "1.1.9") and not updates.is_newer("1.1.3", "1.1.3")
    assert not updates.is_newer("", "1.0") and not updates.is_newer("abc", "1.0")
    log = [{"pr": 5, "version": "1.2.1", "title": "c"}, {"pr": 4, "version": "1.2.0", "title": "b"},
           {"pr": 3, "version": "1.1.9", "title": "a"}, {"pr": 2, "title": "버전 없음"}]
    assert [e["pr"] for e in updates.changes_between(log, "1.1.9", "1.2.0")] == [4]
    assert [e["pr"] for e in updates.changes_between(log, "1.1.0", "1.2.1")] == [5, 4, 3]


class FakeResp:
    def __init__(self, status, text):
        self.status, self._text = status, text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeHttp:
    def __init__(self, files):
        self.files, self.urls = files, []

    def get(self, url, **kw):
        self.urls.append(url)
        for path, body in self.files.items():
            if url.endswith(path):
                return FakeResp(200, body)
        return FakeResp(404, "")


def test_checker_finds_newer_version():
    newer = "9.9.9"
    http = FakeHttp({"ym_player/__init__.py": f'"""x"""\n__version__ = "{newer}"\n',
                     "ym_player/web/js/credits.js": 'export const CHANGELOG = [\n    {"pr": 99, "version": "9.9.9", "date": "d", "title": "새 기능"},\n'
                                                     f'    {{"pr": 1, "version": "{__version__}", "title": "지금"}},\n];\n'})
    info = asyncio.run(updates.UpdateChecker().check(http))
    assert info["newer"] and info["latest"] == newer and info["error"] == ""
    assert [e["pr"] for e in info["changes"]] == [99]
    assert http.urls[0].startswith("https://raw.githubusercontent.com/yume-script/ym_player/main/")
    assert "git pull" in info["command"]

    same = asyncio.run(updates.UpdateChecker().check(FakeHttp({"ym_player/__init__.py": f'__version__ = "{__version__}"'})))
    assert not same["newer"] and same["changes"] == []
    bad = asyncio.run(updates.UpdateChecker().check(FakeHttp({})))
    assert not bad["newer"] and "404" in bad["error"]


def test_update_api_is_admin_only(data, monkeypatch):  # noqa: F811
    async def fake_check(self, http):
        self.latest, self.checked_at = "9.9.9", 1
        return self.info()
    monkeypatch.setattr(updates.UpdateChecker, "check", fake_check)

    async def flow(c, app):
        from ym_player import auth
        await c.post("/api/auth/setup", json={"username": "admin", "password": "adminpw"})
        assert (await (await c.get("/api/admin/update")).json())["latest"] == ""
        j = await (await c.post("/api/admin/update/check")).json()
        assert j["newer"] and j["latest"] == "9.9.9" and j["current"] == __version__
        auth.create_user("kim", "kimpass1", "user")
        await c.post("/api/auth/logout")
        c.session.cookie_jar.clear()
        await c.post("/api/auth/login", json={"username": "kim", "password": "kimpass1"})
        assert (await c.get("/api/admin/update")).status == 403
        assert (await c.post("/api/admin/update/check")).status == 403
    run(flow)
