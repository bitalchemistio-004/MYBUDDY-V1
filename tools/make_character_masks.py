"""Writes shell/ui/tests/fixtures/character-masks.json: where each buddy's body and eyes are in its 320 x 240 picture.

The decoration audit (shell/ui/js/buddy/wardrobe-audit.js) uses it to check that no decoration hides the buddy. The body is every
pixel that is at least half opaque in the first frame of the idle sheet; the eyes are the bright pixels inside the eye shapes below
(centre x, centre y, half width, half height, measured from the art). Run after the character art changes:
    python tools/make_character_masks.py
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CHARS = ROOT / "shell/ui/assets/buddy/chars"
EYES = {
    "blink": [(121.6, 111.6, 15, 17), (197.6, 111.6, 15, 17)],
    "void": [(135.6, 109.6, 13, 15), (183.6, 109.7, 13, 15)],
    "glitch": [(133.9, 110.4, 12, 17), (185.5, 109.5, 12, 17)],
    "cinder": [(133.6, 117.7, 12, 15), (185.6, 117.6, 12, 15)],
    "oracle": [(159.6, 111.5, 46, 40)],           # one big eye
}


def runs(mask):
    out = []
    for y in range(mask.shape[0]):
        x = 0
        while x < mask.shape[1]:
            if mask[y, x]:
                x0 = x
                while x < mask.shape[1] and mask[y, x]:
                    x += 1
                out.append([y, x0, x])
            else:
                x += 1
    return out


def main() -> None:
    out = {}
    yy, xx = np.mgrid[0:240, 0:320]
    for name, eyes in EYES.items():
        a = np.array(Image.open(CHARS / name / "idle.webp").convert("RGBA").crop((0, 0, 320, 240))).astype(float)
        lum = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
        body = a[..., 3] > 128
        inside = np.zeros_like(body)
        for cx, cy, rx, ry in eyes:
            inside |= ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 <= 1
        bright = (a[..., 3] > 200) & (lum > 190) & inside
        out[name] = {"body": runs(body), "eyes": runs(bright)}
        print(name, int(body.sum()), int(bright.sum()))
    path = ROOT / "shell/ui/tests/fixtures/character-masks.json"
    path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print("wrote", path, path.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
