"""Build the buddy's app assets from the drawn frames: one sheet per clip.

Source: docs/design/buddy_art/chars/<name>/frames/NNN.png + frames.json (made
by draw_chars.py; not in git, it regenerates them). Output, committed:
shell/ui/assets/buddy/chars/<name>/<clip>.webp and <name>/clips.json.

One sheet for a whole character was 8000x5040 and decoded to ~160 MB in the
window. Per clip, only the clips in use are loaded (idle first), a few MB
each.

clips.json says which clips repeat (the generator's LOOPS), so the window
never keeps its own list of clip names. still.webp is one resting frame, for
places that show a character without playing it (the pickers). thumbs.webp
is one small frame per clip, for the gallery of reactions.

Usage: .venv\\Scripts\\python.exe tools\\build_buddy_assets.py [--manifest]
--manifest rewrites clips.json and still.webp only (the sheets take minutes).
"""
from __future__ import annotations

import ast
import json
import shutil
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "docs" / "design" / "buddy_art" / "chars"
OUT = REPO / "shell" / "ui" / "assets" / "buddy" / "chars"
COLS = 8   # frames per row in a clip's sheet


def loops() -> set[str]:
    """The clips that repeat, read from the generator (its LOOPS line) without running it."""
    tree = ast.parse((SRC.parent / "draw_chars.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "LOOPS" for t in node.targets):
            return set(ast.literal_eval(node.value))
    sys.exit("draw_chars.py has no LOOPS line")


def build(name: str, sheets: bool = True) -> dict:
    src = SRC / name
    spec = json.loads((src / "frames.json").read_text(encoding="utf-8"))
    w, h = spec["w"], spec["h"]
    clips: dict[str, list[dict]] = {}
    for frame in spec["frames"]:
        clips.setdefault(frame["anim"], []).append(frame)
    out = OUT / name
    if sheets and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"w": w, "h": h, "clips": {}}
    total = 0
    repeat = loops()
    for clip, frames in clips.items():
        cols = min(COLS, len(frames))
        manifest["clips"][clip] = {"cols": cols, "loop": clip in repeat, "durations": [int(f["ms"]) for f in frames]}
        if not sheets:
            continue
        rows = -(-len(frames) // cols)
        sheet = Image.new("RGBA", (cols * w, rows * h), (0, 0, 0, 0))
        for n, frame in enumerate(frames):
            tile = Image.open(src / "frames" / f"{frame['i']:03d}.png").convert("RGBA")
            sheet.paste(tile, ((n % cols) * w, (n // cols) * h))
        path = out / f"{clip}.webp"
        # Flat art is smallest lossless; art with glows is smallest as high-quality
        # lossy (lossless was 24 MB for the cast). Both are tried; the smaller is
        # kept. method 3: the slowest setting took over 10 minutes.
        sheet.save(path, "WEBP", lossless=True, method=3)
        lossy = path.with_suffix(".lossy.webp")
        sheet.save(lossy, "WEBP", quality=90, alpha_quality=100, method=3)
        if lossy.stat().st_size < path.stat().st_size:
            lossy.replace(path)
        else:
            lossy.unlink()
        total += path.stat().st_size
    rest = clips.get("idle") or next(iter(clips.values()))
    still = Image.open(src / "frames" / f"{rest[0]['i']:03d}.png").convert("RGBA")
    box = still.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox() or (0, 0, w, h)  # the character, not its soft shadow
    side = max(box[2] - box[0], box[3] - box[1]) + 16
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))   # small places (a 36 px avatar) need the character to fill the picture
    square.alpha_composite(still.crop(box), ((side - (box[2] - box[0])) // 2, (side - (box[3] - box[1])) // 2))
    square.save(out / "still.webp", "WEBP", lossless=True, method=3)
    # One small picture per clip, for the gallery of reactions: loading every
    # clip's own sheet just to show a thumbnail would take hundreds of MB.
    tw, th = w // 2, h // 2
    names = list(clips)
    tcols = min(COLS, len(names))
    strip = Image.new("RGBA", (tcols * tw, -(-len(names) // tcols) * th), (0, 0, 0, 0))
    for n, clip in enumerate(names):
        frames = clips[clip]
        pick = frames[int(len(frames) * 0.45)]   # a frame from the middle of the move, where the pose is clearest
        tile = Image.open(src / "frames" / f"{pick['i']:03d}.png").convert("RGBA").resize((tw, th), Image.LANCZOS)
        strip.paste(tile, ((n % tcols) * tw, (n // tcols) * th))
    strip.save(out / "thumbs.webp", "WEBP", quality=90, alpha_quality=100, method=4)
    manifest["thumbs"] = {"w": tw, "h": th, "cols": tcols, "order": names}
    (out / "clips.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    return {"name": name, "clips": len(clips), "frames": len(spec["frames"]), "kb": total // 1024}


def main() -> None:
    names = sorted(p.name for p in SRC.iterdir() if (p / "frames.json").is_file())
    if not names:
        sys.exit(f"no drawn frames under {SRC}: run docs/design/buddy_art/draw_chars.py first")
    for stale in [p for p in OUT.iterdir()] if OUT.exists() else []:
        if stale.name not in names:   # a character that is no longer in the cast, or the old whole sheets
            shutil.rmtree(stale) if stale.is_dir() else stale.unlink()
    for name in names:
        print(build(name, sheets="--manifest" not in sys.argv))


if __name__ == "__main__":
    main()
