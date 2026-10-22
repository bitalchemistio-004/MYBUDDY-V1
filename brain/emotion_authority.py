"""
brain/emotion_authority.py - authoritative emotion validation.

The cognitive engine owns MBUDDY's internal emotional state. LLM emotion tags
are treated as suggestions that may confirm or refine the cognitive decision,
but they cannot overwrite a valid cognitive emotion with an incompatible state.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from brain.personality import STATE_TO_TAG


UNKNOWN_EMOTIONS = {"", "none", "unknown", "null"}

EMOTION_ALIASES = {
    "analytical": "Focused",
    "attentive": "Focused",
    "curious": "Focused",
    "listening": "Focused",
    "concerned": "Worried",
    "battery critical": "Worried",
    "tired": "Sleepy",
    "exhausted": "Sleepy",
    "playful": "Glee",
    "excited": "Glee",
    "relieved": "Happy",
}

# Focused and Normal are the cognitive engine's default for nearly every
# turn, so their sets decide how much feeling reaches the voice. Gentle,
# conversational emotions are allowed ("Oh yeah! Lina!" can sound happy, "my
# head hurts" can sound worried); big swings (Sleepy, Angry, Scared) are not.
# Harness 2026-09-26: 18 of 22 replies were voiced Focused.
COMPATIBILITY_MATRIX = {
    "Focused": {"Focused", "Curious", "Analytical", "Attentive", "Listening", "Happy", "Worried", "Normal"},
    "Worried": {"Worried", "Concerned", "Sad", "Scared", "Battery Critical"},
    "Sleepy": {"Sleepy", "Tired", "Exhausted"},
    "Happy": {"Happy", "Relieved", "Normal"},
    "Glee": {"Glee", "Excited", "Playful", "Happy"},
    "Normal": {"Normal", "Focused", "Happy", "Skeptic", "Worried", "Sad", "Glee", "Surprised"},
    "Skeptic": {"Skeptic", "Focused", "Suspicious", "Squint"},
    "Suspicious": {"Suspicious", "Skeptic", "Squint", "Focused"},
    "Squint": {"Squint", "Suspicious", "Skeptic"},
    "Sad": {"Sad", "Worried", "Sleepy"},
    "Scared": {"Scared", "Worried"},
    "Annoyed": {"Annoyed", "Frustrated", "Unimpressed"},
    "Frustrated": {"Frustrated", "Annoyed", "Unimpressed"},
    "Unimpressed": {"Unimpressed", "Annoyed", "Skeptic"},
    "Angry": {"Angry", "Furious", "Annoyed"},
    "Furious": {"Furious", "Angry"},
    "Surprised": {"Surprised", "Awe", "Scared"},
    "Awe": {"Awe", "Surprised", "Happy"},
}


@dataclass(frozen=True)
class EmotionDecision:
    cognitive_emotion: str
    llm_suggested_emotion: str
    final_emotion: str
    authority_source: str
    accepted: bool
    rejection_reason: str = ""
    reason: str = ""
    compatible: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_emotion(emotion: str | None) -> str:
    """Return a firmware-supported emotion state, or an empty string."""
    raw = str(emotion or "").strip()
    if raw.lower() in UNKNOWN_EMOTIONS:
        return ""
    for state in STATE_TO_TAG:
        if raw.lower() == state.lower():
            return state
    return EMOTION_ALIASES.get(raw.lower(), "")


def _is_compatible(cognitive_emotion: str, llm_suggested_emotion: str) -> bool:
    if not cognitive_emotion or not llm_suggested_emotion:
        return False
    allowed = COMPATIBILITY_MATRIX.get(cognitive_emotion, {cognitive_emotion})
    normalized_allowed = {normalize_emotion(item) or item for item in allowed}
    raw_allowed = {item.lower() for item in allowed}
    return (
        llm_suggested_emotion in normalized_allowed
        or llm_suggested_emotion.lower() in raw_allowed
    )


def validate_emotion_decision(
    cognitive_emotion: str | None,
    llm_suggested_emotion: str | None,
    *,
    cognitive_confidence: float = 1.0,
    confidence_threshold: float = 0.5,
) -> EmotionDecision:
    """
    Validate the LLM emotion suggestion against cognitive authority.

    A valid cognitive emotion remains authoritative. The LLM can only take over
    if cognition has no emotion or confidence is below the supplied threshold.
    """
    cognitive_raw = str(cognitive_emotion or "").strip()
    llm_raw = str(llm_suggested_emotion or "").strip()
    cognitive_final = normalize_emotion(cognitive_raw)
    llm_final = normalize_emotion(llm_raw)

    cognition_missing = not cognitive_final or cognitive_raw.lower() in UNKNOWN_EMOTIONS
    cognition_weak = cognitive_confidence < confidence_threshold

    if cognition_missing or cognition_weak:
        final = llm_final or "Normal"
        return EmotionDecision(
            cognitive_emotion=cognitive_raw or "UNKNOWN",
            llm_suggested_emotion=llm_raw or "",
            final_emotion=final,
            authority_source="llm_suggestion" if llm_final else "fallback",
            accepted=bool(llm_final),
            reason=(
                "LLM emotion accepted because cognitive emotion is unknown or below confidence threshold."
                if llm_final
                else "Fallback emotion used because neither cognition nor LLM provided a valid emotion."
            ),
            compatible=bool(llm_final),
        )

    if not llm_final:
        return EmotionDecision(
            cognitive_emotion=cognitive_final,
            llm_suggested_emotion=llm_raw,
            final_emotion=cognitive_final,
            authority_source="cognitive_engine",
            accepted=True,
            reason="No valid LLM emotion suggestion was provided; cognitive emotion remains authoritative.",
            compatible=True,
        )

    if llm_final == cognitive_final:
        return EmotionDecision(
            cognitive_emotion=cognitive_final,
            llm_suggested_emotion=llm_raw or llm_final,
            final_emotion=cognitive_final,
            authority_source="cognitive_engine",
            accepted=True,
            reason="LLM emotion confirmed the authoritative cognitive emotion.",
            compatible=True,
        )

    if _is_compatible(cognitive_final, llm_raw or llm_final):
        return EmotionDecision(
            cognitive_emotion=cognitive_final,
            llm_suggested_emotion=llm_raw or llm_final,
            final_emotion=llm_final,
            authority_source="cognitive_engine",
            accepted=True,
            reason="LLM emotion accepted as a compatible refinement of the cognitive state.",
            compatible=True,
        )

    return EmotionDecision(
        cognitive_emotion=cognitive_final,
        llm_suggested_emotion=llm_raw or llm_final,
        final_emotion=cognitive_final,
        authority_source="cognitive_engine",
        accepted=False,
        rejection_reason="Cognitive emotion is authoritative.",
        reason="LLM emotion conflicts with authoritative cognitive state.",
        compatible=False,
    )
