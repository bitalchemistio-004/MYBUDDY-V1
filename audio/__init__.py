"""
audio - DSP pipeline helpers.

Note: audio_rms / is_silent live in utils.diagnostics (shared with other packages).
Wake-word policy lives in stt.language_detect; per-session buffering lives in
audio.session_state.
"""
from audio.preprocessing import (
    dc_remove, highpass_filter, rms_agc,
    soft_noise_gate, trim_silence, preprocess_audio,
)
from audio.vad import webrtc_vad_check, energy_is_trivial

__all__ = [
    # Preprocessing DSP pipeline
    "dc_remove", "highpass_filter", "rms_agc",
    "soft_noise_gate", "trim_silence", "preprocess_audio",
    # VAD
    "webrtc_vad_check", "energy_is_trivial",
]
