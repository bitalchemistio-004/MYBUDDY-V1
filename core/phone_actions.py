"""Things MBUDDY wants the user's PHONE to do: set an alarm, start a timer, add a calendar event, start a call or a message,
open directions. A web page cannot do these by itself and the PC cannot reach into the phone, so the PC queues them here and
the phone page (shell/ui/phone.html) shows each as a button; one tap on the phone makes Android do it (an Android "intent":
the same mechanism a shortcut uses). Nothing happens on the phone without that tap, and nothing here controls the phone.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

KINDS = ("alarm", "timer", "calendar", "call", "sms", "whatsapp", "navigate", "search", "email", "open_gallery")
KEEP_S = 3 * 86400
_lock = threading.RLock()


def _path() -> Path:
    override = os.getenv("MBUDDY_PHONE_ACTIONS_PATH")
    return Path(override) if override else Path.home() / ".buddy" / "phone_actions.json"


def _load() -> list[dict]:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(rows: list[dict]) -> None:
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def clean(kind: str, params: dict) -> dict:
    """Only the fields each kind uses, short and printable. Raises ValueError for what cannot be a real action."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    text = lambda k, n=200: str(params.get(k, "") or "").strip()[:n]            # noqa: E731
    out: dict = {}
    if kind == "alarm":
        hour, minute = params.get("hour"), params.get("minute", 0)
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (hour, minute)) or not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("an alarm needs hour 0-23 and minute 0-59")
        out = {"hour": hour, "minute": minute, "label": text("label", 80)}
    elif kind == "timer":
        seconds = params.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 24 * 3600:
            raise ValueError("a timer is 1 second to 24 hours")
        out = {"seconds": seconds, "label": text("label", 80)}
    elif kind == "calendar":
        start = text("start", 40)
        if not start:
            raise ValueError("an event needs a start time")
        out = {"title": text("title", 120) or "Event", "start": start, "end": text("end", 40), "place": text("place", 120), "notes": text("notes", 400)}
    elif kind in ("call", "sms", "whatsapp"):
        number = "".join(c for c in text("number", 30) if c.isdigit() or c == "+")
        if len(number) < 3:
            raise ValueError("a number is needed (a web page can't look up contacts)")
        out = {"number": number, "body": text("body", 500)}
    elif kind == "navigate":
        if not text("place"):
            raise ValueError("a place is needed")
        out = {"place": text("place")}
    elif kind == "search":
        if not text("query"):
            raise ValueError("a search needs words")
        out = {"query": text("query")}
    elif kind == "email":
        out = {"to": text("to", 120), "subject": text("subject", 160), "body": text("body", 800)}
    return out


def add(kind: str, params: dict, source: str = "mbuddy", now: float | None = None) -> dict:
    """Queue one action for the phone. Returns the stored action."""
    data = clean(kind, params or {})
    now = time.time() if now is None else now
    row = {"id": uuid.uuid4().hex[:12], "kind": kind, "params": data, "source": source[:30], "created": now, "status": "pending"}
    with _lock:
        rows = [r for r in _load() if now - r["created"] < KEEP_S]
        rows.append(row)
        _save(rows)
    return row


def pending(now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    with _lock:
        return [r for r in _load() if r["status"] == "pending" and now - r["created"] < KEEP_S]


def mark(action_id: str, status: str) -> bool:
    if status not in ("done", "dismissed"):
        return False
    with _lock:
        rows = _load()
        for r in rows:
            if r["id"] == action_id:
                r["status"] = status
                _save(rows)
                return True
    return False


def describe(row: dict) -> str:
    p, k = row["params"], row["kind"]
    if k == "alarm":
        return f"alarm at {p['hour']:02d}:{p['minute']:02d}" + (f" ({p['label']})" if p.get("label") else "")
    if k == "timer":
        m, s = divmod(p["seconds"], 60)
        return f"timer for {m} min" + (f" {s} s" if s else "") if m else f"timer for {s} s"
    if k == "calendar":
        return f"calendar event “{p['title']}” at {p['start']}"
    if k in ("call", "sms", "whatsapp"):
        return f"{ {'call': 'call', 'sms': 'text', 'whatsapp': 'WhatsApp message to'}[k] } {p['number']}"
    if k == "navigate":
        return f"directions to {p['place']}"
    if k == "search":
        return f"search for “{p['query']}”"
    if k == "email":
        return "email" + (f" to {p['to']}" if p.get("to") else "")
    return "open AI Edge Gallery"
