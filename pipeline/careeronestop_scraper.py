"""
Fetch local job listings from the CareerOneStop Jobs V2 API.

This script adds a structured third source to the student job finder pipeline.
It saves normalized JSON records that rank_jobs.py can merge with USAJOBS and
Indeed results.
"""

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import requests


_REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = _REPO_ROOT / "data"

BASE_URL = "https://api.careeronestop.org"
DEFAULT_CREDENTIALS_FILE = str(_REPO_ROOT / "careeronestop_credentials.json")
DEFAULT_LOCATION = "Cupertino, CA"
DEFAULT_RADIUS = 10
DEFAULT_RESULTS = 25
DEFAULT_DAYS = 30
DEFAULT_OUTPUT_FILE = str(DATA_DIR / "jobs_careeronestop.json")
from utils import NOT_SPECIFIED, clean_text, parse_float, slugify_location

SEARCH_KEYWORDS = [
    "cashier",
    "retail",
    "food service",
    "barista",
    "restaurant",
    "seasonal",
    "summer",
    "student",
    "camp counselor",
    "lifeguard",
    "crew",
]


def load_local_credentials(filename: str = DEFAULT_CREDENTIALS_FILE) -> dict[str, str]:
    """Load local credentials when environment variables are not set."""
    if not os.path.exists(filename):
        return {}

    try:
        with open(filename, "r", encoding="utf-8") as file:
            credentials = json.load(file)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Could not read {filename}: {exc}") from exc

    return {
        "user_id": credentials.get("user_id", ""),
        "api_token": credentials.get("api_token", ""),
    }


def make_jobs_url(
    user_id: str,
    keyword: str,
    location: str,
    radius: int,
    start_record: int,
    page_size: int,
    days: int,
) -> str:
    """Build a CareerOneStop Jobs V2 search URL."""
    path_parts = [
        "v2",
        "jobsearch",
        user_id,
        keyword,
        location,
        str(radius),
        "0",
        "0",
        str(start_record),
        str(page_size),
        str(days),
    ]
    path = "/".join(quote(part, safe="") for part in path_parts)
    return f"{BASE_URL}/{path}"


def fetch_jobs_for_keyword(
    user_id: str,
    api_token: str,
    keyword: str,
    location: str,
    radius: int,
    page_size: int,
    days: int,
) -> dict[str, Any]:
    """Call the CareerOneStop Jobs V2 API for one keyword."""
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
    }
    params = {
        "showFilters": "false",
        "enableJobDescriptionSnippet": "true",
        "enableMetaData": "false",
    }
    url = make_jobs_url(user_id, keyword, location, radius, 0, page_size, days)

    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Could not connect to the CareerOneStop API for {keyword!r}: {exc}"
        ) from exc

    if response.status_code != 200:
        message = response.text.strip()[:500] or "No response body returned."
        raise RuntimeError(
            "CareerOneStop API request failed "
            f"for {keyword!r} with status {response.status_code}: {message}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("CareerOneStop API returned a non-JSON response.") from exc

    error = clean_text(data.get("ErrorMessage"))
    if error != NOT_SPECIFIED:
        raise RuntimeError(f"CareerOneStop API error for {keyword!r}: {error}")

    return data


def normalize_job(job: dict[str, Any], keyword: str) -> dict[str, Any]:
    """Convert one CareerOneStop result to the pipeline's raw JSON shape."""
    return {
        "job_id": clean_text(job.get("JvId")),
        "title": clean_text(job.get("JobTitle")),
        "company": clean_text(job.get("Company")),
        "location": clean_text(job.get("Location")),
        "description_snippet": clean_text(job.get("DescriptionSnippet")),
        "distance": parse_float(job.get("Distance")),
        "date_posted": clean_text(job.get("AcquisitionDate")),
        "job_url": clean_text(job.get("URL")),
        "onet_codes": job.get("OnetCodes") if isinstance(job.get("OnetCodes"), list) else [],
        "search_keyword": keyword,
    }


def fetch_jobs(
    user_id: str,
    api_token: str,
    location: str,
    radius: int,
    page_size: int,
    days: int,
    keywords: list[str],
) -> list[dict[str, Any]]:
    """Fetch and de-duplicate CareerOneStop jobs across keywords."""
    jobs = []
    seen = set()

    for keyword in keywords:
        print(f"  CareerOneStop search: {keyword!r} near {location}")
        raw_data = fetch_jobs_for_keyword(
            user_id,
            api_token,
            keyword,
            location,
            radius,
            page_size,
            days,
        )
        keyword_jobs = raw_data.get("Jobs", []) or []
        if not isinstance(keyword_jobs, list):
            keyword_jobs = []

        added = 0
        for raw_job in keyword_jobs:
            if not isinstance(raw_job, dict):
                continue
            job = normalize_job(raw_job, keyword)
            unique_key = job["job_id"]
            if unique_key == NOT_SPECIFIED:
                unique_key = "|".join(
                    job[field].lower()
                    for field in ("title", "company", "location")
                )
            if unique_key in seen:
                continue
            seen.add(unique_key)
            jobs.append(job)
            added += 1

        print(f"    {len(keyword_jobs)} returned, {added} new ({len(jobs)} total)")

    return jobs


def save_jobs(jobs: list[dict[str, Any]], filename: str) -> None:
    """Save normalized CareerOneStop jobs to JSON."""
    output = Path(filename)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")


def preview_jobs(jobs: list[dict[str, Any]], count: int = 5) -> None:
    """Print a small preview of fetched jobs."""
    print(f"\nCareerOneStop jobs fetched: {len(jobs)}")
    for job in jobs[:count]:
        distance = job.get("distance")
        miles = f"{distance:.1f} mi" if distance is not None else "N/A"
        print(
            f"- {job['title']} | {job['company']} | "
            f"{job['location']} | {miles}"
        )


def main() -> None:
    """Fetch, normalize, save, and preview CareerOneStop jobs."""
    local_credentials = load_local_credentials()

    parser = argparse.ArgumentParser(
        description="Fetch local job listings from the CareerOneStop Jobs V2 API."
    )
    parser.add_argument(
        "--user-id",
        default=os.getenv("CAREERONESTOP_USER_ID") or local_credentials.get("user_id"),
        help="CareerOneStop API user ID. Defaults to env/local credentials file.",
    )
    parser.add_argument(
        "--api-token",
        default=(
            os.getenv("CAREERONESTOP_API_TOKEN")
            or local_credentials.get("api_token")
        ),
        help="CareerOneStop API token. Defaults to env/local credentials file.",
    )
    parser.add_argument(
        "--location",
        default=DEFAULT_LOCATION,
        help=f"City/state or ZIP for the search. Default: {DEFAULT_LOCATION}.",
    )
    parser.add_argument(
        "--radius",
        type=int,
        default=DEFAULT_RADIUS,
        help=f"Search radius in miles. Default: {DEFAULT_RADIUS}.",
    )
    parser.add_argument(
        "--results",
        type=int,
        default=DEFAULT_RESULTS,
        help=f"Results per keyword. Default: {DEFAULT_RESULTS}.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Only include jobs from the last N days. Default: {DEFAULT_DAYS}.",
    )
    parser.add_argument(
        "--keywords",
        nargs="+",
        default=None,
        help="Search keywords. Default: built-in student-friendly list.",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_FILE,
        help=f"Output JSON file. Default: {DEFAULT_OUTPUT_FILE}.",
    )
    parser.add_argument(
        "--preview-count",
        type=int,
        default=5,
        help="Number of fetched jobs to preview. Default: 5.",
    )
    args = parser.parse_args()

    if not args.user_id:
        raise SystemExit(
            "Missing CareerOneStop user ID. Set CAREERONESTOP_USER_ID, "
            "pass --user-id, or add careeronestop_credentials.json."
        )
    if not args.api_token:
        raise SystemExit(
            "Missing CareerOneStop API token. Set CAREERONESTOP_API_TOKEN, "
            "pass --api-token, or add careeronestop_credentials.json."
        )
    if args.results < 1:
        raise SystemExit("--results must be greater than 0.")
    if args.days < 0:
        raise SystemExit("--days must be 0 or greater.")

    keywords = args.keywords or SEARCH_KEYWORDS
    output = args.output
    if output == DEFAULT_OUTPUT_FILE:
        output = str(DATA_DIR / f"jobs_careeronestop_{slugify_location(args.location)}.json")

    try:
        jobs = fetch_jobs(
            args.user_id,
            args.api_token,
            args.location,
            args.radius,
            args.results,
            args.days,
            keywords,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    save_jobs(jobs, output)
    preview_jobs(jobs, args.preview_count)
    print(f"\nSaved CareerOneStop jobs to {output}")


if __name__ == "__main__":
    main()
