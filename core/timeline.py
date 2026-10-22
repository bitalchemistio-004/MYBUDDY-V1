"""One timeline per turn: when each stage started and ended (M2 S6c).

After S6/S6b the first words still came 4.5-28 s after a turn began, and the
log couldn't say where the time went ("hey, how's it going?": decide 1.8 s,
~0.5 s of prompt reading, first text at 5.7 s). Every stage marks itself here;
the turn logs one "[Turn] timeline" line. The timeline travels with the turn
through asyncio tasks and worker threads (contextvars are copied into both).
"""
from __future__ import annotations

import time
from contextvars import ContextVar

_current: ContextVar["Timeline | None"] = ContextVar("mbuddy_turn_timeline", default=None)


class Timeline:
    def __init__(self, start: float | None = None):
        self.start = time.monotonic() if start is None else start
        self.marks: list[tuple[str, float]] = []

    def mark(self, name: str) -> None:
        self.marks.append((name, time.monotonic() - self.start))

    def line(self) -> str:
        """Stages as "name start-end" (a pair of name> and name< marks) or
        "name at" (a single mark), in the order they happened."""
        opened: dict[str, float] = {}
        parts: list[tuple[float, str]] = []
        for name, at in self.marks:
            if name.endswith(">"):
                opened[name[:-1]] = at
            elif name.endswith("<") and name[:-1] in opened:
                began = opened.pop(name[:-1])
                parts.append((began, f"{name[:-1]} {began:.2f}-{at:.2f}"))
            else:
                parts.append((at, f"{name} {at:.2f}"))
        parts += [(began, f"{name} {began:.2f}-?") for name, began in opened.items()]
        return " | ".join(text for _at, text in sorted(parts))


def begin(start: float | None = None) -> Timeline:
    """Start this turn's timeline (start: the loop time the turn began)."""
    tl = Timeline(start)
    _current.set(tl)
    return tl


def current() -> Timeline | None:
    return _current.get()


def mark(name: str) -> None:
    """Mark a stage; "x>" opens and "x<" closes a span. No timeline: nothing."""
    tl = _current.get()
    if tl is not None:
        tl.mark(name)
