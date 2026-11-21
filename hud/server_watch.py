"""Wardogs server watch (Oct 23): saves the server ID automatically, no clicks.

The Server Browser (DEPLOY) shows the selected server's ID in its details panel ("SERVER ID: 700-118"), and
JOIN BY ID (bottom-left of the browser) takes that 6-digit number. While the HUD sees the browser it keeps
reading the ID of the server you have selected; when the browser goes away (you joined) that ID is saved to
Stream Keeper's wardogs_servers.json - tray > "Copy last Wardogs server" puts it on the clipboard.
The ESC menu's SERVER ID also counts whenever it is revealed (eye icon). Masked (******) is ignored.
Cost: two small OCR reads every 2 s while the game is in front (the full right half only while in the browser).
"""
import os, re, sys, time
from PIL import Image
import wardogs_detect as wd

def _stream_keeper_dirs():
    env = os.environ.get("STREAM_KEEPER_DIR") or os.environ.get("PROTOCOL_HUD_STREAM_KEEPER")
    if env:
        yield env
    here = os.path.dirname(os.path.abspath(__file__))
    yield os.path.normpath(os.path.join(here, "..", "..", "StreamKeeper"))
    yield os.path.normpath(os.path.join(here, "..", "stream-keeper"))


for _sk in _stream_keeper_dirs():
    if _sk and os.path.isdir(_sk) and _sk not in sys.path:
        sys.path.insert(0, _sk)
try:
    import server_memory as sm  # noqa: E402
except ImportError:
    sm = None  # Stream Keeper is optional; server IDs stay in memory only

wd.REGIONS.setdefault("sw_browser", (0.00, 0.82, 0.45, 1.00))   # JOIN BY ID / JOIN MATCH / FILTER row
wd.REGIONS.setdefault("sw_panel", (0.45, 0.08, 1.00, 1.00))     # details panel (browser only)
wd.REGIONS.setdefault("sw_esc", (0.76, 0.62, 1.00, 0.80))       # ESC menu: CURRENT SERVER / SERVER ID

wd.REGIONS.setdefault("sw_title", (0.655, 0.33, 0.97, 0.42))   # S3 details panel: server name + tags
# "JOIN BY ID" often OCRs as "JOIN BY 10" (Nov 13 real frame), so the ID part is not required
RE_BROWSER = re.compile(r"J[O0]IN\s*BY\b|J[O0]IN\s*MATCH|SERVER\s*BR[O0]WSER|SH[O0]W\s*C[O0]MMUN", re.I)
RE_SID = re.compile(r"SERVER\s*[I1l|]D\s*[:.;]?\s*(\d{3})\s*[-–—.]?\s*(\d{3})(?!\d)", re.I)
# Season 3 (Nov 12): the browser's details panel no longer shows a SERVER ID, only the name:
# "OFFICIAL WARDOGS | NORTH AMERICA (CENTRAL) | #21" (+ tags like LIMITED GEAR). Save that name instead.
RE_NAME = re.compile(
    r"(NORTH\s*AMERICA|SOUTH\s*AMERICA|EUROPE|ASIA|OCEANIA|AUSTRALIA|MIDDLE\s*EAST|AFRICA)\s*"
    r"[(\[{]\s*([A-Z]+)\s*[)\]}]\s*[|I1l!]?\s*[#H]\s*(\d{1,4})(?!\d)", re.I)
DEBUG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "server_watch_last_browser.jpg")


def find_name(text: str):
    """'North America (Central) #21 - Limited Gear' from the S3 details panel text, else None."""
    m = RE_NAME.search(text or "")
    if not m:
        return None
    region = " ".join(w.capitalize() for w in
                      re.sub(r"(?i)(NORTH|SOUTH|MIDDLE)\s*", r"\1 ", m.group(1)).split())
    name = f"{region} ({m.group(2).capitalize()}) #{int(m.group(3))}"
    if re.search(r"L[I1l]M[I1l]TED\s*GEAR", text, re.I):
        name += " - Limited Gear"
    return name


RE_REGION_ONLY = re.compile(
    r"(NORTH\s*AMERICA|SOUTH\s*AMERICA|EUROPE|ASIA|OCEANIA|AUSTRALIA|MIDDLE\s*EAST|AFRICA)\s*[(\[{]\s*([A-Z]+)\s*[)\]}]", re.I)
RE_NUM_AFTER_BAR = re.compile(r"[)\]}]?\s*[|I1l!]\s*[#H]?\s*(\d{1,4})(?!\d)|#\s*(\d{1,4})(?!\d)")


def _pad(img):
    from PIL import ImageOps
    return ImageOps.expand(img.convert("RGB"), border=40, fill=(0, 0, 0))


def title_name(frame):
    """S3 details-panel title -> 'North America (Central) #21[ - Limited Gear]' or None.
    Windows OCR often drops the short '#21' at the end of the title line (Nov 13 frame), so when the region part
    reads but the number doesn't, the area right of '(CENTRAL)' is read again on its own at a few scales."""
    t = wd.region(frame, "sw_title")
    text_seen = ""
    for k in (2, 3):
        tt = t.resize((t.width * k, t.height * k))
        for fn in (wd.v_gray, wd.v_inv, wd.v_thr):
            txt = " ".join(wd.ocr_lines(fn(tt)))
            text_seen += " " + txt
            name = find_name(txt)
            if name:
                return name
    m = RE_REGION_ONLY.search(text_seen)
    if not m:
        return None
    t2 = t.resize((t.width * 2, t.height * 2))
    words = wd.ocr_words(wd.v_gray(t2))
    sub = next(((x, y) for w, x, y in words if re.fullmatch(r"[(\[{]?" + re.escape(m.group(2)) + r"[)\]}]?\|?", w, re.I)), None)
    if not sub:
        return None
    x, y = sub
    W, H = t.size
    box = (int(W * max(0.0, x - 0.06)), int(H * max(0.0, y - 0.18)), int(W * min(1.0, x + 0.16)), int(H * min(1.0, y + 0.18)))
    part = t.crop(box)
    for k in (3, 2, 4):
        p = part.resize((part.width * k, part.height * k))
        for fn in (wd.v_gray, wd.v_inv):
            n = RE_NUM_AFTER_BAR.search(" ".join(wd.ocr_lines(_pad(fn(p)))))
            if n:
                return find_name(f"{m.group(1)} ({m.group(2)}) #{n.group(1) or n.group(2)} {text_seen}")
    return None


def find_id(text: str):
    """6-digit SERVER ID (Season 2 layout / ESC menu), else the S3 server name, else None."""
    m = RE_SID.search(text or "")
    return (m.group(1) + m.group(2)) if m else find_name(text)


class ServerWatch:
    CHECK_S = 2.0      # how often to look for the browser / ESC menu
    PANEL_S = 1.0      # how often to read the details panel while the browser is up
    LEAVE_S = 6.0      # browser gone this long = you joined the selected server

    def __init__(self, save=None, log=print):
        self.save = save or self._save_keeper
        self.log = log
        self.last_check = self.last_panel = self.browser_seen = -1e9
        self.candidate = None
        self.last_saved = None
        self.debug_saved = False

    @staticmethod
    def _save_keeper(sid, how):
        if sm is None:
            return
        sm.save(sm.remember(sm.load(), sid, time.time()))

    def _commit(self, sid, how):
        if sid != self.last_saved:
            self.save(sid, how)
            self.last_saved = sid
            self.log(time.strftime("%Y-%m-%d %H:%M:%S"), "auto: server", sid, "(%s)" % how, flush=True)
        return sid

    def feed(self, frame: Image.Image, ts: float):
        """Returns the server ID when one is saved on this frame, else None."""
        saved = None
        if ts - self.last_check >= self.CHECK_S:
            self.last_check = ts
            btxt = " ".join(wd.ocr_lines(wd.v_gray(wd.region(frame, "sw_browser"))))
            if RE_BROWSER.search(btxt):
                self.browser_seen = ts
            else:
                sid = find_id(" ".join(wd.ocr_lines(wd.v_gray(wd.region(frame, "sw_esc")))))
                if sid:
                    saved = self._commit(sid, "ESC menu")
        if ts - self.browser_seen < self.CHECK_S * 1.5 and ts - self.last_panel >= self.PANEL_S:
            self.last_panel = ts
            sid = find_id(" ".join(wd.ocr_lines(wd.v_gray(wd.region(frame, "sw_panel")))))
            if sid:
                self.candidate = sid
            if not self.debug_saved:          # one frame per HUD run, so the layout can be checked later
                self.debug_saved = True
                try:
                    img = frame if isinstance(frame, Image.Image) else frame.crop((0, 0) + tuple(frame.size))
                    img.convert("RGB").resize((img.width // 2, img.height // 2)).save(DEBUG, quality=80)
                except Exception:  # noqa: BLE001
                    pass
        if self.candidate and ts - self.browser_seen > self.LEAVE_S:
            saved = self._commit(self.candidate, "server browser")
            self.candidate = None
        return saved
