"""
core/embodiment.py — Where MBUDDY currently lives

**What it does:**
MBUDDY runs on Windows and can optionally drive a physical ESP32 body. This
module is the single place that answers "does MBUDDY have a body right now,
and was it expecting one?".

That distinction matters. A missing body is only a fault if a body was
supposed to be there. When MBUDDY is deliberately running on the PC alone —
because the hardware is charging, or you are working at the desk — the absence
of a display is normal, and MBUDDY should carry on thinking, using tools, and
holding a conversation without treating it as an emergency.

Three states:

| State       | Meaning                                                   |
|-------------|-----------------------------------------------------------|
| `headless`  | No body paired. Windows-only operation is the intent.     |
| `embodied`  | A body is paired and currently connected.                 |
| `searching` | A body is paired but not reachable — this one is a fault. |

**How to use:**
```python
from core.embodiment import get_embodiment

emb = get_embodiment()

emb.select_body("mbuddy-desk")     # pair a body (from the desktop Settings tab)
emb.set_link(connected=True)       # called when the WebSocket connects
emb.state                          # "embodied"
emb.expects_body                   # True once a body is paired

emb.release_body()                 # go back to Windows-only operation
```

Transitions are recorded, so MBUDDY can notice it has just moved between
bodies and say something about it rather than silently changing behaviour.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.time_manager import get_time_manager

log = logging.getLogger(__name__)

# Paired-body choice lives next to the other user config so the desktop app
# and the backend agree on which body is selected across restarts.
EMBODIMENT_PATH = Path.home() / ".buddy" / "config" / "embodiment.json"

# How many transitions to keep. Enough for MBUDDY to reference the last
# change without the record growing without bound.
MAX_TRANSITIONS = 12

STATE_HEADLESS = "headless"
STATE_EMBODIED = "embodied"
STATE_SEARCHING = "searching"


@dataclass
class BodyProfile:
    """
    A physical body MBUDDY can occupy.

    Attributes:
        body_id: stable identifier, used to match a connecting device
        name: what to call it out loud, e.g. "the desk one"
        display: panel description, e.g. "128x64 OLED"
        has_speaker: whether this body can speak
        has_microphone: whether this body can listen
    """
    body_id: str
    name: str = ""
    display: str = "128x64 OLED"
    has_speaker: bool = True
    has_microphone: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "body_id": self.body_id,
            "name": self.name or self.body_id,
            "display": self.display,
            "has_speaker": self.has_speaker,
            "has_microphone": self.has_microphone,
        }


class Embodiment:
    """
    Tracks whether MBUDDY has a body, and remembers which one was chosen.

    The paired body is persisted; the live link is not, because whether the
    device is reachable is only knowable at runtime.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else EMBODIMENT_PATH
        self.selected: Optional[BodyProfile] = None
        self.link_connected: bool = False
        self.transitions: List[Dict[str, Any]] = []
        self._last_state: str = STATE_HEADLESS
        self._load()

    # ── Persistence ───────────────────────────────────────────────────
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("Could not read embodiment config at %s", self.path)
            return

        body = data.get("selected_body")
        if isinstance(body, dict) and body.get("body_id"):
            self.selected = BodyProfile(
                body_id=str(body["body_id"]),
                name=str(body.get("name", "")),
                display=str(body.get("display", "128x64 OLED")),
                has_speaker=bool(body.get("has_speaker", True)),
                has_microphone=bool(body.get("has_microphone", True)),
            )
        trans = data.get("transitions")
        if isinstance(trans, list):
            self.transitions = trans[-MAX_TRANSITIONS:]
        self._last_state = self.state

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    {
                        "selected_body": self.selected.to_dict() if self.selected else None,
                        "transitions": self.transitions[-MAX_TRANSITIONS:],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            log.warning("Could not write embodiment config to %s", self.path)

    # ── State ─────────────────────────────────────────────────────────
    @property
    def expects_body(self) -> bool:
        """True when a body has been paired, so absence is a real fault."""
        return self.selected is not None

    @property
    def state(self) -> str:
        """One of `headless`, `embodied`, or `searching`."""
        if not self.expects_body:
            return STATE_HEADLESS
        return STATE_EMBODIED if self.link_connected else STATE_SEARCHING

    @property
    def is_fault(self) -> bool:
        """
        True only when a paired body cannot be reached.

        This is the check behaviour selection should use, rather than asking
        whether the link is up. Running on Windows alone is a valid mode, not
        a fault to recover from.
        """
        return self.state == STATE_SEARCHING

    @property
    def can_show_visuals(self) -> bool:
        """Whether there is a display attached to animate right now."""
        return self.state == STATE_EMBODIED

    @property
    def body_name(self) -> str:
        """What to call the current body, or "Windows" when headless."""
        if self.selected is None:
            return "Windows"
        return self.selected.name or self.selected.body_id

    # ── Mutations ─────────────────────────────────────────────────────
    def select_body(self, body_id: str, name: str = "", **kwargs) -> BodyProfile:
        """
        Pair a body. From now on, losing the link counts as a fault.

        Args:
            body_id: stable identifier for the device
            name: friendly name to use out loud
            **kwargs: any other `BodyProfile` field

        Returns:
            BodyProfile: the newly selected body
        """
        previous = self.body_name
        self.selected = BodyProfile(body_id=str(body_id), name=name, **kwargs)
        self._record(f"moved into {self.body_name}", previous)
        self._save()
        log.info("Embodiment: selected body %s", self.body_name)
        return self.selected

    def release_body(self) -> None:
        """Unpair the current body and return to Windows-only operation."""
        if self.selected is None:
            return
        previous = self.body_name
        self.selected = None
        self.link_connected = False
        self._record("returned to Windows only", previous)
        self._save()
        log.info("Embodiment: released body, now headless")

    def set_link(self, connected: bool) -> bool:
        """
        Record whether the paired body is currently reachable.

        Call this from the WebSocket connect and disconnect handlers.

        Returns:
            bool: True if this changed the embodiment state
        """
        was = self.state
        self.link_connected = bool(connected)
        now = self.state
        if now != was:
            self._record(f"{was} to {now}", self.body_name)
            self._save()
            return True
        return False

    def _record(self, what: str, where: str) -> None:
        self.transitions.append({
            "at": get_time_manager().naive_now().isoformat(),
            "change": what,
            "body": where,
        })
        self.transitions = self.transitions[-MAX_TRANSITIONS:]

    def recent_transition(self, within_seconds: float = 120.0) -> Optional[Dict[str, Any]]:
        """
        The most recent embodiment change, if it happened just now.

        Lets MBUDDY mention having just moved bodies instead of silently
        behaving differently.
        """
        if not self.transitions:
            return None
        last = self.transitions[-1]
        try:
            when = get_time_manager().from_iso(last["at"])
        except Exception:
            return None
        age = (get_time_manager().naive_now() - when).total_seconds()
        return last if 0 <= age <= within_seconds else None

    # ── Reporting ─────────────────────────────────────────────────────
    def describe(self) -> str:
        """A one-line description for the prompt and the desktop status bar."""
        if self.state == STATE_HEADLESS:
            return "Running on Windows with no body attached. This is normal, not a fault."
        if self.state == STATE_EMBODIED:
            return f"Running on Windows and embodied in {self.body_name}."
        return f"Paired with {self.body_name}, but it is not reachable right now."

    def to_dict(self) -> Dict[str, Any]:
        """Snapshot for the world model and the desktop app."""
        return {
            "state": self.state,
            "expects_body": self.expects_body,
            "link_connected": self.link_connected,
            "can_show_visuals": self.can_show_visuals,
            "is_fault": self.is_fault,
            "body": self.selected.to_dict() if self.selected else None,
            "body_name": self.body_name,
            "description": self.describe(),
        }


# ── Module singleton ──────────────────────────────────────────────────
_embodiment: Optional[Embodiment] = None


def get_embodiment() -> Embodiment:
    """Get the shared embodiment tracker, creating it on first use."""
    global _embodiment
    if _embodiment is None:
        _embodiment = Embodiment()
    return _embodiment
