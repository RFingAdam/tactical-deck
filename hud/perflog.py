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


def gpu_stats():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,utilization.encoder,clocks.gr,power.draw",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                             creationflags=0x08000000).stdout.strip()
        return [x.strip() for x in out.split(",")]
    except Exception:
        return ["", "", "", ""]


def app_cpu():
    """Per-app CPU seconds via PowerShell-free WMIC-less route: use tasklist is too coarse, so read via psutil-like ctypes is long;
    use 'typeperf' style query through PowerShell once per sample (cheap enough every 2 s)."""
    try:
        ps = ("Get-Process | Select-Object ProcessName,CPU | ConvertTo-Csv -NoTypeInformation")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=8,
                             creationflags=0x08000000).stdout
        tot = {k: 0.0 for k in APPS}
        for row in csv.reader(out.splitlines()[1:]):
            if len(row) < 2 or not row[1]:
                continue
            name = row[0].lower()
            for k, pat in APPS.items():
                if name.startswith(pat):
                    tot[k] += float(row[1])
        return tot
    except Exception:
        return {k: 0.0 for k in APPS}


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
