"""
tools/bench_stt.py - local speech-to-text benchmark for the Phase 2 rebuild.

Measures faster-whisper models on THIS machine: load time, GPU memory, time
per clip and word error rate against known text. Nothing is estimated; a
measurement that fails is recorded as null with the error.

Test material:
- Scripted English clips with known text, generated once with edge-tts and
  cached in test_lab/stt_bench/ (cloud is used only to build the test set,
  never at runtime).
- The same sentences read by the user into the laptop microphone
  (tools/record_stt_set.py writes test_lab/stt_bench/mic/sNN.wav). This is
  the input MBUDDY actually gets, so its WER is the number that decides.

Usage:
    .venv/Scripts/python.exe tools/bench_stt.py small large-v3-turbo
    .venv/Scripts/python.exe tools/bench_stt.py --cpu small
Writes docs/audit/bench/stt_results.json.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SET_DIR = ROOT / "test_lab" / "stt_bench"
MIC_DIR = SET_DIR / "mic"
OUT = ROOT / "docs" / "audit" / "bench" / ("stt_results_cpu.json" if "--cpu" in sys.argv else "stt_results.json")
SR = 16000
_KEEP_ALIVE: list = []

SENTENCES = [
    "Hey buddy, what time is it?",
    "Remind me to call my mom at five thirty.",
    "Set a timer for twelve minutes.",
    "What's the weather like in Beirut tomorrow?",
    "Turn the volume down a little.",
    "I just finished a long workout and I'm exhausted.",
    "Can you open Visual Studio Code and my project folder?",
    "Add buy milk and eggs to my task list.",
    "What did I ask you to remember yesterday?",
    "Tell me something interesting about octopuses.",
    "Cancel that, never mind.",
    "How many days until the twenty fourth of December?",
]
VOICES = ["en-US-AriaNeural", "en-GB-RyanNeural"]


_NUMBERS = {
    "5.30": "five thirty", "5:30": "five thirty", "12": "twelve", "24": "twenty fourth",
    "24th": "twenty fourth", "5": "five",
}


def _norm(text: str) -> list[str]:
    """Lower-case words; spoken numbers and digits count as the same word,
    so '12 minutes' is not scored as an error against 'twelve minutes'."""
    text = text.lower().replace("'", "").replace("nevermind", "never mind")
    for digits, words in _NUMBERS.items():
        text = re.sub(rf"(?<![\w.:]){re.escape(digits)}(?![\w:]|[.:]\d)", words, text)
    return re.sub(r"[^a-z0-9 ]+", " ", text).split()


def wer(ref: str, hyp: str) -> float:
    r, h = _norm(ref), _norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(len(r), 1)


async def _synth(text: str, voice: str, path: Path) -> None:
    import edge_tts
    import miniaudio

    mp3 = b""
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            mp3 += chunk["data"]
    pcm = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=SR)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.samples.tobytes())


def build_set() -> list[dict]:
    SET_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for vi, voice in enumerate(VOICES):
        for si, text in enumerate(SENTENCES):
            path = SET_DIR / f"v{vi}_s{si:02d}.wav"
            if not path.exists():
                asyncio.run(_synth(text, voice, path))
            items.append({"path": path, "text": text, "voice": voice})
    return items


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SR and w.getnchannels() == 1, path
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def production_preprocess(audio: np.ndarray) -> np.ndarray:
    """The backend's own chain (DC, high-pass, capped AGC, soft gate)."""
    sys.path.insert(0, str(ROOT))
    from audio.preprocessing import preprocess_audio
    return preprocess_audio(audio)


def peak_normalize(audio: np.ndarray, peak: float = 0.7) -> np.ndarray:
    """Plain gain to a fixed peak: what the audio could give at a healthy level."""
    m = float(np.abs(audio).max())
    return audio if m < 1e-6 else (audio * (peak / m)).astype(np.float32)


def vram_mb() -> float | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return float(out.splitlines()[0])
    except Exception:
        return None


def bench_model(name: str, scripted: list[dict], mic: list[dict]) -> dict:
    sys.path.insert(0, str(ROOT))
    from stt.cuda_libs import register_cuda_dll_dirs
    register_cuda_dll_dirs()
    from faster_whisper import WhisperModel

    device = "cpu" if "--cpu" in sys.argv else "cuda"
    compute = "int8" if device == "cpu" else "int8_float16"
    result: dict = {"model": name, "device": device, "compute_type": compute}
    base = vram_mb()
    t0 = time.perf_counter()
    try:
        model = WhisperModel(name, device=device, compute_type=compute)
    except Exception as exc:
        result["error"] = f"load failed: {exc}"
        return result
    result["load_s"] = round(time.perf_counter() - t0, 2)

    def run(audio: np.ndarray) -> tuple[str, str, float]:
        t = time.perf_counter()
        segments, info = model.transcribe(audio, beam_size=1, vad_filter=False)
        text = " ".join(s.text.strip() for s in segments)
        return text, info.language, time.perf_counter() - t

    run(load_wav(scripted[0]["path"]))  # warm-up, not counted
    loaded = vram_mb()
    result["vram_mb"] = round(loaded - base, 0) if (loaded is not None and base is not None) else None

    def score(items: list[dict], prep=None) -> dict | None:
        if not items:
            return None
        rows, wers, times, audio_s = [], [], [], 0.0
        for item in items:
            audio = load_wav(item["path"])
            if prep is not None:
                audio = prep(audio)
            text, lang, dt = run(audio)
            e = wer(item["text"], text)
            rows.append({"ref": item["text"], "hyp": text, "lang": lang, "wer": round(e, 3), "s": round(dt, 3)})
            wers.append(e)
            times.append(dt)
            audio_s += len(audio) / SR
        return {
            "clips": len(rows),
            "mean_wer": round(float(np.mean(wers)), 4),
            "exact_clips": sum(1 for w in wers if w == 0),
            "mean_latency_s": round(float(np.mean(times)), 3),
            "p90_latency_s": round(float(np.percentile(times, 90)), 3),
            "real_time_factor": round(sum(times) / audio_s, 4),
            "english_detected": sum(1 for r in rows if r["lang"] == "en"),
            "rows": rows,
        }

    result["scripted"] = score(scripted)
    result["laptop_mic"] = score(mic)
    result["laptop_mic_production"] = score(mic, production_preprocess)
    result["laptop_mic_normalized"] = score(mic, peak_normalize)
    # Freeing a CUDA CTranslate2 model aborts the interpreter on this machine
    # (0xC0000409), including when a local simply goes out of scope. The
    # model is parked in a module-level list so it is never freed, and each
    # model runs in its own subprocess that ends with os._exit.
    _KEEP_ALIVE.append(model)
    return result


def mic_set() -> list[dict]:
    return [
        {"path": MIC_DIR / f"s{i:02d}.wav", "text": text, "voice": "laptop-mic"}
        for i, text in enumerate(SENTENCES)
        if (MIC_DIR / f"s{i:02d}.wav").exists()
    ]


def _print_set(label: str, s: dict | None) -> None:
    if s is None:
        print(f"  {label}: no clips")
        return
    print(f"  {label}: WER {s['mean_wer']:.1%}  exact {s['exact_clips']}/{s['clips']}  "
          f"latency mean {s['mean_latency_s']}s p90 {s['p90_latency_s']}s  RTF {s['real_time_factor']}")
    for row in s["rows"]:
        if row["wer"] > 0:
            print(f"    miss ({row['wer']:.0%}): {row['ref']!r} -> {row['hyp']!r}")


def run_one(name: str, out_path: Path) -> None:
    """Child mode: benchmark one model, write its result, exit hard."""
    import os
    result = bench_model(name, build_set(), mic_set())
    out_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    sys.stdout.flush()
    os._exit(0)


def main(models: list[str]) -> None:
    scripted = build_set()
    mic = mic_set()
    print(f"scripted clips: {len(scripted)}, laptop-mic clips: {len(mic)}")
    results = []
    for name in models:
        print(f"\n=== {name} ===", flush=True)
        part = OUT.with_name(f".part_{name}.json")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        args = [sys.executable, __file__, "--one", name, str(part)] + (["--cpu"] if "--cpu" in sys.argv else [])
        proc = subprocess.run(args)
        if not part.exists():
            r = {"model": name, "error": f"benchmark process exited {proc.returncode} without a result"}
        else:
            r = json.loads(part.read_text(encoding="utf-8"))
            part.unlink()
        results.append(r)
        if "error" in r:
            print("  ERROR:", r["error"])
            continue
        print(f"  load {r['load_s']}s  vram +{r['vram_mb']} MB")
        _print_set("scripted", r["scripted"])
        _print_set("laptop mic (raw)", r["laptop_mic"])
        _print_set("laptop mic (production preprocess)", r["laptop_mic_production"])
        _print_set("laptop mic (normalized)", r["laptop_mic_normalized"])
    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--one" in sys.argv:
        i = sys.argv.index("--one")
        run_one(sys.argv[i + 1], Path(sys.argv[i + 2]))
    names = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(names or ["small", "large-v3-turbo"])
