from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from audit import audit_leads
from config import ConfigError, load_settings
from scoring import score_lead
from storage import append_leads
from telegram_reporter import send_report
from yandex_maps import YandexMapsParser


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Парсер локальных бизнесов с Яндекс.Карт и техническим аудитом сайтов."
    )
    parser.add_argument("--city", required=True, help="Город, например: Молодечно или Минск")
    parser.add_argument("--query", required=True, help="Поисковый запрос, например: автосервис")
    parser.add_argument("--limit", type=int, default=30, help="Сколько лидов собрать после фильтрации")
    parser.add_argument(
        "--min-reviews",
        type=int,
        default=0,
        help="Минимальное количество отзывов для попадания в базу",
    )
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit должен быть >= 1")
    if args.min_reviews < 0:
        parser.error("--min-reviews должен быть >= 0")
    return args


def build_summary(
    *,
    city: str,
    query: str,
    leads: list,
    added: int,
    duplicates: int,
    total_rows: int,
    csv_path: Path,
    captcha_detected: bool,
) -> str:
    priority_a = sum(1 for lead in leads if lead.priority == "A")
    priority_b = sum(1 for lead in leads if lead.priority == "B")
    priority_c = sum(1 for lead in leads if lead.priority == "C")

    lines = [
        "Сводка lead-parser",
        f"Город: {city}",
        f"Запрос: {query}",
        f"Найдено после фильтра: {len(leads)}",
        f"Priority A: {priority_a}",
        f"Priority B: {priority_b}",
        f"Priority C: {priority_c}",
        f"Новых строк в БАЗА.csv: {added}",
        f"Дубликатов пропущено: {duplicates}",
        f"Всего строк в CSV: {total_rows}",
        f"Файл: {csv_path}",
    ]
    if captcha_detected:
        lines.append("ВНИМАНИЕ: обнаружена капча, парсинг остановлен досрочно. Сохранены собранные лиды.")
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    print("Настройки загружены.")
    print(f"CSV: {settings.csv_path}")
    print(f"HEADLESS: {settings.headless}")

    parser = YandexMapsParser(
        city=args.city,
        query=args.query,
        limit=args.limit,
        min_reviews=args.min_reviews,
        headless=settings.headless,
        timeout_ms=settings.yandex_timeout_ms,
    )
    leads, captcha_detected = await parser.parse()
    leads = [lead for lead in leads if lead.reviews >= args.min_reviews][: args.limit]
    print(f"Собрано лидов после фильтра: {len(leads)}")

    print("Запускаю технический аудит сайтов...")
    await audit_leads(
        leads,
        timeout=settings.audit_timeout_s,
        concurrency=settings.audit_concurrency,
    )

    for lead in leads:
        score_lead(lead, args.min_reviews)

    storage_result = append_leads(settings.csv_path, leads)
    print(
        f"CSV обновлён: добавлено {storage_result.added}, "
        f"дубликатов {storage_result.duplicates}, всего строк {storage_result.total_rows}"
    )

    summary = build_summary(
        city=args.city,
        query=args.query,
        leads=leads,
        added=storage_result.added,
        duplicates=storage_result.duplicates,
        total_rows=storage_result.total_rows,
        csv_path=settings.csv_path,
        captcha_detected=captcha_detected,
    )
    print("\n" + summary)

    try:
        await send_report(
            bot_token=settings.parser_bot_token,
            admin_id=settings.admin_id,
            message=summary,
            csv_path=settings.csv_path,
        )
        print("Отчёт и CSV отправлены в Telegram.")
    except Exception as exc:
        print(f"ВНИМАНИЕ: не удалось отправить отчёт в Telegram: {type(exc).__name__}: {exc}")
        return 2

    return 0


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    try:
        exit_code = asyncio.run(run(args))
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}")
        exit_code = 1
    except KeyboardInterrupt:
        print("Остановлено пользователем.")
        exit_code = 130
    except Exception as exc:
        print(f"Ошибка выполнения: {type(exc).__name__}: {exc}")
        exit_code = 1
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
