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


class NpFrame:
    """Lazy RGB view over a BGRA screen grab: only the regions the detector actually crops get converted
    (the old path converted the whole 3440x1440 frame to RGB every tick)."""

    def __init__(self, bgra):
        self.a = bgra
        self.size = (bgra.shape[1], bgra.shape[0])

    def crop(self, box):
        import numpy as np
        from PIL import Image
        l, t, r, b = (int(v) for v in box)
        return Image.fromarray(np.ascontiguousarray(self.a[t:b, l:r, 2::-1]), "RGB")


class Capture:
    """Screen capture for the detector. Oct 18: Desktop Duplication (dxcam) instead of a GDI BitBlt of the whole
    game window - the GDI grab took ~77 ms (up to 142 ms) four times a second and stalls the compositor (hitches);
    dxcam takes ~14 ms. Falls back to the old mss/GDI grab if Desktop Duplication isn't available."""

    def __init__(self):
        self.cam, self.last, self.retry_at = None, None, 0.0
        self.gdi = None
        self._open()

    def _open(self):
        try:
            import dxcam
            self.cam = dxcam.create(device_idx=0, output_idx=0, output_color="BGRA")
            self.out = (self.cam.width, self.cam.height)
        except Exception as e:
            self.cam = None
            self.retry_at = time.time() + 30
            print("capture: Desktop Duplication unavailable, using GDI for now:", e, flush=True)

    def grab(self, rect):
        l, t = rect["left"], rect["top"]
        r, b = l + rect["width"], t + rect["height"]
        if self.cam is None and time.time() >= self.retry_at:
            self._open()
        if self.cam is not None and l >= 0 and t >= 0 and r <= self.out[0] and b <= self.out[1]:
            try:
                f = self.cam.grab(region=(l, t, r, b))
                if f is None and self.last is not None and self.last.shape[:2] == (b - t, r - l):
                    f = self.last  # no new frame since the last grab (screen unchanged)
                if f is not None:
                    self.last = f
                    return NpFrame(f)
            except Exception as e:
                print("capture: Desktop Duplication error, falling back to GDI:", e, flush=True)
                try:
                    self.cam.release()
                except Exception:
                    pass
                self.cam, self.retry_at = None, time.time() + 10
        import mss
        from PIL import Image
        if self.gdi is None:
            self.gdi = (getattr(mss, "MSS", None) or mss.mss)()
        shot = self.gdi.grab(rect)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


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
            import wardogs_detect as wd
        except Exception as e:
            self.status["error"] = f"auto-detect unavailable: {e}"
            return
        grabber = Capture()
        det = wd.Detector()
        try:
            from server_watch import ServerWatch   # Wardogs server ID -> Stream Keeper (rejoin after a crash)
            watch = ServerWatch()
        except Exception as e:
            watch = None
            print('server watch disabled:', e)
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
                frame = grabber.grab(rect)
                events, _dbg = det.feed(frame, t0)
                if perf:
                    perf.maybe_log(frame, wd)
                if watch:
                    try:
                        sid = watch.feed(frame, t0)
                        if sid:
                            self.status["server"] = sid
                    except Exception as e:  # never let it break kill/death detection
                        print('server watch error:', e)
                for ev in events:
                    if isinstance(ev, tuple):  # ("cash", balance) or ("cash", balance, how)
                        self.status[ev[0]] = ev[1]
                        extra = f" how={ev[2]}" if len(ev) >= 3 else ""
                        print(time.strftime("%Y-%m-%d %H:%M:%S"), "auto:", ev[0], ev[1], extra, flush=True)
                        self.emit(ev)
                        continue
                    if ev + "s" in self.status:
                        self.status[ev + "s"] += 1
                    self.status["last_event"] = [ev, time.strftime("%H:%M:%S")]
                    print(time.strftime("%Y-%m-%d %H:%M:%S"), "auto:", ev, "| downed-ui:", (_dbg.get("downed") or "")[:40],
                          ("| death-text: " + (_dbg.get("death_text") or "")) if ev == "death" else "", flush=True)
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
