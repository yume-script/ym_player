# -*- coding: utf-8 -*-
"""화면에서 업데이트 (ym_player/selfupdate.py) - 임시 git 저장소(원격 + 설치 폴더)로 시험한다"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ym_player import config_store, selfupdate as updater  # noqa: E402

HAVE_GIT = bool(shutil.which("git"))


def sh(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


@unittest.skipUnless(HAVE_GIT, "git 없음")
class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="upd-")
        self.origin = os.path.join(self.d, "origin")
        self.work = os.path.join(self.d, "work")      # GitHub 쪽 (커밋을 만드는 곳)
        self.root = os.path.join(self.d, "install")   # 설치 폴더
        sh(self.d, "init", "-q", "--bare", "-b", "main", self.origin)
        sh(self.d, "clone", "-q", self.origin, self.work)
        os.makedirs(os.path.join(self.work, "ym_player"))
        write(os.path.join(self.work, "ym_player", "__init__.py"), '__version__ = "1.0.0"\n')
        write(os.path.join(self.work, "a.py"), "x = 1\n")
        sh(self.work, "add", "-A")
        sh(self.work, "commit", "-qm", "첫 커밋")
        sh(self.work, "push", "-q", "origin", "HEAD:main")
        sh(self.d, "clone", "-q", "-b", "main", self.origin, self.root)
        self.saved = (updater.ROOT, updater.CHECK_IMPORTS, config_store.DATA_DIR, updater._restart_cb, updater.in_docker)
        updater.in_docker = lambda: False
        updater.ROOT = self.root
        updater.CHECK_IMPORTS = "pass"
        config_store.DATA_DIR = os.path.join(self.d, "data")
        updater._st.update(running=False, restart=False, log=[], fetched_at=0.0, fetch_error="")
        self.restarted = []
        updater.set_restart(lambda: self.restarted.append(1))

    def tearDown(self):
        updater.ROOT, updater.CHECK_IMPORTS, config_store.DATA_DIR, updater._restart_cb, updater.in_docker = self.saved
        updater._st.update(running=False, restart=False)
        shutil.rmtree(self.d, ignore_errors=True)

    def push(self, name, text, msg, version=None):
        write(os.path.join(self.work, name), text)
        if version:
            write(os.path.join(self.work, "ym_player", "__init__.py"), '__version__ = "%s"\n' % version)
        sh(self.work, "add", "-A")
        sh(self.work, "commit", "-qm", msg)
        sh(self.work, "push", "-q", "origin", "HEAD:main")

    def wait(self):
        for _ in range(200):
            if not updater._st["running"]:
                return
            time.sleep(0.05)
        self.fail("업데이트가 끝나지 않음")

    def head(self):
        return sh(self.root, "rev-parse", "HEAD")

    def test_status_lists_new_commits(self):
        self.push("a.py", "x = 2\n", "고침 하나", "1.1.0")
        st = updater.status(fetch=True)
        self.assertEqual(st["blocked"], "")
        self.assertEqual(st["branch"], "main")
        self.assertEqual([c["title"] for c in st["behind"]], ["고침 하나"])
        self.assertEqual(st["dirty"], [])

    def test_update_then_rollback(self):
        old = self.head()
        self.push("a.py", "x = 2\n", "고침", "1.1.0")
        updater.start("update")
        self.wait()
        self.assertNotEqual(self.head(), old)
        self.assertEqual(self.restarted, [1])
        self.assertTrue(updater.restart_pending())
        saved = updater.load_saved()
        self.assertEqual((saved["prev"], saved["from"], saved["to"], saved["result"]), (old, "1.0.0", "1.1.0", "ok"))
        updater._st["restart"] = False
        updater.start("rollback")
        self.wait()
        self.assertEqual(self.head(), old)
        self.assertEqual(len(self.restarted), 2)

    def test_broken_code_is_reverted(self):
        old = self.head()
        self.push("a.py", "def (:\n", "문법 오류")
        updater.start("update")
        self.wait()
        self.assertEqual(self.head(), old)              # 원래대로
        self.assertEqual(self.restarted, [])
        self.assertFalse(updater.restart_pending())
        self.assertIn("실패", updater.load_saved()["result"])
        self.assertTrue(any("되돌렸습니다" in x for x in updater._st["log"]))

    def test_dirty_refused(self):
        self.push("a.py", "x = 2\n", "고침")
        write(os.path.join(self.root, "a.py"), "x = 99\n")
        with self.assertRaises(RuntimeError) as cm:
            updater.start("update")
        self.assertIn("a.py", str(cm.exception))

    def test_already_latest(self):
        updater.start("update")
        self.wait()
        self.assertEqual(self.restarted, [])
        self.assertIn("최신", updater._st["phase"])

    def test_rollback_without_prev(self):
        updater.start("rollback")
        self.wait()
        self.assertEqual(self.restarted, [])
        self.assertIn("되돌릴 이전 버전이 없습니다", updater._st["phase"])

    def test_not_git(self):
        updater.ROOT = self.d
        self.assertIn("git", updater.blocker())


if __name__ == "__main__":
    unittest.main()
