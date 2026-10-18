"""Performance logger: while Wardogs is focused, every ~2 s log the in-game FPS counter (OCR),
GPU load/clock/encoder, total CPU and per-app CPU to tools/perf.csv. Started by server.py."""
import csv, ctypes, os, re, subprocess, threading, time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "tools", "perf.csv")
APPS = {"wardogs": "wardogsclient", "streamlabs": "obs64", "tiktok": "tiktok live studio", "discord_clips": "discord_clips"}


class FILETIME(ctypes.Structure):
    _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]


def _ft(f):
    return (f.hi << 32) | f.lo


def system_times():
    i, k, u = FILETIME(), FILETIME(), FILETIME()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
    return _ft(i), _ft(k) + _ft(u)


# Oct 18: these used to spawn nvidia-smi (~170 ms) and a whole PowerShell (~1.8 s of CPU) every 2 s while the game
# ran. Now both are read in-process: NVML through pynvml (opened once) and per-app CPU through psutil.
_NVML = {"h": None, "failed_at": 0.0}


def gpu_stats():
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import pynvml
        if _NVML["h"] is None:
            if time.time() - _NVML["failed_at"] < 60:
                return ["", "", "", ""]
            pynvml.nvmlInit()
            _NVML["h"] = pynvml.nvmlDeviceGetHandleByIndex(0)
        h = _NVML["h"]
        util = pynvml.nvmlDeviceGetUtilizationRates(h).gpu
        enc = pynvml.nvmlDeviceGetEncoderUtilization(h)[0]
        clk = pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_GRAPHICS)
        pw = pynvml.nvmlDeviceGetPowerUsage(h) / 1000.0
        return [str(util), str(enc), str(clk), f"{pw:.2f}"]
    except Exception:
        _NVML["h"], _NVML["failed_at"] = None, time.time()
        return ["", "", "", ""]


def app_cpu():
    """Per-app CPU seconds (user + kernel, like Get-Process 'CPU'), in-process via psutil."""
    tot = {k: 0.0 for k in APPS}
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            name = (p.info.get("name") or "").lower()
            for k, pat in APPS.items():
                if name.startswith(pat):
                    try:
                        ct = p.cpu_times()
                        tot[k] += ct.user + ct.system
                    except psutil.Error:
                        pass
    except Exception:
        pass
    return tot


def read_fps(frame, wd):
    """OCR the game's own FPS counter in the top-left corner (e.g. '133 FPS')."""
    W, H = frame.size
    c = frame.crop((0, 0, int(W * 0.04), int(H * 0.02)))
    c = c.resize((c.width * 6, c.height * 6))
    txt = " ".join(wd.ocr_lines(wd.v_gray(c)) + wd.ocr_lines(wd.v_inv(c)))
    m = re.search(r"(\d{1,3})\s*F", txt)
    return int(m.group(1)) if m else ""


class PerfLog:
    def __init__(self):
        self.lock = threading.Lock()
        self.last = 0.0
        self.prev_sys = system_times()
        self.prev_app = app_cpu()
        self.prev_t = time.time()
        self.busy = False
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        if not os.path.exists(OUT):
            with open(OUT, "w", newline="") as fh:
                csv.writer(fh).writerow(["time", "game_fps", "gpu_util", "gpu_enc", "gpu_mhz", "gpu_w", "cpu_total"] +
                                        [f"cpu_{k}" for k in APPS])

    def maybe_log(self, frame, wd):
        now = time.time()
        if now - self.last < 2.0 or self.busy:
            return
        self.last = now
        self.busy = True
        fps = read_fps(frame, wd)  # OCR on the detector thread (fast)
        threading.Thread(target=self._sample, args=(fps,), daemon=True).start()

    def _sample(self, fps):
        try:
            g = gpu_stats()
            idle, busy = system_times()
            pi, pb = self.prev_sys
            d_busy, d_idle = busy - pb, idle - pi
            cpu_total = round(100.0 * (d_busy - d_idle) / d_busy, 1) if d_busy > 0 else ""
            self.prev_sys = (idle, busy)
            apps = app_cpu()
            dt = time.time() - self.prev_t
            n = os.cpu_count() or 1
            per = {k: round(100.0 * (apps[k] - self.prev_app.get(k, 0)) / dt / n, 1) if dt > 0 else "" for k in APPS}
            self.prev_app, self.prev_t = apps, time.time()
            with open(OUT, "a", newline="") as fh:
                csv.writer(fh).writerow([time.strftime("%Y-%m-%d %H:%M:%S"), fps] + g + [cpu_total] + [per[k] for k in APPS])
        finally:
            self.busy = False
