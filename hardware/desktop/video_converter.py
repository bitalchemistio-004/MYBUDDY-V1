"""
hardware/desktop/video_converter.py — Video Format Converter for Display Systems

Converts MP4, AVI, WebM, and other video formats to formats optimized for:
- ESP32 displays (sprite sheets, compressed frames)
- Windows desktop (optimized codecs)
- Animated GIF sequences

Supports batch conversion and provides CLI interface.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np
from PIL import Image


class OutputFormat(Enum):
    """Supported output formats"""
    SPRITE_SHEET = "sprite_sheet"  # For ESP32 - all frames in one image
    GIF = "gif"  # Animated GIF
    FRAME_SEQUENCE = "frame_sequence"  # Individual PNG/JPG frames
    MP4_OPTIMIZED = "mp4_optimized"  # Re-encoded for size
    WEBM = "webm"  # Web-optimized format


@dataclass
class ConversionConfig:
    """Video conversion configuration"""
    target_width: int = 320
    target_height: int = 240
    target_fps: int = 10
    max_frames: Optional[int] = None  # Limit total frames
    quality: int = 85  # JPEG quality 0-100
    color_depth: int = 16  # bits per pixel (16 for ESP32)
    loop: bool = True


class VideoConverter:
    """Converts videos to display-optimized formats"""

    def __init__(self):
        self.supported_inputs = ['.mp4', '.avi', '.mov', '.webm', '.mkv', '.flv']

    def convert(
        self,
        input_path: str,
        output_format: OutputFormat,
        config: ConversionConfig,
        output_path: Optional[str] = None
    ) -> str:
        """
        Convert video to specified format.

        Args:
            input_path: Path to input video
            output_format: Target format enum
            config: Conversion configuration
            output_path: Optional output path (auto-generated if None)

        Returns:
            Path to output file
        """
        input_path = Path(input_path)
        if not input_path.exists():
            raise FileNotFoundError(f"Input video not found: {input_path}")

        if input_path.suffix.lower() not in self.supported_inputs:
            raise ValueError(f"Unsupported input format: {input_path.suffix}")

        # Generate output path if not provided
        if output_path is None:
            output_path = self._generate_output_path(input_path, output_format)

        print(f"🎬 Converting {input_path.name} → {output_format.value}")
        print(f"   Target: {config.target_width}x{config.target_height} @ {config.target_fps}fps")

        # Route to appropriate converter
        if output_format == OutputFormat.SPRITE_SHEET:
            return self._convert_to_sprite_sheet(input_path, output_path, config)
        elif output_format == OutputFormat.GIF:
            return self._convert_to_gif(input_path, output_path, config)
        elif output_format == OutputFormat.FRAME_SEQUENCE:
            return self._convert_to_frame_sequence(input_path, output_path, config)
        elif output_format == OutputFormat.MP4_OPTIMIZED:
            return self._convert_to_mp4(input_path, output_path, config)
        elif output_format == OutputFormat.WEBM:
            return self._convert_to_webm(input_path, output_path, config)
        else:
            raise NotImplementedError(f"Format {output_format} not implemented")

    def _generate_output_path(self, input_path: Path, output_format: OutputFormat) -> str:
        """Generate output filename"""
        output_dir = input_path.parent / "converted"
        output_dir.mkdir(exist_ok=True)

        ext_map = {
            OutputFormat.SPRITE_SHEET: ".png",
            OutputFormat.GIF: ".gif",
            OutputFormat.FRAME_SEQUENCE: "",  # Directory
            OutputFormat.MP4_OPTIMIZED: ".mp4",
            OutputFormat.WEBM: ".webm"
        }

        ext = ext_map[output_format]
        return str(output_dir / f"{input_path.stem}_converted{ext}")

    def _convert_to_sprite_sheet(self, input_path: Path, output_path: str, config: ConversionConfig) -> str:
        """
        Convert to sprite sheet (single image with all frames).
        Perfect for ESP32 - loads entire animation at once.
        """
        cap = cv2.VideoCapture(str(input_path))

        # Calculate frame sampling
        original_fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_skip = max(1, int(original_fps / config.target_fps))

        frames = []
        frame_idx = 0

        print(f"   Extracting frames (1 every {frame_skip})...")

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % frame_skip == 0:
                # Resize frame
                frame = cv2.resize(frame, (config.target_width, config.target_height))

                # Convert to RGB
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                # Reduce color depth if needed (for ESP32)
                if config.color_depth == 16:
                    frame = self._reduce_to_rgb565(frame)

                frames.append(frame)

                if config.max_frames and len(frames) >= config.max_frames:
                    break

            frame_idx += 1

        cap.release()

        print(f"   Collected {len(frames)} frames")

        # Arrange frames in grid
        cols = int(np.ceil(np.sqrt(len(frames))))
        rows = int(np.ceil(len(frames) / cols))

        print(f"   Creating sprite sheet ({cols}x{rows} grid)...")

        sprite_width = config.target_width * cols
        sprite_height = config.target_height * rows
        sprite_sheet = np.zeros((sprite_height, sprite_width, 3), dtype=np.uint8)

        for idx, frame in enumerate(frames):
            row = idx // cols
            col = idx % cols
            y = row * config.target_height
            x = col * config.target_width
            sprite_sheet[y:y+config.target_height, x:x+config.target_width] = frame

        # Save as PNG
        img = Image.fromarray(sprite_sheet)
        img.save(output_path, optimize=True)

        # Save metadata
        metadata_path = output_path.replace('.png', '_meta.txt')
        with open(metadata_path, 'w') as f:
            f.write(f"frames={len(frames)}\n")
            f.write(f"frame_width={config.target_width}\n")
            f.write(f"frame_height={config.target_height}\n")
            f.write(f"cols={cols}\n")
            f.write(f"rows={rows}\n")
            f.write(f"fps={config.target_fps}\n")

        file_size = os.path.getsize(output_path)
        print(f"✓ Sprite sheet saved: {output_path} ({file_size/1024:.1f} KB)")
        print(f"✓ Metadata saved: {metadata_path}")

        return output_path

    def _convert_to_gif(self, input_path: Path, output_path: str, config: ConversionConfig) -> str:
        """Convert to animated GIF"""
        cap = cv2.VideoCapture(str(input_path))

        original_fps = cap.get(cv2.CAP_PROP_FPS)
        frame_skip = max(1, int(original_fps / config.target_fps))

        frames = []
        frame_idx = 0

        print(f"   Extracting frames...")

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % frame_skip == 0:
                frame = cv2.resize(frame, (config.target_width, config.target_height))
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frames.append(Image.fromarray(frame))

                if config.max_frames and len(frames) >= config.max_frames:
                    break

            frame_idx += 1

        cap.release()

        print(f"   Saving GIF with {len(frames)} frames...")

        # Save as GIF
        duration = int(1000 / config.target_fps)  # ms per frame
        frames[0].save(
            output_path,
            save_all=True,
            append_images=frames[1:],
            duration=duration,
            loop=0 if config.loop else 1,
            optimize=True
        )

        file_size = os.path.getsize(output_path)
        print(f"✓ GIF saved: {output_path} ({file_size/1024:.1f} KB)")

        return output_path

    def _convert_to_frame_sequence(self, input_path: Path, output_path: str, config: ConversionConfig) -> str:
        """Convert to individual frame images"""
        output_dir = Path(output_path) if output_path else input_path.parent / f"{input_path.stem}_frames"
        output_dir.mkdir(exist_ok=True)

        cap = cv2.VideoCapture(str(input_path))

        original_fps = cap.get(cv2.CAP_PROP_FPS)
        frame_skip = max(1, int(original_fps / config.target_fps))

        frame_idx = 0
        saved_count = 0

        print(f"   Extracting frames to {output_dir}...")

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % frame_skip == 0:
                frame = cv2.resize(frame, (config.target_width, config.target_height))

                frame_path = output_dir / f"frame_{saved_count:04d}.png"
                cv2.imwrite(str(frame_path), frame, [cv2.IMWRITE_PNG_COMPRESSION, 9])

                saved_count += 1

                if config.max_frames and saved_count >= config.max_frames:
                    break

            frame_idx += 1

        cap.release()

        print(f"✓ Saved {saved_count} frames to {output_dir}")

        return str(output_dir)

    def _convert_to_mp4(self, input_path: Path, output_path: str, config: ConversionConfig) -> str:
        """Re-encode MP4 for smaller size"""
        cap = cv2.VideoCapture(str(input_path))

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(
            output_path,
            fourcc,
            config.target_fps,
            (config.target_width, config.target_height)
        )

        original_fps = cap.get(cv2.CAP_PROP_FPS)
        frame_skip = max(1, int(original_fps / config.target_fps))

        frame_idx = 0
        saved_count = 0

        print(f"   Re-encoding MP4...")

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % frame_skip == 0:
                frame = cv2.resize(frame, (config.target_width, config.target_height))
                out.write(frame)
                saved_count += 1

                if config.max_frames and saved_count >= config.max_frames:
                    break

            frame_idx += 1

        cap.release()
        out.release()

        file_size = os.path.getsize(output_path)
        print(f"✓ MP4 saved: {output_path} ({file_size/1024:.1f} KB)")

        return output_path

    def _convert_to_webm(self, input_path: Path, output_path: str, config: ConversionConfig) -> str:
        """Convert to WebM (VP9 codec)"""
        # Use ffmpeg via subprocess for WebM
        import subprocess

        cmd = [
            'ffmpeg', '-y',
            '-i', str(input_path),
            '-c:v', 'libvpx-vp9',
            '-crf', '30',  # Quality (0-63, lower=better)
            '-b:v', '0',  # Variable bitrate
            '-vf', f'scale={config.target_width}:{config.target_height}',
            '-r', str(config.target_fps),
            output_path
        ]

        if config.max_frames:
            cmd.extend(['-frames:v', str(config.max_frames)])

        print(f"   Running ffmpeg...")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr}")

        file_size = os.path.getsize(output_path)
        print(f"✓ WebM saved: {output_path} ({file_size/1024:.1f} KB)")

        return output_path

    def _reduce_to_rgb565(self, frame: np.ndarray) -> np.ndarray:
        """
        Reduce 24-bit RGB to 16-bit RGB565 (for ESP32).
        R: 5 bits, G: 6 bits, B: 5 bits
        """
        r = (frame[:, :, 0] >> 3) << 11
        g = (frame[:, :, 1] >> 2) << 5
        b = (frame[:, :, 2] >> 3)
        rgb565 = r | g | b

        # Convert back to 24-bit for display
        r_out = ((rgb565 >> 11) & 0x1F) << 3
        g_out = ((rgb565 >> 5) & 0x3F) << 2
        b_out = (rgb565 & 0x1F) << 3

        return np.stack([r_out, g_out, b_out], axis=-1).astype(np.uint8)

    def get_video_info(self, input_path: str) -> dict:
        """Get video metadata"""
        cap = cv2.VideoCapture(input_path)

        info = {
            'width': int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            'height': int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            'fps': cap.get(cv2.CAP_PROP_FPS),
            'frame_count': int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
            'duration': cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS),
            'codec': int(cap.get(cv2.CAP_PROP_FOURCC))
        }

        cap.release()
        return info


# ════════════════════════════════════════════════════════════════
# CLI INTERFACE
# ════════════════════════════════════════════════════════════════

def main():
    """CLI entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Convert videos for Buddy display systems')
    parser.add_argument('input', help='Input video file')
    parser.add_argument('--format', '-f', choices=['sprite', 'gif', 'frames', 'mp4', 'webm'],
                        default='gif', help='Output format (default: gif)')
    parser.add_argument('--width', '-w', type=int, default=320, help='Target width (default: 320)')
    parser.add_argument('--height', '-h', type=int, default=240, help='Target height (default: 240)')
    parser.add_argument('--fps', type=int, default=10, help='Target FPS (default: 10)')
    parser.add_argument('--max-frames', type=int, help='Maximum frames to extract')
    parser.add_argument('--quality', '-q', type=int, default=85, help='JPEG quality 0-100 (default: 85)')
    parser.add_argument('--output', '-o', help='Output path (auto-generated if not specified)')
    parser.add_argument('--info', action='store_true', help='Show video info and exit')

    args = parser.parse_args()

    converter = VideoConverter()

    # Show info
    if args.info:
        info = converter.get_video_info(args.input)
        print(f"\n📹 Video Info: {args.input}")
        print(f"   Resolution: {info['width']}x{info['height']}")
        print(f"   FPS: {info['fps']:.2f}")
        print(f"   Frames: {info['frame_count']}")
        print(f"   Duration: {info['duration']:.2f}s")
        return

    # Convert
    format_map = {
        'sprite': OutputFormat.SPRITE_SHEET,
        'gif': OutputFormat.GIF,
        'frames': OutputFormat.FRAME_SEQUENCE,
        'mp4': OutputFormat.MP4_OPTIMIZED,
        'webm': OutputFormat.WEBM
    }

    config = ConversionConfig(
        target_width=args.width,
        target_height=args.height,
        target_fps=args.fps,
        max_frames=args.max_frames,
        quality=args.quality
    )

    try:
        output_path = converter.convert(
            args.input,
            format_map[args.format],
            config,
            args.output
        )
        print(f"\n✅ Conversion complete!")
        print(f"   Output: {output_path}")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()


# ════════════════════════════════════════════════════════════════
# USAGE EXAMPLES
# ════════════════════════════════════════════════════════════════

"""
# Convert MP4 to GIF (default)
python video_converter.py input.mp4

# Convert to sprite sheet for ESP32
python video_converter.py input.mp4 --format sprite --width 240 --height 240 --fps 10

# Extract frames
python video_converter.py input.mp4 --format frames --max-frames 100

# Get video info
python video_converter.py input.mp4 --info

# Custom output path
python video_converter.py input.mp4 --format gif --output animations/buddy_happy.gif

# High quality WebM
python video_converter.py input.mp4 --format webm --width 800 --height 600 --fps 30
"""
