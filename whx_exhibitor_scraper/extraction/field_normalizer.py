"""Normalize and lightly validate extracted exhibitor fields."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"(\+?\d[\d\s().\-]{6,}\d)")
MULTI_SPACE = re.compile(r"\s+")


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = MULTI_SPACE.sub(" ", str(value)).strip()
    return text or None


def normalize_url(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    if text.startswith("//"):
        text = "https:" + text
    if not re.match(r"^https?://", text, re.I):
        if "." in text and " " not in text:
            text = "https://" + text
        else:
            return text
    parsed = urlparse(text)
    if not parsed.netloc:
        return None
    return text


def normalize_email(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    if text.lower().startswith("mailto:"):
        text = text[7:]
    m = EMAIL_RE.search(text)
    return m.group(0).lower() if m else None


def normalize_phone(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    if text.lower().startswith("tel:"):
        text = text[4:]
    m = PHONE_RE.search(text)
    return clean_text(m.group(1)) if m else text


def join_list(values: list[str] | None) -> str | None:
    if not values:
        return None
    cleaned = [clean_text(v) for v in values]
    cleaned = [v for v in cleaned if v]
    return "; ".join(cleaned) if cleaned else None


def normalize_details(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    for key in (
        "company_name",
        "company_name_alt",
        "description",
        "venue",
        "booth_number",
        "hall_number",
        "zone",
        "country",
        "city",
        "address",
        "contact_person",
        "nature_of_business",
        "products_services",
        "product_categories",
        "countries_covered",
        "event_name",
        "exhibitor_id",
        "validation_notes",
    ):
        if key in out:
            out[key] = clean_text(out.get(key))

    if out.get("booth_number"):
        out["booth_number"] = (
            str(out["booth_number"]).replace("Â·", "·").replace("•", "·")
        )

    out["website"] = normalize_url(out.get("website"))
    out["logo_url"] = normalize_url(out.get("logo_url"))
    out["linkedin_url"] = normalize_url(out.get("linkedin_url"))
    out["facebook_url"] = normalize_url(out.get("facebook_url"))
    out["instagram_url"] = normalize_url(out.get("instagram_url"))
    out["email"] = normalize_email(out.get("email"))
    out["phone"] = normalize_phone(out.get("phone"))
    out["profile_url"] = normalize_url(out.get("profile_url")) or out.get("profile_url")
    return out


def validate_details(details: dict[str, Any]) -> tuple[str, str | None]:
    """
    Returns (extraction_status, validation_notes).
    """
    notes: list[str] = []
    if not details.get("company_name"):
        notes.append("missing_company_name")
    if not details.get("country"):
        notes.append("missing_country")
    if notes and "missing_company_name" in notes:
        return "needs_review", "; ".join(notes)
    # missing_country alone still counts as completed (many pages omit it)
    if notes:
        return "completed", "; ".join(notes)
    return "completed", None
