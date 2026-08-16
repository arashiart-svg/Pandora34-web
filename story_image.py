"""Сборка сторис 9:16: большое лого, канал, жирный текст."""

from __future__ import annotations

import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont

STORY_W = 1080
STORY_H = 1920
HANDLE = "@PANDORA34RU"
CYAN = (45, 156, 245)
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


def _first_sentence(text: str) -> str:
    raw = " ".join(text.replace("\n", " ").split())
    for sep in (". ", "! ", "? ", " — ", " – "):
        if sep in raw:
            head = raw.split(sep, 1)[0].strip(" —–")
            if len(head) >= 12:
                return head + ("." if sep.startswith(".") else "")
    return raw


def _fit_caption(draw: ImageDraw.ImageDraw, text: str) -> tuple[list[str], ImageFont.FreeTypeFont | ImageFont.ImageFont]:
    max_w = STORY_W - 80
    body = " ".join((text or "").split()) or "Pandora34"
    candidates = [body]
    short = _first_sentence(body)
    if short != body:
        candidates.append(short)
    for size in (56, 50, 46, 42):
        font = _ttf(_DISPLAY_FONTS, size)
        for candidate in candidates:
            lines = _wrap(draw, candidate, font, max_w, 2)
            if lines:
                return lines, font
    font = _ttf(_DISPLAY_FONTS, 42)
    return _wrap(draw, short, font, max_w, 2) or [short[:28]], font


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


def _stroke_text(draw, xy, text, font, fill, stroke):
    x, y = xy
    draw.text((x, y), text, font=font, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0, 220))


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
    line_h = int(body_font.size * 1.28) if getattr(body_font, "size", None) else 66
    y = STORY_H - 88 - len(lines) * line_h
    draw.rectangle((40, y - 22, 40 + 88, y - 14), fill=(*CYAN, 255))
    for line in lines:
        _stroke_text(draw, (40, y), line, body_font, (*WHITE, 255), 3)
        y += line_h

    out = BytesIO()
    canvas.convert("RGB").save(out, format="JPEG", quality=95)
    return out.getvalue()
