"""Сборка сторис 9:16: фото как есть, логотип, канал, короткий текст."""

from __future__ import annotations

import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont

STORY_W = 1080
STORY_H = 1920
HANDLE = "@PANDORA34RU"
CYAN = (37, 132, 222)
WHITE = (255, 255, 255)

_DIR = os.path.dirname(os.path.abspath(__file__))
_LOGO_PATHS = (
    os.path.join(_DIR, "assets", "logo.png"),
    "/app/assets/logo.png",
)
_FONT_PATHS = (
    os.path.join(_DIR, "assets", "fonts", "Montserrat-Bold.ttf"),
    "/app/assets/fonts/Montserrat-Bold.ttf",
)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_PATHS:
        if os.path.isfile(path):
            return ImageFont.truetype(path, size)
    names = ["arialbd.ttf", "ARIALBD.TTF"] if bold else ["arial.ttf", "ARIAL.TTF"]
    roots = [r"C:\Windows\Fonts", "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation"]
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
    return lines[:5]


def _cover(photo: Image.Image) -> Image.Image:
    sw, sh = photo.size
    scale = max(STORY_W / sw, STORY_H / sh)
    nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
    photo = photo.resize((nw, nh), Image.Resampling.LANCZOS)
    left = max(0, (nw - STORY_W) // 2)
    top = max(0, (nh - STORY_H) // 2)
    return photo.crop((left, top, left + STORY_W, top + STORY_H))


def _drawn_logo(size: int) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, size - 1, size - 1), fill=(10, 10, 10, 255))
    s = size
    box = (int(s * 0.32), int(s * 0.14), int(s * 0.68), int(s * 0.48))
    d.arc(box, 200, 20, fill=WHITE, width=max(3, s // 18))
    body = (int(s * 0.30), int(s * 0.36), int(s * 0.70), int(s * 0.64))
    d.rounded_rectangle(body, radius=s // 16, fill=WHITE)
    hole_r = max(2, s // 28)
    hx, hy = s // 2, int(s * 0.48)
    d.ellipse((hx - hole_r, hy - hole_r, hx + hole_r, hy + hole_r), fill=(10, 10, 10, 255))
    font = _font(max(10, s // 9), bold=True)
    label = "Pandora34"
    tw = d.textlength(label, font=font)
    d.text(((s - tw) / 2, int(s * 0.70)), label, font=font, fill=WHITE)
    return im


def _load_logo(size: int) -> Image.Image:
    for path in _LOGO_PATHS:
        if os.path.isfile(path):
            return Image.open(path).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    return _drawn_logo(size)


def _bottom_fade() -> Image.Image:
    """Только низ, чёрный — без синей каши по всему кадру."""
    layer = Image.new("RGBA", (STORY_W, STORY_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    start = int(STORY_H * 0.72)
    span = STORY_H - start
    for y in range(start, STORY_H):
        t = (y - start) / max(1, span)
        draw.line([(0, y), (STORY_W, y)], fill=(0, 0, 0, int(12 + 175 * t)))
    return layer


def render_story(photo_bytes: bytes, text: str, brand: str = "PANDORA34") -> bytes:
    del brand
    src = Image.open(BytesIO(photo_bytes)).convert("RGB")
    canvas = _cover(src).convert("RGBA")
    canvas = Image.alpha_composite(canvas, _bottom_fade())
    draw = ImageDraw.Draw(canvas)

    logo_size = 152
    logo = _load_logo(logo_size)
    lx, ly = 40, 48

    pill_w, pill_h = 620, 176
    pill = Image.new("RGBA", (pill_w, pill_h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pill)
    pd.rounded_rectangle((0, 0, pill_w - 1, pill_h - 1), radius=88, fill=(0, 0, 0, 150))
    canvas.paste(pill, (28, 36), pill)

    shadow = Image.new("RGBA", (logo_size + 20, logo_size + 20), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((2, 6, logo_size + 10, logo_size + 14), fill=(0, 0, 0, 90))
    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    canvas.paste(shadow, (lx - 6, ly - 2), shadow)
    canvas.paste(logo, (lx, ly), logo)

    handle_font = _font(36, bold=True)
    sub_font = _font(26, bold=True)
    tx = lx + logo_size + 18
    ty = ly + 38
    draw.text((tx, ty), HANDLE, font=handle_font, fill=(*CYAN, 255))
    draw.text((tx, ty + 46), "автосервис", font=sub_font, fill=(255, 255, 255, 210))

    body = (text or "").strip() or "Pandora34"
    body_font = _font(48, bold=True)
    lines = _wrap(draw, body, body_font, STORY_W - 120)
    line_h = 60
    y = STORY_H - 120 - len(lines) * line_h
    draw.rectangle((48, y - 18, 48 + 64, y - 12), fill=(*CYAN, 255))
    for line in lines:
        draw.text((50, y + 2), line, font=body_font, fill=(0, 0, 0, 90))
        draw.text((48, y), line, font=body_font, fill=(*WHITE, 255))
        y += line_h

    out = BytesIO()
    canvas.convert("RGB").save(out, format="JPEG", quality=95)
    return out.getvalue()
