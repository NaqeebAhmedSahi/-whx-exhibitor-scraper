#!/usr/bin/env python3
"""
WHX Dubai DEC Exhibitor Scraper

Usage:
  python main.py              # launch Tkinter GUI (default)
  python main.py --cli        # run full scrape in CLI mode
  python main.py --cli --details-only
  python main.py --inspect    # open directory, dump DOM inspection, exit
  python main.py --check-chrome  # detect Chrome version + matching driver
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Ensure package root is on sys.path when launched as a script
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.logging_setup import setup_logging  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="WHX Dubai DEC exhibitor scraper")
    p.add_argument("--cli", action="store_true", help="Run without GUI")
    p.add_argument(
        "--details-only",
        action="store_true",
        help="Skip directory discovery; resume pending profile details",
    )
    p.add_argument(
        "--inspect",
        action="store_true",
        help="Inspect directory DOM and write output/dom_inspection.json",
    )
    p.add_argument(
        "--dec-event-view",
        action="store_true",
        help="Use the DEC-prefiltered EventView URL instead of Venue filter",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        help="Override: run Chrome headless (not recommended; default is headed)",
    )
    p.add_argument(
        "--check-chrome",
        action="store_true",
        help="Detect installed Chrome version and verify a matching ChromeDriver starts",
    )
    return p


def run_check_chrome(headless: bool = True) -> int:
    """Verify Chrome binary/version detection and matching driver startup."""
    from browser.chrome_detector import detect_chrome
    from browser.chrome_manager import ChromeManager
    from config import settings

    log = setup_logging()
    info = detect_chrome(settings.CHROME_BINARY)
    print(f"Chrome binary : {info.binary}")
    print(f"Chrome version: {info.version} (major {info.major})")
    print(f"OS            : {info.os_name}")
    log.info("Detected %s", info.summary)

    chrome = ChromeManager(headless=headless)
    try:
        driver = chrome.start()
        caps = driver.capabilities or {}
        browser_v = caps.get("browserVersion") or caps.get("version")
        driver_v = (caps.get("chrome") or {}).get("chromedriverVersion", "")
        print(f"Session browser : {browser_v}")
        print(f"Session driver  : {driver_v}")
        print("OK — Chrome + matching ChromeDriver are ready on this laptop.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to start matching ChromeDriver: {exc}")
        return 1
    finally:
        chrome.stop()


def run_inspect(headless: bool = False) -> int:
    from browser.chrome_manager import ChromeManager
    from browser.iframe_handler import open_directory
    from config import settings
    from discovery.directory_inspector import inspect_directory, save_inspection
    from discovery.venue_filter import VenueFilter

    log = setup_logging()
    chrome = ChromeManager(headless=headless)
    try:
        chrome.start()
        open_directory(chrome.driver, chrome.wait, prefer_direct=True)
        chrome.raise_if_blocked()
        VenueFilter(chrome.driver, chrome.wait).wait_for_directory_ready()
        data = {"directory": inspect_directory(chrome.driver)}
        path = save_inspection(data)
        log.info("Inspection written to %s", path)
        print(path)
        return 0
    finally:
        chrome.stop()


def run_cli(details_only: bool, dec_event_view: bool, headless: bool) -> int:
    from config import settings
    from scraper_service import ScraperService

    if headless:
        settings.HEADLESS = True

    log = setup_logging()
    done = {"flag": False, "result": None}

    def on_finished(result: str) -> None:
        done["flag"] = True
        done["result"] = result

    service = ScraperService(
        on_status=lambda m: log.info(m),
        on_progress=lambda p: log.info(
            "progress phase=%s completed=%s/%s",
            p.get("phase"),
            p.get("completed"),
            p.get("discovered"),
        ),
        on_finished=on_finished,
    )

    if dec_event_view and not details_only:
        # Patch collector path via meta flag consumed indirectly:
        # run discovery manually for DEC EventView then details.
        from browser.chrome_manager import CaptchaDetectedError
        from discovery.exhibitor_collector import ExhibitorCollector
        from extraction.profile_extractor import ProfileExtractor

        chrome = service.chrome
        try:
            chrome.start()
            collector = ExhibitorCollector(
                chrome,
                service.db,
                on_status=lambda m: log.info(m),
                on_progress=lambda p: None,
                should_stop=service.should_stop,
            )
            collector.collect(use_dec_event_view=True)
            extractor = ProfileExtractor(
                chrome,
                service.db,
                on_status=lambda m: log.info(m),
            )
            extractor.run()
            return 0
        except CaptchaDetectedError as exc:
            log.error("%s", exc)
            print(exc)
            return 2
        finally:
            chrome.stop()

    service.start(skip_discovery=details_only)
    try:
        while not done["flag"]:
            time.sleep(0.5)
    except KeyboardInterrupt:
        service.stop()
        return 130

    if done["result"] == "captcha":
        print(service.last_error)
        return 2
    if done["result"] == "error":
        print(service.last_error)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()

    if args.check_chrome:
        return run_check_chrome(headless=True)

    # Always auto-detect Chrome before GUI / CLI / inspect so make run
    # picks the correct browser + matching driver for this laptop.
    from browser.chrome_preflight import ensure_chrome_ready

    try:
        chrome_info = ensure_chrome_ready()
    except RuntimeError as exc:
        print(exc)
        return 1

    if args.inspect:
        return run_inspect(headless=args.headless)

    if args.cli:
        return run_cli(
            details_only=args.details_only,
            dec_event_view=args.dec_event_view,
            headless=args.headless,
        )

    from gui.app import run_gui

    run_gui(chrome_info=chrome_info)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
