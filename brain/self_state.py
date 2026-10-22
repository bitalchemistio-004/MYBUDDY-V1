"""What MBUDDY is, right now, from its real state (W1).

Asked to say something to a friend in the room, it answered "I can't talk out
loud, I don't have a voice": nothing in its prompt said it speaks. This card
is built from the real settings and devices each turn, so what it says about
itself is true: it has a voice when the voice is on and Windows has a speaker,
it hears through the microphone when the mic is on, and it names the apps the
user connected.
"""
from __future__ import annotations

import time

_audio: tuple[float, bool] = (0.0, True)
AUDIO_CHECK_S = 30.0


def speakers_present() -> bool:
    """Does Windows have an active output device? Checked at most every 30 s."""
    global _audio
    at, present = _audio
    if time.monotonic() - at < AUDIO_CHECK_S and at:
        return present
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities
        from brain.tools import _output_volume
        comtypes.CoInitialize()
        try:
            _output_volume(AudioUtilities)
            present = True
        finally:
            from brain.com_guard import end as _com_end; _com_end()
    except OSError:
        present = False   # "no active output device"
    except Exception:
        present = True    # can't tell: don't claim it's broken
    _audio = (time.monotonic(), present)
    return present


def self_card() -> str:
    """A few true lines about MBUDDY's own senses and reach, for the turn block."""
    from core import ui_settings
    lines = []
    if not ui_settings.get("speak_replies"):
        lines.append("Your voice is switched off in settings, so your replies show as text for now.")
    elif not speakers_present():
        lines.append("You have a voice, but Windows has no active speakers or headphones right now, so he reads "
                     "your replies until one is plugged in.")
    else:
        lines.append("You have a voice: every reply you write is spoken out loud through the PC's speakers, so "
                     "you can say things to him and to people near him.")
    lines.append("You hear him through the microphone." if ui_settings.get("microphone_on")
                 else "The microphone is off, so he types to you.")
    apps = [a.get("name", "") for a in ui_settings.connected_apps() if a.get("name")]
    if apps:
        lines.append("Apps he connected for you to work in: " + ", ".join(apps[:8]) + ".")
    return "ABOUT YOU RIGHT NOW (true):\n- " + "\n- ".join(lines)
