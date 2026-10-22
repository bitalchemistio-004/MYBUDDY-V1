"""Due-reminder detection, ported from the legacy desktop app into the backend."""
import sqlite3
from datetime import datetime, timedelta

from brain.task_tracker import TaskTracker
from core.status import RuntimeStatus


NOW = datetime(2026, 9, 26, 12, 0, 0)


def _tracker(tmp_path):
    return TaskTracker(db_path=tmp_path / "buddy.db")


def _add_reminder(tracker, title, when, recurring=None):
    conn = sqlite3.connect(tracker.db_path)
    cur = conn.execute(
        "INSERT INTO reminders (title, trigger_time, recurring, completed) VALUES (?, ?, ?, 0)",
        (title, when.isoformat(), recurring),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def _reminder_row(tracker, rid):
    conn = sqlite3.connect(tracker.db_path)
    row = conn.execute("SELECT trigger_time, completed FROM reminders WHERE id=?", (rid,)).fetchone()
    conn.close()
    return row


def test_one_shot_reminder_fires_once_and_completes(tmp_path):
    t = _tracker(tmp_path)
    rid = _add_reminder(t, "call mom", NOW - timedelta(minutes=1))

    fired = t.fire_due_reminders(now=NOW)
    assert [f["title"] for f in fired] == ["call mom"]
    assert fired[0]["id"] == f"reminder:{rid}"
    assert fired[0]["source"] == "reminder"
    assert _reminder_row(t, rid)[1] == 1
    assert t.fire_due_reminders(now=NOW) == []


def test_future_items_do_not_fire(tmp_path):
    t = _tracker(tmp_path)
    _add_reminder(t, "later", NOW + timedelta(hours=1))
    t.create_task("later task", due_date=(NOW + timedelta(hours=1)).isoformat())
    assert t.fire_due_reminders(now=NOW) == []


def test_recurring_reminder_after_downtime_fires_once_and_moves_to_future(tmp_path):
    t = _tracker(tmp_path)
    # Three days missed while the PC was off: one notification, not three,
    # and the next occurrence must land in the future (the legacy app added
    # a single day, which left it overdue and firing on every poll).
    rid = _add_reminder(t, "water plants", NOW - timedelta(days=3, minutes=5), recurring="daily")

    fired = t.fire_due_reminders(now=NOW)
    assert len(fired) == 1
    trigger, completed = _reminder_row(t, rid)
    assert completed == 0
    next_time = datetime.fromisoformat(trigger)
    assert NOW < next_time <= NOW + timedelta(days=1)
    assert t.fire_due_reminders(now=NOW) == []


def test_weekly_reminder_advances_by_weeks(tmp_path):
    t = _tracker(tmp_path)
    start = NOW - timedelta(days=1)
    rid = _add_reminder(t, "review week", start, recurring="weekly")
    t.fire_due_reminders(now=NOW)
    assert datetime.fromisoformat(_reminder_row(t, rid)[0]) == start + timedelta(weeks=1)


def test_voice_task_with_due_time_fires_exactly_once(tmp_path):
    t = _tracker(tmp_path)
    task = t.create_task("take medicine", due_date=(NOW - timedelta(minutes=2)).isoformat())

    fired = t.fire_due_reminders(now=NOW)
    assert fired == [{
        "id": f"task:{task.task_id}",
        "title": "take medicine",
        "due": task.due_date,
        "source": "task",
    }]
    assert t.fire_due_reminders(now=NOW) == []


def test_rescheduled_task_fires_again(tmp_path):
    t = _tracker(tmp_path)
    task = t.create_task("stretch", due_date=(NOW - timedelta(minutes=2)).isoformat())
    t.fire_due_reminders(now=NOW)

    new_due = (NOW + timedelta(minutes=30)).isoformat()
    conn = sqlite3.connect(t.db_path)
    conn.execute("UPDATE tasks SET due_date=? WHERE id=?", (new_due, task.task_id))
    conn.commit()
    conn.close()

    assert t.fire_due_reminders(now=NOW + timedelta(minutes=31))[0]["due"] == new_due


def test_completed_task_does_not_fire(tmp_path):
    t = _tracker(tmp_path)
    task = t.create_task("done already", due_date=(NOW - timedelta(minutes=2)).isoformat())
    t.complete_task(task.task_id)
    assert t.fire_due_reminders(now=NOW) == []


def test_runtime_status_keeps_recent_fired_reminders_bounded():
    status = RuntimeStatus()
    for i in range(30):
        status.record_fired_reminders([{"id": f"task:{i}", "title": str(i), "due": "", "source": "task"}])
    snap = status.snapshot()
    assert len(snap["fired_reminders"]) == 20
    assert snap["fired_reminders"][-1]["id"] == "task:29"
    assert all("fired_at" in r for r in snap["fired_reminders"])


def test_reminder_announcement_is_one_natural_sentence():
    from brain.task_tracker import reminder_announcement

    one = [{"id": "task:1", "title": "stretch", "source": "task"}]
    two = one + [{"id": "reminder:4", "title": "call mom", "source": "reminder"}]
    assert reminder_announcement([]) == ""
    assert reminder_announcement(one) == "[NORMAL] Hey, reminder: stretch."
    assert reminder_announcement(two) == "[NORMAL] Hey, two reminders: stretch, and call mom."
