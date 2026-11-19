from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from models import Lead

CSV_HEADERS = [
    "Название",
    "Адрес",
    "Телефон",
    "Рейтинг",
    "Отзывы",
    "Сайт",
    "Ссылка_на_карточку",
    "HTTP_статус",
    "HTTPS",
    "Viewport",
    "Время_ответа_сек",
    "Priority",
    "Статус_звонка",
    "Заметки",
    "Дата_парсинга",
]


class StorageError(RuntimeError):
    """Ошибка чтения/записи CSV."""


@dataclass(frozen=True)
class StorageResult:
    added: int
    duplicates: int
    total_rows: int


def normalize_phone(value: str) -> str:
    """Оставляет цифры и последние 9 цифр: +375 29... и 8 029... совпадут."""
    digits = re.sub(r"\D", "", value or "")
    return digits[-9:] if len(digits) >= 9 else digits


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я]+", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _name_address_key(name: str, address: str) -> str:
    normalized_name = normalize_text(name)
    normalized_address = normalize_text(address)
    if not normalized_name or not normalized_address:
        return ""
    return f"{normalized_name}|{normalized_address}"


def _empty_to(value: object) -> str:
    return "" if value is None else str(value)


def _format_lead(lead: Lead) -> dict[str, str]:
    return {
        "Название": _empty_to(lead.name),
        "Адрес": _empty_to(lead.address),
        "Телефон": _empty_to(lead.phone),
        "Рейтинг": _empty_to(lead.rating),
        "Отзывы": _empty_to(lead.reviews),
        "Сайт": _empty_to(lead.website),
        "Ссылка_на_карточку": _empty_to(lead.card_url),
        "HTTP_статус": _empty_to(lead.http_status),
        "HTTPS": "" if lead.https is None else ("Да" if lead.https else "Нет"),
        "Viewport": "" if lead.viewport is None else ("Да" if lead.viewport else "Нет"),
        "Время_ответа_сек": "" if lead.response_time is None else f"{lead.response_time:.2f}",
        "Priority": _empty_to(lead.priority),
        "Статус_звонка": "",
        "Заметки": "",
        "Дата_парсинга": _empty_to(lead.parse_date),
    }


def append_leads(csv_path: Path, leads: Iterable[Lead]) -> StorageResult:
    csv_path = csv_path.resolve()
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    existing_rows: list[dict[str, str]] = []
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0

    if file_exists:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            if not reader.fieldnames:
                raise StorageError(f"CSV-файл пустой или без заголовка: {csv_path}")
            missing = [column for column in CSV_HEADERS if column not in reader.fieldnames]
            if missing:
                raise StorageError(
                    f"В {csv_path} нет обязательных колонок: {', '.join(missing)}"
                )
            existing_rows = list(reader)

    phone_keys: set[str] = set()
    name_address_keys: set[str] = set()
    for row in existing_rows:
        phone_key = normalize_phone(row.get("Телефон", ""))
        if phone_key:
            phone_keys.add(phone_key)
        composite_key = _name_address_key(row.get("Название", ""), row.get("Адрес", ""))
        if composite_key:
            name_address_keys.add(composite_key)

    new_rows: list[dict[str, str]] = []
    duplicates = 0
    for lead in leads:
        phone_key = normalize_phone(lead.phone)
        composite_key = _name_address_key(lead.name, lead.address)
        is_duplicate = bool(
            (phone_key and phone_key in phone_keys)
            or (composite_key and composite_key in name_address_keys)
        )
        if is_duplicate:
            duplicates += 1
            continue

        new_rows.append(_format_lead(lead))
        if phone_key:
            phone_keys.add(phone_key)
        if composite_key:
            name_address_keys.add(composite_key)

    with csv_path.open("a", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_HEADERS, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerows(new_rows)

    return StorageResult(
        added=len(new_rows),
        duplicates=duplicates,
        total_rows=len(existing_rows) + len(new_rows),
    )
