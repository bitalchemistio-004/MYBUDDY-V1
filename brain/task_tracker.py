"""
brain/task_tracker.py — Multi-turn planning and task tracking system.

Persistent task management with natural language parsing, reminders,
and completion tracking across sessions.

Keeps its tasks in ~/.buddy/database/buddy.db, the file the old Tkinter
desktop app (removed 2026-10-11, replaced by the shell/ app) also used, so
tasks made before the change are still there.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any

import utils.logger as log


TASKS_DB = Path.home() / ".buddy" / "database" / "buddy.db"

# The desktop app's schema uses 3 priority levels; voice/chat requests use a
# richer "urgent" concept for natural phrasing, mapped down to "high" so both
# surfaces stay on one shared vocabulary.
_PRIORITY_MAP = {"urgent": "high", "important": "high", "critical": "high"}


class Task:
    """Represents a tracked task, matching the desktop app's `tasks` table."""

    def __init__(
        self,
        task_id: int,
        title: str,
        created_at: str,
        completed: bool = False,
        due_date: Optional[str] = None,
        priority: str = "medium",
        description: str = "",
    ):
        self.task_id = task_id
        self.title = title
        self.created_at = created_at
        self.completed = completed
        self.due_date = due_date
        self.priority = priority
        self.description = description

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "created_at": self.created_at,
            "completed": self.completed,
            "due_date": self.due_date,
            "priority": self.priority,
        }


# how far a repeating reminder moves each time it fires
RECURRENCE = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "weekly": timedelta(weeks=1)}


class TaskTracker:
    """Manages tasks against the shared desktop-app SQLite database."""

    def __init__(self, db_path: Path = TASKS_DB):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """
        Ensure the shared `tasks` table exists.

        Uses IF NOT EXISTS with the desktop app's exact column set
        (database_service.py::DatabaseService._init_database) so whichever
        process starts first creates it and the other just attaches.
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                priority TEXT CHECK(priority IN ('high','medium','low')),
                due_date DATETIME,
                completed BOOLEAN DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status_priority_due ON tasks(completed, priority, due_date)")
        # The desktop app's reminders table, same columns, so reminders made
        # in its Schedule tab keep firing once delivery lives here.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                trigger_time DATETIME NOT NULL,
                recurring TEXT,
                completed BOOLEAN DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Which task due times have already been announced. Keyed by the due
        # time too, so a task moved to a new time is announced again.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_notifications (
                task_id INTEGER NOT NULL,
                due_date TEXT NOT NULL,
                fired_at TEXT NOT NULL,
                PRIMARY KEY (task_id, due_date)
            )
        """)

        conn.commit()
        conn.close()

    def fire_due_reminders(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        Claim every reminder and task whose time has come, and return them.

        Covers the Schedule tab's `reminders` table (one-shot, daily, weekly)
        and tasks with a due time, which is what "remind me at 5" by voice
        creates. Each item is returned once: a one-shot reminder is completed,
        a recurring one moves to its next occurrence *in the future* (so days
        missed while the PC was off give one notice, not one per poll), and a
        task's due time is recorded in `task_notifications`.

        Claims are conditional updates, so another process polling the same
        file cannot announce the same item twice.
        """
        now = now or datetime.now()
        now_iso = now.isoformat()
        fired: List[Dict[str, Any]] = []

        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            due_reminders = conn.execute(
                "SELECT id, title, trigger_time, recurring FROM reminders "
                "WHERE completed=0 AND trigger_time <= ? ORDER BY trigger_time",
                (now_iso,),
            ).fetchall()
            for rid, title, trigger, recurring in due_reminders:
                step = RECURRENCE.get(recurring or "")
                if step is None:
                    claim = conn.execute(
                        "UPDATE reminders SET completed=1 WHERE id=? AND completed=0 AND trigger_time=?",
                        (rid, trigger),
                    )
                else:
                    next_time = datetime.fromisoformat(trigger)
                    while next_time <= now:
                        next_time += step
                    claim = conn.execute(
                        "UPDATE reminders SET trigger_time=? WHERE id=? AND completed=0 AND trigger_time=?",
                        (next_time.isoformat(), rid, trigger),
                    )
                if claim.rowcount == 1:
                    fired.append({"id": f"reminder:{rid}", "title": title, "due": trigger, "source": "reminder"})

            due_tasks = conn.execute(
                "SELECT t.id, t.title, t.due_date FROM tasks t "
                "WHERE t.completed=0 AND t.due_date IS NOT NULL AND t.due_date <= ? "
                "AND NOT EXISTS (SELECT 1 FROM task_notifications n "
                "                WHERE n.task_id=t.id AND n.due_date=t.due_date) "
                "ORDER BY t.due_date",
                (now_iso,),
            ).fetchall()
            for tid, title, due in due_tasks:
                claim = conn.execute(
                    "INSERT OR IGNORE INTO task_notifications (task_id, due_date, fired_at) VALUES (?, ?, ?)",
                    (tid, due, now_iso),
                )
                if claim.rowcount == 1:
                    fired.append({"id": f"task:{tid}", "title": title, "due": due, "source": "task"})

            conn.commit()
        finally:
            conn.close()

        for item in fired:
            log.info(f"Reminder due: {item['id']} {item['title']}")
        return fired

    def _row_to_task(self, row) -> Task:
        return Task(
            task_id=row[0],
            title=row[1],
            description=row[2] or "",
            priority=row[3] or "medium",
            due_date=row[4],
            completed=bool(row[5]),
            created_at=row[6],
        )

    def create_recurring_reminder(self, title: str, first: datetime, recurring: str) -> int:
        """A reminder that repeats ("hourly", "daily" or "weekly"); returns its id. It is delivered by fire_due_reminders."""
        if recurring not in RECURRENCE:
            raise ValueError(f"repeat must be one of {', '.join(RECURRENCE)}")
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            cur = conn.execute("INSERT INTO reminders (title, trigger_time, recurring) VALUES (?, ?, ?)",
                               (title.strip()[:200], first.isoformat(), recurring))
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def stop_reminders(self, words: list[str]) -> list[str]:
        """Complete the open repeating reminders whose title has all of [words]; returns their titles."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            rows = conn.execute("SELECT id, title FROM reminders WHERE completed=0 AND recurring IS NOT NULL AND recurring != ''").fetchall()
            hit = [(i, t) for i, t in rows if all(w in t.lower() for w in words)] if words else []
            for i, _t in hit:
                conn.execute("UPDATE reminders SET completed=1 WHERE id=?", (i,))
            conn.commit()
            return [t for _i, t in hit]
        finally:
            conn.close()

    def find_open(self, words: list[str]) -> list[dict]:
        """Open tasks (a one-off reminder is a task with a time) and open repeating reminders whose title has all of
        [words]: [{"kind": "task"|"reminder", "id", "title", "when", "repeat"}]."""
        if not words:
            return []
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            tasks = conn.execute("SELECT id, title, due_date FROM tasks WHERE completed = 0").fetchall()
            rems = conn.execute("SELECT id, title, trigger_time, recurring FROM reminders WHERE completed = 0").fetchall()
        finally:
            conn.close()
        hit = lambda t: all(w in (t or "").lower() for w in words)
        return ([{"kind": "task", "id": i, "title": t, "when": d, "repeat": ""} for i, t, d in tasks if hit(t)]
                + [{"kind": "reminder", "id": i, "title": t, "when": w, "repeat": r or ""} for i, t, w, r in rems if hit(t)])

    def get_open(self, kind: str, item_id: int) -> Optional[dict]:
        """One open task or repeating reminder, read back from the database (None if it is not open)."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            if kind == "task":
                r = conn.execute("SELECT id, title, due_date FROM tasks WHERE id = ? AND completed = 0", (item_id,)).fetchone()
                return {"kind": "task", "id": r[0], "title": r[1], "when": r[2], "repeat": ""} if r else None
            r = conn.execute("SELECT id, title, trigger_time, recurring FROM reminders WHERE id = ? AND completed = 0", (item_id,)).fetchone()
            return {"kind": "reminder", "id": r[0], "title": r[1], "when": r[2], "repeat": r[3] or ""} if r else None
        finally:
            conn.close()

    def complete_reminder(self, reminder_id: int) -> bool:
        """Stop one repeating reminder by its id."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            n = conn.execute("UPDATE reminders SET completed = 1 WHERE id = ? AND completed = 0", (reminder_id,)).rowcount
            conn.commit()
            return n > 0
        finally:
            conn.close()

    def reschedule(self, item: dict, when: Optional[datetime], repeat: str) -> dict:
        """Change one open item's time and/or repeat in place. repeat: "" keeps it, "once" makes it one-off, or a
        RECURRENCE key. A repeating reminder made one-off becomes a timed task (and the other way round), so there is
        never a duplicate. Returns the item as it is now."""
        if repeat and repeat != "once" and repeat not in RECURRENCE:
            raise ValueError(f"repeat must be once or one of {', '.join(RECURRENCE)}")
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            if item["kind"] == "task":
                due = when.isoformat() if when else item["when"]
                if repeat and repeat != "once":
                    if not due:
                        raise ValueError("a repeating reminder needs a time")
                    conn.execute("UPDATE tasks SET completed = 1 WHERE id = ?", (item["id"],))
                    cur = conn.execute("INSERT INTO reminders (title, trigger_time, recurring) VALUES (?, ?, ?)", (item["title"], due, repeat))
                    conn.commit()
                    return {"kind": "reminder", "id": int(cur.lastrowid), "title": item["title"], "when": due, "repeat": repeat}
                conn.execute("UPDATE tasks SET due_date = ? WHERE id = ?", (due, item["id"]))
                conn.commit()
                return {**item, "when": due, "repeat": ""}
            trigger = when.isoformat() if when else item["when"]
            if repeat == "once":
                conn.execute("UPDATE reminders SET completed = 1 WHERE id = ?", (item["id"],))
                cur = conn.execute("INSERT INTO tasks (title, description, priority, due_date, completed, created_at) VALUES (?, '', 'medium', ?, 0, ?)",
                                   (item["title"], trigger, datetime.now().isoformat()))
                conn.commit()
                return {"kind": "task", "id": int(cur.lastrowid), "title": item["title"], "when": trigger, "repeat": ""}
            conn.execute("UPDATE reminders SET trigger_time = ?, recurring = ? WHERE id = ?", (trigger, repeat or item["repeat"], item["id"]))
            conn.commit()
            return {**item, "when": trigger, "repeat": repeat or item["repeat"]}
        finally:
            conn.close()

    def create_task(
        self,
        description: str,
        due_date: Optional[str] = None,
        priority: str = "normal",
        context: str = "",
    ) -> Task:
        """Create a new task. `description` becomes the task's title."""
        now = datetime.now().isoformat()
        priority = _PRIORITY_MAP.get(priority, priority)
        if priority not in ("high", "medium", "low"):
            priority = "medium"

        conn = sqlite3.connect(self.db_path, timeout=10)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO tasks (title, description, priority, due_date, completed, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
        """, (description, context, priority, due_date, now))

        task_id = cursor.lastrowid
        conn.commit()
        conn.close()

        log.info(f"Created task #{task_id}: {description}")

        return Task(
            task_id=task_id,
            title=description,
            description=context,
            due_date=due_date,
            priority=priority,
            created_at=now,
        )

    def get_pending_tasks(self, limit: int = 50) -> List[Task]:
        """Get all pending (not completed) tasks."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, title, description, priority, due_date, completed, created_at
            FROM tasks
            WHERE completed = 0
            ORDER BY
                CASE priority
                    WHEN 'high' THEN 0
                    WHEN 'medium' THEN 1
                    WHEN 'low' THEN 2
                    ELSE 3
                END,
                due_date IS NULL,
                due_date ASC
            LIMIT ?
        """, (limit,))

        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_task(row) for row in rows]

    def get_overdue_tasks(self) -> List[Task]:
        """Get pending tasks past their due date."""
        now = datetime.now().isoformat()

        conn = sqlite3.connect(self.db_path, timeout=10)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, title, description, priority, due_date, completed, created_at
            FROM tasks
            WHERE completed = 0 AND due_date IS NOT NULL AND due_date < ?
            ORDER BY due_date
        """, (now,))

        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_task(row) for row in rows]

    def complete_task(self, task_id: int) -> bool:
        """Mark a task as completed."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        cursor = conn.cursor()

        cursor.execute("UPDATE tasks SET completed = 1 WHERE id = ? AND completed = 0", (task_id,))

        rows_affected = cursor.rowcount
        conn.commit()
        conn.close()

        if rows_affected > 0:
            log.info(f"Completed task #{task_id}")
            try:                                           # progress for what really happened; never in the way of finishing a task
                from core import rewards
                rewards.award("task_done", str(task_id))
            except Exception:
                pass
            return True
        return False

    def snooze_task(self, task_id: int, minutes: int, now: Optional[datetime] = None) -> Optional[str]:
        """Move a pending task's time to `minutes` from now; it is announced
        again then (announcements are keyed by the due time). Returns the new
        time, or None when there is no such pending task."""
        due = ((now or datetime.now()) + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            changed = conn.execute("UPDATE tasks SET due_date = ? WHERE id = ? AND completed = 0", (due, task_id)).rowcount
            conn.commit()
        finally:
            conn.close()
        if changed:
            log.info(f"Snoozed task #{task_id} to {due}")
            return due
        return None

    def delete_task(self, task_id: int) -> bool:
        """Delete a task."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        cursor = conn.cursor()

        cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

        rows_affected = cursor.rowcount
        conn.commit()
        conn.close()

        if rows_affected > 0:
            log.info(f"Deleted task #{task_id}")
            return True
        return False

    def search_tasks(self, query: str, status: str = "pending") -> List[Task]:
        """Search pending tasks by title."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        cursor = conn.cursor()

        pattern = f"%{query}%"
        completed = 0 if status == "pending" else 1
        cursor.execute("""
            SELECT id, title, description, priority, due_date, completed, created_at
            FROM tasks
            WHERE title LIKE ? AND completed = ?
            ORDER BY created_at DESC
        """, (pattern, completed))

        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_task(row) for row in rows]


# Matches a temporal phrase at the very end of a task description, e.g.
# "...tomorrow at 9am", "...tonight", "...next friday", "...at 5pm". Used to
# split "remind me to water the plants tomorrow at 9am" into description
# "water the plants" and time_ref "tomorrow at 9am" — a plain
# `\s+(?:by|at|in|on)\s+(.+)$` split loses the day word, matching only the
# text after "at" ("9am") and leaving "tomorrow" stuck in the description,
# where parse_time_reference() never sees it.
# Spoken amounts: "5", "one", "an", "a couple of", "half an" ...
_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90,
    "a couple of": 2, "a couple": 2, "a few": 3,
}
_AMOUNT = r'(?:\d+|half\s+an?|' + "|".join(sorted((re.escape(w) for w in _NUMBER_WORDS), key=len, reverse=True)) + r')'
_UNIT = r'(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)'
# "in 5 minutes", "after one minute", "in half an hour"
_RELATIVE_RE = re.compile(r'\b(?:in|after)\s+(' + _AMOUNT + r')\s+(' + _UNIT + r')\b')


def _relative_delta(time_ref: str) -> Optional[timedelta]:
    """timedelta for "in/after <amount> <unit>", or None."""
    match = _RELATIVE_RE.search(time_ref)
    if not match:
        return None
    amount_text, unit = match.group(1), match.group(2)
    if amount_text.startswith("half"):
        amount = 0.5
    elif amount_text.isdigit():
        amount = int(amount_text)
    else:
        amount = _NUMBER_WORDS[" ".join(amount_text.split())]
    if unit.startswith("s"):
        return timedelta(seconds=amount)
    if unit.startswith("m"):
        return timedelta(minutes=amount)
    if unit.startswith("h"):
        return timedelta(hours=amount)
    return timedelta(days=amount)


_TIME_TAIL_RE = re.compile(
    r'\s+('
    r'(?:tomorrow|today|tonight'
    r'|next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)'
    r'|(?:in|after)\s+' + _AMOUNT + r'\s+' + _UNIT +
    r')'
    r'(?:\s+(?:at|by)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?'
    r'|(?:at|by)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?'
    r')\s*$',
    re.IGNORECASE,
)


def _split_description_and_time(phrase: str) -> tuple[str, Optional[str]]:
    """Split a task phrase into (description, time_ref), time_ref may be None."""
    match = _TIME_TAIL_RE.search(phrase)
    if match:
        return phrase[:match.start()].strip(), match.group(1).strip()
    return phrase.strip(), None


_COUNT_WORDS = {2: "two", 3: "three", 4: "four", 5: "five"}


def reminder_announcement(items: List[Dict[str, Any]]) -> str:
    """
    What MBUDDY says out loud when reminders come due: one sentence for any
    number of them, with an emotion tag so the speech path accepts it.
    Empty when nothing is due.
    """
    titles = [str(i.get("title", "")).strip().rstrip(".") for i in items if str(i.get("title", "")).strip()]
    if not titles:
        return ""
    if len(titles) == 1:
        return f"[NORMAL] Hey, reminder: {titles[0]}."
    count = _COUNT_WORDS.get(len(titles), str(len(titles)))
    listed = ", ".join(titles[:-1]) + f", and {titles[-1]}"
    return f"[NORMAL] Hey, {count} reminders: {listed}."


def parse_task_from_message(message: str) -> Optional[Dict[str, Any]]:
    """
    Extract task information from natural language.

    Patterns:
    - "remind me to [task] [time]"
    - "add task: [task]"
    - "I need to [task] by [date]"
    - "[task] is urgent"

    Returns:
        Dict with 'description', 'due_date', 'priority' or None
    """
    message_lower = message.lower().strip().rstrip(".!?")

    # Pattern: "remind me in/after <time> to X" (time first)
    match = re.search(r'remind\s+me\s+((?:in|after)\s+' + _AMOUNT + r'\s+' + _UNIT + r')\s+to\s+(.+)$', message_lower)
    if match:
        return {
            "description": match.group(2).strip(),
            "due_date": parse_time_reference(match.group(1)),
            "priority": "normal",
        }

    # Pattern: "remind me to X"
    match = re.search(r'remind\s+me\s+to\s+(.+)$', message_lower)
    if match:
        description, time_ref = _split_description_and_time(match.group(1))
        due_date = parse_time_reference(time_ref) if time_ref else None

        return {
            "description": description,
            "due_date": due_date,
            "priority": "normal",
        }

    # Pattern: "I need to X by Y"
    match = re.search(r'(?:i\s+need\s+to|i\s+have\s+to|i\s+must)\s+(.+?)\s+by\s+(.+)', message_lower)
    if match:
        description = match.group(1).strip()
        time_ref = match.group(2).strip()
        due_date = parse_time_reference(time_ref)

        return {
            "description": description,
            "due_date": due_date,
            "priority": "normal",
        }

    # Pattern: "X is urgent"
    match = re.search(r'(.+?)\s+is\s+(urgent|important|critical)', message_lower)
    if match:
        description = match.group(1).strip()
        priority_map = {"urgent": "urgent", "important": "high", "critical": "urgent"}
        priority = priority_map.get(match.group(2), "normal")

        return {
            "description": description,
            "due_date": None,
            "priority": priority,
        }

    return None


_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}
_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DAY_RE = r"(\d{1,2})(?:st|nd|rd|th)?"


def _month_number(word: str) -> int:
    word = word.lower()
    return next(n for name, n in _MONTHS.items() if name.startswith(word[:3]))


def _clock_in(text: str) -> tuple[int, int] | None:
    """The time of day in "... at 5pm" / "17:30" / "5 pm", if any."""
    m = re.search(r"(?:\bat\s+)?\b(\d{1,2})(?::(\d{2}))\s*(am|pm)?\b|\b(\d{1,2})\s*(am|pm)\b", text)
    if not m:
        return None
    if m.group(1):
        hour, minute, period = int(m.group(1)), int(m.group(2)), m.group(3)
    else:
        hour, minute, period = int(m.group(4)), 0, m.group(5)
    if period == "pm" and hour < 12:
        hour += 12
    elif period == "am" and hour == 12:
        hour = 0
    return (hour, minute) if hour < 24 and minute < 60 else None


def _calendar_date(text: str, now: datetime) -> Optional[datetime]:
    """A named date, at its time of day or 9:00; past dates roll to next year/month."""
    year = None
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = (re.search(rf"\b{_DAY_RE}\s+(?:of\s+)?{_MONTH_RE}\b", text)
             or re.search(rf"\b{_MONTH_RE}\s+(?:the\s+)?{_DAY_RE}\b", text))
        if m:
            a, b = m.group(1), m.group(2)
            day, month = (int(a), _month_number(b)) if a.isdigit() else (int(b), _month_number(a))
            y = re.search(r"\b(20\d{2})\b", text)
            year = int(y.group(1)) if y else None
        else:
            m = re.search(rf"\bon\s+the\s+{_DAY_RE}\b", text)
            if not m:
                return None
            day, month = int(m.group(1)), now.month
    clock = _clock_in(text[:m.start()] + " " + text[m.end():]) or (9, 0)
    try:
        target = datetime(year or now.year, month, day, clock[0], clock[1])
    except ValueError:
        return None  # "31 february": not a date, say so rather than guess
    if year is None and target < now:
        if "the" in m.group(0) and not re.search(_MONTH_RE, m.group(0)):
            month, yr = (month % 12) + 1, now.year + (1 if month == 12 else 0)  # "the 5th" of next month
            try:
                target = target.replace(year=yr, month=month)
            except ValueError:
                return None
        else:
            target = target.replace(year=target.year + 1)
    return target


def parse_time_reference(time_ref: str) -> Optional[str]:
    """
    Parse relative time reference to ISO datetime.

    Supports:
    - "tomorrow", "today", "tonight"
    - "in X hours/days"
    - "next monday/tuesday/etc"
    - Specific times: "3pm", "14:30"

    Returns:
        ISO datetime string or None
    """
    time_ref = time_ref.lower().strip()
    now = datetime.now()

    # Tomorrow
    if "tomorrow" in time_ref:
        target = now + timedelta(days=1)
        # Extract time if specified
        time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', time_ref)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            period = time_match.group(3)

            if period == "pm" and hour < 12:
                hour += 12
            elif period == "am" and hour == 12:
                hour = 0

            target = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
        else:
            target = target.replace(hour=9, minute=0, second=0, microsecond=0)

        return target.isoformat()

    # Today
    if "today" in time_ref or "tonight" in time_ref:
        target = now
        time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', time_ref)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            period = time_match.group(3)

            if period == "pm" and hour < 12:
                hour += 12
            elif period == "am" and hour == 12:
                hour = 0

            target = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
        elif "tonight" in time_ref:
            target = target.replace(hour=20, minute=0, second=0, microsecond=0)

        return target.isoformat()

    # In/after X minutes/hours/days, spoken or in digits
    delta = _relative_delta(time_ref)
    if delta is not None:
        return (now + delta).isoformat()

    # A clock time: "17:00", "at 5pm", "before 17:00". Today, or tomorrow if
    # that time has passed; "before" lands 15 minutes early so the reminder
    # comes ahead of the deadline, not at it.
    clock = re.fullmatch(r'(?:(before|by|at)\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', time_ref)
    if clock and (clock.group(3) or clock.group(4)):
        hour, minute, period = int(clock.group(2)), int(clock.group(3) or 0), clock.group(4)
        if period == "pm" and hour < 12:
            hour += 12
        elif period == "am" and hour == 12:
            hour = 0
        if hour < 24 and minute < 60:
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if clock.group(1) in ("before", "by"):
                target -= timedelta(minutes=15)
            if target <= now:
                target += timedelta(days=1)
            return target.isoformat()

    # A calendar date: "1 october", "october 1st", "the 1st of oct at 5pm",
    # "2026-10-01", "on the 5th" (live: "on 1 october" wasn't understood, the
    # reminder failed, and the reply called the event "not real").
    dated = _calendar_date(time_ref, now)
    if dated is not None:
        return dated.isoformat()

    # Next weekday
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, day in enumerate(weekdays):
        if day in time_ref:
            current_weekday = now.weekday()
            days_ahead = i - current_weekday
            if days_ahead <= 0:
                days_ahead += 7
            # its time of day, or 9:00 (live: "Friday at 3pm" was set for Friday 09:00)
            hour, minute = _clock_in(time_ref) or (9, 0)
            target = now + timedelta(days=days_ahead)
            target = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
            return target.isoformat()

    return None


def format_task_list(tasks: List[Task]) -> str:
    """Format task list for display."""
    if not tasks:
        return "No pending tasks."

    lines = []
    for task in tasks:
        priority_icon = {"high": "🔴", "medium": "🔵", "low": "⚪"}.get(task.priority, "🔵")

        due_str = ""
        if task.due_date:
            due_dt = datetime.fromisoformat(task.due_date)
            now = datetime.now()
            if due_dt < now:
                due_str = " (OVERDUE)"
            elif due_dt.date() == now.date():
                due_str = f" (today {due_dt.strftime('%H:%M')})"
            elif due_dt.date() == (now + timedelta(days=1)).date():
                due_str = f" (tomorrow {due_dt.strftime('%H:%M')})"
            else:
                due_str = f" ({due_dt.strftime('%b %d %H:%M')})"

        lines.append(f"{priority_icon} #{task.task_id}: {task.title}{due_str}")

    return "\n".join(lines)


# Global instance
task_tracker = TaskTracker()
