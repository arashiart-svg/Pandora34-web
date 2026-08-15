"""Сборка картинки сторис 9:16: фото + текст + Pandora34."""

from __future__ import annotations

import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

STORY_W = 1080
STORY_H = 1920


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = (
        ["arialbd.ttf", "ARIALBD.TTF"] if bold else ["arial.ttf", "ARIAL.TTF"]
    )
    roots = [
        r"C:\Windows\Fonts",
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/truetype/liberation",
    ]
    extra = ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"]
    for root in roots:
        for name in list(names) + extra:
            path = os.path.join(root, name)
            if os.path.isfile(path):
                return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.replace("\n", " ").split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:8]


def _cover(photo: Image.Image) -> Image.Image:
    sw, sh = photo.size
    scale = max(STORY_W / sw, STORY_H / sh)
    nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
    photo = photo.resize((nw, nh), Image.Resampling.LANCZOS)
    left = max(0, (nw - STORY_W) // 2)
    top = max(0, (nh - STORY_H) // 2)
    return photo.crop((left, top, left + STORY_W, top + STORY_H))


def render_story(photo_bytes: bytes, text: str, brand: str = "PANDORA34") -> bytes:
    """Вертикальный кадр под сторис: фото на весь экран, снизу текст."""
    src = Image.open(BytesIO(photo_bytes)).convert("RGB")
    canvas = _cover(src).convert("RGBA")

    shade = Image.new("RGBA", (STORY_W, STORY_H), (0, 0, 0, 0))
    shade_draw = ImageDraw.Draw(shade)
    start = int(STORY_H * 0.52)
    for y in range(start, STORY_H):
        t = (y - start) / (STORY_H - start)
        alpha = int(40 + 190 * t)
        shade_draw.line([(0, y), (STORY_W, y)], fill=(0, 0, 0, alpha))
    canvas = Image.alpha_composite(canvas, shade)
    draw = ImageDraw.Draw(canvas)

    brand_font = _font(42, bold=True)
    draw.text((56, 72), brand, font=brand_font, fill=(255, 255, 255, 235))

    body_font = _font(52, bold=True)
    lines = _wrap(draw, (text or "").strip() or "Pandora34", body_font, STORY_W - 112)
    line_h = 64
    block_h = len(lines) * line_h
    y = STORY_H - 140 - block_h
    for line in lines:
        draw.text((56, y), line, font=body_font, fill=(255, 255, 255, 255))
        y += line_h

    out = BytesIO()
    canvas.convert("RGB").save(out, format="JPEG", quality=92)
    return out.getvalue()
