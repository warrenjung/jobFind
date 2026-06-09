---
name: job-pipeline
description: >-
  Run the full job-finding pipeline for a given location: scrape USAJOBS,
  scrape Indeed with a headless browser, filter and rank results, then present
  a ranked list of entry-level jobs suitable for a high-school senior (~18 years
  old) with no degree, license, or certification. Use this skill whenever the
  user asks to "find jobs", "run the job pipeline", "search for jobs near X",
  "get ranked jobs", "what jobs are available near X", or any similar request
  to get a full ranked list of local entry-level job opportunities. Also trigger
  when the user wants fresh job results and has a location in mind.
---

# Job Pipeline

Run the complete end-to-end job-finding pipeline for a location. This skill:

1. Fetches temporary/seasonal jobs from the USAJOBS API
2. Scrapes Indeed with a headless Playwright browser (`scrape_indeed.py`)
3. Filters Indeed results to teen-friendly roles (`build_csv.py`)
4. Ranks and deduplicates all jobs into a single scored list (`rank_jobs.py`)
5. Presents the top results to the user

The pipeline is fully automated — no manual browser steps required.

## Inputs to confirm

Before running, confirm:

- **Location** (required) — city + state, e.g. `"Cupertino, CA"`. If the user
  gives only a city, infer the state and confirm.
- **Radius** — default **10 miles**. Accepted values: 0, 5, 10, 15, 25, 35, 50.
- **USAJOBS results** — default 25.
- **Indeed pages per query** — default 3 (≈30 results per search term).
- **Project folder** — the `jobFind` project directory where pipeline scripts live.

If everything is clear from context, just run.

## Workflow

### 1. Locate the project folder

The pipeline scripts live in the `jobFind` project directory. Confirm the path
(typically `/Users/<user>/project/jobFind`). All commands below should be run
from that directory.

Check that the required scripts exist:
- `pipeline/run_job_pipeline.py`
- `scrape_indeed.py`
- `rank_jobs.py`
- `usajobs_summer_scraper.py`
- `pipeline/build_csv.py`

### 2. Check USAJOBS credentials

The USAJOBS scraper needs credentials. Check for `usajobs_credentials.json` in
the project folder, or `USAJOBS_API_KEY` / `USAJOBS_USER_AGENT` env vars. If
missing, warn the user that USAJOBS results will be skipped but Indeed results
will still be fetched.

### 3. Run the pipeline

Run the full pipeline with a single command:

```bash
cd /path/to/jobFind && python3 pipeline/run_job_pipeline.py \
  --location "<CITY, STATE>" \
  --indeed-radius <RADIUS> \
  --indeed-pages <PAGES> \
  --num-usajobs-results <N> \
  --preview-count 15
```

Common flags:
- `--skip-usajobs` — skip USAJOBS fetch (use if credentials are missing)
- `--skip-indeed` — skip Indeed scraping (use an existing CSV)
- `--indeed-radius 25` — wider search area if Cupertino-sized city returns few results

Stream the output to the user so they can see progress. The scraper will print
each query as it runs (e.g. "Searching: 'cashier' near Cupertino, CA").

**Expected output files:**
- `jobs_scraped_<location>.json` — raw Indeed cards
- `indeed_jobs_<city>.csv` — filtered teen-friendly Indeed jobs
- `jobs_raw.json` — raw USAJOBS results
- `jobs_ranked.json` — final ranked list

### 4. Handle common issues

- **"No cards found" for most Indeed queries** — normal for small cities. The
  scraper still captures what it can. Suggest `--indeed-radius 25` for broader
  coverage.
- **USAJOBS SSL warning** (`NotOpenSSLWarning`) — harmless, can be ignored.
- **`TypeError: unsupported operand type(s) for |`** — Python 3.9 compatibility
  issue. The scripts have been patched; if it still appears, check that
  `build_csv.py` uses `Optional[X]` not `X | None` in type hints.
- **0 Indeed jobs after scraping** — Indeed may have changed its DOM. Run
  `scrape_indeed.py` standalone with `--pages 1 --queries cashier` and check
  the output.

### 5. Present results

Once `jobs_ranked.json` exists, read it and present a summary:

- Total jobs found (Indeed vs. USAJOBS breakdown)
- Top 10–15 jobs with: rank, score, title, company, location, pay, distance
- Any notable filters that removed a lot of results

Offer follow-up options:
- Widen the radius (`--indeed-radius 25`)
- Re-run with `--skip-usajobs` to refresh only Indeed results
- Open `jobs_ranked.json` for the full list

## Notes

- The Indeed scraper (`scrape_indeed.py`) uses Playwright + headless Chromium.
  It must be installed once: `pip3 install playwright && python3 -m playwright install chromium`.
- The scraper goes **directly** to search URLs (no homepage warmup) — the
  homepage visit triggers bot detection.
- All pipeline scripts are pure Python; only `playwright` and `requests` are
  third-party dependencies.
- USAJOBS results skew toward federal/government jobs; Indeed results cover
  local retail, food service, and other typical teen jobs. Both sources together
  give the best coverage.
