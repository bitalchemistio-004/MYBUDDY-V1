"""
core/companion_settings.py — How often MBUDDY thinks to itself

**What it does:**
The Inner Life Loop generates MBUDDY's private thoughts while it is idle. Its
cadence used to be hardcoded to a random 25-75 seconds, which is both
unconfigurable and expensive: every cycle is a Groq call competing with your
actual conversation for the same tokens-per-minute budget.

This makes the interval yours to set, and lets you turn it off entirely
without touching code.

**How to use:**
```python
from core.companion_settings import get_companion_settings

cs = get_companion_settings()

cs.set_inner_interval(60, 180)   # think every 1-3 minutes
cs.set_inner_enabled(False)      # or not at all
cs.next_inner_wait(energy=90)    # seconds to wait before the next thought
```

The interval you set is the baseline. Energy still modulates it — a tired
MBUDDY muses less often, an alert one more — but always in proportion to your
setting rather than overriding it.
"""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

COMPANION_CONFIG_PATH = Path.home() / ".buddy" / "config" / "companion_settings.json"

# Guard rails. Below the minimum the loop would burn the token budget faster
# than conversation can use it; above the maximum it stops feeling alive.
MIN_INTERVAL_S = 10
MAX_INTERVAL_S = 3600

DEFAULT_MIN_S = 25
DEFAULT_MAX_S = 75


class CompanionSettings:
    """Cadence and switches for MBUDDY's autonomous behaviour."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else COMPANION_CONFIG_PATH
        self._inner_min_s: int = DEFAULT_MIN_S
        self._inner_max_s: int = DEFAULT_MAX_S
        self._inner_enabled: bool = False  # off by default: it runs the model (GPU, fans) all day
        self._load()

    # ── Persistence ───────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                lo = int(data.get("inner_min_s", DEFAULT_MIN_S))
                hi = int(data.get("inner_max_s", DEFAULT_MAX_S))
                self._inner_min_s, self._inner_max_s = self._clamp_pair(lo, hi)
                self._inner_enabled = bool(data.get("inner_enabled", False))
        except Exception:
            log.debug("Could not read companion settings; using defaults")

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    {
                        "inner_min_s": self._inner_min_s,
                        "inner_max_s": self._inner_max_s,
                        "inner_enabled": self._inner_enabled,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            log.debug("Could not write companion settings")

    @staticmethod
    def _clamp_pair(lo, hi) -> tuple[int, int]:
        """Keep the pair inside the guard rails and correctly ordered."""
        try:
            lo = int(lo)
            hi = int(hi)
        except (TypeError, ValueError):
            return DEFAULT_MIN_S, DEFAULT_MAX_S
        lo = max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, lo))
        hi = max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, hi))
        if hi < lo:
            lo, hi = hi, lo
        return lo, hi

    # ── Inner life ────────────────────────────────────────────────────
    @property
    def inner_enabled(self) -> bool:
        """Whether MBUDDY generates private thoughts at all."""
        return self._inner_enabled

    def set_inner_enabled(self, enabled: bool) -> bool:
        """
        Turn the inner life on or off.

        Off means MBUDDY stops thinking to itself entirely, which also stops
        it spending any of the language-model token budget while idle.
        """
        self._inner_enabled = bool(enabled)
        self._save()
        return self._inner_enabled

    @property
    def inner_interval(self) -> tuple[int, int]:
        """The configured (minimum, maximum) seconds between thoughts."""
        return self._inner_min_s, self._inner_max_s

    def set_inner_interval(self, min_s, max_s) -> tuple[int, int]:
        """
        Set how often MBUDDY thinks to itself.

        Args:
            min_s: shortest gap between thoughts, in seconds
            max_s: longest gap, in seconds

        Both are clamped to 10 s - 1 h and swapped if given the wrong way
        round, so a mistyped pair cannot stall or flood the loop.

        Returns:
            tuple: the stored (min, max)
        """
        self._inner_min_s, self._inner_max_s = self._clamp_pair(min_s, max_s)
        self._save()
        return self._inner_min_s, self._inner_max_s

    def next_inner_wait(self, energy: float = 60.0) -> float:
        """
        How long to wait before the next private thought.

        Your interval is the baseline; energy stretches or compresses it.
        A tired MBUDDY muses roughly half as often, an alert one about twice
        as often, but always relative to what you set.

        Args:
            energy: MBUDDY's current energy, 0-100

        Returns:
            float: seconds to sleep
        """
        lo, hi = self._inner_min_s, self._inner_max_s
        base = random.uniform(lo, hi)

        if energy < 25:
            base *= 2.1          # Barely awake; thoughts come slowly.
        elif energy < 45:
            base *= 1.4
        elif energy > 82:
            base *= 0.55         # Alert and restless.

        return max(float(MIN_INTERVAL_S), min(float(MAX_INTERVAL_S), base))

    def to_dict(self) -> dict:
        """Snapshot for the status payload and the desktop app."""
        return {
            "inner_enabled": self._inner_enabled,
            "inner_min_s": self._inner_min_s,
            "inner_max_s": self._inner_max_s,
        }


# ── Module singleton ──────────────────────────────────────────────────
_companion_settings: Optional[CompanionSettings] = None


def get_companion_settings() -> CompanionSettings:
    """Get the shared companion settings, creating them on first use."""
    global _companion_settings
    if _companion_settings is None:
        _companion_settings = CompanionSettings()
    return _companion_settings
