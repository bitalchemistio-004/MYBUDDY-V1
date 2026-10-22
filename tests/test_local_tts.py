"""Local Kokoro TTS with expression clips (Phase 2)."""
import asyncio
from pathlib import Path

import numpy as np
import pytest

from tts.local_tts import split_expressions

MODEL = Path(__file__).resolve().parent.parent / "models" / "kokoro" / "kokoro-v1.0.onnx"


def test_split_expressions_reads_both_styles_in_order():
    assert split_expressions("*laughs* okay, you got me [sigh] fine.") == [
        ("clip", "laugh"), ("say", "okay, you got me"), ("clip", "sigh"), ("say", "fine."),
    ]
    assert split_expressions("*chuckling* okay") == [("clip", "chuckle"), ("say", "okay")]
    assert split_expressions("No expressions here.") == [("say", "No expressions here.")]
    assert split_expressions("") == []


@pytest.mark.skipif(not MODEL.exists(), reason="Kokoro model not downloaded")
def test_synthesize_tts_pcm_is_local_and_well_formed():
    from tts.speaker import TARGET_SR, synthesize_tts_pcm

    pcm = asyncio.run(synthesize_tts_pcm("Done. I set a timer for twelve minutes.", emotion="Happy", leader_ms=0, trailer_ms=0))
    samples = np.frombuffer(pcm, dtype=np.int16)
    seconds = len(samples) / TARGET_SR
    assert 1.5 < seconds < 6.0
    assert np.abs(samples).max() > 3000  # audible, not silence


@pytest.mark.skipif(not MODEL.exists(), reason="Kokoro model not downloaded")
def test_emoji_and_markup_are_not_spoken():
    from tts.speaker import synthesize_tts_pcm

    plain = asyncio.run(synthesize_tts_pcm("Congrats on fixing the bug!", leader_ms=0, trailer_ms=0))
    with_emoji = asyncio.run(synthesize_tts_pcm("Congrats on fixing the bug! 🎉🌟", leader_ms=0, trailer_ms=0))
    # Same words, so the audio must be about as long: emojis are not read out.
    assert abs(len(with_emoji) - len(plain)) < 0.25 * len(plain)


def test_stage_directions_without_a_sound_are_not_read_aloud():
    from tts.local_tts import split_expressions
    assert split_expressions("*adjusts volume* ...Now it's at 50%.") == [("say", "...Now it's at 50%.")]
    assert split_expressions("Oh *laughs* okay.") == [("say", "Oh"), ("clip", "laugh"), ("say", "okay.")]
