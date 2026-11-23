"""Visible Google Chrome session management via Selenium 4 (+ optional UC fallback)."""

from __future__ import annotations

import logging
import platform
import time
from typing import Callable

from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

from browser.chrome_detector import ChromeInfo, detect_chrome, driver_version_for_chrome
from config import settings

logger = logging.getLogger("whx_scraper.browser")


class CaptchaDetectedError(RuntimeError):
    """Raised when the site presents a CAPTCHA / client challenge."""


class AccessRestrictedError(RuntimeError):
    """Raised for explicit access denials that should not be retried blindly."""


class SitePageError(RuntimeError):
    """Raised when Swapcard shows 'Oops, something went wrong'."""


class ChromeManager:
    def __init__(
        self,
        *,
        headless: bool | None = None,
        on_status: Callable[[str], None] | None = None,
        chrome_binary: str | None = None,
        use_undetected: bool = False,
    ) -> None:
        self.headless = settings.HEADLESS if headless is None else headless
        self.on_status = on_status or (lambda _msg: None)
        self.chrome_binary_override = chrome_binary or getattr(
            settings, "CHROME_BINARY", None
        )
        self.use_undetected = use_undetected
        self.driver: webdriver.Chrome | None = None
        self.chrome_info: ChromeInfo | None = None

    def _emit(self, message: str) -> None:
        logger.info(message)
        self.on_status(message)

    def _build_options(self, chrome: ChromeInfo) -> Options:
        options = Options()
        options.binary_location = chrome.binary

        if self.headless:
            options.add_argument("--headless=new")
        else:
            if platform.system() != "Darwin":
                options.add_argument("--start-maximized")

        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-notifications")
        options.add_argument("--lang=en-US")
        options.add_argument(
            f"--window-size={settings.WINDOW_SIZE[0]},{settings.WINDOW_SIZE[1]}"
        )
        options.page_load_strategy = "normal"
        return options

    def _start_with_selenium_manager(self, options: Options) -> webdriver.Chrome:
        self._emit("Resolving ChromeDriver via Selenium Manager (match installed Chrome)")
        return webdriver.Chrome(options=options)

    def _start_with_webdriver_manager(
        self, options: Options, chrome: ChromeInfo
    ) -> webdriver.Chrome:
        from webdriver_manager.chrome import ChromeDriverManager

        wanted = driver_version_for_chrome(chrome)
        self._emit(
            f"Resolving ChromeDriver via webdriver-manager for Chrome {wanted}"
        )
        try:
            driver_path = ChromeDriverManager(driver_version=wanted).install()
        except Exception as exc:  # noqa: BLE001
            self._emit(
                f"Exact driver {wanted} unavailable ({exc}); trying major {chrome.major}"
            )
            driver_path = ChromeDriverManager(driver_version=chrome.major).install()

        self._emit(f"ChromeDriver path: {driver_path}")
        service = Service(driver_path)
        return webdriver.Chrome(service=service, options=options)

    def _start_with_undetected(self, chrome: ChromeInfo) -> webdriver.Chrome:
        """
        Fallback when normal Selenium is blocked by the site (Oops pages).
        Uses undetected-chromedriver matched to the installed Chrome major version.
        Still does NOT solve CAPTCHAs — if a challenge appears, we pause for the user.
        """
        import undetected_chromedriver as uc

        self._emit(
            f"Starting undetected-chromedriver for Chrome major {chrome.major} "
            f"(fallback after normal driver was blocked)"
        )
        options = uc.ChromeOptions()
        options.binary_location = chrome.binary
        if self.headless:
            options.add_argument("--headless=new")
        else:
            if platform.system() != "Darwin":
                options.add_argument("--start-maximized")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-notifications")
        options.add_argument("--lang=en-US")
        options.add_argument(
            f"--window-size={settings.WINDOW_SIZE[0]},{settings.WINDOW_SIZE[1]}"
        )

        # version_main pins UC driver to installed Chrome major (e.g. 143)
        try:
            version_main = int(chrome.major)
        except ValueError:
            version_main = None

        driver = uc.Chrome(
            options=options,
            version_main=version_main,
            use_subprocess=True,
        )
        return driver

    def start(self) -> webdriver.Chrome:
        if self.driver is not None:
            return self.driver

        chrome = detect_chrome(self.chrome_binary_override)
        self.chrome_info = chrome
        self._emit(f"Detected {chrome.summary}")
        mode = "undetected-chromedriver" if self.use_undetected else "standard ChromeDriver"
        self._emit(
            f"Starting Google Chrome via {mode} "
            f"({'headed' if not self.headless else 'headless'})"
        )

        errors: list[str] = []

        if self.use_undetected:
            try:
                self.driver = self._start_with_undetected(chrome)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"undetected-chromedriver: {exc}")
                self._emit(f"undetected-chromedriver failed: {exc}; trying standard driver")
                self.use_undetected = False

        if self.driver is None:
            options = self._build_options(chrome)
            try:
                self.driver = self._start_with_selenium_manager(options)
            except (WebDriverException, SessionNotCreatedException, OSError) as exc:
                errors.append(f"Selenium Manager: {exc}")
                self._emit(f"Selenium Manager failed: {exc}")
                try:
                    self.driver = self._start_with_webdriver_manager(options, chrome)
                except Exception as exc2:  # noqa: BLE001
                    errors.append(f"webdriver-manager: {exc2}")
                    # Last resort: UC even if not requested
                    if settings.USE_UNDETECTED_FALLBACK:
                        try:
                            self.use_undetected = True
                            self.driver = self._start_with_undetected(chrome)
                        except Exception as exc3:  # noqa: BLE001
                            errors.append(f"undetected-chromedriver: {exc3}")
                            raise RuntimeError(
                                "Could not start Chrome with a matching driver.\n"
                                f"Chrome detected: {chrome.summary}\n"
                                + "\n".join(errors)
                            ) from exc3
                    else:
                        raise RuntimeError(
                            "Could not start Chrome with a matching ChromeDriver.\n"
                            f"Chrome detected: {chrome.summary}\n"
                            + "\n".join(errors)
                        ) from exc2

        assert self.driver is not None
        self.driver.set_page_load_timeout(settings.PAGE_LOAD_TIMEOUT)
        self.driver.implicitly_wait(settings.IMPLICIT_WAIT)

        try:
            if not self.headless:
                self.driver.set_window_size(
                    settings.WINDOW_SIZE[0], settings.WINDOW_SIZE[1]
                )
                try:
                    self.driver.maximize_window()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

        caps_version = ""
        try:
            caps = self.driver.capabilities or {}
            browser = caps.get("browserVersion") or caps.get("version") or ""
            driver_v = (
                (caps.get("chrome") or {}).get("chromedriverVersion")
                or caps.get("chrome.chromedriverVersion")
                or ""
            )
            mode = "uc" if self.use_undetected else "standard"
            caps_version = f"mode={mode} browser={browser} chromedriver={driver_v}"
        except Exception:  # noqa: BLE001
            pass

        self._emit(f"Browser ready ({caps_version or chrome.version})")
        return self.driver

    def stop(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:  # noqa: BLE001
                logger.exception("Error while quitting Chrome")
            self.driver = None
            self._emit("Browser closed")

    def restart(self, *, use_undetected: bool | None = None) -> webdriver.Chrome:
        if use_undetected is not None:
            self.use_undetected = use_undetected
        self._emit(
            "Recovering browser session"
            + (" with undetected-chromedriver..." if self.use_undetected else "...")
        )
        self.stop()
        time.sleep(1.5)
        return self.start()

    def switch_to_undetected(self) -> webdriver.Chrome:
        """Close standard Chrome and reopen with undetected-chromedriver."""
        self._emit(
            "Normal ChromeDriver is blocked by the site — switching to undetected-chromedriver"
        )
        return self.restart(use_undetected=True)

    @property
    def wait(self) -> WebDriverWait:
        if not self.driver:
            raise RuntimeError("Browser not started")
        return WebDriverWait(self.driver, settings.EXPLICIT_WAIT)

    def get(self, url: str) -> None:
        if not self.driver:
            self.start()
        assert self.driver is not None
        self._emit(f"Navigating: {url}")
        self.driver.get(url)
        time.sleep(settings.REQUEST_DELAY_SECONDS)
        self.raise_if_blocked()

    def page_body_text(self) -> str:
        if not self.driver:
            return ""
        try:
            return self.driver.find_element("tag name", "body").text or ""
        except Exception:  # noqa: BLE001
            return ""

    def raise_if_blocked(self) -> None:
        """Detect CAPTCHA / Oops / access walls. Does not solve CAPTCHAs."""
        if not self.driver:
            return
        title = (self.driver.title or "").strip()
        body = self.page_body_text()

        if any(m.lower() in title.lower() for m in settings.CAPTCHA_TITLE_MARKERS):
            raise CaptchaDetectedError(
                "CAPTCHA / Client Challenge detected. Solve it manually in the "
                "visible Chrome window, then click Resume. The scraper will not bypass it."
            )
        lowered = body.lower()
        if any(m.lower() in lowered for m in settings.CAPTCHA_BODY_MARKERS):
            raise CaptchaDetectedError(
                "CAPTCHA challenge form detected on page. Manual intervention required."
            )
        if "oops, something went wrong" in lowered or (
            "something went wrong" in lowered and "back" in lowered
        ):
            raise SitePageError(
                "Site error page: 'Oops, something went wrong'. "
                "Will return to directory and continue with the next exhibitor."
            )
        if "access denied" in lowered or "403 forbidden" in lowered:
            raise AccessRestrictedError("Explicit access restriction detected.")

    def current_url(self) -> str:
        return self.driver.current_url if self.driver else ""

    def page_source_snippet(self, n: int = 500) -> str:
        if not self.driver:
            return ""
        return (self.driver.page_source or "")[:n]
