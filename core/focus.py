"""Focus sprints: a timer the user starts, that counts toward their day.

A sprint is a set number of minutes. It ends by itself (completed) or when
the user stops it. The week's history is real: only sprints that ran are
counted, and only the minutes that were really focused. While a sprint runs,
MBUDDY can nudge the user if the program in front is one they told it is not
work (learned in Today, core/activity.py); that needs tracking to be on, and
it never closes or hides anything.

State lives in ~/.buddy/activity/focus.json next to Today's data.
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta

from core import activity

MIN_MIN, MAX_MIN = 5, 180
NUDGE_AFTER_S = 60         # in a not-work program this long during a sprint: one nudge
NUDGE_GAP_S = 5 * 60       # and then at most one nudge this often
_lock = threading.RLock()
_last_nudge = 0.0


def _path():
    return activity.root() / "focus.json"


def _state() -> dict:
    data = activity._read(_path(), {})
    data.setdefault("current", None)
    data.setdefault("history", [])
    data.setdefault("settings", {"nudge": True})
    return data


def _save(data: dict) -> None:
    data["history"] = data["history"][-400:]
    activity._write(_path(), data)


def _finish(data: dict, now: float, completed: bool) -> dict:
    cur = data["current"]
    focused = min(now, cur["ends"]) - cur["start"]
    rec = {"id": cur["id"], "date": datetime.fromtimestamp(cur["start"]).date().isoformat(),
           "start": datetime.fromtimestamp(cur["start"]).isoformat(timespec="seconds"),
           "planned_min": cur["minutes"], "focused_s": max(0, int(focused)), "completed": completed}
    data["history"].append(rec)
    data["current"] = None
    data["last_finished"] = {**rec, "at": now}
    if completed and rec["focused_s"] >= 600:              # a sprint pays only when ten minutes were really focused
        try:
            from core import rewards
            rewards.award("focus_sprint", str(rec["id"]), f"Focus sprint: {rec['planned_min']} min")
        except Exception:
            pass
    return rec


def start(minutes: int, now: float | None = None) -> dict:
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not MIN_MIN <= minutes <= MAX_MIN:
        raise ValueError(f"minutes must be a whole number from {MIN_MIN} to {MAX_MIN}")
    now = time.time() if now is None else now
    with _lock:
        data = _state()
        if data["current"] and now < data["current"]["ends"]:
            raise ValueError("a sprint is already running")
        if data["current"]:
            _finish(data, now, True)
        data["current"] = {"id": int(now), "start": now, "minutes": minutes, "ends": now + minutes * 60}
        _save(data)
    return current(now)


def stop(now: float | None = None) -> dict | None:
    now = time.time() if now is None else now
    with _lock:
        data = _state()
        if not data["current"]:
            return None
        rec = _finish(data, now, now >= data["current"]["ends"])
        _save(data)
    return rec


def current(now: float | None = None) -> dict | None:
    """The running sprint, or None. A sprint whose time is up is closed here as completed."""
    now = time.time() if now is None else now
    with _lock:
        data = _state()
        cur = data["current"]
        if not cur:
            return None
        if now >= cur["ends"]:
            _finish(data, now, True)
            _save(data)
            return None
    return {"id": cur["id"], "minutes": cur["minutes"], "remaining_s": int(cur["ends"] - now),
            "elapsed_s": int(now - cur["start"]), "ends_at": datetime.fromtimestamp(cur["ends"]).isoformat(timespec="seconds")}


def just_finished(now: float | None = None, within_s: float = 20) -> dict | None:
    """The sprint that ended in the last few seconds (the buddy celebrates once)."""
    now = time.time() if now is None else now
    current(now)
    done = _state().get("last_finished")
    return done if done and now - done["at"] <= within_s and done["completed"] else None


def settings() -> dict:
    return _state()["settings"]


def set_settings(changes: dict) -> dict:
    with _lock:
        data = _state()
        for key, value in (changes or {}).items():
            if key != "nudge" or not isinstance(value, bool):
                raise ValueError("nudge must be true or false")
            data["settings"][key] = value
        _save(data)
        return data["settings"]


def week(today: date | None = None) -> list[dict]:
    today = today or date.today()
    history = _state()["history"]
    out = []
    for back in range(6, -1, -1):
        day = (today - timedelta(days=back)).isoformat()
        mine = [h for h in history if h["date"] == day]
        out.append({"date": day, "minutes": round(sum(h["focused_s"] for h in mine) / 60),
                    "sprints": len(mine), "completed": sum(1 for h in mine if h["completed"])})
    return out


def nudge(now: float | None = None) -> dict | None:
    """During a sprint: the program in front is one the user said is not work."""
    global _last_nudge
    now = time.time() if now is None else now
    if not settings().get("nudge") or not current(now) or not activity.settings().get("tracking"):
        return None
    t = activity.TRACKER
    if not t.run_key or activity.label_of(t.run_key) != "off":
        return None
    if now - t.run_start < NUDGE_AFTER_S or now - _last_nudge < NUDGE_GAP_S:
        return None
    _last_nudge = now
    name = (t._day or {}).get("apps", {}).get(t.run_key, {}).get("name", t.run_key)
    return {"app": name, "key": t.run_key}
