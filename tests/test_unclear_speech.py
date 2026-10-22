"""Unclear speech goes to the brain; only garbage is dropped (Phase 2).

Live case, 2026-09-26: the user asked "Amazing, can you laugh?", local Whisper
heard "Amazing, can you law?" at avg_logprob -0.88, and the old -0.75 floor
dropped it for a canned "that came in messy. Repeat it clean?". A language
model can recover the meaning from context, so middling confidence is passed
on with a note instead.
"""
import stt.local_whisper as lw
from brain.llm_router import user_content_for_turn
from stt.language_detect import transcript_confidence


def conf(text, lp=-0.3, ns=0.05, dur=2.0):
    return transcript_confidence(text, duration_s=dur, rms=0.05, avg_logprob=lp, no_speech_prob=ns)


def test_middling_confidence_is_uncertain_not_dropped():
    assert conf("Amazing, can you law?", lp=-0.88) == "uncertain"
    assert conf("Can you love?", lp=-0.8) == "uncertain"


def test_clear_speech_is_ok():
    assert conf("Hey buddy, how are you?", lp=-0.3) == "ok"


def test_garbage_is_still_dropped():
    assert conf("I'm very hungry. Me too.", lp=-1.4) == "garbage"
    assert conf("yeah", lp=-0.3, ns=0.8) == "garbage"          # mostly non-speech
    assert conf("the the the the the the the the", lp=-0.5) == "garbage"


def test_unclear_turn_carries_a_note_for_the_model():
    plain = user_content_for_turn("can you law", "en", heard_unclearly=False)
    noted = user_content_for_turn("can you law", "en", heard_unclearly=True)
    assert plain in noted and noted != plain
    assert "misheard" in noted.lower()


def test_whisper_gets_conversation_context_and_hotwords(monkeypatch):
    seen = {}

    class Info:
        language = "en"
        all_language_probs = [("en", 0.99)]

    class Seg:
        text, avg_logprob, no_speech_prob = "hello", -0.2, 0.01

    def fake_decode(model, audio, language, prompt, hotwords=None):
        seen.update(prompt=prompt, hotwords=hotwords)
        return [Seg()], Info()

    import numpy as np
    monkeypatch.setattr(lw, "get_model", lambda: object())
    monkeypatch.setattr(lw, "_decode", fake_decode)
    lw.transcribe(np.ones(1600, dtype=np.float32), prompt="Sam, MBUDDY", context="Want me to laugh again?")
    from core.ui_settings import personalize   # the name in the user's settings stands in for the placeholder
    assert personalize("Sam") in seen["prompt"] and "laugh again" in seen["prompt"]
    assert "timer" in seen["hotwords"].lower()


def test_unclear_arabic_is_noise_unless_we_are_speaking_arabic():
    # Live: an Arabic hallucination at lp -0.95 was passed on as "unclear"
    # and the model echoed it back in Arabic, mid English conversation.
    from stt.language_detect import settle_confidence

    assert settle_confidence("uncertain", detected_lang="ar", conversation_lang="en") == "garbage"
    assert settle_confidence("uncertain", detected_lang="ar", conversation_lang="ar") == "uncertain"
    assert settle_confidence("uncertain", detected_lang="en", conversation_lang="en") == "uncertain"
    assert settle_confidence("ok", detected_lang="ar", conversation_lang="en") == "ok"
