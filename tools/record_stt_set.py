"""
tools/record_stt_set.py - record the STT benchmark sentences on the laptop mic.

Shows each sentence, counts down, records a fixed window, and saves
test_lab/stt_bench/mic/sNN.wav (16 kHz mono). No keypresses needed, so it
runs from Claude Code's `!` prompt. Existing clips are kept unless --redo.

Usage:
    .venv/Scripts/python.exe tools/record_stt_set.py [--redo] [--seconds 5] [--device NAME]

--device picks the first input whose name contains NAME. The default is the
laptop's built-in "Microphone Array", not the Windows default input (which
on this PC is a phone-as-mic virtual driver).
"""
from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

import numpy as np
import pyaudio

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_stt import MIC_DIR, SENTENCES, SR  # noqa: E402


def find_input(pa: pyaudio.PyAudio, needle: str) -> dict:
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0 and needle.lower() in info["name"].lower():
            return info
    names = sorted({pa.get_device_info_by_index(i)["name"] for i in range(pa.get_device_count())
                    if pa.get_device_info_by_index(i)["maxInputChannels"] > 0})
    raise SystemExit(f"No input device matching {needle!r}. Inputs: {names}")


def main() -> None:
    redo = "--redo" in sys.argv
    seconds = float(sys.argv[sys.argv.index("--seconds") + 1]) if "--seconds" in sys.argv else 5.0
    MIC_DIR.mkdir(parents=True, exist_ok=True)

    needle = sys.argv[sys.argv.index("--device") + 1] if "--device" in sys.argv else "Microphone Array"
    pa = pyaudio.PyAudio()
    info = find_input(pa, needle)
    print(f"Microphone: {info['name']}")
    print(f"Read each sentence aloud when it says RECORDING ({seconds:.0f} s each).\n")
    if sys.stdin.isatty():
        input("Press Enter when you are ready...")
    stream = pa.open(format=pyaudio.paInt16, channels=1, rate=SR, input=True,
                     input_device_index=info["index"], frames_per_buffer=1024)
    try:
        for i, text in enumerate(SENTENCES):
            path = MIC_DIR / f"s{i:02d}.wav"
            if path.exists() and not redo:
                continue
            print(f"[{i + 1}/{len(SENTENCES)}]  \"{text}\"")
            for n in (3, 2, 1):
                print(f"   {n}...", flush=True)
                time.sleep(0.8)
            stream.read(stream.get_read_available(), exception_on_overflow=False)  # drop buffered audio
            print("   RECORDING", flush=True)
            frames = [stream.read(1024, exception_on_overflow=False) for _ in range(int(SR * seconds / 1024))]
            pcm = b"".join(frames)
            peak = int(np.abs(np.frombuffer(pcm, dtype=np.int16)).max())
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SR)
                w.writeframes(pcm)
            warn = "  (very quiet - check the mic)" if peak < 1500 else ""
            print(f"   saved, peak {peak}{warn}\n")
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
    print(f"Done. Clips are in {MIC_DIR}")


if __name__ == "__main__":
    main()
