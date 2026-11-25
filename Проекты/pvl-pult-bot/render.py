"""HTML-рендер карточек SAMOLA CRM для Telegram (parse_mode="HTML")."""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta
from typing import Iterable

from sheets import Deal, Lead

_DASHES = str.maketrans(
    {
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u2e3a": "-",
        "\u2e3b": "-",
        "\ufe58": "-",
        "\ufe63": "-",
        "\uff0d": "-",
    }
)

CLOSED_LEAD_MARKERS = ("отказ", "не беспокоить", "архив", "закрыт")
DONE_DEAL_MARKERS = ("выполн", "отменен", "закрыт", "архив")
HOT_LEAD_MARKERS = ("горяч", "переговор", "демо готово")

_PHONE_RE = re.compile(r"\+?\d[\d\s()\-]{6,}\d")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def clean(value: object) -> str:
    """Однострочное значение ячейки: без переводов строк и лишних пробелов."""
    return " ".join(normalize(value).split())


def normalize(text: object) -> str:
    """Заменяет длинные тире и палки, но сохраняет переводы строк сообщения."""
    return (
        str(text or "")
        .replace("\xa0", " ")
        .replace("|", "/")
        .translate(_DASHES)
    )


def esc(text: object) -> str:
    return html.escape(clean(text), quote=False)


def esc_attr(text: object) -> str:
    return html.escape(clean(text), quote=True)


def esc_block(text: object, limit: int = 800) -> str:
    """Экранирует многострочный текст, сохраняя переводы строк."""
    value = normalize(text).strip()
    if len(value) > limit:
        value = value[:limit].rstrip() + "..."
    return html.escape(value, quote=False)


def truncate(text: object, limit: int = 110) -> str:
    value = clean(text)
    if len(value) <= limit:
        return value
    cut = value[:limit].rstrip()
    if " " in cut:
        cut = cut[: cut.rfind(" ")].rstrip()
    return cut + "..."


# --- фильтры -------------------------------------------------------------
def is_lead_closed(status: str) -> bool:
    value = normalize(status).lower()
    return any(marker in value for marker in CLOSED_LEAD_MARKERS)


def is_deal_done(status: str) -> bool:
    value = normalize(status).lower()
    return value == "готово" or any(marker in value for marker in DONE_DEAL_MARKERS)


def is_hot_lead(lead: Lead) -> bool:
    value = normalize(lead.status).lower()
    return any(marker in value for marker in HOT_LEAD_MARKERS)


def lead_sort_key(lead: Lead):
    return (
        lead.next_time.hour if lead.next_time else 23,
        lead.next_time.minute if lead.next_time else 59,
        normalize(lead.company).lower(),
    )


_PRIORITY_RANK = {"высокий": 0, "средний": 1, "низкий": 2}


def deal_sort_key(deal: Deal):
    return (
        _PRIORITY_RANK.get(normalize(deal.priority).lower(), 3),
        deal.deadline or date.max,
        normalize(deal.task).lower(),
    )


def today_items(leads: Iterable[Lead], deals: Iterable[Deal], today: date):
    calls = [
        lead
        for lead in leads
        if lead.next_date == today and not is_lead_closed(lead.status)
    ]
    tasks = [
        deal
        for deal in deals
        if deal.deadline == today and not is_deal_done(deal.status)
    ]
    return sorted(calls, key=lead_sort_key), sorted(tasks, key=deal_sort_key)


def hot_items(leads: Iterable[Lead]) -> list[Lead]:
    return sorted([lead for lead in leads if is_hot_lead(lead)], key=lead_sort_key)


def overdue_items(leads: Iterable[Lead], deals: Iterable[Deal], today: date):
    calls = [
        lead
        for lead in leads
        if lead.next_date is not None
        and lead.next_date < today
        and not is_lead_closed(lead.status)
    ]
    tasks = [
        deal
        for deal in deals
        if deal.deadline is not None
        and deal.deadline < today
        and not is_deal_done(deal.status)
    ]
    return (
        sorted(calls, key=lambda lead: (lead.next_date, lead_sort_key(lead))),
        sorted(tasks, key=lambda deal: (deal.deadline, deal_sort_key(deal))),
    )


def week_items(leads: Iterable[Lead], deals: Iterable[Deal], today: date):
    limit = today + timedelta(days=7)
    calls = [
        lead
        for lead in leads
        if lead.next_date is not None
        and today <= lead.next_date <= limit
        and not is_lead_closed(lead.status)
    ]
    tasks = [
        deal
        for deal in deals
        if deal.deadline is not None
        and today <= deal.deadline <= limit
        and not is_deal_done(deal.status)
    ]
    return sorted(calls, key=lambda lead: (lead.next_date, lead_sort_key(lead))), sorted(
        tasks, key=lambda deal: (deal.deadline, deal_sort_key(deal))
    )


def active_deals(deals: Iterable[Deal]) -> list[Deal]:
    return sorted(
        [deal for deal in deals if not is_deal_done(deal.status)], key=deal_sort_key
    )


# --- карточки ------------------------------------------------------------
def _phone_html(phone: str) -> str | None:
    """Возвращает номер обычным текстом в международном формате (+375...).

    HTML-ссылки <a href="tel:..."> Telegram Bot API может отклонять,
    поэтому номер не оборачиваем в тег: клиенты Telegram сами делают его
    кликабельным.
    """
    match = _PHONE_RE.search(normalize(phone))
    if not match:
        return None
    shown = match.group(0).strip()
    digits = re.sub(r"\D", "", shown)
    if len(digits) < 10:
        return esc(shown)

    # Приводим распространённые белорусские форматы к +375XXXXXXXXX.
    if len(digits) == 12 and digits.startswith("375"):
        international = "+" + digits
    elif len(digits) == 11 and digits.startswith("80"):
        international = "+375" + digits[2:]
    elif len(digits) == 10 and digits.startswith("0"):
        international = "+375" + digits[1:]
    elif len(digits) == 9:
        international = "+375" + digits
    elif shown.startswith("+"):
        international = "+" + digits
    else:
        international = digits
    return esc(international)


def _demo_line(link: str) -> str | None:
    value = normalize(link)
    if not value or value in {"-", "нет"}:
        return None
    if _URL_RE.match(value):
        return f'🔗 <a href="{esc_attr(value)}">Демо / КП</a>'
    if value.lower().startswith("www."):
        return f'🔗 <a href="https://{esc_attr(value)}">Демо / КП</a>'
    return f"🔗 Демо / КП: {esc(truncate(value, 70))}"


def format_lead_card(lead: Lead, note: str | None = None) -> str:
    title_parts = [normalize(lead.company) or f"Лид {lead.id}"]
    if lead.city:
        title_parts.append(f"({normalize(lead.city)})")
    if lead.status:
        title_parts.append(f"- {normalize(lead.status)}")
    title = truncate(" ".join(title_parts), 110)

    when = "📅 без даты"
    if lead.next_date:
        when = f"📅 {lead.next_date:%d.%m}"
        if lead.next_time:
            when += f" в {lead.next_time:%H:%M}"

    contacts: list[str] = []
    phone = _phone_html(lead.phone)
    if phone:
        contacts.append(phone)
    elif lead.contact:
        contacts.append(esc(truncate(lead.contact, 40)))

    lines = [f"<b>{esc(title)}</b>", " · ".join([when, *contacts])]
    if lead.next_step:
        lines.append(f"Шаг: {esc(truncate(lead.next_step, 105))}")
    demo = _demo_line(lead.link)
    if demo:
        lines.append(demo)
    if note:
        lines.append(f"<i>{esc(note)}</i>")
    return "\n".join(lines)


def format_lead_expanded(lead: Lead) -> str:
    """Раскрытая карточка лида: компактная карточка + блок брифа и заметок."""
    lines = [format_lead_card(lead), "", "<b>Бриф / Заметки</b>"]
    lines.append(esc_block(lead.notes, 900) if lead.notes.strip() else "Бриф не заполнен.")
    if lead.service:
        lines.append(f"<b>Услуга</b>: {esc(truncate(lead.service, 120))}")
    return "\n".join(lines)


def format_deal_card(deal: Deal, note: str | None = None) -> str:
    title = truncate(deal.task or f"Дело {deal.id}", 100)
    deadline = f"{deal.deadline:%d.%m}" if deal.deadline else "без дедлайна"
    line2 = [f"📅 {deadline}"]
    if deal.priority:
        line2.append(f"Приоритет: {normalize(deal.priority)}")
    line3: list[str] = []
    if deal.place:
        line3.append(esc(truncate(deal.place, 35)))
    if deal.status:
        line3.append(esc(normalize(deal.status)))

    lines = [f"<b>{esc(title)}</b>", " · ".join(line2)]
    if line3:
        lines.append(" · ".join(line3))
    if deal.notes:
        lines.append(f"<i>{esc(truncate(deal.notes, 90))}</i>")
    if note:
        lines.append(f"<i>{esc(note)}</i>")
    return "\n".join(lines)


VIEW_TITLES = {
    "today": "Сегодня",
    "hot": "Горячие",
    "overdue": "Просрочено",
    "week": "Неделя",
    "deals": "Дела",
}


def format_header(
    view: str,
    today: date,
    *,
    leads_count: int = 0,
    deals_count: int = 0,
) -> str:
    """Короткая шапка среза: название с количеством карточек и одна строка счёта."""
    title = VIEW_TITLES.get(view, "Пульт")
    total = leads_count + deals_count
    lines = [f"<b>{title} ({total})</b>"]
    if total == 0:
        lines.append("На этот срез ничего нет.")
        return "\n".join(lines)

    if view == "today":
        lines.append(f"Звонков: {leads_count} · Дел: {deals_count}")
    elif view == "deals":
        lines.append(f"Задач: {deals_count}")
    elif view == "hot":
        lines.append(f"Лидов: {leads_count}")
    else:
        lines.append(f"Лидов: {leads_count} · Дел: {deals_count}")
    return "\n".join(lines)


_WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _plural(count: int, one: str, few: str, many: str) -> str:
    count = abs(count)
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def summary_text(
    now: datetime,
    *,
    calls_count: int,
    deals_count: int,
    hot_count: int,
    overdue_count: int,
    leads_total: int,
    active_deals: int,
) -> str:
    """Короткая сводка дня для /start и кнопки «Сводка»."""
    weekday = _WEEKDAYS[now.weekday()]
    return (
        "<b>Пульт SAMOLA CRM</b>\n"
        f"{weekday}, {now:%d.%m} · {now:%H:%M}\n"
        f"Звонки: {calls_count} · Дела: {deals_count}\n"
        f"Горячих: {hot_count} · Просрочено: {overdue_count}\n"
        f"В базе: {leads_total} {_plural(leads_total, 'лид', 'лида', 'лидов')}"
        f" · {active_deals} {_plural(active_deals, 'дело', 'дела', 'дел')} в работе"
    )


def morning_text(
    today: date,
    *,
    leads_count: int,
    deals_count: int,
    overdue_count: int,
) -> str:
    return (
        f"<b>Доброе утро. {today:%d.%m}</b>\n"
        f"Звонков сегодня: {leads_count}\n"
        f"Дел на сегодня: {deals_count}\n"
        f"Просрочено: {overdue_count}"
    )


def reminder_text(lead: Lead) -> str:
    when = ""
    if lead.next_time:
        when = f" в {lead.next_time:%H:%M}"
    return f"⏰ <b>Через 15 минут звонок{when}</b>\n" + format_lead_card(lead)
