# WHX Dubai DEC Exhibitor Scraper
# Usage: make help

PYTHON      ?= python3
VENV        := .venv
BIN         := $(VENV)/bin
PIP         := $(BIN)/pip
PY          := $(BIN)/python
REQ         := requirements.txt
OUTPUT_DIR  := output
LOG_DIR     := logs

.PHONY: help build install run run-cli run-details check-chrome inspect smoke export clean clean-output rebuild ensure-chrome

help:
	@echo "WHX DEC Exhibitor Scraper — make targets"
	@echo ""
	@echo "  make build         Create venv + install dependencies (alias: install)"
	@echo "  make rebuild       Recreate venv and reinstall from requirements.txt"
	@echo "  make run           Auto-detect Chrome, match driver, start GUI"
	@echo "  make run-cli       Auto-detect Chrome, then full CLI scrape"
	@echo "  make run-details   Auto-detect Chrome, resume pending profiles only"
	@echo "  make check-chrome  Detect Chrome version + verify matching ChromeDriver"
	@echo "  make inspect       Dump directory DOM inspection JSON"
	@echo "  make smoke         Bounded end-to-end smoke test"
	@echo "  make export        Re-export CSV/JSON/summary from existing DB"
	@echo "  make clean         Remove venv, caches, and build artifacts"
	@echo "  make clean-output  Remove output/*.csv|json|db|txt and logs"
	@echo ""
	@echo "Examples:"
	@echo "  make build && make run"

build install: $(PY)
	@mkdir -p $(OUTPUT_DIR) $(LOG_DIR)
	$(PIP) install --upgrade pip
	$(PIP) install -r $(REQ)
	@echo ""
	@echo "Build complete. Next: make run"

$(PY):
	$(PYTHON) -m venv $(VENV)
	@echo "Created virtualenv: $(VENV)"

rebuild: clean build

# Ensure deps exist, detect Chrome for this laptop, then start.
ensure-chrome: $(PY)
	@mkdir -p $(OUTPUT_DIR) $(LOG_DIR)
	@if ! $(PY) -c "import selenium" >/dev/null 2>&1; then \
		echo "Dependencies missing — running make build..."; \
		$(MAKE) build; \
	fi
	@echo ">>> Auto-detecting installed Chrome for this laptop..."
	@$(PY) -c "from browser.chrome_preflight import ensure_chrome_ready; ensure_chrome_ready()"

run: ensure-chrome
	@echo ">>> Starting GUI (ChromeDriver will match detected Chrome version)..."
	$(PY) main.py

run-cli: ensure-chrome
	@echo ">>> Starting CLI scrape..."
	$(PY) main.py --cli

run-details: ensure-chrome
	@echo ">>> Resuming pending profile details..."
	$(PY) main.py --cli --details-only

check-chrome: $(PY)
	@if ! $(PY) -c "import selenium" >/dev/null 2>&1; then $(MAKE) build; fi
	$(PY) main.py --check-chrome

inspect: ensure-chrome
	$(PY) main.py --inspect

smoke: ensure-chrome
	$(PY) run_smoke_test.py

export: $(PY)
	@mkdir -p $(OUTPUT_DIR)
	$(PY) -c "from storage.database import Database; from storage.exporters import export_index_csv, export_details_csv, export_details_json, write_summary_report; \
db=Database(); \
print(export_index_csv(db)); \
print(export_details_csv(db)); \
print(export_details_json(db)); \
print(write_summary_report(db))"

clean:
	rm -rf $(VENV) __pycache__
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	@echo "Cleaned venv and Python caches"

clean-output:
	rm -f $(OUTPUT_DIR)/*.csv $(OUTPUT_DIR)/*.json $(OUTPUT_DIR)/*.db $(OUTPUT_DIR)/*.txt
	rm -f $(LOG_DIR)/*.log $(LOG_DIR)/*.log.*
	@echo "Cleaned output and logs"
