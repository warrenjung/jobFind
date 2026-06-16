"""
Fetch jobs from public applicant-tracking-system job boards.

Supported sources:
- Greenhouse Job Board API
- Lever public postings API

These sources are opt-in because they require a local list of company board
tokens/sites that the user wants to search.
"""

from __future__ import annotations

import argparse
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

import requests

from utils import NOT_SPECIFIED, clean_text


DATA_DIR = Path(__file__).parent.parent / "data"
DEFAULT_CONFIG_FILE = Path(__file__).parent.parent / "ats_sources.json"
DEFAULT_OUTPUT_FILE = DATA_DIR / "jobs_ats.json"
REQUEST_TIMEOUT_SECONDS = 20


class TextExtractor(HTMLParser):
    """Small HTML-to-text helper for job descriptions."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return clean_text(" ".join(self.parts))


def strip_html(value: Any) -> str:
    """Convert an HTML description to readable one-line text."""
    text = clean_text(value)
    if text == NOT_SPECIFIED:
        return NOT_SPECIFIED
    parser = TextExtractor()
    parser.feed(text)
    return parser.text()


def load_sources(filename: Path) -> list[dict[str, Any]]:
    """Load the local ATS source config."""
    if not filename.exists():
        raise SystemExit(
            f"No ATS source config found at {filename}. "
            "Copy ats_sources.example.json to ats_sources.json and add boards first."
        )

    with filename.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, dict):
        sources = data.get("sources", [])
    else:
        sources = data

    if not isinstance(sources, list):
        raise SystemExit(f"{filename} must contain a list or a 'sources' list.")

    return [source for source in sources if isinstance(source, dict)]


def source_company(source: dict[str, Any], fallback: str) -> str:
    """Return a readable company name for a configured source."""
    return clean_text(source.get("company") or source.get("name") or fallback)


def fetch_greenhouse_jobs(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch and normalize one Greenhouse board."""
    board_token = clean_text(source.get("board_token") or source.get("token"))
    if board_token == NOT_SPECIFIED:
        raise RuntimeError("Greenhouse source is missing board_token.")

    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
    response = requests.get(
        url,
        params={"content": "true"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    rows = data.get("jobs") if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return []

    company = source_company(source, board_token)
    return [
        normalize_greenhouse_job(row, board_token=board_token, company=company)
        for row in rows
        if isinstance(row, dict)
    ]


def fetch_lever_jobs(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch and normalize one Lever postings site."""
    site = clean_text(source.get("site") or source.get("account"))
    if site == NOT_SPECIFIED:
        raise RuntimeError("Lever source is missing site.")

    url = f"https://api.lever.co/v0/postings/{site}"
    response = requests.get(
        url,
        params={"mode": "json"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        return []

    company = source_company(source, site)
    return [
        normalize_lever_job(row, site=site, company=company)
        for row in rows
        if isinstance(row, dict)
    ]


def normalize_greenhouse_job(
    row: dict[str, Any],
    board_token: str,
    company: str,
) -> dict[str, Any]:
    """Convert one Greenhouse row to the shared ATS job shape."""
    location = greenhouse_location(row)
    return {
        "source": "ats_greenhouse",
        "source_id": f"greenhouse:{board_token}:{clean_text(row.get('id'))}",
        "title": clean_text(row.get("title")),
        "company": company,
        "department": greenhouse_department(row),
        "location": location,
        "city": extract_city(location),
        "state": extract_state(location),
        "pay": NOT_SPECIFIED,
        "hourly_pay_estimate": None,
        "job_type": NOT_SPECIFIED,
        "schedule": NOT_SPECIFIED,
        "date_posted": clean_text(row.get("updated_at")),
        "deadline": NOT_SPECIFIED,
        "teen_fit_reason": "Company career page listing",
        "description": strip_html(row.get("content")),
        "url": clean_text(row.get("absolute_url")),
        "ats_provider": "greenhouse",
        "ats_board": board_token,
    }


def normalize_lever_job(
    row: dict[str, Any],
    site: str,
    company: str,
) -> dict[str, Any]:
    """Convert one Lever row to the shared ATS job shape."""
    categories = row.get("categories") if isinstance(row.get("categories"), dict) else {}
    location = lever_location(categories)
    pay = lever_pay(row)
    return {
        "source": "ats_lever",
        "source_id": f"lever:{site}:{clean_text(row.get('id'))}",
        "title": clean_text(row.get("text")),
        "company": company,
        "department": clean_text(categories.get("team")),
        "location": location,
        "city": extract_city(location),
        "state": extract_state(location),
        "pay": pay,
        "hourly_pay_estimate": None,
        "job_type": clean_text(categories.get("commitment")),
        "schedule": NOT_SPECIFIED,
        "date_posted": clean_text(row.get("createdAt")),
        "deadline": NOT_SPECIFIED,
        "teen_fit_reason": "Company career page listing",
        "description": clean_text(row.get("descriptionPlain") or row.get("openingPlain")),
        "url": clean_text(row.get("hostedUrl") or row.get("applyUrl")),
        "ats_provider": "lever",
        "ats_board": site,
    }


def greenhouse_location(row: dict[str, Any]) -> str:
    """Return the best location text from a Greenhouse job."""
    location = row.get("location")
    if isinstance(location, dict):
        name = clean_text(location.get("name"))
        if name != NOT_SPECIFIED:
            return name

    offices = row.get("offices")
    if isinstance(offices, list):
        names = [
            clean_text(office.get("name"))
            for office in offices
            if isinstance(office, dict) and clean_text(office.get("name")) != NOT_SPECIFIED
        ]
        if names:
            return ", ".join(dict.fromkeys(names))

    return NOT_SPECIFIED


def greenhouse_department(row: dict[str, Any]) -> str:
    """Return a readable Greenhouse department when present."""
    departments = row.get("departments")
    if isinstance(departments, list):
        names = [
            clean_text(department.get("name"))
            for department in departments
            if isinstance(department, dict)
            and clean_text(department.get("name")) != NOT_SPECIFIED
        ]
        if names:
            return ", ".join(dict.fromkeys(names))
    return NOT_SPECIFIED


def lever_location(categories: dict[str, Any]) -> str:
    """Return location text from Lever categories."""
    location = clean_text(categories.get("location"))
    if location != NOT_SPECIFIED:
        return location

    locations = categories.get("allLocations")
    if isinstance(locations, list):
        names = [clean_text(item) for item in locations if clean_text(item) != NOT_SPECIFIED]
        if names:
            return ", ".join(dict.fromkeys(names))

    return NOT_SPECIFIED


def lever_pay(row: dict[str, Any]) -> str:
    """Return a readable Lever salary/pay string when the API includes one."""
    salary = row.get("salaryRange")
    if not isinstance(salary, dict):
        return NOT_SPECIFIED

    description = clean_text(salary.get("description"))
    if description != NOT_SPECIFIED:
        return description

    minimum = salary.get("min")
    maximum = salary.get("max")
    currency = clean_text(salary.get("currency") or "$")
    interval = clean_text(salary.get("interval"))
    if minimum is None and maximum is None:
        return NOT_SPECIFIED
    if minimum is not None and maximum is not None:
        pay = f"{currency}{minimum}-{maximum}"
    else:
        pay = f"{currency}{minimum if minimum is not None else maximum}"
    if interval != NOT_SPECIFIED:
        return f"{pay} / {interval}"
    return pay


def extract_city(location: Any) -> str:
    """Extract a simple city from a location string."""
    text = clean_text(location)
    if text == NOT_SPECIFIED:
        return NOT_SPECIFIED
    first = text.split(",", 1)[0].strip()
    return first or NOT_SPECIFIED


def extract_state(location: Any) -> str:
    """Extract a two-letter state code when present."""
    text = clean_text(location)
    match = re.search(r",\s*([A-Z]{2})\b", text)
    return match.group(1) if match else NOT_SPECIFIED


def location_terms(location: str) -> list[str]:
    """Build location match terms without broad state-only matches.

    A query like ``Cupertino, CA`` should not keep every California job. Prefer
    the city phrase and ZIP codes; fall back to meaningful words only when the
    user did not provide a comma-separated city/state.
    """
    text = clean_text(location).lower()
    if text == NOT_SPECIFIED.lower():
        return []

    zip_codes = re.findall(r"\b\d{5}\b", text)
    if "," in text:
        city = re.sub(r"\b\d{5}(?:-\d{4})?\b", "", text.split(",", 1)[0]).strip()
        city = re.sub(r"\s+", " ", city)
        return [*zip_codes, city] if city else zip_codes

    words = [
        part
        for part in re.split(r"\s+", re.sub(r"[^a-z0-9\s]", " ", text))
        if len(part) >= 3 and not part.isdigit()
    ]
    return [*zip_codes, *words]


def matches_location(job: dict[str, Any], location: str, include_remote: bool = True) -> bool:
    """Return whether a job loosely matches the requested location."""
    if not location:
        return True

    text = f"{job.get('location', '')} {job.get('city', '')} {job.get('state', '')}".lower()
    if include_remote and "remote" in text:
        return True

    terms = location_terms(location)
    return any(term in text for term in terms)


def fetch_sources(sources: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch all configured sources, returning jobs and non-fatal errors."""
    jobs: list[dict[str, Any]] = []
    errors: list[str] = []

    for source in sources:
        provider = clean_text(source.get("provider")).lower()
        try:
            if provider == "greenhouse":
                rows = fetch_greenhouse_jobs(source)
            elif provider == "lever":
                rows = fetch_lever_jobs(source)
            else:
                errors.append(f"Unsupported ATS provider: {provider}")
                continue
        except requests.RequestException as exc:
            errors.append(f"{provider} fetch failed: {exc}")
            continue
        except RuntimeError as exc:
            errors.append(str(exc))
            continue

        jobs.extend(rows)

    return jobs, errors


def save_jobs(jobs: list[dict[str, Any]], filename: Path) -> None:
    """Save normalized ATS jobs as JSON."""
    filename.parent.mkdir(parents=True, exist_ok=True)
    filename.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch optional Greenhouse/Lever company career page jobs."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_FILE,
        help=f"ATS source config file. Default: {DEFAULT_CONFIG_FILE}.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=f"Output JSON file. Default: {DEFAULT_OUTPUT_FILE}.",
    )
    parser.add_argument(
        "--location",
        default="",
        help="Optional city/state used for loose location filtering.",
    )
    parser.add_argument(
        "--include-remote",
        action="store_true",
        help="Keep remote jobs even when a location is provided.",
    )
    args = parser.parse_args()

    sources = load_sources(args.config)
    jobs, errors = fetch_sources(sources)
    if args.location:
        jobs = [
            job for job in jobs
            if matches_location(job, args.location, include_remote=args.include_remote)
        ]

    save_jobs(jobs, args.output)
    print(f"Saved {len(jobs)} ATS jobs to {args.output}")
    for error in errors:
        print(f"Warning: {error}")


if __name__ == "__main__":
    main()
