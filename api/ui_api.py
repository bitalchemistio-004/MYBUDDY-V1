"""
api/ui_api.py - the HTTP API the MBUDDY app uses (docs/PHASE3_BUILD_PLAN.md 3.1).

Local-only, like the rest of /control. Live state (awake, emotion,
listening/thinking/speaking, what was heard and said) is not here: the app
reads it from the existing /status/stream WebSocket. This file adds what
the app needs besides that: settings, an overview of real state, tasks,
memory and the installation checks.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from api.device_control import _require_local, active_session
from core import setup_checks, ui_settings
from core.status import runtime_status


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


async def _body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="Send a JSON object.")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="Send a JSON object.")
    return data


def _voices() -> list[str]:
    """English Kokoro voices on this PC (the voice setting's choices)."""
    try:
        import numpy as np
        path = Path(__file__).resolve().parent.parent / "models" / "kokoro" / "voices-v1.0.bin"
        with np.load(path) as v:
            return sorted(k for k in v.files if k[:2] in ("af", "am", "bf", "bm"))
    except Exception:
        return [ui_settings.get("voice")]


# Microphones the voice bridge can see (it reports them; only it can list them).
_AUDIO_DEVICES: list[str] = []


async def audio_devices_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data = await _body(request)
    devices = [str(d)[:120] for d in (data.get("devices") or []) if str(d).strip()][:20]
    _AUDIO_DEVICES[:] = devices
    return _json({"ok": True, "devices": devices})


# ── settings ──────────────────────────────────────────────────────────────
async def settings_handler(request: web.Request) -> web.Response:
    _require_local(request)
    if request.method == "POST":
        data = await _body(request)
        try:
            settings = ui_settings.update(data.get("changes", data))
        except ui_settings.SettingsError as exc:
            return _json({"ok": False, "error": str(exc)}, 400)
        changed = data.get("changes", data)
        if isinstance(changed, dict) and "brain_mode" in changed:
            from core import cloud_brain
            await asyncio.to_thread(cloud_brain.apply_now)      # the choice takes effect now, wherever it was made
        return _json({"ok": True, "settings": settings})
    # ?only=settings: just the values, for windows that refetch when
    # settings_rev in /ui/live changes (the options below ask Ollama).
    if request.query.get("only") == "settings":
        return _json({"ok": True, "settings": ui_settings.all_settings()})
    from core import cloud_brain
    models = None if cloud_brain.active else await asyncio.to_thread(setup_checks.ollama_models)   # no questions to a model on this PC in cloud mode
    return _json({
        "ok": True,
        "settings": ui_settings.all_settings(),
        "options": {
            "characters": list(ui_settings.CHARACTERS), "themes": list(ui_settings.THEMES),
            "popup_styles": list(ui_settings.POPUP_STYLES), "tools": list(ui_settings.TOOL_NAMES),
            "voices": await asyncio.to_thread(_voices), "models": models or [], "mics": list(_AUDIO_DEVICES),
            "buddy_sizes": list(ui_settings.BUDDY_SIZES), "buddy_sides": list(ui_settings.BUDDY_SIDES),
            "profile_max": ui_settings.PROFILE_MAX, "companion_style_suggestion": ui_settings.COMPANION_STYLE_SUGGESTION,
            "extensions": [{"id": topic, "title": title, "description": description}
                           for topic, (title, description) in ui_settings.EXTENSION_INFO.items()],
            "app_rules_max": ui_settings.APP_RULES_MAX, "app_name_max": ui_settings.APP_NAME_MAX,
        },
    })


# ── installed apps, for Extensions' "Add an app" (S93) ──────────────────
APPS_SHOWN = 50


def _app_matches(names, query: str) -> list[str]:
    """Installed app names that contain `query` in any case. A name the index
    holds in two cases ("notepad" from App Paths, "Notepad" from the Start
    menu) is listed once, with its capitals. Names that start with the query
    come first, then alphabetical."""
    q = " ".join(query.split()).casefold()
    shown: dict[str, str] = {}
    for name in names:
        key = name.casefold()
        if q in key and (key not in shown or (shown[key].islower() and not name.islower())):
            shown[key] = name
    return sorted(shown.values(), key=lambda name: (not name.casefold().startswith(q), name.casefold()))


async def apps_handler(request: web.Request) -> web.Response:
    """Installed apps whose name contains ?q= (any case), at most 50, for the
    app list in Extensions. Read-only. The list comes from
    brain/pc_actions.app_index(), built once per request in a thread (its
    Store-app part runs PowerShell)."""
    _require_local(request)
    from brain import pc_actions
    index = await asyncio.to_thread(pc_actions.app_index)
    if not index:
        return _json({"ok": False, "error": "I couldn't read the apps installed on this PC."}, 500)
    hits = _app_matches(index, request.query.get("q", "")[:100])
    return _json({"ok": True, "apps": hits[:APPS_SHOWN], "total": len(hits), "installed": len(index)})


# ── overview: real state for the Home screen ─────────────────────────────
def _overview() -> dict:
    from brain import action_log
    from brain.task_tracker import task_tracker
    from brain.tools import ToolFailure, _timer_manager, execute_volume_tool, timer_name

    volume = None
    try:
        text = execute_volume_tool("what's the volume")
        if not isinstance(text, ToolFailure):
            import re
            m = re.search(r"(\d+)%", str(text))
            volume = int(m.group(1)) if m else None
    except Exception:
        pass
    now = datetime.now()
    pending = task_tracker.get_pending_tasks()
    upcoming = sorted((t for t in pending if t.due_date), key=lambda t: t.due_date)
    upcoming = [t for t in upcoming if datetime.fromisoformat(t.due_date) >= now]
    return {
        "volume": volume,
        "timers": [{"key": t.label, "label": f"{timer_name(t)} timer".capitalize(), "remaining_s": int(t.remaining_seconds()), "duration_s": t.duration_seconds}
                   for t in _timer_manager.list_timers()],
        "next_reminder": upcoming[0].to_dict() if upcoming else None,
        "pending_tasks": len(pending),
        "recent_actions": action_log.recent(10),
    }


async def rewards_handler(request: web.Request) -> web.Response:
    """Level, XP, streak, badges and the latest awards, from what really happened (core/rewards.py)."""
    _require_local(request)
    from core import rewards
    return _json({"ok": True, **await asyncio.to_thread(rewards.snapshot)})


async def wardrobe_handler(request: web.Request) -> web.Response:
    """What the character may wear (unlocked by real progress) and what it wears. POST changes one thing:
    {"slot", "item"} (item "" takes it off), {"slot", "color"} (that item's own colour, "" follows the ink),
    {"slot", "style"} ("line" or "pixel"), {"finish"}, {"ink"} or {"reset": true}; {"preview": {"kind": "wear"|"notch"|"app", "part", "value"}}
    tries a decoration on for 90 seconds without earning it (nothing is saved), {"stop_preview": true} ends it. Each body may carry {"character"} (whose wardrobe;
    otherwise the buddy that is chosen now), and GET takes ?character=."""
    _require_local(request)
    from core import wardrobe
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        who = str(body.get("character") or "")                 # whose wardrobe: empty means the buddy that is chosen now
        if isinstance(body.get("preview"), dict):
            u = body["preview"]
            ok, why = await asyncio.to_thread(wardrobe.preview, str(u.get("kind") or ""), str(u.get("part") or ""), str(u.get("value") or ""), who)
        elif body.get("stop_preview"):
            await asyncio.to_thread(wardrobe.stop_preview)
            ok, why = True, ""
        elif body.get("reset"):
            await asyncio.to_thread(wardrobe.take_everything_off, who)
            ok, why = True, ""
        elif "finish" in body:
            ok, why = await asyncio.to_thread(wardrobe.set_finish, str(body.get("finish") or ""))
        elif "color" in body:
            ok, why = await asyncio.to_thread(wardrobe.set_color, str(body.get("slot") or ""), str(body.get("color") or ""), who)
        elif "style" in body:
            ok, why = await asyncio.to_thread(wardrobe.set_style, str(body.get("slot") or ""), str(body.get("style") or ""), who)
        elif "notch" in body and isinstance(body["notch"], dict):
            part, value = next(iter(body["notch"].items()), ("", ""))
            ok, why = await asyncio.to_thread(wardrobe.set_notch, str(part), str(value))
        elif "app" in body and isinstance(body["app"], dict):
            part, value = next(iter(body["app"].items()), ("", ""))
            ok, why = await asyncio.to_thread(wardrobe.set_app, str(part), str(value))
        elif "ink" in body:
            ok, why = await asyncio.to_thread(wardrobe.set_ink, str(body.get("ink") or ""))
        else:
            ok, why = await asyncio.to_thread(wardrobe.wear, str(body.get("slot") or ""), str(body.get("item") or ""), None, who)
        st = await asyncio.to_thread(wardrobe.state, None, who)
        return _json({"ok": ok, **({"error": why} if not ok else {}), **st}, status=200 if ok else 400)
    return _json({"ok": True, **await asyncio.to_thread(wardrobe.state, None, request.query.get("character"))})


async def room_handler(request: web.Request) -> web.Response:
    """Each character's room and the reward points that build it (core/room.py). POST changes one thing:
    {"upgrade": {"character", "place"}}, {"buy": {"character", "item"}}, {"move": {"character", "thing", "dx"}},
    {"put_back": "character"}, {"game": {"id", "score", "lower": bool}} (keeps the best score; pays nothing),
    {"trophy": "game"} or {"egg": "id"}."""
    _require_local(request)
    from core import room
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        ok, why = True, ""
        if isinstance(body.get("upgrade"), dict):
            u = body["upgrade"]
            ok, why = await asyncio.to_thread(room.upgrade, str(u.get("character") or ""), str(u.get("place") or ""))
        elif isinstance(body.get("buy"), dict):
            u = body["buy"]
            ok, why = await asyncio.to_thread(room.buy, str(u.get("character") or ""), str(u.get("item") or ""))
        elif isinstance(body.get("move"), dict):
            u = body["move"]
            try:
                dx = int(u.get("dx") or 0)
            except (TypeError, ValueError):
                dx = 0
            ok, why = await asyncio.to_thread(room.move, str(u.get("character") or ""), str(u.get("thing") or ""), dx)
        elif body.get("put_back"):
            await asyncio.to_thread(room.put_back, str(body["put_back"]))
        elif isinstance(body.get("game"), dict):
            u = body["game"]
            try:
                score = int(u.get("score") or 0)
            except (TypeError, ValueError):
                score = 0
            await asyncio.to_thread(room.record_game, str(u.get("id") or ""), score, bool(u.get("lower")))
        elif body.get("trophy"):
            await asyncio.to_thread(room.trophy, str(body["trophy"]))
        elif body.get("egg"):
            await asyncio.to_thread(room.find_egg, str(body["egg"]))
        st = await asyncio.to_thread(room.state)
        return _json({"ok": ok, **({"error": why} if not ok else {}), **st}, status=200 if ok else 400)
    return _json({"ok": True, **await asyncio.to_thread(room.state)})


async def overview_handler(request: web.Request) -> web.Response:
    _require_local(request)
    return _json({"ok": True, **await asyncio.to_thread(_overview)})


async def system_volume_handler(request: web.Request) -> web.Response:
    """The Windows volume (not MBUDDY's own voice level, which is /control/volume)."""
    _require_local(request)
    from brain import action_log
    from brain.tools import ToolFailure, apply_volume_action
    if request.method == "POST":
        data = await _body(request)
        level = data.get("level")
        if isinstance(level, bool) or not isinstance(level, (int, float)) or not 0 <= level <= 100:
            return _json({"ok": False, "error": "level must be 0-100"}, 400)
        result = await asyncio.to_thread(apply_volume_action, "set", int(level))
        if isinstance(result, ToolFailure):
            return _json({"ok": False, "error": str(result)}, 500)
        action_log.record("app_volume", {"level": int(level)}, f"You set the volume to {int(level)}% in the app.")
    overview = await asyncio.to_thread(_overview)
    return _json({"ok": overview["volume"] is not None, "level": overview["volume"]})


# ── tasks and reminders ───────────────────────────────────────────────────
async def tasks_handler(request: web.Request) -> web.Response:
    _require_local(request)
    from brain.task_tracker import parse_time_reference, task_tracker
    if request.method == "POST":
        data = await _body(request)
        title = str(data.get("title", "")).strip()
        if not title or len(title) > 200:
            return _json({"ok": False, "error": "A task needs a title (up to 200 characters)."}, 400)
        when = str(data.get("when", "")).strip()
        due = parse_time_reference(when) if when else None
        if when and not due:
            return _json({"ok": False, "error": f"I couldn't understand the time '{when}'."}, 400)
        task = await asyncio.to_thread(task_tracker.create_task, title, due_date=due)
        return _json({"ok": True, "task": task.to_dict()})
    tasks = await asyncio.to_thread(task_tracker.get_pending_tasks, 200)
    return _json({"ok": True, "tasks": [t.to_dict() for t in tasks]})


async def task_item_handler(request: web.Request) -> web.Response:
    _require_local(request)
    from brain.task_tracker import task_tracker
    try:
        task_id = int(request.match_info["task_id"])
    except ValueError:
        raise web.HTTPBadRequest(text="Bad task id.")
    if request.method == "DELETE":
        ok = await asyncio.to_thread(task_tracker.delete_task, task_id)
    else:  # POST .../done
        ok = await asyncio.to_thread(task_tracker.complete_task, task_id)
    return _json({"ok": bool(ok)} if ok else {"ok": False, "error": "No such task."}, 200 if ok else 404)


# ── memory: what MBUDDY knows about you ──────────────────────────────────
def _live_memory() -> dict:
    session = active_session()
    if session is not None and isinstance(getattr(session, "memory", None), dict):
        return session.memory  # the copy the running conversation uses and saves
    from memory.long_term import load_memory
    return load_memory()


async def memory_handler(request: web.Request) -> web.Response:
    _require_local(request)
    from memory.facts import META_KEY, PREFERENCES_KEY, normalise_fact, preferences
    memory = await asyncio.to_thread(_live_memory)
    if request.method == "DELETE":
        data = await _body(request)
        if "preference" in data:   # a preference is forgotten the same way as a fact
            pref, prefs = str(data.get("preference", "")), memory.get(PREFERENCES_KEY) or []
            if pref not in prefs:
                return _json({"ok": False, "error": "That preference isn't saved."}, 404)
            prefs.remove(pref)
        else:
            fact = str(data.get("fact", ""))
            facts = memory.get("core_facts") or []
            if fact not in facts:
                return _json({"ok": False, "error": "That fact isn't in memory."}, 404)
            facts.remove(fact)
            (memory.get(META_KEY) or {}).pop(normalise_fact(fact), None)
        from memory.long_term import save_memory
        await save_memory(memory)
    return _json({"ok": True, "facts": list(memory.get("core_facts") or []), "preferences": preferences(memory)})


async def pending_action_handler(request: web.Request) -> web.Response:
    """A PC action waiting for the user's yes (brain/pc_windows.py): read it,
    or answer it with the app's Yes / No buttons."""
    _require_local(request)
    from brain import pc_windows
    from brain.tools import ToolFailure
    if request.method == "POST":
        data = await _body(request)
        choice = str(data.get("decision", "")).lower()
        if choice not in ("yes", "no"):
            return _json({"ok": False, "error": "decision must be yes or no"}, 400)
        from brain import pc_actions
        settled = await asyncio.to_thread(pc_windows.resolve, choice)
        if settled is None:
            settled = await asyncio.to_thread(pc_actions.resolve, choice)
        if settled is None:
            return _json({"ok": False, "error": "Nothing is waiting for an answer (it may have expired)."}, 404)
        result = settled[2]
        return _json({"ok": not isinstance(result, ToolFailure), "result": str(result)})
    from brain import pc_actions
    p = pc_windows.pending() or pc_actions.pending()
    return _json({"ok": True, "pending": {"text": p.describe(), "expires": p.expires} if p else None})


def _ability_topics() -> list[dict]:
    """The PC abilities (brain/pc_actions.py REGISTRY) in their Extensions
    groups (S93), each with its switch (`enabled`) and its state now: "on",
    "asks first" (every usable ability in it asks), "off", or "not available"
    (no ability has that topic). A group switched on is still off when
    another setting switches all its abilities off (today every PC ability
    also needs the "windows" tool permission, brain/agent.py TOOL_SETTING);
    `blocked_by` names that tool permission."""
    from brain import agent, pc_actions
    groups: dict[str, list] = {topic: [] for topic in ui_settings.EXTENSION_TOPICS}
    for ability in pc_actions.REGISTRY.values():
        key = ui_settings.extension_for(ability.topic)
        if key:
            groups[key].append(ability)
    topics = []
    for topic, members in groups.items():
        title, description = ui_settings.EXTENSION_INFO[topic]
        enabled = ui_settings.extension_enabled(topic)
        usable = [a for a in members if not agent.tool_switched_off(a.name)]
        state, blocked_by = "on", None
        if not members:
            state = "not available"
        elif not enabled:
            state = "off"
        elif not usable:
            state = "off"
            gates = {agent.TOOL_SETTING.get(a.name) for a in members}
            gate = gates.pop() if len(gates) == 1 else None
            blocked_by = gate if gate in ui_settings.TOOL_NAMES and not ui_settings.tool_allowed(gate) else None
        elif all(a.risk == "critical" for a in usable):
            state = "asks first"
        topics.append({"topic": topic, "title": title, "description": description, "enabled": enabled,
                       "state": state, "blocked_by": blocked_by,
                       "abilities": [{"name": a.name, "risk": a.risk, "description": a.description} for a in members]})
    return topics


async def abilities_handler(request: web.Request) -> web.Response:
    """What MBUDDY can do right now, from the real tool registry and settings
    (the quick panel's "What I can do"): its tools, then the PC abilities by
    Extensions group, then what isn't possible yet."""
    _require_local(request)
    from brain import agent
    items = []
    for t in agent.TOOLS:
        name = t["function"]["name"]
        label = agent._ABILITY.get(name)
        if not label:
            continue
        state = "off" if agent.tool_switched_off(name) else ("asks first" if name in ("window_action", "media_control") else "on")
        items.append({"tool": name, "label": label, "state": state})
    items.append({"tool": "", "label": "send messages or email, read the screen, click inside other apps", "state": "not yet"})
    return _json({"ok": True, "abilities": items, "topics": _ability_topics()})


async def conversation_handler(request: web.Request) -> web.Response:
    """The recent conversation, from the backend's own record (voice and typed
    turns alike), so the app shows what happened while its window was closed."""
    _require_local(request)
    try:
        limit = max(1, min(200, int(request.query.get("limit", "60"))))
    except ValueError:
        limit = 60
    memory = await asyncio.to_thread(_live_memory)
    turns = [
        {"time": e.get("time", ""), "you": e.get("user", ""), "me": e.get("mbuddy", "")}
        for e in list(memory.get("conversation_log") or [])[-limit:]
    ]
    return _json({"ok": True, "turns": turns})


# ── installation checks and model download ───────────────────────────────
async def setup_checks_handler(request: web.Request) -> web.Response:
    _require_local(request)
    result = await asyncio.to_thread(setup_checks.run_all, runtime_status, ui_settings.get("llm_model"))
    return _json({"ok": True, **result})


async def setup_pull_handler(request: web.Request) -> web.Response:
    _require_local(request)
    if request.method == "POST":
        data = await _body(request)
        model = str(data.get("model") or ui_settings.get("llm_model")).strip()
        if not model or len(model) > 120:
            return _json({"ok": False, "error": "Name a model to download."}, 400)
        return _json({"ok": True, "pull": setup_checks.start_pull(model)})
    return _json({"ok": True, "pull": setup_checks.pull_progress()})


# ── live state for the app: small, only when it changes ──────────────────
# /status/stream sends the whole status (world model included, kilobytes)
# on every update, and the mic level updates it ~60 times a second, so the
# app windows were parsing that constantly. This sends only the fields the
# app shows, only when one changes, at most ~7 times a second.
LIVE_HEARTBEAT_S = 4.0
LIVE_FIELDS = ("awake", "chat_until", "listening", "thinking", "speaking", "emotion", "last_heard", "last_reply", "inner_thought", "pending_action",
               "device_conn_state", "device_firmware_version")


def live_snapshot() -> dict:
    s = runtime_status
    data = {k: getattr(s, k, None) for k in LIVE_FIELDS}
    data["level"] = round(float(getattr(s, "last_rms", 0.0) or 0.0), 3)  # room noise is ~0.002-0.005
    data["fired_reminders"] = [{"id": r.get("id"), "title": r.get("title"), "source": r.get("source", "")}
                               for r in (getattr(s, "fired_reminders", None) or [])][-10:]
    try:
        from core import notif_watch
        data["message_alert"] = notif_watch.current_alert()      # a message that has waited long enough: the buddy reminds you
    except Exception:
        data["message_alert"] = None
    data["mic_on"] = bool(ui_settings.get("microphone_on"))   # off: the voice cannot wake, so the app must not call that "asleep"
    data["settings_rev"] = _settings_rev()
    data["job"] = _live_job()
    data["media"] = _media["item"]
    data["pc"] = _media["pc"]
    data["checkin"] = _section_state("core.activity", "pending_checkin")
    data["focus"] = _section_state("core.focus", "current")
    data["focus_done"] = _section_state("core.focus", "just_finished")   # a sprint that was just completed
    data["reward"] = _section_state("core.rewards", "last")              # the newest progress, so a window can show "+10" once
    data["wardrobe"] = _section_state("core.wardrobe", "live")           # what the character wears (only what is unlocked)
    data["focus_nudge"] = _held_nudge()
    data["needs_choice"] = _section_state("core.roles", "pending_choice")   # {"role": "music"} while no app is chosen and one was asked for
    return data


# core.focus.nudge() answers once and then stays quiet for minutes. Several
# windows read the live state many times a second, so the answer is held here
# for a few seconds: every window sees it, whichever asked first.
NUDGE_SHOWN_S = 12.0
_nudge: dict = {"at": 0.0, "item": None}


def _held_nudge():
    fresh = _section_state("core.focus", "nudge")
    now = time.monotonic()
    if fresh:
        _nudge["at"], _nudge["item"] = now, fresh
    elif _nudge["item"] is not None and now - _nudge["at"] >= NUDGE_SHOWN_S:
        _nudge["item"] = None
    return _nudge["item"]


def _section_state(module: str, function: str):
    """What a section's own module says right now (a check-in waiting for an
    answer, a focus sprint in progress); None when there is nothing, or when
    that section is not installed."""
    import sys
    mod = sys.modules.get(module)
    if mod is None:
        import importlib.util
        try:
            if importlib.util.find_spec(module) is None:
                return None
            mod = importlib.import_module(module)
        except Exception:
            return None
    try:
        return getattr(mod, function)()
    except Exception:
        return None


# What is playing on the PC right now (Windows' media session: any player,
# any service), for the buddy. Read on a worker thread at most every few
# seconds and only while a window is listening; the snapshot never waits.
MEDIA_EVERY_S = 3.0
_media: dict = {"at": 0.0, "busy": False, "item": None, "pc": None}


def _read_playing() -> dict | None:
    """What is playing; else what is paused and could go on (the music card
    shows it with a Play button). "playing" says which."""
    from brain import pc_windows
    sessions = pc_windows.now_playing()
    m = (next((m for m in sessions if m.get("status") == "playing"), None)
         or next((m for m in sessions if m.get("status") == "paused"), None))
    if m is None:
        return None
    # A player can keep saying "playing" after the track has run to its end (position at the length): that is stopped.
    ended = m.get("duration_s", 0) > 0 and m.get("position_s", 0) >= m.get("duration_s", 0)
    return {"title": m["title"], "artist": m["artist"], "app": m["app"], "playing": m["status"] == "playing" and not ended,
            "position_s": m.get("position_s", 0), "duration_s": m.get("duration_s", 0)}


def _read_pc() -> dict | None:
    from core import pc_stats
    return pc_stats.read()


async def _refresh_media() -> None:
    if _media["busy"] or time.monotonic() - _media["at"] < MEDIA_EVERY_S:
        return
    _media["busy"] = True
    try:
        _media["item"] = await asyncio.to_thread(_read_playing)
    except Exception:
        _media["item"] = None   # unreadable is "nothing known", never a stale title
    try:
        _media["pc"] = await asyncio.to_thread(_read_pc)
    except Exception:
        _media["pc"] = None
    finally:
        _media["busy"], _media["at"] = False, time.monotonic()


def _live_job() -> dict | None:
    """The background job the buddy shows beside itself (core.jobs), with
    where its app's icon can be fetched when it works in a connected app."""
    try:
        from core import jobs
        job = jobs.on_show()
        if job and job["kind"] == "app" and ui_settings.connected_app(job["subject"]):
            job["icon"] = "/ui/app-icon?name=" + quote(job["subject"])
        return job
    except Exception:
        return None


async def timers_handler(request: web.Request) -> web.Response:
    """Start a timer for the number of seconds picked on the notch's dial, or cancel one (DELETE with its key)."""
    _require_local(request)
    from brain.tools import _timer_manager
    data = await _body(request)
    if request.method == "DELETE":
        ok = await asyncio.to_thread(_timer_manager.cancel_timer, str(data.get("key", "")))
        return _json({"ok": ok} if ok else {"ok": False, "error": "No such timer."}, 200 if ok else 404)
    seconds = data.get("seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 5 <= seconds <= 24 * 3600:
        return _json({"ok": False, "error": "A timer is 5 seconds to 24 hours."}, 400)
    seconds = int(seconds)
    label = str(data.get("label") or "").strip()[:40] or f"{seconds}s timer"
    timer = await asyncio.to_thread(_timer_manager.start_timer, label, seconds)
    return _json({"ok": True, "key": timer.label, "ends_at": datetime.fromtimestamp(timer.end_time).isoformat(timespec="seconds")})


async def media_handler(request: web.Request) -> web.Response:
    """The panel's player buttons: the user's own click, sent to the player
    that is showing (its Windows media session). Then what plays is read again."""
    _require_local(request)
    from brain import pc_windows
    data = await _body(request)
    command = str(data.get("command", ""))
    shown = _media["item"]
    if command not in pc_windows.MEDIA_COMMANDS:
        return _json({"ok": False, "error": "command must be toggle, next or previous"}, 400)
    if not shown:
        return _json({"ok": False, "error": "Nothing is playing."}, 409)
    ok = await asyncio.to_thread(pc_windows.media_command, shown["app"], command)
    await asyncio.sleep(0.4)   # the player needs a moment before it reports the change
    _media["at"] = 0.0
    await _refresh_media()
    return _json({"ok": bool(ok), "media": _media["item"]} if ok
                 else {"ok": False, "error": "The player didn't accept that.", "media": _media["item"]}, 200 if ok else 502)


async def task_snooze_handler(request: web.Request) -> web.Response:
    """"In 10 min" on a reminder: its time moves, and it is announced again then."""
    _require_local(request)
    from brain.task_tracker import task_tracker
    try:
        task_id = int(request.match_info["task_id"])
    except ValueError:
        raise web.HTTPBadRequest(text="Bad task id.")
    data = await _body(request)
    minutes = data.get("minutes", 10)
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 24 * 60:
        return _json({"ok": False, "error": "minutes must be 1 to 1440"}, 400)
    due = await asyncio.to_thread(task_tracker.snooze_task, task_id, minutes)
    return _json({"ok": True, "due_date": due} if due else {"ok": False, "error": "No such task."}, 200 if due else 404)


async def app_icon_handler(request: web.Request) -> web.Response:
    """The real icon of a connected app, as a PNG (the buddy's status bubble,
    the panel). Only apps the user connected: a name is never turned into a
    path here."""
    _require_local(request)
    from core import app_icon
    app = ui_settings.connected_app(request.query.get("name", ""))
    png = await asyncio.to_thread(app_icon.for_path, app["path"]) if app else None
    if not png:
        return web.Response(status=404, text="No icon.")
    return web.Response(body=png, content_type="image/png", headers={"Cache-Control": "max-age=3600"})


def _settings_rev() -> str:
    """Changes whenever a setting any window shows changes: the app settings
    and the inner-life switch (a separate companion setting). Each window
    refetches its settings when it changes, so the quick panel, the buddy and
    Preferences never show a stale switch (S81)."""
    try:
        from core.companion_settings import get_companion_settings
        inner = int(get_companion_settings().inner_enabled)
    except Exception:
        inner = 0
    return f"{ui_settings.revision()}.{inner}"


async def live_handler(request: web.Request) -> web.WebSocketResponse:
    _require_local(request)
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    last = None
    try:
        sent_at = 0.0
        while not ws.closed:
            asyncio.ensure_future(_refresh_media())
            snap = live_snapshot()
            # Also a heartbeat: the same snapshot again every few seconds, so a window can tell "nothing changed" from "the feed
            # is dead" (it used to keep showing music for good after the feed went quiet).
            if snap != last or time.monotonic() - sent_at > LIVE_HEARTBEAT_S:
                await ws.send_json(snap)
                last, sent_at = snap, time.monotonic()
            await asyncio.sleep(0.15)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    return ws


# ── who may call the backend from a web page ──────────────────────────────
# The app's pages come from Tauri's own origin, so their calls to
# 127.0.0.1:5000 are cross-origin and need CORS. Without a rule, though, any
# website open in a browser on this PC could also send requests here: the
# local-only check sees 127.0.0.1 either way. So: the app and local pages
# get CORS; any other origin may not change anything (Taby's local servers
# refuse foreign Origin headers the same way).
_APP_ORIGINS = {"http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"}


def origin_allowed(origin: str) -> bool:
    if origin in _APP_ORIGINS:
        return True
    for local in ("http://127.0.0.1", "http://localhost"):
        if origin == local or origin.startswith(local + ":"):
            return True
    return False


_LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]", "tauri.localhost")


def host_allowed(host: str) -> bool:
    """The Host header names this PC. A foreign name means DNS rebinding: a
    website's own domain pointed at 127.0.0.1 so the browser calls it
    same-origin (docs/audit/SENIOR_REVIEW.md, M1)."""
    name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
    if not host or name.lower() in _LOCAL_HOSTS:
        return True
    # A plain IP address (the ESP32 uses the PC's LAN IP) can't be a
    # rebinding, which needs a domain name (docs/audit/SENIOR_REVIEW.md, C4).
    import ipaddress
    try:
        ipaddress.ip_address(name.strip("[]"))
        return True
    except ValueError:
        return False


@web.middleware
async def origin_guard(request: web.Request, handler):
    origin = request.headers.get("Origin", "")
    if not host_allowed(request.headers.get("Host", "")):
        return web.Response(status=421, text="Unknown host.")
    if origin and not origin_allowed(origin):
        # A WebSocket isn't covered by CORS: a foreign page could read the
        # live feed (what MBUDDY heard and said) unless it is refused here (C1).
        if request.method not in ("GET", "HEAD") or request.headers.get("Upgrade", "").lower() == "websocket":
            return web.Response(status=403, text="Requests from other websites are not allowed.")
        return await handler(request)  # no CORS headers: the browser won't let the page read it
    if origin and request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


# Sections that bring their own API module (each has register(app)). A module
# that is not there yet is skipped, so the app starts without it; one that is
# there but broken is logged, never silently dropped.
SECTION_APIS = ("activity_api", "learned_api", "focus_api", "roles_api", "music_api", "routines_api", "models_api", "remote_api", "notify_api", "phone_api", "phone_agent_api")


def _register_sections(app: web.Application) -> None:
    import importlib
    import utils.logger as log
    for name in SECTION_APIS:
        try:
            module = importlib.import_module(f"api.{name}")
        except ModuleNotFoundError as exc:
            if exc.name != f"api.{name}":
                log.warn(f"[UI] api.{name} could not load: {exc}")
            continue
        except Exception as exc:
            log.warn(f"[UI] api.{name} could not load: {type(exc).__name__}: {exc}")
            continue
        try:
            module.register(app)
        except Exception as exc:
            log.warn(f"[UI] api.{name} did not register: {type(exc).__name__}: {exc}")


async def audio_level_handler(request: web.Request) -> web.Response:
    """The real output level, for the music view's level meter."""
    _require_local(request)
    from brain import pc_windows
    return _json({"ok": True, "level": await asyncio.to_thread(pc_windows.output_level)})


async def dictate_handler(request: web.Request) -> web.Response:
    """Listen once: the next thing said into the microphone comes back as text instead of being a command
    (speech to text on this PC; nothing leaves it). For "speak what you want" in the notch."""
    _require_local(request)
    if not ui_settings.get("microphone_on"):
        return _json({"ok": False, "error": "The microphone is off. Switch it on first."}, 409)
    session = active_session()
    if session is None or not hasattr(session, "capture_next_utterance"):
        return _json({"ok": False, "error": "I can't hear right now: the voice isn't connected."}, 503)
    data = await _body(request)
    try:
        seconds = max(3.0, min(20.0, float(data.get("seconds", 12))))
    except (TypeError, ValueError):
        seconds = 12.0
    text = await session.capture_next_utterance(seconds)
    if not text:
        return _json({"ok": False, "error": "I didn't hear anything. Try again."}, 200)
    return _json({"ok": True, "text": text})


async def perf_handler(request: web.Request) -> web.Response:
    """Diagnostics from the notch: how many frames an animation really drew (logs/notch_perf.jsonl). Local only."""
    _require_local(request)
    try:
        data = await request.json()
    except Exception:
        return _json({"ok": False}, 400)
    import json as _json_mod, time as _time
    path = Path(__file__).resolve().parents[1] / "logs" / "notch_perf.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 150_000:                 # a diagnostic, not a growing log: keep the newest lines
        keep = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-300:]
        path.write_text(chr(10).join(keep) + chr(10), encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(_json_mod.dumps({"t": round(_time.time()), **{k: data.get(k) for k in list(data)[:20]}}) + chr(10))
    return _json({"ok": True})


def register_ui_routes(app: web.Application) -> None:
    app.router.add_post("/ui/perf", perf_handler)
    app.router.add_post("/ui/timers", timers_handler)
    app.router.add_delete("/ui/timers", timers_handler)
    app.router.add_get("/ui/audio-level", audio_level_handler)
    app.router.add_post("/ui/dictate", dictate_handler)
    _register_sections(app)
    app.router.add_get("/ui/live", live_handler)
    app.router.add_post("/ui/audio-devices", audio_devices_handler)
    app.router.add_get("/ui/settings", settings_handler)
    app.router.add_post("/ui/settings", settings_handler)
    app.router.add_get("/ui/conversation", conversation_handler)
    app.router.add_get("/ui/abilities", abilities_handler)
    app.router.add_get("/ui/apps", apps_handler)
    app.router.add_get("/ui/app-icon", app_icon_handler)
    app.router.add_post("/ui/media", media_handler)
    app.router.add_post("/ui/tasks/{task_id}/snooze", task_snooze_handler)
    app.router.add_get("/ui/pending-action", pending_action_handler)
    app.router.add_post("/ui/pending-action", pending_action_handler)
    app.router.add_get("/ui/overview", overview_handler)
    app.router.add_get("/ui/rewards", rewards_handler)
    app.router.add_get("/ui/wardrobe", wardrobe_handler)
    app.router.add_post("/ui/wardrobe", wardrobe_handler)
    app.router.add_get("/ui/room", room_handler)
    app.router.add_post("/ui/room", room_handler)
    app.router.add_get("/ui/system-volume", system_volume_handler)
    app.router.add_post("/ui/system-volume", system_volume_handler)
    app.router.add_get("/ui/tasks", tasks_handler)
    app.router.add_post("/ui/tasks", tasks_handler)
    app.router.add_post("/ui/tasks/{task_id}/done", task_item_handler)
    app.router.add_delete("/ui/tasks/{task_id}", task_item_handler)
    app.router.add_get("/ui/memory", memory_handler)
    app.router.add_delete("/ui/memory", memory_handler)
    app.router.add_get("/setup/checks", setup_checks_handler)
    app.router.add_get("/setup/pull", setup_pull_handler)
    app.router.add_post("/setup/pull", setup_pull_handler)
