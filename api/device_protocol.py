"""Tag-based wire protocol helpers for the ESP32 firmware."""
from __future__ import annotations
import json
from datetime import datetime

# ── Phase 1B: versioned JSON envelope, HELLO handshake only ────────────
#
# Everything else on the wire (bracket tags built by DeviceProtocol below,
# plus raw PCM binary frames) is unchanged — Phase 1B does not migrate the
# existing tag protocol or touch the binary audio path at all. HELLO is a
# new message with no bracket-tag equivalent, so it goes straight to JSON
# rather than being awkwardly wedged into `payload: "[TAG] text"`.
ENVELOPE_VERSION = 1


def parse_hello(text: str) -> dict | None:
    """
    Parse the first message a device sends after the WS opens. Returns the
    parsed dict only if it is a well-formed `type: "hello"` envelope with
    every required field present as a string; None for anything else
    (including plain bracket-tag text, which is not a protocol violation —
    it just isn't HELLO — but IS a violation here, since HELLO must be
    first; the caller treats None as a reason to reject).
    """
    try:
        msg = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(msg, dict) or msg.get("type") != "hello":
        return None
    required = ("device_id", "session_id", "firmware_version", "auth_token")
    if not all(isinstance(msg.get(k), str) and msg.get(k) for k in required):
        return None
    if not isinstance(msg.get("capabilities", {}), dict):
        return None
    return msg


def hello_ack(session_id: str) -> str:
    return json.dumps({"version": ENVELOPE_VERSION, "type": "hello_ack", "accepted": True, "session_id": session_id})


def hello_reject(reason: str) -> str:
    return json.dumps({"version": ENVELOPE_VERSION, "type": "hello_reject", "accepted": False, "reason": reason})


class DeviceProtocol:
    """Builds the existing bracket messages consumed by MBUDDY.ino."""

    @staticmethod
    def speaking(state: str, text: str) -> str:
        return f"[SPEAKING] [STATE:{state}] {text}"

    @staticmethod
    def state_quiet(state: str) -> str:
        return f"[STATE:{state}][QUIET]"

    @staticmethod
    def heard(text: str = "ready") -> str:
        return f"[HEARD] {text}"

    @staticmethod
    def energy_update(level: float) -> str:
        """Tag used for the OLED corner energy display."""
        return f"[ENERGY:{int(level)}]"

    @staticmethod
    def action(name: str, duration_ms: int = 1500) -> str:
        """Temporary companion animation/action tag for the ESP32 OLED."""
        safe = "".join(ch for ch in name.lower() if ch.isalnum() or ch == "_") or "idle"
        return f"[ACT:{safe}:{max(250, int(duration_ms))}]"

    @staticmethod
    def clear_action() -> str:
        """Clear any temporary OLED action and return firmware to the base face."""
        return "[ACT:clear]"

    @staticmethod
    def inner_thought(text: str) -> str:
        """Private non-spoken thought tag for eyes/dashboard context."""
        clean = " ".join(str(text).strip().split())
        return f"[INNER] {clean[:96]}"

    @staticmethod
    def sfx(name: str) -> str:
        """Short generated sound-effect cue tag."""
        safe = "".join(ch for ch in name.lower() if ch.isalnum() or ch == "_") or "tick"
        return f"[SFX:{safe}]"

    @staticmethod
    def time_update(now: datetime | None = None) -> str:
        """Real local clock sync tag for ESP32 clock mode."""
        current = now or datetime.now()
        return f"[TIME:{current.hour:02d}:{current.minute:02d}:{current.second:02d}]"

    THINKING = "[THINKING]"
    QUIET = "[QUIET]"
    STOP = "[STOP]"
