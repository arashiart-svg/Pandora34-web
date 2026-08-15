"""Проверка, кто сотрудник и кто админ."""

from config import settings


def is_employee(chat_id: int | None) -> bool:
    if chat_id is None:
        return False
    return chat_id in settings.employee_chat_ids


def is_admin(chat_id: int | None) -> bool:
    if chat_id is None:
        return False
    return chat_id in settings.admin_chat_ids


def has_access(chat_id: int | None) -> bool:
    """Сотрудник или админ — бот с ними работает. Остальных отшиваем."""
    return is_employee(chat_id) or is_admin(chat_id)
