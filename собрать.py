#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка компактного ПУЛЬТ.md для утреннего обзвона.

Источники: Карточки/*.md и ДЕЛА.md. Карточки не изменяются.
Запуск: python собрать.py [--today ГГГГ-ММ-ДД]
"""

from __future__ import annotations

import argparse
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CARDS_DIR = ROOT / "Карточки"
DELA_FILE = ROOT / "ДЕЛА.md"
PULT_FILE = ROOT / "ПУЛЬТ.md"
BRIEFS_DIR = ROOT / "Брифы"
ICS_FILE = ROOT / "ЗВОНКИ.ics"

STATUS_PRIORITY = (
    ("горяч", 0),
    ("тепл", 1),
    ("переговор", 2),
    ("лпр", 3),
    ("недозвон", 4),
)
CLOSED_MARKERS = ("отказ", "закрыт", "не беспокоить")
WARM_STATUS_MARKERS = ("горяч", "тёпл", "тепл", "демо", "переговор", "в работе")
BLANK_VALUES = {"", "-", "0", "0.0", "0.00", "нет", "не указано", "нет данных", "nan"}

PHONE_RE = re.compile(r"\+?\d(?:[\d\s()\-]{5,}\d)?")
STEP_RE = re.compile(r"^\s*[-*]\s*\[\s*\]\s*(\d{4}-\d{2}-\d{2})\s+(.+?)\s*$", re.MULTILINE)
TIME_RE = re.compile(r"(?<![\d.])([01]?\d|2[0-3])[:.](\d{2})(?![\d.])(?:\s*[-]\s*([01]?\d|2[0-3])[:.](\d{2})(?![\d.]))?\b")
DATE_IN_TEXT_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
AMOUNT_RE = re.compile(r"\d[\d\s]*(?:[.,]\d+)?")
CURRENCY_RE = re.compile(r"\$|€|USD|EUR|BYN|RUB|руб\.?|р\.?|долл\.?|евро", re.IGNORECASE)
DASHES_RE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")


def clean_text(value: object) -> str:
    """Убирает лишние пробелы и длинные тире."""
    text = str(value or "").replace("\ufeff", "").replace("\u00a0", " ")
    text = DASHES_RE.sub("-", text)
    return re.sub(r"\s+", " ", text).strip()


def blank(value: object) -> bool:
    text = clean_text(value).lower()
    if text in BLANK_VALUES:
        return True
    return bool(re.fullmatch(r"0(?:[.,]0+)?(?:\s*[a-zа-яё.$€]+)?", text))


def status_rank(status: str) -> int:
    low = clean_text(status).lower()
    for marker, rank in STATUS_PRIORITY:
        if marker in low:
            return rank
    return 9


def is_closed(status: str) -> bool:
    low = clean_text(status).lower()
    return any(marker in low for marker in CLOSED_MARKERS)


def is_warm_status(status: str) -> bool:
    low = clean_text(status).lower()
    return any(marker in low for marker in WARM_STATUS_MARKERS)


def find_brief_file(card: Card) -> Path | None:
    candidates = (BRIEFS_DIR / f"{card.name}.md", BRIEFS_DIR / card.name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def brief_cell(card: Card | None) -> str:
    if card is None:
        return "-"
    brief = find_brief_file(card)
    if brief is not None:
        return f"[Есть](Брифы/{brief.name})"
    if is_warm_status(card.status):
        return "НЕТ БРИФА"
    return "-"


def brief_note(card: Card | None) -> str:
    if card is None:
        return "-"
    if find_brief_file(card) is not None:
        return "есть"
    if is_warm_status(card.status):
        return "НЕТ БРИФА"
    return "-"


def parse_frontmatter(text: str) -> dict[str, str]:
    text = text.lstrip("\ufeff")
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", text, re.DOTALL)
    if not match:
        return {}
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = clean_text(key).lower()
        value = clean_text(value).strip('"')
        if key:
            meta[key] = value
    return meta


def meta_get(meta: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = meta.get(key.lower())
        if value is not None:
            return value
    return ""


def section_text(text: str, *names: str) -> str:
    wanted = tuple(name.lower() for name in names)
    lines = text.splitlines()
    collecting = False
    collected: list[str] = []
    for line in lines:
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            title = clean_text(heading.group(1)).lower()
            if collecting:
                break
            if any(name in title for name in wanted):
                collecting = True
            continue
        if collecting:
            collected.append(line)
    return "\n".join(collected).strip()


def first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        line = clean_text(re.sub(r"^[-*]\s*", "", line.strip()))
        if line and not line.startswith("#"):
            return line
    return ""


def truncate(value: str, limit: int) -> str:
    text = clean_text(value)
    if len(text) <= limit:
        return text
    cut = text[: limit - 3].rsplit(" ", 1)[0]
    return cut + "..."


def parse_date(value: str) -> date | None:
    match = DATE_IN_TEXT_RE.search(clean_text(value))
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def extract_phone_display(raw: str) -> list[str]:
    phones: list[str] = []
    for chunk in re.split(r"[;\n]+", clean_text(raw)):
        for match in PHONE_RE.findall(chunk):
            phone = clean_text(match).strip(" ,;")
            digits = re.sub(r"\D", "", phone)
            if len(digits) >= 7 and phone not in phones:
                phones.append(phone)
    return phones


def phone_key(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("80"):
        digits = "375" + digits[2:]
    elif len(digits) == 10 and digits.startswith("0"):
        digits = "375" + digits[1:]
    return digits[-9:]


def extract_money(value: str) -> tuple[Decimal, str] | None:
    if blank(value):
        return None
    text = clean_text(value)
    amount_match = AMOUNT_RE.search(text)
    if not amount_match:
        return None
    amount_text = re.sub(r"\s+", "", amount_match.group(0)).replace(",", ".")
    try:
        amount = Decimal(amount_text)
    except InvalidOperation:
        return None
    currency_match = CURRENCY_RE.search(text)
    currency = "BYN"
    if currency_match:
        raw_currency = currency_match.group(0).lower()
        if "$" in raw_currency or "usd" in raw_currency or "долл" in raw_currency:
            currency = "$"
        elif "€" in raw_currency or "eur" in raw_currency or "евро" in raw_currency:
            currency = "€"
        elif "rub" in raw_currency or "руб" in raw_currency or raw_currency == "р.":
            currency = "RUB"
        else:
            currency = "BYN"
    return amount, currency


def format_amount(amount: Decimal) -> str:
    if amount == amount.to_integral_value():
        text = f"{amount:,.0f}"
    else:
        text = f"{amount:,.2f}".rstrip("0").rstrip(".")
    return text.replace(",", " ")


def format_totals(values: defaultdict[str, Decimal]) -> str:
    if not values:
        return "0 BYN"
    parts = []
    for currency in sorted(values):
        parts.append(f"{format_amount(values[currency])} {currency}")
    return ", ".join(parts)


def extract_condition(text: str) -> str:
    text = clean_text(text)
    parts: list[str] = []
    money_match = re.search(
        r"\d[\d\s]*(?:\s*[-]\s*\d[\d\s]*)?\s*(?:\$|€|USD|EUR|BYN|RUB|руб\.?|долл\.?|евро)",
        text,
        re.IGNORECASE,
    )
    if money_match:
        parts.append(clean_text(money_match.group(0)))
    prepay_match = re.search(r"(?:предоплат\w*|аванс\w*)[^,.;]{0,40}", text, re.IGNORECASE)
    if prepay_match:
        parts.append(clean_text(prepay_match.group(0)))
    return "; ".join(parts)


def clean_task(task: str) -> str:
    text = clean_text(task)
    text = re.sub(r"^\d{4}-\d{2}-\d{2}\s*", "", text)
    text = re.sub(
        r"^в\s+\d{1,2}[:.]\d{2}(?:\s*-\s*\d{1,2}[:.]\d{2})?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"^\d{1,2}[:.]\d{2}(?:\s*-\s*\d{1,2}[:.]\d{2})?\s*", "", text)
    text = re.sub(r"^(?:днем|днём|утром|вечером|после обеда)\s*", "", text, flags=re.IGNORECASE)
    return text.strip()


def exact_minutes(task: str) -> int | None:
    match = TIME_RE.search(clean_text(task))
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def time_info(task: str) -> tuple[int | None, str, int]:
    text = clean_text(task)
    match = TIME_RE.search(text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        label = f"{hour:02d}:{minute:02d}"
        if match.group(3):
            label += f"-{int(match.group(3)):02d}:{match.group(4)}"
        return hour * 60 + minute, label, 0

    low = text.lower()
    if "после обеда" in low:
        return 14 * 60, "после обеда", 1
    if "утр" in low:
        return 9 * 60, "утро", 1
    if "вечер" in low:
        return 18 * 60, "вечер", 1
    if "днём" in low or "днем" in low or re.search(r"\bдень\b", low):
        return 12 * 60, "день", 1
    return None, "-", 2


@dataclass
class Step:
    day: date
    source: str
    task: str
    status: str = ""
    is_system: bool = False

    @property
    def rank(self) -> int:
        return 5 if self.is_system else status_rank(self.status)


@dataclass
class Card:
    name: str
    status: str
    company: str
    city: str
    phones: list[str]
    money_wait: str
    money_owe: str
    last_contact: date | None
    current_context: str
    history_last: str
    steps: list[Step] = field(default_factory=list)

    @property
    def closed(self) -> bool:
        return is_closed(self.status)

    @property
    def phone_display(self) -> str:
        return "; ".join(self.phones) if self.phones else "-"


def load_cards() -> list[Card]:
    cards: list[Card] = []
    if not CARDS_DIR.exists():
        return cards

    for path in sorted(CARDS_DIR.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        meta = parse_frontmatter(content)
        name = path.stem
        status = meta_get(meta, "статус")
        company = meta_get(meta, "компания") or name
        city = meta_get(meta, "город")
        phone_raw = meta_get(meta, "телефон", "телефоны", "phone")
        phones = extract_phone_display(phone_raw)

        now_section = section_text(content, "сейчас", "текущая ситуация", "контекст")
        history_section = section_text(content, "история касаний")
        history_last = ""
        for line in history_section.splitlines():
            line = clean_text(re.sub(r"^[-*]\s*", "", line.strip()))
            if line:
                history_last = line
        context = first_meaningful_line(now_section)
        if not context:
            context = re.sub(r"^\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?:\s*", "", history_last)
        if not context:
            context = status

        steps = []
        for match in STEP_RE.finditer(content):
            try:
                step_day = datetime.strptime(match.group(1), "%Y-%m-%d").date()
            except ValueError:
                continue
            steps.append(Step(step_day, name, clean_text(match.group(2)), status))

        cards.append(
            Card(
                name=name,
                status=status,
                company=company,
                city=city,
                phones=phones,
                money_wait=meta_get(meta, "деньги_ждём", "деньги_ждем", "ждём", "ждем"),
                money_owe=meta_get(meta, "мы_должны", "должны"),
                last_contact=parse_date(meta_get(meta, "последний_контакт")),
                current_context=context,
                history_last=history_last,
                steps=steps,
            )
        )
    return cards


def load_dela_steps() -> list[Step]:
    steps: list[Step] = []
    if not DELA_FILE.exists():
        return steps
    content = DELA_FILE.read_text(encoding="utf-8")
    for match in STEP_RE.finditer(content):
        try:
            step_day = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        steps.append(Step(step_day, "ДЕЛА", clean_text(match.group(2)), "ДЕЛА", True))
    return steps


def render_table(headers: list[str], rows: list[list[str]], right: set[int] | None = None) -> str:
    right = right or set()
    clean_rows = [[clean_text(cell) or "-" for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in clean_rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell.replace("|", "/")))

    def pad(value: str, width: int, index: int) -> str:
        value = clean_text(value).replace("|", "/") or "-"
        if index in right:
            return value.rjust(width)
        return value.ljust(width)

    lines = []
    lines.append("| " + " | ".join(pad(header, widths[i], i) for i, header in enumerate(headers)) + " |")
    separators = []
    for index, width in enumerate(widths):
        if index in right:
            separators.append("-" * max(3, width - 1) + ":")
        else:
            separators.append(":" + "-" * max(3, width - 1))
    lines.append("| " + " | ".join(separators) + " |")
    for row in clean_rows:
        lines.append("| " + " | ".join(pad(cell, widths[i], i) for i, cell in enumerate(row)) + " |")
    return "\n".join(lines)


def next_step_for(card: Card, today: date) -> Step | None:
    future = sorted((step for step in card.steps if step.day >= today), key=lambda step: step.day)
    if future:
        return future[0]
    if card.steps:
        return sorted(card.steps, key=lambda step: step.day)[-1]
    return None


def money_cell(card: Card, today: date) -> str:
    parts: list[str] = []
    if not blank(card.money_wait):
        parts.append("ждём: " + clean_text(card.money_wait))
    if not blank(card.money_owe):
        parts.append("должны: " + clean_text(card.money_owe))
    if not parts:
        step = next_step_for(card, today)
        if step:
            condition = extract_condition(step.task)
            if condition:
                parts.append(condition)
    return "; ".join(parts) if parts else "-"


def step_label(step: Step | None) -> str:
    if not step:
        return "не назначен"
    return f"{step.day.isoformat()} {clean_task(step.task)}"


def schedule_key(step: Step) -> tuple:
    minutes, _, time_kind = time_info(step.task)
    return (
        time_kind,
        minutes if minutes is not None else 9999,
        step.rank,
        step.day,
        step.source.lower(),
        clean_task(step.task).lower(),
    )


def build_schedule_rows(steps: list[Step], cards_by_name: dict[str, Card], limit_context: int = 75) -> list[list[str]]:
    rows: list[list[str]] = []
    for step in sorted(steps, key=schedule_key):
        _, time_label, _ = time_info(step.task)
        card = cards_by_name.get(step.source)
        phone = card.phone_display if card else "-"
        context = card.current_context if card else "личное/системное дело"
        rows.append([
            time_label,
            step.source,
            phone,
            truncate(clean_task(step.task), 130),
            truncate(context, limit_context),
            brief_cell(card),
        ])
    return rows


def ics_escape(value: object) -> str:
    text = clean_text(value)
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;").replace(",", "\\,")
    return text


def fold_ics_line(line: str, limit: int = 75) -> list[str]:
    if len(line.encode("utf-8")) <= limit:
        return [line]
    parts: list[str] = []
    current = ""
    for char in line:
        candidate = current + char
        if len(candidate.encode("utf-8")) > limit:
            parts.append(current)
            current = " " + char
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def write_ics(steps: list[Step], cards_by_name: dict[str, Card], today: date, week_end: date) -> int:
    timed: list[tuple[Step, int]] = []
    for step in steps:
        if not (today <= step.day <= week_end):
            continue
        minutes = exact_minutes(step.task)
        if minutes is not None:
            timed.append((step, minutes))
    timed.sort(key=lambda item: (item[0].day, item[1], item[0].source.lower()))

    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//PVL//ЗВОНКИ//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:ЗВОНКИ",
        "X-WR-TIMEZONE:Europe/Minsk",
        "BEGIN:VTIMEZONE",
        "TZID:Europe/Minsk",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0300",
        "TZOFFSETTO:+0300",
        "TZNAME:+03",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    for index, (step, minutes) in enumerate(timed, start=1):
        hour, minute = divmod(minutes, 60)
        start = datetime.combine(step.day, datetime.min.time()).replace(hour=hour, minute=minute)
        end = start + timedelta(minutes=30)
        card = cards_by_name.get(step.source)
        company = (card.company if card and card.company else step.source)
        phone = card.phone_display if card else "-"
        status = card.status if card else "ДЕЛА"
        context = card.current_context if card else "личное/системное дело"
        summary = f"[{company}] - {truncate(clean_task(step.task), 90)}"
        description = "\\n".join([
            f"Телефон: {ics_escape(phone)}",
            f"Статус: {ics_escape(status)}",
            f"Контекст: {ics_escape(context)}",
            f"Бриф: {ics_escape(brief_note(card))}",
        ])
        uid_seed = f"{step.day.isoformat()}|{step.source}|{step.task}|{index}"
        uid = hashlib.sha1(uid_seed.encode("utf-8")).hexdigest()[:20] + "@pvl.local"
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART;TZID=Europe/Minsk:{start.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Europe/Minsk:{end.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{ics_escape(summary)}",
            f"DESCRIPTION:{description}",
            "STATUS:CONFIRMED",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "DESCRIPTION:Напоминание за 30 минут",
            "TRIGGER:-PT30M",
            "END:VALARM",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "DESCRIPTION:Напоминание за 5 минут",
            "TRIGGER:-PT5M",
            "END:VALARM",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    folded: list[str] = []
    for line in lines:
        folded.extend(fold_ics_line(line))
    content = "\r\n".join(folded) + "\r\n"
    ICS_FILE.write_bytes(content.encode("utf-8"))
    return len(timed)


def main() -> None:
    parser = argparse.ArgumentParser(description="Собрать ПУЛЬТ.md")
    parser.add_argument("--today", help="Дата генерации в формате ГГГГ-ММ-ДД")
    args = parser.parse_args()
    today = date.today()
    if args.today:
        today = datetime.strptime(args.today, "%Y-%m-%d").date()

    tomorrow = today + timedelta(days=1)
    week_end = today + timedelta(days=7)

    cards = load_cards()
    cards_by_name = {card.name: card for card in cards}
    active_cards = [card for card in cards if not card.closed]
    closed_cards = [card for card in cards if card.closed]

    active_steps: list[Step] = []
    for card in active_cards:
        active_steps.extend(card.steps)
    active_steps.extend(load_dela_steps())

    deduped_steps: list[Step] = []
    seen_steps: set[tuple] = set()
    for step in active_steps:
        key = (step.day, step.source, step.task)
        if key not in seen_steps:
            seen_steps.add(key)
            deduped_steps.append(step)
    all_steps = deduped_steps

    today_steps = [step for step in all_steps if step.day == today]
    tomorrow_steps = [step for step in all_steps if step.day == tomorrow]
    overdue_steps = [step for step in all_steps if step.day < today]
    week_steps = [step for step in all_steps if tomorrow < step.day <= week_end]

    wait_total: defaultdict[str, Decimal] = defaultdict(Decimal)
    owe_total: defaultdict[str, Decimal] = defaultdict(Decimal)
    for card in cards:
        wait = extract_money(card.money_wait)
        if wait:
            wait_total[wait[1]] += wait[0]
        owe = extract_money(card.money_owe)
        if owe:
            owe_total[owe[1]] += owe[0]

    blocks: list[str] = []
    blocks.append("# ПУЛЬТ")
    blocks.append("_Сгенерировано собрать.py. Руками не править._")

    summary = [
        "## СВОДКА ДНЯ",
        f"- Дата: {today.isoformat()}",
        f"- Активных лидов/клиентов: {len(active_cards)}; касаний: сегодня {len(today_steps)}, завтра {len(tomorrow_steps)}",
        f"- Ждём денег: {format_totals(wait_total)}; мы должны: {format_totals(owe_total)}",
    ]
    blocks.append("\n".join(summary))

    money_rows: list[list[str]] = []
    for card in cards:
        has_money = not blank(card.money_wait) or not blank(card.money_owe)
        hot = "горяч" in card.status.lower() or "в работе" in card.status.lower()
        if not (has_money or hot):
            continue
        money_rows.append([
            card.name,
            card.status or "-",
            money_cell(card, today),
            step_label(next_step_for(card, today)),
        ])
    money_rows.sort(key=lambda row: (status_rank(row[1]), row[0].lower()))
    money_table = render_table(
        ["Имя (компания)", "Статус", "Сумма и условие", "Следующий шаг"],
        money_rows,
    )
    blocks.append("## ДЕНЬГИ И ГОРЯЧИЕ\n" + (money_table if money_rows else "Нет карточек с деньгами или горячим статусом."))

    if overdue_steps:
        overdue_rows = []
        for step in sorted(overdue_steps, key=lambda item: (item.day, item.rank, item.source.lower())):
            overdue_rows.append([
                step.day.isoformat(),
                step.source,
                truncate(clean_task(step.task), 130),
                str((today - step.day).days),
            ])
        overdue_block = "## ПРОСРОЧЕНО\n" + render_table(
            ["Дата", "Компания", "Задача", "Дней"], overdue_rows, right={3}
        )
    else:
        overdue_block = "## ПРОСРОЧЕНО\nПросроченных задач нет."
    blocks.append(overdue_block)

    today_rows = build_schedule_rows(today_steps, cards_by_name)
    blocks.append("## СЕГОДНЯ\n" + (
        render_table(["Время", "Компания", "Телефон", "Что сделать", "Контекст", "Бриф"], today_rows)
        if today_rows else "На сегодня задач нет."
    ))

    tomorrow_rows = build_schedule_rows(tomorrow_steps, cards_by_name)
    blocks.append("## ЗАВТРА\n" + (
        render_table(["Время", "Компания", "Телефон", "Что сделать", "Контекст", "Бриф"], tomorrow_rows)
        if tomorrow_rows else "На завтра задач нет."
    ))

    week_rows = []
    for step in sorted(week_steps, key=lambda item: (item.day, schedule_key(item))):
        card = cards_by_name.get(step.source)
        week_rows.append([
            step.day.isoformat(),
            step.source,
            card.phone_display if card else "-",
            truncate(clean_task(step.task), 110),
            brief_cell(card),
        ])
    week_block = "## НЕДЕЛЯ (ближайшие 7 дней)\n" + (
        render_table(["Дата", "Компания", "Телефон", "Что сделать", "Бриф"], week_rows)
        if week_rows else f"С {tomorrow.isoformat()} по {week_end.isoformat()} задач нет."
    )
    blocks.append(week_block)

    hygiene: list[str] = []
    warm_without_brief = [
        (card.company or card.name) for card in active_cards
        if is_warm_status(card.status) and find_brief_file(card) is None
    ]
    hygiene.append(
        "Тёплые/горячие без брифа: "
        + ("; ".join(warm_without_brief) if warm_without_brief else "нет")
    )
    no_future_step = [
        card.name for card in active_cards
        if not any(step.day >= today for step in card.steps)
    ]
    if no_future_step:
        hygiene.append("Без будущего шага с датой: " + "; ".join(no_future_step))

    no_phone = [card.name for card in active_cards if not card.phones]
    if no_phone:
        hygiene.append("Без телефона в шапке: " + "; ".join(no_phone))

    silent = []
    for card in active_cards:
        if card.last_contact and (today - card.last_contact).days > 14:
            silent.append(f"{card.name} ({(today - card.last_contact).days} дн. с {card.last_contact.isoformat()})")
    if silent:
        hygiene.append("Молчуны >14 дней: " + "; ".join(silent))

    phone_map: defaultdict[str, list[str]] = defaultdict(list)
    for card in cards:
        for phone in card.phones:
            key = phone_key(phone)
            if key and card.name not in phone_map[key]:
                phone_map[key].append(card.name)
    duplicates = [
        f"{phone}: " + ", ".join(names)
        for phone, names in sorted(phone_map.items())
        if len(names) > 1
    ]
    if duplicates:
        hygiene.append("Дубли телефонов: " + "; ".join(duplicates))

    if closed_cards:
        hygiene.append(
            f"Закрытые/отказные: {len(closed_cards)} - " + ", ".join(card.name for card in closed_cards)
        )
    if not hygiene:
        hygiene.append("Всё чисто.")

    hygiene_block = (
        "## КОНТРОЛЬ ГИГИЕНЫ\n"
        "<details>\n"
        f"<summary>Проверки базы ({len(hygiene)} п.)</summary>\n\n"
        + "\n".join(f"- {item}" for item in hygiene)
        + "\n</details>"
    )
    blocks.append(hygiene_block)

    ics_count = write_ics(all_steps, cards_by_name, today, week_end)

    output = "\n\n".join(blocks).strip() + "\n"
    output = DASHES_RE.sub("-", output)
    output = re.sub(r"\n{3,}", "\n\n", output)
    PULT_FILE.write_text(output, encoding="utf-8", newline="\n")
    print(f"ПУЛЬТ.md пересобран: {PULT_FILE}")
    print(f"Активных карточек: {len(active_cards)}, закрытых: {len(closed_cards)}, задач сегодня: {len(today_steps)}, завтра: {len(tomorrow_steps)}")
    print(f"ЗВОНКИ.ics пересобран: {ICS_FILE} (событий: {ics_count})")


if __name__ == "__main__":
    main()