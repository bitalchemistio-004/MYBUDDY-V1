"""What happened without the model: the ledger the model reads when it wakes.

Many things MBUDDY does need no model at all (the time, a timer, the volume, a
reminder, pause or next, an app it was asked to open). Doing them in code is
instant and costs no GPU. But the model must not be left unaware: when it next
runs it is told, in one short block, what was handled without it, so it never
says "I didn't do that" about something that happened, and can refer to it.

The ledger keeps the last few entries (in memory and in ~/.buddy/ledger.json so
a restart does not lose them). `block()` is what goes into the model's prompt:
only entries since the model last ran, newest last, short, and never more than
a handful. Nothing here is a memory of the user; it is a log of actions.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_lock = threading.RLock()
MAX_KEEP = 40          # entries kept
MAX_SHOWN = 6          # entries in a prompt
WINDOW_S = 30 * 60     # older than this is not mentioned to the model
_entries: list[dict] = []
_last_model_turn = 0.0
_loaded = False


def _path() -> Path:
    override = os.environ.get("MBUDDY_LEDGER_PATH")
    return Path(override) if override else Path.home() / ".buddy" / "ledger.json"


def _load() -> None:
    global _loaded, _entries, _last_model_turn
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        _entries = [e for e in data.get("entries", []) if isinstance(e, dict)][-MAX_KEEP:]
        _last_model_turn = float(data.get("last_model_turn") or 0.0)
    except (OSError, ValueError):
        _entries, _last_model_turn = [], 0.0


def _save() -> None:
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"entries": _entries[-MAX_KEEP:], "last_model_turn": _last_model_turn}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def record(said: str, did: str, ok: bool = True, source: str = "reflex", now: float | None = None) -> None:
    """One thing handled without the model. `did` is what really happened, in plain words."""
    with _lock:
        _load()
        _entries.append({"t": time.time() if now is None else now, "said": (said or "")[:120], "did": (did or "")[:200],
                         "ok": bool(ok), "source": source})
        del _entries[:-MAX_KEEP]
        _save()


def mark_model_turn(now: float | None = None) -> None:
    """The model has run (it has been told what the ledger held up to now)."""
    global _last_model_turn
    with _lock:
        _load()
        _last_model_turn = time.time() if now is None else now
        _save()


def since_model(now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    with _lock:
        _load()
        return [dict(e) for e in _entries if e["t"] > _last_model_turn and now - e["t"] <= WINDOW_S][-MAX_SHOWN:]


def block(now: float | None = None) -> str:
    """The prompt block, or "" when nothing was handled without the model."""
    items = since_model(now)
    if not items:
        return ""
    now = time.time() if now is None else now
    lines = []
    for e in items:
        ago = int(max(0, now - e["t"]) // 60)
        when = "just now" if ago < 1 else f"{ago} min ago"
        lines.append(f"- {when}: he said \"{e['said']}\"; {'DONE' if e['ok'] else 'FAILED'} without you: {e['did']}")
    return ("HANDLED WITHOUT YOU (real, already done or answered by MBUDDY's own code; you can refer to it, "
            "and never say you didn't do it):\n" + "\n".join(lines))


def reset() -> None:
    global _entries, _last_model_turn, _loaded
    with _lock:
        _entries, _last_model_turn, _loaded = [], 0.0, True
