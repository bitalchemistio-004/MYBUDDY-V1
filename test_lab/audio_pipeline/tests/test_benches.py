import asyncio
import unittest

from test_lab.audio_pipeline.net_bench import summarise
from test_lab.audio_pipeline.server import LAB
from test_lab.audio_pipeline.tests.test_server import Harness, wait_for


def bench_state(name, phase_in=("done", "failed", "cancelled")):
    return lambda m: m["type"] == "bench" and m["name"] == name and m["state"]["phase"] in phase_in


class NetSummaryTests(unittest.TestCase):
    def test_summary_numbers(self):
        s = summarise([16.0] * 95 + [16.0, 60.0, 16.0, 16.0, 200.0], 16.0)
        self.assertEqual(s["max_gap_ms"], 200.0)
        self.assertGreater(s["jitter_ms"], 0)
        self.assertEqual(s["late_pct"], 2.0)        # 60 and 200 ms exceed 2x expected + 5 ms

    def test_too_few_packets_is_not_a_number(self):
        self.assertEqual(summarise([16.0, 16.0], 16.0)["jitter_ms"], None)


class NetBenchTests(unittest.IsolatedAsyncioTestCase):
    async def run_net(self, h, ui, chunks, seconds=3):
        await ui.send_json({"type": "bench", "name": "net", "action": "run", "params": {"chunks": chunks, "seconds": seconds}})
        return (await wait_for(ui, bench_state("net"), timeout=40))["state"]

    async def test_measures_each_chunk_size_and_restores_the_original(self):
        async with Harness() as h:
            dev, task = h.device()
            ui = await h.ui()
            try:
                await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["streaming"])
                st = await self.run_net(h, ui, [128, 512])
                self.assertEqual(st["phase"], "done", st["message"])
                rows = st["tables"][0]["rows"]
                self.assertEqual([r[0] for r in rows], [128, 512])
                for r, expected in zip(rows, (125.0, 31.25)):
                    got = float(r[1].split("/")[0])
                    self.assertAlmostEqual(got, expected, delta=expected * 0.2, msg=str(r))
                    self.assertEqual(r[2], "0.00%")
                    self.assertEqual(r[9], "0")
                self.assertEqual(st["verdict"]["level"], "ok")
                await asyncio.sleep(0.5)
                self.assertEqual(h.app[LAB].requested["chunk"], 256, "original chunk size restored")
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_loss_on_a_lossy_link_is_counted_and_flagged(self):
        async with Harness() as h:
            dev, task = h.device(drop_every=10)                       # ~10% of packets never arrive
            ui = await h.ui()
            try:
                await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["streaming"])
                st = await self.run_net(h, ui, [256], seconds=4)
                self.assertEqual(st["phase"], "done", st["message"])
                loss = float(st["tables"][0]["rows"][0][2].rstrip("%"))
                self.assertAlmostEqual(loss, 10.0, delta=3.0)
                self.assertIn([0, 2], st["tables"][0]["bad"])
                self.assertIn(st["verdict"]["level"], ("warn", "bad"))
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_no_device_fails_visibly_not_silently(self):
        async with Harness() as h:
            ui = await h.ui()
            st = await self.run_net(h, ui, [256])
            self.assertEqual(st["phase"], "failed")
            self.assertIn("not streaming", st["message"])
            await ui.close()

    async def test_cancel_stops_the_run_and_restores_chunk_size(self):
        async with Harness() as h:
            dev, task = h.device()
            ui = await h.ui()
            try:
                await wait_for(ui, lambda m: m["type"] == "state" and m["wire"]["streaming"])
                await ui.send_json({"type": "bench", "name": "net", "action": "run", "params": {"chunks": [512, 1024], "seconds": 30}})
                await wait_for(ui, lambda m: m["type"] == "bench" and m["state"]["phase"] == "running")
                await asyncio.sleep(1.0)
                await ui.send_json({"type": "bench", "name": "net", "action": "cancel", "params": {}})
                st = (await wait_for(ui, bench_state("net", ("cancelled",)), timeout=10))["state"]
                self.assertEqual(st["phase"], "cancelled")
                await asyncio.sleep(0.5)
                self.assertEqual(h.app[LAB].requested["chunk"], 256)
            finally:
                dev.stop.set(); await task; await ui.close()

    async def test_new_page_client_receives_current_bench_state(self):
        async with Harness() as h:
            ui = await h.ui()
            m = await wait_for(ui, lambda m: m["type"] == "bench" and m["name"] == "net")
            self.assertEqual(m["state"]["phase"], "idle")
            await ui.close()

    async def test_bench_page_is_served_and_guarded(self):
        async with Harness() as h:
            async with h.session.get(f"http://{h.base}/benches") as r:
                self.assertEqual(r.status, 200)
                self.assertIn("Test benches", await r.text())
            async with h.session.get(f"http://{h.base}/benches", headers={"Origin": "http://evil.example"}) as r:
                self.assertEqual(r.status, 403)


if __name__ == "__main__":
    unittest.main()
