"""
Speaker bench: play known sounds through the robot's speaker, hear them with its own microphone.

  * Is it audible?      a 1 kHz tone, compared with the room before it.
  * Level & distortion  tones at rising levels: does the mic level follow, where does it compress or clip,
                        how much harmonic distortion is there.
  * Frequency response  a log sweep 100 Hz - 7.5 kHz: usable bandwidth and ripple.
  * Loop latency        from the moment audio is sent to the moment it is heard.

What is measured is the whole chain (amplifier, speaker, enclosure, air gap, microphone), which is the chain
that matters for echo. Levels are as the MICROPHONE reports them, after the firmware's digital gain.
Nothing is played louder than -6 dBFS.
"""
from __future__ import annotations

import asyncio
import time

import numpy as np

from . import speaker_analysis as sa
from .bench_hub import BenchBase, BenchError, Capture, chart, table

CHUNK = 512                       # speaker samples per binary packet (23 ms)
PREBUFFER_S = 0.35                # audio sent ahead of real time; the device's ring holds ~1.4 s
LEVELS_DB = (-40, -30, -20, -12, -6)
MAX_LEVEL_DB = -6.0
HEARD_CONF = 0.6                  # onset confidence needed to say a sound was really heard (noise alone reaches ~0.4)


class SpeakerLink:
    """Streams PCM to the lab firmware's speaker and waits for it to report the stream finished."""

    def __init__(self, lab) -> None:
        self.lab = lab

    async def check(self) -> None:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and self.lab.device is not None and "spkok" not in self.lab.device:
            await asyncio.sleep(0.2)                       # the first STAT arrives within a second
        dev = self.lab.device
        if dev is None or self.lab.device_ws is None:
            raise BenchError("No device connected.")
        if "spkok" not in dev:
            raise BenchError("The board did not report a speaker: it needs the mic_lab firmware version 4 or newer "
                             "(re-flash firmware/mic_lab, or update it over the air).")
        if str(dev["spkok"]) != "1":
            raise BenchError("The firmware could not start its speaker (I2S or memory error): see the board's serial log.")

    async def play(self, pcm22: np.ndarray, wait_done: bool = True) -> dict:
        lab = self.lab
        ws = lab.device_ws
        if ws is None or ws.closed:
            raise BenchError("The device disconnected.")
        await ws.send_str("SPK reset")
        lab.spk_done.clear()
        n = len(pcm22)
        count = (n + CHUNK - 1) // CHUNK
        dur = CHUNK / sa.SPK_RATE
        t0 = time.monotonic()
        t_first = None
        for i in range(count):
            wait = t0 + i * dur - PREBUFFER_S - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            part = pcm22[i * CHUNK:(i + 1) * CHUNK].astype("<i2")
            await ws.send_bytes(b"SP" + bytes([1, 1 if i == count - 1 else 0]) + (i & 0xFFFF).to_bytes(2, "little") + part.tobytes())
            if t_first is None:
                t_first = time.monotonic()
        info = {"t_first": t_first, "t_sent": time.monotonic(), "seconds": n / sa.SPK_RATE}
        if wait_done:
            try:
                await asyncio.wait_for(lab.spk_done.wait(), timeout=max(4.0, n / sa.SPK_RATE + 3.0))
            except asyncio.TimeoutError:
                raise BenchError("The device never reported that playback finished.")
            info["device"] = dict(lab.spk_last)
        return info


PRE_S = 0.25                      # background-only mic audio kept before the moment audio is sent


def locate(played22: np.ndarray, mic: np.ndarray, sample_at_send: int, band: tuple[float, float] = (200.0, 3400.0),
           frac: float = 0.4, search_s: float | None = None) -> tuple[int, float, float]:
    """
    When did the played sound start in the mic recording, and was it really heard?
    Returns (start sample, confidence 0..1, latency in ms from the moment the audio was sent). See speaker_analysis.onset.
    """
    lo = max(0, sample_at_send - int(PRE_S * sa.MIC_RATE))
    span = search_s if search_s is not None else len(played22) / sa.SPK_RATE + 2.5
    hi = min(len(mic), lo + int(span * sa.MIC_RATE) + int(PRE_S * sa.MIC_RATE))
    seg = mic[lo:hi]
    pre = sample_at_send - lo
    if pre < 1600 or len(seg) <= pre + 1600:
        return sample_at_send, 0.0, 0.0
    idx, conf = sa.onset(seg, pre, band, frac)
    start = lo + idx
    return start, conf, (start - sample_at_send) / sa.MIC_RATE * 1000.0


class SpeakerBench(BenchBase):
    name = "speaker"

    def __init__(self, lab) -> None:
        super().__init__(lab)
        self.link = SpeakerLink(lab)

    async def action(self, action: str, params: dict) -> str | None:
        if action not in ("probe", "levels", "sweep", "all"):
            return f"speaker: unknown action {action!r}"
        if self.running:
            return "A speaker test is already running."
        if self.lab.recorder.active or self.lab.collector.running:
            return "Stop the recording / calibration first."
        self.start(self._run(action))
        return None

    # -- one measurement -----------------------------------------------
    async def _play_and_hear(self, pcm22: np.ndarray, lead: float = 0.8, tail: float = 0.8, band=(200.0, 3400.0), frac: float = 0.4,
                             search_s: float | None = None):
        with Capture(self.lab) as cap:
            await asyncio.sleep(lead)
            info = await self.link.play(pcm22)
            await asyncio.sleep(tail)
        mic = cap.channel("auto")
        s0 = cap.sample_at(info["t_first"])
        start, conf, _ = locate(pcm22, mic, s0, band, frac, search_s)
        # place the sound with the device's own capture clock, not with when its packets happened to arrive
        latency_ms = cap.latency_ms(start, info["t_first"])
        return mic, s0, start, conf, (0.0 if latency_ms is None else latency_ms), info

    def _gain(self) -> float:
        return float((self.lab.applied or self.lab.requested).get("gain", 12.0))

    # -- the tests ---------------------------------------------------------
    async def _probe(self) -> dict:
        await self.update(message="Playing a 1 kHz test tone…", progress=0.1)
        tone = sa.tone(1000, 1.5, -12.0)
        mic, s0, start, conf, latency, info = await self._play_and_hear(tone, lead=1.2, band=(850.0, 1150.0), frac=0.5)
        base_seg = mic[: max(1600, s0 - 1600)]
        base_db = sa.band_rms_dbfs(base_seg, 900, 1100)
        seg = mic[start: start + int(1.5 * sa.MIC_RATE)]
        m = sa.tone_metrics(seg, 1000)
        delta = m["level_dbfs"] - base_db
        audible = conf >= HEARD_CONF and delta >= 10.0
        return {"audible": audible, "delta": round(delta, 1), "base": base_db, "metrics": m, "conf": conf, "latency": latency, "info": info}

    async def _levels(self, progress0: float, progress1: float) -> dict:
        rows, bad, xs, ys, results = [], [], [], [], []
        g = self._gain()
        for i, lvl in enumerate(LEVELS_DB):
            await self.update(message=f"Level & distortion: tone at {lvl} dBFS…", progress=progress0 + (progress1 - progress0) * i / len(LEVELS_DB))
            mic, s0, start, conf, latency, info = await self._play_and_hear(sa.tone(1000, 1.3, float(lvl)), lead=0.5, tail=0.5, band=(850.0, 1150.0), frac=0.5)
            seg = mic[start: start + int(1.3 * sa.MIC_RATE)]
            m = sa.tone_metrics(seg, 1000)
            audible = conf >= HEARD_CONF and m["snr_db"] >= 6
            results.append({"level": lvl, "m": m, "audible": audible, "under": (info.get("device") or {}).get("under")})
            rows.append([lvl, f"{m['level_dbfs']} dBFS" if audible else "not heard", f"{m['thd_pct']}%" if audible else "—",
                         f"{m['snr_db']} dB" if audible else "—", f"{m['peak_dbfs']} dBFS", (info.get("device") or {}).get("under", "—")])
            r = len(rows) - 1
            if audible:
                xs.append(lvl); ys.append(m["level_dbfs"])
                if m["thd_pct"] is not None and m["thd_pct"] > 10:
                    bad.append((r, 2))
                if m["peak_dbfs"] > -0.5:
                    bad.append((r, 4))
            if str((info.get("device") or {}).get("under", "0")) not in ("0", "—"):
                bad.append((r, 5))
        slope = None
        compress = False
        if len(xs) >= 3:
            slope = float(np.polyfit(xs, ys, 1)[0])
            if len(xs) >= 4:
                last_d, prev_d = xs[-1] - xs[-2], ys[-1] - ys[-2]
                compress = last_d > 0 and prev_d < 0.6 * last_d
        return {"rows": rows, "bad": bad, "x": xs, "y": ys, "slope": slope, "compress": compress, "results": results, "gain": g}

    async def _sweep(self, progress0: float) -> dict:
        await self.update(message="Playing a 100 Hz – 7.5 kHz sweep (4 s)…", progress=progress0)
        f1, f2, T = 100.0, 7500.0, 4.0
        marker_s, gap_s = 0.06, 0.3                                      # a noise burst marks the start, then silence, then the sweep
        # the marker is louder than the sweep (peak -8 dBFS, still under the -6 cap) so it is the loudest thing in its search window
        pcm = np.concatenate([sa.noise_burst(marker_s, -8.0), np.zeros(int(gap_s * sa.SPK_RATE), np.int16), sa.log_sweep(f1, f2, T, -18.0)])
        mic, s0, start, conf, latency, info = await self._play_and_hear(pcm, lead=0.6, tail=0.8, band=(600.0, 2800.0), frac=0.3, search_s=1.0)
        if conf < HEARD_CONF:
            return {"ok": False, "conf": conf}
        sweep_start = start + int((marker_s + gap_s) * sa.MIC_RATE)
        f, lvl = sa.sweep_response(mic, f1, f2, T, sweep_start)
        return {"ok": bool(f), "f": f, "lvl": lvl, "conf": conf, "latency": latency, "info": info}

    async def _run(self, which: str) -> None:
        self.require_streaming()
        await self.link.check()
        await self.begin("Starting…")
        tables, charts, notes, problems, warnings = [], [], [], [], []
        audible = True
        p = {"delta": 0}

        if which in ("probe", "all"):
            p = await self._probe()
            audible = p["audible"]
            m = p["metrics"]
            tables.append(table("Is the speaker audible? (1 kHz at -12 dBFS, 1.5 s)", ["Measure", "Result"],
                                [["Room level in the 1 kHz band before the tone", f"{p['base']} dBFS"],
                                 ["Tone as the microphone heard it", f"{m['level_dbfs']} dBFS" if p["audible"] else f"not clearly heard ({m['level_dbfs']} dBFS)"],
                                 ["Rise above the room", f"{p['delta']} dB"],
                                 ["Match between what was sent and what was heard", f"{p['conf']:.2f} (1 = identical)"],
                                 ["Time from sending audio to the microphone capturing it", f"{p['latency']:.0f} ms (server → board → speaker → air → mic; device clock, a few ms)"]],
                                [] if p["audible"] else [(1, 1), (2, 1)]))
            if not audible:
                problems.append("The microphone did not hear the speaker. Check the amplifier's SD/gain pins and wiring (BCLK 5, LRC 4, DIN 6), "
                                "the speaker connection, and that the microphone is near the speaker and unobstructed.")
            elif p["latency"] < 0 or p["latency"] > 1500:
                warnings.append(f"The measured delay ({p['latency']:.0f} ms) is outside a plausible range; treat it with caution.")

        if audible and which in ("levels", "all"):
            lv = await self._levels(0.25 if which == "all" else 0.05, 0.7 if which == "all" else 0.95)
            tables.append(table("Level and distortion of a 1 kHz tone at rising levels", ["Sent (dBFS)", "Heard by the mic", "Harmonic distortion", "Signal-to-noise", "Mic peak", "Playback gaps"], lv["rows"], lv["bad"]))
            if lv["y"]:
                charts.append(chart("Mic level versus level sent", lv["x"], lv["y"], "sent (dBFS)", "heard (dBFS)"))
            if lv["slope"] is not None:
                notes.append(f"Slope of heard level against sent level: {lv['slope']:.2f} dB per dB (1.00 is perfectly linear).")
                if not 0.85 <= lv["slope"] <= 1.15:
                    warnings.append(f"The heard level does not track the level sent (slope {lv['slope']:.2f}): the speaker path or the microphone is not linear.")
            if lv["compress"]:
                warnings.append("The loudest step grew much less than it should: the amplifier, speaker or microphone is compressing/limiting at that level.")
            if any(b[1] == 2 for b in lv["bad"]):
                problems.append("More than 10% harmonic distortion at some level: audible distortion or clipping in the speaker path.")
            if any(b[1] == 4 for b in lv["bad"]):
                problems.append("The microphone clipped while hearing the speaker: too loud for this microphone position/gain.")
            if any(b[1] == 5 for b in lv["bad"]):
                problems.append("The device reported playback gaps (its buffer ran dry): audio was being delivered too slowly.")
            notes.append(f"Mic levels are after the firmware's digital gain (currently {lv['gain']:g}×).")
            notes.append("Distortion is measured at the microphone, so an enclosure resonance that boosts 2-5 kHz (see the frequency response) makes the "
                         "harmonics of a 1 kHz tone look larger than the speaker alone produces.")

        if audible and which in ("sweep", "all"):
            sw = await self._sweep(0.75 if which == "all" else 0.05)
            if not sw["ok"]:
                warnings.append("The sweep could not be matched to the microphone recording (too quiet or too noisy): frequency response not measured.")
            else:
                charts.append(chart("Frequency response of speaker + enclosure + microphone (0 dB = 1 kHz)", sw["f"], sw["lvl"], "frequency (Hz)", "relative level (dB)", logx=True))
                tables.append(table("Frequency response (third-octave points)", ["Frequency", "Relative level"], [[f"{int(f)} Hz", f"{l:+.1f} dB"] for f, l in zip(sw["f"], sw["lvl"])]))
                usable = [f for f, l in zip(sw["f"], sw["lvl"]) if l >= -6.0]
                if usable:
                    lo, hi = min(usable), max(usable)
                    notes.append(f"Within 6 dB of the 1 kHz level from about {int(lo)} Hz to {int(hi)} Hz.")
                    if lo > 300 or hi < 3400:
                        warnings.append(f"The usable band ({int(lo)}–{int(hi)} Hz) does not cover the speech band (300–3400 Hz).")
                speech = [l for f, l in zip(sw["f"], sw["lvl"]) if 300 <= f <= 3400]
                if speech and max(speech) - min(speech) > 12:
                    warnings.append(f"The response varies by {max(speech) - min(speech):.0f} dB across the speech band: a strong enclosure resonance or notch.")
                notes.append(f"Sweep delay from sending to the microphone capturing it: {sw['latency']:.0f} ms.")

        notes.append("This measures speaker + enclosure + microphone together, at the microphone's position on the robot, in your room.")
        notes.append("At most -6 dBFS is ever played. If the speaker is very loud at that level, lower the amplifier gain.")
        ok_text = {"probe": f"The speaker is audible: the microphone hears it {p['delta']} dB above the room." if which == "probe" else "",
                   "levels": "The heard level tracks the level sent, without clipping or heavy distortion.",
                   "sweep": "The frequency response covers the speech band.",
                   "all": "The speaker is audible, tracks level cleanly and covers the speech band."}[which]
        if problems:
            verdict = {"level": "bad", "text": " ".join(problems + warnings)}
        elif warnings:
            verdict = {"level": "warn", "text": " ".join(warnings)}
        else:
            verdict = {"level": "ok", "text": ok_text}
        await self.update(phase="done", message="Finished.", progress=1.0, tables=tables, charts=charts, notes=notes, verdict=verdict)
