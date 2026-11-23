# WHX Dubai DEC Exhibitor Scraper

Python 3 + Selenium 4 scraper for the publicly accessible **World Health Expo / World Healthcare Week 2027** exhibitor directory, limited to exhibitors at **Dubai Exhibition Centre (DEC)**.

Runs **visible Google Chrome** (headed by default). Uses normal ChromeDriver first; if the site keeps serving Oops pages, it **falls back to undetected-chromedriver** and re-tries profile enrichment. It still **does not** solve CAPTCHAs — solve those manually in the Chrome window, then Resume.

## Targets

| Page | URL |
|------|-----|
| Landing | https://www.worldhealthexpo.com/events/healthcare/dubai/exhibitors-at-dec/ |
| Full directory | https://connections.whxevents.com/widget/event/world-healthcare-week-2027/exhibitors/RXZlbnRWaWV3XzI2NTkzNDU |
| DEC EventView (embedded on landing) | `.../exhibitors/RXZlbnRWaWV3XzI4Nzc1NjY=` |
| Profile example | `.../exhibitor/RXhoaWJpdG9yXzI2MDI2NTY=` |

The landing page embeds the Swapcard widget in an iframe. The scraper prefers the **direct widget URL**, opens **Filters → Venue → DEC**, then infinite-scrolls until stable.

## DOM notes (inspected)

- Platform: Swapcard (`img.swapcard.com`, Next.js `#__next`, styled-components).
- Cards: `a[href*="/exhibitor/"]`
- Name: `[class*="grid__Name-"]`
- Type: `[class*="grid__Type-"]` → `Exhibitors at DEC` / `Exhibitors at DWTC`
- Booth: `[class*="location__Booth-"]`
- Filters button: `button[aria-label="Filters"]`
- Venue filter options: **DEC**, **DWTC**
- Other filters: Nature of Business, Country, Country Coverage, Zones, Hall Numbers, product categories
- Profile fields: labeled rows `[class*="style__Name-sc-f4425fbd"]` / `[class*="style__Content-sc-f4425fbd"]`
- Pagination: window/document infinite scroll (no Load More observed); Load More click supported if it appears
- Access: FingerprintJS **Client Challenge** CAPTCHA may appear — scraper **pauses and reports**; solve manually in Chrome, then Resume

Selectors live in `config/selectors.py` so they can be updated without rewriting logic.

## Setup (any Windows / macOS / Linux laptop)

### Using Make (recommended on Linux/macOS)

```bash
cd whx_exhibitor_scraper
make build          # create .venv + install deps
make run            # auto-detect Chrome version, match driver, start GUI
```

`make run` automatically:
1. Ensures dependencies are installed  
2. Detects the Chrome binary/version on this laptop  
3. Starts the GUI; on Start it launches a matching ChromeDriver  

Other targets: `make check-chrome`, `make run-cli`, `make run-details`, `make inspect`, `make smoke`, `make export`, `make clean`, `make help`.

### Manual setup

```bash
cd whx_exhibitor_scraper
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Requires **Google Chrome** installed. On start the scraper:

1. Detects the installed Chrome binary for this OS  
2. Reads its version (e.g. `143.0.7499.169`)  
3. Downloads/uses a **matching ChromeDriver** via Selenium Manager  
4. Falls back to `webdriver-manager` pinned to that same Chrome version if needed  

Optional override if Chrome is in a custom path:

```bash
# Linux/macOS
export WHX_CHROME_BINARY="/usr/bin/google-chrome"

# Windows PowerShell
$env:WHX_CHROME_BINARY="C:\Program Files\Google\Chrome\Application\chrome.exe"
```

Verify on any laptop:

```bash
python main.py --check-chrome
```

## Usage

### GUI (default)

```bash
python main.py
```

Controls: Start / Pause / Resume / Stop / Export CSV / Export JSON / Open output folder.

### CLI

```bash
python main.py --cli
python main.py --cli --details-only          # resume pending profiles only
python main.py --cli --dec-event-view        # use DEC-prefiltered EventView
python main.py --inspect                     # dump DOM inspection JSON
```

## Outputs

| File | Contents |
|------|----------|
| `output/whx_dec_exhibitors.db` | SQLite: `exhibitors_index`, `exhibitors_details` |
| `output/exhibitor_urls.csv` | Discovered profile URLs |
| `output/whx_dec_exhibitors.csv` | Detail extraction |
| `output/whx_dec_exhibitors.json` | Detail extraction (JSON) |
| `output/scrape_summary.txt` | Quality / completion report |
| `logs/scraper.log` | Runtime log |

## Resume / reliability

- Profile URLs are saved to SQLite **before** detail scraping.
- **Mid-run restart is safe:** Start/Resume skips every exhibitor already marked `completed` and continues from the next pending URL.
- If URLs already exist in the DB, directory rediscovery is skipped.
- Stuck `in_progress` rows reset to `pending` on start; completed detail rows are re-synced onto the index so they are never opened again.
- Completed detail rows are never overwritten (including by empty/failed payloads).
- Transient profile failures retry with exponential backoff.
- Permanent access errors → `needs_review`.
- Duplicate profile URLs are logged; canonical key is the normalized profile URL.

## Compliance

- Headed Chrome only by default
- Reasonable delays between actions
- No authentication bypass, no CAPTCHA solving automation
- Only publicly rendered fields are stored; missing values are `NULL`
