"""Клавиатуры Пульта: постоянный ReplyKeyboard и умные inline-кнопки карточек."""

from __future__ import annotations

from datetime import date

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from sheets import Deal, Lead

VIEWS = ("today", "hot", "overdue", "week", "deals")

# Тексты постоянной клавиатуры. Обработчики точных текстов регистрируются
# ДО обработчика свободного текста, иначе ИИ-парсер перехватит навигацию.
BUTTON_VIEWS: dict[str, str] = {
    "Сегодня": "today",
    "Горячие": "hot",
    "Просрочено": "overdue",
    "Неделя": "week",
    "Дела": "deals",
    "Сводка": "summary",
}

SUMMARY_BUTTON = "Сводка"


def pult_kb() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура 2x3 у поля ввода."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Сегодня"),
                KeyboardButton(text="Горячие"),
                KeyboardButton(text="Просрочено"),
            ],
            [
                KeyboardButton(text="Неделя"),
                KeyboardButton(text="Дела"),
                KeyboardButton(text="Сводка"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Пульт: срез кнопкой или обновление текстом/голосом",
    )


def has_brief(lead: Lead) -> bool:
    value = (lead.notes or "").strip()
    return bool(value) and value not in {"-", "нет", "нет."}


def lead_card_kb(lead: Lead, today: date) -> InlineKeyboardMarkup:
    """Кнопки лида: прошедшая/сегодняшняя дата - рабочие, будущая - только Бриф."""
    is_future = lead.next_date is not None and lead.next_date > today
    if is_future:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Действия / Бриф",
                        callback_data=f"a:l:{lead.id}:brief",
                    )
                ]
            ]
        )

    row: list[InlineKeyboardButton] = [
        InlineKeyboardButton(text="Сделано", callback_data=f"a:l:{lead.id}:done"),
        InlineKeyboardButton(text="+1 день", callback_data=f"a:l:{lead.id}:plus"),
    ]
    keyboard = [row]
    if has_brief(lead):
        keyboard.append(
            [
                InlineKeyboardButton(
                    text="Бриф", callback_data=f"a:l:{lead.id}:brief"
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def lead_actions_kb(lead_id: int) -> InlineKeyboardMarkup:
    """Раскрытая карточка будущего лида: двигаем дату или закрываем досрочно."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="На сегодня", callback_data=f"a:l:{lead_id}:today"
                ),
                InlineKeyboardButton(
                    text="+1 день", callback_data=f"a:l:{lead_id}:plus"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Закрыть досрочно", callback_data=f"a:l:{lead_id}:close"
                ),
                InlineKeyboardButton(
                    text="Свернуть", callback_data=f"a:l:{lead_id}:collapse"
                ),
            ],
        ]
    )


def deal_card_kb(deal: Deal, today: date) -> InlineKeyboardMarkup:
    """Дела: просроченные/сегодня - Сделано и +1 день, будущие - только Сделано."""
    keyboard = [
        [
            InlineKeyboardButton(text="Сделано", callback_data=f"a:d:{deal.id}:done")
        ]
    ]
    if deal.deadline is None or deal.deadline <= today:
        keyboard[0].append(
            InlineKeyboardButton(
                text="+1 день", callback_data=f"a:d:{deal.id}:plus"
            )
        )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def confirm_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить", callback_data=f"ai:ok:{token}"
                ),
                InlineKeyboardButton(
                    text="Отмена", callback_data=f"ai:no:{token}"
                ),
            ]
        ]
    )
