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
    created: bool = False


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


def _frontmatter_value(text: str, key: str) -> str | None:
    match = re.match(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n)?", text, re.DOTALL)
    if not match:
        return None
    line_re = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(.*)$", re.MULTILINE)
    found = line_re.search(match.group(1))
    if not found:
        return None
    value = found.group(1).strip()
    return value or None


def _update_frontmatter(text: str, fields: dict[str, str | None]) -> tuple[str, set[str]]:
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


def _insert_under_any_heading(text: str, headings: tuple[str, ...], line: str, fallback_heading: str) -> tuple[str, bool]:
    for heading in headings:
        if re.search(rf"^##\s*{re.escape(heading)}\s*$", text, re.MULTILINE):
            return _insert_under_heading(text, heading, line)
    return _insert_under_heading(text, fallback_heading, line)


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


def _normalize_journal(entry: Any, tz: ZoneInfo, fallback_text: str = "") -> str:
    """Возвращает строку журнала с датой и временем Europe/Minsk."""
    now = datetime.now(tz)
    raw = str(entry or "").strip()
    if not raw:
        raw = _clean_step_text(fallback_text)[:300]
    if not raw:
        raw = "Касание зафиксировано"
    raw = re.sub(r"^[-*]\s*", "", raw)

    match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?\s*:?\s*(.*)$", raw, re.DOTALL)
    if match:
        date_part = match.group(1)
        time_part = match.group(2) or now.strftime("%H:%M")
        body = match.group(3).strip() or raw
        return f"- {date_part} {time_part}: {body}"

    return f"- {now.strftime('%Y-%m-%d %H:%M')}: {raw}"


def _safe_card_stem(value: str) -> str:
    text = str(value or "").strip().strip("\"'`").replace("\\", "/")
    if text.startswith("Карточки/"):
        text = text.split("/", 1)[1]
    if text.lower().endswith(".md"):
        text = text[:-3]
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:120] or "Новый лид"


def _company_from_edit(edit: dict[str, Any]) -> str:
    company = str(edit.get("company") or "").strip()
    if company:
        return company
    raw_target = str(edit.get("target_file") or "").strip()
    return _safe_card_stem(raw_target) if raw_target and raw_target not in {"ДЕЛА.md", "DELA.md"} else ""


def _find_existing_card(workspace: Path, edit: dict[str, Any]) -> Path | None:
    raw_target = str(edit.get("target_file") or "").strip()
    if raw_target and raw_target not in {"ДЕЛА.md", "DELA.md"}:
        try:
            return resolve_target(workspace, raw_target)
        except DocumentError:
            pass
    company = _company_from_edit(edit)
    if company:
        matches = find_cards(workspace, company)
        if len(matches) == 1:
            return matches[0]
        if matches:
            return matches[0]
    return None


def _create_card(workspace: Path, edit: dict[str, Any], tz: ZoneInfo, fallback_text: str = "") -> UpdateResult:
    company = _company_from_edit(edit)
    if not company:
        raise DocumentError("Не удалось определить название компании для новой карточки")

    city = str(edit.get("city") or "").strip()
    display = company if not city or city.casefold() in company.casefold() else f"{company} ({city})"
    card_name = display if display.lower().endswith(".md") else f"{display}.md"
    cards_dir = workspace / "Карточки"
    cards_dir.mkdir(parents=True, exist_ok=True)
    target = cards_dir / card_name
    if target.exists():
        return _update_card(workspace, target, edit, tz, fallback_text)

    now = datetime.now(tz)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    status = str(edit.get("status") or "").strip() or "новый лид"
    phone = str(edit.get("phone") or "").strip() or None
    contact = str(edit.get("contact") or "").strip() or None
    messenger = str(edit.get("messenger") or "").strip() or None
    website = str(edit.get("website") or "").strip() or None
    socials = str(edit.get("socials") or "").strip() or None
    journal_line = _normalize_journal(edit.get("journal_entry"), tz, fallback_text)

    step = _normalize_step(edit.get("step_date"), edit.get("step_text"), tz)
    if step:
        step_date, step_text = step
        step_line = f"- [ ] {step_date} {step_text}"
    else:
        step_date = now.date().isoformat()
        step_text = "Уточнить потребность и договориться о следующем контакте"
        step_line = f"- [ ] {step_date} {step_text}"

    def val(value: str | None) -> str:
        return value or "не указано"

    content = f"""---
тип: лид
статус: {status}
компания: {company}
город: {city or 'не указан'}
контакт: {val(contact)}
телефон: {val(phone)}
мессенджер: {val(messenger)}
сайт: {val(website)}
соцсети: {val(socials)}
последний_контакт: {stamp}
---

# {display}

## НАЗВАНИЕ И ГОРОД
- Компания: {company}
- Город: {city or 'не указан'}

## КОНТАКТ
- Контакт: {val(contact)}
- Телефон: {val(phone)}
- Мессенджер: {val(messenger)}

## ТЕКУЩИЙ САЙТ И СОЦСЕТИ
- Сайт: {val(website)}
- Соцсети: {val(socials)}

## СТАТУС ВОРОНКИ
{status}

## ИСТОРИЯ КАСАНИЙ
{journal_line}

## СЛЕДУЮЩИЙ ШАГ
{step_line}
"""
    target.write_text(content, encoding="utf-8")
    return UpdateResult(
        target=target.relative_to(workspace).as_posix(),
        step_line=step_line,
        journal_line=journal_line,
        status=status,
        phone=phone,
        created=True,
    )


def _update_card(workspace: Path, target: Path, edit: dict[str, Any], tz: ZoneInfo, fallback_text: str = "") -> UpdateResult:
    original = target.read_text(encoding="utf-8")
    text = original
    now = datetime.now(tz)

    status = str(edit.get("status") or "").strip() or _frontmatter_value(original, "статус")
    phone = str(edit.get("phone") or "").strip() or None
    if phone == "-":
        phone = None
    company = str(edit.get("company") or "").strip() or None
    city = str(edit.get("city") or "").strip() or None
    contact = str(edit.get("contact") or "").strip() or None
    messenger = str(edit.get("messenger") or "").strip() or None
    website = str(edit.get("website") or "").strip() or None
    socials = str(edit.get("socials") or "").strip() or None

    fields = {
        "статус": status,
        "телефоны": phone,
        "компания": company,
        "город": city,
        "контакт": contact,
        "мессенджер": messenger,
        "сайт": website,
        "соцсети": socials,
        "последний_контакт": now.strftime("%Y-%m-%d %H:%M"),
    }
    text, _ = _update_frontmatter(text, fields)

    step = _normalize_step(edit.get("step_date"), edit.get("step_text"), tz)
    step_line: str | None = None
    if step:
        step_date, step_text = step
        step_line = f"- [ ] {step_date} {step_text}"
        text, _ = _insert_under_any_heading(
            text,
            ("СЛЕДУЮЩИЙ ШАГ", "ШАГИ"),
            step_line,
            "СЛЕДУЮЩИЙ ШАГ",
        )

    # Журнал пишем всегда, даже если модель не вернула journal_entry.
    journal_line = _normalize_journal(edit.get("journal_entry"), tz, fallback_text)
    text, _ = _insert_under_any_heading(
        text,
        ("ИСТОРИЯ КАСАНИЙ", "ЖУРНАЛ"),
        journal_line,
        "ИСТОРИЯ КАСАНИЙ",
    )

    if text != original:
        target.write_text(text, encoding="utf-8")

    return UpdateResult(
        target=target.relative_to(workspace).as_posix(),
        step_line=step_line,
        journal_line=journal_line,
        status=status,
        phone=phone,
        created=False,
    )


def apply_edit(
    workspace: Path,
    edit: dict[str, Any],
    timezone: str = "Europe/Minsk",
    fallback_text: str = "",
) -> UpdateResult:
    """Применяет структурированный ответ DeepSeek к карточке или ДЕЛА.md.

    Если карточки ещё нет, она создаётся по свободному описанию лида.
    """
    tz = ZoneInfo(timezone)
    raw_target = str(edit.get("target_file") or "").strip()
    action = str(edit.get("action") or "").strip().lower()
    fallback_text = str(fallback_text or edit.get("summary") or "").strip()

    if raw_target in {"ДЕЛА.md", "DELA.md"} or action == "update_dela":
        target = resolve_target(workspace, "ДЕЛА.md")
        original = target.read_text(encoding="utf-8")
        text = original

        step = _normalize_step(edit.get("step_date"), edit.get("step_text"), tz)
        step_line: str | None = None
        if step:
            step_date, step_text = step
            step_line = f"- [ ] {step_date} {step_text}"
            text, _ = _insert_under_any_heading(
                text,
                ("СЛЕДУЮЩИЙ ШАГ", "ШАГИ"),
                step_line,
                "СЛЕДУЮЩИЙ ШАГ",
            )
        if text == original:
            raise DocumentError("В ответе DeepSeek не было изменений для ДЕЛА.md")
        target.write_text(text, encoding="utf-8")
        return UpdateResult(
            target=target.relative_to(workspace).as_posix(),
            step_line=step_line,
            journal_line=None,
            status=None,
            phone=None,
            created=False,
        )

    existing = _find_existing_card(workspace, edit)
    if existing is not None:
        return _update_card(workspace, existing, edit, tz, fallback_text)

    # Нет существующей карточки - создаём новую по свободному сообщению.
    return _create_card(workspace, edit, tz, fallback_text)
