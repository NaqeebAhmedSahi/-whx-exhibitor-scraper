"""Phase-one DOM inspection helpers — dump observed structure for debugging."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver

from config import selectors, settings

logger = logging.getLogger("whx_scraper.inspect")


def inspect_directory(driver: WebDriver) -> dict[str, Any]:
    """Collect structural signals from the currently loaded directory page."""
    data: dict[str, Any] = {
        "url": driver.current_url,
        "title": driver.title,
        "card_count": 0,
        "sample_cards": [],
        "filters_button_present": False,
        "filter_sections": [],
        "has_load_more": False,
        "scroll_height": None,
    }
    try:
        cards = driver.find_elements(By.CSS_SELECTOR, selectors.EXHIBITOR_CARD_LINKS)
        data["card_count"] = len(cards)
        for card in cards[:5]:
            name_el = _first(card, selectors.CARD_NAME)
            type_el = _first(card, selectors.CARD_TYPE)
            booth_el = _first(card, selectors.CARD_BOOTH)
            data["sample_cards"].append(
                {
                    "href": card.get_attribute("href"),
                    "name": name_el.text.strip() if name_el else None,
                    "type": type_el.text.strip() if type_el else None,
                    "booth": booth_el.text.strip() if booth_el else None,
                    "text": card.text.strip()[:200],
                }
            )
    except Exception as exc:  # noqa: BLE001
        data["card_error"] = str(exc)

    try:
        data["filters_button_present"] = bool(
            driver.find_elements(By.CSS_SELECTOR, selectors.FILTERS_BUTTON)
        )
    except Exception:  # noqa: BLE001
        pass

    try:
        headers = driver.find_elements(By.CSS_SELECTOR, selectors.FILTER_SECTION_HEADER)
        data["filter_sections"] = [h.text.strip() for h in headers if h.text.strip()]
    except Exception:  # noqa: BLE001
        pass

    try:
        data["scroll_height"] = driver.execute_script(
            "return document.documentElement.scrollHeight"
        )
    except Exception:  # noqa: BLE001
        pass

    logger.info(
        "Inspection: %s cards, filters=%s, sections=%s",
        data["card_count"],
        data["filters_button_present"],
        data["filter_sections"],
    )
    return data


def inspect_profile(driver: WebDriver) -> dict[str, Any]:
    fields = driver.execute_script(
        """
        const names = [...document.querySelectorAll(arguments[0])];
        return names.map(n => {
          const wrap = n.closest('[class*="style__Wrapper"]') || n.parentElement;
          const content = wrap ? wrap.querySelector(arguments[1]) : null;
          const chips = wrap ? [...wrap.querySelectorAll(arguments[2])].map(c => c.innerText.trim()) : [];
          return {
            label: (n.innerText || '').trim(),
            value: content ? (content.innerText || '').trim() : null,
            chips
          };
        });
        """,
        selectors.PROFILE_FIELD_NAME,
        selectors.PROFILE_FIELD_CONTENT,
        selectors.PROFILE_CHIP,
    )
    name = None
    try:
        name = driver.find_element(By.CSS_SELECTOR, selectors.PROFILE_COMPANY_NAME).text.strip()
    except Exception:  # noqa: BLE001
        pass
    return {
        "url": driver.current_url,
        "title": driver.title,
        "company_name": name,
        "fields": fields,
        "body_text": (driver.find_element(By.TAG_NAME, "body").text or "")[:1500],
    }


def save_inspection(payload: dict[str, Any], path: Path | None = None) -> Path:
    path = path or settings.INSPECTION_DUMP
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _first(root, css: str):
    els = root.find_elements(By.CSS_SELECTOR, css)
    return els[0] if els else None
