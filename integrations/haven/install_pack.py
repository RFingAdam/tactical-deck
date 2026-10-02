"""Install a TacticalDeck sound pack into the Haven FX v2 library (ABI 5 / up to 64 clips).

    python install_pack.py PACK_DIR HAVEN_V2_DIR [--reload]

Existing clips are never touched; pack clips are appended (or refreshed in place if their id
already exists). The candidate library is validated with Haven's own load_library() before it
replaces sound-library.json, and a timestamped backup is written first.
"""
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    pack_dir, v2 = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    sys.path.insert(0, str(v2))
    from settings import load_library  # Haven's validator

    pack = json.loads((pack_dir / "pack.json").read_text())
    lib_path = v2 / "sound-library.json"
    library = json.loads(lib_path.read_text(encoding="utf-8"))
    dest = v2 / "library" / "tactical"
    dest.mkdir(parents=True, exist_ok=True)
    by_id = {c["id"]: i for i, c in enumerate(library["clips"])}
    added, refreshed = [], []
    for clip in pack["clips"]:
        src = pack_dir / clip["file"]
        target = dest / clip["file"]
        shutil.copy2(src, target)
        entry = {"id": clip["id"], "label": clip["label"], "category": "Tactical / " + clip["category"],
                 "file": target.relative_to(v2).as_posix(), "trim_db": 0,
                 "source": "TacticalDeck synth/tactical_pack.py (original synthesis)",
                 "license": clip["license"], "usage": clip["usage"], "seconds": clip["seconds"],
                 "peak": clip["peak"], "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        if clip["id"] in by_id:
            keep_trim = library["clips"][by_id[clip["id"]]].get("trim_db", 0)
            entry["trim_db"] = keep_trim
            library["clips"][by_id[clip["id"]]] = entry
            refreshed.append(clip["id"])
        else:
            library["clips"].append(entry)
            added.append(clip["id"])
    candidate = v2 / "sound-library.candidate.json"
    candidate.write_text(json.dumps(library, indent=2), encoding="utf-8")
    load_library(candidate)  # raises on any problem; live library untouched
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(lib_path, v2 / f"sound-library.before-tactical-{stamp}.json")
    candidate.replace(lib_path)
    print(json.dumps({"added": added, "refreshed": refreshed, "total_clips": len(library["clips"])}))
    if "--reload" in sys.argv:
        subprocess.run([sys.executable, str(v2 / "reload_library.py")], cwd=v2, check=True)


if __name__ == "__main__":
    main()
