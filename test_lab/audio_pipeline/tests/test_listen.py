import asyncio
import json
import unittest

import aiohttp
import numpy as np

from test_lab.audio_pipeline.listen import Listener
from test_lab.audio_pipeline.tests.test_server import Harness, wait_for


async def collect(ws, seconds):
    """Read /ws/listen for a while; return (hello, [(channels, int16 array), ...])."""
    hello, chunks = None, []
    end = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < end:
        try:
            m = await asyncio.wait_for(ws.receive(), 1.0)
        except asyncio.TimeoutError:
            continue
        if m.type == aiohttp.WSMsgType.TEXT:
            hello = json.loads(m.data)
        elif m.type == aiohttp.WSMsgType.BINARY:
            chunks.append((m.data[0], np.frombuffer(m.data[1:], dtype="<i2")))
    return hello, chunks


class ListenerQueueTests(unittest.TestCase):
    def test_coalesces_same_layout_and_splits_on_change(self):
        q = Listener()
        q.push(2, b"\x01\x00\x02\x00"); q.push(2, b"\x03\x00\x04\x00"); q.push(1, b"\x05\x00")
        out = q.take()
        self.assertEqual(out, [bytes([2]) + b"\x01\x00\x02\x00\x03\x00\x04\x00", bytes([1]) + b"\x05\x00"])
        self.assertEqual(q.take(), [], "taking empties the queue")

    def test_slow_listener_loses_oldest_audio_not_newest(self):
        q = Listener(max_bytes=100)
        for i in range(10):
            q.push(1, bytes([i, 0]) * 20)                  # 40 bytes each
        data = b"".join(m[1:] for m in q.take())
        self.assertLessEqual(len(data), 100)
        self.assertEqual(data[-2:], bytes([9, 0]), "newest audio kept")
        self.assertGreater(q.dropped_bytes, 0)

    def test_single_oversized_push_is_trimmed_on_frame_boundary(self):
        q = Listener(max_bytes=64)
        q.push(2, bytes(range(200)))                       # 200 bytes = 50 stereo frames
        out = q.take()
        self.assertEqual(len(out), 1)
        self.assertEqual((len(out[0]) - 1) % 4, 0, "never splits a stereo frame")
        self.assertLessEqual(len(out[0]) - 1, 64)
        self.assertEqual(out[0][-1], 199)


class ListenServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_stereo_at_real_time_rate_with_no_gaps(self):
        async with Harness() as h:
            dev, task = h.device(azimuth_deg=0.0)
            try:
                await asyncio.sleep(0.5)
                async with h.session.ws_connect(f"ws://{h.base}/ws/listen") as ws:
                    hello, chunks = await collect(ws, 2.0)
                self.assertEqual(hello, {"type": "hello", "rate": 16000})
                self.assertTrue(chunks)
                self.assertTrue(all(ch == 2 for ch, _ in chunks))
                frames = sum(len(a) // 2 for _, a in chunks)
                self.assertAlmostEqual(frames / 16000, 2.0, delta=0.3, msg="about 2 s of audio in 2 s: real-time, no backlog")
                self.assertTrue(all(len(a) % 2 == 0 for _, a in chunks))
                self.assertGreater(np.abs(np.concatenate([a for _, a in chunks])).max(), 500, "real signal, not silence")
            finally:
                dev.stop.set(); await task

    async def test_follows_mic_selection_and_uses_processed_audio(self):
        async with Harness() as h:
            dev, task = h.device()
            ui = await h.ui()
            try:
                await ui.send_json({"type": "set_gate", "enabled": False})
                await ui.send_json({"type": "set_trim", "left_db": 0, "right_db": 12})   # right +12 dB = x3.98
                await wait_for(ui, lambda m: m["type"] == "state" and m["calibration"]["settings"]["right_db"] == 12)
                await asyncio.sleep(0.3)
                async with h.session.ws_connect(f"ws://{h.base}/ws/listen") as ws:
                    _, chunks = await collect(ws, 1.5)
                st = np.concatenate([a for ch, a in chunks if ch == 2]).astype(float).reshape(-1, 2)
                ratio = np.sqrt(np.mean(st[:, 1] ** 2)) / np.sqrt(np.mean(st[:, 0] ** 2))
                self.assertAlmostEqual(20 * np.log10(ratio), 12.0, delta=1.0, msg="listening hears the gain that was applied")

                await ui.send_json({"type": "set_config", "config": {"mic": "left"}})
                await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["mic"] == "left")
                async with h.session.ws_connect(f"ws://{h.base}/ws/listen") as ws:
                    _, chunks = await collect(ws, 1.0)
                self.assertTrue(chunks and all(ch == 1 for ch, _ in chunks), "single mic => mono stream")
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_listener_is_removed_on_disconnect_and_no_device_is_fine(self):
        async with Harness() as h:
            async with h.session.ws_connect(f"ws://{h.base}/ws/listen") as ws:
                hello, chunks = await collect(ws, 0.5)
                self.assertEqual(hello["rate"], 16000)
                self.assertEqual(chunks, [], "nothing to play without a device — and no invented audio")
            await asyncio.sleep(0.2)
            from test_lab.audio_pipeline.server import LAB
            self.assertEqual(len(h.app[LAB].listeners), 0)

    async def test_same_access_guards_as_the_rest_of_the_ui(self):
        async with Harness() as h:
            with self.assertRaises(aiohttp.WSServerHandshakeError):
                await h.session.ws_connect(f"ws://{h.base}/ws/listen", headers={"Origin": "http://evil.example"})


if __name__ == "__main__":
    unittest.main()
