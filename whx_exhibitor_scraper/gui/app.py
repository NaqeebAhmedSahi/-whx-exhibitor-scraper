"""Tkinter desktop GUI — responsive via queue + after() polling."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from browser.chrome_detector import ChromeInfo
from config import settings
from scraper_service import ScraperService


class ScraperGUI:
    def __init__(self, root: tk.Tk, chrome_info: ChromeInfo | None = None) -> None:
        self.root = root
        self.root.title("WHX Dubai DEC Exhibitor Scraper")
        self.root.geometry("860x780")
        self.root.minsize(760, 640)
        self.chrome_info = chrome_info

        self.msg_queue: queue.Queue = queue.Queue()
        self.service = ScraperService(
            on_status=lambda m: self.msg_queue.put(("status", m)),
            on_progress=lambda p: self.msg_queue.put(("progress", p)),
            on_finished=lambda m: self.msg_queue.put(("finished", m)),
        )

        chrome_label = (
            f"detected {chrome_info.version}" if chrome_info else "not detected yet"
        )
        self.vars = {
            "target": tk.StringVar(value=settings.DIRECTORY_URL),
            "browser": tk.StringVar(value=f"stopped ({chrome_label})"),
            "chrome": tk.StringVar(
                value=chrome_info.summary if chrome_info else "Auto-detect on Start"
            ),
            "phase": tk.StringVar(value="idle"),
            "discovered": tk.StringVar(value="0"),
            "dec": tk.StringVar(value="0"),
            "unique_urls": tk.StringVar(value="0"),
            "completed": tk.StringVar(value="0"),
            "pending": tk.StringVar(value="0"),
            "failed": tk.StringVar(value="0"),
            "current_company": tk.StringVar(value="—"),
            "current_url": tk.StringVar(value="—"),
            "progress_pct": tk.StringVar(value="0%"),
            "status": tk.StringVar(value="Ready"),
        }

        self._build()
        self._refresh_from_db()
        if chrome_info:
            self._append_log(f"Chrome auto-detected: {chrome_info.summary}")
            self._append_log("Matching ChromeDriver will be used when you click Start.")
        self.root.after(200, self._poll_queue)

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 4}
        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="World Health Expo — DEC Exhibitor Scraper", font=("Segoe UI", 14, "bold")).pack(
            anchor=tk.W
        )

        info = ttk.LabelFrame(frm, text="Status", padding=8)
        info.pack(fill=tk.X, pady=4)
        # Compact 2-column status so the log keeps room + scrollbar
        left_rows = [
            ("Chrome", "chrome"),
            ("Browser", "browser"),
            ("Phase", "phase"),
            ("Discovered", "discovered"),
            ("Completed", "completed"),
            ("Pending", "pending"),
        ]
        right_rows = [
            ("Failed", "failed"),
            ("Progress", "progress_pct"),
            ("Company", "current_company"),
            ("URL", "current_url"),
            ("Target", "target"),
        ]
        for i, (label, key) in enumerate(left_rows):
            ttk.Label(info, text=f"{label}:").grid(row=i, column=0, sticky=tk.W, **pad)
            ttk.Label(info, textvariable=self.vars[key], wraplength=280).grid(
                row=i, column=1, sticky=tk.W, **pad
            )
        for i, (label, key) in enumerate(right_rows):
            ttk.Label(info, text=f"{label}:").grid(row=i, column=2, sticky=tk.W, padx=(20, 10), pady=4)
            ttk.Label(info, textvariable=self.vars[key], wraplength=320).grid(
                row=i, column=3, sticky=tk.W, **pad
            )

        self.progress = ttk.Progressbar(frm, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X, pady=4)

        opts = ttk.LabelFrame(frm, text="What to scrape", padding=8)
        opts.pack(fill=tk.X, pady=4)
        venue_row = ttk.Frame(opts)
        venue_row.pack(fill=tk.X, anchor=tk.W)
        self.venue_vars = {
            "DEC": tk.BooleanVar(value=True),
            "DWTC": tk.BooleanVar(value=False),
            "WHX_TECH": tk.BooleanVar(value=False),
        }
        ttk.Checkbutton(
            venue_row,
            text="DEC",
            variable=self.venue_vars["DEC"],
            command=self._on_venue_toggle,
        ).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Checkbutton(
            venue_row,
            text="DWTC",
            variable=self.venue_vars["DWTC"],
            command=self._on_venue_toggle,
        ).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Checkbutton(
            venue_row,
            text="WHX Tech",
            variable=self.venue_vars["WHX_TECH"],
            command=self._on_venue_toggle,
        ).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Button(
            venue_row, text="Select all", command=self._on_select_all_venues
        ).pack(side=tk.LEFT, padx=(8, 0))

        self.by_hall_var = tk.BooleanVar(value=True)
        self.by_hall_chk = ttk.Checkbutton(
            opts,
            text="Hall by hall / room by room (Hall Numbers filter per venue)",
            variable=self.by_hall_var,
        )
        self.by_hall_chk.pack(anchor=tk.W, pady=(6, 0))
        ttk.Label(
            opts,
            text="Fast mode: direct profile URLs + BeautifulSoup parse.",
            wraplength=720,
        ).pack(anchor=tk.W, pady=(2, 0))

        btns = ttk.Frame(frm)
        btns.pack(fill=tk.X, pady=6)
        ttk.Button(btns, text="Start", command=self._on_start).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Pause", command=self._on_pause).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Resume", command=self._on_resume).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Stop", command=self._on_stop).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Export CSV", command=self._on_export_csv).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Export JSON", command=self._on_export_json).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Open output folder", command=self._on_open_output).pack(
            side=tk.LEFT, padx=4
        )

        ttk.Label(frm, text="Log").pack(anchor=tk.W)
        log_frame = ttk.Frame(frm)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 4))
        self.log = tk.Text(log_frame, height=14, wrap=tk.WORD)
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set, state=tk.DISABLED)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        def _wheel(event: tk.Event) -> str:
            # Linux Button-4/5 or Windows/macOS MouseWheel
            if getattr(event, "num", None) == 4:
                self.log.yview_scroll(-3, "units")
            elif getattr(event, "num", None) == 5:
                self.log.yview_scroll(3, "units")
            else:
                delta = int(getattr(event, "delta", 0) or 0)
                self.log.yview_scroll(int(-1 * (delta / 120)) if delta else -3, "units")
            return "break"

        for widget in (self.log, log_frame):
            widget.bind("<MouseWheel>", _wheel)
            widget.bind("<Button-4>", _wheel)
            widget.bind("<Button-5>", _wheel)

        ttk.Label(frm, textvariable=self.vars["status"]).pack(anchor=tk.W, pady=2)

    def _append_log(self, message: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, message + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)
        self.vars["status"].set(message[:120])

    def _apply_progress(self, payload: dict[str, Any]) -> None:
        mapping = {
            "phase": "phase",
            "discovered": "discovered",
            "dec": "dec",
            "unique_urls": "unique_urls",
            "completed": "completed",
            "pending": "pending",
            "failed": "failed",
            "current_company": "current_company",
            "current_url": "current_url",
        }
        for src, dest in mapping.items():
            if src in payload and payload[src] is not None:
                self.vars[dest].set(str(payload[src]))
        pct = payload.get("progress_pct")
        if pct is not None:
            self.vars["progress_pct"].set(f"{pct}%")
            try:
                self.progress["value"] = float(pct)
            except (TypeError, ValueError):
                pass
        if payload.get("browser"):
            self.vars["browser"].set(str(payload["browser"]))
        elif self.service.chrome.driver:
            self.vars["browser"].set("running")

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "status":
                    self._append_log(str(payload))
                elif kind == "progress":
                    self._apply_progress(payload)
                elif kind == "finished":
                    self._append_log(f"Worker finished: {payload}")
                    snap = self.service.snapshot_progress()
                    self._apply_progress(snap)
                    if payload == "captcha":
                        messagebox.showwarning(
                            "CAPTCHA detected",
                            "A CAPTCHA / Client Challenge appeared.\n\n"
                            "Solve it in the visible Chrome window, then click Resume.\n"
                            "This scraper will not bypass CAPTCHA.",
                        )
        except queue.Empty:
            pass
        self.root.after(200, self._poll_queue)

    def _refresh_from_db(self) -> None:
        self._apply_progress(self.service.snapshot_progress())

    def _on_select_all_venues(self) -> None:
        for var in self.venue_vars.values():
            var.set(True)

    def _on_venue_toggle(self) -> None:
        # Kept for checkbox command= hooks; do not clear hall-by-hall.
        return

    def _selected_venues(self) -> list[str]:
        selected = [k for k, v in self.venue_vars.items() if v.get()]
        return selected or ["DEC"]

    def _on_start(self) -> None:
        self.vars["browser"].set("starting")
        venues = self._selected_venues()
        work = self.service.db.remaining_work_count()
        if work["total"] > 0:
            self._append_log(
                f"Local DB: {work['total']} URLs discovered so far "
                f"({work['completed']} actually scraped, "
                f"{work.get('directory_only', 0)} opened/partial only — not counted scraped, "
                f"{work['pending']} still to process)."
            )
        else:
            self._append_log("Start requested (fresh run)")
        by_hall = bool(self.by_hall_var.get())
        self._append_log(f"Venues: {', '.join(venues)}")
        if by_hall:
            self._append_log("Discovery mode: hall-by-hall Hall Numbers filter")
        self._append_log("Profile scrape: direct URL + BeautifulSoup (fast)")
        self.service.start(
            skip_discovery=None, by_hall=by_hall, venues=venues
        )

    def _on_pause(self) -> None:
        self.service.pause()

    def _on_resume(self) -> None:
        venues = self._selected_venues()
        self._append_log(
            f"Resume with venues: {', '.join(venues)} "
            "(after CAPTCHA, continues profiles — stays on selected venues)"
        )
        self.service.resume(venues=venues)
        self.vars["browser"].set("running")

    def _on_stop(self) -> None:
        self.service.stop()
        self.vars["browser"].set("stopped")

    def _on_export_csv(self) -> None:
        paths = self.service.export_csv()
        self._append_log(f"CSV exported:\n{paths}")
        messagebox.showinfo("Export CSV", paths)

    def _on_export_json(self) -> None:
        path = self.service.export_json()
        self._append_log(f"JSON exported: {path}")
        messagebox.showinfo("Export JSON", path)

    def _on_open_output(self) -> None:
        path = settings.OUTPUT_DIR
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            os.startfile(str(path))  # type: ignore[attr-defined]


def run_gui(chrome_info: ChromeInfo | None = None) -> None:
    root = tk.Tk()
    # Prefer a theme that exists cross-platform
    try:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:  # noqa: BLE001
        pass
    ScraperGUI(root, chrome_info=chrome_info)
    root.mainloop()


if __name__ == "__main__":
    from browser.chrome_preflight import ensure_chrome_ready

    info = ensure_chrome_ready()
    run_gui(chrome_info=info)
