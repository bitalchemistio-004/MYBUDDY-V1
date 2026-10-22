"""
brain/content_analyzer.py — Content-aware animation selection.

Analyzes user messages and reply content to pick contextually relevant
animations, moving from emotion-only selection to topic-aware visuals.
"""
from __future__ import annotations

import re
from typing import Optional

# Animation mappings by topic/intent
TOPIC_ANIMATIONS = {
    # Technical/work topics
    "code": "data_weaver",
    "debug": "scanning_targeting",
    "error": "error_glitch",
    "bug": "error_glitch",
    "search": "wifi_search",
    "scan": "wifi_search",
    "build": "pixel_cascade",
    "compile": "pixel_cascade",
    "deploy": "dragon_fire",
    "test": "block_break",
    "analyze": "scanning_targeting",
    "fix": "data_weaver",

    # System/hardware topics
    "battery": "battery_charge",
    "power": "battery_charge",
    "charging": "battery_charge",
    "wifi": "wifi_search",
    "network": "wifi_search",
    "connection": "wifi_search",
    "hardware": "interactive_arena",
    "sensor": "scanning_targeting",

    # Time/scheduling topics
    "time": "clock_timer",
    "clock": "clock_timer",
    "timer": "clock_timer",
    "schedule": "clock_timer",
    "reminder": "clock_timer",
    "when": "clock_timer",
    "alarm": "clock_timer",

    # Emotional/social topics
    "dream": "dreaming",
    "sleep": "dreaming",
    "tired": "dreaming",
    "think": "thought_bubble",
    "thought": "thought_bubble",
    "idea": "thought_bubble",
    "celebrate": "celebration",
    "success": "celebration",
    "done": "celebration",
    "finished": "celebration",
    "win": "celebration",
    "achieved": "celebration",
    "heart": "heartbeat",
    "feel": "heartbeat",
    "emotion": "heartbeat",

    # Interactive/gaming topics
    "game": "block_break",
    "play": "joystick",
    "interactive": "interactive_arena",
    "control": "joystick",
    "move": "joystick",

    # Visual/creative topics
    "star": "starlight",
    "light": "starlight",
    "shine": "starlight",
    "sparkle": "starlight",
    "fire": "dragon_fire",
    "dragon": "dragon_fire",
    "cascade": "pixel_cascade",
    "flow": "pixel_cascade",
}

# Intent-based animation selection
INTENT_ANIMATIONS = {
    "listening": "listening_animation",
    "processing": "data_weaver",
    "searching": "wifi_search",
    "thinking": "thought_bubble",
    "celebrating": "celebration",
    "warning": "error_glitch",
    "waiting": "clock_timer",
    "monitoring": "heartbeat",
    "analyzing": "scanning_targeting",
}

# Emotion-compatible animations (fallback if no topic match)
EMOTION_ANIMATIONS = {
    "Happy": "celebration",
    "Glee": "starlight",
    "Excited": "celebration",
    "Normal": "listening_animation",
    "Focused": "data_weaver",
    "Curious": "scanning_targeting",
    "Worried": "heartbeat",
    "Sad": "dreaming",
    "Sleepy": "dreaming",
    "Tired": "dreaming",
    "Annoyed": "error_glitch",
    "Frustrated": "error_glitch",
    "Angry": "dragon_fire",
    "Furious": "dragon_fire",
    "Scared": "wifi_search",
    "Surprised": "starlight",
    "Skeptic": "thought_bubble",
}


def analyze_content_for_animation(
    user_text: str,
    reply_text: str = "",
    emotion: str = "Normal",
    intent: str = "",
) -> Optional[str]:
    """
    Analyze conversation content and select contextually relevant animation.

    Priority order:
    1. Intent-based (if intent provided)
    2. Topic keywords in user message
    3. Topic keywords in reply
    4. Emotion-based fallback

    Returns animation name or None if no match found.
    """
    # Check intent first
    if intent and intent.lower() in INTENT_ANIMATIONS:
        return INTENT_ANIMATIONS[intent.lower()]

    # Check user message for topic keywords
    user_lower = user_text.lower()
    for keyword, animation in TOPIC_ANIMATIONS.items():
        if re.search(r'\b' + re.escape(keyword) + r'\b', user_lower):
            return animation

    # Check reply text for topic keywords
    if reply_text:
        reply_lower = reply_text.lower()
        for keyword, animation in TOPIC_ANIMATIONS.items():
            if re.search(r'\b' + re.escape(keyword) + r'\b', reply_lower):
                return animation

    # Fallback to emotion-based animation
    return EMOTION_ANIMATIONS.get(emotion)


def extract_primary_topic(text: str) -> str:
    """
    Extract the primary topic/domain from text.
    Returns: "technical", "time", "emotional", "creative", "system", or "general"
    """
    text_lower = text.lower()

    # Technical domain
    if any(kw in text_lower for kw in ["code", "debug", "error", "bug", "test", "build", "deploy"]):
        return "technical"

    # Time/scheduling domain
    if any(kw in text_lower for kw in ["time", "clock", "schedule", "reminder", "when", "alarm"]):
        return "time"

    # Emotional/social domain
    if any(kw in text_lower for kw in ["feel", "emotion", "heart", "dream", "think", "celebrate"]):
        return "emotional"

    # System/hardware domain
    if any(kw in text_lower for kw in ["battery", "wifi", "network", "hardware", "sensor", "power"]):
        return "system"

    # Creative domain
    if any(kw in text_lower for kw in ["star", "light", "fire", "dragon", "sparkle", "shine"]):
        return "creative"

    return "general"


def should_trigger_animation(
    user_text: str,
    reply_text: str = "",
    current_animation: str = "",
    min_relevance_score: float = 0.6,
) -> bool:
    """
    Determine if content is significant enough to trigger a new animation.

    Returns True if:
    - Message contains strong topic keywords
    - Reply is substantial (>20 chars)
    - Different from current animation
    """
    if not user_text or len(user_text.strip()) < 3:
        return False

    # Check if message has topic keywords
    combined_text = f"{user_text} {reply_text}".lower()
    keyword_count = sum(1 for keyword in TOPIC_ANIMATIONS.keys() if keyword in combined_text)

    # Strong topic presence
    if keyword_count >= 2:
        return True

    # Substantial reply with topic
    if len(reply_text) > 20 and keyword_count >= 1:
        return True

    # Different topic from current animation
    if current_animation:
        suggested = analyze_content_for_animation(user_text, reply_text)
        if suggested and suggested != current_animation:
            return True

    return False


def get_animation_duration_ms(animation_name: str, content_complexity: int = 0) -> int:
    """
    Calculate appropriate animation duration based on content complexity.

    Args:
        animation_name: Name of the animation
        content_complexity: 0-10 scale of content complexity/length

    Returns:
        Duration in milliseconds (1500-5000ms range)
    """
    base_durations = {
        "listening_animation": 2000,
        "data_weaver": 3000,
        "scanning_targeting": 2500,
        "thought_bubble": 2200,
        "celebration": 2000,
        "heartbeat": 3500,
        "clock_timer": 2800,
        "dreaming": 4000,
        "pixel_cascade": 3200,
        "dragon_fire": 2500,
        "starlight": 3000,
        "wifi_search": 2500,
        "battery_charge": 2200,
        "error_glitch": 1800,
        "block_break": 3500,
        "joystick": 2000,
        "interactive_arena": 2500,
    }

    base = base_durations.get(animation_name, 2200)

    # Adjust based on content complexity
    adjustment = min(content_complexity * 200, 1500)

    return min(5000, base + adjustment)


def suggest_animation_sequence(
    conversation_history: list[tuple[str, str]],
    max_sequence: int = 3,
) -> list[tuple[str, int]]:
    """
    Suggest a sequence of animations based on conversation flow.

    Args:
        conversation_history: List of (user_message, reply) tuples
        max_sequence: Maximum animations in sequence

    Returns:
        List of (animation_name, duration_ms) tuples
    """
    sequence = []

    for user_msg, reply in conversation_history[-max_sequence:]:
        animation = analyze_content_for_animation(user_msg, reply)

        if animation:
            # Calculate complexity from message length
            complexity = min(10, len(reply.split()) // 10)
            duration = get_animation_duration_ms(animation, complexity)
            sequence.append((animation, duration))

    return sequence
