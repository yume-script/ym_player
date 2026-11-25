# -*- coding: utf-8 -*-
"""화면에서 업데이트 (설정 > 상태 > 업데이트 > 지금 업데이트) - venv/systemd 설치용 (BookOasis Assistant의 gdw/updater.py와 같은 방식).

  1) git fetch -> 받을 커밋 목록 (HEAD..origin/<브랜치>)
  2) 업데이트: 바로 전 커밋을 기억해 두고 git merge --ff-only (충돌할 수 있으면 하지 않음)
     -> requirements.txt가 바뀌었으면 pip install -> 새 코드를 불러와 보는 점검 (실패하면 바로 전 커밋으로 되돌림)
  3) 다시 시작: 진행 중인 작업을 정상 종료한 뒤 같은 프로세스를 새 코드로 다시 띄운다 (os.execv - systemd에서도 같은 PID)
  되돌리기: 기억해 둔 바로 전 커밋으로 (git reset --keep) -> 같은 점검 -> 다시 시작

도커 설치(이미지 안의 코드)는 화면에서 바꿀 수 없다 - 호스트에서 git pull && docker compose up -d --build.
"""
import json
import logging
import os
import subprocess
import sys
import threading
import time

from . import config_store

log = logging.getLogger("ym_player.selfupdate")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 설치 폴더 (git 저장소)
FETCH_SEC = 60
KEEP_LOG = 200
CHECK_IMPORTS = "import ym_player.server"

_lock = threading.Lock()
_st = {"running": False, "phase": "", "log": [], "fetched_at": 0.0, "fetch_error": "", "restart": False}
_restart_cb = None


def _data_dir():
    return config_store.DATA_DIR


def _state_path():
    return os.path.join(_data_dir(), "selfupdate_state.json")


def load_saved():
    try:
        with open(_state_path(), encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _save(data):
    os.makedirs(_data_dir(), exist_ok=True)
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _state_path())


def git(*args, timeout=120, check=True):
    """git 명령 (설치 폴더). 소유자가 다른 폴더도 되게 safe.directory"""
    p = subprocess.run(["git", "-c", f"safe.directory={ROOT}", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} 실패: {(p.stderr or p.stdout).strip()[-400:]}")
    return p.stdout.rstrip()


def in_docker():
    return os.path.exists("/.dockerenv") or os.environ.get("container") == "docker"


def blocker():
    """화면에서 업데이트할 수 없는 이유 (없으면 "")"""
    if in_docker():
        return "도커 설치는 화면에서 업데이트할 수 없습니다 - 호스트에서 git pull && docker compose up -d --build"
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        return "git으로 받은 설치 폴더가 아닙니다 (git clone으로 설치해야 화면에서 업데이트됨)."
    try:
        git("--version", timeout=10)
    except (OSError, RuntimeError):
        return "서버에 git이 없습니다."
    if not os.access(ROOT, os.W_OK) or not os.access(os.path.join(ROOT, ".git"), os.W_OK):
        return f"이 프로그램을 실행하는 계정이 설치 폴더({ROOT})에 쓸 수 없습니다 (서비스 User와 폴더 소유자 확인)."
    return ""


def _branch():
    b = git("rev-parse", "--abbrev-ref", "HEAD")
    if b == "HEAD":
        raise RuntimeError("특정 커밋에 고정된 상태(detached HEAD)라 업데이트할 수 없습니다 (git checkout main).")
    return b


def _upstream(branch):
    up = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
    return up or f"origin/{branch}"


def _dirty():
    """고친 채 커밋하지 않은 파일 (git이 관리하는 것만 - .env·data/는 git 밖)"""
    out = git("status", "--porcelain", "--untracked-files=no")
    return [line[3:] for line in out.splitlines() if line.strip()]


_busy_cb = None


def set_busy(cb):
    """server.py: 지금 하는 일(재생 중인 사람·자동 올리기 등)을 알려 줄 함수 - 사람이 읽는 문장 목록"""
    global _busy_cb
    _busy_cb = cb


def busy():
    """지금 하고 있는 일 (업데이트하면 끊긴다) - 사람이 읽는 문장 목록"""
    try:
        return list(_busy_cb() or []) if _busy_cb else []
    except Exception:  # noqa: BLE001
        return []


def _say(msg):
    log.info("업데이트: %s", msg)
    with _lock:
        _st["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")
        del _st["log"][:-KEEP_LOG]
        _st["phase"] = msg


def status(fetch=False):
    """{blocked, branch, current, current_version, behind:[{sha,title}], dirty, busy, running, phase, log, prev, last}"""
    why = blocker()
    out = {"success": True, "blocked": why, "running": _st["running"], "phase": _st["phase"], "log": list(_st["log"]),
           "restart": _st["restart"], "busy": busy(), "fetch_error": _st["fetch_error"]}
    saved = load_saved()
    out["prev"] = saved.get("prev") or ""
    out["last"] = {k: saved.get(k) for k in ("at", "action", "from", "to", "result") if saved.get(k)}
    if why:
        return out
    try:
        branch = _branch()
        up = _upstream(branch)
        if fetch and not _st["running"] and time.time() - _st["fetched_at"] > FETCH_SEC:
            try:
                git("fetch", "--quiet", up.split("/", 1)[0], branch, timeout=90)
                _st["fetch_error"] = ""
            except (RuntimeError, subprocess.TimeoutExpired) as e:
                _st["fetch_error"] = str(e)[:300]
            _st["fetched_at"] = time.time()
            out["fetch_error"] = _st["fetch_error"]
        rows = git("log", "--format=%h%x09%s", "-n", "50", f"HEAD..{up}", check=False)
        out.update(branch=branch, upstream=up, current=git("rev-parse", "--short", "HEAD"),
                   behind=[{"sha": r.split("\t", 1)[0], "title": r.split("\t", 1)[-1]} for r in rows.splitlines() if r],
                   ahead=int(git("rev-list", "--count", f"{up}..HEAD", check=False) or 0), dirty=_dirty())
        if out["prev"]:
            out["prev_title"] = git("log", "-1", "--format=%h %s", out["prev"], check=False)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        out["blocked"] = str(e)[:300]
    return out


def set_restart(cb):
    """app.py: 다시 시작할 때 부를 함수 (서버를 멈추고 작업을 정상 종료시킴)"""
    global _restart_cb
    _restart_cb = cb


def restart_pending():
    return _st["restart"]


def exec_self():
    """정상 종료를 마친 뒤 같은 프로세스를 새 코드로 다시 띄운다"""
    log.info("업데이트: 새 코드로 다시 시작합니다.")
    for h in logging.getLogger().handlers:
        try:
            h.flush()
        except Exception:  # noqa: BLE001
            pass
    os.chdir(ROOT)
    argv = list(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])   # (python -m ym_player 그대로)
    os.execv(sys.executable, [sys.executable, *argv[1:]])


def _changed(a, b):
    return git("diff", "--name-only", a, b, check=False).splitlines()


def _check_code(changed=()):
    """새 코드를 다른 프로세스에서 불러와 본다 (바뀐 .py 파일 문법 + 주요 모듈 불러오기 - 문법 오류·빠진 패키지)"""
    files = [f for f in changed if f.endswith(".py") and os.path.isfile(os.path.join(ROOT, f))]
    code = ("import ast, sys\nfor f in sys.argv[1:]:\n    ast.parse(open(f, encoding='utf-8').read(), f)\n" + CHECK_IMPORTS)
    p = subprocess.run([sys.executable, "-c", code, *files], cwd=ROOT, capture_output=True, text=True, timeout=180,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if p.returncode != 0:
        raise RuntimeError("새 코드 점검 실패: " + (p.stderr or p.stdout).strip()[-600:])


def _pip(changed):
    if "requirements.txt" not in changed:
        return
    _say("requirements.txt가 바뀌어 패키지를 설치합니다 (pip install -r requirements.txt)")
    p = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"], cwd=ROOT,
                       capture_output=True, text=True, timeout=900)
    if p.returncode != 0:
        raise RuntimeError("패키지 설치 실패: " + (p.stderr or p.stdout).strip()[-600:])


def _version():
    """설치 폴더의 ym_player/__init__.py __version__ (지금 디스크에 있는 코드 기준)"""
    import re
    try:
        with open(os.path.join(ROOT, "ym_player", "__init__.py"), encoding="utf-8") as f:
            m = re.search(r'__version__\s*=\s*"([^"]+)"', f.read())
        return m.group(1) if m else ""
    except OSError:
        return ""


def _run(action, restart):
    started = time.time()
    old = git("rev-parse", "HEAD")
    old_ver = _version()
    moved = False
    try:
        branch = _branch()
        up = _upstream(branch)
        if action == "rollback":
            target = load_saved().get("prev") or ""
            if not target:
                raise RuntimeError("되돌릴 이전 버전이 없습니다 (화면에서 업데이트한 적이 없음).")
            _say(f"이전 버전으로 되돌립니다 ({target[:7]})")
            git("reset", "--keep", target)
        else:
            _say(f"GitHub에서 받는 중 ({up})")
            git("fetch", "--quiet", up.split("/", 1)[0], branch, timeout=180)
            if git("rev-parse", up) == old:
                _say("이미 최신입니다.")
                return
            _say("새 코드로 바꾸는 중 (git merge --ff-only)")
            git("merge", "--ff-only", up)
        moved = True
        new = git("rev-parse", "HEAD")
        changed = _changed(old, new)
        _say(f"바뀐 파일 {len(changed)}개")
        _pip(changed)
        _say("새 코드 점검 중")
        _check_code(changed)
        _save({"prev": old, "at": int(started), "action": action, "from": old_ver, "to": _version(),
               "from_sha": old[:7], "to_sha": new[:7], "result": "ok"})
        if not restart:
            _say("완료 - 다시 시작하면 반영됩니다.")
            return
        _say(f"완료 (v{old_ver} → v{_version()}) - 진행 중인 작업을 마무리하고 다시 시작합니다")
        _st["restart"] = True
        if _restart_cb:
            _restart_cb()
    except Exception as e:  # noqa: BLE001
        _say(f"실패: {e}")
        if moved:
            try:
                git("reset", "--keep", old)
                _say(f"원래 버전으로 되돌렸습니다 ({old[:7]})")
            except Exception as e2:  # noqa: BLE001
                _say(f"원래 버전으로 되돌리지 못했습니다: {e2} - 설치 폴더에서 git reset --keep {old[:7]}")
        saved = load_saved()
        saved.update(at=int(started), action=action, result=f"실패: {str(e)[:300]}")
        try:
            _save(saved)
        except OSError:
            pass
    finally:
        _st["running"] = False


def start(action="update", restart=True):
    """백그라운드로 업데이트(또는 되돌리기) 시작. 시작할 수 없으면 RuntimeError"""
    if action not in ("update", "rollback"):
        raise RuntimeError("알 수 없는 동작")
    why = blocker()
    if why:
        raise RuntimeError(why)
    with _lock:
        if _st["running"] or _st["restart"]:
            raise RuntimeError("이미 업데이트 중입니다.")
        dirty = _dirty()
        if dirty:
            raise RuntimeError("설치 폴더에 직접 고친 파일이 있어 업데이트하지 않습니다: " + ", ".join(dirty[:10])
                               + " (git stash 또는 git checkout -- 파일)")
        _st.update(running=True, log=[], phase="시작")
    threading.Thread(target=_run, args=(action, restart), name="gdw_update", daemon=True).start()
