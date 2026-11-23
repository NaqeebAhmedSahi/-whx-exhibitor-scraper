"""Collect DEC exhibitor cards from the directory with scroll + persistence."""

from __future__ import annotations

import base64
import logging
import re
import time
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait

from browser.chrome_manager import ChromeManager
from browser.iframe_handler import open_directory
from config import selectors, settings
from discovery.directory_inspector import inspect_directory, save_inspection
from discovery.infinite_scroll import InfiniteScroll
from discovery.venue_filter import VenueFilter
from storage.database import Database, normalize_profile_url, utc_now
from storage.exporters import export_index_csv

logger = logging.getLogger("whx_scraper.collector")

BOOTH_RE = re.compile(
    r"^(?:(?P<venue>DEC|DWTC)\s*[·•\-–—]\s*)?(?P<booth>.+)$",
    re.IGNORECASE,
)
HALL_FROM_BOOTH_RE = re.compile(r"^(?P<prefix>[SNsn])(?P<num>\d+)\.", re.IGNORECASE)


def decode_exhibitor_id(token: str) -> str | None:
    """Decode base64 Swapcard id like RXhoaWJpdG9yXzI2MDI2NTY= -> Exhibitor_2602656."""
    if not token:
        return None
    raw = unquote(token)
    pad = "=" * ((4 - len(raw) % 4) % 4)
    try:
        return base64.b64decode(raw + pad).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return raw


def exhibitor_id_from_url(url: str) -> str | None:
    path = urlparse(url).path.rstrip("/")
    token = path.split("/")[-1] if path else ""
    if not token or token == "exhibitor":
        return None
    return decode_exhibitor_id(token) or token


def infer_hall(booth: str | None) -> str | None:
    if not booth:
        return None
    m = HALL_FROM_BOOTH_RE.match(booth.strip())
    if not m:
        return None
    prefix = m.group("prefix").upper()
    num = m.group("num")
    side = "South Hall" if prefix == "S" else "North Hall"
    return f"{side} - {num}"


def parse_booth_and_venue(booth_text: str | None, type_text: str | None) -> tuple[str | None, str | None]:
    venue = None
    booth = None
    if type_text:
        t = type_text.strip().upper()
        if "DEC" in t:
            venue = "DEC"
        elif "DWTC" in t:
            venue = "DWTC"
    if booth_text:
        m = BOOTH_RE.match(booth_text.strip())
        if m:
            if m.group("venue"):
                venue = m.group("venue").upper()
            booth = m.group("booth").strip()
        else:
            booth = booth_text.strip()
    return venue, booth


def is_dec_record(venue: str | None, type_text: str | None, booth_text: str | None) -> bool:
    blob = " ".join(x for x in (venue, type_text, booth_text) if x).upper()
    if "DWTC" in blob and "DEC" not in blob:
        return False
    if booth_text and booth_text.strip().upper().startswith("DWTC"):
        return False
    if type_text and "DWTC" in type_text.upper() and "DEC" not in type_text.upper():
        return False
    if venue and venue.upper() == "DEC":
        return True
    if type_text and "DEC" in type_text.upper():
        return True
    # Booth-only cards on DEC-filtered EventView often omit venue prefix
    if venue is None and type_text is None and booth_text and not booth_text.upper().startswith("DWTC"):
        return True
    return venue is not None and "DEC" in venue.upper()


class ExhibitorCollector:
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
        self.discovered_urls: set[str] = set()

    def _emit(self, msg: str) -> None:
        logger.info(msg)
        self.on_status(msg)

    def _wait_pause(self) -> None:
        while self.should_pause() and not self.should_stop():
            time.sleep(0.3)

    @property
    def driver(self) -> WebDriver:
        assert self.chrome.driver is not None
        return self.chrome.driver

    @property
    def wait(self) -> WebDriverWait:
        return self.chrome.wait

    def extract_visible_cards(
        self, *, dec_only: bool = True, default_venue: str | None = "DEC"
    ) -> list[dict[str, Any]]:
        # Prefer one-shot HTML parse (bs4) over per-card Selenium calls
        try:
            from extraction.html_parser import extract_directory_urls_html

            html = self.driver.page_source or ""
            base = self.driver.current_url or ""
            raw_records = extract_directory_urls_html(html, base_url=base)
            records: list[dict[str, Any]] = []
            for r in raw_records:
                type_text = r.get("type_text")
                booth_text = r.get("booth_text_raw") or r.get("booth_number")
                venue, booth = parse_booth_and_venue(booth_text, type_text)
                if dec_only and not is_dec_record(venue, type_text, booth_text):
                    continue
                records.append(
                    {
                        "company_name": r.get("company_name"),
                        "profile_url": r["profile_url"],
                        "exhibitor_id": r.get("exhibitor_id"),
                        "venue": venue or default_venue,
                        "booth_number": booth,
                        "hall_number": infer_hall(booth),
                        "discovered_at": utc_now(),
                        "type_text": type_text,
                        "booth_text_raw": booth_text,
                    }
                )
            return records
        except Exception:  # noqa: BLE001
            pass

        cards = self.driver.find_elements(By.CSS_SELECTOR, selectors.EXHIBITOR_CARD_LINKS)
        records = []
        for card in cards:
            href = card.get_attribute("href") or ""
            url = normalize_profile_url(href)
            if not url:
                continue
            name_els = card.find_elements(By.CSS_SELECTOR, selectors.CARD_NAME)
            type_els = card.find_elements(By.CSS_SELECTOR, selectors.CARD_TYPE)
            booth_els = card.find_elements(By.CSS_SELECTOR, selectors.CARD_BOOTH)
            name = name_els[0].text.strip() if name_els else None
            type_text = type_els[0].text.strip() if type_els else None
            booth_text = booth_els[0].text.strip() if booth_els else None
            if not name:
                lines = [ln.strip() for ln in (card.text or "").splitlines() if ln.strip()]
                name = lines[0] if lines else None
            venue, booth = parse_booth_and_venue(booth_text, type_text)
            if dec_only and not is_dec_record(venue, type_text, booth_text):
                continue
            records.append(
                {
                    "company_name": name,
                    "profile_url": url,
                    "exhibitor_id": exhibitor_id_from_url(url),
                    "venue": venue or default_venue,
                    "booth_number": booth,
                    "hall_number": infer_hall(booth),
                    "discovered_at": utc_now(),
                    "type_text": type_text,
                    "booth_text_raw": booth_text,
                }
            )
        return records

    def _visible_url_set(self) -> set[str]:
        """Fast href-only collect for scroll growth checks (avoids N DOM walks)."""
        try:
            hrefs = self.driver.execute_script(
                """
                const out = [];
                for (const a of document.querySelectorAll('a[href*=\"/exhibitor/\"]')) {
                  if (a.href) out.push(a.href);
                }
                return out;
                """
            ) or []
        except Exception:  # noqa: BLE001
            return {
                r["profile_url"]
                for r in self.extract_visible_cards(
                    dec_only=getattr(self, "_dec_only", True),
                    default_venue=getattr(self, "_default_venue", "DEC"),
                )
            }
        urls: set[str] = set()
        for href in hrefs:
            url = normalize_profile_url(href)
            if url:
                urls.add(url)
        return urls

    def _page_has_filter_error(self) -> bool:
        try:
            body = (self.driver.find_element(By.TAG_NAME, "body").text or "").lower()
        except Exception:  # noqa: BLE001
            return False
        return "oops, something went wrong" in body or "something went wrong" in body

    def _open_dec_directory(self, *, prefer_event_view: bool) -> str:
        """
        Open a DEC exhibitor listing.

        The full directory's Venue=DEC UI filter currently returns a site error
        ("Oops, something went wrong"). Prefer the DEC EventView widget, and fall
        back to client-side DEC filtering on the full directory if needed.
        """
        primary = settings.DEC_DIRECTORY_URL if prefer_event_view else settings.DIRECTORY_URL
        secondary = settings.DIRECTORY_URL if prefer_event_view else settings.DEC_DIRECTORY_URL

        open_directory(self.driver, self.wait, prefer_direct=True, directory_url=primary)
        self.chrome.raise_if_blocked()
        venue = VenueFilter(self.driver, self.wait, on_status=self.on_status)
        venue.wait_for_directory_ready()

        if primary == settings.DIRECTORY_URL:
            applied = venue.select_venue(settings.VENUE_FILTER_LABEL)
            if applied and not self._page_has_filter_error():
                self._emit("Venue=DEC filter applied on full directory")
                return primary
            self._emit(
                "Venue=DEC filter failed or errored on full directory; "
                "switching to DEC EventView URL"
            )
            open_directory(
                self.driver, self.wait, prefer_direct=True, directory_url=secondary
            )
            self.chrome.raise_if_blocked()
            venue.wait_for_directory_ready()
            return secondary

        self._emit("Using DEC-prefiltered EventView; validating DEC labels client-side")
        return primary

    def _scroll_and_persist(
        self,
        *,
        known_urls: set[str],
        hall_label: str | None = None,
        venue_key: str = "DEC",
    ) -> set[str]:
        self._default_venue = (
            "WHX Tech" if venue_key == "WHX_TECH" else venue_key
        )
        self._dec_only = venue_key == "DEC"
        scroll = InfiniteScroll(self.driver, on_status=self.on_status)
        hall_tag = hall_label

        def on_new(urls: set[str]) -> None:
            self._wait_pause()
            if self.should_stop():
                return
            result = self._persist_batch(
                urls, hall_label=hall_tag, venue_key=venue_key
            )
            self._emit(
                f"Saved batch: +{result['inserted']} new, {result['duplicates']} dupes"
                + (f" [{hall_tag or venue_key}]" if (hall_tag or venue_key) else "")
            )

        collected = scroll.exhaust(
            extract_batch=self._visible_url_set,
            known_urls=known_urls,
            should_stop=self.should_stop,
            should_pause=self.should_pause,
            on_new_urls=on_new,
        )
        self._persist_batch(hall_label=hall_tag, venue_key=venue_key)
        return collected

    def _persist_batch(
        self,
        urls: set[str] | None = None,
        *,
        hall_label: str | None = None,
        venue_key: str = "DEC",
    ) -> dict[str, int]:
        """
        Persist newly seen URLs. Prefer parsing only the requested URL set so
        late scroll rounds do not re-walk 1000+ cards (that was freezing/dying).
        """
        default_venue = "WHX Tech" if venue_key == "WHX_TECH" else venue_key
        dec_only = venue_key == "DEC"
        try:
            records = self.extract_visible_cards(
                dec_only=dec_only, default_venue=default_venue
            )
        except Exception as exc:  # noqa: BLE001
            self._emit(f"Card parse interrupted ({exc}); saving URL stubs only")
            records = []
            for url in urls or set():
                records.append(
                    {
                        "company_name": None,
                        "profile_url": url,
                        "exhibitor_id": exhibitor_id_from_url(url),
                        "venue": default_venue,
                        "booth_number": None,
                        "hall_number": hall_label,
                        "discovered_at": utc_now(),
                    }
                )
        if urls is not None:
            wanted = set(urls)
            records = [r for r in records if r["profile_url"] in wanted]
            have = {r["profile_url"] for r in records}
            for url in wanted - have:
                records.append(
                    {
                        "company_name": None,
                        "profile_url": url,
                        "exhibitor_id": exhibitor_id_from_url(url),
                        "venue": default_venue,
                        "booth_number": None,
                        "hall_number": hall_label,
                        "discovered_at": utc_now(),
                    }
                )
        if hall_label:
            for r in records:
                if not r.get("hall_number"):
                    r["hall_number"] = hall_label
        by_url: dict[str, dict[str, Any]] = {}
        for r in records:
            by_url[r["profile_url"]] = r
        result = self.db.upsert_index_records(by_url.values())
        self.discovered_urls |= set(by_url.keys())
        export_index_csv(self.db)
        stats = self.db.get_index_stats()
        self.on_progress(
            {
                "phase": "discovery",
                "discovered": stats["total"],
                "dec": stats["dec"],
                "unique_urls": stats["total"],
                "current_hall": hall_label or "",
                "current_venue": venue_key,
                **stats,
            }
        )
        return result

    def collect(
        self,
        *,
        use_dec_event_view: bool = True,
        by_hall: bool = False,
        venues: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Full discovery pipeline for selected venues (DEC / DWTC / WHX Tech).
        """
        venues = venues or ["DEC"]
        existing = {r["profile_url"] for r in self.db.list_index()}
        self.discovered_urls |= existing
        results: list[dict[str, Any]] = []

        for venue_key in venues:
            if self.should_stop():
                break
            meta = settings.VENUE_TARGETS.get(venue_key)
            if not meta:
                self._emit(f"Unknown venue key: {venue_key}")
                continue
            if by_hall:
                results.append(
                    self.collect_by_hall(
                        venue_key=venue_key,
                        use_dec_event_view=use_dec_event_view,
                    )
                )
                continue

            self._emit(f"Phase 2: Collecting {meta['label']} directory")
            directory_url = meta["directory"]
            open_directory(
                self.driver,
                self.wait,
                prefer_direct=True,
                directory_url=directory_url,
            )
            self.chrome.raise_if_blocked()
            VenueFilter(self.driver, self.wait, on_status=self.on_status).wait_for_directory_ready()
            self.db.set_meta(f"discovery_source_url:{venue_key}", directory_url)

            inspection = inspect_directory(self.driver)
            save_inspection(
                {
                    "directory": inspection,
                    "source_url": directory_url,
                    "venue": venue_key,
                }
            )
            before = len(self.discovered_urls)
            collected = self._scroll_and_persist(
                known_urls=self.discovered_urls,
                hall_label=None,
                venue_key=venue_key,
            )
            # Tag new rows with venue when missing
            gained = len(self.discovered_urls) - before
            self._emit(
                f"{venue_key} discovery: +{gained} URLs "
                f"(running total {len(self.discovered_urls)})"
            )
            self.db.set_meta(f"discovery_done:{venue_key}", "1")
            results.append(
                {
                    "venue": venue_key,
                    "urls_collected": len(collected),
                    "gained": gained,
                    "source_url": directory_url,
                    "inspection": inspection,
                }
            )

        export_index_csv(self.db)
        stats = self.db.get_index_stats()
        self.db.set_meta("discovery_completed_at", utc_now())
        self.db.set_meta("discovery_url_count", str(stats["total"]))
        self.db.set_meta("discovery_venues", ",".join(venues))
        self._emit(f"Discovery complete: {stats['total']} unique profile URLs")
        return {"stats": stats, "venues": venues, "results": results}

    def collect_by_hall(
        self,
        *,
        venue_key: str = "DEC",
        use_dec_event_view: bool = True,
    ) -> dict[str, Any]:
        """
        Scrape room/hall by room for one venue: apply each Hall Numbers filter,
        scroll that subset, then move to the next hall.
        """
        meta = settings.VENUE_TARGETS.get(venue_key) or {}
        label = meta.get("label", venue_key)
        self._emit(
            f"Phase 2: Collecting {label} hall-by-hall "
            f"({len(settings.HALL_NUMBERS)} halls/rooms fallback list)"
        )
        existing = {r["profile_url"] for r in self.db.list_index()}
        self.discovered_urls |= existing
        if existing:
            self._emit(f"Resuming with {len(existing)} URLs already in database")

        if venue_key == "DEC":
            used_url = self._open_dec_directory(prefer_event_view=use_dec_event_view)
        else:
            used_url = meta.get("directory") or settings.DIRECTORY_URL
            open_directory(
                self.driver,
                self.wait,
                prefer_direct=True,
                directory_url=used_url,
            )
            self.chrome.raise_if_blocked()
            VenueFilter(
                self.driver, self.wait, on_status=self.on_status
            ).wait_for_directory_ready()

        self.db.set_meta(f"discovery_source_url:{venue_key}", used_url)
        self.db.set_meta("discovery_source_url", used_url)
        self.db.set_meta("discovery_mode", "by_hall")

        filters = VenueFilter(self.driver, self.wait, on_status=self.on_status)
        # Prefer live filter labels from the page when available
        live = filters.list_hall_options()
        halls = list(live) if live else list(settings.HALL_NUMBERS)
        if live:
            self._emit(
                f"Found {len(halls)} Hall Numbers options in Filters UI ({venue_key})"
            )
        else:
            self._emit(
                f"Using built-in hall list ({len(halls)} rooms) — "
                "page options not readable"
            )

        inspection = inspect_directory(self.driver)
        save_inspection(
            {
                "directory": inspection,
                "source_url": used_url,
                "venue": venue_key,
                "halls": halls,
            }
        )

        per_hall: dict[str, int] = {}
        for idx, hall in enumerate(halls, start=1):
            if self.should_stop():
                break
            self._wait_pause()
            done_key = f"hall_done:{venue_key}:{hall}"
            # Legacy DEC key (pre-venue-scoped) still counts as done for DEC
            legacy_done = (
                venue_key == "DEC" and self.db.get_meta(f"hall_done:{hall}") == "1"
            )
            if self.db.get_meta(done_key) == "1" or legacy_done:
                self._emit(
                    f"[{idx}/{len(halls)}] Skip already done hall: {hall} ({venue_key})"
                )
                continue

            self._emit(
                f"[{idx}/{len(halls)}] Scraping {venue_key} hall/room: {hall}"
            )
            self.on_progress(
                {
                    "phase": "discovery",
                    "current_hall": hall,
                    "current_venue": venue_key,
                    "hall_index": idx,
                    "hall_total": len(halls),
                }
            )
            ok = filters.select_hall(hall)
            if not ok:
                self._emit(f"Could not apply Hall Numbers={hall}; skipping")
                continue
            if self._page_has_filter_error():
                self._emit(f"Oops page after selecting {hall}; clearing and skipping")
                filters.clear_hall_filters()
                continue

            before = len(self.discovered_urls)
            # Only count URLs newly seen in this hall pass for per-hall stats;
            # scroll still merges into the global set.
            hall_known = set(self.discovered_urls)
            collected = self._scroll_and_persist(
                known_urls=hall_known,
                hall_label=hall,
                venue_key=venue_key,
            )
            gained = len(self.discovered_urls) - before
            per_hall[hall] = gained
            self.db.set_meta(done_key, "1")
            if venue_key == "DEC":
                self.db.set_meta(f"hall_done:{hall}", "1")
            self.db.set_meta("discovery_url_count", str(len(self.discovered_urls)))
            self._emit(
                f"Finished {venue_key}/{hall}: +{gained} new URLs "
                f"(running total {len(self.discovered_urls)})"
            )
            filters.clear_hall_filters()

        export_index_csv(self.db)
        stats = self.db.get_index_stats()
        self.db.set_meta("discovery_completed_at", utc_now())
        self.db.set_meta("discovery_url_count", str(stats["total"]))
        self.db.set_meta(f"discovery_done:{venue_key}", "1")
        self._emit(
            f"Hall-by-hall discovery complete: {stats['total']} unique URLs "
            f"for {venue_key} across {len(per_hall)} halls"
        )
        return {
            "venue": venue_key,
            "urls_collected": stats["total"],
            "stats": stats,
            "per_hall": per_hall,
            "source_url": used_url,
            "mode": "by_hall",
            "halls": halls,
        }
