"""Select Venue / Hall Numbers filters via the directory Filters UI."""

from __future__ import annotations

import logging
import time
from typing import Callable

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config import selectors, settings

logger = logging.getLogger("whx_scraper.venue_filter")


class VenueFilter:
    def __init__(
        self,
        driver: WebDriver,
        wait: WebDriverWait,
        *,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self.driver = driver
        self.wait = wait
        self.on_status = on_status or (lambda _m: None)

    def _emit(self, msg: str) -> None:
        logger.info(msg)
        self.on_status(msg)

    def filters_already_visible(self) -> bool:
        headers = self.driver.find_elements(By.CSS_SELECTOR, selectors.FILTER_SECTION_HEADER)
        return any((h.text or "").strip() for h in headers)

    def open_filters(self) -> None:
        # Desktop layout keeps the Filters sidebar always visible (no Filters button).
        if self.filters_already_visible():
            self._emit("Filters sidebar already visible")
            return

        buttons = self.driver.find_elements(By.CSS_SELECTOR, selectors.FILTERS_BUTTON)
        if not buttons:
            try:
                self.wait.until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, selectors.FILTER_SECTION_HEADER)
                    )
                )
                if self.filters_already_visible():
                    self._emit("Filters sidebar became visible")
                    return
            except TimeoutException:
                pass
            raise TimeoutException(
                "Neither Filters button nor filter sidebar sections were found"
            )

        btn = self.wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, selectors.FILTERS_BUTTON))
        )
        btn.click()
        time.sleep(0.8)
        try:
            self.wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, selectors.FILTER_SECTION_HEADER)
                )
            )
        except TimeoutException:
            pass
        self._emit("Filters panel opened")

    def _find_section_header(self, title: str):
        headers = self.driver.find_elements(By.CSS_SELECTOR, selectors.FILTER_SECTION_HEADER)
        for h in headers:
            if h.text.strip().lower() == title.lower():
                return h
        for h in headers:
            if title.lower() in h.text.strip().lower():
                return h
        return None

    def _find_option_element(self, section: str, option_label: str):
        return self.driver.execute_script(
            """
            const headerText = arguments[0].toLowerCase();
            const optionText = arguments[1].toLowerCase();
            const headers = [...document.querySelectorAll(arguments[2])];
            const header = headers.find(h => {
              const t = (h.innerText || '').trim().toLowerCase();
              return t === headerText || t.includes(headerText);
            });
            if (!header) return null;
            let root = header;
            for (let i = 0; i < 8 && root; i++) {
              const labels = [...root.querySelectorAll(arguments[3])];
              const match = labels.find(l => {
                const t = (l.innerText || '').trim().toLowerCase();
                return t === optionText || t.includes(optionText);
              });
              if (match) return match;
              const titles = [...root.querySelectorAll(arguments[4])];
              const matchTitle = titles.find(t => {
                const txt = (t.innerText || '').trim().toLowerCase();
                return txt === optionText || txt.includes(optionText);
              });
              if (matchTitle) return matchTitle;
              root = root.parentElement;
            }
            const titles = [...document.querySelectorAll(arguments[4])];
            return titles.find(t => {
              const txt = (t.innerText || '').trim().toLowerCase();
              return txt === optionText;
            }) || null;
            """,
            section,
            option_label,
            selectors.FILTER_SECTION_HEADER,
            selectors.FILTER_CHECKBOX_LABEL,
            selectors.FILTER_CHECKBOX_TITLE,
        )

    def _is_checked(self, option_el) -> bool:
        return bool(
            self.driver.execute_script(
                """
                const el = arguments[0];
                const input = el.tagName === 'INPUT' ? el
                  : (el.querySelector && el.querySelector('input[type="checkbox"]'))
                  || (el.closest && el.closest('label') && el.closest('label').querySelector('input'));
                return input ? !!input.checked : false;
                """,
                option_el,
            )
        )

    def _click_option(self, option_el) -> None:
        try:
            option_el.click()
        except Exception:  # noqa: BLE001
            self.driver.execute_script("arguments[0].click();", option_el)

    def select_filter_option(
        self,
        section: str,
        option_label: str,
        *,
        ensure_only: bool = False,
        wait_refresh: bool = True,
    ) -> bool:
        """
        Check a Filters sidebar option (Venue, Hall Numbers, etc.).
        If ensure_only is True, uncheck other options in that section first.
        """
        self._emit(f"Selecting {section} filter: {option_label}")
        self.open_filters()

        header = self._find_section_header(section)
        if not header:
            self._emit(f"{section} filter section not found")
            return False
        try:
            header.click()
            time.sleep(0.35)
        except Exception:  # noqa: BLE001
            pass

        if ensure_only:
            self.clear_section_options(section, reopen=False)

        option = self._find_option_element(section, option_label)
        if not option:
            self._emit(f"{section} option '{option_label}' not found")
            return False

        if not self._is_checked(option):
            self._click_option(option)
            self._emit(f"Clicked {section}={option_label}")
        else:
            self._emit(f"{section}={option_label} already selected")

        if wait_refresh:
            time.sleep(settings.FILTER_REFRESH_WAIT)
            if self._page_has_oops():
                self._emit(f"Site Oops after {section}={option_label}")
                return False
            try:
                self._wait_for_cards_refresh()
            except TimeoutException:
                self._emit(f"No exhibitor cards after {section}={option_label}")
                return False
        return True

    def clear_section_options(self, section: str, *, reopen: bool = True) -> int:
        """Uncheck every selected checkbox under a filter section. Returns count cleared."""
        if reopen:
            self.open_filters()
            header = self._find_section_header(section)
            if header:
                try:
                    header.click()
                    time.sleep(0.3)
                except Exception:  # noqa: BLE001
                    pass

        cleared = self.driver.execute_script(
            """
            const headerText = arguments[0].toLowerCase();
            const headers = [...document.querySelectorAll(arguments[1])];
            const header = headers.find(h => {
              const t = (h.innerText || '').trim().toLowerCase();
              return t === headerText || t.includes(headerText);
            });
            if (!header) return 0;
            let root = header;
            let inputs = [];
            for (let i = 0; i < 8 && root; i++) {
              inputs = [...root.querySelectorAll('input[type="checkbox"]')];
              if (inputs.length) break;
              root = root.parentElement;
            }
            let n = 0;
            for (const input of inputs) {
              if (input.checked) {
                input.click();
                n += 1;
              }
            }
            return n;
            """,
            section,
            selectors.FILTER_SECTION_HEADER,
        )
        cleared = int(cleared or 0)
        if cleared:
            self._emit(f"Cleared {cleared} {section} filter(s)")
            time.sleep(settings.FILTER_REFRESH_WAIT)
        return cleared

    def select_venue(self, venue_label: str | None = None) -> bool:
        venue_label = venue_label or settings.VENUE_FILTER_LABEL
        ok = self.select_filter_option("Venue", venue_label, ensure_only=False)
        if ok:
            self._close_filters_if_open()
        return ok

    def select_hall(self, hall_label: str) -> bool:
        """Select exactly one Hall Numbers option (room/hall)."""
        ok = self.select_filter_option(
            "Hall Numbers", hall_label, ensure_only=True, wait_refresh=True
        )
        return ok

    def clear_hall_filters(self) -> int:
        return self.clear_section_options("Hall Numbers")

    def list_hall_options(self) -> list[str]:
        """Read Hall Numbers options currently shown in the Filters UI."""
        self.open_filters()
        header = self._find_section_header("Hall Numbers")
        if header:
            try:
                header.click()
                time.sleep(0.3)
            except Exception:  # noqa: BLE001
                pass
        labels = self.driver.execute_script(
            """
            const headerText = 'hall numbers';
            const headers = [...document.querySelectorAll(arguments[0])];
            const header = headers.find(h => (h.innerText || '').trim().toLowerCase().includes(headerText));
            if (!header) return [];
            let root = header;
            let titles = [];
            for (let i = 0; i < 8 && root; i++) {
              titles = [...root.querySelectorAll(arguments[1])]
                .map(t => (t.innerText || '').trim())
                .filter(Boolean);
              if (titles.length) break;
              root = root.parentElement;
            }
            return titles;
            """,
            selectors.FILTER_SECTION_HEADER,
            selectors.FILTER_CHECKBOX_TITLE,
        )
        return [str(x) for x in (labels or []) if str(x).strip()]

    def _page_has_oops(self) -> bool:
        try:
            body = (self.driver.find_element(By.TAG_NAME, "body").text or "").lower()
        except Exception:  # noqa: BLE001
            return False
        return "oops, something went wrong" in body or (
            "something went wrong" in body and "back" in body
        )

    def _close_filters_if_open(self) -> None:
        try:
            headers = self.driver.find_elements(By.CSS_SELECTOR, selectors.FILTER_SECTION_HEADER)
            if not headers:
                return
            btn = self.driver.find_element(By.CSS_SELECTOR, selectors.FILTERS_BUTTON)
            btn.click()
            time.sleep(0.4)
        except Exception:  # noqa: BLE001
            pass

    def _wait_for_cards_refresh(self) -> None:
        try:
            self.wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, selectors.EXHIBITOR_CARD_LINKS)
                )
            )
        except TimeoutException as exc:
            raise TimeoutException("No exhibitor cards after applying filter") from exc
        time.sleep(1.0)

    def wait_for_directory_ready(self) -> None:
        self.wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, selectors.EXHIBITOR_CARD_LINKS))
        )
        self._emit("Directory cards loaded")
