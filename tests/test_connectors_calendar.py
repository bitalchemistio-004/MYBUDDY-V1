"""Calendar .ics connector against a temporary task database and a temporary folder."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import brain.connectors as conn
import brain.connectors.calendar_ics as cal
import brain.task_tracker as tt
from brain.tools import ToolFailure


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    t = tt.TaskTracker(db_path=tmp_path / "buddy.db")
    monkeypatch.setattr(tt, "task_tracker", t)
    monkeypatch.setattr(cal, "OUT_DIR", tmp_path / "out")
    return t


def _soon(hours=2):
    return (datetime.now() + timedelta(hours=hours)).replace(second=0, microsecond=0)


def test_ics_round_trip_keeps_text_and_time():
    start = datetime(2026, 10, 1, 17, 30)
    text = cal.to_ics([{"uid": "a@x", "summary": "Dentist, room 4; bring card", "start": start,
                        "description": "line one\nline two \\ done"}])
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    [e] = cal.parse_ics(text)
    assert e["summary"] == "Dentist, room 4; bring card"
    assert e["description"] == "line one\nline two \\ done"
    assert e["start"] == start and e["uid"] == "a@x"


def test_long_lines_are_folded_and_unfolded():
    title = "A very long meeting title " * 8
    text = cal.to_ics([{"uid": "b@x", "summary": title, "start": datetime(2026, 10, 2, 9)}])
    assert "\r\n " in text
    assert cal.parse_ics(text)[0]["summary"] == title.strip()


def test_parse_date_forms():
    utc = datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc)
    text = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:utc\nDTSTART:20261003T080000Z\nEND:VEVENT\n"
            "BEGIN:VEVENT\nSUMMARY:allday\nDTSTART;VALUE=DATE:20261004\nEND:VEVENT\n"
            "BEGIN:VEVENT\nSUMMARY:zoned\nDTSTART;TZID=Romance Standard Time:20261005T100000\nEND:VEVENT\n"
            "BEGIN:VEVENT\nSUMMARY:broken\nDTSTART:tomorrow\nEND:VEVENT\nEND:VCALENDAR\n")
    e = {x["summary"]: x for x in cal.parse_ics(text)}
    assert e["utc"]["start"] == utc.astimezone().replace(tzinfo=None)
    assert e["allday"]["start"] == datetime(2026, 10, 4, 9, 0) and "all-day" in e["allday"]["note"]
    assert e["zoned"]["start"] == datetime(2026, 10, 5, 10, 0) and "read as local" in e["zoned"]["note"]
    assert e["broken"]["start"] is None


def test_list_shows_dated_tasks_and_reminders(tracker):
    tracker.create_task("Call mum", due_date=_soon().isoformat())
    tracker.create_task("no date task")
    with sqlite3.connect(tracker.db_path) as db:
        db.execute("INSERT INTO reminders (title, trigger_time, recurring) VALUES (?, ?, ?)",
                   ("Stretch", _soon(3).isoformat(), "daily"))
    out = conn.run("calendar_list", {"days": 2})
    assert "Call mum" in out and "Stretch" in out and "repeats daily" in out
    assert "no date task" not in out


def test_list_empty(tracker):
    assert "Nothing on the calendar" in conn.run("calendar_list")


def test_export_writes_a_file_that_parses_back(tracker, tmp_path):
    tracker.create_task("Call mum", due_date=_soon().isoformat())
    tracker.create_task("Gym", due_date=_soon(26).isoformat())
    out = conn.run("calendar_export_ics", {"days": 7})
    assert out.startswith("Exported 2 events to ")
    files = list((tmp_path / "out").glob("*.ics"))
    assert len(files) == 1
    events = cal.parse_ics(files[0].read_text(encoding="utf-8"))
    assert {e["summary"] for e in events} == {"Call mum", "Gym"}


def test_export_with_nothing_is_a_failure(tracker, tmp_path):
    assert isinstance(conn.run("calendar_export_ics"), ToolFailure)
    assert not (tmp_path / "out").exists()


def test_import_adds_future_events_and_skips_the_rest(tracker, tmp_path):
    future = _soon(5)
    tracker.create_task("Already there", due_date=_soon(6).isoformat())
    ics = cal.to_ics([
        {"uid": "1", "summary": "Team call", "start": future, "description": "agenda"},
        {"uid": "2", "summary": "Old thing", "start": datetime.now() - timedelta(days=3)},
        {"uid": "3", "summary": "Already there", "start": _soon(6)},
        {"uid": "4", "summary": "Standup", "start": datetime.now() - timedelta(days=2, hours=-1), "rrule": "FREQ=DAILY"},
    ])
    path = tmp_path / "in.ics"
    path.write_text(ics, encoding="utf-8")
    preview = conn.get("calendar_import_ics").describe({"path": str(path)})
    assert preview.startswith("add 2 reminders from in.ics")
    out = conn.run("calendar_import_ics", {"path": str(path)})
    assert out.startswith("Imported 2 events") and "Team call" in out and "Standup" in out
    assert "in the past" in out and "already on the calendar" in out and "only the next time" in out
    titles = [t.title for t in tracker.get_pending_tasks()]
    assert sorted(titles) == ["Already there", "Standup", "Team call"]
    standup = next(t for t in tracker.get_pending_tasks() if t.title == "Standup")
    assert datetime.fromisoformat(standup.due_date) >= datetime.now()


def test_import_refuses_bad_paths(tracker, tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    for p in ["", str(tmp_path / "missing.ics"), str(tmp_path / "notes.txt")]:
        out = conn.run("calendar_import_ics", {"path": p}) if p else cal.import_calendar({"path": p})
        assert isinstance(out, ToolFailure)
    assert tracker.get_pending_tasks() == []


def test_import_with_nothing_new_is_a_failure(tracker, tmp_path):
    path = tmp_path / "old.ics"
    path.write_text(cal.to_ics([{"uid": "1", "summary": "Gone", "start": datetime(2020, 1, 1, 9)}]), encoding="utf-8")
    out = conn.run("calendar_import_ics", {"path": str(path)})
    assert isinstance(out, ToolFailure) and "in the past" in out
