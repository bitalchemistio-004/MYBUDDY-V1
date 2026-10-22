"""
tts - Text-to-speech: audio synthesis and ESP32 streaming.
"""
from tts.speaker import synthesize_tts_pcm, stream_tts_pcm, TARGET_SR
from tts.sfx import SFX_NAMES, synthesize_sfx_pcm
from stt.language_policy import voice_for_lang

__all__ = [
    "synthesize_tts_pcm",
    "synthesize_sfx_pcm",
    "stream_tts_pcm",
    "TARGET_SR",
    "SFX_NAMES",
    "voice_for_lang",
]
