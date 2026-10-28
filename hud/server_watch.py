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

sys.path.insert(0, r"C:\Users\adame\Documents\StreamKeeper")
import server_memory as sm  # noqa: E402

wd.REGIONS.setdefault("sw_browser", (0.00, 0.82, 0.45, 1.00))   # JOIN BY ID / JOIN MATCH / FILTER row
wd.REGIONS.setdefault("sw_panel", (0.45, 0.08, 1.00, 1.00))     # details panel (browser only)
wd.REGIONS.setdefault("sw_esc", (0.76, 0.62, 1.00, 0.80))       # ESC menu: CURRENT SERVER / SERVER ID

RE_BROWSER = re.compile(r"J[O0]IN\s*BY\s*[I1l|]D|J[O0]IN\s*MATCH|SERVER\s*BR[O0]WSER|SH[O0]W\s*C[O0]MMUN", re.I)
RE_SID = re.compile(r"SERVER\s*[I1l|]D\s*[:.;]?\s*(\d{3})\s*[-–—.]?\s*(\d{3})(?!\d)", re.I)
DEBUG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "server_watch_last_browser.jpg")


def find_id(text: str):
    m = RE_SID.search(text or "")
    return (m.group(1) + m.group(2)) if m else None


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
