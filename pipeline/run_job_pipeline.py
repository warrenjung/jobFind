"""
Run the local job-finder pipeline with one command.

This script coordinates the pieces in this project:
1. Refresh USAJOBS results for a location.
2. Scrape Indeed with a headless Playwright browser (scrape_indeed.py).
3. Build an Indeed CSV from the scraped JSON (build_csv.py).
4. Rank the combined USAJOBS + Indeed results into jobs_ranked.json.
5. Export a clean HTML page for easy reading.

Pass --skip-indeed to skip steps 2-3 and use an existing Indeed CSV.
"""

import argparse
import os
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
INDEED_HELPER = _HERE / "build_csv.py"
INDEED_SCRAPER = _HERE / "scrape_indeed.py"
CLEAN_TABLE_EXPORTER = _HERE / "export_clean_table.py"


def slugify_location(location: str) -> str:
    """Convert a location into a safe filename slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", location.lower()).strip("_")
    return slug or "location"


def city_slug_from_location(location: str) -> str:
    """Return a filename slug for just the city part of a location."""
    return slugify_location(location.split(",", 1)[0])


def run_command(command: list) -> None:
    """Run a child command and stop the pipeline if it fails."""
    command = [str(part) for part in command]
    print(f"\n$ {' '.join(command)}", flush=True)
    subprocess.run(command, check=True)


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
) -> Path:
    """Run the Playwright-based Indeed scraper and return the output JSON path."""
    if not INDEED_SCRAPER.exists():
        raise SystemExit(f"Could not find Indeed scraper at {INDEED_SCRAPER}.")
    output_json = DATA_DIR / f"jobs_scraped_{location_slug}.json"
    run_command(
        [
            sys.executable,
            str(INDEED_SCRAPER),
            "--location",
            location,
            "--radius",
            str(radius),
            "--pages",
            str(pages),
            "--output",
            str(output_json),
        ]
    )
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
        "--skip-usajobs",
        action="store_true",
        help="Use the existing USAJOBS JSON instead of refreshing it.",
    )
    parser.add_argument(
        "--skip-indeed",
        action="store_true",
        help="Skip Indeed scraping and use an existing Indeed CSV.",
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
        "--preview-count",
        type=int,
        default=10,
        help="Number of ranked jobs to preview in the terminal. Default: 10.",
    )
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    location_slug = slugify_location(args.location)
    city_slug = city_slug_from_location(args.location)

    if not args.skip_usajobs:
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
    elif not os.path.exists(args.usajobs_file):
        raise SystemExit(f"USAJOBS file does not exist: {args.usajobs_file}")

    if args.skip_indeed:
        indeed_csv = find_existing_indeed_csv(location_slug, city_slug, args.indeed_file)
    else:
        scraped_json = scrape_indeed(args.location, location_slug, args.indeed_radius, args.indeed_pages)
        indeed_csv = ensure_indeed_csv(
            location_slug,
            city_slug,
            args.indeed_file,
            str(scraped_json),
        )

    run_command(
        [
            sys.executable,
            str(_HERE / "rank_jobs.py"),
            "--home-location",
            args.location,
            "--usajobs-file",
            args.usajobs_file,
            "--indeed-file",
            str(indeed_csv),
            "--output",
            args.ranked_output,
            "--preview-count",
            str(args.preview_count),
        ]
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
        ]
        if args.clean_limit is not None:
            clean_table_command.extend(["--limit", str(args.clean_limit)])
        run_command(clean_table_command)
        print(f"\nClean table saved to {args.clean_output}")

    print(f"\nDone. Ranked jobs saved to {args.ranked_output}")


if __name__ == "__main__":
    main()
