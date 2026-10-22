"""Companion behavior selection for quiet inner-life moments."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompanionCue:
    action: str = ""
    action_ms: int = 0
    sfx: str = ""
    speak_chance: float = 0.0
    quiet: bool = False


def choose_inner_life_cue(state, memory: dict, rng=None) -> CompanionCue:
    """Pick one optional visible/audio cue for an idle inner-life tick.

    The selection is deterministic: state, energy, and needs decide the cue.
    ``rng`` remains accepted for backward-compatible tests/callers.
    """
    mood = getattr(state, "current", "Normal")
    energy = float(getattr(state, "energy", 50.0))
    dominant_need = getattr(state, "dominant_need", lambda: "")()
    recent_inner = memory.get("inner_log", []) if isinstance(memory, dict) else []

    if dominant_need == "rest" and energy < 45:
        return CompanionCue(action="dreaming", action_ms=2800, sfx="sleep_dream", speak_chance=0.05)
    if dominant_need == "connection":
        return CompanionCue(action="listening_animation", action_ms=1800, sfx="", speak_chance=0.18)
    if dominant_need == "novelty":
        return CompanionCue(action="block_break", action_ms=2400, sfx="", speak_chance=0.12)
    if dominant_need == "competence":
        return CompanionCue(action="scanning_targeting", action_ms=1800, sfx="data_blip", speak_chance=0.12)

    if energy < 18:
        return CompanionCue(action="battery_charge", action_ms=2600, sfx="charge_hum", speak_chance=0.04)

    if mood in {"Sleepy"}:
        return CompanionCue(action="dreaming", action_ms=3200, sfx="sleep_dream", speak_chance=0.06)

    if mood in {"Sad", "Scared"} or energy < 30:
        return CompanionCue(action="heartbeat", action_ms=2200, sfx="ecg_beep", speak_chance=0.08)

    if mood in {"Unimpressed", "Skeptic", "Suspicious"}:
        return CompanionCue(action="joystick", action_ms=3000, sfx="chiptune_loop", speak_chance=0.12)

    if mood in {"Annoyed", "Frustrated", "Angry", "Furious", "Squint"}:
        return CompanionCue(action="error_glitch", action_ms=1300, sfx="glitch_screech", speak_chance=0.10)

    if mood in {"Focused", "Normal"}:
        action = "data_weaver" if mood == "Focused" else "starlight"
        sfx = "data_blip" if action == "data_weaver" else ""
        return CompanionCue(action=action, action_ms=1800 if action else 0, sfx=sfx, speak_chance=0.16)

    if mood in {"Happy", "Glee", "Awe", "Surprised"} or energy > 82:
        return CompanionCue(action="celebration", action_ms=1800, sfx="success_chime", speak_chance=0.24)

    if len(recent_inner) >= 6:
        return CompanionCue(quiet=True)
    return CompanionCue(action="starlight", action_ms=1800, sfx="", speak_chance=0.14)
