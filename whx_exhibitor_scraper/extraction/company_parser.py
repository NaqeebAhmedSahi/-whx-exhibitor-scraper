"""Parse exhibitor profile pages — BeautifulSoup on page HTML (fast path)."""

from __future__ import annotations

import logging
from typing import Any

from selenium.webdriver.remote.webdriver import WebDriver

from extraction.html_parser import parse_profile_html

logger = logging.getLogger("whx_scraper.parser")


def parse_profile(driver: WebDriver, profile_url: str) -> dict[str, Any]:
    """
    Extract profile fields from driver.page_source via BeautifulSoup.
    Avoids per-field Selenium find_element round-trips (major speed win).
    """
    html = driver.page_source or ""
    payload = parse_profile_html(html, profile_url)
    logger.debug(
        "bs4 parsed %s company=%s country=%s",
        profile_url,
        payload.get("company_name"),
        payload.get("country"),
    )
    return payload
