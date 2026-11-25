from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

OFFICIAL_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"


class DeepSeekError(RuntimeError):
    """Ошибка обращения к DeepSeek."""


SYSTEM_PROMPT = """Ты аккуратный ассистент отдела продаж и редактор личной базы знаний.
Твоя задача: разобрать ЛЮБОЕ сообщение пользователя о лидах, звонках, контактах,
компаниях и следующих шагах и вернуть только один JSON-объект без пояснений и markdown.

Допустимые поля JSON:
{
  "action": "create_card" | "update_card" | "update_dela" | "noop",
  "target_file": "точное имя существующего файла из списка или Карточки/Название.md, либо ДЕЛА.md",
  "company": "название компании/лида или null",
  "city": "город или null",
  "contact": "имя контактного лица или способ связи или null",
  "phone": "телефон или null",
  "messenger": "мессенджер/ник/канал или null",
  "website": "текущий сайт или null",
  "socials": "соцсети или null",
  "status": "новый статус воронки или null",
  "journal_entry": "YYYY-MM-DD HH:MM: краткая запись касания или null",
  "step_date": "YYYY-MM-DD или null",
  "step_text": "короткий следующий шаг или null",
  "summary": "очень короткая сводка или null"
}

Правила:
1. Понимай свободную форму. Например: "По Автоэкспресс: позвонил, сказали перезвонить через 7 дней",
   "Имя: Иван, телефон +375...", "Позвонил в Автоэкспресс, 7 дней сказали ждать".
2. Если в сообщении есть новая компания/лид и подходящей карточки нет в списке файлов,
   верни action="create_card", company=<название>, target_file="Карточки/<Название>.md".
   Если город явно указан, можно указать "Карточки/<Название> (<Город>).md".
3. Если подходящая карточка уже есть, верни action="update_card" и точный target_file из списка.
4. Если сообщение относится только к делам, верни action="update_dela", target_file="ДЕЛА.md".
5. Для любой карточки journal_entry обязателен: дата и время из блока "Текущие дата и время"
   в формате YYYY-MM-DD HH:MM плюс краткая суть касания. Не выдумывай факты.
6. status, phone, contact, messenger, website, socials заполняй только если пользователь явно сообщил данные.
7. Если из сообщения следует следующий шаг или дата следующего контакта, заполни step_date и step_text.
   Дату бери из сообщения, а если её нет - сегодняшнюю.
8. Не используй длинные тире. Пиши по-русски, коротко.
9. Верни строго один JSON-объект. Никаких комментариев до или после JSON."""


def normalize_base_url(base_url: str | None) -> str:
    """Приводит base_url к виду без лишних /chat/completions, /responses и /v1/v1."""
    raw = str(base_url or "").strip().rstrip("/")
    if not raw:
        return OFFICIAL_DEEPSEEK_BASE_URL

    # Если передали полный endpoint, оставляем только базу.
    lower = raw.lower()
    for suffix in ("/chat/completions", "/responses"):
        if lower.endswith(suffix):
            raw = raw[: -len(suffix)].rstrip("/")
            lower = raw.lower()

    # Страховка от случайного https://api.deepseek.com/v1/v1.
    while "/v1/v1" in raw.lower():
        index = raw.lower().find("/v1/v1")
        raw = raw[:index] + "/v1" + raw[index + len("/v1/v1") :]

    # Убираем завершающий /v1? Нет: оба варианта официально допустимы.
    return raw


def chat_completions_url(base_url: str | None) -> str:
    """Возвращает строго POST-эндпоинт OpenAI-совместимого chat/completions."""
    base = normalize_base_url(base_url)
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DeepSeekError(f"Некорректный DEEPSEEK_BASE_URL: {base_url!r}")
    if parsed.path.lower().endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _extract_json(content: str) -> dict[str, Any]:
    raw = content.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise DeepSeekError("DeepSeek вернул не JSON")
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise DeepSeekError("Не удалось разобрать JSON от DeepSeek") from exc

    if not isinstance(parsed, dict):
        raise DeepSeekError("DeepSeek вернул JSON не в виде объекта")
    return parsed


async def request_json(
    *,
    api_key: str,
    model: str,
    base_url: str,
    messages: list[dict[str, str]],
    temperature: float = 0.1,
    max_tokens: int = 900,
    timeout: float = 90.0,
) -> dict[str, Any]:
    """POST в OpenAI-совместимый chat/completions с JSON-режимом."""
    payload = {
        "model": model or DEFAULT_DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    url = chat_completions_url(base_url)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=15.0)
        ) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        details = exc.response.text[:500]
        raise DeepSeekError(
            f"DeepSeek API ответил {exc.response.status_code} на {url}: {details}"
        ) from exc
    except httpx.HTTPError as exc:
        raise DeepSeekError(f"Не удалось связаться с DeepSeek API ({url}): {exc}") from exc
    except ValueError as exc:
        raise DeepSeekError("DeepSeek API вернул некорректный JSON") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DeepSeekError("Неожиданный формат ответа DeepSeek API") from exc
    return _extract_json(str(content))


async def request_edit(
    *,
    api_key: str,
    model: str,
    base_url: str,
    card_names: list[str],
    now_text: str,
    user_text: str,
    file_context: str,
) -> dict[str, Any]:
    if not card_names:
        card_names_text = "Карточек пока нет. Доступен только ДЕЛА.md."
    else:
        card_names_text = "\n".join(f"- {name}" for name in card_names)

    user_prompt = f"""Список доступных файлов:
{card_names_text}

Текущие дата и время (Europe/Minsk): {now_text}

Сообщение пользователя:
{user_text}

Текущее содержимое файлов для контекста:
{file_context or "Контекст пуст."}
"""

    payload = {
        "model": model or DEFAULT_DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 1800,
        "response_format": {"type": "json_object"},
    }

    url = chat_completions_url(base_url)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        details = exc.response.text[:500]
        raise DeepSeekError(
            f"DeepSeek API ответил {exc.response.status_code} на {url}: {details}"
        ) from exc
    except httpx.HTTPError as exc:
        raise DeepSeekError(f"Не удалось связаться с DeepSeek API ({url}): {exc}") from exc
    except ValueError as exc:
        raise DeepSeekError("DeepSeek API вернул некорректный JSON") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DeepSeekError("Неожиданный формат ответа DeepSeek API") from exc

    return _extract_json(str(content))

