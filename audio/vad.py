"""
audio/vad.py — WebRTC Voice Activity Detection.
Provides frame-level speech probability using Google's WebRTC VAD algorithm.

Resolution: 20ms frames at 16kHz (320 samples per frame).
Aggressiveness: set via VAD_AGGRESSIVENESS in core/config.py.
"""
import numpy as np
import webrtcvad

from core.config import SAMPLE_RATE, VAD_AGGRESSIVENESS, VAD_FRAME_SAMPLES, NOISE_FLOOR

# Single shared VAD instance — webrtcvad is stateless per call, thread-safe.
_vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)


def webrtc_vad_check(chunk_float32: np.ndarray) -> bool:
    """Run WebRTC VAD on a float32 audio chunk.
    Returns True if ANY 20ms sub-frame is classified as speech.

    The ESP32 sends 256-sample (16ms) chunks. This function reframes
    them into 320-sample (20ms) windows as required by the VAD.
    A chunk smaller than one frame is zero-padded.
    """
    pcm = np.clip(chunk_float32, -1.0, 1.0)
    pcm_int16 = (pcm * 32767).astype(np.int16)
    raw_bytes  = pcm_int16.tobytes()

    frame_bytes = VAD_FRAME_SAMPLES * 2   # 2 bytes per int16 sample

    if len(raw_bytes) < frame_bytes:
        # Pad to exactly one 20ms frame
        padded = np.pad(pcm_int16, (0, VAD_FRAME_SAMPLES - len(pcm_int16)), mode="constant")
        try:
            return _vad.is_speech(padded.tobytes(), SAMPLE_RATE)
        except Exception:
            return False

    num_frames = len(raw_bytes) // frame_bytes
    for i in range(num_frames):
        frame = raw_bytes[i * frame_bytes : (i + 1) * frame_bytes]
        try:
            if _vad.is_speech(frame, SAMPLE_RATE):
                return True
        except Exception:
            pass
    return False


def energy_is_trivial(chunk_float32: np.ndarray) -> bool:
    """Fast pre-check: True if energy is so low that VAD should be skipped.
    Avoids running the VAD on pure DMA padding / near-zero packets.
    """
    from utils.diagnostics import audio_rms
    return audio_rms(chunk_float32) < NOISE_FLOOR * 0.8
