"""Сборка сторис 9:16: большое лого, канал, жирный текст."""

from __future__ import annotations

import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont

STORY_W = 1080
STORY_H = 1920
HANDLE = "@PANDORA34RU"
CYAN = (45, 156, 245)
NEON = (0, 186, 255)
SOFT = (45, 156, 245)
WHITE = (255, 255, 255)

_DIR = os.path.dirname(os.path.abspath(__file__))
_LOGO_PATHS = (
    os.path.join(_DIR, "assets", "logo.png"),
    "/app/assets/logo.png",
)
_DISPLAY_FONTS = (
    os.path.join(_DIR, "assets", "fonts", "Unbounded-Bold.ttf"),
    "/app/assets/fonts/Unbounded-Bold.ttf",
)
_UI_FONTS = (
    os.path.join(_DIR, "assets", "fonts", "Montserrat-Bold.ttf"),
    "/app/assets/fonts/Montserrat-Bold.ttf",
)


def _ttf(paths, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in paths:
        if os.path.isfile(path):
            return ImageFont.truetype(path, size)
    fallback = os.path.join("/usr/share/fonts/truetype/dejavu", "DejaVuSans-Bold.ttf")
    if os.path.isfile(fallback):
        return ImageFont.truetype(fallback, size)
    arial = r"C:\Windows\Fonts\arialbd.ttf"
    if os.path.isfile(arial):
        return ImageFont.truetype(arial, size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int) -> list[str] | None:
    words = text.replace("\n", " ").split()
    if not words:
        return []
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
            if draw.textlength(current, font=font) > max_width:
                return None
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        return None
    return lines


def _fit_caption(draw: ImageDraw.ImageDraw, text: str) -> tuple[list[str], ImageFont.FreeTypeFont | ImageFont.ImageFont]:
    max_w = STORY_W - 180
    body = " ".join((text or "").split()) or "Pandora34"
    for max_lines in (2, 3):
        for size in (50, 46, 42, 38, 34, 30):
            font = _ttf(_DISPLAY_FONTS, size)
            lines = _wrap(draw, body, font, max_w, max_lines)
            if lines:
                return lines, font
    font = _ttf(_DISPLAY_FONTS, 28)
    lines = _wrap(draw, body, font, max_w, 4)
    if lines:
        return lines, font
    return [body], font


def _cover(photo: Image.Image) -> Image.Image:
    sw, sh = photo.size
    scale = max(STORY_W / sw, STORY_H / sh)
    nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
    photo = photo.resize((nw, nh), Image.Resampling.LANCZOS)
    left = max(0, (nw - STORY_W) // 2)
    top = max(0, (nh - STORY_H) // 2)
    return photo.crop((left, top, left + STORY_W, top + STORY_H))


def _load_logo(size: int) -> Image.Image:
    for path in _LOGO_PATHS:
        if os.path.isfile(path):
            return Image.open(path).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, size - 1, size - 1), fill=(8, 8, 8, 255))
    return im


def _bottom_fade() -> Image.Image:
    layer = Image.new("RGBA", (STORY_W, STORY_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    start = int(STORY_H * 0.68)
    span = STORY_H - start
    for y in range(start, STORY_H):
        t = (y - start) / max(1, span)
        draw.line([(0, y), (STORY_W, y)], fill=(0, 0, 0, int(8 + 200 * (t ** 1.15))))
    return layer


def render_story(photo_bytes: bytes, text: str, brand: str = "PANDORA34") -> bytes:
    del brand
    src = Image.open(BytesIO(photo_bytes)).convert("RGB")
    canvas = _cover(src).convert("RGBA")
    canvas = Image.alpha_composite(canvas, _bottom_fade())
    draw = ImageDraw.Draw(canvas)

    logo_size = 292
    logo = _load_logo(logo_size)
    lx, ly = 36, 44

    shadow = Image.new("RGBA", (logo_size + 40, logo_size + 40), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((8, 14, logo_size + 24, logo_size + 30), fill=(0, 0, 0, 140))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    canvas.paste(shadow, (lx - 16, ly - 8), shadow)
    canvas.paste(logo, (lx, ly), logo)

    handle_font = _ttf(_DISPLAY_FONTS, 40)
    sub_font = _ttf(_UI_FONTS, 26)
    tx = lx + logo_size + 20
    ty = ly + 78
    handle_w = draw.textlength(HANDLE, font=handle_font)
    sub_w = draw.textlength("АВТОСЕРВИС", font=sub_font)
    pill_w = int(max(handle_w, sub_w) + 48)
    pill_h = 148
    pill = Image.new("RGBA", (pill_w, pill_h), (0, 0, 0, 0))
    ImageDraw.Draw(pill).rounded_rectangle((0, 0, pill_w - 1, pill_h - 1), radius=28, fill=(0, 0, 0, 225))
    canvas.paste(pill, (tx - 22, ty - 28), pill)
    draw = ImageDraw.Draw(canvas)
    draw.text((tx, ty), HANDLE, font=handle_font, fill=(*CYAN, 255))
    draw.text((tx, ty + 58), "АВТОСЕРВИС", font=sub_font, fill=(*WHITE, 255))

    lines, body_font = _fit_caption(draw, text)
    size = getattr(body_font, "size", 50) or 50
    line_h = int(size * 1.2)
    pad_x, pad_y, accent = 32, 26, 12
    max_line_w = max(draw.textlength(line, font=body_font) for line in lines) if lines else 200
    box_w = int(min(STORY_W - 48, accent + pad_x * 2 + max_line_w))
    box_h = int(pad_y * 2 + max(1, len(lines)) * line_h - 8)
    bx, by = 28, STORY_H - 48 - box_h

    glow = Image.new("RGBA", (box_w + 90, box_h + 90), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle(
        (18, 18, box_w + 70, box_h + 70), radius=34, fill=(45, 180, 255, 80)
    )
    glow = glow.filter(ImageFilter.GaussianBlur(16))
    canvas.paste(glow, (bx - 36, by - 36), glow)

    cap = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    cd = ImageDraw.Draw(cap)
    cd.rounded_rectangle((0, 0, box_w - 1, box_h - 1), radius=24, fill=(6, 8, 12, 236))
    cd.rounded_rectangle((10, 16, 10 + accent, box_h - 17), radius=5, fill=(*NEON, 255))
    canvas.paste(cap, (bx, by), cap)
    draw = ImageDraw.Draw(canvas)
    ty = by + pad_y - 6
    tx = bx + accent + pad_x
    for i, line in enumerate(lines):
        fill = NEON if i == 0 else SOFT
        draw.text((tx, ty), line, font=body_font, fill=(*fill, 255))
        ty += line_h

    out = BytesIO()
    canvas.convert("RGB").save(out, format="JPEG", quality=95)
    return out.getvalue()
