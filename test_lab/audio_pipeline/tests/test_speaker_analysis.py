import unittest

import numpy as np
from scipy.signal import lfilter

from test_lab.audio_pipeline import barge, speaker_analysis as sa

FS = 16000


def sine(f, seconds, amp_fs, fs=FS):
    t = np.arange(int(seconds * fs)) / fs
    return np.sin(2 * np.pi * f * t) * amp_fs * 32768


class ToneTests(unittest.TestCase):
    def test_generated_tone_has_the_requested_peak_level(self):
        x = sa.tone(1000, 1.0, -12.0)
        self.assertAlmostEqual(20 * np.log10(np.abs(x).max() / 32768), -12.0, delta=0.1)
        self.assertEqual(x[0], 0)                                    # faded in: no click

    def test_level_of_a_known_tone(self):
        rng = np.random.default_rng(0)
        mic = (sine(1000, 2.0, 0.1) + rng.standard_normal(2 * FS) * 20).astype(np.int16)
        m = sa.tone_metrics(mic, 1000)
        self.assertAlmostEqual(m["level_dbfs"], -20 - 3.01, delta=0.5)          # 0.1 FS peak = -23 dBFS RMS
        self.assertLess(m["thd_pct"], 1.0)
        self.assertGreater(m["snr_db"], 40)

    def test_distortion_is_measured(self):
        for pct in (2.0, 10.0):
            mic = (sine(1000, 2.0, 0.1) + sine(3000, 2.0, 0.1 * pct / 100)).astype(np.int16)
            self.assertAlmostEqual(sa.tone_metrics(mic, 1000)["thd_pct"], pct, delta=pct * 0.15)

    def test_clipping_shows_up_as_distortion_and_peak(self):
        mic = np.clip(sine(1000, 2.0, 0.6) * 4, -32768, 32767).astype(np.int16)
        m = sa.tone_metrics(mic, 1000)
        self.assertGreater(m["thd_pct"], 15)
        self.assertGreater(m["peak_dbfs"], -0.5)

    def test_band_rms_isolates_the_band(self):
        mix = (sine(1000, 1.0, 0.1) + sine(3000, 1.0, 0.1)).astype(np.int16)
        self.assertAlmostEqual(sa.band_rms_dbfs(mix, 900, 1100), -23.0, delta=0.6)
        self.assertLess(sa.band_rms_dbfs(mix, 5000, 6000), -80)


class DelayTests(unittest.TestCase):
    def test_delay_is_recovered(self):
        rng = np.random.default_rng(1)
        played = rng.standard_normal(FS)
        for delay in (0, 173, 640, 2400):
            mic = np.concatenate([np.zeros(delay), played * 0.2, np.zeros(4000)]) + rng.standard_normal(delay + FS + 4000) * 0.05
            k, conf = sa.find_delay(played, mic, 8000)
            self.assertAlmostEqual(k, delay, delta=1, msg=str(delay))
            self.assertGreater(conf, 0.6)

    def test_no_match_gives_low_confidence(self):
        rng = np.random.default_rng(2)
        _, conf = sa.find_delay(rng.standard_normal(FS), rng.standard_normal(FS * 2), 8000)
        self.assertLess(conf, 0.15)


class SweepTests(unittest.TestCase):
    def test_lowpass_shows_up_in_the_response(self):
        f1, f2, T, delay = 100.0, 7500.0, 4.0, 320
        sweep = sa.log_sweep(f1, f2, T, -10.0, rate=FS).astype(float)
        a = np.exp(-2 * np.pi * 2000 / FS)                                   # one-pole low-pass at 2 kHz
        heard = lfilter([1 - a], [1, -a], sweep)
        mic = np.concatenate([np.zeros(delay), heard, np.zeros(2000)])
        f, lvl = sa.sweep_response(mic, f1, f2, T, delay)
        resp = dict(zip(f, lvl))
        self.assertAlmostEqual(resp[1000.0], 0.0, delta=1.0)
        self.assertAlmostEqual(resp[4000.0], -20 * np.log10(np.sqrt(1 + 4)) + 20 * np.log10(np.sqrt(1 + 0.25)), delta=2.0)
        self.assertLess(resp[6300.0], resp[4000.0] - 2)
        self.assertGreater(resp[250.0], -3.0)

    def test_flat_path_is_flat(self):
        f1, f2, T = 100.0, 7500.0, 4.0
        sweep = sa.log_sweep(f1, f2, T, -10.0, rate=FS).astype(float)
        f, lvl = sa.sweep_response(np.concatenate([np.zeros(100), sweep, np.zeros(500)]), f1, f2, T, 100)
        self.assertLess(max(lvl) - min(lvl), 3.0, list(zip(f, lvl)))


class OnsetTests(unittest.TestCase):
    def make(self, start_s, seconds, amp, noise=20.0, seed=0):
        rng = np.random.default_rng(seed)
        n = int(4.0 * FS)
        x = rng.standard_normal(n) * noise
        a = int(start_s * FS)
        x[a:a + int(seconds * FS)] += sine(1000, seconds, amp)
        return x

    def test_finds_the_start_of_a_tone(self):
        for start in (0.6, 1.0, 1.7):
            x = self.make(start, 1.2, 0.05)
            idx, conf = sa.onset(x, int(0.4 * FS), (850, 1150), frac=0.5)
            self.assertAlmostEqual(idx / FS, start, delta=0.02, msg=str(start))
            self.assertGreater(conf, 0.9)

    def test_noise_alone_scores_near_zero(self):
        for seed in range(6):
            x = self.make(0.6, 0.0, 0.0, seed=seed)
            _, conf = sa.onset(x, int(0.4 * FS), (850, 1150), frac=0.5)
            self.assertLess(conf, 0.5, f"seed {seed}")            # chance alone can reach ~0.4

    def test_a_barely_audible_tone_scores_low(self):
        x = self.make(0.8, 1.0, 0.00015)                                  # about the noise level inside the 300 Hz band
        _, conf = sa.onset(x, int(0.4 * FS), (850, 1150), frac=0.5)
        self.assertLess(conf, 0.6)

    def test_clock_drift_between_speaker_and_mic_does_not_matter(self):
        rng = np.random.default_rng(3)
        speech = np.convolve(rng.standard_normal(3 * SPK), np.ones(4) / 4, mode="same") * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * np.arange(3 * SPK) / SPK) ** 2)
        speech = speech / np.abs(speech).max() * 3000
        from scipy.signal import resample_poly
        for ppm in (0, 300, -300):
            heard = resample_poly(speech, 320 * (1000000 + ppm), 441 * 1000000) if ppm else resample_poly(speech, 320, 441)
            x = np.concatenate([rng.standard_normal(int(0.5 * FS)) * 20, heard + rng.standard_normal(len(heard)) * 20, rng.standard_normal(FS) * 20])
            idx, conf = sa.onset(x, int(0.4 * FS), (200, 3400), frac=0.2)
            self.assertGreater(conf, 0.85, f"{ppm} ppm")
            self.assertAlmostEqual(idx / FS, 0.5, delta=0.35)


SPK = 22050


class BargeReplicaTests(unittest.TestCase):
    def test_min_threshold_and_consecutive_frames(self):
        speaking = np.ones(40, bool)
        # ambient 100 => 100 * 3.2 * 6.5 = 2080, so the 5200 minimum is what sets the threshold
        quiet = np.full(40, 5000.0)                                          # under the 5200 floor
        self.assertEqual(barge.simulate(quiet, speaking, ambient=100.0)["triggers"], [])
        loud = np.full(40, 6000.0)                                           # over it
        r = barge.simulate(loud, speaking, ambient=100.0)
        self.assertEqual(r["thresholds"][0], 5200.0)
        self.assertEqual(barge.simulate(loud, speaking)["triggers"], [], "with the firmware's starting floor of 500 the bar is 10400")
        self.assertEqual(r["triggers"][0], 5, "fires on the 6th consecutive frame")
        self.assertEqual(len(r["triggers"]), 6)                              # counter restarts after each trigger

    def test_one_quiet_frame_resets_the_count(self):
        peaks = np.array([6000.0] * 5 + [100.0] + [6000.0] * 5 + [100.0])
        self.assertEqual(barge.simulate(peaks, np.ones(len(peaks), bool))["triggers"], [])

    def test_threshold_follows_the_noise_floor_while_speaking(self):
        speaking = np.ones(20, bool)
        r = barge.simulate(np.full(20, 100.0), speaking, ambient=1000.0)
        self.assertAlmostEqual(r["thresholds"][0], 1000 * 3.2 * 6.5)         # 20800, above the 5200 minimum
        r = barge.simulate(np.full(20, 15000.0), speaking, ambient=1000.0)
        self.assertEqual(r["triggers"], [], "15000 is under 20800")
        r = barge.simulate(np.full(20, 22000.0), speaking, ambient=1000.0)
        self.assertTrue(r["triggers"])

    def test_noise_floor_adapts_only_when_not_speaking(self):
        r = barge.simulate(np.full(100, 2000.0), np.zeros(100, bool), ambient=500.0)
        self.assertGreater(r["ambient"], 1500)                               # 2 s of adaptation toward 2000
        r = barge.simulate(np.full(100, 2000.0), np.ones(100, bool), ambient=500.0)
        self.assertEqual(r["ambient"], 500.0)
        r = barge.simulate(np.full(100, 40.0), np.zeros(100, bool), ambient=500.0)
        self.assertEqual(r["ambient"], 500.0, "peaks of 50 or less are ignored")

    def test_frame_peaks_scale_to_production_gain(self):
        l = np.full(512, 1000, np.int16)
        self.assertEqual(barge.frame_peaks(l, None, lab_gain=12.0).tolist(), [1000.0, 1000.0])
        self.assertEqual(barge.frame_peaks(l, None, lab_gain=6.0).tolist(), [2000.0, 2000.0])
        r = np.full(512, -3000, np.int16)
        self.assertEqual(barge.frame_peaks(l, r, 12.0, "loudest").tolist(), [3000.0, 3000.0], "louder mic per sample, like production")
        self.assertEqual(barge.frame_peaks(l, r, 12.0, "auto").tolist(), [1000.0, 1000.0])

    def test_recommendation(self):
        self.assertTrue(barge.recommend(2000, 12000)["separable"])
        rec = barge.recommend(2000, 12000)
        self.assertEqual(rec["threshold_range"], (2600, 8400))
        self.assertFalse(barge.recommend(5000, 5500)["separable"])
        self.assertFalse(barge.recommend(0, 100)["separable"])


if __name__ == "__main__":
    unittest.main()
