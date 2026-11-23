"""Phase 4: scrape individual exhibitor profile pages with retries."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from browser.chrome_manager import (
    AccessRestrictedError,
    CaptchaDetectedError,
    ChromeManager,
    SitePageError,
)
from browser.iframe_handler import open_directory
from config import selectors, settings
from extraction.company_parser import parse_profile
from extraction.field_normalizer import normalize_details, validate_details
from storage.database import Database, utc_now
from storage.exporters import export_details_csv, export_details_json, write_summary_report
from utils.retries import exponential_backoff

logger = logging.getLogger("whx_scraper.profiles")


class ProfileExtractor:
    def __init__(
        self,
        chrome: ChromeManager,
        db: Database,
        *,
        on_status: Callable[[str], None] | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        should_pause: Callable[[], bool] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        self.chrome = chrome
        self.db = db
        self.on_status = on_status or (lambda _m: None)
        self.on_progress = on_progress or (lambda _p: None)
        self.should_pause = should_pause or (lambda: False)
        self.should_stop = should_stop or (lambda: False)

    def _emit(self, msg: str) -> None:
        logger.info(msg)
        self.on_status(msg)

    def _wait_pause(self) -> None:
        while self.should_pause() and not self.should_stop():
            time.sleep(0.3)

    def _publish_progress(self, current: dict[str, Any] | None = None) -> None:
        stats = self.db.get_index_stats()
        total = max(stats["total"], 1)
        done = stats["completed"]
        payload = {
            "phase": "details",
            "discovered": stats["total"],
            "dec": stats["dec"],
            "unique_urls": stats["total"],
            "completed": stats["completed"],
            "pending": stats["pending"],
            "failed": stats["failed"],
            "needs_review": stats["needs_review"],
            "progress_pct": round(100.0 * done / total, 2),
        }
        if current:
            payload.update(current)
        self.on_progress(payload)

    def _is_permanent(self, exc: Exception) -> bool:
        if isinstance(exc, AccessRestrictedError):
            return True
        msg = str(exc).lower()
        return any(x in msg for x in ("404", "not found", "410", "gone"))

    def _selected_directory_url(self) -> str:
        """Directory URL for the first selected venue (never force DEC)."""
        venues = getattr(self, "_venues", None) or []
        for key in venues:
            meta = settings.VENUE_TARGETS.get(key)
            if meta and meta.get("directory"):
                return str(meta["directory"])
        return settings.DEC_DIRECTORY_URL

    def _on_directory(self) -> bool:
        assert self.chrome.driver is not None
        url = self.chrome.driver.current_url or ""
        return "/exhibitors/" in url and "/exhibitor/" not in url.split("/exhibitors/")[0][-12:]

    def _ensure_directory(self) -> None:
        """Warm session on the selected venue directory for SPA profile clicks."""
        assert self.chrome.driver is not None
        driver = self.chrome.driver
        if self._on_directory() and driver.find_elements(
            By.CSS_SELECTOR, selectors.EXHIBITOR_CARD_LINKS
        ):
            return
        directory_url = self._selected_directory_url()
        venue_label = ", ".join(getattr(self, "_venues", None) or ["DEC"])
        self._emit(f"Opening {venue_label} directory before profile scraping...")
        open_directory(
            driver,
            self.chrome.wait,
            prefer_direct=True,
            directory_url=directory_url,
        )
        self.chrome.raise_if_blocked()
        self.chrome.wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, selectors.EXHIBITOR_CARD_LINKS)
            )
        )
        time.sleep(1.0)

    def _return_to_directory(self) -> None:
        assert self.chrome.driver is not None
        driver = self.chrome.driver
        try:
            backs = driver.find_elements(By.CSS_SELECTOR, selectors.PROFILE_BACK)
            if backs and backs[0].is_displayed():
                backs[0].click()
                time.sleep(1.0)
                if driver.find_elements(By.CSS_SELECTOR, selectors.EXHIBITOR_CARD_LINKS):
                    return
        except Exception:  # noqa: BLE001
            pass
        try:
            driver.back()
            time.sleep(1.0)
            if driver.find_elements(By.CSS_SELECTOR, selectors.EXHIBITOR_CARD_LINKS):
                return
        except Exception:  # noqa: BLE001
            pass
        # Hard recovery: reload selected venue directory (not always DEC)
        open_directory(
            driver,
            self.chrome.wait,
            prefer_direct=True,
            directory_url=self._selected_directory_url(),
        )
        time.sleep(1.0)

    def _find_card(self, profile_url: str):
        assert self.chrome.driver is not None
        driver = self.chrome.driver
        target = (profile_url or "").split("?")[0].rstrip("/")
        token = target.split("/")[-1]
        for link in driver.find_elements(By.CSS_SELECTOR, selectors.EXHIBITOR_CARD_LINKS):
            href = (link.get_attribute("href") or "").split("?")[0].rstrip("/")
            if href == target or href.endswith(token):
                return link
        return None

    def _search_for_company(self, company_hint: str | None) -> None:
        """Use the directory Search UI to bring a company card into the DOM."""
        if not company_hint:
            return
        assert self.chrome.driver is not None
        driver = self.chrome.driver
        try:
            buttons = driver.find_elements(By.CSS_SELECTOR, selectors.SEARCH_BUTTON)
            if buttons:
                buttons[0].click()
                time.sleep(0.5)
            inputs = driver.find_elements(
                By.CSS_SELECTOR,
                'input[type="search"], input[placeholder*="Search" i], input[aria-label*="Search" i]',
            )
            if not inputs:
                return
            box = inputs[0]
            box.clear()
            # Short query is enough to filter the virtual list
            query = company_hint.strip()[:40]
            box.send_keys(query)
            box.send_keys(Keys.ENTER)
            time.sleep(1.5)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Directory search failed: %s", exc)

    def _clear_search_if_any(self) -> None:
        assert self.chrome.driver is not None
        driver = self.chrome.driver
        try:
            inputs = driver.find_elements(
                By.CSS_SELECTOR,
                'input[type="search"], input[placeholder*="Search" i], input[aria-label*="Search" i]',
            )
            if inputs:
                inputs[0].clear()
                inputs[0].send_keys(Keys.ENTER)
                time.sleep(0.8)
        except Exception:  # noqa: BLE001
            pass

    def _open_profile(self, profile_url: str, company_hint: str | None = None) -> None:
        """
        Open a profile as fast as possible.

        Directory cards are virtualized — most URLs are NOT in the DOM, so
        searching/clicking them wastes seconds per exhibitor. With UC / fast
        mode, go straight to the profile URL (no directory card hunt).
        """
        assert self.chrome.driver is not None
        driver = self.chrome.driver
        use_direct = settings.FAST_DIRECT_PROFILE_NAV or self.chrome.use_undetected

        if use_direct:
            driver.get(profile_url)
            time.sleep(settings.PROFILE_NAV_WAIT_SECONDS)
            self.chrome.raise_if_blocked()
            return

        # Legacy path: try visible card / search (slow on large directories)
        self._ensure_directory()
        link = self._find_card(profile_url)
        if link is None and company_hint:
            self._search_for_company(company_hint)
            link = self._find_card(profile_url)

        if link is not None:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', behavior:'auto'});",
                link,
            )
            time.sleep(0.2)
            ActionChains(driver).move_to_element(link).pause(0.1).click().perform()
            time.sleep(settings.PROFILE_NAV_WAIT_SECONDS)
            self.chrome.raise_if_blocked()
            return

        driver.get(profile_url)
        time.sleep(settings.PROFILE_NAV_WAIT_SECONDS)
        self.chrome.raise_if_blocked()

    def scrape_one(
        self, profile_url: str, company_hint: str | None = None
    ) -> dict[str, Any]:
        assert self.chrome.driver is not None
        # Skip only when a real profile scrape already succeeded
        if self.db.has_completed_details(profile_url):
            self.db.mark_index_status(profile_url, settings.STATUS_COMPLETED)
            self._emit(f"Skip already scraped: {company_hint or profile_url}")
            return {
                "profile_url": profile_url,
                "company_name": company_hint,
                "extraction_status": settings.STATUS_COMPLETED,
                "validation_notes": "skipped_already_completed",
            }

        self._emit(f"Opening profile: {company_hint or profile_url}")
        self._open_profile(profile_url, company_hint)

        # Short wait for SPA title/h1 — bs4 parses full HTML afterward
        try:
            WebDriverWait(self.chrome.driver, 8).until(
                lambda d: (d.title or "").strip()
                and (d.title or "").strip().lower() not in {"", "client challenge"}
            )
        except TimeoutException:
            self.chrome.raise_if_blocked()

        time.sleep(0.15)
        self.chrome.raise_if_blocked()
        raw = parse_profile(self.chrome.driver, profile_url)
        details = normalize_details(raw)
        # Fill company from directory hint when profile h1 missing
        if not details.get("company_name") and company_hint:
            details["company_name"] = company_hint
        details["event_name"] = settings.EVENT_NAME
        details["scraped_at"] = utc_now()
        status, notes = validate_details(details)
        details["extraction_status"] = status
        details["validation_notes"] = notes
        if status == settings.STATUS_COMPLETED and not details.get("company_name"):
            details["extraction_status"] = settings.STATUS_PARTIAL
            details["validation_notes"] = "; ".join(
                x for x in (notes, "missing_company_after_open") if x
            )

        self.db.save_details(details)
        if details["extraction_status"] == settings.STATUS_COMPLETED:
            self.db.mark_index_status(profile_url, settings.STATUS_COMPLETED)
        else:
            self.db.mark_index_status(
                profile_url,
                details["extraction_status"]
                if details["extraction_status"]
                in (settings.STATUS_NEEDS_REVIEW, settings.STATUS_FAILED)
                else settings.STATUS_PENDING,
                error=details.get("validation_notes"),
            )
        # Direct-nav mode: skip Back-to-directory (next profile is another get())
        if not (settings.FAST_DIRECT_PROFILE_NAV or self.chrome.use_undetected):
            self._return_to_directory()
            self._clear_search_if_any()
        return details

    def _details_from_directory_row(
        self, row: dict[str, Any], *, note: str | None = None
    ) -> dict[str, Any]:
        return {
            "exhibitor_id": row.get("exhibitor_id"),
            "company_name": row.get("company_name"),
            "profile_url": row["profile_url"],
            "event_name": settings.EVENT_NAME,
            "venue": row.get("venue") or "DEC",
            "booth_number": row.get("booth_number"),
            "hall_number": row.get("hall_number"),
            "scraped_at": utc_now(),
            # Partial = saved card fields only; NOT marked scraped/completed
            "extraction_status": settings.STATUS_PARTIAL,
            "validation_notes": note or settings.DIRECTORY_ONLY_NOTE,
            "raw_extracted_data": {
                "source": "directory_card",
                "profile_pages_blocked": True,
            },
        }

    def _save_directory_record(
        self,
        row: dict[str, Any],
        *,
        note: str | None = None,
        park_as_failed: bool = False,
    ) -> None:
        """Save directory-card fields as partial; do not mark scraped."""
        url = row["profile_url"]
        if self.db.has_completed_details(url):
            self.db.mark_index_status(url, settings.STATUS_COMPLETED)
            return
        details = self._details_from_directory_row(row, note=note)
        self.db.save_details(details)
        # Park as failed (with retry bump) so the same URL is not claimed again
        # immediately in this run; UC switch / requeue brings them back.
        if park_as_failed:
            self.db.mark_index_status(
                url,
                settings.STATUS_FAILED,
                error=(note or "partial_directory_only")[:500],
                increment_retry=True,
            )
        else:
            self.db.mark_index_status(
                url,
                settings.STATUS_PENDING,
                error=(note or "partial_directory_only")[:500],
            )

    def finalize_remaining_from_directory(self, reason: str) -> int:
        """
        Last-resort: keep directory-card fields as partial (not completed) and
        park unfinished index rows as needs_review so this run stops looping.
        Next Start will re-queue them — they are NOT marked scraped.
        """
        unfinished = self.db.list_unfinished_index()
        self._emit(
            f"Profile pages still blocked ({reason}). "
            f"Saving {len(unfinished)} as partial directory data "
            f"(not marked scraped — will retry next run)..."
        )
        saved = 0
        for row in unfinished:
            if self.should_stop():
                break
            self._save_directory_record(row)
            # Park for this run only — not "completed"
            self.db.mark_index_status(
                row["profile_url"],
                settings.STATUS_NEEDS_REVIEW,
                error=f"partial_pending_retry; {reason}"[:500],
            )
            saved += 1
            if saved % 25 == 0:
                self._publish_progress(
                    {
                        "current_company": row.get("company_name"),
                        "current_url": row.get("profile_url"),
                    }
                )
                export_details_csv(self.db)
        export_details_csv(self.db)
        export_details_json(self.db)
        self._emit(
            f"Saved {saved} partial directory records (not counted as scraped)"
        )
        self._publish_progress()
        return saved

    def _try_switch_to_undetected(self) -> bool:
        if not settings.USE_UNDETECTED_FALLBACK:
            return False
        if self.chrome.use_undetected:
            return False
        try:
            self.chrome.switch_to_undetected()
            requeued = self.db.requeue_directory_only_for_enrichment()
            self._emit(
                f"Switched to undetected-chromedriver; "
                f"re-queued {requeued} directory-only rows for profile enrichment"
            )
            # Do not force-open DEC directory in fast/direct mode
            if not (settings.FAST_DIRECT_PROFILE_NAV or self.chrome.use_undetected):
                self._ensure_directory()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to switch to undetected-chromedriver")
            self._emit(f"undetected-chromedriver switch failed: {exc}")
            return False

    def run(self, *, venues: list[str] | None = None) -> dict[str, Any]:
        self._venues = venues
        venue_label = ", ".join(venues) if venues else "all venues"
        self._emit(f"Phase 4: Scraping profiles ({venue_label})")
        demoted = self.db.demote_incomplete_completed_rows()
        if demoted:
            self._emit(
                f"Re-opened {demoted} rows that were wrongly marked complete "
                f"(Oops/directory-only — data was never scraped)"
            )
        synced = self.db.sync_completed_statuses()
        self.db.reset_in_progress()
        requeued = self.db.requeue_directory_only_for_enrichment()
        pending = self.db.count_pending_for_venues(venues)
        if synced:
            self._emit(f"Skipping {synced} exhibitors already scraped (real profile data)")
        if requeued:
            self._emit(f"Re-queued {requeued} partial rows for profile scraping")
        self._emit(
            f"Starting from next pending in [{venue_label}] — {pending} left"
        )

        # Normal Chrome first. If Oops streak hits the limit, switch to
        # undetected-chromedriver. If that also fails, directory fallback.
        oops_streak = 0
        uc_attempted = self.chrome.use_undetected
        # Fast mode: skip warming the directory grid (profiles open by URL)
        if not (settings.FAST_DIRECT_PROFILE_NAV or self.chrome.use_undetected):
            try:
                self._ensure_directory()
            except Exception as exc:  # noqa: BLE001
                self._emit(f"Could not open directory for profile clicks: {exc}")
                if settings.USE_UNDETECTED_FALLBACK and not uc_attempted:
                    if self._try_switch_to_undetected():
                        uc_attempted = True
                        oops_streak = 0
                    else:
                        self.finalize_remaining_from_directory("directory_unavailable")
                        write_summary_report(self.db)
                        return self.db.quality_report()
                else:
                    self.finalize_remaining_from_directory("directory_unavailable")
                    write_summary_report(self.db)
                    return self.db.quality_report()
        else:
            self._emit(
                "Fast mode: direct profile URLs + BeautifulSoup parse "
                "(skip directory card hunt)"
            )

        processed = 0
        while not self.should_stop():
            self._wait_pause()
            if self.should_stop():
                break

            if oops_streak >= settings.SITE_OOPS_STREAK_LIMIT:
                if settings.USE_UNDETECTED_FALLBACK and not uc_attempted:
                    self._emit(
                        f"{oops_streak} consecutive Oops pages on standard Chrome — "
                        "trying undetected-chromedriver"
                    )
                    if self._try_switch_to_undetected():
                        uc_attempted = True
                        oops_streak = 0
                        continue
                self.finalize_remaining_from_directory(
                    f"{oops_streak} consecutive Oops pages"
                    + (" (including undetected-chromedriver)" if uc_attempted else "")
                )
                break

            row = self.db.claim_next_pending(
                include_directory_only=self.chrome.use_undetected,
                venues=getattr(self, "_venues", None),
            )
            if not row:
                self._emit(
                    f"No pending profiles remaining for "
                    f"{', '.join(self._venues) if getattr(self, '_venues', None) else 'all'}"
                )
                break

            url = row["profile_url"]
            name = row.get("company_name")

            # Skip only when real profile data was already scraped
            if self.db.has_completed_details(url):
                self.db.mark_index_status(url, settings.STATUS_COMPLETED)
                self._emit(f"Skip already scraped: {name or url}")
                self._publish_progress({"current_company": name, "current_url": url})
                continue

            self._publish_progress({"current_company": name, "current_url": url})

            success = False
            last_error: Exception | None = None
            for attempt in range(1, 2):
                if self.should_stop():
                    break
                self._wait_pause()
                try:
                    details = self.scrape_one(url, name)
                    status = details.get("extraction_status")
                    if status == settings.STATUS_COMPLETED:
                        self._emit(
                            f"Scraped {details.get('company_name') or name} [completed]"
                            + (" [uc]" if self.chrome.use_undetected else "")
                        )
                        success = True
                        oops_streak = 0
                    else:
                        self._emit(
                            f"Opened but not fully scraped: "
                            f"{details.get('company_name') or name} [{status}] "
                            f"— left pending for retry"
                        )
                        success = False
                    break
                except CaptchaDetectedError as exc:
                    # Leave the CAPTCHA page alone so the user can solve it.
                    # Do NOT navigate to DEC/DWTC directory — that looks "stuck".
                    self._emit(str(exc))
                    self.db.mark_index_status(url, settings.STATUS_PENDING, error=str(exc))
                    raise
                except SitePageError as exc:
                    last_error = exc
                    oops_streak += 1
                    mode = "uc" if self.chrome.use_undetected else "standard"
                    self._emit(
                        f"Site Oops page for {name or url} "
                        f"(streak {oops_streak}/{settings.SITE_OOPS_STREAK_LIMIT}, mode={mode})"
                    )
                    # Park this URL so we do not reclaim it immediately
                    self._save_directory_record(row, park_as_failed=True)
                    # Switch to UC as soon as standard Chrome hits Oops once
                    if (
                        not self.chrome.use_undetected
                        and settings.USE_UNDETECTED_FALLBACK
                        and not uc_attempted
                    ):
                        self._emit(
                            "Oops on standard Chrome — switching to "
                            "undetected-chromedriver now"
                        )
                        if self._try_switch_to_undetected():
                            uc_attempted = True
                            oops_streak = 0
                    break
                except AccessRestrictedError as exc:
                    self._save_directory_record(
                        row, note=f"access_restricted; {exc}", park_as_failed=True
                    )
                    last_error = exc
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.exception(
                        "Profile scrape failed (%s) attempt %s", url, attempt
                    )
                    self._save_directory_record(
                        row,
                        note=f"profile_open_failed; {exc}"[:500],
                        park_as_failed=True,
                    )
                    break

            if not success and last_error and not isinstance(
                last_error, CaptchaDetectedError
            ):
                self._emit(f"Saved directory fields / moving on: {name or url}")

            processed += 1
            export_details_csv(self.db)
            if processed % 5 == 0:
                export_details_json(self.db)
            self._publish_progress()
            time.sleep(settings.PROFILE_DELAY_SECONDS)

        if self.db.list_unfinished_index():
            self.finalize_remaining_from_directory("final sweep")

        export_details_csv(self.db)
        export_details_json(self.db)
        report_path = write_summary_report(self.db)
        stats = self.db.quality_report()
        self._emit(f"Detail phase finished. Summary: {report_path}")
        self._publish_progress()
        return stats
