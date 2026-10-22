"""
tools/bench_tts.py - local text-to-speech benchmark for the Phase 2 rebuild.

Times Kokoro-82M (ONNX, CPU) on THIS machine and writes WAV samples for a
person to listen to, next to the current cloud voice (edge-tts) for
comparison. Numbers are measured, never estimated.

Model files (kokoro-v1.0.onnx, voices-v1.0.bin) are downloaded once into
models/kokoro/ from the kokoro-onnx GitHub release.

Usage:
    .venv/Scripts/python.exe tools/bench_tts.py
Writes test_lab/tts_bench/*.wav and docs/audit/bench/tts_results.json.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "models" / "kokoro"
OUT_DIR = ROOT / "test_lab" / "tts_bench"
RESULTS = ROOT / "docs" / "audit" / "bench" / "tts_results.json"
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = ["kokoro-v1.0.onnx", "voices-v1.0.bin"]

LINES = [
    "Hey! I'm here. What's up?",
    "Done. I set a timer for twelve minutes.",
    "Oh, that sounds exhausting. Want me to remind you to stretch in an hour?",
    "It's five thirty. You asked me to remind you to call your mom.",
]
VOICES = ["af_heart", "af_bella", "am_michael", "bf_emma", "am_puck"]
EDGE_VOICE = "en-US-AriaNeural"


def ensure_models() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        path = MODEL_DIR / name
        if not path.exists():
            print(f"downloading {name} ...", flush=True)
            tmp = path.with_suffix(path.suffix + ".part")
            urllib.request.urlretrieve(RELEASE + name, tmp)
            tmp.rename(path)


def write_wav(path: Path, samples: np.ndarray, sr: int) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


async def edge_reference(text: str, path: Path) -> float:
    import edge_tts
    t = time.perf_counter()
    await edge_tts.Communicate(text, EDGE_VOICE).save(str(path))
    return time.perf_counter() - t


def main() -> None:
    from kokoro_onnx import Kokoro

    ensure_models()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    kokoro = Kokoro(str(MODEL_DIR / FILES[0]), str(MODEL_DIR / FILES[1]))
    load_s = time.perf_counter() - t0
    kokoro.create("Warm up.", voice=VOICES[0], speed=1.0, lang="en-us")  # not counted

    results = {"engine": "kokoro-onnx", "device": "cpu", "load_s": round(load_s, 2), "voices": {}}
    print(f"Kokoro loaded in {load_s:.2f}s")
    for voice in VOICES:
        rows = []
        for i, text in enumerate(LINES):
            t = time.perf_counter()
            samples, sr = kokoro.create(text, voice=voice, speed=1.0, lang="en-us")
            dt = time.perf_counter() - t
            audio_s = len(samples) / sr
            write_wav(OUT_DIR / f"kokoro_{voice}_{i}.wav", samples, sr)
            rows.append({"text": text, "synth_s": round(dt, 3), "audio_s": round(audio_s, 2), "rtf": round(dt / audio_s, 3)})
        mean_rtf = float(np.mean([r["rtf"] for r in rows]))
        first = rows[0]["synth_s"]
        results["voices"][voice] = {"mean_rtf": round(mean_rtf, 3), "short_line_synth_s": first, "rows": rows}
        print(f"  {voice:11s} RTF {mean_rtf:.3f}  (short line ready in {first:.2f}s)")

    edge_rows = []
    for i, text in enumerate(LINES):
        try:
            dt = asyncio.run(edge_reference(text, OUT_DIR / f"edge_{i}.mp3"))
            edge_rows.append({"text": text, "round_trip_s": round(dt, 3)})
        except Exception as exc:
            edge_rows.append({"text": text, "error": str(exc)})
    results["edge_reference"] = {"voice": EDGE_VOICE, "rows": edge_rows}
    ok = [r["round_trip_s"] for r in edge_rows if "round_trip_s" in r]
    if ok:
        print(f"  edge-tts (cloud, current) round trip mean {np.mean(ok):.2f}s")

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"samples in {OUT_DIR}\nwrote {RESULTS}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
