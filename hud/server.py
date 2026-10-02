"""Protocol HUD - live Kills / Deaths / Wins overlay server for Streamlabs.

Stdlib only (Python 3.9+). Run:  pythonw server.py   (or double-click start-hud.cmd)
  Overlay (Streamlabs Browser Source, 1920x1080): http://127.0.0.1:8790/overlay
  Control panel:                                 http://127.0.0.1:8790/control
  Buttons (Companion / Stream Deck, GET or POST): /api/kill  /api/death  /api/win  /api/undo  /api/reset
"""
import json, os, sys, time, threading, queue, mimetypes, ctypes
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(ROOT, "config.json")
STATE_PATH = os.path.join(ROOT, "state.json")
SOUND_DIR = os.path.join(ROOT, "sounds")

DEFAULT_CFG = {
    "host": "127.0.0.1",
    "port": 8790,
    "new_session_after_hours": 4,
    "multikill_window_sec": 8,
    "hotkeys_enabled": True,
    "auto_detect": True,
    "game_boost": True,
    "sound_pack": "tactical",
    "detect_fps": 4,
    "game_exe": "WardogsClient-Win64-Shipping.exe",
    "hotkeys": {
        "kill": "ctrl+alt+1",
        "death": "ctrl+alt+2",
        "win": "ctrl+alt+3",
        "undo": "ctrl+alt+4",
        "reset": "ctrl+alt+0",
    },
}


def load_cfg():
    cfg = json.loads(json.dumps(DEFAULT_CFG))
    if os.path.exists(CFG_PATH):
        try:
            user = json.load(open(CFG_PATH, encoding="utf-8-sig"))
            hk = user.pop("hotkeys", None)
            cfg.update(user)
            if isinstance(hk, dict):
                cfg["hotkeys"].update(hk)
        except Exception as e:
            print("config.json error, using defaults:", e)
    else:
        json.dump(DEFAULT_CFG, open(CFG_PATH, "w", encoding="utf-8"), indent=2)
    return cfg


CFG = load_cfg()
LOCK = threading.RLock()
CLIENTS = set()
DETECTOR = None
BOOST = None
HISTORY = []  # snapshots for undo


def list_packs():
    if not os.path.isdir(SOUND_DIR):
        return {}
    return {d: pack_files(d) for d in sorted(os.listdir(SOUND_DIR)) if os.path.isdir(os.path.join(SOUND_DIR, d))}


def pack_files(pack):
    d = os.path.join(SOUND_DIR, os.path.basename(pack))
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.lower().endswith((".wav", ".mp3", ".ogg")))


def save_cfg():
    with open(CFG_PATH, "w", encoding="utf-8") as fh:
        json.dump(CFG, fh, indent=2)


def fresh():
    now = time.time()
    return {"kills": 0, "deaths": 0, "wins": 0, "streak": 0, "best_streak": 0,
            "multi": 0, "last_kill_ts": 0, "last_event_ts": 0,
            "session_started": now, "session_id": int(now)}


STATE = fresh()


def snapshot():
    return json.loads(json.dumps(STATE))


def save():
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(STATE, fh)
    os.replace(tmp, STATE_PATH)


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            STATE.update(json.load(open(STATE_PATH, encoding="utf-8")))
        except Exception as e:
            print("state.json unreadable, starting fresh:", e)


def build_id():
    """Changes whenever the overlay files change, so browser sources reload themselves."""
    return int(max(os.path.getmtime(os.path.join(ROOT, f)) for f in ("overlay.html", "overlay.js")))


def public():
    s = dict(STATE)
    s["kd"] = round(s["kills"] / max(1, s["deaths"]), 2)
    return s


def broadcast(evt):
    data = json.dumps(evt)
    for q in list(CLIENTS):
        try:
            q.put_nowait(data)
        except Exception:
            pass


def _push_history():
    HISTORY.append(snapshot())
    del HISTORY[:-100]


def do_reset(auto=False):
    _push_history()
    STATE.clear()
    STATE.update(fresh())
    save()
    broadcast({"type": "reset", "auto": auto, "state": public()})


def apply(kind):
    with LOCK:
        now = time.time()
        if kind == "reset":
            do_reset()
            return public()
        if kind == "undo":
            if HISTORY:
                prev = HISTORY.pop()
                STATE.clear()
                STATE.update(prev)
                save()
            broadcast({"type": "undo", "state": public()})
            return public()
        # First event after a long gap = a new stream -> start from zero.
        gap = float(CFG.get("new_session_after_hours", 4)) * 3600
        if gap > 0 and STATE["last_event_ts"] and now - STATE["last_event_ts"] > gap:
            do_reset(auto=True)
        _push_history()
        extra = {}
        if kind == "kill":
            STATE["kills"] += 1
            STATE["streak"] += 1
            STATE["best_streak"] = max(STATE["best_streak"], STATE["streak"])
            window = float(CFG.get("multikill_window_sec", 8))
            STATE["multi"] = STATE["multi"] + 1 if now - STATE["last_kill_ts"] <= window else 1
            STATE["last_kill_ts"] = now
        elif kind == "death":
            extra["ended_streak"] = STATE["streak"]
            STATE["deaths"] += 1
            STATE["streak"] = 0
            STATE["multi"] = 0
        elif kind == "win":
            STATE["wins"] += 1
        STATE["last_event_ts"] = now
        save()
        evt = {"type": kind, "state": public()}
        evt.update(extra)
        broadcast(evt)
        return public()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ProtocolHUD/1.0"

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, rel):
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            return self._send(404, '{"error":"missing file"}')
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        with open(path, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.route()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.route()

    def route(self):
        u = urlparse(self.path)
        p = u.path.rstrip("/") or "/"
        qs = parse_qs(u.query)
        if p in ("/", "/control"):
            return self._file("control.html")
        if p == "/overlay":
            return self._file("overlay.html")
        if p in ("/overlay.js", "/control.js"):
            return self._file(p[1:])
        if p == "/events":
            return self._sse()
        if p.startswith("/api/detector"):
            if DETECTOR is None:
                return self._send(200, json.dumps({"enabled": False, "running": False, "error": "auto-detect off in config.json"}))
            if p.endswith("/on"):
                DETECTOR.enabled = True
            elif p.endswith("/off"):
                DETECTOR.enabled = False
            DETECTOR.status["enabled"] = DETECTOR.enabled
            return self._send(200, json.dumps(DETECTOR.status))
        if p.startswith("/api/overlaysounds"):
            # /api/overlaysounds/on|off -- used by the Tactical Deck Director's reactive mode
            if p.endswith("/on") or p.endswith("/off"):
                CFG["overlay_sounds"] = p.endswith("/on")
                save_cfg()
                broadcast({"type": "overlay_sounds", "on": CFG["overlay_sounds"]})
            return self._send(200, json.dumps({"overlay_sounds": bool(CFG.get("overlay_sounds", True))}))
        if p == "/api/boost":
            return self._send(200, json.dumps(BOOST.status if BOOST else {"active": False, "error": "game_boost off"}))
        if p == "/api/state":
            return self._send(200, json.dumps(public()))
        if p == "/api/sounds":
            pack = CFG.get("sound_pack", "tactical")
            return self._send(200, json.dumps({"pack": pack, "files": pack_files(pack)}))
        if p == "/api/soundpacks":
            return self._send(200, json.dumps({"active": CFG.get("sound_pack"), "packs": list_packs()}))
        if p.startswith("/api/soundpack/"):
            name = p.rsplit("/", 1)[-1]
            if name != "synth" and name not in list_packs():
                return self._send(404, '{"error":"no such pack"}')
            CFG["sound_pack"] = name
            save_cfg()
            broadcast({"type": "soundpack", "pack": name})
            return self._send(200, json.dumps({"active": name}))
        if p.startswith("/sounds/"):
            parts = [os.path.basename(x) for x in p.split("/")[2:4]]
            return self._file(os.path.join("sounds", *parts))
        if p.startswith("/api/test/"):
            kind = p.rsplit("/", 1)[-1]
            try:
                n = max(1, int(qs.get("n", ["1"])[0]))
            except ValueError:
                n = 1
            s = public()
            s.update({"multi": n, "streak": n})
            evt = {"type": kind, "test": True, "state": s}
            if kind == "death":
                evt["ended_streak"] = n
            broadcast(evt)
            return self._send(200, '{"ok":true,"test":true}')
        if p.startswith("/api/"):
            kind = p.rsplit("/", 1)[-1]
            if kind in ("kill", "death", "win", "undo", "reset"):
                return self._send(200, json.dumps(apply(kind)))
        return self._send(404, '{"error":"not found"}')

    def _sse(self):
        q = queue.Queue(maxsize=200)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        CLIENTS.add(q)
        try:
            hello = json.dumps({"type": "hello", "build": build_id(), "state": public(),
                                "overlay_sounds": bool(CFG.get("overlay_sounds", True))})
            self.wfile.write(("retry: 1500\ndata: " + hello + "\n\n").encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(("data: " + msg + "\n\n").encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except Exception:
            pass
        finally:
            CLIENTS.discard(q)
            self.close_connection = True


# ---------- global hotkeys (Windows) ----------
MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
NAMED_VK = {"backspace": 0x08, "tab": 0x09, "enter": 0x0D, "space": 0x20, "pageup": 0x21,
            "pagedown": 0x22, "end": 0x23, "home": 0x24, "insert": 0x2D, "delete": 0x2E,
            "minus": 0xBD, "equals": 0xBB}


def vk_of(key):
    key = key.strip().lower()
    if key in NAMED_VK:
        return NAMED_VK[key]
    if len(key) == 1 and key.isalnum():
        return ord(key.upper())
    if key.startswith("num") and key[3:].isdigit():
        return 0x60 + int(key[3:])
    if key.startswith("f") and key[1:].isdigit():
        return 0x6F + int(key[1:])
    raise ValueError("unknown key " + key)


def hotkey_loop():
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    ids = {}
    for i, (action, combo) in enumerate(CFG["hotkeys"].items(), start=1):
        if not combo:
            continue
        try:
            parts = combo.lower().split("+")
            mods = 0x4000  # MOD_NOREPEAT
            for m in parts[:-1]:
                mods |= MODS[m.strip()]
            if user32.RegisterHotKey(None, i, mods, vk_of(parts[-1])):
                ids[i] = action
                print(f"hotkey {combo} -> {action}")
            else:
                print(f"hotkey {combo} is taken by another app, skipped")
        except Exception as e:
            print(f"bad hotkey {combo}: {e}")
    armed = 0.0
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message != 0x0312:  # WM_HOTKEY
            continue
        action = ids.get(msg.wParam)
        if action == "reset":  # press twice within 2 s so it can't fire by accident
            now = time.time()
            if now - armed < 2:
                armed = 0.0
                apply("reset")
            else:
                armed = now
                broadcast({"type": "armreset"})
        elif action:
            apply(action)


if __name__ == "__main__":
    if sys.stdout is None or os.path.basename(sys.executable).lower().startswith("pythonw"):
        sys.stdout = sys.stderr = open(os.path.join(ROOT, "hud.log"), "a", buffering=1, encoding="utf-8")
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "starting")
    load_state()
    os.makedirs(SOUND_DIR, exist_ok=True)
    if CFG.get("hotkeys_enabled") and sys.platform == "win32":
        threading.Thread(target=hotkey_loop, daemon=True).start()
    if CFG.get("auto_detect") and sys.platform == "win32":
        from autodetect import AutoDetect
        def emit(ev):
            if ev in ("kill", "death", "win"):
                apply(ev)
            else:
                broadcast({"type": ev, "state": public()})
        DETECTOR = AutoDetect(emit, CFG.get("game_exe", "WardogsClient-Win64-Shipping.exe"), CFG.get("detect_fps", 4))
        DETECTOR.start()
    if CFG.get("game_boost", True) and sys.platform == "win32":
        try:
            from boost import GameBoost
            BOOST = GameBoost()
            BOOST.start()
        except Exception as e:
            print("game boost unavailable:", e)
    srv = ThreadingHTTPServer((CFG["host"], int(CFG["port"])), Handler)
    srv.daemon_threads = True
    print(f"Protocol HUD running: http://{CFG['host']}:{CFG['port']}/control")
    srv.serve_forever()
