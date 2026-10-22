"""
Brain connector: the user's day.

- plan_day: the facts a plan needs (today's events, open tasks, free stretches, the hours the user has focused best). Code
  gathers and computes; the model only words the plan, so it cannot invent a meeting or a free hour.
- time_report: how long the user was in each app, from the activity tracker (which is opt-in and local).
- find_anything: one search over tasks, reminders and events, the notes MBUDDY keeps, and today's apps.
- habit_log / habit_status: habits told by voice ("I went for a walk"), with a streak counted from real days.
- distraction_list: which programs are a distraction while a focus sprint runs (they are nudged, never closed).

Everything is local. Each answer is facts, in plain words, and says plainly when there is nothing.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from brain.connectors import Ability, fail

WORK_START, WORK_END = 9, 18          # the stretch of the day a plan is made for
MIN_FREE_MIN = 45                     # a gap shorter than this is not "free time"
EVENT_LEN_MIN = 60                    # an event with no end is assumed to take this long


def _hm(minutes: float) -> str:
    m = max(0, int(round(minutes)))
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m} min"


# ── time_report ──────────────────────────────────────────────────────────────────────────────
def time_report(args: dict) -> str:
    from core import activity
    if not activity.settings().get("tracking"):
        return fail("Activity tracking is switched off, so I haven't been keeping track of your time. "
                    "It can be turned on in Settings.")
    which = str(args.get("day", "today") or "today").lower()
    day = date.today() - timedelta(days=1) if which in ("yesterday", "last night") else date.today()
    data = activity.load_day(day)
    apps = sorted(((a.get("name") or k, int(a.get("s", 0))) for k, a in (data.get("apps") or {}).items()), key=lambda x: -x[1])
    apps = [(n, s) for n, s in apps if s >= 60]
    label = "Today" if day == date.today() else "Yesterday"
    if not apps:
        return f"{label} I have no time recorded yet ({_hm(int(data.get('active_s', 0)) / 60)} active)."
    want = str(args.get("app", "") or "").strip().lower()
    if want:
        hit = [(n, s) for n, s in apps if want in n.lower()]
        if not hit:
            return (f"{label} I have no time recorded in anything called '{want}'. I track programs, not websites inside a browser. "
                    f"Top programs: {', '.join(f'{n} {_hm(s / 60)}' for n, s in apps[:4])}.")
        return f"{label}: " + ", ".join(f"{n} {_hm(s / 60)}" for n, s in hit) + ". (I track programs, not websites inside a browser.)"
    top = ", ".join(f"{n} {_hm(s / 60)}" for n, s in apps[:6])
    return f"{label} you were active {_hm(int(data.get('active_s', 0)) / 60)}: {top}. (I track programs, not websites inside a browser.)"


# ── plan_day ─────────────────────────────────────────────────────────────────────────────────
def _free_windows(events: list[tuple[datetime, int]], start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    busy = sorted((s, s + timedelta(minutes=m)) for s, m in events)
    free, cursor = [], start
    for s, e in busy:
        if e <= cursor:
            continue
        if s > cursor and (s - cursor) >= timedelta(minutes=MIN_FREE_MIN):
            free.append((cursor, min(s, end)))
        cursor = max(cursor, e)
        if cursor >= end:
            break
    if cursor < end and (end - cursor) >= timedelta(minutes=MIN_FREE_MIN):
        free.append((cursor, end))
    return free


def _best_hours() -> list[int]:
    """The hours of the day with the most active time over the last week, when tracking has enough days; else []."""
    try:
        from core import activity
        if not activity.settings().get("tracking"):
            return []
        per_hour: dict[int, float] = {}
        days = 0
        for back in range(1, 8):
            d = activity.load_day(date.today() - timedelta(days=back))
            if not d.get("slots"):
                continue
            days += 1
            for slot, counts in d["slots"].items():
                per_hour[int(slot[:2])] = per_hour.get(int(slot[:2]), 0) + sum(counts.values())
        if days < 3:
            return []
        return [h for h, _s in sorted(per_hour.items(), key=lambda x: -x[1])[:2] if WORK_START <= h < WORK_END]
    except Exception:
        return []


def plan_day(args: dict, now: datetime | None = None) -> str:
    from brain.connectors import calendar_ics
    from brain.task_tracker import task_tracker
    now = now or datetime.now()
    today = now.date()
    events = [e for e in calendar_ics.upcoming_events(days=1, now=now.replace(hour=0, minute=0)) if e["start"].date() == today]
    timed = [(e["start"], EVENT_LEN_MIN) for e in events]
    window_start = max(now, datetime.combine(today, datetime.min.time()).replace(hour=WORK_START))
    window_end = datetime.combine(today, datetime.min.time()).replace(hour=WORK_END)
    lines = [f"Today is {now.strftime('%A %d %B')}; it is {now.strftime('%H:%M')}."]
    if events:
        lines.append("On the calendar today: " + "; ".join(f"{e['summary']} at {e['start'].strftime('%H:%M')}" for e in events) + ".")
    else:
        lines.append("Nothing is on the calendar today.")
    open_tasks = [t for t in task_tracker.get_pending_tasks(limit=50) if not t.due_date]
    if open_tasks:
        lines.append("Open tasks with no time: " + "; ".join(t.title for t in open_tasks[:8]) + ".")
    if window_start < window_end:
        free = _free_windows(timed, window_start, window_end)
        if free:
            lines.append("Free stretches (45 minutes or more): " + ", ".join(f"{a.strftime('%H:%M')} to {b.strftime('%H:%M')}" for a, b in free) + ".")
        else:
            lines.append("There is no free stretch of 45 minutes or more left before 18:00.")
    best = _best_hours()
    if best:
        lines.append("Hours you have worked most this week: " + ", ".join(f"{h:02d}:00" for h in sorted(best)) + ".")
    return ("FACTS FOR PLANNING THE DAY (plan only from these; do not add meetings or times that are not here):\n" + "\n".join(lines))


# ── find_anything ────────────────────────────────────────────────────────────────────────────
def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[\w']+", text.lower()) if len(w) > 2]


_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}
_DATE_WORDS = {"today", "yesterday", "earlier", "ago", "last", "week", "ask", "asked", "said", "say", "told", "tell", "talked", "about", "you",
               "did", "what", "when", "the", "and", "me", "my", "know", "remember", "this", "morning", "tonight", "something",
               "talk", "talking", "we", "us", "discuss", "discussed", "chat", "chatted", "conversation", "conversations"}


def _day_asked(query: str, today: date | None = None):
    """The day a question is about ('on October 12', 'oct 12', 'yesterday', 'earlier today'), or None."""
    today = today or date.today()
    q = query.lower()
    if "yesterday" in q:
        return today - timedelta(days=1)
    if "today" in q or "this morning" in q or "tonight" in q:
        return today
    m = re.search(r"\b(" + "|".join(k[:3] for k in _MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", q) or \
        re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(" + "|".join(k[:3] for k in _MONTHS) + r")[a-z]*\b", q)
    if m:
        a, b = m.groups()
        mon, day = (a, b) if not a.isdigit() else (b, a)
        month = next(v for k, v in _MONTHS.items() if k.startswith(mon[:3]))
        try:
            d = date(today.year, month, int(day))
            return d if d <= today else date(today.year - 1, month, int(day))
        except ValueError:
            return None
    return None


def _conversation_db() -> Path:
    from memory import sqlite_store
    db = Path(sqlite_store.DB_PATH)
    return db if db.is_absolute() else Path(__file__).resolve().parents[2] / db


def past_conversations(query: str, limit: int = 5) -> list[str]:
    """What the user and MBUDDY said before, matching the words and/or the day asked about, newest first."""
    import sqlite3
    day = _day_asked(query)
    words = [w for w in _words(query) if w not in _DATE_WORDS and w not in _MONTHS and not w.isdigit()]
    try:
        con = sqlite3.connect(f"file:{_conversation_db()}?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute("SELECT time, user, mbuddy FROM conversation_log ORDER BY id DESC LIMIT 3000").fetchall()
        finally:
            con.close()
    except Exception:
        return []
    def line(t, you, me, note=""):
        return f"{t}{note}: you said \"{(you or '')[:160]}\"; I said \"{(me or '')[:200]}\""
    def hits(you, me):
        text = f"{you} {me}".lower()
        return sum(1 for w in words if re.search(r"\b" + re.escape(w), text))     # whole words ("ask" is not in "task")
    on_day = [r for r in rows if day and str(r[0]).startswith(day.isoformat())]           # rows are newest first
    if day and not words:
        return [line(*r) for r in on_day[:limit]]
    if day:
        matched = sorted((r for r in on_day if hits(r[1], r[2])), key=lambda r: -hits(r[1], r[2]))
        if matched:
            return [line(*r) for r in matched[:limit]]
        # nothing about it that day: say so, and show where it was said
        other = sorted((r for r in rows if hits(r[1], r[2])), key=lambda r: -hits(r[1], r[2]))
        return [f"(nothing about that on {day.strftime('%a %d %b')})"] + [line(*r) for r in other[:limit]] if other else []
    matched = sorted((r for r in rows if hits(r[1], r[2])), key=lambda r: -hits(r[1], r[2]))
    return [line(*r) for r in matched[:limit]]


def find_anything(args: dict) -> str:
    query = str(args.get("query", "")).strip()
    words = _words(query)
    if not words:
        return fail("Tell me what to look for.")
    found: list[str] = []
    try:
        from brain.task_tracker import task_tracker
        hits = [t for t in task_tracker.get_pending_tasks(limit=500) if any(w in (t.title + " " + (t.description or "")).lower() for w in words)]
        if hits:
            found.append("Tasks: " + "; ".join(f"{t.title}" + (f" (due {t.due_date[:16].replace('T', ' ')})" if t.due_date else "") for t in hits[:5]))
    except Exception:
        pass
    try:
        from brain.connectors import calendar_ics
        ev = [e for e in calendar_ics.upcoming_events(days=60) if any(w in e["summary"].lower() for w in words)]
        if ev:
            found.append("Coming up: " + "; ".join(f"{e['summary']} ({e['start'].strftime('%a %d %b %H:%M')})" for e in ev[:5]))
    except Exception:
        pass
    try:
        from brain import brain_store
        notes = str(brain_store.search_notes(query))
        if notes and not notes.lower().startswith(("nothing", "no ", "i found nothing", "failed")):
            found.append("Notes: " + notes[:400])
    except Exception:
        pass
    said = past_conversations(query)
    if said:
        found.append("Past conversations:\n- " + "\n- ".join(said))
    try:
        from core import activity
        if activity.settings().get("tracking"):
            apps = [a.get("name") or k for k, a in activity.load_day(date.today()).get("apps", {}).items() if any(w in (a.get("name") or k).lower() for w in words)]
            if apps:
                found.append("Apps used today: " + ", ".join(apps[:5]))
    except Exception:
        pass
    if not found:
        return f"Nothing matches '{query}' in your tasks, your calendar, my notes, our past conversations or today's apps."
    return "\n".join(found)


# ── stopping a repeating reminder ────────────────────────────────────────────────────────────
def stop_repeating_reminder(args: dict) -> str:
    from brain.task_tracker import task_tracker
    words = _words(str(args.get("about", "")))
    if not words:
        return fail("Which repeating reminder? Tell me a word from it.")
    stopped = task_tracker.stop_reminders(words)
    if not stopped:
        return f"I found no repeating reminder about '{args.get('about', '')}'."
    return "Stopped: " + "; ".join(stopped) + "."


# ── habits ───────────────────────────────────────────────────────────────────────────────────
def _habit_path() -> Path | None:
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("MBUDDY_HABITS_PATH"):
        return None
    return Path(os.environ.get("MBUDDY_HABITS_PATH") or (Path.home() / ".buddy" / "habits.json"))


_mem: dict = {}


def _load() -> dict:
    path = _habit_path()
    if path is None:
        return _mem
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = _habit_path()
    if path is None:
        _mem.clear()
        _mem.update(data)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())[:60]


def streak(days: list[str], today: date | None = None) -> int:
    """Consecutive days ending today (or yesterday: today is not over yet)."""
    today = today or date.today()
    have = set(days)
    cursor = today if today.isoformat() in have else today - timedelta(days=1)
    n = 0
    while cursor.isoformat() in have:
        n += 1
        cursor -= timedelta(days=1)
    return n


def habit_log(args: dict) -> str:
    name = str(args.get("name", "")).strip()
    if not name:
        return fail("Which habit? Tell me what you did.")
    data = _load()
    h = data.setdefault(_key(name), {"name": name[:60], "days": []})
    today = date.today().isoformat()
    if today in h["days"]:
        return f"'{h['name']}' is already marked for today. Streak: {streak(h['days'])} day{'s' if streak(h['days']) != 1 else ''}."
    h["days"] = sorted(set(h["days"] + [today]))[-400:]
    _save(data)
    try:
        from core import rewards
        rewards.award("habit", f"{_key(name)}:{today}", f"Kept: {h['name']}")
    except Exception:
        pass
    n = streak(h["days"])
    return f"Marked '{h['name']}' for today. Streak: {n} day{'s' if n != 1 else ''}."


def habit_status(args: dict) -> str:
    data = _load()
    if not data:
        return "You haven't told me about any habits yet. Say what you did, like 'I went for a walk', and I'll count it."
    today = date.today().isoformat()
    rows = []
    for h in data.values():
        n = streak(h["days"])
        rows.append(f"{h['name']}: {n}-day streak, {'done today' if today in h['days'] else 'not yet today'}, {len(h['days'])} days in all")
    return "Habits: " + "; ".join(rows) + "."


# ── distractions ─────────────────────────────────────────────────────────────────────────────
def _app_key(name: str) -> tuple[str, str] | None:
    from core import activity
    want = name.strip().lower()
    if not want:
        return None
    seen = {k: n for k, n in getattr(activity, "_names", {}).items()}
    for d in range(0, 7):
        for k, a in activity.load_day(date.today() - timedelta(days=d)).get("apps", {}).items():
            seen.setdefault(k, a.get("name") or k)
    for k, n in seen.items():
        if want == k or want in n.lower() or n.lower() in want:
            return k, n
    return None


def _name_of(key: str) -> str:
    from core import activity
    for back in range(0, 14):
        a = activity.load_day(date.today() - timedelta(days=back)).get("apps", {}).get(key)
        if a and a.get("name"):
            return a["name"]
    return getattr(activity, "_names", {}).get(key) or key


def distraction_list(args: dict) -> str:
    from core import activity
    action = str(args.get("action", "list") or "list").lower()
    if action == "list":
        labels = activity._labels()
        offs = [k for k in labels if activity.label_of(k) == "off"]
        if not offs:
            return "Nothing is on your distraction list yet. Tell me an app to add, like YouTube or Discord."
        names = [_name_of(k) for k in offs]
        return "On your distraction list: " + ", ".join(names) + ". During a focus sprint I will tell you when one is in front; I do not close anything."
    hit = _app_key(str(args.get("app", "")))
    if hit is None:
        return fail(f"I haven't seen an app called '{args.get('app', '')}' on this PC yet, so I can't add it. Open it once and tell me again.")
    key, name = hit
    if action == "add":
        activity.set_label(key, "off")
        return f"Added {name} to your distraction list. During a focus sprint I will tell you when it's in front."
    if action == "remove":
        activity.set_label(key, "unknown")
        return f"Removed {name} from your distraction list."
    return fail("action must be add, remove or list.")


ABILITIES = [
    Ability(name="plan_day", topic="day", risk="read", execute=plan_day,
            description="The facts for planning today: calendar, open tasks, free stretches, the hours the user works best."),
    Ability(name="time_report", topic="day", risk="read", execute=time_report,
            description="How long the user was in each program today or yesterday (activity tracker).",
            params={"day": {"type": "string", "enum": ["today", "yesterday"]},
                    "app": {"type": "string", "description": "one program to report on, if the user named one"}}),
    Ability(name="find_anything", topic="day", risk="read", execute=find_anything,
            description="Search tasks, calendar, your notes, today's apps and your past conversations ('what did I ask you on Oct 12').",
            params={"query": {"type": "string", "description": "the words to look for"}}, required=["query"]),
    Ability(name="stop_repeating_reminder", topic="day", risk="low", execute=stop_repeating_reminder,
            description="Stop a reminder that repeats (every hour, day or week) when the user says to stop it.",
            params={"about": {"type": "string", "description": "a word or two from the reminder"}}, required=["about"],
            preview=lambda a: f"stop the repeating reminder about '{a.get('about', '')}'"),
    Ability(name="habit_log", topic="habits", risk="low", execute=habit_log,
            description="Mark a habit done today when the user says they did it (a walk, reading, gym).",
            params={"name": {"type": "string", "description": "the habit, in the user's words"}}, required=["name"],
            preview=lambda a: f"mark '{a.get('name', 'the habit')}' as done today"),
    Ability(name="habit_status", topic="habits", risk="read", execute=habit_status,
            description="The user's habits, their streaks, and whether each is done today."),
    Ability(name="distraction_list", topic="habits", risk="low", execute=distraction_list,
            description="Apps that count as a distraction in a focus sprint: add, remove or list. Nothing is closed.",
            params={"action": {"type": "string", "enum": ["add", "remove", "list"]},
                    "app": {"type": "string", "description": "the app, for add or remove"}}, required=["action"],
            preview=lambda a: f"{a.get('action', 'change')} {a.get('app', 'an app')} on the distraction list"),
]
