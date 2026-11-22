from __future__ import annotations

import asyncio
import importlib
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import FSInputFile, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import ConfigError, Settings, load_settings
from deepseek_client import DeepSeekError, request_edit
from documents import (
    DocumentError,
    UpdateResult,
    apply_edit,
    build_file_context,
    card_names_for_prompt,
    find_cards,
    list_card_files,
)
from git_ops import GitError, commit_and_push, pull_rebase
from text_utils import safe_answer, safe_send

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("pvl-pult-bot")

_last_git_pull_at: datetime | None = None
_brief_job_lock = asyncio.Lock()


class BuildError(RuntimeError):
    """Ошибка запуска собрать.py."""


class AdminOnly(BaseFilter):
    def __init__(self, admin_id: int) -> None:
        self.admin_id = admin_id

    async def __call__(self, message: Message) -> bool:
        return bool(message.from_user and message.from_user.id == self.admin_id)


def run_build(workspace: Path) -> str:
    result = subprocess.run(
        [sys.executable, "собрать.py"],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise BuildError(details or "собрать.py завершился с ошибкой")
    return (result.stdout or "").strip()


def workspace_module(settings: Settings, name: str):
    workspace_text = str(settings.workspace_dir)
    if workspace_text not in sys.path:
        sys.path.insert(0, workspace_text)
    return importlib.import_module(name)


async def maybe_pull(settings: Settings, force: bool = False) -> None:
    """git pull --rebase не чаще раза в 15 минут, чтобы не спамить GitHub."""
    global _last_git_pull_at
    if not settings.git_sync:
        return
    now = datetime.now(ZoneInfo(settings.timezone))
    if (
        not force
        and _last_git_pull_at is not None
        and (now - _last_git_pull_at).total_seconds() < 15 * 60
    ):
        return
    try:
        await asyncio.to_thread(pull_rebase, settings.workspace_dir)
        _last_git_pull_at = now
    except GitError as exc:
        logger.warning("git pull --rebase не выполнен: %s", exc)


async def generate_and_send_briefs(
    bot: Bot,
    settings: Settings,
    card_query: str | None = None,
) -> list[Path]:
    """Генерирует новые брифы, делает git push и присылает файлы админу."""
    async with _brief_job_lock:
        module = workspace_module(settings, "генератор_брифов")
        if card_query:
            path = await module.generate_for_query(card_query, force=True)
            created = [path] if path is not None else []
        else:
            created = await module.generate_queue()

        if not created:
            return []

        if settings.git_sync:
            names = ", ".join(path.stem for path in created)
            try:
                pushed = await asyncio.to_thread(
                    commit_and_push,
                    settings.workspace_dir,
                    f"briefs: авто-генерация ({names})",
                )
                if not pushed:
                    logger.info("Git: новых изменений для отправки нет")
            except Exception as exc:
                logger.exception("Ошибка git add/commit/pull --rebase/push после генерации брифов")
                await safe_send(
                    bot,
                    settings.admin_id,
                    f"Брифы созданы, но git push не выполнен: {exc}",
                )

        for path in created:
            try:
                await bot.send_document(
                    settings.admin_id,
                    FSInputFile(str(path), filename=path.name),
                    caption=f"Бриф готов: {path.stem}",
                )
            except Exception:
                logger.exception("Не удалось отправить бриф админу: %s", path)
        return created


async def scheduled_reminders(bot: Bot, settings: Settings) -> None:
    try:
        await maybe_pull(settings)
        module = workspace_module(settings, "напоминания")
        tz = ZoneInfo(settings.timezone)
        await asyncio.to_thread(module.run_cycle, datetime.now(tz), False)
    except Exception:
        logger.exception("Ошибка фонового цикла напоминаний")


async def scheduled_briefs(bot: Bot, settings: Settings) -> None:
    try:
        await maybe_pull(settings)
        await generate_and_send_briefs(bot, settings)
    except Exception:
        logger.exception("Ошибка фоновой генерации брифов")


async def auto_brief_after_edit(bot: Bot, settings: Settings, card_name: str) -> None:
    try:
        await maybe_pull(settings)
        created = await generate_and_send_briefs(bot, settings, card_query=card_name)
        if created:
            logger.info("Авто-бриф после правки карточки: %s", card_name)
    except Exception:
        logger.exception("Не удалось автоматически обновить бриф после правки карточки")
        try:
            await safe_send(
                bot,
                settings.admin_id,
                f"Карточка обновлена, но бриф не создан: {card_name}",
            )
        except Exception:
            logger.exception("Не удалось сообщить об ошибке авто-брифа")


async def send_pult(bot: Bot, chat_id: int, settings: Settings) -> None:
    warning = ""
    if settings.git_sync:
        try:
            await asyncio.to_thread(pull_rebase, settings.workspace_dir)
        except GitError as exc:
            logger.warning("git pull --rebase перед /pult не выполнен: %s", exc)
            warning = "Git pull не выполнен, показываю локальную версию ПУЛЬТ.md.\n\n"

    await asyncio.to_thread(run_build, settings.workspace_dir)
    pult_path = settings.workspace_dir / "ПУЛЬТ.md"
    if not pult_path.exists():
        raise BuildError("После сборки файл ПУЛЬТ.md не найден")
    pult_text = pult_path.read_text(encoding="utf-8")
    try:
        workspace_text = str(settings.workspace_dir)
        if workspace_text not in sys.path:
            sys.path.insert(0, workspace_text)
        from напоминания import digest_summary_line

        pult_text = pult_text.rstrip() + "\n\n" + digest_summary_line()
    except Exception:
        logger.exception("Не удалось добавить строку напоминаний в дайджест")
    await safe_send(bot, chat_id, warning + pult_text)


def build_summary(result: UpdateResult, git_status: str) -> str:
    action = "Создано" if result.created else "Обновлено"
    lines = [f"{action}: {result.target}"]
    lines.append(f"Статус: {result.status or 'без изменений'}")

    if result.step_line:
        step_text = result.step_line.removeprefix("- [ ] ").strip()
        lines.append(f"Следующий шаг: {step_text}")
    else:
        lines.append("Следующий шаг: не изменён")

    if result.journal_line:
        lines.append("История касаний: запись добавлена")
    lines.append(git_status)
    return "\n".join(lines)


def build_router(settings: Settings) -> Router:
    router = Router()
    router.message.filter(AdminOnly(settings.admin_id))
    tz = ZoneInfo(settings.timezone)

    @router.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        try:
            await safe_answer(
                message,
                "Пульт в кармане на связи.\n"
                "/pult - собрать и показать ПУЛЬТ.md\n"
                "/card <часть имени> - открыть карточку\n"
                "/brief <часть имени> - пересобрать и прислать бриф\n"
                "Обычным текстом - внести правку в карточку или ДЕЛА.md.\n"
                "Можно писать свободно: 'Позвонил в Автоэкспресс, перезвонить через 7 дней'.",
            )
        except Exception as exc:
            logger.exception("Ошибка /start")
            await safe_answer(message, f"Ошибка: {exc}")

    @router.message(Command("pult"))
    async def cmd_pult(message: Message) -> None:
        try:
            await send_pult(message.bot, message.chat.id, settings)
        except Exception as exc:
            logger.exception("Ошибка /pult")
            await safe_answer(message, f"Ошибка: {exc}")

    @router.message(Command("card"))
    async def cmd_card(message: Message, command: CommandObject) -> None:
        try:
            query = (command.args or "").strip()
            if not query:
                await safe_answer(message, "Использование: /card <часть имени файла>")
                return

            matches = find_cards(settings.workspace_dir, query)
            if not matches:
                names = [path.name for path in list_card_files(settings.workspace_dir)]
                available = "\n".join(f"- {name}" for name in names) if names else "- Карточек пока нет."
                await safe_answer(message, f"Карточка не найдена по запросу: {query}\n\nДоступные файлы:\n{available}")
                return

            if len(matches) > 1:
                found = "\n".join(f"- {path.name}" for path in matches)
                await safe_answer(message, f"Найдено несколько карточек, уточните имя:\n{found}")
                return

            path = matches[0]
            text = path.read_text(encoding="utf-8")
            await safe_answer(message, f"Карточка: {path.name}\n\n{text}")
        except Exception as exc:
            logger.exception("Ошибка /card")
            await safe_answer(message, f"Ошибка: {exc}")

    @router.message(Command("brief"))
    async def cmd_brief(message: Message, command: CommandObject) -> None:
        try:
            query = (command.args or "").strip()
            if not query:
                await safe_answer(message, "Использование: /brief <часть названия компании>")
                return
            await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
            created = await generate_and_send_briefs(message.bot, settings, card_query=query)
            if not created:
                await safe_answer(message, f"Бриф не создан по запросу: {query}")
        except Exception as exc:
            logger.exception("Ошибка /brief")
            await safe_answer(message, f"Ошибка: {exc}")

    @router.message(F.text & ~F.text.startswith("/"))
    async def handle_free_text(message: Message) -> None:
        try:
            user_text = (message.text or "").strip()
            if not user_text:
                return

            await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
            now = datetime.now(tz)
            now_text = now.strftime("%Y-%m-%d %H:%M (%Z)")

            try:
                edit = await request_edit(
                    api_key=settings.deepseek_api_key,
                    model=settings.deepseek_model,
                    base_url=settings.deepseek_base_url,
                    card_names=card_names_for_prompt(settings.workspace_dir),
                    now_text=now_text,
                    user_text=user_text,
                    file_context=build_file_context(settings.workspace_dir),
                )
            except Exception as exc:
                logger.exception("Ошибка вызова DeepSeek API")
                await safe_answer(message, f"Ошибка: {exc}")
                return

            try:
                result = await asyncio.to_thread(
                    apply_edit,
                    settings.workspace_dir,
                    edit,
                    settings.timezone,
                    user_text,
                )
            except Exception as exc:
                logger.exception("Ошибка применения ответа DeepSeek")
                await safe_answer(message, f"Ошибка: {exc}")
                return

            try:
                await asyncio.to_thread(run_build, settings.workspace_dir)
            except Exception as exc:
                logger.exception("Ошибка пересборки ПУЛЬТ.md")
                await safe_answer(
                    message,
                    f"Ошибка: {exc}\n"
                    f"Карточка сохранена: {result.target}\n"
                    "Git: не выполнялся.",
                )
                return

            git_status = "Git: выключен, изменения только локально."
            if settings.git_sync:
                commit_message = f"bot: обновление {result.target} ({now_text})"
                try:
                    pushed = await asyncio.to_thread(
                        commit_and_push,
                        settings.workspace_dir,
                        commit_message,
                    )
                    git_status = (
                        "Git: изменения отправлены."
                        if pushed
                        else "Git: новых изменений для отправки нет."
                    )
                except Exception as exc:
                    logger.exception("Ошибка git add/commit/pull --rebase/push")
                    await safe_answer(
                        message,
                        f"Ошибка: {exc}\n"
                        f"Карточка сохранена локально: {result.target}\n"
                        "Git push не выполнен.",
                    )
                    return

            await safe_answer(message, build_summary(result, git_status))

            try:
                status_module = workspace_module(settings, "собрать")
                if result.status and status_module.is_warm_status(result.status):
                    card_name = Path(result.target).stem
                    asyncio.create_task(auto_brief_after_edit(message.bot, settings, card_name))
            except Exception:
                logger.exception("Не удалось запустить авто-бриф после правки карточки")
        except Exception as exc:
            logger.exception("Необработанная ошибка обработчика сообщений")
            try:
                await safe_answer(message, f"Ошибка: {exc}")
            except Exception:
                logger.exception("Не удалось отправить сообщение об ошибке в Telegram")

    return router


async def scheduled_digest(bot: Bot, settings: Settings) -> None:
    try:
        await send_pult(bot, settings.admin_id, settings)
    except Exception as exc:
        logger.exception("Ошибка утреннего дайджеста")
        await safe_send(bot, settings.admin_id, f"Ошибка: {exc}")


async def main() -> None:
    settings = load_settings()
    tz = ZoneInfo(settings.timezone)

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(settings))

    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        scheduled_reminders,
        IntervalTrigger(minutes=5, timezone=tz),
        args=[bot, settings],
        id="reminders_every_5m",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        scheduled_briefs,
        IntervalTrigger(hours=1, timezone=tz),
        args=[bot, settings],
        id="briefs_every_hour",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        scheduled_digest,
        CronTrigger(hour=9, minute=0, timezone=tz),
        args=[bot, settings],
        id="morning_digest",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Бот запущен. WORKSPACE_DIR=%s, GIT_SYNC=%s, DEEPSEEK_BASE_URL=%s, DEEPSEEK_MODEL=%s",
        settings.workspace_dir,
        settings.git_sync,
        settings.deepseek_base_url,
        settings.deepseek_model,
    )

    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ConfigError as exc:
        logger.error("Конфигурация: %s", exc)