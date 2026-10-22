"""
Signal analysis for the speaker bench: stimuli, and what the microphone heard.

The robot plays a known sound and its own microphone records it, so what is
measured is the whole chain — amplifier, speaker, the robot's enclosure, the air
gap and the microphone — not the speaker alone. That is the chain that matters
for echo and barge-in, and it is stated as such wherever a result is shown.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import hilbert, resample_poly

MIC_RATE = 16000
SPK_RATE = 22050
FULL = 32768.0


def db(x: float) -> float:
    return float(20.0 * np.log10(max(x, 1e-12)))


def tone(freq: float, seconds: float, level_db: float, rate: int = SPK_RATE, fade_s: float = 0.02) -> np.ndarray:
    """int16 sine at `level_db` dBFS (peak), with short fades so it does not click."""
    n = int(seconds * rate)
    t = np.arange(n) / rate
    x = np.sin(2 * np.pi * freq * t) * 10 ** (level_db / 20.0)
    f = max(1, int(fade_s * rate))
    env = np.ones(n)
    env[:f] = np.linspace(0, 1, f)
    env[-f:] = np.linspace(1, 0, f)
    return np.round(x * env * (FULL - 1)).astype(np.int16)


def log_sweep(f1: float, f2: float, seconds: float, level_db: float, rate: int = SPK_RATE, fade_s: float = 0.05) -> np.ndarray:
    """Exponential (log) sine sweep f1 -> f2 Hz."""
    n = int(seconds * rate)
    t = np.arange(n) / rate
    k = np.log(f2 / f1)
    phase = 2 * np.pi * f1 * seconds / k * (np.exp(t / seconds * k) - 1)
    x = np.sin(phase) * 10 ** (level_db / 20.0)
    f = max(1, int(fade_s * rate))
    env = np.ones(n)
    env[:f] = np.linspace(0, 1, f)
    env[-f:] = np.linspace(1, 0, f)
    return np.round(x * env * (FULL - 1)).astype(np.int16)


def sweep_freq_at(t: np.ndarray | float, f1: float, f2: float, seconds: float):
    return f1 * (f2 / f1) ** (np.asarray(t) / seconds)


def to_mic_rate(x: np.ndarray) -> np.ndarray:
    """Resample speaker-rate audio (22.05 kHz) to the microphone rate (16 kHz) as float."""
    return resample_poly(x.astype(np.float64), 320, 441)


# ── one tone ────────────────────────────────────────────────────────────
def tone_metrics(mic: np.ndarray, f0: float, rate: int = MIC_RATE, harmonics: int = 5) -> dict:
    """
    Level, distortion and signal-to-noise of a steady tone in a mic recording.

    level_dbfs: RMS level of the fundamental. thd_pct: harmonics 2..N relative to it.
    snr_db: fundamental vs everything else in 100 Hz .. 7.5 kHz.
    """
    x = mic.astype(np.float64)
    x = x[int(0.15 * rate): len(x) - int(0.15 * rate)]                 # steady part only
    if len(x) < rate // 4:
        raise ValueError("recording too short to analyse")
    x = x - x.mean()
    n = 1 << int(np.ceil(np.log2(len(x))))
    w = np.hanning(len(x))
    spec = np.abs(np.fft.rfft(x * w, n)) ** 2
    freqs = np.fft.rfftfreq(n, 1.0 / rate)
    hz = freqs[1] - freqs[0]
    span = max(3, int(round(4 * rate / len(x) / hz)))                   # a few window main-lobe widths

    def band_power(f: float) -> float:
        i = int(round(f / hz))
        lo, hi = max(0, i - span), min(len(spec), i + span + 1)
        return float(spec[lo:hi].sum())

    p1 = band_power(f0)
    peak_near = freqs[max(0, int(f0 / hz) - span): int(f0 / hz) + span + 1][np.argmax(spec[max(0, int(f0 / hz) - span): int(f0 / hz) + span + 1])]
    ph = sum(band_power(k * f0) for k in range(2, harmonics + 1) if k * f0 < rate / 2 - 200)
    band = (freqs >= 100) & (freqs <= 7500)
    excl = np.zeros_like(band)
    for k in range(1, harmonics + 1):
        i = int(round(k * f0 / hz))
        excl[max(0, i - span): i + span + 1] = True
    pn = float(spec[band & ~excl].sum()) * (1.0)
    # convert window-weighted spectral power to an RMS amplitude of the fundamental
    norm = (np.sum(w) ** 2) / 2.0
    amp = np.sqrt(p1 / norm) if norm > 0 else 0.0
    return {"level_dbfs": round(db(amp / np.sqrt(2) / FULL), 1),
            "thd_pct": round(100.0 * np.sqrt(ph / p1), 2) if p1 > 0 else None,
            "snr_db": round(10 * np.log10(p1 / max(pn, 1e-12)), 1),
            "peak_dbfs": round(db(float(np.max(np.abs(mic))) / FULL), 1),
            "found_hz": round(float(peak_near), 1)}


def band_rms_dbfs(x: np.ndarray, lo: float, hi: float, rate: int = MIC_RATE) -> float:
    """RMS of `x` restricted to lo..hi Hz, in dBFS."""
    x = x.astype(np.float64) - np.mean(x)
    n = len(x)
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / rate)
    spec[(f < lo) | (f > hi)] = 0
    y = np.fft.irfft(spec, n)
    return round(db(float(np.sqrt(np.mean(y ** 2))) / FULL), 1)


def noise_burst(seconds: float, level_db: float, rate: int = SPK_RATE, seed: int = 5) -> np.ndarray:
    """Band-limited (500-3000 Hz) noise burst used as a timing marker before a sweep."""
    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1.0 / rate)
    spec[(f < 500) | (f > 3000)] = 0
    x = np.fft.irfft(spec, n)
    x = x / np.max(np.abs(x)) * 10 ** (level_db / 20.0)
    fade = max(1, int(0.003 * rate))
    env = np.ones(n)
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    return np.round(x * env * (FULL - 1)).astype(np.int16)


def onset(mic: np.ndarray, pre_samples: int, band: tuple[float, float], frac: float = 0.4,
          rate: int = MIC_RATE, smooth_ms: float = 10.0) -> tuple[int, float]:
    """
    When does a sound start in `band`, and how sure are we it is really there?

    `mic[:pre_samples]` must be background only (before anything was played). Returns
    (index of the onset, confidence 0..1). Confidence is how far the loudest part of the band
    envelope rises above the loudest BACKGROUND envelope, so pure noise scores about 0.
    Envelope-based, so it is insensitive to the small clock drift between speaker and microphone
    that would smear a waveform cross-correlation.
    """
    x = mic.astype(np.float64)
    x = x - np.mean(x[:max(1, pre_samples)])
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1.0 / rate)
    spec[(f < band[0]) | (f > band[1])] = 0
    y = np.fft.irfft(spec, len(x))
    env = np.abs(hilbert(y))
    w = max(1, int(smooth_ms / 1000.0 * rate))
    env = np.convolve(env, np.ones(w) / w, mode="same")
    pre = env[w: max(w + 1, pre_samples - w)]                     # drop the filter's edge effects
    bg = float(np.max(pre)) if pre.size else 0.0
    post = env[pre_samples:]
    if post.size == 0:
        return pre_samples, 0.0
    peak = float(np.max(post))
    if peak <= 0:
        return pre_samples, 0.0
    conf = max(0.0, min(1.0, (peak - bg) / peak))
    thr = bg + frac * (peak - bg)
    idx = int(np.argmax(post >= thr))
    return pre_samples + idx, conf


# ── delay and frequency response ────────────────────────────────────────
def find_delay(played: np.ndarray, mic: np.ndarray, max_delay: int) -> tuple[int, float]:
    """
    Where does `played` (16 kHz float) show up in `mic`? Returns (delay in samples, confidence 0..1)
    from the peak of the normalised cross-correlation.
    """
    p = played.astype(np.float64) - np.mean(played)
    m = mic.astype(np.float64) - np.mean(mic)
    n = 1 << int(np.ceil(np.log2(len(m) + len(p))))
    cc = np.fft.irfft(np.fft.rfft(m, n) * np.conj(np.fft.rfft(p, n)), n)
    cc = cc[: max_delay + 1]
    if cc.size == 0:
        return 0, 0.0
    k = int(np.argmax(cc))
    denom = np.sqrt(np.sum(p ** 2) * np.sum(m[k: k + len(p)] ** 2))
    return k, float(cc[k] / denom) if denom > 0 else 0.0


THIRD_OCTAVE = [125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300]


def sweep_response(mic: np.ndarray, f1: float, f2: float, seconds: float, delay: int, rate: int = MIC_RATE) -> tuple[list[float], list[float]]:
    """
    Level versus frequency from a log sweep: for each moment, the mic's level at the frequency the sweep was
    at (minus the acoustic delay), averaged into third-octave points. Relative: 0 dB is the 800-1250 Hz average.
    """
    win = int(0.06 * rate)
    hop = int(0.015 * rate)
    x = mic.astype(np.float64)
    freqs, levels = [], []
    w = np.hanning(win)
    n = 1 << int(np.ceil(np.log2(win * 4)))
    fb = np.fft.rfftfreq(n, 1.0 / rate)
    for start in range(delay, delay + int(seconds * rate) - win, hop):
        if start + win > len(x):
            break
        t_mid = (start + win / 2 - delay) / rate
        f = float(sweep_freq_at(t_mid, f1, f2, seconds))
        seg = x[start:start + win]
        mag = np.abs(np.fft.rfft(seg * w, n)) ** 2
        sel = (fb >= f * 0.93) & (fb <= f * 1.07)
        if not sel.any():
            continue
        freqs.append(f)
        levels.append(10 * np.log10(mag[sel].sum() + 1e-9))
    freqs, levels = np.array(freqs), np.array(levels)
    out_f, out_l = [], []
    for c in THIRD_OCTAVE:
        m = (freqs >= c / 2 ** (1 / 6)) & (freqs < c * 2 ** (1 / 6))
        if m.sum() >= 2 and f1 * 0.95 <= c <= f2:
            out_f.append(float(c))
            out_l.append(float(np.mean(levels[m])))
    if not out_f:
        return [], []
    ref = [l for f, l in zip(out_f, out_l) if 800 <= f <= 1250]
    base = float(np.mean(ref)) if ref else float(np.max(out_l))
    return out_f, [round(l - base, 1) for l in out_l]
