"""Progress for real events only, paid once per key; streaks, levels and badges computed from the stored awards."""
from datetime import date, datetime, timedelta

import pytest

from core import rewards


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_REWARDS_PATH", str(tmp_path / "r.json"))
    rewards.reset()


def at(days_ago, hour=12):
    return datetime.combine(date.today() - timedelta(days=days_ago), datetime.min.time()).replace(hour=hour).timestamp()


def test_an_award_pays_once_per_key():
    a = rewards.award("task_done", "7", "Call Baba")
    assert a and a["xp"] == 10 and a["label"] == "Call Baba"
    assert rewards.award("task_done", "7") is None            # the same task again: nothing
    assert rewards.award("nonsense", "1") is None              # an unknown kind: nothing
    assert rewards.snapshot()["xp"] == 10 and rewards.snapshot()["totals"]["task_done"] == 1


def test_level_and_progress_inside_the_level():
    for i in range(31):
        rewards.award("task_done", str(i))
    s = rewards.snapshot()
    assert s["xp"] == 310 and s["level"] == 2 and s["into_level"] == 10 and s["per_level"] == 300


def test_streaks_count_consecutive_days_and_survive_an_unfinished_today():
    for back in (0, 1, 2, 4):
        rewards.award("habit", f"walk:{back}", now=at(back))
    s = rewards.snapshot()
    assert s["streak"] == 3 and s["best_streak"] == 3
    assert rewards.streak([(date.today() - timedelta(days=1)).isoformat()]) == 1       # today is not over
    assert rewards.streak([(date.today() - timedelta(days=2)).isoformat()]) == 0       # a missed day ends it


def test_badges_follow_the_counts():
    names = lambda: {b["id"] for b in rewards.snapshot()["badges"] if b["earned"]}
    assert names() == set()
    rewards.award("task_done", "1")
    assert names() == {"first_task"}
    rewards.award("focus_sprint", "s1")
    assert {"first_task", "focus_1"} <= names()
    for back in range(3):
        rewards.award("habit", f"h:{back}", now=at(back))
    assert "streak_3" in names()


def test_last_is_what_the_live_feed_shows_once():
    assert rewards.last() is None
    a = rewards.award("habit", "x:1", "Kept: reading")
    assert rewards.last() == {"id": a["id"], "kind": "habit", "xp": 8, "label": "Kept: reading", "at": a["at"]}
    b = rewards.award("task_done", "9")
    assert rewards.last()["id"] == b["id"] > a["id"]


def test_completing_a_task_and_a_focus_sprint_and_a_habit_really_pay(tmp_path, monkeypatch):
    from brain.task_tracker import TaskTracker
    t = TaskTracker(tmp_path / "t.db")
    task = t.create_task("write the report")
    assert t.complete_task(task.task_id) is True
    assert t.complete_task(task.task_id) is False               # already done: no second reward
    assert rewards.snapshot()["totals"]["task_done"] == 1
    from core import focus
    monkeypatch.setenv("MBUDDY_ACTIVITY_DIR", str(tmp_path / "act"))
    base = 1_800_000_000.0
    focus.start(25, now=base)
    focus.current(now=base + 25 * 60 + 1)                       # time is up: the sprint closes as completed
    assert rewards.snapshot()["totals"]["focus_sprint"] == 1
    focus.start(25, now=base + 5000)
    focus.stop(now=base + 5000 + 120)                           # stopped after two minutes: no reward
    assert rewards.snapshot()["totals"]["focus_sprint"] == 1
    from brain.connectors import day
    monkeypatch.setenv("MBUDDY_HABITS_PATH", str(tmp_path / "h.json"))
    day.habit_log({"name": "a walk"})
    day.habit_log({"name": "a walk"})                           # the same habit, the same day: once
    assert rewards.snapshot()["totals"]["habit"] == 1
