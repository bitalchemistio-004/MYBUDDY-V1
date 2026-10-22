"""rewards.py - progress for things the user really did, nothing invented.

XP comes only from events the app itself saw happen: a task completed, a focus sprint finished, a habit logged. Each award has a
key, and a key pays once, so completing the same task twice or logging the same habit twice in a day cannot be farmed. A streak
is consecutive days with at least one award. Level, badges and the streak are computed from the stored awards, so they cannot
drift from them. Everything stays on this PC (~/.buddy/rewards.json).

The phone app has its own progress (shell/ui/js/papp/store.js); the two are separate on purpose: each counts what happened on
its own device.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

XP_PER_LEVEL = 300
KINDS = {
    "task_done": (10, "Task done"),
    "focus_sprint": (25, "Focus sprint finished"),
    "habit": (8, "Habit kept"),
}
MIN_FOCUS_S = 600            # a sprint pays only when at least ten minutes were really focused
KEEP = 600                   # awards kept (older ones are folded into the totals)

_lock = threading.RLock()
_mem: dict = {}


def _path() -> Path | None:
    override = os.environ.get("MBUDDY_REWARDS_PATH")
    if override:
        return Path(override)
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None                       # the tests must not write the user's home folder
    return Path.home() / ".buddy" / "rewards.json"


def _load() -> dict:
    p = _path()
    if p is None:
        return _mem
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    p = _path()
    if p is None:
        _mem.clear()
        _mem.update(d)
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _blank() -> dict:
    return {"awards": [], "keys": [], "totals": {"xp": 0, "task_done": 0, "focus_sprint": 0, "habit": 0}, "seq": 0, "days": []}


def award(kind: str, key: str, label: str = "", now: float | None = None) -> dict | None:
    """Pay [kind] once for [key]. Returns the award ({"id","kind","xp","label","at"}) or None when it was already paid or unknown."""
    if kind not in KINDS:
        return None
    xp, default = KINDS[kind]
    now = time.time() if now is None else now
    full = f"{kind}:{key}"
    with _lock:
        d = _load() or _blank()
        for k, v in _blank().items():
            d.setdefault(k, v)
        if full in d["keys"]:
            return None
        d["seq"] += 1
        a = {"id": d["seq"], "kind": kind, "xp": xp, "label": (label or default)[:80], "at": now}
        d["awards"] = (d["awards"] + [a])[-KEEP:]
        d["keys"] = (d["keys"] + [full])[-4 * KEEP:]
        d["totals"]["xp"] += xp
        d["totals"][kind] = d["totals"].get(kind, 0) + 1
        day = datetime.fromtimestamp(now).date().isoformat()
        if day not in d["days"]:
            d["days"] = sorted(set(d["days"] + [day]))[-400:]
        _save(d)
    return a


def streak(days: list[str], today: date | None = None) -> int:
    """Consecutive days ending today, or yesterday (today is not over yet)."""
    today = today or date.today()
    have = set(days)
    cur = today if today.isoformat() in have else today - timedelta(days=1)
    n = 0
    while cur.isoformat() in have:
        n += 1
        cur -= timedelta(days=1)
    return n


def best_streak(days: list[str]) -> int:
    best = run = 0
    prev: date | None = None
    for s in sorted(set(days)):
        d = date.fromisoformat(s)
        run = run + 1 if prev is not None and d - prev == timedelta(days=1) else 1
        best, prev = max(best, run), d
    return best


BADGES = [
    ("first_task", "First task", "Complete a task", lambda t, s, lv: t["task_done"] >= 1),
    ("tasks_10", "Ten tasks", "Complete ten tasks", lambda t, s, lv: t["task_done"] >= 10),
    ("focus_1", "First focus", "Finish a focus sprint", lambda t, s, lv: t["focus_sprint"] >= 1),
    ("focus_5", "Five sprints", "Finish five focus sprints", lambda t, s, lv: t["focus_sprint"] >= 5),
    ("habit_7", "Seven habits", "Keep a habit seven times", lambda t, s, lv: t["habit"] >= 7),
    ("streak_3", "Three days", "Three days in a row", lambda t, s, lv: s >= 3),
    ("streak_7", "A week", "Seven days in a row", lambda t, s, lv: s >= 7),
    ("level_5", "Level five", "Reach level five", lambda t, s, lv: lv >= 5),
]


def snapshot(today: date | None = None) -> dict:
    with _lock:
        d = _load() or _blank()
    t = {**_blank()["totals"], **(d.get("totals") or {})}
    xp = int(t["xp"])
    level = 1 + xp // XP_PER_LEVEL
    s = streak(d.get("days") or [], today)
    badges = [{"id": b, "name": n, "how": how, "earned": bool(rule(t, s, level))} for b, n, how, rule in BADGES]
    last = (d.get("awards") or [None])[-1]
    return {"xp": xp, "level": level, "into_level": xp % XP_PER_LEVEL, "per_level": XP_PER_LEVEL,
            "streak": s, "best_streak": best_streak(d.get("days") or []), "totals": t, "badges": badges,
            "recent": list(reversed((d.get("awards") or [])[-8:])), "last": last}


def last() -> dict | None:
    """The newest award (the live feed carries it so a window can show "+10" once)."""
    with _lock:
        d = _load()
    a = (d.get("awards") or [None])[-1] if d else None
    return {"id": a["id"], "kind": a["kind"], "xp": a["xp"], "label": a["label"], "at": a["at"]} if a else None


def reset() -> None:
    """Tests only."""
    with _lock:
        _mem.clear()
