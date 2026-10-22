import asyncio
import unittest

import numpy as np

from test_lab.audio_pipeline import barge_bench
from test_lab.audio_pipeline.server import LAB
from test_lab.audio_pipeline.tests.test_server import Harness, wait_for

ROOM = dict(level=0.0, noise=0.0008)            # a quiet room: no talker, a little electrical noise


def finished(name):
    return lambda m: m["type"] == "bench" and m["name"] == name and m["state"]["phase"] in ("done", "failed")


async def run(h, ui, name, action, params=None, timeout=90):
    await ui.send_json({"type": "bench", "name": name, "action": action, "params": params or {}})
    return (await wait_for(ui, finished(name), timeout=timeout))["state"]


def cell(state, title_part, row_label):
    tbl = next(t for t in state["tables"] if title_part in t["title"])
    return next(r for r in tbl["rows"] if str(r[0]).startswith(row_label))


class SpeakerBenchTests(unittest.IsolatedAsyncioTestCase):
    async def setup(self, h, **kw):
        dev, task = h.device(**{**ROOM, **kw})
        ui = await h.ui()
        await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["streaming"] and m["device"] and "spkok" in m["device"])
        return dev, task, ui

    async def test_audible_speaker_is_detected_with_a_sensible_loop_time(self):
        async with Harness() as h:
            dev, task, ui = await self.setup(h, spk_gain=0.05, spk_delay_ms=60)
            try:
                st = await run(h, ui, "speaker", "probe")
                self.assertEqual(st["phase"], "done", st["message"])
                self.assertEqual(st["verdict"]["level"], "ok", st["verdict"])
                rise = float(cell(st, "audible", "Rise above")[1].split()[0])
                self.assertGreater(rise, 20)
                lat = float(cell(st, "audible", "Time from")[1].split()[0])
                # the simulated board's delay is 60 ms, but its clock/pacing runs on Windows' 15.6 ms timer in the same
                # event loop as the server, so allow tens of ms either way; a real board is far tighter (19-44 ms measured)
                self.assertTrue(15 < lat < 250, lat)
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_silent_speaker_is_reported_as_not_heard(self):
        async with Harness() as h:
            dev, task, ui = await self.setup(h, spk_gain=0.0)
            try:
                st = await run(h, ui, "speaker", "probe")
                self.assertEqual(st["verdict"]["level"], "bad")
                self.assertIn("did not hear the speaker", st["verdict"]["text"])
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_older_firmware_without_a_speaker_gets_a_clear_message(self):
        async with Harness() as h:
            dev, task = h.device(**ROOM, has_speaker=False)
            ui = await h.ui()
            try:
                await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["streaming"])
                await asyncio.sleep(1.4)                         # let a STAT arrive (without speaker fields)
                st = await run(h, ui, "speaker", "probe")
                self.assertEqual(st["phase"], "failed")
                self.assertIn("version 4", st["message"])
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_linear_speaker_passes_level_test_and_limiting_speaker_is_caught(self):
        async with Harness() as h:
            dev, task, ui = await self.setup(h, spk_gain=0.3)
            try:
                st = await run(h, ui, "speaker", "levels", timeout=120)
                self.assertEqual(st["phase"], "done", st["message"])
                rows = next(t for t in st["tables"] if "Level and distortion" in t["title"])["rows"]
                self.assertEqual([r[0] for r in rows], [-40, -30, -20, -12, -6])
                heard = [float(r[1].split()[0]) for r in rows if "dBFS" in r[1]]
                self.assertGreaterEqual(len(heard), 4)
                self.assertTrue(all(b > a for a, b in zip(heard, heard[1:])), heard)
                self.assertEqual(st["verdict"]["level"], "ok", st["verdict"])
            finally:
                dev.stop.set(); await task; await ui.close()
        async with Harness() as h:
            dev, task, ui = await self.setup(h, spk_gain=0.6, spk_ceiling=1500.0)      # hits a limiter well before the loudest tone
            try:
                st = await run(h, ui, "speaker", "levels", timeout=120)
                self.assertIn(st["verdict"]["level"], ("warn", "bad"), st["verdict"])
                text = st["verdict"]["text"]
                self.assertTrue("compress" in text or "distortion" in text or "linear" in text, text)
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_sweep_finds_a_low_pass_and_reports_loop_time(self):
        async with Harness() as h:
            dev, task, ui = await self.setup(h, spk_gain=0.05, spk_lowpass_hz=1500.0)
            try:
                st = await run(h, ui, "speaker", "sweep", timeout=60)
                self.assertEqual(st["phase"], "done", st["message"])
                resp = {float(r[0].split()[0]): float(r[1].split()[0]) for r in next(t for t in st["tables"] if "Frequency response" in t["title"])["rows"]}
                self.assertLess(resp[4000.0], -6.0, resp)
                self.assertLess(resp[6300.0], resp[2000.0] - 3)
                self.assertGreater(resp[250.0], -3.0)
                self.assertTrue(st["charts"] and st["charts"][0]["logx"])
                self.assertTrue(any("Sweep delay from sending" in n for n in st["notes"]))
                self.assertIn(st["verdict"]["level"], ("warn", "bad"), "a 1.5 kHz low-pass does not cover the speech band")
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_no_level_above_minus_6_dbfs_is_ever_sent(self):
        async with Harness() as h:
            dev, task, ui = await self.setup(h, spk_gain=0.05)
            seen = []
            orig = dev._on_speaker_packet

            def spy(data):
                seen.append(np.abs(np.frombuffer(data[6:], dtype="<i2")).max() if len(data) > 6 else 0)
                orig(data)
            dev._on_speaker_packet = spy
            try:
                await run(h, ui, "speaker", "all", timeout=180)
                self.assertTrue(seen)
                self.assertLessEqual(max(seen) / 32768, 10 ** (-6.0 / 20) + 0.01, "nothing is ever played louder than -6 dBFS")
            finally:
                dev.stop.set(); await task; await ui.close()


class DeviceClockTimingTests(unittest.TestCase):
    """The delay estimate must come from the device's capture clock, not from bursty packet arrival."""

    def test_bursty_arrival_does_not_move_the_estimate(self):
        from test_lab.audio_pipeline.bench_hub import Capture
        from test_lab.audio_pipeline.protocol import Frame

        class Lab:
            taps = []
        cap = Capture(Lab())
        rng = np.random.default_rng(0)
        t_send = 100.0
        true_latency = 0.120                                   # the sound is captured 120 ms after being sent
        chunk = 256
        arrival_burst = 0.0
        for k in range(200):
            dev_time = 100.0 - 5.0 + (k + 1) * chunk / 16000            # device clock: unrelated offset (-5 s) from the server's
            server_when_captured = dev_time + 5.0
            # Wi-Fi: packets arrive late, sometimes many at once (a burst releases held-back packets)
            if k % 25 == 0:
                arrival_burst = rng.uniform(0.15, 0.4)
            arrival = server_when_captured + 0.004 + arrival_burst * (1 - (k % 25) / 25.0)
            fr = Frame(3, k, chunk, int(round(dev_time * 1000)) & 0xFFFFFFFF, np.zeros(chunk, np.int16), None)
            cap._on(fr, arrival)
        idx = int((t_send + true_latency - (100.0 - 5.0 + 5.0)) * 16000)          # sample captured true_latency after t_send
        est = cap.latency_ms(idx, t_send)
        self.assertAlmostEqual(est, true_latency * 1000, delta=15, msg=f"got {est}")
        # what arrival times alone would claim:
        naive = (cap.arrivals[int(idx // chunk)][0] - t_send) * 1000
        self.assertGreater(abs(naive - true_latency * 1000), 40, "arrival-based timing really is worse here")


def fake_speech(seconds=3.0, rate=22050):
    """Speech-like signal: syllable-rate modulated band-limited noise."""
    rng = np.random.default_rng(0)
    n = int(seconds * rate)
    x = np.convolve(rng.standard_normal(n), np.ones(4) / 4, mode="same")
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * np.arange(n) / rate) ** 2
    return np.round(x * env / np.abs(x * env).max() * 30000).astype(np.int16)


class EchoBenchTests(unittest.IsolatedAsyncioTestCase):
    async def setup(self, h, **kw):
        bench = h.app[LAB].benches["echo"]

        async def clip():
            return fake_speech()
        bench._clip = clip
        dev, task = h.device(**{**ROOM, **kw})
        ui = await h.ui()
        await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["streaming"] and m["device"] and "spkok" in m["device"])
        return dev, task, ui

    async def test_quiet_echo_is_safe(self):
        async with Harness() as h:
            dev, task, ui = await self.setup(h, spk_gain=0.05)
            try:
                st = await run(h, ui, "echo", "scan", {"volumes": "-12,-6", "repeats": 1}, timeout=90)
                self.assertEqual(st["phase"], "done", st["message"])
                rows = st["tables"][0]["rows"]
                self.assertEqual([r[0] for r in rows], ["-12", "-6"])
                self.assertTrue(all(r[7].startswith("OK") and r[6] == 0 for r in rows), rows)
                self.assertEqual(st["verdict"]["level"], "ok")
                # the louder level must show a bigger echo peak. In a quiet room production's noise floor adapts down
                # from its starting 500, so the threshold sits at its 5200 minimum.
                self.assertGreater(int(rows[1][1].split()[0]), int(rows[0][1].split()[0]))
                self.assertEqual(int(rows[0][4]), 5200)
                self.assertLess(int(rows[0][3]), 250)
                self.assertTrue(any("12×" in str(r[1]) for r in next(t for t in st["tables"] if "constants" in t["title"])["rows"]))
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_loud_echo_would_interrupt_the_robot(self):
        async with Harness() as h:
            dev, task, ui = await self.setup(h, spk_gain=1.2)              # a very loud/leaky speaker path
            try:
                st = await run(h, ui, "echo", "scan", {"volumes": "-12", "repeats": 1}, timeout=60)
                row = st["tables"][0]["rows"][0]
                self.assertGreater(row[6], 0, row)
                self.assertEqual(st["verdict"]["level"], "bad")
                self.assertIn([0, 7], st["tables"][0]["bad"])
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_room_thumps_louder_than_the_echo_are_reported_as_hiding_it(self):
        async with Harness() as h:
            dev, task, ui = await self.setup(h, spk_gain=0.05)          # echo peak of a few hundred counts at -6 dBFS
            stop = asyncio.Event()

            async def thumps():                                        # low-frequency room sounds: big peaks, little energy in the speech band
                while not stop.is_set():
                    dev.thump_amp = 0.12
                    await asyncio.sleep(0.2)
                    dev.thump_amp = 0.0
                    await asyncio.sleep(0.5)
            thump_task = asyncio.ensure_future(thumps())
            try:
                st = await run(h, ui, "echo", "scan", {"volumes": "-6", "repeats": 1}, timeout=60)
                row = st["tables"][0]["rows"][0]
                self.assertIn("hidden in room noise", row[7], row)
                self.assertTrue(row[1].startswith("≤"), row[1])
                self.assertEqual(st["charts"], [], "a hidden echo is not plotted as if it were measured")
            finally:
                stop.set(); await thump_task
                dev.stop.set(); await task; await ui.close()

    async def test_constant_loud_noise_means_the_speaker_cannot_be_told_apart(self):
        async with Harness() as h:
            dev, task, ui = await self.setup(h, spk_gain=0.02)
            dev.voice_amp = 0.02                                       # constant noise far louder than the faint echo
            try:
                st = await run(h, ui, "echo", "scan", {"volumes": "-6", "repeats": 1}, timeout=60)
                row = st["tables"][0]["rows"][0]
                self.assertEqual(row[1], "not heard")
                self.assertIn("lost in room noise", row[7])
                self.assertEqual(st["verdict"]["level"], "bad")
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_levels_are_capped_at_minus_6_and_bad_input_is_refused(self):
        self.assertEqual(barge_bench.parse_volumes("-3, 0, -20"), [-20.0, -6.0])
        with self.assertRaises(Exception):
            barge_bench.parse_volumes("  ")
        async with Harness() as h:
            ui = await h.ui()
            await ui.send_json({"type": "bench", "name": "echo", "action": "scan", "params": {"volumes": "loud"}})
            self.assertTrue((await wait_for(ui, lambda m: m["type"] == "notice"))["text"])
            await ui.close()

    async def test_interruption_is_detected_when_a_person_talks_loudly_over_the_robot(self):
        async with Harness() as h:
            dev, task, ui = await self.setup(h, spk_gain=0.05)

            async def talker():
                # do what the page asks: talk while told to
                async for m in ui:
                    import json
                    d = json.loads(m.data)
                    if d.get("type") == "bench" and d["name"] == "echo":
                        p = d["state"].get("prompt")
                        dev.voice_amp = 0.45 if p and p["level"] == "go" else 0.0
                        if d["state"]["phase"] in ("done", "failed"):
                            return d["state"]
            try:
                await ui.send_json({"type": "bench", "name": "echo", "action": "interrupt", "params": {"volume_db": -12}})
                st = await asyncio.wait_for(talker(), 60)
                self.assertEqual(st["phase"], "done", st["message"])
                self.assertEqual(st["verdict"]["level"], "ok", st["verdict"])
                rows = {r[0]: r[1] for r in st["tables"][0]["rows"]}
                self.assertTrue(rows["Production would have interrupted"].startswith("yes"))
                self.assertEqual(rows["Interrupted itself before you spoke"], "no")
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_silence_from_the_person_is_reported_as_no_interruption(self):
        async with Harness() as h:
            dev, task, ui = await self.setup(h, spk_gain=0.05)
            try:
                st = await run(h, ui, "echo", "interrupt", {"volume_db": -12}, timeout=60)
                self.assertEqual(st["verdict"]["level"], "bad")
                self.assertIn("would NOT have stopped", st["verdict"]["text"])
            finally:
                dev.stop.set(); await task; await ui.close()


if __name__ == "__main__":
    unittest.main()
