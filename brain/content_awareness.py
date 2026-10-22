"""
brain/content_awareness.py — Screen capture and visual context understanding.

Provides MBUDDY with the ability to:
- Capture screenshots with user permission
- Analyze visual content using vision APIs (GPT-4V, Claude, Local)
- Extract text via OCR
- Store visual context in conversation memory
- React to screen changes proactively

Usage:
    awareness = ContentAwarenessEngine()
    if await awareness.request_permission():
        context = await awareness.get_current_context()
        print(context.description)
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Optional

import mss
import numpy as np
from PIL import Image

import utils.logger as log


@dataclass
class VisualContext:
    """Captured visual context from screen"""
    timestamp: float
    description: str
    detected_text: str
    detected_apps: list[str]
    activity_type: str  # "coding", "browsing", "writing", "gaming", etc.
    screenshot_hash: str  # For change detection
    confidence: float  # 0.0 - 1.0


class ContentAwarenessEngine:
    """Manages screen capture and visual understanding"""

    def __init__(
        self,
        vision_provider: str = "gemini",
        capture_interval: int = 10,
        auto_capture: bool = False
    ):
        self.vision_provider = vision_provider
        self.capture_interval = capture_interval
        self.auto_capture = auto_capture

        self.enabled = os.getenv("CONTENT_AWARENESS_ENABLED", "false").lower() == "true"
        self.permission_granted = False

        self.last_capture: Optional[Image.Image] = None
        self.last_context: Optional[VisualContext] = None
        self.last_capture_time = 0.0
        self.last_hash = ""

        self._capture_task: Optional[asyncio.Task] = None
        self._change_threshold = 0.15  # 15% pixel difference triggers change

    async def request_permission(self) -> bool:
        """
        Request user permission for screen capture.

        In production, this should show a GUI dialog.
        For now, checks environment variable.
        """
        if not self.enabled:
            log.info("Content awareness disabled in config")
            return False

        # TODO: Show native permission dialog
        # For now, assume granted if enabled in config
        self.permission_granted = True
        log.ok("Screen capture permission granted")

        if self.auto_capture:
            await self.start_auto_capture()

        return True

    async def start_auto_capture(self):
        """Start background capture loop"""
        if self._capture_task:
            return

        async def capture_loop():
            while self.permission_granted:
                try:
                    await self.capture_and_analyze()
                    await asyncio.sleep(self.capture_interval)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    log.error(f"Auto-capture error: {e}")
                    await asyncio.sleep(self.capture_interval * 2)

        self._capture_task = asyncio.create_task(capture_loop())
        log.info(f"Auto-capture started (interval: {self.capture_interval}s)")

    async def stop_auto_capture(self):
        """Stop background capture"""
        if self._capture_task:
            self._capture_task.cancel()
            try:
                await self._capture_task
            except asyncio.CancelledError:
                pass
            self._capture_task = None
            log.info("Auto-capture stopped")

    async def capture_screen(self, monitor: int = 1, resize: tuple[int, int] = (1280, 720)) -> Image.Image:
        """
        Capture screenshot from specified monitor.

        Args:
            monitor: Monitor index (1 = primary)
            resize: Target size to reduce token usage

        Returns:
            PIL Image
        """
        if not self.permission_granted:
            raise PermissionError("Screen capture permission not granted")

        try:
            with mss.mss() as sct:
                # Get monitor info
                if monitor >= len(sct.monitors):
                    monitor = 1

                # Capture
                screenshot = sct.grab(sct.monitors[monitor])

                # Convert to PIL Image
                img = Image.frombytes(
                    'RGB',
                    screenshot.size,
                    screenshot.bgra,
                    'raw',
                    'BGRX'
                )

                # Resize to reduce API costs
                img.thumbnail(resize, Image.Resampling.LANCZOS)

                self.last_capture = img
                self.last_capture_time = time.time()

                return img

        except Exception as e:
            log.error(f"Screen capture failed: {e}")
            raise

    def _compute_image_hash(self, img: Image.Image) -> str:
        """Compute perceptual hash for change detection"""
        # Resize to 8x8 for fast comparison
        small = img.resize((8, 8), Image.Resampling.LANCZOS).convert('L')
        pixels = np.array(small).flatten()

        # Average hash
        avg = pixels.mean()
        bits = ''.join('1' if p > avg else '0' for p in pixels)

        return hashlib.md5(bits.encode()).hexdigest()

    def detect_significant_change(self, new_img: Image.Image) -> bool:
        """Check if screen content changed significantly"""
        if not self.last_capture:
            return True

        new_hash = self._compute_image_hash(new_img)

        if not self.last_hash:
            self.last_hash = new_hash
            return True

        # Compare hashes
        changed = new_hash != self.last_hash
        self.last_hash = new_hash

        return changed

    async def analyze_with_gpt4v(self, img: Image.Image) -> dict:
        """Analyze image using GPT-4 Vision"""
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

            # Convert to base64
            buffered = BytesIO()
            img.save(buffered, format="JPEG", quality=85)
            img_b64 = base64.b64encode(buffered.getvalue()).decode()

            # Query GPT-4V
            response = await client.chat.completions.create(
                model="gpt-4-vision-preview",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": """Analyze this screenshot and provide:
1. Brief description of what's on screen (1-2 sentences)
2. Detected text (if any, key words only)
3. Application/activity type (coding, browsing, writing, gaming, etc.)
4. Confidence level (0.0-1.0)

Respond in JSON format:
{
  "description": "...",
  "detected_text": "...",
  "activity_type": "...",
  "detected_apps": ["..."],
  "confidence": 0.9
}"""
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{img_b64}",
                                    "detail": "low"  # Reduce cost
                                }
                            }
                        ]
                    }
                ],
                max_tokens=300
            )

            # Parse JSON response
            import json
            result = json.loads(response.choices[0].message.content)
            return result

        except Exception as e:
            log.error(f"GPT-4V analysis failed: {e}")
            return {
                "description": "Could not analyze screen content",
                "detected_text": "",
                "activity_type": "unknown",
                "detected_apps": [],
                "confidence": 0.0
            }

    async def analyze_with_gemini(self, img: Image.Image) -> dict:
        """
        Analyze image using Gemini Vision, via Google's OpenAI-compatibility
        endpoint (so this reuses the `openai` package already a dependency,
        rather than adding a second SDK).

        This is the only vision provider actually configured in this
        deployment — GEMINI_API_KEY is set, while OPENAI_API_KEY and
        ANTHROPIC_API_KEY (needed by analyze_with_gpt4v/analyze_with_claude)
        are not. Verified live against the real API: gemini-flash-lite-latest
        answers directly with no hidden "thinking" tokens eating the reply
        budget, unlike gemini-2.5-flash and other non-lite models, which
        silently return an empty message unless max_tokens is raised well
        past what a short vision summary needs.
        """
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=os.getenv("GEMINI_API_KEY"),
                base_url=os.getenv(
                    "GEMINI_BASE_URL",
                    "https://generativelanguage.googleapis.com/v1beta/openai/",
                ),
            )

            buffered = BytesIO()
            img.save(buffered, format="JPEG", quality=85)
            img_b64 = base64.b64encode(buffered.getvalue()).decode()

            response = await client.chat.completions.create(
                model=os.getenv("GEMINI_VISION_MODEL", "gemini-flash-lite-latest"),
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": """Analyze this screenshot and provide:
1. Brief description of what's on screen (1-2 sentences)
2. Detected text (if any, key words only)
3. Application/activity type (coding, browsing, writing, gaming, etc.)
4. Confidence level (0.0-1.0)

Respond with ONLY raw JSON, no markdown fences:
{"description":"...","detected_text":"...","activity_type":"...","detected_apps":["..."],"confidence":0.9}""",
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                            },
                        ],
                    }
                ],
                max_tokens=300,
            )

            import json
            content = (response.choices[0].message.content or "").strip()
            # Gemini occasionally wraps JSON in ```json fences despite being
            # asked not to; strip them rather than failing to parse.
            if content.startswith("```"):
                content = content.strip("`")
                if content.lower().startswith("json"):
                    content = content[4:]
            return json.loads(content.strip())

        except Exception as e:
            log.error(f"Gemini Vision analysis failed: {e}")
            return {
                "description": "Could not analyze screen content",
                "detected_text": "",
                "activity_type": "unknown",
                "detected_apps": [],
                "confidence": 0.0
            }

    async def analyze_with_claude(self, img: Image.Image) -> dict:
        """Analyze image using Claude Vision"""
        try:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

            # Convert to base64
            buffered = BytesIO()
            img.save(buffered, format="JPEG", quality=85)
            img_b64 = base64.b64encode(buffered.getvalue()).decode()

            # Query Claude
            message = await client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=300,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": img_b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": """Analyze this screenshot briefly:
1. What's on screen? (1-2 sentences)
2. Key visible text
3. Activity type (coding/browsing/writing/etc)
4. Detected apps

JSON format: {"description":"...","detected_text":"...","activity_type":"...","detected_apps":[],"confidence":0.9}"""
                            }
                        ],
                    }
                ],
            )

            # Parse response
            import json
            result = json.loads(message.content[0].text)
            return result

        except Exception as e:
            log.error(f"Claude Vision analysis failed: {e}")
            return {
                "description": "Could not analyze screen content",
                "detected_text": "",
                "activity_type": "unknown",
                "detected_apps": [],
                "confidence": 0.0
            }

    async def analyze_with_local(self, img: Image.Image) -> dict:
        """Analyze with local vision model (LLaVA via Ollama)"""
        from core import cloud_brain
        if cloud_brain.active:           # the cloud brain is on: nothing is asked of a model on this PC
            return {"description": "Could not analyze screen content", "detected_text": "", "activity_type": "unknown",
                    "detected_apps": [], "confidence": 0.0}
        try:
            import aiohttp

            # Convert to base64
            buffered = BytesIO()
            img.save(buffered, format="JPEG", quality=85)
            img_b64 = base64.b64encode(buffered.getvalue()).decode()

            endpoint = os.getenv("LLM_LOCAL_ENDPOINT", "http://localhost:11434")

            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "llava",
                    "prompt": "Describe what you see on this screen. What activity is happening?",
                    "images": [img_b64],
                    "stream": False
                }

                async with session.post(f"{endpoint}/api/generate", json=payload) as response:
                    data = await response.json()
                    description = data.get("response", "")

                    # Extract activity type from description
                    activity = "unknown"
                    if any(kw in description.lower() for kw in ["code", "programming", "ide", "terminal"]):
                        activity = "coding"
                    elif any(kw in description.lower() for kw in ["browser", "web", "chrome", "firefox"]):
                        activity = "browsing"
                    elif any(kw in description.lower() for kw in ["document", "text", "writing"]):
                        activity = "writing"

                    return {
                        "description": description[:200],
                        "detected_text": "",  # LLaVA doesn't extract text well
                        "activity_type": activity,
                        "detected_apps": [],
                        "confidence": 0.7
                    }

        except Exception as e:
            log.error(f"Local vision analysis failed: {e}")
            return {
                "description": "Could not analyze screen content",
                "detected_text": "",
                "activity_type": "unknown",
                "detected_apps": [],
                "confidence": 0.0
            }

    async def analyze_content(self, img: Image.Image) -> dict:
        """Route to appropriate vision provider"""
        provider = os.getenv("VISION_PROVIDER", self.vision_provider).lower()

        if provider == "gpt4v":
            return await self.analyze_with_gpt4v(img)
        elif provider == "gemini":
            return await self.analyze_with_gemini(img)
        elif provider == "claude":
            return await self.analyze_with_claude(img)
        elif provider == "local":
            return await self.analyze_with_local(img)
        else:
            log.warn(f"Unknown vision provider: {provider}, using Gemini")
            return await self.analyze_with_gemini(img)

    async def capture_and_analyze(self, force: bool = False) -> Optional[VisualContext]:
        """
        Capture screen and analyze content.

        Args:
            force: Skip change detection, always analyze

        Returns:
            VisualContext or None if no significant change
        """
        try:
            # Capture
            img = await self.capture_screen()

            # Check for change
            if not force and not self.detect_significant_change(img):
                log.info("No significant screen change detected")
                return self.last_context

            log.info("Analyzing screen content...")

            # Analyze
            analysis = await self.analyze_content(img)

            # Build context
            context = VisualContext(
                timestamp=time.time(),
                description=analysis["description"],
                detected_text=analysis["detected_text"],
                detected_apps=analysis["detected_apps"],
                activity_type=analysis["activity_type"],
                screenshot_hash=self.last_hash,
                confidence=analysis["confidence"]
            )

            self.last_context = context
            log.ok(f"Visual context: {context.activity_type} - {context.description[:50]}...")

            return context

        except Exception as e:
            log.error(f"Capture and analyze failed: {e}")
            return None

    async def get_current_context(self) -> Optional[VisualContext]:
        """Get current visual context (cached or fresh)"""
        if not self.last_context:
            return await self.capture_and_analyze(force=True)

        # Return cached if recent
        age = time.time() - self.last_context.timestamp
        if age < self.capture_interval:
            return self.last_context

        # Refresh
        return await self.capture_and_analyze()

    def get_context_summary(self) -> str:
        """Get text summary of current visual context for LLM"""
        if not self.last_context:
            return ""

        ctx = self.last_context
        age = int(time.time() - ctx.timestamp)

        summary = f"[VISUAL CONTEXT ({age}s ago)]\n"
        summary += f"Activity: {ctx.activity_type}\n"
        summary += f"Screen: {ctx.description}\n"

        if ctx.detected_text:
            summary += f"Visible text: {ctx.detected_text[:100]}\n"

        if ctx.detected_apps:
            summary += f"Apps: {', '.join(ctx.detected_apps)}\n"

        return summary

    async def cleanup(self):
        """Cleanup resources"""
        await self.stop_auto_capture()
        self.last_capture = None
        self.last_context = None


# Singleton instance
_awareness_engine: Optional[ContentAwarenessEngine] = None


def get_awareness_engine() -> ContentAwarenessEngine:
    """Get global awareness engine instance"""
    global _awareness_engine
    if _awareness_engine is None:
        _awareness_engine = ContentAwarenessEngine(
            vision_provider=os.getenv("VISION_PROVIDER", "gemini"),
            capture_interval=int(os.getenv("CAPTURE_INTERVAL", "10")),
            auto_capture=os.getenv("AUTO_CAPTURE", "false").lower() == "true"
        )
    return _awareness_engine
