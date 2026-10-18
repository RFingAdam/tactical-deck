"""Protocol HUD server: the "DEBT PAID" total (money earned this stream) built from balance readings.

server.py is copied into a temp folder before import so its config.json / state.json never land in the repo.
"""
import importlib.util
import shutil
from pathlib import Path

import pytest

HUD = Path(__file__).resolve().parents[1] / "hud"


@pytest.fixture
def srv(tmp_path):
    shutil.copy(HUD / "server.py", tmp_path / "server.py")
    spec = importlib.util.spec_from_file_location("hud_server_under_test", tmp_path / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def earned(srv, *balances):
    for b in balances:
        srv.apply_cash(b)
    return srv.public()["cash_delta"]


def test_no_reading_yet(srv):
    assert srv.public()["cash_delta"] is None


def test_increases_add_up_and_spending_does_not_subtract(srv):
    assert earned(srv, 100_000, 120_000, 115_000, 160_000) == 20_000 + 45_000


def test_misread_dip_that_comes_back_is_not_income(srv):
    # 200k read once as 47k (the session delta), then the real balance again plus a little income
    assert earned(srv, 200_000, 47_000, 201_500) == 1_500


def test_real_purchase_then_income(srv):
    assert earned(srv, 200_000, 150_000, 152_000) == 2_000


def test_implausible_jump_is_ignored(srv):
    assert earned(srv, 100_000, 300_000) == 0
