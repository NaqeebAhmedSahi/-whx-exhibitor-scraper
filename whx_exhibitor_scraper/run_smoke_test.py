#!/usr/bin/env python3
"""Bounded end-to-end smoke test: discover a batch, scrape a few profiles, export CSV/JSON."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from browser.chrome_manager import CaptchaDetectedError, ChromeManager
from config import settings
from discovery.exhibitor_collector import ExhibitorCollector
from extraction.profile_extractor import ProfileExtractor
from storage.database import Database
from storage.exporters import (
    export_details_csv,
    export_details_json,
    export_index_csv,
    write_summary_report,
)
from utils.logging_setup import setup_logging


def main() -> int:
    log = setup_logging("whx_smoke")
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Bound the run so the smoke test finishes in a few minutes
    settings.SCROLL_MAX_ROUNDS = 8
    settings.SCROLL_STABILITY_THRESHOLD = 2
    settings.REQUEST_DELAY_SECONDS = 1.0
    settings.PROFILE_DELAY_SECONDS = 0.8
    settings.MAX_PROFILE_RETRIES = 2

    # Fresh smoke DB so results are clear
    smoke_db = settings.OUTPUT_DIR / "smoke_whx_dec_exhibitors.db"
    if smoke_db.exists():
        smoke_db.unlink()
    db = Database(smoke_db)

    chrome = ChromeManager(headless=False, on_status=lambda m: log.info(m))
    max_profiles = 5

    try:
        chrome.start()
        collector = ExhibitorCollector(
            chrome,
            db,
            on_status=lambda m: log.info(m),
            on_progress=lambda p: log.info("progress %s", p),
        )

        log.info("=== Discovery (bounded scroll on DEC EventView) ===")
        result = collector.collect(use_dec_event_view=True)
        index_csv = export_index_csv(db, settings.OUTPUT_DIR / "exhibitor_urls.csv")
        stats = db.get_index_stats()
        log.info("Discovery result: %s", result.get("stats"))
        log.info("Index CSV: %s", index_csv)

        if stats["total"] == 0:
            log.error("No exhibitors discovered — smoke test failed")
            return 1

        log.info("=== Detail extraction (first %s profiles via SPA clicks) ===", max_profiles)
        # Return to top so the first discovered cards are in the DOM for SPA clicks
        chrome.driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1.5)
        extractor = ProfileExtractor(
            chrome,
            db,
            on_status=lambda m: log.info(m),
            on_progress=lambda p: log.info("detail progress %s", p),
        )

        scraped = 0
        while scraped < max_profiles:
            row = db.claim_next_pending()
            if not row:
                break
            url = row["profile_url"]
            name = row.get("company_name")
            try:
                details = extractor.scrape_one(url, name)
                log.info(
                    "OK %s | venue=%s booth=%s country=%s",
                    details.get("company_name"),
                    details.get("venue"),
                    details.get("booth_number"),
                    details.get("country"),
                )
            except CaptchaDetectedError as exc:
                log.error("CAPTCHA during profile scrape: %s", exc)
                db.mark_index_status(url, settings.STATUS_PENDING, error=str(exc))
                # Still export whatever we have so far
                break
            except Exception as exc:  # noqa: BLE001
                log.exception("Profile failed: %s", url)
                db.mark_index_status(
                    url, settings.STATUS_FAILED, error=str(exc), increment_retry=True
                )
            scraped += 1
            time.sleep(settings.PROFILE_DELAY_SECONDS)

        details_csv = export_details_csv(db, settings.OUTPUT_DIR / "whx_dec_exhibitors.csv")
        details_json = export_details_json(db, settings.OUTPUT_DIR / "whx_dec_exhibitors.json")
        summary = write_summary_report(db, settings.OUTPUT_DIR / "scrape_summary.txt")

        csv_text = details_csv.read_text(encoding="utf-8")
        json_data = json.loads(details_json.read_text(encoding="utf-8")) if details_json.stat().st_size else []
        index_lines = index_csv.read_text(encoding="utf-8").strip().splitlines()

        print("\n===== SMOKE TEST RESULTS =====")
        print(f"Index URLs CSV lines : {len(index_lines)} (incl header)")
        print(f"Details CSV path     : {details_csv} ({details_csv.stat().st_size} bytes)")
        print(f"Details JSON path    : {details_json} ({details_json.stat().st_size} bytes)")
        print(f"JSON records         : {len(json_data)}")
        print(f"Summary              : {summary}")
        print(f"DB stats             : {db.get_index_stats()}")
        if json_data:
            sample = {
                k: json_data[0].get(k)
                for k in ("company_name", "venue", "booth_number", "country", "profile_url")
            }
            print(f"Sample record        : {sample}")
        print("==============================\n")

        if len(index_lines) < 2:
            log.error("exhibitor_urls.csv has no data rows")
            return 1
        if not json_data:
            log.error("whx_dec_exhibitors.json is empty — detail scrape did not persist")
            return 2
        if "company_name" not in csv_text:
            log.error("details CSV missing header")
            return 1

        log.info("SMOKE TEST PASSED")
        return 0

    except CaptchaDetectedError as exc:
        log.error("%s", exc)
        print("\nCAPTCHA blocked the smoke test. Solve it in Chrome and re-run.\n")
        print(exc)
        time.sleep(5)
        return 2
    finally:
        chrome.stop()


if __name__ == "__main__":
    raise SystemExit(main())
