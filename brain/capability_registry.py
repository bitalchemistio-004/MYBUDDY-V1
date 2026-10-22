"""
Unified capability registry for MBUDDY.

Capabilities are the single source for tools, animations, SFX, and hardware
surfaces. Other modules may expose tuples for firmware/UI compatibility, but
those tuples must be derived from this registry.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Capability:
    name: str
    kind: str
    description: str
    purpose: str
    limitations: str
    trigger_conditions: list[str]
    requires: list[str] | None = None
    available: bool = True
    healthy: bool = True
    enabled: bool = True
    cooldown_s: float = 0.0
    last_used: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["cooldown_remaining_s"] = self.cooldown_remaining()
        return data

    def cooldown_remaining(self, now: float | None = None) -> float:
        if self.cooldown_s <= 0 or self.last_used <= 0:
            return 0.0
        current = now if now is not None else time.time()
        return max(0.0, round(self.cooldown_s - (current - self.last_used), 2))

    def usable(self, now: float | None = None) -> bool:
        return self.available and self.healthy and self.enabled and self.cooldown_remaining(now) <= 0


class CapabilityRegistry:
    def __init__(self, capabilities: list[Capability] | None = None):
        self._items = {cap.name: cap for cap in (capabilities or default_capabilities())}

    def get(self, name: str) -> Capability | None:
        return self._items.get(name)

    def names(self, kind: str | None = None, *, usable_only: bool = False) -> list[str]:
        now = time.time()
        values = []
        for cap in self._items.values():
            if kind and cap.kind != kind:
                continue
            if usable_only and not cap.usable(now):
                continue
            values.append(cap.name)
        return values

    def mark_used(self, name: str) -> None:
        cap = self.get(name)
        if cap:
            cap.last_used = time.time()

    def set_health(self, name: str, *, available: bool | None = None, healthy: bool | None = None, enabled: bool | None = None) -> None:
        cap = self.get(name)
        if not cap:
            return
        if available is not None:
            cap.available = available
        if healthy is not None:
            cap.healthy = healthy
        if enabled is not None:
            cap.enabled = enabled

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {name: cap.to_dict() for name, cap in sorted(self._items.items())}

    def disconnected_features(self) -> list[dict[str, str]]:
        disconnected = []
        for cap in self._items.values():
            if cap.kind == "tool" and not cap.available:
                disconnected.append({
                    "name": cap.name,
                    "kind": cap.kind,
                    "reason": "Tool is marked unavailable in the capability registry.",
                })
        return disconnected


def _cap(
    name: str,
    kind: str,
    description: str,
    purpose: str,
    limitations: str,
    triggers: list[str],
    *,
    requires: list[str] | None = None,
    cooldown_s: float = 0.0,
    available: bool = True,
    healthy: bool = True,
) -> Capability:
    return Capability(
        name=name,
        kind=kind,
        description=description,
        purpose=purpose,
        limitations=limitations,
        trigger_conditions=triggers,
        requires=requires or [],
        available=available,
        healthy=healthy,
        cooldown_s=cooldown_s,
    )


def default_capabilities() -> list[Capability]:
    caps = [
        _cap("clock", "tool", "Real runtime clock.", "Provide current date and time.", "Cannot predict future events.", ["time/date questions"]),
        _cap("timer", "tool", "Timer management system.", "Start, list, and cancel countdown timers.", "Timers persist across sessions in JSON storage.", ["timer requests"], available=True, healthy=True),
        _cap("task", "tool", "Task and reminder tracker.", "Create, list, and complete to-do items with optional due dates and priority.", "Tasks persist across sessions in SQLite storage.", ["task/reminder/to-do requests"], available=True, healthy=True),
        _cap("sys_volume", "tool", "Windows system volume control.", "Get, set, mute, or unmute the real OS output volume.", "Controls the PC's system volume, not the ESP32 speaker.", ["volume requests"], available=True, healthy=True),
        _cap("calculator", "tool", "Safe mathematical evaluation.", "Calculate math expressions safely.", "Only supports basic arithmetic and safe operations.", ["math/calculation requests"], available=True, healthy=True),
        _cap("weather", "tool", "Weather lookup capability.", "Retrieve current weather when internet is available.", "Requires internet and a provider.", ["weather questions"], requires=["internet"], available=True, healthy=True),
        _cap("memory", "tool", "Companion memory.", "Recall prior interactions when relevant.", "Memory can be stale or unavailable.", ["memory/recall/learning requests"]),
        _cap("web_search", "tool", "Fresh internet search.", "Retrieve current external facts.", "Requires internet; should not be used for stable facts.", ["latest/current/news/search requests"], requires=["internet"]),
        _cap("wifi", "hardware", "Network link awareness.", "Know if the companion can communicate wirelessly.", "Cannot fix upstream outages alone.", ["connection changes"]),
        _cap("battery", "hardware", "Energy and power awareness.", "Adapt behavior when energy is low or charging.", "Laptop/runtime energy may differ from ESP32 battery.", ["low energy/charging"]),
        _cap("oled", "hardware", "1-bit OLED display.", "Show intentional emotions and animations.", "128x64 monochrome display only.", ["visual behavior"]),
        _cap("speaker", "hardware", "Audio output.", "Speak replies and SFX.", "Can overlap if not gated.", ["speech/sfx"]),
        _cap("microphone", "hardware", "Audio input.", "Capture user speech.", "Noise and echo can corrupt STT.", ["listening"]),
        _cap("esp32", "hardware", "Physical companion device.", "Render visuals and stream audio.", "Requires WiFi or serial connection.", ["hardware connection"]),
        _cap("dashboard", "hardware", "Local control dashboard.", "Inspect and manually test state.", "Unsafe if exposed without auth.", ["manual diagnostics"]),
    ]
    for name in (
        "joystick", "interactive_arena", "dragon_fire", "data_weaver", "pixel_cascade",
        "heartbeat", "thought_bubble", "block_break", "starlight", "clock_timer",
        "wifi_search", "battery_charge", "error_glitch", "scanning_targeting",
        "dreaming", "celebration", "listening_animation",
    ):
        caps.append(_cap(
            name,
            "animation",
            f"OLED animation: {name}.",
            "Express a specific companion behavior visually.",
            "Only meaningful when selected by state or user intent.",
            [name.replace("_", " ")],
            requires=["oled"],
            cooldown_s=1.5,
        ))
    for name in (
        "chiptune_loop", "laser_pew", "jump_sweep", "dragon_breath", "data_drone",
        "data_blip", "cascade_arp", "ecg_beep", "bubble_pop", "bounce_click",
        "paddle_bounce", "brick_hit", "star_tinkle", "clock_tick", "sonar_ping",
        "charge_hum", "glitch_screech", "sleep_dream", "success_chime", "listen_ping",
    ):
        caps.append(_cap(
            name,
            "sfx",
            f"Generated sound effect: {name}.",
            "Reinforce an intentional behavior with sound.",
            "Should stay silent when sound would distract or conflict.",
            [name.replace("_", " ")],
            requires=["speaker"],
            cooldown_s=0.5,
        ))
    return caps


def build_capability_registry(*, internet_available: bool = True, hardware_connected: bool = False, auto_visuals: bool = False) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.set_health("web_search", available=internet_available, healthy=internet_available)
    registry.set_health("weather", available=internet_available, healthy=internet_available)
    registry.set_health("timer", available=True, healthy=True)
    # Arithmetic is pure Python and has nothing to do with the display being
    # attached. Tying it to the hardware made MBUDDY refuse to do maths whenever
    # the ESP32 was unplugged.
    registry.set_health("calculator", available=True, healthy=True)
    for name in ("esp32", "oled", "speaker", "microphone", "wifi"):
        registry.set_health(name, available=hardware_connected, healthy=hardware_connected)
    for name in registry.names("animation"):
        registry.set_health(name, available=hardware_connected, healthy=hardware_connected, enabled=auto_visuals)
    return registry


DEFAULT_REGISTRY = CapabilityRegistry()
TOOL_NAMES = tuple(DEFAULT_REGISTRY.names("tool"))
ANIMATION_NAMES = tuple(DEFAULT_REGISTRY.names("animation"))
SFX_NAMES = tuple(DEFAULT_REGISTRY.names("sfx"))
