"""Генерация текста поста через Claude по фото с диагностики."""

from __future__ import annotations

import base64
import logging
import re

import anthropic
from anthropic import AsyncAnthropic

from config import settings

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """
Ты пишешь сторис Pandora34 — автоэлектрика. Не ходовая, не масло, не шины. Фото-бокс не описывай.

Два регистра по теме мастера:

ДОП (сигналка, автозапуск, магнитола, камера, ПТФ, свет, комфорт) — чуть лирики, из слов: контроль 24/7, комфорт, автозапуск, защита, охрана, удобство. Без воров, чужих ключей и «кто полез».
Как владелец пишет:
«Нет ничего лучше прийти зимой в тёплый авто — с автозапуском Pandora.»
«Подошёл — уже тёплая. Автозапуск и охрана Pandora.»
«Следить и управлять авто с телефона? Контроль 24/7 с сигнализацией Pandora.»
«Полный спектр развлечений и комфорта с новой магнитолой. Камера заднего вида? Почему бы и нет.»

РЕМОНТ (пайка, SRS, щиток, проводка, блоки, восстановление пробега) — суше: факт + услуга.
«Щиток Focus снова в работе. Восстановление пайки панелей Ford.»
«Щиток новый — пробег родной. Восстановление пробега.»

Не копируй эталоны дословно под другую работу. Максимум 140 знаков на вариант. Без чая, «заезжай», «доверьте», хештегов, эмодзи.

Всегда дай РОВНО ТРИ разных варианта. Не повторяй одну мысль. Формат строго:

1) первая сторис
2) вторая сторис
3) третья сторис

Без кавычек и без текста вокруг.
""".strip()


def _media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _image_block(photo_bytes: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": _media_type(photo_bytes),
            "data": base64.b64encode(photo_bytes).decode("ascii"),
        },
    }


async def generate_post(photo_bytes: bytes | list[bytes], caption: str | None = None) -> list[str]:
    """Кидает фото и подпись мастера в Claude, возвращает 3 варианта текста."""
    photos = [photo_bytes] if isinstance(photo_bytes, (bytes, bytearray)) else list(photo_bytes)
    if not photos:
        raise ValueError("Нет фото для генерации поста")

    user_text = (caption or "").strip()
    if not settings.anthropic_api_key:
        log.info("Claude нет — текст из подписи мастера или заглушка")
        fallback = user_text or "Работа Pandora34"
        return [fallback, fallback, fallback]

    content: list[dict] = [_image_block(p) for p in photos]
    if user_text:
        content.append(
            {
                "type": "text",
                "text": (
                    f"Работа: {user_text}\n"
                    "Три разных варианта сторис. Если доп — лирично, из слов контроль/комфорт/автозапуск/защита/охрана/удобство. "
                    "Если ремонт — факт + услуга. Бокс не описывай. Каждый вариант до 140 знаков."
                ),
            }
        )
    else:
        content.append(
            {
                "type": "text",
                "text": (
                    "Темы нет. Три разных варианта. Доп — лирично, ремонт — факт + услуга. "
                    "Бокс не описывай. Каждый до 140 знаков."
                ),
            }
        )

    log.info("Отправляю в Claude %s фото, модель %s, подпись: %s", len(photos), MODEL, user_text or "—")

    client_kwargs = {"api_key": settings.anthropic_api_key}
    if settings.anthropic_base_url:
        client_kwargs["base_url"] = settings.anthropic_base_url
        log.info("Claude API через прокси: %s", settings.anthropic_base_url)
    client = AsyncAnthropic(**client_kwargs)
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=500,
            temperature=1.0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.RateLimitError as exc:
        log.exception("Claude: лимит запросов")
        raise RuntimeError("Claude сейчас перегружен (лимит). Попробуй через минуту.") from exc
    except anthropic.APIConnectionError as exc:
        log.exception("Claude: нет связи")
        raise RuntimeError("Не достучался до Claude. Проверь интернет.") from exc
    except anthropic.APIStatusError as exc:
        log.exception("Claude: ошибка API %s", exc.status_code)
        raise RuntimeError(f"Claude вернул ошибку {exc.status_code}.") from exc
    except anthropic.APIError as exc:
        log.exception("Claude: общая ошибка API")
        raise RuntimeError("Claude не смог обработать фото. Попробуй ещё раз.") from exc

    raw = "".join(block.text for block in response.content if getattr(block, "type", None) == "text").strip()
    if not raw:
        raise RuntimeError("Claude вернул пустой текст.")
    variants = _parse_variants(raw)
    log.info("Claude ответил, вариантов: %s", len(variants))
    return variants


def _parse_variants(raw: str) -> list[str]:
    found = re.findall(r"(?:^|\n)\s*(?:[123][\)\.\:]|[-*])\s*(.+)", raw)
    cleaned = [" ".join(item.split()) for item in found if item.strip()]
    uniq: list[str] = []
    for item in cleaned:
        if item not in uniq:
            uniq.append(item)
    if len(uniq) >= 3:
        return uniq[:3]
    if uniq:
        while len(uniq) < 3:
            uniq.append(uniq[0])
        return uniq[:3]
    one = " ".join(raw.split())
    return [one, one, one]
