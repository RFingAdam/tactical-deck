"""Reads Wardogs HUD text with the built-in Windows OCR engine and turns it into kill / death events.

Signals (calibrated on Wardogs highlight clips, 3440x1440 borderless):
  kill  -> "KILL CONFIRMED" lines that stack under the crosshair (one line per kill)
  death -> "RESPAWNING IN ..." countdown (only shows if you were NOT revived)
  downed / revived -> "GIVE UP / CALL FOR HELP" prompt appears / clears without a respawn (info only, not counted).
                      A menu/inventory/map hiding the prompt, or the game losing focus, is NOT a revive.
  win   -> end-of-match scoreboard headed "VICTORY" (DEFEAT and "<TEAM> WINS" splashes are ignored)
  cash  -> account balance from the top-right money readout ("$47,396  $200,699  140" = the game's own session
           delta, balance, level). The balance is the RIGHT-MOST $ amount of that header row (Oct 18; it used to be
           the largest, which picked up the session delta whenever OCR missed the balance). It updates live in
           matches and menus. The server shows balance now minus balance at the start as "stream profit".
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
    "cash": (0.82, 0.00, 1.00, 0.075),  # money readout in the top-right header
}

# OCR is noisy on thin HUD text, so match loosely.
RE_CONFIRM = re.compile(r"(C[O0C]N?F[I1lL|]R|NF[I1lL|]R[MHN]|[KR][I1lL|]{1,3}[\s.>]*[CE][O0CD]N)", re.I)
RE_WIN = re.compile(r"V[I1l|]CT[O0]RY", re.I)
# "RESPAWNING IN 4..." on black = a real death, on its own.
RE_RESPAWN = re.compile(r"RESP[A4]WN[I1l|]NG|SP[A4]WN[I1l|]NG\s*[I1l|]N", re.I)
# The Deployment Board (map + SPAWN button) also opens at the APC / spawn point while alive, so these only
# count as a death if we saw you go down first (bled out straight to the board).
RE_RESPAWN_IF_DOWNED = re.compile(r"RESP[A4]WN|DEPL[O0]YMENT\s*B[O0]ARD", re.I)
RE_DAMAGELOG = re.compile(r"DAMAGE\s*L[O0]G|V[I1l]EW\s*DAMAGE", re.I)
RE_CASH = re.compile(r"(-?)\s?\$\s?(\d{1,3}(?:,\d{3})+)")
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


# ---- money readout (rewritten Oct 18) ----
# Header layouts:   [session delta][arrow] [BALANCE]                      in a match
#                   [session delta][arrow] [BALANCE] [skull][level]       lobby
#                   [BALANCE] [icon] [gold] n [skull][level]              store / other menus
# The old reader took the LARGEST unsigned amount, so when OCR missed the balance (the "$1" of "$159,647" often
# reads as "5") it reported the session delta instead -> fake dips, and the real balance coming back counted as
# ~100k of "income". Now the balance is the RIGHT-MOST $ amount of the header row; a lone amount is only taken when
# its position says it is the balance.
RE_CASH_AMT = re.compile(r"([+-]?)([$S])(\d{1,3}(?:,\d{3})*)(,?)(?!\d)")
RE_CASH_TAIL = re.compile(r"^\d{3}(?:,\d{3})*$")
RE_PLAIN_NUM = re.compile(r"^\d{1,3}$")
CASH_HEADER_MAX_Y = 0.78   # popups ("CONTROL ZONE PRESENCE +$160") sit lower in the region
CASH_BALANCE_X = 0.76      # match layout: balance centre ~0.86 of the region width, delta ~0.60-0.65
CASH_MENU_X = 0.52         # lobby layout: delta ~0.41, balance ~0.61, level ~0.84


def ocr_words(img):
    """[(text, x_centre, y_centre)] as fractions of the image, in OCR reading order."""
    img = img.convert("RGBA")
    w, h = img.size
    dw = DataWriter()
    dw.write_bytes(img.tobytes())
    sb = SoftwareBitmap.create_copy_from_buffer(dw.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
    res = _loop.run_until_complete(_engine.recognize_async(sb))
    out = []
    for ln in res.lines:
        for word in ln.words:
            r = word.bounding_rect
            out.append((word.text, (r.x + r.width / 2) / w, (r.y + r.height / 2) / h))
    return out


def parse_cash(words):
    """-> (amounts, plains). amounts: [(x, sign, value, grouped)] left->right; plains: x of $-less 1-3 digit numbers
    (level, counts). '$' / '-$' tokens are joined to the next token, '$18,' + '194' is joined, junk around a token
    ("$47,047+", "?$78,614", "$5,849k") is ignored."""
    row = [(t.replace(" ", ""), x) for t, x, y in words if y <= CASH_HEADER_MAX_Y]
    amounts, plains, i = [], [], 0
    while i < len(row):
        t, x = row[i]
        if t in ("$", "-$", "-", "S", "-S") and i + 1 < len(row):
            t, x = t + row[i + 1][0], row[i + 1][1]
            i += 1
        m = RE_CASH_AMT.search(t)
        if m and m.group(4) and i + 1 < len(row) and RE_CASH_TAIL.match(row[i + 1][0]):
            t = t[:m.end()] + row[i + 1][0]
            i += 1
            m = RE_CASH_AMT.search(t)
        if m:
            sign, cur, digits, _ = m.groups()
            grouped = "," in digits
            if cur == "$" or grouped:  # 'S' only stands in for '$' before a grouped number
                amounts.append((x, sign, int(digits.replace(",", "")), grouped))
        elif RE_PLAIN_NUM.match(t):
            plains.append(x)
        i += 1
    return sorted(amounts), plains


def classify_cash(amounts, plains):
    """Balance from one OCR pass -> (value or None, how)."""
    if not amounts:
        return None, "none"
    x, sign, v, grouped = amounts[-1]
    if sign or not grouped:
        return None, "rightmost is the delta/partial"
    if len(amounts) >= 2:
        return v, "pair"
    if x >= CASH_BALANCE_X:
        return v, "right"
    right_plains = sum(1 for p in plains if p > x + 0.1)
    if (x >= CASH_MENU_X and right_plains >= 1) or (x >= 0.25 and right_plains >= 2):
        return v, "menu"
    return None, "lone amount in the delta position"


def read_cash(frame):
    """Account balance from the top-right header -> (value, how) or (None, why). Two of three OCR passes must agree."""
    c = region(frame, "cash")
    vals, hows = [], []
    for fn in (v_gray, v_inv, v_thr):
        v, how = classify_cash(*parse_cash(ocr_words(fn(c))))
        if v is not None:
            vals.append(v)
            hows.append(how)
    for v, how in zip(vals, hows):
        if vals.count(v) >= 2:
            return v, how
    return None, "no agreement"


def looks_chopped(cur, v, tol=3_000):
    """v is cur with its leading digit(s) lost ("$56,142" read as "$6,142"): not a purchase."""
    k = len(str(v))
    return v < cur and cur >= 10 ** k and abs(v - cur % 10 ** k) <= tol


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
        self.death_text = ""      # OCR text that triggered the last death (logged)
        self.down_started_ts = -1e9
        self.last_feed_ts = None
        self.last_occl_check = -1e9
        self.occluded = False
        self.last_cash_check = -1e9
        self.cash = None          # last accepted balance
        self.cash_cand = None     # candidate reading and how many checks in a row agreed
        self.cash_cand_n = 0

    def cash_needed(self, v, how):
        """Checks in a row a new balance reading needs before it is accepted."""
        if self.cash is None:
            return 3 if how == "menu" else 2
        d = abs(v - self.cash)
        if looks_chopped(self.cash, v):
            return 30
        if d > 50_000 or (how == "menu" and d > 20_000):
            return 4
        return 2

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
            if RE_RESPAWN.search(rtext) or (self.downed_active and RE_RESPAWN_IF_DOWNED.search(rtext)):
                self.respawn_hits += 1
                if self.respawn_hits >= 2 and ts - self.last_death_ts > self.death_lock_s:
                    events.append("death")
                    self.last_death_ts = ts
                    self.death_text = rtext[:60]
                    self.downed_active = False
            else:
                self.respawn_hits = 0
        # ---- cash: balance readout, checked every 2 s. A new value must read the same several checks in a row
        #      before it counts: 2 normally, 4 for a move over $50k (or a lobby reading moving over $20k),
        #      30 (~1 min) when it looks like the old balance with its leading digit lost ----
        if ts - self.last_cash_check >= 2.0:
            self.last_cash_check = ts
            v, how = read_cash(frame)
            if v is not None:
                if v == self.cash_cand:
                    self.cash_cand_n += 1
                else:
                    self.cash_cand, self.cash_cand_n = v, 1
                if v != self.cash and self.cash_cand_n >= self.cash_needed(v, how):
                    self.cash = v
                    # Include how so the server can treat menu unlocks as invested, not losses.
                    events.append(("cash", v, how))
        # ---- revived: ALL downed UI gone for 6 s of visible gameplay (tolerates white-flash / blur frames,
        #      menus, alt-tab) and no respawn countdown ----
        if self.downed_active and not down and ts - self.last_downed_ts > self.REVIVE_CLEAR_S \
                and self.respawn_hits == 0 and ts - self.last_death_ts > self.REVIVE_CLEAR_S:
            self.downed_active = False
            self.occluded = False
            if ts - self.down_started_ts <= self.MAX_DOWN_S:
                events.append("revived")
        return events, {"confirm": n, "confirm_lines": seen, "downed": dtext, "death_text": self.death_text,
                        "occluded": self.occluded if self.downed_active else False}
