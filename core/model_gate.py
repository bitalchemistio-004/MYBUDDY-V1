"""model_gate.py - who gets the cloud model next.

A cloud plan answers one request at a time: a second request waits behind the first. MBUDDY makes many model calls
that are not the user's turn (the self-check of an action, the lesson writer, the reader of the user's reaction, notes,
music suggestions, a job driving an app), and each of those used to make the user's next sentence wait behind it
(measured 2026-10-13: three requests at once finish after 0.9, 2.2 and 3.3 s; turns that should take 2 s took 20).

Every call has a priority:
  turn        what the user is waiting for now (deciding what to do, the reply)
  job         work the user asked for that runs in the background (an app being driven, a document being written)
  background  housekeeping nobody is waiting for (self-checks, lessons, notes, suggestions)

A turn call goes first. A job or background call also waits for a quiet moment after the last turn call, because a
turn is several calls in a row (decide, then reply) and a gap of a second between them must not be taken by housekeeping.
Nothing waits for ever: a call that has waited MAX_WAIT_S goes next anyway.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import heapq
import itertools
import os
import time

TURN, JOB, BACKGROUND = "turn", "job", "background"
LEVEL = {TURN: 0, JOB: 1, BACKGROUND: 2}
QUIET_S = {JOB: 1.2, BACKGROUND: 3.0}      # how long after the last turn call a lower priority call may start
MAX_WAIT_S = 90.0
HEADER = "X-MBUDDY-Priority"

# Code that makes calls on a background path sets this once instead of tagging every call.
priority: contextvars.ContextVar[str] = contextvars.ContextVar("mbuddy_llm_priority", default=TURN)


def parse(value) -> str:
    v = str(value or "").strip().lower()
    return v if v in LEVEL else TURN


@contextlib.contextmanager
def as_priority(name: str):
    token = priority.set(parse(name))
    try:
        yield
    finally:
        priority.reset(token)


def url_with(url: str, name: str) -> str:
    """The address of a model call tagged with its priority (a real Ollama ignores the extra query)."""
    name = parse(name)
    if name == TURN:
        return url
    return url + ("&" if "?" in url else "?") + "priority=" + name


class Gate:
    def __init__(self, slots: int = 1, quiet=None, max_wait: float = MAX_WAIT_S, clock=time.monotonic):
        self.slots = max(1, int(slots))
        self.quiet = dict(QUIET_S if quiet is None else quiet)
        self.max_wait = max_wait
        self.clock = clock
        self._heap: list = []
        self._seq = itertools.count()
        self._inflight = 0
        self._turns_inflight = 0
        self._last_turn_end = -1e9
        self._timer = None
        self.waited: dict[str, float] = {TURN: 0.0, JOB: 0.0, BACKGROUND: 0.0}   # total seconds spent waiting, for the log

    # ---- the public way in -------------------------------------------------------------
    @contextlib.asynccontextmanager
    async def slot(self, name: str = TURN):
        name = parse(name)
        t0 = self.clock()
        await self._acquire(name)
        self.waited[name] += self.clock() - t0
        try:
            yield
        finally:
            self._release(name)

    # ---- inside ------------------------------------------------------------------------
    async def _acquire(self, name: str) -> None:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        entry = (LEVEL[name], next(self._seq), self.clock(), fut)
        heapq.heappush(self._heap, entry)
        self._pump()
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():      # it was granted while being cancelled: give the slot back
                self._release(name)
            raise

    def _release(self, name: str) -> None:
        self._inflight = max(0, self._inflight - 1)
        if name == TURN:
            self._turns_inflight = max(0, self._turns_inflight - 1)
            self._last_turn_end = self.clock()
        self._pump()

    def _pump(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        while self._heap and self._inflight < self.slots:
            level, _seq, t0, fut = self._heap[0]
            if fut.done():
                heapq.heappop(self._heap)
                continue
            now = self.clock()
            if level > 0 and now - t0 < self.max_wait:
                if self._turns_inflight > 0:
                    return                               # re-checked when that turn call ends
                name = JOB if level == LEVEL[JOB] else BACKGROUND
                need = self.quiet.get(name, 0.0) - (now - self._last_turn_end)
                if need > 0:
                    wake = min(need, self.max_wait - (now - t0))        # also when its own patience runs out
                    self._timer = asyncio.get_running_loop().call_later(max(0.0, wake) + 0.02, self._pump)
                    return
            heapq.heappop(self._heap)
            self._inflight += 1
            if level == 0:
                self._turns_inflight += 1
            fut.set_result(True)


def default_gate() -> Gate:
    return Gate(slots=int(os.getenv("MBUDDY_CLOUD_CONCURRENCY", "1") or 1))
