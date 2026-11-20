from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from deepseek_client import DEFAULT_DEEPSEEK_MODEL, OFFICIAL_DEEPSEEK_BASE_URL, normalize_base_url

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")


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


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_id: int
    workspace_dir: Path
    git_sync: bool
    deepseek_api_key: str
    deepseek_model: str
    deepseek_base_url: str
    timezone: str


def load_settings() -> Settings:
    bot_token = _required("BOT_TOKEN")
    deepseek_api_key = _required("DEEPSEEK_API_KEY")

    admin_raw = _required("ADMIN_ID")
    try:
        admin_id = int(admin_raw)
    except ValueError as exc:
        raise ConfigError("ADMIN_ID должен быть числом") from exc

    workspace_raw = _required("WORKSPACE_DIR")
    workspace = Path(workspace_raw).expanduser()
    if not workspace.is_absolute():
        workspace = (PROJECT_DIR / workspace).resolve()
    else:
        workspace = workspace.resolve()
    if not workspace.exists():
        raise ConfigError(f"WORKSPACE_DIR не существует: {workspace}")

    base_url = normalize_base_url(os.getenv("DEEPSEEK_BASE_URL", OFFICIAL_DEEPSEEK_BASE_URL))
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL).strip() or DEFAULT_DEEPSEEK_MODEL

    return Settings(
        bot_token=bot_token,
        admin_id=admin_id,
        workspace_dir=workspace,
        git_sync=_as_bool(os.getenv("GIT_SYNC", "false")),
        deepseek_api_key=deepseek_api_key,
        deepseek_model=model,
        deepseek_base_url=base_url,
        timezone=os.getenv("TIMEZONE", "Europe/Minsk").strip() or "Europe/Minsk",
    )
