"""
Cross-platform Google Chrome discovery and version detection.

Works on Windows, macOS, and Linux laptops without hard-coded machine paths.
Optional override: set WHX_CHROME_BINARY or config.settings.CHROME_BINARY.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("whx_scraper.chrome_detect")

VERSION_RE = re.compile(r"(\d+\.\d+\.\d+\.\d+|\d+\.\d+\.\d+|\d+)")


@dataclass
class ChromeInfo:
    binary: str
    version: str  # e.g. 143.0.7499.169
    major: str  # e.g. 143
    os_name: str

    @property
    def summary(self) -> str:
        return f"Chrome {self.version} ({self.os_name}) @ {self.binary}"


def _run_version(binary: str) -> str | None:
    try:
        # Chrome prints "Google Chrome 143.0.7499.169" on stdout (or stderr on some builds)
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        text = f"{proc.stdout or ''}\n{proc.stderr or ''}".strip()
        match = VERSION_RE.search(text)
        return match.group(1) if match else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("chrome --version failed for %s: %s", binary, exc)
        return None


def _windows_registry_version() -> str | None:
    if platform.system() != "Windows":
        return None
    try:
        proc = subprocess.run(
            [
                "reg",
                "query",
                r"HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon",
                "/v",
                "version",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        match = VERSION_RE.search(proc.stdout or "")
        if match:
            return match.group(1)
    except Exception:  # noqa: BLE001
        pass
    try:
        proc = subprocess.run(
            [
                "reg",
                "query",
                r"HKEY_LOCAL_MACHINE\SOFTWARE\Google\Chrome\BLBeacon",
                "/v",
                "version",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        match = VERSION_RE.search(proc.stdout or "")
        if match:
            return match.group(1)
    except Exception:  # noqa: BLE001
        pass
    return None


def _candidate_binaries() -> list[str]:
    """Ordered list of likely Chrome executables for this OS."""
    system = platform.system()
    found: list[str] = []

    # Env / settings override first
    for key in ("WHX_CHROME_BINARY", "CHROME_BINARY", "GOOGLE_CHROME_BIN"):
        value = os.environ.get(key)
        if value and Path(value).exists():
            found.append(value)

    path_hits = [
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    for hit in path_hits:
        if hit:
            found.append(hit)

    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        win_paths = [
            str(Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            str(Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            str(Path(pf86) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        ]
        found.extend(win_paths)
    elif system == "Darwin":
        found.extend(
            [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                str(Path.home() / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"),
            ]
        )
    else:
        # Linux / other Unix
        found.extend(
            [
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium-browser",
                "/usr/bin/chromium",
                "/snap/bin/chromium",
                "/opt/google/chrome/chrome",
                "/opt/google/chrome/google-chrome",
            ]
        )

    # Deduplicate while preserving order
    ordered: list[str] = []
    seen: set[str] = set()
    for item in found:
        if not item:
            continue
        key = str(Path(item))
        if key in seen:
            continue
        seen.add(key)
        if Path(item).exists():
            ordered.append(item)
    return ordered


def detect_chrome(preferred_binary: str | None = None) -> ChromeInfo:
    """
    Locate installed Google Chrome and read its version.

    Raises RuntimeError with a clear install hint if Chrome is missing.
    """
    system = platform.system()
    candidates: list[str] = []
    if preferred_binary:
        candidates.append(preferred_binary)
    candidates.extend(_candidate_binaries())

    last_error = None
    for binary in candidates:
        if not binary or not Path(binary).exists():
            continue
        version = _run_version(binary)
        if not version and system == "Windows":
            version = _windows_registry_version()
        if not version:
            last_error = f"Could not read version from {binary}"
            continue
        major = version.split(".", 1)[0]
        info = ChromeInfo(
            binary=str(Path(binary).resolve()) if Path(binary).exists() else binary,
            version=version,
            major=major,
            os_name=f"{system} {platform.machine()}",
        )
        logger.info("Detected %s", info.summary)
        return info

    raise RuntimeError(
        "Google Chrome was not found on this laptop.\n"
        "Install Chrome from https://www.google.com/chrome/ then re-run.\n"
        "Or set WHX_CHROME_BINARY to the full path of chrome/chrome.exe.\n"
        f"OS={system} arch={platform.machine()} last_error={last_error}"
    )


def driver_version_for_chrome(chrome: ChromeInfo) -> str:
    """
    Prefer full browser version for driver matching; major is a safe fallback
    for Chrome for Testing style ChromeDriver channels (115+).
    """
    return chrome.version
