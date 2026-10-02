"""Background thread: watches the Wardogs window and emits kill/death events automatically.
Only reads the screen while the game is the focused window; idles otherwise."""
import ctypes, os, threading, time, traceback
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def foreground_exe():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return hwnd, None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return hwnd, os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(h)
    return hwnd, None


def window_rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return {"left": r.left, "top": r.top, "width": r.right - r.left, "height": r.bottom - r.top}


class AutoDetect(threading.Thread):
    def __init__(self, emit, game_exe="WardogsClient-Win64-Shipping.exe", fps=4.0, enabled=True):
        super().__init__(daemon=True)
        self.emit = emit
        self.game_exe = game_exe.lower()
        self.period = 1.0 / max(0.5, float(fps))
        self.enabled = enabled
        self.status = {"enabled": enabled, "running": False, "game_focused": False, "fps": 0.0,
                       "kills": 0, "deaths": 0, "wins": 0, "last_event": None, "error": None}

    def run(self):
        try:
            import mss
            from PIL import Image
            import wardogs_detect as wd
        except Exception as e:
            self.status["error"] = f"auto-detect unavailable: {e}"
            return
        grabber = (getattr(mss, "MSS", None) or mss.mss)()
        det = wd.Detector()
        try:
            from perflog import PerfLog
            perf = PerfLog()
        except Exception as e:
            perf = None
            print('perf log disabled:', e)
        self.status["running"] = True
        frames, t_rate = 0, time.time()
        while True:
            t0 = time.time()
            try:
                self.status["enabled"] = self.enabled
                if not self.enabled:
                    time.sleep(0.5)
                    continue
                hwnd, exe = foreground_exe()
                focused = exe == self.game_exe
                self.status["game_focused"] = focused
                if not focused:
                    time.sleep(0.5)
                    continue
                rect = window_rect(hwnd)
                if rect["width"] < 640 or rect["height"] < 360:
                    time.sleep(0.5)
                    continue
                shot = grabber.grab(rect)
                frame = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                events, _dbg = det.feed(frame, t0)
                if perf:
                    perf.maybe_log(frame, wd)
                for ev in events:
                    if ev + "s" in self.status:
                        self.status[ev + "s"] += 1
                    self.status["last_event"] = [ev, time.strftime("%H:%M:%S")]
                    print(time.strftime("%Y-%m-%d %H:%M:%S"), "auto:", ev, "| downed-ui:", (_dbg.get("downed") or "")[:40], flush=True)
                    self.emit(ev)
                frames += 1
                if time.time() - t_rate >= 5:
                    self.status["fps"] = round(frames / (time.time() - t_rate), 1)
                    frames, t_rate = 0, time.time()
                self.status["error"] = None
            except Exception as e:
                self.status["error"] = str(e)
                traceback.print_exc()
                time.sleep(1)
            time.sleep(max(0.0, self.period - (time.time() - t0)))
