#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Генерация брифов для тёплых/горячих лидов через DeepSeek API.

Запуск:
    python генератор_брифов.py --dry-run
    python генератор_брифов.py
    python генератор_брифов.py --card "Stimul"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from собрать import Card, clean_text, is_warm_status, load_cards  # noqa: E402

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - зависимость уже есть в requirements бота
    load_dotenv = None

DEFAULT_BASE_URL = "https://freecodex.fun/v1"
CARDS_DIR = ROOT / "Карточки"
BRIEFS_DIR = ROOT / "Брифы"
TEMPLATE_FILE = BRIEFS_DIR / "_ШАБЛОН-БРИФА.md"
PRICE_FILE = ROOT / "knowledge" / "08-ОПЕРАЦИОНКА-SAMOLA.md"
STATE_DIR = ROOT / ".state"
ERROR_STATE_FILE = STATE_DIR / "brief_errors.json"
ENV_FILES = (
    ROOT / ".env",
    ROOT / "Проекты" / "pvl-pult-bot" / ".env",
)
TZ = ZoneInfo("Europe/Minsk")
ERROR_COOLDOWN = timedelta(hours=1)
FORBIDDEN_WORDS = ("конверсия", "CRM", "воронка", "адаптивная вёрстка")
DASHES_RE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")


class BriefError(RuntimeError):
    """Ошибка генерации брифа."""


class BriefConfigError(BriefError):
    """Ошибка конфигурации генератора."""


@dataclass(frozen=True)
class BriefSettings:
    api_key: str
    model: str
    base_url: str


_GENERATION_LOCK = asyncio.Lock()


def _load_env() -> None:
    if load_dotenv is None:
        return
    for path in ENV_FILES:
        if path.is_file():
            load_dotenv(path, override=False)


def normalize_base_url(value: str) -> str:
    base = (value or DEFAULT_BASE_URL).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")].rstrip("/")
    return base or DEFAULT_BASE_URL


def load_settings(require_api: bool = True) -> BriefSettings:
    _load_env()
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    model = os.getenv("DEEPSEEK_MODEL", "").strip()
    base_url = normalize_base_url(os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL))

    if require_api and not api_key:
        raise BriefConfigError("Не задана переменная DEEPSEEK_API_KEY в .env")
    if require_api and not model:
        raise BriefConfigError("Не задана переменная DEEPSEEK_MODEL в .env")
    return BriefSettings(api_key=api_key, model=model, base_url=base_url)


def brief_path(card: Card) -> Path:
    return BRIEFS_DIR / f"{card.name}.md"


def card_path(card: Card) -> Path:
    return CARDS_DIR / f"{card.name}.md"


def display_name(card: Card) -> str:
    return f"{card.name} ({clean_text(card.status)})"


def warm_cards() -> list[Card]:
    return [card for card in load_cards() if not card.closed and is_warm_status(card.status)]


def cards_without_brief() -> list[Card]:
    return [card for card in warm_cards() if not brief_path(card).is_file()]


def find_cards(query: str) -> list[Card]:
    needle = clean_text(query).strip("\"'`").casefold()
    if not needle:
        return []
    cards = load_cards()
    exact = [
        card
        for card in cards
        if card.name.casefold() == needle
        or clean_text(card.company).casefold() == needle
    ]
    if exact:
        return exact
    return [
        card
        for card in cards
        if needle in card.name.casefold() or needle in clean_text(card.company).casefold()
    ]


def _load_error_state() -> dict[str, str]:
    if not ERROR_STATE_FILE.is_file():
        return {}
    try:
        raw = json.loads(ERROR_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _save_error_state(state: dict[str, str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ERROR_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(ERROR_STATE_FILE)


def _error_in_cooldown(card_name: str, now: datetime) -> bool:
    raw = _load_error_state().get(card_name)
    if not raw:
        return False
    try:
        failed_at = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if failed_at.tzinfo is None:
        failed_at = failed_at.replace(tzinfo=TZ)
    return now - failed_at < ERROR_COOLDOWN


def _record_error(card_name: str, now: datetime) -> None:
    state = _load_error_state()
    state[card_name] = now.astimezone(TZ).isoformat(timespec="seconds")
    _save_error_state(state)


def _clear_error(card_name: str) -> None:
    state = _load_error_state()
    if card_name in state:
        state.pop(card_name, None)
        _save_error_state(state)


def extract_price_section(price_text: str) -> str:
    match = re.search(
        r"(?ms)^##\s+[^\n]*Прайс[^\n]*\n.*?(?=^##\s+|\Z)",
        price_text,
    )
    if not match:
        raise BriefError(f"В {PRICE_FILE.name} не найден раздел прайса")
    return match.group(0).strip()


def _read_required(path: Path, label: str) -> str:
    if not path.is_file():
        raise BriefError(f"Не найден {label}: {path}")
    try:
        return path.read_text(encoding="utf-8").lstrip("\ufeff")
    except OSError as exc:
        raise BriefError(f"Не удалось прочитать {label}: {path}: {exc}") from exc


def build_messages(card: Card, card_text: str, price_section: str, template_text: str) -> list[dict[str, str]]:
    forbidden = ", ".join(f"«{word}»" for word in FORBIDDEN_WORDS)
    system = (
        "Ты аккуратный помощник по подготовке коммерческих брифов. "
        "Твоя задача - заполнить предоставленный шаблон только фактами из карточки лида "
        "и ценами из раздела прайса. Ничего не выдумывай: чего нет в карточке, "
        "выноси в раздел «Дыры» и в вопросы. Пиши по-русски, коротко и по делу. "
        "Используй только обычный дефис (-), длинные тире запрещены. "
        f"В 7 вопросах не используй слова: {forbidden}. "
        "Верни только готовый markdown-бриф без пояснений до или после."
    )
    user = (
        "Заполни шаблон брифа строго по фактам из карточки и ценам из прайса.\n\n"
        "ПРАВИЛА:\n"
        "1. Не придумывай факты, имена, телефоны, сроки, цифры и цены.\n"
        "2. Если данных нет - пиши в «Дыры» и формулируй вопрос.\n"
        "3. В разделе цен используй только цены из прайса; если подходящей цены нет, "
        "напиши «уточнить по прайсу» и вынеси в вопросы.\n"
        "4. Ровно 7 коротких вопросов на созвон, простым языком, без жаргона.\n"
        f"5. Не используй слова: {forbidden}.\n"
        "6. Не используй длинные тире, только дефис (-).\n\n"
        f"КАРТОЧКА ЛИДА ({card.name}):\n{card_text.strip()}\n\n"
        f"РАЗДЕЛ ПРАЙСА:\n{price_section.strip()}\n\n"
        f"ШАБЛОН БРИФА:\n{template_text.strip()}\n"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def request_brief(settings: BriefSettings, messages: list[dict[str, str]]) -> str:
    import httpx

    url = f"{settings.base_url}/chat/completions"
    payload = {
        "model": settings.model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 3500,
    }
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        details = exc.response.text[:500]
        raise BriefError(f"DeepSeek API HTTP {exc.response.status_code}: {details}") from exc
    except httpx.TimeoutException as exc:
        raise BriefError("DeepSeek API не ответил за 60 секунд") from exc
    except httpx.HTTPError as exc:
        raise BriefError(f"DeepSeek API недоступен: {exc}") from exc
    except ValueError as exc:
        raise BriefError("DeepSeek API вернул не-JSON ответ") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise BriefError("DeepSeek API вернул ответ без choices[0].message.content") from exc
    text = str(content or "").strip()
    if not text:
        raise BriefError("DeepSeek API вернул пустой бриф")
    return text


def postprocess_brief(text: str) -> str:
    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    text = DASHES_RE.sub("-", text)
    return text.strip() + "\n"


def run_build() -> str:
    result = subprocess.run(
        [sys.executable, "собрать.py"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise BriefError(details or "собрать.py завершился с ошибкой")
    return (result.stdout or "").strip()


async def _generate_one(card: Card, force: bool, now: datetime) -> Path | None:
    target = brief_path(card)
    if target.is_file() and not force:
        return None

    settings = load_settings(require_api=True)
    card_text = _read_required(card_path(card), "карточка")
    template_text = _read_required(TEMPLATE_FILE, "шаблон брифа")
    price_text = _read_required(PRICE_FILE, "файл прайса")
    price_section = extract_price_section(price_text)

    messages = build_messages(card, card_text, price_section, template_text)
    try:
        answer = await request_brief(settings, messages)
        content = postprocess_brief(answer)
        BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        _clear_error(card.name)
        run_build()
        return target
    except Exception:
        _record_error(card.name, now)
        raise


async def generate_brief(card: Card, force: bool = False) -> Path | None:
    now = datetime.now(TZ)
    async with _GENERATION_LOCK:
        return await _generate_one(card, force=force, now=now)


async def generate_for_query(query: str, force: bool = True) -> Path | None:
    matches = find_cards(query)
    if not matches:
        raise BriefError(f"Карточка не найдена по запросу: {query}")
    if len(matches) > 1:
        names = ", ".join(card.name for card in matches)
        raise BriefError(f"Найдено несколько карточек, уточните запрос: {names}")
    return await generate_brief(matches[0], force=force)


async def generate_queue() -> list[Path]:
    now = datetime.now(TZ)
    created: list[Path] = []
    async with _GENERATION_LOCK:
        for card in cards_without_brief():
            if _error_in_cooldown(card.name, now):
                print(f"[cooldown] {card.name}: последняя ошибка меньше часа назад", flush=True)
                continue
            try:
                target = await _generate_one(card, force=False, now=now)
            except Exception as exc:
                print(f"[error] {card.name}: {exc}", file=sys.stderr, flush=True)
                continue
            if target is not None:
                created.append(target)
                print(f"[created] {target}", flush=True)
    return created


def dry_run_report() -> list[Card]:
    now = datetime.now(TZ)
    queue = cards_without_brief()
    if not queue:
        print("[dry-run] Все тёплые/горячие карточки уже имеют брифы.")
        return []
    ready: list[Card] = []
    for card in queue:
        if _error_in_cooldown(card.name, now):
            print(f"[dry-run] пропуск (ошибка меньше часа назад): {display_name(card)}")
        else:
            ready.append(card)
            print(f"[dry-run] будет создан: {display_name(card)}")
    print(f"\n[dry-run] в очереди: {len(queue)}, к генерации: {len(ready)}")
    return ready


async def async_main(args: argparse.Namespace) -> int:
    if args.card:
        if args.dry_run:
            matches = find_cards(args.card)
            if not matches:
                print(f"Карточка не найдена по запросу: {args.card}", file=sys.stderr)
                return 1
            if len(matches) > 1:
                print("Найдено несколько карточек: " + ", ".join(card.name for card in matches), file=sys.stderr)
                return 1
            print(f"[dry-run] будет принудительно перезаписан: {display_name(matches[0])}")
            return 0
        target = await generate_for_query(args.card, force=True)
        if target is not None:
            print(f"Бриф обновлён: {target}")
        return 0

    if args.dry_run:
        dry_run_report()
        return 0

    created = await generate_queue()
    if created:
        print(f"Готово: создано брифов {len(created)}")
    else:
        print("Готово: новых брифов нет")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Генератор брифов PVL")
    parser.add_argument("--dry-run", action="store_true", help="Показать очередь без запроса к API")
    parser.add_argument("--card", help="Сгенерировать или перезаписать бриф одной карточки")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(async_main(args)))
    except BriefConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except BriefError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()