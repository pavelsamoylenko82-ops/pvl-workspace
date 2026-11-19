from __future__ import annotations

from pathlib import Path

import httpx

TELEGRAM_API = "https://api.telegram.org"


async def send_report(
    bot_token: str,
    admin_id: int,
    message: str,
    csv_path: Path,
) -> None:
    base_url = f"{TELEGRAM_API}/bot{bot_token}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{base_url}/sendMessage",
            data={"chat_id": str(admin_id), "text": message},
        )
        response.raise_for_status()

        with csv_path.open("rb") as file:
            response = await client.post(
                f"{base_url}/sendDocument",
                data={
                    "chat_id": str(admin_id),
                    "caption": "Готовый файл БАЗА.csv",
                },
                files={"document": (csv_path.name, file, "text/csv; charset=utf-8")},
            )
            response.raise_for_status()
