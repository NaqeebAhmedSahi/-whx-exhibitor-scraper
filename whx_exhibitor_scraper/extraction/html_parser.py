"""
Fast BeautifulSoup parsers for Swapcard directory/profile HTML.

Selenium only drives the browser; field extraction runs on page_source via bs4
so we avoid hundreds of slow WebElement round-trips per profile.
"""

from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from extraction.field_normalizer import clean_text
from storage.database import normalize_profile_url


def _exhibitor_id_from_url(url: str) -> str | None:
    path = urlparse(url).path.rstrip("/")
    token = path.split("/")[-1] if path else ""
    if not token or token == "exhibitor":
        return None
    raw = unquote(token)
    pad = "=" * ((4 - len(raw) % 4) % 4)
    try:
        return base64.b64decode(raw + pad).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return raw

# ISO-3166-1 alpha-2 → English country name (common exhibitor set)
ISO_COUNTRY = {
    "AE": "United Arab Emirates",
    "AT": "Austria",
    "AU": "Australia",
    "BE": "Belgium",
    "BG": "Bulgaria",
    "BR": "Brazil",
    "CA": "Canada",
    "CH": "Switzerland",
    "CN": "China",
    "CZ": "Czech Republic",
    "DE": "Germany",
    "DK": "Denmark",
    "EG": "Egypt",
    "ES": "Spain",
    "FI": "Finland",
    "FR": "France",
    "GB": "United Kingdom",
    "UK": "United Kingdom",
    "GR": "Greece",
    "HK": "Hong Kong",
    "HR": "Croatia",
    "HU": "Hungary",
    "ID": "Indonesia",
    "IE": "Ireland",
    "IL": "Israel",
    "IN": "India",
    "IT": "Italy",
    "JP": "Japan",
    "KR": "South Korea",
    "KW": "Kuwait",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "MY": "Malaysia",
    "NL": "Netherlands",
    "NO": "Norway",
    "NZ": "New Zealand",
    "PH": "Philippines",
    "PK": "Pakistan",
    "PL": "Poland",
    "PT": "Portugal",
    "QA": "Qatar",
    "RO": "Romania",
    "RU": "Russia",
    "SA": "Saudi Arabia",
    "SE": "Sweden",
    "SG": "Singapore",
    "SI": "Slovenia",
    "SK": "Slovakia",
    "TH": "Thailand",
    "TR": "Turkey",
    "TW": "Taiwan",
    "UA": "Ukraine",
    "US": "United States",
    "VN": "Vietnam",
    "ZA": "South Africa",
}

LABEL_MAP = {
    "venue": "venue",
    "country": "country",
    "hall numbers": "hall_number",
    "hall number": "hall_number",
    "city": "city",
    "address": "address",
    "headquarters": "address",
    "headquarters location": "address",
    "nature of business": "nature_of_business",
    "products & categories offered": "product_categories",
    "products and categories offered": "product_categories",
    "products and services": "products_services",
    "products & services": "products_services",
    "country coverage": "countries_covered",
    "countries covered": "countries_covered",
    "zones": "zone",
    "exhibition zone": "zone",
    "website": "website",
    "email": "email",
    "telephone": "phone",
    "phone": "phone",
    "contact person": "contact_person",
    "contact": "contact_person",
    "company name": "company_name_alt",
    "legal name": "company_name_alt",
    "trading name": "company_name_alt",
    "also known as": "company_name_alt",
    "brand name": "company_name_alt",
}

COUNTRY_IN_TEXT = re.compile(
    r"(?:head\s*quarter[s]?\s+(?:at|in)|based in|located in|"
    r"headquarters?(?:\s+in)?|from)\s+[^.]{0,80}?\b("
    + "|".join(
        sorted(
            {
                "Germany",
                "India",
                "China",
                "Turkey",
                "United Kingdom",
                "United States",
                "France",
                "Italy",
                "Spain",
                "Poland",
                "Switzerland",
                "Netherlands",
                "Belgium",
                "Austria",
                "Japan",
                "South Korea",
                "Canada",
                "Australia",
                "UAE",
                "United Arab Emirates",
                "Saudi Arabia",
                "Egypt",
                "Pakistan",
                "Malaysia",
                "Singapore",
                "Brazil",
                "Sweden",
                "Denmark",
                "Finland",
                "Norway",
                "Czech Republic",
                "Hungary",
                "Romania",
                "Portugal",
                "Ireland",
                "Israel",
                "Taiwan",
                "Thailand",
                "Indonesia",
                "Vietnam",
                "Russia",
                "Ukraine",
                "Croatia",
                "Slovenia",
                "Slovakia",
                "Lithuania",
                "Greece",
                "Kuwait",
                "Qatar",
                "Hong Kong",
                "South Africa",
                "New Zealand",
            },
            key=len,
            reverse=True,
        )
    )
    + r")\b",
    re.I,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "lxml")


def _text(el) -> str | None:
    if el is None:
        return None
    return clean_text(el.get_text(" ", strip=True))


def _map_label(label: str) -> str | None:
    key = re.sub(r"\s+", " ", label.strip().lower())
    return LABEL_MAP.get(key)


def extract_labeled_fields_html(soup: BeautifulSoup) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    raw_fields: dict[str, Any] = {}
    # Swapcard information rows
    for name_el in soup.select('[class*="style__Name-sc-f4425fbd"], [class*="style__Name-"]'):
        label = _text(name_el)
        if not label or len(label) > 80:
            continue
        wrap = name_el.find_parent(class_=re.compile(r"style__Wrapper"))
        if wrap is None:
            wrap = name_el.parent
        content_el = None
        chips: list[str] = []
        if wrap is not None:
            content_el = wrap.select_one('[class*="style__Content-"]')
            seen_chip: set[str] = set()
            for c in wrap.select('[class*="chip__SpanWrapper-"], [class*="chip__"]'):
                t = _text(c)
                if not t:
                    continue
                key = t.casefold()
                if key in seen_chip:
                    continue
                seen_chip.add(key)
                chips.append(t)
        value = _text(content_el) if content_el else None
        if not value and chips:
            value = "; ".join(chips)
        raw_fields[label] = {"value": value, "chips": chips}
        field = _map_label(label)
        if not field:
            continue
        if field == "hall_number" and chips:
            mapped[field] = "; ".join(chips)
        elif value:
            # Collapse accidental "Hall 3; Hall 3" style duplicates in text too
            if field == "hall_number" and ";" in value:
                parts = [p.strip() for p in value.split(";") if p.strip()]
                dedup: list[str] = []
                seen_p: set[str] = set()
                for p in parts:
                    k = p.casefold()
                    if k in seen_p:
                        continue
                    seen_p.add(k)
                    dedup.append(p)
                value = "; ".join(dedup)
            mapped[field] = value
        elif chips:
            mapped[field] = "; ".join(chips)
    mapped["raw_fields"] = raw_fields
    return mapped


def extract_country_code_html(soup: BeautifulSoup) -> str | None:
    """
    Contact-details flag links are often a 2-letter ISO code (DE, IN, TR).
    """
    # Prefer links/spans near "Contact details"
    candidates: list[str] = []
    for a in soup.find_all("a"):
        t = (a.get_text() or "").strip()
        if re.fullmatch(r"[A-Z]{2}", t):
            candidates.append(t)
    for span in soup.find_all(["span", "div", "button"]):
        t = (span.get_text() or "").strip()
        if re.fullmatch(r"[A-Z]{2}", t) and t in ISO_COUNTRY:
            candidates.append(t)
    for code in candidates:
        if code in ISO_COUNTRY:
            return ISO_COUNTRY[code]
    return None


def extract_description_html(soup: BeautifulSoup) -> str | None:
    """Pull the Information-section company blurb (second text block under the name)."""
    # Find Information heading then collect following paragraphs until next section
    info = None
    for h in soup.find_all(["h2", "h3"]):
        if _text(h) and _text(h).lower() == "information":
            info = h
            break
    if info is None:
        return None
    chunks: list[str] = []
    for sib in info.next_siblings:
        name = getattr(sib, "name", None)
        if name in {"h2", "h3"}:
            break
        if name is None:
            continue
        if name in {"p", "div", "span", "section", "article"}:
            # skip labeled field wrappers
            cls = " ".join(sib.get("class") or [])
            if "style__Wrapper" in cls or "style__Name" in cls:
                continue
            t = _text(sib)
            if not t:
                continue
            # stop if we hit labeled field labels
            if t.lower() in {
                "venue",
                "country",
                "hall numbers",
                "nature of business",
                "contact details",
                "products & categories offered",
            }:
                break
            if len(t) > 40:
                chunks.append(t)
        if len(chunks) >= 4:
            break
    if not chunks:
        # Fallback: longest paragraph-like block under Information ancestors
        root = info.find_parent() or info
        paras = []
        for el in root.find_all(["p", "div"]):
            t = _text(el)
            if t and len(t) > 80 and "Venue" not in t[:20]:
                paras.append(t)
        if paras:
            chunks = [max(paras, key=len)]
    text = clean_text(" ".join(chunks)) if chunks else None
    if text and len(text) > 40:
        return text
    return None


def extract_country_from_description(description: str | None) -> str | None:
    if not description:
        return None
    m = COUNTRY_IN_TEXT.search(description)
    if not m:
        return None
    name = m.group(1)
    if name.upper() == "UAE":
        return "United Arab Emirates"
    return name


def parse_profile_html(html: str, profile_url: str) -> dict[str, Any]:
    soup = _soup(html)
    company_name = None
    h1 = soup.select_one('h1[class*="style__Name-"], h1')
    company_name = _text(h1)
    if not company_name:
        title = soup.title.string if soup.title else None
        company_name = clean_text(title)

    type_el = soup.select_one('h2[class*="style__Type-"], h2')
    type_text = _text(type_el)
    if type_text and type_text.lower() in {"information", "contact details"}:
        type_text = None
        for h2 in soup.select("h2"):
            t = _text(h2)
            if t and "exhibitor" in t.lower():
                type_text = t
                break

    booth = _text(soup.select_one('[class*="booth-item__Content-"]'))
    # Secondary / legal / brand name (logo alt or labeled field)
    company_name_alt = None
    logo_url = None
    for img in soup.select('img[class*="image__Image-"], img[alt]'):
        src = img.get("src") or ""
        alt = clean_text(img.get("alt"))
        if src and logo_url is None:
            logo_url = src
        if alt and company_name and alt.lower() != company_name.lower():
            if company_name.lower() not in alt.lower() and len(alt) > 2:
                company_name_alt = alt
                break

    labeled = extract_labeled_fields_html(soup)
    description = extract_description_html(soup)

    # Contacts / socials from anchors
    email = phone = website = None
    linkedin = facebook = instagram = None
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        low = href.lower()
        text = (_text(a) or "").lower()
        if low.startswith("mailto:") and not email:
            email = href.split(":", 1)[1]
        elif low.startswith("tel:") and not phone:
            phone = href.split(":", 1)[1]
        elif "linkedin.com" in low and not linkedin:
            linkedin = href
        elif "facebook.com" in low and not facebook:
            facebook = href
        elif "instagram.com" in low and not instagram:
            instagram = href
        elif not website and href.startswith("http"):
            if not any(
                s in low
                for s in (
                    "connections.whxevents.com",
                    "swapcard.com",
                    "google.com/maps",
                    "linkedin.com",
                    "facebook.com",
                    "instagram.com",
                )
            ):
                if text in {"website", "visit website", "company website"} or True:
                    # Prefer explicit website label via labeled fields first
                    pass
    website = labeled.pop("website", None) or website
    # Second pass for website candidates
    if not website:
        for a in soup.select("a[href^='http']"):
            href = a.get("href") or ""
            low = href.lower()
            if any(
                s in low
                for s in (
                    "connections.whxevents.com",
                    "swapcard.com",
                    "google.com/maps",
                    "linkedin.com",
                    "facebook.com",
                    "instagram.com",
                    "twitter.com",
                    "x.com",
                    "youtube.com",
                )
            ):
                continue
            website = href
            break

    email = labeled.pop("email", None) or email
    phone = labeled.pop("phone", None) or phone
    if labeled.get("company_name_alt"):
        company_name_alt = labeled.pop("company_name_alt") or company_name_alt

    country = labeled.get("country")
    if not country:
        country = extract_country_code_html(soup)
    if not country:
        country = extract_country_from_description(description)

    venue = labeled.get("venue")
    if not venue and type_text:
        up = type_text.upper()
        if "DEC" in up:
            venue = "DEC"
        elif "DWTC" in up:
            venue = "DWTC"
        elif "WHX TECH" in up or "TECH" in up:
            venue = "WHX Tech"

    # Normalize booth "DEC · N25.F56" → keep readable middot
    if booth:
        booth = booth.replace("Â·", "·").replace("•", "·")

    raw_fields = labeled.pop("raw_fields", {})
    body_text = _text(soup.body) or ""
    payload = {
        "company_name": company_name,
        "company_name_alt": company_name_alt,
        "exhibitor_id": _exhibitor_id_from_url(profile_url),
        "profile_url": profile_url,
        "description": description,
        "logo_url": logo_url,
        "booth_number": booth,
        "type_text": type_text,
        "website": website,
        "email": email,
        "phone": phone,
        **labeled,
        "country": country,
        "venue": venue,
        "linkedin_url": linkedin,
        "facebook_url": facebook,
        "instagram_url": instagram,
        "raw_extracted_data": {
            "parser": "bs4",
            "type_text": type_text,
            "raw_fields": raw_fields,
            "body_excerpt": clean_text(body_text[:2000]),
            "company_name_alt": company_name_alt,
        },
    }
    return payload


def extract_directory_urls_html(html: str, base_url: str = "") -> list[dict[str, Any]]:
    """Fast directory card parse from page HTML (href + name + booth)."""
    soup = _soup(html)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in soup.select('a[href*="/exhibitor/"]'):
        href = a.get("href") or ""
        if base_url and href.startswith("/"):
            href = urljoin(base_url, href)
        url = normalize_profile_url(href)
        if not url or url in seen:
            continue
        seen.add(url)
        name_el = a.select_one('[class*="grid__Name-"]')
        type_el = a.select_one('[class*="grid__Type-"]')
        booth_el = a.select_one('[class*="location__Booth-"]')
        name = _text(name_el)
        if not name:
            lines = [ln.strip() for ln in (a.get_text("\n") or "").splitlines() if ln.strip()]
            name = lines[0] if lines else None
        records.append(
            {
                "company_name": name,
                "profile_url": url,
                "exhibitor_id": _exhibitor_id_from_url(url),
                "type_text": _text(type_el),
                "booth_text_raw": _text(booth_el),
                "booth_number": _text(booth_el),
            }
        )
    return records
