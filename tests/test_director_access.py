import copy
import json
from pathlib import Path

import pytest

import director as dmod

DEFAULT = json.loads(
    (Path(__file__).resolve().parents[1] / "director" / "config.default.json").read_text(encoding="utf-8")
)


def _cfg(**over):
    cfg = copy.deepcopy(DEFAULT)
    cfg.update(over)
    return cfg


def test_default_allows_loopback_without_origin():
    cfg = _cfg()
    dmod.validate_config(cfg)
    assert dmod.request_allowed(cfg, 8781, "127.0.0.1:8781", None)
    assert dmod.request_allowed(cfg, 8781, "localhost:8781", None)
    assert dmod.request_allowed(cfg, 8781, "127.0.0.1:8781", "http://127.0.0.1:8781")


def test_default_rejects_lan_host_and_foreign_origin():
    cfg = _cfg()
    assert not dmod.request_allowed(cfg, 8781, "192.168.1.10:8781", None)
    assert not dmod.request_allowed(cfg, 8781, "127.0.0.1:8781", "https://evil.example")
    assert not dmod.request_allowed(cfg, 8781, "127.0.0.1:8781", "http://192.168.1.10:8781")


def test_allowed_hosts_accepts_lan_ip_without_origin():
    cfg = _cfg(allowed_hosts=["192.168.1.10"])
    dmod.validate_config(cfg)
    assert dmod.request_allowed(cfg, 8781, "192.168.1.10:8781", None)
    assert dmod.request_allowed(cfg, 8781, "192.168.1.10:8781", "http://192.168.1.10:8781")
    assert not dmod.request_allowed(cfg, 8781, "192.168.1.11:8781", None)


def test_allowed_origins_for_a_kiosk():
    cfg = _cfg(allowed_hosts=["192.168.1.10"], allowed_origins=["https://deck.example.com"])
    dmod.validate_config(cfg)
    assert dmod.request_allowed(cfg, 8781, "192.168.1.10:8781", "https://deck.example.com")
    assert dmod.request_allowed(cfg, 8781, "192.168.1.10:8781", "https://deck.example.com/")
    assert not dmod.request_allowed(cfg, 8781, "192.168.1.10:8781", "https://other.example.com")


def test_validate_config_rejects_bad_entries():
    with pytest.raises(ValueError, match="allowed host"):
        dmod.validate_config(_cfg(allowed_hosts=["http://192.168.1.10"]))
    with pytest.raises(ValueError, match="allowed host"):
        dmod.validate_config(_cfg(allowed_hosts=["*.example.com"]))
    with pytest.raises(ValueError, match="allowed origin"):
        dmod.validate_config(_cfg(allowed_origins=["192.168.1.10"]))
    with pytest.raises(ValueError, match="bare origin"):
        dmod.validate_config(_cfg(allowed_origins=["https://deck.example.com/play"]))
