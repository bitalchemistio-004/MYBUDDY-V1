"""
audio/preprocessing.py — Audio DSP pipeline for microphone input.
Applied once per collected utterance before STT submission.

Pipeline order:
    DC removal → 80 Hz HPF → RMS-AGC → soft-knee noise gate → clip protection
"""
import numpy as np
from scipy.signal import lfilter, butter

from core.config import (
    SAMPLE_RATE, NOISE_FLOOR, TARGET_AUDIO_RMS, MAX_GAIN,
    HPF_CUTOFF_HZ, SILENCE_TRIM_RMS, SILENCE_TRIM_WIN_MS,
)

# ── Pre-compute HPF coefficients once at import time ────────
_HPF_B, _HPF_A = butter(1, HPF_CUTOFF_HZ / (SAMPLE_RATE / 2.0), btype="high")


def dc_remove(audio_np: np.ndarray) -> np.ndarray:
    """Subtract mean (DC bias) across the full utterance buffer."""
    return audio_np - float(np.mean(audio_np))


def highpass_filter(audio_np: np.ndarray) -> np.ndarray:
    """First-order IIR high-pass at HPF_CUTOFF_HZ (80 Hz).
    Removes DC hum, desk rumble, HVAC noise, and body-contact vibration.
    """
    if audio_np.size < 4:
        return audio_np
    return lfilter(_HPF_B, _HPF_A, audio_np).astype(np.float32)


def rms_agc(audio_np: np.ndarray) -> np.ndarray:
    """RMS-based Automatic Gain Control.
    Targets TARGET_AUDIO_RMS with a hard cap of MAX_GAIN.
    Preserves speech dynamics while lifting quiet/distant audio.
    """
    rms = float(np.sqrt(np.mean(audio_np ** 2)))
    if rms < 1e-7:   # Near-silence — do not amplify noise floor
        return audio_np
    gain = min(TARGET_AUDIO_RMS / rms, MAX_GAIN)
    return np.clip(audio_np * gain, -1.0, 1.0).astype(np.float32)


def soft_noise_gate(audio_np: np.ndarray, threshold: float | None = None) -> np.ndarray:
    """Soft-knee noise gate — fade below threshold instead of hard zeroing.
    Eliminates click transients produced by the old hard-clip gate.
    """
    t = threshold if threshold is not None else max(0.01, NOISE_FLOOR * 1.1)
    knee = t * 0.5
    low  = t - knee
    high = t + knee
    envelope = np.abs(audio_np)
    mask = np.where(
        envelope <= low,  0.0,
        np.where(envelope >= high, 1.0,
                 (envelope - low) / (2.0 * knee))
    ).astype(np.float32)
    return (audio_np * mask).astype(np.float32)


def trim_silence(audio_np: np.ndarray) -> np.ndarray:
    """Trim leading/trailing silence from a collected utterance.
    Uses SILENCE_TRIM_WIN_MS windows with a 20ms safety pad on each side
    so Whisper never loses onset/offset consonants.
    """
    if audio_np.size == 0:
        return audio_np
    win = max(1, int((SILENCE_TRIM_WIN_MS / 1000.0) * SAMPLE_RATE))
    pad = win * 2

    rms_frames = np.array([
        float(np.sqrt(np.mean(audio_np[i : i + win] ** 2)))
        for i in range(0, len(audio_np) - win + 1, win)
    ])

    voiced = np.where(rms_frames > SILENCE_TRIM_RMS)[0]
    if len(voiced) == 0:
        return audio_np  # All silence — upstream guards handle rejection

    start_frame  = max(0, voiced[0] - 2)
    end_frame    = min(len(rms_frames) - 1, voiced[-1] + 2)
    start_sample = max(0, start_frame * win - pad)
    end_sample   = min(len(audio_np), (end_frame + 1) * win + pad)
    return audio_np[start_sample:end_sample]


def preprocess_audio(audio_np: np.ndarray) -> np.ndarray:
    """Full DSP pipeline: DC removal → HPF → RMS-AGC → soft gate → clip."""
    audio_np = audio_np.astype(np.float32)
    audio_np = dc_remove(audio_np)
    audio_np = highpass_filter(audio_np)
    audio_np = rms_agc(audio_np)
    audio_np = soft_noise_gate(audio_np)
    return np.clip(audio_np, -1.0, 1.0).astype(np.float32)
