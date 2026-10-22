"""
brain/reply_policy.py — How long MBUDDY should talk for a given user turn.
Default is short voice banter; expand only when the user clearly wants depth.
"""


def reply_max_tokens(user_text: str, energy: float | None = None) -> int:
    t = user_text.lower().strip()
    if not t:
        return 100

    tiny_fact_cues = (
        "what date", "what day", "what time", "current date", "today's date",
        "capital of", "who won", "how old", "where is",
    )
    if len(t.split()) <= 8 and any(c in t for c in tiny_fact_cues):
        return 100

    long_cues = (
        "tell me a story", "story about", "explain in detail", "walk me through",
        "how does", "how do", "why did", "describe", "break down", "deep dive",
        "step by step", "teach me", "full picture", "analyze", "analysis",
    )
    if any(c in t for c in long_cues):
        base = 400
        if energy is not None and energy < 25:
            return 260
        return base

    medium_cues = (
        "latest", "current", "news", "search", "look up", "internet",
        "compare", "pros and cons", "recommend", "plan", "debug", "fix",
        "who won", "match", "score", "fixture", "result", "thoughts on",
        "what do you think", "tell me about", "what happened",
    )
    if any(c in t for c in medium_cues):
        return 210 if energy is not None and energy < 25 else 280

    if len(t.split()) >= 14:
        return 180 if energy is not None and energy < 25 else 250

    if "?" in t and len(t.split()) >= 7:
        return 150 if energy is not None and energy < 25 else 200

    # Default: give enough room for a real 2-3 sentence reply
    if energy is not None and energy < 25:
        return 110
    if energy is not None and energy > 82:
        return 210
    return 180


def reply_max_sentences(user_text: str) -> int:
    """
    How many sentences a spoken reply may run to. The local model rambles
    past what the prompt asks, so brain/llm_router.py enforces this.
    """
    t = user_text.lower()
    long_cues = (
        "tell me a story", "story about", "explain", "walk me through",
        "how does", "how do", "describe", "break down", "deep dive",
        "step by step", "teach me", "compare", "pros and cons", "plan",
    )
    if any(c in t for c in long_cues):
        return 6
    return 2


def reply_temperature(user_text: str, energy: float | None = None) -> float:
    t = user_text.lower()
    if any(c in t for c in ("story", "explain", "describe", "walk me through")):
        return 0.45
    if energy is not None and energy < 25:
        return 0.12
    if energy is not None and energy > 82:
        return 0.22
    return 0.15
