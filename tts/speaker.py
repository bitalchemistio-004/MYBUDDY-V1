"""
tts/speaker.py — TTS generation and PCM streaming via WebSocket.

Pipeline:
  Kokoro on this PC (tts/local_tts.py, voice af_heart, expression clips)
  → resample 24 kHz → 22.05 kHz → loud peak normalize + cosine fades
  → steady real-time stream

The Phase 2 rebuild replaced the cloud edge-tts voices; nothing leaves the
machine. Kokoro has speed but no pitch or emotion control, so each emotion
gets its pace and pitch from PROSODY_MAP (pitch through the resample step)
and its tone from a voice blend (local_tts.EMOTION_BLEND).
"""
import asyncio
import os
import numpy as np
from scipy.signal import resample_poly

from core.status import runtime_status
from tts import local_tts
from tts.expressions import voice_ready
import utils.logger as log

TARGET_SR         = 22050
CHANNELS          = 1
BYTES_PER_FRAME   = 2 * CHANNELS
BYTES_PER_SEC     = TARGET_SR * BYTES_PER_FRAME
CHUNK_SIZE        = 1024          # 1024 B/frame (~23ms of 22.05kHz mono audio)
PREROLL_BYTES     = 4 * 1024      # Smooth 4KB preroll (~80ms) to prevent ESP32 ring buffer overflow
DRAIN_WAIT_S      = 0.10
TAIL_WAIT_S       = 0.10
TTS_TARGET_PEAK   = 1.0
TTS_MAX_GAIN      = 24.0
TTS_PCM_BOOST     = 2.30   # MAX98357A: wire GAIN pin to 3.3V for hardware loudness
FADE_MS           = 12
FIRST_LEADER_MS   = 90
JOIN_LEADER_MS    = 20
TRAILER_MS        = 20
TTS_SYNTH_TIMEOUT_S = float(os.getenv("MBUDDY_TTS_SYNTH_TIMEOUT_S", "4.5"))
TTS_SYNTH_ATTEMPTS  = int(os.getenv("MBUDDY_TTS_SYNTH_ATTEMPTS", "1"))
TTS_CACHE_MAX       = 32

# How long to leave after a chunk, by emotion, in milliseconds. A tired or
# awed MBUDDY leaves the sentence hanging; an excited one runs straight on.
# These replace a flat TRAILER_MS on every chunk, which is what made the
# cadence metronomic no matter what was being said.
EMOTION_PAUSE_MS = {
    "Sleepy":      340,
    "Awe":         300,
    "Sad":         280,
    "Worried":     240,
    "Suspicious":  230,
    "Skeptic":     220,
    "Unimpressed": 210,
    "Frustrated":  200,
    "Normal":      170,
    "Focused":     140,
    "Happy":       150,
    "Scared":      130,
    "Surprised":   120,
    "Annoyed":     120,
    "Angry":       110,
    "Squint":      170,
    "Furious":      90,
    "Glee":         90,
}

# Multipliers for how the sentence actually ended. A question leaves room for
# an answer; a comma barely breaks at all.
ENDING_PAUSE_SCALE = (
    ("...", 1.6),
    ("?",   1.35),
    ("!",   0.8),
    (".",   1.0),
    (",",   0.5),
    (";",   0.7),
    (":",   0.7),
)

PAUSE_MIN_MS = 60
PAUSE_MAX_MS = 600


def pause_after(text: str, emotion: str = "Normal") -> int:
    """
    How long to stay silent after speaking this chunk.

    Combines MBUDDY's current emotion with the punctuation the sentence
    ended on, so the rhythm of a reply follows its content instead of a
    fixed metronome.

    Args:
        text: the chunk just spoken
        emotion: MBUDDY's emotion while speaking it

    Returns:
        int: milliseconds of silence, clamped to a sane range
    """
    base = EMOTION_PAUSE_MS.get(emotion, EMOTION_PAUSE_MS["Normal"])
    stripped = (text or "").strip()

    scale = 1.0
    for ending, factor in ENDING_PAUSE_SCALE:
        if stripped.endswith(ending):
            scale = factor
            break

    # A vocalization on its own is a beat, not a sentence — hold it slightly.
    if stripped.rstrip(".!?").lower() in {"hmm", "haha", "heh", "ah", "ooh", "uh", "erm"}:
        scale *= 1.4

    return int(max(PAUSE_MIN_MS, min(PAUSE_MAX_MS, base * scale)))


_TTS_PCM_CACHE: dict[tuple[str, str, str, int, int, float], bytes] = {}
_EDGE_TTS_LOCK = asyncio.Semaphore(1)  # one synthesis at a time

PROSODY_MAP = {
    "Normal":      {"rate": "+0%",  "pitch": "+0Hz"},
    "Happy":       {"rate": "+10%", "pitch": "+2Hz"},
    "Glee":        {"rate": "+25%", "pitch": "+5Hz"},
    "Sad":         {"rate": "-15%", "pitch": "-3Hz"},
    "Angry":       {"rate": "+15%", "pitch": "-5Hz"},
    "Furious":     {"rate": "+25%", "pitch": "-8Hz"},
    "Worried":     {"rate": "-5%",  "pitch": "+3Hz"},
    "Scared":      {"rate": "+10%", "pitch": "+8Hz"},
    "Focused":     {"rate": "+5%",  "pitch": "-2Hz"},
    "Annoyed":     {"rate": "-5%",  "pitch": "-4Hz"},
    "Surprised":   {"rate": "+20%", "pitch": "+10Hz"},
    "Skeptic":     {"rate": "-10%", "pitch": "-2Hz"},
    "Suspicious":  {"rate": "-5%",  "pitch": "-3Hz"},
    "Sleepy":      {"rate": "-30%", "pitch": "-5Hz"},
    "Awe":         {"rate": "-20%", "pitch": "+2Hz"},
    "Frustrated":  {"rate": "-10%", "pitch": "-5Hz"},
    "Unimpressed": {"rate": "-15%", "pitch": "-6Hz"},
    "Squint":      {"rate": "-5%",  "pitch": "-2Hz"},
}


def _cosine_fade(
    samples_f: np.ndarray,
    sr: int,
    ms: int = FADE_MS,
    fade_in: bool = True,
    fade_out: bool = True,
) -> np.ndarray:
    n = int(sr * ms / 1000.0)
    if len(samples_f) <= n * 2:
        return samples_f
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, n, dtype=np.float32))
    out = samples_f.copy()
    if fade_in:
        out[:n] *= ramp
    if fade_out:
        out[-n:] *= ramp[::-1]
    return out


def _normalize_loudness(samples_f: np.ndarray) -> np.ndarray:
    out = samples_f.astype(np.float32, copy=True)
    out -= float(np.mean(out))
    peak = float(np.max(np.abs(out)))
    if peak < 1e-7:
        return out
    gain = min(TTS_MAX_GAIN, TTS_TARGET_PEAK / peak)
    out = np.clip(out * gain, -1.0, 1.0)
    return out.astype(np.float32)


def _build_pcm_bytes(
    samples_f: np.ndarray,
    fade_in: bool = True,
    fade_out: bool = True,
    leader_ms: int = FIRST_LEADER_MS,
    trailer_ms: int = TRAILER_MS,
    volume: float = 1.0,
) -> bytes:
    samples_f = _cosine_fade(samples_f, TARGET_SR, fade_in=fade_in, fade_out=fade_out)
    boosted = np.tanh(samples_f * TTS_PCM_BOOST) / np.tanh(TTS_PCM_BOOST)
    boosted = np.clip(boosted * 0.98 * max(0.0, volume), -1.0, 1.0)
    samples_i16 = (boosted * 32767.0).astype(np.int16)

    leader  = np.zeros(int(TARGET_SR * leader_ms / 1000.0) * CHANNELS, dtype=np.int16)
    trailer = np.zeros(int(TARGET_SR * trailer_ms / 1000.0) * CHANNELS, dtype=np.int16)
    return np.concatenate([leader, samples_i16, trailer]).tobytes()


async def _paced_stream(ws, pcm_bytes: bytes) -> None:
    """Stream websocket binary frames sequentially at exact 1:1 real-time playback consumption rate."""
    loop = asyncio.get_event_loop()
    total = len(pcm_bytes)
    if total == 0 or ws.closed:
        return

    # Start timer aligned with tiny preroll to avoid flooding ESP32 ring buffer
    start = loop.time() - (PREROLL_BYTES / BYTES_PER_SEC)
    offset = 0
    while offset < total:
        if ws.closed:
            return
        end = min(offset + CHUNK_SIZE, total)
        try:
            await ws.send_bytes(pcm_bytes[offset:end])
        except Exception:
            return  # Connection dropped mid-stream — exit cleanly
        
        # Calculate real-time duration of sent chunk and sleep precisely
        target_time = start + (end / BYTES_PER_SEC)
        drift = target_time - loop.time()
        if drift > 0:
            await asyncio.sleep(drift)
        offset = end


def emotion_speed(emotion: str) -> float:
    """PROSODY_MAP's rate ("+10%") as a Kokoro speed factor (1.10)."""
    rate = PROSODY_MAP.get(emotion, PROSODY_MAP["Normal"])["rate"]
    return max(0.5, min(2.0, 1.0 + float(rate.rstrip("%")) / 100.0))


def emotion_pitch_factor(emotion: str) -> float:
    """
    PROSODY_MAP's pitch ("+5Hz", written for edge-tts) as a frequency factor.
    Read against a 120 Hz reference so the old offsets are audible on
    Kokoro (+10 Hz is ~1.4 semitones), capped at 2 semitones either way so
    the voice stays itself.
    """
    hz = float(PROSODY_MAP.get(emotion, PROSODY_MAP["Normal"])["pitch"].rstrip("Hz"))
    semitones = max(-2.0, min(2.0, 12 * np.log2((120 + hz) / 120)))
    semitones *= emotion_amount()
    return float(2 ** (semitones / 12))


def emotion_amount() -> float:
    """The app's "Feeling in the voice" setting as a factor: 60 (default) = 1.0, 0 = none."""
    try:
        from core import ui_settings
        return max(0.0, min(100.0, float(ui_settings.get("emotion_strength")))) / 60.0
    except Exception:
        return 1.0


def output_resample_ratio(pitch: float) -> tuple[int, int]:
    """
    (up, down) that turns Kokoro's 24 kHz into the 22.05 kHz stream and
    raises pitch by `pitch` in one step. Fewer output samples at the same
    playback rate = higher and faster; Kokoro is asked to speak slower by
    the same factor, so the pace is unchanged.
    """
    from fractions import Fraction
    ratio = Fraction(147, 160) / Fraction(pitch).limit_denominator(1000)
    ratio = ratio.limit_denominator(2000)
    return ratio.numerator, ratio.denominator


async def _synthesize_local(clean_text: str, lang: str, emotion: str) -> np.ndarray:
    """Speak `clean_text` with Kokoro; float32 mono at TARGET_SR, or empty."""
    if lang == "ar":
        # Kokoro has no Arabic voice, and Arabic is optional for MBUDDY. Say
        # so in the log rather than read Arabic script with an English voice.
        log.warn("No local Arabic voice installed; reply not spoken")
        return np.zeros(0, dtype=np.float32)
    log.tts(f"Generating [kokoro]: '{clean_text[:40]}…' (Mood: {emotion})")
    pitch = emotion_pitch_factor(emotion)
    async with _EDGE_TTS_LOCK:
        from core import ui_settings
        voice = await asyncio.to_thread(local_tts.voice_for_emotion, emotion, ui_settings.get("voice"), emotion_amount())
        samples = await asyncio.wait_for(
            asyncio.to_thread(local_tts.synthesize, clean_text, emotion_speed(emotion) / pitch, voice, voice_ready),
            timeout=max(TTS_SYNTH_TIMEOUT_S, 15.0),
        )
    if samples.size == 0:
        return samples
    # 24 kHz -> 22.05 kHz (the rate the playback side expects), with the
    # emotion's pitch shift folded into the same resample.
    up, down = output_resample_ratio(pitch)
    return resample_poly(samples, up, down).astype(np.float32)





async def synthesize_tts_pcm(
    clean_text: str,
    emotion: str = "Normal",
    lang: str = "en",
    fade_in: bool = False,
    fade_out: bool = True,
    leader_ms: int = FIRST_LEADER_MS,
    trailer_ms: int = TRAILER_MS,
    volume: float = 1.0,
) -> bytes:
    """Synthesize one reply chunk into mono 16-bit PCM bytes at TARGET_SR."""
    cache_key = (
        clean_text,
        emotion,
        lang,
        int(leader_ms),
        int(trailer_ms),
        round(float(volume), 2),
    )
    cached = _TTS_PCM_CACHE.get(cache_key)
    if cached is not None:
        runtime_status.update(last_tts_ok=True)
        return cached

    samples_f = await _synthesize_local(clean_text, lang, emotion)
    if samples_f.size == 0:
        return b""
    samples_f = _normalize_loudness(samples_f)
    pcm = _build_pcm_bytes(
        samples_f,
        fade_in=fade_in,
        fade_out=fade_out,
        leader_ms=leader_ms,
        trailer_ms=trailer_ms,
        volume=volume,
    )
    if len(_TTS_PCM_CACHE) >= TTS_CACHE_MAX:
        _TTS_PCM_CACHE.pop(next(iter(_TTS_PCM_CACHE)))
    _TTS_PCM_CACHE[cache_key] = pcm
    return pcm


async def stream_tts_pcm(
    ws,
    speech_lock: asyncio.Lock,
    pcm_bytes: bytes,
    is_outputting_flag: list[bool],
    session=None,
) -> None:
    """Stream pre-synthesized PCM bytes to the ESP32."""
    if not pcm_bytes:
        return

    async with speech_lock:
        try:
            is_outputting_flag[0] = True
            if session is not None:
                session.is_outputting_audio = True
            await _paced_stream(ws, pcm_bytes)
            runtime_status.update(last_tts_ok=True)
            await asyncio.sleep(TAIL_WAIT_S)
        finally:
            is_outputting_flag[0] = False
            if session is not None:
                session.is_outputting_audio = False
        try:
            await asyncio.sleep(DRAIN_WAIT_S)
        except asyncio.CancelledError:
            if not ws.closed:
                try:
                    await ws.send_str("[STOP]")
                except Exception:
                    pass
            raise
