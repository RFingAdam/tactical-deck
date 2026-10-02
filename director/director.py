"""Tactical Deck Director -- the brain between your stream deck, your soundboard engine,
Streamlabs and your game HUD.

  * Combos      one button runs a timed sequence (airstrike, comms on/off, clutch loop...)
  * Ducking     game/desktop audio dips while a stinger plays -- on the stream (Streamlabs
                fader) and in your headphones (engine-side) -- then glides back
  * Reactive    kills / deaths / wins / downs from the Protocol HUD fire board sounds
  * Settings    http://127.0.0.1:8781/  (everything above is editable live)

Stdlib only, plus `websocket-client` for Streamlabs ducking (optional).
Run:  pythonw director.py      (Windows, no console)   |   python director.py --debug
      python director.py --demo   (no audio, any OS: explore the UI, combos and reactive rules)
"""
from __future__ import annotations

import copy
import json
import logging
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from engines import make_engine  # noqa: E402
from slobs import StreamlabsDucker  # noqa: E402

CONFIG_DEFAULT = ROOT / "config.default.json"
CONFIG_USER = ROOT / "config.json"
LOG = logging.getLogger("director")


# ----------------------------------------------------------------------------- config
def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def load_config() -> dict:
    cfg = json.loads(CONFIG_DEFAULT.read_text(encoding="utf-8"))
    if CONFIG_USER.exists():
        try:
            cfg = deep_merge(cfg, json.loads(CONFIG_USER.read_text(encoding="utf-8-sig")))
        except ValueError as e:
            LOG.error("config.json invalid (%s); using defaults", e)
    return cfg


def validate_config(cfg: dict) -> None:
    d = cfg["duck"]
    if not -24 <= float(d["headphones_db"]) <= 0 or not -30 <= float(d["stream_db"]) <= 0:
        raise ValueError("Duck depths must be 0..-24 dB (headphones) and 0..-30 dB (stream)")
    for name, combo in cfg.get("combos", {}).items():
        for key in ("steps", "on", "off", "loop"):
            for step in combo.get(key, []):
                if not isinstance(step, list) or not step or step[0] not in ("play", "wait", "voice", "stop", "duck"):
                    raise ValueError(f"Combo {name}: bad step {step!r}")
                if step[0] == "wait" and not 0 <= float(step[1]) <= 30:
                    raise ValueError(f"Combo {name}: waits must be 0..30 s")


def save_config(cfg: dict) -> None:
    tmp = CONFIG_USER.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_USER)


# ----------------------------------------------------------------------------- director
class Director:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.engine = make_engine(cfg["engine"])
        self.ducker = StreamlabsDucker(cfg.get("streamlabs", {}))
        self.closed = threading.Event()
        self.combo_threads: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self.toggles: dict[str, bool] = {}
        self.events: list[dict] = []          # recent activity for the UI
        self.reactive_connected = False
        self.cooldowns: dict[str, float] = {}
        self.engine_status: dict = {}
        self.stream_ducked = False
        self._last_active = 0.0
        self.apply_engine_settings()
        self.apply_hud_settings()
        threading.Thread(target=self._duck_loop, daemon=True, name="duck").start()
        threading.Thread(target=self._reactive_loop, daemon=True, name="reactive").start()

    # -- helpers -----------------------------------------------------------------
    def log_event(self, kind: str, detail: str) -> None:
        with self.lock:
            self.events.insert(0, {"t": time.time(), "kind": kind, "detail": detail})
            del self.events[40:]
        LOG.info("%s: %s", kind, detail)

    def apply_engine_settings(self) -> None:
        try:
            d = self.cfg["duck"]
            self.engine.set_duck(float(d["headphones_db"]) if d["enabled"] else 0.0)
            self.engine.set_duck_exclude(d.get("exclude", []))
            self._engine_applied = True
        except Exception as e:  # engine may still be starting at boot; retried by the duck loop
            self._engine_applied = False
            LOG.warning("engine duck not applied yet: %s", e)

    def apply_hud_settings(self) -> None:
        r = self.cfg["reactive"]
        want_overlay = not (r["enabled"] and r.get("mute_overlay_sounds", True))
        try:
            state = "on" if want_overlay else "off"
            urllib.request.urlopen(self.cfg["hud"]["url"] + f"/api/overlaysounds/{state}", timeout=2).read()
        except Exception as e:
            LOG.warning("HUD overlay sound toggle not applied: %s", e)

    def update_config(self, new_cfg: dict) -> None:
        validate_config(new_cfg)
        with self.lock:
            self.cfg = new_cfg
            save_config(new_cfg)
            self.ducker.configure(new_cfg.get("streamlabs", {}))
        self.apply_engine_settings()
        self.apply_hud_settings()
        self.log_event("settings", "saved")

    # -- actions -------------------------------------------------------------------
    def play(self, clip: str, origin="deck") -> None:
        self.engine.play(clip)
        self.log_event(origin, f"play {clip}")

    def stop_all(self) -> None:
        for _, cancel in list(self.combo_threads.values()):
            cancel.set()
        # A toggle that changed state (Comms -> radio voice) must not stay stuck once its toggle is
        # cleared: replay only the state-restoring steps of its "off" branch, silently.
        for name, on in list(self.toggles.items()):
            if on:
                for step in self.cfg["combos"].get(name, {}).get("off", []):
                    try:
                        if step[0] == "voice":
                            self.engine.voice(step[1])
                        elif step[0] == "duck":
                            self.engine.set_duck(float(step[1]))
                    except Exception as e:
                        self.log_event("error", f"stop all: restoring {name}: {e}")
        self.toggles.clear()
        self.engine.stop()
        self.log_event("deck", "stop all")

    def _run_steps(self, steps, cancel: threading.Event) -> None:
        for step in steps:
            if cancel.is_set():
                return
            op = step[0]
            if op == "play":
                self.play(step[1], "combo")
            elif op == "wait":
                if cancel.wait(float(step[1])):
                    return
            elif op == "voice":
                self.engine.voice(step[1])
            elif op == "stop":
                self.engine.stop()
            elif op == "duck":
                self.engine.set_duck(float(step[1]))

    def combo(self, name: str) -> dict:
        combo = self.cfg["combos"].get(name)
        if not combo:
            raise KeyError(name)
        running = self.combo_threads.get(name)
        if combo.get("toggle"):
            turning_on = not self.toggles.get(name, False)
            if running:
                running[1].set()
            self.toggles[name] = turning_on
            if turning_on:
                cancel = threading.Event()

                def run_on():
                    self._run_steps(combo.get("on", []), cancel)
                    if combo.get("loop"):
                        deadline = time.monotonic() + float(combo.get("max_seconds", 90))
                        while not cancel.is_set() and time.monotonic() < deadline:
                            self._run_steps(combo["loop"], cancel)
                        if not cancel.is_set():          # timed out: run the off branch
                            self.toggles[name] = False
                            self._run_steps(combo.get("off", []), threading.Event())

                t = threading.Thread(target=run_on, daemon=True, name=f"combo-{name}")
                self.combo_threads[name] = (t, cancel)
                t.start()
            else:
                cancel = threading.Event()
                t = threading.Thread(target=self._run_steps, args=(combo.get("off", []), cancel), daemon=True)
                self.combo_threads[name] = (t, cancel)
                t.start()
            self.log_event("combo", f"{name} {'ON' if self.toggles[name] else 'OFF'}")
            return {"combo": name, "on": self.toggles[name]}
        if running and running[0].is_alive():
            running[1].set()                      # pressing again restarts it cleanly
        cancel = threading.Event()
        t = threading.Thread(target=self._run_steps, args=(combo.get("steps", []), cancel), daemon=True,
                             name=f"combo-{name}")
        self.combo_threads[name] = (t, cancel)
        t.start()
        self.log_event("combo", name)
        return {"combo": name, "started": True}

    # -- stream ducking ---------------------------------------------------------------
    def _duck_loop(self) -> None:
        self.ducker.restore_if_needed()
        while not self.closed.wait(0.05):
            try:
                st = self.engine.status()
                self.engine_status = st
                # Engine restarted (reboot/reload) or came up after us: re-apply our settings.
                if not getattr(self, "_engine_applied", False) or (
                        st.get("runtime") == "native-rust" and self.cfg["duck"]["enabled"]
                        and st.get("duck_db") != float(self.cfg["duck"]["headphones_db"])):
                    self.apply_engine_settings()
            except Exception:
                self.engine_status = {}
                if self.stream_ducked:
                    self.ducker.release(self.cfg["duck"])
                    self.stream_ducked = False
                time.sleep(0.5)
                continue
            d = self.cfg["duck"]
            if not d.get("enabled") or float(d.get("stream_db", 0)) >= 0:
                if self.stream_ducked:
                    self.ducker.release(d)
                    self.stream_ducked = False
                continue
            playing = [p for p in st.get("playing", []) if p not in d.get("exclude", [])]
            now = time.monotonic()
            if playing:
                self._last_active = now
                if not self.stream_ducked:
                    self.stream_ducked = self.ducker.duck(d)
            elif self.stream_ducked and now - self._last_active > float(d.get("hold_ms", 150)) / 1000:
                self.ducker.release(d)
                self.stream_ducked = False

    # -- reactive (Protocol HUD SSE) -------------------------------------------------------
    def _reactive_loop(self) -> None:
        while not self.closed.is_set():
            url = self.cfg["hud"]["url"] + "/events"
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    self.reactive_connected = True
                    for raw in resp:
                        line = raw.decode("utf-8", "replace").strip()
                        if line.startswith("data:"):
                            try:
                                self.on_hud_event(json.loads(line[5:]))
                            except Exception as e:
                                LOG.warning("bad HUD event: %s", e)
                        if self.closed.is_set():
                            return
            except Exception:
                pass
            self.reactive_connected = False
            self.closed.wait(3)

    def _cooldown_ok(self, key: str, seconds: float) -> bool:
        now = time.monotonic()
        if now - self.cooldowns.get(key, 0) < seconds:
            return False
        self.cooldowns[key] = now
        return True

    def on_hud_event(self, evt: dict) -> None:
        r = self.cfg["reactive"]
        kind = evt.get("type")
        if not r.get("enabled") or kind in (None, "hello", "reset", "undo", "soundpack", "overlay_sounds", "armreset"):
            return
        if evt.get("test") and not r.get("react_to_tests", True):
            return
        ev = r["events"]
        state = evt.get("state", {})
        choice = None
        if kind == "kill":
            streak, multi = int(state.get("streak", 0)), int(state.get("multi", 1))
            s, m = ev.get("streak", {}), ev.get("multi", {})
            if s.get("enabled") and streak and streak % int(s.get("every", 5)) == 0:
                choice = ("streak", s)
            elif m.get("enabled") and multi >= int(m.get("min", 2)):
                choice = ("multi", m)
            elif ev.get("kill", {}).get("enabled"):
                choice = ("kill", ev["kill"])
        elif kind in ev and ev[kind].get("enabled"):
            choice = (kind, ev[kind])
        if not choice:
            return
        key, rule = choice
        if not rule.get("clip") or not self._cooldown_ok(key, float(rule.get("cooldown", 1))):
            return
        try:
            self.play(rule["clip"], f"auto:{key}")
        except Exception as e:
            self.log_event("error", f"auto {key}: {e}")

    def status(self) -> dict:
        st = self.engine_status or {}
        return {"engine": {"ok": bool(st), "clips": st.get("clip_count"), "voice": st.get("voice"),
                           "playing": st.get("playing", []), "duck_db": st.get("duck_db"),
                           "callback_alive": st.get("callback_alive"), "errors": st.get("callback_errors")},
                "stream_duck": {"connected": self.ducker.connected, "ducked": self.stream_ducked,
                                "source": self.ducker.source_name, "error": self.ducker.error},
                "reactive": {"connected": self.reactive_connected, "enabled": self.cfg["reactive"]["enabled"]},
                "toggles": self.toggles, "events": self.events[:15]}


# ----------------------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    server_version = "TacticalDeck/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self) -> bool:
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in hosts and (origin is None or origin in {f"http://{h}" for h in hosts})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method):
        if not self._allowed():
            return self._send(403, {"error": "local requests only"})
        d: Director = self.server.director
        path = urlsplit(self.path).path.rstrip("/") or "/"
        body = b""
        if method == "POST":
            n = int(self.headers.get("Content-Length", "0") or 0)
            if n > 256_000:
                return self._send(413, {"error": "too large"})
            body = self.rfile.read(n)
        try:
            if path in ("/", "/ui"):
                return self._send(200, (ROOT / "ui.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")
            if path == "/api/status":
                return self._send(200, d.status())
            if path == "/api/config":
                if method == "POST":
                    d.update_config(json.loads(body))
                return self._send(200, d.cfg)
            if path == "/api/defaults":
                return self._send(200, json.loads(CONFIG_DEFAULT.read_text(encoding="utf-8")))
            if path == "/api/library":
                return self._send(200, d.engine.library())
            if path == "/api/trims" and method == "POST":
                return self._send(200, d.engine.apply_trims(json.loads(body)))
            if path.startswith("/play/"):
                d.play(path[6:])
                return self._send(200, {"ok": True})
            if path.startswith("/combo/"):
                return self._send(200, d.combo(path[7:]))
            if path == "/stop":
                d.stop_all()
                return self._send(200, {"ok": True})
            if path.startswith("/voice/"):
                d.engine.voice(path[7:])
                return self._send(200, {"ok": True})
            if path.startswith("/test/"):
                kind = path[6:]
                n = 2 if kind == "multi" else 5 if kind == "streak" else 1
                evt = {"type": "kill" if kind in ("multi", "streak") else kind, "test": True,
                       "state": {"multi": n, "streak": n}}
                d.cooldowns.clear()
                d.on_hud_event(evt)
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "unknown path"})
        except KeyError as e:
            return self._send(404, {"error": f"unknown: {e}"})
        except (ValueError, RuntimeError, OSError, urllib.error.URLError) as e:
            return self._send(409, {"error": str(e)})


def main():
    debug = "--debug" in sys.argv
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO,
                        filename=None if debug else str(ROOT / "director.log"),
                        format="%(asctime)s %(levelname)s %(message)s")
    global CONFIG_USER
    if "--demo" in sys.argv:  # demo settings live in their own file so your real config is untouched
        CONFIG_USER = ROOT / "config.demo.json"
    cfg = load_config()
    if "--demo" in sys.argv:
        cfg["engine"] = {**cfg["engine"], "type": "demo"}
        cfg["streamlabs"] = {**cfg.get("streamlabs", {}), "enabled": False}
    validate_config(cfg)
    server = ThreadingHTTPServer((cfg.get("host", "127.0.0.1"), int(cfg.get("port", 8781))), Handler)
    server.daemon_threads = True
    server.director = Director(cfg)
    LOG.info("Tactical Deck Director on http://%s:%s", *server.server_address)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.director.closed.set()
        server.director.ducker.release(cfg["duck"], force=True)


if __name__ == "__main__":
    main()
