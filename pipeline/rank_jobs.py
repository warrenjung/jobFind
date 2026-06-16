"""
Rate and sort jobs from USAJOBS, Indeed, CareerOneStop, and optional ATS
career-page sources for high-school summer job fit.

Inputs:
- jobs_raw.json
- indeed_jobs_cupertino.csv
- jobs_careeronestop_<location>.json
- jobs_ats.json

Output:
- jobs_ranked.json

The scorer is intentionally rule-based and explainable. Later, an LLM score can
be added as another scoring signal without replacing these transparent rules.
"""

import argparse
import csv
import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


DATA_DIR = Path(__file__).parent.parent / "data"
DEFAULT_USAJOBS_FILE = str(DATA_DIR / "jobs_raw.json")
DEFAULT_INDEED_FILE = str(DATA_DIR / "indeed_jobs_cupertino.csv")
DEFAULT_CAREERONESTOP_FILE = ""
DEFAULT_ATS_FILE = ""
DEFAULT_OUTPUT_FILE = str(DATA_DIR / "jobs_ranked.json")
DEFAULT_HOME_LOCATION = "Cupertino, CA"
PERSONAL_KEYWORD_POINTS = 8
PERSONAL_KEYWORD_CAP = 25
AVOID_KEYWORD_POINTS = -10
AVOID_KEYWORD_CAP = -35
from utils import NOT_SPECIFIED, format_row, parse_float


GOOD_KEYWORDS = {
    "student": 16,
    "teen": 14,
    "seasonal": 16,
    "summer": 16,
    "intern": 10,
    "trainee": 10,
    "cashier": 16,
    "barista": 16,
    "retail": 14,
    "sales associate": 14,
    "store associate": 14,
    "grocery": 14,
    "crew": 14,
    "host": 12,
    "server": 12,
    "recreation assistant": 12,
    "lifeguard": 16,
    "tutor": 10,
    "entry level": 10,
    "no experience": 12,
    "no degree": 12,
    "no certification": 10,
}

BAD_KEYWORDS = {
    "physician": -42,
    "attorney": -42,
    "lawyer": -40,
    "engineer": -30,
    "manager": -25,
    "supervisor": -22,
    "supervisory": -25,
    "lead": -14,
    "shift lead": -16,
    "keyholder": -16,
    "chef": -18,
    "cook": -8,
    "bar trivia": -25,
    "bartender": -25,
    "night shift": -14,
    "high-end": -14,
    "luxury": -12,
    "office assistant": -8,
    "front office": -8,
    "office admin": -12,
    "receptionist": -8,
    "administrator": -18,
    "analyst": -18,
    "specialist": -14,
    "officer": -14,
    "examiner": -12,
    "technician": -12,
    "clinical": -18,
    "medical": -12,
    "security clearance": -25,
    "bachelor": -22,
    "master": -25,
    "doctor": -30,
    "license required": -20,
    "certification required": -20,
    "years of experience": -22,
}

NEARBY_CITIES = {
    "cupertino": (37.322998, -122.032181),
    "sunnyvale": (37.368832, -122.036346),
    "santa clara": (37.354107, -121.955238),
    "san jose": (37.338207, -121.886330),
    "campbell": (37.287167, -121.949959),
    "mountain view": (37.386051, -122.083855),
    "los gatos": (37.235809, -121.962379),
    "palo alto": (37.441883, -122.143021),
    "milpitas": (37.432335, -121.899574),
    "saratoga": (37.263832, -122.023018),
    "los altos": (37.385218, -122.114130),
    "redwood city": (37.485215, -122.236355),
    "menlo park": (37.452960, -122.181725),
    "fremont": (37.548271, -121.988571),
}

ZIP_COORDS = {
    "95014": NEARBY_CITIES["cupertino"],
    "94085": NEARBY_CITIES["sunnyvale"],
    "95050": NEARBY_CITIES["santa clara"],
    "95051": NEARBY_CITIES["santa clara"],
    "95110": NEARBY_CITIES["san jose"],
    "95112": NEARBY_CITIES["san jose"],
    "95113": NEARBY_CITIES["san jose"],
    "95122": NEARBY_CITIES["san jose"],
    "95125": NEARBY_CITIES["san jose"],
    "95128": NEARBY_CITIES["san jose"],
    "95131": NEARBY_CITIES["san jose"],
    "95134": NEARBY_CITIES["san jose"],
    "95008": NEARBY_CITIES["campbell"],
    "95011": NEARBY_CITIES["campbell"],
    "94040": NEARBY_CITIES["mountain view"],
    "94041": NEARBY_CITIES["mountain view"],
    "95032": NEARBY_CITIES["los gatos"],
    "94303": NEARBY_CITIES["palo alto"],
    "94306": NEARBY_CITIES["palo alto"],
    "95035": NEARBY_CITIES["milpitas"],
    "95070": NEARBY_CITIES["saratoga"],
    "94022": NEARBY_CITIES["los altos"],
}


def clean_value(value: Any) -> str:
    """Convert missing values to a consistent readable fallback."""
    if value is None:
        return NOT_SPECIFIED
    text = str(value).strip()
    return text if text else NOT_SPECIFIED


def load_usajobs(filename: str) -> list[dict[str, Any]]:
    """Load and normalize USAJOBS records when provided.

    USAJOBS is opt-in, so a missing or empty path is normal — return no jobs
    rather than failing the whole ranking step.
    """
    if not filename or not os.path.exists(filename):
        return []

    with open(filename, "r", encoding="utf-8") as file:
        rows = json.load(file)

    return [normalize_usajobs_job(row) for row in rows]


def load_indeed_jobs(filename: str) -> list[dict[str, Any]]:
    """Load and normalize Indeed CSV records."""
    with open(filename, "r", encoding="utf-8-sig", newline="") as file:
        return [normalize_indeed_job(row) for row in csv.DictReader(file)]


def load_careeronestop_jobs(filename: str) -> list[dict[str, Any]]:
    """Load and normalize CareerOneStop JSON records when provided."""
    if not filename:
        return []

    with open(filename, "r", encoding="utf-8") as file:
        rows = json.load(file)

    if not isinstance(rows, list):
        raise SystemExit(f"{filename} must contain a list of CareerOneStop jobs.")

    return [normalize_careeronestop_job(row) for row in rows if isinstance(row, dict)]


def load_ats_jobs(filename: str) -> list[dict[str, Any]]:
    """Load and normalize optional Greenhouse/Lever ATS JSON records."""
    if not filename:
        return []

    with open(filename, "r", encoding="utf-8") as file:
        rows = json.load(file)

    if not isinstance(rows, list):
        raise SystemExit(f"{filename} must contain a list of ATS jobs.")

    return [normalize_ats_job(row) for row in rows if isinstance(row, dict)]


def normalize_usajobs_job(row: dict[str, Any]) -> dict[str, Any]:
    """Convert one USAJOBS row to the common job shape."""
    pay = format_usajobs_pay(row)
    return {
        "source": "usajobs",
        "source_id": clean_value(row.get("job_id")),
        "title": clean_value(row.get("title")),
        "company": clean_value(row.get("organization")),
        "department": clean_value(row.get("department")),
        "location": clean_value(row.get("location_display")),
        "city": clean_value(row.get("city")),
        "state": clean_value(row.get("state")),
        "latitude": parse_float(row.get("latitude")),
        "longitude": parse_float(row.get("longitude")),
        "pay": pay,
        "hourly_pay_estimate": parse_hourly_pay(pay),
        "job_type": clean_value(row.get("appointment_type")),
        "schedule": clean_value(row.get("schedule")),
        "date_posted": clean_value(row.get("date_posted")),
        "deadline": clean_value(row.get("application_deadline")),
        "teen_fit_reason": NOT_SPECIFIED,
        "description": clean_value(row.get("qualification_summary")),
        "url": clean_value(row.get("job_url")),
    }


def normalize_indeed_job(row: dict[str, Any]) -> dict[str, Any]:
    """Convert one Indeed CSV row to the common job shape."""
    pay = clean_value(row.get("pay"))
    return {
        "source": "indeed",
        "source_id": clean_value(row.get("job_url")),
        "title": clean_value(row.get("title")),
        "company": clean_value(row.get("company")),
        "department": NOT_SPECIFIED,
        "location": clean_value(row.get("location")),
        "city": extract_city(row.get("location")),
        "state": extract_state(row.get("location")),
        "latitude": None,
        "longitude": None,
        "pay": pay,
        "hourly_pay_estimate": parse_hourly_pay(pay),
        "job_type": clean_value(row.get("job_type")),
        "schedule": clean_value(row.get("schedule_hours")),
        "date_posted": clean_value(row.get("date_posted")),
        "deadline": NOT_SPECIFIED,
        "teen_fit_reason": clean_value(row.get("teen_fit_reason")),
        "description": clean_value(row.get("description_snippet")),
        "url": clean_value(row.get("job_url")),
    }


def normalize_careeronestop_job(row: dict[str, Any]) -> dict[str, Any]:
    """Convert one CareerOneStop row to the common job shape."""
    return {
        "source": "careeronestop",
        "source_id": clean_value(row.get("job_id")),
        "title": clean_value(row.get("title")),
        "company": clean_value(row.get("company")),
        "department": NOT_SPECIFIED,
        "location": clean_value(row.get("location")),
        "city": extract_city(row.get("location")),
        "state": extract_state(row.get("location")),
        "latitude": None,
        "longitude": None,
        "pay": NOT_SPECIFIED,
        "hourly_pay_estimate": None,
        "job_type": NOT_SPECIFIED,
        "schedule": NOT_SPECIFIED,
        "date_posted": clean_value(row.get("date_posted")),
        "deadline": NOT_SPECIFIED,
        "teen_fit_reason": NOT_SPECIFIED,
        "description": clean_value(row.get("description_snippet")),
        "url": clean_value(row.get("job_url")),
        "distance_miles": parse_float(row.get("distance")),
        "onet_codes": row.get("onet_codes") if isinstance(row.get("onet_codes"), list) else [],
        "search_keyword": clean_value(row.get("search_keyword")),
    }


def normalize_ats_job(row: dict[str, Any]) -> dict[str, Any]:
    """Convert one normalized ATS scraper row to the common job shape."""
    pay = clean_value(row.get("pay"))
    return {
        "source": clean_value(row.get("source")),
        "source_id": clean_value(row.get("source_id") or row.get("job_id")),
        "title": clean_value(row.get("title")),
        "company": clean_value(row.get("company")),
        "department": clean_value(row.get("department")),
        "location": clean_value(row.get("location")),
        "city": clean_value(row.get("city")) if row.get("city") else extract_city(row.get("location")),
        "state": clean_value(row.get("state")) if row.get("state") else extract_state(row.get("location")),
        "latitude": parse_float(row.get("latitude")),
        "longitude": parse_float(row.get("longitude")),
        "pay": pay,
        "hourly_pay_estimate": parse_hourly_pay(pay),
        "job_type": clean_value(row.get("job_type")),
        "schedule": clean_value(row.get("schedule")),
        "date_posted": clean_value(row.get("date_posted")),
        "deadline": clean_value(row.get("deadline")),
        "teen_fit_reason": clean_value(row.get("teen_fit_reason")),
        "description": clean_value(row.get("description")),
        "url": clean_value(row.get("url") or row.get("job_url")),
        "ats_provider": clean_value(row.get("ats_provider")),
        "ats_board": clean_value(row.get("ats_board")),
    }


def format_usajobs_pay(row: dict[str, Any]) -> str:
    """Format USAJOBS salary fields into one text value."""
    salary_min = clean_value(row.get("salary_min"))
    salary_max = clean_value(row.get("salary_max"))
    pay_period = clean_value(row.get("pay_period"))

    if salary_min == NOT_SPECIFIED and salary_max == NOT_SPECIFIED:
        return NOT_SPECIFIED
    if salary_min == salary_max or salary_max == NOT_SPECIFIED:
        pay = salary_min
    elif salary_min == NOT_SPECIFIED:
        pay = salary_max
    else:
        pay = f"{salary_min}-{salary_max}"

    if pay_period != NOT_SPECIFIED:
        return f"{pay} / {pay_period}"
    return pay


def extract_city(location: Any) -> str:
    """Extract a simple city from a location string."""
    text = clean_value(location)
    if text == NOT_SPECIFIED:
        return NOT_SPECIFIED
    return text.split(",", 1)[0].strip() or NOT_SPECIFIED


def extract_state(location: Any) -> str:
    """Extract a two-letter state code when present."""
    text = clean_value(location)
    match = re.search(r",\s*([A-Z]{2})\b", text)
    return match.group(1) if match else NOT_SPECIFIED


def parse_hourly_pay(pay: Any) -> Optional[float]:
    """Return an estimated hourly pay from common job board pay strings."""
    text = clean_value(pay).lower().replace(",", "")
    if text == NOT_SPECIFIED.lower():
        return None

    hourly_markers = ("hour", "/hr", " hr", "ph", "per hour")
    if not any(marker in text for marker in hourly_markers):
        return None

    numbers = [float(value) for value in re.findall(r"\$?\s*(\d+(?:\.\d+)?)", text)]
    numbers = [value for value in numbers if 7 <= value <= 100]
    if not numbers:
        return None
    if len(numbers) == 1:
        return numbers[0]
    return sum(numbers[:2]) / 2


def rate_job(
    job: dict[str, Any],
    personal_keywords: Optional[list[str]] = None,
    avoid_keywords: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Score one normalized job for high-school student suitability."""
    score = 20
    reasons = ["+20 base score"]

    keyword_score, keyword_reasons = score_keywords(job, GOOD_KEYWORDS, cap=35)
    personal_score, personal_reasons, personal_matches = score_personal_keywords(job, personal_keywords or [])
    avoid_score, avoid_reasons, avoid_matches = score_avoid_keywords(job, avoid_keywords or [])
    penalty_score, penalty_reasons = score_keywords(job, BAD_KEYWORDS, cap=None)
    metadata_score, metadata_reasons = score_teen_fit_metadata(job)
    schedule_score, schedule_reasons = score_schedule(job)
    summer_score, summer_reasons = score_summer_fit(job)
    location_score, location_reasons = score_location(job)
    pay_score, pay_reasons = score_pay(job)
    source_score, source_reasons = score_source_fit(job)

    score += keyword_score
    score += personal_score
    score += avoid_score
    score += penalty_score
    score += metadata_score
    score += schedule_score
    score += summer_score
    score += location_score
    score += pay_score
    score += source_score

    reasons.extend(keyword_reasons)
    reasons.extend(personal_reasons)
    reasons.extend(avoid_reasons)
    reasons.extend(penalty_reasons)
    reasons.extend(metadata_reasons)
    reasons.extend(schedule_reasons)
    reasons.extend(summer_reasons)
    reasons.extend(location_reasons)
    reasons.extend(pay_reasons)
    reasons.extend(source_reasons)

    score = max(0, min(100, score))
    return {
        "student_fit_score": score,
        "rating_label": label_score(score),
        "rating_reasons": reasons,
        "matched_personal_keywords": personal_matches,
        "matched_avoid_keywords": avoid_matches,
    }


def score_keywords(
    job: dict[str, Any],
    keyword_scores: dict[str, int],
    cap: Optional[int],
) -> tuple[int, list[str]]:
    """Score keyword matches from title, description, and fit metadata."""
    text = searchable_text(job)
    total = 0
    reasons = []

    for keyword, points in keyword_scores.items():
        if contains_phrase(text, keyword):
            total += points
            reasons.append(f"{points:+} keyword match: {keyword}")

    if cap is not None and total > cap:
        reasons.append(f"score capped at +{cap} for positive keyword matches")
        total = cap

    return total, reasons


def parse_personal_keywords(value: Any) -> list[str]:
    """Parse comma-separated personal preference phrases."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        raw_parts = [str(item) for item in value]
    else:
        raw_parts = str(value).split(",")

    keywords = []
    seen = set()
    for part in raw_parts:
        keyword = re.sub(r"\s+", " ", part.strip().lower())
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        keywords.append(keyword)
    return keywords


def parse_avoid_keywords(value: Any) -> list[str]:
    """Parse comma-separated avoid/preference penalty phrases."""
    return parse_personal_keywords(value)


def score_personal_keywords(
    job: dict[str, Any],
    personal_keywords: list[str],
) -> tuple[int, list[str], list[str]]:
    """Score user-selected preferred job keywords.

    Returns the score, the explanation reasons, and the list of keywords that
    actually matched (for display in the results page).
    """
    if not personal_keywords:
        return 0, [], []

    text = f"{searchable_text(job)} {metadata_text(job)}"
    total = 0
    reasons = []
    matched = []
    for keyword in personal_keywords:
        if contains_phrase(text, keyword):
            total += PERSONAL_KEYWORD_POINTS
            reasons.append(f"+{PERSONAL_KEYWORD_POINTS} personal keyword match: {keyword}")
            matched.append(keyword)

    if total > PERSONAL_KEYWORD_CAP:
        reasons.append(f"score capped at +{PERSONAL_KEYWORD_CAP} for personal keyword matches")
        total = PERSONAL_KEYWORD_CAP

    return total, reasons, matched


def score_avoid_keywords(
    job: dict[str, Any],
    avoid_keywords: list[str],
) -> tuple[int, list[str], list[str]]:
    """Score user-selected keywords that should make a job less attractive."""
    if not avoid_keywords:
        return 0, [], []

    text = f"{searchable_text(job)} {metadata_text(job)}"
    total = 0
    reasons = []
    matched = []
    for keyword in avoid_keywords:
        if contains_phrase(text, keyword):
            total += AVOID_KEYWORD_POINTS
            reasons.append(f"{AVOID_KEYWORD_POINTS} avoid keyword match: {keyword}")
            matched.append(keyword)

    if total < AVOID_KEYWORD_CAP:
        reasons.append(f"score capped at {AVOID_KEYWORD_CAP} for avoid keyword matches")
        total = AVOID_KEYWORD_CAP

    return total, reasons, matched


def searchable_text(job: dict[str, Any]) -> str:
    """Join the most useful scoring fields into one lowercase text blob."""
    fields = [
        "title",
        "company",
        "location",
        "job_type",
        "schedule",
        "description",
    ]
    return " ".join(clean_value(job.get(field)) for field in fields).lower()


def metadata_text(job: dict[str, Any]) -> str:
    """Return scraper-provided fit metadata that should be scored separately."""
    return clean_value(job.get("teen_fit_reason")).lower()


def contains_phrase(text: str, phrase: str) -> bool:
    """Match phrases on rough word boundaries."""
    escaped = re.escape(phrase.lower())
    return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text) is not None


def score_schedule(job: dict[str, Any]) -> tuple[int, list[str]]:
    """Score whether the schedule works for a high-school student."""
    text = f"{job.get('job_type', '')} {job.get('schedule', '')}".lower()
    score = 0
    reasons = []

    if "part-time" in text:
        score += 18
        reasons.append("+18 part-time schedule")
    if "flexible" in text:
        score += 10
        reasons.append("+10 flexible schedule")
    if "weekend" in text:
        score += 5
        reasons.append("+5 weekend-compatible schedule")
    if "afternoon" in text or "evening" in text:
        score += 5
        reasons.append("+5 afternoon/evening schedule")
    if "full-time" in text and "part-time" not in text:
        score -= 8
        reasons.append("-8 full-time only")
    if "40 to 50 hours" in text or "40 hours per week" in text:
        score -= 12
        reasons.append("-12 heavy weekly hours")

    return max(-20, min(25, score)), reasons


def score_teen_fit_metadata(job: dict[str, Any]) -> tuple[int, list[str]]:
    """Score explicit fit metadata from the source scraper once."""
    text = metadata_text(job)
    score = 0
    reasons = []

    if "part-time friendly" in text:
        score += 8
        reasons.append("+8 scraper metadata: part-time friendly")
    if "flexible hours" in text:
        score += 6
        reasons.append("+6 scraper metadata: flexible hours")
    if "no degree" in text or "no certification" in text:
        score += 6
        reasons.append("+6 scraper metadata: no degree/certification mentioned")

    return min(score, 14), reasons


def score_summer_fit(job: dict[str, Any]) -> tuple[int, list[str]]:
    """Score seasonal or summer relevance."""
    text = f"{searchable_text(job)} {metadata_text(job)}"
    score = 0
    reasons = []

    if "seasonal/summer" in text:
        score += 15
        reasons.append("+15 explicit seasonal/summer fit")
    elif contains_phrase(text, "seasonal") or contains_phrase(text, "summer"):
        score += 12
        reasons.append("+12 seasonal or summer wording")

    return score, reasons


def coordinates_for_location(location: Any) -> Optional[tuple[float, float]]:
    """Resolve a city/ZIP string to approximate built-in coordinates."""
    text = clean_value(location).lower()
    if text == NOT_SPECIFIED.lower():
        return None

    zip_match = re.search(r"\b(\d{5})\b", text)
    if zip_match and zip_match.group(1) in ZIP_COORDS:
        return ZIP_COORDS[zip_match.group(1)]

    for city, coordinates in sorted(
        NEARBY_CITIES.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if city in text:
            return coordinates

    return None


def job_coordinates(job: dict[str, Any]) -> Optional[tuple[float, float]]:
    """Return the best available coordinates for a normalized job."""
    latitude = job.get("latitude")
    longitude = job.get("longitude")
    if latitude and longitude and (latitude != 0 or longitude != 0):
        return float(latitude), float(longitude)

    return coordinates_for_location(f"{job.get('location', '')} {job.get('city', '')}")


def estimate_distance_miles(
    job: dict[str, Any],
    home_coordinates: Optional[tuple[float, float]],
) -> Optional[float]:
    """Estimate distance from home to a job in miles."""
    if home_coordinates is None:
        return None

    coordinates = job_coordinates(job)
    if coordinates is None:
        return None

    return round(haversine_miles(home_coordinates, coordinates), 1)


def haversine_miles(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    """Calculate distance between two lat/lon points."""
    lat1, lon1 = first
    lat2, lon2 = second
    radius_miles = 3958.8

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius_miles * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def score_location(job: dict[str, Any]) -> tuple[int, list[str]]:
    """Score location fit based on estimated distance from home."""
    text = f"{job.get('location', '')} {job.get('city', '')}".lower()

    if "remote job" in text or "anywhere in the u.s." in text:
        return -6, ["-6 remote/nationwide result instead of local job"]

    distance = job.get("distance_miles")
    if distance is None:
        return 0, ["+0 distance unavailable"]

    if distance <= 2:
        return 15, [f"+15 very close: {distance:.1f} miles away"]
    if distance <= 5:
        return 13, [f"+13 close: {distance:.1f} miles away"]
    if distance <= 10:
        return 10, [f"+10 nearby: {distance:.1f} miles away"]
    if distance <= 15:
        return 7, [f"+7 reasonable distance: {distance:.1f} miles away"]
    if distance <= 25:
        return 4, [f"+4 farther but possible: {distance:.1f} miles away"]

    return -4, [f"-4 far away: {distance:.1f} miles away"]


def score_pay(job: dict[str, Any]) -> tuple[int, list[str]]:
    """Score hourly pay without letting pay dominate student fit."""
    hourly_pay = job.get("hourly_pay_estimate")
    if hourly_pay is None:
        return 0, ["+0 hourly pay not available"]

    if hourly_pay >= 24:
        return 10, [f"+10 strong hourly pay: ${hourly_pay:.2f}/hr"]
    if hourly_pay >= 20:
        return 8, [f"+8 good hourly pay: ${hourly_pay:.2f}/hr"]
    if hourly_pay >= 18:
        return 6, [f"+6 decent hourly pay: ${hourly_pay:.2f}/hr"]
    if hourly_pay >= 15:
        return 3, [f"+3 acceptable hourly pay: ${hourly_pay:.2f}/hr"]
    return 0, [f"+0 low hourly pay: ${hourly_pay:.2f}/hr"]


def score_source_fit(job: dict[str, Any]) -> tuple[int, list[str]]:
    """Apply source-specific adjustments based on current data quality."""
    if job.get("source") == "indeed":
        return 4, ["+4 Indeed listing has local student-job metadata"]

    if job.get("source") == "usajobs":
        text = searchable_text(job)
        if not any(word in text for word in ("student", "summer", "seasonal", "intern")):
            return -12, ["-12 USAJOBS listing lacks student/summer wording"]

    if job.get("source") == "careeronestop":
        return 2, ["+2 CareerOneStop listing has structured local API data"]

    if job.get("source") in {"ats_greenhouse", "ats_lever"}:
        return 3, ["+3 direct company career page source"]

    return 0, []


def label_score(score: int) -> str:
    """Convert a numeric score to a readable label."""
    if score >= 80:
        return "Great student fit"
    if score >= 65:
        return "Good student fit"
    if score >= 45:
        return "Possible fit"
    if score >= 25:
        return "Weak fit"
    return "Poor fit"


def dedupe_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate jobs by URL/source ID, then title/company/location."""
    seen_ids = set()
    seen_fallbacks = set()
    deduped = []

    for job in jobs:
        source = clean_value(job.get("source")).lower()
        url = clean_value(job.get("url"))
        source_id = clean_value(job.get("source_id"))
        if url != NOT_SPECIFIED:
            unique_key = f"url:{url.lower()}"
        elif source_id != NOT_SPECIFIED:
            unique_key = f"{source}:{source_id.lower()}"
        else:
            unique_key = NOT_SPECIFIED

        fallback_key = "|".join(
            clean_value(job.get(field)).lower()
            for field in ("source", "title", "company", "location")
        )

        if unique_key != NOT_SPECIFIED and unique_key in seen_ids:
            continue
        if fallback_key in seen_fallbacks:
            continue
        if unique_key != NOT_SPECIFIED:
            seen_ids.add(unique_key)
        seen_fallbacks.add(fallback_key)
        deduped.append(job)

    return deduped


def resolve_home_location(home_location: str) -> str:
    """Ask for a home location when possible, otherwise use a default."""
    if home_location:
        return home_location

    if sys.stdin.isatty():
        entered = input(
            f"Current location for distance ranking [{DEFAULT_HOME_LOCATION}]: "
        ).strip()
        return entered or DEFAULT_HOME_LOCATION

    return DEFAULT_HOME_LOCATION


def rank_jobs(
    jobs: list[dict[str, Any]],
    home_location: str,
    personal_keywords: Optional[list[str]] = None,
    avoid_keywords: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Rate and sort jobs from highest to lowest student fit."""
    home_coordinates = coordinates_for_location(home_location)
    personal_keywords = personal_keywords or []
    avoid_keywords = avoid_keywords or []
    ranked_jobs = []
    for job in jobs:
        source_distance = parse_float(job.get("distance_miles"))
        if source_distance is not None:
            job["distance_miles"] = source_distance
        else:
            job["distance_miles"] = estimate_distance_miles(job, home_coordinates)
        rating = rate_job(job, personal_keywords, avoid_keywords)
        ranked_jobs.append({**job, "home_location": home_location, **rating})

    ranked_jobs.sort(
        key=lambda job: (
            -job["student_fit_score"],
            deadline_sort_value(job.get("deadline")),
            distance_sort_value(job.get("distance_miles")),
            -(job.get("hourly_pay_estimate") or 0),
            job.get("title", ""),
        )
    )
    return ranked_jobs


def deadline_sort_value(deadline: Any) -> float:
    """Return an earlier-is-better timestamp; unknown deadlines sort last."""
    text = clean_value(deadline)
    if text == NOT_SPECIFIED:
        return float("inf")

    date_match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if date_match:
        return datetime.strptime(date_match.group(1), "%Y-%m-%d").timestamp()

    try:
        normalized = text.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return float("inf")


def distance_sort_value(distance: Any) -> float:
    """Return an earlier-is-better distance value; unknown distance sorts last."""
    if distance is None:
        return float("inf")
    return float(distance)


def save_ranked_jobs(jobs: list[dict[str, Any]], filename: str) -> None:
    """Save ranked jobs as JSON."""
    Path(filename).parent.mkdir(parents=True, exist_ok=True)
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(jobs, file, indent=2, ensure_ascii=False)


def preview_ranked_jobs(jobs: list[dict[str, Any]], count: int = 10) -> None:
    """Print a compact preview of top-ranked jobs."""
    print(f"\nRanked jobs saved: {len(jobs)}")
    if not jobs:
        return

    headers = ["Score", "Source", "Miles", "Deadline", "Title", "Pay"]
    rows = []
    for job in jobs[:count]:
        rows.append(
            [
                str(job["student_fit_score"]),
                job["source"],
                format_distance(job.get("distance_miles")),
                truncate(format_deadline(job.get("deadline")), 10),
                truncate(job["title"], 34),
                truncate(job["pay"], 20),
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


def truncate(text: Any, max_length: int) -> str:
    """Shorten long preview fields."""
    value = clean_value(text)
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3]}..."


def format_distance(distance: Any) -> str:
    """Format distance for preview output."""
    if distance is None:
        return "N/A"
    return f"{float(distance):.1f}"


def format_deadline(deadline: Any) -> str:
    """Format deadline for preview output."""
    text = clean_value(deadline)
    if text == NOT_SPECIFIED:
        return NOT_SPECIFIED
    return text.split("T", 1)[0]




def main() -> None:
    """Load, rate, sort, save, and preview jobs from all configured sources."""
    parser = argparse.ArgumentParser(
        description=(
            "Rank USAJOBS, Indeed, CareerOneStop, and optional ATS listings "
            "by high-school summer job fit."
        )
    )
    parser.add_argument("--usajobs-file", default=DEFAULT_USAJOBS_FILE)
    parser.add_argument("--indeed-file", default=DEFAULT_INDEED_FILE)
    parser.add_argument("--careeronestop-file", default=DEFAULT_CAREERONESTOP_FILE)
    parser.add_argument("--ats-file", default=DEFAULT_ATS_FILE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT_FILE)
    parser.add_argument(
        "--home-location",
        default="",
        help=(
            "Current city or ZIP for distance ranking. If omitted in an "
            f"interactive terminal, you will be prompted. Default: {DEFAULT_HOME_LOCATION}."
        ),
    )
    parser.add_argument("--preview-count", type=int, default=10)
    parser.add_argument(
        "--personal-keywords",
        default="",
        help="Comma-separated preferred job terms to boost in scoring.",
    )
    parser.add_argument(
        "--avoid-keywords",
        default="",
        help="Comma-separated job terms to penalize during scoring.",
    )
    args = parser.parse_args()

    home_location = resolve_home_location(args.home_location)
    personal_keywords = parse_personal_keywords(args.personal_keywords)
    avoid_keywords = parse_avoid_keywords(args.avoid_keywords)
    jobs = (
        load_usajobs(args.usajobs_file)
        + load_indeed_jobs(args.indeed_file)
        + load_careeronestop_jobs(args.careeronestop_file)
        + load_ats_jobs(args.ats_file)
    )
    jobs = dedupe_jobs(jobs)
    ranked_jobs = rank_jobs(jobs, home_location, personal_keywords, avoid_keywords)
    save_ranked_jobs(ranked_jobs, args.output)
    preview_ranked_jobs(ranked_jobs, args.preview_count)
    print(f"\nSaved ranked jobs to {args.output}")


if __name__ == "__main__":
    main()
