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
    "new_session_after_hours": 0,  # disabled: baseline only on go-live / Start session
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
        "invest": "ctrl+alt+5",
        "invest_undo": "ctrl+alt+6",
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
            "multi": 0, "last_kill_ts": 0, "last_death_ts": 0, "last_event_ts": 0,
            "cash_start": None, "cash_now": None, "cash_earned": 0,
            "cash_invested": 0, "cash_kits": 0, "cash_session_started": False,
            "cash_last_drop": None,  # {amount, ts, classified}
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
    # STREAM PROFIT = (cash_now - cash_start) + cash_invested
    # One-time unlocks/buys are invested (not losses). Per-life kits stay as costs.
    if s.get("cash_now") is not None and s.get("cash_start") is not None:
        raw = int(s["cash_now"]) - int(s["cash_start"])
        invested = int(s.get("cash_invested") or 0)
        s["cash_delta"] = raw + invested
        s["cash_raw_delta"] = raw
        s["cash_invested"] = invested
        s["cash_kits"] = int(s.get("cash_kits") or 0)
        # kits + other non-invest drops counted as spend
        s["cash_spent"] = max(0, int(s.get("cash_earned") or 0) - s["cash_delta"])
    else:
        s["cash_delta"] = None
        s["cash_raw_delta"] = None
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


THEMES = ["protocol", "fieldkit", "nvg", "stencil", "killfeed"]
CASH_MAX_STEP = 150_000  # a single balance jump bigger than this is treated as a misread, not income
CASH_DIP_MIN = 20_000     # drops at least this big are remembered for ...
CASH_DIP_WINDOW_S = 240   # ... 4 minutes, in case the old balance comes back (then the drop was a misread)
CASH_SPIKE_WINDOW_S = 240  # spike-then-drop misread mirror of dip-then-recover
CASH_INVEST_MIN = 5_000    # drops below this are never one-time unlocks
CASH_KIT_WINDOW_S = 60     # drops within this after death/respawn stay as kit costs
CASH_ROUND_MOD = 1_000     # "round number" unlock amounts


def is_round_amount(amount):
    """True when amount looks like a store unlock price (round thousands)."""
    a = abs(int(amount))
    return a >= CASH_INVEST_MIN and a % CASH_ROUND_MOD == 0


def classify_drop(amount, *, how=None, now=None, last_death_ts=0, kit_window_s=CASH_KIT_WINDOW_S):
    """Classify a balance drop as investment (one-time unlock/buy) or kit/cost.

    Investment when ALL hold:
      - amount >= $5K
      - NOT within ~60s after death/respawn
      - menu reading OR round amount
    Per-life kits (~$16K respawns) stay costs via the death window.
    """
    amt = abs(int(amount))
    now = time.time() if now is None else float(now)
    if amt < CASH_INVEST_MIN:
        return "kit" if amt > 0 else "none"
    if last_death_ts and (now - float(last_death_ts)) <= float(kit_window_s):
        return "kit"
    menu = (how or "") == "menu"
    if menu or is_round_amount(amt):
        return "invest"
    return "kit"


def start_cash_session(balance=None):
    """Explicit session baseline (go-live / Start session). Clears invest counters."""
    with LOCK:
        now = time.time()
        bal = balance if balance is not None else STATE.get("cash_now")
        STATE["cash_start"] = bal
        STATE["cash_now"] = bal
        STATE["cash_earned"] = 0
        STATE["cash_invested"] = 0
        STATE["cash_kits"] = 0
        STATE["cash_last_drop"] = None
        STATE["cash_dip"] = None
        STATE["cash_spike"] = None
        STATE["cash_session_started"] = True
        STATE["session_started"] = now
        STATE["last_event_ts"] = now
        save()
        broadcast({"type": "cash", "state": public()})
        return public()


def mark_last_drop_invested():
    """Manual override: move the last non-invest drop into cash_invested."""
    with LOCK:
        drop = STATE.get("cash_last_drop")
        if not drop or drop.get("classified") == "invest":
            return public()
        amt = int(drop.get("amount") or 0)
        if amt <= 0:
            return public()
        _push_history()
        if drop.get("classified") == "kit":
            STATE["cash_kits"] = max(0, int(STATE.get("cash_kits") or 0) - amt)
        STATE["cash_invested"] = int(STATE.get("cash_invested") or 0) + amt
        drop = dict(drop)
        drop["classified"] = "invest"
        drop["manual"] = True
        STATE["cash_last_drop"] = drop
        save()
        broadcast({"type": "cash", "state": public()})
        return public()


def undo_last_invest():
    """Undo the last manual (or auto) invest classification on the last drop."""
    with LOCK:
        drop = STATE.get("cash_last_drop")
        if not drop or drop.get("classified") != "invest":
            return public()
        amt = int(drop.get("amount") or 0)
        if amt <= 0:
            return public()
        _push_history()
        STATE["cash_invested"] = max(0, int(STATE.get("cash_invested") or 0) - amt)
        drop = dict(drop)
        drop["classified"] = "kit"
        drop["manual"] = False
        STATE["cash_kits"] = int(STATE.get("cash_kits") or 0) + amt
        STATE["cash_last_drop"] = drop
        save()
        broadcast({"type": "cash", "state": public()})
        return public()


def apply_cash(balance, how=None):
    """In-game balance reading. how is detector context ('menu'|'pair'|'right'|...).

    Profit = (cash_now - cash_start) + cash_invested.
    One-time unlocks/buys (>=$5K, not post-death, menu or round) add to cash_invested.
    """
    with LOCK:
        now = time.time()
        # Idle auto-reset removed for stream sessions; only explicit Start session / reset.
        gap = float(CFG.get("new_session_after_hours", 0) or 0) * 3600
        if gap > 0 and STATE["last_event_ts"] and now - STATE["last_event_ts"] > gap:
            do_reset(auto=True)
        STATE["last_event_ts"] = now
        # Do not auto-baseline on first OCR tick anymore — wait for Start session / go-live
        # unless a session was already started (or legacy state has cash_start set).
        if STATE.get("cash_start") is None and STATE.get("cash_session_started"):
            STATE["cash_start"] = balance
        elif STATE.get("cash_start") is None and not STATE.get("cash_session_started"):
            # Legacy / first-run convenience: first reading still seeds baseline so the
            # overlay is not stuck on "waiting" mid-stream after a hot deploy. Operator
            # can hit Start session to re-baseline cleanly.
            STATE["cash_start"] = balance
            STATE["cash_session_started"] = True
        prev = STATE.get("cash_now")
        if prev == balance:
            return public()
        dip = STATE.get("cash_dip")
        spike = STATE.get("cash_spike")
        if dip and now - dip[1] > CASH_DIP_WINDOW_S:
            dip = None
        if spike and now - spike[1] > CASH_SPIKE_WINDOW_S:
            spike = None  # window expired: earnings were real
        gain = balance - prev if prev is not None else 0
        skip_drop = False
        # Drop-then-recover misread (existing)
        if prev is not None and -gain >= CASH_DIP_MIN and not dip:
            dip = [prev, now]
        elif gain > 0 and dip and dip[0] - 2_000 <= balance <= dip[0] + 15_000:
            print(f"cash: {dip[0]} -> {prev} -> {balance} looks like a misread dip; counting {max(0, balance - dip[0])}",
                  flush=True)
            gain = max(0, balance - dip[0])
            dip = None
        # Spike-then-drop misread (mirror of dip): a large upward jump that FULLY reverses
        # back near the pre-spike baseline was OCR noise. Earn-then-spend does NOT reverse
        # all the way back, so it keeps the earnings and classifies the drop normally.
        if prev is not None and gain >= CASH_DIP_MIN and spike is None:
            spike = [prev, now, gain]  # base, ts, credited_gain
        elif gain < 0 and spike is not None:
            base, sts, credited = spike[0], spike[1], spike[2]
            drop_here = prev - balance
            # Only reverse when the entire spike fully unwinds back to the pre-spike
            # baseline (OCR flash). A partial spend (kit / unlock) after a real earn
            # must NOT wipe the earnings — end balance stays well above base, or the
            # drop is much smaller than the spike.
            full_unwind = (
                abs(balance - base) <= 2_000
                and abs(drop_here - credited) <= 5_000
                and drop_here >= 0.85 * max(1, credited)
            )
            if full_unwind:
                print(f"cash: {base} -> {prev} -> {balance} looks like a misread spike; reversing +{credited}",
                      flush=True)
                STATE["cash_earned"] = max(0, int(STATE.get("cash_earned") or 0) - int(credited))
                skip_drop = True
                gain = 0
                spike = None
                dip = None
            else:
                # Real spend after a real earn — drop the spike arm, keep earnings.
                spike = None
        if 0 < gain <= CASH_MAX_STEP:
            STATE["cash_earned"] = int(STATE.get("cash_earned") or 0) + gain
        # Spending / unlocks (skipped when the drop is just undoing a fake spike)
        if prev is not None and balance < prev and not skip_drop:
            drop_amt = prev - balance
            if 0 < drop_amt <= CASH_MAX_STEP:
                kind = classify_drop(
                    drop_amt,
                    how=how,
                    now=now,
                    last_death_ts=STATE.get("last_death_ts") or 0,
                )
                if kind == "invest":
                    STATE["cash_invested"] = int(STATE.get("cash_invested") or 0) + int(drop_amt)
                elif kind == "kit":
                    STATE["cash_kits"] = int(STATE.get("cash_kits") or 0) + int(drop_amt)
                STATE["cash_last_drop"] = {
                    "amount": int(drop_amt),
                    "ts": now,
                    "classified": kind,
                    "how": how,
                }
        STATE["cash_dip"] = dip
        STATE["cash_spike"] = spike
        STATE["cash_now"] = balance
        save()
        broadcast({"type": "cash", "state": public()})
        return public()


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
            STATE["last_death_ts"] = now
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
            if kind == "cash":
                s["cash_delta"] = (s.get("cash_delta") or 0) + n
            evt = {"type": kind, "test": True, "state": s}
            if kind == "death":
                evt["ended_streak"] = n
            broadcast(evt)
            return self._send(200, '{"ok":true,"test":true}')
        if p.startswith("/api/theme"):
            # /api/theme/<name> -- switch every overlay without touching the Streamlabs URL
            name = p.rsplit("/", 1)[-1]
            if name in THEMES:
                CFG["overlay_theme"] = name
                save_cfg()
                broadcast({"type": "theme", "name": name})
            return self._send(200, json.dumps({"theme": CFG.get("overlay_theme", "protocol"), "themes": THEMES}))
        if p in ("/api/cash/invest", "/api/invest"):
            return self._send(200, json.dumps(mark_last_drop_invested()))
        if p in ("/api/cash/invest/undo", "/api/invest/undo"):
            return self._send(200, json.dumps(undo_last_invest()))
        if p in ("/api/cash/session/start", "/api/session/start"):
            # optional ?balance=N; else uses current cash_now
            bal = None
            if "balance" in qs:
                try:
                    bal = int(qs.get("balance", ["0"])[0].replace(",", ""))
                except ValueError:
                    return self._send(400, '{"error":"balance must be a number"}')
            return self._send(200, json.dumps(start_cash_session(bal)))
        if p.startswith("/api/cash/"):
            # /api/cash/<balance> -- feed a balance by hand (testing, or a manual correction)
            # optional ?how=menu|pair|right
            try:
                bal = int(p.rsplit("/", 1)[-1].replace(",", ""))
            except ValueError:
                return self._send(400, '{"error":"balance must be a number"}')
            how = (qs.get("how") or [None])[0]
            return self._send(200, json.dumps(apply_cash(bal, how=how)))
        if p.startswith("/api/"):
            kind = p.rsplit("/", 1)[-1]
            if kind in ("kill", "death", "win", "undo", "reset", "invest"):
                if kind == "invest":
                    return self._send(200, json.dumps(mark_last_drop_invested()))
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
                                "overlay_sounds": bool(CFG.get("overlay_sounds", True)),
                                "theme": CFG.get("overlay_theme", "protocol")})
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
        elif action == "invest":
            mark_last_drop_invested()
        elif action == "invest_undo":
            undo_last_invest()
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
            if isinstance(ev, tuple) and ev[0] == "cash":
                # ("cash", balance) or ("cash", balance, how)
                how = ev[2] if len(ev) >= 3 else None
                apply_cash(ev[1], how=how)
            elif ev in ("kill", "death", "win"):
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
