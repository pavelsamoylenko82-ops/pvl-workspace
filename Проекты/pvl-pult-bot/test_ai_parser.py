"""Юнит-тест ИИ-парсера CRM с моком DeepSeek. В таблицу ничего не пишет.

Запуск: python test_ai_parser.py
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import ai_crm
from ai_crm import apply_action, format_preview, parse_iso_date, parse_response, parse_text
from sheets import CrmData, Deal, Lead

TZ = ZoneInfo("Europe/Minsk")
NOW = datetime(2026, 11, 25, 9, 30, tzinfo=TZ)
TODAY = NOW.date()

FORBIDDEN = ("\u2014", "\u2013", "\u2015", "<a href=\"tel:")


def make_lead(lead_id: int, company: str, next_date: date | None) -> Lead:
    return Lead(
        row=lead_id + 1,
        id=lead_id,
        company=company,
        city="Минск",
        status="Горячий (демо готово)",
        phone="+375 29 718-70-24",
        contact="Viber",
        service="Сайт-каталог СТО",
        budget="350-650",
        next_date=next_date,
        next_time=time(11, 0),
        next_step="Отправить демо",
        link="-",
        notes="Степянка",
    )


def make_deal(deal_id: int, task: str) -> Deal:
    return Deal(
        row=deal_id + 1,
        id=deal_id,
        task=task,
        priority="Средний",
        deadline=date(2026, 11, 26),
        place="ПК",
        status="В работе",
        notes="",
    )


def sample_data() -> CrmData:
    return CrmData(
        leads=[
            make_lead(1, "СтеклоПак Торг", date(2026, 11, 26)),
            make_lead(4, "Хоттабыч сервис", date(2026, 11, 27)),
            make_lead(5, "СТО Клюмин", date(2026, 11, 27)),
        ],
        deals=[
            make_deal(3, "Перевести Пульт-бота на Google Sheets"),
            make_deal(4, "Собрать демо для 7 Дней"),
        ],
    )


class FakeCrm:
    """Заглушка вместо Google Sheets: пишет только в память теста."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def update_lead(self, lead: Lead, **kwargs):
        self.calls.append(("update_lead", lead.id, kwargs))
        changes = [f"статус: {kwargs.get('status', '')}".strip()]
        return lead, changes

    def add_task(self, **kwargs):
        self.calls.append(("add_task", kwargs))
        deal = Deal(
            row=6,
            id=5,
            task=kwargs["task"],
            priority=kwargs.get("priority", ""),
            deadline=kwargs.get("deadline"),
            place=kwargs.get("place", ""),
            status="В работе",
            notes=kwargs.get("note", ""),
        )
        return deal, ["добавлено дело ID 5"]

    def update_deal(self, deal: Deal, **kwargs):
        self.calls.append(("update_deal", deal.id, kwargs))
        return deal, [f"статус: {kwargs.get('status', '')}"]


def check_html(title: str, text: str) -> None:
    assert text.count("<b>") == text.count("</b>"), f"{title}: несбалансированный <b>"
    for token in FORBIDDEN:
        assert token not in text, f"{title}: запрещённый фрагмент {token!r}"
    assert "<script" not in text.lower()


async def test_update_lead_phrase() -> None:
    phrase = "Хоттабыч, статус переговоры, перезвонить 28.11 в 11:00, после обеда"
    action = await parse_text(
        text=phrase,
        data=sample_data(),
        now=NOW,
        api_key="test-key",
        model="test-model",
        base_url="https://example.invalid/v1",
    )
    assert action.kind == "update_lead", action
    assert action.lead is not None and action.lead.id == 4
    assert action.lead.status.startswith("Переговоры")
    assert action.lead.next_date == date(2026, 11, 28)
    assert action.lead.next_time == time(11, 0)
    assert action.lead.note_append.startswith("перезвонить")

    preview = format_preview(action, sample_data().leads, sample_data().deals)
    check_html("Превью лида", preview)
    print("\n--- Превью: обновление лида ---")
    print(preview)
    assert "Лид 4" in preview and "28.11" in preview

    crm = FakeCrm()
    result = apply_action(crm, action, leads=sample_data().leads, deals=sample_data().deals, today=TODAY)
    check_html("Итог записи лида", result)
    print("\n--- Итог: обновление лида ---")
    print(result)
    assert crm.calls and crm.calls[0][0] == "update_lead"
    _, lead_id, kwargs = crm.calls[0]
    assert lead_id == 4
    assert kwargs["next_date"] == date(2026, 11, 28)
    assert kwargs["next_time"] == time(11, 0)
    assert kwargs["status"].startswith("Переговоры")
    print("Записано (dry-run):", crm.calls[0])


async def test_add_task_phrase() -> None:
    phrase = "Добавь дело: собрать демо для 7 Дней до 29.11, приоритет высокий"
    action = await parse_text(
        text=phrase,
        data=sample_data(),
        now=NOW,
        api_key="test-key",
        model="test-model",
        base_url="https://example.invalid/v1",
    )
    assert action.kind == "add_task", action
    task = action.new_task
    assert task is not None
    assert task.task == "Собрать демо для 7 Дней"
    assert task.priority == "Высокий"
    assert task.deadline == date(2026, 11, 29)

    preview = format_preview(action, sample_data().leads, sample_data().deals)
    check_html("Превью дела", preview)
    print("\n--- Превью: новое дело ---")
    print(preview)
    assert "Новое дело" in preview and "29.11" in preview

    crm = FakeCrm()
    result = apply_action(crm, action, leads=sample_data().leads, deals=sample_data().deals, today=TODAY)
    check_html("Итог записи дела", result)
    print("\n--- Итог: новое дело ---")
    print(result)
    kind, kwargs = crm.calls[0]
    assert kind == "add_task"
    assert kwargs["task"] == "Собрать демо для 7 Дней"
    assert kwargs["deadline"] == date(2026, 11, 29)
    assert kwargs["priority"] == "Высокий"
    print("Записано (dry-run):", crm.calls[0])


def test_update_task_and_unknown() -> None:
    data = sample_data()
    payload = {
        "action": "update_task",
        "update_task": {"id": 3, "status": "Выполнено", "note_append": "сделано"},
    }
    action = parse_response(payload, leads=data.leads, deals=data.deals, today=TODAY)
    assert action.kind == "update_task" and action.task is not None
    assert action.task.id == 3 and action.task.status == "Выполнено"

    preview = format_preview(action, data.leads, data.deals)
    check_html("Превью дела-обновления", preview)
    print("\n--- Превью: обновление дела ---")
    print(preview)

    crm = FakeCrm()
    result = apply_action(crm, action, leads=data.leads, deals=data.deals, today=TODAY)
    check_html("Итог обновления дела", result)
    assert crm.calls[0][0] == "update_deal"

    unknown = parse_response(
        {"action": "unknown", "unknown": {"reason": "не понял"}},
        leads=data.leads,
        deals=data.deals,
        today=TODAY,
    )
    assert unknown.kind == "unknown" and unknown.reason == "не понял"
    print("Unknown:", unknown.reason)

    missing = parse_response(
        {"action": "update_lead", "update_lead": {"id": 999, "status": "Тёплый"}},
        leads=data.leads,
        deals=data.deals,
        today=TODAY,
    )
    assert missing.kind == "unknown" and "999" in missing.reason
    print("Несуществующий ID:", missing.reason)

    empty = parse_response(
        {"action": "update_lead", "update_lead": {"id": 4}},
        leads=data.leads,
        deals=data.deals,
        today=TODAY,
    )
    assert empty.kind == "unknown"
    print("Пустое обновление:", empty.reason)


def test_date_parsing() -> None:
    assert parse_iso_date("2026-11-28", TODAY) == date(2026, 11, 28)
    assert parse_iso_date("27.11", TODAY) == date(2026, 11, 27)
    assert parse_iso_date("28.11.2026", TODAY) == date(2026, 11, 28)
    assert parse_iso_date("завтра", TODAY) == date(2026, 11, 26)
    assert parse_iso_date("сегодня", TODAY) == TODAY
    assert parse_iso_date(None, TODAY) is None
    print("Разбор дат: OK")


async def _run() -> None:
    # Мок DeepSeek: подменяем транспорт, никаких сетевых вызовов.
    original = ai_crm.request_json

    async def fake_request_json(*, messages, **kwargs):
        user_text = messages[-1]["content"].split("Фраза пользователя:")[-1]
        if "добавь дело" in user_text.lower():
            return {
                "action": "add_task",
                "add_task": {
                    "task": "Собрать демо для 7 Дней",
                    "priority": "Высокий",
                    "deadline": "2026-11-29",
                    "where": "ПК",
                    "note": "до 12:00",
                },
            }
        return {
            "action": "update_lead",
            "update_lead": {
                "id": 4,
                "status": "Переговоры (демо готово)",
                "next_date": "2026-11-28",
                "next_time": "11:00",
                "note_append": "перезвонить после обеда",
            },
        }

    ai_crm.request_json = fake_request_json
    try:
        await test_update_lead_phrase()
        await test_add_task_phrase()
    finally:
        ai_crm.request_json = original


async def main() -> None:
    test_date_parsing()
    await _run()
    test_update_task_and_unknown()
    print("\nOK: ИИ-парсер проверен на моке DeepSeek, без записи в таблицу.")


if __name__ == "__main__":
    asyncio.run(main())
