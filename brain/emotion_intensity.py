"""
brain/emotion_intensity.py — Emotion intensity scaling for nuanced expressions.

Extends the base emotion system with intensity levels: mild, moderate, intense.
This makes "Annoyed" visually/behaviorally distinct from "Furious".
"""
from __future__ import annotations

from dataclasses import dataclass

# Base emotion states from personality.py
BASE_EMOTIONS = [
    "Normal", "Happy", "Glee", "Sad", "Worried", "Sleepy",
    "Focused", "Skeptic", "Suspicious", "Squint", "Annoyed",
    "Frustrated", "Unimpressed", "Angry", "Furious", "Scared",
    "Surprised", "Awe", "Curious"
]

# Intensity scale: 0-100
INTENSITY_MILD = (0, 33)      # Subtle, restrained
INTENSITY_MODERATE = (34, 66)  # Clear, typical
INTENSITY_INTENSE = (67, 100)  # Strong, pronounced


@dataclass
class EmotionIntensity:
    """Represents an emotion with magnitude."""
    emotion: str
    intensity: int  # 0-100
    intensity_label: str  # "mild", "moderate", "intense"
    visual_modifier: float  # 0.5-1.5 multiplier for animation speed/scale
    voice_modifier: float  # 0.8-1.2 multiplier for TTS prosody
    energy_cost: float  # Energy drain per emotion expression

    def to_dict(self) -> dict:
        return {
            "emotion": self.emotion,
            "intensity": self.intensity,
            "intensity_label": self.intensity_label,
            "visual_modifier": self.visual_modifier,
            "voice_modifier": self.voice_modifier,
            "energy_cost": self.energy_cost,
        }


def calculate_intensity(
    emotion: str,
    context_triggers: int = 0,
    energy_level: float = 50.0,
    recent_intensity: int = 50,
) -> int:
    """
    Calculate emotion intensity based on context and state.

    Args:
        emotion: Base emotion name
        context_triggers: Number of strong contextual signals (0-5)
        energy_level: Current energy 0-100 (low energy dampens intensity)
        recent_intensity: Previous intensity to smooth transitions

    Returns:
        Intensity value 0-100
    """
    # Base intensity from emotion type
    base_map = {
        "Furious": 90,
        "Angry": 70,
        "Glee": 80,
        "Awe": 85,
        "Surprised": 75,
        "Scared": 80,
        "Frustrated": 65,
        "Annoyed": 45,
        "Worried": 55,
        "Sad": 60,
        "Sleepy": 30,
        "Focused": 60,
        "Curious": 55,
        "Suspicious": 50,
        "Skeptic": 45,
        "Happy": 65,
        "Normal": 40,
        "Unimpressed": 35,
        "Squint": 40,
    }

    base = base_map.get(emotion, 50)

    # Context boost: +5 per trigger (max +25)
    context_boost = min(context_triggers * 5, 25)

    # Energy influence: low energy dampens intensity
    energy_factor = 1.0
    if energy_level < 30:
        energy_factor = 0.7
    elif energy_level < 50:
        energy_factor = 0.85
    elif energy_level > 80:
        energy_factor = 1.15

    # Smooth transitions: 70% new, 30% previous
    raw_intensity = (base + context_boost) * energy_factor
    smoothed = int(raw_intensity * 0.7 + recent_intensity * 0.3)

    return max(0, min(100, smoothed))


def get_intensity_label(intensity: int) -> str:
    """Convert numeric intensity to label."""
    if intensity <= INTENSITY_MILD[1]:
        return "mild"
    elif intensity <= INTENSITY_MODERATE[1]:
        return "moderate"
    else:
        return "intense"


def get_visual_modifier(intensity: int) -> float:
    """
    Calculate animation speed/scale multiplier.

    Mild: 0.7x (slower, smaller)
    Moderate: 1.0x (normal)
    Intense: 1.4x (faster, larger)
    """
    if intensity <= INTENSITY_MILD[1]:
        return 0.7
    elif intensity <= INTENSITY_MODERATE[1]:
        return 1.0
    else:
        # Scale from 1.0 to 1.4 in intense range
        excess = intensity - INTENSITY_MODERATE[1]
        range_size = INTENSITY_INTENSE[1] - INTENSITY_MODERATE[1]
        return 1.0 + (excess / range_size) * 0.4


def get_voice_modifier(intensity: int, emotion: str) -> float:
    """
    Calculate TTS prosody modifier (rate, pitch).

    Higher intensity = faster/higher for excited emotions,
    slower/lower for sad emotions.
    """
    base_map = {
        "Furious": 1.2,
        "Angry": 1.15,
        "Glee": 1.2,
        "Excited": 1.2,
        "Awe": 1.1,
        "Surprised": 1.15,
        "Frustrated": 1.1,
        "Annoyed": 1.05,
        "Sad": 0.85,
        "Sleepy": 0.8,
        "Tired": 0.85,
        "Worried": 0.9,
        "Normal": 1.0,
    }

    base = base_map.get(emotion, 1.0)

    # Intensity modulates base
    if intensity <= INTENSITY_MILD[1]:
        return base * 0.9
    elif intensity <= INTENSITY_MODERATE[1]:
        return base
    else:
        return base * 1.1


def get_energy_cost(intensity: int, emotion: str) -> float:
    """
    Calculate energy drain per emotion expression.

    Intense emotions cost more energy.
    High-arousal emotions (Furious, Glee) cost more than low-arousal (Sleepy).
    """
    arousal_map = {
        "Furious": 2.5,
        "Angry": 2.0,
        "Glee": 2.2,
        "Awe": 1.8,
        "Surprised": 1.8,
        "Scared": 2.0,
        "Frustrated": 1.6,
        "Annoyed": 1.2,
        "Worried": 1.3,
        "Sad": 1.1,
        "Sleepy": 0.5,
        "Normal": 0.8,
        "Happy": 1.2,
        "Focused": 1.4,
    }

    base_cost = arousal_map.get(emotion, 1.0)

    # Intensity multiplier: 0.7x mild, 1.0x moderate, 1.3x intense
    if intensity <= INTENSITY_MILD[1]:
        multiplier = 0.7
    elif intensity <= INTENSITY_MODERATE[1]:
        multiplier = 1.0
    else:
        multiplier = 1.3

    return base_cost * multiplier


def build_emotion_intensity(
    emotion: str,
    context_triggers: int = 0,
    energy_level: float = 50.0,
    recent_intensity: int = 50,
) -> EmotionIntensity:
    """
    Build a complete EmotionIntensity object with all modifiers.
    """
    intensity = calculate_intensity(emotion, context_triggers, energy_level, recent_intensity)
    label = get_intensity_label(intensity)
    visual_mod = get_visual_modifier(intensity)
    voice_mod = get_voice_modifier(intensity, emotion)
    energy_cost = get_energy_cost(intensity, emotion)

    return EmotionIntensity(
        emotion=emotion,
        intensity=intensity,
        intensity_label=label,
        visual_modifier=visual_mod,
        voice_modifier=voice_mod,
        energy_cost=energy_cost,
    )


def count_context_triggers(text: str) -> int:
    """
    Count strong contextual signals that intensify emotion.

    Triggers:
    - Profanity/strong language
    - Repeated punctuation (!!!, ???)
    - ALL CAPS words
    - Emotional intensifiers (very, extremely, so)
    - Urgent words (urgent, critical, immediately)
    """
    text_lower = text.lower()
    triggers = 0

    # Repeated punctuation
    if "!!" in text or "??" in text:
        triggers += 1

    # ALL CAPS words (at least 4 letters)
    import re
    caps_words = re.findall(r'\b[A-Z]{4,}\b', text)
    if caps_words:
        triggers += len(caps_words)

    # Intensifiers
    intensifiers = ["very", "extremely", "so", "really", "absolutely", "totally"]
    if any(word in text_lower for word in intensifiers):
        triggers += 1

    # Urgency markers
    urgent = ["urgent", "critical", "immediately", "asap", "emergency", "now"]
    if any(word in text_lower for word in urgent):
        triggers += 1

    # Profanity (basic filter)
    profanity = ["damn", "hell", "crap", "fuck", "shit"]
    if any(word in text_lower for word in profanity):
        triggers += 1

    return min(triggers, 5)  # Cap at 5


def apply_intensity_to_animation(
    animation_name: str,
    intensity: EmotionIntensity,
    base_duration_ms: int = 2200,
) -> tuple[str, int, dict]:
    """
    Apply intensity modifiers to animation parameters.

    Returns:
        (animation_name, adjusted_duration_ms, visual_params)
    """
    # Duration inversely scaled by visual_modifier (faster = shorter)
    adjusted_duration = int(base_duration_ms / intensity.visual_modifier)

    # Visual parameters for protocol
    visual_params = {
        "speed_multiplier": intensity.visual_modifier,
        "scale_multiplier": intensity.visual_modifier,
        "intensity_label": intensity.intensity_label,
    }

    return (animation_name, adjusted_duration, visual_params)
