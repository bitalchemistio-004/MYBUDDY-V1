"""
core/status.py - Runtime status snapshot for dashboards and diagnostics.

The ESP32 protocol stays tag-based. This module exposes backend state for
HTTP clients such as a Windows dashboard without changing device messages.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict, fields
from enum import Enum
from typing import Any

VALID_PRIORITIES = {"critical", "high", "normal", "low"}


class DeviceConnState(str, Enum):
    """
    Authoritative connection lifecycle for the ESP32 device link.

    REGISTERED  — process has booted; no device has connected yet this run.
    ONLINE      — connected and heard from recently.
    DEGRADED    — still connected (socket open) but quieter than expected;
                  a soft warning, not yet treated as gone.
    RECONNECTING — the socket closed; the firmware auto-reconnects within
                  ~10 s (MBUDDY.ino's WS_RECONNECT_MS), so this is not
                  immediately OFFLINE.
    OFFLINE     — silent or disconnected past the full timeout with no
                  recovery.

    Phase 1A note: device_id/session_id/firmware_version/capabilities are
    tracked here but stay empty until Phase 1B adds the HELLO handshake that
    actually reports them — there is no other source for that data yet.
    """

    REGISTERED = "registered"
    ONLINE = "online"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    OFFLINE = "offline"


def _notify_broadcast() -> None:
    """Lazily import the broadcaster to avoid a circular import at module load."""
    try:
        from core.broadcast import broadcaster
        broadcaster.mark_dirty()
    except Exception:
        pass


@dataclass
class RuntimeStatus:
    started_at: float = field(default_factory=time.time)
    # Authoritative connection state (Phase 1A). device_connected below is a
    # derived read-only property, not a stored field — see its definition.
    device_conn_state: str = DeviceConnState.REGISTERED.value
    device_id: str = ""              # populated once Phase 1B's HELLO exists
    device_session_id: str = ""      # populated once Phase 1B's HELLO exists
    device_firmware_version: str = ""  # populated once Phase 1B's HELLO exists
    device_capabilities: dict[str, Any] = field(default_factory=dict)
    device_connected_at: float | None = None     # start of the current/most recent connection
    device_disconnected_at: float | None = None  # when that connection was last observed to end
    mode: str = "starting"
    mode_reason: str = "boot"
    remote: str | None = None
    awake: bool = False
    # A typed chat is going on until this time (epoch s): the buddy looks awake while it does, though the voice stays asleep.
    chat_until: float = 0.0
    emotion: str = "Normal"
    energy: float = 0.0
    listening: bool = False
    speaking: bool = False
    thinking: bool = False
    barge_armed: bool = False
    last_heard: str = ""
    last_reply: str = ""
    # The inner life's latest thought, shown as a quiet bubble on the buddy: {"text", "at"}.
    inner_thought: dict = field(default_factory=dict)
    # A PC action waiting for the user's yes (brain/pc_windows.py): {"text", "expires"}.
    pending_action: dict = field(default_factory=dict)
    last_error: str | None = None
    last_stt_ok: bool = False
    last_llm_ok: bool = False
    last_tts_ok: bool = False
    last_rms: float = 0.0
    last_ws_seen_at: float = 0.0
    memory_count: int = 0
    inner_count: int = 0
    current_action: str = ""
    current_action_thought: str = ""
    last_sfx: str = ""
    needs: dict[str, Any] = field(default_factory=dict)
    auto_visuals: bool = False
    content_awareness_enabled: bool = False
    world_model: dict[str, Any] = field(default_factory=dict)
    capability_registry: dict[str, Any] = field(default_factory=dict)
    disconnected_features: list[dict[str, Any]] = field(default_factory=list)
    cognitive_state: dict[str, Any] = field(default_factory=dict)
    behavior_decision: dict[str, Any] = field(default_factory=dict)
    emotion_decision: dict[str, Any] = field(default_factory=dict)
    # The real 0-100 intensity `brain/emotion_intensity.py` computes on every
    # turn. `websocket_server.py` has called `runtime_status.update(
    # emotion_intensity=...)` since that module existed, but this field was
    # never declared — `update()` only sets attributes that already exist, so
    # every one of those calls silently did nothing and `/status` never
    # carried the value at all.
    emotion_intensity: dict[str, Any] = field(default_factory=dict)
    emotion_disagreements: list[dict[str, Any]] = field(default_factory=list)
    behavior_traces: list[dict[str, Any]] = field(default_factory=list)
    recent_actions: list[str] = field(default_factory=list)
    recent_emotions: list[str] = field(default_factory=list)
    recent_sfx: list[str] = field(default_factory=list)
    recent_animations: list[str] = field(default_factory=list)
    behavior_audit_log: list[str] = field(default_factory=list)
    daily_stats: dict[str, Any] = field(default_factory=dict)
    # Real per-request token counts from the provider's own usage field
    # (Groq's `stream_options={"include_usage": True}`), not an estimate.
    # Resets when the process restarts — there is no persistent daily-usage
    # store yet, so this covers "since this session started," and the
    # dashboard should say so rather than imply a true calendar-day total.
    llm_tokens_prompt: int = 0
    llm_tokens_completion: int = 0
    llm_request_count: int = 0
    llm_last_model: str = ""
    llm_usage_since: float = field(default_factory=time.time)
    active_project_thread: str = ""
    pending_reminders_count: int = 0
    # Reminders the backend has just claimed as due, newest last. Surfaces
    # (the desktop shell's notifications, the device face) show each one once,
    # by its `id`; the backend decides what is due, surfaces only display it.
    fired_reminders: list[dict[str, Any]] = field(default_factory=list)

    def update(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if key == "device_connected":
                # device_connected is now a derived, read-only view of
                # device_conn_state (Phase 1A) — a caller wanting to change
                # connectivity must go through device_connect()/
                # device_heartbeat()/device_disconnect() so the state machine
                # stays authoritative. Rejected rather than applied (a plain
                # `continue` here would silently hide a stale caller instead
                # of surfacing it), but logged rather than raised so a
                # leftover call site degrades instead of crashing the app.
                try:
                    import utils.logger as log
                    log.warn(
                        "RuntimeStatus.update(device_connected=...) ignored — "
                        "use device_connect()/device_heartbeat()/device_disconnect() instead."
                    )
                except Exception:
                    pass
                continue
            if hasattr(self, key):
                setattr(self, key, value)
        _notify_broadcast()

    @property
    def device_connected(self) -> bool:
        """
        Derived from device_conn_state — true only while the device is
        reachable (ONLINE or DEGRADED). This used to be a plain stored
        field that was set True on connect and never cleared on
        disconnect, so a dead device could read as connected indefinitely;
        it is now computed from the state machine on every read instead.
        """
        return self.device_conn_state in (DeviceConnState.ONLINE.value, DeviceConnState.DEGRADED.value)

    def device_connect(
        self,
        remote: str | None = None,
        *,
        device_id: str = "",
        session_id: str = "",
        firmware_version: str = "",
        capabilities: dict[str, Any] | None = None,
    ) -> None:
        """
        A device HELLO was accepted (Phase 1B: this is only called after
        authentication succeeds — see websocket_handler). Authoritative
        entry to ONLINE regardless of the prior state — a fresh connect
        always wins over a stale RECONNECTING/OFFLINE/DEGRADED reading.

        device_id/session_id/firmware_version/capabilities come from the
        HELLO payload; callers that don't have one yet (none should, now
        that HELLO is required) get the empty defaults from Phase 1A.
        """
        now = time.time()
        self.device_conn_state = DeviceConnState.ONLINE.value
        self.device_connected_at = now
        self.device_disconnected_at = None
        self.last_ws_seen_at = now
        if remote is not None:
            self.remote = remote
        self.device_id = device_id
        self.device_session_id = session_id
        self.device_firmware_version = firmware_version
        self.device_capabilities = capabilities or {}
        self.last_error = None
        _notify_broadcast()

    def device_heartbeat(self) -> None:
        """A frame arrived from the device. Recovers ONLINE from a soft DEGRADED reading."""
        self.last_ws_seen_at = time.time()
        if self.device_conn_state == DeviceConnState.DEGRADED.value:
            self.device_conn_state = DeviceConnState.ONLINE.value
        _notify_broadcast()

    def device_disconnect(self) -> None:
        """
        The WebSocket closed. The firmware auto-reconnects within ~10 s
        (MBUDDY.ino's WS_RECONNECT_MS), so this moves to RECONNECTING
        rather than straight to OFFLINE — device_connected already reads
        False in RECONNECTING, so nothing downstream is misled meanwhile.
        device_sweep() promotes this to OFFLINE if no reconnect arrives.
        """
        if self.device_conn_state == DeviceConnState.OFFLINE.value:
            return
        self.device_conn_state = DeviceConnState.RECONNECTING.value
        self.device_disconnected_at = time.time()
        _notify_broadcast()

    def device_sweep(self) -> None:
        """
        Periodic reconciliation for a connection that goes bad without a
        clean close (crash, WiFi drop, cable pull). device_connect()/
        device_heartbeat()/device_disconnect() already handle the ordinary
        cases the instant they happen; this only catches the case nothing
        told us about. Call on a timer (see websocket_handler).
        """
        now = time.time()
        if self.device_conn_state == DeviceConnState.ONLINE.value and self.last_ws_seen_at:
            if now - self.last_ws_seen_at > self.DEVICE_SOFT_SILENCE_S:
                self.device_conn_state = DeviceConnState.DEGRADED.value
                _notify_broadcast()
        elif self.device_conn_state in (DeviceConnState.DEGRADED.value, DeviceConnState.RECONNECTING.value):
            reference = self.device_disconnected_at or self.last_ws_seen_at
            if reference and now - reference > self.DEVICE_SILENCE_TIMEOUT_S:
                self.device_conn_state = DeviceConnState.OFFLINE.value
                _notify_broadcast()

    def record_llm_usage(self, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        """Add one request's real, provider-reported token counts to the running total."""
        self.llm_tokens_prompt += max(0, prompt_tokens)
        self.llm_tokens_completion += max(0, completion_tokens)
        self.llm_request_count += 1
        self.llm_last_model = model
        _notify_broadcast()

    def mark_mode(self, mode: str, reason: str = "") -> None:
        self.mode = mode
        self.mode_reason = reason

    def record_fired_reminders(self, items: list[dict[str, Any]], limit: int = 20) -> None:
        """Publish reminders that just came due; keeps the newest `limit`."""
        if not items:
            return
        fired_at = time.time()
        self.fired_reminders = (
            self.fired_reminders + [{**item, "fired_at": fired_at} for item in items]
        )[-limit:]
        _notify_broadcast()

    def record_behavior_trace(self, trace: dict[str, Any]) -> None:
        """Append a validated behavior trace and update cooldown inspection lists."""
        reason = str(trace.get("reason", "")).strip()
        if not reason:
            raise ValueError("BehaviorTrace rejected: reason is required.")
        priority = str(trace.get("priority", "normal")).strip().lower()
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"BehaviorTrace rejected: invalid priority {priority!r}.")
        trace["priority"] = priority
        world_snapshot = trace.get("world_state_snapshot") or self.world_model or {}
        trace["world_state_snapshot"] = world_snapshot
        trace.setdefault("need", "")
        trace.setdefault("emotion_reason", trace.get("reason", ""))
        trace.setdefault("tool_selected", trace.get("tool_used", ""))
        trace.setdefault("tool_reason", "")
        trace.setdefault("animation_selected", trace.get("animation", ""))
        trace.setdefault("animation_reason", "")
        trace.setdefault("sfx_selected", trace.get("sfx", ""))
        trace.setdefault("sfx_reason", "")
        trace.setdefault("reply_reason", "")

        def push_unique_recent(attr: str, value: Any, limit: int = 20) -> None:
            clean = str(value or "").strip()
            if not clean:
                return
            items = [item for item in getattr(self, attr) if item != clean]
            items.insert(0, clean)
            setattr(self, attr, items[:limit])

        self.behavior_traces.insert(0, trace)
        self.behavior_traces = self.behavior_traces[:100]
        self.behavior_decision = dict(trace.get("decision", {}) or {})
        push_unique_recent("recent_actions", trace.get("action"))
        push_unique_recent("recent_emotions", trace.get("emotion"))
        push_unique_recent("recent_sfx", trace.get("sfx"))
        push_unique_recent("recent_animations", trace.get("animation"))

        chosen = trace.get("action") or trace.get("animation") or trace.get("sfx") or trace.get("emotion") or "text_only"
        audit = (
            f"Behavior Chosen: {chosen} | Reason: {reason} | "
            f"Competing Behaviors: {len(trace.get('competing_behaviors') or [])}"
        )
        self.behavior_audit_log.insert(0, audit)
        self.behavior_audit_log = self.behavior_audit_log[:100]

    def record_emotion_decision(
        self,
        decision: dict[str, Any],
        *,
        goal: str = "answer_user",
        intent: str = "emotion_validation",
        priority: str = "normal",
        tool_used: str = "",
        source: str = "emotion_validation",
        behavior_decision: dict[str, Any] | None = None,
        competing_behaviors: list[Any] | None = None,
        conflicts: list[Any] | None = None,
    ) -> None:
        """Record authoritative emotion validation for dashboard/audit review."""
        trace = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": "emotion_validation",
            "goal": goal,
            "intent": intent,
            "emotion": decision.get("final_emotion", ""),
            "action": "",
            "animation": "",
            "sfx": "",
            "tool_used": tool_used,
            "reason": decision.get("reason") or decision.get("rejection_reason") or "Emotion validation completed.",
            "priority": priority,
            "source": source,
            "cognitive_emotion": decision.get("cognitive_emotion", ""),
            "llm_suggested_emotion": decision.get("llm_suggested_emotion", ""),
            "final_emotion": decision.get("final_emotion", ""),
            "authority_source": decision.get("authority_source", ""),
            "accepted": bool(decision.get("accepted")),
            "rejection_reason": decision.get("rejection_reason", ""),
            "decision": behavior_decision or {},
            "emotion_decision": decision,
            "competing_behaviors": competing_behaviors or [],
            "conflicts": conflicts or [],
        }
        self.emotion_decision = dict(decision)
        if not decision.get("accepted"):
            self.emotion_disagreements.insert(0, dict(decision))
            self.emotion_disagreements = self.emotion_disagreements[:100]
        self.record_behavior_trace(trace)

    # Diagnostic fields that are large, change constantly, and are of no use
    # to a live status consumer. `behavior_traces` alone reached 2.4 MB
    # because every trace embeds a full world-model snapshot.
    HEAVY_FIELDS = ("behavior_traces", "behavior_audit_log", "cognitive_state")

    # Both thresholds below are grounded in the firmware's own timing
    # constants (firmware/mbuddy/src/MBUDDY.ino), not a guess:
    #   - Mic PCM streams continuously while connected and not TTS-gated
    #     (micTask, canStream check ~line 1318) — normal idle silence should
    #     be near-zero, not seconds.
    #   - The one guaranteed periodic gap is the application-level
    #     "keep-alive" TEXT message, sent every 30000 ms when otherwise idle
    #     (line ~1605: `millis() - sys.lastKeepalive > 30000`). A perfectly
    #     healthy device can therefore go silent for just under 30 s.
    #   - WS-level ping/pong heartbeat: 10 s interval, 3 s pong timeout,
    #     tolerating 2 consecutive failures before the client library gives
    #     up (`wsClient.enableHeartbeat(10000, 3000, 2)`, line ~596) — up to
    #     ~26 s to detect a truly dead TCP connection from the firmware side.
    #   - Reconnect interval after that: `WS_RECONNECT_MS = 10000` (10 s).
    #
    # An earlier version of this constant (10.0 s) was an unvalidated guess
    # made before this was checked, and would have falsely flagged a
    # perfectly idle, healthy device as DEGRADED well before its next
    # keep-alive — verified against firmware source, not live traffic (no
    # board was intentionally driven idle for 30+ s to confirm timing).

    # Quieter than this while still ONLINE is a soft warning (DEGRADED), not
    # yet treated as gone. Set above the 30 s keep-alive interval with a
    # small margin for scheduler/network jitter, so ordinary idle silence
    # never trips it.
    DEVICE_SOFT_SILENCE_S = 35.0

    # A device that has not sent anything for this long is treated as gone.
    # Set to comfortably cover keep-alive (30 s) plus one heartbeat-detection
    # + reconnect cycle (~26 s + 10 s) with margin, rather than sitting right
    # at the keep-alive boundary — the previous 30 s value was exactly equal
    # to the keep-alive interval, leaving no safety margin at all.
    DEVICE_SILENCE_TIMEOUT_S = 60.0

    def _serial_is_live(self) -> bool:
        """
        Whether a USB-serial link to the device is open.

        Imported lazily: `core` must not depend on `api` at import time, and
        the bridge is optional when pyserial is missing.
        """
        try:
            from api.serial_bridge import serial_bridge
        except ImportError:
            return False
        # `connected` is a property, not a method. Calling it raised TypeError,
        # which a broad `except` turned into "no device" — a live USB link
        # reported as absent, with nothing logged to say why.
        return bool(serial_bridge.connected)

    def device_transport(self) -> str | None:
        """
        Which link is currently carrying the device: "ws", "serial", or None.

        WebSocket wins when both are up, because it is the only one that
        carries audio; serial is control-only.
        """
        if self.device_connected and self.last_ws_seen_at:
            age = time.time() - self.last_ws_seen_at
            if age <= self.DEVICE_SILENCE_TIMEOUT_S:
                return "ws"
        if self._serial_is_live():
            return "serial"
        return None

    def device_is_live(self) -> bool:
        """
        Whether the ESP32 is genuinely reachable right now, over any transport.

        `device_connected` reflects the connection state machine (Phase 1A)
        and is cleared on disconnect/timeout on its own, but is still not
        enough by itself: it only speaks to the WebSocket. A device on USB
        with no WiFi is still a body MBUDDY can drive, so serial is checked too.
        """
        return self.device_transport() is not None

    def snapshot(self, full: bool = False) -> dict[str, Any]:
        """
        Current runtime status.

        Args:
            full: include the heavy diagnostic fields. Off by default,
                because this snapshot is broadcast to every connected client
                roughly once a second, and those fields made each message
                about 2.5 MB — over the default frame limit of the desktop's
                WebSocket client, which is why the dashboard kept
                disconnecting.

        Returns:
            dict: the status payload. Without `full`, a summary of each heavy
            field is included instead of its contents, so a consumer can still
            see that traces exist and how many.
        """
        if full:
            # Deep copy, so a caller inspecting traces cannot be tripped up by
            # the live status mutating underneath them.
            data = asdict(self)
        else:
            # Shallow field access. `asdict` recurses into every trace and its
            # embedded world snapshot — 327 ms with a full buffer — and all of
            # that work was thrown away by the blanking below.
            data = {f.name: getattr(self, f.name) for f in fields(self)}
            for name in self.HEAVY_FIELDS:
                value = data.get(name)
                if isinstance(value, list):
                    data[name] = []
                    data[f"{name}_count"] = len(value)
                elif isinstance(value, dict):
                    data[name] = {}
                    data[f"{name}_count"] = len(value)

        data["uptime_s"] = round(time.time() - self.started_at, 2)
        data["last_ws_age_s"] = (
            round(time.time() - self.last_ws_seen_at, 2)
            if self.last_ws_seen_at
            else None
        )

        # Report the link honestly, in both directions. A stored True from a
        # connect that happened hours ago makes every consumer believe a dead
        # device is present and freezes the emotion at its last value; but a
        # WebSocket-only test denies a device that is reachable over USB.
        transport = self.device_transport()
        data["device_transport"] = transport
        data["device_connected"] = transport is not None

        # Serial carries control but not audio, so say so rather than letting
        # a caller assume a fully capable body.
        data["device_audio_capable"] = transport == "ws"

        if self.device_connected and transport is None:
            data["device_stale"] = True
            data["mode"] = "limited"
            data["mode_reason"] = "device_silent"
        else:
            data["device_stale"] = False
            if transport == "serial":
                data["mode_reason"] = "serial_only"

            # The capability registry is 20 KB of largely static prose —
            # descriptions, purposes, limitations — and it appears twice, once
            # at the top level and again nested inside the world model. A live
            # consumer only needs to know what exists and whether it works.
            data["capability_registry"] = _compact_registry(data.get("capability_registry"))
            world = data.get("world_model")
            if isinstance(world, dict) and "capability_registry" in world:
                world = dict(world)
                world["capability_registry"] = data["capability_registry"]
                data["world_model"] = world

        return data



def _compact_registry(registry):
    """
    Reduce the capability registry to what a live consumer needs.

    Keeps each capability's availability and health, drops the descriptive
    text. The full registry is still available through `snapshot(full=True)`.
    """
    if not isinstance(registry, dict):
        return registry

    def shrink(entry):
        if not isinstance(entry, dict):
            return entry
        return {
            k: entry[k]
            for k in ("available", "healthy", "enabled", "kind")
            if k in entry
        }

    out = {}
    for key, value in registry.items():
        if isinstance(value, dict) and value and all(isinstance(v, dict) for v in value.values()):
            out[key] = {n: shrink(v) for n, v in value.items()}
        elif isinstance(value, dict):
            out[key] = shrink(value)
        else:
            out[key] = value
    return out


runtime_status = RuntimeStatus()
