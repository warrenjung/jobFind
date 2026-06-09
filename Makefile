LOCATION ?= Cupertino, CA
RADIUS   ?= 10
PAGES    ?= 3
RESULTS  ?= 25
MIN_SCORE ?= 50
PORT     ?= 8000
PYTHON   := python3

# ── Setup ─────────────────────────────────────────────────────────────────────

.PHONY: install
install: ## Install Python deps and download Playwright's Chromium browser
	pip3 install -r requirements.txt
	$(PYTHON) -m playwright install chromium

# ── Pipeline ──────────────────────────────────────────────────────────────────

.PHONY: run
run: ## Run the full pipeline  (override location: make run LOCATION="San Jose, CA")
	$(PYTHON) pipeline/run_job_pipeline.py \
		--location "$(LOCATION)" \
		--indeed-radius $(RADIUS) \
		--indeed-pages $(PAGES) \
		--num-usajobs-results $(RESULTS) \
		--clean-min-score $(MIN_SCORE)

.PHONY: run-wide
run-wide: ## Same as run but with a 25-mile radius (good for small cities)
	$(MAKE) run RADIUS=25

.PHONY: run-skip-indeed
run-skip-indeed: ## Re-rank using the existing Indeed CSV (no scraping)
	$(PYTHON) pipeline/run_job_pipeline.py \
		--location "$(LOCATION)" \
		--skip-indeed \
		--clean-min-score $(MIN_SCORE)

.PHONY: run-skip-usajobs
run-skip-usajobs: ## Re-scrape Indeed only, skip USAJOBS fetch
	$(PYTHON) pipeline/run_job_pipeline.py \
		--location "$(LOCATION)" \
		--indeed-radius $(RADIUS) \
		--indeed-pages $(PAGES) \
		--skip-usajobs \
		--clean-min-score $(MIN_SCORE)

# ── Utilities ─────────────────────────────────────────────────────────────────

.PHONY: table
table: ## Regenerate the clean HTML cards from existing ranked jobs
	$(PYTHON) pipeline/export_clean_table.py --min-score $(MIN_SCORE) --location "$(LOCATION)"

.PHONY: serve
serve: ## Serve the HTML results over HTTP (default port 8000; override with PORT=)
	@echo "Open http://localhost:$(PORT)/jobs_clean.html  (Ctrl+C to stop)"
	$(PYTHON) -m http.server $(PORT) --directory data

.PHONY: clean
clean: ## Remove all generated output files (data/)
	rm -rf data/

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
