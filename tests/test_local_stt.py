"""Local faster-whisper transcription (Phase 2): real model, real audio."""
import asyncio
import wave
from pathlib import Path

import numpy as np
import pytest

from stt.local_whisper import pick_language

CLIPS = Path(__file__).resolve().parent.parent / "test_lab" / "stt_bench"


def _load(name: str) -> np.ndarray:
    with wave.open(str(CLIPS / name), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def test_pick_language_keeps_supported_detection():
    assert pick_language("en", {"en": 0.9, "ar": 0.05}) == "en"
    assert pick_language("ar", {"ar": 0.8, "en": 0.1}) == "ar"


def test_pick_language_maps_other_languages_to_the_likelier_of_en_ar():
    assert pick_language("fr", {"fr": 0.6, "en": 0.3, "ar": 0.1}) == "en"
    assert pick_language("fa", {"fa": 0.5, "ar": 0.4, "en": 0.1}) == "ar"
    assert pick_language("fr", {}) == "en"


@pytest.mark.skipif(not (CLIPS / "v0_s02.wav").exists(), reason="benchmark clips not generated")
def test_transcribes_real_speech_locally():
    from stt.transcriber import transcribe_audio

    text, lang, avg_logprob, no_speech = asyncio.run(
        transcribe_audio(_load("v0_s02.wav"), already_preprocessed=True)
    )
    assert lang == "en"
    words = text.lower()
    assert "timer" in words and ("12" in words or "twelve" in words)
    assert avg_logprob is not None and no_speech is not None


def test_silence_returns_nothing():
    from stt.transcriber import transcribe_audio

    text, lang, _, _ = asyncio.run(transcribe_audio(np.zeros(16000, dtype=np.float32), already_preprocessed=True))
    assert text is None


def test_whisper_prompt_echoes_are_dropped_and_real_words_kept(monkeypatch):
    # Live: "Sam, MBUDDY" x23 and MBUDDY's own last reply were transcribed
    # from noise and twice answered as the user (L6).
    from stt.transcriber import prompt_echo
    last = "[NORMAL] No audio is running. Want me to check again?"
    from core import ui_settings   # the user named Sam in Settings: their name is in the speech hints
    monkeypatch.setattr(ui_settings, "personalize", lambda t, fallback="": t.replace("USER_NAME", "Sam"))
    for echo in ["Sam, MBUDDY", "Sam.", "Sam, timer, remind me, reminder", "No audio is running.",
                 "no audio is running want me to check"]:
        assert prompt_echo(echo, last), echo
    for real in ["Hey buddy, what time is it?", "Hey buddy, weather?", "Hey buddy.", "Volume.", "set a timer",
                 "Remind me to call my mom", "No.", "Is audio running?"]:
        assert not prompt_echo(real, last), real
