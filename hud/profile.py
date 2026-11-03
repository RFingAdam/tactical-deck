"""Per-game Protocol HUD profiles (hud/games/<id>.json)."""
from __future__ import annotations

import json
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
GAMES_DIR = os.path.join(ROOT, "games")

SCHEMA_VERSION = 1
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{6})$")
_STAT_KEYS = ("kills", "deaths", "wins", "cash", "kd", "streak", "best")
_COLOR_KEYS = ("kill", "death", "win", "cash")
_BANNER_KEYS = ("kill", "death", "win", "win_tag", "downed", "revived")
_REGION_KEYS = ("confirm", "downed", "center", "win", "respawn", "damagelog", "cash")
_KEYWORD_KEYS = (
    "confirm", "win", "respawn", "respawn_if_downed", "damagelog", "downed", "menu", "cash",
)


class ProfileError(ValueError):
    """Raised when a game profile is missing, malformed, or fails schema checks."""


def _err(path, msg):
    return ProfileError(f"{_rel(path)}: {msg}")


def _rel(path):
    try:
        return os.path.relpath(path, ROOT).replace("\\", "/")
    except Exception:
        return os.path.basename(path)


def _require_dict(path, obj, what):
    if not isinstance(obj, dict):
        raise _err(path, f"{what} must be an object")
    return obj


def _opt_str(path, obj, key, errors, allow_empty=False):
    if key not in obj:
        return
    v = obj[key]
    if not isinstance(v, str):
        errors.append(f"{key} must be a string")
    elif not allow_empty and not v.strip():
        errors.append(f"{key} must be a non-empty string")


def _hex_color(path, value, key, errors):
    if not isinstance(value, str) or not _COLOR_RE.match(value):
        errors.append(f"colors.{key} must be #RRGGBB (got {value!r})")


def _region(path, value, key, errors):
    if not (isinstance(value, (list, tuple)) and len(value) == 4):
        errors.append(f"detect.regions.{key} must be [left, top, right, bottom] fractions")
        return
    try:
        left, top, right, bottom = (float(v) for v in value)
    except (TypeError, ValueError):
        errors.append(f"detect.regions.{key} values must be numbers")
        return
    if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
        errors.append(
            f"detect.regions.{key} must satisfy 0 <= left < right <= 1 and 0 <= top < bottom <= 1"
        )


def validate_profile(data, path="profile.json"):
    """Validate a profile dict. Returns the dict on success; raises ProfileError otherwise."""
    _require_dict(path, data, "profile")
    errors = []
    pid = data.get("id")
    if not isinstance(pid, str) or not _ID_RE.match(pid):
        errors.append("id must be a slug like 'wardogs' (lowercase letters, digits, _-)")
    else:
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem not in ("profile", pid) and not path.endswith("profile.json"):
            if stem != pid:
                errors.append(f"id {pid!r} must match the filename stem {stem!r}")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        errors.append("name must be a non-empty string")
    ver = data.get("schema", SCHEMA_VERSION)
    if ver != SCHEMA_VERSION:
        errors.append(f"unsupported schema version {ver!r} (expected {SCHEMA_VERSION})")

    _opt_str(path, data, "game_exe", errors, allow_empty=True)
    _opt_str(path, data, "banner", errors)

    stats = data.get("stat_names")
    if stats is not None:
        _require_dict(path, stats, "stat_names")
        for k, v in stats.items():
            if k not in _STAT_KEYS:
                errors.append(f"stat_names.{k} is not a known stat")
            elif not isinstance(v, str) or not v.strip():
                errors.append(f"stat_names.{k} must be a non-empty string")

    colors = data.get("colors")
    if colors is not None:
        _require_dict(path, colors, "colors")
        for k, v in colors.items():
            if k not in _COLOR_KEYS:
                errors.append(f"colors.{k} is not a known color")
            else:
                _hex_color(path, v, k, errors)

    banners = data.get("banners")
    if banners is not None:
        _require_dict(path, banners, "banners")
        for k, v in banners.items():
            if k not in _BANNER_KEYS:
                errors.append(f"banners.{k} is not a known banner")
            elif not isinstance(v, str) or not v.strip():
                errors.append(f"banners.{k} must be a non-empty string")

    sprees = data.get("sprees")
    if sprees is not None:
        _require_dict(path, sprees, "sprees")
        for k, v in sprees.items():
            try:
                n = int(k)
            except (TypeError, ValueError):
                n = -1
            if n < 2:
                errors.append(f"sprees keys must be kill-streak integers >= 2 (got {k!r})")
            if not isinstance(v, str) or not v.strip():
                errors.append(f"sprees.{k} must be a non-empty string")

    detect = data.get("detect")
    if detect is not None:
        _require_dict(path, detect, "detect")
        regions = detect.get("regions")
        if regions is not None:
            _require_dict(path, regions, "detect.regions")
            for k, v in regions.items():
                if k not in _REGION_KEYS:
                    errors.append(f"detect.regions.{k} is not a known OCR region")
                else:
                    _region(path, v, k, errors)
        keywords = detect.get("keywords")
        if keywords is not None:
            _require_dict(path, keywords, "detect.keywords")
            for k, v in keywords.items():
                if k not in _KEYWORD_KEYS:
                    errors.append(f"detect.keywords.{k} is not a known keyword")
                elif not isinstance(v, str) or not v.strip():
                    errors.append(f"detect.keywords.{k} must be a non-empty regex string")
                else:
                    try:
                        re.compile(v, re.I)
                    except re.error as e:
                        errors.append(f"detect.keywords.{k} is not a valid regex: {e}")

    extra_top = set(data) - {
        "schema", "id", "name", "game_exe", "banner", "stat_names", "colors",
        "banners", "sprees", "detect",
    }
    if extra_top:
        errors.append("unknown keys: " + ", ".join(sorted(extra_top)))

    if errors:
        raise _err(path, "; ".join(errors))
    return data


def load_profile_file(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as e:
        raise _err(path, "file not found") from e
    except json.JSONDecodeError as e:
        raise _err(path, f"invalid JSON ({e.msg} at line {e.lineno})") from e
    return validate_profile(data, path)


def games_dir():
    return GAMES_DIR


def list_game_ids(directory=None):
    d = directory or GAMES_DIR
    if not os.path.isdir(d):
        return []
    ids = []
    for name in sorted(os.listdir(d)):
        if name.endswith(".json") and not name.startswith("."):
            ids.append(os.path.splitext(name)[0])
    return ids


def load_game(game_id, directory=None):
    d = directory or GAMES_DIR
    if not isinstance(game_id, str) or not _ID_RE.match(game_id):
        raise ProfileError(f"unknown game {game_id!r}")
    path = os.path.join(d, game_id + ".json")
    if not os.path.isfile(path):
        known = ", ".join(list_game_ids(d)) or "(none)"
        raise ProfileError(f"unknown game {game_id!r}; available: {known}")
    return load_profile_file(path)


def list_games(directory=None, skip_bad=True):
    """Return [{id, name, error?}, ...] for every profile on disk."""
    d = directory or GAMES_DIR
    out = []
    for gid in list_game_ids(d):
        path = os.path.join(d, gid + ".json")
        try:
            p = load_profile_file(path)
            out.append({"id": p["id"], "name": p["name"], "game_exe": p.get("game_exe") or ""})
        except ProfileError as e:
            if skip_bad:
                out.append({"id": gid, "name": gid, "error": str(e)})
            else:
                raise
    return out


def public_profile(p):
    if not p:
        return None
    sprees = p.get("sprees") or {}
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "banner": p.get("banner") or "Protocol // Live Stats",
        "game_exe": p.get("game_exe") or "",
        "stat_names": dict(p.get("stat_names") or {}),
        "colors": dict(p.get("colors") or {}),
        "banners": dict(p.get("banners") or {}),
        "sprees": {str(k): v for k, v in sprees.items()},
    }
