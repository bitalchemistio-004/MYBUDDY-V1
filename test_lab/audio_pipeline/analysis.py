"""
Signal analysis for the audio lab: levels and left/right direction cues.

Everything here is computed from PCM that really arrived from the device. When
there is not enough signal to say anything (silence, one channel missing) the
functions return None rather than a plausible-looking number.

Direction is estimated two independent ways, because the two-microphone
geometry supports both and they fail differently:

* Time difference of arrival (TDOA): sound reaches the nearer mic first. The
  lag that best aligns the two channels gives the bearing via the mic spacing.
  Robust to gain mismatch between the mics; needs broadband sound.
* Level difference (ILD): the louder side is the nearer side. Always
  available, but only a *lean* — turning it into degrees needs a calibration
  for the actual mic pair and enclosure, which the lab does not have.
"""
from __future__ import annotations

import time

import numpy as np

FULL_SCALE = 32768.0
FLOOR_DB = -120.0

# Below this RMS (dBFS, per channel) a direction estimate is refused: it would
# be correlating noise floor with noise floor.
MIN_DIRECTION_DBFS = -55.0
MAX_LAG_SAMPLES = 16          # search window; physical limit is far smaller at ~10 cm
DIRECTION_WINDOW = 2048       # samples (128 ms at 16 kHz) used for each estimate
LEVEL_WINDOW = 1600           # samples (100 ms) used for each level reading
CHANNEL_STALE_S = 0.5         # a channel that has not received data for this long is "inactive"


def dbfs(x: np.ndarray) -> float:
    """RMS level of an int16 array in dB relative to full scale."""
    if x is None or x.size == 0:
        return FLOOR_DB
    rms = float(np.sqrt(np.mean(np.square(x.astype(np.float64))))) / FULL_SCALE
    return max(FLOOR_DB, 20.0 * np.log10(rms)) if rms > 0 else FLOOR_DB


def peak_dbfs(x: np.ndarray) -> float:
    if x is None or x.size == 0:
        return FLOOR_DB
    peak = float(np.max(np.abs(x.astype(np.int32)))) / FULL_SCALE
    return max(FLOOR_DB, 20.0 * np.log10(peak)) if peak > 0 else FLOOR_DB


class Ring:
    """Fixed-size sample history for one channel."""

    def __init__(self, size: int) -> None:
        self._buf = np.zeros(size, dtype=np.int16)
        self._filled = 0
        self.last_push = 0.0

    def push(self, samples: np.ndarray, now: float | None = None) -> None:
        n = len(samples)
        if n >= len(self._buf):
            self._buf[:] = samples[-len(self._buf):]
        else:
            self._buf[:-n] = self._buf[n:]
            self._buf[-n:] = samples
        self._filled = min(len(self._buf), self._filled + n)
        self.last_push = time.monotonic() if now is None else now

    def latest(self, n: int) -> np.ndarray:
        n = min(n, self._filled)
        return self._buf[len(self._buf) - n:]

    def active(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return self._filled > 0 and (now - self.last_push) < CHANNEL_STALE_S

    def clear(self) -> None:
        self._filled = 0
        self.last_push = 0.0


def levels(left: Ring, right: Ring, now: float | None = None) -> dict:
    """Per-channel and combined levels over the last LEVEL_WINDOW samples."""
    now = time.monotonic() if now is None else now
    out: dict = {"left": None, "right": None, "both": None}
    l_act, r_act = left.active(now), right.active(now)
    lx = left.latest(LEVEL_WINDOW) if l_act else None
    rx = right.latest(LEVEL_WINDOW) if r_act else None
    if lx is not None:
        out["left"] = {"rms": dbfs(lx), "peak": peak_dbfs(lx)}
    if rx is not None:
        out["right"] = {"rms": dbfs(rx), "peak": peak_dbfs(rx)}
    if lx is not None and rx is not None:
        n = min(len(lx), len(rx))
        mix = (lx[-n:].astype(np.int32) + rx[-n:].astype(np.int32)) // 2
        out["both"] = {"rms": dbfs(mix), "peak": peak_dbfs(mix)}
    return out


def estimate_lag(l: np.ndarray, r: np.ndarray, max_lag: int = MAX_LAG_SAMPLES) -> tuple[float, float] | None:
    """
    Sub-sample lag between two channels and how well they correlate there.

    Sign convention: lag > 0 means the RIGHT channel is delayed relative to the
    LEFT, i.e. the sound reached the left mic first (source on the left).
    Returns (lag_samples, correlation 0..1), or None if either channel is flat.
    """
    l = l.astype(np.float64) - float(np.mean(l))
    r = r.astype(np.float64) - float(np.mean(r))
    n = min(len(l), len(r))
    if n <= 2 * max_lag + 1:
        return None
    l, r = l[-n:], r[-n:]
    if not np.any(l) or not np.any(r):
        return None

    corr = np.empty(2 * max_lag + 1)
    for i, k in enumerate(range(-max_lag, max_lag + 1)):
        a, b = (l[:n - k], r[k:]) if k >= 0 else (l[-k:], r[:n + k])
        denom = np.sqrt(np.dot(a, a) * np.dot(b, b))
        corr[i] = np.dot(a, b) / denom if denom > 0 else 0.0

    peak = int(np.argmax(corr))
    lag = float(peak - max_lag)
    if 0 < peak < len(corr) - 1:                       # parabolic sub-sample refinement
        y0, y1, y2 = corr[peak - 1], corr[peak], corr[peak + 1]
        curve = y0 - 2 * y1 + y2
        if curve < 0:
            lag += 0.5 * (y0 - y2) / curve
    return lag, float(np.clip(corr[peak], 0.0, 1.0))


def direction(left: Ring, right: Ring, now: float | None = None) -> dict | None:
    """
    Direction cues from the most recent audio, or None when they cannot be
    trusted (a mic is not transmitting, or the room is effectively silent).
    """
    now = time.monotonic() if now is None else now
    if not (left.active(now) and right.active(now)):
        return None
    l, r = left.latest(DIRECTION_WINDOW), right.latest(DIRECTION_WINDOW)
    n = min(len(l), len(r))
    if n < DIRECTION_WINDOW // 2:
        return None
    l, r = l[-n:], r[-n:]
    l_db, r_db = dbfs(l), dbfs(r)
    if max(l_db, r_db) < MIN_DIRECTION_DBFS:
        return None

    result = {"ild_db": round(r_db - l_db, 2), "lag_samples": None, "corr": None}
    lag = estimate_lag(l, r)
    if lag is not None:
        result["lag_samples"] = round(lag[0], 3)
        result["corr"] = round(lag[1], 3)
    return result
