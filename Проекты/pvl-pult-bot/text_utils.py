from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.types import Message

# Все варианты длинного тире и типографских дефисов приводим к обычному "-".
_DASH_TRANSLATION = str.maketrans(
    {
        "\u2012": "-",  # figure dash
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2015": "-",  # horizontal bar
        "\u2212": "-",  # minus sign
        "\u2e3a": "-",  # two-em dash
        "\u2e3b": "-",  # three-em dash
        "\ufe58": "-",  # small em dash
        "\ufe63": "-",  # small hyphen-minus
        "\uff0d": "-",  # fullwidth hyphen-minus
    }
)

TELEGRAM_CHUNK_SIZE = 3900


def normalize_dashes(text: str) -> str:
    """Принудительно заменяет длинные тире на обычный дефис."""
    return str(text).translate(_DASH_TRANSLATION)


def split_text(text: str, limit: int = TELEGRAM_CHUNK_SIZE) -> list[str]:
    """Делит длинный текст на части, не разрывая строки без необходимости."""
    text = text.strip()
    if not text:
        return ["-"]
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


async def safe_answer(message: Message, text: str, **kwargs: Any) -> None:
    """Единственная точка ответа в Telegram: здесь всегда чистим тире."""
    for chunk in split_text(normalize_dashes(str(text))):
        await message.answer(chunk, **kwargs)


async def safe_send(bot: Bot, chat_id: int, text: str, **kwargs: Any) -> None:
    """Безопасная отправка по chat_id для планировщика и фоновых задач."""
    for chunk in split_text(normalize_dashes(str(text))):
        await bot.send_message(chat_id=chat_id, text=chunk, **kwargs)
