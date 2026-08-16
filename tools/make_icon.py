#!/usr/bin/env python3
"""Generate adops.ico from the app's favicon mark (blue tile + summit glyph).

Mirrors the SVG favicon in web/index.html so the desktop shortcut, browser tab
and in-app brand mark all match. Drawn at 8x and downsampled for clean edges.

    python tools/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / 'web' / 'adops.ico'
ACCENT = (108, 140, 255, 255)   # #6C8CFF
INK = (15, 17, 23, 255)         # #0F1117
SIZES = [256, 128, 64, 48, 32, 16]


def render(size: int) -> Image.Image:
    s = size * 8  # supersample
    img = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 7 / 32), fill=ACCENT)

    # Summit/chart peak: SVG path M9 22 L16 9 L23 22 on a 32-unit grid.
    u = s / 32.0
    pts = [(9 * u, 22 * u), (16 * u, 9 * u), (23 * u, 22 * u)]
    d.line(pts, fill=INK, width=int(3 * u), joint='curve')
    # Round the caps the SVG's stroke-linecap="round" would give.
    r = 1.5 * u
    for x, y in (pts[0], pts[2]):
        d.ellipse([x - r, y - r, x + r, y + r], fill=INK)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    frames = [render(n) for n in SIZES]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(OUT, format='ICO', sizes=[(n, n) for n in SIZES])
    print(f'wrote {OUT} ({", ".join(f"{n}x{n}" for n in SIZES)})')


if __name__ == '__main__':
    main()
