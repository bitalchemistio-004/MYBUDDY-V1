"""
End-to-end tests of the server against the simulated device.

These cover the server, protocol and recorder. They do NOT cover the
microphones, I2S or the firmware — the simulator is software.
"""
import asyncio
import json
import tempfile
import unittest
import wave
from pathlib import Path

import aiohttp
import numpy as np
from aiohttp import web

from test_lab.audio_pipeline import stt_branch
from test_lab.audio_pipeline.server import make_app
from test_lab.audio_pipeline.tests.fake_device import FakeDevice


class Harness:
    def __init__(self, calib_file=None, calib=(2.0, 6.0)):
        # calib_file=None keeps tests away from the real calibration.json (no loading, no saving)
        self.calib_file, self.calib = calib_file, calib

    async def __aenter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = make_app(start_beacon=False, load_stt=False, rec_dir=Path(self.tmp.name),
                            calibration_file=self.calib_file, calib_quiet_s=self.calib[0], calib_sound_s=self.calib[1])
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.base = f"127.0.0.1:{self.port}"
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc):
        await self.session.close()
        await self.runner.cleanup()
        self.tmp.cleanup()

    def device(self, **kw):
        dev = FakeDevice(f"ws://{self.base}/ws/device", **kw)
        task = asyncio.create_task(dev.run())
        return dev, task

    async def ui(self):
        return await self.session.ws_connect(f"ws://{self.base}/ws/ui")


async def wait_for(ws, predicate, timeout=6.0):
    """Read UI messages until predicate(msg) is truthy; return that msg."""
    async def loop():
        async for m in ws:
            data = json.loads(m.data)
            if predicate(data):
                return data
    return await asyncio.wait_for(loop(), timeout)


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_measured_chunk_follows_config_change(self):
        async with Harness() as h:
            dev, task = h.device()
            ui = await h.ui()
            try:
                st = await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["streaming"] and m["applied"])
                self.assertEqual(st["wire"]["chunk_frames"], 256)
                self.assertEqual(st["applied"]["chunk"], 256)

                await ui.send_json({"type": "set_config", "config": {"chunk": 512}})
                st = await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["chunk_frames"] == 512
                                    and m["applied"] and m["applied"]["chunk"] == 512)
                self.assertEqual(st["wire"]["packet_bytes"], 12 + 512 * 2 * 2)
                self.assertEqual(st["requested"]["chunk"], 512)
                self.assertEqual(st["wire"]["dropped"], 0)
                self.assertTrue(any("chunk=512" in c for c in dev.received_cfg))
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_invalid_config_rejected_before_reaching_device(self):
        async with Harness() as h:
            dev, task = h.device()
            ui = await h.ui()
            try:
                await wait_for(ui, lambda m: m["type"] == "state" and m["applied"])
                n = len(dev.received_cfg)
                await ui.send_json({"type": "set_config", "config": {"chunk": 100}})
                note = await wait_for(ui, lambda m: m["type"] == "notice")
                self.assertIn("multiple of 32", note["text"])
                await asyncio.sleep(0.2)
                self.assertEqual(len(dev.received_cfg), n)
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_mic_selection_changes_channels_on_wire(self):
        async with Harness() as h:
            dev, task = h.device()
            ui = await h.ui()
            try:
                await ui.send_json({"type": "set_config", "config": {"mic": "left"}})
                await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["mic"] == "left")
                lv = await wait_for(ui, lambda m: m["type"] == "levels" and m["levels"]["right"] is None
                                    and m["levels"]["left"] is not None)
                self.assertIsNone(lv["levels"]["both"])
                self.assertIsNone(lv["direction"], "no direction with a single microphone")

                await ui.send_json({"type": "set_config", "config": {"mic": "both"}})
                lv = await wait_for(ui, lambda m: m["type"] == "levels" and m["levels"]["both"] is not None)
                self.assertIsNotNone(lv["levels"]["left"])
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_direction_sign_left_and_right(self):
        for azimuth, sign in ((-40.0, +1), (40.0, -1)):     # source on the left => right mic delayed => lag > 0
            async with Harness() as h:
                dev, task = h.device(azimuth_deg=azimuth)
                ui = await h.ui()
                try:
                    msg = await wait_for(ui, lambda m: m["type"] == "levels" and m["direction"]
                                         and m["direction"]["lag_samples"] is not None and abs(m["direction"]["lag_samples"]) > 0.5,
                                         timeout=8)
                    lag = msg["direction"]["lag_samples"]
                    self.assertEqual(np.sign(lag), sign, f"azimuth {azimuth}: lag {lag}")
                    self.assertGreater(msg["direction"]["corr"], 0.8)
                finally:
                    dev.stop.set(); await task; await ui.close()

    async def test_recording_writes_valid_wav_of_received_audio(self):
        for mic, channels in (("both", 2), ("right", 1)):
            async with Harness() as h:
                dev, task = h.device()
                ui = await h.ui()
                try:
                    await ui.send_json({"type": "set_config", "config": {"mic": mic, "chunk": 128}})
                    await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["mic"] == mic and m["wire"]["chunk_frames"] == 128)
                    await ui.send_json({"type": "rec_start"})
                    await wait_for(ui, lambda m: m["type"] == "state" and m["recording"]["seconds"] > 0.5)
                    # config is locked while recording
                    await ui.send_json({"type": "set_config", "config": {"chunk": 64}})
                    note = await wait_for(ui, lambda m: m["type"] == "notice")
                    self.assertIn("Stop the recording", note["text"])
                    await ui.send_json({"type": "rec_stop"})
                    recs = await wait_for(ui, lambda m: m["type"] == "recordings" and m["items"])
                    meta = recs["items"][0]
                    self.assertEqual((meta["channels"], meta["mic"], meta["chunk_frames"]), (channels, mic, 128))

                    with wave.open(str(Path(h.tmp.name) / meta["name"])) as w:
                        self.assertEqual((w.getnchannels(), w.getframerate(), w.getsampwidth()), (channels, 16000, 2))
                        self.assertGreater(w.getnframes() / 16000, 0.5)
                        self.assertEqual(w.getnframes(), meta["frames"])
                        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
                    self.assertGreater(np.abs(pcm).max(), 100, "recording is not silent")

                    async with h.session.get(f"http://{h.base}/recordings/{meta['name']}") as r:
                        self.assertEqual(r.status, 200)
                        self.assertEqual(r.headers["Content-Type"], "audio/wav")
                finally:
                    dev.stop.set(); await task; await ui.close()

    async def test_record_refused_without_device(self):
        async with Harness() as h:
            ui = await h.ui()
            await ui.send_json({"type": "rec_start"})
            note = await wait_for(ui, lambda m: m["type"] == "notice")
            self.assertIn("No device", note["text"])
            await ui.close()

    async def test_path_traversal_and_unknown_recording(self):
        async with Harness() as h:
            for name in ("..%2F..%2Fserver.py", "nope.wav", "a.txt"):
                async with h.session.get(f"http://{h.base}/recordings/{name}") as r:
                    self.assertEqual(r.status, 404, name)
            async with h.session.post(f"http://{h.base}/api/transcribe", json={"name": "../x.wav"}) as r:
                self.assertEqual(r.status, 404)

    async def test_cross_origin_requests_refused(self):
        async with Harness() as h:
            async with h.session.get(f"http://{h.base}/", headers={"Origin": "http://evil.example"}) as r:
                self.assertEqual(r.status, 403)
            with self.assertRaises(aiohttp.WSServerHandshakeError):
                await h.session.ws_connect(f"ws://{h.base}/ws/ui", headers={"Origin": "http://evil.example"})
            async with h.session.get(f"http://{h.base}/") as r:          # same-origin/no-origin is fine
                self.assertEqual(r.status, 200)

    async def test_malformed_packets_are_counted_not_recorded(self):
        async with Harness() as h:
            ui = await h.ui()
            async with h.session.ws_connect(f"ws://{h.base}/ws/device") as dev:
                await dev.send_bytes(b"garbage-not-a-packet")
                st = await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["bad"] == 1)
                self.assertEqual(st["wire"]["packets"], 0)
                self.assertIn("bad packet", st["error"])
            await ui.close()

    async def test_sequence_gap_reported_as_loss(self):
        from test_lab.audio_pipeline.protocol import MASK_BOTH, pack_frame
        async with Harness() as h:
            ui = await h.ui()
            z = np.zeros(64, np.int16)
            async with h.session.ws_connect(f"ws://{h.base}/ws/device") as dev:
                for seq in (0, 1, 2, 6, 7):                           # 3,4,5 lost
                    await dev.send_bytes(pack_frame(MASK_BOTH, seq, 0, z, z))
                st = await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["packets"] == 5)
                self.assertEqual(st["wire"]["dropped"], 3)
            await ui.close()


class SttBranchTests(unittest.TestCase):
    def test_pick_source(self):
        pcm = np.array([[100, -300], [-200, 50]], dtype=np.int16)
        f = lambda s: (stt_branch.pick_source(pcm, s)[0] * 32768).round().astype(int).tolist()
        self.assertEqual(f("left"), [100, -200])
        self.assertEqual(f("right"), [-300, 50])
        self.assertEqual(f("mix"), [-100, -75])
        self.assertEqual(f("loudest"), [-300, -200])
        self.assertEqual(stt_branch.pick_source(pcm[:, :1], "right")[1], "mono (single microphone)")

    def test_rejects_bad_options(self):
        with self.assertRaises(ValueError):
            asyncio.run(stt_branch.transcribe(Path("x.wav"), source="bogus"))
        with self.assertRaises(ValueError):
            asyncio.run(stt_branch.transcribe(Path("x.wav"), language="fr"))     # Arabic and English only


if __name__ == "__main__":
    unittest.main()
