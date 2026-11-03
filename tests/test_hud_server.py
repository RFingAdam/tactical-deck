"""Protocol HUD server: STREAM PROFIT, display layout presets, per-game profiles.

server.py is copied into a temp folder before import so its config.json / state.json never land in the repo.
"""
import importlib
import importlib.util
import json
import shutil
import struct
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

HUD = Path(__file__).resolve().parents[1] / "hud"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path[:0] = [str(HUD)]
layout = importlib.import_module("layout")
hudprof = importlib.import_module("profile")


def _copy_hud(tmp_path):
    shutil.copy(HUD / "server.py", tmp_path / "server.py")
    shutil.copy(HUD / "layout.py", tmp_path / "layout.py")
    shutil.copy(HUD / "profile.py", tmp_path / "profile.py")
    shutil.copytree(HUD / "games", tmp_path / "games")
    for name in ("overlay.html", "overlay.js", "control.html"):
        shutil.copy(HUD / name, tmp_path / name)


def _exec_server(tmp_path):
    spec = importlib.util.spec_from_file_location("hud_server_under_test", tmp_path / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def srv(tmp_path):
    _copy_hud(tmp_path)
    return _exec_server(tmp_path)


@pytest.fixture
def hud_http(srv):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    yield f"http://{host}:{port}", srv
    httpd.shutdown()


def _get(url):
    with urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(url):
    req = Request(url, data=b"", method="POST")
    with urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def profit(srv, *balances, how=None):
    for b in balances:
        srv.apply_cash(b, how=how)
    return srv.public()["cash_delta"]


def test_no_reading_yet(srv):
    assert srv.public()["cash_delta"] is None


def test_increases_add_up_and_round_spend_is_invested(srv):
    # 100 -> 120 earn; 120 -> 115 round $5k unlock (invest); 115 -> 160 earn
    # raw = +60k; invested 5k; profit = 65k
    assert profit(srv, 100_000, 120_000, 115_000, 160_000) == 65_000
    assert srv.public()["cash_invested"] == 5_000


def test_misread_dip_that_comes_back_is_not_income(srv):
    # 200k read once as 47k (the session delta), then the real balance again plus a little income
    # old debt-paid counted only +1500 earned above the dip base; STREAM PROFIT raw = +1500
    assert profit(srv, 200_000, 47_000, 201_500) == 1_500


def test_real_purchase_then_income(srv):
    # 200 -> 150 purchase (50k round = invest), then +2k
    # raw = 152k-200k = -48k; invested 50k; profit = +2k
    assert profit(srv, 200_000, 150_000, 152_000) == 2_000
    assert srv.public()["cash_invested"] == 50_000


def test_implausible_jump_is_ignored(srv):
    # 100k -> 300k is > CASH_MAX_STEP; balance must not move, profit stays 0
    assert profit(srv, 100_000, 300_000) == 0
    assert srv.public()["cash_now"] == 100_000


def test_menu_unlock_is_invested(srv):
    # 100 -> 50 menu unlock $50k; profit = raw(-50k) + invest(50k) = 0
    assert profit(srv, 100_000, 50_000, how="menu") == 0
    assert srv.public()["cash_invested"] == 50_000


def test_new_user_defaults_to_16x9_and_wardogs(srv):
    st = srv.public()
    assert srv.CFG["port"] == 8790
    assert st["display_layout"] == "16:9"
    assert st["game_rect"] == {"x": 0, "y": 0, "w": 1920, "h": 1080}
    assert st["game"] == "wardogs"
    assert st["profile"]["id"] == "wardogs"
    assert st["profile"]["stat_names"]["kills"] == "KILLS"
    assert "kills" in st and "kd" in st  # original /api/state fields stay


def test_existing_config_without_layout_stays_ultrawide(tmp_path):
    _copy_hud(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({
        "host": "127.0.0.1", "port": 8790, "auto_detect": False, "game_boost": False,
        "sound_pack": "tactical", "hotkeys": {},
    }), encoding="utf-8")
    mod = _exec_server(tmp_path)
    assert mod.CFG["display_layout"] == "ultrawide-letterbox"
    assert mod.public()["game_rect"] == {"x": 0, "y": 138, "w": 1920, "h": 803}


def test_ultrawide_letterbox_matches_current_overlay_css():
    a = layout.hud_anchors("ultrawide-letterbox", pos="bar")
    assert a["hud"]["left"] == 40 and a["hud"]["top"] == 19
    assert a["cashbox"]["right"] == 40 and a["cashbox"]["top"] == 19
    assert a["callout"]["top"] == 205
    assert a["kia"]["top"] == 330
    html = (HUD / "overlay.html").read_text(encoding="utf-8")
    assert "left:40px;top:19px" in html
    assert "right:40px;top:19px" in html
    assert "body.layout-bar #callout{top:205px}" in html
    assert "body.layout-bar #kia{top:330px}" in html
    for pos, left, top in (("tl", 36, 36),):
        c = layout.hud_anchors("ultrawide-letterbox", pos=pos)
        assert c["hud"]["left"] == left and c["hud"]["top"] == top


def test_16x9_hud_does_not_overlap_kill_feed_minimap_or_ammo():
    boxes = layout.persistent_boxes("16:9", pos="bar")
    zones = layout.keep_clear_boxes("16:9")
    assert set(zones) == {"kill_feed", "minimap", "ammo_health"}
    for name, box in boxes.items():
        for zone_name, zone in zones.items():
            assert not layout.rects_overlap(box, zone), f"{name} {box} overlaps {zone_name} {zone}"


def test_16x9_fixture_screenshot_is_1920x1080():
    path = FIXTURES / "wardogs_16x9.png"
    assert path.is_file(), "add tests/fixtures/wardogs_16x9.png (1920x1080 layout proof)"
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (1920, 1080)


def test_ocr_crops_follow_the_game_rect():
    frac = (0.40, 0.66, 0.60, 0.88)  # Wardogs kill-confirm region
    full = layout.region_pixels(frac, (1920, 1080), "16:9")
    letter = layout.region_pixels(frac, (1920, 1080), "ultrawide-letterbox")
    assert full == (int(0.40 * 1920), int(0.66 * 1080), int(0.60 * 1920), int(0.88 * 1080))
    assert letter == (
        int(0.40 * 1920),
        int(138 + 0.66 * 803),
        int(0.60 * 1920),
        int(138 + 0.88 * 803),
    )
    assert letter != full
    # Live ultrawide game window is already the game — full-frame crop, no regression.
    assert layout.ocr_game_rect(3440, 1440, "ultrawide-letterbox") == (0, 0, 3440, 1440)
    assert layout.ocr_game_rect(3440, 1440, "16:9") == (0, 0, 3440, 1440)


def test_custom_game_rect_is_clamped_and_anchored():
    g = layout.game_rect("custom", {"x": 80, "y": 100, "w": 1600, "h": 720})
    assert g == {"x": 80, "y": 100, "w": 1600, "h": 720}
    a = layout.hud_anchors("custom", g, pos="bar")
    assert a["game_rect"] == g
    assert a["hud"]["left"] == 80 + 40


def test_api_state_exposes_layout_and_persists(hud_http):
    base, srv = hud_http
    st = _get(base + "/api/state")
    assert st["display_layout"] == "16:9"
    assert st["game_rect"]["w"] == 1920
    assert st["kills"] == 0
    body = _post(base + "/api/layout/16%3A9")
    assert body["display_layout"] == "16:9"
    body = _post(base + "/api/layout/" + "ultrawide-letterbox")
    assert body["display_layout"] == "ultrawide-letterbox"
    assert body["game_rect"] == {"x": 0, "y": 138, "w": 1920, "h": 803}
    saved = json.loads((Path(srv.CFG_PATH)).read_text(encoding="utf-8"))
    assert saved["display_layout"] == "ultrawide-letterbox"
    again = _get(base + "/api/layout")
    assert again["display_layout"] == "ultrawide-letterbox"
    custom = _post(base + "/api/layout/custom?x=10&y=120&w=1900&h=800")
    assert custom["display_layout"] == "custom"
    assert custom["game_rect"]["y"] == 120


def test_unknown_layout_is_rejected(hud_http):
    base, _srv = hud_http
    with pytest.raises(HTTPError) as err:
        _post(base + "/api/layout/superultrawide")
    assert err.value.code == 400
    msg = json.loads(err.value.read().decode("utf-8"))
    assert "error" in msg
    assert "16:9" in msg["error"] or "unknown" in msg["error"]


def test_wardogs_profile_matches_builtin_regions():
    p = hudprof.load_game("wardogs")
    assert p["id"] == "wardogs"
    assert p["name"] == "Wardogs"
    assert p["game_exe"] == "WardogsClient-Win64-Shipping.exe"
    assert p["stat_names"]["cash"] == "STREAM PROFIT"
    assert p["banners"]["kill"] == "KILL CONFIRMED"
    regions = {k: tuple(v) for k, v in p["detect"]["regions"].items()}
    assert regions["confirm"] == (0.40, 0.66, 0.60, 0.88)
    assert regions["cash"] == (0.82, 0.00, 1.00, 0.075)
    assert regions["downed"] == (0.38, 0.86, 0.64, 0.94)


def test_example_profile_loads():
    p = hudprof.load_game("example")
    assert p["id"] == "example"
    assert p["stat_names"]["kills"] == "ELIMS"
    pub = hudprof.public_profile(p)
    assert pub["banner"] == "Example // Live Stats"


def test_bad_profile_has_clear_error(tmp_path):
    broken = tmp_path / "oops.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(hudprof.ProfileError, match="invalid JSON"):
        hudprof.load_profile_file(str(broken))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"id": "bad", "name": "Bad", "colors": {"kill": "red"}}), encoding="utf-8")
    with pytest.raises(hudprof.ProfileError, match="#RRGGBB"):
        hudprof.load_profile_file(str(bad))
    with pytest.raises(hudprof.ProfileError, match="unknown game"):
        hudprof.load_game("does-not-exist")


def test_game_switch_live_without_restart(hud_http):
    base, srv = hud_http
    listed = _get(base + "/api/games")
    ids = {g["id"] for g in listed["games"]}
    assert ids >= {"wardogs", "example"}
    assert listed["active"] == "wardogs"
    ex = _post(base + "/api/game/example")
    assert ex["game"] == "example"
    assert ex["profile"]["stat_names"]["kills"] == "ELIMS"
    assert srv.CFG["game"] == "example"
    saved = json.loads(Path(srv.CFG_PATH).read_text(encoding="utf-8"))
    assert saved["game"] == "example"
    wd = _post(base + "/api/game/wardogs")
    assert wd["game"] == "wardogs"
    assert wd["profile"]["banners"]["kill"] == "KILL CONFIRMED"


def test_control_page_has_layout_and_game_controls():
    html = (HUD / "control.html").read_text(encoding="utf-8")
    assert "DISPLAY LAYOUT" in html
    assert "ultrawide-letterbox" in html
    assert 'id="games"' in html
    assert "/api/layout/" in html
    assert "/api/game/" in html
    assert 'id="gx"' in html and 'id="apply-custom"' in html


def test_contributing_has_add_a_game_and_pr_checklist():
    text = (HUD.parent / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "## Add a game" in text
    assert "## PR checklist" in text
    assert "No ripped game assets" in text or "ripped game assets" in text
    assert "hud/games/" in text


def test_host_still_binds_8790():
    assert layout.CANVAS_W == 1920 and layout.CANVAS_H == 1080
    cfg = json.loads((HUD / "config.example.json").read_text(encoding="utf-8"))
    assert cfg["port"] == 8790
    assert cfg["display_layout"] == "16:9"
    assert cfg["game"] == "wardogs"
