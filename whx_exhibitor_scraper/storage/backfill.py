"""Backfill country / description / company_name from already-saved raw HTML excerpts."""

from __future__ import annotations

import json
import re
from typing import Any

from extraction.html_parser import (
    ISO_COUNTRY,
    extract_country_from_description,
)
from storage.database import Database


CONTACT_ISO_RE = re.compile(
    r"Contact details\s+([A-Z]{2})\b|\b([A-Z]{2})\s+/widget/",
    re.I,
)
INFO_DESC_RE = re.compile(
    r"Information\s+(.*?)\s+(?:Venue|Country|Hall Numbers|Nature of Business|"
    r"Contact details|Products)",
    re.S | re.I,
)


def _country_from_excerpt(excerpt: str) -> str | None:
    m = CONTACT_ISO_RE.search(excerpt or "")
    if m:
        code = (m.group(1) or m.group(2) or "").upper()
        return ISO_COUNTRY.get(code)
    return None


def _description_from_excerpt(excerpt: str) -> str | None:
    m = INFO_DESC_RE.search(excerpt or "")
    if not m:
        return None
    text = re.sub(r"\s+", " ", m.group(1)).strip()
    return text if len(text) > 40 else None


def backfill_missing_fields(db: Database) -> dict[str, int]:
    """
    Fill empty country / description / company_name using raw_extracted_data
    already stored from prior scrapes (no browser needed).
    """
    updated = {"country": 0, "description": 0, "company_name": 0}
    rows = db.list_details()
    for row in rows:
        raw: dict[str, Any] = {}
        try:
            raw = json.loads(row.get("raw_extracted_data") or "{}")
        except Exception:  # noqa: BLE001
            raw = {}
        excerpt = raw.get("body_excerpt") or ""
        rf = raw.get("raw_fields") or {}
        changed = False
        payload = dict(row)

        if not (payload.get("country") or "").strip():
            country = None
            if isinstance(rf.get("Country"), dict):
                country = (rf["Country"].get("value") or "").strip() or None
            if not country:
                country = _country_from_excerpt(excerpt)
            if not country:
                country = extract_country_from_description(excerpt)
            if country:
                payload["country"] = country
                updated["country"] += 1
                changed = True

        if not (payload.get("description") or "").strip():
            desc = _description_from_excerpt(excerpt)
            if desc:
                payload["description"] = desc
                updated["description"] += 1
                changed = True

        if not (payload.get("company_name") or "").strip():
            # First line after Back in excerpt is usually the company name
            m = re.match(r"Back\s+(.+?)\s+Exhibitors?", excerpt)
            if m:
                payload["company_name"] = m.group(1).strip()
                updated["company_name"] += 1
                changed = True

        if payload.get("booth_number"):
            fixed = (
                str(payload["booth_number"]).replace("Â·", "·").replace("•", "·")
            )
            if fixed != payload["booth_number"]:
                payload["booth_number"] = fixed
                changed = True

        if changed:
            payload["_force_overwrite"] = True
            db.save_details(payload)

    return updated
