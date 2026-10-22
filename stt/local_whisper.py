"""
stt/local_whisper.py - on-device speech-to-text with faster-whisper.

One Whisper model per process, loaded on first use and kept for the life of
the process. It is never freed on purpose: releasing a CUDA CTranslate2 model
aborts the interpreter on this machine (0xC0000409, found while
benchmarking; see docs/audit/AUDIT_LOG.md 2.2).

Model and device come from the environment:
    STT_MODEL   (default "small")      faster-whisper size or path
    STT_DEVICE  (default "cuda")       falls back to CPU int8 if CUDA fails

MBUDDY hears English and Arabic only. Whisper sometimes labels short or
accented English as another language; such a result is re-decoded as
whichever of the two supported languages Whisper found likelier.
"""
from __future__ import annotations

import os
import re
import contextlib
import time
import threading
from dataclasses import dataclass

import numpy as np

import utils.logger as log
from stt.cuda_libs import register_cuda_dll_dirs

SUPPORTED = ("en", "ar")

_model = None
_model_lock = threading.Lock()
_decode_lock = threading.Lock()  # one decode at a time on the GPU
_device = ""                      # "cuda/int8_float16" or "cpu/int8", once loaded
_holder = ""                      # what the decoder is busy with, for the wait log


@contextlib.contextmanager
def _decoder(kind: str):
    """The decode lock, timed. STT once took 40 s for 8.8 s of audio while
    the median is 0.7 s (M2 S4b): the wait for the decoder is logged apart
    from the decode, with what held it, so the log says which it was."""
    global _holder
    started, busy_with = time.perf_counter(), _holder
    with _decode_lock:
        waited = time.perf_counter() - started
        _holder = kind
        try:
            yield waited, busy_with
        finally:
            _holder = ""


@dataclass
class Transcript:
    text: str
    language: str
    avg_logprob: float | None
    no_speech_prob: float | None


def pick_language(detected: str, probs: dict[str, float]) -> str:
    """The detected language if supported, else the likelier of English/Arabic."""
    if detected in SUPPORTED:
        return detected
    return max(SUPPORTED, key=lambda code: probs.get(code, 0.0))


def _load(name: str, **kwargs):
    """Use the cached model without asking the internet; download only when missing."""
    from faster_whisper import WhisperModel
    from huggingface_hub.errors import LocalEntryNotFoundError

    try:
        return WhisperModel(name, local_files_only=True, **kwargs)
    except LocalEntryNotFoundError:
        log.info(f"Whisper {name} is not cached yet; downloading it")
        return WhisperModel(name, **kwargs)


def _speech_on_cpu() -> bool:
    """The user chose to keep the graphics card free (Settings, Models): hearing then runs on the processor."""
    try:
        from core import ui_settings
        return ui_settings.get("speech_device") == "cpu"
    except Exception:
        return False


def get_model():
    global _model
    with _model_lock:
        if _model is not None:
            return _model
        name = os.getenv("STT_MODEL", "small")
        device = os.getenv("STT_DEVICE") or ("cpu" if _speech_on_cpu() else "cuda")
        global _device
        loading = time.perf_counter()
        if device == "cuda":
            register_cuda_dll_dirs()
            try:
                _model = _load(name, device="cuda", compute_type="int8_float16")
                _device = "cuda/int8_float16"
                log.info(f"Local STT ready: whisper {name} on GPU ({time.perf_counter() - loading:.1f}s to load)")
                return _model
            except Exception as exc:
                log.warn(f"Local STT could not use the GPU ({exc}); falling back to CPU")
        _model = _load(name, device="cpu", compute_type="int8")
        _device = "cpu/int8"
        log.info(f"Local STT ready: whisper {name} on CPU ({time.perf_counter() - loading:.1f}s to load)")
        return _model


def _decode(model, audio: np.ndarray, language: str | None, prompt: str | None, hotwords: str | None = None,
            beam_size: int = 5):
    segments, info = model.transcribe(
        audio,
        language=language,
        beam_size=beam_size,
        vad_filter=True,
        condition_on_previous_text=False,
        initial_prompt=prompt or None,
        hotwords=hotwords or None,
    )
    return list(segments), info


def build_prompt(names: str | None, context: str | None) -> str | None:
    """
    Whisper's prompt: known names, then the last thing MBUDDY said. A reply
    is usually what the user answers, so its words bias Whisper toward what
    fits ("laugh" after "want me to laugh?") rather than a near-homophone.
    """
    tail = re.sub(r"\[[^\]]*\]", "", context or "").strip()[-200:]
    parts = [p for p in ((names or "").strip(), tail) if p]
    return ". ".join(parts) or None


def wake_check(audio: np.ndarray) -> str:
    """
    Cheap listen while asleep: only "is the wake phrase in here?" matters, so
    one greedy English pass (no language detection, no second decode, no
    conversation prompt). Room talk and TV cost a fraction of a full decode;
    the full transcription runs only once the wake phrase is heard.
    """
    if audio.size == 0:
        return ""
    from core.config import STT_HOTWORDS
    from core.ui_settings import personalize

    model = get_model()
    with _decoder("wake check") as (waited, busy_with):
        decoding = time.perf_counter()
        segments, _ = _decode(model, audio, "en", None, personalize(STT_HOTWORDS, fallback=""), beam_size=1)
        _log_timing("wake check", waited, busy_with, time.perf_counter() - decoding, audio, "")
    return " ".join(s.text.strip() for s in segments).strip()


def transcribe(
    audio: np.ndarray,
    language_hint: str | None = None,
    prompt: str | None = None,
    context: str | None = None,
) -> Transcript | None:
    """Blocking transcription of a 16 kHz mono float32 utterance."""
    if audio.size == 0:
        return None
    from core.config import STT_HOTWORDS

    model = get_model()
    hint = language_hint if language_hint in SUPPORTED else None
    from core.ui_settings import personalize  # the user's name helps Whisper spell it
    full_prompt = personalize(build_prompt(prompt, context) or "", fallback="") or None
    hotwords = personalize(STT_HOTWORDS, fallback="")
    with _decoder("transcribe") as (waited, busy_with):
        decoding = time.perf_counter()
        segments, info = _decode(model, audio, hint, full_prompt, hotwords)
        language = hint or pick_language(info.language, dict(info.all_language_probs or []))
        again = ""
        if language != info.language:  # a second full decode in the language MBUDDY uses
            again = f"{info.language}->{language}"
            segments, info = _decode(model, audio, language, full_prompt, hotwords)
        _log_timing("transcribe", waited, busy_with, time.perf_counter() - decoding, audio, again)

    text = " ".join(s.text.strip() for s in segments).strip()
    if not text:
        return None
    logprobs = [s.avg_logprob for s in segments]
    return Transcript(
        text=text,
        language=language,
        avg_logprob=sum(logprobs) / len(logprobs) if logprobs else None,
        no_speech_prob=max((s.no_speech_prob for s in segments), default=None),
    )


def _log_timing(kind: str, waited: float, busy_with: str, decoded: float, audio: np.ndarray, again: str) -> None:
    log.info(f"[STT] {kind}: waited {waited:.2f}s for the decoder" + (f" (it was busy: {busy_with})" if busy_with else "")
             + f", decoded {decoded:.2f}s" + (f" twice (language {again})" if again else "")
             + f" for {audio.size / 16000:.1f}s audio on {_device or 'unknown'}")
