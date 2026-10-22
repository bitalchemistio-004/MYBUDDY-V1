"""Routines: things the user does the same way, saved as steps and run with one word.

A routine is a named list of steps. A step is one of a few real things MBUDDY
can already do, and each runs through the same abilities MBUDDY uses when you
ask (so the same checks apply and each step reports what really happened):

  open_app    open a program            (the open_app ability)
  play_music  play something            (the music ability: the app the user chose)
  open_url    open a web address        (the open_url ability)
  focus       start a focus sprint      (core/focus.py)

A routine only ever runs what the user put in it, when the user runs it (a
click, or asking MBUDDY to run it). It stops at the first step that fails and
says which one and why. Nothing is scheduled and nothing runs by itself.
While it runs it is a job, so the buddy shows the real progress (step n of m).
Routines live in ~/.buddy/config/routines.json.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from core import jobs

_lock = threading.RLock()
STEP_TYPES = {
    "open_app": {"label": "Open an app", "field": "name", "ability": "open_app", "arg": "name"},
    "play_music": {"label": "Play music", "field": "query", "ability": "music_play", "arg": "query"},
    "open_url": {"label": "Open a website", "field": "url", "ability": "open_url", "arg": "url"},
    "focus": {"label": "Start a focus sprint", "field": "minutes", "ability": None, "arg": "minutes"},
}
MAX_STEPS = 12
_running: dict[str, threading.Thread] = {}


def _path() -> Path:
    override = os.environ.get("MBUDDY_ROUTINES_PATH")
    return Path(override) if override else Path.home() / ".buddy" / "config" / "routines.json"


def _load() -> list[dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save(data: list[dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def clean_step(step: dict) -> dict:
    """A validated step, or ValueError with a message the user can act on."""
    kind = (step or {}).get("type")
    if kind not in STEP_TYPES:
        raise ValueError(f"a step is one of: {', '.join(STEP_TYPES)}")
    field = STEP_TYPES[kind]["field"]
    value = step.get(field)
    if kind == "focus":
        if isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 180:
            raise ValueError("a focus sprint is 5 to 180 minutes")
        return {"type": kind, "minutes": value}
    value = str(value or "").strip()
    if not value or len(value) > 200:
        raise ValueError(f"'{STEP_TYPES[kind]['label']}' needs its {field} (up to 200 characters)")
    if kind == "open_url" and not re.match(r"^https?://\S+$", value):
        raise ValueError("a website step needs an address that starts with http:// or https://")
    return {"type": kind, "label": STEP_TYPES[kind]["label"], field: value}


def routines() -> list[dict]:
    return _load()


def get(routine_id: str) -> dict | None:
    return next((r for r in _load() if r["id"] == routine_id), None)


def find_by_name(name: str) -> dict | None:
    """The routine the user means ("work day", "run my wind down routine")."""
    words = set(re.findall(r"[a-z0-9]+", (name or "").lower())) - {"my", "the", "routine", "please", "run", "start"}
    if not words:
        return None
    best, score = None, 0
    for r in _load():
        rw = set(re.findall(r"[a-z0-9]+", r["name"].lower())) - {"my", "the", "routine"}
        n = len(words & rw)
        if n and (words <= rw or rw <= words) and n > score:
            best, score = r, n
    return best


def save(name: str, steps: list[dict], routine_id: str | None = None) -> dict:
    name = (name or "").strip()
    if not 1 <= len(name) <= 60:
        raise ValueError("a routine needs a name of 1 to 60 characters")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f"a routine needs 1 to {MAX_STEPS} steps")
    cleaned = [clean_step(s) for s in steps]
    with _lock:
        data = _load()
        other = [r for r in data if r["id"] != routine_id]
        if any(r["name"].lower() == name.lower() for r in other):
            raise ValueError("a routine with that name exists")
        if routine_id:
            existing = next((r for r in data if r["id"] == routine_id), None)
            if existing is None:
                raise KeyError(routine_id)
            existing.update({"name": name, "steps": cleaned})
            record = existing
        else:
            n = 1
            ids = {r["id"] for r in data}
            while f"r{n}" in ids:
                n += 1
            record = {"id": f"r{n}", "name": name, "created": datetime.now().isoformat(timespec="seconds"), "steps": cleaned, "last_run": None}
            data.append(record)
        _save(data)
    return record


def delete(routine_id: str) -> bool:
    with _lock:
        data = _load()
        keep = [r for r in data if r["id"] != routine_id]
        if len(keep) == len(data):
            return False
        _save(keep)
        return True


# ── running ───────────────────────────────────────────────────────────────
def _do_step(step: dict) -> tuple[bool, str]:
    kind = step["type"]
    if kind == "focus":
        from core import focus
        try:
            cur = focus.start(step["minutes"])
            return True, f"A {cur['minutes']} minute focus sprint started."
        except ValueError as exc:
            return False, str(exc)
    from brain import pc_actions
    from brain.tools import ToolFailure
    meta = STEP_TYPES[kind]
    out = pc_actions.run(meta["ability"], {meta["arg"]: step[meta["field"]]})
    text = str(out)
    return (not isinstance(out, ToolFailure) and not text.startswith(("FAILED", "NOT PLAYING", "NEEDS CHOICE"))), text


def run(routine_id: str, do_step=_do_step, pause_s: float = 1.5) -> dict:
    """Run a routine now (blocking). Stops at the first step that fails. Returns the record of what happened."""
    r = get(routine_id)
    if r is None:
        raise KeyError(routine_id)
    steps = r["steps"]
    job = jobs.start("routine", r["name"], f"run the routine {r['name']}", total=len(steps))
    results, ok = [], True
    for i, step in enumerate(steps, 1):
        label = step.get("label") or STEP_TYPES[step["type"]]["label"]
        detail = step.get(STEP_TYPES[step["type"]]["field"])
        jobs.step(job, i, doing=f"{label}: {detail}" if detail else label, phase="executing", progress=(i - 1) / len(steps))
        try:
            done, text = do_step(step)
        except Exception as exc:  # noqa: BLE001
            done, text = False, f"{type(exc).__name__}: {exc}"
        results.append({"step": i, "type": step["type"], "ok": bool(done), "text": text[:300]})
        if not done:
            ok = False
            break
        jobs.step(job, i, last=text[:120], progress=i / len(steps))
        if i < len(steps):
            time.sleep(pause_s)
    outcome = (f"Ran {r['name']}: all {len(steps)} steps worked." if ok
               else f"Stopped {r['name']} at step {results[-1]['step']} of {len(steps)}: {results[-1]['text'][:160]}")
    jobs.finish(job, ok, outcome)
    record = {"t": datetime.now().isoformat(timespec="seconds"), "ok": ok, "results": results}
    with _lock:
        data = _load()
        for x in data:
            if x["id"] == routine_id:
                x["last_run"] = record
        _save(data)
    return {"ok": ok, "outcome": outcome, **record}


def run_async(routine_id: str) -> bool:
    """Start it in the background (one run of a routine at a time). False if it is already running."""
    with _lock:
        t = _running.get(routine_id)
        if t and t.is_alive():
            return False
        t = threading.Thread(target=run, args=(routine_id,), name=f"mbuddy-routine-{routine_id}", daemon=True)
        _running[routine_id] = t
    t.start()
    return True


def is_running(routine_id: str) -> bool:
    t = _running.get(routine_id)
    return bool(t and t.is_alive())
