"""Where the user's time goes, and whether it moved their work forward (Today).

A light sampler notes which program is in front, every few seconds, and adds
the time to today's totals. Nothing here is about a particular program:

- Whether a program counts as work is LEARNED from the user's answers to
  check-ins ("Working", "On a break", "Busted") or set by hand in Today. Until
  then it is "unknown" and counts for nothing. No list of "good" and "bad"
  programs exists anywhere in the code.
- It is opt-in. Nothing is sampled until the user turns tracking on. Window
  titles are never read or stored, only the program (its exe name and its
  description as Windows shows it).
- Everything stays on this PC, in ~/.buddy/activity/ (one small json file per
  day, the learned answers, and the settings).
- The check-in is rate-limited and stays quiet in full-screen apps.

The sampler's rules are in `Tracker.tick`, which takes the moment, the front
program, the idle time and whether it is full screen as plain arguments, so
they are tested with a fake clock. `_front()` and `_idle_s()` are the only
parts that touch Windows.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

SAMPLE_S = 5              # how often the front program is read
IDLE_S = 120              # no input for this long: the time isn't counted
GAP_S = 150               # a gap longer than this ends a continuous stretch
SLOT_S = 300              # the day is stored in 5-minute slots
CHECKIN_GAP_S = 45 * 60   # at most one check-in this often
ANSWERED_WORK_S = 3 * 3600  # after "Working", don't ask about that program again for this long
BREAK_S = 30 * 60         # after "On a break", stay quiet for this long

DEFAULTS = {"tracking": False, "checkins": True, "pc_warnings": True, "checkin_minutes": 40, "work_goal_min": 60}
LIMITS = {"checkin_minutes": (15, 180), "work_goal_min": (10, 600)}

_lock = threading.RLock()


# ── storage ───────────────────────────────────────────────────────────────
def root() -> Path:
    override = os.environ.get("MBUDDY_ACTIVITY_DIR")
    path = Path(override) if override else Path.home() / ".buddy" / "activity"
    (path / "days").mkdir(parents=True, exist_ok=True)
    return path


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def settings() -> dict:
    saved = _read(root() / "settings.json", {})
    out = dict(DEFAULTS)
    for key, value in saved.items():
        if key in DEFAULTS and type(value) is type(DEFAULTS[key]):
            out[key] = value
    return out


def set_settings(changes: dict) -> dict:
    """Validated; returns the new settings. Raises ValueError on a bad value."""
    current = settings()
    for key, value in (changes or {}).items():
        if key not in DEFAULTS:
            raise ValueError(f"unknown setting {key}")
        if isinstance(DEFAULTS[key], bool):
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
        else:
            lo, hi = LIMITS[key]
            if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                raise ValueError(f"{key} must be a whole number from {lo} to {hi}")
        current[key] = value
    with _lock:
        _write(root() / "settings.json", current)
    return current


def _day_path(day: date) -> Path:
    return root() / "days" / f"{day.isoformat()}.json"


def load_day(day: date) -> dict:
    data = _read(_day_path(day), None)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("date", day.isoformat())
    data.setdefault("active_s", 0)
    data.setdefault("apps", {})
    data.setdefault("slots", {})
    return data


def save_day(data: dict) -> None:
    with _lock:
        _write(root() / "days" / f"{data['date']}.json", data)


# ── what the user taught it ──────────────────────────────────────────────
def _labels() -> dict:
    return _read(root() / "labels.json", {})


def label_of(key: str) -> str:
    """work | off | mixed | unknown, from the answers so far."""
    entry = _labels().get(key) or {}
    if entry.get("set"):                      # set by hand: the user's word wins
        return entry["set"]
    yes, no = int(entry.get("yes", 0)), int(entry.get("no", 0))
    if yes and not no:
        return "work"
    if no and not yes:
        return "off"
    if yes and no:
        return "mixed"
    return "unknown"


def learn(key: str, answer: str, now: float | None = None) -> str:
    """Record one answer about a program. answer: work | off | break."""
    now = time.time() if now is None else now
    with _lock:
        labels = _labels()
        entry = labels.setdefault(key, {"yes": 0, "no": 0, "answers": []})
        if answer == "work":
            entry["yes"] = int(entry.get("yes", 0)) + 1
        elif answer == "off":
            entry["no"] = int(entry.get("no", 0)) + 1
        entry["answers"] = (entry.get("answers", []) + [{"a": answer, "t": datetime.fromtimestamp(now).isoformat(timespec="seconds")}])[-20:]
        _write(root() / "labels.json", labels)
    return label_of(key)


def set_label(key: str, status: str) -> str:
    """The user sets a program's status by hand (or clears it with 'unknown')."""
    if status not in ("work", "off", "mixed", "unknown"):
        raise ValueError("status must be work, off, mixed or unknown")
    with _lock:
        labels = _labels()
        entry = labels.setdefault(key, {"yes": 0, "no": 0, "answers": []})
        if status == "unknown":
            entry.pop("set", None)
            entry["yes"] = entry["no"] = 0
        else:
            entry["set"] = status
        _write(root() / "labels.json", labels)
    return label_of(key)


# ── reading Windows ───────────────────────────────────────────────────────
def _idle_s() -> float:
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return max(0.0, (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0)


def _description(exe: str) -> str:
    """The program's name as Windows shows it (FileDescription), or ''."""
    import ctypes
    from ctypes import wintypes
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(exe, None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(exe, 0, size, buf):
            return ""
        ptr, length = ctypes.c_void_p(), wintypes.UINT()
        if not ver.VerQueryValueW(buf, r"\VarFileInfo\Translation", ctypes.byref(ptr), ctypes.byref(length)) or not length.value:
            return ""
        lang, code = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort * 2)).contents
        sub = f"\\StringFileInfo\\{lang:04x}{code:04x}\\FileDescription"
        if ver.VerQueryValueW(buf, sub, ctypes.byref(ptr), ctypes.byref(length)) and length.value:
            return ctypes.wstring_at(ptr.value, length.value - 1).strip()
    except Exception:
        pass
    return ""


_names: dict[str, str] = {}
_exes: dict[str, str] = {}


def _front() -> tuple[str, str, bool] | None:
    """(key, name, fullscreen) of the program in front, or None (own window, desktop, locked)."""
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value or pid.value == os.getpid():
        return None
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        exe = buf.value
    finally:
        kernel32.CloseHandle(handle)
    key = Path(exe).stem.lower()
    _exes[key] = exe
    if key in ("explorer", "searchhost", "shellexperiencehost", "lockapp", "textinputhost") or key.startswith("mbuddy"):
        return None                                    # the desktop, and MBUDDY's own windows: not the user's time
    if key not in _names:
        _names[key] = (_description(exe) or Path(exe).stem.replace("_", " ").replace("-", " ").title()).strip()
        _names[key] = __import__("re").sub(r"\.(root|exe)$", "", _names[key], flags=__import__("re").I) or key
    rect, mon = wintypes.RECT(), wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    monitor = user32.MonitorFromWindow(hwnd, 2)

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    full = False
    if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        m = info.rcMonitor
        full = (rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom)
    return key, _names[key], full


# ── the sampler ──────────────────────────────────────────────────────────
class Tracker:
    """Adds sampled seconds to today's totals and decides when to ask."""

    def __init__(self):
        self.run_key = ""            # the program of the current continuous stretch
        self.run_start = 0.0
        self.last_seen = 0.0
        self.last_checkin = 0.0
        self.quiet_until: dict[str, float] = {}
        self.pending: dict | None = None
        self._next_id = 0
        self._day: dict | None = None
        self._dirty = 0.0

    # -- called every SAMPLE_S with everything it needs as plain arguments
    def tick(self, now: float, front, idle_s: float, cfg: dict | None = None) -> None:
        cfg = cfg or settings()
        day = date.fromtimestamp(now)
        if self._day is None or self._day["date"] != day.isoformat():
            self.flush()
            self._day = load_day(day)
        if front is None or idle_s >= IDLE_S:
            if now - self.last_seen > GAP_S:
                self.run_key = ""
            return
        key, name, fullscreen = front
        dt = min(SAMPLE_S, now - self.last_seen) if self.last_seen and now - self.last_seen <= GAP_S else SAMPLE_S
        self.last_seen = now
        if key != self.run_key:
            self.run_key, self.run_start = key, now
        d = self._day
        d["active_s"] = d.get("active_s", 0) + dt
        app = d["apps"].setdefault(key, {"name": name, "s": 0})
        app["name"], app["s"] = name, app.get("s", 0) + dt
        if _exes.get(key):
            app["exe"] = _exes[key]
        slot = datetime.fromtimestamp(now).strftime("%H:%M")
        slot = f"{slot[:2]}:{int(slot[3:]) // 5 * 5:02d}"
        counts = d["slots"].setdefault(slot, {})
        counts[key] = counts.get(key, 0) + dt
        self._dirty += dt
        if self._dirty >= 60:
            self.flush()
        self._maybe_checkin(now, key, name, fullscreen, cfg)

    def _maybe_checkin(self, now: float, key: str, name: str, fullscreen: bool, cfg: dict) -> None:
        if self.pending or not cfg.get("checkins") or fullscreen:
            return
        minutes = (now - self.run_start) / 60.0
        if minutes < cfg.get("checkin_minutes", 40):
            return
        if now - self.last_checkin < CHECKIN_GAP_S or now < self.quiet_until.get(key, 0):
            return
        if label_of(key) in ("work", "mixed"):
            return                                 # already known to be work: nothing to ask
        self._next_id += 1
        self.pending = {"id": self._next_id, "key": key, "app": name, "minutes": int(minutes),
                        "known": label_of(key) != "unknown",
                        "question": f"{int(minutes)} minutes on {name}. Working, or wandering?"}
        self.last_checkin = now

    def answer(self, checkin_id: int, answer: str, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        p = self.pending
        if not p or p["id"] != checkin_id:
            raise ValueError("that check-in is no longer waiting")
        if answer not in ("work", "off", "break"):
            raise ValueError("answer must be work, off or break")
        self.pending = None
        if answer == "work":
            self.quiet_until[p["key"]] = now + ANSWERED_WORK_S
            learn(p["key"], "work", now)
        elif answer == "off":
            learn(p["key"], "off", now)
            self.quiet_until[p["key"]] = now + BREAK_S
        else:
            self.quiet_until[p["key"]] = now + BREAK_S
        return {"key": p["key"], "app": p["app"], "answer": answer, "status": label_of(p["key"])}

    def flush(self) -> None:
        if self._day is not None:
            save_day(self._day)
        self._dirty = 0.0


TRACKER = Tracker()
_thread: threading.Thread | None = None
_stop = threading.Event()


def pending_checkin() -> dict | None:
    return TRACKER.pending if settings().get("tracking") else None


def _loop() -> None:
    while not _stop.wait(SAMPLE_S):
        try:
            cfg = settings()
            if not cfg.get("tracking"):
                TRACKER.pending = None
                TRACKER.run_key = ""
                continue
            TRACKER.tick(time.time(), _front(), _idle_s(), cfg)
        except Exception:
            pass
    TRACKER.flush()


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="mbuddy-activity", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()


# ── the day's story ───────────────────────────────────────────────────────
def _split(day: dict) -> dict:
    work = off = mixed = unknown = 0.0
    apps = []
    for key, a in day["apps"].items():
        if key.startswith("mbuddy"):
            continue                                  # older days counted MBUDDY's own window: not the user's time
        status = label_of(key)
        s = float(a.get("s", 0))
        if status == "work":
            work += s
        elif status == "off":
            off += s
        elif status == "mixed":
            mixed += s
        else:
            unknown += s
        apps.append({"key": key, "name": a.get("name") or key, "seconds": int(s), "status": status})
    apps.sort(key=lambda x: -x["seconds"])
    known = work + off + mixed
    score = round(100 * (work + 0.5 * mixed) / known) if known >= 600 else None   # under 10 known minutes: too early to say
    return {"work_s": int(work), "off_s": int(off), "mixed_s": int(mixed), "unknown_s": int(unknown), "score": score, "apps": apps}


def timeline(day: dict) -> list[dict]:
    """Consecutive 5-minute slots with the same status, merged: [{start, end, status}]."""
    out: list[dict] = []
    for slot in sorted(day["slots"]):
        counts = day["slots"][slot]
        if not counts:
            continue
        top = max(counts, key=counts.get)
        status = label_of(top)
        h, m = int(slot[:2]), int(slot[3:])
        end = f"{(h * 60 + m + 5) // 60:02d}:{(h * 60 + m + 5) % 60:02d}"
        if out and out[-1]["status"] == status and out[-1]["end"] == slot:
            out[-1]["end"] = end
        else:
            out.append({"start": slot, "end": end, "status": status})
    return out


def summary(day: date | None = None, cfg: dict | None = None) -> dict:
    cfg = cfg or settings()
    day = day or date.today()
    data = TRACKER._day if TRACKER._day and TRACKER._day["date"] == day.isoformat() else load_day(day)
    split = _split(data)
    goal_s = cfg["work_goal_min"] * 60
    streak = []
    for back in range(6, -1, -1):
        d = day - timedelta(days=back)
        dd = TRACKER._day if TRACKER._day and TRACKER._day["date"] == d.isoformat() else load_day(d)
        streak.append({"date": d.isoformat(), "work_s": _split(dd)["work_s"], "hit": _split(dd)["work_s"] >= goal_s})
    run = 0
    for item in reversed(streak):
        if item["hit"]:
            run += 1
        elif item["date"] == date.today().isoformat():
            continue                              # today isn't over: don't break the streak yet
        else:
            break
    return {"enabled": cfg["tracking"], "date": day.isoformat(), "active_s": int(data["active_s"]), **split,
            "timeline": timeline(data), "week": streak, "streak_days": run, "goal_min": cfg["work_goal_min"],
            "settings": cfg, "checkin": pending_checkin()}
