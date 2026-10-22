"""
Live listening: a per-browser queue of the processed audio, sent in small batches.

The audio is what the rest of the lab hears — after the channel gain and noise
gate — so listening is also a way to judge the calibration by ear.

Wire format to the browser (binary WebSocket messages):

    byte 0      channels in this message (1 = one mic, 2 = stereo L,R)
    bytes 1..   little-endian int16 samples, interleaved when stereo

A text message {"type":"hello","rate":16000} is sent first. The queue is capped
at one second of audio: a slow browser loses the oldest audio instead of
building up delay, and `dropped_bytes` says how much.
"""
from __future__ import annotations

MAX_QUEUE_BYTES = 16000 * 2 * 2          # one second of stereo 16-bit audio


class Listener:
    def __init__(self, max_bytes: int = MAX_QUEUE_BYTES) -> None:
        self.max_bytes = max_bytes
        self._segments: list[list] = []   # [channels, bytearray]
        self._bytes = 0
        self.dropped_bytes = 0

    def push(self, channels: int, pcm: bytes) -> None:
        if self._segments and self._segments[-1][0] == channels:
            self._segments[-1][1].extend(pcm)
        else:
            self._segments.append([channels, bytearray(pcm)])
        self._bytes += len(pcm)
        while self._bytes > self.max_bytes and len(self._segments) > 1:
            _, old = self._segments.pop(0)          # drop whole oldest segments first
            self._bytes -= len(old)
            self.dropped_bytes += len(old)
        if self._bytes > self.max_bytes:            # a single over-long segment: trim its oldest audio
            ch, seg = self._segments[0]
            frame = 2 * ch
            excess = ((self._bytes - self.max_bytes + frame - 1) // frame) * frame
            del seg[:excess]
            self._bytes -= excess
            self.dropped_bytes += excess

    def take(self) -> list[bytes]:
        """Everything queued, as ready-to-send messages (channel byte + samples)."""
        out = [bytes([ch]) + bytes(seg) for ch, seg in self._segments if seg]
        self._segments, self._bytes = [], 0
        return out
