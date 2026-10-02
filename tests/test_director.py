"""Director logic, run against the silent demo engine (no audio device, any OS)."""
import time

import pytest

import director as dmod


@pytest.fixture
def director(monkeypatch, tmp_path):
    monkeypatch.setattr(dmod, "CONFIG_USER", tmp_path / "config.json")
    # No HUD / Streamlabs in CI: keep the background loops from reaching the network.
    monkeypatch.setattr(dmod.Director, "_reactive_loop", lambda self: None)
    monkeypatch.setattr(dmod.Director, "apply_hud_settings", lambda self: None)
    cfg = dmod.load_config()
    cfg["engine"]["type"] = "demo"
    cfg["streamlabs"]["enabled"] = False
    d = dmod.Director(cfg)
    yield d
    d.closed.set()


def wait_for(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_defaults_are_valid_and_reference_real_clips(director):
    dmod.validate_config(director.cfg)
    clips = {c["id"] for c in director.engine.library()["clips"]}
    for name, combo in director.cfg["combos"].items():
        for key in ("steps", "on", "off", "loop"):
            for step in combo.get(key, []):
                if step[0] == "play":
                    assert step[1] in clips, f"{name} plays unknown clip {step[1]}"
    for kind, rule in director.cfg["reactive"]["events"].items():
        assert rule["clip"] in clips, kind


def test_deep_merge_keeps_unspecified_defaults():
    merged = dmod.deep_merge({"a": {"x": 1, "y": 2}, "b": 3}, {"a": {"y": 9}})
    assert merged == {"a": {"x": 1, "y": 9}, "b": 3}


@pytest.mark.parametrize("bad", [
    {"duck": {"headphones_db": 5}},
    {"combos": {"x": {"steps": [["explode"]]}}},
    {"combos": {"x": {"steps": [["wait", 99]]}}},
])
def test_validation_rejects_bad_settings(bad):
    cfg = dmod.deep_merge(dmod.load_config(), bad)
    with pytest.raises(ValueError):
        dmod.validate_config(cfg)


def test_airstrike_combo_runs_in_order(director):
    director.combo("airstrike")
    assert wait_for(lambda: "t_incoming" in director.engine.status()["playing"])
    assert wait_for(lambda: "t_flyby" in director.engine.status()["playing"], 3)
    assert wait_for(lambda: "t_explosion" in director.engine.status()["playing"], 3)


def test_comms_toggle_switches_voice_and_back(director):
    assert director.combo("comms")["on"] is True
    assert wait_for(lambda: director.engine.mode == "radio")
    assert director.combo("comms")["on"] is False
    assert wait_for(lambda: director.engine.mode == "normal")


def test_stop_all_cancels_loops(director):
    director.combo("clutch")
    assert wait_for(lambda: "t_heartbeat" in director.engine.status()["playing"])
    director.stop_all()
    assert director.toggles == {}
    assert director.engine.status()["playing"] == []
    time.sleep(0.2)
    assert director.engine.status()["playing"] == []


def test_stop_all_restores_voice_from_active_toggle(director):
    director.combo("comms")
    assert wait_for(lambda: director.engine.mode == "radio")
    director.stop_all()
    assert director.engine.mode == "normal"
    assert director.engine.status()["playing"] == []   # no squelch sound on Stop All
    assert director.combo("comms")["on"] is True        # next press starts a fresh comms


@pytest.mark.parametrize("state, expected", [
    ({"streak": 5, "multi": 3}, "t_horn"),     # streak beats multi
    ({"streak": 3, "multi": 2}, "t_impact"),   # multi beats a plain kill
    ({"streak": 1, "multi": 1}, "t_confirm"),
])
def test_reactive_priority(director, state, expected):
    director.on_hud_event({"type": "kill", "state": state})
    assert expected in director.engine.status()["playing"]


def test_reactive_cooldown(director):
    director.on_hud_event({"type": "death", "state": {}})
    director.engine.stop()
    director.on_hud_event({"type": "death", "state": {}})
    assert director.engine.status()["playing"] == []


def test_headphone_duck_applied_to_engine(director):
    assert director.engine.duck_db == float(director.cfg["duck"]["headphones_db"])
    assert director.engine.exclude == director.cfg["duck"]["exclude"]
