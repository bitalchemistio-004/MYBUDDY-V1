"""Finished timers are announced (they used to expire silently)."""
import json
import time

from brain.tools import TimerManager, timer_announcement


def test_pop_finished_returns_each_timer_once(tmp_path):
    tm = TimerManager(db_path=tmp_path / "timers.json")
    tm.start_timer("pasta", 1)
    tm.start_timer("laundry", 600)
    assert tm.pop_finished(now=time.time()) == []
    done = tm.pop_finished(now=time.time() + 2)
    assert [t.label for t in done] == ["pasta"]
    assert tm.pop_finished(now=time.time() + 2) == []
    assert [t.label for t in tm.list_timers()] == ["laundry"]


def test_timer_that_finished_while_off_is_announced_once(tmp_path):
    path = tmp_path / "timers.json"
    now = time.time()
    path.write_text(json.dumps([
        {"label": "tea", "duration_seconds": 60, "created_at": now - 300},     # done 4 min ago
        {"label": "ancient", "duration_seconds": 60, "created_at": now - 7200},  # done 2 h ago
    ]), encoding="utf-8")
    tm = TimerManager(db_path=path)
    assert [t.label for t in tm.pop_finished()] == ["tea"]


def test_timer_announcement():
    class T:
        def __init__(self, label, secs):
            self.label, self.duration_seconds = label, secs

    assert timer_announcement([]) == ""
    assert timer_announcement([T("pasta", 720)]) == "[NORMAL] Hey, your pasta timer is done."
    assert timer_announcement([T("120s timer", 120)]) == "[NORMAL] Hey, your 2 minute timer is done."
    assert timer_announcement([T("timer", 30)]) == "[NORMAL] Hey, your 30 second timer is done."
