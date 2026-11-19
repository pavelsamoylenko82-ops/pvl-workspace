from __future__ import annotations

from audit import is_social_or_messenger
from models import Lead


def score_lead(lead: Lead, min_reviews: int) -> str:
    """A: нет сайта/не открывается. B: открывается, но есть техпроблемы. C: исправен."""
    if lead.reviews < min_reviews:
        lead.priority = ""
        return lead.priority

    if not lead.website.strip() or is_social_or_messenger(lead.website):
        lead.priority = "A"
        return lead.priority

    status = lead.http_status
    if lead.audit_error or status is None or status >= 400:
        lead.priority = "A"
        return lead.priority

    if lead.https is False or lead.viewport is False or (lead.response_time or 0) > 4:
        lead.priority = "B"
        return lead.priority

    lead.priority = "C"
    return lead.priority
