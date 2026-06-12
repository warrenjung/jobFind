"""Local saved-answer memory for repeated application questions."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


SENSITIVE_SAVE_PATTERNS = (
    "social security",
    "ssn",
    "password",
    "passcode",
    "captcha",
    "verification code",
    "one-time code",
    "otp",
    "criminal",
    "felony",
    "background check",
    "disability",
    "veteran",
    "race",
    "ethnicity",
    "gender",
    "hispanic",
    "date of birth",
    "birth date",
    "birthdate",
    "authorized to work",
    "legally authorized",
    "work authorization",
    "eligible to work",
    "require sponsorship",
    "visa sponsorship",
    "at least 16",
    "at least 18",
    "work permit",
)


def clean_text(value: Any, max_length: int = 5000) -> str:
    """Normalize user-facing text before saving it."""
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return text[:max_length]


def normalize_question(value: Any) -> str:
    """Return the exact-match key used for saved-answer reuse."""
    return re.sub(r"[^a-z0-9]+", " ", clean_text(value).lower()).strip()


def question_is_safe_to_save(question: Any, kind: Any = "") -> bool:
    """Whether a question is safe to remember and reuse."""
    text = normalize_question(f"{question} {kind}")
    if not text:
        return False
    generic = {
        "yes no question on this step",
        "question on this step",
        "review this field on the page",
        "unlabeled text field",
    }
    if text in generic:
        return False
    return not any(pattern in text for pattern in SENSITIVE_SAVE_PATTERNS)


def read_saved_answers(path: Path) -> dict[str, Any]:
    """Read saved answers, tolerating missing or corrupt files."""
    if not path.exists():
        return {"answers": []}
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {"answers": []}
    if not isinstance(payload, dict):
        return {"answers": []}
    rows = payload.get("answers", [])
    if not isinstance(rows, list):
        rows = []
    return {"answers": [row for row in rows if isinstance(row, dict)]}


def saved_answer_map(payload: dict[str, Any]) -> dict[str, str]:
    """Return normalized question key -> answer for autofill."""
    rows = payload.get("answers", []) if isinstance(payload, dict) else []
    result: dict[str, str] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        key = clean_text(row.get("key"), 500) or normalize_question(row.get("question"))
        answer = clean_text(row.get("answer"))
        if key and answer:
            result[key] = answer
    return result


def write_saved_answers(path: Path, payload: dict[str, Any]) -> None:
    """Write saved answers as stable JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
        file.write("\n")


def markdown_escape(value: Any) -> str:
    """Escape Markdown table separators."""
    return clean_text(value).replace("|", "\\|")


def write_saved_answers_markdown(path: Path, payload: dict[str, Any]) -> None:
    """Generate a readable local answer notebook."""
    rows = payload.get("answers", []) if isinstance(payload, dict) else []
    lines = [
        "# Saved JobFind Answers",
        "",
        "These answers are stored locally and reused for exact non-sensitive application questions.",
        "",
        "| Question | Answer | Kind | Last job | Updated |",
        "|---|---|---|---|---|",
    ]
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        job = " - ".join(
            part
            for part in (
                clean_text(row.get("job_title"), 200),
                clean_text(row.get("employer"), 200),
            )
            if part
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_escape(row.get("question")),
                    markdown_escape(row.get("answer")),
                    markdown_escape(row.get("kind")),
                    markdown_escape(job or "Not specified"),
                    markdown_escape(row.get("updated_at")),
                ]
            )
            + " |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        file.write("\n".join(lines).rstrip() + "\n")


def normalize_save_payload(payload: dict[str, Any]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Normalize an overlay save request."""
    if not isinstance(payload, dict):
        return None, "saved answer payload must be a JSON object."
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    job = payload.get("job") if isinstance(payload.get("job"), dict) else {}
    question = clean_text(payload.get("question") or item.get("question"), 1000)
    answer = clean_text(payload.get("answer"), 5000)
    kind = clean_text(payload.get("kind") or item.get("kind"), 80)
    source = clean_text(payload.get("source"), 80) or "accepted"
    if not question:
        return None, "question is required."
    if not answer:
        return None, "answer is required."
    if not question_is_safe_to_save(question, kind):
        return None, "This question is not safe to save automatically."
    key = normalize_question(question)
    options = item.get("options") if isinstance(item.get("options"), list) else payload.get("options")
    cleaned_options = [clean_text(option, 200) for option in options or [] if clean_text(option, 200)]
    row = {
        "key": key,
        "question": question,
        "answer": answer,
        "kind": kind,
        "source": source,
        "job_title": clean_text(job.get("title"), 300),
        "employer": clean_text(job.get("company") or job.get("employer"), 300),
        "job_url": clean_text(job.get("url"), 1200),
    }
    if cleaned_options:
        row["options"] = cleaned_options
    return row, None


def upsert_saved_answer(
    json_path: Path,
    markdown_path: Path,
    payload: dict[str, Any],
    now: Optional[Callable[[], datetime]] = None,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Create or update a saved answer and refresh Markdown."""
    row, error = normalize_save_payload(payload)
    if error or row is None:
        return None, error
    current = read_saved_answers(json_path)
    rows = current["answers"]
    timestamp = (now or (lambda: datetime.now(timezone.utc)))().isoformat()
    existing = next((item for item in rows if item.get("key") == row["key"]), None)
    if existing:
        existing.update(row)
        existing["updated_at"] = timestamp
        existing["times_saved"] = int(existing.get("times_saved") or 1) + 1
        saved = existing
    else:
        row["first_saved_at"] = timestamp
        row["updated_at"] = timestamp
        row["times_saved"] = 1
        rows.append(row)
        saved = row
    rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    write_saved_answers(json_path, {"answers": rows})
    write_saved_answers_markdown(markdown_path, {"answers": rows})
    return saved, None
