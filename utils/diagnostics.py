"""
utils/diagnostics.py — Text and audio utility functions.
Pure functions with no side-effects; importable by any module.
"""
import re
import hashlib
import numpy as np


# ── Text helpers ────────────────────────────────────────────

def normalize_text(text: str) -> str:
    return " ".join(text.lower().strip().split())


def text_fingerprint(text: str) -> str:
    return hashlib.md5(normalize_text(text).encode()).hexdigest()


def text_similarity(a: str, b: str) -> float:
    """Jaccard similarity on word sets."""
    if not a or not b:
        return 0.0
    sa = set(normalize_text(a).split())
    sb = set(normalize_text(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def tokenize_text(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower(), flags=re.UNICODE)


def has_phrase(text: str, phrases: tuple, max_extra_tokens: int = 2) -> bool:
    """Check if any phrase occurs inside text (token-level, within extra-token budget)."""
    text_tokens = tokenize_text(text)
    if not text_tokens:
        return False
    for phrase in phrases:
        phrase_tokens = tokenize_text(phrase)
        if not phrase_tokens or len(phrase_tokens) > len(text_tokens):
            continue
        for idx in range(len(text_tokens) - len(phrase_tokens) + 1):
            if text_tokens[idx : idx + len(phrase_tokens)] != phrase_tokens:
                continue
            extra_tokens = len(text_tokens) - len(phrase_tokens)
            if extra_tokens <= max_extra_tokens:
                return True
    return False


# ── Audio helpers ────────────────────────────────────────────

def audio_rms(audio_np: np.ndarray) -> float:
    """DC-compensated RMS of an audio array."""
    if audio_np.size == 0:
        return 0.0
    centered = audio_np - float(np.mean(audio_np))
    return float(np.sqrt(np.mean(centered ** 2)))


def is_silent(chunk: np.ndarray, noise_floor: float = 0.035) -> bool:
    return audio_rms(chunk) < noise_floor * 1.5
