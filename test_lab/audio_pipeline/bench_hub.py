"""
Shared plumbing for the extra test benches (speaker, echo/barge-in, speech-to-text,
voice detection, network).

The board can only talk to one server, so every bench runs inside the audio-lab
server and shares its device connection. A bench is a small object with a
`state` dict that the page renders generically:

    phase     idle | running | done | failed | cancelled
    message   one-line status
    progress  0..1
    tables    [{"title", "columns": [...], "rows": [[...], ...], "bad": [(row, col), ...]}]
    charts    [{"title", "x": [...], "y": [...], "xlabel", "ylabel", "logx": bool}]
    notes     [str]        plain-language caveats and findings
    verdict   None | {"level": ok|warn|bad, "text": str}

Benches never invent numbers: anything that cannot be measured is reported as
not measured, with the reason.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable

import numpy as np


class BenchError(Exception):
    """A bench cannot run or cannot trust its measurement; the message says why."""


class BenchBase:
    name = "bench"

    def __init__(self, lab) -> None:
        self.lab = lab
        self.state = self.idle_state()
        self._task: asyncio.Task | None = None

    @staticmethod
    def idle_state() -> dict:
        return {"phase": "idle", "message": "Idle.", "progress": 0.0, "tables": [], "charts": [], "notes": [], "verdict": None, "prompt": None}

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def publish(self) -> None:
        await self.lab.broadcast({"type": "bench", "name": self.name, "state": self.state})

    async def update(self, **kw) -> None:
        self.state.update(kw)
        await self.publish()

    def start(self, coro: Awaitable) -> None:
        """Run `coro` in the background, turning any failure into a visible 'failed' state."""
        async def wrapper():
            try:
                await coro
            except asyncio.CancelledError:
                self.state.update(phase="cancelled", message="Cancelled.")
                await self.publish()
                raise
            except BenchError as exc:
                self.state.update(phase="failed", message=str(exc), verdict={"level": "bad", "text": str(exc)})
                await self.publish()
            except Exception as exc:                        # never leave the page stuck on "running"
                msg = f"{type(exc).__name__}: {exc}"
                self.state.update(phase="failed", message=msg, verdict={"level": "bad", "text": msg})
                await self.publish()
        self._task = asyncio.ensure_future(wrapper())

    async def begin(self, message: str) -> None:
        self.state = {**self.idle_state(), "phase": "running", "message": message}
        await self.publish()

    async def cancel(self) -> None:
        if self.running:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def action(self, action: str, params: dict) -> str | None:     # pragma: no cover - overridden
        return f"{self.name}: unknown action {action!r}"

    # helpers shared by the benches ---------------------------------------
    def require_streaming(self, mic: str | None = None) -> None:
        snap = self.lab.wire.snapshot(time.monotonic())
        if self.lab.device_ws is None or not snap["streaming"]:
            raise BenchError("The device is not streaming audio. Connect the board (lab firmware) and try again.")
        if mic and snap["mic"] not in (mic, "both"):
            raise BenchError(f"This test needs the {mic} microphone to be transmitted (it is sending {snap['mic']}).")


class Capture:
    """Collects the RAW mic samples that arrive while the `with` block is open."""

    def __init__(self, lab) -> None:
        self.lab = lab
        self._l: list[np.ndarray] = []
        self._r: list[np.ndarray] = []
        self.arrivals: list[tuple[float, int]] = []      # (monotonic arrival time, total samples so far)
        self._stamps: list[tuple[float, float, int, int]] = []   # (arrival s, device clock s, first sample index, samples)
        self.samples = 0

    def _on(self, frame, now: float) -> None:
        ref = frame.left if frame.left is not None else frame.right
        if frame.left is not None:
            self._l.append(frame.left.copy())
        if frame.right is not None:
            self._r.append(frame.right.copy())
        self._stamps.append((now, frame.device_ms / 1000.0, self.samples, len(ref)))
        self.samples += len(ref)
        self.arrivals.append((now, self.samples))

    def __enter__(self) -> "Capture":
        self.lab.taps.append(self._on)
        return self

    def __exit__(self, *exc) -> None:
        if self._on in self.lab.taps:
            self.lab.taps.remove(self._on)

    def arrays(self) -> tuple[np.ndarray | None, np.ndarray | None]:
        """(left, right) int16 samples captured so far; None for a channel that never arrived."""
        return (np.concatenate(self._l) if self._l else None, np.concatenate(self._r) if self._r else None)

    def channel(self, which: str = "auto") -> np.ndarray:
        """int16 samples of one channel. 'auto' prefers left, then right."""
        order = {"left": ("l",), "right": ("r",), "auto": ("l", "r")}[which]
        for k in order:
            parts = self._l if k == "l" else self._r
            if parts:
                return np.concatenate(parts)
        raise BenchError(f"No {which} microphone audio was captured.")

    def server_time_of_sample(self, idx: int, rate: int = 16000) -> float | None:
        """
        When (on the server's clock) a sample was captured by the device, from the device's own packet timestamps.

        Packet ARRIVAL times are bursty over Wi-Fi, so they cannot place a sound to better than ~100 ms. The device's
        capture clock does not suffer that; only its offset from the server clock is unknown, and that is estimated
        from the fastest packet (network delay only ever adds). What remains is the device-to-server delay of the
        fastest packet, a few milliseconds on a LAN.
        """
        if not self._stamps:
            return None
        offset = min(arrival - dev for arrival, dev, _s, _n in self._stamps)
        for arrival, dev, start, n in self._stamps:
            if start <= idx < start + n:
                return dev - (n - (idx - start)) / rate + offset      # the stamp is taken at the END of the frame
        return None

    def latency_ms(self, sample_idx: int, t_sent: float) -> float | None:
        """Milliseconds from `t_sent` (server monotonic clock) to the device capturing `sample_idx`."""
        t = self.server_time_of_sample(sample_idx)
        return None if t is None else (t - t_sent) * 1000.0

    def sample_at(self, t_monotonic: float) -> int:
        """Approximate capture sample index that had arrived at time t (packet granularity)."""
        idx = 0
        for t, n in self.arrivals:
            if t > t_monotonic:
                break
            idx = n
        return idx


def table(title: str, columns: list[str], rows: list[list], bad: list[tuple[int, int]] | None = None) -> dict:
    return {"title": title, "columns": columns, "rows": rows, "bad": bad or []}


def chart(title: str, x, y, xlabel: str, ylabel: str, logx: bool = False) -> dict:
    return {"title": title, "x": [float(v) for v in x], "y": [float(v) for v in y], "xlabel": xlabel, "ylabel": ylabel, "logx": logx}
