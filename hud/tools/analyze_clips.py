"""Runs the Wardogs detector over recorded clips to measure accuracy.
Usage: python tools/analyze_clips.py <clip-or-folder> [fps]
Writes one JSON line per clip to tools/clip_results.jsonl and big centre text to tools/center_text.txt
"""
import sys, os, json, subprocess, glob, time
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

src = sys.argv[1]
fps = float(sys.argv[2]) if len(sys.argv) > 2 else 4
clips = sorted(glob.glob(os.path.join(src, "WARDOGS_*.mp4"))) if os.path.isdir(src) else [src]
here = os.path.dirname(os.path.abspath(__file__))
out = open(os.path.join(here, "clip_results.jsonl"), "a", encoding="utf-8")
ctr = open(os.path.join(here, "center_text.txt"), "a", encoding="utf-8")
W, H = 1920, 802
for clip in clips:
    t0 = time.time()
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", clip, "-vf", f"fps={fps},scale={W}:{H},format=rgb24",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, bufsize=0)
    det = wd.Detector()
    events, i = [], 0
    while True:
        buf = read_exact(p.stdout, W * H * 3)
        if len(buf) < W * H * 3:
            break
        frame = Image.frombytes("RGB", (W, H), buf)
        ts = i / fps
        ev, dbg = det.feed(frame, ts)
        for e in ev:
            events.append([e, round(ts, 2)])
        if i % int(fps * 2) == 0:  # centre text every 2 s, for finding the win screen
            lines = wd.ocr_lines(wd.v_gray(wd.region(frame, "center")))
            lines = [l for l in lines if len(l) > 3]
            if lines:
                ctr.write(f"{os.path.basename(clip)}\t{ts:.0f}\t{' | '.join(lines)}\n")
        i += 1
    rec = {"clip": os.path.basename(clip), "secs": round(i / fps, 1), "kills": sum(1 for e in events if e[0] == "kill"),
           "deaths": sum(1 for e in events if e[0] == "death"), "events": events, "took": round(time.time() - t0, 1)}
    out.write(json.dumps(rec) + "\n"); out.flush(); ctr.flush()
    print(json.dumps(rec), flush=True)
