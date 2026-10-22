"""
hardware/desktop/windows_platform.py — Windows Desktop Implementation of HAL

Implements all HAL interfaces for Windows desktop environment.
Provides full-featured companion experience with GUI, animations, and rich features.
"""
from __future__ import annotations

import asyncio
import os
import platform
import subprocess
from pathlib import Path
from typing import AsyncIterator, Optional, Any

import pyaudio
import numpy as np
from PIL import Image, ImageDraw, ImageFont
# pygame/cv2 are only used by the display and video classes below, not by
# audio capture (PC Voice only needs WindowsAudioInterface). Importing them
# eagerly here meant the whole module — audio included — failed to import
# on a machine that only has pyaudio installed, which is exactly what broke
# PC Voice: `_run_pc_voice_loop` in buddy_app.py imports this module purely
# for WindowsAudioInterface and never touches display/video at all.

from hardware.common.hal_interface import (
    Platform, PlatformManager, PlatformCapabilities,
    AudioInterface, DisplayInterface, SensorInterface,
    StorageInterface, NetworkInterface,
    AudioConfig, DisplayConfig, AudioFormat, AnimationState, VideoFormat
)
import utils.logger as log


def list_available_input_devices() -> list[dict]:
    """
    Every real, currently-connected microphone — for Settings to populate a
    device picker before PC Voice has ever been started.

    Restricted to the WASAPI host API specifically, not "every device
    PyAudio can see." The legacy MME enumeration this used to walk lists
    every endpoint Windows has ever registered, including Bluetooth
    microphones that were paired once and are not connected right now, the
    same physical hardware repeated once per array element and once per
    legacy API, and blank placeholder entries — a dozen-plus confusing
    options for what's really one or two real, live microphones. WASAPI is
    Windows' modern audio API and only exposes endpoints that genuinely
    exist and are active right now; it also reports each device's actual
    native sample rate correctly, where the legacy MME view of the exact
    same hardware was observed reporting a different (wrong) rate.
    """
    p = pyaudio.PyAudio()
    try:
        try:
            wasapi_index = p.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
        except Exception:
            wasapi_index = None  # No WASAPI on this system — fall back to everything.

        try:
            default_index = p.get_default_input_device_info().get("index")
        except Exception:
            default_index = None

        devices = []
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) <= 0:
                continue
            if wasapi_index is not None and info.get("hostApi") != wasapi_index:
                continue
            devices.append({
                "index": i,
                "name": info.get("name", f"Device {i}"),
                "is_default": i == default_index,
                "default_sample_rate": int(info.get("defaultSampleRate", 0) or 0),
            })
        return devices
    finally:
        p.terminate()


# Name fragments that mark a device as a virtual audio path rather than a
# real microphone — a loopback/monitor device, or a phone-as-mic app whose
# companion app may not even be running. Matched case-insensitively against
# real, live device names (never a hardcoded index, since indices shift as
# devices connect/disconnect).
_VIRTUAL_DEVICE_MARKERS = (
    "wo mic", "vb-cable", "vb-audio", "cable input", "cable output",
    "voicemeeter", "stereo mix", "sound mapper", "primary sound capture",
    "virtual audio", "loopback",
)


def filter_real_input_devices(devices: list[dict]) -> list[dict]:
    """
    A short, honest list for a human to pick from.

    Windows exposes the same physical microphone multiple times — once per
    audio API it's reachable through (MME, WASAPI, DirectSound, and each
    array element of a "Microphone Array") — plus placeholder entries with
    no real name at all. Listing every one of those made the picker
    genuinely confusing (a dozen-plus options for what's really 2-3
    physical microphones), so this drops blank names, drops anything that
    looks virtual, and keeps only one entry per distinct device name — the
    Windows default if one of the duplicates is it, otherwise the
    lowest-indexed (typically the most broadly-compatible MME endpoint).
    Falls back to the unfiltered list if that would leave nothing, so the
    picker never goes empty.
    """
    def _has_real_name(name: str) -> bool:
        name = name.strip()
        if not name:
            return False
        # Windows sometimes exposes a placeholder endpoint as literally
        # "Input ()" or "Output ()" — a label with nothing inside the
        # parens, not a real device name.
        inner = name[name.find("(") + 1 : name.rfind(")")] if "(" in name and ")" in name else name
        return bool(inner.strip())

    named = [d for d in devices if _has_real_name(d.get("name", ""))]
    real = [d for d in named if not any(m in d["name"].lower() for m in _VIRTUAL_DEVICE_MARKERS)]
    if not real:
        real = named if named else devices

    by_name: dict[str, dict] = {}
    for d in real:
        key = d["name"].strip().lower()
        existing = by_name.get(key)
        if existing is None or (d["is_default"] and not existing["is_default"]):
            by_name[key] = d
    return list(by_name.values())


def pick_best_input_device(devices: list[dict]) -> Optional[dict]:
    """
    A real-world best guess at which device to actually record from, used
    only when nothing has been explicitly chosen in Settings.

    Windows' own "default" is whatever was last selected in Sound Settings
    — as this session found, that can silently be a virtual device (a
    phone-mic app, a loopback monitor) that is receiving nothing, which
    reads identically to "the microphone is broken." This prefers a device
    whose name doesn't match a known-virtual pattern; among those, Windows'
    own default if it qualifies, otherwise the first real one found. Still
    just a heuristic — the picker in Settings is what lets it be corrected
    for whatever this doesn't recognize.
    """
    if not devices:
        return None
    real = [d for d in devices if not any(m in d["name"].lower() for m in _VIRTUAL_DEVICE_MARKERS)]
    if not real:
        # Every visible device looks virtual — no real guess to make, so
        # fall back to whatever Windows calls default rather than refusing.
        return next((d for d in devices if d["is_default"]), devices[0])
    return next((d for d in real if d["is_default"]), real[0])


def _resample_pcm16(data: bytes, from_rate: int, to_rate: int) -> bytes:
    """
    Real polyphase resampling (scipy) of 16-bit mono PCM — not a stretch/
    truncate hack. Needed because most real microphones only support their
    own native rate (commonly 44100/48000Hz) via PyAudio, not an arbitrary
    one, while the backend's VAD/STT pipeline is built for exactly 16kHz.
    """
    import numpy as np
    from math import gcd

    from scipy.signal import resample_poly

    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    g = gcd(from_rate, to_rate)
    resampled = resample_poly(samples, to_rate // g, from_rate // g)
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


# ════════════════════════════════════════════════════════════════
# WINDOWS AUDIO IMPLEMENTATION
# ════════════════════════════════════════════════════════════════

class WindowsAudioInterface(AudioInterface):
    """Windows audio using PyAudio + edge-tts"""

    def __init__(self):
        self.pyaudio_instance = None
        self.input_stream = None
        self.output_stream = None
        self.is_recording = False
        self._playing = False
        self.config: Optional[AudioConfig] = None

    async def initialize(self, config: AudioConfig) -> bool:
        """Initialize PyAudio"""
        try:
            import pyaudio
            self.pyaudio_instance = pyaudio.PyAudio()
            self.config = config
            log.ok("✓ Windows audio initialized")
            return True
        except Exception as e:
            log.error(f"Audio init failed: {e}")
            return False

    async def start_recording(self, input_device_index: Optional[int] = None) -> AsyncIterator[bytes]:
        """
        Record from a microphone.

        Args:
            input_device_index: a specific PyAudio device index (from
                `list_input_devices`), or None for whatever Windows
                currently calls the default — see the caveat above.
        """
        if not self.pyaudio_instance:
            raise RuntimeError("Audio not initialized")

        target_rate = self.config.sample_rate
        capture_rate = target_rate
        open_kwargs = dict(
            format=pyaudio.paInt16,
            channels=self.config.channels,
            rate=target_rate,
            input=True,
            frames_per_buffer=self.config.buffer_size,
        )
        if input_device_index is not None:
            open_kwargs["input_device_index"] = input_device_index

        read_frames = self.config.buffer_size
        try:
            self.input_stream = self.pyaudio_instance.open(**open_kwargs)
        except OSError:
            # Most real microphones only support their own native rate
            # through PyAudio, not an arbitrary one — this is exactly what
            # broke recording for a real physical mic (44100Hz-only) once
            # PC Voice stopped defaulting to a virtual device that happened
            # to tolerate 16000Hz. Capture at the device's real rate instead
            # of failing, and resample each chunk down to what the backend
            # actually expects.
            if input_device_index is not None:
                info = self.pyaudio_instance.get_device_info_by_index(input_device_index)
                capture_rate = int(info.get("defaultSampleRate", 44100)) or 44100
            else:
                capture_rate = 44100
            log.warn(f"{target_rate}Hz unsupported on this device — capturing at its native {capture_rate}Hz and resampling")
            # Reading the same sample COUNT at a higher native rate covers
            # proportionally less real time — resampled back down, each
            # chunk would be a fraction of the duration the rest of the
            # pipeline (VAD included) expects, fragmenting speech into
            # pieces too short to recognize. Scaling the read size keeps
            # each resampled chunk the same real-world duration regardless
            # of the device's native rate.
            read_frames = round(self.config.buffer_size * capture_rate / target_rate)
            open_kwargs["rate"] = capture_rate
            open_kwargs["frames_per_buffer"] = read_frames
            self.input_stream = self.pyaudio_instance.open(**open_kwargs)

        self.is_recording = True
        log.info("🎤 Recording started")

        try:
            while self.is_recording:
                # A blocking PyAudio read sitting directly in this loop
                # blocks the whole asyncio event loop for as long as it
                # takes — normally ~read_frames worth of real time, but if a
                # device/driver ever stalls on a read, the loop freezes
                # completely: it can't even notice the caller asked it to
                # stop, since that check only runs after a chunk comes back.
                # Running the read in a thread, with a hard timeout, turns a
                # silent freeze into a clear, logged failure instead.
                try:
                    data = await asyncio.wait_for(
                        asyncio.to_thread(self.input_stream.read, read_frames, exception_on_overflow=False),
                        timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    log.error(
                        "Microphone read stalled for 2s+ (device may have "
                        "disconnected or the driver hung) — stopping."
                    )
                    break
                if capture_rate != target_rate:
                    data = _resample_pcm16(data, capture_rate, target_rate)
                yield data
                await asyncio.sleep(0.01)
        finally:
            if self.input_stream:
                self.input_stream.stop_stream()
                self.input_stream.close()
                self.input_stream = None

    async def stop_recording(self):
        """Stop recording"""
        self.is_recording = False
        log.info("🎤 Recording stopped")

    async def play_audio(self, audio_data: bytes, format: AudioFormat = AudioFormat.PCM_16KHZ_MONO):
        """Play raw PCM audio"""
        if not self.pyaudio_instance:
            raise RuntimeError("Audio not initialized")

        self._playing = True
        self.output_stream = self.pyaudio_instance.open(
            format=pyaudio.paInt16,
            channels=self.config.channels,
            rate=self.config.sample_rate,
            output=True
        )

        try:
            self.output_stream.write(audio_data)
        finally:
            self.output_stream.stop_stream()
            self.output_stream.close()
            self.output_stream = None
            self._playing = False

    async def play_tts(self, text: str, voice: str = "en-US-AndrewMultilingualNeural"):
        """Use edge-tts for speech synthesis"""
        try:
            import edge_tts
            import tempfile
            import pygame

            self._playing = True

            # Generate TTS to temp file
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp:
                tmp_path = tmp.name

            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(tmp_path)

            # Play with pygame mixer
            pygame.mixer.init()
            pygame.mixer.music.load(tmp_path)
            pygame.mixer.music.play()

            while pygame.mixer.music.get_busy():
                await asyncio.sleep(0.1)

            os.unlink(tmp_path)
            self._playing = False

        except Exception as e:
            log.error(f"TTS playback failed: {e}")
            self._playing = False

    async def stop_playback(self):
        """Stop audio playback"""
        try:
            import pygame
        except ImportError:
            self._playing = False
            return
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
        self._playing = False

    def is_playing(self) -> bool:
        """Check if audio is playing"""
        return self._playing

    async def cleanup(self):
        """Cleanup audio resources"""
        await self.stop_recording()
        await self.stop_playback()
        if self.pyaudio_instance:
            self.pyaudio_instance.terminate()
            self.pyaudio_instance = None


# ════════════════════════════════════════════════════════════════
# WINDOWS DISPLAY IMPLEMENTATION
# ════════════════════════════════════════════════════════════════

class WindowsDisplayInterface(DisplayInterface):
    """Windows display using Pygame + OpenCV for video"""

    def __init__(self):
        # `global pygame` binds it at module scope on first construction, so
        # every method below (and WindowsSensorInterface.read_touch, in the
        # same module) can keep referring to the bare name `pygame` — only
        # actually importing it once a display is really being used, not at
        # module-import time (see the note at the top of this file).
        global pygame
        import pygame

        self.window = None
        self.config: Optional[DisplayConfig] = None
        self.current_animation = None
        self.video_player = None
        self._animation_running = False

    async def initialize(self, config: DisplayConfig) -> bool:
        """Initialize Pygame window"""
        try:
            pygame.init()
            self.window = pygame.display.set_mode((config.width, config.height))
            pygame.display.set_caption("Buddy AI Companion")
            self.config = config
            log.ok(f"✓ Display initialized ({config.width}x{config.height})")
            return True
        except Exception as e:
            log.error(f"Display init failed: {e}")
            return False

    async def show_animation(self, state: AnimationState, context: Optional[str] = None):
        """Show animation for given state"""
        self._animation_running = True

        # Load animation assets
        animation_path = Path("assets/animations") / f"{state.value}.gif"

        if animation_path.exists():
            await self._play_gif_animation(str(animation_path), context)
        else:
            # Fallback: procedural animation
            await self._show_procedural_animation(state, context)

    async def _play_gif_animation(self, gif_path: str, context: Optional[str] = None):
        """Play GIF animation with optional text overlay"""
        from PIL import Image as PILImage

        gif = PILImage.open(gif_path)
        frames = []

        try:
            while True:
                frame = gif.copy().convert('RGBA')

                # Add context text overlay if provided
                if context:
                    draw = ImageDraw.Draw(frame)
                    font = ImageFont.truetype("arial.ttf", 20)

                    # Draw text with background
                    text_bbox = draw.textbbox((0, 0), context, font=font)
                    text_width = text_bbox[2] - text_bbox[0]
                    text_height = text_bbox[3] - text_bbox[1]

                    x = (frame.width - text_width) // 2
                    y = frame.height - text_height - 20

                    draw.rectangle([x-10, y-5, x+text_width+10, y+text_height+5], fill=(0, 0, 0, 180))
                    draw.text((x, y), context, font=font, fill=(255, 255, 255))

                frames.append(frame)
                gif.seek(gif.tell() + 1)
        except EOFError:
            pass

        # Display frames
        for frame in frames:
            if not self._animation_running:
                break

            # Convert PIL to Pygame surface
            mode = frame.mode
            size = frame.size
            data = frame.tobytes()
            py_image = pygame.image.fromstring(data, size, mode)

            # Scale to window size
            py_image = pygame.transform.scale(py_image, (self.config.width, self.config.height))

            self.window.blit(py_image, (0, 0))
            pygame.display.flip()

            await asyncio.sleep(0.1)  # ~10 FPS

    async def _show_procedural_animation(self, state: AnimationState, context: Optional[str] = None):
        """Fallback: Simple procedural animations"""
        colors = {
            AnimationState.IDLE: (100, 100, 200),
            AnimationState.LISTENING: (100, 200, 100),
            AnimationState.THINKING: (200, 200, 100),
            AnimationState.SPEAKING: (200, 100, 200),
            AnimationState.HAPPY: (255, 200, 100),
            AnimationState.SAD: (100, 100, 150),
        }

        color = colors.get(state, (150, 150, 150))

        for i in range(30):  # ~3 seconds at 10 FPS
            if not self._animation_running:
                break

            self.window.fill((20, 20, 20))

            # Pulsing circle
            pulse = int(50 + 30 * abs(np.sin(i / 5)))
            center = (self.config.width // 2, self.config.height // 2)
            pygame.draw.circle(self.window, color, center, pulse)

            # Context text
            if context:
                font = pygame.font.Font(None, 32)
                text = font.render(context, True, (255, 255, 255))
                text_rect = text.get_rect(center=(self.config.width//2, self.config.height - 50))
                self.window.blit(text, text_rect)

            pygame.display.flip()
            await asyncio.sleep(0.1)

    async def play_video(self, video_path: str, loop: bool = False):
        """Play video using OpenCV"""
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30

            while True:
                ret, frame = cap.read()
                if not ret:
                    if loop:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    break

                # Convert BGR to RGB
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frame = np.rot90(frame)
                frame = pygame.surfarray.make_surface(frame)
                frame = pygame.transform.scale(frame, (self.config.width, self.config.height))

                self.window.blit(frame, (0, 0))
                pygame.display.flip()

                await asyncio.sleep(1.0 / fps)

            cap.release()
        except Exception as e:
            log.error(f"Video playback failed: {e}")

    async def show_text(self, text: str, duration: float = 3.0):
        """Display text message"""
        self.window.fill((20, 20, 20))

        font = pygame.font.Font(None, 48)
        text_surface = font.render(text, True, (255, 255, 255))
        text_rect = text_surface.get_rect(center=(self.config.width//2, self.config.height//2))

        self.window.blit(text_surface, text_rect)
        pygame.display.flip()

        await asyncio.sleep(duration)

    async def show_clock(self, style: str = "analog"):
        """Show clock widget"""
        from datetime import datetime

        while True:
            now = datetime.now()

            self.window.fill((20, 20, 20))

            if style == "digital":
                time_str = now.strftime("%H:%M:%S")
                font = pygame.font.Font(None, 120)
                text = font.render(time_str, True, (100, 200, 255))
                text_rect = text.get_rect(center=(self.config.width//2, self.config.height//2))
                self.window.blit(text, text_rect)

            elif style == "analog":
                # Draw analog clock
                center = (self.config.width // 2, self.config.height // 2)
                radius = min(self.config.width, self.config.height) // 3

                # Clock face
                pygame.draw.circle(self.window, (60, 60, 60), center, radius, 5)

                # Hour hand
                hour_angle = (now.hour % 12 + now.minute / 60) * 30 - 90
                hour_x = center[0] + radius * 0.5 * np.cos(np.radians(hour_angle))
                hour_y = center[1] + radius * 0.5 * np.sin(np.radians(hour_angle))
                pygame.draw.line(self.window, (255, 255, 255), center, (int(hour_x), int(hour_y)), 8)

                # Minute hand
                minute_angle = now.minute * 6 - 90
                minute_x = center[0] + radius * 0.7 * np.cos(np.radians(minute_angle))
                minute_y = center[1] + radius * 0.7 * np.sin(np.radians(minute_angle))
                pygame.draw.line(self.window, (100, 200, 255), center, (int(minute_x), int(minute_y)), 5)

                # Second hand
                second_angle = now.second * 6 - 90
                second_x = center[0] + radius * 0.8 * np.cos(np.radians(second_angle))
                second_y = center[1] + radius * 0.8 * np.sin(np.radians(second_angle))
                pygame.draw.line(self.window, (255, 100, 100), center, (int(second_x), int(second_y)), 2)

            pygame.display.flip()
            await asyncio.sleep(0.1)

    async def clear_display(self):
        """Clear display"""
        self._animation_running = False
        self.window.fill((0, 0, 0))
        pygame.display.flip()

    def get_capabilities(self) -> DisplayConfig:
        """Get display capabilities"""
        return self.config

    async def cleanup(self):
        """Cleanup display resources"""
        self._animation_running = False
        if self.window:
            pygame.quit()


# ════════════════════════════════════════════════════════════════
# WINDOWS SENSOR IMPLEMENTATION
# ════════════════════════════════════════════════════════════════

class WindowsSensorInterface(SensorInterface):
    """Windows sensors (mouse as touch substitute)"""

    def __init__(self):
        self.initialized = False

    async def initialize(self) -> bool:
        """Initialize sensors"""
        self.initialized = True
        return True

    async def read_touch(self) -> Optional[tuple[int, int]]:
        """Read mouse position as touch substitute"""
        try:
            import pygame
        except ImportError:
            return None
        if pygame.mouse.get_pressed()[0]:
            return pygame.mouse.get_pos()
        return None

    async def read_temperature(self) -> Optional[float]:
        """Windows doesn't expose temp sensors easily"""
        return None

    async def read_proximity(self) -> Optional[float]:
        """No proximity sensor on desktop"""
        return None

    def has_sensor(self, sensor_type: str) -> bool:
        """Check sensor availability"""
        return sensor_type in ["mouse", "keyboard"]

    async def cleanup(self):
        """Cleanup"""
        pass


# ════════════════════════════════════════════════════════════════
# WINDOWS STORAGE IMPLEMENTATION
# ════════════════════════════════════════════════════════════════

class WindowsStorageInterface(StorageInterface):
    """Windows file system storage"""

    def __init__(self):
        self.config_dir = Path.home() / ".buddy" / "config"
        self.memory_dir = Path.home() / ".buddy" / "memory"
        self.log_dir = Path.home() / ".buddy" / "logs"

    async def initialize(self) -> bool:
        """Create storage directories"""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log.ok(f"✓ Storage initialized at {self.config_dir.parent}")
        return True

    async def save_config(self, key: str, value: Any):
        """Save config value as JSON"""
        import json
        config_file = self.config_dir / f"{key}.json"
        with open(config_file, 'w') as f:
            json.dump(value, f, indent=2)

    async def load_config(self, key: str, default: Any = None) -> Any:
        """Load config value"""
        import json
        config_file = self.config_dir / f"{key}.json"
        if config_file.exists():
            with open(config_file, 'r') as f:
                return json.load(f)
        return default

    async def save_memory(self, memory_data: dict):
        """Save memory data"""
        import json
        memory_file = self.memory_dir / "memory.json"
        with open(memory_file, 'w') as f:
            json.dump(memory_data, f, indent=2)

    async def load_memory(self) -> dict:
        """Load memory data"""
        import json
        memory_file = self.memory_dir / "memory.json"
        if memory_file.exists():
            with open(memory_file, 'r') as f:
                return json.load(f)
        return {}

    async def save_log(self, log_entry: str):
        """Append to log"""
        from datetime import datetime
        log_file = self.log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log"
        with open(log_file, 'a') as f:
            f.write(f"{datetime.now().isoformat()} - {log_entry}\n")

    async def get_free_space(self) -> int:
        """Get free disk space"""
        import shutil
        stat = shutil.disk_usage(self.config_dir)
        return stat.free

    async def cleanup(self):
        """Cleanup"""
        pass


# ════════════════════════════════════════════════════════════════
# WINDOWS NETWORK IMPLEMENTATION
# ════════════════════════════════════════════════════════════════

class WindowsNetworkInterface(NetworkInterface):
    """Windows network using aiohttp"""

    def __init__(self):
        self.session = None

    async def initialize(self) -> bool:
        """Initialize network"""
        import aiohttp
        self.session = aiohttp.ClientSession()
        log.ok("✓ Network initialized")
        return True

    async def connect_wifi(self, ssid: str, password: str) -> bool:
        """Windows handles WiFi automatically"""
        return await self.is_connected()

    async def is_connected(self) -> bool:
        """Check internet connectivity"""
        try:
            import aiohttp
            async with self.session.get('https://www.google.com', timeout=aiohttp.ClientTimeout(total=5)) as resp:
                return resp.status == 200
        except:
            return False

    async def get_ip_address(self) -> Optional[str]:
        """Get local IP"""
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return None

    async def http_request(self, url: str, method: str = "GET", **kwargs) -> dict:
        """Make HTTP request"""
        async with self.session.request(method, url, **kwargs) as resp:
            return await resp.json()

    async def websocket_connect(self, url: str) -> Any:
        """Connect to WebSocket"""
        return self.session.ws_connect(url)

    async def cleanup(self):
        """Cleanup network"""
        if self.session:
            await self.session.close()


# ════════════════════════════════════════════════════════════════
# WINDOWS PLATFORM MANAGER
# ════════════════════════════════════════════════════════════════

class WindowsPlatformManager(PlatformManager):
    """Windows desktop platform manager"""

    def __init__(self):
        super().__init__(Platform.WINDOWS_DESKTOP)

        # Initialize interfaces
        self.audio = WindowsAudioInterface()
        self.display = WindowsDisplayInterface()
        self.sensors = WindowsSensorInterface()
        self.storage = WindowsStorageInterface()
        self.network = WindowsNetworkInterface()

    async def initialize(self) -> bool:
        """Initialize all subsystems"""
        log.info("Initializing Windows platform...")

        audio_config = AudioConfig(sample_rate=16000, channels=1)
        display_config = DisplayConfig(width=800, height=600, color_depth=32)

        results = await asyncio.gather(
            self.audio.initialize(audio_config),
            self.display.initialize(display_config),
            self.sensors.initialize(),
            self.storage.initialize(),
            self.network.initialize(),
            return_exceptions=True
        )

        success = all(r is True for r in results if not isinstance(r, Exception))

        if success:
            log.ok("✓ Windows platform initialized successfully")
        else:
            log.error("⚠ Some subsystems failed to initialize")

        return success

    def get_capabilities(self) -> PlatformCapabilities:
        """Get Windows platform capabilities"""
        return PlatformCapabilities(
            has_microphone=True,
            has_speaker=True,
            has_display=True,
            has_touch=False,  # Mouse substitute
            has_camera=True,  # Webcam available
            has_wifi=True,
            has_bluetooth=True,
            has_gpu=True,
            supports_video=True,
            supports_animations=True,
            max_memory_mb=8192  # Typical desktop
        )

    async def cleanup(self):
        """Cleanup all subsystems"""
        await asyncio.gather(
            self.audio.cleanup(),
            self.display.cleanup(),
            self.sensors.cleanup(),
            self.storage.cleanup(),
            self.network.cleanup(),
            return_exceptions=True
        )
        log.info("Windows platform cleanup complete")
