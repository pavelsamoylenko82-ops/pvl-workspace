from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_WHISPER_MODEL = "tiny"


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


def _path(name: str, default: Path) -> Path:
    raw = os.getenv(name, "").strip()
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = (PROJECT_DIR / path).resolve()
    return path


def _env_candidates() -> list[Path]:
    """Где искать DEEPSEEK_API_KEY, если его нет в .env бота.

    Сначала .env самого бота, затем корень рабочей папки (D:\\учеба\\PVL),
    затем .env соседних проектов. Секреты только читаются, не пишутся.
    """
    candidates: list[Path] = [PROJECT_DIR / ".env", PROJECT_DIR / ".env.local"]
    workspace = PROJECT_DIR.parent.parent
    candidates.append(workspace / ".env")
    candidates.append(workspace / ".env.local")
    projects_dir = PROJECT_DIR.parent
    if projects_dir.is_dir():
        for child in sorted(projects_dir.iterdir()):
            if child.is_dir():
                candidates.append(child / ".env")
    result: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def _fallback_value(name: str, own_value: str) -> str:
    """Возвращает значение из .env бота, иначе из корня или соседних проектов."""
    if own_value:
        return own_value
    for path in _env_candidates():
        try:
            values = dotenv_values(path)
        except OSError:
            continue
        value = str(values.get(name) or "").strip()
        if value:
            return value
    return ""


def _parse_ids(raw: str, admin_id: int) -> frozenset[int]:
    ids: set[int] = {admin_id}
    for chunk in raw.replace(";", ",").split(","):
        text = chunk.strip()
        if not text:
            continue
        try:
            ids.add(int(text))
        except ValueError as exc:
            raise ConfigError(
                "ALLOWED_USER_IDS должен содержать числовые Telegram ID через запятую"
            ) from exc
    return frozenset(ids)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_id: int
    timezone: str
    google_creds_path: Path
    spreadsheet_id: str
    backups_dir: Path
    state_dir: Path
    # Оставлено для старых локальных скриптов, бот их не использует.
    workspace_dir: Path
    allowed_user_ids: frozenset[int] = frozenset()
    git_sync: bool = False
    deepseek_api_key: str = ""
    deepseek_model: str = ""
    deepseek_base_url: str = ""
    whisper_model: str = DEFAULT_WHISPER_MODEL
    whisper_download_root: Path = field(default_factory=lambda: PROJECT_DIR / "models")
    whisper_language: str = "ru"
    whisper_compute_type: str = "int8"
    whisper_device: str = "cpu"

    def is_owner(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        return user_id == self.admin_id or user_id in self.allowed_user_ids


def load_settings() -> Settings:
    bot_token = _required("BOT_TOKEN")
    spreadsheet_id = _required("SPREADSHEET_ID")

    admin_raw = _required("ADMIN_ID")
    try:
        admin_id = int(admin_raw)
    except ValueError as exc:
        raise ConfigError("ADMIN_ID должен быть числом") from exc

    creds_path = _path("GOOGLE_CREDS_PATH", PROJECT_DIR / "google_creds.json")
    if not creds_path.exists():
        raise ConfigError(
            f"Не найден ключ service account: {creds_path}. "
            "Укажите GOOGLE_CREDS_PATH в .env или положите google_creds.json рядом с ботом."
        )

    workspace = _path("WORKSPACE_DIR", PROJECT_DIR)

    return Settings(
        bot_token=bot_token,
        admin_id=admin_id,
        timezone=os.getenv("TIMEZONE", "Europe/Minsk").strip() or "Europe/Minsk",
        google_creds_path=creds_path,
        spreadsheet_id=spreadsheet_id,
        backups_dir=_path("BACKUPS_DIR", PROJECT_DIR / "backups"),
        state_dir=_path("STATE_DIR", PROJECT_DIR / ".state"),
        workspace_dir=workspace,
        allowed_user_ids=_parse_ids(os.getenv("ALLOWED_USER_IDS", ""), admin_id),
        git_sync=_as_bool(os.getenv("GIT_SYNC", "false")),
        deepseek_api_key=_fallback_value(
            "DEEPSEEK_API_KEY", os.getenv("DEEPSEEK_API_KEY", "").strip()
        ),
        deepseek_model=_fallback_value(
            "DEEPSEEK_MODEL",
            os.getenv("DEEPSEEK_MODEL", "").strip() or DEFAULT_DEEPSEEK_MODEL,
        ),
        deepseek_base_url=_fallback_value(
            "DEEPSEEK_BASE_URL",
            os.getenv("DEEPSEEK_BASE_URL", "").strip() or DEFAULT_DEEPSEEK_BASE_URL,
        ),
        whisper_model=os.getenv("WHISPER_MODEL", DEFAULT_WHISPER_MODEL).strip()
        or DEFAULT_WHISPER_MODEL,
        whisper_download_root=_path(
            "WHISPER_DOWNLOAD_ROOT", PROJECT_DIR / "models"
        ),
        whisper_language=os.getenv("WHISPER_LANGUAGE", "ru").strip() or "ru",
        whisper_compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8").strip() or "int8",
        whisper_device=os.getenv("WHISPER_DEVICE", "cpu").strip() or "cpu",
    )
