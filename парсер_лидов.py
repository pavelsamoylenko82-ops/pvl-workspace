#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Единый парсер лидов Яндекс.Карт и 2ГИС.

Возможности:
- сбор карточек из Яндекс.Карт и 2ГИС;
- чистка закрытых, государственных и сетевых организаций;
- склейка дублей между источниками;
- сверка с папкой Карточки и прошлыми БАЗА*.csv;
- асинхронная живая проверка сайтов;
- приоритеты A/B/C, зацепка для звонка, CSV с BOM и ТОП.md без BOM.

Примеры запуска:
    python парсер_лидов.py --city "Молодечно" --niche "ремонт окон" --limit 25
    python парсер_лидов.py --city "Минск" --niche "кухни на заказ" --sources yandex
    python парсер_лидов.py --check-only "D:\\учеба\\PVL\\leads\\БАЗА-Минск-кухни-на-заказ-2026-11-22.csv"
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import io
import json
import random
import re
import ssl
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent
CARDS_DIR = ROOT / "Карточки"
LEADS_DIR = ROOT / "leads"

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

LONG_DASH_RE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")
PHONE_RE = re.compile(r"(?:\+?375|80?)\s*\(?\d{2,4}\)?[\d\s()\-]{5,18}\d")
VIEWPORT_RE = re.compile(
    r"<meta\b[^>]*\bname\s*=\s*[\"']?\s*viewport\b", re.IGNORECASE
)

SOCIAL_DOMAINS = {
    "instagram.com": "Instagram",
    "instagr.am": "Instagram",
    "vk.com": "VK",
    "vk.link": "VK",
    "vk.me": "VK",
    "t.me": "Telegram",
    "telegram.me": "Telegram",
    "wa.me": "WhatsApp",
    "whatsapp.com": "WhatsApp",
    "viber.com": "Viber",
    "invite.viber.com": "Viber",
    "viber.click": "Viber",
    "facebook.com": "Facebook",
    "ok.ru": "OK",
    "fb.com": "Facebook",
    "tiktok.com": "TikTok",
    "taplink.cc": "Taplink",
    "taplink.at": "Taplink",
    "taplink.ws": "Taplink",
    "2gis.by": "2ГИС",
    "2gis.ru": "2ГИС",
    "2gis.kz": "2ГИС",
    "yandex.by": "Яндекс Карты",
    "yandex.ru": "Яндекс Карты",
    "yandex.com": "Яндекс Карты",
    "ya.ru": "Яндекс Карты",
}

YANDEX_HOST_RE = re.compile(
    r"(^|\.)yandex\.(ru|by|com|kz|ua)$|(^|\.)ya\.ru$|(^|\.)yastatic\.net$|passport\.",
    re.IGNORECASE,
)

CLOSED_MARKERS = (
    "больше не работает",
    "временно не работает",
    "закрыто навсегда",
    "организация закрыта",
    "организация больше не работает",
    "снесена",
    "нет деятельности",
)

GOV_CHAIN_PATTERNS = (
    r"(?<![а-яa-z])гу(?![а-яa-z])",
    r"(?<![а-яa-z])уз(?![а-яa-z])",
    r"\bоао\s+бел",
    r"\bфилиал\s*№",
    r"\bфилиал\b",
    r"\bпредставительство\b",
    r"\bгосударственн",
    r"\bминистерств",
    r"\bисполком\b",
    r"\bадминистраци",
    r"\bбел(?:телеком|почта|банк|железная)",
    r"\b(?:мтс|а1|евроопт|green|санта|алми|21\s*век)\b",
)

LEGAL_WORDS_RE = re.compile(
    r"\b(?:ооо|оао|зао|чтуп|чуп|уп|ип|гу|уз|филиал|представительство|сеть|магазин)\b",
    re.IGNORECASE,
)

DEFECT_STATUSES = {"НЕТ В КАРТЕ", "ТОЛЬКО СОЦИАЛИ", "СЛОМАН", "БЕЗ МОБИЛКИ"}

CSV_HEADERS = [
    "Название",
    "Ниша",
    "Город",
    "Адрес",
    "Телефон",
    "Рейтинг",
    "Отзывы",
    "Сайт",
    "Соцсети/мессенджеры",
    "Ссылка на карточку",
    "Источник",
    "Статус сайта",
    "Приоритет",
    "Зацепка",
    "Уже в карточках",
    "Дата проверки",
]

CITY_SLUGS = {
    "минск": "minsk",
    "молодечно": "molodechno",
    "брест": "brest",
    "витебск": "vitebsk",
    "гомель": "gomel",
    "гродно": "grodno",
    "могилев": "mogilev",
    "бобруйск": "bobruysk",
    "барановичи": "baranovichi",
    "борисов": "borisov",
    "орша": "orsha",
    "мозырь": "mozir",
    "пинск": "pinsk",
    "лида": "lida",
    "солигорск": "soligorsk",
    "новополоцк": "novopolotsk",
    "полоцк": "polotsk",
    "жодино": "zhodino",
    "светлогорск": "svetlogorsk",
    "речица": "rechitsa",
    "слуцк": "slutsk",
    "жлобин": "zhlobin",
}


class CaptchaDetected(RuntimeError):
    """Источник показал капчу. Уже собранные данные нужно сохранить."""


@dataclass
class Lead:
    name: str
    niche: str = ""
    city: str = ""
    address: str = ""
    phone: str = ""
    rating: str = ""
    reviews: int = 0
    website: str = ""
    socials: str = ""
    card_url: str = ""
    source: str = ""
    status_site: str = ""
    priority: str = ""
    hook: str = ""
    closed: bool = False
    already_contact: str = ""
    parse_date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))


# ---------------------------------------------------------------------------
# Текстовые утилиты и нормализация
# ---------------------------------------------------------------------------


def clean_text(value: object) -> str:
    text = str(value or "").replace("\ufeff", "").replace("\u00a0", " ")
    text = LONG_DASH_RE.sub("-", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я]+", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def normalize_phone(raw: object) -> str:
    """Нормализует реальный белорусский номер к +375 XX XXX-XX-XX.

    Если номер не похож на белорусский, возвращает пустую строку, чтобы не
    выдумывать телефон.
    """
    digits = re.sub(r"\D", "", str(raw or ""))
    if not digits:
        return ""

    core = ""
    if digits.startswith("375") and len(digits) >= 12:
        core = "0" + digits[3:12]
    elif digits.startswith("80") and len(digits) >= 11:
        core = digits[1:11]
    elif digits.startswith("0") and len(digits) >= 10:
        core = digits[:10]
    elif len(digits) == 9:
        core = "0" + digits
    elif digits.startswith("375") and len(digits) >= 11:
        core = "0" + digits[3:12]

    core = re.sub(r"\D", "", core)
    if len(core) != 10 or not core.startswith("0"):
        return ""
    return f"+375 {core[1:3]} {core[3:6]}-{core[6:8]}-{core[8:10]}"


def phone_key(phone: object) -> str:
    digits = re.sub(r"\D", "", str(phone or ""))
    return digits[-9:] if len(digits) >= 9 else ""


def extract_phones_from_text(text: object) -> list[str]:
    found: list[str] = []
    for match in PHONE_RE.finditer(str(text or "")):
        phone = normalize_phone(match.group(0))
        if phone and phone not in found:
            found.append(phone)
    return found


def parse_reviews(value: object) -> int:
    match = re.search(r"\d[\d\s]*", str(value or ""))
    if not match:
        return 0
    try:
        return int(re.sub(r"\D", "", match.group(0)) or "0")
    except ValueError:
        return 0


def parse_rating(value: object) -> str:
    match = re.search(r"\d[.,]\d", str(value or ""))
    return match.group(0).replace(",", ".") if match else ""


def base_name(name: str) -> str:
    text = normalize_text(name)
    text = LEGAL_WORDS_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip() or normalize_text(name)


def street_key(address: str) -> str:
    text = normalize_text(address)
    if not text:
        return ""
    marker = re.search(
        r"\b(?:ул|улица|пр|проспект|пер|переулок|шоссе|тракт|площадь|пл|мкр|микрорайон)\b",
        text,
    )
    if marker:
        text = text[marker.start():]
    words = [word for word in text.split() if word not in {"дом", "д", "беларусь", "рб"}]
    return " ".join(words[:5])


def name_street_key(lead: Lead) -> str:
    name = normalize_text(lead.name)
    street = street_key(lead.address)
    if name and street:
        return f"{name}|{street}"
    return ""


def host_of(url: str) -> str:
    try:
        return (urlparse(url if "://" in url else "https://" + url).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def normalize_url(value: object) -> str:
    url = clean_text(value)
    if not url:
        return ""
    if re.match(r"^(?:tel|mailto|javascript):", url, flags=re.IGNORECASE):
        return ""
    if url.startswith("//"):
        return "https:" + url
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        return "https://" + url
    return url


def unwrap_2gis_url(url: str) -> str:
    """Достаёт настоящий адрес из редиректа link.2gis.com."""
    raw = clean_text(url)
    if "link.2gis.com" not in host_of(raw):
        return raw
    match = re.search(r"link\.2gis\.com/[^/]+/[^/]+/([^/?#]+)", raw)
    if not match:
        return ""
    token = unquote(match.group(1))
    token += "=" * (-len(token) % 4)
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8", errors="ignore")
    except Exception:
        return ""
    first = (decoded.splitlines() or [""])[0].strip()
    return first if re.match(r"^https?://", first, flags=re.IGNORECASE) else ""


def social_label(url: str) -> str:
    host = host_of(url)
    for domain, label in SOCIAL_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return label
    if "taplink" in host:
        return "Taplink"
    return ""


def is_social_or_aggregator(url: str) -> bool:
    return bool(social_label(url))


def is_yandex_host(host: str) -> bool:
    return bool(YANDEX_HOST_RE.search(host or ""))


def add_social(existing: str, *urls: str) -> str:
    items = [clean_text(part) for part in re.split(r"[;\n]+", existing or "") if clean_text(part)]
    for url in urls:
        url = clean_text(url)
        if not url:
            continue
        label = social_label(url)
        item = f"{label}: {url}" if label else url
        if item not in items and all(url not in old for old in items):
            items.append(item)
    return "; ".join(items)


def status_base(status: str) -> str:
    text = clean_text(status)
    if text.startswith("СЛОМАН"):
        return "СЛОМАН"
    if text.startswith("НОРМ"):
        return "НОРМ"
    return text


# ---------------------------------------------------------------------------
# Живая проверка сайтов
# ---------------------------------------------------------------------------


def has_viewport(html: str) -> bool:
    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html, "html.parser")
            meta = soup.find(
                "meta",
                attrs={"name": lambda value: bool(value and value.strip().lower() == "viewport")},
            )
            return meta is not None
        except Exception:
            pass
    return bool(VIEWPORT_RE.search(html or ""))


def has_contact_form(html: str) -> bool:
    low = (html or "").lower()
    markers = ("<form", "tel:", "t.me", "viber", "whatsapp", "wa.me", "telegram")
    return any(marker in low for marker in markers)


def _ssl_like_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "ssl" in text or "certificate" in text or "cert" in text


async def audit_one(lead: Lead, client: "httpx.AsyncClient", semaphore: asyncio.Semaphore, timeout: float) -> None:
    async with semaphore:
        url = normalize_url(lead.website)
        if not url:
            lead.status_site = "ТОЛЬКО СОЦИАЛИ" if clean_text(lead.socials) else "НЕТ В КАРТЕ"
            return

        label = social_label(url)
        if label:
            lead.socials = add_social(lead.socials, url)
            lead.website = ""
            lead.status_site = "ТОЛЬКО СОЦИАЛИ"
            return

        try:
            response = await client.get(url, timeout=timeout)
        except httpx.TimeoutException:
            lead.status_site = "СЛОМАН (таймаут)"
            return
        except ssl.SSLError:
            lead.status_site = "СЛОМАН (SSL)"
            return
        except Exception as exc:
            reason = "SSL" if _ssl_like_error(exc) else ("ошибка соединения" if httpx else "проверка недоступна")
            lead.status_site = f"СЛОМАН ({reason})"
            return

        if response.status_code >= 400:
            lead.status_site = f"СЛОМАН (HTTP {response.status_code})"
            return

        final_url = str(response.url)
        final_label = social_label(final_url)
        if final_label:
            lead.socials = add_social(lead.socials, final_url)
            lead.website = ""
            lead.status_site = "ТОЛЬКО СОЦИАЛИ"
            return

        html = response.text or ""
        if not has_viewport(html):
            lead.status_site = "БЕЗ МОБИЛКИ"
            return
        if not has_contact_form(html):
            lead.status_site = "БЕЗ ЗАЯВКИ"
            return
        lead.status_site = "НОРМ (под ИИ-консультанта)"


async def audit_leads(leads: Iterable[Lead], timeout: float = 6.0, concurrency: int = 8) -> None:
    if httpx is None:
        for lead in leads:
            lead.status_site = "СЛОМАН (нет httpx)"
        return

    targets = list(leads)
    if not targets:
        return
    semaphore = asyncio.Semaphore(max(1, concurrency))
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    limits = httpx.Limits(max_connections=max(4, concurrency), max_keepalive_connections=4)
    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        verify=False,
        timeout=timeout,
        limits=limits,
    ) as client:
        await asyncio.gather(*(audit_one(lead, client, semaphore, timeout) for lead in targets))


# ---------------------------------------------------------------------------
# Приоритет и зацепка
# ---------------------------------------------------------------------------


def reviews_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "отзыв"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "отзыва"
    return "отзывов"


def make_hook(lead: Lead) -> str:
    count = max(0, int(lead.reviews or 0))
    prefix = f"{count} {reviews_word(count)}" if count else "нет отзывов"
    kind = status_base(lead.status_site)

    if kind == "НЕТ В КАРТЕ":
        suffix = "сайта нет в карточке"
    elif kind == "ТОЛЬКО СОЦИАЛИ":
        label = ""
        for part in re.split(r"[;\n]+", lead.socials or ""):
            found = social_label(part.split(": ", 1)[-1].strip())
            if found:
                label = found
                break
        suffix = f"вместо сайта только {label}" if label else "вместо сайта только соцсети"
    elif kind == "СЛОМАН":
        match = re.search(r"\(([^)]+)\)", lead.status_site)
        reason = match.group(1) if match else "сайт не отвечает"
        suffix = f"сайт не открывается ({reason})"
    elif kind == "БЕЗ МОБИЛКИ":
        suffix = "сайт без мобильной версии"
    elif kind == "БЕЗ ЗАЯВКИ":
        suffix = "на сайте нет формы заявки"
    elif kind == "НОРМ":
        suffix = "сайт работает, форма заявки есть"
    else:
        suffix = "сайт не проверен"

    if not lead.phone:
        suffix += ", телефона нет"
    return clean_text(f"{prefix}, {suffix}")


def compute_priority(lead: Lead) -> str:
    kind = status_base(lead.status_site)
    has_phone = bool(lead.phone)
    reviews = int(lead.reviews or 0)

    if has_phone and reviews >= 15 and kind in DEFECT_STATUSES:
        return "A"
    if has_phone and ((reviews < 15 and kind in DEFECT_STATUSES) or kind == "БЕЗ ЗАЯВКИ"):
        return "B"
    return "C"


def finalize_lead(lead: Lead) -> None:
    lead.status_site = clean_text(lead.status_site)
    lead.priority = compute_priority(lead)
    lead.hook = make_hook(lead)


def priority_rank(priority: str) -> int:
    return {"A": 0, "B": 1, "C": 2}.get((priority or "").strip().upper(), 9)


def sort_leads(leads: list[Lead]) -> list[Lead]:
    return sorted(leads, key=lambda lead: (priority_rank(lead.priority), -int(lead.reviews or 0), lead.name.lower()))


# ---------------------------------------------------------------------------
# Чистка, дедупликация и сверка с карточками
# ---------------------------------------------------------------------------


def is_closed_lead(lead: Lead) -> bool:
    text = " ".join((lead.name, lead.address, lead.website, lead.socials)).lower().replace("ё", "е")
    return any(marker in text for marker in CLOSED_MARKERS)


def is_gov_or_chain(lead: Lead, base_counts: Counter[str]) -> bool:
    name = lead.name or ""
    low = name.lower().replace("ё", "е")
    for pattern in GOV_CHAIN_PATTERNS:
        if re.search(pattern, low, flags=re.IGNORECASE):
            return True
    base = base_name(name)
    return bool(base and base_counts.get(base, 0) > 3)


def merge_leads(target: Lead, other: Lead) -> Lead:
    target.name = target.name or other.name
    target.niche = target.niche or other.niche
    target.city = target.city or other.city
    if len(other.address or "") > len(target.address or ""):
        target.address = other.address
    target.phone = target.phone or other.phone
    target.rating = target.rating or other.rating
    target.reviews = max(int(target.reviews or 0), int(other.reviews or 0))
    if not target.website or (is_social_or_aggregator(target.website) and other.website and not is_social_or_aggregator(other.website)):
        target.website = other.website
    for part in re.split(r"[;\n]+", other.socials or ""):
        item = part.strip()
        if item:
            target.socials = add_social(target.socials, item.split(": ", 1)[-1])
    if other.website and is_social_or_aggregator(other.website):
        target.socials = add_social(target.socials, other.website)
    cards = [part.strip() for part in re.split(r"[;|]+", f"{target.card_url};{other.card_url}") if part.strip()]
    target.card_url = "; ".join(dict.fromkeys(cards))
    sources = [part.strip() for part in re.split(r"[,;]+", f"{target.source},{other.source}") if part.strip()]
    target.source = ", ".join(dict.fromkeys(sources))
    target.closed = target.closed or other.closed
    target.already_contact = target.already_contact or other.already_contact
    return target


def deduplicate_leads(leads: list[Lead]) -> list[Lead]:
    result: list[Lead] = []
    by_phone: dict[str, Lead] = {}
    by_name_street: dict[str, Lead] = {}

    for lead in leads:
        pkey = phone_key(lead.phone)
        nkey = name_street_key(lead)
        existing = by_phone.get(pkey) if pkey else None
        if existing is None and nkey:
            existing = by_name_street.get(nkey)
        if existing is not None:
            merge_leads(existing, lead)
            if pkey:
                by_phone[pkey] = existing
            if nkey:
                by_name_street[nkey] = existing
            continue

        result.append(lead)
        if pkey:
            by_phone[pkey] = lead
        if nkey:
            by_name_street[nkey] = lead
    return result


@dataclass
class KnownContacts:
    phones: set[str] = field(default_factory=set)
    names: set[str] = field(default_factory=set)

    def match(self, lead: Lead) -> str:
        pkey = phone_key(lead.phone)
        if pkey and pkey in self.phones:
            return "телефон"
        nkey = normalize_text(lead.name)
        if nkey and nkey in self.names:
            return "название"
        return ""


def _known_name_variants(value: str) -> set[str]:
    text = clean_text(value)
    variants = {normalize_text(text)}
    no_city = re.sub(r"\s*\([^)]*\)\s*$", "", text)
    variants.add(normalize_text(no_city))
    variants.add(normalize_text(Path(no_city).stem))
    return {item for item in variants if item}


def load_known_contacts() -> KnownContacts:
    known = KnownContacts()

    if CARDS_DIR.exists():
        for path in CARDS_DIR.rglob("*"):
            if not path.is_file():
                continue
            try:
                if path.suffix.lower() in {".md", ".txt", ".json", ".csv"}:
                    text = path.read_text(encoding="utf-8-sig", errors="ignore")
                else:
                    continue
            except Exception:
                continue
            for phone in extract_phones_from_text(text):
                known.phones.add(phone_key(phone))
            for variant in _known_name_variants(path.stem):
                known.names.add(variant)
            for line in text.splitlines():
                match = re.match(r"^\s*(?:компания|название)\s*:\s*(.+?)\s*$", line, flags=re.IGNORECASE)
                if match:
                    known.names.update(_known_name_variants(match.group(1)))
                if line.lstrip().startswith("# "):
                    known.names.update(_known_name_variants(line.lstrip()[2:]))

    for path in list(ROOT.glob("БАЗА*.csv")) + list(LEADS_DIR.glob("БАЗА*.csv")):
        try:
            rows = read_csv_rows(path)
        except Exception:
            continue
        for row in rows:
            phone = row_value(row, "телефон", "phone")
            name = row_value(row, "название", "компания", "name")
            if phone:
                normalized = normalize_phone(phone)
                if normalized:
                    known.phones.add(phone_key(normalized))
            if name:
                known.names.update(_known_name_variants(name))

    return known


# ---------------------------------------------------------------------------
# CSV и Markdown
# ---------------------------------------------------------------------------


def row_value(row: dict[str, object], *aliases: str) -> str:
    normalized = {normalize_text(key): value for key, value in row.items() if key is not None}
    for alias in aliases:
        key = normalize_text(alias)
        if key in normalized and normalized[key] is not None:
            return clean_text(normalized[key])
    return ""


def read_csv_rows(path: Path) -> list[dict[str, object]]:
    raw = path.read_bytes()
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        return []
    first = text.splitlines()[0] if text.splitlines() else ""
    delimiter = ";" if first.count(";") >= first.count(",") else ","
    try:
        return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
    except Exception:
        return []


def lead_from_row(row: dict[str, object]) -> Lead:
    return Lead(
        name=row_value(row, "название", "компания", "name"),
        niche=row_value(row, "ниша", "niche", "запрос", "query"),
        city=row_value(row, "город", "city"),
        address=row_value(row, "адрес", "address"),
        phone=normalize_phone(row_value(row, "телефон", "phone")) or row_value(row, "телефон", "phone"),
        rating=parse_rating(row_value(row, "рейтинг", "rating")),
        reviews=parse_reviews(row_value(row, "отзывы", "число отзывов", "reviews")),
        website=row_value(row, "сайт", "website", "url"),
        socials=row_value(row, "соцсети/мессенджеры", "соцсети", "socials", "мессенджер"),
        card_url=row_value(row, "ссылка на карточку", "ссылка_на_карточку", "card_url", "карточка"),
        source=row_value(row, "источник", "source"),
        status_site=row_value(row, "статус сайта", "status_site", "статус"),
        priority=row_value(row, "приоритет", "priority"),
        hook=row_value(row, "зацепка", "hook"),
        already_contact=row_value(row, "уже в карточках", "already_contact"),
        parse_date=row_value(row, "дата проверки", "дата_парсинга") or datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


def load_leads_csv(path: Path) -> list[Lead]:
    return [lead_from_row(row) for row in read_csv_rows(path) if row_value(row, "название", "компания", "name")]


def write_leads_csv(path: Path, leads: Iterable[Lead]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for lead in leads:
            writer.writerow(
                {
                    "Название": clean_text(lead.name),
                    "Ниша": clean_text(lead.niche),
                    "Город": clean_text(lead.city),
                    "Адрес": clean_text(lead.address),
                    "Телефон": clean_text(lead.phone),
                    "Рейтинг": clean_text(lead.rating),
                    "Отзывы": int(lead.reviews or 0),
                    "Сайт": clean_text(lead.website),
                    "Соцсети/мессенджеры": clean_text(lead.socials),
                    "Ссылка на карточку": clean_text(lead.card_url),
                    "Источник": clean_text(lead.source),
                    "Статус сайта": clean_text(lead.status_site),
                    "Приоритет": clean_text(lead.priority),
                    "Зацепка": clean_text(lead.hook),
                    "Уже в карточках": clean_text(lead.already_contact),
                    "Дата проверки": clean_text(lead.parse_date),
                }
            )


def slugify_filename(value: str) -> str:
    text = clean_text(value)
    text = re.sub(r'[<>:"/\\|?*]+', "", text)
    text = re.sub(r"\s+", "-", text)
    return text.strip(".-") or "без-названия"


def md_escape(value: object) -> str:
    return clean_text(value).replace("|", "\\|")


def write_top_md(path: Path, leads: list[Lead], city: str, niche: str, generated: date) -> None:
    selected = [lead for lead in sort_leads(leads) if lead.priority in {"A", "B"}][:20]
    lines = [
        f"# ТОП лидов: {clean_text(city)}, {clean_text(niche)}",
        "",
        f"Дата: {generated.isoformat()}. Приоритеты A и B, первые 20.",
        "",
        "| Компания | Телефон | Отзывы | Статус сайта | Зацепка | Карточка |",
        "|---|---|---:|---|---|---|",
    ]
    if not selected:
        lines.append("| Нет лидов A/B | - | 0 | - | - | - |")
    else:
        for lead in selected:
            card = f"[Открыть]({clean_text(lead.card_url)})" if lead.card_url else "-"
            lines.append(
                "| "
                + " | ".join(
                    [
                        md_escape(lead.name),
                        md_escape(lead.phone or "нет"),
                        str(int(lead.reviews or 0)),
                        md_escape(lead.status_site),
                        md_escape(lead.hook),
                        card,
                    ]
                )
                + " |"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Playwright: Яндекс.Карты
# ---------------------------------------------------------------------------


class YandexMapsSource:
    def __init__(
        self,
        city: str,
        niche: str,
        limit: int,
        on_lead: Callable[[Lead], None] | None = None,
        headless: bool = True,
        timeout_ms: int = 45000,
    ) -> None:
        self.city = city
        self.niche = niche
        self.limit = max(1, limit)
        self.on_lead = on_lead
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.leads: list[Lead] = []
        self.captcha_detected = False

    async def parse(self) -> tuple[list[Lead], bool]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright не установлен") from exc

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            context = await browser.new_context(
                locale="ru-RU",
                timezone_id="Europe/Minsk",
                viewport={"width": 1440, "height": 960},
                user_agent=USER_AGENT,
                ignore_https_errors=True,
            )
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )
            page = await context.new_page()
            try:
                try:
                    await self._load_search(page)
                except CaptchaDetected:
                    raise
                except Exception as exc:
                    print(f"  Яндекс: выдача не загрузилась: {type(exc).__name__}: {exc}")
                    return self.leads, self.captcha_detected

                links = await self._collect_links(page)
                print(f"  Яндекс: найдено ссылок на карточки: {len(links)}")
                candidate_limit = min(len(links), max(self.limit * 4, 40))
                for index, card_url in enumerate(links[:candidate_limit], start=1):
                    if len(self.leads) >= self.limit:
                        break
                    print(f"  Яндекс: карточка {index}/{candidate_limit}")
                    try:
                        lead = await self._parse_card(page, card_url)
                    except CaptchaDetected:
                        raise
                    except Exception as exc:
                        print(f"    пропуск: {type(exc).__name__}: {exc}")
                        await asyncio.sleep(random.uniform(0.8, 1.6))
                        continue
                    if lead and lead.name:
                        if lead.closed:
                            print(f"    - {lead.name}: закрыта")
                        else:
                            self.leads.append(lead)
                            print(f"    + {lead.name} | отзывы: {lead.reviews} | сайт: {lead.website or 'нет'}")
                            if self.on_lead:
                                self.on_lead(lead)
                    await asyncio.sleep(random.uniform(1.2, 2.8))
            except CaptchaDetected:
                self.captcha_detected = True
                print("  ВНИМАНИЕ: Яндекс.Карты показали капчу. Собранное сохраняю, 2ГИС продолжаю.")
            finally:
                await context.close()
                await browser.close()
        return self.leads, self.captcha_detected

    async def _load_search(self, page) -> None:
        search_url = "https://yandex.ru/maps/?text=" + quote(f"{self.niche} {self.city}")
        print(f"  Яндекс: открываю {search_url}")
        await page.goto(search_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        await page.wait_for_timeout(random.randint(1700, 2800))
        await self._check_captcha(page)
        try:
            await page.wait_for_selector('a[href*="/org/"]', timeout=20000)
        except Exception:
            await self._check_captcha(page)
            body = clean_text(await page.locator("body").inner_text(timeout=3000))
            raise RuntimeError(f"нет карточек в выдаче. Фрагмент: {body[:250]}")

    async def _check_captcha(self, page) -> None:
        try:
            url = page.url.lower()
            body = (await page.locator("body").inner_text(timeout=2500)).lower()
        except Exception:
            return
        markers = ("подтвердите, что вы не робот", "я не робот", "captcha", "smartcaptcha")
        if "captcha" in url or "showcaptcha" in url or any(marker in body for marker in markers):
            raise CaptchaDetected("Яндекс.Карты показали капчу")

    async def _collect_links(self, page) -> list[str]:
        seen: dict[str, None] = {}
        previous = 0
        no_growth = 0
        for _ in range(30):
            await self._check_captcha(page)
            try:
                anchors = await page.locator('a[href*="/org/"]').evaluate_all(
                    "els => els.map(e => e.href || e.getAttribute('href') || '')"
                )
            except Exception:
                anchors = []
            for href in anchors:
                parsed = urlparse(href)
                match = re.search(r"(/maps/org/[^/]+/\d+)", parsed.path)
                if not match:
                    match = re.search(r"(/org/[^/]+/\d+)", parsed.path)
                if match:
                    seen.setdefault(parsed._replace(path=match.group(1) + "/", query="", fragment="").geturl(), None)
            current = len(seen)
            if current >= self.limit * 3:
                break
            no_growth = no_growth + 1 if current <= previous else 0
            previous = current
            if no_growth >= 6:
                break
            await self._scroll(page)
            await page.wait_for_timeout(random.randint(800, 1500))
        return list(seen.keys())

    async def _scroll(self, page) -> None:
        try:
            await page.evaluate(
                "() => { const links = [...document.querySelectorAll('a[href*=\"/org/\"]')];"
                " if (links.length) links[links.length - 1].scrollIntoView({block: 'end'});"
                " else window.scrollBy(0, window.innerHeight); }"
            )
        except Exception:
            pass

    async def _parse_card(self, page, card_url: str) -> Lead | None:
        await page.goto(card_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        await page.wait_for_timeout(random.randint(1200, 2200))
        await self._check_captcha(page)
        try:
            await page.wait_for_selector("h1", timeout=10000)
        except Exception:
            await self._check_captcha(page)

        body = ""
        try:
            body = await page.locator("body").inner_text(timeout=3500)
        except Exception:
            pass
        closed = any(marker in body.lower().replace("ё", "е") for marker in CLOSED_MARKERS)

        name = await self._first_text(page, ("h1", "[itemprop='name']", "[class*='orgpage-header-view__header']"))
        if not name:
            try:
                name = clean_text(await page.title())
            except Exception:
                name = ""
        if not name:
            return None

        phone = await self._extract_phone(page, body)
        address = await self._extract_address(page, body)
        rating, reviews = await self._extract_rating_reviews(page)
        website = await self._extract_website(page)
        socials = await self._extract_socials(page)
        if website and is_social_or_aggregator(website):
            socials = add_social(socials, website)

        return Lead(
            name=name,
            niche=self.niche,
            city=self.city,
            address=address,
            phone=phone,
            rating=rating,
            reviews=reviews,
            website=website,
            socials=socials,
            card_url=card_url,
            source="yandex",
            closed=closed,
        )

    async def _first_text(self, page, selectors: tuple[str, ...]) -> str:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                text = clean_text(await locator.inner_text(timeout=2500))
                if text:
                    return text
            except Exception:
                continue
        return ""

    async def _extract_phone(self, page, body: str) -> str:
        try:
            hrefs = await page.locator("a[href^='tel:']").evaluate_all(
                "els => els.map(e => e.getAttribute('href') || '')"
            )
            for href in hrefs:
                phone = normalize_phone(unquote(str(href).replace("tel:", "", 1)))
                if phone:
                    return phone
        except Exception:
            pass
        for selector in ("[itemprop='telephone']", "[class*='phone']"):
            try:
                text = clean_text(await page.locator(selector).first.inner_text(timeout=2000))
                phone = normalize_phone(text)
                if phone:
                    return phone
            except Exception:
                continue
        phones = extract_phones_from_text(body)
        return phones[0] if phones else ""

    async def _extract_address(self, page, body: str) -> str:
        address = await self._first_text(
            page,
            (
                "[itemprop='address']",
                "a[href*='whatshere']",
                "[class*='business-contacts-view__address']",
                "[class*='orgpage-header-view__address']",
            ),
        )
        if address:
            return address
        match = re.search(r"(?:Адрес\s*\n)([^\n]{5,180})", body, flags=re.IGNORECASE)
        return clean_text(match.group(1)) if match else ""

    async def _extract_website(self, page) -> str:
        try:
            website = await page.evaluate(
                r"""() => {
                    const yandexHost = host => /(^|\.)yandex\.(ru|by|com|kz|ua)$|(^|\.)ya\.ru$|(^|\.)yastatic\.net$|passport\./i.test(host);
                    const unwrap = raw => {
                        try {
                            const url = new URL(raw, location.href);
                            for (const key of ['url', 'to', 'target', 'redir', 'redirect']) {
                                const value = url.searchParams.get(key);
                                if (value && /^https?:\/\//i.test(value)) return value;
                            }
                            return url.href;
                        } catch { return ''; }
                    };
                    const candidates = [];
                    for (const anchor of document.querySelectorAll('a[href]')) {
                        if (anchor.closest('.card-footer-view')) continue;
                        const href = unwrap(anchor.getAttribute('href') || '');
                        if (!/^https?:\/\//i.test(href)) continue;
                        if (/vk\.com\/yandex\.maps|t\.me\/mapsyandex/i.test(href)) continue;
                        let host = '';
                        try { host = new URL(href).hostname.toLowerCase(); } catch { continue; }
                        if (yandexHost(host)) continue;
                        const text = (anchor.textContent || '').trim().toLowerCase();
                        const cls = ((anchor.className || '') + ' ' + (anchor.getAttribute('data-testid') || '')).toLowerCase();
                        let score = 0;
                        if (/website|site|contacts-view__url/.test(cls)) score += 6;
                        if (text === 'сайт' || text.startsWith('сайт') || text === 'перейти на сайт') score += 10;
                        if (/^[\w.-]+\.[a-zа-я]{2,}/i.test(text)) score += 4;
                        candidates.push({href, score});
                    }
                    candidates.sort((a, b) => b.score - a.score);
                    return candidates.length ? candidates[0].href : '';
                }"""
            )
            return normalize_url(website)
        except Exception:
            return ""

    async def _extract_socials(self, page) -> str:
        links: list[str] = []
        try:
            links = await page.locator(
                ".business-contacts-view__social-button a[href]"
            ).evaluate_all("els => els.map(e => e.href || e.getAttribute('href') || '')")
        except Exception:
            links = []
        socials = ""
        for href in links:
            host = host_of(href)
            if social_label(href) and not is_yandex_host(host) and "2gis." not in host:
                if "vk.com/yandex.maps" in href.lower() or "t.me/mapsyandex" in href.lower():
                    continue
                socials = add_social(socials, href)
        return socials

    async def _extract_rating_reviews(self, page) -> tuple[str, int]:
        try:
            data = await page.evaluate(
                r"""() => {
                    const text = document.body.innerText || '';
                    let rating = '';
                    const ratingElement = document.querySelector('[itemprop="ratingValue"]');
                    if (ratingElement) rating = ratingElement.getAttribute('content') || ratingElement.textContent || '';
                    if (!rating) {
                        const match = text.match(/(?:Рейтинг\s*)?(\d[.,]\d)\s*(?:\d[\d\s]*\s*оцен)/i);
                        if (match) rating = match[1];
                    }
                    let reviews = 0;
                    const reviewElement = document.querySelector('[itemprop="reviewCount"]');
                    if (reviewElement) {
                        const raw = reviewElement.getAttribute('content') || reviewElement.textContent || '';
                        const digits = (raw.match(/\d[\d\s]*/) || [''])[0].replace(/\s/g, '');
                        if (digits) reviews = parseInt(digits, 10);
                    }
                    if (!reviews) {
                        const match = text.match(/(\d[\d\s]{0,8})\s*(?:отзыв(?:а|ов)?|оцен(?:ка|ки|ок))/i);
                        if (match) reviews = parseInt(match[1].replace(/\s/g, ''), 10);
                    }
                    return {rating, reviews};
                }"""
            )
            return parse_rating(data.get("rating")), int(data.get("reviews") or 0)
        except Exception:
            return "", 0


# ---------------------------------------------------------------------------
# Playwright: 2ГИС
# ---------------------------------------------------------------------------


class TwoGisSource:
    def __init__(
        self,
        city: str,
        niche: str,
        limit: int,
        on_lead: Callable[[Lead], None] | None = None,
        headless: bool = True,
        timeout_ms: int = 45000,
    ) -> None:
        self.city = city
        self.niche = niche
        self.limit = max(1, limit)
        self.on_lead = on_lead
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.leads: list[Lead] = []
        self.captcha_detected = False

    def _search_url(self) -> str:
        key = self.city.lower().replace("ё", "е").strip()
        slug = CITY_SLUGS.get(key) or re.sub(r"[^a-zа-я0-9]+", "-", key).strip("-")
        return f"https://2gis.by/{slug}/search/{quote(self.niche)}"

    async def parse(self) -> tuple[list[Lead], bool]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright не установлен") from exc

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                locale="ru-RU",
                timezone_id="Europe/Minsk",
                viewport={"width": 1440, "height": 960},
                user_agent=USER_AGENT,
                ignore_https_errors=True,
            )
            page = await context.new_page()
            try:
                try:
                    await self._load_search(page)
                except CaptchaDetected:
                    raise
                except Exception as exc:
                    print(f"  2ГИС: выдача не загрузилась: {type(exc).__name__}: {exc}")
                    return self.leads, self.captcha_detected

                links = await self._collect_links(page)
                print(f"  2ГИС: найдено ссылок на карточки: {len(links)}")
                candidate_limit = min(len(links), max(self.limit * 4, 40))
                for index, card_url in enumerate(links[:candidate_limit], start=1):
                    if len(self.leads) >= self.limit:
                        break
                    print(f"  2ГИС: карточка {index}/{candidate_limit}")
                    try:
                        lead = await self._parse_card(page, card_url)
                    except CaptchaDetected:
                        raise
                    except Exception as exc:
                        print(f"    пропуск: {type(exc).__name__}: {exc}")
                        await asyncio.sleep(random.uniform(0.8, 1.6))
                        continue
                    if lead and lead.name:
                        if lead.closed:
                            print(f"    - {lead.name}: закрыта")
                        else:
                            self.leads.append(lead)
                            print(f"    + {lead.name} | отзывы: {lead.reviews} | сайт: {lead.website or 'нет'}")
                            if self.on_lead:
                                self.on_lead(lead)
                    await asyncio.sleep(random.uniform(1.2, 2.8))
            except CaptchaDetected:
                self.captcha_detected = True
                print("  ВНИМАНИЕ: 2ГИС показал капчу. Собранное сохраняю, остальные источники продолжаю.")
            finally:
                await context.close()
                await browser.close()
        return self.leads, self.captcha_detected

    async def _load_search(self, page) -> None:
        url = self._search_url()
        print(f"  2ГИС: открываю {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        await page.wait_for_timeout(random.randint(2200, 3500))
        await self._check_captcha(page)

        try:
            current_body = (await page.locator("body").inner_text(timeout=2500)).lower()
        except Exception:
            current_body = ""
        if "браузер устарел" in current_body or "/museum" in page.url.lower():
            print("  2ГИС: служебная страница о браузере, перехожу по ссылке 'Перейти на сайт'")
            try:
                await page.get_by_text("Перейти на сайт", exact=False).first.click(timeout=5000)
                await page.wait_for_timeout(random.randint(4000, 6000))
            except Exception as exc:
                raise CaptchaDetected(f"2ГИС не пустил дальше служебной страницы: {type(exc).__name__}") from exc
            await self._check_captcha(page)

        requested = CITY_SLUGS.get(self.city.lower().replace("ё", "е").strip())
        current_path = urlparse(page.url).path.lower()
        if requested and f"/{requested}/" not in current_path and requested != "minsk":
            raise RuntimeError(f"2ГИС перенаправил в другой город: {page.url}")

        try:
            await page.wait_for_selector('a[href*="/firm/"]', timeout=20000)
        except Exception:
            await self._check_captcha(page)
            body = clean_text(await page.locator("body").inner_text(timeout=3000))
            raise RuntimeError(f"нет карточек в выдаче. Фрагмент: {body[:250]}")

    async def _check_captcha(self, page) -> None:
        try:
            url = page.url.lower()
            body = (await page.locator("body").inner_text(timeout=2500)).lower()
        except Exception:
            return
        if "captcha.2gis" in url or "captcha" in url:
            raise CaptchaDetected("2ГИС показал капчу")
        if "подозрительную активность" in body or "подтвердите, что вы не робот" in body:
            raise CaptchaDetected("2ГИС показал капчу")

    async def _collect_links(self, page) -> list[str]:
        seen: dict[str, None] = {}
        previous = 0
        no_growth = 0
        for _ in range(30):
            await self._check_captcha(page)
            try:
                anchors = await page.locator('a[href*="/firm/"]').evaluate_all(
                    "els => els.map(e => e.href || e.getAttribute('href') || '')"
                )
            except Exception:
                anchors = []
            for href in anchors:
                parsed = urlparse(href)
                match = re.search(r"(/[^/]+/firm/\d+)", parsed.path)
                if match:
                    seen.setdefault(parsed._replace(path=match.group(1) + "/", query="", fragment="").geturl(), None)
            current = len(seen)
            if current >= self.limit * 3:
                break
            no_growth = no_growth + 1 if current <= previous else 0
            previous = current
            if no_growth >= 6:
                break
            try:
                await page.evaluate(
                    "() => { const links = [...document.querySelectorAll('a[href*=\"/firm/\"]')];"
                    " if (links.length) links[links.length - 1].scrollIntoView({block: 'end'});"
                    " else window.scrollBy(0, window.innerHeight); }"
                )
            except Exception:
                pass
            await page.wait_for_timeout(random.randint(800, 1500))
        return list(seen.keys())

    async def _parse_card(self, page, card_url: str) -> Lead | None:
        await page.goto(card_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        await page.wait_for_timeout(random.randint(1400, 2400))
        await self._check_captcha(page)

        try:
            current_body = (await page.locator("body").inner_text(timeout=2000)).lower()
        except Exception:
            current_body = ""
        if "браузер устарел" in current_body or "/museum" in page.url.lower():
            try:
                await page.get_by_text("Перейти на сайт", exact=False).first.click(timeout=5000)
                await page.wait_for_timeout(random.randint(3500, 5500))
            except Exception as exc:
                raise CaptchaDetected(f"2ГИС не пустил к карточке: {type(exc).__name__}") from exc
            await self._check_captcha(page)

        body = ""
        try:
            body = await page.locator("body").inner_text(timeout=3500)
        except Exception:
            pass
        closed = any(marker in body.lower().replace("ё", "е") for marker in CLOSED_MARKERS)

        name = await self._first_text(page, ("h1", "[itemprop='name']", "[class*=' _1']"))
        if not name:
            try:
                name = clean_text(await page.title()).replace(" - 2ГИС", "")
            except Exception:
                name = ""
        if not name:
            return None

        phone = await self._extract_phone(page, body)
        address = await self._extract_address(page, body)
        rating, reviews = await self._extract_rating_reviews(page, body)
        website = await self._extract_website(page)
        socials = await self._extract_socials(page)
        if website and is_social_or_aggregator(website):
            socials = add_social(socials, website)
            website = ""

        return Lead(
            name=name,
            niche=self.niche,
            city=self.city,
            address=address,
            phone=phone,
            rating=rating,
            reviews=reviews,
            website=website,
            socials=socials,
            card_url=card_url,
            source="2gis",
            closed=closed,
        )

    async def _first_text(self, page, selectors: tuple[str, ...]) -> str:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                text = clean_text(await locator.inner_text(timeout=2500))
                if text:
                    return text
            except Exception:
                continue
        return ""

    async def _extract_phone(self, page, body: str) -> str:
        try:
            hrefs = await page.locator("a[href^='tel:']").evaluate_all(
                "els => els.map(e => e.getAttribute('href') || '')"
            )
            for href in hrefs:
                phone = normalize_phone(unquote(str(href).replace("tel:", "", 1)))
                if phone:
                    return phone
        except Exception:
            pass
        for selector in ("[itemprop='telephone']", "[data-testid='phone']", "[class*='phone']"):
            try:
                text = clean_text(await page.locator(selector).first.inner_text(timeout=2000))
                phone = normalize_phone(text)
                if phone:
                    return phone
            except Exception:
                continue
        phones = extract_phones_from_text(body)
        return phones[0] if phones else ""

    async def _extract_address(self, page, body: str) -> str:
        address = await self._first_text(
            page,
            ("[itemprop='address']", "[data-testid='address']", "a[href*='/geo/']", "[class*='address']"),
        )
        if address:
            return address
        match = re.search(r"\b(?:[А-ЯЁ][а-яё-]+,\s*)?(?:ул\.|улица|пр-т|проспект|пер\.|шоссе)[^\n]{3,160}", body)
        return clean_text(match.group(0)) if match else ""

    async def _extract_website(self, page) -> str:
        try:
            links = await page.locator("a[href]").evaluate_all(
                "els => els.map(e => ({href: e.href || e.getAttribute('href') || '', text: (e.textContent || '').trim()}))"
            )
        except Exception:
            links = []
        candidates: list[tuple[int, str]] = []
        for item in links:
            raw_href = clean_text(item.get("href"))
            if not raw_href or raw_href.lower().startswith(("tel:", "mailto:")):
                continue
            href = unwrap_2gis_url(raw_href) or normalize_url(raw_href)
            if not href:
                continue
            host = host_of(href)
            if not host or "2gis." in host or is_yandex_host(host) or social_label(href):
                continue
            score = 0
            text = (item.get("text") or "").lower()
            if text in {"сайт", "перейти на сайт"} or text.startswith("сайт"):
                score += 10
            if text and re.match(r"^[\w.-]+\.[a-zа-я]{2,}$", text):
                score += 8
            score += 3
            candidates.append((score, href))
        candidates.sort(reverse=True)
        return candidates[0][1] if candidates else ""

    async def _extract_socials(self, page) -> str:
        try:
            links = await page.locator("a[href]").evaluate_all(
                "els => els.map(e => e.href || e.getAttribute('href') || '')"
            )
        except Exception:
            links = []
        socials = ""
        for raw_href in links:
            href = unwrap_2gis_url(raw_href) or normalize_url(raw_href)
            if not href:
                continue
            host = host_of(href)
            if social_label(href) and not is_yandex_host(host) and "2gis." not in host:
                socials = add_social(socials, href)
        return socials

    async def _extract_rating_reviews(self, page, body: str) -> tuple[str, int]:
        rating = ""
        reviews = 0
        try:
            data = await page.evaluate(
                r"""() => {
                    const text = document.body.innerText || '';
                    let rating = '';
                    let reviews = 0;
                    const r = document.querySelector('[itemprop="ratingValue"]');
                    if (r) rating = r.getAttribute('content') || r.textContent || '';
                    const c = document.querySelector('[itemprop="reviewCount"]');
                    if (c) {
                        const raw = c.getAttribute('content') || c.textContent || '';
                        const m = raw.match(/\d[\d\s]*/);
                        if (m) reviews = parseInt(m[0].replace(/\s/g, ''), 10);
                    }
                    if (!reviews) {
                        const link = document.querySelector('a[href*="tab/reviews"]');
                        if (link) {
                            const m = (link.textContent || '').match(/\d[\d\s]*/);
                            if (m) reviews = parseInt(m[0].replace(/\s/g, ''), 10);
                        }
                    }
                    if (!reviews) {
                        const m = text.match(/(\d[\d\s]{0,8})\s*(?:отзыв|оцен)/ig);
                        if (m) reviews = parseInt(m[0].replace(/\D/g, ''), 10);
                    }
                    if (!rating) {
                        const m = text.match(/(\d[.,]\d)\s*(?:·|\s)\s*\d+\s*(?:отзыв|оцен)/i);
                        if (m) rating = m[1];
                    }
                    return {rating, reviews};
                }"""
            )
            rating = parse_rating(data.get("rating"))
            reviews = int(data.get("reviews") or 0)
        except Exception:
            pass
        if not rating:
            rating = parse_rating(body)
        if not reviews:
            match = re.search(r"(\d[\d\s]{0,8})\s*отзыв", body, flags=re.IGNORECASE)
            if match:
                reviews = parse_reviews(match.group(1))
        return rating, reviews


# ---------------------------------------------------------------------------
# Пайплайн
# ---------------------------------------------------------------------------


def checkpoint_path(city: str, niche: str) -> Path:
    return LEADS_DIR / f".checkpoint-{slugify_filename(city)}-{slugify_filename(niche)}-{date.today().isoformat()}.csv"


def print_warning(message: str) -> None:
    print(f"ВНИМАНИЕ: {message}")


def collect_stats(raw: list[Lead], closed: int, chains: int, duplicates: int, known: int, final: list[Lead]) -> None:
    counts = Counter(lead.priority for lead in final)
    print("\nСТАТИСТИКА")
    print(f"Собрано сырых карточек: {len(raw)}")
    print(f"Отсеяно: {closed + chains + duplicates + known} "
          f"(закрытые: {closed}, гос/сети: {chains}, дубли: {duplicates}, уже в карточках: {known})")
    print(f"В итоговой базе: {len(final)}")
    print(f"Приоритет A: {counts.get('A', 0)}")
    print(f"Приоритет B: {counts.get('B', 0)}")
    print(f"Приоритет C: {counts.get('C', 0)}")


async def run_collect(args: argparse.Namespace) -> int:
    city = clean_text(args.city)
    niche = clean_text(args.niche)
    sources = [item.strip().lower() for item in re.split(r"[,;]+", args.sources or "") if item.strip()]
    if not sources:
        sources = ["yandex", "2gis"]
    if "both" in sources:
        sources = ["yandex", "2gis"]
    invalid = [item for item in sources if item not in {"yandex", "2gis"}]
    if invalid:
        print(f"Ошибка: неизвестный источник {invalid}. Допустимо: yandex,2gis")
        return 1

    LEADS_DIR.mkdir(parents=True, exist_ok=True)
    raw: list[Lead] = []
    captcha_sources: list[str] = []
    cp_path = checkpoint_path(city, niche)

    def save_checkpoint() -> None:
        try:
            combined = deduplicate_leads([lead for lead in raw if not lead.closed])
            write_leads_csv(cp_path, sort_leads(combined))
        except Exception as exc:
            print_warning(f"не удалось сохранить промежуточный файл: {type(exc).__name__}: {exc}")

    for source_name in sources:
        print(f"\nИсточник: {source_name}")
        source = (
            YandexMapsSource(city, niche, args.limit, on_lead=lambda lead: (raw.append(lead), save_checkpoint()))
            if source_name == "yandex"
            else TwoGisSource(city, niche, args.limit, on_lead=lambda lead: (raw.append(lead), save_checkpoint()))
        )
        try:
            leads, captcha = await source.parse()
        except Exception as exc:
            print_warning(f"{source_name} не отработал: {type(exc).__name__}: {exc}")
            continue
        if captcha:
            captcha_sources.append(source_name)
        if not source.leads and not captcha:
            print_warning(f"{source_name} не вернул карточек")

    raw = [lead for lead in raw if not lead.closed]
    closed_count = sum(1 for lead in raw if is_closed_lead(lead))
    stage = [lead for lead in raw if not is_closed_lead(lead)]

    base_counts = Counter(base_name(lead.name) for lead in stage)
    chain_flags = [is_gov_or_chain(lead, base_counts) for lead in stage]
    chains_count = sum(1 for flag in chain_flags if flag)
    stage = [lead for lead, flag in zip(stage, chain_flags) if not flag]

    before_dedup = len(stage)
    stage = deduplicate_leads(stage)
    duplicates_count = before_dedup - len(stage)

    known = load_known_contacts()
    fresh: list[Lead] = []
    known_count = 0
    for lead in stage:
        reason = known.match(lead)
        if reason:
            lead.already_contact = f"да ({reason})"
            known_count += 1
        else:
            lead.already_contact = "нет"
            fresh.append(lead)

    print("\nПроверяю сайты (таймаут 6 сек)...")
    await audit_leads(fresh, timeout=6.0, concurrency=8)
    for lead in fresh:
        finalize_lead(lead)
    fresh = sort_leads(fresh)

    final_path = LEADS_DIR / f"БАЗА-{slugify_filename(city)}-{slugify_filename(niche)}-{date.today().isoformat()}.csv"
    top_path = LEADS_DIR / f"ТОП-{slugify_filename(city)}-{slugify_filename(niche)}-{date.today().isoformat()}.md"
    write_leads_csv(final_path, fresh)
    write_top_md(top_path, fresh, city, niche, date.today())

    collect_stats(raw=raw, closed=closed_count, chains=chains_count, duplicates=duplicates_count, known=known_count, final=fresh)
    if captcha_sources:
        print_warning(f"капча у источников: {', '.join(captcha_sources)}. Второй источник обработан, собранное сохранено.")
    print(f"\nCSV: {final_path}")
    print(f"MD:  {top_path}")
    print(f"Промежуточный файл: {cp_path}")
    return 0


async def run_check_only(path: Path) -> int:
    if not path.exists():
        print(f"Ошибка: файл не найден: {path}")
        return 1
    leads = load_leads_csv(path)
    if not leads:
        print(f"Ошибка: в CSV нет строк: {path}")
        return 1
    print(f"Повторная живая проверка {len(leads)} строк: {path}")
    await audit_leads(leads, timeout=6.0, concurrency=8)
    for lead in leads:
        finalize_lead(lead)
    leads = sort_leads(leads)
    write_leads_csv(path, leads)

    city = next((lead.city for lead in leads if lead.city), "")
    niche = next((lead.niche for lead in leads if lead.niche), "")
    if city and niche:
        top_path = path.parent / f"ТОП-{slugify_filename(city)}-{slugify_filename(niche)}-{date.today().isoformat()}.md"
        write_top_md(top_path, leads, city, niche, date.today())
        print(f"MD: {top_path}")

    counts = Counter(lead.priority for lead in leads)
    print("\nСТАТИСТИКА ПРОВЕРКИ")
    print(f"Проверено: {len(leads)}")
    print(f"Приоритет A: {counts.get('A', 0)}")
    print(f"Приоритет B: {counts.get('B', 0)}")
    print(f"Приоритет C: {counts.get('C', 0)}")
    print(f"CSV обновлён: {path}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Парсер лидов Яндекс.Карт и 2ГИС с живой проверкой сайтов")
    parser.add_argument("--city", help="Город, например: Минск или Молодечно")
    parser.add_argument("--niche", help="Ниша, например: кухни на заказ, ремонт окон, автосервис")
    parser.add_argument("--limit", type=int, default=30, help="Лимит компаний на источник (по умолчанию 30)")
    parser.add_argument("--sources", default="yandex,2gis", help="Источники: yandex,2gis или оба (по умолчанию оба)")
    parser.add_argument("--check-only", type=Path, help="Только повторная проверка сайтов по готовому CSV")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit должен быть >= 1")
    if not args.check_only and (not args.city or not args.niche):
        parser.error("нужны --city и --niche (либо --check-only)")
    return args


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    try:
        if args.check_only:
            exit_code = asyncio.run(run_check_only(args.check_only))
        else:
            exit_code = asyncio.run(run_collect(args))
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
        exit_code = 130
    except Exception as exc:
        print(f"Ошибка выполнения: {type(exc).__name__}: {exc}")
        exit_code = 1
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
