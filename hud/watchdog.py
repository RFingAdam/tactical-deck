"""Hidden Protocol HUD watchdog.

Runs in Session 1 via the ProtocolHudWatchdog logon task (pythonw, no window).
Every CHECK_S seconds it hits http://127.0.0.1:8790/health. If the server is
down or reports stale for more than DOWN_S seconds it restarts the HUD in
Session 1 through the ProtocolHudStart task, with a log and a restart backoff.

It will not restart a healthy HUD. FeedOBS (the Elgato game feed) is not treated
as a live stream and does not block a stale/down restart.
"""
import json, os, subprocess, sys, time, urllib.error, urllib.request

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
LOG_PATH = os.path.join(ROOT, "watchdog.log")
PORT = 8790
CHECK_S = 30
DOWN_S = 60
HEALTH_URL = "http://127.0.0.1:%s/health" % PORT
STATE_URL = "http://127.0.0.1:%s/api/state" % PORT
START_TASK = "ProtocolHudStart"
PYW = find_pythonw()
STARTER = os.path.join(ROOT, "start_hud_hidden.py")
BACKOFF_S = (30, 60, 120, 300)


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        out_name = getattr(sys.stdout, "name", None)
        if out_name and os.path.abspath(out_name) == os.path.abspath(LOG_PATH):
            return  # already printed into the log
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _get(url, timeout=3):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
        return r.status, raw


def poll():
    """Return (ok, stale, detail_dict_or_error_string)."""
    try:
        status, raw = _get(HEALTH_URL)
        data = json.loads(raw) if raw else {}
        stale = bool(data.get("stale"))
        ok = (status == 200) and bool(data.get("ok", True)) and not stale
        return ok, stale, data
    except Exception as e:
        # Older HUD builds have no /health; /api/state means the process is up
        # and not known-stale, so do not bounce it in a restart loop.
        try:
            status, raw = _get(STATE_URL)
            if status == 200 and raw:
                return True, False, {"ok": True, "stale": False, "legacy": True}
        except Exception:
            pass
        return False, False, "down: %s" % e


def _taskkill_hud():
    """Stop only this tree's server.py, never the sound insert / PTT / other pythonw."""
    marker = os.path.normcase(ROOT)
    killed = []
    cmd = (
        "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine } "
        "| Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
    )
    try:
        raw = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=20)
        data = json.loads(raw.decode("utf-8", "replace") or "[]")
        if isinstance(data, dict):
            data = [data]
        for item in data or []:
            cl = os.path.normcase(str(item.get("CommandLine") or "").replace("/", "\\"))
            if marker in cl and "server.py" in cl:
                pid = int(item["ProcessId"])
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10)
                killed.append(pid)
        return "KILLED " + ",".join(str(p) for p in killed) if killed else "none"
    except Exception as e:
        return "kill-err %s" % e


def _run_start_task():
    try:
        out = subprocess.check_output(
            ["schtasks.exe", "/Run", "/TN", START_TASK],
            stderr=subprocess.STDOUT, timeout=20,
        )
        return "task " + out.decode("utf-8", "replace").strip()
    except Exception as e:
        # Fallback: start the hidden starter directly (still Session 1 if we are).
        try:
            flags = 0x08000000 | 0x00000008 | 0x00000200
            kwargs = {"cwd": ROOT, "close_fds": True}
            if sys.platform == "win32":
                kwargs["creationflags"] = flags
            subprocess.Popen([PYW, STARTER], **kwargs)
            return "starter-fallback after task-err %s" % e
        except Exception as e2:
            return "start-failed %s / %s" % (e, e2)


def restart(reason):
    log("RESTART begin reason=%s" % reason)
    killed = _taskkill_hud()
    log("RESTART killed %s" % (killed or "none"))
    # wait for :8790 to drop
    deadline = time.time() + 15
    while time.time() < deadline:
        ok, _stale, _info = poll()
        if not ok:
            break
        time.sleep(0.4)
    started = _run_start_task()
    log("RESTART start %s" % started)
    deadline = time.time() + 45
    last = None
    while time.time() < deadline:
        ok, stale, info = poll()
        last = (ok, stale, info)
        if ok and not stale:
            pid = info.get("pid") if isinstance(info, dict) else None
            log("RESTART ok pid=%s" % pid)
            return True
        time.sleep(0.5)
    log("RESTART failed last=%s" % (last,))
    return False


def main():
    if sys.stdout is None or os.path.basename(sys.executable).lower().startswith("pythonw"):
        try:
            sys.stdout = sys.stderr = open(LOG_PATH, "a", buffering=1, encoding="utf-8")
        except Exception:
            pass
    log("watchdog start pid=%s check=%ss down=%ss" % (os.getpid(), CHECK_S, DOWN_S))
    bad_since = None
    failures = 0
    backoff_until = 0.0
    while True:
        ok, stale, info = poll()
        if ok:
            if bad_since is not None:
                log("recovered after %.0fs info=%s" % (time.time() - bad_since, _brief(info)))
            bad_since = None
            failures = 0
        else:
            if bad_since is None:
                bad_since = time.time()
                log("unhealthy begin stale=%s info=%s" % (stale, _brief(info)))
            elif time.time() - bad_since >= DOWN_S and time.time() >= backoff_until:
                reason = "stale>%.0fs" % (time.time() - bad_since) if stale else "down>%.0fs" % (time.time() - bad_since)
                if restart(reason):
                    failures = 0
                    bad_since = None
                    backoff_until = time.time() + BACKOFF_S[0]
                else:
                    delay = BACKOFF_S[min(failures, len(BACKOFF_S) - 1)]
                    failures += 1
                    backoff_until = time.time() + delay
                    log("backoff %ss after failed restart" % delay)
                    bad_since = time.time()  # require another DOWN_S of failure
        time.sleep(CHECK_S)


def _brief(info):
    if isinstance(info, dict):
        cap = info.get("capture") or {}
        return "ok=%s stale=%s pid=%s backend=%s age=%s err=%s" % (
            info.get("ok"), info.get("stale"), info.get("pid"),
            cap.get("backend"), cap.get("last_fresh_age_s"), cap.get("last_error"),
        )
    return str(info)


if __name__ == "__main__":
    main()
