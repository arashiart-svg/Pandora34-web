"""Точка входа: запуск бота Pandora34."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message, TelegramObject

from access import has_access
from config import settings
from handlers import router

log = logging.getLogger(__name__)


class AccessMiddleware(BaseMiddleware):
    """Чужим — один вежливый отказ, дальше молчим."""

    def __init__(self) -> None:
        self._told: set[int] = set()

    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        # /start всегда пропускаем — иначе человек не узнает свой chat_id
        if isinstance(event, Message) and (event.text or "").startswith("/start"):
            return await handler(event, data)
        if has_access(user.id):
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer("Нет доступа.", show_alert=True)
            return None

        if isinstance(event, Message) and user.id not in self._told:
            self._told.add(user.id)
            log.info("Отказ в доступе: %s id=%s", user.full_name, user.id)
            await event.answer(
                "Это внутренний бот автосервиса Pandora34. Доступа нет.\n"
                f"Если ты из сервиса — скинь владельцу свой id: {user.id}"
            )
        return None


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    if not settings.admin_chat_ids:
        log.warning("ADMIN_CHAT_IDS пустой — согласовывать черновики будет некому")
    if not settings.employee_chat_ids:
        log.warning("EMPLOYEE_CHAT_IDS пустой — фото не от кого принимать")

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    mw = AccessMiddleware()
    dp.message.outer_middleware(mw)
    dp.callback_query.outer_middleware(mw)
    dp.include_router(router)

    me = await bot.get_me()
    log.info("Бот @%s запущен. Сторис уходят админу в личку.", me.username)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
