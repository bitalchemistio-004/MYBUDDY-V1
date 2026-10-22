"""
Channel-balance calibration: per-channel gain trims plus a noise-floor match.

Why this exists: the two INMP441s do not have the same acoustic sensitivity
(observed: one mic 18-37 dB less sensitive than the other). Two things must be
balanced for the two meters to agree, and a gain alone can only do one of them:

* SIGNAL balance (while sound plays): a gain trim on the weak channel.
* REST balance (nothing playing): the weak mic's electrical noise is boosted by
  that same trim, so at rest it reads far louder than the healthy mic. A noise
  gate on the boosted channel pulls its floor back to the other channel's floor.

The gate opens only when the channel's own level rises clearly above its own
measured noise floor, so sound the weak mic really hears passes through at the
calibrated gain. It cannot recover speech that is buried in that mic's noise:
the weaker the mic, the more its output is just gated noise. The result says so.

How it measures: the operator stays quiet (noise floor of each channel), then
makes a steady sound from an equal distance. Both phases are captured BEFORE
any processing. The trim comes from speech-band levels with each channel's
noise subtracted; the check that follows runs the captured audio through the
same conditioner the live path uses and reports what the meters would show at
rest and during sound. Anything that makes a number unreliable is refused, or
reported as a warning, instead of being hidden.
"""
from __future__ import annotations

import json
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfiltfilt

from .protocol import SAMPLE_RATE

TRIM_MIN_DB = -20.0
TRIM_MAX_DB = 40.0
TARGET_DB = 2.0                 # "acceptable": channels within this many dB of each other

BAND_HZ = (250.0, 3400.0)       # speech band
BLOCK = SAMPLE_RATE // 10       # 100 ms blocks
MIN_ACTIVE_BLOCKS = 10          # at least 1 s of usable sound
ACTIVE_SNR = 4.0                # a block counts as sound if the louder mic is 6 dB over its noise
MIN_WEAK_SNR = 2.0              # the weaker mic must average 3 dB over its own noise
WARN_WEAK_SNR_DB = 6.0          # below this the weak mic's output is mostly noise
WARN_IMBALANCE_DB = 25.0        # a healthy pair differs by a few dB
MAX_QUIET_SPREAD = 4.0          # quiet phase: p90 block power / median must stay under this
MAX_HALF_SPREAD_DB = 4.0        # the two halves of the sound phase must agree within this

# Noise gate on a boosted channel (levels are relative to that channel's own raw noise floor)
GATE_CLOSE_DB = 2.0             # at or below floor + 2 dB: fully closed
GATE_OPEN_DB = 6.0              # at or above floor + 6 dB: fully open
LEVEL_TAU_S = 0.03              # level detector time constant
ATTACK_S = 0.008                # opening speed
RELEASE_S = 0.12                # closing speed
MIN_GATE_DB = 1.0               # a reduction smaller than this is not worth gating


class CalibrationError(ValueError):
    """The measurement cannot be trusted; the message says what to change."""


def _db(power: float) -> float:
    return 10.0 * np.log10(max(power, 1e-12))


def _dbfs_power(power: float) -> float:
    return _db(power / (32768.0 ** 2))


def block_powers(x: np.ndarray) -> np.ndarray:
    """Mean power per 100 ms block, in the speech band."""
    sos = butter(4, BAND_HZ, btype="band", fs=SAMPLE_RATE, output="sos")
    y = sosfiltfilt(sos, x.astype(np.float64))
    n = len(y) // BLOCK
    if n == 0:
        return np.zeros(0)
    return np.mean(np.square(y[: n * BLOCK].reshape(n, BLOCK)), axis=1)


def broadband_block_powers(x: np.ndarray) -> np.ndarray:
    n = len(x) // BLOCK
    if n == 0:
        return np.zeros(0)
    return np.mean(np.square(x[: n * BLOCK].astype(np.float64).reshape(n, BLOCK)), axis=1)


def _scale(x: np.ndarray, db: float) -> np.ndarray:
    if db == 0:
        return x
    y = np.rint(x.astype(np.float64) * (10.0 ** (db / 20.0)))
    return np.clip(y, -32768, 32767).astype(np.int16)


# ── the live conditioner ────────────────────────────────────────────────
@dataclass
class Conditioner:
    """
    Per-channel trim + noise-floor gate, applied to audio as it arrives.

    Settings persist to `path`. `noise_rms` is each channel's RAW broadband
    noise floor (int16 units) from the last calibration; the gate reduction is
    derived from it and the trims, so a manual trim keeps the rest balance.
    """
    left_db: float = 0.0
    right_db: float = 0.0
    gate_enabled: bool = True
    noise_rms: dict | None = None
    path: Path | None = None
    _state: dict = field(default_factory=dict, repr=False)

    # settings ---------------------------------------------------------
    @classmethod
    def load(cls, path: Path | None) -> "Conditioner":
        c = cls(path=path)
        if path and path.is_file():
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                c.left_db, c.right_db = _clamp(float(d["left_db"])), _clamp(float(d["right_db"]))
                c.gate_enabled = bool(d.get("gate_enabled", True))
                nr = d.get("noise_rms")
                if nr and float(nr["left"]) > 0 and float(nr["right"]) > 0:
                    c.noise_rms = {"left": float(nr["left"]), "right": float(nr["right"])}
            except (ValueError, KeyError, TypeError, OSError):
                c.left_db = c.right_db = 0.0             # unreadable file => uncalibrated, never guess
                c.noise_rms = None
        return c

    def _save(self) -> None:
        if self.path:
            self.path.write_text(json.dumps({"left_db": self.left_db, "right_db": self.right_db,
                                             "gate_enabled": self.gate_enabled, "noise_rms": self.noise_rms}, indent=2),
                                 encoding="utf-8")

    def set_trims(self, left_db: float, right_db: float) -> None:
        for v in (left_db, right_db):
            if not (TRIM_MIN_DB <= v <= TRIM_MAX_DB):
                raise CalibrationError(f"trim must be between {TRIM_MIN_DB:.0f} and +{TRIM_MAX_DB:.0f} dB")
        self.left_db, self.right_db = round(float(left_db), 2), round(float(right_db), 2)
        self._state.clear()
        self._save()

    def set_noise(self, noise_rms: dict | None) -> None:
        self.noise_rms = None if noise_rms is None else {"left": float(noise_rms["left"]), "right": float(noise_rms["right"])}
        self._state.clear()
        self._save()

    def set_gate(self, enabled: bool) -> None:
        self.gate_enabled = bool(enabled)
        self._state.clear()
        self._save()

    def copy_with(self, left_db=None, right_db=None, gate_enabled=None, noise_rms="keep") -> "Conditioner":
        """A fresh, unsaved conditioner (clean detector state) for offline evaluation."""
        return Conditioner(self.left_db if left_db is None else left_db,
                           self.right_db if right_db is None else right_db,
                           self.gate_enabled if gate_enabled is None else gate_enabled,
                           self.noise_rms if noise_rms == "keep" else noise_rms)

    def trim(self, ch: str) -> float:
        return self.left_db if ch == "left" else self.right_db

    def reductions(self) -> dict:
        """dB the gate takes off each channel when it is closed (0 = no gate on that channel)."""
        none = {"left": 0.0, "right": 0.0}
        if not (self.gate_enabled and self.noise_rms):
            return none
        post = {ch: 20.0 * np.log10(self.noise_rms[ch]) + self.trim(ch) for ch in ("left", "right")}
        floor = min(post.values())
        return {ch: (round(post[ch] - floor, 2) if post[ch] - floor >= MIN_GATE_DB else 0.0) for ch in post}

    def as_dict(self) -> dict:
        return {"left_db": self.left_db, "right_db": self.right_db, "gate_enabled": self.gate_enabled,
                "gate_reduction_db": self.reductions(),
                "noise_dbfs_raw": None if not self.noise_rms else
                {k: round(20.0 * np.log10(v / 32768.0), 1) for k, v in self.noise_rms.items()}}

    # processing -------------------------------------------------------
    def process_channel(self, ch: str, x: np.ndarray) -> np.ndarray:
        """Trim (and gate) one channel's next block of samples. Bit-exact when trim 0 and no gate."""
        trim, red = self.trim(ch), self.reductions()[ch]
        if red == 0.0:
            self._state.pop(ch, None)
            return _scale(x, trim)
        n = len(x)
        if n == 0:
            return x
        floor_pow = self.noise_rms[ch] ** 2
        st = self._state.get(ch)
        if st is None:
            st = self._state[ch] = [floor_pow, red]            # start closed
        dt = n / SAMPLE_RATE
        a = np.exp(-dt / LEVEL_TAU_S)
        st[0] = a * st[0] + (1.0 - a) * float(np.mean(np.square(x.astype(np.float64))))
        over = _db(st[0] / floor_pow)
        open_frac = min(1.0, max(0.0, (over - GATE_CLOSE_DB) / (GATE_OPEN_DB - GATE_CLOSE_DB)))
        target = red * (1.0 - open_frac)
        b = np.exp(-dt / (ATTACK_S if target < st[1] else RELEASE_S))
        new_r = b * st[1] + (1.0 - b) * target
        g0, g1 = 10.0 ** ((trim - st[1]) / 20.0), 10.0 ** ((trim - new_r) / 20.0)
        st[1] = new_r
        y = np.rint(x.astype(np.float64) * np.linspace(g0, g1, n))
        return np.clip(y, -32768, 32767).astype(np.int16)

    def apply(self, frame):
        """Return the frame with each present channel conditioned."""
        from dataclasses import replace
        left = self.process_channel("left", frame.left) if frame.left is not None else None
        right = self.process_channel("right", frame.right) if frame.right is not None else None
        return replace(frame, left=left, right=right)


def _clamp(v: float) -> float:
    return max(TRIM_MIN_DB, min(TRIM_MAX_DB, v))


def process_full(cond: Conditioner, ch: str, x: np.ndarray, chunk: int = 256) -> np.ndarray:
    """Run a whole capture through a conditioner in packet-sized pieces, exactly as the live path does."""
    if len(x) == 0:
        return x
    return np.concatenate([cond.process_channel(ch, x[i:i + chunk]) for i in range(0, len(x), chunk)])


def evaluate(cond: Conditioner, quiet_l, quiet_r, sound_l, sound_r, active: np.ndarray) -> dict:
    """
    What the meters would show with `cond` on the captured audio: broadband level
    of each channel over the quiet phase (rest) and over the sound blocks.
    """
    rest, sound = {}, {}
    for ch, q, s in (("left", quiet_l, sound_l), ("right", quiet_r, sound_r)):
        c = cond.copy_with()
        pq = process_full(c, ch, q)
        rest[ch] = _dbfs_power(float(np.mean(np.square(pq.astype(np.float64)))))
        c = cond.copy_with()
        ps = broadband_block_powers(process_full(c, ch, s))
        m = min(len(ps), len(active))
        sound[ch] = _dbfs_power(float(np.mean(ps[:m][active[:m]])))
    return {"rest_dbfs": {k: round(v, 1) for k, v in rest.items()},
            "sound_dbfs": {k: round(v, 1) for k, v in sound.items()},
            "rest_diff_db": round(rest["right"] - rest["left"], 2),
            "sound_diff_db": round(sound["right"] - sound["left"], 2),
            "balanced": bool(abs(rest["right"] - rest["left"]) <= TARGET_DB and abs(sound["right"] - sound["left"]) <= TARGET_DB)}


# ── measurement ─────────────────────────────────────────────────────────
def measure(quiet_l, quiet_r, sound_l, sound_r, trim_left_db: float = 0.0, trim_right_db: float = 0.0,
            current: Conditioner | None = None, gate: bool = True) -> dict:
    """
    Compare the two channels. Inputs are RAW (untrimmed) int16 sample arrays.

    `current` is the conditioner active when the audio was captured (default: the
    given trims, no gate). Returns the signal imbalance, what the meters showed
    with `current` (`before`), and the settings that balance them plus what the
    meters would show with those (`after`).
    """
    if current is None:
        current = Conditioner(trim_left_db, trim_right_db, gate_enabled=False)
    ql, qr = block_powers(quiet_l), block_powers(quiet_r)
    sl, sr = block_powers(sound_l), block_powers(sound_r)
    if min(len(ql), len(qr)) < 10:
        raise CalibrationError("Not enough audio in the quiet phase. Check the device is streaming both microphones.")
    if min(len(sl), len(sr)) < MIN_ACTIVE_BLOCKS:
        raise CalibrationError("Not enough audio in the sound phase. Check the device is streaming both microphones.")

    noise = {"left": float(np.median(ql)), "right": float(np.median(qr))}
    for ch, q in (("left", ql), ("right", qr)):
        if noise[ch] <= 0:
            raise CalibrationError(f"The {ch} channel is digitally silent — nothing to calibrate.")
        if np.percentile(q, 90) > MAX_QUIET_SPREAD * noise[ch]:
            raise CalibrationError("The room was not steady during the quiet phase — stay silent for the first seconds and retry.")

    n = min(len(sl), len(sr))
    sl, sr = sl[:n], sr[:n]
    sig_l = np.maximum(sl - noise["left"], 0.0)
    sig_r = np.maximum(sr - noise["right"], 0.0)
    active = (sl > ACTIVE_SNR * noise["left"]) | (sr > ACTIVE_SNR * noise["right"])
    if int(active.sum()) < MIN_ACTIVE_BLOCKS:
        raise CalibrationError("Not enough sound above the noise floor — make a louder, steadier sound (talk continuously or play noise) closer to the robot.")

    weak = "right" if sig_r[active].sum() < sig_l[active].sum() else "left"
    weak_p, weak_n = (sr, noise["right"]) if weak == "right" else (sl, noise["left"])
    weak_snr = float(np.mean(weak_p[active]) / weak_n)
    if weak_snr < MIN_WEAK_SNR:
        raise CalibrationError(
            f"The {weak} microphone barely rises above its own noise ({_db(weak_snr):.1f} dB) while the sound is playing, "
            "so its sensitivity cannot be measured reliably. Bring the sound closer or make it louder.")

    def diff(idx) -> float:
        a, b = sig_l[idx].sum(), sig_r[idx].sum()
        if a <= 0 or b <= 0:
            raise CalibrationError("One channel shows no signal at all during the sound — check it is connected.")
        return _db(b / a)

    raw_diff = diff(active)                                # right minus left, signal power, dB
    idx = np.flatnonzero(active)
    half = len(idx) // 2
    spread = abs(diff(idx[:half]) - diff(idx[half:])) if half >= 3 else 0.0
    if spread > MAX_HALF_SPREAD_DB:
        raise CalibrationError(f"The imbalance changed by {spread:.1f} dB during the measurement — keep the sound source steady and centred, then retry.")
    if abs(raw_diff) > TRIM_MAX_DB:
        raise CalibrationError(f"The imbalance ({abs(raw_diff):.1f} dB) is larger than the +{TRIM_MAX_DB:.0f} dB trim limit — the microphone is effectively faulty.")

    # raw broadband noise floors (what the gate compares against)
    raw_noise = {"left": float(np.sqrt(np.median(broadband_block_powers(quiet_l)))),
                 "right": float(np.sqrt(np.median(broadband_block_powers(quiet_r))))}

    signal_trim = round(abs(raw_diff), 1) if abs(raw_diff) >= 0.05 else 0.0

    def build(extra: float) -> Conditioner:
        t = min(TRIM_MAX_DB, max(0.0, signal_trim + extra))
        return Conditioner(t if weak == "left" else 0.0, t if weak == "right" else 0.0,
                           gate_enabled=gate, noise_rms=raw_noise)

    strong = "left" if weak == "right" else "right"

    def err(extra: float) -> float:                         # weak minus strong, dB, during sound (0 = matched)
        e = evaluate(build(extra), quiet_l, quiet_r, sound_l, sound_r, active)
        return e["sound_dbfs"][weak] - e["sound_dbfs"][strong]

    extra = 0.0
    if gate and signal_trim > 0:                            # the gate costs some weak-mic signal; win it back with gain
        lo, hi = -3.0, min(15.0, TRIM_MAX_DB - signal_trim)
        if err(lo) >= 0:
            extra = lo
        elif err(hi) <= 0:
            extra = hi
        else:
            for _ in range(9):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if err(mid) < 0 else (lo, mid)
            extra = (lo + hi) / 2
    proposed = build(extra)
    proposed.left_db, proposed.right_db = round(proposed.left_db, 1), round(proposed.right_db, 1)

    before = evaluate(current, quiet_l, quiet_r, sound_l, sound_r, active)
    after = evaluate(proposed, quiet_l, quiet_r, sound_l, sound_r, active)
    residual_now = raw_diff + current.right_db - current.left_db

    warnings = []
    if _db(weak_snr) < WARN_WEAK_SNR_DB:
        warnings.append(f"The {weak} mic's signal is only {_db(weak_snr):.1f} dB above its own noise while sound plays, so its output is mostly "
                        "noise. The balance below is cosmetic: level and direction readings, and speech-to-text, on that channel stay poor.")
    if abs(raw_diff) > WARN_IMBALANCE_DB:
        warnings.append(f"A {abs(raw_diff):.0f} dB imbalance is far beyond a healthy microphone pair (a few dB). Check the {weak} mic "
                        "(sound port, moisture, soldering) or replace it.")
    if gate and proposed.reductions()[weak] > 0 and after["balanced"] is False:
        warnings.append("The gate and trim could not bring both the at-rest and the during-sound levels within "
                        f"{TARGET_DB:g} dB — see the table for which one is off.")

    return {
        "raw_diff_db": round(raw_diff, 2),                 # right minus left, signal power, before any processing
        "weak": weak if abs(raw_diff) >= 0.05 else None,
        "residual_with_current_trims_db": round(residual_now, 2),
        "signal_trim_db": {"left": signal_trim if weak == "left" else 0.0, "right": signal_trim if weak == "right" else 0.0},
        "proposed_trim_db": {"left": proposed.left_db, "right": proposed.right_db},
        "gate_reduction_db": proposed.reductions(),
        "noise_rms_raw": raw_noise,
        "noise_dbfs": {k: round(_dbfs_power(v), 1) for k, v in noise.items()},
        "noise_dbfs_after_proposed": {"left": round(_dbfs_power(noise["left"]) + proposed.left_db, 1),
                                      "right": round(_dbfs_power(noise["right"]) + proposed.right_db, 1)},
        "before": before,
        "after": after,
        "weak_channel_snr_db": round(_db(weak_snr), 1),
        "active_seconds": round(int(active.sum()) * BLOCK / SAMPLE_RATE, 1),
        "consistency_db": round(spread, 2),
        "target_db": TARGET_DB,
        "within_target_now": bool(abs(residual_now) <= TARGET_DB),
        "gate": bool(gate),
        "warnings": warnings,
    }


def save_run(path: Path, quiet_l, quiet_r, sound_l, sound_r) -> None:
    """Keep the raw capture of the last calibration (quiet phase, then sound phase) for inspection."""
    l, r = np.concatenate([quiet_l, sound_l]), np.concatenate([quiet_r, sound_r])
    st = np.empty(len(l) * 2, dtype="<i2")
    st[0::2], st[1::2] = l, r
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
        w.writeframes(st.tobytes())


class Collector:
    """Gathers RAW samples for one calibration run: a quiet phase, then a sound phase."""

    def __init__(self, quiet_s: float = 2.0, sound_s: float = 6.0) -> None:
        self.quiet_s, self.sound_s = quiet_s, sound_s
        self.reset()

    def reset(self) -> None:
        self.phase = "idle"        # idle | quiet | sound | processing | done | failed
        self.apply = False
        self.started = 0.0
        self.message: str | None = None
        self.result: dict | None = None
        self._buf = {"quiet": ([], []), "sound": ([], [])}

    def begin(self, apply: bool, now: float) -> None:
        self.reset()
        self.apply, self.phase, self.started = apply, "quiet", now

    def enter_sound(self, now: float) -> None:
        self.phase, self.started = "sound", now

    @property
    def running(self) -> bool:
        return self.phase in ("quiet", "sound", "processing")

    def feed(self, frame) -> None:
        if self.phase in ("quiet", "sound") and frame.left is not None and frame.right is not None:
            l, r = self._buf[self.phase]
            l.append(frame.left.copy())
            r.append(frame.right.copy())

    def arrays(self) -> tuple[np.ndarray, ...]:
        def cat(parts):
            return np.concatenate(parts) if parts else np.zeros(0, np.int16)
        return (cat(self._buf["quiet"][0]), cat(self._buf["quiet"][1]),
                cat(self._buf["sound"][0]), cat(self._buf["sound"][1]))

    def remaining(self, now: float) -> float | None:
        span = {"quiet": self.quiet_s, "sound": self.sound_s}.get(self.phase)
        return None if span is None else max(0.0, span - (now - self.started))
