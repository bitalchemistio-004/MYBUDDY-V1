from datetime import datetime

import pytest

from core import activity, focus


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_ACTIVITY_DIR", str(tmp_path / "a"))
    monkeypatch.setattr(activity, "TRACKER", activity.Tracker())
    monkeypatch.setattr(focus, "_last_nudge", 0.0)


T0 = datetime(2026, 9, 30, 10, 0).timestamp()


def test_a_sprint_runs_ends_by_itself_and_counts_only_what_ran():
    cur = focus.start(25, now=T0)
    assert cur["remaining_s"] == 1500
    assert focus.current(T0 + 600)["remaining_s"] == 900
    with pytest.raises(ValueError):
        focus.start(25, now=T0 + 10)
    assert focus.current(T0 + 1501) is None
    done = focus.just_finished(T0 + 1502)
    assert done and done["completed"] and done["focused_s"] == 1500
    assert focus.just_finished(T0 + 1502 + 60) is None            # the buddy celebrates once


def test_stopping_early_counts_the_minutes_that_were_really_focused():
    focus.start(45, now=T0)
    rec = focus.stop(now=T0 + 600)
    assert rec["completed"] is False and rec["focused_s"] == 600
    w = focus.week(datetime(2026, 9, 30).date())
    assert w[-1]["minutes"] == 10 and w[-1]["sprints"] == 1 and w[-1]["completed"] == 0
    assert focus.stop(now=T0 + 700) is None


def test_lengths_are_validated():
    for bad in (1, 500, "25", True, None):
        with pytest.raises(ValueError):
            focus.start(bad, now=T0)


def test_the_nudge_needs_a_sprint_tracking_and_a_program_the_user_called_not_work():
    activity.set_settings({"tracking": True})
    t = activity.TRACKER
    activity.learn("player", "off")
    t.run_key, t.run_start = "player", T0
    t._day = {"date": "2026-09-30", "active_s": 0, "apps": {"player": {"name": "Video player", "s": 1}}, "slots": {}}
    assert focus.nudge(T0 + 120) is None                          # no sprint running
    focus.start(25, now=T0)
    assert focus.nudge(T0 + 30) is None                           # not long enough yet
    n = focus.nudge(T0 + 120)
    assert n and n["app"] == "Video player"
    assert focus.nudge(T0 + 130) is None                          # rate limited
    focus.set_settings({"nudge": False})
    assert focus.nudge(T0 + 1000) is None
