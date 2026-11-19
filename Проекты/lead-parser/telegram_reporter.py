from __future__ import annotations

from pathlib import Path

import httpx

TELEGRAM_API = "https://api.telegram.org"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def send_report(
    bot_token: str,
    admin_id: int,
    message: str,
    xlsx_path: Path,
) -> None:
    base_url = f"{TELEGRAM_API}/bot{bot_token}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{base_url}/sendMessage",
            data={"chat_id": str(admin_id), "text": message},
        )
        response.raise_for_status()

        with xlsx_path.open("rb") as file:
            response = await client.post(
                f"{base_url}/sendDocument",
                data={
                    "chat_id": str(admin_id),
                    "caption": "Актуальная база БАЗА.xlsx",
                },
                files={"document": (xlsx_path.name, file, XLSX_MIME)},
            )
            response.raise_for_status()
