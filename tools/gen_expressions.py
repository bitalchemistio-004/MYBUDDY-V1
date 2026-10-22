"""
tools/gen_expressions.py - one-time generation of MBUDDY's expression clips.

Kokoro (the runtime voice) cannot laugh or cough; it reads "Haha" as a word.
Chatterbox Turbo can perform paralinguistic tags ([laugh], [chuckle],
[cough]) and clone a voice from a short reference, so this renders those
sounds once in af_heart's voice (reference clip made with Kokoro). The
runtime only plays the resulting WAVs; Chatterbox never runs live.

Runs in the isolated env, because chatterbox pins its own torch:
    models/expr_env/Scripts/python.exe tools/gen_expressions.py
Writes models/expressions/<name>_<n>.wav (24 kHz mono, trimmed, loudness-
matched to Kokoro) for a person to audition.
"""
from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "models" / "expressions"
REFERENCE = OUT / "af_heart_reference.wav"
KOKORO_SR = 24000

# name -> (text given to Chatterbox, variants)
EXPRESSIONS = {
    "laugh": ("[laugh]", 3),
    "chuckle": ("[chuckle]", 3),
    "cough": ("[cough]", 2),
    "sigh": ("[sigh]", 2),  # not in the documented tag list; kept only if it sounds right
}


def to_kokoro_format(audio: np.ndarray, sr: int) -> np.ndarray:
    """Resample to 24 kHz, trim silence, match Kokoro's loudness."""
    from scipy.signal import resample_poly

    if sr != KOKORO_SR:
        audio = resample_poly(audio, KOKORO_SR, sr)
    env = np.abs(audio)
    voiced = np.where(env > 0.02 * env.max())[0]
    if voiced.size:
        pad = int(0.04 * KOKORO_SR)
        audio = audio[max(0, voiced[0] - pad): voiced[-1] + pad]
    with wave.open(str(REFERENCE), "rb") as w:
        ref = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    target_rms = float(np.sqrt(np.mean(ref ** 2)))
    rms = float(np.sqrt(np.mean(audio ** 2))) or 1e-9
    audio = audio * (target_rms / rms)
    peak = float(np.abs(audio).max())
    return audio * (0.95 / peak) if peak > 0.95 else audio


def save(path: Path, audio: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(KOKORO_SR)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())


def main() -> None:
    import torch
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    device = "cuda" if torch.cuda.is_available() else "cpu"
    t0 = time.perf_counter()
    model = ChatterboxTurboTTS.from_pretrained(device=device)
    print(f"Chatterbox Turbo loaded on {device} in {time.perf_counter() - t0:.1f}s", flush=True)
    for name, (text, variants) in EXPRESSIONS.items():
        for n in range(1, variants + 1):
            torch.manual_seed(1000 * n + len(name))
            t = time.perf_counter()
            wav = model.generate(text, audio_prompt_path=str(REFERENCE))
            audio = to_kokoro_format(wav.squeeze().cpu().numpy().astype(np.float32), model.sr)
            path = OUT / f"{name}_{n}.wav"
            save(path, audio)
            print(f"  {path.name}: {len(audio) / KOKORO_SR:.2f}s (took {time.perf_counter() - t:.1f}s)", flush=True)
    print("done")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
