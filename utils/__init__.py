"""
utils — Logging and shared text/audio helper functions.
"""
from utils.logger import ok, info, warn, error, debug, mem, tts, mic, brain, ws
from utils.diagnostics import (
    normalize_text, text_fingerprint, text_similarity,
    tokenize_text, has_phrase, audio_rms, is_silent,
)

__all__ = [
    # Logger
    "ok", "info", "warn", "error", "debug", "mem", "tts", "mic", "brain", "ws",
    # Text helpers
    "normalize_text", "text_fingerprint", "text_similarity",
    "tokenize_text", "has_phrase",
    # Audio helpers
    "audio_rms", "is_silent",
]
