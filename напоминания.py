#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Точные Telegram-напоминания о звонках из карточек PVL.

Запуск:
    python напоминания.py
    python напоминания.py --dry-run --at "2026-11-23 10:00"
    python напоминания.py --send-test
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from собрать import (  # noqa: E402
    Card,
    Step,
    clean_task,
    clean_text,
    find_brief_file,
    is_warm_status,
    load_cards,
    load_dela_steps,
    truncate,
)

TZ = ZoneInfo("Europe/Minsk")
STATE_DIR = ROOT / ".state"
STATE_FILE = STATE_DIR / "sent_reminders.json"
ENV_FILES = (
    ROOT / ".env",
    ROOT / "Проекты" / "pvl-pult-bot" / ".env",
)
EXACT_TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
TELEGRAM_MESSAGE_LIMIT = 4096
STATE_TTL_DAYS = 7

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("pvl-reminders")


@dataclass
class Reminder:
    key: str
    window: str
    text: str
    brief_path: Path | None = None


def env_value(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    for path in ENV_FILES:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            continue
        values: dict[str, str] = {}
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
        for name in names:
            if values.get(name):
                return values[name]
    return ""


def telegram_credentials() -> tuple[str, str]:
    token = env_value("TELEGRAM_BOT_TOKEN", "BOT_TOKEN")
    chat_id = env_value("TELEGRAM_CHAT_ID", "CHAT_ID", "ADMIN_ID")
    if not token or not chat_id:
        raise SystemExit(
            "Не найдены TELEGRAM_BOT_TOKEN/BOT_TOKEN и TELEGRAM_CHAT_ID/CHAT_ID/ADMIN_ID. "
            "Проверьте .env."
        )
    return token, chat_id


def collect_data() -> tuple[list[Card], dict[str, Card], list[Step]]:
    cards = load_cards()
    cards_by_name = {card.name: card for card in cards}
    steps: list[Step] = []
    for card in cards:
        if not card.closed:
            steps.extend(card.steps)
    steps.extend(load_dela_steps())

    deduped: list[Step] = []
    seen: set[tuple] = set()
    for step in steps:
        key = (step.day, step.source, step.task)
        if key not in seen:
            seen.add(key)
            deduped.append(step)
    return cards, cards_by_name, deduped


def exact_minutes(task: str) -> int | None:
    match = EXACT_TIME_RE.search(clean_text(task))
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def time_label(minutes: int) -> str:
    hour, minute = divmod(minutes, 60)
    return f"{hour:02d}:{minute:02d}"


def call_datetime(step: Step, minutes: int) -> datetime:
    hour, minute = divmod(minutes, 60)
    return datetime.combine(step.day, time(hour=hour, minute=minute), tzinfo=TZ)


def company_for(step: Step, card: Card | None) -> str:
    if card is None:
        return "ДЕЛА"
    return clean_text(card.company or card.name)


def status_for(card: Card | None) -> str:
    if card is None:
        return "ДЕЛА"
    return clean_text(card.status or "-")


def phone_for(card: Card | None) -> str:
    if card is None:
        return "-"
    return clean_text(card.phone_display)


def context_for(card: Card | None) -> str:
    if card is None:
        return "личное/системное дело"
    return truncate(card.current_context or "-", 220)


def task_for(step: Step, limit: int = 280) -> str:
    return truncate(clean_task(step.task), limit)


def reminder_30(step: Step, card: Card | None, minutes: int) -> Reminder:
    company = company_for(step, card)
    status = status_for(card)
    phone = phone_for(card)
    context = context_for(card)
    label = time_label(minutes)
    text = "\n".join(
        [
            f"Через 30 мин звонок: {company} ({status})",
            f"Время: {label}",
            f"Телефон: {phone}",
            f"Задача: {task_for(step)}",
            f"Контекст: {context}",
        ]
    )
    brief_path = find_brief_file(card) if card is not None else None
    return Reminder(
        key=f"{step.day.isoformat()}|{step.source}|{label}|30m",
        window="30m",
        text=text,
        brief_path=brief_path,
    )


def reminder_5(step: Step, card: Card | None, minutes: int) -> Reminder:
    company = company_for(step, card)
    phone = phone_for(card)
    text = f"Через 5 мин звонок: {company} | {phone} | {task_for(step, 160)}"
    return Reminder(
        key=f"{step.day.isoformat()}|{step.source}|{time_label(minutes)}|5m",
        window="5m",
        text=text,
    )


def reminder_no_time(now: datetime, steps: list[Step], cards_by_name: dict[str, Card]) -> Reminder | None:
    if not steps:
        return None
    if not (time(13, 25) <= now.time() <= time(13, 40)):
        return None
    lines = [f"Напоминание на 13:30: сегодня без точного времени осталось {len(steps)}:"]
    for step in sorted(steps, key=lambda item: item.source.lower()):
        card = cards_by_name.get(step.source)
        lines.append(f"- {company_for(step, card)} | {phone_for(card)} | {task_for(step, 160)}")
    return Reminder(
        key=f"{now.date().isoformat()}|СПИСОК-БЕЗ-ВРЕМЕНИ|13:30|no-time",
        window="13:30",
        text="\n".join(lines),
    )


def find_due(now: datetime, steps: list[Step], cards_by_name: dict[str, Card]) -> list[Reminder]:
    due: list[Reminder] = []
    no_time_steps: list[Step] = []
    for step in steps:
        if step.day != now.date():
            continue
        minutes = exact_minutes(step.task)
        if minutes is None:
            no_time_steps.append(step)
            continue
        card = cards_by_name.get(step.source)
        delta = (call_datetime(step, minutes) - now).total_seconds() / 60
        if 25 <= delta <= 35:
            due.append(reminder_30(step, card, minutes))
        elif 0 <= delta <= 8:
            due.append(reminder_5(step, card, minutes))

    no_time = reminder_no_time(now, no_time_steps, cards_by_name)
    if no_time is not None:
        due.append(no_time)
    return due


def load_state() -> dict[str, str]:
    if not STATE_FILE.is_file():
        return {}
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Файл состояния повреждён, начинаю с пустого.")
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def prune_state(state: dict[str, str], today: date) -> dict[str, str]:
    cutoff = today - timedelta(days=STATE_TTL_DAYS)
    cleaned: dict[str, str] = {}
    for key, value in state.items():
        raw_date = key.split("|", 1)[0]
        try:
            key_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if key_date >= cutoff:
            cleaned[key] = value
    return cleaned


def save_state(state: dict[str, str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_FILE.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp_path.replace(STATE_FILE)


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit and current:
            chunks.append(current)
            current = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current += line
    if current:
        chunks.append(current)
    return chunks


def telegram_api(method: str, payload: dict[str, str], file_path: Path | None = None) -> None:
    token, _ = telegram_credentials()
    url = f"https://api.telegram.org/bot{token}/{method}"
    if file_path is None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
    else:
        boundary = "----PVLReminderBoundary7MA4YWxkTrZu0gW"
        parts: list[bytes] = []
        for name, value in payload.items():
            parts.append(f"--{boundary}\r\n".encode("utf-8"))
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            parts.append(str(value).encode("utf-8"))
            parts.append(b"\r\n")
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(
            (
                f'Content-Disposition: form-data; name="document"; filename="{file_path.name}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(file_path.read_bytes())
        parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        request = urllib.request.Request(
            url,
            data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API HTTP {exc.code}: {details[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Telegram API недоступен: {exc.reason}") from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Telegram API вернул не-JSON ответ") from exc
    if not parsed.get("ok"):
        raise RuntimeError(f"Telegram API отклонил запрос: {parsed.get('description', 'без описания')}")


def send_message(text: str) -> None:
    _, chat_id = telegram_credentials()
    for chunk in split_message(text):
        telegram_api("sendMessage", {"chat_id": chat_id, "text": chunk})


def send_document(path: Path, caption: str) -> None:
    _, chat_id = telegram_credentials()
    telegram_api(
        "sendDocument",
        {"chat_id": chat_id, "caption": truncate(caption, 900)},
        file_path=path,
    )


def deliver(reminder: Reminder) -> None:
    send_message(reminder.text)
    if reminder.brief_path is not None:
        send_document(reminder.brief_path, f"Бриф: {reminder.brief_path.name}")


def send_test(now: datetime) -> None:
    text = (
        "Тест напоминаний PVL.\n"
        f"Время: {now.strftime('%Y-%m-%d %H:%M')} ({now.tzinfo})\n"
        "Канал Telegram настроен."
    )
    send_message(text)
    print("Тестовое напоминание отправлено в Telegram.")


def digest_summary_line(today: date | None = None) -> str:
    cards, _, steps = collect_data()
    today = today or datetime.now(TZ).date()
    timed_count = sum(
        1 for step in steps
        if step.day == today and exact_minutes(step.task) is not None
    )
    warm_without_brief = [
        clean_text(card.company or card.name)
        for card in cards
        if not card.closed
        and is_warm_status(card.status)
        and find_brief_file(card) is None
    ]
    warm_text = ", ".join(warm_without_brief) if warm_without_brief else "нет"
    return (
        f"Напоминания: сегодня звонков с точным временем: {timed_count}; "
        f"тёплые без брифа: {warm_text}."
    )


def parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.now(TZ)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise SystemExit("Формат --at: ГГГГ-ММ-ДД ЧЧ:ММ") from exc
    return parsed.replace(tzinfo=TZ)


def run_cycle(now: datetime | None = None, dry_run: bool = False) -> list[str]:
    """Один цикл напоминаний. Возвращает ключи отправленных напоминаний."""
    now = now or datetime.now(TZ)
    cards, cards_by_name, steps = collect_data()
    due = find_due(now, steps, cards_by_name)

    state = prune_state(load_state(), now.date())
    fresh = [item for item in due if item.key not in state]
    sent: list[str] = []

    if not fresh:
        mode = "DRY-RUN" if dry_run else "LIVE"
        print(f"[{mode}] {now.strftime('%Y-%m-%d %H:%M')} Europe/Minsk: новых напоминаний нет.")
        if not dry_run:
            save_state(state)
        return sent

    for reminder in fresh:
        if dry_run:
            print(f"\n[DRY-RUN {reminder.window}] {reminder.key}")
            print(reminder.text)
            if reminder.brief_path is not None:
                print(f"[вложение] {reminder.brief_path}")
            else:
                print("[вложение] брифа нет")
            continue

        try:
            deliver(reminder)
        except Exception as exc:
            logger.error("Не отправлено %s: %s", reminder.key, exc)
            continue
        state[reminder.key] = datetime.now(TZ).isoformat(timespec="seconds")
        save_state(state)
        sent.append(reminder.key)
        print(f"[SENT {reminder.window}] {reminder.key}")

    if dry_run:
        print(f"\n[DRY-RUN] всего сработало бы: {len(fresh)}")
    else:
        save_state(state)
    return sent


def main() -> None:
    parser = argparse.ArgumentParser(description="Точные Telegram-напоминания о звонках PVL")
    parser.add_argument("--dry-run", action="store_true", help="Показать напоминания без отправки")
    parser.add_argument("--at", help="Симуляция времени: ГГГГ-ММ-ДД ЧЧ:ММ")
    parser.add_argument("--send-test", action="store_true", help="Отправить одно тестовое сообщение")
    args = parser.parse_args()

    now = parse_now(args.at)

    if args.send_test:
        send_test(now)
        return

    run_cycle(now=now, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
