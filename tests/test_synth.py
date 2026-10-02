"""The pack renders deterministically and every clip meets the loudness spec."""
import hashlib
import json

import numpy as np
import pytest
import tactical_pack
from scipy.io import wavfile


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    out = tmp_path_factory.mktemp("pack")
    tactical_pack.build(out, False)
    return out, json.loads((out / "pack.json").read_text())


def test_all_clips_rendered(pack):
    out, meta = pack
    assert len(meta["clips"]) == 24
    for c in meta["clips"]:
        assert (out / c["file"]).exists()
        assert c["license"] == "CC-BY-4.0" and "SwampBewdy" in c["credit"]


def test_loudness_spec(pack):
    out, meta = pack
    for c in meta["clips"]:
        rate, x = wavfile.read(out / c["file"])
        assert rate == 48000
        x = x.astype(np.float32)
        if x.dtype.kind == "i" or np.abs(x).max() > 1.5:
            x = x / 32768.0
        assert np.abs(x).max() <= 0.235, c["id"]          # engine clamps SFX at 0.25
        assert -26 <= c["rms_db"] <= -19, c["id"]           # matched loudness, no "one loud button"


def test_deterministic(tmp_path, pack):
    out, meta = pack
    tactical_pack.build(tmp_path, False)
    for c in meta["clips"][:4]:
        a = hashlib.sha256((out / c["file"]).read_bytes()).hexdigest()
        b = hashlib.sha256((tmp_path / c["file"]).read_bytes()).hexdigest()
        assert a == b, c["id"]


def test_credit_is_embedded_in_every_wav(pack):
    out, meta = pack
    for c in meta["clips"]:
        raw = (out / c["file"]).read_bytes()
        assert b"LIST" in raw and b"Tactical Pack by SwampBewdy" in raw, c["id"]
        assert int.from_bytes(raw[4:8], "little") == len(raw) - 8   # RIFF size still valid
