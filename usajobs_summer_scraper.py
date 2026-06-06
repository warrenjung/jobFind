"""
Fetch summer-friendly temporary job listings from the USAJOBS API.

This is the first step in a student summer job finder pipeline. It pulls job
data from USAJOBS, extracts the fields needed by later scoring steps, saves the
cleaned records to JSON, and prints a small console preview.
"""

import argparse
import json
import os
from typing import Any

import requests


BASE_URL = "https://data.usajobs.gov/api/Search"
DEFAULT_KEYWORD = ""
DEFAULT_CITY = "San Francisco, California"
DEFAULT_NUM_RESULTS = 25
DEFAULT_OUTPUT_FILE = "jobs_raw.json"
DEFAULT_CREDENTIALS_FILE = "usajobs_credentials.json"
TEMPORARY_APPOINTMENT_CODE = "15317"
NOT_SPECIFIED = "Not specified"


def value_or_default(value: Any, default: str = NOT_SPECIFIED) -> Any:
    """Return a readable fallback when the API provides a blank/null value."""
    if value is None or value == "":
        return default
    return value


def first_list_item(items: Any) -> dict[str, Any]:
    """Safely return the first dict from a list-like API field."""
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return {}


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
        "api_key": credentials.get("api_key", ""),
        "email": credentials.get("email", ""),
    }


def fetch_jobs(
    api_key: str,
    email: str,
    keyword: str,
    city: str,
    num_results: int,
) -> dict[str, Any]:
    """Call the USAJOBS Search API and return the raw JSON response."""
    headers = {
        "Authorization-Key": api_key,
        "User-Agent": email,
        "Host": "data.usajobs.gov",
    }

    params = {
        "PositionOfferingTypeCode": TEMPORARY_APPOINTMENT_CODE,
        "ResultsPerPage": num_results,
        "Fields": "full",
    }
    if keyword:
        params["Keyword"] = keyword
    if city:
        params["LocationName"] = city

    try:
        response = requests.get(
            BASE_URL,
            headers=headers,
            params=params,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not connect to the USAJOBS API: {exc}") from exc

    if response.status_code != 200:
        message = response.text.strip()[:500] or "No response body returned."
        raise RuntimeError(
            f"USAJOBS API request failed with status {response.status_code}: {message}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("USAJOBS API returned a non-JSON response.") from exc


def parse_jobs(raw_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the fields needed by the summer job finder pipeline."""
    search_result = raw_data.get("SearchResult", {}) or {}
    items = search_result.get("SearchResultItems", []) or []

    jobs = []
    for item in items:
        descriptor = item.get("MatchedObjectDescriptor", {}) or {}
        location = first_list_item(descriptor.get("PositionLocation"))
        remuneration = first_list_item(descriptor.get("PositionRemuneration"))
        schedule = first_list_item(descriptor.get("PositionSchedule"))
        offering_type = first_list_item(descriptor.get("PositionOfferingType"))

        jobs.append(
            {
                "job_id": value_or_default(item.get("MatchedObjectId")),
                "title": value_or_default(descriptor.get("PositionTitle")),
                "organization": value_or_default(descriptor.get("OrganizationName")),
                "department": value_or_default(descriptor.get("DepartmentName")),
                "location_display": value_or_default(
                    descriptor.get("PositionLocationDisplay")
                ),
                "city": value_or_default(location.get("CityName")),
                "state": value_or_default(location.get("CountrySubDivisionCode")),
                "latitude": value_or_default(location.get("Latitude")),
                "longitude": value_or_default(location.get("Longitude")),
                "salary_min": value_or_default(remuneration.get("MinimumRange")),
                "salary_max": value_or_default(remuneration.get("MaximumRange")),
                "pay_period": value_or_default(remuneration.get("RateIntervalCode")),
                "schedule": value_or_default(schedule.get("Name")),
                "appointment_type": value_or_default(offering_type.get("Name")),
                "date_posted": value_or_default(
                    descriptor.get("PublicationStartDate")
                ),
                "application_deadline": value_or_default(
                    descriptor.get("ApplicationCloseDate")
                ),
                "qualification_summary": value_or_default(
                    descriptor.get("QualificationSummary")
                ),
                "job_url": value_or_default(descriptor.get("PositionURI")),
            }
        )

    return jobs


def save_jobs(jobs: list[dict[str, Any]], filename: str) -> None:
    """Save extracted job records to a JSON file."""
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(jobs, file, indent=2, ensure_ascii=False)


def preview_jobs(jobs: list[dict[str, Any]], n: int = 5) -> None:
    """Print a compact preview table for the first n jobs."""
    print(f"\nTotal jobs fetched: {len(jobs)}")

    if not jobs:
        print("No jobs found.")
        return

    preview = jobs[:n]
    headers = ["Title", "Location", "Pay", "Deadline"]
    rows = []

    for job in preview:
        pay = format_pay(
            job.get("salary_min"),
            job.get("salary_max"),
            job.get("pay_period"),
        )
        rows.append(
            [
                truncate(str(job.get("title", NOT_SPECIFIED)), 38),
                truncate(str(job.get("location_display", NOT_SPECIFIED)), 28),
                truncate(pay, 24),
                str(job.get("application_deadline", NOT_SPECIFIED)),
            ]
        )

    widths = [
        max(len(str(row[index])) for row in [headers, *rows])
        for index in range(len(headers))
    ]

    print()
    print(format_row(headers, widths))
    print(format_row(["-" * width for width in widths], widths))
    for row in rows:
        print(format_row(row, widths))


def format_pay(salary_min: Any, salary_max: Any, pay_period: Any) -> str:
    """Format salary fields into one preview-table value."""
    if salary_min == NOT_SPECIFIED and salary_max == NOT_SPECIFIED:
        return NOT_SPECIFIED

    if salary_min == salary_max or salary_max == NOT_SPECIFIED:
        pay = str(salary_min)
    elif salary_min == NOT_SPECIFIED:
        pay = str(salary_max)
    else:
        pay = f"{salary_min}-{salary_max}"

    if pay_period != NOT_SPECIFIED:
        pay = f"{pay} / {pay_period}"

    return pay


def truncate(text: str, max_length: int) -> str:
    """Shorten long table cells while keeping the preview readable."""
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3]}..."


def format_row(values: list[str], widths: list[int]) -> str:
    """Format one console preview row."""
    return " | ".join(value.ljust(width) for value, width in zip(values, widths))


def main() -> None:
    """Fetch, parse, save, and preview summer job listings."""
    local_credentials = load_local_credentials()

    parser = argparse.ArgumentParser(
        description="Fetch student-friendly summer jobs from the USAJOBS API."
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("USAJOBS_API_KEY") or local_credentials.get("api_key"),
        help=(
            "USAJOBS API key. Defaults to USAJOBS_API_KEY, then "
            f"{DEFAULT_CREDENTIALS_FILE}."
        ),
    )
    parser.add_argument(
        "--email",
        default=os.getenv("USAJOBS_EMAIL") or local_credentials.get("email"),
        help=(
            "Email for the User-Agent header. Defaults to USAJOBS_EMAIL, then "
            f"{DEFAULT_CREDENTIALS_FILE}."
        ),
    )
    parser.add_argument(
        "--keyword",
        default=DEFAULT_KEYWORD,
        help="Optional search keyword string. Defaults to no keyword filter.",
    )
    parser.add_argument(
        "--city",
        default=DEFAULT_CITY,
        help=f"City/location search string. Defaults to: {DEFAULT_CITY!r}.",
    )
    parser.add_argument(
        "--num-results",
        type=int,
        default=DEFAULT_NUM_RESULTS,
        help=f"Number of results to request. Defaults to {DEFAULT_NUM_RESULTS}.",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_FILE,
        help=f"Output JSON filename. Defaults to {DEFAULT_OUTPUT_FILE}.",
    )
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit(
            "Missing USAJOBS API key. Set USAJOBS_API_KEY or pass --api-key."
        )
    if not args.email:
        raise SystemExit("Missing email. Set USAJOBS_EMAIL or pass --email.")

    raw_data = fetch_jobs(
        args.api_key,
        args.email,
        args.keyword,
        args.city,
        args.num_results,
    )
    jobs = parse_jobs(raw_data)
    save_jobs(jobs, args.output)
    preview_jobs(jobs)
    print(f"\nSaved jobs to {args.output}")


if __name__ == "__main__":
    main()
