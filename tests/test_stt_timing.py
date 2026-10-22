"""Why speech-to-text was sometimes slow (M2 S4b): each decode logs how long
it waited for the decoder, what held it, how long it decoded, and whether it
decoded twice. A fake decoder stands in for Whisper."""
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from stt import local_whisper as lw


@pytest.fixture
def fake(monkeypatch):
    logged = []
    detected = {"language": "en"}

    def decode(model, audio, language, prompt, hotwords=None, beam_size=5):
        time.sleep(0.3)
        seg = SimpleNamespace(text=" hello there", avg_logprob=-0.2, no_speech_prob=0.01)
        info = SimpleNamespace(language=language or detected["language"], all_language_probs=[(detected["language"], 0.9)])
        return [seg], info
    monkeypatch.setattr(lw, "_decode", decode)
    monkeypatch.setattr(lw, "get_model", lambda: object())
    monkeypatch.setattr(lw, "_device", "cuda/int8_float16")
    monkeypatch.setattr(lw.log, "info", lambda msg: logged.append(msg))
    return logged, detected


def test_a_decode_that_waits_behind_another_says_so(fake):
    logged, _ = fake
    audio = np.zeros(16000 * 2, dtype=np.float32)
    first = threading.Thread(target=lambda: lw.wake_check(audio))
    first.start()
    time.sleep(0.05)
    lw.transcribe(audio, language_hint="en")
    first.join()
    line = next(m for m in logged if m.startswith("[STT] transcribe"))
    waited = float(line.split("waited ")[1].split("s")[0])
    assert waited >= 0.15 and "(it was busy: wake check)" in line
    assert "for 2.0s audio on cuda/int8_float16" in line


def test_a_second_full_decode_for_the_language_is_logged(fake):
    logged, detected = fake
    detected["language"] = "cy"   # Whisper's guess on noise; MBUDDY uses English
    lw.transcribe(np.zeros(16000, dtype=np.float32))
    line = next(m for m in logged if m.startswith("[STT] transcribe"))
    assert "twice (language cy->" in line
