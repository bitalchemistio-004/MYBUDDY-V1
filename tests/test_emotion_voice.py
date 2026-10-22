"""Emotion that reaches the voice (user, S17: "the new TTS has no tone variations").

Harness 2026-09-26: 18 of 22 replies were voiced Focused, because the
cognitive default overruled every warmer tag the model chose.
"""
from brain.emotion_authority import validate_emotion_decision


def test_gentle_feelings_reach_the_voice():
    assert validate_emotion_decision("Focused", "Happy").final_emotion == "Happy"
    assert validate_emotion_decision("Normal", "Worried").final_emotion == "Worried"
    assert validate_emotion_decision("Normal", "Sad").final_emotion == "Sad"


def test_big_swings_are_still_blocked():
    assert validate_emotion_decision("Focused", "Sleepy").final_emotion == "Focused"
    assert validate_emotion_decision("Normal", "Furious").final_emotion == "Normal"
    assert validate_emotion_decision("Focused", "Sad").final_emotion == "Focused"


def test_pitch_follows_the_emotion_and_stays_close_to_the_voice():
    from tts.speaker import emotion_pitch_factor, output_resample_ratio
    assert emotion_pitch_factor("Normal") == 1.0
    assert 1.0 < emotion_pitch_factor("Happy") <= 2 ** (2 / 12)
    assert 2 ** (-2 / 12) <= emotion_pitch_factor("Sad") < 1.0
    assert output_resample_ratio(1.0) == (147, 160)


def test_each_feeling_has_its_own_voice_colour():
    import numpy as np
    from tts.local_tts import voice_for_emotion
    assert voice_for_emotion("Normal") == "af_heart"
    happy, sad = voice_for_emotion("Happy"), voice_for_emotion("Sad")
    assert happy.shape == sad.shape and not np.allclose(happy, sad)


def test_a_pitched_line_keeps_its_pace():
    import asyncio
    from tts.speaker import _synthesize_local
    line = "That sounds like a really good idea, let's do it."
    normal = asyncio.run(_synthesize_local(line, "en", "Normal"))
    happy = asyncio.run(_synthesize_local(line, "en", "Happy"))  # +10% rate, higher pitch
    ratio = normal.size / happy.size
    assert 1.0 < ratio < 1.25, ratio  # faster by about its rate, not by the pitch shift too
