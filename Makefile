# Convenience wrapper — delegates to whx_exhibitor_scraper/Makefile

SCRAPER := whx_exhibitor_scraper

.PHONY: help build install rebuild run run-cli run-details check-chrome inspect smoke export clean clean-output

help build install rebuild run run-cli run-details check-chrome inspect smoke export clean clean-output:
	@$(MAKE) -C $(SCRAPER) $@
