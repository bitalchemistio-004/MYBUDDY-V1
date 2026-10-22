"""
tools/bench_wake.py - does "Hey Buddy" wake MBUDDY, and does nothing else?

MBUDDY's wake word is transcript-based: while asleep, each speech segment
goes through the same local Whisper as conversation, and stt.language_detect
.should_wake() looks for "hey buddy" / "hi buddy". This measures that path
end to end with speech that is not the model's own training data:

  Kokoro voices (female/male, US/UK) x speeds x noise levels
  -> 16 kHz -> stt.transcriber.transcribe_audio (live preprocessing)
  -> should_wake()

Positives must wake; near misses ("my buddy", "hey Bobby") must not.
Writes docs/audit/bench/wake_results.json.

Usage: .venv/Scripts/python.exe tools/bench_wake.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("TTS_DEVICE", "cpu")  # leave the GPU to Whisper and the running backend

from stt.language_detect import should_wake  # noqa: E402
from stt.transcriber import transcribe_audio  # noqa: E402
from tts import local_tts  # noqa: E402

VOICES = ["af_heart", "af_bella", "am_adam", "am_michael", "bf_emma", "bm_george"]
SPEEDS = [0.9, 1.0, 1.15]
NOISE_SNR_DB = [None, 20, 10]  # clean, quiet room, noisy room
POSITIVE = ["Hey Buddy.", "Hey Buddy, what time is it?", "Hi buddy."]
NEGATIVE = ["Hey, what's up?", "My buddy called me yesterday.", "Hey Bobby, come here.",
            "I need a new body pillow.", "Hey, did you see that?"]
OUT = REPO / "docs" / "audit" / "bench" / "wake_results.json"


def with_noise(x: np.ndarray, snr_db: float | None, rng) -> np.ndarray:
    if snr_db is None:
        return x
    # Pink-ish noise (room hum and fans are mostly low-frequency).
    white = rng.standard_normal(x.size + 64)
    pink = np.convolve(white, np.ones(64) / 8, mode="valid")[: x.size]
    p_sig = np.mean(x ** 2) + 1e-12
    pink *= np.sqrt(p_sig / (10 ** (snr_db / 10)) / (np.mean(pink ** 2) + 1e-12))
    return (x + pink).astype(np.float32)


async def main() -> None:
    rng = np.random.default_rng(7)
    rows = []
    t0 = time.perf_counter()
    for phrase, expect in [(p, True) for p in POSITIVE] + [(n, False) for n in NEGATIVE]:
        for voice in VOICES:
            for speed in SPEEDS:
                speech = resample_poly(local_tts.synthesize(phrase, speed, voice), 2, 3).astype(np.float32)
                pad = np.zeros(int(0.3 * 16000), dtype=np.float32)
                speech = np.concatenate([pad, speech, pad])
                for snr in NOISE_SNR_DB:
                    audio = with_noise(speech, snr, rng)
                    # The live path while asleep (cheap wake check first), timed
                    # against the full transcription it replaced.
                    t = time.perf_counter()
                    full, _lang, _lp, _ns = await transcribe_audio(audio)
                    full_s = time.perf_counter() - t
                    t = time.perf_counter()
                    text, _lang, _lp, _ns = await transcribe_audio(audio, asleep=True)
                    asleep_s = time.perf_counter() - t
                    woke = should_wake(text or "")
                    rows.append({"phrase": phrase, "expect_wake": expect, "voice": voice, "speed": speed,
                                 "snr_db": snr, "heard": text, "woke": woke, "ok": woke == expect,
                                 "full_woke": should_wake(full or ""), "full_s": round(full_s, 3),
                                 "asleep_s": round(asleep_s, 3)})
                    if woke != expect:
                        print(f"MISS  {phrase!r:34} {voice:10} x{speed} snr={snr}: heard {text!r}", flush=True)
    pos = [r for r in rows if r["expect_wake"]]
    neg = [r for r in rows if not r["expect_wake"]]
    summary = {
        "wake_rate": f"{sum(r['woke'] for r in pos)}/{len(pos)}",
        "false_wakes": f"{sum(r['woke'] for r in neg)}/{len(neg)}",
        "by_noise": {str(s): f"{sum(r['woke'] for r in pos if r['snr_db'] == s)}/{sum(1 for r in pos if r['snr_db'] == s)}"
                     for s in NOISE_SNR_DB},
        "misheard_as": sorted({r["heard"] for r in pos if not r["woke"] and r["heard"]}),
        "full_path_wake_rate": f"{sum(r['full_woke'] for r in pos)}/{len(pos)}",
        "full_path_false_wakes": f"{sum(r['full_woke'] for r in neg)}/{len(neg)}",
        "stt_seconds_non_wake_full_vs_asleep": [round(float(np.mean([r['full_s'] for r in neg])), 3),
                                                round(float(np.mean([r['asleep_s'] for r in neg])), 3)],
        "stt_seconds_wake_full_vs_asleep": [round(float(np.mean([r['full_s'] for r in pos])), 3),
                                            round(float(np.mean([r['asleep_s'] for r in pos])), 3)],
        "seconds": round(time.perf_counter() - t0, 1),
    }
    print("SUMMARY", json.dumps(summary, indent=1, ensure_ascii=False))
    OUT.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
    sys.stdout.flush()
    os._exit(0)  # freeing the CUDA Whisper model aborts the process (audit 2.2a)
