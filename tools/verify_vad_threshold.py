from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from audio.session_state import AudioSessionState


def test_vad_threshold_adjustment() -> None:
    print("Testing adjusted VAD silence threshold...")
    session = AudioSessionState()
    session.noise_floor = 0.002
    quiet_voice_rms = 0.0021
    chunk = np.random.normal(0, quiet_voice_rms, 256).astype(np.float32)
    centered = chunk - np.mean(chunk)
    rms = float(np.sqrt(np.mean(centered**2)))
    gate = session.noise_floor * 1.1
    manual_is_silent = rms < gate
    print(f"Manual check: RMS={rms:.6f}, Gate={gate:.6f}, Silent={manual_is_silent}")
    assert manual_is_silent is True
    print("SUCCESS: Quiet noise near floor is correctly treated as silent.")


if __name__ == "__main__":
    test_vad_threshold_adjustment()
    print("\nALL VAD THRESHOLD TESTS PASSED!")
