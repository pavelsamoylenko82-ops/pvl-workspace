"""Telegram-бот «Пульт»: прямая работа с Google Sheets SAMOLA CRM."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import BaseFilter, Command
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ai_crm import CrmAction, apply_action, format_preview, parse_text
from config import ConfigError, Settings, load_settings
from keyboards import (
    BUTTON_VIEWS,
    VIEWS,
    deal_card_kb,
    lead_actions_kb,
    lead_card_kb,
    confirm_kb,
    pult_kb,
)
from render import (
    active_deals,
    esc,
    format_deal_card,
    format_header,
    format_lead_card,
    format_lead_expanded,
    hot_items,
    is_deal_done,
    is_lead_closed,
    morning_text,
    normalize,
    overdue_items,
    reminder_text,
    summary_text,
    today_items,
    week_items,
)
from sheets import CrmData, CrmSheets, Deal, Lead, write_backup
from voice import Transcriber, VoiceError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("pvl-pult-bot")

MAX_CARDS = 25
REMINDER_WINDOW_MINUTES = 15
MAX_AI_INPUT = 1000
PENDING_TTL = timedelta(minutes=60)


class AdminOnly(BaseFilter):
    """Пропускает только владельца бота и ID из ALLOWED_USER_IDS."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and self.settings.is_owner(user.id))


@dataclass
class PendingAction:
    action: CrmAction
    owner_id: int
    created_at: datetime
    is_voice: bool = False


def link_preview_off() -> LinkPreviewOptions:
    return LinkPreviewOptions(is_disabled=True)


async def send_html(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    reply_markup=None,
) -> Message:
    return await bot.send_message(
        chat_id=chat_id,
        text=normalize(text),
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
        link_preview_options=link_preview_off(),
    )


async def edit_html(message: Message, text: str, *, reply_markup=None) -> None:
    await message.edit_text(
        normalize(text),
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
        link_preview_options=link_preview_off(),
    )


async def load_data(crm: CrmSheets, settings: Settings) -> CrmData:
    return await asyncio.to_thread(crm.load, settings.timezone)


def build_view(
    data: CrmData, view: str, today: date
) -> tuple[str, list[tuple[str, Lead | Deal]]]:
    if view == "today":
        leads, deals = today_items(data.leads, data.deals, today)
        header = format_header(
            view, today, leads_count=len(leads), deals_count=len(deals)
        )
        items = [("l", lead) for lead in leads] + [("d", deal) for deal in deals]
        return header, items

    if view == "hot":
        leads = hot_items(data.leads)
        header = format_header(view, today, leads_count=len(leads))
        return header, [("l", lead) for lead in leads]

    if view == "overdue":
        leads, deals = overdue_items(data.leads, data.deals, today)
        header = format_header(
            view, today, leads_count=len(leads), deals_count=len(deals)
        )
        items = [("l", lead) for lead in leads] + [("d", deal) for deal in deals]
        return header, items

    if view == "week":
        leads, deals = week_items(data.leads, data.deals, today)
        header = format_header(
            view, today, leads_count=len(leads), deals_count=len(deals)
        )
        items = [("l", lead) for lead in leads] + [("d", deal) for deal in deals]
        return header, items

    if view == "deals":
        deals = active_deals(data.deals)
        header = format_header(view, today, deals_count=len(deals))
        return header, [("d", deal) for deal in deals]

    raise ValueError(f"Неизвестный срез: {view}")


def card_keyboard(kind: str, item: Lead | Deal, today: date):
    if kind == "l":
        return lead_card_kb(item, today)
    return deal_card_kb(item, today)


def item_text(kind: str, item: Lead | Deal) -> str:
    return format_lead_card(item) if kind == "l" else format_deal_card(item)


async def send_view(
    bot: Bot,
    crm: CrmSheets,
    settings: Settings,
    chat_id: int,
    view: str,
) -> None:
    tz = ZoneInfo(settings.timezone)
    today = datetime.now(tz).date()
    data = await load_data(crm, settings)
    header, items = build_view(data, view, today)

    # Сначала короткая шапка среза, затем карточки.
    await send_html(bot, chat_id, header, reply_markup=pult_kb())

    for kind, item in items[:MAX_CARDS]:
        await send_html(
            bot,
            chat_id,
            item_text(kind, item),
            reply_markup=card_keyboard(kind, item, today),
        )
    if len(items) > MAX_CARDS:
        await send_html(
            bot,
            chat_id,
            f"Показаны первые {MAX_CARDS} из {len(items)}. Уточните срез или обновите таблицу.",
            reply_markup=pult_kb(),
        )


async def send_summary(
    bot: Bot, crm: CrmSheets, settings: Settings, chat_id: int
) -> None:
    """Короткая сводка дня с постоянной клавиатурой (для /start и «Сводка»)."""
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    today = now.date()
    try:
        data = await load_data(crm, settings)
        leads, deals = today_items(data.leads, data.deals, today)
        late_leads, late_deals = overdue_items(data.leads, data.deals, today)
        text = summary_text(
            now,
            calls_count=len(leads),
            deals_count=len(deals),
            hot_count=len(hot_items(data.leads)),
            overdue_count=len(late_leads) + len(late_deals),
            leads_total=len(data.leads),
            active_deals=len(active_deals(data.deals)),
        )
    except Exception as exc:
        logger.exception("Не удалось прочитать таблицу для сводки")
        text = (
            "<b>Пульт SAMOLA CRM</b>\n"
            f"Таблица недоступна: {esc(exc)}\n"
            "Проверьте GOOGLE_CREDS_PATH и доступ service account."
        )
    await send_html(bot, chat_id, text, reply_markup=pult_kb())


def load_reminder_state(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def save_reminder_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)


def prune_reminder_state(state: dict[str, str], now: datetime) -> dict[str, str]:
    cutoff = now - timedelta(days=7)
    result: dict[str, str] = {}
    for key, sent_at in state.items():
        try:
            moment = datetime.fromisoformat(sent_at)
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=now.tzinfo)
        if moment >= cutoff:
            result[key] = sent_at
    return result


async def scheduled_reminders(
    bot: Bot, crm: CrmSheets, settings: Settings
) -> None:
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    state_path = settings.state_dir / "reminders_sent.json"
    try:
        data = await load_data(crm, settings)
        original = load_reminder_state(state_path)
        state = prune_reminder_state(original, now)
        if state != original:
            save_reminder_state(state_path, state)

        for lead in sorted(
            data.leads,
            key=lambda item: (
                item.next_date or date.max,
                item.next_time or datetime.min.time(),
            ),
        ):
            if lead.next_date is None or lead.next_time is None:
                continue
            if is_lead_closed(lead.status):
                continue
            call_at = datetime.combine(lead.next_date, lead.next_time).replace(
                tzinfo=tz
            )
            minutes = (call_at - now).total_seconds() / 60
            if not 0 < minutes <= REMINDER_WINDOW_MINUTES:
                continue
            key = f"{lead.id}:{lead.next_date.isoformat()}:{lead.next_time:%H:%M}"
            if key in state:
                continue

            try:
                await send_html(
                    bot,
                    settings.admin_id,
                    reminder_text(lead),
                    reply_markup=lead_card_kb(lead, now.date()),
                )
            except Exception:
                logger.exception("Не удалось отправить напоминание: %s", lead.company)
                continue

            state[key] = now.isoformat()
            save_reminder_state(state_path, state)
            logger.info("Напоминание о звонке: %s в %s", lead.company, lead.next_time)
    except Exception:
        logger.exception("Ошибка цикла напоминаний")


async def scheduled_morning(
    bot: Bot, crm: CrmSheets, settings: Settings
) -> None:
    tz = ZoneInfo(settings.timezone)
    today = datetime.now(tz).date()
    try:
        data = await load_data(crm, settings)
        leads, deals = today_items(data.leads, data.deals, today)
        overdue_leads, overdue_deals = overdue_items(
            data.leads, data.deals, today
        )
        text = morning_text(
            today,
            leads_count=len(leads),
            deals_count=len(deals),
            overdue_count=len(overdue_leads) + len(overdue_deals),
        )
        await send_html(bot, settings.admin_id, text, reply_markup=pult_kb())
    except Exception as exc:
        logger.exception("Ошибка утренней сводки")
        await send_html(
            bot,
            settings.admin_id,
            f"Утренняя сводка не собрана: {esc(exc)}",
        )


async def scheduled_backup(
    bot: Bot, crm: CrmSheets, settings: Settings
) -> None:
    try:
        path = await asyncio.to_thread(
            write_backup,
            crm,
            settings.backups_dir,
            keep=14,
            timezone=settings.timezone,
        )
        logger.info("Бэкап SAMOLA CRM: %s", path)
    except Exception:
        logger.exception("Ошибка JSON-бэкапа SAMOLA CRM")


async def handle_card_action(
    callback: CallbackQuery,
    crm: CrmSheets,
    settings: Settings,
    kind: str,
    item_id: int,
    action: str,
) -> None:
    if callback.message is None:
        await callback.answer("Сообщение устарело, откройте меню заново.", show_alert=True)
        return

    tz = ZoneInfo(settings.timezone)
    today = datetime.now(tz).date()
    data = await load_data(crm, settings)

    if kind == "l":
        lead = next((item for item in data.leads if item.id == item_id), None)
        if lead is None:
            await callback.answer("Лид не найден, обновите таблицу.", show_alert=True)
            return

        if action == "brief":
            await edit_html(
                callback.message,
                format_lead_expanded(lead),
                reply_markup=lead_actions_kb(lead.id),
            )
            await callback.answer("Бриф и заметки раскрыты")
            return

        if action == "collapse":
            await edit_html(
                callback.message,
                format_lead_card(lead),
                reply_markup=lead_card_kb(lead, today),
            )
            await callback.answer("Свернуто")
            return

        if action == "today":
            lead, changes = await asyncio.to_thread(
                crm.update_lead,
                lead,
                next_date=today,
                today=today,
                source="кнопка",
            )
            await edit_html(
                callback.message,
                format_lead_card(lead),
                reply_markup=lead_card_kb(lead, today),
            )
            await callback.answer("Перенесено на сегодня")
            return

        if action in {"done", "close"}:
            if lead.next_date is None:
                await callback.answer("У лида уже нет следующей даты.", show_alert=True)
                return
            reason = "Закрыто досрочно" if action == "close" else "Выполнен шаг"
            lead = await asyncio.to_thread(
                crm.mark_lead_done, lead, today, reason
            )
            text = format_lead_card(lead) + "\n✅ <i>Записано в Историю</i>"
            await edit_html(
                callback.message,
                text,
                reply_markup=lead_card_kb(lead, today),
            )
            await callback.answer(
                "Закрыто досрочно" if action == "close" else "Записано в Историю"
            )
            return

        if action == "plus":
            lead = await asyncio.to_thread(crm.shift_lead_date, lead, 1, today)
            await edit_html(
                callback.message,
                format_lead_card(lead),
                reply_markup=lead_card_kb(lead, today),
            )
            await callback.answer(f"Новая дата: {lead.next_date:%d.%m}")
            return

    else:
        deal = next((item for item in data.deals if item.id == item_id), None)
        if deal is None:
            await callback.answer("Дело не найдено, обновите таблицу.", show_alert=True)
            return

        if action == "done":
            if is_deal_done(deal.status):
                await callback.answer("Это дело уже выполнено.", show_alert=True)
                return
            deal = await asyncio.to_thread(crm.mark_deal_done, deal, today)
            text = format_deal_card(deal) + "\n✅ <i>Записано в Историю</i>"
            await edit_html(callback.message, text, reply_markup=None)
            await callback.answer("Дело выполнено, запись добавлена в Историю")
            return

        if action == "plus":
            deal = await asyncio.to_thread(crm.shift_deal_date, deal, 1, today)
            await edit_html(
                callback.message,
                format_deal_card(deal),
                reply_markup=deal_card_kb(deal, today),
            )
            await callback.answer(f"Новый дедлайн: {deal.deadline:%d.%m}")
            return

    await callback.answer("Неизвестное действие", show_alert=True)


def build_router(settings: Settings, crm: CrmSheets) -> Router:
    router = Router()
    router.message.filter(AdminOnly(settings))
    router.callback_query.filter(AdminOnly(settings))

    pending: dict[str, PendingAction] = {}
    transcriber = Transcriber(
        model_name=settings.whisper_model,
        download_root=settings.whisper_download_root,
        language=settings.whisper_language,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
    )

    def prune_pending(now: datetime) -> None:
        for token, item in list(pending.items()):
            if now - item.created_at > PENDING_TTL:
                pending.pop(token, None)

    async def ask_ai(message: Message, text: str, *, is_voice: bool) -> None:
        tz = ZoneInfo(settings.timezone)
        now = datetime.now(tz)
        text = text.strip()
        if not text:
            await send_html(
                message.bot,
                message.chat.id,
                "Не расслышал. Повторите голосом или напишите текстом.",
                reply_markup=pult_kb(),
            )
            return

        placeholder = await send_html(
            message.bot, message.chat.id, "⏳ Разбираю обновление..."
        )
        try:
            data = await load_data(crm, settings)
            action = await parse_text(
                text=text[:MAX_AI_INPUT],
                data=data,
                now=now,
                api_key=settings.deepseek_api_key,
                model=settings.deepseek_model,
                base_url=settings.deepseek_base_url,
            )
        except Exception as exc:
            logger.exception("ИИ-разбор не удался")
            await edit_html(
                placeholder,
                f"<b>Не удалось разобрать</b>\n{esc(exc)}\n"
                "Попробуйте короче или проверьте DEEPSEEK_API_KEY.",
            )
            return

        if not action.is_known:
            await edit_html(
                placeholder,
                "<b>Не понял формулировку</b>\n"
                f"{esc(action.reason or 'уточните, о каком лиде или деле речь')}\n"
                "Пример: «Хоттабыч, статус переговоры, перезвонить 28.11 в 11:00» "
                "или «Добавь дело: собрать демо 7 Дней до 29.11».",
            )
            return

        prune_pending(now)
        token = secrets.token_urlsafe(8)
        pending[token] = PendingAction(
            action=action,
            owner_id=message.from_user.id if message.from_user else settings.admin_id,
            created_at=now,
            is_voice=is_voice,
        )
        preview = format_preview(action, data.leads, data.deals)
        if is_voice:
            preview = "🎤 <i>Голос распознан</i>\n" + preview
        await edit_html(placeholder, preview, reply_markup=confirm_kb(token))
        logger.info("Подготовлено ИИ-обновление CRM: %s", action.kind)

    @router.message(Command("start", "menu"))
    async def cmd_start(message: Message) -> None:
        try:
            await send_summary(message.bot, crm, settings, message.chat.id)
        except Exception as exc:
            logger.exception("Ошибка /start")
            await send_html(
                message.bot, message.chat.id, f"Ошибка: {esc(exc)}", reply_markup=pult_kb()
            )

    # Точные тексты ReplyKeyboard. Регистрируются ДО обработчика свободного текста.
    @router.message(F.text.in_(set(BUTTON_VIEWS)))
    async def reply_keyboard(message: Message) -> None:
        view = BUTTON_VIEWS.get((message.text or "").strip(), "")
        try:
            if view == "summary":
                await send_summary(message.bot, crm, settings, message.chat.id)
            elif view in VIEWS:
                await send_view(message.bot, crm, settings, message.chat.id, view)
            else:
                await send_summary(message.bot, crm, settings, message.chat.id)
        except Exception as exc:
            logger.exception("Ошибка кнопки %s", message.text)
            await send_html(
                message.bot,
                message.chat.id,
                f"Ошибка: {esc(exc)}",
                reply_markup=pult_kb(),
            )

    @router.callback_query(F.data.startswith("v:"))
    async def cb_view(callback: CallbackQuery) -> None:
        view = (callback.data or "").split(":", 1)[1]
        if callback.message is None:
            await callback.answer()
            return
        if view in {"main", "summary"}:
            await callback.answer()
            await send_summary(
                callback.bot, crm, settings, callback.message.chat.id
            )
            return
        if view not in VIEWS:
            await callback.answer("Неизвестный срез", show_alert=True)
            return
        await callback.answer()
        try:
            await send_view(
                callback.bot, crm, settings, callback.message.chat.id, view
            )
        except Exception as exc:
            logger.exception("Ошибка среза %s", view)
            await send_html(
                callback.bot,
                callback.message.chat.id,
                f"Ошибка: {esc(exc)}",
                reply_markup=pult_kb(),
            )

    @router.callback_query(F.data.startswith("a:"))
    async def cb_action(callback: CallbackQuery) -> None:
        try:
            _, kind, raw_id, action = (callback.data or "").split(":")
            item_id = int(raw_id)
            if kind not in {"l", "d"}:
                raise ValueError("bad kind")
            allowed = {"done", "plus", "brief", "collapse", "today", "close"}
            if action not in allowed:
                raise ValueError("bad action")
        except ValueError:
            await callback.answer("Некорректная кнопка", show_alert=True)
            return

        try:
            await handle_card_action(
                callback, crm, settings, kind, item_id, action
            )
        except Exception as exc:
            logger.exception("Ошибка действия %s над %s", action, raw_id)
            await callback.answer(
                f"Ошибка Google Sheets: {normalize(exc)[:150]}", show_alert=True
            )

    @router.callback_query(F.data.startswith("ai:"))
    async def cb_ai(callback: CallbackQuery) -> None:
        try:
            _, verdict, token = (callback.data or "").split(":")
        except ValueError:
            await callback.answer("Некорректная кнопка", show_alert=True)
            return
        if callback.message is None:
            await callback.answer("Сообщение устарело.", show_alert=True)
            return

        item = pending.pop(token, None)
        now = datetime.now(ZoneInfo(settings.timezone))
        if item is None or now - item.created_at > PENDING_TTL:
            await callback.answer(
                "Подтверждение устарело, отправьте фразу заново.", show_alert=True
            )
            try:
                await edit_html(
                    callback.message,
                    "<b>Подтверждение устарело</b>\nОтправьте фразу заново.",
                )
            except Exception:
                pass
            return

        if verdict == "no":
            await edit_html(callback.message, "<b>Отменено</b>\nВ CRM ничего не записано.")
            await callback.answer("Отменено")
            return

        if verdict != "ok":
            await callback.answer("Некорректная кнопка", show_alert=True)
            return

        try:
            data = await load_data(crm, settings)
            today = now.date()
            result = await asyncio.to_thread(
                apply_action,
                crm,
                item.action,
                leads=data.leads,
                deals=data.deals,
                today=today,
            )
        except Exception as exc:
            logger.exception("Ошибка записи ИИ-обновления в CRM")
            await edit_html(
                callback.message,
                f"<b>Не записано</b>\n{esc(exc)}",
            )
            await callback.answer("Ошибка записи", show_alert=True)
            return

        await edit_html(callback.message, result)
        await callback.answer("Записано в SAMOLA CRM")
        logger.info("ИИ-обновление записано: %s", item.action.kind)

    @router.message(F.voice)
    async def voice_message(message: Message) -> None:
        if message.voice is None:
            return
        await send_html(
            message.bot, message.chat.id, "🎤 Слушаю, распознаю..."
        )
        buffer = io.BytesIO()
        try:
            await message.bot.download(message.voice, destination=buffer)
            audio = buffer.getvalue()
        except Exception as exc:
            logger.exception("Не удалось скачать голосовое")
            await send_html(
                message.bot,
                message.chat.id,
                f"Не удалось получить голосовое: {esc(exc)}",
                reply_markup=pult_kb(),
            )
            return

        try:
            text = await transcriber.transcribe_bytes(audio)
        except VoiceError as exc:
            await send_html(
                message.bot,
                message.chat.id,
                f"<b>Голос не распознан</b>\n{esc(exc)}\nНапишите текстом.",
                reply_markup=pult_kb(),
            )
            return
        except Exception as exc:
            logger.exception("Ошибка распознавания")
            await send_html(
                message.bot,
                message.chat.id,
                f"<b>Голос не распознан</b>\n{esc(exc)}",
                reply_markup=pult_kb(),
            )
            return

        if not text.strip():
            await send_html(
                message.bot,
                message.chat.id,
                "Не расслышал. Повторите голосом или напишите текстом.",
                reply_markup=pult_kb(),
            )
            return
        await ask_ai(message, text, is_voice=True)

    # Свободный текст: только владелец (фильтр роутера), не команды и не кнопки.
    @router.message(F.text & ~F.text.startswith("/") & ~F.text.in_(set(BUTTON_VIEWS)))
    async def free_text(message: Message) -> None:
        await ask_ai(message, message.text or "", is_voice=False)

    return router


async def main() -> None:
    settings = load_settings()
    tz = ZoneInfo(settings.timezone)

    crm = CrmSheets(settings.google_creds_path, settings.spreadsheet_id)
    try:
        title = await asyncio.to_thread(crm.ping)
        logger.info("Google Sheets доступны: %s", title)
    except Exception as exc:
        logger.error("Google Sheets недоступны на старте: %s", exc)

    if settings.deepseek_api_key:
        logger.info(
            "DeepSeek настроен: model=%s base=%s",
            settings.deepseek_model,
            settings.deepseek_base_url,
        )
    else:
        logger.warning(
            "DEEPSEEK_API_KEY не найден: текстовые и голосовые обновления CRM не сработают"
        )

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(settings, crm))

    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        scheduled_reminders,
        IntervalTrigger(minutes=1, timezone=tz),
        args=[bot, crm, settings],
        id="reminders_every_minute",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        scheduled_morning,
        CronTrigger(hour=8, minute=30, timezone=tz),
        args=[bot, crm, settings],
        id="morning_summary",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        scheduled_backup,
        CronTrigger(hour=3, minute=10, timezone=tz),
        args=[bot, crm, settings],
        id="daily_backup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()

    logger.info(
        "Бот запущен. SPREADSHEET_ID=%s, TIMEZONE=%s, WHISPER=%s, BACKUPS_DIR=%s",
        settings.spreadsheet_id,
        settings.timezone,
        settings.whisper_model,
        settings.backups_dir,
    )

    # Первый бэкап сразу после старта, чтобы не ждать 03:10.
    asyncio.create_task(scheduled_backup(bot, crm, settings))

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
