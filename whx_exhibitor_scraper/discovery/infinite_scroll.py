"""Infinite-scroll / Load-More pagination for the exhibitor directory."""

from __future__ import annotations

import logging
import time
from typing import Callable

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.wheel_input import ScrollOrigin
from selenium.webdriver.remote.webdriver import WebDriver

from config import selectors, settings

logger = logging.getLogger("whx_scraper.scroll")


class InfiniteScroll:
    def __init__(
        self,
        driver: WebDriver,
        *,
        stability_threshold: int | None = None,
        max_rounds: int | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self.driver = driver
        self.stability_threshold = stability_threshold or settings.SCROLL_STABILITY_THRESHOLD
        self.max_rounds = max_rounds or settings.SCROLL_MAX_ROUNDS
        self.on_status = on_status or (lambda _m: None)

    def _emit(self, msg: str) -> None:
        logger.info(msg)
        self.on_status(msg)

    def card_count(self) -> int:
        return len(self.driver.find_elements(By.CSS_SELECTOR, selectors.EXHIBITOR_CARD_LINKS))

    def scroll_height(self) -> int:
        try:
            return int(
                self.driver.execute_script(
                    "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
                )
                or 0
            )
        except Exception:  # noqa: BLE001
            return 0

    def _loading_visible(self) -> bool:
        for sel in selectors.LOADING_INDICATORS:
            try:
                for el in self.driver.find_elements(By.CSS_SELECTOR, sel):
                    if el.is_displayed():
                        return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _click_load_more_if_present(self) -> bool:
        css_part, _, xpath_part = selectors.LOAD_MORE_BUTTON.partition(", //")
        candidates = []
        try:
            candidates.extend(self.driver.find_elements(By.CSS_SELECTOR, css_part.strip()))
        except Exception:  # noqa: BLE001
            pass
        if xpath_part:
            try:
                candidates.extend(
                    self.driver.find_elements(By.XPATH, "//" + xpath_part.strip())
                )
            except Exception:  # noqa: BLE001
                pass
        for btn in candidates:
            try:
                if btn.is_displayed() and btn.is_enabled():
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", btn
                    )
                    time.sleep(0.3)
                    btn.click()
                    self._emit("Clicked Load More")
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def scroll_to_bottom(self) -> None:
        """
        Trigger Swapcard's infinite loader by bringing the last card to the
        viewport edge, then nudging further with scrollBy / wheel.
        """
        links = self.driver.find_elements(By.CSS_SELECTOR, selectors.EXHIBITOR_CARD_LINKS)
        if links:
            last = links[-1]
            try:
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'end', inline:'nearest', behavior:'auto'});",
                    last,
                )
            except Exception:  # noqa: BLE001
                try:
                    ActionChains(self.driver).move_to_element(last).perform()
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.25)
            # Nudge past the last card so the intersection observer fires
            try:
                origin = ScrollOrigin.from_element(last)
                ActionChains(self.driver).scroll_from_origin(origin, 0, 900).perform()
            except Exception:  # noqa: BLE001
                try:
                    ActionChains(self.driver).move_to_element(last).scroll_by_amount(
                        0, 900
                    ).perform()
                except Exception:  # noqa: BLE001
                    self.driver.execute_script("window.scrollBy(0, arguments[0]);", 900)

        self.driver.execute_script(
            """
            const step = arguments[0];
            window.scrollBy(0, step);
            const el = document.scrollingElement || document.documentElement;
            el.scrollTop = el.scrollHeight;
            """
            ,
            settings.SCROLL_PIXEL_STEP,
        )

        # CDP mouse wheel as a final reliable trigger in headed Chrome
        try:
            size = self.driver.get_window_size()
            self.driver.execute_cdp_cmd(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseWheel",
                    "x": int(size.get("width", 1200) / 2),
                    "y": int(size.get("height", 800) * 0.7),
                    "deltaX": 0,
                    "deltaY": 1200,
                    "pointerType": "mouse",
                },
            )
        except Exception:  # noqa: BLE001
            pass

    def wait_for_growth(
        self,
        *,
        before_cards: int,
        before_height: int,
        before_urls: set[str],
        extract_batch: Callable[[], set[str]],
        timeout: float | None = None,
    ) -> set[str]:
        """Poll until more cards/URLs/height appear, or timeout."""
        timeout = timeout if timeout is not None else max(settings.SCROLL_PAUSE_SECONDS, 2.5)
        end = time.time() + timeout
        latest = set(before_urls)
        while time.time() < end:
            if self._loading_visible():
                time.sleep(0.2)
                continue
            latest = extract_batch()
            cards = self.card_count()
            height = self.scroll_height()
            if len(latest) > len(before_urls) or cards > before_cards or height > before_height + 50:
                # Brief settle so a burst of cards can finish appending
                time.sleep(0.6)
                latest = extract_batch()
                return latest
            time.sleep(0.25)
        return extract_batch()

    def exhaust(
        self,
        *,
        extract_batch: Callable[[], set[str]],
        known_urls: set[str],
        should_stop: Callable[[], bool] | None = None,
        should_pause: Callable[[], bool] | None = None,
        on_new_urls: Callable[[set[str]], None] | None = None,
    ) -> set[str]:
        """
        Scroll / load-more until stability threshold of no-new-URL rounds.

        Termination checks:
          1. Loading indicator disappears
          2. No new unique profile URLs
          3. Repeated bottom-scroll attempts produce no growth
          4. Optional caller stop flag
        """
        collected = set(known_urls)
        stable_rounds = 0
        round_idx = 0

        def _wait_if_paused() -> None:
            if not should_pause:
                return
            while should_pause() and not (should_stop and should_stop()):
                time.sleep(0.3)

        def _safe_extract() -> set[str]:
            try:
                return extract_batch()
            except Exception as exc:  # noqa: BLE001
                self._emit(f"URL extract hiccup ({exc}); retrying once")
                time.sleep(1.0)
                return extract_batch()

        def _safe_persist(fresh: set[str]) -> None:
            if not fresh or not on_new_urls:
                return
            try:
                on_new_urls(fresh)
            except Exception as exc:  # noqa: BLE001
                # Keep scrolling — URLs may still be recoverable next round /
                # on restart from DB. Do not kill the whole discovery run.
                self._emit(f"Batch save hiccup ({exc}); continuing scroll")

        initial = _safe_extract()
        new_initial = initial - collected
        _safe_persist(new_initial)
        collected |= initial
        self._emit(f"Initial visible unique URLs: {len(collected)}")

        while round_idx < self.max_rounds:
            _wait_if_paused()
            if should_stop and should_stop():
                self._emit("Scroll stopped by caller")
                break

            round_idx += 1
            before = len(collected)
            try:
                before_cards = self.card_count()
                before_height = self.scroll_height()
            except Exception as exc:  # noqa: BLE001
                self._emit(f"Browser disconnected during scroll ({exc}) — stopping discovery early")
                break
            before_urls = set(collected)

            clicked = self._click_load_more_if_present()
            if not clicked:
                try:
                    self.scroll_to_bottom()
                except Exception as exc:  # noqa: BLE001
                    self._emit(f"Scroll failed ({exc}) — stopping discovery early")
                    break

            try:
                batch = self.wait_for_growth(
                    before_cards=before_cards,
                    before_height=before_height,
                    before_urls=before_urls,
                    extract_batch=_safe_extract,
                )
            except Exception as exc:  # noqa: BLE001
                self._emit(f"Growth wait failed ({exc}) — stopping discovery early")
                break
            # Always do one more bottom nudge if nothing grew yet
            if len(batch) <= before:
                try:
                    self.scroll_to_bottom()
                    batch = self.wait_for_growth(
                        before_cards=self.card_count(),
                        before_height=self.scroll_height(),
                        before_urls=before_urls,
                        extract_batch=_safe_extract,
                        timeout=max(settings.SCROLL_PAUSE_SECONDS, 2.0),
                    )
                except Exception as exc:  # noqa: BLE001
                    self._emit(f"Scroll retry failed ({exc}) — keeping {len(collected)} URLs")
                    break

            fresh = batch - collected
            _safe_persist(fresh)
            collected |= batch

            try:
                after_cards = self.card_count()
                after_height = self.scroll_height()
            except Exception:  # noqa: BLE001
                after_cards, after_height = before_cards, before_height
            gained = len(collected) - before
            self._emit(
                f"Scroll round {round_idx}: cards {before_cards}->{after_cards}, "
                f"height {before_height}->{after_height}, "
                f"+{gained} URLs (total {len(collected)})"
            )

            if gained == 0:
                stable_rounds += 1
            else:
                stable_rounds = 0

            if stable_rounds >= self.stability_threshold:
                try:
                    self.scroll_to_bottom()
                    confirm_batch = self.wait_for_growth(
                        before_cards=self.card_count(),
                        before_height=self.scroll_height(),
                        before_urls=collected,
                        extract_batch=_safe_extract,
                        timeout=3.0,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._emit(f"Confirm scroll failed ({exc}) — ending discovery")
                    break
                confirm = confirm_batch - collected
                if not confirm:
                    self._emit(
                        f"No new URLs after {self.stability_threshold} stable rounds — done"
                    )
                    break
                _safe_persist(confirm)
                collected |= confirm
                stable_rounds = 0

        return collected
