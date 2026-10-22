"""
Voice-detection bench: production's own VAD, wake-phrase and hallucination logic.

Nothing here re-implements production behaviour — it imports and calls
`audio.vad.webrtc_vad_check`, `stt.language_detect.should_wake` and
`is_probable_hallucination`, so a threshold change in production shows up here.

1. The PRODUCTION GATE (`audio.session_state.AudioSessionState`: adaptive energy
   gate, then the VAD, then a required run of voiced chunks, then silence
   timing) driven with a virtual clock, so 30 s of audio takes milliseconds but
   the logic is production's own. On noise-only clips it counts how often the
   robot would start "listening" and send audio to speech-to-text; on generated
   speech it checks the utterance is found once, starts promptly and is
   captured whole.
2. The raw VAD alone on speech and noise (informational): what the gate has to
   filter.
3. Text logic: wake phrases must wake, ordinary sentences must not, and
   Whisper's well-known ghost transcripts must be rejected while real speech
   (including any Arabic) is not.
4. Optional: speech-to-text in the loop — generated "hey buddy ..." clips
   through Groq, then the wake check (uses your quota).
"""
from __future__ import annotations

import asyncio
import statistics

import numpy as np

from . import corpus, stt_branch
from .bench_hub import BenchBase, table

CHUNK = 256
RATE = 16000
SPEECH_CONDITIONS = [("clean", None), ("15 dB SNR", 15.0), ("5 dB SNR", 5.0), ("0 dB SNR", 0.0)]
RECALL_WARN, RECALL_BAD = 0.85, 0.7
FALSE_ALARM_WARN, FALSE_ALARM_BAD = 0.05, 0.2


# ── production hooks (imported lazily; the stack is slow to import) ─────
def load_production():
    if str(stt_branch.PROJECT_ROOT) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(stt_branch.PROJECT_ROOT))
    from audio.vad import webrtc_vad_check
    from core import config
    from stt.language_detect import is_probable_hallucination, is_wake_only, should_wake
    from audio import session_state
    return {"vad": webrtc_vad_check, "wake": should_wake, "wake_only": is_wake_only,
            "halluc": is_probable_hallucination, "config": config, "session": session_state}


# ── the production gate on a virtual clock ──────────────────────────────
class _Clock:
    """Stands in for the event loop's clock so the gate's silence timing runs faster than real time."""

    def __init__(self) -> None:
        self.t = 0.0

    def time(self) -> float:
        return self.t


class _Asyncio:
    def __init__(self, clock: _Clock) -> None:
        self._clock = clock

    def get_event_loop(self) -> _Clock:
        return self._clock


class _QuietLog:
    def __getattr__(self, _name):
        return lambda *a, **k: None


def run_gate(prod: dict, pcm: np.ndarray) -> dict:
    """
    Feed production's AudioSessionState 256-sample chunks, one per 16 ms of virtual time.
    Returns how many utterances it started and ended, when the first began, and what it collected.
    """
    mod = prod["session"]
    clock = _Clock()
    saved = (mod.asyncio, mod.log)
    mod.asyncio, mod.log = _Asyncio(clock), _QuietLog()
    try:
        state = mod.AudioSessionState()
        starts = ended = voiced = speaking_chunks = 0
        first_start_s = None
        collected: list[np.ndarray] = []
        was = False
        for i in range(0, len(pcm) - CHUNK + 1, CHUNK):
            clock.t += CHUNK / RATE
            chunk = pcm[i:i + CHUNK].astype(np.float32) / 32768.0
            done = state.feed_chunk(chunk)
            if state.is_speaking and not was:
                starts += 1
                if first_start_s is None:
                    first_start_s = i / RATE
            was = state.is_speaking
            speaking_chunks += int(state.is_speaking)
            if done:
                ended += 1
                collected.append(state.drain_buffer())
                was = False
        if state.is_speaking:                                   # still open at the end of the clip
            collected.append(state.drain_buffer())
        audio = np.concatenate(collected) if collected else np.zeros(0, np.float32)
        return {"starts": starts, "ended": ended, "first_start_s": first_start_s, "collected": audio,
                "speaking_s": speaking_chunks * CHUNK / RATE, "leftover_voiced": state.voiced_frames}
    finally:
        mod.asyncio, mod.log = saved


def embed_speech(pcm: np.ndarray, rng: np.random.Generator, lead_s: float = 1.0, tail_s: float = 3.0) -> tuple[np.ndarray, float]:
    """Room tone, then the speech, then enough silence for production's 2.2 s end-of-speech timer to fire."""
    lead = np.round(corpus.pink_noise(int(lead_s * RATE), rng) * 30).astype(np.int16)        # ~ -60 dBFS room tone
    tail = np.round(corpus.pink_noise(int(tail_s * RATE), rng) * 30).astype(np.int16)
    return np.concatenate([lead, pcm, tail]), lead_s


# ── signals ─────────────────────────────────────────────────────────────
def _dbfs_scale(x: np.ndarray, dbfs: float) -> np.ndarray:
    rms = float(np.sqrt(np.mean(x ** 2))) or 1.0
    return np.clip(np.round(x / rms * 32768.0 * 10 ** (dbfs / 20.0)), -32768, 32767).astype(np.int16)


def noise_clip(kind: str, seconds: float, rng: np.random.Generator) -> np.ndarray:
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    if kind == "White noise (-30 dBFS)":
        return _dbfs_scale(rng.standard_normal(n), -30)
    if kind == "Pink noise (-30 dBFS)":
        return _dbfs_scale(corpus.pink_noise(n, rng), -30)
    if kind == "Fan / brown noise (-20 dBFS)":
        return _dbfs_scale(_brown(n, rng), -20)
    if kind == "Mains hum 50 Hz + harmonics (-30 dBFS)":
        return _dbfs_scale(sum(np.sin(2 * np.pi * 50 * k * t + k) / k for k in range(1, 9)), -30)
    if kind == "Keyboard-like clicks (-20 dBFS)":
        x = np.zeros(n)
        for pos in rng.integers(0, n - 40, size=int(seconds * 4)):
            x[pos:pos + 24] += rng.standard_normal(24) * np.exp(-np.arange(24) / 5.0)
        return _dbfs_scale(x + rng.standard_normal(n) * 0.001, -20)
    if kind == "Musical chord (-25 dBFS)":
        vib = 1 + 0.003 * np.sin(2 * np.pi * 5 * t)
        return _dbfs_scale(sum(np.sin(2 * np.pi * f * vib * t) / (i + 1) ** 0.5 for i, f in enumerate((261.6, 329.6, 392.0, 523.3, 659.3))), -25)
    if kind == "Near silence (-70 dBFS)":
        return _dbfs_scale(rng.standard_normal(n), -70)
    raise ValueError(kind)


def _brown(n: int, rng: np.random.Generator) -> np.ndarray:
    x = np.cumsum(rng.standard_normal(n))
    return x - np.linspace(x[0], x[-1], n)


NOISE_KINDS = ["White noise (-30 dBFS)", "Pink noise (-30 dBFS)", "Fan / brown noise (-20 dBFS)",
               "Mains hum 50 Hz + harmonics (-30 dBFS)", "Keyboard-like clicks (-20 dBFS)", "Musical chord (-25 dBFS)",
               "Near silence (-70 dBFS)"]


def vad_flags(vad, pcm: np.ndarray) -> np.ndarray:
    """Feed the VAD 256-sample chunks like the robot's stream; one bool per chunk."""
    out = []
    for i in range(0, len(pcm) - CHUNK + 1, CHUNK):
        out.append(bool(vad(pcm[i:i + CHUNK].astype(np.float32) / 32768.0)))
    return np.array(out, dtype=bool)


def chunk_truth(clean: np.ndarray) -> np.ndarray:
    """True for chunks that are mostly speech, judged from the clean clip's energy."""
    frames = corpus.active_frames(clean, 320)
    per_sample = np.repeat(frames, 320)
    n = len(clean) // CHUNK
    per_sample = np.pad(per_sample, (0, max(0, n * CHUNK - len(per_sample))))[: n * CHUNK]
    return per_sample.reshape(n, CHUNK).mean(axis=1) > 0.5


def score(flags: np.ndarray, truth: np.ndarray) -> tuple[float | None, float | None]:
    """(recall on speech chunks, false-alarm rate on non-speech chunks); None where there are none."""
    n = min(len(flags), len(truth))
    f, t = flags[:n], truth[:n]
    recall = float(f[t].mean()) if t.any() else None
    fa = float(f[~t].mean()) if (~t).any() else None
    return recall, fa


# ── the text cases (expectations follow production's documented behaviour) ─
def text_cases(config) -> list[tuple[str, str, str, bool]]:
    """(function, text, why, expected). function is 'wake' or 'halluc'."""
    wake = list(config.WAKE_PHRASES)
    cases = []
    for p in wake:
        cases += [("wake", p, "the exact wake phrase", True),
                  ("wake", f"{p.upper()}!", "same phrase, shouted with punctuation", True),
                  ("wake", f"{p}, what time is it?", "wake phrase followed by a question", True)]
    cases += [("wake", "what is the weather today", "an ordinary sentence", False),
              ("wake", "buddy system", "one word of the phrase only", False),
              ("wake", "hey there", "a greeting that is not the phrase", False),
              ("wake", "مرحبا كيف حالك", "ordinary Arabic", False),
              ("wake", "", "empty transcript", False)]
    cases += [("halluc", "Thank you.", "Whisper's classic ghost on silence", True),
              ("halluc", "Thanks for watching!", "video-outro ghost", True),
              ("halluc", "Please subscribe", "video-outro ghost", True),
              ("halluc", "Subtitles by the Amara.org community", "subtitle credit ghost", True),
              ("halluc", "you you you you you you you you", "a word looping", True),
              ("halluc", "okay", "a lone filler word", True),
              ("halluc", "What is the weather like today?", "a real English request", False),
              ("halluc", "Set a timer for me please", "a real English request", False),
              ("halluc", "مرحبا كيف حالك اليوم", "real Arabic speech is never a hallucination", False),
              ("halluc", "hey buddy", "a real wake call must never be filtered", False),
              ("halluc", "hey buddy thank you", "wake phrase plus thanks", False)]
    return cases


class VadBench(BenchBase):
    name = "vad"

    async def action(self, action: str, params: dict) -> str | None:
        if action != "run":
            return f"vad: unknown action {action!r}"
        if self.running:
            return "The voice-detection test is already running."
        self.start(self._run(bool("stt_wake" in (params.get("extras") or []))))
        return None

    async def _clip(self, lang: str, idx: int):
        return await corpus.get_clip(lang, idx, RATE)

    async def _transcribe(self, audio_f32: np.ndarray) -> dict:
        return await stt_branch.transcribe_array(audio_f32, "generated clip", "auto", True)

    async def _run(self, with_stt: bool) -> None:
        await self.begin("Loading production voice-detection code…")
        prod = await asyncio.to_thread(load_production)
        rng = np.random.default_rng(11)
        notes, tables, bad_flags = [], [], []

        # 0. The production gate ---------------------------------------------
        await self.update(message="Running production's gate on 30 s of each noise…", progress=0.1)
        gate_rows, gate_bad = [], []
        for kind in NOISE_KINDS:
            clip = noise_clip(kind, 30.0, rng)
            r = await asyncio.to_thread(run_gate, prod, clip)
            gate_rows.append([kind, r["starts"], r["ended"], f"{r['speaking_s']:.1f} s"])
            if r["starts"]:
                gate_bad.append((len(gate_rows) - 1, 1))
                gate_bad.append((len(gate_rows) - 1, 2))
        tables.append(table("Production gate on 30 s of noise-only audio (it should never start listening)",
                            ["Noise", "Times it started listening", "Utterances sent to speech-to-text", "Time spent 'listening'"], gate_rows, gate_bad))
        gate_false_starts = sum(r[1] for r in gate_rows)

        # 1. VAD on speech ---------------------------------------------------
        speech_rows, speech_bad = [], []
        clips, clip_errors = [], []
        for lang in ("en", "ar"):
            for idx in range(3):
                await self.update(message=f"Preparing {lang.upper()} speech clip {idx + 1}/3…", progress=0.05 + 0.1 * (len(clips) / 6))
                try:
                    clips.append((lang, await self._clip(lang, idx)))
                except Exception as exc:
                    clip_errors.append(f"{type(exc).__name__}: {exc}")
        gate_speech_ok = None
        if clips:
            await self.update(message="Running production's gate on speech…", progress=0.2)
            grows, gbad = [], []
            ok_all = 0
            total_all = 0
            for label, snr in SPEECH_CONDITIONS:
                found, captured, delays, extra = 0, [], [], 0
                for lang, (pcm, _voice, _text) in clips:
                    noisy = pcm if snr is None else corpus.add_noise(pcm, snr, rng)
                    sig, lead = embed_speech(noisy, rng)
                    r = await asyncio.to_thread(run_gate, prod, sig)
                    total_all += 1
                    if r["starts"] >= 1 and r["ended"] >= 1:
                        found += 1
                        ok_all += 1 if (r["starts"] == 1 and r["ended"] == 1) else 0
                        delays.append(max(0.0, (r["first_start_s"] - lead) * 1000))
                    extra += max(0, r["ended"] - 1)
                    e_in = float(np.sum(noisy.astype(np.float64) ** 2))
                    e_out = float(np.sum((r["collected"].astype(np.float64) * 32768.0) ** 2))
                    captured.append(min(1.0, e_out / e_in) if e_in > 0 else 0.0)
                grows.append([label, len(clips), f"{found}/{len(clips)}", f"{statistics.mean(captured):.0%}",
                              "—" if not delays else f"{statistics.median(delays):.0f} ms", extra])
                i = len(grows) - 1
                if found < len(clips):
                    gbad.append((i, 2))
                if statistics.mean(captured) < 0.9:
                    gbad.append((i, 3))
                if extra:
                    gbad.append((i, 5))
            tables.append(table("Production gate on generated speech (room tone, speech, then silence)",
                                ["Condition", "Clips", "Utterance found", "Speech energy captured", "Median delay to start listening", "Extra utterances"], grows, gbad))
            gate_speech_ok = (ok_all, total_all)
            await self.update(message="Running the raw voice detector on speech…", progress=0.3)
            for label, snr in SPEECH_CONDITIONS:
                rec, fas = [], []
                for lang, (pcm, _voice, _text) in clips:
                    noisy = pcm if snr is None else corpus.add_noise(pcm, snr, rng)
                    r, fa = await asyncio.to_thread(lambda: score(vad_flags(prod["vad"], noisy), chunk_truth(pcm)))
                    if r is not None:
                        rec.append(r)
                    if fa is not None:
                        fas.append(fa)
                r_m = statistics.mean(rec) if rec else None
                f_m = statistics.mean(fas) if fas else None
                speech_rows.append([label, len(clips), "—" if r_m is None else f"{r_m:.0%}", "—" if f_m is None else f"{f_m:.0%}"])
                i = len(speech_rows) - 1
                if r_m is not None and r_m < RECALL_WARN:
                    speech_bad.append((i, 2))
                if f_m is not None and f_m > FALSE_ALARM_WARN:
                    speech_bad.append((i, 3))
            tables.append(table(f"Raw detector alone on speech (informational; {len(clips)} generated clips, aggressiveness {prod['config'].VAD_AGGRESSIVENESS})",
                                ["Condition", "Clips", "Speech chunks caught", "Fires in the pauses"], speech_rows, speech_bad))
        else:
            notes.append("Speech clips could not be generated, so speech detection was not measured: " + "; ".join(sorted(set(clip_errors))[:2]))

        # 2. VAD on noise-only ----------------------------------------------
        await self.update(message="Running the voice detector on noise-only clips…", progress=0.45)
        noise_rows, noise_bad = [], []
        for kind in NOISE_KINDS:
            clip = noise_clip(kind, 6.0, rng)
            fa = float(await asyncio.to_thread(lambda: vad_flags(prod["vad"], clip).mean()))
            noise_rows.append([kind, f"{fa:.0%}"])
            if fa > FALSE_ALARM_WARN:
                noise_bad.append((len(noise_rows) - 1, 1))
        tables.append(table("Raw detector alone on noise (informational: production's energy gate and start rule sit in front of this)",
                            ["Noise", "Chunks flagged as speech"], noise_rows, noise_bad))

        # 3. Text logic ------------------------------------------------------
        await self.update(message="Checking wake-phrase and hallucination logic…", progress=0.6)
        text_rows, text_bad = [], []
        for fn, text, why, expected in text_cases(prod["config"]):
            actual = bool(prod["wake"](text) if fn == "wake" else prod["halluc"](text))
            ok = actual == expected
            text_rows.append(["wake" if fn == "wake" else "hallucination filter", text or "(empty)", why, "yes" if expected else "no",
                              "yes" if actual else "no", "pass" if ok else "FAIL"])
            if not ok:
                text_bad.append((len(text_rows) - 1, 5))
        tables.append(table("Wake phrase and hallucination logic on transcripts", ["Check", "Transcript", "Case", "Expected", "Production says", "Result"], text_rows, text_bad))
        failures = len(text_bad)

        # 4. STT in the loop -------------------------------------------------
        stt_bad = 0
        if with_stt:
            stt_rows = []
            wake_lines = [("en", "Hey buddy, what time is it?", "en-US-AriaNeural"), ("en", "Hi buddy, tell me a joke.", "en-GB-RyanNeural")]
            for n, (lang, text, voice) in enumerate(wake_lines):
                try:
                    mp3 = await corpus.synth(text, voice)
                    pcm = corpus.decode_mp3(mp3, RATE)
                except Exception as exc:
                    stt_rows.append([text, "—", "—", f"clip generation failed: {exc}"]); stt_bad += 1
                    continue
                for label, snr in (("clean", None), ("10 dB SNR", 10.0)):
                    await self.update(message=f"Speech-to-text wake test: {text!r} ({label})…", progress=0.7 + 0.25 * (n / len(wake_lines)))
                    audio = pcm if snr is None else corpus.add_noise(pcm, snr, rng)
                    try:
                        r = await self._transcribe(audio.astype(np.float32) / 32768.0)
                        heard = r.get("text") or ""
                    except Exception as exc:
                        heard = ""
                        r = {"note": f"{type(exc).__name__}: {exc}"}
                    woke = bool(heard) and bool(prod["wake"](heard))
                    stt_rows.append([f"{text} ({label})", heard or "(no text)", "yes" if woke else "NO", "" if heard else (r.get("note") or "")])
                    stt_bad += 0 if woke else 1
                    await asyncio.sleep(1.2)
            tables.append(table("Wake phrase through speech-to-text", ["Spoken", "Heard", "Woke the robot", "Note"], stt_rows,
                                [(i, 2) for i, r in enumerate(stt_rows) if r[2] == "NO"]))

        # verdict ------------------------------------------------------------
        noisy_kinds = [r[0] for r in gate_rows if r[1]]
        problems = []
        if failures:
            problems.append(f"{failures} wake/hallucination checks disagree with what they should do")
        if noisy_kinds:
            problems.append(f"production's gate starts listening on noise-only audio ({gate_false_starts} false starts in {len(gate_rows)} × 30 s; worst: "
                            + ", ".join(k.split(" (")[0].lower() for k in noisy_kinds[:3]) + ")")
        if gate_speech_ok is not None and gate_speech_ok[0] < gate_speech_ok[1]:
            problems.append(f"only {gate_speech_ok[0]}/{gate_speech_ok[1]} speech clips were found exactly once")
        if stt_bad:
            problems.append(f"{stt_bad} speech-to-text wake checks failed")
        level = "ok" if not problems else "bad" if (failures or noisy_kinds) else "warn"
        verdict = {"level": level, "text": "All voice-detection checks look healthy." if not problems else "; ".join(problems).capitalize() + "."}
        notes += ["'Production gate' tables drive audio.session_state.AudioSessionState itself (energy gate, VAD, the required run of voiced chunks, "
                  "silence timing) on a virtual clock. The noises are deliberately LOUD and steady (-30 to -20 dBFS: a running fan, music, mains hum): "
                  "a false start means loud steady noise can open the microphone, not that a quiet room does. A false start means audio would be sent to speech-to-text; production's later filters "
                  "(Whisper no-speech probability, the hallucination list) are a second line of defence that this bench does not include.",
                  "Voice-activity detection runs on 256-sample chunks zero-padded to one 20 ms frame, exactly as production does.",
                  "'Speech chunks caught' is judged against where the clean clip really has speech; 'fires in the pauses' is the share of quiet chunks flagged.",
                  "'Thank you' alone is treated as a Whisper ghost by production, so a user who really says only 'thank you' is ignored: that is a design trade-off this table makes visible.",
                  "Generated speech is clean and steady; real rooms are harder. Treat this as a regression baseline."]
        await self.update(phase="done", message="Finished.", progress=1.0, tables=tables, notes=notes, verdict=verdict)
