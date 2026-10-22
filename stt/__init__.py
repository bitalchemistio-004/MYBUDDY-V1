"""
stt — Speech-to-text: transcription and language/text utilities.
"""
from stt.transcriber import transcribe_audio
from stt.language_detect import (
    is_arabic, is_english, is_mixed,
    normalize_text, text_fingerprint, text_similarity,
    tokenize_text, has_phrase, should_wake, is_final_goodbye,
    is_goodbye_confirmation, is_soft_goodbye, is_probable_hallucination,
    is_incomplete_transcript, is_unreliable_transcript,
)
from stt.language_policy import map_whisper_lang, infer_language, voice_for_lang

__all__ = [
    # Transcription
    "transcribe_audio",
    # Language detection & text helpers
    "is_arabic", "is_english", "is_mixed", "map_whisper_lang",
    "normalize_text", "text_fingerprint", "text_similarity",
    "tokenize_text", "has_phrase", "should_wake", "is_final_goodbye",
    "is_goodbye_confirmation", "is_soft_goodbye", "is_probable_hallucination",
    "is_incomplete_transcript", "is_unreliable_transcript",
]
