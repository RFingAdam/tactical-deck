"""Display-layout presets for the Protocol HUD overlay and OCR crops.

The overlay canvas is 1920x1080 (Streamlabs Browser Source). HUD chrome and kill-feed
OCR regions are anchored to a game rect on that canvas, not to absolute canvas pixels.

Presets:
  16:9                  full-frame game (default for new users)
  ultrawide-letterbox   3440x1440 (or similar) game fitted into 16:9 with letterbox
                        bands. Pixel-identical to the original SwampBewdy layout.
  custom                caller-supplied game rect {x, y, w, h}
"""
from __future__ import annotations

CANVAS_W = 1920
CANVAS_H = 1080

# 3440x1440 fitted to 1920 width: round(1440 * 1920 / 3440) = 804, but the live
# overlay and analyze_segment.py were calibrated on a 803px-tall game band with
# a 138px top bar (bottom bar 139px). Do not change these: ultrawide-letterbox
# must stay pixel-identical to today's overlay.
ULTRAWIDE_X = 0
ULTRAWIDE_Y = 138
ULTRAWIDE_W = 1920
ULTRAWIDE_H = 803

PRESETS = ("16:9", "ultrawide-letterbox", "custom")
_ALIASES = {
    "16:9": "16:9",
    "16x9": "16:9",
    "ultrawide-letterbox": "ultrawide-letterbox",
    "ultrawide": "ultrawide-letterbox",
    "custom": "custom",
}

# pos=bar tile geometry (must match overlay.html)
BAR_TILE_W, BAR_TILE_H = 136, 100
BAR_INNER_GAP = 6
BAR_HUD_GAP = 10
BAR_SUB_W = 112
BAR_CASH_W, BAR_CASH_H = 196, 100
BAR_LEFT = 40
CORNER = 36

# How far the streak / K.I.A. banners sit into the game on the letterbox layout.
# 205 - 138 = 67; 330 - 138 = 192. Kept as constants so the ultrawide preset
# reproduces the original CSS pixel-for-pixel.
CALLOUT_INSET = 67
KIA_INSET = 192
CALLOUT_INGAME = 130
KIA_INGAME = 290

# Wardogs on-screen HUD, as fractions of the *game* rect. Used to prove the 16:9
# overlay chrome does not cover the kill feed, minimap or ammo/health.
# kill_feed: OCR "confirm" + "downed" union. minimap / ammo: from the letterboxed
# capture (docs/media/hud-lethal.jpg) mapped to full-frame 16:9.
KEEP_CLEAR = {
    "kill_feed": (0.38, 0.66, 0.64, 0.94),
    "minimap": (0.008, 0.70, 0.195, 0.985),
    "ammo_health": (0.78, 0.86, 0.995, 0.995),
}

# In-game cash readout (Wardogs top-right header). 16:9 STREAM PROFIT sits below it.
CASH_HEADER_FR = 0.075


class LayoutError(ValueError):
    """Invalid layout preset or custom game rect."""


def normalize_layout(name, default="16:9"):
    if name is None or name == "":
        return default
    key = str(name).strip().lower().replace("_", "-")
    if key not in _ALIASES:
        raise LayoutError(
            f"unknown display layout {name!r}; expected 16:9, ultrawide-letterbox or custom"
        )
    return _ALIASES[key]


def clamp_rect(x, y, w, h, canvas_w=CANVAS_W, canvas_h=CANVAS_H):
    w = max(64, min(int(w), int(canvas_w)))
    h = max(64, min(int(h), int(canvas_h)))
    x = max(0, min(int(x), int(canvas_w) - w))
    y = max(0, min(int(y), int(canvas_h) - h))
    return {"x": x, "y": y, "w": w, "h": h}


def game_rect(preset, custom=None, canvas_w=CANVAS_W, canvas_h=CANVAS_H):
    """Return {x, y, w, h} of the game area on the overlay canvas."""
    preset = normalize_layout(preset)
    if preset == "16:9":
        return {"x": 0, "y": 0, "w": int(canvas_w), "h": int(canvas_h)}
    if preset == "ultrawide-letterbox":
        if canvas_w == CANVAS_W and canvas_h == CANVAS_H:
            return {"x": ULTRAWIDE_X, "y": ULTRAWIDE_Y, "w": ULTRAWIDE_W, "h": ULTRAWIDE_H}
        sx = canvas_w / CANVAS_W
        sy = canvas_h / CANVAS_H
        return {
            "x": int(round(ULTRAWIDE_X * sx)),
            "y": int(round(ULTRAWIDE_Y * sy)),
            "w": int(round(ULTRAWIDE_W * sx)),
            "h": int(round(ULTRAWIDE_H * sy)),
        }
    if not isinstance(custom, dict):
        raise LayoutError("custom layout needs a game rect {x, y, w, h}")
    try:
        return clamp_rect(custom["x"], custom["y"], custom["w"], custom["h"], canvas_w, canvas_h)
    except (KeyError, TypeError, ValueError) as e:
        raise LayoutError("custom layout needs numeric x, y, w, h") from e


def _box(left=None, top=None, right=None, bottom=None):
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def hud_anchors(preset, custom=None, pos="bar", canvas_w=CANVAS_W, canvas_h=CANVAS_H):
    """Pixel positions for overlay chrome, relative to the game rect / letterbox bands.

    When preset is ultrawide-letterbox on a 1920x1080 canvas, the numbers match the
    original overlay.html CSS exactly (left:40/top:19 bar, cashbox right:40/top:19,
    callout top:205, kia top:330).
    """
    g = game_rect(preset, custom, canvas_w, canvas_h)
    pos = pos if pos in ("bar", "tl", "tr", "bl", "br") else "tl"
    top_band = g["y"]
    bottom_band = canvas_h - (g["y"] + g["h"])
    right_band = canvas_w - (g["x"] + g["w"])
    letterbox_bar = pos == "bar" and top_band >= BAR_TILE_H

    if letterbox_bar:
        pad = (top_band - BAR_TILE_H) // 2
        hud = _box(left=g["x"] + BAR_LEFT, top=pad)
        cash = _box(right=right_band + BAR_LEFT, top=pad)
        callout_top = g["y"] + CALLOUT_INSET
        kia_top = g["y"] + KIA_INSET
        mode = "letterbox"
    elif pos == "bar":
        pad_top = g["y"] + 16
        hud = _box(left=g["x"] + 36, top=pad_top)
        # Keep STREAM PROFIT off the in-game cash header (top-right of the game).
        cash_top = g["y"] + int(g["h"] * CASH_HEADER_FR) + 8
        cash = _box(right=right_band + 40, top=cash_top)
        callout_top = g["y"] + CALLOUT_INGAME
        kia_top = g["y"] + KIA_INGAME
        mode = "ingame"
    elif pos == "tl":
        # Corner layouts sit in the matching letterbox band when it exists,
        # otherwise inset from the game rect. Ultrawide bands reproduce CSS 36px.
        hud = _box(
            left=g["x"] + CORNER,
            top=CORNER if top_band >= CORNER else g["y"] + CORNER,
        )
        cash = _box(
            right=right_band + CORNER,
            top=CORNER if top_band >= CORNER else g["y"] + CORNER,
        )
        callout_top = g["y"] + 118 if top_band < CORNER else 118
        kia_top = g["y"] + 290 if top_band < CORNER else 290
        mode = "letterbox" if top_band >= CORNER else "ingame"
    elif pos == "tr":
        hud = _box(
            right=right_band + CORNER,
            top=CORNER if top_band >= CORNER else g["y"] + CORNER,
        )
        cash = _box(
            left=g["x"] + CORNER,
            top=CORNER if top_band >= CORNER else g["y"] + CORNER,
        )
        callout_top = g["y"] + 118 if top_band < CORNER else 118
        kia_top = g["y"] + 290 if top_band < CORNER else 290
        mode = "letterbox" if top_band >= CORNER else "ingame"
    elif pos == "bl":
        hud = _box(
            left=g["x"] + CORNER,
            bottom=CORNER if bottom_band >= CORNER else canvas_h - (g["y"] + g["h"]) + CORNER,
        )
        cash = _box(
            right=right_band + CORNER,
            top=CORNER if top_band >= CORNER else g["y"] + CORNER,
        )
        callout_top = g["y"] + 118 if top_band < CORNER else 118
        kia_top = g["y"] + 290 if top_band < CORNER else 290
        mode = "letterbox" if bottom_band >= CORNER else "ingame"
    else:  # br
        hud = _box(
            right=right_band + CORNER,
            bottom=CORNER if bottom_band >= CORNER else canvas_h - (g["y"] + g["h"]) + CORNER,
        )
        cash = _box(
            right=right_band + CORNER,
            top=CORNER if top_band >= CORNER else g["y"] + CORNER,
        )
        callout_top = g["y"] + 118 if top_band < CORNER else 118
        kia_top = g["y"] + 290 if top_band < CORNER else 290
        mode = "letterbox" if bottom_band >= CORNER else "ingame"

    return {
        "game_rect": g,
        "mode": mode,
        "pos": pos,
        "hud": hud,
        "cashbox": cash,
        "callout": {"top": callout_top},
        "kia": {"top": kia_top},
    }


def all_anchors(preset, custom=None, canvas_w=CANVAS_W, canvas_h=CANVAS_H):
    return {p: hud_anchors(preset, custom, pos=p, canvas_w=canvas_w, canvas_h=canvas_h) for p in ("bar", "tl", "tr", "bl", "br")}


def persistent_boxes(preset, custom=None, pos="bar", canvas_w=CANVAS_W, canvas_h=CANVAS_H):
    """Axis-aligned boxes for the always-on stats chrome (no transient callouts)."""
    a = hud_anchors(preset, custom, pos=pos, canvas_w=canvas_w, canvas_h=canvas_h)
    g = a["game_rect"]
    hud, cash = a["hud"], a["cashbox"]
    if pos == "bar":
        x0 = hud["left"] if hud["left"] is not None else g["x"] + 36
        y0 = hud["top"] if hud["top"] is not None else g["y"] + 16
        boxes = {
            "kills": (x0, y0, BAR_TILE_W, BAR_TILE_H),
            "deaths": (x0 + BAR_TILE_W + BAR_INNER_GAP, y0, BAR_TILE_W, BAR_TILE_H),
            "wins": (x0 + 2 * (BAR_TILE_W + BAR_INNER_GAP), y0, BAR_TILE_W, BAR_TILE_H),
            "sub": (x0 + 3 * BAR_TILE_W + 2 * BAR_INNER_GAP + BAR_HUD_GAP, y0, BAR_SUB_W, BAR_TILE_H),
        }
        cw, ch = BAR_CASH_W, BAR_CASH_H
    else:
        x0 = hud["left"] if hud["left"] is not None else g["x"] + CORNER
        y0 = hud["top"] if hud["top"] is not None else g["y"] + CORNER
        boxes = {
            "kills": (x0, y0, 150, 106),
            "deaths": (x0 + 156, y0, 150, 106),
            "wins": (x0 + 312, y0, 150, 106),
        }
        cw, ch = 210, 106
    if cash.get("right") is not None:
        cx = canvas_w - cash["right"] - cw
    else:
        cx = cash.get("left") or 0
    cy = cash["top"] if cash.get("top") is not None else g["y"]
    boxes["cashbox"] = (cx, cy, cw, ch)
    return boxes


def keep_clear_boxes(preset="16:9", custom=None, canvas_w=CANVAS_W, canvas_h=CANVAS_H):
    """Reserved in-game HUD zones, in canvas pixels, for the given game rect."""
    g = game_rect(preset, custom, canvas_w, canvas_h)
    out = {}
    for name, (left, top, right, bottom) in KEEP_CLEAR.items():
        x = g["x"] + left * g["w"]
        y = g["y"] + top * g["h"]
        out[name] = (x, y, (right - left) * g["w"], (bottom - top) * g["h"])
    return out


def rects_overlap(a, b, pad=0):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (
        ax + aw + pad <= bx
        or bx + bw + pad <= ax
        or ay + ah + pad <= by
        or by + bh + pad <= ay
    )


def ocr_game_rect(frame_w, frame_h, preset, custom=None):
    """Game rect in *captured-frame* pixels.

    A 16:9 canvas (stream recording / 1920x1080 capture) uses the overlay game
    rect, so letterboxed recordings crop the same way the overlay is anchored.
    A native game window (ultrawide 3440x1440, etc.) already *is* the game, so
    the crop is the full frame — no regression for the live Wardogs detector.
    """
    preset = normalize_layout(preset)
    fw, fh = int(frame_w), int(frame_h)
    aspect = fw / max(1, fh)
    canvas_aspect = CANVAS_W / CANVAS_H
    if abs(aspect - canvas_aspect) < 0.08:
        g = game_rect(preset, custom)
        sx = fw / CANVAS_W
        sy = fh / CANVAS_H
        return (
            int(round(g["x"] * sx)),
            int(round(g["y"] * sy)),
            int(round(g["w"] * sx)),
            int(round(g["h"] * sy)),
        )
    return (0, 0, fw, fh)


def region_pixels(frac, frame_wh, preset, custom=None):
    """Map a game-relative (l, t, r, b) fraction onto frame pixels via the game rect."""
    gx, gy, gw, gh = ocr_game_rect(frame_wh[0], frame_wh[1], preset, custom)
    left, top, right, bottom = frac
    return (
        int(gx + left * gw),
        int(gy + top * gh),
        int(gx + right * gw),
        int(gy + bottom * gh),
    )


def layout_public(preset, custom=None, canvas_w=CANVAS_W, canvas_h=CANVAS_H):
    preset = normalize_layout(preset)
    g = game_rect(preset, custom, canvas_w, canvas_h)
    return {
        "display_layout": preset,
        "game_rect": g,
        "canvas": {"w": canvas_w, "h": canvas_h},
        "presets": list(PRESETS),
        "anchors": all_anchors(preset, custom, canvas_w, canvas_h),
    }
