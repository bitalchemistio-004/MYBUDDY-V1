import asyncio
import unittest

import numpy as np

from test_lab.audio_pipeline.diag import Assembler, DiagError, analyze
from test_lab.audio_pipeline.server import LAB
from test_lab.audio_pipeline.tests.test_server import Harness, wait_for

N = 4096


def healthy(rng, rms24=300.0, n=N):
    """What a working INMP441 sends: small 24-bit values, left-justified, low byte 0."""
    v = np.round(rng.standard_normal(n) * rms24 + 900).astype(np.int64)     # some DC offset like a real mic
    return (v << 8).astype(np.int32)


def pair(a, b):
    return np.stack([a, b], axis=1).astype(np.int32)


def chunks_for(words, count=8):
    """Build the 'DG' chunks the firmware would send."""
    per = len(words) // count
    out = []
    for i in range(count):
        part = np.ascontiguousarray(words[i * per:(i + 1) * per].astype("<i4"))
        out.append(b"DG" + bytes([1, i, count]) + per.to_bytes(2, "little") + b"\x00" + part.tobytes())
    return out


def levels(report):
    return [v["level"] for v in report["verdicts"]]


class AnalyzeTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(3)

    def test_two_healthy_slots_are_reported_healthy(self):
        r = analyze(pair(healthy(self.rng), healthy(self.rng)))
        for s in r["slots"]:
            self.assertEqual((s["big_pct"], s["low_active_pct"], s["stuck"]), (0.0, 0.0, False))
        self.assertEqual(levels(r), ["ok"])

    def test_garbage_in_one_slot_is_pinned_to_that_slot(self):
        garbage = self.rng.integers(-2 ** 31, 2 ** 31, N, dtype=np.int64).astype(np.int32)       # random 32-bit words
        r = analyze(pair(healthy(self.rng), garbage))
        self.assertGreater(r["slots"][1]["big_pct"], 80)
        self.assertLess(r["slots"][0]["big_pct"], 1)
        text = " ".join(v["text"] for v in r["verdicts"])
        self.assertIn("Slot 1", text)
        self.assertNotIn("Slot 0:", text)
        self.assertIn("bad", levels(r))

    def test_bit_shifted_slot_shows_as_low_byte_activity(self):
        good = healthy(self.rng)
        shifted = (healthy(self.rng).astype(np.int64) >> 3).astype(np.int32)          # data arrives 3 bit-clocks late
        r = analyze(pair(good, shifted))
        self.assertGreater(r["slots"][1]["low_active_pct"], 50)
        self.assertEqual(r["slots"][0]["low_active_pct"], 0.0)
        self.assertTrue(any("shifted" in v["text"] for v in r["verdicts"]))

    def test_stuck_slot(self):
        r = analyze(pair(healthy(self.rng), np.zeros(N, dtype=np.int32)))
        self.assertTrue(r["slots"][1]["stuck"])
        self.assertTrue(any("never changes" in v["text"] for v in r["verdicts"]))

    def test_slot_that_mirrors_its_neighbour_is_flagged_as_a_weak_copy(self):
        base = self.rng.standard_normal(N) * 2000
        loud = (np.round(base).astype(np.int64) << 8).astype(np.int32)
        weak = (np.round(base * 0.05 + self.rng.standard_normal(N) * 3).astype(np.int64) << 8).astype(np.int32)
        r = analyze(pair(loud, weak))
        self.assertGreater(r["slot_correlation"], 0.6)
        self.assertTrue(any("weaker copy of slot 0" in v["text"] for v in r["verdicts"]), r["verdicts"])

    def test_large_noise_gap_between_healthy_looking_slots_is_a_warning_not_a_sync_fault(self):
        r = analyze(pair(healthy(self.rng, 200.0), healthy(self.rng, 6000.0)))
        self.assertEqual(levels(r), ["warn"])
        self.assertIn("larger", r["verdicts"][0]["text"])

    def test_bit7_repeating_bit8_is_normal_not_a_shift(self):
        """On the real INMP441 bit 7 mirrors the data LSB (bit 8) and bits 6..0 stay 0."""
        def with_mirrored_bit7(w):
            w = w.astype(np.int64) & 0xFFFFFFFF
            return (w | (((w >> 8) & 1) << 7)).astype(np.uint32).view(np.int32)
        r = analyze(pair(with_mirrored_bit7(healthy(self.rng)), with_mirrored_bit7(healthy(self.rng))))
        self.assertEqual([s["low_active_pct"] for s in r["slots"]], [0.0, 0.0])
        self.assertEqual(levels(r), ["ok"])

    def test_glitch_locked_to_the_i2s_buffer_is_found_on_the_right_slot_only(self):
        left = healthy(self.rng)
        right_v = (healthy(self.rng) >> 8).astype(np.int64)
        right_v[46::256] -= 40000                                  # one big step at the same point of every 256-frame buffer
        right = (right_v << 8).astype(np.int32)
        r = analyze(pair(left, right))
        self.assertGreater(r["slots"][1]["periodic_pct"], 15)
        self.assertEqual(r["slots"][1]["periodic_at"], 46)
        self.assertLess(r["slots"][0]["periodic_pct"], 15)
        warn = [v["text"] for v in r["verdicts"] if v["level"] == "warn" and "I2S buffer" in v["text"]]
        self.assertEqual(len(warn), 1)
        self.assertTrue(warn[0].startswith("Slot 1") and "sample 46" in warn[0], warn)

    def test_random_noise_is_not_reported_as_repeating(self):
        for seed in range(5):
            rng = np.random.default_rng(seed)
            r = analyze(pair(healthy(rng), healthy(rng)))
            self.assertLess(max(s["periodic_pct"] for s in r["slots"]), 15, f"seed {seed}")
            self.assertEqual(levels(r), ["ok"])

    def test_too_short(self):
        with self.assertRaises(DiagError):
            analyze(np.zeros((10, 2), np.int32))


class AssemblerTests(unittest.TestCase):
    def test_reassembles_in_order_even_if_chunks_arrive_shuffled(self):
        rng = np.random.default_rng(1)
        words = pair(healthy(rng), healthy(rng))
        a = Assembler()
        result = None
        for c in reversed(chunks_for(words)):
            got = a.add(c)
            if got is not None:
                result = got
        np.testing.assert_array_equal(result, words)

    def test_incomplete_capture_returns_nothing_and_new_capture_restarts(self):
        rng = np.random.default_rng(2)
        words = pair(healthy(rng), healthy(rng))
        a = Assembler()
        for c in chunks_for(words)[:5]:
            self.assertIsNone(a.add(c))
        again = None
        for c in chunks_for(words, count=4):                 # a different capture layout resets the partial one
            got = a.add(c)
            if got is not None:
                again = got
        np.testing.assert_array_equal(again, words)

    def test_rejects_malformed_chunks(self):
        good = chunks_for(pair(healthy(np.random.default_rng(0)), healthy(np.random.default_rng(1))))[0]
        for bad in (b"", b"XX" + good[2:], good[:-4], good[:2] + bytes([9]) + good[3:]):
            with self.assertRaises(DiagError):
                Assembler().add(bad)


class DiagServerTests(unittest.IsolatedAsyncioTestCase):
    async def capture(self, **device_kw):
        async with Harness() as h:
            dev, task = h.device(**device_kw)
            ui = await h.ui()
            try:
                await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["streaming"])
                await ui.send_json({"type": "diag_capture"})
                st = await wait_for(ui, lambda m: m["type"] == "state" and m["diag"]["phase"] in ("done", "failed"), timeout=8)
                return st["diag"]
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_healthy_device_reports_ok(self):
        d = await self.capture(diag_slot1="healthy")
        self.assertEqual(d["phase"], "done")
        self.assertEqual(d["result"]["frames"], 4096)
        self.assertEqual([v["level"] for v in d["result"]["verdicts"]], ["ok"])

    async def test_garbage_right_slot_is_pinned_on_slot_1(self):
        d = await self.capture(diag_slot1="garbage")
        self.assertEqual(d["phase"], "done")
        bad = [v["text"] for v in d["result"]["verdicts"] if v["level"] == "bad"]
        self.assertTrue(bad and all(t.startswith("Slot 1") for t in bad), bad)

    async def test_stuck_right_slot(self):
        d = await self.capture(diag_slot1="stuck")
        self.assertTrue(any("never changes" in v["text"] for v in d["result"]["verdicts"]))

    async def test_wifi_paused_capture_is_requested_and_compared(self):
        async with Harness() as h:
            dev, task = h.device(wifi_glitch=True)
            ui = await h.ui()
            try:
                await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["streaming"])
                await ui.send_json({"type": "diag_capture", "quiet": False})
                st = await wait_for(ui, lambda m: m["type"] == "state" and m["diag"]["phase"] == "done", timeout=8)
                self.assertGreaterEqual(st["diag"]["result"]["slots"][1]["periodic_pct"], 15)
                await ui.send_json({"type": "diag_capture", "quiet": True})
                st = await wait_for(ui, lambda m: m["type"] == "state" and m["diag"]["phase"] == "done" and m["diag"]["quiet"], timeout=8)
                runs = st["diag"]["runs"]
                self.assertGreaterEqual(runs["normal"]["slots"][1]["periodic_pct"], 15, "normal capture remembered")
                self.assertLess(runs["quiet"]["slots"][1]["periodic_pct"], 15, "glitch gone with Wi-Fi audio paused")
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_old_firmware_that_ignores_the_request_gets_a_clear_message(self):
        async with Harness() as h:
            h.app[LAB].diag_timeout_s = 0.6
            dev, task = h.device(answer_diag=False)
            ui = await h.ui()
            try:
                await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["streaming"])
                await ui.send_json({"type": "diag_capture"})
                st = await wait_for(ui, lambda m: m["type"] == "state" and m["diag"]["phase"] == "failed", timeout=6)
                self.assertIn("newer test firmware", st["diag"]["message"])
                # and a later request still works
                dev.answer_diag = True
                await ui.send_json({"type": "diag_capture"})
                st = await wait_for(ui, lambda m: m["type"] == "state" and m["diag"]["phase"] == "done", timeout=6)
                self.assertEqual(st["diag"]["result"]["frames"], 4096)
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_capture_refused_without_device_and_audio_is_unaffected_by_diag_chunks(self):
        async with Harness() as h:
            ui = await h.ui()
            await ui.send_json({"type": "diag_capture"})
            self.assertIn("No device", (await wait_for(ui, lambda m: m["type"] == "notice"))["text"])
            await ui.close()
        d = await self.capture(diag_slot1="healthy")          # diag chunks must not be counted as malformed audio packets
        self.assertEqual(d["phase"], "done")

    async def test_diag_chunks_do_not_count_as_bad_audio_packets(self):
        async with Harness() as h:
            dev, task = h.device()
            ui = await h.ui()
            try:
                await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["streaming"])
                await ui.send_json({"type": "diag_capture"})
                st = await wait_for(ui, lambda m: m["type"] == "state" and m["diag"]["phase"] == "done", timeout=8)
                await asyncio.sleep(0.5)
                st = await wait_for(ui, lambda m: m["type"] == "state")
                self.assertEqual(st["wire"]["bad"], 0)
            finally:
                dev.stop.set(); await task; await ui.close()


if __name__ == "__main__":
    unittest.main()
