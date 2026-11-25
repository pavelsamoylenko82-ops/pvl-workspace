"""ИИ-обновление SAMOLA CRM: разбор текста/голоса через DeepSeek и превью записи."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta

from deepseek_client import request_json
from render import esc, normalize, truncate
from sheets import CrmData, CrmSheets, Deal, Lead, SheetError, parse_date, parse_time

logger = logging.getLogger("pvl-pult-bot.ai")

MAX_LEADS_IN_CONTEXT = 40
MAX_DEALS_IN_CONTEXT = 30

SYSTEM_PROMPT = """Ты оператор личной CRM отдела продаж. Пользователь диктует или пишет
короткое обновление по лиду или делу. Твоя задача: вернуть СТРОГО один JSON-объект
без markdown, без комментариев, без пояснений.

Формат ответа (выбери ровно один action):
{
  "action": "update_lead",
  "update_lead": {"id": 4, "status": "Переговоры (демо готово)", "next_date": "2026-11-28", "next_time": "11:00", "next_step": "Отправить КП", "note_append": "просил перезвонить после обеда"}
}
{
  "action": "add_task",
  "add_task": {"task": "Собрать демо для 7 Дней", "priority": "Высокий", "deadline": "2026-11-28", "where": "ПК", "note": "до 12:00"}
}
{
  "action": "update_task",
  "update_task": {"id": 3, "status": "Выполнено", "deadline": "2026-11-28", "note_append": "сделано"}
}
{
  "action": "unknown",
  "unknown": {"reason": "не понял, к какому лиду или делу относится"}
}

Правила:
1. id бери ТОЛЬКО из списка лидов/дел ниже. Не выдумывай id.
2. Если фраза про лида (клиента, компанию, звонок, демо, КП, перезвон) - action="update_lead".
3. Если фраза про задачу/дело/поручение/собрать/подготовить/отправить -
   action="update_task", когда дело уже есть в списке. Если пользователь прямо говорит
   "добавь дело", "новое дело", "заведи задачу", "напомни сделать" или похожего дела
   в списке нет - action="add_task".
4. Даты строго в формате YYYY-MM-DD. "сегодня" = текущая дата из блока ниже, "завтра" = +1 день,
   "через неделю" = +7 дней, "в понедельник" = ближайший понедельник после сегодня.
5. Время строго HH:MM (24 часа). Если время не названо - null.
6. Меняй только те поля, которые явно следуют из фразы. Остальные поля не включай вовсе.
7. note_append - короткая новая заметка без даты и без повторов того, что уже есть в карточке.
8. status для лида - короткий статус воронки, по возможности из списка статусов ниже.
   status для дела - "Выполнено" (закрыть), "В работе", "В очереди".
9. priority - "Высокий", "Средний" или "Низкий". По умолчанию "Средний".
10. Никаких длинных тире, только обычный дефис.
11. Если непонятно, о каком лиде/деле речь или что именно изменить - верни action="unknown"
    и причину по-русски коротко. Не угадывай.
12. Не выдумывай факты, которых нет во фразе."""


@dataclass
class LeadUpdate:
    id: int
    status: str = ""
    next_date: date | None = None
    has_next_date: bool = False
    next_time: dtime | None = None
    has_next_time: bool = False
    next_step: str = ""
    note_append: str = ""


@dataclass
class TaskAdd:
    task: str
    priority: str = "Средний"
    deadline: date | None = None
    where: str = ""
    note: str = ""


@dataclass
class TaskUpdate:
    id: int
    status: str = ""
    deadline: date | None = None
    has_deadline: bool = False
    note_append: str = ""


@dataclass
class CrmAction:
    kind: str
    source_text: str = ""
    lead: LeadUpdate | None = None
    new_task: TaskAdd | None = None
    task: TaskUpdate | None = None
    reason: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def is_known(self) -> bool:
        return self.kind in {"update_lead", "add_task", "update_task"}


def _clean(value: object) -> str:
    return " ".join(normalize(value).split())


def parse_iso_date(value: object, today: date) -> date | None:
    """Понимает ISO, dd.mm(.yyyy), dd/mm/yyyy и слова сегодня/завтра/послезавтра."""
    text = _clean(value).lower()
    if not text or text in {"null", "none", "-", "нет"}:
        return None
    relatives = {
        "сегодня": 0,
        "завтра": 1,
        "послезавтра": 2,
        "вчера": -1,
    }
    if text in relatives:
        return today + timedelta(days=relatives[text])
    match = re.fullmatch(r"(\d{1,2})[./](\d{1,2})", text)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        try:
            return date(today.year, month, day)
        except ValueError:
            return None

    parsed = parse_date(text)
    # "27.11" без года parse_date принимает за число-серийник; такие даты не берём.
    if parsed and parsed.year >= 2000:
        return parsed
    return None


def _parse_priority(value: object) -> str:
    text = _clean(value).lower()
    if any(marker in text for marker in ("высок", "срочн", "важн")):
        return "Высокий"
    if any(marker in text for marker in ("низк", "потом", "не срочн")):
        return "Низкий"
    return "Средний"


def _find_lead(leads: list[Lead], lead_id: int) -> Lead | None:
    return next((lead for lead in leads if lead.id == lead_id), None)


def _find_deal(deals: list[Deal], deal_id: int) -> Deal | None:
    return next((deal for deal in deals if deal.id == deal_id), None)


def build_context(leads: list[Lead], deals: list[Deal]) -> str:
    """Компактный список лидов и активных дел для промпта."""
    def lead_line(lead: Lead) -> str:
        when = f"{lead.next_date:%d.%m}" if lead.next_date else "нет"
        return (
            f"- id {lead.id}: {lead.company or 'без названия'}"
            f" | {lead.status or 'без статуса'} | след. {when}"
        )

    def deal_line(deal: Deal) -> str:
        when = f"{deal.deadline:%d.%m}" if deal.deadline else "нет"
        return (
            f"- id {deal.id}: {truncate(deal.task, 70)}"
            f" | {deal.status or 'без статуса'} | дедлайн {when}"
        )

    lead_lines = [lead_line(lead) for lead in leads[:MAX_LEADS_IN_CONTEXT]]
    deal_lines = [deal_line(deal) for deal in deals[:MAX_DEALS_IN_CONTEXT]]
    statuses = sorted({lead.status for lead in leads if lead.status})
    return (
        "Лиды:\n"
        + ("\n".join(lead_lines) if lead_lines else "- нет")
        + "\n\nАктивные дела:\n"
        + ("\n".join(deal_lines) if deal_lines else "- нет")
        + "\n\nСтатусы лидов в базе: "
        + (", ".join(statuses) if statuses else "нет")
    )


def build_messages(user_text: str, context: str, now: datetime) -> list[dict[str, str]]:
    user_prompt = (
        f"Сейчас: {now:%Y-%m-%d %H:%M} ({now.tzname() or 'Europe/Minsk'}), "
        f"сегодня {now:%Y-%m-%d}, день недели {now:%A}.\n\n"
        f"{context}\n\n"
        f"Фраза пользователя:\n{user_text}\n\n"
        "Верни один JSON-объект по схеме."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def _int(value: object) -> int | None:
    text = _clean(value)
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def parse_response(
    payload: dict,
    *,
    leads: list[Lead],
    deals: list[Deal],
    today: date,
    source_text: str = "",
) -> CrmAction:
    """Приводит ответ модели к CrmAction и проверяет id/поля."""
    if not isinstance(payload, dict):
        return CrmAction("unknown", source_text=source_text, reason="модель вернула не объект")

    action = _clean(payload.get("action") or "").lower()
    if action not in {"update_lead", "add_task", "update_task", "unknown"}:
        for key in ("update_lead", "add_task", "update_task", "unknown"):
            if isinstance(payload.get(key), dict):
                action = key
                break

    if action == "unknown" or not action:
        reason = _clean(_as_dict(payload.get("unknown")).get("reason")) or _clean(
            payload.get("reason")
        )
        return CrmAction(
            "unknown",
            source_text=source_text,
            reason=reason or "не понял формулировку",
            raw=payload,
        )

    if action == "update_lead":
        data = _as_dict(payload.get("update_lead")) or payload
        lead_id = _int(data.get("id"))
        if lead_id is None:
            return CrmAction("unknown", source_text=source_text, reason="не назван лид")
        lead = _find_lead(leads, lead_id)
        if lead is None:
            return CrmAction(
                "unknown",
                source_text=source_text,
                reason=f"лид с ID {lead_id} не найден",
            )
        update = LeadUpdate(id=lead_id)
        update.status = truncate(data.get("status") or "", 80)
        if data.get("next_date") not in (None, ""):
            update.has_next_date = True
            update.next_date = parse_iso_date(data.get("next_date"), today)
        if data.get("next_time") not in (None, ""):
            update.has_next_time = True
            update.next_time = parse_time(data.get("next_time"))
        update.next_step = truncate(data.get("next_step") or "", 200)
        update.note_append = truncate(data.get("note_append") or "", 300)

        if not any(
            (
                update.status,
                update.has_next_date,
                update.has_next_time,
                update.next_step,
                update.note_append,
            )
        ):
            return CrmAction(
                "unknown",
                source_text=source_text,
                reason="не понял, что именно изменить у лида",
            )
        return CrmAction(
            "update_lead", source_text=source_text, lead=update, raw=payload
        )

    if action == "add_task":
        data = _as_dict(payload.get("add_task")) or payload
        task = truncate(data.get("task") or "", 150)
        if not task:
            return CrmAction(
                "unknown", source_text=source_text, reason="не понял текст задачи"
            )
        deadline_raw = data.get("deadline")
        add = TaskAdd(
            task=task,
            priority=_parse_priority(data.get("priority")),
            deadline=parse_iso_date(deadline_raw, today),
            where=truncate(data.get("where") or "", 60),
            note=truncate(data.get("note") or "", 300),
        )
        return CrmAction("add_task", source_text=source_text, new_task=add, raw=payload)

    data = _as_dict(payload.get("update_task")) or payload
    task_id = _int(data.get("id"))
    if task_id is None:
        return CrmAction("unknown", source_text=source_text, reason="не названо дело")
    deal = _find_deal(deals, task_id)
    if deal is None:
        return CrmAction(
            "unknown", source_text=source_text, reason=f"дело с ID {task_id} не найдено"
        )
    update = TaskUpdate(id=task_id)
    update.status = truncate(data.get("status") or "", 40)
    if data.get("deadline") not in (None, ""):
        update.has_deadline = True
        update.deadline = parse_iso_date(data.get("deadline"), today)
    update.note_append = truncate(data.get("note_append") or "", 300)
    if not any((update.status, update.has_deadline, update.note_append)):
        return CrmAction(
            "unknown",
            source_text=source_text,
            reason="не понял, что именно изменить у дела",
        )
    return CrmAction("update_task", source_text=source_text, task=update, raw=payload)


async def parse_text(
    *,
    text: str,
    data: CrmData,
    now: datetime,
    api_key: str,
    model: str,
    base_url: str,
) -> CrmAction:
    """Отправляет фразу и контекст CRM в DeepSeek, возвращает проверенное действие."""
    if not api_key:
        raise RuntimeError(
            "Не задан DEEPSEEK_API_KEY: добавьте его в .env бота или в корневой .env"
        )
    active = [deal for deal in data.deals if deal.status.strip().lower() != "выполнено"]
    messages = build_messages(text, build_context(data.leads, active), now)
    payload = await request_json(
        api_key=api_key,
        model=model,
        base_url=base_url,
        messages=messages,
        max_tokens=900,
    )
    logger.info("DeepSeek CRM ответ: %s", json.dumps(payload, ensure_ascii=False)[:500])
    return parse_response(
        payload, leads=data.leads, deals=data.deals, today=now.date(), source_text=text
    )


def _date_text(value: date | None) -> str:
    return f"{value:%d.%m}" if value else "нет"


def format_preview(action: CrmAction, leads: list[Lead], deals: list[Deal]) -> str:
    """Короткое HTML-превью: что распознано и какие поля изменятся."""
    heard = esc(truncate(action.source_text, 220))
    lines = ["<b>Понял так</b>"]
    if heard:
        lines.append(f"<i>{heard}</i>")

    if action.kind == "update_lead" and action.lead:
        lead = _find_lead(leads, action.lead.id)
        title = esc(truncate(lead.company if lead else str(action.lead.id), 70))
        lines.append(f"Лид {action.lead.id}: {title}")
        changes: list[str] = []
        update = action.lead
        if update.status:
            changes.append(f"статус: {esc(update.status)}")
        if update.has_next_date:
            changes.append(f"след. дата: {_date_text(update.next_date)}")
        if update.has_next_time:
            changes.append(
                f"время: {update.next_time:%H:%M}" if update.next_time else "время: очищено"
            )
        if update.next_step:
            changes.append(f"шаг: {esc(truncate(update.next_step, 120))}")
        if update.note_append:
            changes.append(f"заметка: {esc(truncate(update.note_append, 120))}")
        lines.append("Изменения: " + " · ".join(changes))
        lines.append("<b>Записать в SAMOLA CRM?</b>")
        return "\n".join(lines)

    if action.kind == "add_task" and action.new_task:
        task = action.new_task
        lines.append("<b>Новое дело</b>")
        lines.append(f"Задача: {esc(task.task)}")
        details = [f"Приоритет: {esc(task.priority)}"]
        if task.deadline:
            details.append(f"дедлайн: {_date_text(task.deadline)}")
        if task.where:
            details.append(f"где: {esc(task.where)}")
        lines.append(" · ".join(details))
        if task.note:
            lines.append(f"Заметка: {esc(truncate(task.note, 120))}")
        lines.append("<b>Добавить дело в SAMOLA CRM?</b>")
        return "\n".join(lines)

    if action.kind == "update_task" and action.task:
        deal = _find_deal(deals, action.task.id)
        lines.append(f"Дело {action.task.id}: {esc(truncate(deal.task if deal else '', 80))}")
        changes = []
        if action.task.status:
            changes.append(f"статус: {esc(action.task.status)}")
        if action.task.has_deadline:
            changes.append(f"дедлайн: {_date_text(action.task.deadline)}")
        if action.task.note_append:
            changes.append(f"заметка: {esc(truncate(action.task.note_append, 120))}")
        lines.append("Изменения: " + " · ".join(changes))
        lines.append("<b>Записать в SAMOLA CRM?</b>")
        return "\n".join(lines)

    return "\n".join(lines + ["Не понял, что изменить.", "Уточните формулировку."])


def apply_action(
    crm: CrmSheets,
    action: CrmAction,
    *,
    leads: list[Lead],
    deals: list[Deal],
    today: date,
) -> str:
    """Пишет подтверждённое действие в таблицу и возвращает HTML-итог.

    Функция синхронная: вызывается из бота через asyncio.to_thread,
    чтобы не блокировать цикл событий запросами к Google Sheets.
    """
    if action.kind == "update_lead" and action.lead:
        lead = _find_lead(leads, action.lead.id)
        if lead is None:
            raise SheetError(f"Лид с ID {action.lead.id} не найден")
        update = action.lead
        kwargs: dict = {"today": today}
        if update.status:
            kwargs["status"] = update.status
        if update.has_next_date:
            kwargs["next_date"] = update.next_date
        if update.has_next_time:
            kwargs["next_time"] = update.next_time
        if update.next_step:
            kwargs["next_step"] = update.next_step
        if update.note_append:
            kwargs["note_append"] = update.note_append
        _, changes = crm.update_lead(lead, **kwargs)
        return (
            f"<b>Записано: лид {lead.id}</b>\n"
            f"{esc(truncate(lead.company, 70))}\n"
            + esc(" · ".join(changes))
            + "\n<i>Строка добавлена в Историю.</i>"
        )

    if action.kind == "add_task" and action.new_task:
        task = action.new_task
        deal, changes = crm.add_task(
            task=task.task,
            priority=task.priority,
            deadline=task.deadline,
            place=task.where,
            note=task.note,
            today=today,
        )
        return (
            f"<b>Дело добавлено: ID {deal.id}</b>\n"
            f"{esc(truncate(deal.task, 90))}\n"
            f"Дедлайн: {_date_text(deal.deadline)} · {esc(deal.priority)}\n"
            "<i>Строка добавлена в Историю.</i>"
        )

    if action.kind == "update_task" and action.task:
        deal = _find_deal(deals, action.task.id)
        if deal is None:
            raise SheetError(f"Дело с ID {action.task.id} не найдено")
        update = action.task
        kwargs = {"today": today}
        if update.status:
            kwargs["status"] = update.status
        if update.has_deadline:
            kwargs["deadline"] = update.deadline
        if update.note_append:
            kwargs["note_append"] = update.note_append
        _, changes = crm.update_deal(deal, **kwargs)
        return (
            f"<b>Записано: дело {deal.id}</b>\n"
            f"{esc(truncate(deal.task, 90))}\n"
            + esc(" · ".join(changes))
            + "\n<i>Строка добавлена в Историю.</i>"
        )

    raise SheetError("Нечего записывать: действие не распознано")
