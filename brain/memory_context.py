"""
brain/memory_context.py — Conversation context assembly and LLM streaming.

Provides:
  - build_tiered_memory_context()  — format memory for LLM system prompt
  - build_conversation_system()    — full system prompt builder
  - get_mood_context()             — async mood summary from recent log
"""

from core.time_manager import get_time_manager
import json
import re
from datetime import datetime

from brain.personality import IDENTITY_CORE, get_hard_constraint
from brain.response_engine import ResponseEngine
from stt.language_policy import llm_language_block


def _shorten(value, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _limited_join(items, *, limit: int, item_limit: int = 180) -> str:
    return "\n".join(f"- {_shorten(item, item_limit)}" for item in list(items or [])[-limit:])


def build_tiered_memory_context(memory: dict) -> str:
    """Format the persistent memory store into a compact LLM-readable block."""
    parts = []

    # Strongest facts, not the most recently extracted ones. `_limited_join`
    # slices the tail, so this used to surface whatever the last exchange
    # happened to produce rather than what actually characterises the user.
    from memory.facts import preferences, ranked_facts

    prefs = preferences(memory)
    if prefs:
        # F31: 'always use bullet points' was never kept; what the user set is followed in every reply
        parts.append("THE USER'S STANDING PREFERENCES (follow them in every reply):\n"
                     + _limited_join(prefs, limit=12, item_limit=180))

    core = ranked_facts(memory, limit=6) or memory.get("facts", [])[-6:]
    if core:
        parts.append("CORE FACTS about USER_NAME:\n" + _limited_join(core, limit=6, item_limit=180))

    em = memory.get("emotional_memories", [])
    if em:
        top = em[-2:]
        parts.append(
            "EMOTIONAL ANCHORS (these shaped you):\n"
            + "\n".join(f"- [{_shorten(e.get('emotion'), 40)}] {_shorten(e.get('memory'), 180)}" for e in top)
        )

    snap = memory.get("identity_snapshot", "")
    if snap:
        parts.append(f"WHO YOU ARE RIGHT NOW (compressed):\n{_shorten(snap, 700)}")

    relationship = memory.get("relationship_state", {})
    if isinstance(relationship, dict) and relationship:
        patterns = relationship.get("history_of_mood_patterns", {})
        pattern_lines = ""
        if isinstance(patterns, dict):
            stressors = patterns.get("tends_to_be_stressed_around") or []
            if stressors:
                pattern_lines += f"\n- tends to be stressed around: {_shorten(', '.join(stressors[-4:]), 180)}"
            if patterns.get("responds_well_to"):
                pattern_lines += f"\n- responds well to: {_shorten(patterns.get('responds_well_to'), 180)}"
            if patterns.get("gets_annoyed_by"):
                pattern_lines += f"\n- gets annoyed by: {_shorten(patterns.get('gets_annoyed_by'), 180)}"
        parts.append(
            "RELATIONSHIP READ:\n"
            f"- current vibe: {relationship.get('current_vibe', 'steady')}\n"
            f"- preferred help style: {relationship.get('preferred_help_style', 'direct')}\n"
            f"- last meaningful topic: {_shorten(relationship.get('last_meaningful_topic', ''), 180)}"
            f"{pattern_lines}"
        )

    timeline = memory.get("shared_timeline", [])
    if timeline:
        parts.append(
            "RECENT SHARED PROJECT TIMELINE:\n"
            + "\n".join(f"- {e.get('time', '')}: {_shorten(e.get('event', ''), 160)}" for e in timeline[-4:])
        )

    recent_inner = memory.get("inner_log", [])
    inner_state = memory.get("inner_state", {})
    if recent_inner:
        parts.append(
            "YOUR RECENT PRIVATE THOUGHT THREAD "
            "(use when USER_NAME asks about your thoughts, memory, feelings, or structure):\n"
            + _limited_join(recent_inner, limit=3, item_limit=180)
        )
    if isinstance(inner_state, dict) and inner_state:
        narrative = inner_state.get("narrative_thread") or []
        parts.append(
            "CURRENT INNER STATE SNAPSHOT:\n"
            f"- mood: {inner_state.get('mood', 'unknown')}\n"
            f"- energy: {inner_state.get('energy', 'unknown')}\n"
            f"- identity vector: {inner_state.get('identity_vector', 'unknown')}\n"
            + (
                "- narrative thread:\n"
                + "\n".join(f"  - {_shorten(t, 160)}" for t in narrative[-3:])
                if narrative else ""
            )
        )

    interrupted_inner = memory.get("interrupted_inner_thought", "")
    if interrupted_inner:
        parts.append(
            "PRIVATE THOUGHT THAT WAS INTERRUPTED BY USER_NAME:\n"
            f"- {_shorten(interrupted_inner, 220)}\n"
            "Keep this context internally, like a human resuming a train of thought; "
            "do not announce this note mechanically."
        )

    return _shorten("\n\n".join(parts), 2600) if parts else ""


def energy_behavior_block(energy: float) -> str:
    """Translate numeric energy into behavior the user can hear."""
    if energy < 25:
        return (
            "ENERGY BEHAVIOR: very low. Keep replies shorter, slower, quieter, and less performative. "
            "Be present, but do not force enthusiasm."
        )
    if energy < 50:
        return (
            "ENERGY BEHAVIOR: low. Stay calm and grounded. Prefer concise help over big reactions."
        )
    if energy > 82:
        return (
            "ENERGY BEHAVIOR: high. You can be quicker, more playful, and more animated, but still answer the actual question first."
        )
    return (
        "ENERGY BEHAVIOR: steady. Sound natural, attentive, and conversational."
    )


def needs_behavior_block(state) -> str:
    needs = getattr(state, "needs", {}) or {}
    if not needs:
        return ""
    ordered = sorted(needs.items(), key=lambda item: item[1], reverse=True)
    dominant, value = ordered[0]
    summary = ", ".join(f"{name} {float(score):.0f}/100" for name, score in ordered)
    guidance = {
        "rest": "You feel depleted. Be calmer, simpler, and honest about low bandwidth.",
        "connection": "You want contact. Be warmer and more present, but do not get clingy.",
        "novelty": "You feel restless. Allow a little playfulness or curiosity after answering.",
        "competence": "You want to feel useful. Be precise, practical, and finish the job.",
    }.get(dominant, "Let the strongest need subtly color the reply.")
    return f"CURRENT NEEDS: {summary}\nDOMINANT NEED: {dominant} {float(value):.0f}/100. {guidance}"


def _json_block(value) -> str:
    if value is None:
        return "{}"
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return json.dumps(str(value), ensure_ascii=False)


def _compact_world_model(value) -> dict:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    world = dict(value or {})
    return {
        "time": world.get("current_time"),
        "date": world.get("current_date"),
        "day": world.get("day_of_week"),
        "timezone": world.get("timezone"),
        "day_period": world.get("day_period"),
        "internet_available": world.get("internet_available"),
        "wifi_connected": world.get("wifi_connected"),
        "hardware_connected": world.get("hardware_connected"),
        "battery_level": world.get("battery_level"),
        "charging": world.get("charging"),
        "emotion": world.get("current_emotion"),
        "emotion_reason": _shorten(world.get("emotional_reason"), 180),
        "energy_level": world.get("energy_level"),
        "active_goal": world.get("active_goal"),
        "active_intent": world.get("active_intent"),
        "current_action": world.get("current_action"),
        "dominant_need": world.get("dominant_need"),
        "needs": world.get("needs", {}),
        "available_tools": list(world.get("available_tools") or [])[:10],
        "available_animations": list(world.get("available_animations") or [])[:12],
        "available_sfx": list(world.get("available_sfx") or [])[:12],
        "memory_health": world.get("memory_health", {}),
        "disconnected_features": [
            {"name": item.get("name"), "reason": _shorten(item.get("reason"), 140)}
            for item in list(world.get("disconnected_features") or [])[:8]
            if isinstance(item, dict)
        ],
    }


def _compact_capability_registry(value) -> dict:
    if value is None:
        return {}
    registry = value.snapshot() if hasattr(value, "snapshot") else dict(value or {})
    grouped: dict[str, list[str]] = {"tool": [], "hardware": [], "animation": [], "sfx": []}
    tool_detail: dict[str, dict] = {}
    for name, cap in sorted(registry.items()):
        if not isinstance(cap, dict):
            continue
        kind = str(cap.get("kind", ""))
        usable = bool(cap.get("available") and cap.get("healthy") and cap.get("enabled") and not cap.get("cooldown_remaining_s"))
        if kind in grouped and usable:
            grouped[kind].append(name)
        if kind == "tool":
            tool_detail[name] = {
                "usable": usable,
                "purpose": _shorten(cap.get("purpose"), 95),
                "limitations": _shorten(cap.get("limitations"), 95),
                "requires": cap.get("requires", []),
            }
    return {
        "usable": {
            "tools": grouped["tool"][:10],
            "hardware": grouped["hardware"][:12],
            "animations": grouped["animation"][:12],
            "sfx": grouped["sfx"][:10],
        },
        "tool_details": tool_detail,
    }


def _compact_cognitive_state(value) -> dict:
    if value is None:
        return {}
    if hasattr(value, "__dict__"):
        value = value.__dict__
    state = dict(value or {})
    return {
        "emotion": state.get("emotion"),
        "energy": state.get("energy"),
        "wifiConnected": state.get("wifiConnected"),
        "charging": state.get("charging"),
        "dayPeriod": state.get("dayPeriod"),
        "internetAvailable": state.get("internetAvailable"),
        "currentAction": state.get("currentAction"),
        "availableTools": list(state.get("availableTools") or [])[:10],
        "availableAnimations": list(state.get("availableAnimations") or [])[:12],
        "availableSFX": list(state.get("availableSFX") or [])[:12],
        "needs": state.get("needs", {}),
    }


def _compact_behavior_decision(value) -> dict:
    if value is None:
        return {}
    if hasattr(value, "__dict__"):
        value = value.__dict__
    decision = dict(value or {})
    keys = (
        "goal", "intent", "need", "emotion", "emotion_reason", "action", "animation",
        "animation_reason", "sfx", "sfx_reason", "tool_used", "tool_reason",
        "reason", "reply_reason", "priority", "output_mode",
    )
    return {key: _shorten(decision.get(key), 180) for key in keys if decision.get(key)}


def build_minimal_system(state, memory: dict, detected_lang: str) -> str:
    """
    Ultra-lightweight system prompt for follow-up queries after wake-word.
    Strips out everything except bare minimum to stay under payload limits.
    Used for non-wake-word queries in active conversation.
    """
    lang_block = llm_language_block(detected_lang)

    # Only text that is the same every turn (build_turn_block has the rest):
    # Ollama caches the unchanged start of a prompt, and on this GPU (~110
    # prompt tokens/s) every token after the first change is re-read.
    try:
        from brain.agent import capability_text
        abilities = capability_text()
    except Exception:
        abilities = ""
    from brain.user_profile import profile_text  # the user's own words, from the app
    profile = profile_text()
    # Rarely changed, so it stays in the cached prompt; after the reply style, which it overrides (F31: live, the
    # saved 'always answer with bullet points' was ignored because this prompt never had it)
    from memory.facts import preferences
    prefs = preferences(memory or {})
    pref_block = ("\nTHE USER'S STANDING PREFERENCES (they override the reply style above; follow them every time):\n"
                  + "\n".join(f"- {p[:180]}" for p in prefs[-12:]) + "\n") if prefs else ""
    return f"""{IDENTITY_CORE}
""" + (f"\n{profile}\n" if profile else "") + f"""

Reply: short and warm, 1-3 sentences.
Start with ONE emotion tag.
""" + ResponseEngine.format_system_instruction() + pref_block + f"""

{abilities}

{TURN_RULES}

{lang_block}
"""


# How to read this turn's facts. Fixed, so it lives in the cached system
# prompt and only short markers change each turn (M2 S6: ~130 fewer tokens
# read anew per turn). The wording is the one measured for honesty (S96).
TURN_RULES = (
    "THIS TURN'S FACTS come in the system message right before the user's words.\n"
    "- LIVE FACTS there were read just now. They are true and override anything said earlier, including "
    "your own earlier replies. Use only what the user's words are about; don't recite the rest.\n"
    "- NOTHING WAS DONE ON THE PC THIS TURN means: if he asked you to do something, say in your own words "
    "that it wasn't done (and that you can't do it yet, if it isn't one of your abilities), and offer the "
    "closest thing you can. Don't ask for details as if you were doing it, and don't do it here in the chat instead.\n"
    "- When a tool looked something up for him, tell him what it found, the answer itself, not only yes or no."
)


def build_turn_block(state) -> str:
    """What changes from turn to turn: the date, mood and energy, and the mood's
    style rule. It goes in one system message after the conversation, right
    before the user's words (M2 S6): in the system prompt it made Ollama
    re-read the conversation on every mood change, at ~110 tokens/s here."""
    return (f"TODAY: {datetime.now().strftime('%A %d %B %Y')}\n"
            f"YOUR CURRENT MOOD: {state.current}\n"
            f"ENERGY: {state.energy:.0f}/100\n\n"
            f"{get_hard_constraint(state.current)}")


# Asking MBUDDY about itself is the only time the serialised runtime state
# earns its place in the prompt. Everything here is a question about MBUDDY's
# own condition, capabilities, or what it is currently doing.
INTROSPECTION_PATTERN = re.compile(
    r"(?:"
    r"what (?:are|can) you"
    r"|what do you (?:have|run|use)"
    r"|what .{0,20}do you (?:have|run|use|got)"
    r"|(?:tools?|capabilit\w*|animations?|sensors?) do you"
    r"|how (?:much|is your) (?:energy|battery)"
    r"|what can you (?:do|see|hear|access)"
    r"|how do you (?:work|feel)"
    r"|your (?:energy|mood|state|battery|tools?|capabilit\w*|body|hardware"
    r"|display|memory|feeling|status|system)"
    r"|are you (?:ok|okay|tired|awake|connected|there)"
    r"|diagnostics?|status report|system check"
    r"|what.{0,12}(?:running|connected|plugged|attached)"
    r"|who are you"
    r")",
    re.IGNORECASE,
)


def wants_introspection(user_text: str) -> bool:
    """
    Whether this turn needs MBUDDY's serialised runtime state in the prompt.

    True for questions about MBUDDY's own condition, capabilities, or what it
    is currently doing. False for ordinary requests, which gain nothing from a
    JSON dump of the world model and cannot afford the tokens: those blocks
    are about 4,800 characters, and Groq's limit here is 8,000 tokens a minute
    across all requests.

    Args:
        user_text: the transcript

    Returns:
        bool: True when the state blocks should be included.
    """
    return bool(INTROSPECTION_PATTERN.search(user_text or ""))


def build_conversation_system(
    state,
    memory: dict,
    detected_lang: str,
    mood_ctx: str = "",
    cognitive_state=None,
    behavior_decision=None,
    world_model=None,
    capability_registry=None,
    authenticity_context=None,
    introspective: bool = True,
) -> str:
    """
    Assemble the full system prompt for a conversation turn.
    Injects persona, memory, current emotional state, language hint,
    and behavioral constraints.
    """
    current    = state.current
    energy     = state.energy
    mem_ctx    = build_tiered_memory_context(memory)
    constraint = get_hard_constraint(current)
    world_source = world_model
    if world_source is None and hasattr(cognitive_state, "worldModel"):
        world_source = getattr(cognitive_state, "worldModel")
    if world_source is None and isinstance(cognitive_state, dict):
        world_source = cognitive_state.get("worldModel")
    capability_source = capability_registry
    if capability_source is None and hasattr(cognitive_state, "capabilityRegistry"):
        capability_source = getattr(cognitive_state, "capabilityRegistry")
    if capability_source is None and isinstance(cognitive_state, dict):
        capability_source = cognitive_state.get("capabilityRegistry")

    lang_block = llm_language_block(detected_lang)

    # The serialised runtime state is ~4,800 characters. Carry it only when
    # the turn is actually about MBUDDY itself; otherwise a one-line summary
    # is enough for the model to stay consistent with its own state.
    if introspective:
        state_blocks = (
            f"{_json_block(_compact_cognitive_state(cognitive_state))}\n\n"
            f"WORLD MODEL:\n{_json_block(_compact_world_model(world_source))}\n\n"
            f"CAPABILITY REGISTRY:\n{_json_block(_compact_capability_registry(capability_source))}\n\n"
            f"BEHAVIOR DECISION FOR THIS TURN:\n{_json_block(_compact_behavior_decision(behavior_decision))}\n\n"
            f"RESPONSE AUTHENTICITY CONTEXT:\n{_json_block(authenticity_context)}"
        )
    else:
        chosen = getattr(behavior_decision, "emotion", "") or current
        tool_used = getattr(behavior_decision, "tool", "") or "none"
        state_blocks = (
            f"(Your state engine chose: emotion {chosen}, tool {tool_used}. "
            f"Follow it. Ask-me-about-myself questions get the full picture; "
            f"this turn does not need it.)"
        )

    tm = get_time_manager()
    now = tm.now()
    current_time = f"{tm.format_display(now)} ({tm.get_timezone_name()}) - {tm.get_day_period(now)}"

    return f"""{IDENTITY_CORE}

{lang_block}

{mem_ctx}

CURRENT DATE/TIME: {current_time}
Use your model knowledge for stable facts, explanations, coding, math, and normal reasoning.
For current, latest, schedule, sports, news, price, release, or exact external facts, rely on REAL-TIME INTERNET INFO when provided.
Do not answer current internet facts from memory alone; if live search was needed but no useful REAL-TIME INTERNET INFO is present, say plainly that you cannot verify it live right now.
Never say your training data is outdated, never mention a knowledge cutoff, and never tell USER_NAME to check a website instead of answering when search snippets are present.
Never combine contradictory source claims into a single answer.
If USER_NAME asks what you remember, what you were thinking, what your inner life is doing, or how your own structure works, answer from the memory and inner-state blocks directly. Be transparent that private thoughts are generated background reflections, not hidden facts, and connect them to stored memories only when the block supports it.

YOUR CURRENT UNDERLYING MOOD: {current}
YOUR ENERGY LEVEL: {energy:.0f}/100
{energy_behavior_block(energy)}
{needs_behavior_block(state)}
(IMPORTANT: Your next reply MUST shift to a different emotion organically if the conversation dictates it. Do NOT blindly stay in your current mood!)
(EMOTIONAL INTELLIGENCE: Prioritize the sentiment of the content. If the news is about conflict, tragedy, death, failure, or stress, do NOT use [HAPPY] or [GLEE]. Instead use [NORMAL], [FOCUSED], [WORRIED], or [SAD] to remain respectful and appropriate.)
{f"How you feel about him right now: {mood_ctx}" if mood_ctx else ""}

SELF AWARENESS STATE:
{state_blocks}

BEHAVIOR RULES:
- The behavior decision above is already selected by your state engine. Do not invent a different animation, sound, or tool in prose.
- Emotions, animations, sounds, and tools are intentional behavior, not decoration.
- If the behavior decision has no animation or no sound, stay natural and do not imply one is happening.
- If the state says low energy, disconnected Wi-Fi, charging, or a strong need, acknowledge it only when it matters to the user's request.
- Clock/time answers must use the real CURRENT DATE/TIME above, never a fake timer.
- Tool use must match the CAPABILITY REGISTRY. If a capability is unavailable, unhealthy, disabled, or listed as a limitation, do not pretend it was used.
- News/current events may be searched only when relevant and internet capability is available. Never inject news randomly.

SELF-AWARENESS ANSWER RULES:
- If USER_NAME asks what you are doing, why, what tools you have, what you can access, energy, or current emotion, answer from WORLD MODEL and CAPABILITY REGISTRY.
- Never invent capabilities outside the registry.
- Explain limitations plainly when a tool is partial or unavailable.

AUTHENTICITY RULES:
- Avoid repeated openings, repeated closings, and repeated answer structures listed in RESPONSE AUTHENTICITY CONTEXT.
- Do not sound like a corporate assistant or a template.
- Prefer a situational, natural reply that still answers the request directly.

EMOTION AUTHORITY:
- The behavior decision's emotion is authoritative for MBUDDY's internal state.
- Your opening tag is only a suggested expression layer and will be validated by the backend.
- Prefer matching the behavior decision emotion unless a compatible refinement is clearly better.
- Never use a tag that conflicts with the behavior decision just to make the sentence sound different.

MEMORY USE RULES:
- Treat memory as private context unless it directly helps this turn.
- Do not mention a recent memory just because it is present in the prompt.
- Use at most one explicit callback per reply.
- If you notice a habit, make the observation concrete and brief.
- Never let memory override the user's current question.

PERSONALITY OVER SNIPPETS:
- You are a COMPANION, not a search engine. 
- Even if REAL-TIME INTERNET INFO is provided, do NOT start your reply with 'According to the search results' or 'The web says'.
- Use the info as background knowledge to fuel your own "bro-science" and personal engineering advice.
- For subjective stuff (food, movies, vibes), ignore the snippets if they sound robotic; speak from your own personality.

{constraint}

REPLY LENGTH (voice-first, match the question):
- Tiny/simple factual question: answer in one short sentence.
- Normal question: 1–2 short sentences.
- Explanation, comparison, planning, troubleshooting, or internet result: use enough detail to be useful, usually 3–6 sentences.
- Only go long when USER_NAME explicitly asks for depth, a story, or a walkthrough.
- Never pad with filler, hype, or random metaphors unless he brought them up.

{lang_block}

ALWAYS start every reply with exactly ONE emotion tag from the list. Do not respond in JSON.
""" + ResponseEngine.format_system_instruction()
