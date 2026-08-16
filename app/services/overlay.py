"""Deterministic Pillow post-processing for generated ads.

Image models garble small legal text, so the mandatory FSP disclaimer is
rendered as a real text bar and the exact client logo is composited - never
left to the model.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    r'C:\Windows\Fonts\Montserrat-Regular.ttf',
    r'C:\Windows\Fonts\segoeui.ttf',
    r'C:\Windows\Fonts\arial.ttf',
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words, lines, current = text.split(), [], ''
    for word in words:
        trial = f'{current} {word}'.strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def apply_overlay(image_path: str, disclaimer: str = None, logo_path: str = None,
                  out_path: str = None, target_size: tuple = (1080, 1080)) -> str:
    """Composite a disclaimer bar (bottom) and logo (top-right) onto an ad image.

    Resizes square images to target_size first (the client boosted-ad spec is
    1080x1080; Gemini emits 1024x1024)."""
    img = Image.open(image_path).convert('RGBA')
    if target_size and img.width == img.height and img.size != target_size:
        img = img.resize(target_size, Image.LANCZOS)
    w, h = img.size
    draw = ImageDraw.Draw(img, 'RGBA')

    if logo_path and Path(logo_path).exists() and not logo_path.lower().endswith('.svg'):
        logo = Image.open(logo_path).convert('RGBA')
        target_w = int(w * 0.20)
        ratio = target_w / logo.width
        logo = logo.resize((target_w, int(logo.height * ratio)), Image.LANCZOS)
        margin = int(w * 0.035)
        img.alpha_composite(logo, (w - logo.width - margin, margin))

    if disclaimer:
        font_size = max(12, int(w * 0.016))
        font = _font(font_size)
        pad = int(w * 0.02)
        lines = _wrap(draw, disclaimer, font, w - pad * 2)
        line_h = font_size + 4
        bar_h = pad * 2 + line_h * len(lines)
        bar = Image.new('RGBA', (w, bar_h), (10, 10, 14, 215))
        img.alpha_composite(bar, (0, h - bar_h))
        y = h - bar_h + pad
        for line in lines:
            draw.text((pad, y), line, font=font, fill=(235, 235, 240, 255))
            y += line_h

    out = out_path or image_path
    img.convert('RGB').save(out, 'PNG')
    return out
