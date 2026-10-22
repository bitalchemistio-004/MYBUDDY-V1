"""
api/animation_editor.py — Enhanced animation editor with preview and management.

Provides REST API endpoints for:
- Live animation preview
- Animation library management
- Upload/download animations
- Frame-by-frame editing
- GIF export
"""
from aiohttp import web
import json
import base64
from pathlib import Path

# Animation library directory
ANIMATIONS_DIR = Path("animations")
ANIMATIONS_DIR.mkdir(exist_ok=True)


async def list_animations_handler(request: web.Request) -> web.Response:
    """List all available animations with metadata."""
    animations = []

    for anim_file in ANIMATIONS_DIR.glob("*.json"):
        try:
            with open(anim_file, 'r') as f:
                data = json.load(f)
                animations.append({
                    "name": anim_file.stem,
                    "frames": len(data.get("frames", [])),
                    "duration": data.get("duration", 0),
                    "tags": data.get("tags", []),
                    "description": data.get("description", ""),
                })
        except Exception:
            continue

    return web.json_response({"animations": animations})


async def get_animation_handler(request: web.Request) -> web.Response:
    """Get animation data for preview/editing."""
    name = request.match_info.get("name")

    anim_file = ANIMATIONS_DIR / f"{name}.json"
    if not anim_file.exists():
        return web.json_response({"error": "Animation not found"}, status=404)

    try:
        with open(anim_file, 'r') as f:
            data = json.load(f)
        return web.json_response(data)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def save_animation_handler(request: web.Request) -> web.Response:
    """Save/update animation."""
    try:
        data = await request.json()
        name = data.get("name")

        if not name:
            return web.json_response({"error": "Name required"}, status=400)

        # Validate structure
        if "frames" not in data:
            return web.json_response({"error": "Frames required"}, status=400)

        anim_file = ANIMATIONS_DIR / f"{name}.json"
        with open(anim_file, 'w') as f:
            json.dump(data, f, indent=2)

        return web.json_response({"ok": True, "name": name})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def delete_animation_handler(request: web.Request) -> web.Response:
    """Delete animation."""
    name = request.match_info.get("name")

    anim_file = ANIMATIONS_DIR / f"{name}.json"
    if not anim_file.exists():
        return web.json_response({"error": "Animation not found"}, status=404)

    try:
        anim_file.unlink()
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def export_gif_handler(request: web.Request) -> web.Response:
    """Export animation as GIF (requires PIL)."""
    name = request.match_info.get("name")

    anim_file = ANIMATIONS_DIR / f"{name}.json"
    if not anim_file.exists():
        return web.json_response({"error": "Animation not found"}, status=404)

    try:
        from PIL import Image

        with open(anim_file, 'r') as f:
            data = json.load(f)

        frames = data.get("frames", [])
        if not frames:
            return web.json_response({"error": "No frames"}, status=400)

        # Each frame must carry its own real pixel data as a flat 128*64
        # array (0/1 per pixel, row-major, matching the OLED's monochrome
        # 128x64 layout) under "pixels". Frames with no pixel data used to
        # silently render as solid black and still report {"ok": true} with
        # a real GIF file attached — a fabricated success. If nothing here
        # actually has pixel data, say so instead of exporting blank frames.
        images = []
        for i, frame in enumerate(frames):
            pixels = frame.get("pixels")
            if not pixels or len(pixels) != 128 * 64:
                return web.json_response({
                    "error": (
                        f"Frame {i} has no usable pixel data (expected a "
                        f"128x64 'pixels' array); refusing to export a blank "
                        f"GIF in its place."
                    ),
                }, status=400)
            img = Image.new('1', (128, 64), 0)
            img.putdata([1 if p else 0 for p in pixels])
            images.append(img)

        # Save as GIF
        gif_path = ANIMATIONS_DIR / f"{name}.gif"
        images[0].save(
            gif_path,
            save_all=True,
            append_images=images[1:],
            duration=data.get("frame_duration", 100),
            loop=0
        )

        # Return as base64
        with open(gif_path, 'rb') as f:
            gif_data = base64.b64encode(f.read()).decode()

        return web.json_response({
            "ok": True,
            "gif": f"data:image/gif;base64,{gif_data}"
        })
    except ImportError:
        return web.json_response({"error": "PIL not installed"}, status=500)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


def register_animation_editor_routes(app: web.Application) -> None:
    """Register animation editor routes."""
    app.router.add_get("/animations", list_animations_handler)
    app.router.add_get("/animations/{name}", get_animation_handler)
    app.router.add_post("/animations", save_animation_handler)
    app.router.add_delete("/animations/{name}", delete_animation_handler)
    app.router.add_get("/animations/{name}/export/gif", export_gif_handler)
