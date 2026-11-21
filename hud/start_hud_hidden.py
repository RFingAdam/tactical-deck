"""Start Protocol HUD in the current Windows session with no console window.

Used by the ProtocolHudStart scheduled task (Interactive, Session 1). If :8790
is already listening this exits without starting a second copy. The HUD process
is detached so it outlives this starter and any task time limit.
"""
import os, subprocess, sys, time

def find_pythonw():
    import shutil
    env = os.environ.get("PROTOCOL_HUD_PYTHON")
    if env:
        return env
    for name in ("pythonw.exe", "pythonw", "python.exe", "python"):
        found = shutil.which(name)
        if found:
            return found
    return sys.executable


ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = 8790
PYW = find_pythonw()
SERVER = os.path.join(ROOT, "server.py")
LOG = os.path.join(ROOT, "start_hud.log")
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def _log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _listening():
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%s/health" % PORT, timeout=2) as r:
            return r.status == 200
    except Exception:
        pass
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%s/api/state" % PORT, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _popen():
    flags = CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    kwargs = {"cwd": ROOT, "close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = flags
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        kwargs["startupinfo"] = startup
    return subprocess.Popen([PYW, SERVER], **kwargs)


def main():
    if _listening():
        _log("already listening, nothing to do")
        return 0
    try:
        proc = _popen()
        _log("spawned pid=%s" % proc.pid)
    except Exception as e:
        _log("spawn failed: %s" % e)
        return 1
    deadline = time.time() + 30
    while time.time() < deadline:
        if _listening():
            _log("listen ok")
            return 0
        time.sleep(0.4)
    _log("timeout waiting for :8790")
    return 1


if __name__ == "__main__":
    sys.exit(main())
