import math
import unittest

import numpy as np

from test_lab.audio_pipeline import analysis
from test_lab.audio_pipeline.protocol import (HEADER, MASK_BOTH, MASK_LEFT, MASK_RIGHT, ProtocolError,
                                              config_command, pack_frame, parse_frame, parse_kv,
                                              validate_config)


class ProtocolTests(unittest.TestCase):
    def test_header_is_12_bytes(self):
        self.assertEqual(HEADER.size, 12)

    def test_roundtrip_all_masks(self):
        l = np.arange(0, 256, dtype=np.int16)
        r = -l
        for mask, want_l, want_r in ((MASK_BOTH, l, r), (MASK_LEFT, l, None), (MASK_RIGHT, None, r)):
            f = parse_frame(pack_frame(mask, 7, 1234, l, r))
            self.assertEqual((f.mask, f.seq, f.frames, f.device_ms), (mask, 7, 256, 1234))
            for got, want in ((f.left, want_l), (f.right, want_r)):
                if want is None:
                    self.assertIsNone(got)
                else:
                    np.testing.assert_array_equal(got, want)

    def test_interleaved_matches_wav_layout(self):
        f = parse_frame(pack_frame(MASK_BOTH, 0, 0, np.array([1, 2, 3], np.int16), np.array([-1, -2, -3], np.int16)))
        self.assertEqual(f.interleaved().tolist(), [1, -1, 2, -2, 3, -3])

    def test_rejects_malformed(self):
        good = pack_frame(MASK_BOTH, 0, 0, np.zeros(64, np.int16), np.zeros(64, np.int16))
        for bad in (good[:5], b"XX" + good[2:], good[:-2], good + b"\0\0", good[:3] + b"\x07" + good[4:]):
            with self.assertRaises(ProtocolError):
                parse_frame(bad)

    def test_config_validation(self):
        self.assertEqual(validate_config({"chunk": 512, "mic": "LEFT", "gain": "6"})["mic"], "left")
        for bad in ({"chunk": 100}, {"chunk": 16}, {"chunk": 2048}, {"mic": "middle"}, {"gain": 0.1}, {"gain": 99}, {"chunk": "x"}):
            with self.assertRaises(ProtocolError):
                validate_config(bad)

    def test_command_roundtrip(self):
        cfg = validate_config({"chunk": 384, "mic": "right", "gain": 7.5, "swap": 1})
        self.assertEqual(config_command(cfg), "CFG chunk=384 mic=right gain=7.5 swap=1")
        self.assertEqual(validate_config(parse_kv(config_command(cfg))), cfg)


def _fill(ring, x, now=100.0):
    ring.push(x.astype(np.int16), now)


class AnalysisTests(unittest.TestCase):
    def test_dbfs_of_known_sine(self):
        t = np.arange(16000)
        sine = (0.5 * 32767 * np.sin(2 * math.pi * 440 * t / 16000)).astype(np.int16)
        self.assertAlmostEqual(analysis.dbfs(sine), 20 * math.log10(0.5 / math.sqrt(2)), delta=0.05)
        self.assertEqual(analysis.dbfs(np.zeros(100, np.int16)), analysis.FLOOR_DB)

    def test_lag_sign_and_size(self):
        rng = np.random.default_rng(0)
        src = np.convolve(rng.standard_normal(6000), np.ones(4) / 4, mode="same") * 4000
        for delay in (0, 1, 3, 5):
            l, r = src[10:4010], src[10 - delay:4010 - delay]          # right delayed by `delay` => lag > 0
            lag, corr = analysis.estimate_lag(l, r)
            self.assertAlmostEqual(lag, delay, delta=0.25, msg=f"delay {delay}")
            self.assertGreater(corr, 0.9)
            lag2, _ = analysis.estimate_lag(r, l)                       # swapped channels => negative lag
            self.assertAlmostEqual(lag2, -delay, delta=0.25)

    def test_direction_refuses_silence_and_missing_channel(self):
        l, r = analysis.Ring(4096), analysis.Ring(4096)
        self.assertIsNone(analysis.direction(l, r, now=100.0))
        rng = np.random.default_rng(1)
        _fill(l, rng.standard_normal(4096) * 3000)
        self.assertIsNone(analysis.direction(l, r, now=100.0), "right mic never transmitted")
        _fill(r, rng.standard_normal(4096) * 1)                        # ~-90 dBFS: below the floor
        _fill(l, rng.standard_normal(4096) * 1)
        self.assertIsNone(analysis.direction(l, r, now=100.0), "too quiet to trust")

    def test_direction_reports_ild_and_lag(self):
        rng = np.random.default_rng(2)
        src = np.convolve(rng.standard_normal(8000), np.ones(4) / 4, mode="same") * 3000
        l, r = analysis.Ring(4096), analysis.Ring(4096)
        _fill(l, src[100:4196])
        _fill(r, src[98:4194] * 0.5)                                    # right: delayed 2 samples and 6 dB quieter
        d = analysis.direction(l, r, now=100.0)
        self.assertAlmostEqual(d["lag_samples"], 2.0, delta=0.3)
        self.assertAlmostEqual(d["ild_db"], -6.0, delta=0.5)

    def test_stale_channel_becomes_inactive(self):
        l, r = analysis.Ring(4096), analysis.Ring(4096)
        _fill(l, np.ones(2048) * 1000, now=100.0)
        _fill(r, np.ones(2048) * 1000, now=100.0)
        self.assertIsNotNone(analysis.levels(l, r, now=100.1)["both"])
        lv = analysis.levels(l, r, now=101.0)
        self.assertIsNone(lv["left"])
        self.assertIsNone(lv["both"])

    def test_both_level_is_mix(self):
        l, r = analysis.Ring(4096), analysis.Ring(4096)
        _fill(l, np.full(2048, 8000)); _fill(r, np.full(2048, -8000))    # opposite polarity cancels in the mix
        lv = analysis.levels(l, r, now=100.1)
        self.assertGreater(lv["left"]["rms"], -13)
        self.assertEqual(lv["both"]["rms"], analysis.FLOOR_DB)


if __name__ == "__main__":
    unittest.main()
