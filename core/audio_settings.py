"""
core/audio_settings.py — User-controlled audio levels

**What it does:**
MBUDDY's speaking volume was derived entirely from its energy level, with no
way for the user to set it. That is fine as expression — a tired companion speaks
more quietly — but it is not a substitute for a volume control.

This adds a master volume that scales whatever the energy curve produces, so
the expressive variation is preserved while the overall level is yours. The
setting persists, so it survives a restart.

**How to use:**
```python
from core.audio_settings import get_audio_settings

audio = get_audio_settings()
audio.set_master_volume(0.6)        # 0.0 (mute) .. 1.5 (boost)
audio.apply(0.86)                   # scale an energy-derived level
audio.master_volume                 # read it back
```
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

AUDIO_CONFIG_PATH = Path.home() / ".buddy" / "config" / "audio_settings.json"

# The MAX98357A amplifier clips above roughly 1.5x, so allow headroom for a
# quiet room without letting the setting reach distortion.
MIN_VOLUME = 0.0
MAX_VOLUME = 1.5
DEFAULT_VOLUME = 1.0

# Below this, treat the device as muted rather than sending near-silent audio
# the amplifier will only add hiss to.
MUTE_THRESHOLD = 0.02

# core/config.py's MIN_FINALIZE_RMS (0.015) as the built-in starting point —
# how loud an utterance's average level must be before it's even sent to
# Whisper at all. Overridable and persisted here rather than left a fixed
# constant, because the right value depends on the room and the mic, not on
# the code: too low, and background chatter/TV/ambient noise gets treated as
# speech directed at MBUDDY; too high, and quieter real speech gets ignored
# outright before it ever reaches STT.
MIN_INPUT_RMS = 0.0
MAX_INPUT_RMS = 0.2
DEFAULT_INPUT_RMS = 0.015


class AudioSettings:
    """User-facing audio levels, persisted between runs."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else AUDIO_CONFIG_PATH
        self._master_volume: float = DEFAULT_VOLUME
        self._sfx_volume: float = DEFAULT_VOLUME
        # Private thoughts are meant to sit under normal speech, so they have
        # their own level rather than following the master.
        self._inner_volume: float = 0.35
        self._min_input_rms: float = DEFAULT_INPUT_RMS
        self._load()

    # ── Persistence ───────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._master_volume = self._clamp(data.get("master_volume", DEFAULT_VOLUME))
                self._sfx_volume = self._clamp(data.get("sfx_volume", DEFAULT_VOLUME))
                self._inner_volume = self._clamp(data.get("inner_volume", 0.35))
                self._min_input_rms = self._clamp_input_rms(
                    data.get("min_input_rms", DEFAULT_INPUT_RMS)
                )
        except Exception:
            log.debug("Could not read audio settings; using defaults")

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    {
                        "master_volume": self._master_volume,
                        "sfx_volume": self._sfx_volume,
                        "inner_volume": self._inner_volume,
                        "min_input_rms": self._min_input_rms,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            log.debug("Could not write audio settings")

    @staticmethod
    def _clamp(value) -> float:
        try:
            return max(MIN_VOLUME, min(MAX_VOLUME, float(value)))
        except (TypeError, ValueError):
            return DEFAULT_VOLUME

    @staticmethod
    def _clamp_input_rms(value) -> float:
        try:
            return max(MIN_INPUT_RMS, min(MAX_INPUT_RMS, float(value)))
        except (TypeError, ValueError):
            return DEFAULT_INPUT_RMS

    # ── Input sensitivity (reply gate) ────────────────────────────────
    @property
    def min_input_rms(self) -> float:
        """
        How loud an utterance's average level must be before it's sent to
        Whisper at all — everything quieter is discarded as ambient noise,
        never reaching STT or the brain. Calibrate this from the live mic
        level shown in the Hardware tab: watch what background noise and TV/
        other-room chatter reads as, then set this comfortably above it but
        below what real, deliberate speech to MBUDDY reads as.
        """
        return self._min_input_rms

    def set_min_input_rms(self, value: float) -> float:
        """Set the input sensitivity gate. Returns the clamped value stored."""
        self._min_input_rms = self._clamp_input_rms(value)
        self._save()
        return self._min_input_rms

    # ── Master volume ─────────────────────────────────────────────────
    @property
    def master_volume(self) -> float:
        """Current master level, 0.0 to 1.5."""
        return self._master_volume

    def set_master_volume(self, value: float) -> float:
        """
        Set the speaking volume.

        Args:
            value: 0.0 mutes, 1.0 is the natural level, up to 1.5 boosts

        Returns:
            float: the clamped value that was stored
        """
        self._master_volume = self._clamp(value)
        self._save()
        return self._master_volume

    @property
    def sfx_volume(self) -> float:
        """Level for sound effects, independent of speech."""
        return self._sfx_volume

    def set_sfx_volume(self, value: float) -> float:
        """Set the sound-effect volume, 0.0 to 1.5."""
        self._sfx_volume = self._clamp(value)
        self._save()
        return self._sfx_volume

    @property
    def is_muted(self) -> bool:
        """Whether the master level is low enough to count as silent."""
        return self._master_volume < MUTE_THRESHOLD

    # ── Application ───────────────────────────────────────────────────
    def apply(self, energy_volume: float) -> float:
        """
        Turn an energy-derived level into an actual output level.

        The master volume is the **absolute** level of the system: whatever
        you set is what MBUDDY speaks at. Mood and energy only soften it from
        there, they never push past it — so setting 0.5 means MBUDDY is never
        louder than 0.5, however excited it gets.

        The energy curve spans roughly 0.72 (tired) to 1.06 (alert). That is
        normalised against its own ceiling to become a 0.68-1.0 modulation,
        which is then applied to the master level.

        Args:
            energy_volume: the level the energy curve produced

        Returns:
            float: the level to hand to the synthesizer
        """
        # Highest value the energy curve can produce; anything at or above it
        # means "speak at the full level the user chose".
        ENERGY_CEILING = 1.06
        modulation = max(0.0, min(1.0, float(energy_volume) / ENERGY_CEILING))
        return max(0.0, min(MAX_VOLUME, self._master_volume * modulation))

    @property
    def inner_volume(self) -> float:
        """Level for MBUDDY's spoken private thoughts."""
        return self._inner_volume

    def set_inner_volume(self, value: float) -> float:
        """
        Set how loudly MBUDDY murmurs its own thoughts.

        These are background musings rather than replies, so the natural
        setting is well below speech. 0.0 keeps them silent while still
        letting them happen internally.
        """
        self._inner_volume = self._clamp(value)
        self._save()
        return self._inner_volume

    def apply_inner(self) -> float:
        """The output level for an inner thought, bounded by the master."""
        return max(0.0, min(self._master_volume, self._inner_volume))

    def apply_sfx(self, base_volume: float) -> float:
        """Scale a sound-effect level by the user's SFX volume."""
        return max(0.0, min(MAX_VOLUME, float(base_volume) * self._sfx_volume))

    def to_dict(self) -> dict:
        """Snapshot for the status payload and the desktop app."""
        return {
            "master_volume": round(self._master_volume, 3),
            "sfx_volume": round(self._sfx_volume, 3),
            "inner_volume": round(self._inner_volume, 3),
            "muted": self.is_muted,
            "min_input_rms": round(self._min_input_rms, 4),
        }


# ── Module singleton ──────────────────────────────────────────────────
_audio_settings: Optional[AudioSettings] = None


def get_audio_settings() -> AudioSettings:
    """Get the shared audio settings, creating them on first use."""
    global _audio_settings
    if _audio_settings is None:
        _audio_settings = AudioSettings()
    return _audio_settings
