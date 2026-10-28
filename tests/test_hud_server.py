"""Protocol HUD server: STREAM PROFIT from balance readings + invest accounting.

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
