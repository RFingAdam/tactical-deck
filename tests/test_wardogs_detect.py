"""Down/revive state machine of the Wardogs profile, driven by scripted frames (no OCR, any OS).

A frame is a dict: {"down": bool, "center": [ocr lines], "respawn": [...], "win": [...]}.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hud"))


def _stub_winrt():
    """wardogs_detect imports the Windows OCR bindings (and Pillow) at import time; stub whatever is
    missing off-Windows / in CI. The tests below replace every function that would touch them."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        pil = types.ModuleType("PIL")
        pil.Image, pil.ImageOps = types.ModuleType("PIL.Image"), types.ModuleType("PIL.ImageOps")
        sys.modules.update({"PIL": pil, "PIL.Image": pil.Image, "PIL.ImageOps": pil.ImageOps})
    for name in ("winrt", "winrt.windows", "winrt.windows.media", "winrt.windows.media.ocr",
                 "winrt.windows.graphics", "winrt.windows.graphics.imaging",
                 "winrt.windows.storage", "winrt.windows.storage.streams"):
        sys.modules.setdefault(name, types.ModuleType(name))
    ocr = sys.modules["winrt.windows.media.ocr"]
    ocr.OcrEngine = types.SimpleNamespace(try_create_from_user_profile_languages=lambda: None)
    img = sys.modules["winrt.windows.graphics.imaging"]
    img.SoftwareBitmap, img.BitmapPixelFormat = object, types.SimpleNamespace(BGRA8=0)
    sys.modules["winrt.windows.storage.streams"].DataWriter = object


try:
    import wardogs_detect as wd
except Exception:
    _stub_winrt()
    import wardogs_detect as wd


@pytest.fixture(autouse=True)
def scripted(monkeypatch):
    monkeypatch.setattr(wd, "count_confirm", lambda f: (0, []))
    monkeypatch.setattr(wd, "downed_cues", lambda f: (f.get("down", False), "GIVE UP" if f.get("down") else ""))
    monkeypatch.setattr(wd, "region", lambda f, name: (f, name))
    monkeypatch.setattr(wd, "v_gray", lambda c: c)
    monkeypatch.setattr(wd, "ocr_lines", lambda c: c[0].get(c[1], []))


GAME = {}
DOWN = {"down": True}
INVENTORY = {"center": ["INVENTORY", "Primary weapon", "Bandage x2", "Frag grenade", "Drop item"]}
MAP = {"center": ["Valkyra", "Airfield", "North Ridge", "Quarry", "Harbor"]}
RESPAWN = {"respawn": ["RESPAWNING IN 5"]}


def run(script, fps=4.0):
    """script: list of (frame, seconds) or ("gap", seconds). Returns [(t, event)]."""
    det, t, out = wd.Detector(), 1000.0, []
    for frame, secs in script:
        if frame == "gap":
            t += secs
            continue
        for _ in range(int(secs * fps)):
            evs, _ = det.feed(frame, t)
            out += [(round(t - 1000, 2), e) for e in evs]
            t += 1 / fps
    return out


def kinds(evts):
    return [e for _, e in evts]


def test_real_revive_fires_once_after_grace():
    ev = run([(GAME, 2), (DOWN, 4), (GAME, 8)])
    assert kinds(ev) == ["downed", "revived"]
    t_revive = ev[-1][0]
    assert 6 + 6 <= t_revive <= 6 + 6.6       # ~6 s after the prompt disappeared


@pytest.mark.parametrize("menu", [INVENTORY, MAP], ids=["inventory", "map"])
def test_menu_while_downed_is_not_a_revive(menu):
    ev = run([(GAME, 2), (DOWN, 4), (menu, 15), (DOWN, 4), (RESPAWN, 2)])
    assert kinds(ev) == ["downed", "death"]   # no revived, and no second "downed" when the prompt returns


def test_menu_then_real_revive_still_fires():
    ev = run([(GAME, 2), (DOWN, 4), (INVENTORY, 10), (GAME, 8)])
    assert kinds(ev) == ["downed", "revived"]
    assert ev[-1][0] >= 2 + 4 + 10 + 6        # grace window starts when the menu closes


def test_alt_tab_while_downed_is_not_a_revive():
    # Frames stop while the game is unfocused; we come back to a pause-menu-free view still downed.
    ev = run([(GAME, 2), (DOWN, 4), ("gap", 30), (GAME, 2), (DOWN, 4), (RESPAWN, 2)])
    assert kinds(ev) == ["downed", "death"]


def test_alt_tab_then_revived_waits_full_grace():
    ev = run([(GAME, 2), (DOWN, 4), ("gap", 30), (GAME, 8)])
    assert kinds(ev) == ["downed", "revived"]
    assert ev[-1][0] >= 2 + 4 + 30 + 6


def test_stale_down_clears_silently():
    ev = run([(GAME, 1), (DOWN, 3), (INVENTORY, 150), (GAME, 8)])
    assert kinds(ev) == ["downed"]


def test_gameplay_with_a_couple_of_words_is_not_a_menu():
    # A teammate name tag or a sign in the world must not freeze the revive forever.
    world = {"center": ["SwampBewdy", "Gas"]}
    ev = run([(GAME, 2), (DOWN, 4), (world, 8)])
    assert kinds(ev) == ["downed", "revived"]
