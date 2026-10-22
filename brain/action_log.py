"""
brain/action_log.py - what MBUDDY actually did, the only basis for "I did X".

Every tool that really runs (keyword route or agent) is recorded here with
its result. The reply prompt gets this list instead of relying on what
MBUDDY said in earlier turns: live, it kept repeating its own false "Volume
cut to 50%" as if it were a fact.
"""
from __future__ import annotations

import threading
import time
from collections import deque

_MAX = 10
_log: deque = deque(maxlen=_MAX)
_lock = threading.Lock()


def record(name: str, args: dict | None, result: str) -> None:
    with _lock:
        _log.append({"time": time.strftime("%H:%M:%S"), "at": time.time(), "tool": name,
                     "args": args or {}, "result": str(result)[:200]})


def recent(n: int = _MAX) -> list[dict]:
    with _lock:
        return list(_log)[-n:]


def since(at: float) -> list[dict]:
    """Actions recorded after the time `at` (seconds since the epoch)."""
    with _lock:
        return [a for a in _log if a.get("at", 0) > at]


def clear() -> None:
    with _lock:
        _log.clear()


MAX_AGE_S = 15 * 60  # older actions are the timeline's job, not this turn's


def block(n: int = 5) -> str:
    """Prompt block listing the most recent real actions, or ""."""
    now = time.time()
    items = [a for a in recent(n) if now - float(a.get("at", now)) <= MAX_AGE_S]
    if not items:
        return ""
    # No clock times: the model spun them into stories ("no drops since 19:19").
    lines = [f"- {a['tool']}: {a['result']}" for a in items]
    return ("WHAT YOU ACTUALLY DID RECENTLY, oldest first (the only actions you may say you did; "
            "earlier replies are not evidence):\n" + "\n".join(lines))
