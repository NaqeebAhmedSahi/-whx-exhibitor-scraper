"""Shared Chrome auto-detect preflight used by make run / GUI / CLI."""

from __future__ import annotations

import logging
from typing import Callable

from browser.chrome_detector import ChromeInfo, detect_chrome
from config import settings

logger = logging.getLogger("whx_scraper.preflight")


def ensure_chrome_ready(
    *,
    on_status: Callable[[str], None] | None = None,
) -> ChromeInfo:
    """
    Detect the Chrome binary/version on this laptop before scraping starts.
    Matching ChromeDriver is resolved later when the browser session starts.
    """
    emit = on_status or (lambda m: None)
    info = detect_chrome(settings.CHROME_BINARY)
    msg = (
        f"Auto-detected Chrome {info.version} on {info.os_name}\n"
        f"Binary: {info.binary}\n"
        f"ChromeDriver will be matched to this version on Start."
    )
    logger.info(msg.replace("\n", " | "))
    emit(msg)
    print(msg)
    return info
