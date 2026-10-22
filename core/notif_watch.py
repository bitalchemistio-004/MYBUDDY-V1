"""Messages that are waiting for you: MBUDDY reads the notifications Windows already shows you and reminds you
about the ones you have not looked at.

How it works, and what it never does:
- Windows keeps the notifications that are in the Action Center in a small database in YOUR profile
  (%LOCALAPPDATA%\\Microsoft\\Windows\\Notifications\\wpndatabase.db). MBUDDY reads a copy of it; nothing is installed in
  WhatsApp or anywhere else, nothing is sent anywhere, no app is opened or controlled.
- It is OFF until the user switches it on (Settings, Messages). Only the apps the user chose are kept; for every other
  app only its name and a count are noticed, so the Settings screen can offer it as a choice. Their text is dropped.
- A message counts as "waiting" while its notification is still there and its app has not been in front of you since
  it arrived. When it has waited long enough (sooner for an important one) the buddy reminds you, a few times at most.
  Opening the app, clearing the notification, "In 10 min", "Mute this chat" or "I saw it" all settle it.
- What is important is decided by what the user said (people and words they care about), then, if the user allows it,
  by the local model. Text only goes to the model on this PC; with the cloud brain it goes nowhere unless the user
  allowed that too.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import utils.logger as log

POLL_S = 6.0
KEEP_DAYS = 7
from core import ui_settings as _ui_settings

DEFAULTS = _ui_settings.DEFAULTS["notify"]
# What is called what: a word in the notification's app id -> the name shown, and the programs that mean "you are in that app".
APPS = {
    "WhatsApp": (["whatsapp"], ["whatsapp", "whatsapp.root"]),
    "Telegram": (["telegram"], ["telegram"]),
    "Teams": (["msteams", "teams"], ["ms-teams", "teams"]),
    "Slack": (["slack"], ["slack"]),
    "Discord": (["discord"], ["discord"]),
    "Signal": (["signal"], ["signal"]),
    "Messenger": (["messenger"], ["messenger"]),
    "Skype": (["skype"], ["skype"]),
    "Outlook": (["outlook", "olk"], ["olk", "outlook"]),
    "Mail": (["windowscommunicationsapps"], ["hxoutlook", "hxmail"]),
}
_lock = threading.RLock()
_wake = threading.Event()
_thread: threading.Thread | None = None
_focus: dict[str, float] = {}           # app name -> when it was last in front of the user
_apps_seen: dict[str, int] = {}         # app name -> notifications noticed (counts only, for the choice list)
_last_db_mark: tuple = ()


def db_path() -> Path:
    override = os.getenv("MBUDDY_WPN_DB")
    return Path(override) if override else Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "Microsoft" / "Windows" / "Notifications" / "wpndatabase.db"


def state_path() -> Path:
    override = os.getenv("MBUDDY_NOTIFY_PATH")
    return Path(override) if override else Path.home() / ".buddy" / "notifications.json"


# ── settings ─────────────────────────────────────────────────────────────
def settings() -> dict:
    from core import ui_settings
    stored = ui_settings.get("notify") or {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def friendly(app_id: str) -> str:
    low = (app_id or "").lower()
    for name, (needles, _procs) in APPS.items():
        if any(n in low for n in needles):
            return name
    tail = re.split(r"[!\\]", app_id or "")[-1] if "!" in (app_id or "") else (app_id or "")
    base = re.split(r"[_]", tail)[0] if "_" in tail else tail
    base = base.split(".")[-1] if "." in base and " " not in base else base
    return (re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base).strip() or "Unknown app")[:40]


# ── reading Windows' notification database ───────────────────────────────
def _filetime_to_epoch(ft: int) -> float:
    return ft / 1e7 - 11644473600.0


def _parse_toast(payload) -> tuple[str, str]:
    text = payload.decode("utf-8", "ignore") if isinstance(payload, (bytes, bytearray)) else str(payload or "")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return "", ""
    parts = [(t.text or "").strip() for t in root.iter("text")]
    parts = [p for p in parts if p]
    return (parts[0] if parts else ""), " ".join(parts[1:])


def read_database(path: Path | None = None) -> list[dict]:
    """The toasts that are in the Action Center right now: [{id, app_id, app, title, text, arrived}]. [] when the
    database can't be read (it is copied first, so Windows is never held up)."""
    path = path or db_path()
    if not path.exists():
        return []
    tmp = Path(tempfile.mkdtemp(prefix="mbuddy-wpn-"))
    try:
        for ext in ("", "-wal", "-shm"):
            src = Path(str(path) + ext)
            if src.exists():
                shutil.copy(src, tmp / ("w.db" + ext))
        con = sqlite3.connect(str(tmp / "w.db"))
        try:
            rows = con.execute("select n.Id, h.PrimaryId, n.ArrivalTime, n.Payload from Notification n "
                               "join NotificationHandler h on h.RecordId = n.HandlerId where n.Type = 'toast'").fetchall()
        finally:
            con.close()
    except (OSError, sqlite3.Error) as exc:
        log.warn(f"[Messages] could not read the notification database: {type(exc).__name__}")
        return []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out = []
    for nid, app_id, arrival, payload in rows:
        title, text = _parse_toast(payload)
        if not (title or text):
            continue
        out.append({"id": f"{nid}", "app_id": app_id, "app": friendly(app_id), "title": title, "text": text,
                    "arrived": _filetime_to_epoch(int(arrival or 0))})
    return out


# ── state the watcher keeps ──────────────────────────────────────────────
_cache: dict = {"path": None, "state": None}


def _load() -> dict:
    """The saved state, kept in memory (the live state asks for it several times a second)."""
    path = state_path()
    if _cache["path"] == path and _cache["state"] is not None:
        return _cache["state"]
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {"records": {}}
    _cache.update(path=path, state=state)
    return state


def _save(state: dict) -> None:
    _cache.update(path=state_path(), state=state)
    try:
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warn(f"[Messages] could not save: {exc}")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def watched(app: str, title: str, cfg: dict) -> bool:
    """Is this the kind of message to keep: from a chosen app, from a chat that matters (if some were named), not muted?"""
    if _norm(app) not in {_norm(a) for a in cfg["apps"]}:
        return False
    t = _norm(title)
    if any(_norm(m) and _norm(m) in t for m in cfg["muted"]):
        return False
    chats = [_norm(c) for c in cfg["chats"] if _norm(c)]
    return not chats or any(c in t for c in chats)


def rules_say(title: str, text: str, cfg: dict) -> dict:
    """What can be told without the model: a person on the user's list, an urgent word, a question, a time."""
    t, body = _norm(title), _norm(text)
    vip = [c for c in cfg["chats"] if _norm(c) and _norm(c) in t]
    words = [k for k in cfg["keywords"] if _norm(k) and re.search(rf"\b{re.escape(_norm(k))}\b", body)]
    asks = "?" in text or bool(re.search(r"\b(when|where|can you|could you|are you|did you|please|call)\b", body))
    important = bool(vip or words)
    why = "from someone on your list" if vip else (f"says “{words[0]}”" if words else ("asks you something" if asks else ""))
    return {"important": important, "needs_reply": asks or important, "why": why}


def judge_with_model(app: str, title: str, text: str) -> dict | None:
    """Ask the model on this PC whether a message needs an answer soon. None when it can't answer (never guessed)."""
    try:
        import requests
        from brain import agent
        schema = {"type": "object", "properties": {"important": {"type": "boolean"}, "needs_reply": {"type": "boolean"}, "why": {"type": "string"}},
                  "required": ["important", "needs_reply", "why"]}
        prompt = (f"A message arrived in {app} from {title}: \"{text[:300]}\". Does it need the user's answer soon "
                  "(a question to them, a plan, something urgent or about money, health, work or family)? Answer in JSON.")
        r = requests.post(agent._ollama_chat_url("background"), json={
            "model": agent._chat_model(), "stream": False, "think": False, "format": schema,
            "messages": [{"role": "user", "content": prompt}], "options": {"temperature": 0, "num_predict": 100}}, timeout=25)
        data = json.loads(((r.json().get("message") or {}).get("content")) or "{}")
        return {"important": bool(data.get("important")), "needs_reply": bool(data.get("needs_reply")), "why": str(data.get("why", ""))[:80]}
    except Exception as exc:
        log.warn(f"[Messages] the model could not judge a message: {type(exc).__name__}")
        return None


def _may_use_model(cfg: dict) -> bool:
    from core import cloud_brain
    return bool(cfg["model_judge"]) and (not cloud_brain.active or bool(cfg["share_with_cloud"]))


# ── one pass ─────────────────────────────────────────────────────────────
def process(now: float | None = None, toasts: list[dict] | None = None, judge=judge_with_model) -> None:
    """Take in what is in the Action Center, settle what the user has dealt with, and keep the state tidy."""
    now = time.time() if now is None else now
    cfg = settings()
    toasts = read_database() if toasts is None else toasts
    present = {t["id"] for t in toasts}
    with _lock:
        state = _load()
        recs = state.setdefault("records", {})
        for t in toasts:
            _apps_seen[t["app"]] = _apps_seen.get(t["app"], 0) + (0 if t["id"] in recs else 1)
            if t["id"] in recs or not watched(t["app"], t["title"], cfg):
                continue
            verdict = rules_say(t["title"], t["text"], cfg)
            recs[t["id"]] = {"id": t["id"], "app": t["app"], "title": t["title"][:80], "text": t["text"][:400], "arrived": t["arrived"],
                             "status": "waiting", "alerts": 0, "next_at": 0.0, **verdict, "judged": False}
        for r in recs.values():
            if r["status"] != "waiting":
                continue
            if r["id"] not in present:                                      # cleared or opened from the notification
                r["status"] = "seen"
            elif _focus.get(r["app"], 0) > r["arrived"]:                   # the app was in front of the user since
                r["status"] = "seen"
        # old ones go
        for k in [k for k, r in recs.items() if now - r["arrived"] > KEEP_DAYS * 86400]:
            del recs[k]
        _save(state)
    # The model looks at what the rules could not settle, one message at a time, outside the lock.
    if cfg["enabled"] and _may_use_model(cfg):
        with _lock:
            todo = next((r for r in _load()["records"].values() if r["status"] == "waiting" and not r["judged"] and not r["important"]), None)
        if todo is not None:
            verdict = judge(todo["app"], todo["title"], todo["text"])
            with _lock:
                state = _load()
                rec = state["records"].get(todo["id"])
                if rec:
                    rec["judged"] = True
                    if verdict:
                        rec["important"] = rec["important"] or verdict["important"]
                        rec["needs_reply"] = rec["needs_reply"] or verdict["needs_reply"]
                        rec["why"] = rec["why"] or verdict["why"]
                    _save(state)


def current_alert(now: float | None = None) -> dict | None:
    """The message to remind the user about now, if any: it has waited long enough, is not snoozed, and does not need a
    reply-less shrug (a message that asks nothing and is not important is left alone)."""
    now = time.time() if now is None else now
    cfg = settings()
    if not cfg["enabled"]:
        return None
    from core import ui_settings
    if ui_settings.get("do_not_disturb"):
        return None
    with _lock:
        recs = [r for r in _load()["records"].values() if r["status"] == "waiting" and r["alerts"] < cfg["max_reminders"]]
    due = []
    for r in recs:
        if not (r["important"] or r["needs_reply"]):
            continue
        wait_s = (cfg["important_after_min"] if r["important"] else cfg["remind_after_min"]) * 60
        when = max(r["arrived"] + wait_s, r.get("next_at", 0.0))
        if now >= when:
            due.append((when, r))
    if not due:
        return None
    _when, r = min(due, key=lambda x: (not x[1]["important"], x[0]))
    return {"id": r["id"], "app": r["app"], "who": r["title"], "text": r["text"], "important": r["important"], "why": r["why"],
            "minutes": max(1, int((now - r["arrived"]) / 60)), "alerts": r["alerts"]}


def act(rec_id: str, action: str, now: float | None = None) -> bool:
    """What the user pressed on a reminder: seen, snooze, mute (the chat), or that the reminder was shown."""
    now = time.time() if now is None else now
    cfg = settings()
    with _lock:
        state = _load()
        r = state["records"].get(rec_id)
        if not r:
            return False
        if action == "seen":
            r["status"] = "seen"
        elif action == "snooze":
            r["next_at"] = now + 10 * 60
        elif action == "shown":
            r["alerts"] += 1
            r["next_at"] = now + cfg["repeat_every_min"] * 60
        elif action == "mute":
            r["status"] = "seen"
            from core import ui_settings
            muted = list(cfg["muted"])
            if r["title"] and r["title"] not in muted:
                muted.append(r["title"])
            ui_settings.update({"notify": {"muted": muted}})
        else:
            return False
        _save(state)
    return True


def recent(limit: int = 20) -> list[dict]:
    with _lock:
        recs = sorted(_load()["records"].values(), key=lambda r: -r["arrived"])
    return recs[:limit]


def missed(now: float | None = None) -> list[dict]:
    """The messages still waiting, oldest first (for "what did I miss?")."""
    with _lock:
        return sorted((r for r in _load()["records"].values() if r["status"] == "waiting"), key=lambda r: r["arrived"])


def apps_seen() -> dict[str, int]:
    return dict(_apps_seen)


def forget_all() -> None:
    with _lock:
        _save({"records": {}})


def bring_to_front(app: str) -> bool:
    """The user pressed "Open WhatsApp" on a reminder: put that app's window in front (it is their own click)."""
    procs = APPS.get(app, ([], [app.lower()]))[1]
    try:
        import ctypes
        from brain import pc_windows
        for w in pc_windows.list_windows():
            if str(w.app).lower().removesuffix(".exe") in procs:
                user32 = ctypes.windll.user32
                user32.ShowWindow(w.hwnd, 9)           # restore it if it was minimised
                user32.SetForegroundWindow(w.hwnd)
                note_focus(str(w.app))
                return True
    except Exception as exc:
        log.warn(f"[Messages] could not bring {app} to the front: {type(exc).__name__}")
    return False


# ── which app is in front of the user ────────────────────────────────────
def note_focus(process_name: str, now: float | None = None) -> None:
    low = (process_name or "").lower().removesuffix(".exe")
    for name, (_needles, procs) in APPS.items():
        if low in procs:
            _focus[name] = time.time() if now is None else now


def _foreground_process() -> str:
    try:
        import ctypes
        import psutil
        user32 = ctypes.windll.user32
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
        return psutil.Process(pid.value).name()
    except Exception:
        return ""


# ── the background loop ──────────────────────────────────────────────────
def _db_mark() -> tuple:
    p = db_path()
    try:
        return tuple((q.stat().st_mtime_ns, q.stat().st_size) for q in (p, Path(str(p) + "-wal")) if q.exists())
    except OSError:
        return ()


def _loop() -> None:
    global _last_db_mark
    ticks = 0
    while True:
        _wake.wait(1.0)
        _wake.clear()
        try:
            if not settings()["enabled"]:
                continue
            note_focus(_foreground_process())
            ticks += 1
            mark = _db_mark()
            if mark != _last_db_mark or ticks % 8 == 0:        # the database changed, or every few seconds anyway (focus, ageing)
                _last_db_mark = mark
                process()
        except Exception as exc:                                  # the watcher must never take MBUDDY down
            log.warn(f"[Messages] watcher step failed: {type(exc).__name__}: {exc}")


def start() -> None:
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_loop, name="mbuddy-messages", daemon=True)
        _thread.start()
