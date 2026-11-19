from __future__ import annotations

import json
import re
from typing import Any

import httpx


class DeepSeekError(RuntimeError):
    """Ошибка обращения к DeepSeek."""


SYSTEM_PROMPT = """Ты аккуратный редактор личной базы знаний.
Твоя задача: по сообщению пользователя выбрать один файл и вернуть только JSON без пояснений и markdown.

Допустимые поля JSON:
{
  "target_file": "точное имя из списка файлов или ДЕЛА.md",
  "step_date": "YYYY-MM-DD или null",
  "step_text": "короткий следующий шаг или null",
  "journal_entry": "YYYY-MM-DD: краткая запись или null",
  "status": "новый статус карточки или null",
  "phone": "телефон или null",
  "summary": "очень короткая сводка или null"
}

Правила:
1. target_file выбирай только из переданного списка карточек или "ДЕЛА.md".
2. Если в сообщении есть задача/следующий шаг, заполни step_date и step_text. Дату бери из сообщения, а если её нет - сегодняшнюю.
3. Для карточки новую запись журнала верни в journal_entry. Для ДЕЛА.md journal_entry обычно null.
4. status и phone заполняй только если пользователь явно сообщил новый статус или телефон. Не выдумывай данные.
5. Если сообщение не относится к карточкам и делам, всё равно выбери наиболее подходящий файл, но не придумывай факты.
6. Не используй длинные тире. Пиши по-русски, коротко.
7. Верни строго один JSON-объект. Никаких комментариев до или после JSON."""


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
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
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
        raise DeepSeekError(f"DeepSeek API ответил {exc.response.status_code}: {details}") from exc
    except httpx.HTTPError as exc:
        raise DeepSeekError(f"Не удалось связаться с DeepSeek API: {exc}") from exc
    except ValueError as exc:
        raise DeepSeekError("DeepSeek API вернул некорректный JSON") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DeepSeekError("Неожиданный формат ответа DeepSeek API") from exc

    return _extract_json(str(content))
