from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Lead:
    name: str
    address: str = ""
    phone: str = ""
    rating: str = ""
    reviews: int = 0
    website: str = ""
    card_url: str = ""
    http_status: int | None = None
    https: bool | None = None
    viewport: bool | None = None
    response_time: float | None = None
    audit_error: str = ""
    priority: str = ""
    parse_date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
