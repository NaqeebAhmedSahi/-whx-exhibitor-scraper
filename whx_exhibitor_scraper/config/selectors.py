"""
CSS / XPath selectors derived from live DOM inspection (Swapcard widget).

Styled-components hashes change between builds; prefer attribute and
stable class-prefix selectors (e.g. [class*="grid__Name-"]).
Update this file when the site markup changes — scraping logic reads from here.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Challenge / loading
# ---------------------------------------------------------------------------
CAPTCHA_ANSWER_INPUT = 'input[name="Answer"], input[aria-label*="Answer" i], input[placeholder*="Answer" i]'
CAPTCHA_SUBMIT = 'button[type="submit"]'
LOADING_INDICATORS = [
    '[class*="loading" i]',
    '[class*="spinner" i]',
    '[aria-busy="true"]',
]

# ---------------------------------------------------------------------------
# Directory shell
# ---------------------------------------------------------------------------
APP_ROOT = "#__next"
DIRECTORY_TITLE = 'h2[class*="list__Title-"], [class*="mobile-headline__Title-"]'
FILTERS_BUTTON = 'button[aria-label="Filters"]'
SEARCH_BUTTON = 'button[aria-label="Search"]'
FILTER_PANEL_HEADER = '[class*="style__Header-"]'
FILTER_SECTION_HEADER = '[class*="item__ItemHeader-"]'
FILTER_CHECKBOX_LABEL = '[class*="checkbox-with-label__Label-"]'
FILTER_CHECKBOX_TITLE = '[class*="checkbox-with-label__Title-"]'
FILTER_CHECKBOX_INPUT = 'input[type="checkbox"]'
LOAD_MORE_BUTTON = (
    'button[aria-label*="Load more" i], '
    'button[aria-label*="Show more" i], '
    '//button[contains(translate(., "LOAD MORE", "load more"), "load more")]'
)

# Landing-page iframe embedding the Swapcard widget
LANDING_IFRAME = (
    'iframe[src*="connections.whxevents.com"], '
    'iframe[src*="swapcard"], '
    'iframe[id]'
)
COOKIE_ACCEPT_SELECTORS = [
    '#transcend-consent-manager',
    'button#onetrust-accept-btn-handler',
    '//button[contains(., "Accept all")]',
    '//button[contains(., "Accept All")]',
]

# ---------------------------------------------------------------------------
# Exhibitor cards (directory grid)
# Observed structure:
#   a[href*="/exhibitor/"]
#     img[class*="image__Image-"][alt=company]
#     span[class*="grid__Name-"]
#     span[class*="grid__Type-"]   -> "Exhibitors at DEC" | "Exhibitors at DWTC"
#     span[class*="location__Booth-"]  -> "S9.B90" | "DEC · N33.D38" | "DWTC · ..."
# ---------------------------------------------------------------------------
EXHIBITOR_CARD_LINKS = 'a[href*="/exhibitor/"]'
EXHIBITOR_GRID = '[class*="container__Grid-"], [class*="wrapper__GridWrapper-"]'
CARD_NAME = '[class*="grid__Name-"]'
CARD_TYPE = '[class*="grid__Type-"]'
CARD_BOOTH = '[class*="location__Booth-"]'
CARD_LOCATION_ROW = '[class*="location__LocationRow-"]'
CARD_THUMBNAIL = '[class*="grid__Thumbnail-"] img, img[class*="image__Image-"]'

# ---------------------------------------------------------------------------
# Profile page
# Observed for 1A+A+A Wundprodukte GmbH:
#   h1[class*="style__Name-"]
#   h2[class*="style__Type-"] -> "Exhibitors at DEC"
#   [class*="booth-item__Content-"] -> booth
#   Information rows: [class*="style__Name-sc-f4425fbd"] / [class*="style__Content-sc-f4425fbd"]
#   Hall chips: [class*="chip__SpanWrapper-"]
# ---------------------------------------------------------------------------
PROFILE_BACK = 'button[aria-label="Back"]'
PROFILE_COMPANY_NAME = 'h1[class*="style__Name-"], h1'
PROFILE_TYPE = 'h2[class*="style__Type-"]'
PROFILE_BOOTH = '[class*="booth-item__Content-"]'
PROFILE_SECTION_TITLE = 'h2[class*="title__Title-"]'
PROFILE_FIELD_WRAPPER = '[class*="style__Wrapper-sc-f4425fbd"]'
PROFILE_FIELD_NAME = '[class*="style__Name-sc-f4425fbd"]'
PROFILE_FIELD_CONTENT = '[class*="style__Content-sc-f4425fbd"]'
PROFILE_CHIP = '[class*="chip__SpanWrapper-"]'
PROFILE_LOGO = 'img[class*="image__Image-"], img[alt]'
PROFILE_DESCRIPTION = (
    '[class*="description" i], '
    '[class*="About" i], '
    '[data-testid*="description" i]'
)
PROFILE_SOCIAL_LINKS = 'a[href*="linkedin.com"], a[href*="facebook.com"], a[href*="instagram.com"], a[href*="twitter.com"], a[href*="x.com"], a[href*="youtube.com"]'
PROFILE_WEBSITE_CANDIDATES = (
    'a[href^="http"]:not([href*="connections.whxevents.com"])'
    ':not([href*="swapcard.com"])'
    ':not([href*="google.com/maps"])'
    ':not([href*="linkedin.com"])'
    ':not([href*="facebook.com"])'
    ':not([href*="instagram.com"])'
)
PROFILE_EMAIL_LINKS = 'a[href^="mailto:"]'
PROFILE_PHONE_LINKS = 'a[href^="tel:"]'
PROFILE_CONTACT_BLOCK = '//h2[contains(., "Contact")]/ancestor::div[contains(@class,"Block") or contains(@class,"Section")][1]'

# Known Information-section field labels observed on profiles
PROFILE_KNOWN_FIELD_LABELS = (
    "Venue",
    "Country",
    "Hall Numbers",
    "City",
    "Address",
    "Nature of Business",
    "Products & Categories Offered",
    "Products and Services",
    "Country Coverage",
    "Zones",
    "Exhibition Zone",
    "Website",
    "Email",
    "Telephone",
    "Phone",
    "Contact Person",
)

# ---------------------------------------------------------------------------
# Empty / end-of-list hints
# ---------------------------------------------------------------------------
EMPTY_RESULTS = [
    '//*[contains(., "No results")]',
    '//*[contains(., "No exhibitors")]',
    '[class*="empty" i]',
]
