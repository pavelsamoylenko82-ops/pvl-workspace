from __future__ import annotations

import asyncio
import re
import time
from typing import Iterable
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from models import Lead

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

SOCIAL_DOMAINS = (
    "instagram.com",
    "vk.com",
    "vk.link",
    "vk.me",
    "t.me",
    "telegram.me",
    "wa.me",
    "whatsapp.com",
    "facebook.com",
    "ok.ru",
)

VIEWPORT_RE = re.compile(r"^\s*viewport\s*$", re.IGNORECASE)


def normalize_site_url(value: str) -> str:
    url = (value or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        return "https://" + url
    return url


def is_social_or_messenger(value: str) -> bool:
    """Соцсеть/мессенджер считаем отсутствием полноценного сайта."""
    url = normalize_site_url(value)
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return False
    if "taplink" in host:
        return True
    return any(host == domain or host.endswith("." + domain) for domain in SOCIAL_DOMAINS)


def _has_viewport(html: str) -> bool:
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return False
    meta = soup.find("meta", attrs={"name": lambda value: bool(value and VIEWPORT_RE.match(value))})
    return meta is not None


async def _audit_one(lead: Lead, client: httpx.AsyncClient, timeout: float) -> None:
    url = normalize_site_url(lead.website)
    if not url or is_social_or_messenger(url):
        return

    started = time.perf_counter()
    try:
        response = await client.get(url, timeout=timeout)
        elapsed = time.perf_counter() - started
        lead.http_status = response.status_code
        lead.https = (urlparse(str(response.url)).scheme or "").lower() == "https"
        lead.viewport = _has_viewport(response.text)
        lead.response_time = round(elapsed, 2)
        lead.audit_error = ""
    except Exception as exc:
        elapsed = time.perf_counter() - started
        lead.http_status = None
        lead.https = None
        lead.viewport = None
        lead.response_time = round(elapsed, 2)
        lead.audit_error = f"{type(exc).__name__}: {exc}"[:250]


async def audit_leads(leads: Iterable[Lead], timeout: float = 10.0, concurrency: int = 8) -> None:
    targets = [lead for lead in leads if lead.website.strip() and not is_social_or_messenger(lead.website)]
    if not targets:
        return

    semaphore = asyncio.Semaphore(max(1, concurrency))
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        verify=False,
        timeout=timeout,
    ) as client:
        async def worker(lead: Lead) -> None:
            async with semaphore:
                await _audit_one(lead, client, timeout)

        await asyncio.gather(*(worker(lead) for lead in targets))
