from __future__ import annotations

import csv
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from models import Lead

XLSX_HEADERS = [
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

CALL_STATUSES = [
    "Новый",
    "Не дозвонился",
    "Отправил демо",
    "Думает",
    "Созвон",
    "Отказ",
    "Сделка",
]

HEADER_FILL = PatternFill("solid", fgColor="1E293B")
HEADER_FONT = Font(color="FFFFFF", bold=True)
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)

PRIORITY_STYLES = {
    "A": (PatternFill("solid", fgColor="FEE2E2"), Font(bold=True, color="991B1B")),
    "B": (PatternFill("solid", fgColor="FEF3C7"), Font(bold=True, color="92400E")),
    "C": (PatternFill("solid", fgColor="DCFCE7"), Font(color="166534")),
}

YES_NO_STYLES = {
    "Нет": PatternFill("solid", fgColor="FEE2E2"),
    "Да": PatternFill("solid", fgColor="DCFCE7"),
}

MANUAL_FILL = PatternFill("solid", fgColor="F0F9FF")
HYPERLINK_FONT = Font(color="0563C1", underline="single")
THIN_GREY = Side(style="thin", color="CBD5E1")
CELL_BORDER = Border(left=THIN_GREY, right=THIN_GREY, top=THIN_GREY, bottom=THIN_GREY)
DATA_ALIGNMENT = Alignment(vertical="center", wrap_text=True)
HEADER_ROW_HEIGHT = 28
DATA_ROW_HEIGHT = 24

NUMERIC_FORMATS = {
    "Рейтинг": "0.0",
    "Отзывы": "0",
    "HTTP_статус": "0",
    "Время_ответа_сек": "0.00",
}


class StorageError(RuntimeError):
    """Ошибка чтения/записи Excel-базы."""


@dataclass(frozen=True)
class StorageResult:
    added: int
    duplicates: int
    total_rows: int
    priority_a: int
    priority_b: int
    priority_c: int


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


def _as_int(value: object) -> int:
    text = _empty_to(value).strip().replace(" ", "").replace(",", ".")
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _to_number(value: object, column: str) -> Any:
    text = _empty_to(value).strip().replace(",", ".")
    if not text:
        return ""
    if column in {"Отзывы", "HTTP_статус"}:
        try:
            return int(float(text))
        except ValueError:
            return _empty_to(value)
    if column in {"Рейтинг", "Время_ответа_сек"}:
        try:
            return float(text)
        except ValueError:
            return _empty_to(value)
    return _empty_to(value)


def _format_lead(lead: Lead) -> dict[str, Any]:
    return {
        "Название": _empty_to(lead.name),
        "Адрес": _empty_to(lead.address),
        "Телефон": _empty_to(lead.phone),
        "Рейтинг": _to_number(lead.rating, "Рейтинг"),
        "Отзывы": _to_number(lead.reviews, "Отзывы"),
        "Сайт": _empty_to(lead.website),
        "Ссылка_на_карточку": _empty_to(lead.card_url),
        "HTTP_статус": _to_number(lead.http_status, "HTTP_статус"),
        "HTTPS": "" if lead.https is None else ("Да" if lead.https else "Нет"),
        "Viewport": "" if lead.viewport is None else ("Да" if lead.viewport else "Нет"),
        "Время_ответа_сек": _to_number(lead.response_time, "Время_ответа_сек"),
        "Priority": _empty_to(lead.priority),
        "Статус_звонка": "",
        "Заметки": "",
        "Дата_парсинга": _empty_to(lead.parse_date),
    }


def _read_csv_rows(csv_path: Path) -> list[dict[str, Any]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            raise StorageError(f"CSV-файл пустой или без заголовка: {csv_path}")
        missing = [column for column in XLSX_HEADERS if column not in reader.fieldnames]
        if missing:
            raise StorageError(
                f"В {csv_path} нет обязательных колонок: {', '.join(missing)}"
            )
        rows: list[dict[str, Any]] = []
        for source in reader:
            row = {column: _empty_to(source.get(column, "")) for column in XLSX_HEADERS}
            rows.append(_prepare_loaded_row(row))
        return rows


def _read_xlsx_rows(xlsx_path: Path) -> list[dict[str, Any]]:
    workbook = load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        worksheet = workbook.active
        values = worksheet.iter_rows(values_only=True)
        try:
            header_values = next(values)
        except StopIteration as exc:
            raise StorageError(f"Excel-файл пустой или без заголовка: {xlsx_path}") from exc

        headers = [_empty_to(value).strip() for value in header_values]
        missing = [column for column in XLSX_HEADERS if column not in headers]
        if missing:
            raise StorageError(
                f"В {xlsx_path} нет обязательных колонок: {', '.join(missing)}"
            )

        index_by_header = {header: index for index, header in enumerate(headers)}
        rows: list[dict[str, Any]] = []
        for source in values:
            if source is None or all(value is None for value in source):
                continue
            row = {
                column: _empty_to(
                    source[index_by_header[column]]
                    if index_by_header[column] < len(source)
                    else ""
                )
                for column in XLSX_HEADERS
            }
            rows.append(_prepare_loaded_row(row))
        return rows
    finally:
        workbook.close()


def _prepare_loaded_row(row: dict[str, Any]) -> dict[str, Any]:
    prepared: dict[str, Any] = {}
    for column in XLSX_HEADERS:
        value = row.get(column, "")
        if column in NUMERIC_FORMATS:
            prepared[column] = _to_number(value, column)
        else:
            prepared[column] = _empty_to(value)
    return prepared


def _load_or_migrate(xlsx_path: Path) -> list[dict[str, Any]]:
    if xlsx_path.exists() and xlsx_path.stat().st_size > 0:
        return _read_xlsx_rows(xlsx_path)

    legacy_csv_path = xlsx_path.with_suffix(".csv")
    if legacy_csv_path.exists() and legacy_csv_path.stat().st_size > 0:
        rows = _read_csv_rows(legacy_csv_path)
        rows = _sorted_rows(rows)
        _write_workbook(xlsx_path, rows)
        try:
            legacy_csv_path.unlink()
        except OSError as exc:
            print(
                f"ВНИМАНИЕ: Excel создан, но старый CSV не удалось удалить: "
                f"{legacy_csv_path}: {exc}"
            )
        return rows

    _write_workbook(xlsx_path, [])
    return []


def _priority_rank(value: object) -> int:
    return {"A": 0, "B": 1, "C": 2}.get(_empty_to(value).strip().upper(), 3)


def _sorted_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (_priority_rank(row.get("Priority")), -_as_int(row.get("Отзывы"))),
    )


def _write_workbook(xlsx_path: Path, rows: list[dict[str, Any]]) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Лиды"

    for column_index, header in enumerate(XLSX_HEADERS, start=1):
        worksheet.cell(row=1, column=column_index, value=header)

    for row_index, row in enumerate(rows, start=2):
        for column_index, header in enumerate(XLSX_HEADERS, start=1):
            worksheet.cell(row=row_index, column=column_index, value=row.get(header, ""))

    _apply_formatting(worksheet, len(rows))

    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = xlsx_path.with_name(f".{xlsx_path.name}.tmp")
    try:
        workbook.save(temporary_path)
        os.replace(temporary_path, xlsx_path)
    finally:
        workbook.close()
        if temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _is_web_url(value: str) -> bool:
    return value.strip().lower().startswith(("http://", "https://"))


def _apply_formatting(worksheet, row_count: int) -> None:
    last_column = len(XLSX_HEADERS)
    last_column_letter = get_column_letter(last_column)
    last_row = max(row_count + 1, 1)

    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = f"A1:{last_column_letter}{last_row}"
    worksheet.row_dimensions[1].height = HEADER_ROW_HEIGHT

    for column_index in range(1, last_column + 1):
        cell = worksheet.cell(row=1, column=column_index)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGNMENT
        cell.border = CELL_BORDER

    for row_index in range(2, row_count + 2):
        worksheet.row_dimensions[row_index].height = DATA_ROW_HEIGHT
        for column_index, header in enumerate(XLSX_HEADERS, start=1):
            cell = worksheet.cell(row=row_index, column=column_index)
            cell.border = CELL_BORDER
            cell.alignment = DATA_ALIGNMENT
            if header in NUMERIC_FORMATS:
                cell.number_format = NUMERIC_FORMATS[header]

        priority_cell = worksheet.cell(row=row_index, column=XLSX_HEADERS.index("Priority") + 1)
        priority_value = _empty_to(priority_cell.value).strip().upper()
        if priority_value in PRIORITY_STYLES:
            priority_fill, priority_font = PRIORITY_STYLES[priority_value]
            priority_cell.fill = priority_fill
            priority_cell.font = priority_font

        for header in ("HTTPS", "Viewport"):
            cell = worksheet.cell(row=row_index, column=XLSX_HEADERS.index(header) + 1)
            fill = YES_NO_STYLES.get(_empty_to(cell.value).strip())
            if fill is not None:
                cell.fill = fill

        for header in ("Статус_звонка", "Заметки"):
            cell = worksheet.cell(row=row_index, column=XLSX_HEADERS.index(header) + 1)
            cell.fill = MANUAL_FILL

        for header in ("Сайт", "Ссылка_на_карточку"):
            cell = worksheet.cell(row=row_index, column=XLSX_HEADERS.index(header) + 1)
            value = _empty_to(cell.value).strip()
            if value and _is_web_url(value):
                cell.hyperlink = value
                cell.font = HYPERLINK_FONT

    status_column_letter = get_column_letter(XLSX_HEADERS.index("Статус_звонка") + 1)
    validation = DataValidation(
        type="list",
        formula1=f'"{",".join(CALL_STATUSES)}"',
        allow_blank=True,
    )
    validation.error = "Выберите значение из выпадающего списка."
    validation.errorTitle = "Недопустимый статус"
    validation.prompt = "Выберите статус звонка."
    validation.promptTitle = "Статус звонка"
    worksheet.add_data_validation(validation)
    validation.add(f"{status_column_letter}2:{status_column_letter}{last_row}")

    for column_index, header in enumerate(XLSX_HEADERS, start=1):
        max_length = len(header)
        for row_index in range(2, row_count + 2):
            value = worksheet.cell(row=row_index, column=column_index).value
            text = _empty_to(value)
            max_length = max(max_length, *(len(line) for line in text.splitlines() or [""]))
        cap = 35 if header in {"Адрес", "Заметки"} else 45
        width = min(max(10, max_length + 2), cap)
        worksheet.column_dimensions[get_column_letter(column_index)].width = width


def append_leads(xlsx_path: Path, leads: Iterable[Lead]) -> StorageResult:
    xlsx_path = xlsx_path.resolve()
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)

    existing_rows = _load_or_migrate(xlsx_path)

    phone_keys: set[str] = set()
    name_address_keys: set[str] = set()
    for row in existing_rows:
        phone_key = normalize_phone(_empty_to(row.get("Телефон")))
        if phone_key:
            phone_keys.add(phone_key)
        composite_key = _name_address_key(
            _empty_to(row.get("Название")),
            _empty_to(row.get("Адрес")),
        )
        if composite_key:
            name_address_keys.add(composite_key)

    new_rows: list[dict[str, Any]] = []
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

    all_rows = _sorted_rows([*existing_rows, *new_rows])
    _write_workbook(xlsx_path, all_rows)

    return StorageResult(
        added=len(new_rows),
        duplicates=duplicates,
        total_rows=len(all_rows),
        priority_a=sum(1 for row in all_rows if _priority_rank(row.get("Priority")) == 0),
        priority_b=sum(1 for row in all_rows if _priority_rank(row.get("Priority")) == 1),
        priority_c=sum(1 for row in all_rows if _priority_rank(row.get("Priority")) == 2),
    )
