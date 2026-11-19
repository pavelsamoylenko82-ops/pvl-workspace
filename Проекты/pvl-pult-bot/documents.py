from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo


class DocumentError(RuntimeError):
    """Ошибка выбора или изменения документа."""


@dataclass(frozen=True)
class UpdateResult:
    target: str
    step_line: str | None
    journal_line: str | None
    status: str | None
    phone: str | None


def list_card_files(workspace: Path) -> list[Path]:
    cards_dir = workspace / "Карточки"
    if not cards_dir.exists():
        return []
    return sorted((p for p in cards_dir.glob("*.md") if p.is_file()), key=lambda p: p.name.casefold())


def card_names_for_prompt(workspace: Path) -> list[str]:
    return [f"Карточки/{path.name}" for path in list_card_files(workspace)]


def find_cards(workspace: Path, query: str) -> list[Path]:
    needle = query.strip().strip("\"'`").casefold()
    if not needle:
        return []
    cards = list_card_files(workspace)
    exact = [p for p in cards if p.stem.casefold() == needle or p.name.casefold() == needle]
    if exact:
        return exact
    return [p for p in cards if needle in p.stem.casefold() or needle in p.name.casefold()]


def resolve_target(workspace: Path, target: str) -> Path:
    raw = str(target or "").strip().strip("\"'`").replace("\\", "/")
    if raw in {"ДЕЛА.md", "DELA.md"}:
        path = (workspace / "ДЕЛА.md").resolve()
        if not path.exists():
            raise DocumentError("Файл ДЕЛА.md не найден")
        return path

    if raw.startswith("Карточки/"):
        raw = raw.split("/", 1)[1]

    cards = list_card_files(workspace)
    exact = [p for p in cards if p.name.casefold() == raw.casefold() or p.stem.casefold() == raw.casefold()]
    if len(exact) == 1:
        return exact[0]

    matches = find_cards(workspace, raw)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise DocumentError(f"Файл не найден по имени: {target}")
    names = ", ".join(p.name for p in matches[:5])
    raise DocumentError(f"Найдено несколько файлов: {names}. Нужно точное имя.")


def build_file_context(workspace: Path, max_chars: int = 24000) -> str:
    """Контекст для DeepSeek: содержимое карточек и ДЕЛА.md в разумном лимите."""
    parts: list[str] = []
    total = 0
    for path in list_card_files(workspace):
        content = path.read_text(encoding="utf-8")
        block = f"\n### Карточки/{path.name}\n{content.strip()}\n"
        if total + len(block) > max_chars:
            parts.append("\n### ...часть карточек пропущена из-за лимита контекста\n")
            break
        parts.append(block)
        total += len(block)

    dela = workspace / "ДЕЛА.md"
    if dela.exists():
        block = f"\n### ДЕЛА.md\n{dela.read_text(encoding='utf-8').strip()}\n"
        if total + len(block) <= max_chars:
            parts.append(block)
    return "".join(parts).strip()


def _update_frontmatter(text: str, fields: dict[str, str]) -> tuple[str, set[str]]:
    match = re.match(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n)?", text, re.DOTALL)
    if not match:
        return text, set()

    lines = match.group(1).splitlines()
    changed: set[str] = set()

    for key, value in fields.items():
        if not value:
            continue
        line_re = re.compile(rf"^\s*{re.escape(key)}\s*:(.*)$")
        replaced = False
        for index, line in enumerate(lines):
            found = line_re.match(line)
            if found:
                old_value = found.group(1).strip()
                if old_value != value:
                    lines[index] = f"{key}: {value}"
                    changed.add(key)
                replaced = True
                break
        if not replaced:
            lines.append(f"{key}: {value}")
            changed.add(key)

    if not changed:
        return text, set()

    new_text = "---\n" + "\n".join(lines) + "\n---\n" + text[match.end():]
    return new_text, changed


def _insert_under_heading(text: str, heading: str, line: str) -> tuple[str, bool]:
    if line in {row.strip() for row in text.splitlines()}:
        return text, False

    heading_re = re.compile(rf"^##\s*{re.escape(heading)}\s*$", re.MULTILINE)
    match = heading_re.search(text)
    if not match:
        separator = "" if text.endswith("\n") else "\n"
        return f"{text}{separator}\n## {heading}\n{line}\n", True

    newline_at = text.find("\n", match.end())
    if newline_at == -1:
        return f"{text}\n{line}\n", True

    insert_at = newline_at + 1
    # Убираем пустые строки сразу после заголовка, чтобы новая запись была первой.
    while insert_at < len(text) and text[insert_at] in "\r\n":
        insert_at += 1
    return text[: newline_at + 1] + f"{line}\n" + text[insert_at:], True


def _clean_step_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^[-*]\s*", "", text)
    text = re.sub(r"^\[\s*\]\s*", "", text)
    return text.strip()


def _normalize_step(
    step_date: Any,
    step_text: Any,
    tz: ZoneInfo,
) -> tuple[str, str] | None:
    if not step_text:
        return None
    date_raw = str(step_date or datetime.now(tz).date().isoformat()).strip()
    try:
        parsed = date.fromisoformat(date_raw)
    except ValueError as exc:
        raise DocumentError(f"DeepSeek вернул некорректную дату шага: {date_raw}") from exc
    text = _clean_step_text(step_text)
    if not text:
        return None
    return parsed.isoformat(), text


def _normalize_journal(entry: Any, tz: ZoneInfo) -> str | None:
    raw = str(entry or "").strip()
    if not raw:
        return None
    if not re.match(r"^\d{4}-\d{2}-\d{2}\b", raw):
        raw = f"{datetime.now(tz).date().isoformat()}: {raw}"
    return raw if raw.startswith("- ") else f"- {raw}"


def apply_edit(
    workspace: Path,
    edit: dict[str, Any],
    timezone: str = "Europe/Minsk",
) -> UpdateResult:
    """Применяет структурированный ответ DeepSeek к карточке или ДЕЛА.md."""
    tz = ZoneInfo(timezone)
    target = resolve_target(workspace, str(edit.get("target_file", "")))
    original = target.read_text(encoding="utf-8")
    text = original

    status = str(edit.get("status") or "").strip() or None
    phone = str(edit.get("phone") or "").strip() or None
    if phone == "-":
        phone = None

    if target.name != "ДЕЛА.md":
        text, _ = _update_frontmatter(
            text,
            {key: value for key, value in (("статус", status), ("телефоны", phone)) if value},
        )

    step = _normalize_step(edit.get("step_date"), edit.get("step_text"), tz)
    step_line: str | None = None
    if step:
        step_date, step_text = step
        step_line = f"- [ ] {step_date} {step_text}"
        text, _ = _insert_under_heading(text, "ШАГИ", step_line)

    journal_line: str | None = None
    if target.name != "ДЕЛА.md":
        journal_line = _normalize_journal(edit.get("journal_entry"), tz)
        if journal_line:
            text, _ = _insert_under_heading(text, "ЖУРНАЛ", journal_line)

    if text == original:
        raise DocumentError("В ответе DeepSeek не было изменений для записи")

    target.write_text(text, encoding="utf-8")
    return UpdateResult(
        target=target.relative_to(workspace).as_posix(),
        step_line=step_line,
        journal_line=journal_line,
        status=status,
        phone=phone,
    )
