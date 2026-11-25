"""Проверка: чтение всех 3 вкладок SAMOLA CRM и рендер HTML-карточек.

Запуск: python test_sheets.py
В Telegram ничего не отправляется.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from config import load_settings
from render import (
    active_deals,
    esc,
    format_deal_card,
    format_header,
    format_lead_card,
    hot_items,
    is_deal_done,
    morning_text,
    normalize,
    overdue_items,
    reminder_text,
    today_items,
    week_items,
)
from sheets import CrmSheets, Deal, Lead, parse_date, parse_time

FORBIDDEN = ("<details", "|", "\u2014", "\u2013", "\u2015")


def check_card(title: str, text: str, max_lines: int = 4) -> None:
    print(f"\n--- {title} ---")
    print(text)
    lines = text.splitlines()
    assert len(lines) <= max_lines, f"{title}: строк {len(lines)}, ожидалось <= {max_lines}"
    assert len(normalize(text).splitlines()) == len(lines), f"{title}: send_html ломает строки"
    for token in FORBIDDEN:
        assert token not in text, f"{title}: запрещённый фрагмент {token!r}"
    assert text.count("<b>") == text.count("</b>"), f"{title}: несбалансированный <b>"
    assert text.count("<a ") == text.count("</a>"), f"{title}: несбалансированный <a>"
    assert "<script" not in text.lower()


def main() -> None:
    settings = load_settings()
    crm = CrmSheets(settings.google_creds_path, settings.spreadsheet_id)
    tz = ZoneInfo(settings.timezone)
    today = datetime.now(tz).date()

    print(f"Таблица: {crm.ping()}")
    print(f"SPREADSHEET_ID: {settings.spreadsheet_id}")
    print(f"Сегодня: {today:%d.%m.%Y} ({settings.timezone})")

    leads = crm.read_leads()
    deals = crm.read_deals()
    history = crm.read_history()
    print(f"\nЛиды: {len(leads)} | Дела: {len(deals)} | История: {len(history)}")

    print("\nПервые 3 лида:")
    for lead in leads[:3]:
        print(
            f"  row {lead.row}: id={lead.id} | {lead.company} ({lead.city}) | "
            f"{lead.status} | {lead.next_date} {lead.next_time} | {lead.phone}"
        )

    print("\nДела:")
    for deal in deals:
        print(
            f"  row {deal.row}: id={deal.id} | {deal.task[:60]} | "
            f"{deal.deadline} | {deal.status}"
        )

    print("\nПоследние 3 записи Истории:")
    for entry in history[-3:]:
        print(f"  row {entry.row}: {entry.date} | {entry.kind} | {entry.client[:50]}")

    # Срезы главного меню.
    calls_today, deals_today = today_items(leads, deals, today)
    hot = hot_items(leads)
    late_calls, late_deals = overdue_items(leads, deals, today)
    week_calls, week_deals = week_items(leads, deals, today)
    active = active_deals(deals)

    print("\n=== Срезы ===")
    print(f"Сегодня: звонков {len(calls_today)}, дел {len(deals_today)}")
    print(f"Горячие: {len(hot)}")
    print(f"Просрочено: лидов {len(late_calls)}, дел {len(late_deals)}")
    print(f"Неделя: лидов {len(week_calls)}, дел {len(week_deals)}")
    print(f"Дела в работе: {len(active)}")

    print("\n=== Примеры HTML для Telegram ===")
    print(f"\n[Главное меню]\n<b>Пульт SAMOLA CRM</b>")
    print(
        format_header(
            "today", today, leads_count=len(calls_today), deals_count=len(deals_today)
        )
    )

    if leads:
        check_card("Карточка лида", format_lead_card(leads[0]))
    if deals:
        check_card("Карточка дела", format_deal_card(deals[0]))
    if hot:
        check_card("Горячий лид", format_lead_card(hot[0]))
    if late_calls:
        check_card("Просроченный лид", format_lead_card(late_calls[0]))
    if calls_today:
        check_card("Напоминание о звонке", reminder_text(calls_today[0]), max_lines=5)

    print("\n--- Утренняя сводка ---")
    print(
        morning_text(
            today,
            leads_count=len(calls_today),
            deals_count=len(deals_today),
            overdue_count=len(late_calls) + len(late_deals),
        )
    )

    # Экранирование и фильтрация запрещённых символов.
    sample = Lead(
        row=99,
        id=99,
        company="Тест <b>&",
        city="Минск",
        status="Горячий — тест",
        phone="+375 29 111-22-33",
        contact="Telegram",
        service="Сайт",
        budget="300",
        next_date=today,
        next_time=time(12, 30),
        next_step="Проверить <script> и | палки",
        link="https://example.com/?a=1&b=2",
        notes="x",
    )
    check_card("Экранирование", format_lead_card(sample))
    assert "&lt;b&gt;" in format_lead_card(sample)
    assert "&amp;" in format_lead_card(sample)
    assert "<script>" not in format_lead_card(sample)

    # Парсеры дат и времени.
    assert parse_date("2026-11-26") == date(2026, 11, 26)
    assert parse_date("26.11.2026") == date(2026, 11, 26)
    assert parse_date("") is None
    assert parse_time("8:30") == time(8, 30)
    assert parse_time("11:00") == time(11, 0)

    # Вкладка «Лиды»: строки сводки не должны попадать в лиды.
    assert all(isinstance(lead.id, int) and lead.id > 0 for lead in leads)
    assert all(lead.company for lead in leads), "у лида пустая компания"
    assert all(deal.id > 0 for deal in deals)
    assert all(not is_deal_done(deal.status) for deal in active)

    # Никаких длинных тире в готовом тексте.
    for text in [
        *(format_lead_card(lead) for lead in leads[:5]),
        *(format_deal_card(deal) for deal in deals[:5]),
    ]:
        for dash in ("\u2014", "\u2013", "\u2015"):
            assert dash not in text

    _ = normalize("тест — тире")
    _ = esc("<x>")
    print("\nOK: все проверки пройдены.")


if __name__ == "__main__":
    main()
