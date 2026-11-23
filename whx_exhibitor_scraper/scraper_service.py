"""Orchestrates discovery + detail extraction with pause/resume/stop controls."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

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

logger = logging.getLogger("whx_scraper.service")


class ScraperService:
    def __init__(
        self,
        *,
        on_status: Callable[[str], None] | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        on_finished: Callable[[str], None] | None = None,
    ) -> None:
        self.on_status = on_status or (lambda _m: None)
        self.on_progress = on_progress or (lambda _p: None)
        self.on_finished = on_finished or (lambda _m: None)

        self.db = Database()
        self.chrome = ChromeManager(on_status=self.on_status)

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.clear()
        self.phase = "idle"
        self.last_error: str | None = None

    # ------------------------------------------------------------------
    # Control surface (thread-safe flags)
    # ------------------------------------------------------------------
    def start(
        self,
        *,
        skip_discovery: bool | None = None,
        by_hall: bool = False,
        venues: list[str] | None = None,
    ) -> None:
        if self._thread and self._thread.is_alive():
            self.on_status("Scraper already running")
            return
        self._stop.clear()
        self._pause.clear()
        self.last_error = None
        self._by_hall = by_hall
        self._venues = venues or ["DEC"]

        def target() -> None:
            try:
                self._run(
                    skip_discovery=skip_discovery,
                    by_hall=by_hall,
                    venues=self._venues,
                )
                self.on_finished("completed")
            except CaptchaDetectedError as exc:
                self.last_error = str(exc)
                self.phase = "captcha"
                self.on_status(str(exc))
                self.on_finished("captcha")
            except Exception as exc:  # noqa: BLE001
                logger.exception("Scraper crashed")
                self.last_error = str(exc)
                self.phase = "error"
                self.on_status(f"Error: {exc}")
                self.on_finished("error")
            finally:
                # Keep browser open on CAPTCHA so user can solve it
                if self.phase != "captcha":
                    self.chrome.stop()

        self._thread = threading.Thread(target=target, name="whx-scraper", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        self._pause.set()
        self.on_status("Paused")
        self.phase = "paused"

    def resume(self, *, venues: list[str] | None = None) -> None:
        was_captcha = self.phase == "captcha"
        was_dead = not (self._thread and self._thread.is_alive())
        prev_phase = self.phase
        self._pause.clear()
        if venues:
            self._venues = list(venues)
        if was_dead or was_captcha:
            # After CAPTCHA: keep the solved browser session, skip rediscovery,
            # continue profile scrape for the selected venues only.
            skip = True if was_captcha else None
            if was_captcha and settings.USE_UNDETECTED_FALLBACK:
                try:
                    if self.chrome.driver and not self.chrome.use_undetected:
                        self.on_status(
                            "After CAPTCHA — switching to undetected-chromedriver "
                            "for profile scrape"
                        )
                        self.chrome.switch_to_undetected()
                except Exception as exc:  # noqa: BLE001
                    self.on_status(
                        f"undetected-chromedriver unavailable after CAPTCHA ({exc}); "
                        "continuing with current browser"
                    )
            self.on_status(
                "Resumed after CAPTCHA — continuing next pending profile "
                f"for {', '.join(self._venues)}"
                if was_captcha
                else "Resumed — restarting worker"
            )
            self.start(
                skip_discovery=skip,
                by_hall=bool(getattr(self, "_by_hall", False)),
                venues=list(getattr(self, "_venues", ["DEC"])),
            )
        else:
            if prev_phase == "discovery" or prev_phase == "paused":
                self.on_status("Resumed — continuing directory scroll / scrape")
            else:
                self.on_status("Resumed — continuing from next unsaved exhibitor")
            self.phase = "running" if prev_phase == "paused" else prev_phase

    def stop(self) -> None:
        self._stop.set()
        self._pause.clear()
        self.on_status("Stop requested")
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.chrome.stop()
        self.phase = "stopped"
        self.on_finished("stopped")

    def should_pause(self) -> bool:
        return self._pause.is_set()

    def should_stop(self) -> bool:
        return self._stop.is_set()

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------
    def _run(
        self,
        *,
        skip_discovery: bool | None = None,
        by_hall: bool = False,
        venues: list[str] | None = None,
    ) -> None:
        settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        settings.LOG_DIR.mkdir(parents=True, exist_ok=True)

        self.phase = "starting"
        # Fix false "completed" from Oops/directory-only opens, then sync real scrapes
        demoted = self.db.demote_incomplete_completed_rows()
        synced = self.db.sync_completed_statuses()
        reset = self.db.reset_in_progress()
        work = self.db.remaining_work_count()
        if demoted:
            self.on_status(
                f"Fixed {demoted} false completions "
                f"(opened/Oops without scraped data) — will scrape again"
            )
        if synced:
            self.on_status(f"Synced {synced} already-scraped exhibitors (will skip)")
        if reset:
            self.on_status(f"Re-queued {reset} interrupted in-progress profiles")
        self.on_status(
            f"Local DB so far: {work['total']} discovered URLs "
            f"({work['completed']} fully scraped, "
            f"{work.get('directory_only', 0)} directory-only placeholders, "
            f"{work['pending']} still to process). "
            f"This is NOT the live site total — rediscovery will add more."
        )
        self.on_progress(self.snapshot_progress())

        venues = venues or list(getattr(self, "_venues", ["DEC"])) or ["DEC"]
        self._venues = venues

        # Only exit early when selected venues are fully done AND discovered
        need_discover_pre = self.db.venues_needing_discovery(venues)
        pending_pre = self.db.count_pending_for_venues(venues)
        if (
            not need_discover_pre
            and pending_pre == 0
            and work["needs_review"] == 0
        ):
            self.phase = "done"
            export_index_csv(self.db)
            export_details_csv(self.db)
            export_details_json(self.db)
            write_summary_report(self.db)
            self.on_status(
                f"Nothing left for {', '.join(venues)} — selected venues are complete"
            )
            return

        # Reuse existing Chrome session after CAPTCHA (do not reopen DEC)
        if self.chrome.driver is None:
            self.chrome.start()

        stats = self.db.get_index_stats()

        # Discover only venues that have not been collected yet
        need_discover = self.db.venues_needing_discovery(venues)
        if skip_discovery is True:
            need_discover = []
        elif skip_discovery is False:
            need_discover = list(venues)

        if need_discover:
            self.phase = "discovery"
            if settings.USE_UNDETECTED_FALLBACK and not self.chrome.use_undetected:
                self.on_status(
                    "Switching to undetected-chromedriver for directory scroll"
                )
                try:
                    self.chrome.switch_to_undetected()
                except Exception as exc:  # noqa: BLE001
                    self.on_status(
                        f"undetected-chromedriver unavailable ({exc}); "
                        "continuing with standard Chrome"
                    )
            collector = ExhibitorCollector(
                self.chrome,
                self.db,
                on_status=self.on_status,
                on_progress=self.on_progress,
                should_pause=self.should_pause,
                should_stop=self.should_stop,
            )
            self.on_status(f"Discovery venues: {', '.join(need_discover)}")
            if by_hall:
                self.on_status(
                    "Discovery: hall-by-hall (Hall Numbers filter) for "
                    + ", ".join(need_discover)
                )
            collector.collect(
                use_dec_event_view=True,
                by_hall=by_hall,
                venues=need_discover,
            )
            if self.should_stop():
                return
            self.db.sync_completed_statuses()
            if settings.USE_UNDETECTED_FALLBACK:
                requeued = self.db.requeue_directory_only_for_enrichment()
                if requeued:
                    self.on_status(
                        f"Re-queued {requeued} directory-only rows for enrichment"
                    )
        else:
            self.on_status(
                f"Selected venues already discovered ({', '.join(venues)}). "
                f"Continuing profile scrape for those venues only "
                f"(not opening other venue directories)."
            )
            export_index_csv(self.db)

        if self.should_stop():
            return

        pending_for_venues = self.db.count_pending_for_venues(venues)
        self.on_status(
            f"Profile phase for {', '.join(venues)}: "
            f"{pending_for_venues} pending in selected venues"
        )
        if pending_for_venues == 0 and need_discover == []:
            # Selected venue has no URLs yet — force discovery once
            if venues and self.db.venues_needing_discovery(venues):
                pass
            elif pending_for_venues == 0:
                self.on_status(
                    f"No pending profiles for {', '.join(venues)}. "
                    f"If this is a new venue, discovery should have run — "
                    f"clear discovery_done meta or re-run with Start."
                )

        self.phase = "details"
        extractor = ProfileExtractor(
            self.chrome,
            self.db,
            on_status=self.on_status,
            on_progress=self.on_progress,
            should_pause=self.should_pause,
            should_stop=self.should_stop,
        )
        extractor.run(venues=venues)
        self.phase = "done"
        write_summary_report(self.db)
        self.on_status("All phases finished")

    # ------------------------------------------------------------------
    # Exports
    # ------------------------------------------------------------------
    def export_csv(self) -> str:
        p1 = export_index_csv(self.db)
        p2 = export_details_csv(self.db)
        write_summary_report(self.db)
        return f"{p1}\n{p2}"

    def export_json(self) -> str:
        path = export_details_json(self.db)
        write_summary_report(self.db)
        return str(path)

    def snapshot_progress(self) -> dict[str, Any]:
        stats = self.db.get_index_stats()
        total = max(stats["total"], 1)
        return {
            "phase": self.phase,
            "target": settings.DIRECTORY_URL,
            "discovered": stats["total"],
            "dec": stats["dec"],
            "unique_urls": stats["total"],
            "completed": stats["completed"],
            "pending": stats["pending"],
            "failed": stats["failed"],
            "needs_review": stats["needs_review"],
            "progress_pct": round(100.0 * stats["completed"] / total, 2),
            "browser": "running" if self.chrome.driver else "stopped",
            "last_error": self.last_error,
        }
