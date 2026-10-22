"""
A branch of the production STT stage for recorded WAVs.

This calls the project's own `stt.transcriber.transcribe_audio` — the same
function the live pipeline uses (DSP -> silence trim -> Groq Whisper) — so a
transcript here reflects what production would hear from the same audio. It
does NOT include the live-only stages around it (VAD, hallucination filters,
dedup, language policy); the lab isolates the STT stage on purpose.

The production stack is imported lazily and in a worker thread: it takes
~15 s to import (sentence-transformers, faiss, ...), and the lab UI should be
usable in the meantime.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SOURCES = ("auto", "left", "right", "mix", "loudest")
LANGUAGES = {"auto": None, "en": "en", "ar": "ar"}     # MBUDDY supports Arabic and English only

_loader: asyncio.Future | None = None
_state = {"state": "idle", "detail": "not loaded yet", "groq_key": None}


def status() -> dict:
    return dict(_state)


def _load():
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    os.chdir(PROJECT_ROOT)                      # core.config reads ./.env relative to cwd
    from core import config
    from stt.transcriber import transcribe_audio
    return transcribe_audio, bool(config.GROQ_API_KEY)


def start_loading() -> None:
    """Begin importing the production STT stack in the background (idempotent)."""
    global _loader
    if _loader is not None:
        return
    _state.update(state="loading", detail="importing production STT stack…")

    async def run():
        try:
            fn, has_key = await asyncio.to_thread(_load)
            _state.update(state="ready" if has_key else "no_key",
                          detail="ready" if has_key else "GROQ_API_KEY is not set — transcription will fail",
                          groq_key=has_key)
            return fn
        except Exception as exc:                    # import failure must be visible, not swallowed
            _state.update(state="unavailable", detail=f"{type(exc).__name__}: {exc}")
            raise

    _loader = asyncio.ensure_future(run())


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """WAV -> int16 array shaped (frames, channels), plus the sample rate."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("only 16-bit PCM WAVs are supported")
        ch, rate = w.getnchannels(), w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return pcm.reshape(-1, ch), rate


def pick_source(pcm: np.ndarray, source: str) -> tuple[np.ndarray, str]:
    """
    Reduce (frames, channels) to mono float32 in [-1, 1] and describe how.

    `loudest` mirrors what the production firmware does (per-sample pick of the
    louder slot); `mix` is the plain average of the two mics.
    """
    if pcm.shape[1] == 1:
        return pcm[:, 0].astype(np.float32) / 32768.0, "mono (single microphone)"
    l, r = pcm[:, 0].astype(np.int32), pcm[:, 1].astype(np.int32)
    if source == "left":
        out, how = l, "left channel"
    elif source == "right":
        out, how = r, "right channel"
    elif source == "loudest":
        out, how = np.where(np.abs(r) > np.abs(l), r, l), "louder mic per sample (as production firmware)"
    else:
        out, how = (l + r) // 2, "average of left and right"
    return out.astype(np.float32) / 32768.0, how


async def transcribe_array(audio: np.ndarray, how: str = "in-memory clip", language: str = "auto",
                           apply_dsp: bool = True, rate: int = 16000) -> dict:
    """Run mono float32 audio in [-1, 1] through the production STT stage."""
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {tuple(LANGUAGES)}")
    if rate != 16000:
        raise ValueError(f"expected 16 kHz audio, got {rate} Hz")
    if audio.size == 0:
        raise ValueError("recording contains no audio")
    start_loading()
    transcribe_audio = await _loader                # raises the import error if loading failed
    t0 = time.perf_counter()
    text, lang, avg_logprob, no_speech = await transcribe_audio(
        audio, already_preprocessed=not apply_dsp, language_hint=LANGUAGES[language]
    )
    return {
        "text": text,
        "language": lang,
        "avg_logprob": None if avg_logprob is None else round(avg_logprob, 3),
        "no_speech_prob": None if no_speech is None else round(no_speech, 3),
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "audio": {"source": how, "duration_s": round(audio.size / rate, 2), "production_dsp": apply_dsp},
        # transcribe_audio reports every failure (rate limit, API error, silence) as "no text";
        # say so instead of guessing which one it was.
        "note": None if text else
                "No text was returned. Production's STT stage returns nothing for silence, "
                "rate-limit and API errors alike — check the server console for the reason.",
    }


async def transcribe(path: Path, source: str = "auto", language: str = "auto", apply_dsp: bool = True) -> dict:
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {tuple(LANGUAGES)}")
    pcm, rate = read_wav(path)
    if rate != 16000:
        raise ValueError(f"expected 16 kHz audio, file is {rate} Hz")
    audio, how = pick_source(pcm, "mix" if source == "auto" else source)
    return await transcribe_array(audio, how, language, apply_dsp, rate)
