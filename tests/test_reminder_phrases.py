"""Relative reminders the way people say them (live failure, 2026-09-26).

"Remind me after one minute to do nothing." picked no tool and the model
replied that it had no clock. The parser only knew digit amounts after the
task ("remind me to X in 5 minutes").
"""
from datetime import datetime, timedelta

import pytest

from brain.cognitive_state import decide_behavior
from brain.task_tracker import parse_task_from_message, parse_time_reference


def _minutes_from_now(iso: str) -> float:
    return (datetime.fromisoformat(iso) - datetime.now()).total_seconds() / 60


@pytest.mark.parametrize("ref, minutes", [
    ("in one minute", 1), ("after one minute", 1), ("in 5 minutes", 5),
    ("in an hour", 60), ("after 2 hours", 120), ("in half an hour", 30),
    ("in fifteen minutes", 15), ("in a couple of minutes", 2), ("in 10 mins", 10),
])
def test_relative_times(ref, minutes):
    due = parse_time_reference(ref)
    assert due is not None, ref
    assert abs(_minutes_from_now(due) - minutes) < 0.1


@pytest.mark.parametrize("message, task, minutes", [
    ("Remind me after one minute to do nothing.", "do nothing", 1),
    ("Remind me in 5 minutes to check the oven", "check the oven", 5),
    ("remind me in an hour to stretch", "stretch", 60),
    ("remind me to call mom in 10 minutes", "call mom", 10),
    ("Remind me to drink water after twenty minutes.", "drink water", 20),
])
def test_reminder_messages(message, task, minutes):
    parsed = parse_task_from_message(message)
    assert parsed is not None, message
    assert parsed["description"] == task
    assert abs(_minutes_from_now(parsed["due_date"]) - minutes) < 0.1


class _Cog:
    """The fields decide_behavior reads, for a conversation turn."""
    def __getattr__(self, name):
        return None


def test_remind_me_after_picks_the_task_tool():
    from brain.cognitive_state import build_cognitive_state
    from brain.world_model import build_world_model
    from core.state_manager import MBUDDYState
    from core.status import runtime_status

    text = "Remind me after one minute to do nothing."
    state = MBUDDYState()
    wm = build_world_model(state, session=None, runtime=runtime_status, memory={}, event="thinking", text=text)
    cs = build_cognitive_state(state, session=None, runtime=runtime_status, memory={}, world_model=wm)
    assert decide_behavior(cs, event="thinking", text=text).tool == "task"


@pytest.mark.parametrize("ref", ["at 17:00", "17:00", "5 pm", "at 5pm", "before 17:00", "tomorrow 9am"])
def test_clock_times_the_agent_produces(ref):
    # The tool-calling model writes times like these for add_reminder.
    due = parse_time_reference(ref)
    assert due is not None, ref
    dt = datetime.fromisoformat(due)
    assert dt > datetime.now() - timedelta(minutes=1)
    if "before" in ref:
        assert (dt.hour, dt.minute) < (17, 0) or dt.date() > datetime.now().date()
    elif "tomorrow" in ref:
        assert (dt.hour, dt.minute) == (9, 0)
    else:
        assert (dt.hour, dt.minute) == (17, 0)


# Live 2026-09-27: "plan on my calendar MATE90RELASE on 1 october" failed
# because calendar dates weren't understood; the reply then called it "not real".
def _at(iso):
    return datetime.fromisoformat(iso)


def test_calendar_dates(monkeypatch):
    import brain.task_tracker as tt

    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 27, 15, 0)

    monkeypatch.setattr(tt, "datetime", Fixed)
    p = tt.parse_time_reference
    assert _at(p("on 1 october")) == datetime(2026, 10, 1, 9, 0)
    assert _at(p("October 1st")) == datetime(2026, 10, 1, 9, 0)
    assert _at(p("the 1st of oct at 5pm")) == datetime(2026, 10, 1, 17, 0)
    assert _at(p("Oct 1 at 17:30")) == datetime(2026, 10, 1, 17, 30)
    assert _at(p("2026-10-01")) == datetime(2026, 10, 1, 9, 0)
    assert _at(p("1 october 2027")) == datetime(2027, 10, 1, 9, 0)
    assert _at(p("on the 5th")) == datetime(2026, 10, 5, 9, 0)   # passed this month: next month
    assert _at(p("3 march")) == datetime(2027, 3, 3, 9, 0)      # passed this year: next year
    assert p("31 february") is None and p("dinner with sam") is None


def test_the_reminder_tool_accepts_a_calendar_date(tmp_path, monkeypatch):
    from brain import agent
    from brain.task_tracker import TaskTracker
    import brain.task_tracker as tt
    monkeypatch.setattr(tt, "task_tracker", TaskTracker(db_path=tmp_path / "t.db"))
    out = agent._reminder({"text": "MATE90RELASE", "when": "on 1 october"}, {})
    assert out.startswith("Reminder set: MATE90RELASE on ") and "01 Oct" in out
