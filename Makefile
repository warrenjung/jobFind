LOCATION ?= Cupertino, CA
RADIUS   ?= 10
PAGES    ?= 3
RESULTS  ?= 25
MIN_SCORE ?= 50
FROMAGE  ?= 14
KEYWORDS ?=
CAREER_RESULTS ?= 25
CAREER_DAYS ?= 30
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
		--indeed-fromage $(FROMAGE) \
		--personal-keywords "$(KEYWORDS)" \
		--clean-min-score $(MIN_SCORE)

.PHONY: run-wide
run-wide: ## Same as run but with a 25-mile radius (good for small cities)
	$(MAKE) run RADIUS=25

.PHONY: run-fast
run-fast: ## Faster smoke run with fewer Indeed queries/pages
	$(PYTHON) pipeline/run_job_pipeline.py \
		--location "$(LOCATION)" \
		--indeed-radius $(RADIUS) \
		--indeed-pages 1 \
		--indeed-fromage $(FROMAGE) \
		--indeed-queries cashier "retail associate" barista \
		--personal-keywords "$(KEYWORDS)" \
		--clean-min-score $(MIN_SCORE)

.PHONY: run-skip-indeed
run-skip-indeed: ## Re-rank using the existing Indeed CSV (no scraping)
	$(PYTHON) pipeline/run_job_pipeline.py \
		--location "$(LOCATION)" \
		--skip-indeed \
		--personal-keywords "$(KEYWORDS)" \
		--clean-min-score $(MIN_SCORE)

.PHONY: run-with-usajobs
run-with-usajobs: ## Run the pipeline and include USAJOBS federal results
	$(PYTHON) pipeline/run_job_pipeline.py \
		--location "$(LOCATION)" \
		--indeed-radius $(RADIUS) \
		--indeed-pages $(PAGES) \
		--indeed-fromage $(FROMAGE) \
		--include-usajobs \
		--num-usajobs-results $(RESULTS) \
		--personal-keywords "$(KEYWORDS)" \
		--clean-min-score $(MIN_SCORE)

.PHONY: run-with-careeronestop
run-with-careeronestop: ## Include CareerOneStop API results once NLx access is approved
	$(PYTHON) pipeline/run_job_pipeline.py \
		--location "$(LOCATION)" \
		--indeed-radius $(RADIUS) \
		--indeed-pages $(PAGES) \
		--indeed-fromage $(FROMAGE) \
		--include-careeronestop \
		--careeronestop-results $(CAREER_RESULTS) \
		--careeronestop-days $(CAREER_DAYS) \
		--personal-keywords "$(KEYWORDS)" \
		--clean-min-score $(MIN_SCORE)

# ── Utilities ─────────────────────────────────────────────────────────────────

.PHONY: table
table: ## Regenerate the clean HTML cards from existing ranked jobs
	$(PYTHON) pipeline/export_clean_table.py --min-score $(MIN_SCORE) --location "$(LOCATION)"

.PHONY: app
app: ## Run the local browser search app (default port 8000; override with PORT=)
	@echo "Open http://localhost:$(PORT)  (Ctrl+C to stop)"
	$(PYTHON) pipeline/local_search_server.py --port $(PORT)

.PHONY: serve
serve: ## Serve the HTML results over HTTP (default port 8000; override with PORT=)
	@echo "Open http://localhost:$(PORT)/jobs_clean.html  (Ctrl+C to stop)"
	$(PYTHON) -m http.server $(PORT) --directory data

.PHONY: test
test: ## Run the unit test suite
	$(PYTHON) -m pytest -q

.PHONY: clean
clean: ## Remove all generated output files (data/)
	rm -rf data/

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
