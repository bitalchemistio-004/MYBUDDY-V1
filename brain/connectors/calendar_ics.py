"""
Calendar connector: the local calendar in task_tracker, as .ics files.

MBUDDY's calendar is the shared SQLite file task_tracker uses: tasks with a
due time (what add_reminder creates) and the Schedule tab's `reminders`
table. This connector reads those, writes them to a standard iCalendar
(.ics, RFC 5545) file any calendar app can import (the new Outlook on this
PC opens .ics files), and imports an .ics file back as reminders.

Pure Python: no icalendar package is installed, and the subset needed
(VEVENT with SUMMARY, DTSTART, DESCRIPTION, UID, RRULE) is small. Times are
exported in UTC; imported times are turned into local time. There is no
time-zone database in this venv (no tzdata), so an event with a named
TZID is read as local time and the result says so rather than guessing.

Risks: listing is "read"; export writes one new file in Documents\\MBUDDY
("low"); import adds reminders ("low"). Both run at once like pc_actions low entries (preview() words a question if they should ask),
and each result is checked (the file is parsed back; the tasks are read back).
"""
from __future__ import annotations

import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from brain.connectors import Ability, fail
from brain.tools import ToolFailure

MAX_IMPORT_BYTES = 1_000_000
MAX_IMPORT_EVENTS = 200
PRODID = "-//MBUDDY//Local calendar//EN"

OUT_DIR: Path | None = None  # tests point this at a temp folder


def _tracker():
    from brain import task_tracker as tt
    return tt.task_tracker


def _out_dir() -> Path:
    if OUT_DIR is not None:
        return OUT_DIR
    try:
        from win32com.shell import shell, shellcon
        docs = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_PERSONAL, None, 0))
    except Exception:
        docs = Path.home() / "Documents"
    return docs / "MBUDDY"


# ── iCalendar text ───────────────────────────────────────────────────────
def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _unescape(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _fold(line: str) -> str:
    """RFC 5545 3.1: lines longer than 75 octets continue with CRLF + space."""
    parts, current = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        limit = 75 if not parts else 74  # continuation lines start with a space
        if len(current) + len(b) > limit:
            parts.append(current.decode("utf-8"))
            current = b""
        current += b
    parts.append(current.decode("utf-8"))
    return "\r\n ".join(parts)


def _utc_stamp(local_naive: datetime) -> str:
    return local_naive.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def to_ics(events: list[dict], now: datetime | None = None) -> str:
    """events: {"uid", "summary", "start" (local naive datetime), "description"?, "rrule"?}."""
    stamp = (now or datetime.now()).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN"]
    for e in events:
        lines += ["BEGIN:VEVENT",
                  f"UID:{e['uid']}",
                  f"DTSTAMP:{stamp}",
                  f"DTSTART:{_utc_stamp(e['start'])}",
                  f"DTEND:{_utc_stamp(e['start'] + timedelta(minutes=30))}",
                  f"SUMMARY:{_escape(e['summary'])}"]
        if e.get("description"):
            lines.append(f"DESCRIPTION:{_escape(e['description'])}")
        if e.get("rrule"):
            lines.append(f"RRULE:{e['rrule']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def _parse_dt(params: str, value: str) -> tuple[datetime | None, str]:
    """(local naive datetime, note). Handles UTC 'Z', floating, TZID and VALUE=DATE."""
    value = value.strip()
    note = ""
    try:
        if "VALUE=DATE" in params.upper() or re.fullmatch(r"\d{8}", value):
            d = datetime.strptime(value[:8], "%Y%m%d")
            return d.replace(hour=9), "all-day, set for 09:00"
        if value.endswith("Z"):
            utc = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            return utc.astimezone().replace(tzinfo=None), ""
        tzid = re.search(r"TZID=([^;:]+)", params, re.I)
        if tzid:
            note = f"time zone '{tzid.group(1).strip(chr(34))}' read as local time"
        return datetime.strptime(value[:15], "%Y%m%dT%H%M%S"), note
    except ValueError:
        return None, f"couldn't read the time '{value}'"


def parse_ics(text: str) -> list[dict]:
    """VEVENTs as {"uid", "summary", "start", "description", "rrule", "note"}."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n[ \t]", "", text)  # unfold
    events, current = [], None
    for line in text.split("\n"):
        if not line.strip():
            continue
        name_part, _, value = line.partition(":")
        name, _, params = name_part.partition(";")
        name = name.upper()
        if name == "BEGIN" and value.strip().upper() == "VEVENT":
            current = {"uid": "", "summary": "", "start": None, "description": "", "rrule": "", "note": ""}
        elif name == "END" and value.strip().upper() == "VEVENT":
            if current is not None:
                events.append(current)
            current = None
        elif current is not None:
            if name == "SUMMARY":
                current["summary"] = _unescape(value).strip()
            elif name == "DESCRIPTION":
                current["description"] = _unescape(value).strip()
            elif name == "UID":
                current["uid"] = value.strip()
            elif name == "RRULE":
                current["rrule"] = value.strip()
            elif name == "DTSTART":
                current["start"], current["note"] = _parse_dt(params, value)
    return events


# ── the local calendar ───────────────────────────────────────────────────
def upcoming_events(days: int = 30, now: datetime | None = None) -> list[dict]:
    """Pending tasks with a due time, and open Schedule-tab reminders, in the window."""
    now = now or datetime.now()
    end = now + timedelta(days=days)
    tracker = _tracker()
    events = []
    for t in tracker.get_pending_tasks(limit=500):
        if not t.due_date:
            continue
        try:
            start = datetime.fromisoformat(t.due_date)
        except ValueError:
            continue
        if now <= start <= end:
            events.append({"uid": f"mbuddy-task-{t.task_id}@mbuddy.local", "summary": t.title,
                           "start": start, "description": t.description or "", "rrule": ""})
    conn = sqlite3.connect(tracker.db_path, timeout=10)
    try:
        rows = conn.execute("SELECT id, title, trigger_time, recurring FROM reminders WHERE completed=0").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    for rid, title, trigger, recurring in rows:
        try:
            start = datetime.fromisoformat(trigger)
        except (TypeError, ValueError):
            continue
        rrule = {"daily": "FREQ=DAILY", "weekly": "FREQ=WEEKLY"}.get(recurring or "", "")
        if (now <= start <= end) or (rrule and start <= end):
            events.append({"uid": f"mbuddy-reminder-{rid}@mbuddy.local", "summary": title,
                           "start": start, "description": "", "rrule": rrule})
    events.sort(key=lambda e: e["start"])
    return events


def _days(args: dict, default: int) -> int:
    try:
        return max(1, min(366, int(args.get("days") or default)))
    except (TypeError, ValueError):
        return default


def list_calendar(args: dict) -> str:
    days = _days(args, 7)
    events = upcoming_events(days)
    if not events:
        return f"Nothing on the calendar in the next {days} days."
    lines = [f"- {e['start'].strftime('%a %d %b %H:%M')}: {e['summary']}"
             + (f" (repeats {e['rrule'].split('=')[1].lower()})" if e["rrule"] else "") for e in events[:25]]
    more = f"\n(+{len(events) - 25} more)" if len(events) > 25 else ""
    return f"Calendar, next {days} days ({len(events)}):\n" + "\n".join(lines) + more


def export_calendar(args: dict) -> str:
    days = _days(args, 30)
    events = upcoming_events(days)
    if not events:
        return fail(f"There's nothing on the calendar in the next {days} days to export.")
    folder = _out_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"mbuddy-calendar-{datetime.now().strftime('%Y%m%d-%H%M%S')}.ics"
    path.write_text(to_ics(events), encoding="utf-8", newline="")
    # Checked at the source: the file parses back to the same number of events.
    back = parse_ics(path.read_text(encoding="utf-8"))
    if len(back) != len(events):
        return fail(f"I wrote {path}, but it reads back with {len(back)} of {len(events)} events.")
    return f"Exported {len(events)} events to {path}."


def _import_path(args: dict) -> Path | str:
    raw = str(args.get("path", "")).strip().strip('"')
    if not raw:
        return "No file was given."
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if path.suffix.lower() != ".ics":
        return f"'{path.name}' isn't an .ics calendar file."
    if not path.is_file():
        return f"There's no file at {path}."
    if path.stat().st_size > MAX_IMPORT_BYTES:
        return f"{path.name} is larger than {MAX_IMPORT_BYTES // 1000} KB."
    return path


def _plan_import(path: Path, now: datetime | None = None) -> tuple[list[dict], list[str]]:
    """(events to add, notes on what was skipped or approximated)."""
    now = now or datetime.now()
    events = parse_ics(path.read_text(encoding="utf-8", errors="replace"))[:MAX_IMPORT_EVENTS]
    tracker = _tracker()
    existing = {(t.title.strip().lower(), (t.due_date or "")[:16]) for t in tracker.get_pending_tasks(limit=1000)}
    add, notes = [], []
    for e in events:
        title = e["summary"] or "(untitled event)"
        if e["start"] is None:
            notes.append(f"'{title}': {e['note'] or 'no start time'}, skipped")
            continue
        start = e["start"]
        if e["rrule"]:
            step = {"DAILY": timedelta(days=1), "WEEKLY": timedelta(weeks=1)}.get(
                (re.search(r"FREQ=(\w+)", e["rrule"]) or [None, ""])[1])
            if step:
                while start < now:
                    start += step
            notes.append(f"'{title}' repeats; only the next time was added")
        if start < now:
            notes.append(f"'{title}' is in the past, skipped")
            continue
        if (title.lower(), start.isoformat()[:16]) in existing:
            notes.append(f"'{title}' is already on the calendar, skipped")
            continue
        if e["note"] and "read as local" in e["note"]:
            notes.append(f"'{title}': {e['note']}")
        add.append({**e, "summary": title, "start": start})
    return add, notes


def import_calendar(args: dict) -> str:
    path = _import_path(args)
    if isinstance(path, str):
        return fail(path)
    add, notes = _plan_import(path)
    if not add:
        return fail(f"Nothing to import from {path.name}" + (": " + "; ".join(notes[:5]) if notes else "."))
    tracker = _tracker()
    made = [tracker.create_task(e["summary"], due_date=e["start"].isoformat(), context=e["description"][:500])
            for e in add]
    # Checked at the source: every new task reads back from the database.
    ids = {t.task_id for t in tracker.get_pending_tasks(limit=1000)}
    missing = [m.title for m in made if m.task_id not in ids]
    if missing:
        return fail(f"Added {len(made) - len(missing)} of {len(made)}; missing: {', '.join(missing[:5])}.")
    first = "; ".join(f"{e['summary']} ({e['start'].strftime('%a %d %b %H:%M')})" for e in add[:5])
    tail = f" Notes: {'; '.join(notes[:5])}." if notes else ""
    return f"Imported {len(made)} events from {path.name}: {first}.{tail}"


def _preview_export(args: dict) -> str:
    days = _days(args, 30)
    try:
        n = len(upcoming_events(days))
    except Exception:
        n = "your"
    return f"save {n} calendar events from the next {days} days as an .ics file in {_out_dir()}"


def _preview_import(args: dict) -> str:
    path = _import_path(args)
    if isinstance(path, str):
        return f"import calendar events ({path})"
    try:
        add, _notes = _plan_import(path)
    except Exception:
        return f"import the events in {path.name} as reminders"
    names = ", ".join(e["summary"] for e in add[:3]) + (" ..." if len(add) > 3 else "")
    return f"add {len(add)} reminders from {path.name}" + (f": {names}" if add else "")


ABILITIES = [
    Ability(
        name="calendar_list",
        topic="calendar",
        description="List what's on the user's local calendar (reminders and dated tasks) for the next days.",
        params={"days": {"type": "integer", "description": "how many days ahead, default 7"}},
        risk="read",
        execute=list_calendar,
    ),
    Ability(
        name="calendar_export_ics",
        topic="calendar",
        description="Save the upcoming calendar as an .ics file other calendar apps can import.",
        params={"days": {"type": "integer", "description": "how many days ahead, default 30"}},
        risk="low",
        execute=export_calendar,
        preview=_preview_export,
    ),
    Ability(
        name="calendar_import_ics",
        topic="calendar",
        description="Add the events in an .ics file to the user's calendar as reminders.",
        params={"path": {"type": "string", "description": "full path of the .ics file"}}, required=["path"],
        risk="low",
        execute=import_calendar,
        preview=_preview_import,
    ),
]
