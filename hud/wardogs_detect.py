"""Reads Wardogs HUD text with the built-in Windows OCR engine and turns it into kill / death events.

Signals (calibrated on Wardogs highlight clips, 3440x1440 borderless):
  kill  -> "KILL CONFIRMED" lines that stack under the crosshair (one line per kill)
  death -> "RESPAWNING IN ..." countdown (only shows if you were NOT revived)
  downed / revived -> "GIVE UP / CALL FOR HELP" prompt appears / clears without a respawn (info only, not counted).
                      A menu/inventory/map hiding the prompt, or the game losing focus, is NOT a revive.
  win   -> end-of-match scoreboard headed "VICTORY" (DEFEAT and "<TEAM> WINS" splashes are ignored)
"""
import asyncio, re, time
from PIL import Image, ImageOps
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat
from winrt.windows.storage.streams import DataWriter

# Regions as fractions of the game frame: (left, top, right, bottom)
REGIONS = {
    "confirm": (0.40, 0.66, 0.60, 0.88),  # tall enough for 5+ stacked lines
    "downed": (0.38, 0.86, 0.64, 0.94),
    "center": (0.15, 0.10, 0.85, 0.70),
    "win": (0.28, 0.06, 0.72, 0.36),  # big VICTORY / DEFEAT header
    "respawn": (0.30, 0.36, 0.70, 0.58),  # "RESPAWNING IN 5..." on black
    "damagelog": (0.86, 0.60, 1.00, 0.78),  # "VIEW DAMAGE LOG": on screen for the whole down, even mid-revive
}

# OCR is noisy on thin HUD text, so match loosely.
RE_CONFIRM = re.compile(r"(C[O0C]N?F[I1lL|]R|NF[I1lL|]R[MHN]|[KR][I1lL|]{1,3}[\s.>]*[CE][O0CD]N)", re.I)
RE_WIN = re.compile(r"V[I1l|]CT[O0]RY", re.I)
RE_RESPAWN = re.compile(r"RESP[A4]WN|SP[A4]WNING\s*IN|DEPL[O0]YMENT\s*B[O0]ARD", re.I)
RE_DAMAGELOG = re.compile(r"DAMAGE\s*L[O0]G|V[I1l]EW\s*DAMAGE", re.I)
RE_DOWNED = re.compile(r"G[I1l]VE\s*U|F[O0]R\s*HEL|CALL\s*F[O0]R", re.I)
# Full-screen UI (inventory, map, pause/settings, scoreboard) that can hide the downed prompt.
RE_MENU = re.compile(r"INVENT[O0]RY|BACKPACK|L[O0]AD[O0]UT|EQUIP|SETTINGS|[O0]PTI[O0]NS|RESUME|SC[O0]REB[O0]ARD"
                     r"|LEAVE\s*MATCH|QUIT|KEYBIND|CONTR[O0]LS|AUDI[O0]|GRAPHICS|DR[O0]P\s*ITEM", re.I)

_engine = OcrEngine.try_create_from_user_profile_languages()
_loop = asyncio.new_event_loop()


def ocr_lines(img):
    """Return OCR'd text lines for a PIL image."""
    img = img.convert("RGBA")
    w, h = img.size
    dw = DataWriter()
    dw.write_bytes(img.tobytes())
    sb = SoftwareBitmap.create_copy_from_buffer(dw.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
    res = _loop.run_until_complete(_engine.recognize_async(sb))
    return [ln.text for ln in res.lines]


def region(frame, name):
    W, H = frame.size
    l, t, r, b = REGIONS[name]
    img = frame.crop((int(l * W), int(t * H), int(r * W), int(b * H)))
    scale = 2.4 * 802 / H if H < 1200 else 1.35 * 1440 / H  # clips are 802 px tall, live is 1440
    return img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)


def v_gray(c):
    return ImageOps.autocontrast(c.convert("L")).convert("RGBA")


def v_inv(c):
    return ImageOps.invert(ImageOps.autocontrast(c.convert("L"), cutoff=2)).convert("RGBA")


def v_thr(c):
    return c.convert("L").point(lambda v: 0 if v > 170 else 255).convert("RGBA")


def count_confirm(frame):
    c = region(frame, "confirm")
    best, seen = 0, []
    for fn in (v_gray, v_inv, v_thr):
        lines = ocr_lines(fn(c))
        n = sum(1 for s in lines if RE_CONFIRM.search(s))
        seen.append(lines)
        best = max(best, n)
    return best, seen


def downed_cues(frame):
    """True if the downed UI is up: the GIVE UP prompt OR the VIEW DAMAGE LOG panel."""
    down, text = is_downed(frame)
    if down:
        return True, text
    c = region(frame, "damagelog")
    for fn in (v_gray, v_inv):
        t = " ".join(ocr_lines(fn(c)))
        if RE_DAMAGELOG.search(t):
            return True, t
    return False, ""


def is_occluded(frame):
    """True when a full-screen menu/inventory/map covers the HUD. The 3D world yields almost no OCR words;
    menus yield many lines or a known menu keyword. Only called while downed, so its cost is bounded."""
    lines = ocr_lines(v_gray(region(frame, "center")))
    wordy = [ln for ln in lines if re.search(r"[A-Za-z]{3,}", ln)]
    return len(wordy) >= 4 or bool(RE_MENU.search(" ".join(lines)))


def is_downed(frame):
    c = region(frame, "downed")
    for fn in (v_gray, v_inv):
        text = " ".join(ocr_lines(fn(c)))
        if RE_DOWNED.search(text):
            return True, text
    return False, ""


class Detector:
    """Feed frames in order (with timestamps); get 'kill' / 'death' events back."""

    REVIVE_CLEAR_S = 6.0     # downed UI must be gone this long (in visible gameplay) before "revived"
    FEED_GAP_S = 1.5         # a longer gap between frames = game lost focus (alt-tab); restart the grace window
    MAX_DOWN_S = 120.0       # a "revive" this long after going down is stale; clear silently instead

    def __init__(self, death_lock_s=20.0, burst_gap_s=2.0):
        self.death_lock_s = death_lock_s
        self.burst_gap_s = burst_gap_s
        self.burst_max = 0        # most KILL CONFIRMED lines seen in the current burst
        self.pending = 0
        self.last_confirm_ts = -1e9
        self.downed_hits = 0
        self.downed_active = False
        self.last_downed_ts = -1e9
        self.last_death_ts = -1e9
        self.last_win_check = -1e9
        self.last_win_ts = -1e9
        self.win_hits = 0
        self.respawn_hits = 0
        self.last_respawn_check = -1e9
        self.down_started_ts = -1e9
        self.last_feed_ts = None
        self.last_occl_check = -1e9
        self.occluded = False

    def feed(self, frame, ts=None):
        ts = time.time() if ts is None else ts
        events = []
        # Frames stopped (game unfocused / paused): we know nothing about that stretch, so a downed player
        # must be seen in gameplay for the full grace window again before we call it a revive.
        if self.downed_active and self.last_feed_ts is not None and ts - self.last_feed_ts > self.FEED_GAP_S:
            self.last_downed_ts = ts
        self.last_feed_ts = ts
        n, seen = count_confirm(frame)
        if n:
            self.last_confirm_ts = ts
        elif ts - self.last_confirm_ts > self.burst_gap_s:
            self.burst_max = 0
            self.pending = 0
        if n > self.burst_max:
            if self.pending and n >= self.pending:  # confirmed on two consecutive samples
                new = min(n, self.pending) - self.burst_max
                events += ["kill"] * max(1, new)
                self.burst_max = min(n, self.pending)
                self.pending = 0
            else:
                self.pending = n
        else:
            self.pending = 0
        # ---- wins: end-of-match scoreboard ----
        if ts - self.last_win_check >= 1.0:
            self.last_win_check = ts
            wtext = " ".join(ocr_lines(v_gray(region(frame, "win"))))
            if RE_WIN.search(wtext):
                self.win_hits += 1
                if self.win_hits >= 2 and ts - self.last_win_ts > 120:
                    events.append("win")
                    self.last_win_ts = ts
            else:
                self.win_hits = 0
        # ---- downed (info) ----
        down, dtext = downed_cues(frame)
        if down:
            self.downed_hits += 1
            self.last_downed_ts = ts
            if self.downed_hits >= 2 and not self.downed_active:
                self.downed_active = True
                self.down_started_ts = ts
                events.append("downed")
        else:
            self.downed_hits = 0
            # Prompt gone while downed: is a menu/inventory/map covering it? Then hold, don't count it.
            if self.downed_active:
                if ts - self.last_occl_check >= 0.5:
                    self.last_occl_check = ts
                    self.occluded = is_occluded(frame)
                if self.occluded:
                    self.last_downed_ts = ts
        # ---- death: respawn countdown (checked every 0.5 s) ----
        if ts - self.last_respawn_check >= 0.5:
            self.last_respawn_check = ts
            rtext = " ".join(ocr_lines(v_gray(region(frame, "respawn"))))
            if RE_RESPAWN.search(rtext):
                self.respawn_hits += 1
                if self.respawn_hits >= 2 and ts - self.last_death_ts > self.death_lock_s:
                    events.append("death")
                    self.last_death_ts = ts
                    self.downed_active = False
            else:
                self.respawn_hits = 0
        # ---- revived: ALL downed UI gone for 6 s of visible gameplay (tolerates white-flash / blur frames,
        #      menus, alt-tab) and no respawn countdown ----
        if self.downed_active and not down and ts - self.last_downed_ts > self.REVIVE_CLEAR_S \
                and self.respawn_hits == 0 and ts - self.last_death_ts > self.REVIVE_CLEAR_S:
            self.downed_active = False
            self.occluded = False
            if ts - self.down_started_ts <= self.MAX_DOWN_S:
                events.append("revived")
        return events, {"confirm": n, "confirm_lines": seen, "downed": dtext,
                        "occluded": self.occluded if self.downed_active else False}
