"""
core/permissions.py — Which sensitive Windows capabilities MBUDDY may use.

**What it does:**
Owns the "ask once, then remember" model for anything that reaches outside
MBUDDY's own process on this PC: the microphone, the screen, and whatever
comes next (camera, file access, ...). A capability lives here whether or
not it's actually built yet — an unimplemented one still gets a real entry
so Settings can show it honestly ("nothing uses this yet") instead of the
desktop inventing a toggle for a feature that doesn't exist.

Granting happens once, from Settings or from the first real use-attempt;
after that, the trigger points (`buddy_app.py`'s PC Voice button,
`settings_tab.py`'s content-awareness checkbox) check `is_granted()` before
acting instead of asking every time.

**How to use:**
```python
from core.permissions import get_permission_store, PERMISSIONS

store = get_permission_store()
store.is_granted("microphone")   # False until granted
store.grant("microphone")
store.snapshot()                 # real state for every known capability
```
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

log = logging.getLogger(__name__)

# Lives next to embodiment.json / user_preferences.json — the same
# per-machine config directory every other cross-process setting already
# uses this session, so the desktop and the backend agree on what's granted
# without either one owning a private copy.
PERMISSIONS_PATH = Path.home() / ".buddy" / "config" / "permissions.json"


@dataclass(frozen=True)
class PermissionCapability:
    """One thing MBUDDY could be granted access to."""
    name: str
    label: str
    description: str
    # False for a capability that has no real code behind it yet. Settings
    # shows this honestly rather than letting a grant imply a feature exists.
    implemented: bool = True


# The known capability list. Adding a future one (camera, file access, ...)
# is exactly one entry here — Settings and the API both read from this, so
# nothing else needs to change to make a new capability askable.
PERMISSIONS: List[PermissionCapability] = [
    PermissionCapability(
        name="microphone",
        label="Microphone (PC Voice)",
        description="Stream this PC's microphone to MBUDDY so it can listen "
                     "without the paired hardware body.",
        implemented=True,
    ),
    PermissionCapability(
        name="screen_capture",
        label="Screen / content awareness",
        description="Let MBUDDY periodically see what's on screen to react "
                     "to what you're working on.",
        implemented=True,
    ),
    PermissionCapability(
        name="camera",
        label="Camera",
        description="Reserved for a future feature — no code reads the "
                     "camera yet, so granting this does nothing today.",
        implemented=False,
    ),
]

_BY_NAME = {p.name: p for p in PERMISSIONS}


class PermissionStore:
    """Persisted grant/revoke state for every known capability."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else PERMISSIONS_PATH
        self._grants: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._grants = data.get("grants", {})
        except Exception:
            log.warning("Could not read permissions config at %s", self.path)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"grants": self._grants}, indent=2), encoding="utf-8"
            )
        except Exception:
            log.warning("Could not write permissions config to %s", self.path)

    def is_granted(self, name: str) -> bool:
        return bool(self._grants.get(name, {}).get("granted", False))

    def grant(self, name: str) -> None:
        if name not in _BY_NAME:
            raise ValueError(f"Unknown capability: {name}")
        self._grants[name] = {"granted": True, "at": time.time()}
        self._save()
        log.info("Permission granted: %s", name)

    def revoke(self, name: str) -> None:
        if name not in _BY_NAME:
            raise ValueError(f"Unknown capability: {name}")
        self._grants[name] = {"granted": False, "at": time.time()}
        self._save()
        log.info("Permission revoked: %s", name)

    def snapshot(self) -> List[Dict[str, Any]]:
        """Every known capability with its real current grant state."""
        out = []
        for cap in PERMISSIONS:
            entry = self._grants.get(cap.name, {})
            out.append({
                "name": cap.name,
                "label": cap.label,
                "description": cap.description,
                "implemented": cap.implemented,
                "granted": bool(entry.get("granted", False)),
                "granted_at": entry.get("at"),
            })
        return out


_store: PermissionStore | None = None


def get_permission_store() -> PermissionStore:
    """One shared, persisted grant store for the whole process."""
    global _store
    if _store is None:
        _store = PermissionStore()
    return _store
