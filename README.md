# Job Finder

Find entry-level, teen-friendly summer jobs near any city. Scrapes **Indeed** by
default, with optional **USAJOBS** and **CareerOneStop** sources. It ranks
everything by fit for an ~18-year-old with no degree, license, or certification,
then exports an interactive HTML page you can filter and sort.

> **Note:** USAJOBS is **off by default** — it returns remote/nationwide federal
> career roles (analyst, physician, etc.) that aren't realistic teen jobs. Add
> `make run-with-usajobs` (or `--include-usajobs`) if you want them.

## How it works

```
USAJOBS API ────────────────────────────────┐
Indeed (Playwright headless browser) ───────┼─► rank_jobs.py ──► jobs_ranked.json
CareerOneStop Jobs V2 API (optional) ───────┘                      ▼
  └─ scrape_indeed.py                                      jobs_clean.html
  └─ pipeline/build_csv.py (filter)
```

1. `usajobs_summer_scraper.py` — fetches temporary/seasonal roles from USAJOBS
2. `careeronestop_scraper.py` — fetches optional local listings from CareerOneStop Jobs V2
3. `scrape_indeed.py` — scrapes Indeed with a headless Chromium browser
4. `pipeline/build_csv.py` — filters out jobs requiring degrees, licenses, or 3+ years experience
5. `rank_jobs.py` — scores and ranks all jobs; outputs `jobs_ranked.json`
6. `export_clean_table.py` — exports easy-to-read HTML job cards
7. `run_job_pipeline.py` — orchestrates all of the above in one command

## Setup

**One-time install** (run after cloning):

```bash
make install
```

This installs `playwright` and downloads the Chromium browser used for Indeed
scraping. Requires Python 3.9+ and `pip3`.

### USAJOBS credentials

The USAJOBS API requires a free account. Register at
[developer.usajobs.gov](https://developer.usajobs.gov) then provide your key
in **one** of these ways:

**Option A — credentials file** (recommended, already gitignored):
```json
// usajobs_credentials.json
{
  "api_key": "YOUR_KEY",
  "user_agent": "your@email.com"
}
```

**Option B — environment variables:**
```bash
export USAJOBS_API_KEY=YOUR_KEY
export USAJOBS_USER_AGENT=your@email.com
```

### CareerOneStop credentials

CareerOneStop code is included, but the pipeline skips it by default because
Jobs V2 requires separate NLx jobs-data approval. Once access is approved,
provide credentials in **one** of these ways and use `make run-with-careeronestop`:

**Option A — credentials file** (recommended, already gitignored):
```json
// careeronestop_credentials.json
{
  "user_id": "YOUR_USER_ID",
  "api_token": "YOUR_API_TOKEN"
}
```

**Option B — environment variables:**
```bash
export CAREERONESTOP_USER_ID=YOUR_USER_ID
export CAREERONESTOP_API_TOKEN=YOUR_API_TOKEN
```

## Usage

```bash
# Full pipeline for a city
make run LOCATION="San Jose, CA"

# Wider search radius (useful for small cities that return few results)
make run-wide LOCATION="Cupertino, CA"

# Fast smoke run with fewer Indeed queries/pages
make run-fast LOCATION="Cupertino, CA"

# Start a local browser app for searching without rerunning terminal commands
make app

# Re-rank only — reuse existing Indeed CSV, skip scraping
make run-skip-indeed LOCATION="Cupertino, CA"

# Add USAJOBS federal results (off by default)
make run-with-usajobs LOCATION="Cupertino, CA"

# Include CareerOneStop after NLx Jobs API access is approved
make run-with-careeronestop LOCATION="Cupertino, CA"

# Run the unit tests
make test

# Rebuild the readable HTML cards from existing ranked jobs
make table

# Serve the HTML results in a browser (http://localhost:8000/jobs_clean.html)
make serve

# Remove all generated output files
make clean
```

All targets:

```
make install          Install Python deps + Playwright Chromium
make run              Run the full pipeline
make run-wide         Same as run but with a 25-mile radius
make run-fast         Faster smoke run with fewer Indeed queries/pages
make run-skip-indeed  Re-rank using the existing Indeed CSV
make run-with-usajobs Run the pipeline and include USAJOBS federal results
make run-with-careeronestop Include CareerOneStop API results once NLx access is approved
make table            Regenerate the readable HTML cards
make app              Run the local browser search app
make serve            Serve the HTML results over HTTP (port 8000)
make test             Run the unit test suite
make clean            Remove all generated output files
make help             Show this message
```

Override any default:
```bash
make run LOCATION="Seattle, WA" RADIUS=25 PAGES=5 MIN_SCORE=60 FROMAGE=30
```

`FROMAGE` controls the Indeed freshness window in days (default 14; use 0 for no
limit). The generated HTML page lets you filter by title/employer/city and sort
by best fit, highest pay, or closest — all client-side, no rerun needed.

For targeted Indeed experiments, pass custom queries directly:
```bash
python3 pipeline/run_job_pipeline.py --location "Seattle, WA" --indeed-queries cashier barista "retail associate"
```

CareerOneStop-specific options are used by `make run-with-careeronestop`:
```bash
make run-with-careeronestop LOCATION="Seattle, WA" CAREER_RESULTS=50 CAREER_DAYS=60
```

The readable HTML output hides obvious poor fits by default, only showing jobs
with a score of 50 or higher. Use `MIN_SCORE=0` to show every ranked job.

## Local browser app

Run this once:

```bash
make app
```

Then open [http://localhost:8000](http://localhost:8000). The page lets you
enter a location, choose a radius, pick Fast or Full mode, set a minimum score,
and refresh the embedded results page from the browser.

This app is local-only. The browser sends form values to the Python server on
your machine, and the server runs the same pipeline commands documented above.
API keys stay in gitignored credential files or environment variables and are
not sent to the browser. A static public HTML file can show existing results,
but it cannot safely run new API-backed searches for each visitor without a
real backend.

## Output files

All generated files are written to `data/` (created automatically, gitignored):

| File | Description |
|------|-------------|
| `data/jobs_clean.html` | Clean readable job cards rated 50+ with job, employer, description, pay, distance, score, and apply link |
| `data/jobs_clean.md` | Optional raw Markdown table export |
| `data/jobs_ranked.json` | Detailed ranked list from all sources |
| `data/jobs_raw.json` | Raw USAJOBS results |
| `data/jobs_careeronestop_<city>.json` | Optional raw CareerOneStop API results |
| `data/jobs_scraped_<city>.json` | Raw Indeed cards from the scraper |
| `data/indeed_jobs_<city>.csv` | Filtered teen-friendly Indeed jobs |
| `data/indeed_jobs_<city>_excluded.csv` | Filtered-out jobs and reasons |

Most users should open `data/jobs_clean.html` in a browser first. The detailed
`data/jobs_ranked.json` file keeps the full ranked list, including lower-scored
jobs hidden from the clean page. The optional `data/jobs_clean.md` file is raw
Markdown and can look strange in plain text editors if the app does not render
Markdown tables. Run `make clean` to remove the whole `data/` directory.

## Claude Code skill

This repo includes a Claude Code skill at `.claude/skills/job-pipeline/SKILL.md`.
When you open this project in Claude Code, you can run the full pipeline just by
asking:

> *"Find jobs near San Jose, CA"*
> *"Run the job pipeline for Seattle"*

Claude will invoke `run_job_pipeline.py` automatically.

## Project layout

```
jobFind/
├── .claude/
│   └── skills/
│       ├── job-pipeline/           # Full pipeline skill (auto-loaded by Claude Code)
│       │   └── SKILL.md
│       └── indeed-job-scrape/      # Legacy browser skill + reference docs
│           ├── SKILL.md
│           └── references/
│               └── indeed-reference.md
├── pipeline/                       # All Python pipeline scripts
│   ├── run_job_pipeline.py         # Main orchestrator
│   ├── scrape_indeed.py            # Headless Indeed scraper (Playwright)
│   ├── rank_jobs.py                # Scoring + ranking engine
│   ├── export_clean_table.py       # Human-readable HTML/Markdown exporter
│   ├── careeronestop_scraper.py    # CareerOneStop Jobs V2 API client
│   ├── usajobs_summer_scraper.py   # USAJOBS API client
│   └── build_csv.py               # Filter scraped JSON → clean CSV
├── data/                           # Generated outputs (gitignored, auto-created)
├── usajobs_credentials.json        # Your USAJOBS API key (gitignored)
├── careeronestop_credentials.json  # Your CareerOneStop API token (gitignored)
├── Makefile                        # install / run / clean targets
├── requirements.txt                # Python dependencies
└── README.md
```

## Troubleshooting

**`playwright: command not found`** — use `python3 -m playwright install chromium`
instead, or just run `make install`.

**Indeed returns 0 jobs** — try a wider radius (`make run-wide`), a faster
targeted smoke run (`make run-fast`), or check if Indeed is showing a bot
challenge. Debug standalone:
`python3 pipeline/scrape_indeed.py --location "City, ST" --pages 1 --queries cashier`

**USAJOBS SSL warning** (`NotOpenSSLWarning`) — harmless on macOS with system
Python; results still fetch correctly.

**CareerOneStop returns 401 Unauthorized** — the token can be valid for general
CareerOneStop APIs while Jobs V2 remains unavailable until NLx jobs-data access
is approved. Use the normal `make run` while waiting, then switch to
`make run-with-careeronestop`.

**`TypeError: unsupported operand type(s) for |`** — Python 3.9 compatibility
issue. Scripts are patched; if it reappears in `build_csv.py`, replace any
`X | None` type hints with `Optional[X]` and add `from typing import Optional`.
