"""
stt/transcriber.py - Speech-to-text, fully on this PC.

Pipeline:
  1. preprocess_audio()  - DC removal, HPF, RMS-AGC, soft gate (skipped when
     the caller already cleaned the audio, e.g. WebRTC capture in the shell)
  2. trim_silence()      - drop leading/trailing silence
  3. local faster-whisper (stt/local_whisper.py), English/Arabic only
  4. Hallucination guard - done by the caller with the returned confidence

This replaced the Groq Whisper API call in the Phase 2 local rebuild; no
audio leaves the machine.
"""
import asyncio
import difflib
import re
import time

import numpy as np

from core.config import STT_WHISPER_PROMPT
from audio.preprocessing import preprocess_audio, trim_silence
from stt import local_whisper
import utils.logger as log


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def prompt_echo(text: str, context: str | None) -> str:
    """
    What a transcript merely repeats, or "". On noise or very quiet audio
    Whisper writes out its own prompt: "the user, MBUDDY" (23 times in one log),
    "the user, timer, remind me, reminder", or MBUDDY's last reply, which was
    then answered as if the user had said it (L6).
    """
    from core.config import STT_HOTWORDS
    from core.ui_settings import personalize

    words = _words(text)
    if not words:
        return ""
    # The wake words are never echo material ("Hey buddy, weather?" is real).
    hints = set(_words(personalize(f"{STT_WHISPER_PROMPT}, {STT_HOTWORDS}", fallback=""))) - {"hey", "hi", "buddy"}
    names = set(_words(personalize(STT_WHISPER_PROMPT, fallback="")))
    if words and all(w in hints for w in words) and (len(words) >= 2 or words[0] in names):
        return "Whisper's own hint words"
    reply = " ".join(_words(re.sub(r"\[[^\]]*\]", "", context or "")))
    if reply and len(words) >= 3:
        said = " ".join(words)
        if said in reply or difflib.SequenceMatcher(None, said, reply[-len(said) - 20:]).ratio() >= 0.85:
            return "MBUDDY's last reply"
    return ""


async def transcribe_audio(
    audio_np: np.ndarray,
    *,
    already_preprocessed: bool = False,
    language_hint: str | None = None,
    context: str | None = None,
    asleep: bool = False,
) -> tuple[str | None, str | None, float | None, float | None]:
    """
    Transcribe one 16 kHz mono float32 utterance.

    Returns (text, language_code, avg_logprob, no_speech_prob); text is None
    when nothing was said. The model runs in a worker thread so the event
    loop keeps serving audio while it decodes. `context` is the last thing
    MBUDDY said, used to bias Whisper toward words that fit the conversation.
    """
    if not already_preprocessed:
        audio_np = preprocess_audio(audio_np)
    audio_np = trim_silence(audio_np)

    if audio_np.size == 0:
        return None, "en", None, None

    hint = language_hint if language_hint not in (None, "mixed") else None
    started = time.perf_counter()
    if asleep:
        # Asleep: a cheap pass decides whether this is the wake phrase at all.
        from stt.language_detect import is_wake_only, should_wake
        try:
            heard = await asyncio.to_thread(local_whisper.wake_check, audio_np.astype(np.float32))
        except Exception as e:
            log.error("Local STT error", e)
            return None, "en", None, None
        if not should_wake(heard):
            took = time.perf_counter() - started
            log.info(f"STT asleep, no wake: '{heard[:60]}' ({took:.2f}s for {audio_np.size / 16000:.1f}s audio)")
            return (heard or None), "en", None, None
        if is_wake_only(heard):
            # Just "Hey Buddy": nothing more to get right, skip the full pass.
            log.info(f"STT asleep, woken: '{heard[:60]}' ({time.perf_counter() - started:.2f}s)")
            return heard, "en", None, None
        # Woken with a command: transcribe properly so the command is right.
    try:
        result = await asyncio.to_thread(
            local_whisper.transcribe, audio_np.astype(np.float32), hint, STT_WHISPER_PROMPT, context
        )
    except Exception as e:
        log.error("Local STT error", e)
        return None, "en", None, None

    if result is None:
        log.info(f"STT empty ({time.perf_counter() - started:.2f}s for {audio_np.size / 16000:.1f}s audio)")
        return None, "en", None, None

    echo = prompt_echo(result.text, context)
    if echo:
        log.info(f"STT dropped, it repeats {echo}: '{result.text[:60]}'")
        return None, "en", None, None
    conf = f" lp={result.avg_logprob:.2f}" if result.avg_logprob is not None else ""
    took = time.perf_counter() - started
    log.info(f"STT [{result.language}]: '{result.text[:60]}'{conf} ({took:.2f}s for {audio_np.size / 16000:.1f}s audio)")
    return result.text, result.language, result.avg_logprob, result.no_speech_prob
