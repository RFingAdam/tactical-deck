"""Offline tests for the game status line (hud/status_line.py).
Replays OCR text read from two real stream recordings (fixtures/status_line/r15.jsonl, r16.jsonl: Windows OCR via
the HUD's own regions, one frame every 3 s, reduced to HUD words) plus scripted event sequences."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hud"))
from status_line import IDLE_TEXT, RE_DEPLOY, RE_FLYING, StatusLine  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def load(name):
    with open(os.path.join(HERE, "fixtures", "status_line", name), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def replay(rows, step=3.0):
    clk = Clock()
    sl = StatusLine(clock=clk)
    out = {}
    for row in rows:
        clk.t += step
        sl.on_frame_text(row["respawn"], row["side"])
        out[int(row["f"][2:6])] = sl.current()
    return out


class Events(unittest.TestCase):
    def setUp(self):
        self.clk = Clock()
        self.sl = StatusLine(clock=self.clk)

    def at(self, dt):
        self.clk.t += dt
        return self.sl.current()

    def test_idle_fallback(self):
        self.assertEqual(self.sl.current()["text"], IDLE_TEXT)

    def test_streak_builds_and_resets_on_death(self):
        for n in (1, 2):
            self.sl.on_event("kill", streak=n)
        self.assertEqual(self.at(0)["state"], "idle")          # 1-2 kills: no streak line
        self.sl.on_event("kill", streak=3)
        self.assertEqual(self.at(0)["text"], "3 KILL STREAK")
        self.sl.on_event("kill", streak=4)
        self.assertEqual(self.at(0.5)["text"], "4 KILL STREAK")  # same-state update is immediate
        self.sl.on_event("kill", streak=5)
        self.assertEqual(self.at(0.5)["text"], "5 KILL STREAK - ON A TEAR")
        self.sl.on_event("death")
        s = self.at(0)
        self.assertEqual(s["text"], "KIA - REDEPLOYING")
        self.assertEqual(s["streak"], 0)

    def test_streak_line_expires(self):
        self.sl.on_event("kill", streak=3)
        self.assertEqual(self.at(19)["state"], "streak")
        self.assertEqual(self.at(2)["state"], "idle")

    def test_downed_then_revived(self):
        self.sl.on_event("downed")
        self.assertEqual(self.at(0)["text"], "DOWN - WAITING ON A REVIVE")
        self.sl.on_event("revived")
        self.assertEqual(self.at(0)["text"], "BACK UP - REVIVED")
        self.assertEqual(self.at(9)["state"], "idle")

    def test_downed_never_sticks(self):
        self.sl.on_event("downed")                              # revive missed by OCR
        self.assertEqual(self.at(70)["state"], "downed")
        self.assertEqual(self.at(6)["state"], "idle")

    def test_kill_clears_downed(self):
        self.sl.on_event("downed")
        self.at(1)
        self.sl.on_event("kill", streak=1)
        self.assertEqual(self.at(4)["state"], "idle")

    def test_death_deploy_back_in_the_fight(self):
        self.sl.on_event("downed")
        self.at(5)
        self.sl.on_event("death")
        self.assertEqual(self.at(0)["state"], "kia")
        for _ in range(4):                                      # "WAITING TO DEPLOY... 8" read every 0.5 s
            self.clk.t += 0.5
            self.sl.on_frame_text("WAITING TO DEPLOY... 8", "")
        self.assertEqual(self.at(0)["text"], "DEPLOYING")
        self.assertEqual(self.at(4)["text"], "BACK IN THE FIGHT")  # screen gone > 3 s
        self.assertEqual(self.at(11)["text"], IDLE_TEXT)

    def test_single_ocr_blip_does_not_switch(self):
        self.sl.on_frame_text("WAITING TO DEPLOY", "")         # 1 hit < 2 needed
        self.clk.t += 0.5
        self.sl.on_frame_text("", "")
        self.assertEqual(self.at(0)["state"], "idle")

    def test_min_hold_no_flicker(self):
        self.sl.on_event("kill", streak=3)
        self.at(1)
        self.sl.on_event("death")                               # outranks: immediate
        self.assertEqual(self.at(0)["state"], "kia")
        self.sl.on_frame_text("", "INTERACT CHANGE SEAT")
        for _ in range(3):
            self.clk.t += 0.25
            self.sl.on_frame_text(None, "INTERACT CHANGE SEAT")
        self.assertEqual(self.at(0)["text"], "RIDING IN")       # in a seat = alive, clears KIA
        for _ in range(4):
            self.clk.t += 0.25
            self.sl.on_frame_text(None, "COLLECTIVE LIFT COLLECTIVE DESCENT DEPLOY FLARES")
        self.assertEqual(self.at(0)["text"], "FLYING IN")       # outranks riding
        self.sl.on_frame_text(None, "")
        self.assertEqual(self.at(4)["text"], "FLYING IN")       # 5 s grace
        self.assertNotEqual(self.at(6)["state"], "flying")

    def test_hold_before_idle(self):
        self.sl.on_event("kill", streak=3)
        self.at(1)
        self.sl.on_event("revived")                             # no downed before: ignored
        self.sl.streak = 3
        self.sl.last_kill_ts = None                             # streak line just ended
        self.assertEqual(self.at(0)["state"], "streak")         # held, not yanked to idle after 1 s
        self.assertEqual(self.at(3)["state"], "idle")

    def test_victory(self):
        self.sl.on_event("win")
        self.assertEqual(self.at(0)["text"], "VICTORY")
        self.assertEqual(self.at(31)["state"], "idle")

    def test_none_text_means_not_read(self):
        for _ in range(3):
            self.clk.t += 0.25
            self.sl.on_frame_text(None, "COLLECTIVE LIFT")
        self.assertEqual(self.at(0)["state"], "flying")
        self.clk.t += 0.25
        self.sl.on_frame_text(None, None)                       # downed prompt path skipped the hint OCR
        self.assertEqual(self.at(0)["state"], "flying")


class RealFrames(unittest.TestCase):
    """r15 (2026-11-15 19:50 CT): frames 6-220 Adam flies an AH-6 (pilot seat), crashes at ~224, RESPAWNING at 226,
    deploy screen 'WAITING TO DEPLOY' at 232-234, back on foot at 236+. r16 (2026-11-16 09:50 CT): passenger seat
    in a heli/vehicle at 586-632, no piloting, no deploy screen."""

    def test_regex_precision_on_real_text(self):
        for name, fly_ok in (("r15.jsonl", range(6, 221)), ("r16.jsonl", range(0))):
            for row in load(name):
                n = int(row["f"][2:6])
                if RE_FLYING.search(row["side"]):
                    self.assertIn(n, fly_ok, f"{name} frame {n} flagged FLYING: {row['side'][:80]}")
                if RE_DEPLOY.search(row["respawn"]):
                    self.assertTrue(name == "r15.jsonl" and 232 <= n <= 234, f"{name} {n} flagged DEPLOY")

    def test_replay_r15(self):
        out = replay(load("r15.jsonl"))
        flying = [n for n, s in out.items() if s["state"] == "flying"]
        self.assertTrue(flying and min(flying) >= 6 and max(flying) <= 225, (min(flying), max(flying)))
        in_seat = [n for n in range(10, 221) if out[n]["state"] in ("flying", "riding")]
        self.assertGreater(len(in_seat) / 211, 0.80)
        self.assertEqual(out[233]["state"], "deploying")
        self.assertIn(out[236]["state"], ("deploying", "back"))
        self.assertEqual(out[238]["state"], "back")             # 10 s hold, sampled every 3 s
        self.assertTrue(all(s["state"] in ("idle", "back") for n, s in out.items() if n > 245))

    def test_replay_r16(self):
        out = replay(load("r16.jsonl"))
        riding = [n for n, s in out.items() if s["state"] == "riding"]
        self.assertTrue(riding and min(riding) >= 586 and max(riding) <= 635, riding[:5])
        self.assertFalse(any(s["state"] in ("flying", "deploying") for s in out.values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
