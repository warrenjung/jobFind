LOCATION ?= Cupertino, CA
RADIUS   ?= 10
PAGES    ?= 3
RESULTS  ?= 25
PYTHON   := python3

# ── Setup ─────────────────────────────────────────────────────────────────────

.PHONY: install
install: ## Install Python deps and download Playwright's Chromium browser
	pip3 install -r requirements.txt
	$(PYTHON) -m playwright install chromium

# ── Pipeline ──────────────────────────────────────────────────────────────────

.PHONY: run
run: ## Run the full pipeline  (override location: make run LOCATION="San Jose, CA")
	$(PYTHON) run_job_pipeline.py \
		--location "$(LOCATION)" \
		--indeed-radius $(RADIUS) \
		--indeed-pages $(PAGES) \
		--num-usajobs-results $(RESULTS)

.PHONY: run-wide
run-wide: ## Same as run but with a 25-mile radius (good for small cities)
	$(MAKE) run RADIUS=25

.PHONY: run-skip-indeed
run-skip-indeed: ## Re-rank using the existing Indeed CSV (no scraping)
	$(PYTHON) run_job_pipeline.py \
		--location "$(LOCATION)" \
		--skip-indeed

.PHONY: run-skip-usajobs
run-skip-usajobs: ## Re-scrape Indeed only, skip USAJOBS fetch
	$(PYTHON) run_job_pipeline.py \
		--location "$(LOCATION)" \
		--indeed-radius $(RADIUS) \
		--indeed-pages $(PAGES) \
		--skip-usajobs

# ── Utilities ─────────────────────────────────────────────────────────────────

.PHONY: clean
clean: ## Remove all generated output files
	rm -f jobs_raw.json jobs_ranked.json jobs_scraped*.json
	rm -f indeed_jobs*.csv indeed_jobs*_excluded.csv

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
