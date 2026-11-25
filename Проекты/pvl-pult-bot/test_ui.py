"""Проверка UX-кнопок и рендера карточек Пульта без сети и без записи в таблицу.

Запуск: python test_ui.py
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from keyboards import (
    BUTTON_VIEWS,
    deal_card_kb,
    lead_actions_kb,
    lead_card_kb,
    pult_kb,
)
from render import (
    format_deal_card,
    format_header,
    format_lead_card,
    format_lead_expanded,
    normalize,
    summary_text,
)
from sheets import Deal, Lead

TODAY = date(2026, 11, 25)
FUTURE = date(2026, 11, 27)
NOW = datetime(2026, 11, 25, 9, 30, tzinfo=ZoneInfo("Europe/Minsk"))

FORBIDDEN = ("<details", "\u2014", "\u2013", "\u2015", "<a href=\"tel:")


def make_lead(
    lead_id: int = 4,
    *,
    company: str = "Хоттабыч сервис",
    status: str = "Горячий (демо готово)",
    next_date: date | None = FUTURE,
    next_time: time | None = time(11, 0),
    next_step: str = "Отправить демо и смету",
    notes: str = "Степянка, просил каталог",
) -> Lead:
    return Lead(
        row=lead_id + 1,
        id=lead_id,
        company=company,
        city="Минск",
        status=status,
        phone="+375 29 718-70-24",
        contact="Viber",
        service="Сайт-каталог СТО",
        budget="350-650",
        next_date=next_date,
        next_time=next_time,
        next_step=next_step,
        link="https://example.com/demo",
        notes=notes,
    )


def make_deal(
    deal_id: int = 3,
    *,
    task: str = "Перевести Пульт-бота на Google Sheets",
    deadline: date | None = FUTURE,
    status: str = "В очереди",
) -> Deal:
    return Deal(
        row=deal_id + 1,
        id=deal_id,
        task=task,
        priority="Средний",
        deadline=deadline,
        place="ПК (DeepSeek Low)",
        status=status,
        notes="Заменить git pull и ПУЛЬТ.md",
    )


def button_texts(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


def check_card(title: str, text: str, max_lines: int = 4) -> None:
    print(f"\n--- {title} ---")
    print(text)
    lines = text.splitlines()
    assert len(lines) <= max_lines, f"{title}: строк {len(lines)}, ожидалось <= {max_lines}"
    assert len(normalize(text).splitlines()) == len(lines), f"{title}: ломаются строки"
    for token in FORBIDDEN:
        assert token not in text, f"{title}: запрещённый фрагмент {token!r}"
    assert text.count("<b>") == text.count("</b>"), f"{title}: несбалансированный <b>"
    assert "<script" not in text.lower()


def test_reply_keyboard() -> None:
    kb = pult_kb()
    rows = [[button.text for button in row] for row in kb.keyboard]
    assert rows == [
        ["Сегодня", "Горячие", "Просрочено"],
        ["Неделя", "Дела", "Сводка"],
    ], rows
    assert kb.resize_keyboard is True
    assert kb.is_persistent is True
    assert set(BUTTON_VIEWS) == {cell for row in rows for cell in row}
    assert len(BUTTON_VIEWS) == 6
    print("ReplyKeyboard 2x3: OK", rows)


def test_lead_today_keyboard() -> None:
    lead = make_lead(next_date=TODAY, status="Тёплый")
    texts = button_texts(lead_card_kb(lead, TODAY))
    assert texts[0] == ["Сделано", "+1 день"], texts
    assert texts[1] == ["Бриф"], "у лида с заметками должна быть кнопка Бриф"
    print("Лид на сегодня:", texts)

    no_brief = make_lead(next_date=TODAY, notes="")
    assert button_texts(lead_card_kb(no_brief, TODAY)) == [["Сделано", "+1 день"]]
    print("Лид без брифа:", button_texts(lead_card_kb(no_brief, TODAY)))

    no_date = make_lead(next_date=None, next_time=None, notes="")
    assert button_texts(lead_card_kb(no_date, TODAY)) == [["Сделано", "+1 день"]]
    print("Лид без даты:", button_texts(lead_card_kb(no_date, TODAY)))


def test_lead_future_keyboard() -> None:
    lead = make_lead(next_date=FUTURE)
    texts = button_texts(lead_card_kb(lead, TODAY))
    assert texts == [["Действия / Бриф"]], texts
    data = lead_card_kb(lead, TODAY).inline_keyboard[0][0].callback_data
    assert data == f"a:l:{lead.id}:brief", data
    print("Лид на 27.11:", texts, data)

    actions = button_texts(lead_actions_kb(lead.id))
    assert actions == [
        ["На сегодня", "+1 день"],
        ["Закрыть досрочно", "Свернуть"],
    ], actions
    print("Раскрытые действия:", actions)

    expanded = format_lead_expanded(lead)
    assert "Бриф / Заметки" in expanded
    assert "Степянка" in expanded
    assert len(expanded.splitlines()) > 4
    for token in FORBIDDEN:
        assert token not in expanded, token
    print("\n--- Раскрытая карточка ---")
    print(expanded)


def test_deal_keyboards() -> None:
    future = make_deal(deadline=FUTURE)
    assert button_texts(deal_card_kb(future, TODAY)) == [["Сделано"]]
    print("Будущее дело:", button_texts(deal_card_kb(future, TODAY)))

    today_deal = make_deal(deadline=TODAY)
    assert button_texts(deal_card_kb(today_deal, TODAY)) == [["Сделано", "+1 день"]]
    print("Дело на сегодня:", button_texts(deal_card_kb(today_deal, TODAY)))

    overdue = make_deal(deadline=date(2026, 11, 20))
    assert button_texts(deal_card_kb(overdue, TODAY)) == [["Сделано", "+1 день"]]
    print("Просроченное дело:", button_texts(deal_card_kb(overdue, TODAY)))

    no_deadline = make_deal(deadline=None)
    assert button_texts(deal_card_kb(no_deadline, TODAY)) == [["Сделано", "+1 день"]]
    print("Дело без дедлайна:", button_texts(deal_card_kb(no_deadline, TODAY)))


def test_headers_and_cards() -> None:
    assert format_header("hot", TODAY, leads_count=4) == "<b>Горячие (4)</b>\nЛидов: 4"
    assert format_header(
        "today", TODAY, leads_count=2, deals_count=1
    ) == "<b>Сегодня (3)</b>\nЗвонков: 2 · Дел: 1"
    assert format_header("deals", TODAY, deals_count=0) == (
        "<b>Дела (0)</b>\nНа этот срез ничего нет."
    )
    print("Шапки срезов: OK")

    summary = summary_text(
        NOW,
        calls_count=2,
        deals_count=1,
        hot_count=4,
        overdue_count=3,
        leads_total=13,
        active_deals=3,
    )
    print("\n--- Сводка дня ---")
    print(summary)
    assert summary.splitlines()[0] == "<b>Пульт SAMOLA CRM</b>"
    assert len(summary.splitlines()) == 5
    assert "<b>" in summary

    lead_today = make_lead(next_date=TODAY, status="Тёплый")
    check_card("Карточка лида на сегодня", format_lead_card(lead_today))
    check_card("Карточка лида на 27.11", format_lead_card(make_lead()))
    check_card("Карточка будущего дела", format_deal_card(make_deal()))


def main() -> None:
    test_reply_keyboard()
    test_lead_today_keyboard()
    test_lead_future_keyboard()
    test_deal_keyboards()
    test_headers_and_cards()
    print("\nOK: UX-кнопки и рендер проверены.")


if __name__ == "__main__":
    main()
