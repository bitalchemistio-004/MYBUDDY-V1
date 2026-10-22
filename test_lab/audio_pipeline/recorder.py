"""
WAV recording of the audio that arrived from the device, with the channel-balance
trims (calibration.py) applied; the trims used are stored in the sidecar JSON.

The file is written with exactly the channels the firmware transmitted:
left -> mono, right -> mono, both -> stereo (L,R). Lost packets are counted and
recorded in the sidecar JSON; they are NOT papered over with inserted silence,
so a WAV never contains samples that did not come from a microphone.
"""
from __future__ import annotations

import json
import re
import time
import wave
from datetime import datetime
from pathlib import Path

from .protocol import SAMPLE_RATE, Frame, mic_from_mask

NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+\.wav$")


class Recorder:
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self._armed = False
        self._wav: wave.Wave_write | None = None
        self._meta: dict = {}
        self._frames = 0
        self._started = 0.0
        self._dropped_at_start = 0

    @property
    def active(self) -> bool:
        return self._armed or self._wav is not None

    @property
    def seconds(self) -> float:
        return self._frames / SAMPLE_RATE

    @property
    def waiting_for_audio(self) -> bool:
        return self._armed and self._wav is None

    def start(self, dropped_packets: int) -> None:
        self._armed = True
        self._frames = 0
        self._dropped_at_start = dropped_packets

    def write(self, frame: Frame, cfg: dict, dropped_packets: int) -> None:
        """Append a frame. The first frame after start() fixes the file's channel layout."""
        if not self._armed:
            return
        if self._wav is None:
            mic = mic_from_mask(frame.mask)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"{stamp}_{mic}_c{frame.frames}.wav"
            self._wav = wave.open(str(self.dir / name), "wb")
            self._wav.setnchannels(frame.channels)
            self._wav.setsampwidth(2)
            self._wav.setframerate(SAMPLE_RATE)
            self._started = time.time()
            self._meta = {
                "name": name,
                "created": datetime.now().isoformat(timespec="seconds"),
                "mic": mic,
                "channels": frame.channels,
                "sample_rate": SAMPLE_RATE,
                "chunk_frames": frame.frames,
                "gain": cfg.get("gain"),
                "swap": cfg.get("swap"),
                "conditioner": cfg.get("conditioner"),  # channel trims / noise gate baked into this audio
            }
        elif frame.channels != self._wav.getnchannels():
            return  # layout changed mid-recording; config changes are refused while recording
        self._wav.writeframes(frame.interleaved().tobytes())
        self._frames += frame.frames
        self._meta["dropped_packets"] = dropped_packets - self._dropped_at_start

    def stop(self) -> dict | None:
        """Finish the file. Returns its metadata, or None if no audio ever arrived."""
        wav, self._wav, self._armed = self._wav, None, False
        if wav is None:
            return None
        wav.close()
        meta = dict(self._meta)
        meta["frames"] = self._frames
        meta["duration_s"] = round(self._frames / SAMPLE_RATE, 2)
        (self.dir / meta["name"]).with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return meta

    def list(self) -> list[dict]:
        """Recordings on disk, newest first, from their sidecars (or the WAV header)."""
        items = []
        for wav_path in self.dir.glob("*.wav"):
            if not NAME_RE.match(wav_path.name):
                continue
            side = wav_path.with_suffix(".json")
            try:
                meta = json.loads(side.read_text(encoding="utf-8")) if side.exists() else self._from_header(wav_path)
            except Exception:
                meta = self._from_header(wav_path)
            meta["name"] = wav_path.name
            meta["size_bytes"] = wav_path.stat().st_size
            items.append(meta)
        items.sort(key=lambda m: m["name"], reverse=True)
        return items

    @staticmethod
    def _from_header(path: Path) -> dict:
        with wave.open(str(path), "rb") as w:
            frames, rate, ch = w.getnframes(), w.getframerate(), w.getnchannels()
        return {"channels": ch, "sample_rate": rate, "frames": frames,
                "duration_s": round(frames / rate, 2) if rate else 0,
                "mic": {1: "mono", 2: "both"}.get(ch, "unknown")}

    def path_for(self, name: str) -> Path | None:
        if not NAME_RE.match(name):
            return None
        p = self.dir / name
        return p if p.is_file() else None
