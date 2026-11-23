"""Helpers for landing-page iframe embedding of the Swapcard directory."""

from __future__ import annotations

import logging
import time
from typing import Optional

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config import selectors, settings

logger = logging.getLogger("whx_scraper.iframe")


def dismiss_cookies(driver: WebDriver, timeout: float = 5.0) -> None:
    """Best-effort cookie banner dismissal (Transcend / OneTrust / text button)."""
    end = time.time() + timeout
    while time.time() < end:
        # Shadow-DOM Transcend manager
        try:
            host = driver.find_element(By.CSS_SELECTOR, "#transcend-consent-manager")
            clicked = driver.execute_script(
                """
                const host = arguments[0];
                if (!host || !host.shadowRoot) return false;
                const btn = [...host.shadowRoot.querySelectorAll('button')]
                  .find(b => /accept all/i.test(b.textContent || ''));
                if (btn) { btn.click(); return true; }
                return false;
                """,
                host,
            )
            if clicked:
                logger.info("Dismissed Transcend cookie banner")
                time.sleep(0.5)
                return
        except NoSuchElementException:
            pass

        for sel in selectors.COOKIE_ACCEPT_SELECTORS:
            try:
                if sel.startswith("//"):
                    el = driver.find_element(By.XPATH, sel)
                else:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                if el.is_displayed():
                    el.click()
                    logger.info("Dismissed cookie banner via %s", sel)
                    time.sleep(0.5)
                    return
            except Exception:  # noqa: BLE001
                continue
        time.sleep(0.3)


def find_directory_iframe(driver: WebDriver) -> Optional[object]:
    try:
        return driver.find_element(By.CSS_SELECTOR, selectors.LANDING_IFRAME)
    except NoSuchElementException:
        return None


def switch_to_directory_iframe(driver: WebDriver, wait: WebDriverWait) -> bool:
    """
    Switch into the embedded exhibitor directory iframe if present.
    Returns True when switched, False when content is already top-level.
    """
    driver.switch_to.default_content()
    try:
        iframe = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, selectors.LANDING_IFRAME))
        )
        src = iframe.get_attribute("src") or ""
        logger.info("Found directory iframe src=%s", src)
        driver.switch_to.frame(iframe)
        return True
    except TimeoutException:
        logger.info("No directory iframe found; assuming top-level widget URL")
        return False


def iframe_src(driver: WebDriver) -> Optional[str]:
    driver.switch_to.default_content()
    iframe = find_directory_iframe(driver)
    if not iframe:
        return None
    return iframe.get_attribute("src")


def open_directory(
    driver: WebDriver,
    wait: WebDriverWait,
    *,
    prefer_direct: bool = True,
    directory_url: str | None = None,
) -> None:
    """
    Open the exhibitor directory.

    Prefer the direct widget URL (more reliable than landing-page iframe + cookies).
    Landing page remains available for inspection / fallback.
    """
    url = directory_url or settings.DIRECTORY_URL
    if prefer_direct:
        driver.get(url)
        time.sleep(settings.REQUEST_DELAY_SECONDS)
        return

    driver.get(settings.LANDING_PAGE_URL)
    time.sleep(settings.REQUEST_DELAY_SECONDS)
    dismiss_cookies(driver)
    switched = switch_to_directory_iframe(driver, wait)
    if not switched:
        # Fall back to direct URL from iframe src or configured directory
        src = iframe_src(driver) or url
        driver.get(src)
        time.sleep(settings.REQUEST_DELAY_SECONDS)
