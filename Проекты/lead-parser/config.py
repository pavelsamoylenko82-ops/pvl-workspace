from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

DEFAULT_XLSX_PATH = Path(r"D:\учеба\PVL\БАЗА.xlsx")


class ConfigError(RuntimeError):
    """Ошибка конфигурации приложения."""


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Не задана переменная {name} в .env")
    return value


def _as_bool(value: str, default: bool = False) -> bool:
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


def _as_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должен быть целым числом") from exc
    if value < minimum:
        raise ConfigError(f"{name} должен быть >= {minimum}")
    return value


def _as_float(name: str, default: float, minimum: float = 0.1) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должен быть числом") from exc
    if value < minimum:
        raise ConfigError(f"{name} должен быть >= {minimum}")
    return value


@dataclass(frozen=True)
class Settings:
    parser_bot_token: str
    admin_id: int
    xlsx_path: Path
    headless: bool
    yandex_timeout_ms: int
    audit_timeout_s: float
    audit_concurrency: int


def load_settings() -> Settings:
    parser_bot_token = _required("PARSER_BOT_TOKEN")

    admin_raw = _required("ADMIN_ID")
    try:
        admin_id = int(admin_raw)
    except ValueError as exc:
        raise ConfigError("ADMIN_ID должен быть числом") from exc

    xlsx_raw = os.getenv("LEADS_XLSX_PATH", "").strip()
    xlsx_path = Path(xlsx_raw).expanduser() if xlsx_raw else DEFAULT_XLSX_PATH
    if not xlsx_path.is_absolute():
        xlsx_path = (PROJECT_DIR / xlsx_path).resolve()
    else:
        xlsx_path = xlsx_path.resolve()

    return Settings(
        parser_bot_token=parser_bot_token,
        admin_id=admin_id,
        xlsx_path=xlsx_path,
        headless=_as_bool(os.getenv("HEADLESS", "true"), default=True),
        yandex_timeout_ms=_as_int("YANDEX_TIMEOUT_MS", 45000, minimum=5000),
        audit_timeout_s=_as_float("AUDIT_TIMEOUT", 10.0, minimum=1.0),
        audit_concurrency=_as_int("AUDIT_CONCURRENCY", 8, minimum=1),
    )
