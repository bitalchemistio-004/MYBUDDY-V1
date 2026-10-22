"""The phone app's icon: true black, a warm glow, and the buddy's two eyes. Own drawing, no borrowed art.
Run once: .venv/Scripts/python.exe tools/make_pwa_icons.py  (writes shell/ui/assets/pwa/icon-192.png and icon-512.png)"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parents[1] / "shell" / "ui" / "assets" / "pwa"


def icon(size: int) -> Image.Image:
    s = size * 2
    img = Image.new("RGB", (s, s), "#000000")
    glow = Image.new("RGB", (s, s), "#000000")
    d = ImageDraw.Draw(glow)
    d.ellipse((s * 0.14, s * 0.2, s * 0.86, s * 0.8), fill="#ffcc33")
    glow = glow.filter(ImageFilter.GaussianBlur(s * 0.12))
    img = Image.blend(img, glow, 0.55)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((s * 0.2, s * 0.32, s * 0.8, s * 0.68), radius=s * 0.17, fill="#050505", outline="#ffcc33", width=max(2, int(s * 0.012)))
    for cx in (0.38, 0.62):
        d.ellipse((s * (cx - 0.055), s * 0.44, s * (cx + 0.055), s * 0.56), fill="#ffffff")
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for n in (192, 512):
        icon(n).save(OUT / f"icon-{n}.png")
    print("wrote", [p.name for p in OUT.iterdir()])
