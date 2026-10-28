#!/usr/bin/env python3
"""Unit tests for STREAM PROFIT: one-time unlocks/buys count as invested, not losses.

Run:  python3 test_stream_profit.py
No HUD server, no OCR, no network. Isolates the cash math from server.py.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent


def load_server(tmp: Path):
    """Import server.py against a temp ROOT so state.json never touches the live HUD."""
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    # Force ROOT to tmp and skip hotkey/detector side effects on import
    src = src.replace(
        'ROOT = os.path.dirname(os.path.abspath(__file__))',
        f'ROOT = r"{tmp.as_posix()}"',
    )
    path = tmp / "server_under_test.py"
    path.write_text(src, encoding="utf-8")
    # minimal config so load_cfg is happy
    (tmp / "config.json").write_text(
        '{"host":"127.0.0.1","port":8790,"new_session_after_hours":0,'
        '"multikill_window_sec":8,"hotkeys_enabled":false,"auto_detect":false,'
        '"game_boost":false,"sound_pack":"tactical","detect_fps":4,'
        '"game_exe":"WardogsClient-Win64-Shipping.exe",'
        '"hotkeys":{},"overlay_sounds":false,"overlay_theme":"protocol"}',
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location("server_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    # silence broadcast / print noise
    with mock.patch("builtins.print"):
        spec.loader.exec_module(mod)
    # no-op broadcast / save side effects for speed
    mod.broadcast = lambda *a, **k: None
    return mod


class ClassifyDropTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hud-profit-"))
        self.s = load_server(self.tmp)

    def test_kit_after_death_window(self):
        now = time.time()
        # $16k drop 10s after death = kit
        kind = self.s.classify_drop(16_000, how="pair", now=now, last_death_ts=now - 10)
        self.assertEqual(kind, "kit")

    def test_menu_unlock_is_invest(self):
        now = time.time()
        kind = self.s.classify_drop(50_000, how="menu", now=now, last_death_ts=0)
        self.assertEqual(kind, "invest")

    def test_round_amount_is_invest(self):
        now = time.time()
        kind = self.s.classify_drop(25_000, how="pair", now=now, last_death_ts=0)
        self.assertEqual(kind, "invest")

    def test_small_drop_not_invest(self):
        now = time.time()
        kind = self.s.classify_drop(2_500, how="menu", now=now, last_death_ts=0)
        self.assertEqual(kind, "kit")

    def test_odd_amount_not_round_without_menu(self):
        now = time.time()
        kind = self.s.classify_drop(16_347, how="pair", now=now, last_death_ts=0)
        self.assertEqual(kind, "kit")


class ApplyCashMathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hud-profit-"))
        self.s = load_server(self.tmp)
        self.s.STATE = self.s.fresh()
        self.s.HISTORY.clear()

    def test_profit_formula_invest_added_back(self):
        # Start session at 100k
        self.s.start_cash_session(100_000)
        # Earn 20k
        self.s.apply_cash(120_000, how="pair")
        # Menu unlock 50k -> invested
        self.s.apply_cash(70_000, how="menu")
        pub = self.s.public()
        # raw = 70k - 100k = -30k; invested 50k; profit = +20k
        self.assertEqual(pub["cash_invested"], 50_000)
        self.assertEqual(pub["cash_raw_delta"], -30_000)
        self.assertEqual(pub["cash_delta"], 20_000)
        self.assertEqual(pub["cash_earned"], 20_000)

    def test_kit_after_death_stays_cost(self):
        self.s.start_cash_session(200_000)
        self.s.apply_cash(220_000, how="pair")  # +20k earn
        # death
        with self.s.LOCK:
            self.s.STATE["last_death_ts"] = time.time()
        self.s.apply_cash(204_000, how="pair")  # -16k kit
        pub = self.s.public()
        self.assertEqual(pub["cash_invested"], 0)
        self.assertEqual(pub["cash_kits"], 16_000)
        # raw = 204k-200k = +4k; profit = 4k
        self.assertEqual(pub["cash_delta"], 4_000)

    def test_manual_mark_invest(self):
        self.s.start_cash_session(100_000)
        # non-round, non-menu drop so auto-class stays kit
        self.s.apply_cash(83_653, how="pair")
        self.assertEqual(self.s.public()["cash_invested"], 0)
        self.assertEqual(self.s.public()["cash_kits"], 16_347)
        self.s.mark_last_drop_invested()
        pub = self.s.public()
        self.assertEqual(pub["cash_invested"], 16_347)
        self.assertEqual(pub["cash_kits"], 0)
        # raw -16347 + invest 16347 = 0
        self.assertEqual(pub["cash_delta"], 0)

    def test_spike_then_drop_misread_ignored(self):
        self.s.start_cash_session(100_000)
        # OCR spike to 180k then back to ~100k within window
        self.s.apply_cash(180_000, how="pair")
        self.s.apply_cash(100_500, how="pair")
        pub = self.s.public()
        # spike path should zero the fake gain; settle near start
        self.assertLessEqual(pub["cash_earned"], 15_000)
        self.assertIsNotNone(pub["cash_delta"])


class Replay1028Tests(unittest.TestCase):
    """Synthetic 2026-10-28 stream cash path.

    Goal: with the OLD formula (balance_now - balance_start only) this session
    looks deeply negative because of one-time unlocks. With invest accounting
    it lands near +$43,000.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hud-profit-"))
        self.s = load_server(self.tmp)
        self.s.STATE = self.s.fresh()
        self.s.HISTORY.clear()

    def test_oct28_replay_calibrated_plus_43k(self):
        """Calibrated path: old formula ~ deeply negative; new formula ≈ +$43,000."""
        START = 500_000
        self.s.start_cash_session(START)

        # profit = (end - start) + invested = +43_000
        # invested = 95_000 (50+25+20); kits = 32_000 (2x16k)
        # end - start = 43_000 - 95_000 = -52_000 → end = 448_000
        # earnings = 75_000
        # Check: 500k + 75k - 32k - 95k = 448k ✓
        # old raw = -52k; new = +43k ✓

        # (balance, how, mark_death_before, advance_clock_s)
        path = [
            (525_000, "pair", False, 0),
            (550_000, "pair", False, 0),
            (575_000, "pair", False, 0),   # earned 75k
            (525_000, "menu", False, 0),   # -50k unlock -> invest
            (509_000, "pair", True, 0),    # death then -16k kit
            (484_000, "pair", False, 90),  # past kit window; -25k round -> invest
            (464_000, "menu", False, 0),   # -20k unlock -> invest
            (448_000, "pair", True, 0),    # death then -16k kit
        ]

        now = time.time()
        for bal, how, died, adv in path:
            if adv:
                now += adv
            if died:
                with self.s.LOCK:
                    self.s.STATE["last_death_ts"] = now
            if self.s.STATE.get("cash_now") == bal:
                continue
            # pin "now" so kit-window classification sees advanced clock
            with mock.patch.object(self.s.time, "time", return_value=now):
                self.s.apply_cash(bal, how=how)
            now += 2  # small gap between readings

        pub = self.s.public()
        self.assertEqual(pub["cash_start"], START)
        self.assertEqual(pub["cash_now"], 448_000)
        self.assertEqual(pub["cash_invested"], 95_000)
        self.assertEqual(pub["cash_kits"], 32_000)
        self.assertEqual(pub["cash_raw_delta"], -52_000)
        self.assertEqual(pub["cash_delta"], 43_000)

        old_formula = pub["cash_now"] - pub["cash_start"]
        self.assertEqual(old_formula, -52_000)
        self.assertEqual(old_formula + pub["cash_invested"], 43_000)

        print("OCT28_REPLAY", {
            "start": pub["cash_start"],
            "end": pub["cash_now"],
            "earned": pub["cash_earned"],
            "invested": pub["cash_invested"],
            "kits": pub["cash_kits"],
            "raw_delta": pub["cash_raw_delta"],
            "stream_profit": pub["cash_delta"],
            "old_formula": old_formula,
        })


class SessionStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hud-profit-"))
        self.s = load_server(self.tmp)
        self.s.STATE = self.s.fresh()

    def test_start_session_clears_invest(self):
        self.s.start_cash_session(100_000)
        self.s.apply_cash(50_000, how="menu")
        self.assertEqual(self.s.public()["cash_invested"], 50_000)
        self.s.start_cash_session(50_000)
        pub = self.s.public()
        self.assertEqual(pub["cash_invested"], 0)
        self.assertEqual(pub["cash_kits"], 0)
        self.assertEqual(pub["cash_earned"], 0)
        self.assertEqual(pub["cash_delta"], 0)


if __name__ == "__main__":
    # Make sure we don't need Windows-only imports
    os.environ.setdefault("PROTOCOL_HUD_TEST", "1")
    r = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if r.result.wasSuccessful() else 1)
