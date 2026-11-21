"""Unit tests for Protocol HUD capture recovery (no Windows / dxcam required)."""
import unittest

from autodetect import STALE_REINIT_S, REUSE_MAX_S, DXGI_ACCESS_LOST, classify_frame, is_access_lost


class ClassifyFrameTests(unittest.TestCase):
    def test_fresh_frame_always_fresh(self):
        self.assertEqual(classify_frame(10.0, 9.0, True), "fresh")
        self.assertEqual(classify_frame(10.0, None, True), "fresh")

    def test_short_none_reuses_last(self):
        self.assertEqual(classify_frame(10.5, 10.0, False), "reuse")
        self.assertEqual(classify_frame(10.0 + REUSE_MAX_S, 10.0, False), "reuse")

    def test_mid_gap_skips_stale_last(self):
        mid = 10.0 + REUSE_MAX_S + 0.5
        self.assertLess(mid - 10.0, STALE_REINIT_S)
        self.assertEqual(classify_frame(mid, 10.0, False), "skip")

    def test_long_none_reinits(self):
        self.assertEqual(classify_frame(10.0 + STALE_REINIT_S, 10.0, False), "reinit")
        self.assertEqual(classify_frame(20.0, 10.0, False), "reinit")

    def test_never_had_a_frame_reinits(self):
        self.assertEqual(classify_frame(10.0, None, False), "reinit")

    def test_access_lost_reinits_even_if_frame_present(self):
        self.assertEqual(classify_frame(10.0, 9.9, True, access_lost=True), "reinit")
        self.assertEqual(classify_frame(10.0, 9.9, False, access_lost=True), "reinit")


class AccessLostTests(unittest.TestCase):
    def test_hresult_in_args(self):
        signed = DXGI_ACCESS_LOST - (1 << 32)  # 0x887A0026 as int32: -2005270490
        self.assertTrue(is_access_lost(OSError(DXGI_ACCESS_LOST, "fail")))
        self.assertTrue(is_access_lost(OSError(signed, "fail")))
        class ComErr(Exception):
            def __init__(self):
                super().__init__("access lost")
                self.hresult = signed
        self.assertTrue(is_access_lost(ComErr()))

    def test_message_text(self):
        self.assertTrue(is_access_lost(RuntimeError("Desktop duplication access loss (HRESULT=0x887A0026)")))
        self.assertTrue(is_access_lost(RuntimeError("AccessLost")))

    def test_unrelated(self):
        self.assertFalse(is_access_lost(RuntimeError("Invalid Region: Region should be in 1080x1920")))
        self.assertFalse(is_access_lost(ValueError("nope")))


class HealthIdleTests(unittest.TestCase):
    def test_idle_not_stale_even_if_last_frame_is_old(self):
        # Import Capture without opening dxcam by stubbing _open.
        import autodetect as ad

        class Dummy(ad.Capture):
            def _open(self):
                self.backend = "dxcam"
                self.cam = object()
                self.out = (1920, 1080)

        cap = Dummy()
        cap.last_fresh_ts = 0.0
        idle = cap.health(capturing=False)
        self.assertFalse(idle["stale"])
        active = cap.health(capturing=True)
        self.assertTrue(active["stale"])


if __name__ == "__main__":
    unittest.main()
