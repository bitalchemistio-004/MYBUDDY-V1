"""Small local PCM sound effects for MBUDDY companion cues."""
from __future__ import annotations

import math

import numpy as np

from tts.speaker import TARGET_SR


SFX_NAMES = {
    "chiptune_loop",
    "laser_pew",
    "jump_sweep",
    "dragon_breath",
    "data_drone",
    "data_blip",
    "cascade_arp",
    "ecg_beep",
    "bubble_pop",
    "bounce_click",
    "paddle_bounce",
    "brick_hit",
    "star_tinkle",
    "clock_tick",
    "sonar_ping",
    "charge_hum",
    "glitch_screech",
    "sleep_dream",
    "success_chime",
    "listen_ping",
}

SFX_TARGET_PEAK = 0.92
SFX_DRIVE = 1.85
SFX_LEADER_MS = 35
SFX_TRAILER_MS = 90


def _tone(freq: float, duration_s: float, volume: float = 0.35) -> np.ndarray:
    n = max(1, int(TARGET_SR * duration_s))
    t = np.arange(n, dtype=np.float32) / TARGET_SR
    return np.sin(2.0 * math.pi * freq * t).astype(np.float32) * volume


def _square(freq: float, duration_s: float, volume: float = 0.35) -> np.ndarray:
    n = max(1, int(TARGET_SR * duration_s))
    t = np.arange(n, dtype=np.float32) / TARGET_SR
    return np.sign(np.sin(2.0 * math.pi * freq * t)).astype(np.float32) * volume


def _sweep(start: float, end: float, duration_s: float, volume: float = 0.35, shape: str = "sine") -> np.ndarray:
    n = max(1, int(TARGET_SR * duration_s))
    freq = np.linspace(start, end, n, dtype=np.float32)
    phase = np.cumsum(2.0 * math.pi * freq / TARGET_SR)
    wave = np.sin(phase).astype(np.float32)
    if shape == "square":
        wave = np.sign(wave).astype(np.float32)
    elif shape == "triangle":
        wave = (2.0 / math.pi * np.arcsin(wave)).astype(np.float32)
    return wave * volume


def _fade(samples: np.ndarray, ms: int = 12) -> np.ndarray:
    if samples.size == 0:
        return samples
    n = min(samples.size // 2, max(1, int(TARGET_SR * ms / 1000.0)))
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    out = samples.astype(np.float32, copy=True)
    out[:n] *= ramp
    out[-n:] *= ramp[::-1]
    return out


def _silence(duration_s: float) -> np.ndarray:
    return np.zeros(max(1, int(TARGET_SR * duration_s)), dtype=np.float32)


def _click(volume: float = 0.75) -> np.ndarray:
    samples = np.zeros(int(TARGET_SR * 0.055), dtype=np.float32)
    samples[:28] = np.linspace(volume, -volume * 0.45, 28, dtype=np.float32)
    samples[28:55] = np.linspace(-volume * 0.2, volume * 0.08, 27, dtype=np.float32)
    return _fade(samples, ms=3)


def _noise(duration_s: float, volume: float = 0.18) -> np.ndarray:
    rng = np.random.default_rng(42)
    samples = rng.normal(0.0, volume, int(TARGET_SR * duration_s)).astype(np.float32)
    return _fade(samples, ms=30)


def _chiptune_loop() -> np.ndarray:
    parts = []
    for _ in range(4):
        parts.extend([_square(120, 0.022, 0.55), _silence(0.018), _noise(0.018, 0.22), _silence(0.02)])
    return _fade(np.concatenate(parts), ms=4)


def _laser_pew() -> np.ndarray:
    return _fade(_sweep(1500, 800, 0.08, 0.56, "square"), ms=4)


def _jump_sweep() -> np.ndarray:
    return _fade(_sweep(400, 1200, 0.12, 0.45, "triangle"), ms=8)


def _data_drone() -> np.ndarray:
    n = int(TARGET_SR * 0.72)
    t = np.arange(n, dtype=np.float32) / TARGET_SR
    freq = np.where((t % 1.0) < 0.5, 65.0, 70.0).astype(np.float32)
    phase = np.cumsum(2.0 * math.pi * freq / TARGET_SR)
    wave = (2.0 / math.pi * np.arcsin(np.sin(phase))).astype(np.float32)
    return _fade(wave * 0.18, ms=45)


def _data_blip() -> np.ndarray:
    return _fade(_square(3200, 0.008, 0.42), ms=1)


def _cascade_arp() -> np.ndarray:
    notes = (1318, 1046, 784, 659)
    return _fade(np.concatenate([_tone(n, 0.030, 0.38) for n in notes]), ms=4)


def _ecg_beep() -> np.ndarray:
    tone = _tone(1046, 0.07, 0.65)
    env = np.exp(np.linspace(0.0, -5.0, tone.size, dtype=np.float32))
    return _fade(tone * env, ms=2)


def _bubble_pop() -> np.ndarray:
    return np.concatenate([_fade(_tone(f, 0.012, 0.38), ms=2) for f in (400, 600, 900)])


def _bounce_click() -> np.ndarray:
    return _fade(_noise(0.005, 0.55), ms=1)


def _brick_hit() -> np.ndarray:
    return _fade(_sweep(1200, 1200, 0.015, 0.45, "triangle"), ms=2)


def _star_tinkle() -> np.ndarray:
    tone = _sweep(2400, 3000, 0.25, 0.32, "sine")
    env = np.exp(np.linspace(0.0, -4.2, tone.size, dtype=np.float32))
    return _fade(tone * env, ms=35)


def _sonar_ping() -> np.ndarray:
    tone = _sweep(800, 400, 0.20, 0.45, "sine")
    env = np.exp(np.linspace(0.0, -4.8, tone.size, dtype=np.float32))
    return _fade(tone * env, ms=10)


def _charge_hum() -> np.ndarray:
    n = int(TARGET_SR * 0.55)
    t = np.arange(n, dtype=np.float32) / TARGET_SR
    swell = np.linspace(0.18, 0.42, n, dtype=np.float32) * (0.85 + 0.15 * np.sin(2.0 * math.pi * 6.0 * t))
    return _fade(np.sin(2.0 * math.pi * 100.0 * t).astype(np.float32) * swell, ms=35)


def _glitch_screech() -> np.ndarray:
    parts = []
    for i in range(54):
        freq = 100 if i % 2 == 0 else 3500
        parts.append(_square(freq, 0.003, 0.45))
    return _fade(np.concatenate(parts + [_click(0.9)]), ms=2)


def _dragon_breath() -> np.ndarray:
    blast = _noise(0.22, 0.34) + _sweep(220, 90, 0.22, 0.22, "square")
    return _fade(blast, ms=8)


def _sleep_dream() -> np.ndarray:
    return _fade(_tone(392, 0.10, 0.20) + _tone(523, 0.10, 0.12), ms=18)


def _success_chime() -> np.ndarray:
    return np.concatenate([_fade(_tone(f, 0.055, 0.36), ms=5) for f in (659, 880, 1175)])


def _listen_ping() -> np.ndarray:
    return _fade(_sweep(900, 1300, 0.07, 0.28, "sine"), ms=5)


def _to_pcm(samples: np.ndarray, volume: float) -> bytes:
    if samples.size == 0:
        return b""
    out = samples.astype(np.float32, copy=True)
    out -= float(np.mean(out))
    peak = float(np.max(np.abs(out)))
    if peak > 1e-6:
        out *= SFX_TARGET_PEAK / peak
    out = np.tanh(out * SFX_DRIVE) / np.tanh(SFX_DRIVE)
    out = np.clip(out * max(0.0, volume), -1.0, 1.0)
    leader = np.zeros(int(TARGET_SR * SFX_LEADER_MS / 1000.0), dtype=np.float32)
    trailer = np.zeros(int(TARGET_SR * SFX_TRAILER_MS / 1000.0), dtype=np.float32)
    out = np.concatenate([leader, _fade(out, ms=8), trailer])
    return (out * 32767.0).astype(np.int16).tobytes()


def synthesize_sfx_pcm(name: str, volume: float = 1.0) -> bytes:
    """Return mono signed-int16 PCM bytes for a short local sound effect."""
    key = (name or "").strip().lower()
    if key == "chiptune_loop":
        samples = _chiptune_loop()
    elif key == "laser_pew":
        samples = _laser_pew()
    elif key == "jump_sweep":
        samples = _jump_sweep()
    elif key == "dragon_breath":
        samples = _dragon_breath()
    elif key == "data_drone":
        samples = _data_drone()
    elif key == "data_blip":
        samples = _data_blip()
    elif key == "cascade_arp":
        samples = _cascade_arp()
    elif key == "ecg_beep":
        samples = _ecg_beep()
    elif key == "bubble_pop":
        samples = _bubble_pop()
    elif key == "bounce_click":
        samples = _bounce_click()
    elif key == "paddle_bounce":
        samples = _square(440, 0.035, 0.48)
    elif key == "brick_hit":
        samples = _brick_hit()
    elif key == "star_tinkle":
        samples = _star_tinkle()
    elif key == "clock_tick":
        samples = _square(1760, 0.005, 0.50)
    elif key == "sonar_ping":
        samples = _sonar_ping()
    elif key == "charge_hum":
        samples = _charge_hum()
    elif key == "glitch_screech":
        samples = _glitch_screech()
    elif key == "sleep_dream":
        samples = _sleep_dream()
    elif key == "success_chime":
        samples = _success_chime()
    elif key == "listen_ping":
        samples = _listen_ping()
    else:
        samples = _click(0.35)

    return _to_pcm(samples, volume)


def play_sfx(name: str, volume: float = 1.0) -> bool:
    """Synthesize and play an SFX clip locally using PyAudio or Pygame."""
    try:
        pcm = synthesize_sfx_pcm(name, volume)
        if not pcm:
            return False

        # 1. Try PyAudio
        try:
            import pyaudio
            p = pyaudio.PyAudio()
            stream = p.open(format=pyaudio.paInt16, channels=1, rate=TARGET_SR, output=True)
            stream.write(pcm)
            stream.stop_stream()
            stream.close()
            p.terminate()
            return True
        except Exception:
            pass

        # 2. Try Pygame fallback
        try:
            import pygame
            pygame.mixer.init(frequency=TARGET_SR, size=-16, channels=1)
            sound = pygame.mixer.Sound(buffer=pcm)
            sound.play()
            return True
        except Exception:
            pass

        return False
    except Exception:
        return False

