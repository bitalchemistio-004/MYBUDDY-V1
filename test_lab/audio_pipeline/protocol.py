"""
Wire protocol between the mic_lab test firmware and the audio-lab server.

This is a test-only protocol; it is deliberately separate from the production
firmware/backend protocol (raw PCM binary + bracketed text tags) so the lab can
carry the metadata a measurement needs — which channels are present, a
sequence number to detect loss, and how many samples each packet really holds.

Device -> server, binary, one PCM packet:

    offset  size  field
    0       2     magic  "MB"
    2       1     version (1)
    3       1     channel mask: bit0 = left mic present, bit1 = right mic present
    4       2     seq (u16, wraps)
    6       2     frames  — samples PER CHANNEL in this packet (the "chunk size")
    8       4     device millis() when the packet was captured
    12      ...   frames * channels little-endian int16, interleaved L,R when both

Server -> device, text:      CFG chunk=<n> mic=<left|right|both> gain=<x> swap=<0|1>
Device -> server, text:      HELLO ... | ACK chunk=.. mic=.. gain=.. swap=.. | NAK <reason> | STAT ...

Keep the limits below in sync with the constants at the top of
firmware/mic_lab/src/main.cpp.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

MAGIC = b"MB"
VERSION = 1
HEADER = struct.Struct("<2sBBHHI")
assert HEADER.size == 12

SAMPLE_RATE = 16000

MASK_LEFT = 0b01
MASK_RIGHT = 0b10
MASK_BOTH = 0b11

CHUNK_MIN = 32
CHUNK_MAX = 1024
CHUNK_STEP = 32
GAIN_MIN = 1.0
GAIN_MAX = 32.0
MIC_CHOICES = ("left", "right", "both")

# The production firmware uses 256 frames/packet and a 12x digital gain.
DEFAULT_CONFIG = {"chunk": 256, "mic": "both", "gain": 12.0, "swap": 0}


class ProtocolError(ValueError):
    """A packet or config that does not match the protocol."""


@dataclass(frozen=True)
class Frame:
    mask: int
    seq: int
    frames: int
    device_ms: int
    left: np.ndarray | None    # int16, length `frames`, or None if not transmitted
    right: np.ndarray | None

    @property
    def channels(self) -> int:
        return (1 if self.left is not None else 0) + (1 if self.right is not None else 0)

    def interleaved(self) -> np.ndarray:
        """Samples exactly as they belong in a WAV file with this frame's channels."""
        if self.left is not None and self.right is not None:
            out = np.empty(self.frames * 2, dtype="<i2")
            out[0::2] = self.left
            out[1::2] = self.right
            return out
        return (self.left if self.left is not None else self.right).astype("<i2", copy=False)


def mic_from_mask(mask: int) -> str:
    return {MASK_LEFT: "left", MASK_RIGHT: "right", MASK_BOTH: "both"}.get(mask, "unknown")


def parse_frame(data: bytes) -> Frame:
    if len(data) < HEADER.size:
        raise ProtocolError(f"packet too short ({len(data)} bytes)")
    magic, version, mask, seq, frames, device_ms = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise ProtocolError("bad magic")
    if version != VERSION:
        raise ProtocolError(f"unsupported version {version}")
    if mask not in (MASK_LEFT, MASK_RIGHT, MASK_BOTH):
        raise ProtocolError(f"bad channel mask {mask}")
    channels = 2 if mask == MASK_BOTH else 1
    expected = HEADER.size + frames * channels * 2
    if len(data) != expected:
        raise ProtocolError(f"length {len(data)} != expected {expected} for {frames} frames x {channels} ch")

    pcm = np.frombuffer(data, dtype="<i2", offset=HEADER.size)
    if mask == MASK_BOTH:
        left, right = pcm[0::2], pcm[1::2]
    elif mask == MASK_LEFT:
        left, right = pcm, None
    else:
        left, right = None, pcm
    return Frame(mask, seq, frames, device_ms, left, right)


def pack_frame(mask: int, seq: int, device_ms: int, left=None, right=None) -> bytes:
    """Build a packet. Used by the tests and the fake-device simulator."""
    if mask == MASK_BOTH:
        body = np.empty(len(left) * 2, dtype="<i2")
        body[0::2], body[1::2] = left, right
        frames = len(left)
    else:
        src = left if mask == MASK_LEFT else right
        body, frames = np.asarray(src, dtype="<i2"), len(src)
    return HEADER.pack(MAGIC, VERSION, mask, seq & 0xFFFF, frames, device_ms & 0xFFFFFFFF) + body.tobytes()


def validate_config(raw: dict) -> dict:
    """Normalise and range-check a config request. Raises ProtocolError."""
    try:
        chunk = int(raw.get("chunk", DEFAULT_CONFIG["chunk"]))
        gain = float(raw.get("gain", DEFAULT_CONFIG["gain"]))
        swap = 1 if int(raw.get("swap", 0)) else 0
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"invalid config value: {exc}") from exc
    mic = str(raw.get("mic", DEFAULT_CONFIG["mic"])).lower()
    if mic not in MIC_CHOICES:
        raise ProtocolError(f"mic must be one of {MIC_CHOICES}")
    if not (CHUNK_MIN <= chunk <= CHUNK_MAX) or chunk % CHUNK_STEP:
        raise ProtocolError(f"chunk must be a multiple of {CHUNK_STEP} between {CHUNK_MIN} and {CHUNK_MAX}")
    if not (GAIN_MIN <= gain <= GAIN_MAX):
        raise ProtocolError(f"gain must be between {GAIN_MIN} and {GAIN_MAX}")
    return {"chunk": chunk, "mic": mic, "gain": round(gain, 2), "swap": swap}


def config_command(cfg: dict) -> str:
    return f"CFG chunk={cfg['chunk']} mic={cfg['mic']} gain={cfg['gain']:g} swap={cfg['swap']}"


def parse_kv(text: str) -> dict[str, str]:
    """Parse `KEY a=1 b=two` -> {"a": "1", "b": "two"} (the leading verb is skipped)."""
    out = {}
    for token in text.split()[1:]:
        if "=" in token:
            k, v = token.split("=", 1)
            out[k] = v
    return out
