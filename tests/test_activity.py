"""Today: the sampler's rules with a fake clock (core/activity.py)."""
import os
import time
from datetime import datetime

import pytest

from core import activity as act


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MBUDDY_ACTIVITY_DIR", str(tmp_path / "activity"))
    monkeypatch.setattr(act, "TRACKER", act.Tracker())
    return tmp_path


def at(h, m=0, s=0):
    return datetime(2026, 9, 30, h, m, s).timestamp()


def run(tracker, start, seconds, front, idle=1.0, cfg=None):
    t = start
    end = start + seconds
    while t < end:
        tracker.tick(t, front, idle, cfg or act.settings())
        t += act.SAMPLE_S
    return t


APP = ("editor", "Code editor", False)
VID = ("player", "Video player", False)


def test_nothing_is_sampled_or_asked_by_default():
    assert act.settings()["tracking"] is False
    assert act.pending_checkin() is None


def test_time_is_added_to_the_program_in_front_and_idle_time_is_not():
    t = act.Tracker()
    run(t, at(9), 600, APP)
    run(t, at(9, 10), 600, APP, idle=act.IDLE_S + 5)
    t.flush()
    day = act.load_day(datetime(2026, 9, 30).date())
    assert 590 <= day["apps"]["editor"]["s"] <= 610
    assert day["active_s"] < 700


def test_unknown_programs_count_for_nothing_until_answered():
    t = act.Tracker()
    run(t, at(9), 1800, APP)
    t.flush()
    s = act.summary(datetime(2026, 9, 30).date())
    assert s["score"] is None and s["unknown_s"] >= 1700 and s["work_s"] == 0


def test_the_checkin_waits_for_a_long_stretch_and_is_rate_limited():
    t = act.Tracker()
    cfg = {**act.DEFAULTS, "tracking": True, "checkin_minutes": 40}
    end = run(t, at(9), 39 * 60, VID, cfg=cfg)
    assert t.pending is None
    run(t, end, 3 * 60, VID, cfg=cfg)
    assert t.pending and t.pending["key"] == "player" and t.pending["minutes"] >= 40
    first = t.pending["id"]
    t.answer(first, "break", now=end + 200)
    run(t, end + 200, 20 * 60, VID, cfg=cfg)
    assert t.pending is None                      # inside the 45-minute gap and the break


def test_full_screen_and_switched_off_stay_quiet():
    t = act.Tracker()
    cfg = {**act.DEFAULTS, "tracking": True}
    run(t, at(9), 60 * 60, ("game", "A game", True), cfg=cfg)
    assert t.pending is None
    t2 = act.Tracker()
    run(t2, at(9), 60 * 60, VID, cfg={**cfg, "checkins": False})
    assert t2.pending is None


def test_answers_teach_which_programs_are_work_and_nothing_is_preset():
    assert act.label_of("editor") == "unknown" and act.label_of("player") == "unknown"
    act.learn("editor", "work")
    act.learn("player", "off")
    assert act.label_of("editor") == "work" and act.label_of("player") == "off"
    act.learn("player", "work")
    assert act.label_of("player") == "mixed"
    act.set_label("player", "off")                # the user's own word wins
    assert act.label_of("player") == "off"
    act.set_label("player", "unknown")
    assert act.label_of("player") == "unknown"


def test_a_work_program_is_never_asked_about_and_working_quiets_the_program():
    t = act.Tracker()
    cfg = {**act.DEFAULTS, "tracking": True, "checkin_minutes": 15}
    act.learn("editor", "work")
    run(t, at(9), 60 * 60, APP, cfg=cfg)
    assert t.pending is None
    end = run(t, at(11), 16 * 60, VID, cfg=cfg)
    assert t.pending
    t.answer(t.pending["id"], "work", now=end)
    run(t, end, 60 * 60, VID, cfg=cfg)
    assert t.pending is None


def test_the_focus_score_and_streak_come_from_learned_time_only():
    t = act.Tracker()
    cfg = {**act.DEFAULTS, "tracking": True, "checkins": False, "work_goal_min": 30}
    act.learn("editor", "work")
    act.learn("player", "off")
    end = run(t, at(9), 40 * 60, APP, cfg=cfg)
    run(t, end, 20 * 60, VID, cfg=cfg)
    t.flush()
    s = act.summary(datetime(2026, 9, 30).date(), cfg)
    assert s["score"] == 67 and s["streak_days"] >= 0
    assert s["apps"][0]["key"] == "editor" and s["apps"][0]["status"] == "work"
    assert s["timeline"] and s["timeline"][0]["status"] == "work"


def test_settings_are_validated_and_persist():
    assert act.set_settings({"tracking": True})["tracking"] is True
    assert act.settings()["tracking"] is True
    with pytest.raises(ValueError):
        act.set_settings({"checkin_minutes": 5})
    with pytest.raises(ValueError):
        act.set_settings({"tracking": "yes"})
    with pytest.raises(ValueError):
        act.set_settings({"nope": 1})


def test_mbuddys_own_window_from_older_days_is_not_shown():
    day = act.load_day(datetime(2026, 9, 30).date())
    day["apps"] = {"mbuddy-shell": {"name": "MBUDDY", "s": 900}, "editor": {"name": "Code editor", "s": 600}}
    day["active_s"] = 1500
    act.save_day(day)
    s = act.summary(datetime(2026, 9, 30).date())
    assert [a["key"] for a in s["apps"]] == ["editor"]
