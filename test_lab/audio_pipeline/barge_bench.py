"""
Echo and barge-in bench.

The robot must not interrupt itself with its own voice, yet must stop when a person talks over it. Production
decides from the microphone PEAK against a threshold (see barge.py, an exact replica of the firmware rule).
This bench measures the numbers that decision depends on:

  * Echo scan: the robot speaks at several levels; the peak its own microphone hears is compared with the
    threshold in force and the replica reports any false self-interruptions.
  * Interruption test: the robot speaks and you talk over it; the peak of your voice is compared with the
    echo's, and the replica says whether it would have stopped — and whether a peak threshold can separate them.
"""
from __future__ import annotations

import asyncio
import time

import numpy as np

from . import barge, corpus
from .bench_hub import BenchBase, BenchError, Capture, chart, table
from .speaker_analysis import MIC_RATE, SPK_RATE
from .speaker_bench import HEARD_CONF, MAX_LEVEL_DB, SpeakerLink, locate

DEFAULT_VOLUMES = (-24, -18, -12, -6)


def _speech_clip_22k(text_idx: int = 0):
    return corpus.get_clip("en", text_idx, SPK_RATE)


def _fit_level(clip: np.ndarray, level_db: float) -> np.ndarray:
    peak = float(np.max(np.abs(clip))) or 1.0
    return np.round(clip.astype(np.float64) / peak * 32767.0 * 10 ** (min(level_db, MAX_LEVEL_DB) / 20.0)).astype(np.int16)


def parse_volumes(text: str) -> list[float]:
    vals = []
    for part in str(text).replace(";", ",").split(","):
        part = part.strip()
        if part:
            vals.append(min(float(part), MAX_LEVEL_DB))
    if not vals:
        raise BenchError("Enter at least one speaker level, e.g. -24,-18,-12,-6.")
    return sorted(set(vals))[:8]


class EchoBench(BenchBase):
    name = "echo"

    def __init__(self, lab) -> None:
        super().__init__(lab)
        self.link = SpeakerLink(lab)

    async def action(self, action: str, params: dict) -> str | None:
        if action not in ("scan", "interrupt"):
            return f"echo: unknown action {action!r}"
        if self.running:
            return "An echo test is already running."
        if self.lab.recorder.active or self.lab.collector.running:
            return "Stop the recording / calibration first."
        try:
            if action == "scan":
                vols = parse_volumes(params.get("volumes", ",".join(map(str, DEFAULT_VOLUMES))))
                self.start(self._scan(vols, max(1, min(3, int(params.get("repeats", 2))))))
            else:
                self.start(self._interrupt(min(float(params.get("volume_db", -12)), MAX_LEVEL_DB)))
        except (BenchError, ValueError) as exc:
            return str(exc)
        return None

    async def _clip(self):
        pcm, _voice, _text = await _speech_clip_22k(0)
        return pcm

    def _lab_gain(self) -> float:
        return float((self.lab.applied or self.lab.requested).get("gain", 12.0))

    def _constants_table(self) -> dict:
        return table("Production barge-in constants used (from MBUDDY.ino)", ["Constant", "Value"],
                     [["Threshold while the robot speaks", f"noise floor × {barge.BARGE_IN_MULTIPLIER} × {barge.BARGE_SELF_REJECT_MULT} (at least {barge.MIN_BARGE_THRESHOLD})"],
                      ["Frames in a row above it", barge.BARGE_CONSECUTIVE_FRAMES],
                      ["Noise floor", f"starts at {int(barge.INITIAL_AMBIENT)}, tracks the peak at {barge.NOISE_FLOOR_ADAPT:.0%} per frame while silent"],
                      ["Peak scale", f"int16 after the firmware's {int(barge.PRODUCTION_GAIN)}× gain (lab readings are rescaled from {self._lab_gain():g}×)"]])

    # -- shared: play a clip and hear it, returning per-frame peaks ---------
    async def _capture(self, pcm22: np.ndarray, lead: float, tail: float, prompts=None, search_s: float | None = None):
        with Capture(self.lab) as cap:
            await asyncio.sleep(lead)
            task = asyncio.ensure_future(prompts(time.monotonic())) if prompts else None
            info = await self.link.play(pcm22)
            await asyncio.sleep(tail)
            if task:
                await task
        left, right = cap.arrays()
        if left is None and right is None:
            raise BenchError("No microphone audio was captured.")
        n = min(len(a) for a in (left, right) if a is not None)
        left = left[:n] if left is not None else None
        right = right[:n] if right is not None else None
        peaks = barge.frame_peaks(left, right, self._lab_gain(), "auto")
        s0 = cap.sample_at(info["t_first"])
        ref_mic = left if left is not None else right
        start, conf, latency = locate(pcm22, ref_mic, s0, (200.0, 3400.0), 0.2, search_s)
        return peaks, s0, start, conf, latency, info

    async def _scan(self, volumes: list[float], repeats: int = 2) -> None:
        self.require_streaming()
        await self.link.check()
        await self.begin("Preparing the speech clip…")
        clip = await self._clip()
        rows, bad, xs, ys, verdict_levels = [], [], [], [], []
        steps = len(volumes) * repeats
        step = 0
        for vol in volumes:
            pcm = _fit_level(clip, vol)
            runs = []
            for rep in range(repeats):
                await self.update(message=f"Echo at {vol:g} dBFS (run {rep + 1}/{repeats}): the robot speaks…", progress=step / steps)
                step += 1
                peaks, s0, start, conf, latency, info = await self._capture(pcm, lead=3.0, tail=0.6)
                frames_start = int(start // barge.FRAME)
                frames_end = frames_start + int(len(pcm) / SPK_RATE * MIC_RATE // barge.FRAME)
                speaking = np.zeros(len(peaks), bool)
                speaking[frames_start:min(frames_end, len(peaks))] = True
                if conf < HEARD_CONF or not speaking.any():
                    continue
                sim = barge.simulate(peaks, speaking)
                room = peaks[: max(1, frames_start - 4)]
                runs.append({"echo": float(peaks[speaking].max()), "room": float(room.max()), "sim": sim, "speaking": speaking})
            if not runs:
                rows.append([f"{vol:g}", "not heard", "—", "—", "—", "—", "—", "the microphone did not clearly hear the speaker (too faint, or lost in room noise)"])
                bad.append((len(rows) - 1, 7))
                continue
            best = min(runs, key=lambda r: r["echo"])                  # a random room noise will not appear in every run
            echo_peak = best["echo"]
            room_peak = max(r["room"] for r in runs)
            sim = best["sim"]
            thr = float(min(t for t, sp in zip(sim["thresholds"], best["speaking"]) if sp))
            margin = 20 * np.log10(thr / max(echo_peak, 1.0))
            false = len(sim["triggers"])
            hidden = echo_peak <= 1.3 * room_peak                    # the "echo" is no louder than the room's own peaks
            ok = false == 0 and margin >= 3.0
            result = ("OK" if ok else "would interrupt itself" if false else "margin under 3 dB")
            if hidden:
                result += " (echo is hidden in room noise: an upper bound)"
            rows.append([f"{vol:g}", ("≤ " if hidden else "") + f"{int(echo_peak)}  ({20 * np.log10(echo_peak / 32768):.0f} dBFS)",
                         f"{int(room_peak)}", f"{int(sim['ambient'])}", f"{int(thr)}", f"{margin:+.1f} dB", false, result])
            r = len(rows) - 1
            if not ok:
                bad.append((r, 5)); bad.append((r, 7))
            if not hidden:
                xs.append(vol); ys.append(20 * np.log10(echo_peak / 32768))
            verdict_levels.append((vol, ok, margin, false))
        safe = [v for v, ok, _m, _f in verdict_levels if ok]
        if not verdict_levels:
            verdict = {"level": "bad", "text": "The microphone did not hear the speaker at any level: run the Speaker test first."}
        elif len(safe) == len(verdict_levels):
            verdict = {"level": "ok", "text": f"The robot never interrupted itself up to {max(safe):g} dBFS (smallest margin {min(m for _v, _o, m, _f in verdict_levels):+.1f} dB)."}
        elif safe:
            verdict = {"level": "warn", "text": f"Safe up to {max(safe):g} dBFS; louder than that the robot would interrupt itself."}
        else:
            verdict = {"level": "bad", "text": "The robot's own voice would trigger barge-in even at the quietest level tested."}
        notes = ["Each level is played " + (f"{repeats} times and the LOWER echo peak is used: a random sound in the room will not repeat in every run. " if repeats > 1 else "once. ") +
                 "'Room peak' is the loudest the microphone heard in the seconds BEFORE the robot spoke: an echo no louder than that is hidden in room noise "
                 "(shown with ≤ and left off the chart), so its true value is lower.",
                 "Peaks use the LEFT microphone only, rescaled to the firmware's 12× gain. Production picks the louder of the two microphones per sample, "
                 "so a noisy second microphone would raise these numbers.",
                 "'Margin' is 20·log10(threshold ÷ echo peak): 0 dB means the echo just reaches the threshold; under 3 dB is treated as unsafe. "
                 "The noise floor is what production would have tracked in the 3 s of room sound before the speech.",
                 "This tests the robot's own voice only. Whether a person can still interrupt is the Interruption test."]
        await self.update(phase="done", message="Finished.", progress=1.0, verdict=verdict, notes=notes,
                          tables=[table("Echo versus the barge-in threshold", ["Speaker level (dBFS)", "Echo peak heard", "Room peak before speaking", "Noise floor",
                                                                             "Threshold while speaking", "Margin", "False interruptions", "Result"], rows, bad), self._constants_table()],
                          charts=[chart("Echo peak the microphone hears (levels where it is above room noise)", xs, ys, "speaker level (dBFS)", "echo peak (dBFS)")] if xs else [])

    async def _interrupt(self, vol: float) -> None:
        self.require_streaming()
        await self.link.check()
        await self.begin("Preparing the speech clip…")
        clip = await self._clip()
        reps = int(np.ceil(8.0 * SPK_RATE / len(clip)))
        pcm = _fit_level(np.tile(clip, reps)[: int(8.5 * SPK_RATE)], vol)

        async def prompts(t0: float):
            for at, text, level in ((0.2, "The robot is speaking — stay quiet…", "wait"), (2.4, "TALK LOUDLY OVER IT NOW", "go"), (5.4, "Stop talking", "stop")):
                await asyncio.sleep(max(0.0, t0 + at - time.monotonic()))
                await self.update(prompt={"text": text, "level": level}, message=text)
            await asyncio.sleep(2.8)

        await self.update(message="Starting: stay quiet until told…", progress=0.1)
        # production's noise floor is tracked while silent, so give it 3 s of room sound first
        # locate the start of playback from the first 2 s only: a person's voice is far louder than the echo and,
        # once they start talking (2.4 s in), would otherwise be mistaken for the start of the robot's speech
        peaks, s0, start, conf, latency, info = await self._capture(pcm, lead=3.0, tail=0.8, prompts=prompts, search_s=2.0)
        await self.update(prompt=None, message="Analysing…", progress=0.9)
        if conf < HEARD_CONF:
            raise BenchError("The microphone did not hear the speaker: run the Speaker test first.")
        fps = MIC_RATE / barge.FRAME
        f0 = int(start // barge.FRAME)
        n_frames = int(len(pcm) / SPK_RATE * fps)
        speaking = np.zeros(len(peaks), bool)
        speaking[f0:min(f0 + n_frames, len(peaks))] = True
        sim = barge.simulate(peaks, speaking)
        win = lambda a, b: slice(f0 + int(a * fps), f0 + int(b * fps))
        echo_peak = float(peaks[win(0.5, 2.0)].max())
        talk_peak = float(peaks[win(2.9, 5.2)].max())
        trig = [(t - f0) / fps for t in sim["triggers"]]
        false_early = [t for t in trig if t < 2.4]
        in_talk = [t for t in trig if 2.4 <= t <= 6.0]
        rec = barge.recommend(echo_peak, talk_peak)
        thr = float(min(t for t, s in zip(sim["thresholds"], speaking) if s))
        rows = [["Robot's own voice (before you talk)", f"{int(echo_peak)}  ({20 * np.log10(echo_peak / 32768):.0f} dBFS)"],
                ["Your voice over the robot", f"{int(talk_peak)}  ({20 * np.log10(talk_peak / 32768):.0f} dBFS)"],
                ["Your voice vs its echo", f"{rec.get('separation_db', 0):+.1f} dB"],
                ["Production threshold while speaking", int(thr)],
                ["Production would have interrupted", "yes, " + f"{in_talk[0] - 2.4:.2f} s after you started" if in_talk else "no"],
                ["Interrupted itself before you spoke", "YES" if false_early else "no"]]
        bad = []
        if not in_talk:
            bad.append((4, 1))
        if false_early:
            bad.append((5, 1))
        if rec.get("separable") and rec.get("threshold_range"):
            lo, hi = rec["threshold_range"]
            rows.append(["A threshold that separates them", f"between about {lo} and {hi} (production uses {int(thr)})"])
        else:
            rows.append(["A threshold that separates them", "none: your voice and the echo are too close in peak level"])
            bad.append((len(rows) - 1, 1))
        if false_early:
            verdict = {"level": "bad", "text": "The robot interrupted itself with its own voice before you spoke."}
        elif not in_talk:
            verdict = {"level": "bad", "text": f"Production would NOT have stopped when you talked over it (your peak {int(talk_peak)} vs threshold {int(thr)}). "
                                                "Talk louder or closer, or the threshold is too high for this echo level."}
        elif rec.get("separation_db", 0) < 6:
            verdict = {"level": "warn", "text": "It stopped, but your voice is less than 6 dB above the echo: a small change in room or volume could break it."}
        else:
            verdict = {"level": "ok", "text": "Production stopped promptly when you talked over it, without interrupting itself."}
        notes = ["Whether you were loud enough is part of the result: repeat with the same effort you would use in real life.",
                 "Left microphone only, rescaled to the firmware's 12× gain (production picks the louder of the two microphones per sample).",
                 "The robot talks for about 8 s; you are asked to talk from about 2.4 s to 5.4 s."]
        await self.update(phase="done", message="Finished.", progress=1.0, prompt=None, verdict=verdict, notes=notes,
                          tables=[table("Talking over the robot", ["Measure", "Result"], rows, bad), self._constants_table()])
