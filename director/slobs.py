"""Streamlabs Desktop remote-control client used to duck the game/desktop source on stream.

Auth token: Streamlabs -> Settings -> Remote Control -> show details (paste into config.json
"streamlabs.token"), or leave empty and the token is read from a local Bitfocus Companion
'slobs' connection if you have one.  The token is never logged.

Crash safety: before every duck the original fader value is written to duck_state.json; on the
next start it is restored, so a crash can never leave your game audio stuck quiet.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from pathlib import Path

LOG = logging.getLogger("director.slobs")
STATE = Path(__file__).resolve().parent / "duck_state.json"


class StreamlabsDucker:
    def __init__(self, cfg: dict):
        self.lock = threading.RLock()
        self.ws = None
        self.msg_id = 0
        self.connected = False
        self.error = None
        self.resource = None
        self.base_mul = None
        self.source_name = None
        self._next_try = 0.0
        self.configure(cfg)

    def configure(self, cfg: dict) -> None:
        with self.lock:
            self.cfg = cfg or {}
            self.source_name = self.cfg.get("duck_source") or None
            self.resource = None

    # -- transport --------------------------------------------------------------------
    def _token(self) -> str:
        if self.cfg.get("token"):
            return self.cfg["token"]
        url = self.cfg.get("token_from_companion")
        if url:
            full = json.loads(urllib.request.urlopen(
                url.rstrip("/") + "/int/export/full?includeSecrets=true&format=json", timeout=5).read())
            for inst in full.get("instances", {}).values():
                if inst.get("label") == self.cfg.get("companion_label", "slobs"):
                    tok = inst.get("secrets", {}).get("token") or inst.get("config", {}).get("token")
                    if tok:
                        return tok
        raise RuntimeError("No Streamlabs token (set streamlabs.token in config)")

    def _connect(self) -> bool:
        if self.ws is not None:
            return True
        if time.monotonic() < self._next_try:
            return False
        self._next_try = time.monotonic() + 10
        try:
            import websocket  # websocket-client
            ws = websocket.create_connection(
                f"ws://127.0.0.1:{int(self.cfg.get('port', 59650))}/api/000/tacticaldeck/websocket", timeout=5)
            if ws.recv() != "o":
                raise RuntimeError("unexpected Streamlabs transport")
            self.ws = ws
            if not self._call("TcpServerService", "auth", self._token()):
                raise RuntimeError("Streamlabs rejected the token")
            self.connected, self.error = True, None
            return True
        except Exception as e:  # Streamlabs closed, token missing, module missing...
            self._drop(str(e))
            return False

    def _drop(self, err=None):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        self.ws, self.connected, self.resource = None, False, None
        if err:
            self.error = err
            LOG.warning("streamlabs: %s", err)

    def _call(self, resource, method, *args):
        self.msg_id += 1
        payload = {"jsonrpc": "2.0", "id": self.msg_id, "method": method,
                   "params": {"resource": resource, "args": list(args)}}
        self.ws.send(json.dumps([json.dumps(payload)]))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            frame = self.ws.recv()
            if not frame.startswith("a"):
                continue
            for raw in json.loads(frame[1:]):
                value = json.loads(raw)
                if value.get("id") == self.msg_id:
                    if "error" in value:
                        raise RuntimeError(str(value["error"]))
                    return value.get("result")
        raise RuntimeError("Streamlabs call timed out")

    def _find_source(self):
        if self.resource:
            return True
        for src in self._call("AudioService", "getSources"):
            if (self.source_name and src.get("name") == self.source_name) or \
               (not self.source_name and src.get("sourceId", "").startswith("wasapi_output_capture")):
                self.resource, self.source_name = src["resourceId"], src["name"]
                return True
        raise RuntimeError(f"Audio source '{self.source_name}' not found in Streamlabs")

    def _current_mul(self) -> float:
        for src in self._call("AudioService", "getSources"):
            if src.get("resourceId") == self.resource:
                return float(src["fader"]["mul"])
        raise RuntimeError("duck source disappeared")

    def _ramp(self, start: float, end: float, ms: float, steps: int):
        steps = max(1, steps)
        for i in range(1, steps + 1):
            self._call(self.resource, "setMul", start + (end - start) * i / steps)
            if i < steps:
                time.sleep(ms / 1000 / steps)

    # -- public ------------------------------------------------------------------------------
    def duck(self, dcfg: dict) -> bool:
        if not self.cfg.get("enabled", True):
            return False
        with self.lock:
            if not self._connect():
                return False
            try:
                self._find_source()
                base = self._current_mul()
                self.base_mul = base
                STATE.write_text(json.dumps({"resource": self.resource, "base_mul": base}))
                target = base * 10 ** (float(dcfg.get("stream_db", -8)) / 20)
                self._ramp(base, target, float(dcfg.get("stream_attack_ms", 60)), 3)
                return True
            except Exception as e:
                self._drop(str(e))
                return False

    def release(self, dcfg: dict, force=False) -> None:
        with self.lock:
            if self.base_mul is None and not STATE.exists():
                return
            if not self._connect():
                return
            try:
                if self.base_mul is None:
                    st = json.loads(STATE.read_text())
                    self.resource, self.base_mul = st["resource"], float(st["base_mul"])
                self._find_source()
                cur = self._current_mul()
                steps = 2 if force else 6
                self._ramp(cur, self.base_mul, 0 if force else float(dcfg.get("stream_release_ms", 450)), steps)
                self.base_mul = None
                STATE.unlink(missing_ok=True)
            except Exception as e:
                self._drop(str(e))

    def restore_if_needed(self) -> None:
        if STATE.exists():
            LOG.info("restoring stream fader left ducked by a previous run")
            for _ in range(30):                 # Streamlabs may still be starting at boot
                self.release({}, force=True)
                if not STATE.exists():
                    return
                time.sleep(10)
