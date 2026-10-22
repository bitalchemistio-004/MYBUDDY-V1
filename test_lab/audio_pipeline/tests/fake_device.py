"""
A software stand-in for the mic_lab firmware, speaking the same protocol.

Use it to exercise the server and web UI without the robot:

    python -m test_lab.audio_pipeline.tests.fake_device --azimuth -30

IMPORTANT: results obtained with this simulator prove the server and UI only.
They say nothing about the microphones, I2S wiring or firmware — that needs the
real device (see README, "What is and is not verified").
"""
from __future__ import annotations

import argparse
import asyncio
import time

import aiohttp
import numpy as np

from ..protocol import (CHUNK_MAX, CHUNK_MIN, DEFAULT_CONFIG, MASK_BOTH, MASK_LEFT, MASK_RIGHT,
                        SAMPLE_RATE, ProtocolError, pack_frame, parse_kv, validate_config)

SPEED_OF_SOUND = 343.0


def source_signal(n: int, rng: np.random.Generator, amp: float) -> np.ndarray:
    """Broadband 'voice-like' noise, band-limited so it correlates the way speech does."""
    x = rng.standard_normal(n + 64)
    x = np.convolve(x, np.ones(4) / 4, mode="same")
    return (x[32:32 + n] * amp * 32767).astype(np.float64)


class FakeDevice:
    def __init__(self, url: str, azimuth_deg: float = 0.0, spacing_m: float = 0.10, level: float = 0.15,
                 right_sens_db: float = 0.0, noise: float = 0.0, diag_slot1: str = "healthy", answer_diag: bool = True,
                 wifi_glitch: bool = False, drop_every: int = 0,
                 has_speaker: bool = True, spk_gain: float = 0.05, spk_delay_ms: float = 60.0,
                 spk_lowpass_hz: float | None = None, spk_ceiling: float | None = None) -> None:
        self.url = url
        self.diag_slot1 = diag_slot1           # what the raw right slot looks like: healthy | garbage | stuck
        self.answer_diag = answer_diag
        # speaker simulation: what the robot plays is heard by its own microphone
        self.has_speaker = has_speaker           # False models the older firmware that reports no speaker
        self.spk_gain = spk_gain                 # linear gain from the speaker signal (int16 counts) to the mic
        self.spk_delay_ms = spk_delay_ms
        self.spk_lowpass_hz = spk_lowpass_hz
        self.spk_ceiling = spk_ceiling           # soft limit on what the mic hears from the speaker (int16 counts)
        self.thump_amp = 0.0                     # low-frequency (80 Hz) thumps: big peaks outside the speech band
        self._thump_n = 0
        self._thump_env = 0.0
        self.voice_amp = 0.0                     # set > 0 to model a person talking loudly at the mic (fraction of full scale)
        self._play_q = np.zeros(0)
        self._playing = False
        self._end_marked = False
        self._spk_done_pending = False
        self._lp_state = 0.0
        self.spk_played = 0
        self.drop_every = drop_every           # simulate a lossy link: every Nth packet is numbered but never sent
        self.wifi_glitch = wifi_glitch         # right slot gets a buffer-locked glitch, but only in a normal (streaming) capture
        self.right_sens_db = right_sens_db     # right mic acoustic sensitivity relative to the left (e.g. -18)
        self.noise = noise                     # independent electrical noise per channel, fraction of full scale
        self.cfg = dict(DEFAULT_CONFIG)
        self.azimuth = azimuth_deg
        self.spacing = spacing_m
        self.level = level
        self.seq = 0
        self.rng = np.random.default_rng(1)
        self.history = np.zeros(0)
        self.received_cfg: list[str] = []
        self.stop = asyncio.Event()

    def _delay_samples(self) -> float:
        # source at +azimuth (right of centre) reaches the right mic first => right leads => lag < 0
        return -self.spacing * np.sin(np.radians(self.azimuth)) * SAMPLE_RATE / SPEED_OF_SOUND

    def _chunk(self, n: int) -> tuple[np.ndarray, np.ndarray]:
        src = source_signal(n, self.rng, self.level)
        self.history = np.concatenate([self.history, src])[-8192:]
        d = self._delay_samples()
        base = len(self.history)
        idx = np.arange(base - n, base, dtype=np.float64)
        # d > 0: sound reaches the left mic first, so the RIGHT channel is the delayed one
        left = np.interp(idx - max(-d, 0.0), np.arange(base), self.history)
        right = np.interp(idx - max(d, 0.0), np.arange(base), self.history)
        right = right * (10.0 ** (self.right_sens_db / 20.0))      # weaker acoustic sensitivity...
        if self.noise:                                              # ...but the same electrical noise floor
            left = left + self.rng.standard_normal(n) * self.noise * 32767
            right = right + self.rng.standard_normal(n) * self.noise * 32767
        echo = self._speaker_echo(n)
        left, right = left + echo, right + echo
        if self.voice_amp:
            voice = self.rng.standard_normal(n) * self.voice_amp * 32767
            left, right = left + voice, right + voice
        self._thump_env += (self.thump_amp - self._thump_env) * 0.12       # smooth on/off, like a real thump (no clicks)
        if self._thump_env > 1e-4:
            thump = np.sin(2 * np.pi * 80 * (self._thump_n + np.arange(n)) / 16000) * self._thump_env * 32767
            left, right = left + thump, right + thump
        self._thump_n += n
        return np.clip(left, -32768, 32767).astype("<i2"), np.clip(right, -32768, 32767).astype("<i2")

    def _speaker_echo(self, n: int) -> np.ndarray:
        """The next n microphone samples' worth of what the (simulated) speaker is playing."""
        need = int(round(n * 22050 / 16000))
        if not self._playing or len(self._play_q) == 0:
            if self._playing and self._end_marked and len(self._play_q) == 0:
                self._playing = False
                self._spk_done_pending = True
            return np.zeros(n)
        take = self._play_q[:need]
        self._play_q = self._play_q[need:]
        self.spk_played += len(take) * 2
        if len(take) < need:
            take = np.concatenate([take, np.zeros(need - len(take))])
        y = np.interp(np.linspace(0, need, n, endpoint=False), np.arange(need), take) * self.spk_gain
        if self.spk_lowpass_hz:
            a = np.exp(-2 * np.pi * self.spk_lowpass_hz / 16000)
            out = np.empty(n)
            st = self._lp_state
            for i in range(n):
                st = (1 - a) * y[i] + a * st
                out[i] = st
            self._lp_state, y = st, out
        if self.spk_ceiling:
            y = np.tanh(y / self.spk_ceiling) * self.spk_ceiling
        if self._end_marked and len(self._play_q) == 0:
            self._playing = False
            self._spk_done_pending = True
        return y

    def _on_speaker_packet(self, data: bytes) -> None:
        if len(data) < 7 or data[:2] != b"SP":
            return
        pcm = np.frombuffer(data[6:], dtype="<i2").astype(np.float64)
        if not self._playing:
            self._play_q = np.zeros(int(self.spk_delay_ms * 22.05))             # the path's fixed delay before the first sample
            self._playing, self._end_marked = True, False
        self._play_q = np.concatenate([self._play_q, pcm])
        if data[3] & 1:
            self._end_marked = True

    async def run(self) -> None:
        async with aiohttp.ClientSession() as session, session.ws_connect(self.url) as ws:
            await ws.send_str("HELLO fw=fake/1 fs=16000 ip=127.0.0.1 rssi=-40 chunk_min=32 chunk_max=1024")
            reader = asyncio.create_task(self._read(ws))
            t0 = time.monotonic()
            last_stat = 0.0
            sent = 0
            try:
                while not self.stop.is_set() and not ws.closed:
                    n = self.cfg["chunk"]
                    left, right = self._chunk(n)
                    mic = self.cfg["mic"]
                    mask = {"left": MASK_LEFT, "right": MASK_RIGHT, "both": MASK_BOTH}[mic]
                    g = self.cfg["gain"] / 12.0
                    left, right = np.clip(left * g, -32768, 32767).astype("<i2"), np.clip(right * g, -32768, 32767).astype("<i2")
                    if not (self.drop_every and self.seq % self.drop_every == self.drop_every - 1):
                        await ws.send_bytes(pack_frame(mask, self.seq, int((time.monotonic() - t0) * 1000), left, right))
                    self.seq += 1
                    sent += n
                    if self._spk_done_pending:
                        self._spk_done_pending = False
                        await ws.send_str(f"SPKDONE played={self.spk_played} under=0 drop=0")
                    if time.monotonic() - last_stat >= 1.0:
                        last_stat = time.monotonic()
                        spk = f" spkok=1 spkplayed={self.spk_played} spkunder=0 spkdrop=0 spkbuf={len(self._play_q) * 2}" if self.has_speaker else ""
                        await ws.send_str(f"STAT rssi=-40 heap=200000 up=10 qdrop=0 i2serr=0 sendfail=0 slot0=100 slot1=100{spk}")
                    # pace to real time like an I2S read would
                    await asyncio.sleep(max(0.0, t0 + sent / SAMPLE_RATE - time.monotonic()))
            finally:
                reader.cancel()

    async def _send_diag(self, ws, quiet: bool = False) -> None:
        """Answer a DIAG request like the firmware: 4096 raw frames in 8 'DG' chunks."""
        n = 4096
        def mic(rms=300.0):
            return (np.round(self.rng.standard_normal(n) * rms + 900).astype(np.int64) << 8).astype(np.int32)
        left = mic()
        if self.diag_slot1 == "garbage":
            right = self.rng.integers(-2 ** 31, 2 ** 31, n, dtype=np.int64).astype(np.int32)
        elif self.diag_slot1 == "stuck":
            right = np.zeros(n, dtype=np.int32)
        else:
            right = mic()
        if self.wifi_glitch and not quiet:
            v = (right.astype(np.int64) >> 8)
            v[46::256] -= 40000
            right = (v << 8).astype(np.int32)
        words = np.stack([left, right], axis=1).astype("<i4")
        per = n // 8
        for i in range(8):
            part = np.ascontiguousarray(words[i * per:(i + 1) * per])
            await ws.send_bytes(b"DG" + bytes([1, i, 8]) + per.to_bytes(2, "little") + b"\x00" + part.tobytes())
        await ws.send_str("DIAGDONE")

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.BINARY and self.has_speaker:
                self._on_speaker_packet(msg.data)
                continue
            if msg.type == aiohttp.WSMsgType.TEXT and msg.data.startswith("SPK reset"):
                self._play_q, self._playing, self._end_marked = np.zeros(0), False, False
                continue
            if msg.type == aiohttp.WSMsgType.TEXT and msg.data.startswith("DIAG") and self.answer_diag:
                await self._send_diag(ws, quiet=msg.data.startswith("DIAG quiet"))
                continue
            if msg.type != aiohttp.WSMsgType.TEXT or not msg.data.startswith("CFG "):
                continue
            self.received_cfg.append(msg.data)
            kv = parse_kv(msg.data)
            try:
                cfg = validate_config(kv)
            except ProtocolError as exc:
                await ws.send_str(f"NAK {exc}")
                continue
            self.cfg = cfg
            await ws.send_str(f"ACK chunk={cfg['chunk']} mic={cfg['mic']} gain={cfg['gain']:g} swap={cfg['swap']}")


async def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:5100/ws/device")
    ap.add_argument("--azimuth", type=float, default=0.0, help="source bearing in degrees, negative = left")
    ap.add_argument("--spacing-cm", type=float, default=10.0)
    ap.add_argument("--right-sens-db", type=float, default=0.0, help="make the right mic this many dB less sensitive")
    ap.add_argument("--noise", type=float, default=0.0, help="electrical noise, fraction of full scale (e.g. 0.0015)")
    args = ap.parse_args()
    print(f"fake device -> {args.url}  azimuth={args.azimuth}°  spacing={args.spacing_cm} cm  (Ctrl+C to stop)")
    await FakeDevice(args.url, args.azimuth, args.spacing_cm / 100,
                     right_sens_db=args.right_sens_db, noise=args.noise).run()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
