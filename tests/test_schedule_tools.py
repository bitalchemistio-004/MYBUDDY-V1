"""Reminders and tasks that do what was asked (the field test, 2026-10-19).

Every reminder became daily because `repeat` was required and had no "once"; "mark it done" added a task
called "(done)"; "move it to 7pm" added a duplicate. These tests hold the fixes."""
import sqlite3
from datetime import datetime

import pytest

from brain import agent
from brain.task_tracker import TaskTracker
from brain.tools import ToolFailure
import brain.task_tracker as tt


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    t = TaskTracker(db_path=tmp_path / "buddy.db")
    monkeypatch.setattr(tt, "task_tracker", t)
    return t


def rows(t, sql):
    con = sqlite3.connect(t.db_path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_repeat_offers_once_and_once_is_one_reminder(tracker):
    spec = next(x for x in agent.TOOLS if x["function"]["name"] == "add_reminder")
    assert spec["function"]["parameters"]["properties"]["repeat"]["enum"][0] == "once"
    out = agent._reminder({"text": "call the supplier", "when": "tomorrow 6pm", "repeat": "once"}, None)
    assert not isinstance(out, ToolFailure) and "every" not in out
    assert rows(tracker, "select count(*) from reminders")[0][0] == 0             # nothing repeating
    assert rows(tracker, "select title from tasks where completed = 0") == [("call the supplier",)]


def test_daily_still_repeats_when_asked(tracker):
    out = agent._reminder({"text": "take my pills", "when": "tomorrow 8am", "repeat": "daily"}, None)
    assert "every day" in out
    assert rows(tracker, "select recurring from reminders") == [("daily",)]


def test_mark_done_completes_the_task_and_adds_nothing(tracker):
    tracker.create_task("send the Q4 budget deck to finance")
    out = agent._complete_task({"about": "Q4 budget deck"}, None)
    assert out.startswith("Marked done")
    assert rows(tracker, "select title, completed from tasks") == [("send the Q4 budget deck to finance", 1)]


def test_mark_done_asks_when_two_tasks_match(tracker):
    tracker.create_task("call the Dubai client")
    tracker.create_task("call the Cairo client")
    out = agent._complete_task({"about": "call client"}, None)
    assert isinstance(out, ToolFailure) and "Which one" in str(out)
    assert rows(tracker, "select count(*) from tasks where completed = 1")[0][0] == 0


def test_move_a_reminder_changes_it_in_place(tracker):
    agent._reminder({"text": "call the supplier", "when": "tomorrow 6pm", "repeat": "daily"}, None)
    out = agent._change_reminder({"about": "my supplier reminder", "when": "tomorrow 7pm", "repeat": "keep"}, None)
    assert out.startswith("Changed") and "19:00" in out and "every day" in out
    open_rows = rows(tracker, "select trigger_time, recurring from reminders where completed = 0")
    assert len(open_rows) == 1 and open_rows[0][0].endswith("19:00:00") and open_rows[0][1] == "daily"


def test_only_once_turns_a_daily_reminder_into_one_and_cleans_duplicates(tracker):
    for _ in range(3):           # what the field test left behind: three daily copies
        agent._reminder({"text": "call the supplier", "when": "tomorrow 6pm", "repeat": "daily"}, None)
    out = agent._change_reminder({"about": "supplier", "when": "", "repeat": "once"}, None)
    assert "once" in out and "removed 2 duplicates" in out
    assert rows(tracker, "select count(*) from reminders where completed = 0")[0][0] == 0
    assert rows(tracker, "select title from tasks where completed = 0") == [("call the supplier",)]


def test_change_reports_what_is_stored_not_what_was_meant(tracker, monkeypatch):
    agent._reminder({"text": "exam", "when": "tomorrow 9am", "repeat": "once"}, None)
    monkeypatch.setattr(tracker, "get_open", lambda kind, i: None)       # the read-back fails
    out = agent._change_reminder({"about": "exam", "when": "tomorrow 10am", "repeat": "keep"}, None)
    assert isinstance(out, ToolFailure)


def test_a_weekday_keeps_its_time_of_day():
    # live 2026-10-20: "Friday at 3pm" was set for Friday 09:00
    from brain.task_tracker import parse_time_reference
    for said, clock in (("Friday at 3pm", "15:00"), ("on friday at 3:30 pm", "15:30"), ("monday at 17:30", "17:30"),
                        ("next tuesday", "09:00")):
        due = parse_time_reference(said)
        assert due and due[11:16] == clock, (said, due)
