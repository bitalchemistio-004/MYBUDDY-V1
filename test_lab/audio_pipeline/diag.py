"""
Wiring diagnostic: bit-level analysis of the RAW 32-bit I2S words.

A healthy INMP441 sends 24 data bits MSB-first, left-justified in a 32-bit slot.
On the real hardware this lab was built for, bits 6..0 are always 0 and bit 7
simply repeats bit 8 (both slots, measured), so only bits 6..0 count as "low
bits". In a quiet room the value stays small, so the top 4 bits of the word are
all equal (sign extension: within +/-12.5% of full scale). Problems on the shared
bus leave characteristic fingerprints in exactly those bits:

* lost word-select (WS) or clock (SCK) sync   -> data lands in the wrong bit
  positions: full-scale-looking values appear and/or the low bits fill up
  (the low-bits test does not depend on how loud the room is);
* a mic that is not driving the data line (SD) -> the slot reads a constant, or a
  faint copy of the other slot's activity picked up by the floating line;
* interference on SD                          -> full-scale-looking garbage.

A separate check looks for a glitch that repeats every I2S buffer (256 frames,
62.5 Hz): that is synchronous with the ESP32's own DMA/CPU activity, so it points
at supply/ground/wiring coupling into one microphone rather than at sound.

These are FINGERPRINTS, not proof: the report says what the bits are consistent
with and leaves the physical check (continuity, resolder, swap wires) to you.

Wire format from the firmware, 8 chunks per capture (see firmware main.cpp):

    'D' 'G' version idx count frames_lo frames_hi 0x00   then frames*2 int32 LE (slot0, slot1)
"""
from __future__ import annotations

import numpy as np

FULL_24 = float(2 ** 23)
BIG_LIMIT = 2.0             # % of samples beyond +/-12.5% of full scale; healthy is ~0 in a quiet room
LOW_ACTIVE_LIMIT = 10.0      # % of samples with anything in bits 6..0; healthy is ~0
DMA_FRAMES = 256             # I2S DMA buffer length in the firmware (dma_buf_len); keep in sync with main.cpp
PERIODIC_LIMIT = 15.0        # % of a slot's fast-signal power repeating every DMA buffer; pure chance is ~1/periods (6% for 16)
NOISE_GAP_DB = 10.0          # slots' noise floors this far apart is worth saying
COPY_CORR = 0.8              # |correlation| between slots that suggests one is a copy of the other (a shared real sound also correlates, so keep this high)


class DiagError(ValueError):
    pass


class Assembler:
    """Collects the 'DG' chunks of one capture and returns the raw words when it is complete."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._chunks: dict[int, np.ndarray] = {}
        self._count: int | None = None

    def add(self, data: bytes) -> np.ndarray | None:
        if len(data) < 8 or data[:2] != b"DG":
            raise DiagError("not a diagnostic chunk")
        version, idx, count = data[2], data[3], data[4]
        frames = data[5] | (data[6] << 8)
        if version != 1 or count == 0 or idx >= count:
            raise DiagError(f"bad chunk header (version {version}, {idx}/{count})")
        if len(data) != 8 + frames * 2 * 4:
            raise DiagError(f"chunk length {len(data)} does not match {frames} frames")
        if self._count is not None and self._count != count:
            self.reset()                                   # a new capture started before the old one finished
        self._count = count
        self._chunks[idx] = np.frombuffer(data, dtype="<i4", offset=8).reshape(-1, 2)
        if len(self._chunks) == count:
            words = np.concatenate([self._chunks[i] for i in range(count)])
            self.reset()
            return words
        return None


def _dbfs(x: float) -> float:
    return round(float(20.0 * np.log10(max(x, 1e-9) / FULL_24)), 1)


def analyze(words: np.ndarray) -> dict:
    """Bit-structure report for a capture of shape (frames, 2): slot 0 and slot 1, raw int32."""
    words = np.asarray(words, dtype=np.int32).reshape(-1, 2)
    if len(words) < 256:
        raise DiagError("capture too short to analyse")
    slots, ac = [], []
    for s in (0, 1):
        w = words[:, s]
        u = w.astype(np.int64) & 0xFFFFFFFF
        v24 = (w >> 8).astype(np.float64)                  # arithmetic shift: the signed 24-bit sample
        top = (u >> 28) & 0xF
        centred = v24 - v24.mean()
        ac.append(centred)
        distinct = int(len(np.unique(v24)))
        slots.append({
            "big_pct": round(float(np.mean((top != 0) & (top != 0xF)) * 100), 2),
            "low_active_pct": round(float(np.mean((u & 0x7F) != 0) * 100), 2),
            "noise_dbfs": _dbfs(float(np.sqrt(np.mean(centred ** 2)))),
            "peak_dbfs": _dbfs(float(np.max(np.abs(centred)))),
            "dc_dbfs": _dbfs(abs(float(v24.mean()))),
            "distinct_values": distinct,
            "stuck": distinct <= 2,
        })

    # how alike are the two slots' fluctuations (a floating slot mirrors its neighbour)
    a, b = ac
    corr = 0.0
    if a.std() > 0 and b.std() > 0:
        corr = max(abs(float(np.corrcoef(a[3:len(a) - 3], np.roll(b, k)[3:len(b) - 3])[0, 1])) for k in range(-3, 4))

    for s in (0, 1):
        slots[s].update(_periodic(words[:, s]))
    verdicts = _verdicts(slots, corr)
    return {"frames": int(len(words)), "slots": slots, "slot_correlation": round(corr, 2), "verdicts": verdicts}


def _periodic(w: np.ndarray) -> dict:
    """How much of a slot's fast-changing signal repeats identically every I2S DMA buffer."""
    periods = len(w) // DMA_FRAMES
    if periods < 4:
        return {"periodic_pct": 0.0, "periodic_chance_pct": 100.0, "periodic_at": None}
    v = (w[: periods * DMA_FRAMES] >> 8).astype(np.float64)
    v -= np.median(v)
    fast = v - np.convolve(v, np.ones(64) / 64, mode="same")          # drop slow drift; keep glitches and hiss
    total = float(np.var(fast))
    if total <= 0:
        return {"periodic_pct": 0.0, "periodic_chance_pct": round(100.0 / periods, 1), "periodic_at": None}
    fold = fast.reshape(periods, DMA_FRAMES).mean(axis=0)
    return {"periodic_pct": round(float(np.var(fold) / total * 100), 1),
            "periodic_chance_pct": round(100.0 / periods, 1),
            "periodic_at": int(np.argmax(np.abs(fold)))}


def _verdicts(slots: list[dict], corr: float) -> list[dict]:
    out: list[dict] = []
    add = lambda level, text: out.append({"level": level, "text": text})
    name = ("slot 0", "slot 1")

    for i in (0, 1):
        o = 1 - i
        s, t = slots[i], slots[o]
        if s["stuck"]:
            add("bad", f"{name[i].capitalize()} never changes ({s['distinct_values']} distinct value). Its microphone is not driving the data line: "
                       "no clock or word-select reaching it, no power, a wrong L/R pin level, or SD shorted.")
        if s["big_pct"] > BIG_LIMIT and t["big_pct"] <= BIG_LIMIT / 4:
            add("bad", f"{name[i].capitalize()}: {s['big_pct']:.1f}% of samples are full-scale-sized (beyond ±12.5% of full scale) "
                       f"while the other slot has {t['big_pct']:.1f}%. In a quiet room that is what a microphone that loses "
                       "word-select/clock sync produces, or a data line picking up interference (if the room was not quiet, capture again).")
        if s["low_active_pct"] > LOW_ACTIVE_LIMIT and t["low_active_pct"] <= LOW_ACTIVE_LIMIT / 5:
            add("bad", f"{name[i].capitalize()}: bits 6..0 are active in {s['low_active_pct']:.0f}% of samples (the other slot: {t['low_active_pct']:.1f}%). "
                       "A healthy INMP441 leaves them at 0, so this slot's bits are arriving shifted: a word-select or clock phase/edge problem on that microphone.")
        if s["periodic_pct"] >= PERIODIC_LIMIT and s["periodic_pct"] > 2 * t["periodic_pct"]:
            add("warn", f"{name[i].capitalize()}: {s['periodic_pct']:.0f}% of its fast-changing signal repeats identically every {DMA_FRAMES}-sample I2S buffer "
                        f"(chance would be about {s['periodic_chance_pct']:.0f}%; the other slot: {t['periodic_pct']:.0f}%), with the biggest step at sample "
                        f"{s['periodic_at']} of the buffer. A glitch locked to the buffer cycle is synchronous with the ESP32's own DMA/CPU activity, so it is "
                        "being coupled into that microphone (supply, ground or wiring), not heard as sound.")

    gap = slots[1]["noise_dbfs"] - slots[0]["noise_dbfs"]
    if abs(gap) >= NOISE_GAP_DB:
        hi = 1 if gap > 0 else 0
        add("warn", f"{name[hi].capitalize()}'s fluctuation is {abs(gap):.0f} dB larger than {name[1 - hi]}'s in a quiet room "
                    f"({slots[hi]['noise_dbfs']} vs {slots[1 - hi]['noise_dbfs']} dBFS).")

    if corr >= COPY_CORR and abs(gap) >= 6:
        lo = 0 if gap > 0 else 1
        add("warn", f"{name[lo].capitalize()} looks like a weaker copy of {name[1 - lo]} (correlation {corr:.2f}). "
                    "That is what a slot with nothing driving it shows (it mirrors its neighbour through the shared data line), though a loud sound both mics hear can look similar: capture again in a quiet room.")

    if not out:
        add("ok", "Both slots have the same bit structure: top bits are proper sign extensions, bits 6..0 are idle, and neither is stuck or "
                  "a copy of the other. Nothing here points at a word-select, clock or data-line sync fault; look at analogue causes "
                  "(supply/ground noise, the microphone itself).")
    return out
