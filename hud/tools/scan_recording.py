"""Scan a long stream recording (keyframes only, fast) and OCR the whole frame.
Writes <ts>\t<text lines> to tools/scan_<name>.txt so end-of-match screens can be found.
Usage: python tools/scan_recording.py <file> [every_n_keyframes]
"""
import sys, os, re, subprocess, threading, queue
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image, ImageOps
import wardogs_detect as wd

def read_exact(stream, n):
    """Read exactly n bytes from a pipe (or fewer only at EOF)."""
    chunks, got = [], 0
    while got < n:
        c = stream.read(n - got)
        if not c:
            break
        chunks.append(c)
        got += len(c)
    return b"".join(chunks)

src = sys.argv[1]
every = int(sys.argv[2]) if len(sys.argv) > 2 else 1
W, H = 1920, 1080
name = os.path.splitext(os.path.basename(src))[0].replace(" ", "_")
out = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), f"scan_{name}.txt"), "w", encoding="utf-8")
p = subprocess.Popen(["ffmpeg", "-v", "info", "-skip_frame", "nokey", "-i", src, "-vf", f"scale={W}:{H},showinfo",
                      "-fps_mode", "vfr", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
pts = queue.Queue()
def read_err():
    for line in iter(p.stderr.readline, b""):
        m = re.search(rb"pts_time:\s*([\d.]+)", line)
        if m:
            pts.put(float(m.group(1)))
threading.Thread(target=read_err, daemon=True).start()
i = 0
while True:
    buf = read_exact(p.stdout, W * H * 3)
    if len(buf) < W * H * 3:
        break
    ts = pts.get(timeout=30)
    i += 1
    if i % every:
        continue
    frame = Image.frombytes("RGB", (W, H), buf)
    # centre band where big banners live
    band = frame.crop((int(W * 0.15), int(H * 0.08), int(W * 0.85), int(H * 0.75)))
    lines = wd.ocr_lines(ImageOps.autocontrast(band.convert("L")).convert("RGBA"))
    lines = [l for l in lines if len(l.strip()) >= 4]
    if lines:
        out.write(f"{ts:.1f}\t{' | '.join(lines)}\n")
        out.flush()
print("done", i)
