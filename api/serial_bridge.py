"""USB serial control bridge for MBUDDY hardware."""
from __future__ import annotations

from collections import deque
import threading
import time
from typing import Any

try:
    import serial
    from serial.tools import list_ports
except Exception:  # pragma: no cover - handled at runtime for missing optional dep
    serial = None
    list_ports = None


class SerialBridge:
    """Small line-oriented serial transport for the ESP32 bracket protocol."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._serial: Any | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # What the device last reported drawing, as opposed to what it was
        # told to draw. None until the firmware reports a face.
        self.drawn_face: str | None = None
        self.drawn_face_index: int | None = None
        self.drawn_face_at: float = 0.0
        # A short trail of what the device drew, newest last. The single latest
        # value cannot answer "did it draw what I asked" on a live device,
        # because MBUDDY legitimately reacts to the room a moment later — a
        # voice nearby forces Focused. The trail answers it without pretending
        # the face should have frozen.
        self.drawn_face_trail: deque[tuple[str, float]] = deque(maxlen=24)
        self.face_blocked_count: int = 0
        self.last_face_block_at: float = 0.0

        self._logs: deque[str] = deque(maxlen=400)
        # Per-frame device chatter. Kept separately so it cannot evict real
        # events from the main log within seconds of them arriving.
        self._noise: deque[str] = deque(maxlen=20)
        # Startup output, retained for the life of the connection: the OLED
        # driver, its address, and whether the display initialised live here.
        self._boot: deque[str] = deque(maxlen=60)
        self.port: str = ""
        self.baud: int = 115200
        self.last_seen_at: float = 0.0
        self.last_error: str = ""

    @property
    def available(self) -> bool:
        return serial is not None and list_ports is not None

    @property
    def connected(self) -> bool:
        with self._lock:
            return bool(self._serial and self._serial.is_open)

    def list_ports(self) -> list[dict[str, str]]:
        if not self.available:
            return []
        return [
            {
                "device": p.device,
                "description": p.description,
                "hwid": p.hwid,
            }
            for p in list_ports.comports()
        ]

    def connect(self, port: str, baud: int = 115200) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("pyserial is not installed.")
        clean_port = (port or "").strip()
        if not clean_port:
            raise ValueError("Serial port is required.")
        self.disconnect()
        with self._lock:
            # Open with DTR and RTS deasserted. On a CH340 those lines are
            # wired to EN/BOOT, so opening the port the default way hard-resets
            # the ESP32: the WiFi session drops, the face falls back to its
            # boot state, and anything mid-flight is lost. Attaching a monitor
            # must not reboot the thing being monitored.
            ser = serial.Serial()
            ser.port = clean_port
            ser.baudrate = int(baud)
            ser.timeout = 0.15
            ser.write_timeout = 0.5
            ser.dtr = False
            ser.rts = False
            ser.open()

            self._serial = ser
            self.port = clean_port
            self.baud = int(baud)
            self.last_error = ""
            self._stop.clear()
            self._thread = threading.Thread(target=self._reader, name="mbuddy-serial", daemon=True)
            self._thread.start()
        return self.snapshot()

    def disconnect(self) -> None:
        self._stop.set()
        with self._lock:
            ser = self._serial
            self._serial = None
        if ser is not None:
            try:
                ser.close()
            except Exception as exc:
                self.last_error = str(exc)

    def send_line(self, line: str) -> bool:
        """
        Write one protocol line to the device.

        Returns False on any failure rather than letting the exception
        propagate. A write can fail even when `is_open` is True: on Windows a
        USB device that re-enumerates (a new COM port assigned on replug, or
        after certain resets) leaves the old handle "open" but pointing at
        nothing — the OS returns PermissionError("Access is denied") on the
        next write. That used to crash the whole request handler with a 500;
        now the caller sees a clean False, the same as any other disconnected
        transport, and the stale handle is torn down so the port stops
        claiming to be connected.
        """
        payload = (line or "").strip()
        if not payload:
            return False
        with self._lock:
            ser = self._serial
            if ser is None or not ser.is_open:
                return False
            try:
                ser.write((payload + "\n").encode("utf-8", errors="replace"))
                ser.flush()
            except Exception as exc:
                self.last_error = str(exc)
                self._drop_stale_handle()
                return False
            self._logs.appendleft(f"> {payload}")
            return True

    def _drop_stale_handle(self) -> None:
        """
        Release a handle that Windows has invalidated out from under us.

        Called with the lock already held. Closing may itself raise on a
        handle the OS has already torn down; that failure is expected here and
        not worth surfacing as a fresh error.
        """
        ser = self._serial
        self._serial = None
        self._stop.set()
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "available": self.available,
                "connected": self.connected,
                "port": self.port if self.connected else "",
                "baud": self.baud,
                "last_seen_at": self.last_seen_at,
                "last_error": self.last_error,
                "drawn_face": self.drawn_face,
                "drawn_face_index": self.drawn_face_index,
                "drawn_face_age_s": (
                    round(time.time() - self.drawn_face_at, 2)
                    if self.drawn_face_at else None
                ),
                "face_blocked_count": self.face_blocked_count,
                "drawn_face_trail": [
                    {"emotion": name, "age_s": round(time.time() - at, 2)}
                    for name, at in self.drawn_face_trail
                ],
                "logs": list(self._logs)[:60],
                "boot": list(self._boot),
                "noise": list(self._noise)[:5],
            }

    def _reader(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                ser = self._serial
            if ser is None or not ser.is_open:
                break
            try:
                raw = ser.readline()
                if not raw:
                    continue
                text = raw.decode("utf-8", errors="replace").strip()
                if text:
                    self.last_seen_at = time.time()
                    self._record_line(text)
            except Exception as exc:
                self.last_error = str(exc)
                break

    # Lines the device emits every audio frame. Useful when debugging the
    # microphone, useless the rest of the time, and frequent enough to erase
    # everything else from a ring buffer.
    NOISE_PREFIXES = ("MIC diag", "I2S diag", "heap:", "RSSI:")

    # Index order is the C++ `enum eEmotions` in FaceEmotions.hpp; the
    # firmware reports the index, so the names have to line up exactly.
    FACE_EMOTIONS = (
        "Normal", "Angry", "Glee", "Happy", "Sad", "Worried", "Focused",
        "Annoyed", "Surprised", "Skeptic", "Frustrated", "Unimpressed",
        "Sleepy", "Suspicious", "Squint", "Furious", "Scared", "Awe",
    )

    # Lines worth keeping for the whole session, because they only appear once.
    # Matched as substrings, so each has to be specific enough not to catch
    # runtime chatter. A bare "Face" also matched every "Face Applied" line,
    # which refilled this buffer on each mood change and pushed the OLED init
    # lines out — losing exactly what the buffer exists to keep.
    BOOT_MARKERS = (
        "OLED", "u8g2", "MBUDDY ready", "WiFi", "I2C", "brownout",
        "TX queue", "audio-init", "boot", "Face allocation", "driver=",
    )

    def _note_face(self, text: str) -> None:
        """
        Remember the emotion the firmware says it just drew.

        Held as a value rather than read back out of the log, because on a
        busy device the line falls out of the rolling window within a second.
        """
        if text.startswith("Face Applied:"):
            try:
                idx = int(text.split(":", 1)[1].strip())
            except ValueError:
                return
            if 0 <= idx < len(self.FACE_EMOTIONS):
                self.drawn_face = self.FACE_EMOTIONS[idx]
                self.drawn_face_index = idx
                self.drawn_face_at = time.time()
                self.drawn_face_trail.append((self.drawn_face, self.drawn_face_at))
        elif text.startswith("Face Apply BLOCKED"):
            # The change lost the race for the face mutex, so the panel is
            # still showing the previous emotion. Worth surfacing: this is a
            # commanded change that did not happen.
            self.face_blocked_count += 1
            self.last_face_block_at = time.time()

    def _record_line(self, text: str) -> None:
        """
        File an incoming serial line into the right buffer.

        Args:
            text: one line from the device, already decoded and stripped
        """
        self._note_face(text)
        entry = f"< {text}"

        if any(text.startswith(p) for p in self.NOISE_PREFIXES):
            self._noise.appendleft(entry)
            return

        if any(m.lower() in text.lower() for m in self.BOOT_MARKERS):
            self._boot.appendleft(entry)

        self._logs.appendleft(entry)

    def boot_log(self) -> list[str]:
        """
        Startup output from the device, oldest last.

        This is where a blank-display diagnosis starts: the firmware prints
        which OLED driver it selected, the I2C address it found, and what
        `u8g2.begin()` returned.
        """
        return list(self._boot)


serial_bridge = SerialBridge()
