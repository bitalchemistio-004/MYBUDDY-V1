"""
brain/pc_windows.py - what's open on the PC, and window actions that wait for a yes.

Reading is free: the open windows (title, app, which is in front) and the
system state (battery, CPU, memory). Acting is never immediate (S49): a
requested action is stored as *pending* and MBUDDY asks first; only a clear
yes from the user (spoken, typed, or the app's button) carries it out. A no,
or a minute without an answer, cancels it; other talk leaves it waiting. Each action is then checked
at the source (the window really is closed / minimized / in front) before
MBUDDY says it happened.
"""
from __future__ import annotations

import ctypes
import os
import re
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

import psutil

import utils.logger as log

CONFIRM_WINDOW_S = 60
ACTIONS = ("focus", "minimize", "close")
MEDIA_ACTIONS = ("pause", "resume", "next", "previous")
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
_OWN_APPS = {"mbuddy-shell.exe", "msedgewebview2.exe"}

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi


# ── reading ──────────────────────────────────────────────────────────────
@dataclass
class Window:
    hwnd: int
    title: str
    app: str        # process name without .exe, e.g. "chrome"
    focused: bool
    minimized: bool


def _cloaked(hwnd: int) -> bool:
    """Hidden UWP/background windows report as visible but are 'cloaked'."""
    value = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(value), ctypes.sizeof(value))  # DWMWA_CLOAKED
    return value.value != 0


def _process_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name()
    except (psutil.Error, ValueError):
        return ""


def list_windows(include_own: bool = False) -> list[Window]:
    """Top-level windows a person would see in the taskbar."""
    foreground = user32.GetForegroundWindow()
    found: list[Window] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER: skip dialogs/tool windows
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0 or _cloaked(hwnd):
            return True
        # Overlays and tool windows (not in the taskbar), and the desktop itself.
        ex_style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        if ex_style & 0x80 or ex_style & 0x08000000:  # WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value in ("Progman", "WorkerW"):
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        exe = _process_name(hwnd)
        if not include_own and exe.lower() in _OWN_APPS:
            return True
        found.append(Window(int(hwnd), buf.value, re.sub(r"\.exe$", "", exe, flags=re.I),
                            int(hwnd) == int(foreground or 0), bool(user32.IsIconic(hwnd))))
        return True

    user32.EnumWindows(visit, 0)
    return found


def windows_of(pids: set[int]) -> list[int]:
    """Visible top-level windows (dialogs included) owned by these processes."""
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    user32.EnumWindows(visit, 0)
    return found


def _get_text(hwnd: int) -> str:
    """A window's or control's text, also from another process (WM_GETTEXT)."""
    buf = ctypes.create_unicode_buffer(4096)
    result = ctypes.c_size_t(0)
    user32.SendMessageTimeoutW(hwnd, 0x000D, 4096, buf, 0x0002, 500, ctypes.byref(result))  # WM_GETTEXT, SMTO_ABORTIFHUNG
    return buf.value.strip()


def window_text(hwnd: int, limit: int = 2000) -> str:
    """Everything a window says: its title and the text of its controls
    (labels, message text, buttons). Native controls are read directly; for
    toolkits that draw their own widgets, UI Automation is asked instead.
    Only reads; nothing is clicked."""
    parts = [_get_text(hwnd)]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def child(h, _lparam):
        text = _get_text(h)
        if text and text not in parts:
            parts.append(text)
        return True

    user32.EnumChildWindows(hwnd, child, 0)
    if len(parts) <= 1:
        try:
            import comtypes.client
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation, TreeScope_Descendants
            uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
            elements = uia.ElementFromHandle(hwnd).FindAll(TreeScope_Descendants, uia.CreateTrueCondition())
            for i in range(min(elements.Length, 200)):
                name = (elements.GetElement(i).CurrentName or "").strip()
                if name and name not in parts:
                    parts.append(name)
        except Exception:
            pass
    return "\n".join(p for p in parts if p)[:limit]


def system_state() -> str:
    parts = []
    battery = psutil.sensors_battery()
    if battery is not None:
        parts.append(f"battery {battery.percent:.0f}% ({'charging' if battery.power_plugged else 'on battery'})")
    parts.append(f"CPU {psutil.cpu_percent(interval=0.3):.0f}%")
    parts.append(f"memory {psutil.virtual_memory().percent:.0f}% used")
    return ", ".join(parts)


def playing_audio() -> list[tuple[str, bool]]:
    """Apps making sound right now, as (app, muted), from Windows' own per-app
    peak meters (an open but silent stream doesn't count; MBUDDY's voice is left out)."""
    import comtypes
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
    from brain.tools import is_mbuddy_voice

    comtypes.CoInitialize()
    try:
        sessions = [s for s in AudioUtilities.GetAllSessions()
                    if s.Process is not None and s.State == 1 and not is_mbuddy_voice(s.Process)]
        meters = [(s, s._ctl.QueryInterface(IAudioMeterInformation)) for s in sessions]
        loudest: dict[str, float] = {}
        for _ in range(4):  # a few samples: a quiet moment in a song isn't silence
            for s, m in meters:
                name = re.sub(r"\.exe$", "", s.Process.name(), flags=re.I)
                loudest[name] = max(loudest.get(name, 0.0), m.GetPeakValue())
            time.sleep(0.05)
        muted = {re.sub(r"\.exe$", "", s.Process.name(), flags=re.I): bool(s.SimpleAudioVolume.GetMute()) for s in sessions}
        return [(name, muted.get(name, False)) for name, peak in loudest.items() if peak > 0.01]
    finally:
        from brain.com_guard import end as _com_end; _com_end()


def output_level() -> float | None:
    """How loud the sound leaving this PC is right now (0 to 1): Windows' own peak meter on the default speakers.
    None when it cannot be read. This is the real level, never a made-up animation."""
    try:
        import comtypes
        from comtypes import CLSCTX_ALL
        from ctypes import POINTER, cast
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        comtypes.CoInitialize()
        try:
            speakers = AudioUtilities.GetSpeakers()
            speakers = getattr(speakers, "_dev", speakers)       # newer pycaw wraps the COM device
            meter = cast(speakers.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None), POINTER(IAudioMeterInformation))
            return round(float(meter.GetPeakValue()), 3)
        finally:
            from brain.com_guard import end as _com_end; _com_end()
    except Exception:
        return None


_PLAYBACK = {4: "playing", 5: "paused", 3: "stopped"}


MEDIA_TIMEOUT_S = 3.0   # a media read never holds a turn longer than this
MEDIA_CACHE_S = 2.0     # the per-turn status may reuse a read this recent
_media_loop = None      # one event loop on one worker thread, started once
_media_lock = threading.Lock()
_media_last: tuple[float, list[dict]] = (0.0, [])


def _media_worker() -> "asyncio.AbstractEventLoop":
    """The media reader's own thread and loop, in the multi-threaded COM
    apartment. It used to run asyncio.run() on whichever thread asked,
    including the server's loop thread, and WinRT failed there with
    RPC_E_WRONG_THREAD (0x8001010e), swallowed, so the status came back empty."""
    import asyncio
    global _media_loop
    with _media_lock:
        if _media_loop is None:
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def run():
                try:
                    from winrt.runtime import ApartmentType, init_apartment
                    init_apartment(ApartmentType.MULTI_THREADED)
                except Exception:
                    pass  # already initialised, or no WinRT: reads then fail and say so
                asyncio.set_event_loop(loop)
                ready.set()
                loop.run_forever()

            threading.Thread(target=run, name="mbuddy-media", daemon=True).start()
            ready.wait(2.0)
            _media_loop = loop
        return _media_loop


async def _read_media() -> list[dict]:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
    manager = await Manager.request_async()
    items = []
    for session in manager.get_sessions():
        props = await session.try_get_media_properties_async()
        status = _PLAYBACK.get(int(session.get_playback_info().playback_status), "")
        if props.title and status:
            app = re.sub(r"\.exe$", "", (session.source_app_user_model_id or "").split("!")[-1], flags=re.I)
            items.append({"app": app, "title": props.title, "artist": props.artist or "", "status": status,
                          **_timeline(session, status)})
    return items


def _timeline(session, status: str) -> dict:
    """Where the track is and how long it is, in seconds; zeros when the
    player doesn't say (many don't). The position a player reports is from
    its last update, so while playing the time since then is added."""
    try:
        from datetime import datetime, timezone
        line = session.get_timeline_properties()
        length = (line.end_time - line.start_time).total_seconds()
        position = (line.position - line.start_time).total_seconds()
        if status == "playing" and length > 0:
            position += max(0.0, (datetime.now(timezone.utc) - line.last_updated_time).total_seconds())
        if length <= 0:
            return {"position_s": 0, "duration_s": 0}
        return {"position_s": int(max(0, min(position, length))), "duration_s": int(length)}
    except Exception:
        return {"position_s": 0, "duration_s": 0}


MEDIA_COMMANDS = ("toggle", "next", "previous")


async def _send_media(app: str, command: str) -> bool:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
    manager = await Manager.request_async()
    for session in manager.get_sessions():
        name = re.sub(r"\.exe$", "", (session.source_app_user_model_id or "").split("!")[-1], flags=re.I)
        if name.lower() != app.lower():
            continue
        if command == "toggle":
            return bool(await session.try_toggle_play_pause_async())
        if command == "next":
            return bool(await session.try_skip_next_async())
        return bool(await session.try_skip_previous_async())
    return False


def media_command(app: str, command: str) -> bool:
    """The user's own click on a player button (the panel's music card): sent
    to that player's media session, the way the keyboard's media keys or
    Windows' own flyout would. True when the player accepted it. This is not
    the model acting: MBUDDY's own media actions still ask first (request())."""
    import asyncio
    if command not in MEDIA_COMMANDS or not app:
        return False
    future = asyncio.run_coroutine_threadsafe(_send_media(app, command), _media_worker())
    try:
        return bool(future.result(timeout=MEDIA_TIMEOUT_S))
    except Exception as exc:
        future.cancel()
        log.warn(f"[PC] media command failed: {type(exc).__name__}: {exc}"[:200])
        return False


def now_playing(max_age: float = 0.0) -> list[dict]:
    """What Windows' media session reports: exact title, artist, app, and
    whether it's playing (window titles made the model guess; live it invented
    an artist). Empty when nothing is registered or the reader isn't available.
    Read fresh unless the caller accepts a read up to `max_age` seconds old;
    checks that a song really changed always read fresh."""
    import asyncio
    global _media_last
    at, items = _media_last
    if max_age > 0 and time.monotonic() - at < max_age:
        return [dict(m) for m in items]
    future = asyncio.run_coroutine_threadsafe(_read_media(), _media_worker())
    try:
        items = future.result(timeout=MEDIA_TIMEOUT_S)
    except Exception as exc:
        future.cancel()
        log.warn(f"[PC] media read failed: {type(exc).__name__}: {exc}"[:200])
        items = []
    _media_last = (time.monotonic(), items)
    return [dict(m) for m in items]


def pc_status_text() -> str:
    """What the reply is told: real, read just now."""
    windows = list_windows()
    lines = []
    for w in windows[:15]:
        flags = " (in front)" if w.focused else " (minimized)" if w.minimized else ""
        lines.append(f"- {w.app}: {w.title[:80]}{flags}")
    front = next((w for w in windows if w.focused), None)
    head = f"Open windows ({len(windows)}):" if windows else "No app windows are open."
    try:
        sound = []
        for app, muted in playing_audio():
            title = next((w.title[:60] for w in windows if w.app.lower() == app.lower()), "")
            sound.append(f"{app}{f' ({title})' if title else ''}{' - muted' if muted else ''}")
        audio_line = "Playing sound: " + "; ".join(sound) if sound else "Playing sound: nothing right now."
    except Exception:
        audio_line = "Playing sound: couldn't read the audio sessions."
    try:
        from brain.tools import ToolFailure, execute_volume_tool
        volume = execute_volume_tool("what's the volume")
        volume_line = f"PC volume: {volume}" if not isinstance(volume, ToolFailure) else "PC volume: couldn't read it."
    except Exception:
        volume_line = "PC volume: couldn't read it."
    media = now_playing(max_age=MEDIA_CACHE_S)  # every turn: a read this recent is fine
    media_lines = [f"Media: \"{m['title']}\"{' by ' + m['artist'] if m['artist'] and m['artist'] not in m['title'] else ''}"
                   f" in {m['app']} ({m['status']})" for m in media] or ["Media: nothing reported by Windows' media controls."]
    return "\n".join([head, *lines,
                      f"In front: {front.app} ({front.title[:60]})" if front else "In front: the desktop or MBUDDY",
                      *media_lines, audio_line, volume_line,
                      f"System: {system_state()}"])


# Questions about the PC's state right now are always read live, never
# answered from the conversation (live: "Volume is at 0%" when it was 10%).
_PC_QUESTION = re.compile(
    r"\b(playing|running|open)\b.{0,25}\?|\b(what|which|any(thing)?|is (there )?anything|what'?s)\b.{0,30}"
    r"\b(playing|running|open|making (that |this |a |any )?(noise|sound))\b"
    r"|\bapps?\b.{0,15}\b(open|running)\b|\bhow'?s? (is )?my (battery|pc|computer|laptop)\b",
    re.IGNORECASE,
)


def is_pc_question(text: str) -> bool:
    return bool(_PC_QUESTION.search(text or ""))


def find_window(target: str) -> Window | None:
    """The window the user means: app name first, then words in the title."""
    t = (target or "").strip().lower()
    if not t:
        return None
    windows = list_windows()
    t_app = re.sub(r"\.exe$", "", t)
    for w in windows:
        if w.app.lower() == t_app:
            return w
    for w in windows:
        if t in w.title.lower() or t_app in w.app.lower():
            return w
    words = [x for x in re.findall(r"\w+", t) if len(x) > 2]
    for w in windows:
        hay = f"{w.app} {w.title}".lower()
        if words and all(x in hay for x in words):
            return w
    return None


# ── direct commands ──────────────────────────────────────────────────────
_COMMAND = re.compile(
    r"^\s*(?:(?:hey |hi )?buddy,?\s*)?(?:please\s+|can you\s+|could you\s+)?"
    r"(close|minimi[sz]e|hide|bring up|switch to|focus|show me)\s+"
    r"(?:the\s+|my\s+|that\s+)?(.+?)(?:\s+(?:window|app))?(?:\s+please)?\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_VERB_ACTION = {"close": "close", "minimize": "minimize", "minimise": "minimize", "hide": "minimize",
                "bring up": "focus", "switch to": "focus", "focus": "focus", "show me": "focus"}


_MEDIA_COMMAND = re.compile(
    r"^\s*(?:(?:hey |hi )?buddy,?\s*)?(?:(?:ok(?:ay)?|then|so|yes|yeah),?\s*)?(?:please\s+|can you\s+|could you\s+)?"
    r"(pause|resume|unpause|stop)\s*(?:it|that|this|the (?:music|song|audio|sound|video)|(?:the )?(.+?))?"
    r"(?:\s+please)?\s*[.!?]?\s*$",
    re.IGNORECASE,
)


_SKIP_COMMAND = re.compile(
    r"^\s*(?:(?:hey |hi )?buddy,?\s*)?(?:please\s+|can you\s+|could you\s+)?"
    r"(?:(next|skip)(?: (?:this|the|to the next))? ?(?:song|track|one)?|(?:play the |go to the |go back to the )?(previous|last) (?:song|track|one)"
    r"|change (?:the|this) (?:song|track)(?: i'?m listening to)?)"
    r"(?:\s+please)?\s*[.!?]?\s*$",
    re.IGNORECASE,
)


def direct_command(text: str) -> tuple[str, str] | None:
    """("close", "Spotify") for "close Spotify"; ("pause", "") for "pause it";
    ("next", "") for "skip this song" / "change the song"."""
    m = _SKIP_COMMAND.match(text or "")
    if m:
        return ("previous" if m.group(2) else "next"), ""
    m = _MEDIA_COMMAND.match(text or "")
    if m:
        verb = m.group(1).lower()
        target = (m.group(2) or "").strip()
        if verb == "stop" and target and target.lower() not in ("music", "song", "audio", "sound", "video"):
            return None  # "stop worrying": not a media command
        return ("resume" if verb in ("resume", "unpause") else "pause"), target
    m = _COMMAND.match(text or "")
    if not m:
        return None
    return _VERB_ACTION[m.group(1).lower()], m.group(2).strip()


# ── acting, only after a yes ─────────────────────────────────────────────
@dataclass
class Pending:
    action: str
    hwnd: int
    app: str
    title: str
    expires: float
    resume_after_s: float = 0.0     # pause for this long, then play it again (0: just pause)

    def describe(self) -> str:
        if self.action in ("next", "previous"):
            now = f" (now: \"{self.title[:60]}\")" if self.title else ""
            return f"skip to the {self.action} song in {self.app}{now}"
        if self.action in MEDIA_ACTIONS:
            where = f" ({self.title[:60]})" if self.title else ""
            again = f" and play it again in {_span(self.resume_after_s)}" if self.action == "pause" and self.resume_after_s > 0 else ""
            return f"{self.action} what's playing in {self.app}{where}{again}"
        what = {"focus": "bring up", "minimize": "minimize", "close": "close"}[self.action]
        return f"{what} {self.app} ({self.title[:60]})"


_pending: Pending | None = None
_lock = threading.Lock()


def _publish() -> None:
    """Let the app and the buddy show the question (and clear it after)."""
    try:
        from core.status import runtime_status
        p = pending()
        runtime_status.update(pending_action={"text": p.describe(), "expires": p.expires} if p else {})
    except Exception:
        pass


def pending() -> Pending | None:
    global _pending
    with _lock:
        if _pending and time.time() > _pending.expires:
            _pending = None
        return _pending


def _newest_wins() -> None:
    """A new question replaces a waiting pc_actions one, so a yes always
    answers the question just asked (docs/audit/SENIOR_REVIEW.md, C3)."""
    try:
        from brain import pc_actions
        pc_actions.drop_pending()
    except Exception:
        pass


def drop_pending() -> None:
    """Forget the waiting question without publishing (pc_actions asked a newer one)."""
    global _pending
    with _lock:
        _pending = None


def request(action: str, target: str, resume_after_min: float = 0.0) -> str:
    """Store the action and tell the reply to ask; nothing is done yet."""
    global _pending
    from brain.tools import ToolFailure
    if action in MEDIA_ACTIONS:
        return _request_media(action, target, resume_after_min)
    if action not in ACTIONS:
        return ToolFailure(f"I can only focus, minimize or close a window, not '{action}'.")
    w = find_window(target)
    if w is None:
        return ToolFailure(f"There's no open window matching '{target}'.")
    from brain.pc_actions import blocked_app
    off = blocked_app(w.app, w.app)
    if off:
        return ToolFailure(f"{off} is switched off in your extensions, so I won't touch it.")
    p = Pending(action, w.hwnd, w.app, w.title, time.time() + CONFIRM_WINDOW_S)
    _newest_wins()
    with _lock:
        _pending = p
    _publish()
    return (f"NOT DONE YET - waiting for the user's yes. Ask in one short question whether to "
            f"{p.describe()}. Do not say it is done.")


def request_hwnd(action: str, hwnd: int) -> bool:
    """Ask about one known window (one MBUDDY opened itself); False when it's gone. Nothing is done yet."""
    global _pending
    w = next((w for w in list_windows() if w.hwnd == hwnd), None)
    if w is None or action not in ACTIONS:
        return False
    p = Pending(action, w.hwnd, w.app, w.title, time.time() + CONFIRM_WINDOW_S)
    _newest_wins()
    with _lock:
        _pending = p
    _publish()
    return True


_last_paused: tuple[str, str] | None = None  # (app, title) MBUDDY paused, for "resume"
_PAUSED_KEEP_S = 6 * 3600                    # what MBUDDY paused is still "what he stopped" for this long


def _span(seconds: float) -> str:
    m = max(1, round(seconds / 60))
    return f"{m // 60} hour{'s' if m >= 120 else ''}" if m % 60 == 0 and m >= 60 else f"{m} minute{'s' if m != 1 else ''}"


def _paused_path():
    """Where "what I paused" is kept so a restart does not forget it (live 2026-10-11: paused, the backend restarted, "play the
    music" answered "I haven't paused anything"). None under the tests, which must not write the user's home folder."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    from pathlib import Path
    return Path(os.environ.get("MBUDDY_PAUSED_PATH") or (Path.home() / ".buddy" / "last_paused.json"))


def _remember_paused(app: str, title: str) -> None:
    global _last_paused
    _last_paused = (app, title) if app else None
    path = _paused_path()
    if path is None:
        return
    try:
        import json
        path.parent.mkdir(parents=True, exist_ok=True)
        if app:
            path.write_text(json.dumps({"app": app, "title": title, "at": time.time()}), encoding="utf-8")
        elif path.exists():
            path.unlink()
    except OSError:
        pass


def _paused_by_me() -> tuple[str, str] | None:
    """What MBUDDY paused: in memory, else what it wrote down before a restart (not older than a few hours)."""
    if _last_paused:
        return _last_paused
    path = _paused_path()
    if path is None:
        return None
    try:
        import json
        d = json.loads(path.read_text(encoding="utf-8"))
        if d.get("app") and time.time() - float(d.get("at", 0)) < _PAUSED_KEEP_S:
            return (str(d["app"]), str(d.get("title", "")))
    except (OSError, ValueError):
        pass
    return None


def _paused_in_a_player() -> tuple[str, str] | None:
    """A player Windows' media controls show as paused (what could be resumed even if MBUDDY did not stop it)."""
    try:
        m = next((m for m in now_playing() if m.get("status") == "paused"), None)
    except Exception:
        return None
    return (m["app"], m["title"]) if m else None


def _request_media(action: str, target: str, resume_after_min: float = 0.0):
    """Pause what's really playing / resume what MBUDDY paused; ask first."""
    global _pending
    from brain.tools import ToolFailure
    if action in ("next", "previous"):
        # Only a player Windows' media controls know, so the result can be checked.
        media = sorted(now_playing(), key=lambda m: m["status"] != "playing")
        if not media:
            return ToolFailure("No music player is showing in Windows' media controls, so I can't skip.")
        m = media[0]
        p = Pending(action, 0, m["app"], m["title"], time.time() + CONFIRM_WINDOW_S)
        _newest_wins()
        with _lock:
            _pending = p
        _publish()
        return (f"NOT DONE YET - waiting for the user's yes. Ask in one short question whether to "
                f"{p.describe()}. Do not say it is done.")
    playing = [app for app, _muted in playing_audio()]
    t = (target or "").strip().lower()
    windows = list_windows()
    if action == "pause":
        if not playing:
            return ToolFailure("Nothing is playing sound right now, so there's nothing to pause.")
        app = next((a for a in playing if t and (t in a.lower() or any(t in w.title.lower() for w in windows if w.app.lower() == a.lower()))), playing[0])
    else:
        if playing:
            return ToolFailure(f"{', '.join(playing)} is already playing sound.")
        mine = _paused_by_me() or _paused_in_a_player()
        if mine is None and not t:
            return ToolFailure("Nothing is paused that I know of: no player shows a paused song, and I have not paused anything lately.")
        app = mine[0] if mine and (not t or t in mine[0].lower()) else (target or "")
    title = next((w.title for w in windows if w.app.lower() == app.lower()), "")
    after = max(0.0, min(float(resume_after_min or 0), 8 * 60)) * 60 if action == "pause" else 0.0
    p = Pending(action, 0, app, title, time.time() + CONFIRM_WINDOW_S, resume_after_s=after)
    _newest_wins()
    with _lock:
        _pending = p
    _publish()
    return (f"NOT DONE YET - waiting for the user's yes. Ask in one short question whether to "
            f"{p.describe()}. Do not say it is done.")


def _auto_resume(app: str, title: str) -> None:
    """The pause the user asked for is over: play it again, once, and only if nothing else has started it meanwhile."""
    from brain import action_log
    try:
        if any(a.lower() == app.lower() for a, _m in playing_audio()):
            return
        user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
        user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 2, 0)
        for _ in range(8):
            time.sleep(0.25)
            if any(a.lower() == app.lower() for a, _m in playing_audio()):
                _remember_paused("", "")
                result = f"Resumed {app}; it's playing again (the pause you asked for was over)."
                break
        else:
            result = f"The pause was over, but {app} did not start playing again."
    except Exception as exc:  # noqa: BLE001
        result = f"The pause was over, but I could not start {app}: {exc}"
    log.info(f"[PC] auto-resume: {result}")
    action_log.record("media_control", {"action": "resume", "app": app, "after_pause": True}, result)


def _execute_media(p: Pending) -> str:
    """The media key toggles, so it's pressed only in the right state, then the
    app's own sound meter says whether it really stopped / started."""
    from brain.tools import ToolFailure
    if p.action in ("next", "previous"):
        key = VK_MEDIA_NEXT_TRACK if p.action == "next" else VK_MEDIA_PREV_TRACK
        user32.keybd_event(key, 0, 0, 0)
        user32.keybd_event(key, 0, 2, 0)
        for _ in range(16):  # up to 4 s for the player to change track
            time.sleep(0.25)
            now = next((m for m in now_playing() if m["app"].lower() == p.app.lower()), None)
            if now and now["title"] != p.title:
                by = f" by {now['artist']}" if now["artist"] and now["artist"] not in now["title"] else ""
                return f"Skipped. Now playing \"{now['title']}\"{by} in {p.app}."
        return ToolFailure(f"I pressed {p.action}, but {p.app} still shows \"{p.title[:60]}\".")
    playing_now = any(a.lower() == p.app.lower() for a, _m in playing_audio())
    if p.action == "pause" and not playing_now:
        return ToolFailure(f"{p.app} had already stopped playing.")
    if p.action == "resume" and playing_now:
        return ToolFailure(f"{p.app} is already playing.")
    user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
    user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 2, 0)  # KEYEVENTF_KEYUP
    for _ in range(8):
        time.sleep(0.25)
        playing_now = any(a.lower() == p.app.lower() for a, _m in playing_audio())
        if p.action == "pause" and not playing_now:
            _remember_paused(p.app, p.title)
            if p.resume_after_s > 0:
                t = threading.Timer(p.resume_after_s, _auto_resume, args=(p.app, p.title))
                t.daemon = True
                t.start()
                return f"Paused what was playing in {p.app}. It will play again in {_span(p.resume_after_s)}."
            return f"Paused what was playing in {p.app}."
        if p.action == "resume" and playing_now:
            _remember_paused("", "")
            return f"Resumed {p.app}; it's playing again."
    return ToolFailure(f"I pressed {p.action}, but {p.app} {'is still playing' if p.action == 'pause' else 'is still silent'}.")


_YES = re.compile(r"^\s*(yes|yeah|yep|yup|sure|ok(ay)?|do it|go ahead|please do|confirm(ed)?|affirmative)\b", re.I)
_NO = re.compile(r"^\s*(no|nope|nah|don'?t|do not|cancel|stop|never ?mind|leave it)\b", re.I)


def decision(text: str) -> str | None:
    """'yes', 'no', or None when the words are neither."""
    if _NO.search(text or ""):
        return "no"
    if _YES.search(text or ""):
        return "yes"
    return None


def resolve(text_or_decision: str) -> tuple[str, dict, str] | None:
    """
    Settle a pending action with the user's answer: a clear yes does it, a no
    cancels it. Anything else (live: someone else in the room said "Good
    luck!") is not an answer: it returns None and the question keeps waiting
    until it expires. Nothing is ever done without a yes.
    """
    global _pending
    p = pending()
    if p is None:
        return None
    d = text_or_decision if text_or_decision in ("yes", "no") else decision(text_or_decision)
    if d is None:
        return None
    with _lock:
        _pending = None
    _publish()
    args = {"action": p.action, "target": p.app}
    if d == "no":
        return ("window_action", args, f"Cancelled: the user said no, so nothing was done to {p.app}.")
    result = _execute(p)
    from brain import action_log
    action_log.record("window_action", args, result)
    return ("window_action", args, result)


def _execute(p: Pending) -> str:
    """Do it, then check at the source whether it really happened."""
    from brain.tools import ToolFailure
    if p.action in MEDIA_ACTIONS:
        return _execute_media(p)
    hwnd = p.hwnd
    if not user32.IsWindow(hwnd):
        return ToolFailure(f"{p.app} was already closed.")
    if p.action == "close":
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE: the app may still ask to save
        for _ in range(20):
            time.sleep(0.1)
            if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
                return f"Closed {p.app} ({p.title[:60]})."
        return ToolFailure(f"Asked {p.app} to close, but it's still open (it may be asking to save).")
    if p.action == "minimize":
        user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        time.sleep(0.2)
        return f"Minimized {p.app}." if user32.IsIconic(hwnd) else ToolFailure(f"{p.app} didn't minimize.")
    # focus
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)
    if user32.GetForegroundWindow() == hwnd:
        return f"Brought {p.app} to the front."
    return ToolFailure(f"Windows didn't let me bring {p.app} to the front (it only allows that in some cases).")
