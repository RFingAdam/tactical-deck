"""Run the detector over a slice of a letterboxed stream recording.
Usage: python tools/analyze_segment.py <file> <start_s> <dur_s> [fps]"""
import sys, os, subprocess, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image
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
f, ss, dur = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
fps = float(sys.argv[4]) if len(sys.argv) > 4 else 4
W, H = 1920, 803  # game area inside the 1080 canvas
p = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", str(ss), "-t", str(dur), "-i", f,
                      "-vf", f"fps={fps},scale=1920:1080,crop=1920:803:0:138,format=rgb24", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                     stdout=subprocess.PIPE, bufsize=0)
det, i, events = wd.Detector(), 0, []
while True:
    b = read_exact(p.stdout, W * H * 3)
    if len(b) < W * H * 3:
        break
    ts = ss + i / fps
    ev, _ = det.feed(Image.frombytes("RGB", (W, H), b), ts)
    events += [[e, round(ts, 1)] for e in ev]
    i += 1
print(json.dumps({"frames": i, "events": events}))
