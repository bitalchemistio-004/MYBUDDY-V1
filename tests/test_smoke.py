"""
tests/test_smoke.py — Import-chain smoke test.

Verifies that every MBUDDY package can be imported cleanly without raising
any ImportError, NameError, or missing-dependency errors.

Intentionally does NOT make network calls, open files, or require hardware.

Run with:
    .venv\\Scripts\\python.exe -m pytest tests/test_smoke.py -v
"""


def test_core_config_imports():
    """core.config must be importable with zero side-effects."""
    from core import config
    assert config.SAMPLE_RATE == 16000
    assert config.POST_TTS_STT_COOLDOWN <= 0.25
    assert config.GROQ_CHAT_MODEL
    assert isinstance(config.WAKE_PHRASES, tuple)
    assert isinstance(config.STT_HALLUCINATIONS, set)
    assert isinstance(config.HALLUCINATION_SHORT_PHRASES, set)


def test_brain_personality_imports():
    from brain.personality import (
        EMOTION_TAG_TO_STATE, STATE_TO_TAG,
        get_hard_constraint,
        strip_emotion_tags, extract_emotion_state,
    )
    assert "[NORMAL]" in EMOTION_TAG_TO_STATE
    assert "Normal" in STATE_TO_TAG
    assert get_hard_constraint("Normal")
    assert strip_emotion_tags("[HAPPY] hello") == "hello"
    assert extract_emotion_state("[HAPPY] hey") == "Happy"


def test_stt_language_detect_imports():
    from stt.language_detect import (
        is_arabic, is_english, normalize_text, has_phrase, should_wake, is_final_goodbye, is_goodbye_confirmation,
        is_soft_goodbye, is_probable_hallucination,
        is_incomplete_transcript, is_unreliable_transcript, text_similarity,
        contains_arabizi, is_arabizi,
    )
    assert is_arabic("مرحبا")
    assert is_english("hello")
    assert contains_arabizi("kifak today")
    assert contains_arabizi("مرحبا kifak")
    assert is_arabizi("shou badde")
    assert not is_arabizi("مرحبا hello")
    assert not contains_arabizi("debug the esp32 in 2026")
    assert normalize_text("  Hello  World  ") == "hello world"
    assert has_phrase("hey buddy how are you", ("hey buddy",), max_extra_tokens=4)
    assert should_wake("hey buddy")
    assert should_wake("hey buddy wake up")
    assert should_wake("hi buddy")
    assert should_wake("يا بادي")
    assert should_wake("هاي بادي")
    assert not should_wake("hey bud")
    assert not should_wake("buddy")
    assert not should_wake("hey")
    assert not should_wake("what time is it")
    from stt.language_detect import is_wake_only
    assert is_wake_only("hey buddy")
    assert not is_wake_only("do you know who i am")
    assert is_probable_hallucination("hey")
    assert is_probable_hallucination("hey.")
    assert not is_probable_hallucination("hey buddy")
    assert not is_probable_hallucination("مرحبا كيفك")
    assert is_unreliable_transcript("goose", duration_s=0.4, rms=0.06)
    assert is_incomplete_transcript("okay, what is the result that...")
    assert is_unreliable_transcript("okay, what is the result that...", duration_s=1.2, rms=0.04)
    assert not is_incomplete_transcript("what is the result")
    assert is_final_goodbye("go to sleep")
    assert is_final_goodbye("okay good night buddy")
    assert is_final_goodbye("تصبح على خير")
    assert not is_final_goodbye("did you have a good night at the gym")
    assert not is_final_goodbye("sleep now is a command in the docs")
    assert is_soft_goodbye("see you later")
    assert is_soft_goodbye("i'm going now")
    assert not is_soft_goodbye("yalla let's debug")
    assert is_goodbye_confirmation("yes")
    assert not is_goodbye_confirmation("yes tell me more")
    assert not is_unreliable_transcript("hey buddy", duration_s=0.8, rms=0.08)
    from stt.language_policy import should_offer_language, offer_message
    assert not should_offer_language("ar", {"en", "ar"})
    assert not should_offer_language("ar", {"en"})
    assert not should_offer_language("en", {"ar"})
    assert not should_offer_language("mixed", {"en", "ar"})
    # MBUDDY speaks English and Arabic only; other languages are handled as
    # unsupported (offer_message below), never offered (stt/language_policy.py).
    assert not should_offer_language("fr", {"en", "ar"})
    assert "French" in offer_message("fr")
    assert "only speak English or Arabic" in offer_message("fr")
    from stt.language_policy import llm_language_block, reply_matches_language, user_message_for_lang, voice_for_reply
    assert reply_matches_language("[HAPPY] مرحبا يا صديقي", "ar")
    assert not reply_matches_language("[SURPRISED] Ah, ya Sam, Taha", "ar")
    # Dialect is a style choice, not a wrong language (stt/language_policy.py):
    # any real Arabic reply passes when the conversation is Arabic.
    assert reply_matches_language("[خلاص] ما بيكونش بس عاوزك تروح", "ar")
    assert reply_matches_language("[SAD] ما بيكونش بس عاوزك تروح", "ar")
    assert "ar-" in voice_for_reply("[SURPRISED] طه أخوك", "ar")
    from stt.language_policy import infer_language
    assert infer_language("check this song", "ar") == "en"
    assert infer_language("hey buddy", "ar") == "en"
    assert infer_language("هل تعلم", "en") == "ar"
    assert infer_language("kifak", "en") == "arabizi"
    assert infer_language("a clio!", "Spanish") == "es"
    assert infer_language("hello buddy", "Spanish") == "en"
    assert infer_language("مرحبا kifak", "ar") == "arabizi"
    from stt.language_policy import has_non_english_latin_marker
    assert not has_non_english_latin_marker("a clio!")
    assert has_non_english_latin_marker("hola amigo")
    from stt.language_policy import is_clear_english_transcript, resolve_conversation_lang
    assert is_clear_english_transcript("check this song")
    assert not is_clear_english_transcript("ame speaks feroz")
    assert not is_clear_english_transcript("tarif fairuz")
    assert resolve_conversation_lang("هل تعلم", "en", {"en"}, "en") == "ar"
    assert resolve_conversation_lang("hello", "ar", {"ar"}, "ar") == "en"
    assert resolve_conversation_lang("check this song", "ar", {"ar"}, "ar") == "en"
    # The reply follows the script actually spoken; Whisper's label is only a
    # tie-breaker, so an English sentence gets an English reply.
    assert resolve_conversation_lang("do you like arabic songs?", "Arabic", {"en", "ar"}, "en") == "en"
    # Latin-script sentences get English even when Whisper says "Arabic"; a
    # mostly-Arabic sentence in an Arabic conversation stays Arabic.
    assert resolve_conversation_lang("what did you eat today? banana?", "Arabic", {"en", "ar"}, "en") == "en"
    assert resolve_conversation_lang("ame speaks feroz", "Arabic", {"en", "ar"}, "en") == "en"
    assert resolve_conversation_lang("tarif fairuz", "Arabic", {"en", "ar"}, "en") == "en"
    assert resolve_conversation_lang("شو يعني banana", "Arabic", {"en", "ar"}, "ar") == "ar"
    assert resolve_conversation_lang("kifak", "Arabic", {"en", "ar"}, "ar") == "en"
    assert reply_matches_language("[HAPPY] It means banana.", "mixed")
    assert not reply_matches_language("[SURPRISED] Hola! Ana msh fahem, what's going on?", "en")
    assert not reply_matches_language("[SURPRISED] Vamos a hablar en inglés, amigo!", "en")
    assert not reply_matches_language("[HAPPY] يعني banana.", "mixed")
    from brain.fallbacks import fallback_reply
    assert reply_matches_language(fallback_reply("llm_timeout", "Focused", lang="ar"), "ar")
    assert "reply ENTIRELY and ONLY in English" in llm_language_block("mixed")
    # The user's words are kept exactly as spoken; language direction lives
    # only in the system prompt (llm_language_block).
    assert user_message_for_lang("شو يعني banana", "mixed") == "شو يعني banana"
    assert user_message_for_lang("bonjour", "fr") == "bonjour"
    assert "en-US" in voice_for_reply("[HAPPY] hey! what's up?", "ar")
    assert is_probable_hallucination("thank you for watching")
    assert is_probable_hallucination("transcription by eso")
    assert is_unreliable_transcript("transcription by eso. translation by", duration_s=1.0, rms=0.04)
    assert is_probable_hallucination("transcribe spoken english accurately")
    assert is_probable_hallucination("english language only")
    assert not is_probable_hallucination("let's build something today")
    assert text_similarity("hello world", "hello world") == 1.0
    assert text_similarity("hello", "goodbye") == 0.0


def test_utils_logger_imports():
    from utils.logger import ok, info, warn, mem, tts, debug
    # All logger functions are callable
    for fn in (ok, info, warn, mem, tts, debug):
        assert callable(fn)


def test_utils_diagnostics_imports():
    import numpy as np
    from utils.diagnostics import (
        text_fingerprint, text_similarity,
        audio_rms, is_silent,
    )
    arr = np.zeros(100, dtype=np.float32)
    assert audio_rms(arr) == 0.0
    assert is_silent(arr)
    assert text_fingerprint("hello") == text_fingerprint("hello")
    assert text_similarity("a b c", "b c d") > 0


def test_audio_preprocessing_imports():
    import numpy as np
    from audio.preprocessing import (
        preprocess_audio,
    )
    dummy = np.random.randn(1600).astype(np.float32) * 0.1
    result = preprocess_audio(dummy)
    assert result.shape == dummy.shape
    assert result.dtype == np.float32


def test_audio_vad_imports():
    from audio.vad import energy_is_trivial
    import numpy as np
    silent = np.zeros(320, dtype=np.float32)
    # Near-silent chunk: energy_is_trivial should return True
    assert energy_is_trivial(silent)


def test_brain_memory_context_imports():
    from brain.memory_context import (
        build_tiered_memory_context, build_conversation_system,
    )
    mem = {
        "core_facts": ["Sam is an engineer"],
        "emotional_memories": [],
        "conversation_log": [],
        "inner_log": [],
        "identity_snapshot": "",
    }
    ctx = build_tiered_memory_context(mem)
    assert "Sam" in ctx
    class DummyState:
        current = "Normal"
        energy = 85

    sys = build_conversation_system(DummyState(), mem, "en")
    assert "CURRENT DATE/TIME" in sys
    assert "match the question" in sys


def test_memory_long_term_imports():
    from memory.long_term import load_memory
    # load_memory should return a valid skeleton if file doesn't exist
    m = load_memory.__module__
    assert m == "memory.long_term"



def test_memory_vector_store_imports():
    from memory.vector_store import get_vector_store
    vs = get_vector_store()
    assert vs.search("anything") == []


def test_tts_speaker_imports():
    from tts.speaker import TARGET_SR, BYTES_PER_FRAME, CHANNELS
    from stt.language_policy import voice_for_lang
    assert TARGET_SR == 22050
    assert CHANNELS == 1
    assert BYTES_PER_FRAME == 2
    assert "Neural" in voice_for_lang("en")
    assert "ar-" in voice_for_lang("ar")


def test_tts_pcm_wire_format_is_mono_i16():
    import numpy as np
    from tts.speaker import _build_pcm_bytes, BYTES_PER_FRAME

    samples = np.array([0.25, -0.25], dtype=np.float32)
    pcm = _build_pcm_bytes(samples, fade_in=False, fade_out=False)
    words = np.frombuffer(pcm, dtype=np.int16)

    assert len(pcm) % BYTES_PER_FRAME == 0
    nonzero = words[words != 0]
    assert nonzero.size >= 2
    assert nonzero[0] > 0
    assert nonzero[1] < 0


def test_tts_pcm_volume_scales_output():
    import numpy as np
    from tts.speaker import _build_pcm_bytes

    samples = np.array([0.25, -0.25, 0.5, -0.5], dtype=np.float32)
    normal = np.frombuffer(
        _build_pcm_bytes(samples, fade_in=False, fade_out=False, leader_ms=0, trailer_ms=0),
        dtype=np.int16,
    )
    quiet = np.frombuffer(
        _build_pcm_bytes(samples, fade_in=False, fade_out=False, leader_ms=0, trailer_ms=0, volume=0.35),
        dtype=np.int16,
    )

    assert np.max(np.abs(quiet.astype(np.int32))) < np.max(np.abs(normal.astype(np.int32)))


def test_core_state_manager_imports():
    from core.state_manager import MBUDDYState
    state = MBUDDYState()
    assert state.current == "Normal"
    assert state.energy == 85.0
    # boost() is capped by the hour (circadian ceiling), so at night it could
    # not rise and this failed; pin the ceiling to test the boost itself.
    from unittest import mock
    with mock.patch("core.state_manager.circadian_ceiling", return_value=100.0):
        state.boost(10)
    assert state.energy > 85.0
    result = state.tick()
    assert result in MBUDDYState.STATES


def test_package_versions():
    from core import __version__
    assert __version__.startswith("4.1.0")
