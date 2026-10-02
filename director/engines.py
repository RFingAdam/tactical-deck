"""Soundboard engine adapters.

haven  -- Haven FX v2 native worker (Voicemeeter insert: clips go to your mic bus, voice FX,
          sample-accurate headphone ducking). This is what the author runs.
local  -- self-contained player for everyone else: mixes clips from a pack folder and plays them
          to any output device (e.g. a VB-Cable input that your mic/stream captures).
demo   -- no audio at all: simulates playback from the pack's clip lengths, so you can explore the
          settings UI, combos and reactive rules on any OS (`python director.py --demo`).
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path


def _http(method: str, url: str, timeout=3.0):
    req = urllib.request.Request(url, data=b"" if method == "POST" else None, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


class HavenEngine:
    VOICES = {"normal", "radio", "megaphone", "robot"}

    def __init__(self, cfg: dict):
        self.url = cfg.get("url", "http://127.0.0.1:8778").rstrip("/")
        self.v2 = Path(cfg["haven_v2_dir"]) if cfg.get("haven_v2_dir") else None

    def play(self, clip: str):
        _http("POST", f"{self.url}/sfx/{clip}")

    def stop(self):
        _http("POST", f"{self.url}/stop")

    def voice(self, mode: str):
        if mode not in self.VOICES:
            raise ValueError(f"voice must be one of {sorted(self.VOICES)}")
        _http("POST", f"{self.url}/voice/{mode}")

    def set_duck(self, db: float):
        _http("POST", f"{self.url}/duck/{db:g}")

    def set_duck_exclude(self, clips):
        _http("POST", f"{self.url}/duck_exclude/{','.join(clips) or '-'}")

    def status(self) -> dict:
        return _http("GET", f"{self.url}/status", timeout=1.0)

    def library(self) -> dict:
        lib = _http("GET", f"{self.url}/library")
        return {"clips": [{"id": c["id"], "label": c.get("label", c["id"]), "category": c.get("category", ""),
                           "seconds": c.get("seconds") or c.get("duration"), "trim_db": c.get("trim_db", 0),
                           "empty": bool(c.get("empty"))} for c in lib.get("clips", [])]}

    def apply_trims(self, trims: dict) -> dict:
        """Per-button level. Edits Haven's library, validates with Haven's own loader, reloads."""
        if not self.v2:
            raise RuntimeError("Set engine.haven_v2_dir in config to edit clip levels")
        path = self.v2 / "sound-library.json"
        lib = json.loads(path.read_text(encoding="utf-8"))
        changed = 0
        for clip in lib["clips"]:
            if clip["id"] in trims:
                value = float(trims[clip["id"]])
                if not -30 <= value <= 0:
                    raise ValueError("trim must be -30..0 dB")
                if clip.get("trim_db") != value:
                    clip["trim_db"] = value
                    changed += 1
        if not changed:
            return {"changed": 0}
        cand = self.v2 / "sound-library.director-candidate.json"
        cand.write_text(json.dumps(lib, indent=2), encoding="utf-8")
        check = subprocess.run([sys.executable, "-c",
                                f"import sys; sys.path.insert(0, r'{self.v2}'); from settings import load_library; "
                                f"load_library(r'{cand}')"], capture_output=True, text=True, timeout=30)
        if check.returncode:
            cand.unlink(missing_ok=True)
            raise RuntimeError("Haven rejected the library: " + check.stderr.strip()[-300:])
        cand.replace(path)
        reload = subprocess.run([sys.executable, str(self.v2 / "reload_library.py")], cwd=self.v2,
                                capture_output=True, text=True, timeout=90)
        if reload.returncode:
            raise RuntimeError("Engine reload failed: " + reload.stderr.strip()[-300:])
        return {"changed": changed, "reload": reload.stdout.strip()[-300:]}


class LocalEngine:
    """Minimal mixer: up to 8 voices, clips loaded from a pack folder (pack.json + WAVs)."""

    def __init__(self, cfg: dict):
        import numpy as np
        import sounddevice as sd
        from scipy.io import wavfile

        self.np, self.sd = np, sd
        self.rate = 48000
        self.gain = 10 ** (float(cfg.get("gain_db", 0)) / 20)
        self.clips, self.meta = {}, []
        for folder in cfg.get("packs", ["../sounds/tactical"]):
            folder = (Path(__file__).parent / folder).resolve()
            pack = json.loads((folder / "pack.json").read_text())
            for c in pack["clips"]:
                rate, x = wavfile.read(folder / c["file"])
                x = x.astype("float32")
                if x.ndim == 2:
                    x = x.mean(axis=1)
                self.clips[c["id"]] = x
                self.meta.append({"id": c["id"], "label": c["label"], "category": c["category"],
                                  "seconds": c["seconds"], "trim_db": 0, "empty": False})
        self.voices: list[list] = []
        self.lock = threading.Lock()
        self.stream = sd.OutputStream(samplerate=self.rate, channels=2, dtype="float32",
                                      device=cfg.get("device"), callback=self._callback, blocksize=512)
        self.stream.start()

    def _callback(self, out, frames, _time, _status):
        np = self.np
        mix = np.zeros(frames, dtype=np.float32)
        with self.lock:
            alive = []
            for v in self.voices:
                clip, pos = self.clips[v[0]], v[1]
                chunk = clip[pos:pos + frames]
                mix[:len(chunk)] += chunk
                v[1] += frames
                if v[1] < len(clip):
                    alive.append(v)
            self.voices = alive[-8:]
        out[:] = np.repeat(np.clip(mix * self.gain * 4.0, -1, 1)[:, None], 2, axis=1)  # pack is mastered ~-22 dBFS

    def play(self, clip):
        if clip not in self.clips:
            raise KeyError(clip)
        with self.lock:
            self.voices = [v for v in self.voices if v[0] != clip] + [[clip, 0]]

    def stop(self):
        with self.lock:
            self.voices = []

    def voice(self, mode):
        raise RuntimeError("voice effects need the Haven engine")

    def set_duck(self, db):
        pass  # local engine cannot reach desktop audio; stream ducking still works via Streamlabs

    def set_duck_exclude(self, clips):
        pass

    def status(self):
        with self.lock:
            playing = [v[0] for v in self.voices]
        return {"clip_count": len(self.clips), "playing": playing, "voice": "n/a", "callback_alive": True,
                "callback_errors": 0, "duck_db": 0}

    def library(self):
        return {"clips": self.meta}

    def apply_trims(self, trims):
        raise RuntimeError("Edit levels in the pack for the local engine")


def _load_packs(cfg: dict) -> list[dict]:
    clips = []
    for folder in cfg.get("packs", ["../sounds/tactical"]):
        folder = (Path(__file__).parent / folder).resolve()
        clips += json.loads((folder / "pack.json").read_text())["clips"]
    return clips


class DemoEngine:
    """Silent engine: tracks what *would* be playing, using each clip's real length."""

    VOICES = HavenEngine.VOICES

    def __init__(self, cfg: dict):
        self.meta = [{"id": c["id"], "label": c["label"], "category": c["category"], "seconds": c["seconds"],
                      "trim_db": 0, "empty": False} for c in _load_packs(cfg)]
        self.length = {c["id"]: float(c["seconds"]) for c in self.meta}
        self.until: dict[str, float] = {}
        self.mode, self.duck_db, self.exclude = "normal", 0.0, []
        self.lock = threading.Lock()

    def play(self, clip):
        if clip not in self.length:
            raise KeyError(clip)
        with self.lock:
            self.until[clip] = time.monotonic() + self.length[clip]

    def stop(self):
        with self.lock:
            self.until.clear()

    def voice(self, mode):
        if mode not in self.VOICES:
            raise ValueError(f"voice must be one of {sorted(self.VOICES)}")
        self.mode = mode

    def set_duck(self, db):
        self.duck_db = float(db)

    def set_duck_exclude(self, clips):
        self.exclude = list(clips)

    def status(self):
        now = time.monotonic()
        with self.lock:
            self.until = {k: v for k, v in self.until.items() if v > now}
            playing = list(self.until)
        return {"runtime": "demo", "clip_count": len(self.meta), "playing": playing, "voice": self.mode,
                "callback_alive": True, "callback_errors": 0, "duck_db": self.duck_db}

    def library(self):
        return {"clips": self.meta}

    def apply_trims(self, trims):
        for c in self.meta:
            if c["id"] in trims:
                c["trim_db"] = float(trims[c["id"]])
        return {"changed": len(trims)}


def make_engine(cfg: dict):
    kind = cfg.get("type", "local")
    if kind == "haven":
        return HavenEngine(cfg)
    if kind == "local":
        return LocalEngine(cfg)
    if kind == "demo":
        return DemoEngine(cfg)
    raise ValueError(f"unknown engine type {kind}")
