"""The day connector: plan, time report, find, habits, distractions. Facts only; nothing here talks to a model."""
import json
from datetime import date, datetime, timedelta

import pytest

from brain.connectors import day


@pytest.fixture()
def activity_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_ACTIVITY_DIR", str(tmp_path / "act"))
    from core import activity
    activity.set_settings({"tracking": True})
    return activity


def _put_day(activity, d, apps, active_s, slots=None):
    data = activity.load_day(d)
    data["apps"] = {k: {"name": n, "s": s} for k, (n, s) in apps.items()}
    data["active_s"] = active_s
    data["slots"] = slots or {}
    activity.save_day(data)


def test_time_report_reads_the_tracker_and_is_honest_about_websites(activity_dir):
    _put_day(activity_dir, date.today(), {"chrome": ("Google Chrome", 4800), "code": ("Visual Studio Code", 3240), "tiny": ("Tiny", 20)}, 8100)
    out = day.time_report({})
    assert "active 2h15" in out and "Google Chrome 1h20" in out and "Visual Studio Code 54 min" in out and "Tiny" not in out
    assert "websites" in out
    assert "Google Chrome 1h20" in day.time_report({"app": "chrome"})
    miss = day.time_report({"app": "youtube"})
    assert "no time recorded in anything called 'youtube'" in miss and "Google Chrome" in miss


def test_time_report_says_so_when_tracking_is_off(activity_dir):
    activity_dir.set_settings({"tracking": False})
    assert str(day.time_report({})).startswith("FAILED") and "switched off" in str(day.time_report({}))


def test_free_windows_are_the_gaps_between_events():
    d = datetime(2026, 10, 13)
    ev = [(d.replace(hour=11), 60), (d.replace(hour=14), 60)]
    free = day._free_windows(ev, d.replace(hour=9), d.replace(hour=18))
    assert [(a.hour, b.hour) for a, b in free] == [(9, 11), (12, 14), (15, 18)]
    short = day._free_windows([(d.replace(hour=9, minute=30), 60)], d.replace(hour=9), d.replace(hour=11))
    assert short == []                                             # 30 minutes before and 30 after: no 45-minute gap


def test_plan_day_hands_over_only_real_facts(monkeypatch):
    from brain.connectors import calendar_ics
    now = datetime(2026, 10, 13, 10, 0)
    monkeypatch.setattr(calendar_ics, "upcoming_events", lambda days=1, now=None: [
        {"summary": "Design review", "start": datetime(2026, 10, 13, 14, 0), "description": "", "rrule": ""}])
    out = day.plan_day({}, now=now)
    assert "Design review at 14:00" in out and "FACTS FOR PLANNING" in out and "10:00 to 14:00" in out and "15:00 to 18:00" in out


def test_find_anything_searches_tasks_and_says_when_nothing(monkeypatch, tmp_path):
    # its own task database: this test used to add a task to the user's real one on every run (and complete it,
    # which can award reward points), leaving "Zzqxv call the plumber" rows behind
    import brain.task_tracker as tt
    from brain import brain_store
    tracker = tt.TaskTracker(db_path=tmp_path / "buddy.db")
    monkeypatch.setattr(tt, "task_tracker", tracker)
    monkeypatch.setattr(brain_store, "search_notes", lambda q: "Nothing in my notes about that.")
    monkeypatch.setattr(day, "past_conversations", lambda q, limit=5: [])
    tracker.create_task("Zzqxv call the plumber")
    assert "Zzqxv call the plumber" in day.find_anything({"query": "plumber zzqxv"})
    assert day.find_anything({"query": "qqqqnotathing"}).startswith("Nothing matches")


def test_habits_count_real_days(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_HABITS_PATH", str(tmp_path / "h.json"))
    today = date.today()
    assert day.streak([today.isoformat(), (today - timedelta(days=1)).isoformat(), (today - timedelta(days=3)).isoformat()]) == 2
    assert day.streak([(today - timedelta(days=1)).isoformat()]) == 1       # today is not over: yesterday still counts
    assert day.streak([(today - timedelta(days=2)).isoformat()]) == 0
    assert "Marked 'a walk' for today. Streak: 1 day." in day.habit_log({"name": "a walk"})
    assert "already marked" in day.habit_log({"name": "A walk"})
    assert "a walk: 1-day streak, done today" in day.habit_status({})
    assert json.loads((tmp_path / "h.json").read_text())["a walk"]["days"] == [today.isoformat()]


def test_the_distraction_list_marks_programs_off_and_closes_nothing(activity_dir):
    _put_day(activity_dir, date.today(), {"discord": ("Discord", 600)}, 600)
    assert "Nothing is on your distraction list" in day.distraction_list({"action": "list"})
    assert "Added Discord" in day.distraction_list({"action": "add", "app": "discord"})
    assert activity_dir.label_of("discord") == "off"
    assert "Discord" in day.distraction_list({"action": "list"}) and "do not close anything" in day.distraction_list({"action": "list"})
    assert str(day.distraction_list({"action": "add", "app": "nosuchapp"})).startswith("FAILED")
    assert "Removed Discord" in day.distraction_list({"action": "remove", "app": "Discord"})
    assert activity_dir.label_of("discord") == "unknown"


def test_a_reminder_can_repeat_by_voice_and_be_stopped(tmp_path, monkeypatch):
    from brain import agent
    from brain.task_tracker import TaskTracker
    tracker = TaskTracker(tmp_path / "t.db")
    monkeypatch.setattr("brain.task_tracker.task_tracker", tracker)
    out = agent._reminder({"text": "drink water", "repeat": "hourly"}, {})
    assert "every hour" in out and "Reminder set: drink water" in out
    # it fires, and moves on by an hour instead of finishing
    later = datetime.now() + timedelta(hours=1, minutes=1)
    fired = tracker.fire_due_reminders(later)
    assert [f["title"] for f in fired if f["source"] == "reminder"] == ["drink water"]
    assert tracker.fire_due_reminders(later) == []                                    # not twice
    from brain.connectors import day as d
    assert "Stopped: drink water" in d.stop_repeating_reminder({"about": "water"})
    assert "found no repeating reminder" in d.stop_repeating_reminder({"about": "water"})
    assert "I can repeat a reminder every hour, day or week" in str(agent._reminder({"text": "x", "repeat": "yearly"}, {}))
