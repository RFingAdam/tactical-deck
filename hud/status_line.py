"""Game status line: one short, code-only line that tells a muted viewer what is happening.

No AI, no network. Fed by events the HUD already detects (kill / death / downed / revived / win)
and two level signals read from OCR text the detector already produces (no extra OCR passes):
  deploying -> "WAITING TO DEPLOY..." in the centre respawn region
  flying    -> helicopter pilot key hints ("COLLECTIVE LIFT / DESCENT", "DEPLOY FLARES") in the lower-right hint region
  riding    -> "CHANGE SEAT" key hint without the pilot hints (passenger / any vehicle seat)
"""
import re, threading, time

IDLE_TEXT = "WARDOGS S3 - LIVE WITH THE SQUAD"

RE_DEPLOY = re.compile(r"WA[I1l|]T[I1l|]NG\s*T[O0]\s*DEPL[O0]Y", re.I)
# Pilot-only key hints. OCR mangles them ("COLLECTIVE UFT", "C LECTIVELIFT", "COLLECTiVEOESCENT"), so match loosely.
# Validated 2026-11-22 on 1,581 frames from two stream recordings: 0 false positives, ~84% per-frame recall in the pilot seat.
RE_FLYING = re.compile(r"LLECT[I1l|i]V|DEPL[O0]Y\s*F[L|I1]?[A4]RES|C[O0]LLECT[I1l|i]", re.I)
# "CHANGE SEAT" only shows inside a vehicle (heli passenger, APC, truck). Can't tell air from ground, so the line is
# "RIDING IN", not "FLYING IN".
RE_SEAT = re.compile(r"CH?[A4]NGE\s*SE[A4]T|ANGE\s*SE[A4]T", re.I)

# priority: higher wins. A shown line also stays up at least MIN_HOLD_S unless something higher replaces it.
PRIORITY = {"downed": 90, "kia": 80, "deploying": 70, "victory": 60, "flying": 50, "riding": 45,
            "streak": 40, "back": 30, "revived": 30, "idle": 0}


def streak_text(n):
    if n >= 5:
        return f"{n} KILL STREAK - ON A TEAR"
    return f"{n} KILL STREAK"


class StatusLine:
    MIN_HOLD_S = 4.0        # anti-flicker: a line stays at least this long unless outranked
    DOWN_MAX_S = 75.0       # a missed revive must not leave "DOWN" up while he's fighting
    KIA_MAX_S = 30.0        # "KIA" until the deploy screen shows (or this times out)
    STREAK_HOLD_S = 20.0    # after the last kill
    STREAK_MIN = 3
    BACK_HOLD_S = 10.0      # "BACK IN THE FIGHT" after the deploy screen goes away
    REVIVED_HOLD_S = 8.0
    VICTORY_HOLD_S = 30.0
    SIGNALS = ("deploying", "flying", "riding")
    SIGNAL_GRACE_S = {"deploying": 3.0, "flying": 5.0, "riding": 5.0}   # on this long after last seen
    SIGNAL_HITS = {"deploying": 2, "flying": 2, "riding": 3}            # consecutive sightings to turn on

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.RLock()
        self.streak = 0
        self.down_ts = None
        self.kia_ts = None
        self.last_kill_ts = None
        self.back_until = 0.0
        self.revived_until = 0.0
        self.victory_until = 0.0
        self.sig_last = {k: None for k in self.SIGNALS}
        self.sig_hits = {k: 0 for k in self.SIGNALS}
        self.sig_on = {k: False for k in self.SIGNALS}
        self.shown = ("idle", IDLE_TEXT)
        self.shown_ts = 0.0
        self.demo = None
        self.listeners = []

    # ---------- inputs ----------
    def on_event(self, kind, streak=None, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            if kind == "kill":
                self.streak = streak if streak is not None else self.streak + 1
                self.last_kill_ts = now
                self.down_ts = None          # can't be down and scoring
            elif kind == "death":
                self.streak = 0
                self.down_ts = None
                self.kia_ts = now
                self.last_kill_ts = None
            elif kind == "downed":
                self.down_ts = now
            elif kind == "revived":
                if self.down_ts is not None:
                    self.revived_until = now + self.REVIVED_HOLD_S
                self.down_ts = None
            elif kind == "win":
                self.victory_until = now + self.VICTORY_HOLD_S
            elif kind == "reset":
                self.streak = 0
                self.down_ts = self.kia_ts = self.last_kill_ts = None
        self._changed(now)

    def on_frame_text(self, respawn_text="", side_text="", now=None):
        """Called once per detector frame with OCR text the detector already read (None = not read this frame)."""
        now = self.clock() if now is None else now
        seen = {}
        if respawn_text is not None:
            seen["deploying"] = bool(RE_DEPLOY.search(respawn_text))
        if side_text is not None:
            fly = bool(RE_FLYING.search(side_text))
            seen["flying"] = fly
            seen["riding"] = (not fly) and bool(RE_SEAT.search(side_text))
        self.on_signals(seen, now)

    def on_signals(self, seen, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            for name, hit in seen.items():
                if hit:
                    self.sig_hits[name] += 1
                    self.sig_last[name] = now
                    if self.sig_hits[name] >= self.SIGNAL_HITS[name] and not self.sig_on[name]:
                        self.sig_on[name] = True
                        if name == "deploying":
                            self.kia_ts = None       # death screen is over, he's on the deploy screen
                            self.down_ts = None
                        if name in ("flying", "riding"):
                            self.down_ts = None      # in a seat = alive and up
                            self.kia_ts = None
                else:
                    self.sig_hits[name] = 0
            self._expire_signals(now)
        self._changed(now)

    def _expire_signals(self, now):
        for name in self.sig_on:
            if self.sig_on[name] and (self.sig_last[name] is None or now - self.sig_last[name] > self.SIGNAL_GRACE_S[name]):
                self.sig_on[name] = False
                if name == "deploying":
                    self.back_until = now + self.BACK_HOLD_S

    # ---------- output ----------
    def candidate(self, now):
        if self.down_ts is not None and now - self.down_ts > self.DOWN_MAX_S:
            self.down_ts = None
        if self.kia_ts is not None and now - self.kia_ts > self.KIA_MAX_S:
            self.kia_ts = None
        if self.last_kill_ts is not None and now - self.last_kill_ts > self.STREAK_HOLD_S:
            self.last_kill_ts = None
        if self.down_ts is not None:
            return "downed", "DOWN - WAITING ON A REVIVE"
        if self.kia_ts is not None:
            return "kia", "KIA - REDEPLOYING"
        if self.sig_on["deploying"]:
            return "deploying", "DEPLOYING"
        if now < self.victory_until:
            return "victory", "VICTORY"
        if self.sig_on["flying"]:
            return "flying", "FLYING IN"
        if self.sig_on["riding"]:
            return "riding", "RIDING IN"
        if self.last_kill_ts is not None and self.streak >= self.STREAK_MIN:
            return "streak", streak_text(self.streak)
        if now < self.revived_until:
            return "revived", "BACK UP - REVIVED"
        if now < self.back_until:
            return "back", "BACK IN THE FIGHT"
        return "idle", IDLE_TEXT

    def current(self, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            self._expire_signals(now)
            state, text = self.candidate(now)
            if (state, text) != self.shown:
                held = now - self.shown_ts
                outranks = PRIORITY[state] > PRIORITY[self.shown[0]]
                same_state_update = state == self.shown[0]   # 3 -> 4 kill streak
                # The hold only delays dropping back to the idle line; a real change (revived, deploying, in a seat)
                # shows at once. Level signals are already debounced (hits + grace), so they can't flicker.
                if outranks or same_state_update or state != "idle" or held >= self.MIN_HOLD_S:
                    self.shown = (state, text)
                    self.shown_ts = now
            return {"state": self.shown[0], "text": self.shown[1], "since": round(self.shown_ts, 2),
                    "streak": self.streak, "signals": dict(self.sig_on), "demo": False}

    def _changed(self, now):
        snap = self.current(now)
        for fn in list(self.listeners):
            try:
                fn(snap)
            except Exception as e:
                print("status listener error:", e)


DEMO_STATES = {
    "idle": ("idle", IDLE_TEXT),
    "flying": ("flying", "FLYING IN"),
    "riding": ("riding", "RIDING IN"),
    "deploying": ("deploying", "DEPLOYING"),
    "downed": ("downed", "DOWN - WAITING ON A REVIVE"),
    "kia": ("kia", "KIA - REDEPLOYING"),
    "streak3": ("streak", streak_text(3)),
    "streak5": ("streak", streak_text(5)),
    "back": ("back", "BACK IN THE FIGHT"),
    "revived": ("revived", "BACK UP - REVIVED"),
    "victory": ("victory", "VICTORY"),
}
