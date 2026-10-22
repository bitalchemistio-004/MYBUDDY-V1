"""Manual local control endpoints for the connected MBUDDY device."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import asyncio

from aiohttp import web

from api.auth import require_auth
from api.device_protocol import DeviceProtocol
from api.serial_bridge import serial_bridge
from api.tunnel_manager import tunnel_manager
from api.validation import safe_json, validate_string, validate_number, validate_boolean
from brain.capability_registry import ANIMATION_NAMES, SFX_NAMES
from brain.personality import STATE_TO_TAG
from core.status import runtime_status
import utils.logger as log


_session: Any | None = None

VALID_ACTIONS = ANIMATION_NAMES
VALID_SFX = SFX_NAMES
VALID_STATES = tuple(STATE_TO_TAG.keys())

ACTION_THOUGHTS = {
    "joystick": "I am turning the idle space into a tiny duel so my energy has somewhere to go.",
    "interactive_arena": "I am testing control and direction, like a small maze in my head.",
    "dragon_fire": "I need a hard reset moment, so I am burning the screen clean.",
    "data_weaver": "I am connecting loose ideas and watching the thought network grow.",
    "pixel_cascade": "I am letting the noise fall into order, line by line.",
    "heartbeat": "I am checking my own pulse before I act too fast.",
    "thought_bubble": "I am holding one idea in the air until it becomes clear.",
    "block_break": "I am playing a little game to burn off restless energy.",
    "starlight": "I am staying quiet and letting the room feel softer.",
    "clock_timer": "I am anchoring myself to the real time instead of drifting.",
    "wifi_search": "I am searching for a stable signal and waiting for the lock.",
    "battery_charge": "I am low enough to care about power, so I am charging first.",
    "error_glitch": "Something feels tangled, so I am warning before I recover.",
    "scanning_targeting": "I am narrowing attention until the target is obvious.",
    "dreaming": "I am half-asleep, still processing in the background.",
    "celebration": "That worked, and I am letting myself show it.",
    "listening_animation": "I am focused on the voice coming in, not preparing a speech yet.",
}


def _trace(
    *,
    goal: str,
    intent: str,
    emotion: str = "",
    action: str = "",
    animation: str = "",
    sfx: str = "",
    tool_used: str = "",
    reason: str,
    priority: str = "normal",
    source: str = "dashboard",
) -> None:
    runtime_status.record_behavior_trace({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "goal": goal,
        "intent": intent,
        "emotion": emotion,
        "action": action,
        "animation": animation,
        "sfx": sfx,
        "tool_used": tool_used,
        "reason": reason,
        "priority": priority,
        "source": source,
        "decision": {},
        "competing_behaviors": [],
        "conflicts": [],
    })


def set_active_session(session: Any) -> None:
    global _session
    _session = session


def clear_active_session(session: Any) -> None:
    global _session
    if _session is session:
        _session = None


def device_session() -> Any | None:
    """
    The session for a genuinely attached device, or None.

    Use this for anything that only makes sense against real hardware —
    streaming audio, driving the OLED, reading device telemetry — so those
    paths still fail honestly when there is no body.
    """
    if _session is None or getattr(_session, "closing", False):
        return None
    if getattr(_session.ws, "closed", True):
        return None
    return _session


def active_session() -> Any | None:
    """
    The session that should handle a turn — with a body if there is one.

    Falls back to the Runtime's headless session so that conversation, memory
    and mood keep working with no device attached. Previously this returned
    None whenever the transport was closed, which made every conversational
    endpoint refuse: unplugging the ESP32 removed the mind, not just the body.
    """
    live = device_session()
    if live is not None:
        return live

    try:
        from core.runtime import get_runtime

        return get_runtime().headless_session()
    except Exception:
        # Never let the fallback itself break a request; a caller that truly
        # needs hardware is using device_session() anyway.
        return None


def _allow_request(request: web.Request) -> bool:
    if os.getenv("MBUDDY_DASHBOARD_ALLOW_LAN", "0").lower() in {"1", "true", "yes"}:
        return True
    return request.remote in {"127.0.0.1", "::1", "localhost"}


def _require_local(request: web.Request) -> None:
    if not _allow_request(request):
        raise web.HTTPForbidden(text="Manual controls are local-only. Set MBUDDY_DASHBOARD_ALLOW_LAN=1 to allow LAN access.")


def _require_session() -> Any:
    session = active_session()
    if session is None:
        raise web.HTTPServiceUnavailable(text="No ESP32 session is connected.")
    return session


def _target_from(data: dict[str, Any] | None) -> str:
    target = str((data or {}).get("target", "auto")).strip().lower()
    if target not in {"auto", "wireless", "serial", "both"}:
        raise web.HTTPBadRequest(text=f"Unknown target: {target}")
    return target


CHAT_STAYS_AWAKE_S = 45.0   # how long the buddy keeps looking awake after a typed reply


async def _send_protocol(
    payload: str, target: str = "auto", required: bool = True
) -> dict[str, bool]:
    """
    Push a protocol line to whichever device transports are attached.

    Args:
        payload: the bracket-protocol line
        target: "auto", "wireless", "serial" or "both"
        required: whether reaching a transport is necessary for the caller to
            have succeeded. Agent-state changes pass False — the emotion still
            changes with no body, it simply is not drawn anywhere. Device
            effects such as a sound leave this True, because a sound nobody
            can hear did not happen.

    Returns:
        Which transports accepted the payload.
    """
    # Only a genuinely attached device can receive anything; the headless
    # session's transport reports itself closed, so sends correctly no-op.
    session = device_session()
    sent_wireless = False
    sent_serial = False

    if target in {"auto", "wireless", "both"} and session is not None:
        sent_wireless = bool(await session._safe_send_str(payload))

    if target in {"auto", "serial", "both"} and (target != "auto" or not sent_wireless):
        sent_serial = serial_bridge.send_line(payload)

    if required:
        if target == "wireless" and not sent_wireless:
            raise web.HTTPServiceUnavailable(text="Wireless ESP32 session is not connected.")
        if target == "serial" and not sent_serial:
            raise web.HTTPServiceUnavailable(text="USB serial transport is not connected.")
        if target in {"both", "auto"} and not (sent_wireless or sent_serial):
            raise web.HTTPServiceUnavailable(text="No ESP32 transport is connected.")

    return {"wireless": sent_wireless, "serial": sent_serial}


async def control_manifest_handler(request: web.Request) -> web.Response:
    _require_local(request)
    session = active_session()
    content_awareness_enabled = (
        bool(getattr(session, "auto_visuals_enabled", runtime_status.content_awareness_enabled))
        if session
        else runtime_status.content_awareness_enabled
    )
    return web.json_response({
        "connected": session is not None,
        "serial": serial_bridge.snapshot(),
        "actions": VALID_ACTIONS,
        "sfx": VALID_SFX,
        "states": VALID_STATES,
        "auto_visuals": content_awareness_enabled,
        "content_awareness_enabled": content_awareness_enabled,
        "allow_lan": os.getenv("MBUDDY_DASHBOARD_ALLOW_LAN", "0").lower() in {"1", "true", "yes"},
    })


async def action_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data = await safe_json(request)
    name = validate_string(data, "name").lower()
    duration_ms = validate_number(
        data,
        "duration_ms",
        required=False,
        default=1500,
        min_value=250,
        max_value=60000,
        integer=True,
    )
    target = _target_from(data)
    if name not in VALID_ACTIONS:
        raise web.HTTPBadRequest(text=f"Unknown action: {name}")
    protocol = DeviceProtocol()
    if name == "clock_timer":
        await _send_protocol(protocol.time_update(), target)
    transports = await _send_protocol(protocol.action(name, duration_ms), target)
    session = active_session()
    if session is not None:
        setattr(session, "manual_visual_until", time.time() + (max(250, duration_ms) / 1000.0) + 3.0)
    thought = ACTION_THOUGHTS.get(name, "I am showing this action as a quiet visual state.")
    await _send_protocol(protocol.inner_thought(thought), target)
    runtime_status.update(current_action=name, current_action_thought=thought)
    _trace(
        goal="manual_visual_test",
        intent="render_dashboard_selected_animation",
        emotion=getattr(getattr(session, "state", None), "current", runtime_status.emotion) if session else runtime_status.emotion,
        action=name,
        animation=name,
        reason=thought,
        priority="normal",
        source="dashboard_action",
    )
    return web.json_response({
        "ok": True,
        "action": name,
        "thought": thought,
        "duration_ms": max(250, duration_ms),
        "sent": transports,
    })


@require_auth(allow_local=True)
async def clear_action_handler(request: web.Request) -> web.Response:
    data = await safe_json(request)
    target = _target_from(data)
    session = active_session()
    if session is not None:
        setattr(session, "manual_visual_until", 0.0)
    try:
        transports = await _send_protocol(DeviceProtocol.clear_action(), target)
    except web.HTTPServiceUnavailable:
        transports = {"wireless": False, "serial": False}
    runtime_status.update(current_action="", current_action_thought="")
    _trace(
        goal="clear_visual_action",
        intent="return_display_to_base_state",
        emotion=runtime_status.emotion,
        action="clear",
        reason="Dashboard requested action clear, so the current animation state was removed.",
        priority="high",
        source="dashboard_clear",
    )
    return web.json_response({"ok": True, "action": "", "sent": transports})


@require_auth(allow_local=True)
async def auto_visuals_handler(request: web.Request) -> web.Response:
    data = await safe_json(request)
    enabled = validate_boolean(data, "enabled", required=False, default=True)
    session = active_session()
    if session is not None:
        setattr(session, "auto_visuals_enabled", enabled)
        if not enabled:
            setattr(session, "manual_visual_until", time.time() + 3600.0)
        else:
            setattr(session, "manual_visual_until", 0.0)
    runtime_status.update(auto_visuals=enabled, content_awareness_enabled=enabled)
    sent = {"wireless": False, "serial": False}
    if not enabled:
        target = _target_from(data)
        try:
            sent = await _send_protocol(DeviceProtocol.clear_action(), target)
        except web.HTTPServiceUnavailable:
            sent = {"wireless": False, "serial": False}
        runtime_status.update(current_action="", current_action_thought="")
    _trace(
        goal="configure_auto_visuals",
        intent="toggle_automatic_behavior_output",
        emotion=runtime_status.emotion,
        action="auto_visuals_on" if enabled else "auto_visuals_off",
        reason="Dashboard changed whether MBUDDY may automatically choose visual actions.",
        priority="high",
        source="dashboard_auto_visuals",
    )
    return web.json_response({
        "ok": True,
        "auto_visuals": enabled,
        "content_awareness_enabled": enabled,
        "sent": sent,
    })


@require_auth(allow_local=True)
async def sfx_handler(request: web.Request) -> web.Response:
    data = await safe_json(request)
    name = validate_string(data, "name", choices=list(VALID_SFX))
    target = _target_from(data)
    sent = {"wireless": False, "serial": False}
    # A sound needs a speaker, so this asks for a genuinely attached device
    # rather than the headless session — which would otherwise make the branch
    # below claim success for a sound nobody could hear.
    session = device_session()
    if target in {"auto", "wireless", "both"} and session is not None:
        await session._play_sfx(name, force=True)
        sent["wireless"] = True
    if target in {"auto", "serial", "both"} and (target != "auto" or not sent["wireless"]):
        sent["serial"] = serial_bridge.send_line(DeviceProtocol.sfx(name))
    if target == "wireless" and not sent["wireless"]:
        raise web.HTTPServiceUnavailable(text="Wireless ESP32 session is not connected.")
    if target == "serial" and not sent["serial"]:
        raise web.HTTPServiceUnavailable(text="USB serial transport is not connected.")
    if not (sent["wireless"] or sent["serial"]):
        raise web.HTTPServiceUnavailable(text="No ESP32 transport is connected.")
    runtime_status.update(last_sfx=name)
    _trace(
        goal="manual_sfx_test",
        intent="play_dashboard_selected_sound",
        emotion=runtime_status.emotion,
        sfx=name,
        reason=f"Dashboard manually requested SFX {name}.",
        priority="normal",
        source="dashboard_sfx",
    )
    return web.json_response({"ok": True, "sfx": name, "sent": sent})


async def state_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data = await safe_json(request)
    state = validate_string(data, "state", choices=list(VALID_STATES))
    target = _target_from(data)
    session = active_session()
    if session is not None:
        session.state.current = state
    # Not required: the emotion is MBUDDY's, and changes whether or not there
    # is a screen to show it on.
    transports = await _send_protocol(
        DeviceProtocol.state_quiet(state), target, required=False
    )
    runtime_status.update(emotion=state)
    _trace(
        goal="manual_emotion_state",
        intent="set_display_emotion",
        emotion=state,
        reason=f"Dashboard manually set MBUDDY emotion state to {state}.",
        priority="normal",
        source="dashboard_state",
    )
    return web.json_response({
        "ok": True,
        "state": state,
        "sent": transports,
        # Say plainly whether anything displayed it, rather than letting a
        # bare "ok" imply the face changed.
        "shown_on_device": bool(transports.get("wireless") or transports.get("serial")),
    })


async def energy_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data = await safe_json(request)
    session = active_session()
    current_energy = getattr(getattr(session, "state", None), "energy", runtime_status.energy)
    level = validate_number(
        data,
        "level",
        required=False,
        default=current_energy,
        min_value=0,
        max_value=100,
    )
    target = _target_from(data)
    if session is not None:
        session.state.set_energy(level, reason="manual_dashboard")
        session.memory["inner_state"] = session.state.to_inner_state(
            session.memory.get("last_inner_thought", "")
        )
        from memory.long_term import save_memory
        await save_memory(session.memory)
    # Energy is the agent's, like emotion — it changes with or without a body
    # to show the little bar in the corner of the OLED.
    transports = await _send_protocol(
        DeviceProtocol.energy_update(level), target, required=False
    )
    runtime_status.update(energy=level, emotion=getattr(session.state, "current", runtime_status.emotion) if session else runtime_status.emotion)
    _trace(
        goal="manual_energy_update",
        intent="sync_energy_state",
        emotion=getattr(session.state, "current", runtime_status.emotion) if session else runtime_status.emotion,
        action="energy_update",
        reason=f"Dashboard manually set energy to {level:.0f}/100.",
        priority="high" if level < 20 else "normal",
        source="dashboard_energy",
    )
    return web.json_response({"ok": True, "energy": level, "sent": transports})


async def tag_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data = await safe_json(request)
    tag = validate_string(data, "tag", max_length=120)
    target = _target_from(data)
    if not tag.startswith("["):
        raise web.HTTPBadRequest(text="Tag must be a bracket protocol message under 120 characters.")
    transports = await _send_protocol(tag, target)
    _trace(
        goal="manual_protocol_tag",
        intent="send_raw_device_protocol",
        emotion=runtime_status.emotion,
        action="raw_tag",
        reason=f"Dashboard sent raw protocol tag for diagnostics: {tag[:40]}",
        priority="high",
        source="dashboard_raw_tag",
    )
    return web.json_response({"ok": any(transports.values()), "tag": tag, "sent": transports})


async def power_handler(request: web.Request) -> web.Response:
    """
    Wake MBUDDY, put it to sleep, or read which it currently is.

    Sleeping stops MBUDDY responding to speech until it hears its wake phrase
    again, and stops the inner-life loop spending tokens. Waking is the same
    thing the wake phrase does, so it greets you.

    Request body:
        action — "wake", "sleep", or "toggle"
    """
    _require_local(request)
    session = active_session()

    if request.method == "GET":
        return web.json_response({
            "ok": True,
            "awake": bool(getattr(session, "is_awake", False)) if session else False,
            "connected": session is not None,
        })

    data = await safe_json(request)
    action = validate_string(
        data, "action", required=False, default="toggle",
        choices=["wake", "sleep", "toggle"],
    )

    if session is None:
        raise web.HTTPServiceUnavailable(
            text="No active session. MBUDDY needs a connected device first."
        )

    was = bool(getattr(session, "is_awake", False))
    want = {"wake": True, "sleep": False, "toggle": not was}[action]

    session._set_awake(want)
    if want:
        session._cancel_inner()
        session.state.boost(20)
        await session._send_wake_greeting()
    else:
        # Going to sleep should look like it, not just stop replying.
        await session._send_checked("[SLEEPY] alright, I'm out.", "Sleepy")

    return web.json_response({
        "ok": True, "awake": want, "was": was, "action": action,
        "emotion": getattr(session.state, "current", "Normal"),
        "energy": round(float(getattr(session.state, "energy", 0)), 1),
    })


async def llm_handler(request: web.Request) -> web.Response:
    """
    Read or switch the language-model provider.

    GET lists every provider an adapter exists for, whether its credentials
    are usable, and which is active. POST with `provider` switches to it for
    this run, without editing config or restarting.
    """
    _require_local(request)
    from brain.brain_adapters import (
        discover_providers, get_available_providers, set_active_providers,
    )

    if request.method == "GET":
        return web.json_response({
            "ok": True,
            "providers": discover_providers(),
            "active": get_available_providers(),
        })

    data = await safe_json(request)
    provider = data.get("provider") or data.get("providers")
    if not provider:
        raise web.HTTPBadRequest(text="Provide a 'provider' name to switch to.")
    try:
        active = set_active_providers(provider)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc))
    return web.json_response({
        "ok": True, "active": active, "providers": discover_providers(),
    })


async def llm_identity_handler(request: web.Request) -> web.Response:
    """
    Read-only view of MBUDDY's actual system identity prompt and the model
    currently serving it.

    Deliberately not an editor. A live prompt-editing feature is a real
    capability worth having eventually, but it needs its own safety
    thinking — validation, a way to revert a bad edit, awareness that it
    changes behaviour for every future turn — and building that carelessly
    under time pressure is exactly how a "Context Editor" panel ends up
    silently corrupting the one prompt every reply depends on. This shows
    the truth without that risk.
    """
    _require_local(request)
    from brain.personality import IDENTITY_CORE
    import core.config as cfg

    return web.json_response({
        "ok": True,
        "identity_core": IDENTITY_CORE,
        "chat_model": getattr(cfg, "GROQ_CHAT_MODEL", ""),
        "utility_model": getattr(cfg, "GROQ_UTILITY_MODEL", ""),
    })


async def memory_vectors_handler(request: web.Request) -> web.Response:
    """
    Real stats and a real 2D layout of MBUDDY's semantic memory index.

    Nothing here is synthetic: count/dimension come straight from the loaded
    `.npz`, and the scatter layout is an actual PCA projection (via SVD) of
    the real embedding vectors — not a random or placeholder point cloud. With
    fewer than 2 stored vectors a projection is meaningless, so `points` is
    left empty rather than faked, and the caller can tell the two "nothing to
    show" cases apart: no vectors at all vs. too few to project.
    """
    _require_local(request)
    import numpy as np
    from memory.vector_store import get_vector_store

    store = get_vector_store()
    count = len(store.vectors)
    dim = int(store.vectors[0].shape[0]) if count else 0

    storage_bytes = 0
    if os.path.exists(store.storage_path):
        storage_bytes = os.path.getsize(store.storage_path)

    points = []
    if count >= 2:
        matrix = np.asarray(store.vectors, dtype=np.float32)
        centered = matrix - matrix.mean(axis=0, keepdims=True)
        try:
            _, _, vt = np.linalg.svd(centered, full_matrices=False)
            projected = centered @ vt[:2].T
            for i, (x, y) in enumerate(projected.tolist()):
                text = (store.metadata[i] or {}).get("text", "") if i < len(store.metadata) else ""
                points.append({
                    "x": round(float(x), 4), "y": round(float(y), 4),
                    "preview": text[:80],
                })
        except Exception:
            points = []

    return web.json_response({
        "ok": True,
        "enabled": store.enabled,
        "model_loaded": store.model is not None,
        "model_name": store.model_name,
        "count": count,
        "dimension": dim,
        "storage_bytes": storage_bytes,
        "pending_unflushed": store._pending,
        "points": points,
    })


async def memory_snapshot_handler(request: web.Request) -> web.Response:
    """
    MBUDDY's real persisted memory — the same store the brain reads and
    writes every turn, whether a conversation happened over the ESP32,
    PC Voice, or typed chat.

    The desktop app's Memory tab previously showed only its own local
    activity log (services/history_service.py's SQLite file), which never
    saw anything MBUDDY actually said or learned outside of the desktop
    app's own UI actions. This reads memory/long_term.py's real store
    instead — facts, conversation log, relationship state, and the shared
    timeline the brain itself uses to decide what it remembers.
    """
    _require_local(request)
    from memory.long_term import load_memory
    from memory.facts import META_KEY, ranked_facts

    memory = load_memory()
    meta = memory.get(META_KEY) or {}

    facts = []
    for text in ranked_facts(memory, limit=200):
        key = None
        for k, v in meta.items():
            if v.get("text") == text:
                key = k
                break
        m = meta.get(key, {}) if key else {}
        facts.append({
            "text": text,
            "confidence": round(float(m.get("confidence", 0.0)), 2),
            "mentions": int(m.get("mentions", 1)),
            "last_seen": m.get("last_seen"),
        })

    conversation_log = list(memory.get("conversation_log", []))[-200:]
    timeline = list(memory.get("shared_timeline", []))[-100:]
    emotional_memories = list(memory.get("emotional_memories", []))[-40:]

    return web.json_response({
        "ok": True,
        "facts": facts,
        "conversation_log": conversation_log,
        "conversation_log_total": len(memory.get("conversation_log", [])),
        "shared_timeline": timeline,
        "emotional_memories": emotional_memories,
        "relationship_state": memory.get("relationship_state", {}),
        "identity_snapshot": memory.get("identity_snapshot", ""),
        "reminders": memory.get("reminders", []),
        "project_threads": memory.get("project_threads", []),
        "daily_summaries": list(memory.get("daily_summaries", []))[-30:],
    })


# How long /control/chat waits for a reply before answering "pending" (the app then asks /control/chat/result).
CHAT_WAIT_S = 45.0


async def chat_handler(request: web.Request) -> web.Response:
    """
    Send typed text to MBUDDY as if it had been spoken.

    The text enters at the same point a finished transcription does, so a
    typed turn behaves exactly like a spoken one: the same language
    resolution, tool selection, emotion and memory writes. `speak` controls
    whether the reply is also voiced on the device, which is useful when
    testing without wanting the room filled with speech.

    Request body:
        text  — what to say to MBUDDY (required)
        speak — voice the reply on the device as well (default true)
    """
    _require_local(request)
    data = await safe_json(request)
    text = validate_string(data, "text", max_length=1000)
    speak = bool(data.get("speak", True))

    session = active_session()
    if session is None:
        raise web.HTTPServiceUnavailable(
            text="No active session. MBUDDY needs a connected device or an open "
                 "WebSocket before it can hold a conversation."
        )

    # Typed input is already a clean transcript, so mark the session awake and
    # let the normal routing take over.
    # Use the setter, not the attribute: the inner-life loop watches a
    # separate awake_flag list, and setting is_awake alone left it generating
    # private thoughts mid-conversation, competing for the Groq rate limit.
    was_awake = bool(getattr(session, "is_awake", False))
    # The buddy looks awake for the whole chat (it thinks and answers on screen), while the voice stays asleep.
    runtime_status.update(chat_until=time.time() + 180)
    session._set_awake(True)
    session._cancel_inner()

    detected_lang = "ar" if any("\u0600" <= c <= "\u06FF" for c in text) else "en"

    previous_reply = getattr(session, "last_reply_text", None)
    replies_before = getattr(session, "reply_count", 0)
    turn_started = time.time()
    if not speak:
        # Suppress synthesis for this turn only; the reply still reaches
        # memory, the display, and the response we return here. The text is
        # remembered too, so a turn that waits in the queue stays silent when
        # it runs later (the field test: a muted "17*23" was spoken aloud).
        session._suppress_speech = True
        if hasattr(session, "_silent_texts"):
            session._silent_texts.add(text)
    try:
        await session._route_intent(text, detected_lang, typed=True)  # never lost to a later voice turn
        # _route_intent dispatches the reply as a background task (or queues it behind a turn still finishing). Wait
        # for THIS message's own reply, with a ceiling; another turn finishing meanwhile is not this one's answer
        # (the field test: "whats 17*23" came back with the previous question's answer).
        deadline = asyncio.get_event_loop().time() + CHAT_WAIT_S
        while asyncio.get_event_loop().time() < deadline:
            if _reply_for(session, text, replies_before) is not None or not _still_running(session, text, replies_before):
                break
            await asyncio.sleep(0.1)
    finally:
        if not (speak is False and _still_running(session, text, replies_before)) and not getattr(session, "_silent_turn", None):
            session._suppress_speech = False     # a silent turn still running (this one or a queued one) keeps its silence
        runtime_status.update(chat_until=time.time() + CHAT_STAYS_AWAKE_S)   # it stays up a while after the reply, not asleep at once
        # Typing doesn't wake the voice: if it was asleep, it goes back to
        # sleep once this reply has been said (F9).
        if not was_awake and hasattr(session, "sleep_after_typed_turn"):
            asyncio.get_event_loop().create_task(session.sleep_after_typed_turn())

    if _still_running(session, text, replies_before):
        # Still working after the ceiling: the app waits for this turn's reply (/control/chat/result) instead of
        # being told to ask again; the reply used to be lost to the user although it came a little later.
        return web.json_response({"ok": False, "you": text, "pending": True, "after": replies_before,
                                  "started": turn_started}, status=202)
    reply = _reply_for(session, text, replies_before)
    if reply is None:
        # No reply was finished for this turn (something else came in and
        # replaced it): say so instead of returning another turn's text.
        return web.json_response({"ok": False, "you": text, "error": "interrupted"}, status=409)
    return web.json_response({
        "ok": True,
        "you": text,
        "reply": reply,
        "emotion": getattr(session.state, "current", runtime_status.emotion),
        "energy": round(float(getattr(session.state, "energy", runtime_status.energy)), 1),
        "language": getattr(session, "active_language", detected_lang),
        "spoke": speak,
        "via": _via_line(turn_started),
    })


def _reply_for(session, text: str, after: int) -> str | None:
    """The reply recorded for [text] since the reply count was [after], or None. A session without a conversation
    log (a test double) falls back to its last reply once any new reply was counted."""
    if getattr(session, "reply_count", 0) <= after:
        return None
    entries = (getattr(session, "memory", None) or {}).get("conversation_log")
    if entries is None:
        return getattr(session, "last_reply_text", "") or runtime_status.last_reply or ""
    for entry in reversed(entries[-20:]):
        if (entry.get("user") or "").strip() == text.strip():
            return entry.get("mbuddy", "")
    return None


def _still_running(session, text: str, replies_before: int) -> bool:
    """This message has no reply yet but is still on its way (waiting in the queue, or its turn running)."""
    queued = session.is_queued(text) if hasattr(session, "is_queued") else getattr(session, "queued_text", "") == text
    if queued:
        return True
    if _reply_for(session, text, replies_before) is not None:
        return False
    task = getattr(session, "response_task", None)
    return task is not None and not task.done()


async def chat_result_handler(request: web.Request) -> web.Response:
    """The reply to a typed turn that was still running when /control/chat returned pending.

    Query: text (what was typed), after (the reply count /control/chat returned), started (its start time).
    Answers {"ok": true, "reply", "via"} once this text has its reply, {"pending": true} while it is still on its way,
    and {"ok": false, "error": "interrupted"} if it ended without one."""
    _require_local(request)
    text = (request.query.get("text") or "").strip()
    try:
        after = int(request.query.get("after", "0"))
        started = float(request.query.get("started", "0"))
    except ValueError:
        raise web.HTTPBadRequest(text="after and started must be numbers")
    session = active_session()
    if session is None or not text:
        return web.json_response({"ok": False, "error": "no_session"}, status=503)
    reply = _reply_for(session, text, after)
    if reply is not None:
        return web.json_response({"ok": True, "you": text, "reply": reply, "via": _via_line(started) if started else ""})
    if _still_running(session, text, after):
        return web.json_response({"ok": False, "you": text, "pending": True})
    return web.json_response({"ok": False, "you": text, "error": "interrupted"}, status=409)


def _via_line(started: float) -> str:
    """Who answered this turn, for the line under the reply: the cloud model when it was asked during the
    turn (from the usage log), otherwise the local model."""
    try:
        from core import cloud
        rec = cloud.last_call()
        if rec and rec.get("t", 0) >= started - 0.5:
            return cloud.via_text(rec)
    except Exception:  # noqa: BLE001
        pass
    return f"LOCAL 4B · {round(time.time() - started, 1)} s"


async def volume_handler(request: web.Request) -> web.Response:
    """
    Read or set MBUDDY's speaking volume.

    GET returns the current levels. POST with a `master` and/or `sfx` field
    sets them, each between 0.0 (silent) and 1.5 (boosted). The value scales
    the energy-derived level rather than replacing it, so MBUDDY still speaks
    more quietly when it is tired.
    """
    _require_local(request)
    from core.audio_settings import get_audio_settings

    audio = get_audio_settings()

    if request.method == "GET":
        return web.json_response({"ok": True, "audio": audio.to_dict()})

    data = await safe_json(request)
    changed = []

    if "master" in data or "master_volume" in data:
        value = validate_number(
            data,
            "master" if "master" in data else "master_volume",
            required=False,
            default=audio.master_volume,
            min_value=0.0,
            max_value=1.5,
        )
        audio.set_master_volume(value)
        changed.append("master")

    if "sfx" in data or "sfx_volume" in data:
        value = validate_number(
            data,
            "sfx" if "sfx" in data else "sfx_volume",
            required=False,
            default=audio.sfx_volume,
            min_value=0.0,
            max_value=1.5,
        )
        audio.set_sfx_volume(value)
        changed.append("sfx")

    if "inner" in data or "inner_volume" in data:
        value = validate_number(
            data,
            "inner" if "inner" in data else "inner_volume",
            required=False,
            default=audio.inner_volume,
            min_value=0.0,
            max_value=1.5,
        )
        audio.set_inner_volume(value)
        changed.append("inner")

    if "min_input_rms" in data:
        from core.audio_settings import MAX_INPUT_RMS, MIN_INPUT_RMS

        value = validate_number(
            data,
            "min_input_rms",
            required=False,
            default=audio.min_input_rms,
            min_value=MIN_INPUT_RMS,
            max_value=MAX_INPUT_RMS,
        )
        audio.set_min_input_rms(value)
        changed.append("min_input_rms")

    if not changed:
        raise web.HTTPBadRequest(
            text="Provide 'master', 'sfx', 'inner' (0.0-1.5) and/or 'min_input_rms' (0.0-0.2)."
        )

    return web.json_response({"ok": True, "changed": changed, "audio": audio.to_dict()})


async def companion_settings_handler(request: web.Request) -> web.Response:
    """
    Read or set the inner-life cadence: whether MBUDDY thinks to itself
    between turns, and how often.

    GET returns the current settings. POST with `inner_enabled` (bool) and/or
    `inner_min_s`/`inner_max_s` (seconds) sets them.

    This exists because the desktop settings screen used to mutate
    `core.companion_settings` directly in its own process — the backend
    process never saw the change until restarted, the same cross-process bug
    the volume slider had before it was routed through this API pattern.
    """
    _require_local(request)
    from core.companion_settings import get_companion_settings

    cs = get_companion_settings()

    if request.method == "GET":
        return web.json_response({"ok": True, "companion": cs.to_dict()})

    data = await safe_json(request)
    changed = []

    if "inner_enabled" in data:
        cs.set_inner_enabled(bool(data["inner_enabled"]))
        changed.append("inner_enabled")

    if "inner_min_s" in data or "inner_max_s" in data:
        lo = validate_number(data, "inner_min_s", required=False,
                             default=cs.inner_interval[0], min_value=1, max_value=3600)
        hi = validate_number(data, "inner_max_s", required=False,
                             default=cs.inner_interval[1], min_value=1, max_value=3600)
        cs.set_inner_interval(int(lo), int(hi))
        changed.append("inner_interval")

    if not changed:
        raise web.HTTPBadRequest(
            text="Provide 'inner_enabled', and/or 'inner_min_s'/'inner_max_s'."
        )

    return web.json_response({"ok": True, "changed": changed, "companion": cs.to_dict()})


async def embodiment_handler(request: web.Request) -> web.Response:
    """
    Read or change what body, if any, MBUDDY expects to be embodied in.

    GET returns the current state (`headless`, `embodied`, or `searching`),
    the paired body's name if any, and a human-readable description. POST
    with `action: "pair"` (and optional `body_name`) pairs a body; `action:
    "release"` returns MBUDDY to Windows-only operation.

    Pairing from the desktop used to call `core.embodiment.select_body()`
    directly in the desktop's own process, which has zero effect on the
    backend's actual running embodiment state — the two processes each load
    their own singleton. This is the endpoint that makes pairing real.
    """
    _require_local(request)
    from core.embodiment import get_embodiment

    emb = get_embodiment()

    if request.method == "GET":
        return web.json_response({
            "ok": True,
            "state": emb.state,
            "body_name": emb.body_name,
            "selected": emb.expects_body,
            "description": emb.describe(),
            # Real, already-recorded history of every headless<->embodied
            # transition this process has seen (core/embodiment.py::_record).
            # It was being tracked and simply never sent before now.
            "transitions": emb.transitions,
        })

    data = await safe_json(request)
    action = validate_string(data, "action", choices=["pair", "release"])

    if action == "pair":
        name = validate_string(data, "body_name", required=False, default="MBUDDY body")
        emb.select_body("mbuddy-esp32", name=name)
    else:
        emb.release_body()

    return web.json_response({
        "ok": True,
        "state": emb.state,
        "body_name": emb.body_name,
        "selected": emb.expects_body,
        "description": emb.describe(),
        "transitions": emb.transitions,
    })


async def permissions_handler(request: web.Request) -> web.Response:
    """
    Read or change which sensitive Windows capabilities MBUDDY may use.

    GET returns every known capability (microphone, screen capture, and
    whatever is added later — see `core/permissions.py`) with its real,
    persisted grant state; an unimplemented one is still listed, marked
    honestly rather than hidden. POST with `name` and `action` ("grant" or
    "revoke") changes one capability — this is the single place a grant is
    written, so the desktop's PC Voice button and the content-awareness
    checkbox both check it here instead of each keeping their own notion of
    "did the user say yes".
    """
    _require_local(request)
    from core.permissions import get_permission_store

    store = get_permission_store()

    if request.method == "GET":
        return web.json_response({"ok": True, "capabilities": store.snapshot()})

    data = await safe_json(request)
    name = validate_string(data, "name")
    action = validate_string(data, "action", choices=["grant", "revoke"])
    try:
        if action == "grant":
            store.grant(name)
        else:
            store.revoke(name)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc))

    return web.json_response({"ok": True, "capabilities": store.snapshot()})


async def time_handler(request: web.Request) -> web.Response:
    """
    Read or set the clock MBUDDY believes it is.

    GET returns the current display string, timezone name and offset. POST
    with an `iso` datetime string sets it — used for testing time-of-day
    behaviour (energy, mood, greetings) without waiting for real time to pass.
    """
    _require_local(request)
    from datetime import datetime as _dt

    from core.time_manager import get_time_manager

    tm = get_time_manager()

    if request.method == "GET":
        return web.json_response({
            "ok": True,
            "display": tm.format_display(),
            "timezone_name": tm.get_timezone_name(),
            "timezone_offset_min": tm.get_timezone_offset(),
            "is_shifted": tm.is_offset(),
            "shift_minutes": tm.get_offset_minutes(),
        })

    data = await safe_json(request)

    if data.get("action") == "reset":
        tm.clear_offset()
        return web.json_response({
            "ok": True,
            "display": tm.format_display(),
            "is_shifted": False,
            "shift_minutes": 0.0,
        })

    iso = validate_string(data, "iso")
    try:
        target = _dt.fromisoformat(iso)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=f"Could not parse 'iso' as a datetime: {exc}")

    offset = tm.set_time(target)
    return web.json_response({
        "ok": True,
        "display": tm.format_display(),
        "is_shifted": tm.is_offset(),
        "shift_minutes": offset,
    })


async def say_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data = await safe_json(request)
    session = active_session()
    text = validate_string(data, "text", max_length=1000)
    state = validate_string(
        data,
        "state",
        required=False,
        default=getattr(getattr(session, "state", None), "current", "Normal"),
        choices=list(VALID_STATES),
    )
    lang = validate_string(
        data,
        "lang",
        required=False,
        default=getattr(session, "active_language", "en") if session else "en",
        max_length=12,
    ) or "en"
    target = _target_from(data)
    reply = text if text.startswith("[") else f"{STATE_TO_TAG.get(state, '[NORMAL]')} {text}"
    sent = {"wireless": False, "serial": False}
    if target in {"auto", "wireless", "both"} and session is not None:
        await session._send_checked(reply, state, lang=lang)
        sent["wireless"] = True
    if target in {"auto", "serial", "both"} and (target != "auto" or not sent["wireless"]):
        sent["serial"] = serial_bridge.send_line(DeviceProtocol.speaking(state, text))
    if not (sent["wireless"] or sent["serial"]):
        raise web.HTTPServiceUnavailable(text="No ESP32 transport is connected.")
    _trace(
        goal="manual_speech",
        intent="speak_dashboard_text",
        emotion=state,
        action="speak",
        reason=f"Dashboard requested MBUDDY to speak text using emotion {state}.",
        priority="normal",
        source="dashboard_say",
    )
    return web.json_response({"ok": True, "text": text, "state": state, "lang": lang, "sent": sent})


async def command_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data: dict[str, Any] = {}
    if request.can_read_body:
        try:
            data = await request.json()
        except Exception:
            data = {}
    target = _target_from(data)
    command = request.match_info["command"]
    protocol = DeviceProtocol()
    if command == "stop":
        payload = protocol.STOP
    elif command == "quiet":
        payload = protocol.QUIET
    elif command == "clear_action":
        payload = protocol.clear_action()
    elif command == "thinking":
        payload = protocol.THINKING
    elif command == "heard":
        payload = protocol.heard("manual")
    else:
        raise web.HTTPBadRequest(text=f"Unknown command: {command}")
    transports = await _send_protocol(payload, target)
    if command == "clear_action":
        runtime_status.update(current_action="", current_action_thought="")
    _trace(
        goal="manual_device_command",
        intent=f"send_{command}_command",
        emotion=runtime_status.emotion,
        action=command,
        reason=f"Dashboard sent protocol command {command}.",
        priority="high" if command in {"stop", "quiet", "clear_action"} else "normal",
        source="dashboard_command",
    )
    return web.json_response({"ok": any(transports.values()), "command": command, "payload": payload, "sent": transports})


async def serial_ports_handler(request: web.Request) -> web.Response:
    _require_local(request)
    return web.json_response({"ports": serial_bridge.list_ports(), "serial": serial_bridge.snapshot()})


async def serial_connect_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data = await safe_json(request)
    port = validate_string(data, "port", max_length=64)
    baud = validate_number(
        data,
        "baud",
        required=False,
        default=115200,
        min_value=1200,
        max_value=1000000,
        integer=True,
    )
    try:
        serial = serial_bridge.connect(port, baud)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc))
    except RuntimeError as exc:
        raise web.HTTPServiceUnavailable(text=str(exc))
    return web.json_response({"ok": True, "serial": serial})


async def pc_voice_chunk_handler(request: web.Request) -> web.Response:
    _require_local(request)
    if request.content_length is not None and request.content_length > 65536:
        raise web.HTTPRequestEntityTooLarge(max_size=65536, actual_size=request.content_length)

    session = _require_session()
    chunk = await request.read()
    if not chunk:
        raise web.HTTPBadRequest(text="PCM audio chunk is required.")
    if len(chunk) > 65536:
        raise web.HTTPRequestEntityTooLarge(max_size=65536, actual_size=len(chunk))
    if len(chunk) % 2:
        raise web.HTTPBadRequest(text="PCM audio chunk must contain 16-bit mono samples.")

    await session.handle_audio(chunk)
    return web.json_response({"ok": True, "bytes": len(chunk), "sample_rate": 16000, "format": "pcm_s16le_mono"})


async def shutdown_handler(request: web.Request) -> web.Response:
    """
    Stop this backend process — the desktop app's "Stop Backend" button.

    Lets a background backend session (one that outlived a previous desktop
    app window, or one started independently) actually be shut down from the
    UI, instead of the only options being "leave it running forever" or
    "find it in Task Manager."
    """
    _require_local(request)

    async def _terminate():
        await asyncio.sleep(0.3)  # let this response reach the client first
        os._exit(0)

    asyncio.ensure_future(_terminate())
    return web.json_response({"ok": True, "message": "Shutting down"})


async def pc_voice_end_handler(request: web.Request) -> web.Response:
    """
    The PC mic stream just stopped (push-to-talk key/button released).

    The VAD's end-of-utterance detection expects ~2.2s of trailing silence
    chunks after speech (SILENCE_DURATION) — the ESP32 keeps streaming
    ambient noise, so that trailing silence naturally arrives. Push-to-talk
    stops streaming the instant the key is released, so that silence never
    comes and finalize_utterance() would otherwise never run. Releasing the
    key IS the end-of-utterance signal here, so finalize directly.
    """
    _require_local(request)
    session = _require_session()
    audio = getattr(session, "audio", None)
    if audio is None:
        log.info("PC voice released: session has no audio state")
        return web.json_response({"ok": True})

    # Always log — even "nothing to finalize" — so the next attempt gives a
    # real answer instead of silence indistinguishable from "nothing tried
    # to log": chunks_seen=0 means audio never arrived at all; is_speaking
    # False with chunks_seen>0 means it arrived but never crossed the VAD
    # gate (too quiet/short); is_speaking True is the normal case this
    # endpoint exists for.
    log.info(
        f"PC voice released: is_speaking={audio.is_speaking} "
        f"last_rms={audio.last_rms:.5f} noise_floor={audio.noise_floor:.4f} "
        f"buffered_chunks={len(audio.audio_buffer)} chunks_seen={audio._diag_chunk_count}"
    )
    was_speaking = audio.is_speaking
    audio.is_speaking = False
    if was_speaking or audio.audio_buffer:
        await session.finalize_utterance()
    return web.json_response({"ok": True})


async def content_capture_handler(request: web.Request) -> web.Response:
    """Capture and analyze the Windows desktop screen for local dashboard requests."""
    _require_local(request)
    data = await safe_json(request)
    force = validate_boolean(data, "force", required=False, default=True)

    try:
        from brain.content_awareness import get_awareness_engine

        engine = get_awareness_engine()
        engine.enabled = True
        engine.permission_granted = True
        context = await engine.capture_and_analyze(force=force)
    except Exception as exc:
        runtime_status.update(last_error=f"content_capture:{type(exc).__name__}")
        raise web.HTTPServiceUnavailable(text=f"Screen capture failed: {exc}")

    if context is None:
        return web.json_response({"ok": False, "message": "No significant screen change detected."})

    payload = {
        "timestamp": context.timestamp,
        "description": context.description,
        "detected_text": context.detected_text,
        "detected_apps": context.detected_apps,
        "activity_type": context.activity_type,
        "confidence": context.confidence,
    }
    runtime_status.update(
        content_awareness_enabled=True,
        cognitive_state={**(runtime_status.cognitive_state or {}), "visual_context": payload},
        last_error=None,
    )
    _trace(
        goal="manual_content_capture",
        intent="capture_current_screen_context",
        emotion=runtime_status.emotion,
        action="content_capture",
        reason=f"Dashboard captured screen context as {context.activity_type}.",
        priority="normal",
        source="dashboard_content_capture",
    )
    return web.json_response({"ok": True, "context": payload})


async def serial_disconnect_handler(request: web.Request) -> web.Response:
    _require_local(request)
    serial_bridge.disconnect()
    return web.json_response({"ok": True, "serial": serial_bridge.snapshot()})


async def tunnel_start_handler(request: web.Request) -> web.Response:
    _require_local(request)
    data: dict[str, Any] = {}
    if request.can_read_body:
        try:
            data = await request.json()
        except Exception:
            data = {}
    url = str(data.get("url", "http://127.0.0.1:5000")).strip() or "http://127.0.0.1:5000"
    return web.json_response({"ok": True, "tunnel": tunnel_manager.start(url)})


async def tunnel_stop_handler(request: web.Request) -> web.Response:
    _require_local(request)
    tunnel_manager.stop()
    return web.json_response({"ok": True, "tunnel": tunnel_manager.snapshot()})


def register_control_routes(app: web.Application) -> None:
    app.router.add_get("/control/manifest", control_manifest_handler)
    app.router.add_post("/control/action", action_handler)
    app.router.add_post("/control/action/clear", clear_action_handler)
    app.router.add_post("/control/auto_visuals", auto_visuals_handler)
    app.router.add_post("/control/sfx", sfx_handler)
    app.router.add_post("/control/state", state_handler)
    app.router.add_post("/control/energy", energy_handler)
    app.router.add_post("/control/tag", tag_handler)
    app.router.add_post("/control/say", say_handler)
    app.router.add_post("/control/chat", chat_handler)
    app.router.add_get("/control/chat/result", chat_result_handler)
    app.router.add_get("/control/power", power_handler)
    app.router.add_post("/control/power", power_handler)
    app.router.add_get("/control/llm", llm_handler)
    app.router.add_post("/control/llm", llm_handler)
    app.router.add_get("/control/llm/identity", llm_identity_handler)
    app.router.add_get("/control/memory/vectors", memory_vectors_handler)
    app.router.add_get("/control/memory/snapshot", memory_snapshot_handler)
    app.router.add_get("/control/volume", volume_handler)
    app.router.add_post("/control/volume", volume_handler)
    app.router.add_get("/control/companion_settings", companion_settings_handler)
    app.router.add_post("/control/companion_settings", companion_settings_handler)
    app.router.add_get("/control/embodiment", embodiment_handler)
    app.router.add_post("/control/embodiment", embodiment_handler)
    app.router.add_get("/control/permissions", permissions_handler)
    app.router.add_post("/control/permissions", permissions_handler)
    app.router.add_get("/control/time", time_handler)
    app.router.add_post("/control/time", time_handler)
    app.router.add_get("/control/serial/ports", serial_ports_handler)
    app.router.add_post("/control/serial/connect", serial_connect_handler)
    app.router.add_post("/control/serial/disconnect", serial_disconnect_handler)
    app.router.add_post("/control/pc-voice/chunk", pc_voice_chunk_handler)
    app.router.add_post("/control/pc-voice/end", pc_voice_end_handler)
    app.router.add_post("/control/shutdown", shutdown_handler)
    app.router.add_post("/control/content/capture", content_capture_handler)
    app.router.add_post("/control/tunnel/start", tunnel_start_handler)
    app.router.add_post("/control/tunnel/stop", tunnel_stop_handler)
    app.router.add_post("/control/{command}", command_handler)
