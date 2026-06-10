---
name: job-pipeline
description: >-
  Run the full job-finding pipeline for a given location: scrape Indeed with a
  headless browser, filter and rank results, then present a ranked list of
  entry-level jobs suitable for a high-school senior (~18 years old) with no
  degree, license, or certification. USAJOBS and CareerOneStop are optional
  extra sources. Use this skill whenever the user asks to "find jobs", "run the
  job pipeline", "search for jobs near X", "get ranked jobs", "what jobs are
  available near X", or any similar request to get a full ranked list of local
  entry-level job opportunities. Also trigger when the user wants fresh job
  results and has a location in mind.
---

# Job Pipeline

Run the complete end-to-end job-finding pipeline for a location. This skill:

1. Scrapes Indeed with a headless Playwright browser (`scrape_indeed.py`)
2. Filters Indeed results to teen-friendly roles (`build_csv.py`)
3. Ranks and deduplicates all jobs into a single scored list (`rank_jobs.py`)
4. Exports an interactive HTML page (`export_clean_table.py`)
5. Presents the top results to the user

Optional extra sources: **USAJOBS** (federal roles — opt-in) and **CareerOneStop**
(opt-in, needs NLx approval). The pipeline is fully automated — no manual browser
steps required.

## Inputs to confirm

Before running, confirm:

- **Location** (required) — city + state, e.g. `"Cupertino, CA"`. If the user
  gives only a city, infer the state and confirm.
- **Radius** — default **10 miles**. Accepted values: 0, 5, 10, 15, 25, 35, 50.
- **Indeed pages per query** — default 3 (≈30 results per search term).
- **Freshness window** — default **14 days** (`--indeed-fromage`). Use 0 for no limit.
- **Project folder** — the `jobFind` project directory where pipeline scripts live.

If everything is clear from context, just run.

## Workflow

### 1. Locate the project folder

The pipeline scripts live in the `jobFind` project directory. Confirm the path
(typically `/Users/<user>/project/jobFind`). All commands below should be run
from that directory.

Check that the required scripts exist:
- `pipeline/run_job_pipeline.py`
- `pipeline/scrape_indeed.py`
- `pipeline/rank_jobs.py`
- `pipeline/build_csv.py`
- `pipeline/export_clean_table.py`

### 2. Run the pipeline

By default the pipeline uses **only Indeed** — no credentials are needed. Run:

```bash
cd /path/to/jobFind && python3 pipeline/run_job_pipeline.py \
  --location "<CITY, STATE>" \
  --indeed-radius <RADIUS> \
  --indeed-pages <PAGES> \
  --indeed-fromage <DAYS> \
  --preview-count 15
```

The simplest equivalent is `make run LOCATION="<CITY, STATE>"`.

Common flags:
- `--include-usajobs` — also fetch USAJOBS federal roles (needs USAJOBS creds; see
  below). Off by default because these are remote/nationwide career jobs, not teen
  jobs.
- `--skip-indeed` — skip Indeed scraping and rank an existing CSV.
- `--indeed-radius 25` — wider search area if a small city returns few results.
- `--indeed-queries cashier barista ...` — override the built-in query list.

**USAJOBS credentials (only if `--include-usajobs`):** check for
`usajobs_credentials.json` in the project folder, or `USAJOBS_API_KEY` /
`USAJOBS_USER_AGENT` env vars. If missing, tell the user USAJOBS will be skipped;
Indeed results are unaffected.

Stream the output to the user so they can see progress. The scraper prints each
query as it runs (e.g. "Searching: 'cashier' near Cupertino, CA").

**Expected output files (in `data/`):**
- `jobs_scraped_<city>.json` — raw Indeed cards
- `indeed_jobs_<city>.csv` — filtered teen-friendly Indeed jobs
- `jobs_ranked.json` — final ranked list (all sources)
- `jobs_clean.html` — interactive page (filter + sort)
- `jobs_raw.json` — raw USAJOBS results (only when `--include-usajobs`)

### 3. Handle common issues

- **"No cards found" for most Indeed queries** — usually fine; the scraper retries
  an empty first page once, then moves on. Suggest `--indeed-radius 25` for broader
  coverage in small cities.
- **USAJOBS SSL warning** (`NotOpenSSLWarning`) — harmless, can be ignored.
- **0 Indeed jobs after scraping** — Indeed may have changed its DOM. Run
  `python3 pipeline/scrape_indeed.py --pages 1 --queries cashier` standalone and
  check the output. Run `make test` to confirm the parsing helpers still pass.

### 4. Present results

Once `jobs_ranked.json` exists, read it and present a summary:

- Total jobs found (and source breakdown if USAJOBS/CareerOneStop were included)
- Top 10–15 jobs with: rank, score, title, company, location, pay, distance
- Point the user at `data/jobs_clean.html` (or `make serve` / `make app`) — it
  has a search box and sort controls (best fit / highest pay / closest)

Offer follow-up options:
- Widen the radius (`--indeed-radius 25` or `make run-wide`)
- Add federal roles (`--include-usajobs`)
- Open `data/jobs_clean.html` in a browser

## Notes

- The Indeed scraper uses Playwright + headless Chromium. Install once:
  `make install` (or `pip3 install -r requirements.txt && python3 -m playwright install chromium`).
- The scraper uses a **fresh browser context per query** and spaces requests out —
  this is what lets every query return results instead of just the first.
- The scraper goes **directly** to search URLs (no homepage warmup) — the homepage
  visit triggers bot detection.
- Indeed result cards carry no posting date, so freshness is shown as the search
  window ("Within last N days") rather than per-job dates.
- Third-party dependencies are `playwright`, `requests`, and `pytest` (tests).
