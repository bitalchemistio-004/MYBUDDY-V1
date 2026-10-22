"""
Network bench: how well does the Wi-Fi link carry the audio at each PCM chunk size?

For each chunk size it holds the setting for a while and measures, from the
packets that actually arrive: packets per second against what the chunk size
should give, sequence-number loss, inter-arrival jitter and the longest gap,
the device's own send-queue drops and Wi-Fi signal (from its STAT report), and
whether the link dropped. The chunk size is set on the device and only counted
once its packets show it, so a slow-to-apply change cannot pollute a result.
"""
from __future__ import annotations

import asyncio
import statistics
import time
from collections import deque

import numpy as np

from .bench_hub import BenchBase, BenchError, table
from .protocol import SAMPLE_RATE

DEFAULT_CHUNKS = (128, 256, 512, 1024)


class NetTracker:
    """Always-on inter-arrival statistics for the audio packets."""

    def __init__(self) -> None:
        self._last: float | None = None
        self.gaps: deque[float] = deque(maxlen=20000)      # milliseconds between consecutive packets
        self.total = 0

    def reset_window(self) -> None:
        self.gaps.clear()

    def note(self, now: float) -> None:
        if self._last is not None:
            self.gaps.append((now - self._last) * 1000.0)
        self._last = now
        self.total += 1

    def break_link(self) -> None:
        self._last = None                                   # a reconnect is not a "gap between packets"


def summarise(gaps: list[float], expected_ms: float) -> dict:
    if len(gaps) < 5:
        return {"jitter_ms": None, "p99_gap_ms": None, "max_gap_ms": None, "late_pct": None}
    g = np.asarray(gaps)
    return {"jitter_ms": round(float(np.std(g)), 1), "p99_gap_ms": round(float(np.percentile(g, 99)), 1),
            "max_gap_ms": round(float(g.max()), 1), "late_pct": round(float(np.mean(g > 2 * expected_ms + 5) * 100), 2)}


class NetBench(BenchBase):
    name = "net"

    async def action(self, action: str, params: dict) -> str | None:
        if action != "run":
            return f"net: unknown action {action!r}"
        if self.running:
            return "The network test is already running."
        if self.lab.recorder.active or self.lab.collector.running:
            return "Stop the recording / calibration first."
        chunks = [int(c) for c in params.get("chunks") or DEFAULT_CHUNKS]
        seconds = max(3, min(600, int(params.get("seconds", 20))))
        self.start(self._run(chunks, seconds))
        return None

    async def _apply_chunk(self, chunk: int) -> None:
        err = await self.lab.set_config({"chunk": chunk})
        if err:
            raise BenchError(err)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            snap = self.lab.wire.snapshot(time.monotonic())
            if snap["chunk_frames"] == chunk and self.lab.applied and self.lab.applied["chunk"] == chunk:
                return
            await asyncio.sleep(0.1)
        raise BenchError(f"The device did not switch to {chunk}-sample packets within 10 s (requested {chunk}, measured "
                         f"{self.lab.wire.chunk_frames}).")

    async def _run(self, chunks: list[int], seconds: int) -> None:
        lab = self.lab
        self.require_streaming()
        original = lab.requested["chunk"]
        await self.begin(f"Testing {len(chunks)} chunk sizes, {seconds} s each…")
        rows, bad, notes = [], [], []
        try:
            for n, chunk in enumerate(chunks):
                await self.update(message=f"Chunk {chunk}: switching the device…", progress=n / len(chunks))
                await self._apply_chunk(chunk)
                await asyncio.sleep(2.0)                                        # let the link settle at the new rate
                start_dropped, start_packets = lab.wire.dropped, lab.wire.packets
                start_connects = lab.connect_count
                dev0 = dict(lab.device or {})
                lab.net.reset_window()
                rssi: list[int] = []
                for s in range(seconds):
                    await asyncio.sleep(1.0)
                    if lab.device is None:
                        raise BenchError("The device disconnected during the test.")
                    try:
                        rssi.append(int(lab.device.get("rssi")))
                    except (TypeError, ValueError):
                        pass
                    await self.update(message=f"Chunk {chunk}: {s + 1}/{seconds} s", progress=(n + (s + 1) / seconds) / len(chunks))
                got = lab.wire.packets - start_packets
                lost = lab.wire.dropped - start_dropped
                expected_pps = SAMPLE_RATE / chunk
                pps = got / seconds
                loss_pct = 100.0 * lost / max(1, got + lost)
                st = summarise(list(lab.net.gaps), 1000.0 / expected_pps)
                dev1 = lab.device or {}
                qd = _delta(dev1, dev0, "qdrop")
                sf = _delta(dev1, dev0, "sendfail")
                reconnects = lab.connect_count - start_connects
                row = [chunk, f"{pps:.1f} / {expected_pps:.1f}", f"{loss_pct:.2f}%", _fmt(st["jitter_ms"], " ms"),
                       _fmt(st["p99_gap_ms"], " ms"), _fmt(st["max_gap_ms"], " ms"),
                       "—" if qd is None else str(qd), "—" if sf is None else str(sf),
                       f"{min(rssi)}…{max(rssi)} dBm" if rssi else "—", str(reconnects)]
                rows.append(row)
                r = len(rows) - 1
                if loss_pct > 0.5:
                    bad.append((r, 2))
                if st["max_gap_ms"] is not None and st["max_gap_ms"] > 250:
                    bad.append((r, 5))
                if pps < 0.9 * expected_pps:
                    bad.append((r, 1))
                if reconnects:
                    bad.append((r, 9))
        finally:
            try:
                await lab.set_config({"chunk": original})
            except Exception:
                pass
        worst = max((float(r[2].rstrip("%")) for r in rows), default=0.0)
        notes.append("Loss is counted from packet sequence numbers, so it includes only packets the device sent. "
                     "A device send-queue drop (qdrop) is also counted by sequence gaps.")
        notes.append("Expected packets/s is 16000 ÷ chunk size. A shortfall with no loss means the device is sending late, not losing data.")
        verdict = {"level": "ok", "text": "Every chunk size held under 0.5% loss with no long gaps or reconnects."} if not bad else \
                  {"level": "warn" if worst < 2 else "bad", "text": "Some chunk sizes show loss, long gaps, a rate shortfall or reconnects (highlighted)."}
        await self.update(phase="done", message="Finished.", progress=1.0, verdict=verdict, notes=notes,
                          tables=[table("Link quality per PCM chunk size",
                                        ["Chunk (samples)", "Packets/s (got / expected)", "Loss", "Jitter (std)", "p99 gap", "Longest gap",
                                         "Device queue drops", "Send fails", "Wi-Fi signal", "Reconnects"], rows, bad)])


def _delta(now: dict, before: dict, key: str) -> int | None:
    try:
        return int(now[key]) - int(before[key])
    except (KeyError, TypeError, ValueError):
        return None


def _fmt(v, unit: str) -> str:
    return "—" if v is None else f"{v}{unit}"
