"""
tools/mic_probe.py - find the capture path that gives clean laptop-mic audio.

The first recordings sounded robotic and muffled: MME was asked for 16 kHz
mono from a 4-microphone Intel array running at 44.1/48 kHz, so Windows both
mixed the mics down (phase cancellation) and resampled. This records the same
sentence once per capture path, keeps every raw channel, and writes a 16 kHz
mono version made with a proper resampler, so the paths can be compared by
ear and by Whisper.

Usage (in its own console, it waits for Enter):
    .venv/Scripts/python.exe tools/mic_probe.py
Writes test_lab/stt_bench/probe/<path>.wav (16 kHz mono) and
<path>_raw.wav (native rate, all channels).
"""
from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

import numpy as np
import pyaudio
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "test_lab" / "stt_bench" / "probe"
SENTENCE = "Can you open Visual Studio Code and my project folder?"
SECONDS = 5.0

# (label, host API, device-name fragment, rate, channels, channel to keep or None to average)
PATHS = [
    ("mme_16k_mono", "MME", "Microphone Array", 16000, 1, 0),
    ("wasapi_48k_ch0", "Windows WASAPI", "Microphone Array", 48000, 2, 0),
    ("wasapi_48k_avg", "Windows WASAPI", "Microphone Array", 48000, 2, None),
    ("wdmks_array2_16k_ch0", "Windows WDM-KS", "Microphone Array 2", 16000, 4, 0),
]


def find(pa: pyaudio.PyAudio, api: str, fragment: str) -> dict | None:
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d["maxInputChannels"] > 0 and fragment in d["name"] and pa.get_host_api_info_by_index(d["hostApi"])["name"] == api:
            return d
    return None


def save(path: Path, pcm: np.ndarray, rate: int) -> None:
    pcm = np.atleast_2d(pcm.T).T if pcm.ndim == 1 else pcm
    with wave.open(str(path), "wb") as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.clip(pcm, -32768, 32767).astype(np.int16).tobytes())


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pa = pyaudio.PyAudio()
    print(f'Each take: read this sentence aloud when it says RECORDING:\n\n   "{SENTENCE}"\n')
    if sys.stdin.isatty():
        input("Sit at the laptop and press Enter to start...")
    for label, api, fragment, rate, channels, keep in PATHS:
        dev = find(pa, api, fragment)
        if dev is None:
            print(f"[{label}] device not found, skipped\n")
            continue
        try:
            stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=rate, input=True,
                             input_device_index=dev["index"], frames_per_buffer=1024)
        except Exception as exc:
            print(f"[{label}] could not open: {exc}\n")
            continue
        print(f"[{label}]  {api} / {dev['name']}")
        for n in (3, 2, 1):
            print(f"   {n}...", flush=True)
            time.sleep(0.8)
        print("   RECORDING", flush=True)
        frames = [stream.read(1024, exception_on_overflow=False) for _ in range(int(rate * SECONDS / 1024))]
        stream.stop_stream()
        stream.close()
        raw = np.frombuffer(b"".join(frames), dtype=np.int16).reshape(-1, channels).astype(np.float32)
        save(OUT / f"{label}_raw.wav", raw, rate)
        mono = raw[:, keep] if keep is not None else raw.mean(axis=1)
        if rate != 16000:
            mono = resample_poly(mono, 16000, rate)
        save(OUT / f"{label}.wav", mono, 16000)
        rms = 20 * np.log10(np.sqrt(np.mean(mono ** 2)) / 32768 + 1e-9)
        print(f"   saved, level {rms:.1f} dBFS\n")
    pa.terminate()
    print(f"Done. Takes are in {OUT}")


if __name__ == "__main__":
    main()
