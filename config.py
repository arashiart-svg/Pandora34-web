"""Загрузка настроек из .env."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"В .env не задано обязательное поле {name}")
    return value


def _parse_ids(raw: str) -> frozenset[int]:
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        ids.append(int(part))
    return frozenset(ids)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    employee_chat_ids: frozenset[int]
    admin_chat_ids: frozenset[int]
    channel_id: str
    anthropic_api_key: str


def load_settings() -> Settings:
    return Settings(
        bot_token=_require("BOT_TOKEN"),
        employee_chat_ids=_parse_ids(os.getenv("EMPLOYEE_CHAT_IDS") or ""),
        admin_chat_ids=_parse_ids(os.getenv("ADMIN_CHAT_IDS") or ""),
        channel_id=(os.getenv("CHANNEL_ID") or "").strip(),
        anthropic_api_key=(os.getenv("ANTHROPIC_API_KEY") or "").strip(),
    )


settings = load_settings()
