# Job Finder

Find entry-level, teen-friendly summer jobs near any city. Pulls from two
sources — the **USAJOBS API** (federal/government temp roles) and **Indeed**
(local retail, food service, etc.) — ranks everything by fit for an ~18-year-old
with no degree, license, or certification, and outputs a single scored list.

## How it works

```
USAJOBS API ─────────────────────────────────┐
                                             ▼
Indeed (Playwright headless browser) ──► rank_jobs.py ──► jobs_ranked.json
  └─ scrape_indeed.py
  └─ indeed-job-scrape/scripts/build_csv.py (filter)
```

1. `usajobs_summer_scraper.py` — fetches temporary/seasonal roles from USAJOBS
2. `scrape_indeed.py` — scrapes Indeed with a headless Chromium browser (Playwright)
3. `indeed-job-scrape/scripts/build_csv.py` — filters out jobs requiring degrees, licenses, or 3+ years experience
4. `rank_jobs.py` — scores and ranks all jobs; outputs `jobs_ranked.json`
5. `run_job_pipeline.py` — orchestrates all of the above in one command

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

## Usage

```bash
# Full pipeline for a city
make run LOCATION="San Jose, CA"

# Wider search radius (useful for small cities that return few results)
make run-wide LOCATION="Cupertino, CA"

# Re-rank only — reuse existing Indeed CSV, skip scraping
make run-skip-indeed LOCATION="Cupertino, CA"

# Re-scrape Indeed only — skip USAJOBS fetch
make run-skip-usajobs LOCATION="Cupertino, CA"

# Remove all generated output files
make clean
```

All targets:

```
make install          Install Python deps + Playwright Chromium
make run              Run the full pipeline
make run-wide         Same as run but with a 25-mile radius
make run-skip-indeed  Re-rank using the existing Indeed CSV
make run-skip-usajobs Re-scrape Indeed, skip USAJOBS
make clean            Remove all generated output files
make help             Show this message
```

Override any default:
```bash
make run LOCATION="Seattle, WA" RADIUS=25 PAGES=5 RESULTS=50
```

## Output files

| File | Description |
|------|-------------|
| `jobs_raw.json` | Raw USAJOBS results |
| `jobs_scraped_<city>.json` | Raw Indeed cards from the scraper |
| `indeed_jobs_<city>.csv` | Filtered teen-friendly Indeed jobs |
| `indeed_jobs_<city>_excluded.csv` | Filtered-out jobs and reasons |
| `jobs_ranked.json` | Final ranked list from both sources |

All output files are gitignored.

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
│       └── job-pipeline/
│           └── SKILL.md            # Claude Code skill (auto-loaded)
├── indeed-job-scrape/
│   ├── SKILL.md                    # Legacy Claude browser skill
│   ├── references/
│   │   └── indeed-reference.md    # Indeed URL params + JSON schema docs
│   └── scripts/
│       └── build_csv.py           # Filter scraped JSON → clean CSV
├── Makefile                        # install / run / clean targets
├── requirements.txt                # Python dependencies
├── run_job_pipeline.py             # Main orchestrator
├── scrape_indeed.py                # Headless Indeed scraper (Playwright)
├── rank_jobs.py                    # Scoring + ranking engine
└── usajobs_summer_scraper.py       # USAJOBS API client
```

## Troubleshooting

**`playwright: command not found`** — use `python3 -m playwright install chromium`
instead, or just run `make install`.

**Indeed returns 0 jobs** — try a wider radius (`make run-wide`) or check if
Indeed is showing a bot challenge. Debug standalone:
`python3 scrape_indeed.py --location "City, ST" --pages 1 --queries cashier`

**USAJOBS SSL warning** (`NotOpenSSLWarning`) — harmless on macOS with system
Python; results still fetch correctly.

**`TypeError: unsupported operand type(s) for |`** — Python 3.9 compatibility
issue. Scripts are patched; if it reappears in `build_csv.py`, replace any
`X | None` type hints with `Optional[X]` and add `from typing import Optional`.
