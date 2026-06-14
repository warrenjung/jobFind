"""Profile and common-answer helpers for the local JobFind server."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional

from utils import atomic_write_text


DEFAULT_PROFILE_REQUIRED_LABELS = {
    "name": "name",
    "email": "email",
    "phone": "phone",
    "city": "city",
    "state": "state",
    "availability": "availability",
}

DEFAULT_COMMON_ANSWER_FIELDS = [
    ("ideal_job", "Ideal job", "What does your ideal job look like?"),
    ("availability", "Availability", "When are you available to work?"),
    ("transportation", "Transportation", "Do you have reliable transportation?"),
    ("why_this_company", "Why this company", "Why do you want to work here?"),
    ("customer_service", "Customer service", "Tell us about your customer service experience."),
    ("teamwork", "Teamwork", "Tell us about a time you worked on a team."),
    ("extra_notes", "Extra notes", "Anything else an employer should know?"),
]

DEFAULT_COMMON_ANSWER_ALIASES = {
    "ideal_job": (
        "What are 3 things you'd look for in an ideal job?",
        "What are you looking for in a job?",
    ),
    "availability": (
        "Are you available weekends?",
        "How many hours per week can you work?",
    ),
    "transportation": (
        "Do you have reliable transportation?",
        "Can you get to work reliably?",
    ),
    "why_this_company": (
        "Why are you interested in this job?",
        "Why are you interested in this company?",
    ),
    "customer_service": (
        "Do you have any service experience?",
        "Describe any food service or retail service experience",
    ),
    "teamwork": (
        "Describe teamwork with coworkers",
        "Tell us about teamwork",
    ),
}


def read_json_file(path: Path, fallback: Any) -> Any:
    """Read JSON from disk, returning fallback for missing or invalid files."""
    if not path.exists():
        return fallback
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return fallback


def write_json_file(path: Path, payload: Any) -> None:
    """Write a JSON payload atomically, creating parent directories as needed."""
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n")


def load_profile(profile_file: Path, read_json: Callable[[Path, Any], Any] = read_json_file) -> dict[str, Any]:
    """Load a local applicant profile."""
    profile = read_json(profile_file, {})
    return profile if isinstance(profile, dict) else {}


def profile_missing_fields(
    profile: dict[str, Any],
    clean_text: Callable[[Any, int], str],
    required_labels: dict[str, str] = DEFAULT_PROFILE_REQUIRED_LABELS,
) -> list[str]:
    """Return friendly names for required profile fields that are missing."""
    missing = []
    first = clean_text(profile.get("first_name"), 500)
    last = clean_text(profile.get("last_name"), 500)
    full_name = clean_text(profile.get("name"), 500)
    if not full_name and not (first and last):
        missing.append(required_labels["name"])
    for key in ("email", "phone", "city", "state", "availability"):
        if not clean_text(profile.get(key), 500):
            missing.append(required_labels[key])
    return missing


def common_answer_field_map(
    fields: list[tuple[str, str, str]] = DEFAULT_COMMON_ANSWER_FIELDS,
) -> dict[str, dict[str, str]]:
    """Return metadata for editable common answers."""
    return {
        key: {"key": key, "label": label, "prompt": prompt}
        for key, label, prompt in fields
    }


def common_answers_from_profile(
    profile: dict[str, Any],
    field_map: dict[str, dict[str, str]],
    aliases: dict[str, tuple[str, ...]],
    clean_text: Callable[[Any, int], str],
) -> dict[str, str]:
    """Return common answers from the profile's custom_answers map."""
    raw_answers = profile.get("custom_answers", {})
    answers = raw_answers if isinstance(raw_answers, dict) else {}
    result: dict[str, str] = {}
    for key in field_map:
        prompts = (field_map[key]["prompt"], *aliases.get(key, ()))
        result[key] = ""
        for prompt in prompts:
            value = clean_text(answers.get(prompt), 5000)
            if value:
                result[key] = value
                break
    if not result.get("availability"):
        result["availability"] = clean_text(profile.get("availability"), 5000)
    return result


def common_answers_payload(
    payload: dict[str, Any],
    field_map: dict[str, dict[str, str]],
    clean_text: Callable[[Any, int], str],
) -> tuple[dict[str, str], Optional[str]]:
    """Normalize a common-answer save payload."""
    raw_answers = payload.get("answers", payload)
    if not isinstance(raw_answers, dict):
        return {}, "answers must be a JSON object."
    result = {}
    for key in field_map:
        if key in raw_answers:
            result[key] = clean_text(raw_answers.get(key), 5000)
    return result, None


def save_profile_updates(
    profile_file: Path,
    payload: dict[str, Any],
    edit_fields: list[str],
    clean_text: Callable[[Any, int], str],
    resume_path_issue: Callable[[dict[str, Any]], Optional[str]],
    load_profile_fn: Callable[[], dict[str, Any]],
    write_json: Callable[[Path, Any], None],
) -> tuple[dict[str, Any], list[str]]:
    """Save user-editable profile fields while preserving advanced data."""
    profile = load_profile_fn()
    for key in edit_fields:
        if key in payload:
            profile[key] = clean_text(payload.get(key), 5000)

    write_json(profile_file, profile)
    warnings = []
    resume_issue = resume_path_issue(profile)
    if resume_issue:
        warnings.append(resume_issue)
    return profile, warnings


def save_common_answers_updates(
    profile_file: Path,
    payload: dict[str, Any],
    field_map: dict[str, dict[str, str]],
    aliases: dict[str, tuple[str, ...]],
    clean_text: Callable[[Any, int], str],
    load_profile_fn: Callable[[], dict[str, Any]],
    write_json: Callable[[Path, Any], None],
) -> tuple[dict[str, Any], dict[str, str], Optional[str]]:
    """Save common answers while preserving unrelated custom answers."""
    updates, error = common_answers_payload(payload, field_map, clean_text)
    if error:
        return {}, {}, error
    profile = load_profile_fn()
    existing = profile.get("custom_answers", {})
    custom = dict(existing) if isinstance(existing, dict) else {}
    for key, value in updates.items():
        prompt = field_map[key]["prompt"]
        if value:
            custom[prompt] = value
        else:
            custom.pop(prompt, None)
    profile["custom_answers"] = custom
    write_json(profile_file, profile)
    answers = common_answers_from_profile(profile, field_map, aliases, clean_text)
    return profile, answers, None
