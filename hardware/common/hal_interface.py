"""
hardware/common/hal_interface.py — Hardware Abstraction Layer Interface

Defines the unified interface that all platform implementations must follow.
This allows Buddy to run on ESP32, Windows Desktop, Mobile, etc. with the same code.

Each platform implements these interfaces:
- AudioInterface: Microphone input, speaker output, TTS
- DisplayInterface: Screen rendering, animations, visual feedback
- SensorInterface: Touch, temperature, proximity, etc.
- StorageInterface: Configuration, memory, logs
- NetworkInterface: WiFi, Bluetooth, communication protocols
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Optional, Any


# ════════════════════════════════════════════════════════════════
# ENUMS & DATA CLASSES
# ════════════════════════════════════════════════════════════════

class Platform(Enum):
    """Supported hardware platforms"""
    ESP32 = "esp32"
    WINDOWS_DESKTOP = "windows_desktop"
    ANDROID = "android"
    IOS = "ios"
    LINUX_DESKTOP = "linux_desktop"


class AnimationState(Enum):
    """Visual animation states"""
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    HAPPY = "happy"
    SAD = "sad"
    CONFUSED = "confused"
    EXCITED = "excited"
    SLEEPING = "sleeping"
    WAKING_UP = "waking_up"
    ERROR = "error"


class AudioFormat(Enum):
    """Audio format specifications"""
    PCM_16KHZ_MONO = "pcm_16khz_mono"
    PCM_44KHZ_STEREO = "pcm_44khz_stereo"
    MP3 = "mp3"
    WAV = "wav"


class VideoFormat(Enum):
    """Supported video formats for display"""
    MP4 = "mp4"
    AVI = "avi"
    GIF = "gif"
    WEBM = "webm"
    SPRITE_SHEET = "sprite_sheet"  # For animations on ESP32


@dataclass
class AudioConfig:
    """Audio configuration"""
    sample_rate: int = 16000
    channels: int = 1
    format: AudioFormat = AudioFormat.PCM_16KHZ_MONO
    buffer_size: int = 4096


@dataclass
class DisplayConfig:
    """Display configuration"""
    width: int
    height: int
    color_depth: int = 16  # bits per pixel
    refresh_rate: int = 30  # FPS
    has_touch: bool = False


@dataclass
class PlatformCapabilities:
    """Describes what a platform can do"""
    has_microphone: bool = True
    has_speaker: bool = True
    has_display: bool = True
    has_touch: bool = False
    has_camera: bool = False
    has_wifi: bool = True
    has_bluetooth: bool = False
    has_gpu: bool = False
    supports_video: bool = False
    supports_animations: bool = True
    max_memory_mb: int = 512


# ════════════════════════════════════════════════════════════════
# AUDIO INTERFACE
# ════════════════════════════════════════════════════════════════

class AudioInterface(ABC):
    """Abstract interface for audio I/O"""

    @abstractmethod
    async def initialize(self, config: AudioConfig) -> bool:
        """Initialize audio hardware"""
        pass

    @abstractmethod
    async def start_recording(self) -> AsyncIterator[bytes]:
        """
        Start recording audio from microphone.
        Yields raw audio chunks as they're captured.
        """
        pass

    @abstractmethod
    async def stop_recording(self):
        """Stop microphone recording"""
        pass

    @abstractmethod
    async def play_audio(self, audio_data: bytes, format: AudioFormat = AudioFormat.PCM_16KHZ_MONO):
        """
        Play audio through speaker.

        Args:
            audio_data: Raw audio bytes
            format: Audio format specification
        """
        pass

    @abstractmethod
    async def play_tts(self, text: str, voice: str = "en-US-AndrewMultilingualNeural"):
        """
        Text-to-speech playback.

        Args:
            text: Text to synthesize
            voice: Voice ID (platform-specific)
        """
        pass

    @abstractmethod
    async def stop_playback(self):
        """Stop current audio playback"""
        pass

    @abstractmethod
    def is_playing(self) -> bool:
        """Check if audio is currently playing"""
        pass

    @abstractmethod
    async def cleanup(self):
        """Release audio resources"""
        pass


# ════════════════════════════════════════════════════════════════
# DISPLAY INTERFACE
# ════════════════════════════════════════════════════════════════

class DisplayInterface(ABC):
    """Abstract interface for display/visual output"""

    @abstractmethod
    async def initialize(self, config: DisplayConfig) -> bool:
        """Initialize display hardware"""
        pass

    @abstractmethod
    async def show_animation(self, state: AnimationState, context: Optional[str] = None):
        """
        Show visual animation for given state.

        Args:
            state: Animation state enum
            context: Optional text context to display (user-provided explanation)
        """
        pass

    @abstractmethod
    async def play_video(self, video_path: str, loop: bool = False):
        """
        Play video file on display.

        Args:
            video_path: Path to video file
            loop: Whether to loop playback
        """
        pass

    @abstractmethod
    async def show_text(self, text: str, duration: float = 3.0):
        """
        Display text message.

        Args:
            text: Text to display
            duration: How long to show (seconds)
        """
        pass

    @abstractmethod
    async def show_clock(self, style: str = "analog"):
        """
        Show clock widget for standby mode.

        Args:
            style: "analog", "digital", "custom"
        """
        pass

    @abstractmethod
    async def clear_display(self):
        """Clear display / return to idle"""
        pass

    @abstractmethod
    def get_capabilities(self) -> DisplayConfig:
        """Get display hardware capabilities"""
        pass

    @abstractmethod
    async def cleanup(self):
        """Release display resources"""
        pass


# ════════════════════════════════════════════════════════════════
# SENSOR INTERFACE
# ════════════════════════════════════════════════════════════════

class SensorInterface(ABC):
    """Abstract interface for sensors (touch, temperature, etc.)"""

    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize sensor hardware"""
        pass

    @abstractmethod
    async def read_touch(self) -> Optional[tuple[int, int]]:
        """
        Read touch input (if available).

        Returns:
            (x, y) coordinates or None
        """
        pass

    @abstractmethod
    async def read_temperature(self) -> Optional[float]:
        """Read temperature sensor (if available)"""
        pass

    @abstractmethod
    async def read_proximity(self) -> Optional[float]:
        """Read proximity sensor (if available)"""
        pass

    @abstractmethod
    def has_sensor(self, sensor_type: str) -> bool:
        """Check if specific sensor is available"""
        pass

    @abstractmethod
    async def cleanup(self):
        """Release sensor resources"""
        pass


# ════════════════════════════════════════════════════════════════
# STORAGE INTERFACE
# ════════════════════════════════════════════════════════════════

class StorageInterface(ABC):
    """Abstract interface for persistent storage"""

    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize storage system"""
        pass

    @abstractmethod
    async def save_config(self, key: str, value: Any):
        """Save configuration value"""
        pass

    @abstractmethod
    async def load_config(self, key: str, default: Any = None) -> Any:
        """Load configuration value"""
        pass

    @abstractmethod
    async def save_memory(self, memory_data: dict):
        """Save conversation memory"""
        pass

    @abstractmethod
    async def load_memory(self) -> dict:
        """Load conversation memory"""
        pass

    @abstractmethod
    async def save_log(self, log_entry: str):
        """Append to log file"""
        pass

    @abstractmethod
    async def get_free_space(self) -> int:
        """Get available storage space (bytes)"""
        pass

    @abstractmethod
    async def cleanup(self):
        """Release storage resources"""
        pass


# ════════════════════════════════════════════════════════════════
# NETWORK INTERFACE
# ════════════════════════════════════════════════════════════════

class NetworkInterface(ABC):
    """Abstract interface for network communication"""

    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize network hardware"""
        pass

    @abstractmethod
    async def connect_wifi(self, ssid: str, password: str) -> bool:
        """Connect to WiFi network"""
        pass

    @abstractmethod
    async def is_connected(self) -> bool:
        """Check if network is connected"""
        pass

    @abstractmethod
    async def get_ip_address(self) -> Optional[str]:
        """Get current IP address"""
        pass

    @abstractmethod
    async def http_request(self, url: str, method: str = "GET", **kwargs) -> dict:
        """Make HTTP request"""
        pass

    @abstractmethod
    async def websocket_connect(self, url: str) -> Any:
        """Connect to WebSocket server"""
        pass

    @abstractmethod
    async def cleanup(self):
        """Release network resources"""
        pass


# ════════════════════════════════════════════════════════════════
# PLATFORM MANAGER (Main HAL Entry Point)
# ════════════════════════════════════════════════════════════════

class PlatformManager(ABC):
    """
    Main hardware abstraction layer manager.
    Factory class that provides platform-specific implementations.
    """

    def __init__(self, platform: Platform):
        self.platform = platform
        self.audio: Optional[AudioInterface] = None
        self.display: Optional[DisplayInterface] = None
        self.sensors: Optional[SensorInterface] = None
        self.storage: Optional[StorageInterface] = None
        self.network: Optional[NetworkInterface] = None

    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize all platform subsystems"""
        pass

    @abstractmethod
    def get_capabilities(self) -> PlatformCapabilities:
        """Get platform capabilities"""
        pass

    @abstractmethod
    async def cleanup(self):
        """Cleanup all subsystems"""
        pass

    @classmethod
    def create(cls, platform: Platform) -> PlatformManager:
        """
        Factory method to create platform-specific manager.

        Args:
            platform: Target platform enum

        Returns:
            Platform-specific PlatformManager instance
        """
        if platform == Platform.ESP32:
            from hardware.esp32.esp32_platform import ESP32PlatformManager
            return ESP32PlatformManager()
        elif platform == Platform.WINDOWS_DESKTOP:
            from hardware.desktop.windows_platform import WindowsPlatformManager
            return WindowsPlatformManager()
        elif platform == Platform.LINUX_DESKTOP:
            from hardware.desktop.linux_platform import LinuxPlatformManager
            return LinuxPlatformManager()
        else:
            raise NotImplementedError(f"Platform {platform} not yet implemented")


# ════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ════════════════════════════════════════════════════════════════

def detect_platform() -> Platform:
    """Auto-detect current platform"""
    import sys
    import os

    # Check if running on MicroPython (ESP32)
    try:
        import machine
        return Platform.ESP32
    except ImportError:
        pass

    # Check OS
    if sys.platform == "win32":
        return Platform.WINDOWS_DESKTOP
    elif sys.platform == "linux":
        # Check if Android
        if os.path.exists("/system/build.prop"):
            return Platform.ANDROID
        return Platform.LINUX_DESKTOP
    elif sys.platform == "darwin":
        return Platform.IOS  # or macOS, needs more detection

    raise RuntimeError(f"Unknown platform: {sys.platform}")


# ════════════════════════════════════════════════════════════════
# USAGE EXAMPLE
# ════════════════════════════════════════════════════════════════

"""
# In your main application code:

from hardware.common.hal_interface import PlatformManager, detect_platform

# Auto-detect or specify platform
platform = detect_platform()
hal = PlatformManager.create(platform)

# Initialize
await hal.initialize()

# Use abstracted interfaces
async for audio_chunk in hal.audio.start_recording():
    # Process audio (works on both ESP32 and Desktop!)
    transcribed_text = await stt_process(audio_chunk)

# Show animation
await hal.display.show_animation(AnimationState.THINKING)

# Play response
await hal.audio.play_tts("Hello, I'm thinking about that!")

# Cleanup
await hal.cleanup()
"""
