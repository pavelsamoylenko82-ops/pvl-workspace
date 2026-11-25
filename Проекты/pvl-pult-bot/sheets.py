"""Прямая работа с Google Sheets SAMOLA CRM (только Sheets API, без Drive API)."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1

logger = logging.getLogger("pvl-pult-bot.sheets")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
LEADS_SHEET = "Лиды"
DEALS_SHEET = "Дела"
HISTORY_SHEET = "История"

LEADS_HEADERS = [
    "ID",
    "Компания",
    "Город",
    "Статус",
    "Телефон",
    "Контакт / Канал",
    "Услуга / Фокус",
    "Чек ($)",
    "След. дата",
    "Время",
    "Следующий шаг",
    "Ссылка на демо / КП",
    "Бриф / Заметки",
]
DEALS_HEADERS = ["ID", "Задача", "Приоритет", "Дедлайн", "Где делать", "Статус", "Заметки"]
HISTORY_HEADERS = ["Дата", "Тип", "Клиент / Задача", "Результат"]

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d.%m.%y",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
)
_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d.%m.%Y %H:%M",
)
_TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%H.%M")

_SHEETS_EPOCH = date(1899, 12, 30)

_UNSET = object()


class SheetError(RuntimeError):
    """Понятная человеку ошибка чтения или записи Google Sheets."""


def _cell(row: list[str], index: int) -> str:
    if index < 0 or index >= len(row):
        return ""
    return str(row[index]).strip()


def _column_letter(index: int) -> str:
    """0-based индекс колонки -> буква A1 (0 -> A, 26 -> AA)."""
    letters = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _retry(action, *, attempts: int = 3, delay: float = 1.0):
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except Exception as exc:  # сеть и квоты Google иногда отвечают ошибкой
            last = exc
            logger.warning("Google Sheets, попытка %s/%s: %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(delay * attempt)
    raise SheetError(f"Google Sheets недоступен: {last}") from last


def parse_date(value: Any) -> date | None:
    """Понимает ISO, dd.mm.yyyy, дату-время и числовой serial Google Sheets."""
    text = _clean(value)
    if not text or text in {"-", "—", "–"}:
        return None

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass

    head = text.split("T", 1)[0].split(" ", 1)[0]
    if head != text:
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(head, fmt).date()
            except ValueError:
                pass

    try:
        serial = float(text.replace(",", "."))
    except ValueError:
        return None
    if 1 <= serial <= 200000:
        return _SHEETS_EPOCH + timedelta(days=int(serial))
    return None


def parse_time(value: Any) -> dtime | None:
    text = _clean(value)
    if not text or text in {"-", "—", "–"}:
        return None

    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            pass
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            pass

    try:
        fraction = float(text.replace(",", "."))
    except ValueError:
        return None
    if 0 <= fraction < 1:
        minutes = round(fraction * 24 * 60)
        return dtime(hour=minutes // 60 % 24, minute=minutes % 60)
    if 1 <= fraction <= 200000:
        # Время, сохранённое как доля суток поверх serial-даты.
        fraction = fraction - int(fraction)
        minutes = round(fraction * 24 * 60)
        return dtime(hour=minutes // 60 % 24, minute=minutes % 60)
    return None


@dataclass
class Lead:
    row: int
    id: int
    company: str
    city: str
    status: str
    phone: str
    contact: str
    service: str
    budget: str
    next_date: date | None
    next_time: dtime | None
    next_step: str
    link: str
    notes: str

    @property
    def next_dt(self) -> datetime | None:
        if self.next_date is None:
            return None
        return datetime.combine(self.next_date, self.next_time or dtime(10, 0))

    @property
    def title(self) -> str:
        parts = [self.company or f"Лид {self.id}"]
        if self.city:
            parts.append(f"({self.city})")
        if self.status:
            parts.append(f"- {self.status}")
        return " ".join(parts)


@dataclass
class Deal:
    row: int
    id: int
    task: str
    priority: str
    deadline: date | None
    place: str
    status: str
    notes: str

    @property
    def title(self) -> str:
        return self.task or f"Дело {self.id}"


@dataclass
class HistoryEntry:
    row: int
    date: date | None
    kind: str
    client: str
    result: str


@dataclass
class CrmData:
    leads: list[Lead] = field(default_factory=list)
    deals: list[Deal] = field(default_factory=list)
    history: list[HistoryEntry] = field(default_factory=list)
    fetched_at: datetime | None = None


class CrmSheets:
    """Тонкая обёртка над gspread: читает вкладки и точечно пишет изменения."""

    def __init__(self, creds_path: Path, spreadsheet_id: str) -> None:
        self.creds_path = Path(creds_path)
        self.spreadsheet_id = spreadsheet_id.strip()
        self._client: gspread.Client | None = None
        self._spreadsheet: gspread.Spreadsheet | None = None
        self._worksheets: dict[str, gspread.Worksheet] = {}
        self._layouts: dict[str, dict[str, int]] = {}
        self._write_lock = threading.RLock()

    # --- подключение -----------------------------------------------------
    def _connect(self) -> gspread.Spreadsheet:
        if self._spreadsheet is not None:
            return self._spreadsheet
        if not self.creds_path.exists():
            raise SheetError(f"Не найден ключ service account: {self.creds_path}")
        if not self.spreadsheet_id:
            raise SheetError("Не задан SPREADSHEET_ID")

        credentials = Credentials.from_service_account_file(
            str(self.creds_path), scopes=SCOPES
        )
        self._client = gspread.authorize(credentials)
        # Строго open_by_key: Google Drive API не нужен.
        self._spreadsheet = _retry(
            lambda: self._client.open_by_key(self.spreadsheet_id)
        )
        logger.info("Подключено к таблице: %s", self._spreadsheet.title)
        return self._spreadsheet

    def worksheet(self, name: str) -> gspread.Worksheet:
        cached = self._worksheets.get(name)
        if cached is not None:
            return cached
        spreadsheet = self._connect()
        sheet = _retry(lambda: spreadsheet.worksheet(name))
        self._worksheets[name] = sheet
        return sheet

    def ping(self) -> str:
        return self._connect().title

    # --- чтение ----------------------------------------------------------
    @staticmethod
    def _columns(header: list[str], expected: list[str]) -> dict[str, int]:
        mapping: dict[str, int] = {}
        normalized = [_clean(cell) for cell in header]
        for position, name in enumerate(expected):
            if name in normalized:
                mapping[name] = normalized.index(name)
            else:
                mapping[name] = position
        return mapping

    def _values(self, sheet_name: str) -> list[list[str]]:
        sheet = self.worksheet(sheet_name)
        return _retry(lambda: sheet.get_all_values())

    def read_leads(self) -> list[Lead]:
        values = self._values(LEADS_SHEET)
        if not values:
            return []
        cols = self._columns(values[0], LEADS_HEADERS)
        leads: list[Lead] = []
        for row_number, row in enumerate(values[1:], start=2):
            raw_id = _cell(row, cols["ID"])
            if not raw_id:
                # Пустая строка закрывает таблицу: ниже идёт сводка с формулами.
                if leads:
                    break
                continue
            if not raw_id.isdigit():
                break
            leads.append(
                Lead(
                    row=row_number,
                    id=int(raw_id),
                    company=_cell(row, cols["Компания"]),
                    city=_cell(row, cols["Город"]),
                    status=_cell(row, cols["Статус"]),
                    phone=_cell(row, cols["Телефон"]),
                    contact=_cell(row, cols["Контакт / Канал"]),
                    service=_cell(row, cols["Услуга / Фокус"]),
                    budget=_cell(row, cols["Чек ($)"]),
                    next_date=parse_date(_cell(row, cols["След. дата"])),
                    next_time=parse_time(_cell(row, cols["Время"])),
                    next_step=_cell(row, cols["Следующий шаг"]),
                    link=_cell(row, cols["Ссылка на демо / КП"]),
                    notes=_cell(row, cols["Бриф / Заметки"]),
                )
            )
        return leads

    def read_deals(self) -> list[Deal]:
        values = self._values(DEALS_SHEET)
        if not values:
            return []
        cols = self._columns(values[0], DEALS_HEADERS)
        deals: list[Deal] = []
        for row_number, row in enumerate(values[1:], start=2):
            raw_id = _cell(row, cols["ID"])
            if not raw_id:
                if deals:
                    break
                continue
            if not raw_id.isdigit():
                break
            deals.append(
                Deal(
                    row=row_number,
                    id=int(raw_id),
                    task=_cell(row, cols["Задача"]),
                    priority=_cell(row, cols["Приоритет"]),
                    deadline=parse_date(_cell(row, cols["Дедлайн"])),
                    place=_cell(row, cols["Где делать"]),
                    status=_cell(row, cols["Статус"]),
                    notes=_cell(row, cols["Заметки"]),
                )
            )
        return deals

    def read_history(self) -> list[HistoryEntry]:
        values = self._values(HISTORY_SHEET)
        if not values:
            return []
        cols = self._columns(values[0], HISTORY_HEADERS)
        history: list[HistoryEntry] = []
        for row_number, row in enumerate(values[1:], start=2):
            if not any(_cell(row, cols[name]) for name in HISTORY_HEADERS):
                continue
            history.append(
                HistoryEntry(
                    row=row_number,
                    date=parse_date(_cell(row, cols["Дата"])),
                    kind=_cell(row, cols["Тип"]),
                    client=_cell(row, cols["Клиент / Задача"]),
                    result=_cell(row, cols["Результат"]),
                )
            )
        return history

    def load(self, timezone: str = "Europe/Minsk") -> CrmData:
        return CrmData(
            leads=self.read_leads(),
            deals=self.read_deals(),
            history=self.read_history(),
            fetched_at=datetime.now(ZoneInfo(timezone)),
        )

    # --- поиск строк ------------------------------------------------------
    def _row_by_id(self, sheet_name: str, item_id: int) -> int:
        sheet = self.worksheet(sheet_name)
        values = _retry(lambda: sheet.col_values(1))
        best: int | None = None
        for index, raw in enumerate(values[1:], start=2):
            text = _clean(raw)
            if text.isdigit() and int(text) == item_id:
                best = index
                break
        if best is None:
            raise SheetError(f"Строка с ID {item_id} не найдена на вкладке {sheet_name}")
        return best

    def _append_history(self, rows: Iterable[list[str]]) -> None:
        sheet = self.worksheet(HISTORY_SHEET)
        for row in rows:
            _retry(
                lambda row=row: sheet.append_row(
                    row, value_input_option="RAW", table_range="A1"
                )
            )

    # --- запись ----------------------------------------------------------
    def _layout(self, sheet_name: str, expected: list[str]) -> dict[str, int]:
        """Индексы колонок по фактической шапке вкладки."""
        cached = self._layouts.get(sheet_name)
        if cached is not None:
            return cached
        sheet = self.worksheet(sheet_name)
        header = _retry(lambda: sheet.row_values(1))
        layout = self._columns(header, expected)
        self._layouts[sheet_name] = layout
        return layout

    def _batch_cells(
        self, sheet_name: str, updates: list[tuple[int, int, str]]
    ) -> None:
        if not updates:
            return
        sheet = self.worksheet(sheet_name)
        payload = [
            {
                "range": rowcol_to_a1(row, column),
                "values": [[value]],
            }
            for row, column, value in updates
        ]
        _retry(
            lambda: sheet.batch_update(payload, value_input_option="RAW")
        )

    @staticmethod
    def _merge_notes(existing: str, addition: str) -> str:
        addition = _clean(addition)
        if not addition:
            return existing
        base = _clean(existing)
        if not base or base in {"-", "нет"}:
            return addition
        if addition in base:
            return base
        return f"{base}; {addition}"

    def update_lead(
        self,
        lead: Lead,
        *,
        status: object = _UNSET,
        next_date: object = _UNSET,
        next_time: object = _UNSET,
        next_step: object = _UNSET,
        note_append: str = "",
        today: date | None = None,
        source: str = "ИИ",
    ) -> tuple[Lead, list[str]]:
        """Точечно обновляет поля лида и дублирует шаг строкой в Историю."""
        today = today or date.today()
        cols = self._layout(LEADS_SHEET, LEADS_HEADERS)
        updates: list[tuple[int, int, str]] = []
        changes: list[str] = []

        if status is not _UNSET:
            value = _clean(status)
            updates.append((lead.row, cols["Статус"], value))
            changes.append(f"статус: {value}")
            lead.status = value
        if next_date is not _UNSET:
            value = next_date.isoformat() if next_date else ""
            updates.append((lead.row, cols["След. дата"], value))
            changes.append(
                f"след. дата: {next_date:%d.%m}" if next_date else "след. дата: очищена"
            )
            lead.next_date = next_date
        if next_time is not _UNSET:
            value = next_time.strftime("%H:%M") if next_time else ""
            updates.append((lead.row, cols["Время"], value))
            changes.append(f"время: {value}" if value else "время: очищено")
            lead.next_time = next_time
        if next_step is not _UNSET:
            value = _clean(next_step)
            updates.append((lead.row, cols["Следующий шаг"], value))
            changes.append(f"шаг: {value}")
            lead.next_step = value
        if note_append:
            merged = self._merge_notes(lead.notes, note_append)
            if merged != lead.notes:
                updates.append((lead.row, cols["Бриф / Заметки"], merged))
                changes.append(f"заметка: {_clean(note_append)}")
                lead.notes = merged

        if not updates:
            return lead, []

        with self._write_lock:
            row = self._row_by_id(LEADS_SHEET, lead.id)
            if row != lead.row:
                updates = [(row, column, value) for _, column, value in updates]
                lead.row = row
            self._batch_cells(LEADS_SHEET, updates)
            client = lead.company + (f" ({lead.city})" if lead.city else "")
            result = ("Обновлено" + (f" ({source})" if source else "") + ": ") + "; ".join(
                changes
            )
            self._append_history(
                [[today.isoformat(), "Лид", client, result[:900]]]
            )
        return lead, changes

    def update_deal(
        self,
        deal: Deal,
        *,
        status: object = _UNSET,
        deadline: object = _UNSET,
        note_append: str = "",
        today: date | None = None,
        source: str = "ИИ",
    ) -> tuple[Deal, list[str]]:
        """Точечно обновляет дело и дублирует изменение строкой в Историю."""
        today = today or date.today()
        cols = self._layout(DEALS_SHEET, DEALS_HEADERS)
        updates: list[tuple[int, int, str]] = []
        changes: list[str] = []

        if status is not _UNSET:
            value = _clean(status)
            updates.append((deal.row, cols["Статус"], value))
            changes.append(f"статус: {value}")
            deal.status = value
        if deadline is not _UNSET:
            value = deadline.isoformat() if deadline else ""
            updates.append((deal.row, cols["Дедлайн"], value))
            changes.append(
                f"дедлайн: {deadline:%d.%m}" if deadline else "дедлайн: очищен"
            )
            deal.deadline = deadline
        if note_append:
            merged = self._merge_notes(deal.notes, note_append)
            if merged != deal.notes:
                updates.append((deal.row, cols["Заметки"], merged))
                changes.append(f"заметка: {_clean(note_append)}")
                deal.notes = merged

        if not updates:
            return deal, []

        with self._write_lock:
            row = self._row_by_id(DEALS_SHEET, deal.id)
            if row != deal.row:
                updates = [(row, column, value) for _, column, value in updates]
                deal.row = row
            self._batch_cells(DEALS_SHEET, updates)
            self._append_history(
                [
                    [
                        today.isoformat(),
                        "Дело",
                        deal.task,
                        ("Обновлено" + (f" ({source})" if source else "") + ": ")
                        + "; ".join(changes),
                    ]
                ]
            )
        return deal, changes

    def add_task(
        self,
        *,
        task: str,
        priority: str = "",
        deadline: date | None = None,
        place: str = "",
        status: str = "В работе",
        note: str = "",
        today: date | None = None,
        source: str = "ИИ",
    ) -> tuple[Deal, list[str]]:
        """Добавляет дело в конец таблицы, не задевая строки ниже."""
        today = today or date.today()
        title = _clean(task)
        if not title:
            raise SheetError("Нельзя добавить дело без текста задачи")

        cols = self._layout(DEALS_SHEET, DEALS_HEADERS)
        with self._write_lock:
            existing = self.read_deals()
            new_id = max((deal.id for deal in existing), default=0) + 1
            last_row = max((deal.row for deal in existing), default=1)
            target = last_row + 1

            sheet = self.worksheet(DEALS_SHEET)
            width = max(cols.values()) + 1
            row_values = [""] * width
            row_values[cols["ID"]] = str(new_id)
            row_values[cols["Задача"]] = title
            row_values[cols["Приоритет"]] = _clean(priority)
            row_values[cols["Дедлайн"]] = (
                deadline.isoformat() if deadline else ""
            )
            row_values[cols["Где делать"]] = _clean(place)
            row_values[cols["Статус"]] = _clean(status) or "В работе"
            row_values[cols["Заметки"]] = _clean(note)

            occupied = _retry(lambda: sheet.row_values(target))
            if any(str(cell).strip() for cell in occupied):
                # Ниже есть данные (например, сводка): вставляем строку, не стирая её.
                _retry(
                    lambda: sheet.insert_row(
                        row_values, target, value_input_option="RAW"
                    )
                )
            else:
                end_col = _column_letter(width)
                _retry(
                    lambda: sheet.update(
                        range_name=f"A{target}:{end_col}{target}",
                        values=[row_values],
                        value_input_option="RAW",
                    )
                )
            self._append_history(
                [
                    [
                        today.isoformat(),
                        "Дело",
                        title,
                        ("Добавлено" + (f" ({source})" if source else "") + ": ")
                        + (f"дедлайн {deadline:%d.%m}" if deadline else "без дедлайна"),
                    ]
                ]
            )
        deal = Deal(
            row=target,
            id=new_id,
            task=title,
            priority=_clean(priority),
            deadline=deadline,
            place=_clean(place),
            status=_clean(status) or "В работе",
            notes=_clean(note),
        )
        return deal, [f"добавлено дело ID {new_id}"]

    def mark_lead_done(
        self,
        lead: Lead,
        today: date | None = None,
        reason: str = "Выполнен шаг",
    ) -> Lead:
        """Пишет шаг в Историю и очищает След. дата/Время, не удаляя строк."""
        today = today or date.today()
        with self._write_lock:
            row = self._row_by_id(LEADS_SHEET, lead.id)
            step = lead.next_step or lead.service or lead.status or "касание"
            result = f"{reason}: {step}"
            client = lead.company + (f" ({lead.city})" if lead.city else "")
            self._append_history([[today.isoformat(), "Лид", client, result[:900]]])

            sheet = self.worksheet(LEADS_SHEET)
            _retry(
                lambda: sheet.batch_update(
                    [
                        {
                            "range": f"I{row}:J{row}",
                            "values": [["", ""]],
                        }
                    ],
                    value_input_option="RAW",
                )
            )

        lead.next_date = None
        lead.next_time = None
        return lead

    def mark_deal_done(self, deal: Deal, today: date | None = None) -> Deal:
        today = today or date.today()
        with self._write_lock:
            row = self._row_by_id(DEALS_SHEET, deal.id)
            sheet = self.worksheet(DEALS_SHEET)
            _retry(
                lambda: sheet.update(
                    range_name=f"F{row}",
                    values=[["Выполнено"]],
                    value_input_option="RAW",
                )
            )
            self._append_history(
                [[today.isoformat(), "Дело", deal.task, "Выполнено"]]
            )

        deal.status = "Выполнено"
        return deal

    def shift_lead_date(
        self, lead: Lead, days: int = 1, today: date | None = None
    ) -> Lead:
        base = lead.next_date or today or date.today()
        new_date = base + timedelta(days=days)
        with self._write_lock:
            row = self._row_by_id(LEADS_SHEET, lead.id)
            sheet = self.worksheet(LEADS_SHEET)
            _retry(
                lambda: sheet.update(
                    range_name=f"I{row}",
                    values=[[new_date.isoformat()]],
                    value_input_option="RAW",
                )
            )
        lead.next_date = new_date
        return lead

    def shift_deal_date(
        self, deal: Deal, days: int = 1, today: date | None = None
    ) -> Deal:
        base = deal.deadline or today or date.today()
        new_date = base + timedelta(days=days)
        with self._write_lock:
            row = self._row_by_id(DEALS_SHEET, deal.id)
            sheet = self.worksheet(DEALS_SHEET)
            _retry(
                lambda: sheet.update(
                    range_name=f"D{row}",
                    values=[[new_date.isoformat()]],
                    value_input_option="RAW",
                )
            )
        deal.deadline = new_date
        return deal

    # --- бэкап -----------------------------------------------------------
    def snapshot(self, timezone: str = "Europe/Minsk") -> dict[str, Any]:
        sheets: dict[str, Any] = {}
        for name in (LEADS_SHEET, DEALS_SHEET, HISTORY_SHEET):
            values = self._values(name)
            sheets[name] = {
                "headers": values[0] if values else [],
                "rows": values[1:] if values else [],
            }
        return {
            "spreadsheet_id": self.spreadsheet_id,
            "exported_at": datetime.now(ZoneInfo(timezone)).isoformat(),
            "sheets": sheets,
        }


def write_backup(
    crm: CrmSheets,
    backups_dir: Path,
    *,
    keep: int = 14,
    timezone: str = "Europe/Minsk",
    today: date | None = None,
) -> Path:
    """Сохраняет JSON-снимок всех 3 вкладок и оставляет последние keep файлов."""
    today = today or datetime.now(ZoneInfo(timezone)).date()
    destination = Path(backups_dir)
    _retry(lambda: destination.mkdir(parents=True, exist_ok=True))

    snapshot = crm.snapshot(timezone)
    path = destination / f"samola-crm-{today.isoformat()}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)

    files = sorted(destination.glob("samola-crm-*.json"), key=lambda p: p.name)
    for old in files[:-keep]:
        try:
            old.unlink()
        except OSError:
            logger.warning("Не удалось удалить старый бэкап: %s", old)
    return path
