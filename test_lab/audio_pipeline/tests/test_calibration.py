import asyncio
import json
import statistics
import tempfile
import unittest
from pathlib import Path

import numpy as np

from test_lab.audio_pipeline.calibration import (CalibrationError, Conditioner, evaluate, measure,
                                                 process_full)
from test_lab.audio_pipeline.protocol import MASK_BOTH, pack_frame, parse_frame
from test_lab.audio_pipeline.tests.test_server import Harness, wait_for

FS = 16000


def speechlike(n, rng, amp):
    """Broadband noise band-limited into the speech range, with a slow envelope."""
    x = np.convolve(rng.standard_normal(n), np.ones(3) / 3, mode="same")
    env = 0.6 + 0.4 * np.sin(np.arange(n) / FS * 2 * np.pi * 3)
    return x * env * amp


def scenario(weak_db=-18.0, noise_lsb=45.0, weak_noise_lsb=None, quiet_s=2.0, sound_s=6.0, seed=0,
             right_signal=True, strong_amp=2500.0):
    """Left is the strong mic, right the weak one (weak_db below it) with its own noise floor."""
    rng = np.random.default_rng(seed)
    nq, ns = int(quiet_s * FS), int(sound_s * FS)
    wn = noise_lsb if weak_noise_lsb is None else weak_noise_lsb
    sig = speechlike(ns, rng, strong_amp)
    ql, qr = rng.standard_normal(nq) * noise_lsb, rng.standard_normal(nq) * wn
    sl = sig + rng.standard_normal(ns) * noise_lsb
    sr = (sig * 10 ** (weak_db / 20) if right_signal else 0 * sig) + rng.standard_normal(ns) * wn
    i16 = lambda a: np.clip(a, -32768, 32767).astype(np.int16)
    return i16(ql), i16(qr), i16(sl), i16(sr)


class MeasureTests(unittest.TestCase):
    def test_recovers_known_imbalance_despite_equal_noise_floors(self):
        for weak_db in (-18.0, -8.0, +6.0):
            r = measure(*scenario(weak_db=weak_db), gate=False)
            self.assertAlmostEqual(r["raw_diff_db"], weak_db, delta=1.0, msg=f"weak {weak_db}")
            t = r["proposed_trim_db"]
            self.assertAlmostEqual(t["right"] - t["left"], -weak_db, delta=1.0)
            self.assertEqual(min(t.values()), 0.0, "only the weak channel is boosted")
            self.assertEqual(r["weak"], "right" if weak_db < 0 else "left")

    def test_noise_cost_is_reported_not_hidden(self):
        r = measure(*scenario(weak_db=-18.0), gate=False)
        n, after = r["noise_dbfs"], r["noise_dbfs_after_proposed"]
        self.assertAlmostEqual(after["left"], n["left"], delta=0.01)
        self.assertAlmostEqual(after["right"], n["right"] + 18.0, delta=1.0)

    def test_residual_with_existing_trims(self):
        r = measure(*scenario(weak_db=-18.0), trim_left_db=0.0, trim_right_db=18.0, gate=False)
        self.assertLess(abs(r["residual_with_current_trims_db"]), 1.0)
        self.assertTrue(r["within_target_now"])
        r = measure(*scenario(weak_db=-18.0), trim_left_db=0.0, trim_right_db=8.0, gate=False)
        self.assertFalse(r["within_target_now"])

    def test_calibration_stable_across_independent_runs(self):
        trims = [measure(*scenario(weak_db=-18.0, seed=s), gate=False)["proposed_trim_db"]["right"] for s in range(5)]
        self.assertLess(max(trims) - min(trims), 1.5)
        trim = statistics.median(trims)
        for s in range(10, 14):
            self.assertLessEqual(abs(measure(*scenario(weak_db=-18.0, seed=s), trim_right_db=trim, gate=False)["residual_with_current_trims_db"]), 2.0)

    def test_refuses_when_weak_mic_hears_nothing(self):
        with self.assertRaisesRegex(CalibrationError, "barely rises|no signal"):
            measure(*scenario(right_signal=False))

    def test_refuses_when_room_not_quiet_in_quiet_phase(self):
        ql, qr, sl, sr = scenario()
        rng = np.random.default_rng(3)
        ql = ql.copy(); ql[FS: FS + 8000] = np.clip(speechlike(8000, rng, 4000), -32768, 32767)
        with self.assertRaisesRegex(CalibrationError, "not steady"):
            measure(ql, qr, sl, sr)

    def test_refuses_too_little_sound(self):
        with self.assertRaisesRegex(CalibrationError, "Not enough"):
            measure(*scenario(sound_s=0.5))

    def test_refuses_unsteady_source(self):
        ql, qr, sl, sr = scenario(weak_db=-18.0)
        sr = sr.copy()
        sr[len(sr) // 2:] = (sr[len(sr) // 2:] * 4).astype(np.int16)
        with self.assertRaisesRegex(CalibrationError, "changed by"):
            measure(ql, qr, sl, sr)

    def test_refuses_faulty_beyond_limit(self):
        with self.assertRaisesRegex(CalibrationError, "limit|barely"):
            measure(*scenario(weak_db=-50.0, noise_lsb=5.0))


class RestBalanceTests(unittest.TestCase):
    """The failure seen on the real device: a trim balanced the sound but the boosted noise ruined the rest levels."""

    def test_trim_alone_leaves_rest_unbalanced_and_gate_fixes_it(self):
        data = scenario(weak_db=-18.0)
        ungated = measure(*data, gate=False)
        self.assertTrue(abs(ungated["after"]["rest_diff_db"]) > 10, "boosted noise reads far louder at rest")
        self.assertFalse(ungated["after"]["balanced"])
        gated = measure(*data, gate=True)
        self.assertLessEqual(abs(gated["after"]["rest_diff_db"]), 2.0, gated["after"])
        self.assertLessEqual(abs(gated["after"]["sound_diff_db"]), 2.0, gated["after"])
        self.assertTrue(gated["after"]["balanced"])
        self.assertGreater(gated["gate_reduction_db"]["right"], 10)
        self.assertEqual(gated["gate_reduction_db"]["left"], 0.0)

    def test_matches_what_the_live_path_produces_on_fresh_audio(self):
        """Calibrate on one capture, then check a different capture through the same conditioner."""
        r = measure(*scenario(weak_db=-18.0, seed=0))
        cond = Conditioner(r["proposed_trim_db"]["left"], r["proposed_trim_db"]["right"], True, r["noise_rms_raw"])
        for seed in (5, 6, 7):
            ql, qr, sl, sr = scenario(weak_db=-18.0, seed=seed)
            active = np.ones(len(sl) // 1600, dtype=bool)
            e = evaluate(cond, ql, qr, sl, sr, active)
            self.assertLessEqual(abs(e["rest_diff_db"]), 2.0, e)
            self.assertLessEqual(abs(e["sound_diff_db"]), 2.5, e)

    def test_very_weak_noisy_mic_is_warned_about(self):
        """Weak mic only ~4 dB above its own noise, floors far apart (like the real fault): must warn, and report the gap honestly."""
        data = scenario(weak_db=-37.0, noise_lsb=200.0, weak_noise_lsb=45.0, strong_amp=9000.0)
        r = measure(*data)
        self.assertTrue(any("mostly" in w and "noise" in w for w in r["warnings"]), r["warnings"])
        self.assertTrue(any("far beyond a healthy" in w for w in r["warnings"]))
        # at rest the gate must still bring the boosted channel down to the other channel's floor
        self.assertLessEqual(abs(r["after"]["rest_diff_db"]), 2.0, r["after"])
        # and the un-gated result would have been badly unbalanced at rest, as the user saw
        self.assertGreater(abs(measure(*data, gate=False)["after"]["rest_diff_db"]), 10)

    def test_gate_state_is_continuous_across_packet_sizes(self):
        rng = np.random.default_rng(1)
        x = (rng.standard_normal(FS * 2) * 45).astype(np.int16)
        c = Conditioner(0.0, 30.0, True, {"left": 45.0, "right": 45.0})
        outs = [process_full(c.copy_with(), "right", x, chunk=n) for n in (32, 256, 1024)]
        levels = [20 * np.log10(np.sqrt(np.mean(o[FS // 2:].astype(float) ** 2)) / 32768) for o in outs]
        self.assertLess(max(levels) - min(levels), 1.5, levels)


class ConditionerTests(unittest.TestCase):
    def frame(self, l, r):
        return parse_frame(pack_frame(MASK_BOTH, 0, 0, np.array(l, np.int16), np.array(r, np.int16)))

    def test_zero_trim_is_bit_exact_and_boost_clips(self):
        f = self.frame([100, -200, 300], [5, 6, 7])
        same = Conditioner().apply(f)
        np.testing.assert_array_equal(same.left, f.left)
        np.testing.assert_array_equal(same.right, f.right)
        t = Conditioner(gate_enabled=False); t.set_trims(0, 20)
        out = t.apply(self.frame([100, 0, 0], [5, 3000, -3000]))
        self.assertEqual(out.right.tolist(), [50, 30000, -30000])
        out = t.apply(self.frame([0, 0, 0], [5000, -5000, 0]))
        self.assertEqual(out.right.tolist(), [32767, -32768, 0])

    def test_gate_closes_on_noise_and_opens_on_signal(self):
        rng = np.random.default_rng(2)
        noise_rms = 45.0
        c = Conditioner(0.0, 30.0, True, {"left": 45.0, "right": noise_rms})
        red = c.reductions()["right"]
        self.assertAlmostEqual(red, 30.0, delta=0.5)                      # left floor equal, so the full boost is gated at rest
        quiet = process_full(c.copy_with(), "right", (rng.standard_normal(FS) * noise_rms).astype(np.int16))
        rest_db = 20 * np.log10(np.sqrt(np.mean(quiet[FS // 2:].astype(float) ** 2)) / 32768)
        target_db = 20 * np.log10(noise_rms / 32768)                       # the other channel's floor
        self.assertAlmostEqual(rest_db, target_db, delta=2.0)
        loud = (rng.standard_normal(FS) * 600).astype(np.int16)            # +22 dB over the floor
        out = process_full(c.copy_with(), "right", loud)
        gain_db = 20 * np.log10(np.sqrt(np.mean(out[FS // 2:].astype(float) ** 2)) / np.sqrt(np.mean(loud[FS // 2:].astype(float) ** 2)))
        self.assertAlmostEqual(gain_db, 30.0, delta=1.0)                   # fully open: the calibrated trim

    def test_limits_and_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cal.json"
            t = Conditioner(path=path)
            t.set_trims(0, 17.5)
            t.set_noise({"left": 40.0, "right": 30.0})
            t.set_gate(False)
            back = Conditioner.load(path)
            self.assertEqual((back.right_db, back.gate_enabled, back.noise_rms["right"]), (17.5, False, 30.0))
            for bad in ((0, 41), (-21, 0)):
                with self.assertRaises(CalibrationError):
                    t.set_trims(*bad)
            self.assertEqual(Conditioner.load(path).right_db, 17.5, "a rejected value must not be saved")
            path.write_text('{"left_db": 3.0, "right_db": 0.0}')           # older file format: trims only
            old = Conditioner.load(path)
            self.assertEqual((old.left_db, old.noise_rms, old.reductions()), (3.0, None, {"left": 0.0, "right": 0.0}))
            path.write_text("{ corrupt")
            self.assertEqual(Conditioner.load(path).as_dict()["left_db"], 0.0)

    def test_gate_reduction_follows_manual_trim(self):
        c = Conditioner(0.0, 0.0, True, {"left": 45.0, "right": 45.0})
        self.assertEqual(c.reductions(), {"left": 0.0, "right": 0.0})
        c.set_trims(0.0, 12.0)
        self.assertAlmostEqual(c.reductions()["right"], 12.0, delta=0.05)
        c.set_gate(False)
        self.assertEqual(c.reductions(), {"left": 0.0, "right": 0.0})


class CalibrationServerTests(unittest.IsolatedAsyncioTestCase):
    async def run_calibration(self, h, ui, dev, apply=True):
        """Quiet phase, then sound phase, driven through the real UI socket."""
        dev.level = 0.0
        await ui.send_json({"type": "calibrate", "apply": apply})
        await wait_for(ui, lambda m: m["type"] == "state" and m["calibration"]["phase"] == "quiet")
        await asyncio.sleep(1.55)                      # stay silent for the whole quiet phase (1.4 s)
        dev.level = 0.15
        st = await wait_for(ui, lambda m: m["type"] == "state" and m["calibration"]["phase"] in ("done", "failed"), timeout=15)
        return st["calibration"]

    async def meter_diff(self, ui, n=20):
        diffs = []
        for _ in range(n):
            lv = (await wait_for(ui, lambda m: m["type"] == "levels" and m["levels"]["both"]))["levels"]
            diffs.append(lv["right"]["rms"] - lv["left"]["rms"])
        return statistics.median(diffs)

    async def test_full_calibration_balances_meters_at_rest_and_in_sound(self):
        tmp = tempfile.TemporaryDirectory()
        cal_file = Path(tmp.name) / "cal.json"
        async with Harness(calib_file=cal_file, calib=(1.4, 2.5)) as h:
            dev, task = h.device(right_sens_db=-18.0, noise=0.0015)
            ui = await h.ui()
            try:
                await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["mic"] == "both")
                dev.level = 0.15
                await asyncio.sleep(0.5)
                self.assertLess(await self.meter_diff(ui, 8), -12, "before: right meter far below left")

                cal = await self.run_calibration(h, ui, dev)
                self.assertEqual(cal["phase"], "done", cal.get("message"))
                res, s = cal["result"], cal["settings"]
                self.assertTrue(res["applied"])
                self.assertAlmostEqual(s["right_db"], 18.0, delta=2.5)
                self.assertEqual(s["left_db"], 0.0)
                self.assertGreater(s["gate_reduction_db"]["right"], 10)
                self.assertTrue(res["after"]["balanced"], res["after"])
                saved = json.loads(cal_file.read_text())
                self.assertEqual(saved["right_db"], s["right_db"])
                self.assertIsNotNone(saved["noise_rms"], "noise floors persisted so the rest balance survives a restart")

                # live meters while sound plays
                await asyncio.sleep(0.6)
                self.assertLessEqual(abs(await self.meter_diff(ui)), 2.0, "during sound")
                # live meters AT REST (the case that failed on the device)
                dev.level = 0.0
                await asyncio.sleep(1.2)
                self.assertLessEqual(abs(await self.meter_diff(ui)), 2.0, "at rest")

                # an independent measure-only run reports both, and changes nothing
                before = dict(cal["settings"])
                dev.level = 0.0
                cal2 = await self.run_calibration(h, ui, dev, apply=False)
                self.assertEqual(cal2["phase"], "done", cal2.get("message"))
                b = cal2["result"]["before"]
                self.assertLessEqual(abs(b["rest_diff_db"]), 2.0, b)
                self.assertLessEqual(abs(b["sound_diff_db"]), 2.0, b)
                self.assertTrue(b["balanced"])
                self.assertEqual(cal2["settings"], before)

                # the raw capture of the run is kept for inspection
                self.assertTrue((Path(tmp.name) / "calibration_last_run.wav").is_file())

                # recordings carry the conditioner settings
                dev.level = 0.15
                await ui.send_json({"type": "rec_start"})
                await wait_for(ui, lambda m: m["type"] == "state" and m["recording"]["seconds"] > 0.6)
                await ui.send_json({"type": "rec_stop"})
                meta = (await wait_for(ui, lambda m: m["type"] == "recordings" and m["items"]))["items"][0]
                self.assertEqual(meta["conditioner"]["right_db"], before["right_db"])
            finally:
                dev.stop.set(); await task; await ui.close()
        tmp.cleanup()

    async def test_gate_toggle_and_manual_trim(self):
        async with Harness(calib=(1.4, 2.5)) as h:
            dev, task = h.device()
            ui = await h.ui()
            try:
                await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["streaming"])
                await ui.send_json({"type": "set_gate", "enabled": False})
                st = await wait_for(ui, lambda m: m["type"] == "state" and m["calibration"]["settings"]["gate_enabled"] is False)
                self.assertEqual(st["calibration"]["settings"]["gate_reduction_db"], {"left": 0.0, "right": 0.0})
                await ui.send_json({"type": "set_trim", "left_db": 0, "right_db": 6.5})
                await wait_for(ui, lambda m: m["type"] == "state" and m["calibration"]["settings"]["right_db"] == 6.5)
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_failed_calibration_changes_nothing(self):
        async with Harness(calib=(1.4, 1.6)) as h:
            dev, task = h.device(right_sens_db=-18.0, noise=0.0015)
            ui = await h.ui()
            try:
                await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["mic"] == "both")
                dev.level = 0.0
                await ui.send_json({"type": "calibrate", "apply": True})
                st = await wait_for(ui, lambda m: m["type"] == "state" and m["calibration"]["phase"] in ("done", "failed"), timeout=12)
                self.assertEqual(st["calibration"]["phase"], "failed")
                self.assertIn("Not enough sound", st["calibration"]["message"])
                s = st["calibration"]["settings"]
                self.assertEqual((s["left_db"], s["right_db"]), (0.0, 0.0))
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_guards(self):
        async with Harness(calib=(0.5, 1.0)) as h:
            ui = await h.ui()
            await ui.send_json({"type": "calibrate"})
            self.assertIn("No audio", (await wait_for(ui, lambda m: m["type"] == "notice"))["text"])
            dev, task = h.device()
            try:
                await ui.send_json({"type": "set_config", "config": {"mic": "left"}})
                await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["mic"] == "left")
                await ui.send_json({"type": "calibrate"})
                self.assertIn("Select 'Both'", (await wait_for(ui, lambda m: m["type"] == "notice"))["text"])
                await ui.send_json({"type": "set_config", "config": {"mic": "both"}})
                await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["mic"] == "both")
                await ui.send_json({"type": "calibrate"})
                await wait_for(ui, lambda m: m["type"] == "state" and m["calibration"]["phase"] == "quiet")
                await ui.send_json({"type": "set_config", "config": {"chunk": 64}})
                self.assertIn("calibration is running", (await wait_for(ui, lambda m: m["type"] == "notice"))["text"])
                await ui.send_json({"type": "set_trim", "left_db": 0, "right_db": 5})
                self.assertIn("calibration is running", (await wait_for(ui, lambda m: m["type"] == "notice"))["text"])
                await ui.send_json({"type": "calibrate_cancel"})
                await ui.send_json({"type": "set_trim", "left_db": 0, "right_db": 99})
                self.assertIn("between", (await wait_for(ui, lambda m: m["type"] == "notice"))["text"])
            finally:
                dev.stop.set(); await task; await ui.close()


if __name__ == "__main__":
    unittest.main()
