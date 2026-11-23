"""CSV / JSON / summary export helpers."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from config import settings
from storage.database import Database


INDEX_CSV_FIELDS = [
    "id",
    "company_name",
    "profile_url",
    "exhibitor_id",
    "venue",
    "booth_number",
    "hall_number",
    "discovered_at",
    "detail_status",
    "retry_count",
    "last_error",
]

DETAIL_CSV_FIELDS = [
    "exhibitor_id",
    "company_name",
    "company_name_alt",
    "description",
    "profile_url",
    "logo_url",
    "event_name",
    "venue",
    "booth_number",
    "hall_number",
    "zone",
    "country",
    "city",
    "address",
    "website",
    "email",
    "phone",
    "contact_person",
    "linkedin_url",
    "facebook_url",
    "instagram_url",
    "nature_of_business",
    "products_services",
    "product_categories",
    "countries_covered",
    "scraped_at",
    "extraction_status",
    "validation_notes",
]


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fields})
    return path


def export_index_csv(db: Database, path: Path | None = None) -> Path:
    return _write_csv(path or settings.EXHIBITOR_URLS_CSV, db.list_index(), INDEX_CSV_FIELDS)


def export_details_csv(db: Database, path: Path | None = None) -> Path:
    return _write_csv(path or settings.DETAILS_CSV, db.list_details(), DETAIL_CSV_FIELDS)


def export_details_json(db: Database, path: Path | None = None) -> Path:
    path = path or settings.DETAILS_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = db.list_details()
    # Drop bulky raw blob from default JSON export root but keep if present
    cleaned = []
    for row in rows:
        item = dict(row)
        raw = item.pop("raw_extracted_data", None)
        if raw:
            try:
                item["raw_extracted_data"] = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                item["raw_extracted_data"] = raw
        cleaned.append(item)
    path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_summary_report(db: Database, path: Path | None = None) -> Path:
    path = path or settings.SUMMARY_REPORT
    report = db.quality_report()
    lines = [
        "WHX Dubai DEC Exhibitor Scrape Summary",
        "=" * 42,
        f"Total unique exhibitors discovered : {report['total']}",
        f"DEC exhibitors identified          : {report['dec']}",
        f"Detail profiles completed          : {report['completed']}",
        f"Pending profiles                   : {report['pending']}",
        f"In-progress profiles               : {report['in_progress']}",
        f"Failed profiles                    : {report['failed']}",
        f"Needs review                       : {report['needs_review']}",
        f"Duplicate URLs logged              : {report['duplicates']}",
        f"Detail rows stored                 : {report['details_total']}",
        f"Missing company names              : {report['missing_company_names']}",
        f"% with website                     : {report['pct_with_website']}",
        f"% with email                       : {report['pct_with_email']}",
        f"% with telephone                   : {report['pct_with_phone']}",
        f"URLs without detail extraction     : {report['urls_without_detail_row']}",
        "",
        "Complete only when pending/failed are accounted for and",
        "every discovered unique profile URL has an extraction status.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
