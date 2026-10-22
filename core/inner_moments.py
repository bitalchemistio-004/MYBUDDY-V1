"""
core/inner_moments.py - when MBUDDY's inner life may think, and what it shows.

The inner life used to call the model on a timer (every 1.5-4 min, all day,
~500 calls). It now thinks only at moments that matter, only while the user
is actually at the PC, and several thoughts per call (docs/audit 3.9, S53):

  moments   a conversation just ended; the user came back after being away;
            the first time they are around in the morning / evening; a
            reminder is coming up.
  presence  not when the PC has been idle, on battery, while a full-screen
            app or game runs, while the GPU is busy, or in Do Not Disturb.
  batch     one call writes several thoughts; they are shown one at a time.
  showing   thoughts appear as a quiet bubble on the buddy, never spoken
            while the user is away; one is said when they come back.

Everything here is plain logic plus read-only Windows queries, so it can be
tested without a model.
"""
from __future__ import annotations

import ctypes
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta

AWAY_S = 10 * 60          # idle this long = the user is away
PRESENT_S = 2 * 60        # input within this = the user is here
SHOW_GAP_S = 25 * 60      # at most one shown thought per this, while present
QUEUE_LOW = 2             # generate a new batch only when fewer are waiting
BATCH_SIZE = 6
GPU_BUSY_PCT = 50
REMINDER_SOON_S = 15 * 60
ANCHORS = {"morning": (6, 11), "evening": (18, 22)}


# ── read-only Windows state ──────────────────────────────────────────────
def idle_seconds() -> float | None:
    """Seconds since the last keyboard/mouse input (None if unknown)."""
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return None


def on_battery() -> bool:
    try:
        class STATUS(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

        s = STATUS()
        return bool(ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s))) and s.ACLineStatus == 0
    except Exception:
        return False


def fullscreen_busy() -> bool:
    """A full-screen app, game or presentation is running (Windows' own 'busy' states)."""
    try:
        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
        # 2 busy (full-screen), 3 Direct3D full-screen, 4 presentation mode
        return state.value in (2, 3, 4)
    except Exception:
        return False


def gpu_busy() -> bool:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        return max(int(x) for x in out.split()) >= GPU_BUSY_PCT
    except Exception:
        return False


def blockers(dnd: bool, idle_s: float | None) -> list[str]:
    """Why the inner life should not use the model right now (empty = go)."""
    reasons = []
    if dnd:
        reasons.append("do not disturb")
    if idle_s is not None and idle_s >= AWAY_S:
        reasons.append("user away")
    if on_battery():
        reasons.append("on battery")
    if fullscreen_busy():
        reasons.append("full-screen app")
    if not reasons and gpu_busy():  # the one check that starts a process: last
        reasons.append("GPU busy")
    return reasons


# ── moments ──────────────────────────────────────────────────────────────
@dataclass
class Moments:
    """Tracks which moments have fired; one instance per inner-life loop."""
    conversation_ended: bool = True        # the loop starts when a conversation ends
    was_away: bool = False
    anchors_done: dict = field(default_factory=dict)   # name -> date
    reminders_done: set = field(default_factory=set)
    last_shown: datetime | None = None

    def due(self, now: datetime, idle_s: float | None, upcoming: list[dict]) -> str | None:
        """The moment happening now, if any (each fires once)."""
        present = idle_s is None or idle_s < PRESENT_S
        if idle_s is not None and idle_s >= AWAY_S:
            self.was_away = True
            return None
        if self.was_away and present:
            self.was_away = False
            return "returned"
        if self.conversation_ended:
            self.conversation_ended = False
            return "conversation_ended"
        if not present:
            return None
        for name, (start, end) in ANCHORS.items():
            if start <= now.hour < end and self.anchors_done.get(name) != now.date():
                self.anchors_done[name] = now.date()
                return name
        for r in upcoming:
            rid, due = r.get("id"), r.get("due")
            if rid in self.reminders_done or not isinstance(due, datetime):
                continue
            if now <= due <= now + timedelta(seconds=REMINDER_SOON_S):
                self.reminders_done.add(rid)
                return "reminder_soon"
        return None

    def may_show(self, now: datetime, moment: str | None, idle_s: float | None) -> bool:
        """Show a queued thought: always on return, else at most one per gap while present."""
        if moment == "returned":
            return True
        if idle_s is not None and idle_s >= PRESENT_S:
            return False
        return self.last_shown is None or (now - self.last_shown).total_seconds() >= SHOW_GAP_S


def upcoming_reminders() -> list[dict]:
    """Pending tasks with a due time, as {"id", "due"} (read-only)."""
    try:
        from brain.task_tracker import task_tracker
        out = []
        for t in task_tracker.get_pending_tasks():
            try:
                due = datetime.fromisoformat(t.due_date) if t.due_date else None
            except ValueError:
                continue
            if due is not None:
                out.append({"id": t.task_id, "due": due})
        return out
    except Exception:
        return []


# ── batches ──────────────────────────────────────────────────────────────
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


def parse_batch(text: str, limit: int = BATCH_SIZE) -> list[str]:
    """One thought per line; numbering and bullets stripped, QUIET and blanks dropped."""
    thoughts = []
    for line in (text or "").splitlines():
        line = _BULLET.sub("", line).strip().strip('"').strip()
        if not line or line.upper().rstrip(".") == "QUIET" or len(line) < 8:
            continue
        if line not in thoughts:
            thoughts.append(line)
    return thoughts[:limit]
