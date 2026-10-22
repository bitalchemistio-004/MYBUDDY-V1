"""Deterministic self-awareness and behavior selection for MBUDDY.

This module keeps emotions, tools, animations, and SFX tied to explicit state
instead of letting call sites trigger visuals opportunistically.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from core.time_manager import get_time_manager
from core.embodiment import get_embodiment

from brain.capability_registry import ANIMATION_NAMES, SFX_NAMES, TOOL_NAMES
from brain.world_model import build_world_model

PRIORITY_RANK = {"critical": 4, "high": 3, "normal": 2, "low": 1}
CONFLICTING_ANIMATIONS = {
    frozenset(("wifi_search", "celebration")): "Connection recovery and celebration communicate opposite system states.",
}

AVAILABLE_TOOLS = TOOL_NAMES
AVAILABLE_ANIMATIONS = ANIMATION_NAMES
AVAILABLE_SFX = SFX_NAMES

ACTION_DURATIONS_MS = {
    "listening_animation": 1200,
    "data_weaver": 1800,
    "scanning_targeting": 1800,
    "thought_bubble": 1800,
    "battery_charge": 2600,
    "wifi_search": 2200,
    "clock_timer": 2200,
    "celebration": 1800,
    "error_glitch": 1400,
    "starlight": 2200,
    "dreaming": 3200,
    "block_break": 2600,
    "interactive_arena": 2600,
    "heartbeat": 2200,
}


@dataclass(frozen=True)
class CognitiveState:
    emotion: str
    energy: float
    wifiConnected: bool
    charging: bool
    time: str
    currentAction: str
    availableTools: list[str] = field(default_factory=list)
    availableAnimations: list[str] = field(default_factory=list)
    availableSFX: list[str] = field(default_factory=list)
    needs: dict[str, Any] = field(default_factory=dict)
    dominantNeed: str = ""
    autoVisuals: bool = False
    awake: bool = False
    listening: bool = False
    speaking: bool = False
    thinking: bool = False
    recentActions: list[str] = field(default_factory=list)
    recentEmotions: list[str] = field(default_factory=list)
    recentSFX: list[str] = field(default_factory=list)
    recentAnimations: list[str] = field(default_factory=list)
    currentDate: str = ""
    dayOfWeek: str = ""
    timezone: str = ""
    dayPeriod: str = ""
    internetAvailable: bool = False
    memoryHealth: dict[str, Any] = field(default_factory=dict)
    capabilityRegistry: dict[str, Any] = field(default_factory=dict)
    disconnectedFeatures: list[dict[str, str]] = field(default_factory=list)
    worldModel: dict[str, Any] = field(default_factory=dict)
    embodiment: str = "headless"
    bodyFault: bool = False
    bodyName: str = "Windows"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BehaviorDecision:
    event: str
    goal: str
    intent: str
    emotion: str
    action: str = ""
    animation: str = ""
    animation_ms: int = 0
    sfx: str = ""
    tool: str = ""
    output_mode: str = "text_only"
    reason: str = ""
    need: str = ""
    emotion_reason: str = ""
    tool_reason: str = ""
    animation_reason: str = ""
    sfx_reason: str = ""
    reply_reason: str = ""
    world_state_snapshot: dict[str, Any] = field(default_factory=dict)
    priority: str = "normal"
    competing_behaviors: list[dict[str, str]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    rejected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _has(pattern: str, text: str) -> bool:
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def _output_mode(animation: str, sfx: str, event: str) -> str:
    text = event not in {"idle", "long_idle", "success", "error"}
    if text and animation and sfx:
        return "text_animation_sound"
    if text and animation:
        return "text_animation"
    if text and sfx:
        return "text_sound"
    if animation and sfx:
        return "animation_sound"
    if animation:
        return "animation_only"
    if sfx:
        return "sound_only"
    return "text_only" if text else "quiet"


def build_cognitive_state(state, *, session=None, runtime=None, memory: dict | None = None, world_model=None) -> CognitiveState:
    """Build the structured self-awareness object injected before each reply."""
    world_model = world_model or build_world_model(
        state,
        session=session,
        runtime=runtime,
        memory=memory,
    )
    world = world_model.to_dict() if hasattr(world_model, "to_dict") else dict(world_model or {})
    embodiment = get_embodiment()
    # The live link is authoritative for whether a body is reachable; the
    # embodiment record is authoritative for whether one was expected.
    embodiment.link_connected = bool(world.get("wifi_connected"))
    now = get_time_manager().now()
    needs = dict(getattr(state, "needs", {}) or {})
    dominant = ""
    if hasattr(state, "dominant_need"):
        try:
            dominant = state.dominant_need()
        except Exception:
            dominant = ""

    current_action = (
        getattr(runtime, "current_action", "")
        if runtime is not None
        else ""
    )
    return CognitiveState(
        emotion=getattr(state, "current", "Normal"),
        energy=float(getattr(state, "energy", 50.0)),
        wifiConnected=bool(world.get("wifi_connected")),
        charging=bool(getattr(runtime, "charging", False)) if runtime is not None else False,
        time=str(world.get("current_time") or now.strftime("%H:%M")),
        currentAction=current_action,
        availableTools=list(world.get("available_tools") or []),
        availableAnimations=list(world.get("available_animations") or []),
        availableSFX=list(world.get("available_sfx") or []),
        needs=needs,
        dominantNeed=dominant,
        autoVisuals=bool(getattr(session, "auto_visuals_enabled", False)) if session is not None else bool(getattr(runtime, "auto_visuals", False)),
        awake=bool(getattr(session, "is_awake", False)) if session is not None else bool(getattr(runtime, "awake", False)),
        listening=bool(getattr(runtime, "listening", False)) if runtime is not None else False,
        speaking=bool(getattr(runtime, "speaking", False)) if runtime is not None else False,
        thinking=bool(getattr(runtime, "thinking", False)) if runtime is not None else False,
        recentActions=list(getattr(runtime, "recent_actions", []) or []) if runtime is not None else [],
        recentEmotions=list(getattr(runtime, "recent_emotions", []) or []) if runtime is not None else [],
        recentSFX=list(getattr(runtime, "recent_sfx", []) or []) if runtime is not None else [],
        recentAnimations=list(getattr(runtime, "recent_animations", []) or []) if runtime is not None else [],
        currentDate=str(world.get("current_date", "")),
        dayOfWeek=str(world.get("day_of_week", "")),
        timezone=str(world.get("timezone", "")),
        dayPeriod=str(world.get("day_period", "")),
        internetAvailable=bool(world.get("internet_available")),
        memoryHealth=dict(world.get("memory_health", {}) or {}),
        capabilityRegistry=dict(world.get("capability_registry", {}) or {}),
        disconnectedFeatures=list(world.get("disconnected_features", []) or []),
        worldModel=world,
        embodiment=embodiment.state,
        bodyFault=embodiment.is_fault,
        bodyName=embodiment.body_name,
    )


def validate_behavior_decision(decision: BehaviorDecision) -> BehaviorDecision:
    """Reject decisions that cannot explain behavior surfaces they touch."""
    if not decision.reason.strip():
        raise ValueError("Behavior decision rejected: reason is required.")
    if decision.priority not in PRIORITY_RANK:
        raise ValueError(f"Behavior decision rejected: invalid priority {decision.priority!r}.")
    if (decision.emotion or decision.action or decision.animation or decision.sfx or decision.tool) and not decision.reason.strip():
        raise ValueError("Behavior decision rejected: behavior surfaces require a reason.")
    if decision.animation and not decision.animation_reason:
        raise ValueError("Behavior decision rejected: animation_reason is required when an animation is selected.")
    if decision.sfx and not decision.sfx_reason:
        raise ValueError("Behavior decision rejected: sfx_reason is required when SFX is selected.")
    if decision.tool and not decision.tool_reason:
        raise ValueError("Behavior decision rejected: tool_reason is required when a tool is selected.")
    return decision


def _goal_for_event(event: str, text: str) -> str:
    if event == "listening":
        return "capture_user_input"
    if event == "idle":
        return "maintain_idle_presence"
    if event == "long_idle":
        return "recover_during_long_idle"
    if event == "success":
        return "acknowledge_success"
    if event == "error":
        return "recover_from_error"
    if _has(r"\b(time|clock|timer|alarm|date)\b", text):
        return "answer_time_request"
    if _has(r"\b(search|find|look up|weather|latest|current|today|now|internet)\b", text):
        return "retrieve_information"
    return "answer_user"


def _intent_for_event(event: str, text: str) -> str:
    if event == "listening":
        return "listen_without_speaking"
    if event in {"idle", "long_idle"}:
        return "non_intrusive_background_state"
    if event == "success":
        return "show_success_feedback"
    if event == "error":
        return "show_failure_feedback"
    if _has(r"\b(calculate|math|compute|convert|sum|percent|percentage)\b", text):
        return "calculate_or_structure"
    if _has(r"\b(remember|memory|learn|teach|save|note|idea)\b", text):
        return "use_or_update_memory"
    if _has(r"\b(search|find|look up|weather|latest|current|today|now|internet)\b", text):
        return "search_or_scan"
    return "respond_conversationally"


def _conflict_reasons(animation: str, current_action: str) -> list[str]:
    if not animation or not current_action:
        return []
    conflicts: list[str] = []
    pair = frozenset((animation, current_action))
    if pair in CONFLICTING_ANIMATIONS:
        conflicts.append(CONFLICTING_ANIMATIONS[pair])
    return conflicts


# The current time or date, asked plainly ("what time is it", "whats the time", "what's the date", Arabic too); not a
# question about the time OF something ("what time do I take my pills", "what time is the meeting").
_CLOCK_ASK = re.compile(
    r"\b(what'?s the time|whats the time|what is the time|what time is it|the time (?:now|right now|please)|tell me the time|current time"
    r"|time right now|what'?s (?:the |today'?s )?date|whats (?:the |today'?s )?date|what is (?:the |today'?s )?date|date today|today'?s date"
    r"|what day is (?:it|today))\b"
    r"|كم الساعة|الساعة كم|شو الساعة|قديش الساعة|ما الوقت|كم الوقت|ما التاريخ|شو التاريخ")
_CLOCK_NOT = re.compile(r"\bwhat time (?:do|does|did|should|will|would|can|are|am|is (?:my|the|our|your|his|her|their))\b")


def _asks_clock(lower: str) -> bool:
    return bool(_CLOCK_ASK.search(lower)) and not _CLOCK_NOT.search(lower)


def _keyword_tool(lower: str) -> dict | None:
    """The tool a plain request names, in the order decide_behavior always checked them, or None. A lookup request
    (news, weather, "search") is left to the agent, as before, so it returns None and stops the other checks."""
    def t(tool, emotion, animation, sfx, reason):
        return {"tool": tool, "emotion": emotion, "animation": animation, "sfx": sfx, "reason": reason}
    if _asks_clock(lower):
        return t("clock", "Focused", "clock_timer", "clock_tick", "The user asked about time, so the real clock tool and clock animation fit.")
    is_about_self = _has(r"\b(what|who|how) (are|is|do|much|does) your?\b|\byour (energy|mood|state|battery|body|hardware|tools?|memory|capabilit\w*)\b"
                         r"|\bare you (ok|okay|tired|awake|connected|there)\b", lower)
    if not is_about_self and (_has(r"\b(search|look up|google|browse|find out)\b", lower) or _has(
            r"\b(weather|forecast|news|price|stock|score|scores|standings|fixture|exchange rate|release date|who won|headlines)\b", lower)):
        return None
    if _has(r"\b(remember|memory|learn|teach|save|note|idea|think about)\b", lower):
        return t("memory", "Glee", "thought_bubble", "", "The request is about learning or memory, so an idea/thought visual fits.")
    if _has(r"\b(calculate|math|compute|convert|sum|percent|percentage|times|plus|minus|divided|equals)\b", lower) \
            and not _has(r"\b(volume|sound|audio)\b", lower) and len(lower.split()) <= 16:
        return t("calculator", "Focused", "data_weaver", "", "The request needs structured calculation, so a thought-network visual fits.")
    if _has(r"\b(remind me to|remind me (in|after) .+ to|add (a )?task|to-?do|my tasks|pending tasks|task list"
            r"|i need to .+ by|i have to .+ by|mark .+ (done|complete))\b", lower):
        return t("task", "Focused", "thought_bubble", "", "The request is a task/reminder to track, not a countdown, so the task tracker fits.")
    if _has(r"\b(timer|countdown|remind me in|set a timer|start timer)\b", lower):
        return t("timer", "Focused", "clock_timer", "clock_tick", "Timer request needs time-tracking tool and clock visual.")
    if _has(r"\b(volume|mute|unmute|louder|quieter|turn (up|down) the sound|speaker level)\b", lower):
        return t("sys_volume", "Focused", "", "", "The request is about system volume, so the real volume-control tool fits.")
    return None


def decide_behavior(cog: CognitiveState, *, event: str, text: str = "") -> BehaviorDecision:
    """Choose one intentional behavior. No random animation/SFX selection."""
    lower = text.lower().strip()
    candidates: list[dict[str, str]] = []

    def candidate(name: str, priority: str, reason: str) -> None:
        candidates.append({"behavior": name, "priority": priority, "reason": reason})

    def make(
        emotion: str,
        *,
        animation: str = "",
        sfx: str = "",
        tool: str = "",
        priority: str = "normal",
        reason: str,
    ) -> BehaviorDecision:
        if not reason.strip():
            raise ValueError("Behavior decision rejected: reason is required.")
        conflicts = _conflict_reasons(animation, cog.currentAction)
        if conflicts and PRIORITY_RANK.get(priority, 0) < PRIORITY_RANK["critical"]:
            candidates.append({
                "behavior": animation,
                "priority": priority,
                "reason": reason,
                "lost_because": "; ".join(conflicts),
            })
            reason = f"{reason} Suppressed conflicting animation: {'; '.join(conflicts)}"
            animation = ""
        if animation and not cog.autoVisuals:
            reason = f"{reason} Auto visuals are paused, so the visual output is intentionally suppressed."
            animation = ""
        if animation and animation in cog.recentAnimations[:3] and PRIORITY_RANK.get(priority, 0) <= PRIORITY_RANK["normal"]:
            candidates.append({
                "behavior": animation,
                "priority": priority,
                "reason": reason,
                "lost_because": "Recently used animation cooldown.",
            })
            reason = f"{reason} Animation suppressed by recent-use cooldown."
            animation = ""
        registry_supplied = bool(cog.capabilityRegistry)
        if registry_supplied and animation and animation not in cog.availableAnimations and priority != "critical":
            candidates.append({
                "behavior": animation,
                "priority": priority,
                "reason": reason,
                "lost_because": "Animation capability is unavailable or disabled in the registry.",
            })
            reason = f"{reason} Animation suppressed because capability registry marks it unavailable."
            animation = ""
        if registry_supplied and sfx and sfx not in cog.availableSFX and priority != "critical":
            candidates.append({
                "behavior": sfx,
                "priority": priority,
                "reason": reason,
                "lost_because": "SFX capability is unavailable or disabled in the registry.",
            })
            reason = f"{reason} SFX suppressed because capability registry marks it unavailable."
            sfx = ""
        if registry_supplied and tool and tool not in cog.availableTools:
            candidates.append({
                "behavior": tool,
                "priority": priority,
                "reason": reason,
                "lost_because": "Tool capability is unavailable or not implemented as usable.",
            })
            reason = f"{reason} Tool {tool} is unavailable, so MBUDDY must not pretend it can execute it."
            tool = ""
        action = animation or ""
        competing = [
            {
                **item,
                "lost_because": item.get("lost_because")
                or ("Lower priority than chosen behavior." if item.get("behavior") != (action or sfx or emotion) else "Chosen.")
            }
            for item in candidates
            if item.get("behavior") != (action or sfx or emotion)
        ]
        return BehaviorDecision(
            event=event,
            goal=_goal_for_event(event, text),
            intent=_intent_for_event(event, text),
            emotion=emotion,
            action=action,
            animation=animation,
            animation_ms=ACTION_DURATIONS_MS.get(animation, 0),
            sfx=sfx,
            tool=tool,
            output_mode=_output_mode(animation, sfx, event),
            reason=reason,
            need=cog.dominantNeed,
            emotion_reason=(
                "Selected by low energy state." if emotion == "Sleepy"
                else "Selected by focused reasoning state." if emotion == "Focused"
                else "Selected by success/positive state." if emotion in {"Happy", "Glee"}
                else "Selected by warning/recovery state." if emotion == "Worried"
                else "Selected by baseline conversational state."
            ),
            tool_reason=(f"Tool {tool} is relevant to this request and usable in the capability registry." if tool else ""),
            animation_reason=(f"Animation {animation} matches the active goal and capability registry state." if animation else ""),
            sfx_reason=(f"SFX {sfx} matches the active goal and is usable in the capability registry." if sfx else ""),
            reply_reason="Reply should answer the user while reflecting the selected state and constraints.",
            world_state_snapshot=cog.worldModel,
            priority=priority,
            competing_behaviors=competing,
            conflicts=conflicts,
        )

    if cog.charging:
        candidate("battery_charge", "high", "Charging state competes with other visuals.")
    if cog.energy < 20:
        candidate("battery_charge", "high", "Low energy competes with conversational visuals.")
    # A body that was paired but cannot be reached is a genuine fault.
    # No body at all just means MBUDDY is running on Windows, which is fine.
    if cog.bodyFault:
        candidate("wifi_search", "high", "A paired body is unreachable, which competes with routine visuals.")

    if event == "error" or _has(r"\b(error|crash|failed|failure|timeout|broken|glitch)\b", lower):
        return validate_behavior_decision(make("Worried", animation="error_glitch", sfx="glitch_screech", priority="critical", reason="A failure or recovery condition needs a visible warning."))

    # A request a tool answers by code (the time, a sum, a timer, a task, the volume) is found before the power state:
    # charging or low energy used to return first, so while the laptop was plugged in no keyword tool ever ran and the
    # model guessed the time (the field test, 2026-10-19). The mood still follows the power state.
    if text and event not in {"listening", "success", "idle", "long_idle"}:
        hit = _keyword_tool(lower)
        if hit:
            mood = "Sleepy" if cog.energy < 20 else hit["emotion"]
            return validate_behavior_decision(make(mood, animation=hit["animation"], sfx=hit["sfx"], tool=hit["tool"], priority="normal", reason=hit["reason"]))

    if cog.charging:
        return validate_behavior_decision(make("Happy", animation="battery_charge", sfx="charge_hum", priority="high", reason="Charging is active, so power state is the dominant behavior."))

    if cog.energy < 20:
        return validate_behavior_decision(make("Sleepy", animation="battery_charge", sfx="", priority="high", reason="Energy is below 20%, so MBUDDY should act aware of low power."))

    # Reconnection used to return here, which meant no tool, intent, or
    # emotion branch below ever ran while the device was away. It now only
    # surfaces when there is nothing more useful to do, further down.

    if event == "listening":
        return validate_behavior_decision(make("Focused", animation="listening_animation", sfx="", priority="normal", reason="Audio input is arriving; MBUDDY should look attentive without speaking."))

    if event == "success":
        return validate_behavior_decision(make("Happy", animation="celebration", sfx="success_chime", priority="high", reason="The previous operation completed successfully."))

    if event == "long_idle":
        return validate_behavior_decision(make("Sleepy", animation="dreaming", sfx="sleep_dream", priority="low", reason="Long idle time should move into a quiet dreaming state."))

    if event == "idle":
        if cog.bodyFault:
            return validate_behavior_decision(make("Focused", animation="wifi_search", sfx="sonar_ping", priority="high", reason="A paired body is unreachable, so idle time is spent trying to reach it."))
        if cog.dominantNeed == "novelty":
            return validate_behavior_decision(make("Focused", animation="block_break", priority="low", reason="The novelty need is dominant, so idle behavior becomes a tiny game."))
        if cog.dominantNeed == "competence":
            return validate_behavior_decision(make("Focused", animation="scanning_targeting", priority="low", reason="The competence need is dominant, so idle behavior checks targets."))
        return validate_behavior_decision(make("Normal", animation="starlight", priority="low", reason="Idle is calm and stable, so a quiet ambient animation fits."))

    if _asks_clock(lower):
        return validate_behavior_decision(make("Focused", animation="clock_timer", sfx="clock_tick", tool="clock", priority="normal", reason="The user asked about time, so the real clock tool and clock animation fit."))

    # Questions about MBUDDY itself must never reach for the internet. "What
    # are you running on right now" used to match on the bare word "now" and
    # come back with search results about server hardware.
    is_about_self = _has(
        r"\b(what|who|how) (are|is|do|much|does) your?\b"
        r"|\byour (energy|mood|state|battery|body|hardware|tools?|memory|capabilit\w*)\b"
        r"|\bare you (ok|okay|tired|awake|connected|there)\b",
        lower,
    )
    # A bare "now", "today" or "where" is far too weak on its own — those words
    # appear constantly in ordinary conversation. Require either an explicit
    # lookup verb or a topic that genuinely changes from day to day.
    wants_lookup = _has(r"\b(search|look up|google|browse|find out)\b", lower)
    wants_fresh_topic = _has(
        r"\b(weather|forecast|news|price|stock|score|scores|standings|fixture"
        r"|exchange rate|release date|who won|headlines)\b",
        lower,
    )
    if not is_about_self and (wants_lookup or wants_fresh_topic):
        # The look only: the agent's model picks the tool (web_search,
        # get_weather, or a music/Chrome search). Choosing web_search here on
        # the word "search" sent "Search for Avicii on YouTube Music" to an
        # internet search, and the model then claimed it was playing (S89).
        return validate_behavior_decision(make("Focused", animation="scanning_targeting", sfx="", priority="normal", reason="The request needs focused lookup or scanning behavior."))

    if _has(r"\b(remember|memory|learn|teach|save|note|idea|think about)\b", lower):
        return validate_behavior_decision(make("Glee", animation="thought_bubble", sfx="", tool="memory", priority="normal", reason="The request is about learning or memory, so an idea/thought visual fits."))

    # "Set the volume to 50 percent" is a volume request, not arithmetic. A long question that happens to say
    # "times" ("the start times from 9") is a reasoning problem for the model, not for the calculator
    # (live, 2026-10-08), so only a short request counts.
    if _has(r"\b(calculate|math|compute|convert|sum|percent|percentage|times|plus|minus|divided|equals)\b", lower) \
            and not _has(r"\b(volume|sound|audio)\b", lower) and len(lower.split()) <= 16:
        return validate_behavior_decision(make("Focused", animation="data_weaver", tool="calculator", priority="normal", reason="The request needs structured calculation, so a thought-network visual fits."))

    if _has(
        r"\b(remind me to|remind me (in|after) .+ to|add (a )?task|to-?do|my tasks|pending tasks|task list"
        r"|i need to .+ by|i have to .+ by|mark .+ (done|complete))\b",
        lower,
    ):
        return validate_behavior_decision(make("Focused", animation="thought_bubble", sfx="", tool="task", priority="normal", reason="The request is a task/reminder to track, not a countdown, so the task tracker fits."))

    if _has(r"\b(timer|countdown|remind me in|set a timer|start timer)\b", lower):
        return validate_behavior_decision(make("Focused", animation="clock_timer", sfx="clock_tick", tool="timer", priority="normal", reason="Timer request needs time-tracking tool and clock visual."))

    if _has(r"\b(volume|mute|unmute|louder|quieter|turn (up|down) the sound|speaker level)\b", lower):
        return validate_behavior_decision(make("Focused", tool="sys_volume", priority="normal", reason="The request is about system volume, so the real volume-control tool fits."))

    if event in {"thinking", "conversation"}:
        word_count = len(lower.split())
        complex_turn = "?" in text or word_count >= 5 or _has(r"\b(code|debug|explain|plan|why|how|fix|build)\b", lower)
        if complex_turn:
            return validate_behavior_decision(make("Focused", animation="data_weaver", priority="normal", reason="The reply needs reasoning, so MBUDDY shows its thought network."))
        return validate_behavior_decision(make("Normal", priority="low", reason="This is a simple conversational turn; no animation or sound is needed."))

    return validate_behavior_decision(make(cog.emotion or "Normal", priority="low", reason="No stronger system condition applies, so MBUDDY stays quiet visually."))
