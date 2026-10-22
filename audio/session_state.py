"""Audio buffering and VAD state for one connected companion device."""
from __future__ import annotations

import asyncio

import numpy as np

from audio.vad import webrtc_vad_check
from core.config import (
    BARGE_IN_CONSECUTIVE,
    BARGE_IN_MIN_RMS,
    BARGE_IN_PEAK_INT16,
    BARGE_IN_RMS_RATIO,
    MAX_UTTERANCE_DURATION,
    NOISE_FLOOR,
    PRE_ROLL_CHUNKS,
    SILENCE_DURATION,
    SILENCE_THRESHOLD,
    VAD_SPEECH_FRAMES,
)
from utils.diagnostics import audio_rms
import utils.logger as log


class AudioSessionState:
    """Owns mic chunk buffering, VAD, and barge-in peak tracking."""

    def __init__(self) -> None:
        self.audio_buffer: list[np.ndarray] = []
        self.pre_roll_buffer: list[np.ndarray] = []
        self.last_voice_time: float = 0.0
        self.utterance_start_time: float = 0.0
        self.is_speaking: bool = False
        self.voiced_frames: int = 0
        self.barge_peak_frames: int = 0
        self.noise_floor: float = NOISE_FLOOR  # Adaptive floor starts at config default
        self.last_rms: float = 0.0
        self.last_barge_rms: float = 0.0
        self.last_barge_gate: float = max(NOISE_FLOOR, BARGE_IN_MIN_RMS) * BARGE_IN_RMS_RATIO
        self._diag_chunk_count: int = 0  # for periodic RMS logging

    def feed_chunk(self, chunk: np.ndarray) -> bool:
        chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if chunk.size == 0:
            return False

        now = asyncio.get_event_loop().time()
        rms = audio_rms(chunk)
        self.last_rms = rms

        # Periodic RMS diagnostic — a line every ~40 s shows the mic level against
        # the gate without flooding the log (it used to write one every 0.8 s).
        self._diag_chunk_count += 1
        if self._diag_chunk_count % 2500 == 0:   # ~every 40 s (it was every 0.8 s: two thirds of the whole log)
            log.info(
                f"🎙 RMS={rms:.4f} "
                f"floor={self.noise_floor:.3f} "
                f"gate={self.noise_floor*1.1:.4f} "
                f"speaking={self.is_speaking}"
            )

        # Adaptive noise floor learning (only if not currently speaking)
        if not self.is_speaking and rms > 0.0001:
            # Slow adaptation to current room background
            self.noise_floor = (self.noise_floor * 0.96) + (rms * 0.04)
            # Prevent it from dropping too low or rising too high (clamping)
            self.noise_floor = np.clip(self.noise_floor, 0.005, 0.035)

        energy_silent = rms < self.noise_floor * 1.1
        is_voiced = False
        if not energy_silent:
            try:
                is_voiced = webrtc_vad_check(chunk)
            except Exception:
                is_voiced = rms >= max(SILENCE_THRESHOLD, self.noise_floor)

        self.pre_roll_buffer.append(chunk.copy())
        if len(self.pre_roll_buffer) > PRE_ROLL_CHUNKS:
            self.pre_roll_buffer.pop(0)

        if is_voiced:
            if not self.is_speaking:
                self.voiced_frames += 1
                if self.voiced_frames >= VAD_SPEECH_FRAMES:
                    log.info(f"WebRTC VAD triggered - RMS {rms:.4f}")
                    self.is_speaking = True
                    self.utterance_start_time = now
                    pre_roll_snapshot = list(self.pre_roll_buffer)
                    self.pre_roll_buffer.clear()
                    self.audio_buffer.extend(pre_roll_snapshot)
                else:
                    return False

            self.last_voice_time = now
            self.audio_buffer.append(chunk.copy())
            return False

        if not self.is_speaking:
            return False

        self.audio_buffer.append(chunk.copy())
        elapsed_silence = now - self.last_voice_time
        utterance_length = now - self.utterance_start_time

        if elapsed_silence >= SILENCE_DURATION or utterance_length >= MAX_UTTERANCE_DURATION:
            self.is_speaking = False
            return True

        return False

    def drain_buffer(self) -> np.ndarray:
        if not self.audio_buffer:
            return np.array([], dtype=np.float32)

        sanitized: list[np.ndarray] = []
        for chunk in self.audio_buffer:
            arr = np.asarray(chunk, dtype=np.float32).reshape(-1)
            if arr.size:
                sanitized.append(arr)

        self.audio_buffer = []
        self.is_speaking = False
        self.voiced_frames = 0
        self.last_voice_time = 0.0
        self.utterance_start_time = 0.0

        if not sanitized:
            return np.array([], dtype=np.float32)
        return np.concatenate(sanitized).astype(np.float32, copy=False)

    def note_loud_chunk(self, data: bytes) -> bool:
        if len(data) < 2:
            return False
        samples = np.frombuffer(data, dtype=np.int16)
        if samples.size == 0:
            return False
        peak = int(np.max(np.abs(samples.astype(np.int32))))
        normalized = samples.astype(np.float32) / 32768.0
        rms = audio_rms(normalized)
        self.last_barge_rms = rms

        gate = max(BARGE_IN_MIN_RMS, self.noise_floor * BARGE_IN_RMS_RATIO)
        self.last_barge_gate = gate
        ratio_hit = rms >= gate
        peak_hit = peak >= BARGE_IN_PEAK_INT16 and rms >= BARGE_IN_MIN_RMS

        if ratio_hit or peak_hit:
            self.barge_peak_frames += 1
        else:
            self.barge_peak_frames = 0
            if rms > 0:
                self.noise_floor = max(
                    0.002,
                    (self.noise_floor * 0.92) + (rms * 0.08),
                )
        return self.barge_peak_frames >= BARGE_IN_CONSECUTIVE

    def reset_barge(self) -> None:
        self.barge_peak_frames = 0
