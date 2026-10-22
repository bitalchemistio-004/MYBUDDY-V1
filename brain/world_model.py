"""Persistent runtime world model for MBUDDY's cognitive pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from brain.capability_registry import build_capability_registry
from core.config import MBUDDY_ENABLE_WEB_SEARCH
from core.time_manager import get_time_manager


@dataclass(frozen=True)
class WorldModel:
    current_time: str
    current_date: str
    day_of_week: str
    timezone: str
    hour: int
    day_period: str
    internet_available: bool
    wifi_connected: bool
    battery_level: float
    charging: bool
    current_emotion: str
    emotional_reason: str
    energy_level: float
    active_goal: str
    active_intent: str
    current_action: str
    available_tools: list[str] = field(default_factory=list)
    available_animations: list[str] = field(default_factory=list)
    available_sfx: list[str] = field(default_factory=list)
    memory_health: dict[str, Any] = field(default_factory=dict)
    hardware_connected: bool = False
    last_user_interaction: str = ""
    session_duration: float = 0.0
    needs: dict[str, Any] = field(default_factory=dict)
    dominant_need: str = ""
    capability_registry: dict[str, Any] = field(default_factory=dict)
    disconnected_features: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_world_model(state, *, session=None, runtime=None, memory: dict | None = None, event: str = "", text: str = "") -> WorldModel:
    tm = get_time_manager()
    now = tm.now()
    # Ask whether the device is actually alive, not merely whether a
    # connection was once recorded — a stale flag kept the cognitive
    # layer believing a disconnected device was still present.
    if runtime is not None and hasattr(runtime, "device_is_live"):
        hardware_connected = bool(runtime.device_is_live())
    else:
        hardware_connected = bool(getattr(runtime, "device_connected", False)) if runtime is not None else False
    session_connected = session is not None and not bool(getattr(getattr(session, "ws", None), "closed", True))
    wifi_connected = hardware_connected or session_connected
    internet_available = bool(MBUDDY_ENABLE_WEB_SEARCH)
    needs = dict(getattr(state, "needs", {}) or {})
    dominant_need = ""
    if hasattr(state, "dominant_need"):
        try:
            dominant_need = state.dominant_need()
        except Exception:
            dominant_need = ""
    current_emotion = getattr(state, "current", getattr(state, "emotion", "Normal"))
    energy = float(getattr(state, "energy", 50.0))
    if energy < 20:
        emotional_reason = "Energy is low, so the emotional baseline is tired/concerned."
    elif event == "listening":
        emotional_reason = "User audio is arriving, so attention is the emotional baseline."
    elif event in {"thinking", "conversation"} and len(text.split()) >= 5:
        emotional_reason = "The user request requires reasoning."
    else:
        emotional_reason = "No stronger state trigger is active."

    registry = build_capability_registry(
        internet_available=internet_available,
        hardware_connected=wifi_connected,
        auto_visuals=bool(getattr(session, "auto_visuals_enabled", False)) if session is not None else bool(getattr(runtime, "auto_visuals", False)),
    )
    memory = memory or {}
    memory_health = {
        "conversation_count": len(memory.get("conversation_log", [])),
        "inner_count": len(memory.get("inner_log", [])),
        "facts_count": len(memory.get("facts", [])) + len(memory.get("core_facts", [])),
        "has_identity_snapshot": bool(memory.get("identity_snapshot")),
    }
    started_at = float(getattr(runtime, "started_at", now.timestamp())) if runtime is not None else now.timestamp()
    return WorldModel(
        current_time=now.strftime("%H:%M:%S"),
        current_date=now.strftime("%Y-%m-%d"),
        day_of_week=now.strftime("%A"),
        timezone=tm.get_timezone_name(),
        hour=now.hour,
        day_period=tm.get_day_period(now),
        internet_available=internet_available,
        wifi_connected=wifi_connected,
        battery_level=energy,
        charging=bool(getattr(runtime, "charging", False)) if runtime is not None else False,
        current_emotion=current_emotion,
        emotional_reason=emotional_reason,
        energy_level=energy,
        active_goal="",
        active_intent="",
        current_action=str(getattr(runtime, "current_action", "")) if runtime is not None else "",
        available_tools=registry.names("tool", usable_only=True),
        available_animations=registry.names("animation", usable_only=True),
        available_sfx=registry.names("sfx", usable_only=True),
        memory_health=memory_health,
        hardware_connected=wifi_connected,
        last_user_interaction=str(getattr(runtime, "last_heard", "")) if runtime is not None else "",
        session_duration=max(0.0, round(now.timestamp() - started_at, 2)),
        needs=needs,
        dominant_need=dominant_need,
        capability_registry=registry.snapshot(),
        disconnected_features=registry.disconnected_features(),
    )
