"""Сообщения, кнопки, согласование. Готовую сторис кидает тебе в личку — выкладываешь сам."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    User,
)

from access import is_admin, is_employee
from claude_client import generate_post
from config import settings
from story_image import render_story

log = logging.getLogger(__name__)
router = Router()

# Альбомы прилетают пачкой сообщений с одним media_group_id — собираем их сюда.
_albums: dict[str, list[Message]] = {}
_album_flush: dict[str, asyncio.Task] = {}


class EditDraft(StatesGroup):
    waiting_text = State()


@dataclass
class AdminCopy:
    chat_id: int
    message_id: int


@dataclass
class Draft:
    id: str
    employee_id: int
    employee_label: str
    file_ids: list[str]
    photos: list[bytes]
    text: str
    processed: bool = False
    admin_copies: list[AdminCopy] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


drafts: dict[str, Draft] = {}


def _who(user: User | None) -> str:
    if user is None:
        return "неизвестный"
    parts = [user.full_name]
    if user.username:
        parts.append(f"@{user.username}")
    parts.append(f"id={user.id}")
    return " ".join(parts)


def _can_submit(user_id: int) -> bool:
    # Фото принимают сотрудники. Админа тоже пускаем — владелец часто только в ADMIN.
    return is_employee(user_id) or is_admin(user_id)


def _draft_kb(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Сторис мне", callback_data=f"pub:{draft_id}"),
            ],
            [
                InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"edit:{draft_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"rej:{draft_id}"),
            ],
        ]
    )


def _draft_body(draft: Draft, footer: str | None = None) -> str:
    text = (
        f"Черновик сторис\n\n"
        f"От: {draft.employee_label}\n\n"
        f"{draft.text}"
    )
    if footer:
        text += f"\n\n{footer}"
    return text


async def _download_photo(bot: Bot, file_id: str) -> bytes:
    file = await bot.get_file(file_id)
    buf = BytesIO()
    await bot.download(file, destination=buf)
    return buf.getvalue()


async def _send_photos(bot: Bot, chat_id: int, file_ids: list[str]) -> None:
    if len(file_ids) == 1:
        await bot.send_photo(chat_id, file_ids[0])
        return
    media = [InputMediaPhoto(media=fid) for fid in file_ids]
    await bot.send_media_group(chat_id, media)


async def _edit_admin_copies(bot: Bot, draft: Draft, footer: str) -> None:
    """Убираем кнопки у всех админов и пишем, кто что решил."""
    body = _draft_body(draft, footer)
    for copy in draft.admin_copies:
        try:
            await bot.edit_message_text(
                text=body,
                chat_id=copy.chat_id,
                message_id=copy.message_id,
                reply_markup=None,
            )
        except TelegramBadRequest as exc:
            log.warning("Не смог обновить черновик админу %s: %s", copy.chat_id, exc)


async def _refresh_admin_copies(bot: Bot, draft: Draft) -> None:
    """После правки текста снова показываем кнопки всем админам."""
    body = _draft_body(draft)
    kb = _draft_kb(draft.id)
    for copy in draft.admin_copies:
        try:
            await bot.edit_message_text(
                text=body,
                chat_id=copy.chat_id,
                message_id=copy.message_id,
                reply_markup=kb,
            )
        except TelegramBadRequest as exc:
            log.warning("Не смог обновить текст черновика %s: %s", copy.chat_id, exc)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    log.info("/start от %s", _who(message.from_user))
    if is_admin(uid):
        await message.answer(
            f"Ты админ. Черновики сторис приходят сюда. Кнопка «Сторис мне» — "
            f"готовая картинка 9:16 тебе в чат, выкладываешь со своего аккаунта.\n"
            f"Твой chat_id: {uid}"
        )
        return
    if is_employee(uid):
        await message.answer(
            "Кидай фото с работы (одно или несколько). "
            "Подпись к фото по желанию — я соберу черновик и отправлю владельцу на согласование.\n"
            f"Твой chat_id: {uid}"
        )
        return
    await message.answer(
        f"Твой chat_id: {uid}\n\n"
        "Тебя ещё нет в списке. Впиши это число в .env в ADMIN_CHAT_IDS и EMPLOYEE_CHAT_IDS, "
        "сохрани файл и снова запусти start.bat."
    )


@router.message(F.photo, StateFilter(None))
async def on_photo(message: Message, bot: Bot) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not _can_submit(uid):
        await message.answer("Фото принимает только сотрудник из списка.")
        return

    group_id = message.media_group_id
    if not group_id:
        await _handle_submission([message], bot)
        return

    _albums.setdefault(group_id, []).append(message)
    old = _album_flush.get(group_id)
    if old:
        old.cancel()

    async def _flush() -> None:
        try:
            await asyncio.sleep(1.3)
        except asyncio.CancelledError:
            return
        msgs = _albums.pop(group_id, [])
        _album_flush.pop(group_id, None)
        if msgs:
            await _handle_submission(msgs, bot)

    _album_flush[group_id] = asyncio.create_task(_flush())


async def _handle_submission(messages: list[Message], bot: Bot) -> None:
    first = messages[0]
    user = first.from_user
    if user is None:
        return
    caption = next((m.caption for m in messages if m.caption), None)
    file_ids = [m.photo[-1].file_id for m in messages if m.photo]
    log.info(
        "Фото от %s, штук: %s, подпись: %s",
        _who(user),
        len(file_ids),
        caption or "—",
    )

    wait = await first.answer("Собираю черновик сторис…")
    photos: list[bytes] = []
    try:
        photos = [await _download_photo(bot, fid) for fid in file_ids]
        text = await generate_post(photos, caption)
    except Exception as exc:
        log.exception("Не смог сделать черновик")
        await first.answer(f"Не получилось собрать пост: {exc}")
        return
    finally:
        try:
            await wait.delete()
        except TelegramBadRequest:
            pass

    draft_id = uuid.uuid4().hex
    draft = Draft(
        id=draft_id,
        employee_id=user.id if user else 0,
        employee_label=_who(user),
        file_ids=file_ids,
        photos=photos,
        text=text,
    )
    drafts[draft_id] = draft
    log.info("Черновик %s готов, текст: %s", draft_id, text)

    await first.answer("Принято, отправил на согласование")

    if not settings.admin_chat_ids:
        await first.answer("Список админов пустой — черновик никуда не ушёл. Пропиши ADMIN_CHAT_IDS в .env")
        return

    body = _draft_body(draft)
    kb = _draft_kb(draft_id)
    for admin_id in settings.admin_chat_ids:
        try:
            await _send_photos(bot, admin_id, file_ids)
            sent = await bot.send_message(admin_id, body, reply_markup=kb)
            draft.admin_copies.append(AdminCopy(chat_id=admin_id, message_id=sent.message_id))
        except Exception:
            log.exception("Не смог отправить черновик админу %s", admin_id)


@router.callback_query(F.data.startswith("pub:"))
async def on_publish(callback: CallbackQuery, bot: Bot) -> None:
    await _decide(callback, bot, "publish")


@router.callback_query(F.data.startswith("rej:"))
async def on_reject(callback: CallbackQuery, bot: Bot) -> None:
    await _decide(callback, bot, "reject")


@router.callback_query(F.data.startswith("edit:"))
async def on_edit(callback: CallbackQuery, state: FSMContext) -> None:
    user = callback.from_user
    if not is_admin(user.id if user else None):
        await callback.answer("Публиковать и править может только админ.", show_alert=True)
        return

    draft_id = (callback.data or "").split(":", 1)[-1]
    draft = drafts.get(draft_id)
    if draft is None:
        await callback.answer("Черновик уже не найден.", show_alert=True)
        return
    if draft.processed:
        await callback.answer("Уже обработано.", show_alert=True)
        return

    await state.set_state(EditDraft.waiting_text)
    await state.update_data(draft_id=draft_id)
    await callback.answer()
    if callback.message:
        await callback.message.answer("Пришли новый текст сторис одним сообщением.")


async def _decide(callback: CallbackQuery, bot: Bot, action: str) -> None:
    user = callback.from_user
    if not is_admin(user.id if user else None):
        await callback.answer("Это только для админа.", show_alert=True)
        return

    draft_id = (callback.data or "").split(":", 1)[-1]
    draft = drafts.get(draft_id)
    if draft is None:
        await callback.answer("Черновик уже не найден.", show_alert=True)
        return

    async with draft.lock:
        if draft.processed:
            await callback.answer("Уже обработано.", show_alert=True)
            return
        draft.processed = True

        who = _who(user)
        if action == "reject":
            log.info("Черновик %s отклонил %s", draft.id, who)
            footer = f"❌ Отклонил {who}"
            await _edit_admin_copies(bot, draft, footer)
            await callback.answer("Отклонено")
            try:
                await bot.send_message(draft.employee_id, "Черновик сторис отклонили.")
            except Exception:
                log.exception("Не смог написать сотруднику %s", draft.employee_id)
            return

        log.info("Черновик %s — сторис для %s", draft.id, who)
        try:
            await _send_story(bot, draft, user.id)
        except Exception as exc:
            draft.processed = False
            log.exception("Сторис не собралась")
            await callback.answer("Не смог собрать сторис", show_alert=True)
            if callback.message:
                await callback.message.answer(f"Ошибка: {exc}")
            return

        footer = f"✅ Сторис собрал {who} — картинка у него в чате с ботом"
        await _edit_admin_copies(bot, draft, footer)
        await callback.answer("Готово, смотри чат")
        try:
            await bot.send_message(draft.employee_id, "Сторис собрали, у владельца в боте.")
        except Exception:
            log.exception("Не смог написать сотруднику")


async def _send_story(bot: Bot, draft: Draft, admin_id: int) -> None:
    """Кидает готовую сторис админу в личку — он сам выкладывает со своего аккаунта."""
    if not draft.photos:
        raise RuntimeError("Нет фото в черновике")
    image = render_story(draft.photos[0], draft.text)
    file = BufferedInputFile(image, filename="pandora34_story.jpg")
    hint = (
        "Готовая сторис 9:16.\n"
        "Телега: открой фото → поделиться → историю.\n"
        "Инста: сохрани файл и выложи сторис со своего аккаунта."
    )
    await bot.send_photo(admin_id, file, caption=hint)
    log.info("Сторис ушла админу %s, draft=%s", admin_id, draft.id)


@router.message(EditDraft.waiting_text, F.text)
async def on_new_text(message: Message, state: FSMContext, bot: Bot) -> None:
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    data = await state.get_data()
    await state.clear()
    draft = drafts.get(data.get("draft_id", ""))
    if draft is None or draft.processed:
        await message.answer("Этот черновик уже закрыт.")
        return

    new_text = (message.text or "").strip()
    if not new_text:
        await message.answer("Пустой текст не беру. Пришли ещё раз или нажми кнопки на черновике.")
        return

    draft.text = new_text
    log.info("Админ %s переписал черновик %s", _who(message.from_user), draft.id)
    await _refresh_admin_copies(bot, draft)
    await message.answer("Текст обновил, кнопки снова на черновике.")


@router.message(EditDraft.waiting_text)
async def on_new_text_wrong(message: Message) -> None:
    await message.answer("Нужен именно текст сторис, без фото.")


@router.message(F.text)
async def on_plain_text(message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    if _can_submit(uid):
        await message.answer("Пришли фото с работы — подпись к фото можно сразу.")
