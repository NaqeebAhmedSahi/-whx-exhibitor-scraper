"""SQLite persistence for exhibitor index and detail records."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import urlparse, urlunparse

from config import settings


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_profile_url(url: str) -> str:
    """Canonicalize profile URL for deduplication."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/")
    # Keep exhibitor id segment; drop query/fragment
    return urlunparse(("https", parsed.netloc or "connections.whxevents.com", path, "", "", ""))


class Database:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or settings.DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=60)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS exhibitors_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_name TEXT,
                    profile_url TEXT NOT NULL UNIQUE,
                    exhibitor_id TEXT,
                    venue TEXT,
                    booth_number TEXT,
                    hall_number TEXT,
                    discovered_at TEXT NOT NULL,
                    detail_status TEXT NOT NULL DEFAULT 'pending',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS exhibitors_details (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exhibitor_id TEXT,
                    company_name TEXT,
                    company_name_alt TEXT,
                    description TEXT,
                    profile_url TEXT NOT NULL UNIQUE,
                    logo_url TEXT,
                    event_name TEXT,
                    venue TEXT,
                    booth_number TEXT,
                    hall_number TEXT,
                    zone TEXT,
                    country TEXT,
                    city TEXT,
                    address TEXT,
                    website TEXT,
                    email TEXT,
                    phone TEXT,
                    contact_person TEXT,
                    linkedin_url TEXT,
                    facebook_url TEXT,
                    instagram_url TEXT,
                    nature_of_business TEXT,
                    products_services TEXT,
                    product_categories TEXT,
                    countries_covered TEXT,
                    raw_extracted_data TEXT,
                    scraped_at TEXT NOT NULL,
                    extraction_status TEXT NOT NULL,
                    validation_notes TEXT
                );

                CREATE TABLE IF NOT EXISTS scrape_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS duplicate_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_url TEXT NOT NULL,
                    company_name TEXT,
                    noted_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_index_status
                    ON exhibitors_index(detail_status);
                CREATE INDEX IF NOT EXISTS idx_index_venue
                    ON exhibitors_index(venue);
                """
            )
            # Migrations for columns added after first release
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(exhibitors_details)").fetchall()
            }
            if "company_name_alt" not in cols:
                conn.execute(
                    "ALTER TABLE exhibitors_details ADD COLUMN company_name_alt TEXT"
                )

    # ------------------------------------------------------------------
    # Index / discovery
    # ------------------------------------------------------------------
    def upsert_index_records(self, records: Iterable[dict[str, Any]]) -> dict[str, int]:
        inserted = 0
        duplicates = 0
        with self.connect() as conn:
            for rec in records:
                url = normalize_profile_url(rec.get("profile_url", ""))
                if not url:
                    continue
                existing = conn.execute(
                    "SELECT id FROM exhibitors_index WHERE profile_url = ?",
                    (url,),
                ).fetchone()
                if existing:
                    duplicates += 1
                    # Fill missing hall/name on re-discovery (hall-by-hall pass)
                    if rec.get("hall_number") or rec.get("company_name"):
                        conn.execute(
                            """
                            UPDATE exhibitors_index
                            SET hall_number=COALESCE(NULLIF(hall_number,''), ?),
                                company_name=COALESCE(NULLIF(company_name,''), ?),
                                booth_number=COALESCE(NULLIF(booth_number,''), ?)
                            WHERE profile_url=?
                            """,
                            (
                                rec.get("hall_number"),
                                rec.get("company_name"),
                                rec.get("booth_number"),
                                url,
                            ),
                        )
                    conn.execute(
                        "INSERT INTO duplicate_log(profile_url, company_name, noted_at) VALUES (?,?,?)",
                        (url, rec.get("company_name"), utc_now()),
                    )
                    continue
                conn.execute(
                    """
                    INSERT INTO exhibitors_index (
                        company_name, profile_url, exhibitor_id, venue,
                        booth_number, hall_number, discovered_at, detail_status
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        rec.get("company_name"),
                        url,
                        rec.get("exhibitor_id"),
                        rec.get("venue"),
                        rec.get("booth_number"),
                        rec.get("hall_number"),
                        rec.get("discovered_at") or utc_now(),
                        settings.STATUS_PENDING,
                    ),
                )
                inserted += 1
        return {"inserted": inserted, "duplicates": duplicates}

    def get_index_stats(self) -> dict[str, int]:
        with self.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM exhibitors_index").fetchone()[0]
            dec = conn.execute(
                "SELECT COUNT(*) FROM exhibitors_index WHERE UPPER(COALESCE(venue,'')) LIKE '%DEC%'"
            ).fetchone()[0]
            by_status = {
                row["detail_status"]: row["c"]
                for row in conn.execute(
                    "SELECT detail_status, COUNT(*) AS c FROM exhibitors_index GROUP BY detail_status"
                )
            }
            dupes = conn.execute("SELECT COUNT(*) FROM duplicate_log").fetchone()[0]
        return {
            "total": total,
            "dec": dec,
            "pending": by_status.get(settings.STATUS_PENDING, 0),
            "in_progress": by_status.get(settings.STATUS_IN_PROGRESS, 0),
            "completed": by_status.get(settings.STATUS_COMPLETED, 0),
            "failed": by_status.get(settings.STATUS_FAILED, 0),
            "needs_review": by_status.get(settings.STATUS_NEEDS_REVIEW, 0),
            "duplicates": dupes,
        }

    def list_index(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM exhibitors_index ORDER BY company_name COLLATE NOCASE"
            ).fetchall()
        return [dict(r) for r in rows]

    def has_completed_details(self, profile_url: str) -> bool:
        """
        True only when a real profile page was scraped successfully.
        Partial / directory-only / failed opens do NOT count as scraped.
        """
        url = normalize_profile_url(profile_url)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM exhibitors_details
                WHERE profile_url=?
                  AND extraction_status=?
                  AND (
                    validation_notes IS NULL
                    OR (
                      validation_notes NOT LIKE '%directory_only%'
                      AND validation_notes NOT LIKE '%profile_open_failed%'
                      AND validation_notes != 'skipped_already_completed'
                    )
                  )
                LIMIT 1
                """,
                (url, settings.STATUS_COMPLETED),
            ).fetchone()
        return row is not None

    def demote_incomplete_completed_rows(self) -> int:
        """
        Fix prior runs that marked Oops/directory-card rows as completed.
        Those are partial — not scraped — so they must be re-queued.
        """
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE exhibitors_details
                SET extraction_status=?
                WHERE extraction_status=?
                  AND (
                    validation_notes LIKE '%directory_only%'
                    OR validation_notes LIKE '%profile_open_failed%'
                    OR validation_notes LIKE '%access_restricted%'
                  )
                """,
                (settings.STATUS_PARTIAL, settings.STATUS_COMPLETED),
            )
            demoted = cur.rowcount
            conn.execute(
                """
                UPDATE exhibitors_index
                SET detail_status=?, last_error=NULL, retry_count=0
                WHERE profile_url IN (
                    SELECT profile_url FROM exhibitors_details
                    WHERE extraction_status=?
                )
                """,
                (settings.STATUS_PENDING, settings.STATUS_PARTIAL),
            )
            return demoted

    def sync_completed_statuses(self) -> int:
        """
        Mark index completed only for rows with a real successful profile scrape.
        """
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE exhibitors_index
                SET detail_status=?, last_error=NULL
                WHERE detail_status != ?
                  AND profile_url IN (
                    SELECT profile_url FROM exhibitors_details
                    WHERE extraction_status=?
                      AND (
                        validation_notes IS NULL
                        OR (
                          validation_notes NOT LIKE '%directory_only%'
                          AND validation_notes NOT LIKE '%profile_open_failed%'
                        )
                      )
                  )
                """,
                (
                    settings.STATUS_COMPLETED,
                    settings.STATUS_COMPLETED,
                    settings.STATUS_COMPLETED,
                ),
            )
            return cur.rowcount

    def count_directory_only(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM exhibitors_details
                WHERE extraction_status=?
                   OR validation_notes LIKE '%directory_only%'
                """,
                (settings.STATUS_PARTIAL,),
            ).fetchone()
        return int(row["c"] if row else 0)

    def remaining_work_count(self) -> dict[str, int]:
        stats = self.get_index_stats()
        with self.connect() as conn:
            real_done = conn.execute(
                """
                SELECT COUNT(*) AS c FROM exhibitors_details
                WHERE extraction_status=?
                  AND (
                    validation_notes IS NULL
                    OR (
                      validation_notes NOT LIKE '%directory_only%'
                      AND validation_notes NOT LIKE '%profile_open_failed%'
                    )
                  )
                """,
                (settings.STATUS_COMPLETED,),
            ).fetchone()["c"]
        directory_only = self.count_directory_only()
        pending = max(0, stats["total"] - int(real_done))
        return {
            "completed": int(real_done),
            "pending": pending,
            "needs_review": stats["needs_review"],
            "total": stats["total"],
            "directory_only": directory_only,
        }

    def list_unfinished_index(self) -> list[dict[str, Any]]:
        """Index rows that do not yet have a real completed profile scrape."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT i.* FROM exhibitors_index i
                WHERE NOT EXISTS (
                    SELECT 1 FROM exhibitors_details d
                    WHERE d.profile_url = i.profile_url
                      AND d.extraction_status = ?
                      AND (
                        d.validation_notes IS NULL
                        OR (
                          d.validation_notes NOT LIKE '%directory_only%'
                          AND d.validation_notes NOT LIKE '%profile_open_failed%'
                        )
                      )
                )
                ORDER BY i.id ASC
                """,
                (settings.STATUS_COMPLETED,),
            ).fetchall()
        return [dict(r) for r in rows]

    def requeue_directory_only_for_enrichment(self) -> int:
        """Re-queue partial / directory-only rows so profile scraping can retry."""
        demoted = self.demote_incomplete_completed_rows()
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE exhibitors_index
                SET detail_status=?, last_error=NULL, retry_count=0
                WHERE profile_url IN (
                    SELECT profile_url FROM exhibitors_details
                    WHERE extraction_status=?
                       OR validation_notes LIKE '%directory_only%'
                       OR validation_notes LIKE '%profile_open_failed%'
                )
                """,
                (settings.STATUS_PENDING, settings.STATUS_PARTIAL),
            )
            return max(demoted, cur.rowcount)

    @staticmethod
    def _venue_sql_labels(venues: list[str] | None) -> list[str] | None:
        """Map GUI venue keys to values stored in exhibitors_index.venue."""
        if not venues:
            return None
        labels: list[str] = []
        for v in venues:
            key = (v or "").upper().replace(" ", "_")
            if key == "DEC":
                labels.append("DEC")
            elif key == "DWTC":
                labels.append("DWTC")
            elif key in {"WHX_TECH", "WHXTECH", "TECH"}:
                labels.append("WHX Tech")
                labels.append("DWTC")  # Tech cards often labeled DWTC booth
            else:
                labels.append(v)
        # unique preserve order
        seen: set[str] = set()
        out: list[str] = []
        for x in labels:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    def claim_next_pending(
        self,
        *,
        include_directory_only: bool = False,
        venues: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """
        Claim the next unfinished exhibitor. Only a real successful profile
        scrape blocks claiming — partial/directory-only never counts as done.
        When venues is set, only rows for those venues are claimed.
        """
        _ = include_directory_only
        venue_labels = self._venue_sql_labels(venues)
        with self.connect() as conn:
            while True:
                if venue_labels:
                    placeholders = ",".join("?" * len(venue_labels))
                    row = conn.execute(
                        f"""
                        SELECT i.* FROM exhibitors_index i
                        WHERE i.detail_status IN (?, ?)
                          AND UPPER(COALESCE(i.venue,'')) IN ({placeholders})
                          AND NOT EXISTS (
                            SELECT 1 FROM exhibitors_details d
                            WHERE d.profile_url = i.profile_url
                              AND d.extraction_status = ?
                              AND (
                                d.validation_notes IS NULL
                                OR (
                                  d.validation_notes NOT LIKE '%directory_only%'
                                  AND d.validation_notes NOT LIKE '%profile_open_failed%'
                                )
                              )
                          )
                        ORDER BY
                            CASE i.detail_status WHEN ? THEN 0 ELSE 1 END,
                            i.retry_count ASC,
                            i.id ASC
                        LIMIT 1
                        """,
                        (
                            settings.STATUS_PENDING,
                            settings.STATUS_FAILED,
                            *[x.upper() for x in venue_labels],
                            settings.STATUS_COMPLETED,
                            settings.STATUS_PENDING,
                        ),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """
                        SELECT i.* FROM exhibitors_index i
                        WHERE i.detail_status IN (?, ?)
                          AND NOT EXISTS (
                            SELECT 1 FROM exhibitors_details d
                            WHERE d.profile_url = i.profile_url
                              AND d.extraction_status = ?
                              AND (
                                d.validation_notes IS NULL
                                OR (
                                  d.validation_notes NOT LIKE '%directory_only%'
                                  AND d.validation_notes NOT LIKE '%profile_open_failed%'
                                )
                              )
                          )
                        ORDER BY
                            CASE i.detail_status WHEN ? THEN 0 ELSE 1 END,
                            i.retry_count ASC,
                            i.id ASC
                        LIMIT 1
                        """,
                        (
                            settings.STATUS_PENDING,
                            settings.STATUS_FAILED,
                            settings.STATUS_COMPLETED,
                            settings.STATUS_PENDING,
                        ),
                    ).fetchone()
                if not row:
                    return None
                if (
                    row["retry_count"] >= settings.MAX_PROFILE_RETRIES
                    and row["detail_status"] == settings.STATUS_FAILED
                ):
                    conn.execute(
                        "UPDATE exhibitors_index SET detail_status=? WHERE id=?",
                        (settings.STATUS_NEEDS_REVIEW, row["id"]),
                    )
                    continue
                conn.execute(
                    "UPDATE exhibitors_index SET detail_status=? WHERE id=?",
                    (settings.STATUS_IN_PROGRESS, row["id"]),
                )
                return dict(row)

    def count_pending_for_venues(self, venues: list[str] | None) -> int:
        labels = self._venue_sql_labels(venues)
        if not labels:
            return self.remaining_work_count()["pending"]
        with self.connect() as conn:
            placeholders = ",".join("?" * len(labels))
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS c FROM exhibitors_index i
                WHERE i.detail_status IN (?, ?, ?)
                  AND UPPER(COALESCE(i.venue,'')) IN ({placeholders})
                  AND NOT EXISTS (
                    SELECT 1 FROM exhibitors_details d
                    WHERE d.profile_url = i.profile_url
                      AND d.extraction_status = ?
                      AND (
                        d.validation_notes IS NULL
                        OR (
                          d.validation_notes NOT LIKE '%directory_only%'
                          AND d.validation_notes NOT LIKE '%profile_open_failed%'
                        )
                      )
                  )
                """,
                (
                    settings.STATUS_PENDING,
                    settings.STATUS_FAILED,
                    settings.STATUS_IN_PROGRESS,
                    *[x.upper() for x in labels],
                    settings.STATUS_COMPLETED,
                ),
            ).fetchone()
        return int(row["c"] if row else 0)

    def venues_needing_discovery(self, venues: list[str]) -> list[str]:
        """Return venue keys that have not finished a discovery pass yet."""
        needed: list[str] = []
        for v in venues:
            if self.get_meta(f"discovery_done:{v}") != "1":
                needed.append(v)
        return needed

    def mark_index_status(
        self,
        profile_url: str,
        status: str,
        *,
        error: str | None = None,
        increment_retry: bool = False,
    ) -> None:
        url = normalize_profile_url(profile_url)
        with self.connect() as conn:
            if increment_retry:
                conn.execute(
                    """
                    UPDATE exhibitors_index
                    SET detail_status=?, last_error=?, retry_count=retry_count+1
                    WHERE profile_url=?
                    """,
                    (status, error, url),
                )
            else:
                conn.execute(
                    """
                    UPDATE exhibitors_index
                    SET detail_status=?, last_error=?
                    WHERE profile_url=?
                    """,
                    (status, error, url),
                )

    def reset_in_progress(self) -> int:
        """On resume, return stuck in_progress rows to pending."""
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE exhibitors_index
                SET detail_status=?
                WHERE detail_status=?
                """,
                (settings.STATUS_PENDING, settings.STATUS_IN_PROGRESS),
            )
            return cur.rowcount

    # ------------------------------------------------------------------
    # Details
    # ------------------------------------------------------------------
    def save_details(self, details: dict[str, Any]) -> bool:
        """
        Persist a detail record. Never overwrite a *real* completed scrape with
        a weaker payload. Partial / failed opens may always be upgraded.
        """
        url = normalize_profile_url(details.get("profile_url", ""))
        if not url:
            return False
        status = details.get("extraction_status") or settings.STATUS_COMPLETED
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT extraction_status, validation_notes, company_name
                FROM exhibitors_details WHERE profile_url=?
                """,
                (url,),
            ).fetchone()
            if existing and existing["extraction_status"] == settings.STATUS_COMPLETED:
                notes = existing["validation_notes"] or ""
                real_done = (
                    "directory_only" not in notes
                    and "profile_open_failed" not in notes
                )
                force = bool(details.get("_force_overwrite"))
                upgrading = status == settings.STATUS_COMPLETED and real_done is False
                if real_done and not force and not upgrading:
                    # Never overwrite a real successful profile scrape
                    return False
                if real_done and status in (
                    settings.STATUS_PARTIAL,
                    settings.STATUS_FAILED,
                    settings.STATUS_PENDING,
                ):
                    return False

            payload = {
                "exhibitor_id": details.get("exhibitor_id"),
                "company_name": details.get("company_name"),
                "company_name_alt": details.get("company_name_alt"),
                "description": details.get("description"),
                "profile_url": url,
                "logo_url": details.get("logo_url"),
                "event_name": details.get("event_name") or settings.EVENT_NAME,
                "venue": details.get("venue"),
                "booth_number": details.get("booth_number"),
                "hall_number": details.get("hall_number"),
                "zone": details.get("zone"),
                "country": details.get("country"),
                "city": details.get("city"),
                "address": details.get("address"),
                "website": details.get("website"),
                "email": details.get("email"),
                "phone": details.get("phone"),
                "contact_person": details.get("contact_person"),
                "linkedin_url": details.get("linkedin_url"),
                "facebook_url": details.get("facebook_url"),
                "instagram_url": details.get("instagram_url"),
                "nature_of_business": details.get("nature_of_business"),
                "products_services": details.get("products_services"),
                "product_categories": details.get("product_categories"),
                "countries_covered": details.get("countries_covered"),
                "raw_extracted_data": json.dumps(details.get("raw_extracted_data") or details, ensure_ascii=False),
                "scraped_at": details.get("scraped_at") or utc_now(),
                "extraction_status": status,
                "validation_notes": details.get("validation_notes"),
            }
            cols = ", ".join(payload.keys())
            placeholders = ", ".join(["?"] * len(payload))
            updates = ", ".join(f"{k}=excluded.{k}" for k in payload if k != "profile_url")
            conn.execute(
                f"""
                INSERT INTO exhibitors_details ({cols})
                VALUES ({placeholders})
                ON CONFLICT(profile_url) DO UPDATE SET {updates}
                """,
                tuple(payload.values()),
            )
        return True

    def list_details(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM exhibitors_details ORDER BY company_name COLLATE NOCASE"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_meta(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO scrape_meta(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM scrape_meta WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def quality_report(self) -> dict[str, Any]:
        stats = self.get_index_stats()
        with self.connect() as conn:
            details_total = conn.execute("SELECT COUNT(*) FROM exhibitors_details").fetchone()[0]
            completed = conn.execute(
                "SELECT COUNT(*) FROM exhibitors_details WHERE extraction_status=?",
                (settings.STATUS_COMPLETED,),
            ).fetchone()[0]
            missing_name = conn.execute(
                "SELECT COUNT(*) FROM exhibitors_details WHERE company_name IS NULL OR TRIM(company_name)=''"
            ).fetchone()[0]
            with_website = conn.execute(
                "SELECT COUNT(*) FROM exhibitors_details WHERE website IS NOT NULL AND TRIM(website)!=''"
            ).fetchone()[0]
            with_email = conn.execute(
                "SELECT COUNT(*) FROM exhibitors_details WHERE email IS NOT NULL AND TRIM(email)!=''"
            ).fetchone()[0]
            with_phone = conn.execute(
                "SELECT COUNT(*) FROM exhibitors_details WHERE phone IS NOT NULL AND TRIM(phone)!=''"
            ).fetchone()[0]
            review = conn.execute(
                "SELECT COUNT(*) FROM exhibitors_index WHERE detail_status=?",
                (settings.STATUS_NEEDS_REVIEW,),
            ).fetchone()[0]
            unmatched = conn.execute(
                """
                SELECT COUNT(*) FROM exhibitors_index i
                LEFT JOIN exhibitors_details d ON i.profile_url = d.profile_url
                WHERE d.profile_url IS NULL
                """
            ).fetchone()[0]

        def pct(n: int, d: int) -> float:
            return round((100.0 * n / d), 2) if d else 0.0

        return {
            **stats,
            "details_total": details_total,
            "details_completed": completed,
            "missing_company_names": missing_name,
            "pct_with_website": pct(with_website, max(completed, 1)),
            "pct_with_email": pct(with_email, max(completed, 1)),
            "pct_with_phone": pct(with_phone, max(completed, 1)),
            "needs_review": review,
            "urls_without_detail_row": unmatched,
        }
