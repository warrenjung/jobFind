# USAJOBS Summer Job Finder

Small Python scraper for finding student-friendly summer job listings from the
USAJOBS API.

## What It Does

- Queries the USAJOBS Search API
- Supports location searches with `--city`
- Supports optional keyword searches with `--keyword`
- Extracts useful job fields into clean dictionaries
- Saves results to `jobs_raw.json`
- Prints a preview table of the first few jobs

## Run It

```bash
python3 usajobs_summer_scraper.py
```

Try a different city:

```bash
python3 usajobs_summer_scraper.py --city "Los Angeles, California"
```

Combine city and keyword:

```bash
python3 usajobs_summer_scraper.py --city "San Jose, California" --keyword "student"
```

## One-Command Pipeline

After an Indeed CSV exists for the location, run the full local pipeline with:

```bash
python3 run_job_pipeline.py --location "Cupertino, CA"
```

This refreshes USAJOBS, uses the matching Indeed CSV, and regenerates
`jobs_ranked.json`.

If you already have a Claude/Indeed scraped JSON file, the pipeline can convert
it first:

```bash
python3 run_job_pipeline.py \
  --location "Cupertino, CA" \
  --scraped-json jobs_scraped.json
```

Indeed scraping itself still has to be done through the browser-driven
Claude/Indeed skill because Indeed blocks normal command-line scraping.

## Credentials

The script looks for credentials in this order:

1. Command-line flags: `--api-key` and `--email`
2. Environment variables: `USAJOBS_API_KEY` and `USAJOBS_EMAIL`
3. A local `usajobs_credentials.json` file

`usajobs_credentials.json` is ignored by git so secrets are not uploaded.
