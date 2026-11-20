from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PROJECT_DIR, ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("pvl-lead-api")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
TIMEZONE = os.getenv("TIMEZONE", "Europe/Minsk").strip() or "Europe/Minsk"
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "LEAD_ALLOWED_ORIGINS",
        "https://pavelsamoylenko82-ops.github.io,"
        "https://pavelsamoylenko82-ops.github.io/samola-site,"
        "http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if origin.strip()
]

app = FastAPI(title="PVL lead API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
)


class LeadPayload(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    contact: str = Field(min_length=3, max_length=200)
    service: str = Field(min_length=1, max_length=200)
    source: str = Field(default="samola-site", max_length=120)
    page: str = Field(default="", max_length=500)


def plain_dashes(value: str) -> str:
    return value.replace("\u2014", "-").replace("\u2013", "-")


def build_message(payload: LeadPayload, request: Request) -> str:
    now = datetime.now(ZoneInfo(TIMEZONE)).strftime("%d.%m.%Y %H:%M")
    client_ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "")
    lines = [
        "Новая заявка с сайта SAMOLA Studio",
        f"Имя: {payload.name.strip()}",
        f"Контакт: {payload.contact.strip()}",
        f"Услуга: {payload.service.strip()}",
        f"Источник: {payload.source.strip() or 'samola-site'}",
    ]
    if payload.page.strip():
        lines.append(f"Страница: {payload.page.strip()}")
    lines.append(f"Время: {now} ({TIMEZONE})")
    if client_ip:
        lines.append(f"IP: {client_ip.split(',')[0].strip()}")
    return plain_dashes("\n".join(lines))


async def send_telegram(text: str) -> dict[str, Any]:
    if not BOT_TOKEN or not ADMIN_ID:
        raise HTTPException(status_code=503, detail="Telegram is not configured")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ADMIN_ID,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, json=payload)
        data = response.json()
    except Exception as exc:
        logger.exception("Telegram request failed")
        raise HTTPException(status_code=502, detail="Telegram request failed") from exc

    if response.status_code != 200 or not data.get("ok"):
        description = str(data.get("description", "unknown error"))
        logger.error("Telegram rejected message: status=%s description=%s", response.status_code, description)
        raise HTTPException(status_code=502, detail="Telegram rejected message")

    return data


@app.get("/api/lead")
async def lead_health() -> dict[str, Any]:
    return {"ok": True, "service": "pvl-lead-api"}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True}


@app.post("/api/lead")
async def create_lead(payload: LeadPayload, request: Request) -> dict[str, Any]:
    text = build_message(payload, request)
    await send_telegram(text)
    logger.info("Lead delivered: name=%s service=%s", payload.name, payload.service)
    return {"ok": True, "delivered": True}
