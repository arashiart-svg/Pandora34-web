"""Генерация текста поста через Claude по фото с диагностики."""

from __future__ import annotations

import base64
import logging

import anthropic
from anthropic import AsyncAnthropic

from config import settings

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """
Ты автор сторис автосервиса Pandora34. Пишешь как живой человек из бокса, не как нейросеть и не как приёмка.

Формат: 1 короткая фраза, максимум 2. 8–18 слов. На «ты». Без точки в конце, если звучит жёстче.

Как писать:
- Работа из темы мастера + одна деталь с фото (ключ на полу, панель, номер, грязь, провода).
- Настоящее время или «сегодня / сейчас».
- Можно суховатый юмор, без шуток про магию и «профессионалов».

Запрещено слово в слово:
приехал на, прибыл, осуществлён, произведён, работаем для вас, чёрная магия, работает как надо, ещё один день, куча проводов, маленькая плата, доверьте, подпишись, хештеги, эмодзи.

Не копируй тему мастера как предложение. Тема — это ЧТО делаем, не готовый текст.
Не выдумывай то, чего нет на фото и в теме.

Плохо: «Hyundai Solaris приехал на удаление катализатора»
Хорошо: «Катализатор с Solaris сегодня остаётся у нас»
Хорошо: «Гайковёрт на полу, катализатор на выход»

Пиши только текст сторис, без кавычек и пояснений.
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


async def generate_post(photo_bytes: bytes | list[bytes], caption: str | None = None) -> str:
    """Кидает фото (одно или несколько) и подпись мастера в Claude, возвращает текст поста."""
    photos = [photo_bytes] if isinstance(photo_bytes, (bytes, bytearray)) else list(photo_bytes)
    if not photos:
        raise ValueError("Нет фото для генерации поста")

    user_text = (caption or "").strip()
    if not settings.anthropic_api_key:
        log.info("Claude нет — текст из подписи мастера или заглушка")
        return user_text or "Работа Pandora34"

    content: list[dict] = [_image_block(p) for p in photos]
    if user_text:
        content.append(
            {
                "type": "text",
                "text": (
                    f"Работа: {user_text}\n"
                    "Это вид работы, не готовая фраза. Не пиши «приехал на …». "
                    "Собери сторис: работа + что видно на кадре."
                ),
            }
        )
    else:
        content.append(
            {
                "type": "text",
                "text": "Темы от мастера нет. Смотри фото и напиши сторис только по кадру.",
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
            max_tokens=160,
            temperature=0.95,
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

    text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text").strip()
    if not text:
        raise RuntimeError("Claude вернул пустой текст.")
    log.info("Claude ответил, %s символов", len(text))
    return text
