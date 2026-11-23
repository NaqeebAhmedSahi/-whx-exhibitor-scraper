"""Runtime settings for the WHX DEC exhibitor scraper."""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = PACKAGE_ROOT
OUTPUT_DIR = PROJECT_ROOT / "output"
LOG_DIR = PROJECT_ROOT / "logs"
DB_PATH = OUTPUT_DIR / "whx_dec_exhibitors.db"

EXHIBITOR_URLS_CSV = OUTPUT_DIR / "exhibitor_urls.csv"
DETAILS_CSV = OUTPUT_DIR / "whx_dec_exhibitors.csv"
DETAILS_JSON = OUTPUT_DIR / "whx_dec_exhibitors.json"
SUMMARY_REPORT = OUTPUT_DIR / "scrape_summary.txt"
INSPECTION_DUMP = OUTPUT_DIR / "dom_inspection.json"

# ---------------------------------------------------------------------------
# Target URLs (inspected Nov 2026)
# ---------------------------------------------------------------------------
LANDING_PAGE_URL = (
    "https://www.worldhealthexpo.com/events/healthcare/dubai/exhibitors-at-dec/"
)

# Full multi-venue directory (requires Venue=DEC filter)
DIRECTORY_URL = (
    "https://connections.whxevents.com/widget/event/"
    "world-healthcare-week-2027/exhibitors/RXZlbnRWaWV3XzI2NTkzNDU"
)

# DEC-prefiltered EventView embedded on the landing page (backup discovery URL)
DEC_DIRECTORY_URL = (
    "https://connections.whxevents.com/widget/event/"
    "world-healthcare-week-2027/exhibitors/RXZlbnRWaWV3XzI4Nzc1NjY="
)
DWTC_DIRECTORY_URL = (
    "https://connections.whxevents.com/widget/event/"
    "world-healthcare-week-2027/exhibitors/RXZlbnRWaWV3XzI4Nzc1Njc="
)
WHX_TECH_DIRECTORY_URL = (
    "https://connections.whxevents.com/widget/event/"
    "world-healthcare-week-2027/exhibitors/RXZlbnRWaWV3XzI2NTkzMzQ="
)

# Scrape targets selectable in the GUI (checkboxes)
VENUE_TARGETS = {
    "DEC": {
        "label": "Exhibitors at DEC",
        "landing": LANDING_PAGE_URL,
        "directory": DEC_DIRECTORY_URL,
        "by_hall": True,
    },
    "DWTC": {
        "label": "Exhibitors at DWTC",
        "landing": "https://www.worldhealthexpo.com/events/healthcare/dubai/exhibitors-at-dwtc/",
        "directory": DWTC_DIRECTORY_URL,
        "by_hall": True,
    },
    "WHX_TECH": {
        "label": "Exhibitors at WHX Tech",
        "landing": "https://www.worldhealthexpo.com/events/healthcare/tech/",
        "directory": WHX_TECH_DIRECTORY_URL,
        "by_hall": True,
    },
}

EVENT_SLUG = "world-healthcare-week-2027"
EVENT_NAME = "World Healthcare Week 2027"
TARGET_VENUE = "DEC"
VENUE_FILTER_LABEL = "DEC"

# Hall Numbers filter options (room-by-room / hall-by-hall scrape)
HALL_NUMBERS = (
    "South Hall - 1",
    "South Hall - 3",
    "South Hall - 5",
    "South Hall - 7",
    "South Hall - 9",
    "South Hall - 11",
    "South Hall - 13",
    "South Hall - 15",
    "South Hall - 17",
    "South Hall - 19",
    "North Hall - 39",
    "North Hall - 37",
    "North Hall - 35",
    "North Hall - 33",
    "North Hall - 31",
    "North Hall - 29",
    "North Hall - 27",
    "North Hall - 25",
    "North Hall - 23",
    "North Hall - 21",
    "North Concourse",
    "South Concourse",
)

PROFILE_URL_TEMPLATE = (
    "https://connections.whxevents.com/widget/event/"
    f"{EVENT_SLUG}/exhibitor/{{exhibitor_id}}"
)

# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------
HEADLESS = False
PAGE_LOAD_TIMEOUT = 60
EXPLICIT_WAIT = 25
IMPLICIT_WAIT = 0
WINDOW_SIZE = (1400, 1000)
USER_AGENT = None  # use Chrome default; do not spoof
# Optional override for any laptop, e.g. r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CHROME_BINARY = os.environ.get("WHX_CHROME_BINARY") or os.environ.get("CHROME_BINARY")

# ---------------------------------------------------------------------------
# Timing / polite scraping
# ---------------------------------------------------------------------------
REQUEST_DELAY_SECONDS = 0.6
SCROLL_PAUSE_SECONDS = 2.0
FILTER_REFRESH_WAIT = 2.0
PROFILE_DELAY_SECONDS = 0.35
# Skip directory card hunt (virtual list) — direct profile URL + bs4 parse
FAST_DIRECT_PROFILE_NAV = True
PROFILE_NAV_WAIT_SECONDS = 0.45

# Infinite-scroll termination
SCROLL_STABILITY_THRESHOLD = 4  # consecutive rounds with no new URLs
SCROLL_MAX_ROUNDS = 400
SCROLL_PIXEL_STEP = 1400
# If fewer URLs than this are in the DB, treat discovery as incomplete and
# re-scroll (standard Selenium often stalls around ~50 cards).
MIN_EXPECTED_DISCOVERY_COUNT = 200

# Retries
MAX_PROFILE_RETRIES = 3
RETRY_BASE_BACKOFF = 2.0
SESSION_RECOVERY_ATTEMPTS = 2

# If this many exhibitors in a row hit the site "Oops" page under normal Chrome,
# switch to undetected-chromedriver and retry profiles. If UC also fails, fall
# back to directory-card data.
SITE_OOPS_STREAK_LIMIT = 3
USE_UNDETECTED_FALLBACK = True
DIRECTORY_ONLY_NOTE = (
    "directory_only: profile pages return 'Oops, something went wrong' "
    "under automated Chrome; saved public directory-card fields instead"
)

# ---------------------------------------------------------------------------
# CAPTCHA / access restriction detection
# ---------------------------------------------------------------------------
CAPTCHA_TITLE_MARKERS = ("Client Challenge",)
CAPTCHA_BODY_MARKERS = (
    "Enter the characters seen in the image",
    "Play audio CAPTCHA",
    "Download audio CAPTCHA",
)

# ---------------------------------------------------------------------------
# Detail status values
# ---------------------------------------------------------------------------
STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"  # directory card only / open failed — NOT fully scraped
STATUS_FAILED = "failed"
STATUS_NEEDS_REVIEW = "needs_review"
