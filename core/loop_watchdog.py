"""Catches a frozen backend and says why.

The backend's event loop has frozen (HTTP requests refused or never answered while a side thread kept printing).
This ticks a timestamp from inside the loop every second; a plain thread notices when the ticks stop for longer
than STALL_S and writes every thread's stack to logs/freeze-<time>.txt (once per freeze). It changes nothing and
costs one tiny call a second.
"""
from __future__ import annotations

import asyncio
import faulthandler
import threading
import time
from pathlib import Path

import utils.logger as log

STALL_S = 12.0
_last = [time.monotonic()]
_started = [False]


def _dump_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "logs"


def _watch() -> None:
    reported = False
    while True:
        time.sleep(3)
        stalled = time.monotonic() - _last[0]
        if stalled > STALL_S and not reported:
            reported = True
            path = _dump_dir() / f"freeze-{time.strftime('%Y%m%d-%H%M%S')}.txt"
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("w") as f:
                    f.write(f"event loop did not tick for {stalled:.0f} s\n")
                    f.flush()
                    faulthandler.dump_traceback(file=f, all_threads=True)
                log.warn(f"[Watchdog] the event loop froze for {stalled:.0f} s; stacks are in {path.name}")
            except OSError:
                pass
        elif stalled <= STALL_S:
            reported = False


async def start(app) -> None:
    if _started[0]:
        return
    _started[0] = True

    async def tick():
        while True:
            _last[0] = time.monotonic()
            await asyncio.sleep(1)

    app["watchdog_tick"] = asyncio.create_task(tick())
    threading.Thread(target=_watch, name="mbuddy-loop-watchdog", daemon=True).start()
