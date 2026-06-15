"""
Run the local job-finder pipeline with one command.

This script coordinates the pieces in this project:
1. Refresh USAJOBS results for a location.
2. Scrape Indeed with a headless Playwright browser (scrape_indeed.py).
3. Build an Indeed CSV from the scraped JSON (build_csv.py).
4. Optionally refresh CareerOneStop results for a location.
5. Rank the combined USAJOBS + Indeed + CareerOneStop results into jobs_ranked.json.
6. Export a clean HTML page for easy reading.

Pass --skip-indeed to skip steps 2-3 and use an existing Indeed CSV.
Pass --include-careeronestop to include CareerOneStop once Jobs API access works.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


_HERE = Path(__file__).parent
DATA_DIR = _HERE.parent / "data"

DEFAULT_LOCATION = "Cupertino, CA"
DEFAULT_USAJOBS_FILE = DATA_DIR / "jobs_raw.json"
DEFAULT_RANKED_FILE = DATA_DIR / "jobs_ranked.json"
DEFAULT_CLEAN_FILE = DATA_DIR / "jobs_clean.html"
DEFAULT_CLEAN_MIN_SCORE = 50
DEFAULT_RESULTS = 25
DEFAULT_CAREERONESTOP_RESULTS = 25
DEFAULT_CAREERONESTOP_DAYS = 30
INDEED_HELPER = _HERE / "build_csv.py"
INDEED_SCRAPER = _HERE / "scrape_indeed.py"
CAREERONESTOP_SCRAPER = _HERE / "careeronestop_scraper.py"
CLEAN_TABLE_EXPORTER = _HERE / "export_clean_table.py"


from utils import slugify_location


def city_slug_from_location(location: str) -> str:
    """Return a filename slug for just the city part of a location."""
    return slugify_location(location.split(",", 1)[0])


def run_command(command: list) -> None:
    """Run a child command and stop the pipeline if it fails."""
    command = [str(part) for part in command]
    print(f"\n$ {' '.join(command)}", flush=True)
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"\nCommand failed with exit code {exc.returncode}: {' '.join(command)}"
        ) from exc


def find_scraped_json(
    location_slug: str,
    explicit_path: Optional[str],
) -> Optional[Path]:
    """Find a Claude/Indeed scraped JSON file if one exists."""
    candidates = []
    if explicit_path:
        candidates.append(Path(explicit_path))
    candidates.extend(
        [
            DATA_DIR / f"jobs_scraped_{location_slug}.json",
            DATA_DIR / "jobs_scraped.json",
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def scrape_indeed(
    location: str,
    location_slug: str,
    radius: int,
    pages: int,
    queries: Optional[list[str]],
    fromage: int,
) -> Path:
    """Run the Playwright-based Indeed scraper and return the output JSON path."""
    if not INDEED_SCRAPER.exists():
        raise SystemExit(f"Could not find Indeed scraper at {INDEED_SCRAPER}.")
    output_json = DATA_DIR / f"jobs_scraped_{location_slug}.json"
    command = [
        sys.executable,
        str(INDEED_SCRAPER),
        "--location",
        location,
        "--radius",
        str(radius),
        "--pages",
        str(pages),
        "--fromage",
        str(fromage),
        "--output",
        str(output_json),
    ]
    if queries:
        command.extend(["--queries", *queries])
    run_command(command)
    return output_json


def ensure_indeed_csv(
    location_slug: str,
    city_slug: str,
    indeed_file: Optional[str],
    scraped_json: Optional[str],
) -> Path:
    """Return an Indeed CSV, building it from scraped JSON."""
    output_csv = Path(indeed_file) if indeed_file else DATA_DIR / f"indeed_jobs_{city_slug}.csv"
    scraped_path = find_scraped_json(location_slug, scraped_json)

    if not scraped_path:
        raise SystemExit(
            f"\nNo scraped JSON found (expected jobs_scraped_{location_slug}.json). "
            "This should not happen if --skip-indeed was not passed."
        )

    if not INDEED_HELPER.exists():
        raise SystemExit(f"Could not find Indeed CSV helper at {INDEED_HELPER}.")

    run_command(
        [
            sys.executable,
            str(INDEED_HELPER),
            str(scraped_path),
            "--output",
            str(output_csv),
        ]
    )
    return output_csv


def find_existing_indeed_csv(
    location_slug: str,
    city_slug: str,
    indeed_file: Optional[str],
) -> Path:
    """Return a pre-existing Indeed CSV when --skip-indeed is used."""
    output_csv = Path(indeed_file) if indeed_file else DATA_DIR / f"indeed_jobs_{city_slug}.csv"
    if output_csv.exists():
        print(f"\nUsing existing Indeed CSV: {output_csv}")
        return output_csv
    alternate_csv = DATA_DIR / f"indeed_jobs_{location_slug}.csv"
    if not indeed_file and alternate_csv.exists():
        print(f"\nUsing existing Indeed CSV: {alternate_csv}")
        return alternate_csv
    raise SystemExit(
        f"\n--skip-indeed was passed but no Indeed CSV was found.\n"
        f"Expected: {output_csv} or {alternate_csv}"
    )


def fetch_careeronestop_jobs(
    location: str,
    location_slug: str,
    careeronestop_file: Optional[str],
    radius: int,
    results: int,
    days: int,
) -> Path:
    """Run the CareerOneStop API fetcher and return the output JSON path."""
    if not CAREERONESTOP_SCRAPER.exists():
        raise SystemExit(
            f"Could not find CareerOneStop scraper at {CAREERONESTOP_SCRAPER}."
        )
    output_json = (
        Path(careeronestop_file)
        if careeronestop_file
        else DATA_DIR / f"jobs_careeronestop_{location_slug}.json"
    )
    run_command(
        [
            sys.executable,
            str(CAREERONESTOP_SCRAPER),
            "--location",
            location,
            "--radius",
            str(radius),
            "--results",
            str(results),
            "--days",
            str(days),
            "--output",
            str(output_json),
        ]
    )
    return output_json


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh USAJOBS, prepare Indeed data, and rank jobs."
    )
    parser.add_argument(
        "--location",
        default=DEFAULT_LOCATION,
        help=f"City/state or ZIP for the search. Default: {DEFAULT_LOCATION}.",
    )
    parser.add_argument(
        "--usajobs-file",
        default=DEFAULT_USAJOBS_FILE,
        help=f"USAJOBS output file. Default: {DEFAULT_USAJOBS_FILE}.",
    )
    parser.add_argument(
        "--indeed-file",
        default=None,
        help="Indeed CSV file. Default: indeed_jobs_<location>.csv.",
    )
    parser.add_argument(
        "--scraped-json",
        default=None,
        help="Optional Indeed scraped JSON file to convert to CSV.",
    )
    parser.add_argument(
        "--ranked-output",
        default=DEFAULT_RANKED_FILE,
        help=f"Ranked output JSON file. Default: {DEFAULT_RANKED_FILE}.",
    )
    parser.add_argument(
        "--careeronestop-file",
        default=None,
        help="CareerOneStop JSON file. Default: jobs_careeronestop_<location>.json.",
    )
    parser.add_argument(
        "--clean-output",
        default=DEFAULT_CLEAN_FILE,
        help=f"Clean HTML output file. Default: {DEFAULT_CLEAN_FILE}.",
    )
    parser.add_argument(
        "--clean-limit",
        type=int,
        default=None,
        help="Optional number of top jobs to include in the clean output.",
    )
    parser.add_argument(
        "--clean-min-score",
        type=float,
        default=DEFAULT_CLEAN_MIN_SCORE,
        help=(
            "Minimum score to include in the clean output. "
            f"Default: {DEFAULT_CLEAN_MIN_SCORE}. Use 0 to show all jobs."
        ),
    )
    parser.add_argument(
        "--personal-keywords",
        default="",
        help="Comma-separated preferred job terms to boost during ranking.",
    )
    parser.add_argument(
        "--skip-clean-table",
        action="store_true",
        help="Skip generating the clean readable output.",
    )
    parser.add_argument(
        "--num-usajobs-results",
        type=int,
        default=DEFAULT_RESULTS,
        help=f"Number of USAJOBS results to fetch. Default: {DEFAULT_RESULTS}.",
    )
    parser.add_argument(
        "--include-usajobs",
        action="store_true",
        help="Include USAJOBS results. Off by default — USAJOBS returns federal "
        "career roles, not local teen jobs.",
    )
    parser.add_argument(
        "--skip-indeed",
        action="store_true",
        help="Skip Indeed scraping and use an existing Indeed CSV.",
    )
    parser.add_argument(
        "--skip-careeronestop",
        action="store_true",
        help="Skip CareerOneStop API fetching and ranking.",
    )
    parser.add_argument(
        "--include-careeronestop",
        action="store_true",
        help="Include CareerOneStop API results. Off by default until Jobs API access is approved.",
    )
    parser.add_argument(
        "--indeed-radius",
        type=int,
        default=10,
        choices=[0, 5, 10, 15, 25, 35, 50],
        help="Search radius in miles for Indeed. Default: 10.",
    )
    parser.add_argument(
        "--indeed-pages",
        type=int,
        default=3,
        help="Pages per Indeed search query (10 results each). Default: 3.",
    )
    parser.add_argument(
        "--indeed-queries",
        nargs="+",
        default=None,
        help="Optional Indeed search queries. Default: scraper's built-in list.",
    )
    parser.add_argument(
        "--indeed-fromage",
        type=int,
        default=14,
        help="Only include Indeed postings from the last N days. Default: 14. Use 0 for no limit.",
    )
    parser.add_argument(
        "--careeronestop-radius",
        type=int,
        default=None,
        help="Search radius in miles for CareerOneStop. Default: Indeed radius.",
    )
    parser.add_argument(
        "--careeronestop-results",
        type=int,
        default=DEFAULT_CAREERONESTOP_RESULTS,
        help=(
            "CareerOneStop results per keyword. "
            f"Default: {DEFAULT_CAREERONESTOP_RESULTS}."
        ),
    )
    parser.add_argument(
        "--careeronestop-days",
        type=int,
        default=DEFAULT_CAREERONESTOP_DAYS,
        help=(
            "Only include CareerOneStop jobs from the last N days. "
            f"Default: {DEFAULT_CAREERONESTOP_DAYS}. Use 0 for all jobs."
        ),
    )
    parser.add_argument(
        "--preview-count",
        type=int,
        default=10,
        help="Number of ranked jobs to preview in the terminal. Default: 10.",
    )
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    location_slug = slugify_location(args.location)
    city_slug = city_slug_from_location(args.location)

    if args.include_usajobs:
        run_command(
            [
                sys.executable,
                str(_HERE / "usajobs_summer_scraper.py"),
                "--city",
                args.location,
                "--num-results",
                str(args.num_usajobs_results),
                "--output",
                args.usajobs_file,
            ]
        )
    else:
        print("\nSkipping USAJOBS by default (federal career roles). Use --include-usajobs to add them.")

    if args.skip_indeed:
        indeed_csv = find_existing_indeed_csv(location_slug, city_slug, args.indeed_file)
    else:
        scraped_json = scrape_indeed(
            args.location,
            location_slug,
            args.indeed_radius,
            args.indeed_pages,
            args.indeed_queries,
            args.indeed_fromage,
        )
        indeed_csv = ensure_indeed_csv(
            location_slug,
            city_slug,
            args.indeed_file,
            str(scraped_json),
        )

    careeronestop_json = None
    if args.include_careeronestop and not args.skip_careeronestop:
        careeronestop_radius = (
            args.careeronestop_radius
            if args.careeronestop_radius is not None
            else args.indeed_radius
        )
        careeronestop_json = fetch_careeronestop_jobs(
            args.location,
            location_slug,
            args.careeronestop_file,
            careeronestop_radius,
            args.careeronestop_results,
            args.careeronestop_days,
        )
    elif args.skip_careeronestop:
        print("\nSkipping CareerOneStop (--skip-careeronestop).")
    else:
        print("\nSkipping CareerOneStop by default. Use --include-careeronestop after NLx Jobs API access is approved.")

    rank_command = [
        sys.executable,
        str(_HERE / "rank_jobs.py"),
        "--home-location",
        args.location,
        "--usajobs-file",
        args.usajobs_file if args.include_usajobs else "",
        "--indeed-file",
        str(indeed_csv),
        "--output",
        args.ranked_output,
        "--preview-count",
        str(args.preview_count),
    ]
    if args.personal_keywords.strip():
        rank_command.extend(["--personal-keywords", args.personal_keywords])
    if careeronestop_json is not None:
        rank_command.extend(["--careeronestop-file", str(careeronestop_json)])

    run_command(
        rank_command
    )

    if not args.skip_clean_table:
        clean_table_command = [
            sys.executable,
            str(CLEAN_TABLE_EXPORTER),
            "--input",
            args.ranked_output,
            "--output",
            args.clean_output,
            "--min-score",
            str(args.clean_min_score),
            "--location",
            args.location,
            "--fromage",
            str(args.indeed_fromage),
        ]
        if args.clean_limit is not None:
            clean_table_command.extend(["--limit", str(args.clean_limit)])
        run_command(clean_table_command)
        print(f"\nClean table saved to {args.clean_output}")

    print(f"\nDone. Ranked jobs saved to {args.ranked_output}")


if __name__ == "__main__":
    main()
