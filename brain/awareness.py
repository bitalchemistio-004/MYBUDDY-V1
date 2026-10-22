"""awareness.py - what MBUDDY knows about the moment, as one small block that goes with every turn.

The code could already read the front window, the day's time per app, the next event, the timers, what is playing and the
battery, but a reply got only the date and its own mood: ask "what am I doing?" and it had to guess or call a tool.
This module gathers those facts on a background thread every few seconds and hands the newest snapshot over instantly, so a
turn never waits for it. Nothing here is invented: a line is there only when its source answered, and a window that holds
passwords is named by its app only.

The user can switch it off (Settings, `awareness`); activity lines also follow the Activity tracking switch.
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime

import utils.logger as log

REFRESH_S = 12          # how often the snapshot is re-read while the app runs
STALE_S = 90            # an older snapshot is not shown (a stale "in front" is worse than none)
MAX_CHARS = 700         # the whole block: about 150 tokens

_lock = threading.Lock()
_snapshot: dict | None = None
_taken = 0.0
_thread: threading.Thread | None = None


# ── reading the sources, each one tolerant of the others failing ─────────────────────────────
def _front() -> dict | None:
    try:
        from brain import pc_windows
        from core import window_reader
        for w in pc_windows.list_windows():
            if getattr(w, "focused", False):
                private = bool(window_reader.refuses(w.title, w.app))
                return {"app": w.app, "title": "" if private else (w.title or "")[:70], "private": private}
    except Exception as exc:  # noqa: BLE001
        log.info(f"[Awareness] front window not read: {type(exc).__name__}")
    return None


def _activity() -> dict | None:
    try:
        from core import activity
        if not activity.settings().get("tracking"):
            return None
        day = activity.load_day(date.today())
        apps = sorted(((a.get("name") or k, int(a.get("s", 0))) for k, a in (day.get("apps") or {}).items()),
                      key=lambda x: -x[1])
        return {"active_s": int(day.get("active_s", 0)), "apps": apps[:3]}
    except Exception as exc:  # noqa: BLE001
        log.info(f"[Awareness] activity not read: {type(exc).__name__}")
    return None


def _schedule() -> dict:
    out: dict = {"next": None, "tasks": 0, "timers": [], "focus": None}
    try:
        from brain.connectors import calendar_ics
        now = datetime.now()
        for e in calendar_ics.upcoming_events(days=2, now=now):
            out["next"] = {"title": e["summary"], "at": e["start"].isoformat(timespec="minutes")}
            break
    except Exception as exc:  # noqa: BLE001
        log.info(f"[Awareness] calendar not read: {type(exc).__name__}")
    try:
        from brain.task_tracker import task_tracker
        out["tasks"] = len(task_tracker.get_pending_tasks())
    except Exception:
        pass
    try:
        from brain.tools import _timer_manager, timer_name
        out["timers"] = [{"name": timer_name(t), "left_s": int(t.remaining_seconds())} for t in _timer_manager.list_timers()][:3]
    except Exception:
        pass
    try:
        from core import focus
        cur = focus.current()
        if cur:
            out["focus"] = {"left_s": int(cur["remaining_s"]), "minutes": int(cur["minutes"])}
    except Exception:
        pass
    return out


def _media() -> dict | None:
    try:
        from brain import pc_windows
        sessions = pc_windows.now_playing(max_age=10)
        m = next((m for m in sessions if m.get("status") == "playing"), None) or next((m for m in sessions if m.get("status") == "paused"), None)
        if m:
            return {"title": m.get("title", "")[:60], "artist": m.get("artist", "")[:40], "app": m.get("app", ""), "playing": m.get("status") == "playing"}
    except Exception:
        pass
    return None


def _device() -> dict:
    try:
        from core import pc_stats
        r = pc_stats.read()
        return {"battery": r.get("battery"), "charging": r.get("charging"), "strain": bool(r.get("strain"))}
    except Exception:
        return {}


def _messages() -> int:
    try:
        from core import notif_watch
        return 1 if notif_watch.current_alert() else 0
    except Exception:
        return 0


def collect() -> dict:
    """One reading of everything (slow-ish: it runs on the background thread, never inside a turn)."""
    return {"at": time.time(), "front": _front(), "activity": _activity(), "schedule": _schedule(),
            "media": _media(), "device": _device(), "messages": _messages()}


# ── turning a reading into words ─────────────────────────────────────────────────────────────
def _mins(seconds: int) -> str:
    m = max(0, int(seconds)) // 60
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m} min"


def render(s: dict | None, now: datetime | None = None) -> str:
    """The block for a snapshot. Empty when there is nothing worth saying."""
    if not s:
        return ""
    now = now or datetime.now()
    lines: list[str] = []
    f = s.get("front")
    if f:
        if f.get("private"):
            lines.append(f"- In front: {f['app']} (contents not read)")
        elif f.get("title"):
            lines.append(f"- In front: {f['app']}, \"{f['title']}\"")
        else:
            lines.append(f"- In front: {f['app']}")
    a = s.get("activity")
    if a and a.get("apps"):
        top = ", ".join(f"{n} {_mins(sec)}" for n, sec in a["apps"] if sec >= 60)
        if top:
            lines.append(f"- On the PC today ({_mins(a.get('active_s', 0))} active): {top}")
    sch = s.get("schedule") or {}
    bits: list[str] = []
    nx = sch.get("next")
    if nx:
        try:
            at = datetime.fromisoformat(nx["at"])
            mins = int((at - now).total_seconds() // 60)
            when = f"in {_mins(mins * 60)}" if 0 <= mins < 180 else at.strftime("%a %H:%M")
            bits.append(f"next: {nx['title']} {when}")
        except (TypeError, ValueError):
            pass
    if sch.get("tasks"):
        bits.append(f"{sch['tasks']} open task{'s' if sch['tasks'] != 1 else ''}")
    for t in sch.get("timers") or []:
        bits.append(f"{t['name']} timer {_mins(t['left_s'])} left" if t["left_s"] >= 60 else f"{t['name']} timer under a minute left")
    if sch.get("focus"):
        bits.append(f"focus sprint {_mins(sch['focus']['left_s'])} left")
    if bits:
        lines.append("- Schedule: " + "; ".join(bits))
    m = s.get("media")
    if m and m.get("title"):
        who = f" by {m['artist']}" if m.get("artist") and m["artist"] not in m["title"] else ""
        lines.append(f"- Media: \"{m['title']}\"{who} in {m.get('app', 'a player')}, {'playing' if m.get('playing') else 'paused'}")
    d = s.get("device") or {}
    dev: list[str] = []
    if d.get("battery") is not None and (d["battery"] <= 30 or d.get("charging") is False):
        dev.append(f"battery {d['battery']}%{'' if d.get('charging') else ' (not charging)'}")
    if d.get("strain"):
        dev.append("the PC is under heavy load")
    if dev:
        lines.append("- PC: " + ", ".join(dev))
    if s.get("messages"):
        lines.append("- A message is waiting for you to answer")
    if not lines:
        return ""
    block = ("RIGHT NOW, read moments ago (use only what the user's words are about; never recite this list):\n" + "\n".join(lines))
    return block[:MAX_CHARS]


# ── the snapshot, kept fresh in the background ───────────────────────────────────────────────
def _loop() -> None:
    global _snapshot, _taken
    while True:
        try:
            snap = collect()
            with _lock:
                _snapshot, _taken = snap, time.time()
        except Exception as exc:  # noqa: BLE001
            log.warn(f"[Awareness] refresh failed: {type(exc).__name__}: {str(exc)[:80]}")
        time.sleep(REFRESH_S)


def _enabled() -> bool:
    try:
        from core import ui_settings
        return bool(ui_settings.get("awareness"))
    except Exception:
        return True


def start() -> None:
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_loop, name="mbuddy-awareness", daemon=True)
        _thread.start()


def block() -> str:
    """The block for this turn, instantly: the newest snapshot, or "" when switched off or too old."""
    if not _enabled():
        return ""
    start()
    with _lock:
        snap, age = _snapshot, time.time() - _taken
    if snap is None or age > STALE_S:
        return ""
    return render(snap)
