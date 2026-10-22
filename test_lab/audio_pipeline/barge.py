"""
Production's barge-in rule, replicated exactly, for the echo bench.

Source: firmware/mbuddy/src/MBUDDY.ino, `canTransmitMic()`. Per 256-sample mic
buffer the firmware takes the PEAK of the conditioned signal and, while the robot
is speaking (playing, locked out, or in the post-speech grace period):

    threshold = ambientNoiseFloor * 3.2            (BARGE_IN_MULTIPLIER)
    threshold *= 6.5 while playing / locked out    (BARGE_SELF_REJECT_MULT)
    threshold = max(threshold, 5200)               (MIN_BARGE_THRESHOLD)
    interrupt after 6 consecutive frames above it  (BARGE_CONSECUTIVE_FRAMES)

While NOT speaking, the noise floor tracks the peak slowly:
    ambient = ambient * (1 - 0.02) + peak * 0.02   when peak > 50   (NOISE_FLOOR_ADAPT)
and it starts at 500. The constants below mirror the firmware; if they change
there they must change here (the echo bench prints them so a mismatch is visible).
"""
from __future__ import annotations

import numpy as np

FRAME = 256
MIN_BARGE_THRESHOLD = 5200
BARGE_IN_MULTIPLIER = 3.2
BARGE_SELF_REJECT_MULT = 6.5
BARGE_CONSECUTIVE_FRAMES = 6
NOISE_FLOOR_ADAPT = 0.02
INITIAL_AMBIENT = 500.0
PRODUCTION_GAIN = 12.0            # the firmware's digital mic gain (MIC_GAIN in readMicPcm)


def frame_peaks(left: np.ndarray | None, right: np.ndarray | None, lab_gain: float, mode: str = "auto") -> np.ndarray:
    """
    Per-256-sample peak as the production firmware would compute it, from the lab's raw channels.

    The lab applies `lab_gain` to each channel; production applies 12x after picking the LOUDER
    microphone per sample. `mode='loudest'` reproduces that pick; `mode='auto'` uses the left mic
    only (use it when the right one is not trustworthy).
    """
    scale = PRODUCTION_GAIN / lab_gain
    if mode == "loudest" and left is not None and right is not None:
        n = min(len(left), len(right))
        l, r = left[:n].astype(np.float64), right[:n].astype(np.float64)
        x = np.where(np.abs(r) > np.abs(l), r, l)
    else:
        src = left if left is not None else right
        x = src.astype(np.float64)
    x = np.clip(x * scale, -32768, 32767)
    n = len(x) // FRAME
    return np.abs(x[: n * FRAME]).reshape(n, FRAME).max(axis=1)


def simulate(peaks: np.ndarray, speaking: np.ndarray, ambient: float = INITIAL_AMBIENT) -> dict:
    """
    Run the firmware's rule over per-frame peaks. `speaking[i]` is True while the robot is outputting audio.
    Returns the frames at which it would interrupt itself, the threshold in force per frame and the final noise floor.
    """
    triggers, thresholds = [], []
    count = 0
    for i, (peak, spk) in enumerate(zip(peaks, speaking)):
        if spk:
            thr = max(ambient * BARGE_IN_MULTIPLIER * BARGE_SELF_REJECT_MULT, MIN_BARGE_THRESHOLD)
            thresholds.append(thr)
            if peak > int(thr):
                count += 1
                if count >= BARGE_CONSECUTIVE_FRAMES:
                    triggers.append(i)
                    count = 0
            else:
                count = 0
        else:
            thresholds.append(max(ambient * BARGE_IN_MULTIPLIER, MIN_BARGE_THRESHOLD))
            count = 0
            if peak > 50:
                ambient = ambient * (1.0 - NOISE_FLOOR_ADAPT) + float(peak) * NOISE_FLOOR_ADAPT
    return {"triggers": triggers, "thresholds": thresholds, "ambient": ambient}


def threshold_while_speaking(ambient: float) -> float:
    return max(ambient * BARGE_IN_MULTIPLIER * BARGE_SELF_REJECT_MULT, MIN_BARGE_THRESHOLD)


def recommend(echo_peak: float, user_peak: float) -> dict:
    """
    Can a peak threshold tell the robot's own echo from someone talking over it?
    Needs the user's peak to clear the echo's by a workable margin (about 6 dB either side of a threshold).
    """
    if echo_peak <= 0 or user_peak <= 0:
        return {"separable": False, "reason": "no signal measured"}
    sep_db = 20 * np.log10(user_peak / echo_peak)
    lo, hi = echo_peak * 1.3, user_peak * 0.7
    return {"separable": bool(hi > lo), "separation_db": round(float(sep_db), 1),
            "threshold_range": (int(lo), int(hi)) if hi > lo else None}
