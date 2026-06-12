"""
Filter, dedupe, and write scraped Indeed jobs to a CSV.

Input: a JSON file containing a list of job records extracted from Indeed
result pages (see references/indeed-reference.md for the expected shape).

Output: a CSV of opportunities that an ~18-year-old with no degree, license, or
certification could realistically be hired for, plus a companion CSV of the
postings that were excluded (so nothing is silently dropped).

Pure standard library — no installation required.

Usage:
    python3 build_csv.py jobs_scraped.json --output indeed_jobs_cupertino.csv
"""

import argparse
import csv
import json
import os
import re
import sys
from typing import Any, Optional

from utils import NOT_SPECIFIED

COLUMNS = [
    "title",
    "company",
    "location",
    "pay",
    "job_type",
    "schedule_hours",
    "date_posted",
    "teen_fit_reason",
    "description_snippet",
    "job_url",
]

# Phrases that signal a posting needs a credential, license, degree, or amount
# of prior experience that a high-school senior with no certifications would not
# have. If any of these appear in the title or description, the job is excluded.
# Word boundaries are used so e.g. "RN" doesn't match "learn".
EXCLUSION_PATTERNS = [
    # Degrees
    r"bachelor'?s?\b",
    r"master'?s?\b",
    r"\bb\.?s\.?\b degree",
    r"\bdegree (?:required|preferred|in)\b",
    r"college degree",
    r"\bphd\b",
    # Licenses / certifications (healthcare, trades, finance, etc.)
    r"\brn\b",
    r"\blpn\b",
    r"\bcna\b(?!\w)",
    r"\bcdl\b",
    r"\bemt\b",
    r"\bcpa\b",
    r"\bhvac\b",
    r"\bcosmetolog",
    r"\bnotary\b",
    r"\bjourneyman\b",
    r"licensed\b",
    r"\blicense required\b",
    r"valid .* license",          # e.g. "valid nursing license" (driver's license handled below)
    r"certified\b",
    r"certification required",
    r"must be certified",
    r"security clearance",
    r"\bclearance required\b",
    # Substantial prior experience
    r"\b[3-9]\+?\s*years",
    r"\b[1-9][0-9]\+?\s*years",
    r"minimum of [3-9] years",
    r"experienced\b.*required",
]

# "valid driver's license" is common for delivery/valet roles and is NOT a
# barrier for an 18-year-old, so don't let the generic "license" rule kill it.
DRIVERS_LICENSE_RE = re.compile(r"driver'?s? license", re.IGNORECASE)

EXCLUSION_RES = [re.compile(p, re.IGNORECASE) for p in EXCLUSION_PATTERNS]

# Seniority / management signals in the TITLE. Indeed's result cards often carry
# no description text, so the title is usually all we can judge — and a teen's
# "typical summer job" is not a manager, director, or head chef. These roles
# almost always require years of prior experience even when no certificate is
# named, so screening them out keeps the list realistic.
SENIORITY_RE = re.compile(
    r"\b(senior|sr\.?|lead\s+(?:manager|supervisor)|principal|director|"
    r"manager|mgr|supervisor|head\s+chef|chef\s+de\s+cuisine|sous\s+chef|"
    r"executive\s+chef|general\s+manager|store\s+manager|"
    r"assistant\s+(?:store\s+)?manager)\b",
    re.IGNORECASE,
)

# A posting quoted as an annual salary at or above this threshold is a career
# role, not an hourly summer job. Hourly teen jobs are advertised per hour; a
# "$90,000 a year" listing is a tell that the role is out of scope.
ANNUAL_SALARY_CEILING = 50000
ANNUAL_PAY_RE = re.compile(r"\$\s*([\d,]+)(?:\s*-\s*\$?\s*([\d,]+))?\s*a\s*year", re.IGNORECASE)


def annual_salary_too_high(pay: str) -> Optional[int]:
    """If the pay is an annual figure at/above the ceiling, return that figure."""
    m = ANNUAL_PAY_RE.search(pay or "")
    if not m:
        return None
    low = int(m.group(1).replace(",", ""))
    if low >= ANNUAL_SALARY_CEILING:
        return low
    return None

# Positive signals used to explain the fit in the teen_fit_reason column.
POSITIVE_SIGNALS = [
    (re.compile(r"no experience", re.IGNORECASE), "No experience required"),
    (re.compile(r"entry[\s-]?level", re.IGNORECASE), "Entry-level role"),
    (re.compile(r"\bpart[\s-]?time\b", re.IGNORECASE), "Part-time friendly"),
    (re.compile(r"\b(seasonal|summer)\b", re.IGNORECASE), "Seasonal/summer"),
    (re.compile(r"will train|training provided|we train", re.IGNORECASE), "Training provided"),
    (re.compile(r"flexible (?:schedule|hours)", re.IGNORECASE), "Flexible hours"),
    (re.compile(r"\b(16|17|18)\+?\b.*(years|older)|must be 18", re.IGNORECASE), "Open to teens"),
]


def clean(value: Any) -> str:
    """Normalize a field to a trimmed string, defaulting to NOT_SPECIFIED."""
    if value is None:
        return NOT_SPECIFIED
    text = str(value).strip()
    return text if text else NOT_SPECIFIED


def dedupe_key(job: dict[str, Any]) -> str:
    """Prefer the job URL; fall back to title+company."""
    url = clean(job.get("job_url"))
    if url != NOT_SPECIFIED:
        return url.lower()
    return f"{clean(job.get('title')).lower()}|{clean(job.get('company')).lower()}"


def excluded_reason(job: dict[str, Any]) -> Optional[str]:
    """Return why a job is excluded, or None if it passes the teen filter."""
    haystack = " ".join(
        clean(job.get(field))
        for field in ("title", "description_snippet")
        if clean(job.get(field)) != NOT_SPECIFIED
    )
    if not haystack:
        return None  # nothing to judge on — keep it rather than guess

    # Strip the benign driver's-license phrase so it can't trip the filter.
    judged = DRIVERS_LICENSE_RE.sub(" ", haystack)

    for pattern in EXCLUSION_RES:
        match = pattern.search(judged)
        if match:
            return f"Requires credential/experience: '{match.group(0).strip()}'"

    # Management / senior titles — judged on the title only, since that's the
    # reliable signal on a results card.
    title = clean(job.get("title"))
    if title != NOT_SPECIFIED:
        sm = SENIORITY_RE.search(title)
        if sm:
            return f"Management/senior role: '{sm.group(0).strip()}'"

    # Career-salaried postings (annual pay at/above the ceiling).
    high = annual_salary_too_high(clean(job.get("pay")))
    if high is not None:
        return f"Career-salary role: ${high:,}+/year"

    return None


def fit_reason(job: dict[str, Any]) -> str:
    """Build a short explanation of why a kept job suits the candidate."""
    haystack = " ".join(
        clean(job.get(field))
        for field in ("title", "description_snippet", "job_type", "schedule_hours")
        if clean(job.get(field)) != NOT_SPECIFIED
    )
    reasons = [label for regex, label in POSITIVE_SIGNALS if regex.search(haystack)]
    if not reasons:
        reasons.append("No degree or certification mentioned")
    # De-duplicate while preserving order, cap at 3 to keep the cell readable.
    seen: list[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.append(reason)
    return "; ".join(seen[:3])


def to_row(job: dict[str, Any]) -> dict[str, str]:
    row = {col: clean(job.get(col)) for col in COLUMNS}
    row["teen_fit_reason"] = fit_reason(job)
    return row


def write_csv(rows: list[dict[str, str]], path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="JSON file with a list of scraped job records.")
    parser.add_argument(
        "--output",
        default="indeed_jobs.csv",
        help="Output CSV path for kept opportunities (default: indeed_jobs.csv).",
    )
    args = parser.parse_args()

    try:
        with open(args.input, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Could not read {args.input}: {exc}")

    if isinstance(data, dict):
        # Tolerate {"jobs": [...]} as well as a bare list.
        data = data.get("jobs", [])
    if not isinstance(data, list):
        raise SystemExit("Input JSON must be a list of job records.")

    kept: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    seen: set[str] = set()

    for job in data:
        if not isinstance(job, dict):
            continue
        key = dedupe_key(job)
        if key in seen:
            continue
        seen.add(key)

        reason = excluded_reason(job)
        if reason is None:
            kept.append(to_row(job))
        else:
            row = {col: clean(job.get(col)) for col in COLUMNS}
            row["teen_fit_reason"] = reason  # reuse column to record exclusion cause
            excluded.append(row)

    write_csv(kept, args.output)
    excluded_path = re.sub(r"\.csv$", "", args.output) + "_excluded.csv"
    if excluded:
        write_csv(excluded, excluded_path)

    print(f"Scraped records:        {len(data)}")
    print(f"After de-duplication:   {len(seen)}")
    print(f"Kept (teen-friendly):   {len(kept)}  -> {args.output}")
    print(f"Excluded (credential):  {len(excluded)}" + (f"  -> {excluded_path}" if excluded else ""))
    if not kept:
        print(
            "\nWarning: nothing was kept. The cards may be missing description "
            "text, or the filter is too aggressive — re-check a few extractions.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
