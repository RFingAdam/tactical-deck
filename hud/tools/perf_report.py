"""Summarise tools/perf.csv into the four test conditions (TikTok on/off x recording on/off)."""
import csv, os, statistics as s

rows = list(csv.DictReader(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "perf.csv"))))


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def seg(name, rs):
    if not rs:
        print(f"{name}: no samples")
        return
    fps = sorted(f(x["game_fps"]) for x in rs if f(x["game_fps"]))

    def m(k):
        v = [f(x[k]) for x in rs if f(x[k]) is not None]
        return round(s.median(v), 1) if v else None

    p10 = fps[len(fps) // 10] if fps else None
    print(f"{name}: n={len(rs):3d} {rs[0]['time'][11:]}-{rs[-1]['time'][11:]} | "
          f"FPS med {s.median(fps) if fps else '-'} p10 {p10} min {fps[0] if fps else '-'} ({len(fps)} reads) | "
          f"GPU {m('gpu_util')}% enc {m('gpu_enc')}% {m('gpu_mhz')}MHz {m('gpu_w')}W | "
          f"CPU total {m('cpu_total')}% wardogs {m('cpu_wardogs')} streamlabs {m('cpu_streamlabs')} tiktok {m('cpu_tiktok')}")


tt = lambda x: (f(x["cpu_tiktok"]) or 0) > 0.3
enc = lambda x: (f(x["gpu_enc"]) or 0) > 0
seg("A TikTok on , no rec", [x for x in rows if tt(x) and not enc(x)])
seg("B TikTok off, no rec", [x for x in rows if not tt(x) and not enc(x)])
seg("C TikTok off, REC   ", [x for x in rows if not tt(x) and enc(x)])
seg("D TikTok on , REC   ", [x for x in rows if tt(x) and enc(x)])
print("last sample:", rows[-1]["time"])
