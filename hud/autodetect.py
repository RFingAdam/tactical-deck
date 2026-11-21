"""Background thread: watches the Wardogs window and emits kill/death events automatically.
Only reads the screen while the game is the focused window; idles otherwise."""
import ctypes, os, sys, threading, time, traceback
from ctypes import wintypes

user32 = kernel32 = None
if sys.platform == "win32":
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

# dxcam.grab() returns None when the desktop has not changed. Reusing the last
# frame is fine for a short moment; past this we must not keep serving it
# (DXGI 0x887A0026 left the HUD frozen on a stale frame).
REUSE_MAX_S = 2.0
STALE_REINIT_S = 5.0
DXCAM_RETRY_S = 15.0
DXGI_ACCESS_LOST = 0x887A0026


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



def is_access_lost(exc):
    text = str(exc).lower()
    if "887a0026" in text or "access loss" in text or "accesslost" in text:
        return True
    for a in getattr(exc, "args", ()):
        try:
            if (int(a) & 0xFFFFFFFF) == DXGI_ACCESS_LOST:
                return True
        except (TypeError, ValueError):
            continue
    hr = getattr(exc, "hresult", None)
    try:
        if hr is not None and (int(hr) & 0xFFFFFFFF) == DXGI_ACCESS_LOST:
            return True
    except (TypeError, ValueError):
        pass
    return False


def classify_frame(now, last_fresh_ts, got_frame, access_lost=False,
                   reuse_max_s=REUSE_MAX_S, stale_s=STALE_REINIT_S):
    """Decide what to do with a dxcam grab result: fresh, reuse, skip, or reinit."""
    if access_lost:
        return "reinit"
    if got_frame:
        return "fresh"
    if last_fresh_ts is None:
        return "reinit"
    age = now - last_fresh_ts
    if age <= reuse_max_s:
        return "reuse"
    if age >= stale_s:
        return "reinit"
    return "skip"


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
    dxcam takes ~14 ms. Falls back to the old mss/GDI grab if Desktop Duplication isn't available.

    Nov 20: do not reuse a stale last-frame forever when dxcam returns None or loses the output
    (HRESULT 0x887A0026). Re-create the camera after STALE_REINIT_S, expose stale on /health, and
    skip detection rather than feed an old frame to the OCR loop."""

    def __init__(self):
        self.cam = None
        self.last = None
        self.retry_at = 0.0
        self.gdi = None
        self.out = (0, 0)
        self.last_fresh_ts = None
        self.stale = False
        self.backend = "none"
        self.last_error = None
        self.reinit_count = 0
        self.opened_at = time.time()
        self._open()

    def health(self, capturing=False):
        now = time.time()
        age = None if self.last_fresh_ts is None else round(now - self.last_fresh_ts, 2)
        stale = bool(capturing and (
            self.last_fresh_ts is None or (now - self.last_fresh_ts) >= STALE_REINIT_S
        ))
        self.stale = stale
        return {
            "backend": self.backend,
            "capturing": bool(capturing),
            "stale": stale,
            "last_fresh_age_s": age,
            "last_error": self.last_error,
            "reinit_count": self.reinit_count,
        }

    def _release_cam(self):
        cam = self.cam
        self.cam = None
        if cam is None:
            return
        try:
            cam.release()
        except Exception:
            pass

    def _open(self):
        self._release_cam()
        try:
            import dxcam
            self.cam = dxcam.create(device_idx=0, output_idx=0, output_color="BGRA")
            self.out = (self.cam.width, self.cam.height)
            self.backend = "dxcam"
            self.last_error = None
            self.opened_at = time.time()
        except Exception as e:
            self.cam = None
            self.backend = "gdi"
            self.retry_at = time.time() + DXCAM_RETRY_S
            self.last_error = str(e)
            print("capture: Desktop Duplication unavailable, using GDI for now:", e, flush=True)

    def _reopen(self, reason):
        self.reinit_count += 1
        self.last = None
        print("capture: reopening Desktop Duplication (%s, attempt %s)" % (reason, self.reinit_count), flush=True)
        self._open()
        if self.cam is None:
            self.stale = True
            self.retry_at = time.time() + DXCAM_RETRY_S
            return False
        return True

    def grab(self, rect):
        l, t = rect["left"], rect["top"]
        r, b = l + rect["width"], t + rect["height"]
        now = time.time()
        if self.cam is None and now >= self.retry_at:
            self._reopen("scheduled retry")
            now = time.time()
        if self.cam is not None and l >= 0 and t >= 0 and r <= self.out[0] and b <= self.out[1]:
            access_lost = False
            frame = None
            err = None
            try:
                frame = self.cam.grab(region=(l, t, r, b))
            except Exception as e:
                err = e
                access_lost = is_access_lost(e)
                self.last_error = str(e)
                print("capture: Desktop Duplication error:", e, flush=True)
            action = classify_frame(now, self.last_fresh_ts, frame is not None, access_lost=access_lost)
            if action == "fresh":
                self.last = frame
                self.last_fresh_ts = now
                self.stale = False
                self.last_error = None
                self.backend = "dxcam"
                return NpFrame(frame)
            if action == "reuse" and self.last is not None and self.last.shape[:2] == (b - t, r - l):
                return NpFrame(self.last)
            if action == "reinit":
                reason = "access lost" if access_lost else (
                    "error" if err is not None else "no fresh frame for %.1fs" % (
                        0 if self.last_fresh_ts is None else now - self.last_fresh_ts
                    )
                )
                self.stale = True
                self.last = None
                if not self._reopen(reason):
                    pass  # fall through to GDI
                elif self.cam is not None:
                    try:
                        frame = self.cam.grab(region=(l, t, r, b))
                    except Exception as e:
                        self.last_error = str(e)
                        frame = None
                        print("capture: Desktop Duplication still failing after reopen:", e, flush=True)
                    if frame is not None:
                        self.last = frame
                        self.last_fresh_ts = time.time()
                        self.stale = False
                        self.last_error = None
                        self.backend = "dxcam"
                        return NpFrame(frame)
            else:
                # skip: too old to reuse, not yet long enough to reinit
                return None
        import mss
        from PIL import Image
        if self.gdi is None:
            self.gdi = (getattr(mss, "MSS", None) or mss.mss)()
        try:
            shot = self.gdi.grab(rect)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            self.backend = "gdi" if self.cam is None else self.backend
            self.last_fresh_ts = time.time()
            self.stale = False
            return img
        except Exception as e:
            self.last_error = "gdi: %s" % e
            self.stale = True
            print("capture: GDI grab failed:", e, flush=True)
            return None


class AutoDetect(threading.Thread):
    def __init__(self, emit, game_exe="WardogsClient-Win64-Shipping.exe", fps=4.0, enabled=True, cfg=None):
        super().__init__(daemon=True)
        self.emit = emit
        self.game_exe = (game_exe or "").lower()
        self.period = 1.0 / max(0.5, float(fps))
        self.enabled = enabled
        self.cfg = cfg if cfg is not None else {}
        self._pending_profile = None
        self.capture = None
        self.status = {"enabled": enabled, "running": False, "game_focused": False, "fps": 0.0,
                       "kills": 0, "deaths": 0, "wins": 0, "last_event": None, "error": None}

    def use_profile(self, profile):
        """Swap OCR regions / keywords and the focused-window exe without restarting the thread."""
        profile = profile or {}
        self.game_exe = (profile.get("game_exe") or "").lower()
        self._pending_profile = profile
        self.status["game"] = profile.get("id")

    def run(self):
        try:
            import wardogs_detect as wd
        except Exception as e:
            self.status["error"] = f"auto-detect unavailable: {e}"
            return
        grabber = Capture()
        self.capture = grabber
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
                pending = self._pending_profile
                if pending is not None:
                    wd.apply_detect_profile(pending)
                    det = wd.Detector()
                    self.game_exe = (pending.get("game_exe") or "").lower()
                    self._pending_profile = None
                if not self.enabled:
                    self.status["capture"] = grabber.health(capturing=False)
                    time.sleep(0.5)
                    continue
                hwnd, exe = foreground_exe()
                focused = bool(self.game_exe) and exe == self.game_exe
                self.status["game_focused"] = focused
                if not focused:
                    self.status["capture"] = grabber.health(capturing=False)
                    time.sleep(0.5)
                    continue
                rect = window_rect(hwnd)
                if rect["width"] < 640 or rect["height"] < 360:
                    self.status["capture"] = grabber.health(capturing=False)
                    time.sleep(0.5)
                    continue
                frame = grabber.grab(rect)
                self.status["capture"] = grabber.health(capturing=True)
                if frame is None:
                    time.sleep(max(0.0, self.period - (time.time() - t0)))
                    continue
                size = getattr(frame, "size", None) or (rect["width"], rect["height"])
                try:
                    import layout as _layout
                    gr = _layout.ocr_game_rect(
                        size[0], size[1],
                        self.cfg.get("display_layout", "16:9"),
                        self.cfg.get("game_rect"),
                    )
                    wd.set_frame_game_rect(gr)
                except Exception:
                    wd.set_frame_game_rect(None)
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
                        extra = (f" how={ev[2]}" if len(ev) >= 3 else "") + \
                                (f" life={ev[3]}" if len(ev) >= 4 and ev[3] is not None else "")
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
                if self.capture is not None:
                    self.status["capture"] = self.capture.health(capturing=True)
                traceback.print_exc()
                time.sleep(1)
            time.sleep(max(0.0, self.period - (time.time() - t0)))
