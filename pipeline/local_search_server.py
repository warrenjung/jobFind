"""
Run a local browser UI for refreshing job results.

The server is intentionally localhost-only. It lets a browser form start the
existing pipeline without exposing API keys to the page.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import application_autofill
import requests


REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "data"
STATIC_DIR = Path(__file__).parent / "app_static"
PIPELINE_SCRIPT = REPO_ROOT / "pipeline" / "run_job_pipeline.py"
RESULTS_FILE = DATA_DIR / "jobs_clean.html"
PROFILE_FILE = REPO_ROOT / "applicant_profile.json"
APPLICATIONS_FILE = DATA_DIR / "applications_status.json"
INDEED_PROFILE_DIR = application_autofill.persistent_profile_path(DATA_DIR, "indeed")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_LOCATION = "Cupertino, CA"
DEFAULT_RADIUS = "10"
DEFAULT_MODE = "fast"
DEFAULT_MIN_SCORE = "50"

RADIUS_CHOICES = {"5", "10", "15", "25", "35", "50"}
RADIUS_ORDER = ["5", "10", "15", "25", "35", "50"]

# Static frontend files served from disk, with their content types. Allowlisted
# (no path traversal) — only these exact names are served.
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}
MODE_CHOICES = {"fast", "full"}
FAST_QUERIES = ["cashier", "retail associate", "barista"]
MAX_LOG_LINES = 240
APPLICATION_STATUSES = {
    "Not started",
    "Opened",
    "Autofilled",
    "Applied",
    "Skipped",
    "Needs follow-up",
}

LIVE_AUTOFILL_REPORTS: list[dict[str, Any]] = []
LIVE_LOGIN_REPORTS: list[dict[str, Any]] = []
SESSION_RECOVERY_MESSAGE = ""
PROFILE_EDIT_FIELDS = [
    "name",
    "first_name",
    "last_name",
    "preferred_name",
    "email",
    "phone",
    "city",
    "state",
    "school",
    "graduation_year",
    "education_level",
    "availability",
    "available_start_date",
    "desired_hours",
    "age_or_work_permit",
    "work_eligibility",
    "resume_path",
    "short_intro",
]
PROFILE_REQUIRED_LABELS = {
    "name": "name",
    "email": "email",
    "phone": "phone",
    "city": "city",
    "state": "state",
    "availability": "availability",
}
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-5.1-mini"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
OLLAMA_SETUP_MESSAGE = (
    "AI polish is optional. For the free local option, install Ollama, run "
    "`ollama pull llama3.2:3b`, start Ollama, then try again."
)
COMMON_ANSWER_FIELDS = [
    ("ideal_job", "Ideal job", "What does your ideal job look like?"),
    ("availability", "Availability", "When are you available to work?"),
    ("transportation", "Transportation", "Do you have reliable transportation?"),
    ("why_this_company", "Why this company", "Why do you want to work here?"),
    ("customer_service", "Customer service", "Tell us about your customer service experience."),
    ("teamwork", "Teamwork", "Tell us about a time you worked on a team."),
    ("extra_notes", "Extra notes", "Anything else an employer should know?"),
]
COMMON_ANSWER_ALIASES = {
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
    ),
    "why_this_company": (
        "Why are you interested in this job?",
        "Why are you interested in this company?",
    ),
    "customer_service": (
        "Tell us about your customer service experience.",
    ),
    "teamwork": (
        "Tell us about a time you worked on a team.",
    ),
    "extra_notes": (
        "Anything else an employer should know?",
    ),
}


class RunState:
    """Thread-safe state for the latest pipeline run."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.status = "idle"
        self.returncode: Optional[int] = None
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.location = DEFAULT_LOCATION
        self.command: list[str] = []
        self.log_lines: list[str] = []

    def snapshot(self) -> dict[str, Any]:
        """Return JSON-safe state for the browser."""
        with self.lock:
            return {
                "status": self.status,
                "returncode": self.returncode,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "location": self.location,
                "command": self.command,
                "logs": "\n".join(self.log_lines[-MAX_LOG_LINES:]),
                "results_available": RESULTS_FILE.exists(),
            }

    def start(self, location: str, command: list[str]) -> bool:
        """Mark a run as started unless another run is already active."""
        with self.lock:
            if self.status == "running":
                return False
            self.status = "running"
            self.returncode = None
            self.started_at = time.time()
            self.finished_at = None
            self.location = location
            self.command = command
            self.log_lines = [f"$ {' '.join(command)}"]
            return True

    def append_log(self, line: str) -> None:
        """Append one log line and trim old output."""
        with self.lock:
            self.log_lines.append(line.rstrip())
            if len(self.log_lines) > MAX_LOG_LINES:
                self.log_lines = self.log_lines[-MAX_LOG_LINES:]

    def finish(self, returncode: int) -> None:
        """Mark the run as complete."""
        with self.lock:
            self.returncode = returncode
            self.finished_at = time.time()
            self.status = "succeeded" if returncode == 0 else "failed"


RUN_STATE = RunState()


class AutofillState:
    """Thread-safe status for the current autofill browser run."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.status = "idle"
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.job: dict[str, str] = {}
        self.report: dict[str, Any] = {}
        self.log_lines: list[str] = []

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "status": self.status,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "job": self.job,
                "report": self.report,
                "logs": "\n".join(self.log_lines[-MAX_LOG_LINES:]),
            }

    def start(self, job: dict[str, str]) -> bool:
        with self.lock:
            if self.status == "running":
                return False
            self.status = "running"
            self.started_at = time.time()
            self.finished_at = None
            self.job = job
            self.report = {}
            self.log_lines = [f"Opening visible autofill browser for {job.get('title') or job.get('url')}"]
            return True

    def append_log(self, line: str) -> None:
        with self.lock:
            self.log_lines.append(line)
            if len(self.log_lines) > MAX_LOG_LINES:
                self.log_lines = self.log_lines[-MAX_LOG_LINES:]

    def finish(self, status: str, report: dict[str, Any]) -> None:
        with self.lock:
            self.status = status
            self.finished_at = time.time()
            self.report = report


AUTOFILL_STATE = AutofillState()
LOGIN_STATE = AutofillState()


def report_needs_login(report: dict[str, Any]) -> bool:
    """Whether an autofill report stopped at an account/login step."""
    stages = report.get("stages") or []
    reason = str(report.get("status_reason") or "").lower()
    return "login_required" in stages or "login" in reason or "verification" in reason


def latest_login_port() -> Optional[int]:
    """Return the debug port of the latest still-running Indeed login Chrome."""
    global SESSION_RECOVERY_MESSAGE
    SESSION_RECOVERY_MESSAGE = ""
    for report in reversed(LIVE_LOGIN_REPORTS):
        port = report.get("_debug_port")
        proc = report.get("_chrome_proc")
        if port is None:
            continue
        if proc is not None and proc.poll() is not None:
            continue  # that Chrome has exited
        if application_autofill.chrome_debug_ready(port):
            return port
    session = application_autofill.load_chrome_session(INDEED_PROFILE_DIR)
    if session:
        port = int(session["debug_port"])
        if application_autofill.chrome_debug_ready(port):
            return port
    recovered = application_autofill.recover_chrome_session(INDEED_PROFILE_DIR, preferred_port=9222)
    if recovered:
        SESSION_RECOVERY_MESSAGE = "Recovered existing JobFind Chrome session."
        return int(recovered["debug_port"])
    if application_autofill.profile_lock_files_exist(INDEED_PROFILE_DIR):
        SESSION_RECOVERY_MESSAGE = "Close JobFind Chrome windows and try Open Indeed Login again."
    return None


def run_open_indeed_login() -> None:
    """Open a persistent visible browser so the user can log into Indeed."""
    try:
        LOGIN_STATE.append_log("Opening visible Indeed login browser.")
        report = application_autofill.open_indeed_login(INDEED_PROFILE_DIR, headless=False)
        LIVE_LOGIN_REPORTS.append(report)
        public = application_autofill.public_report(report)
        LOGIN_STATE.append_log("Log into Indeed there, then return here and click Resume Autofill.")
        LOGIN_STATE.finish("succeeded", public)
    except Exception as exc:
        LOGIN_STATE.append_log(f"Could not open Indeed login browser: {exc}")
        LOGIN_STATE.finish("failed", {"error": str(exc), "submitted": False})


def run_autofill(job: dict[str, str], resume: bool = False) -> None:
    """Run the Playwright autofill worker in a background thread."""
    try:
        AUTOFILL_STATE.append_log("Loading local applicant_profile.json.")
        if resume:
            AUTOFILL_STATE.append_log("Resuming with the saved Indeed browser profile.")
        else:
            AUTOFILL_STATE.append_log("Using the saved Indeed browser profile when available.")
        profile = load_applicant_profile()
        resume_issue = application_autofill.resume_path_issue(profile)
        if resume_issue:
            AUTOFILL_STATE.append_log(resume_issue)
        cdp_port = latest_login_port()
        if cdp_port is not None:
            AUTOFILL_STATE.append_log("Attaching to your logged-in Chrome over CDP.")
            if SESSION_RECOVERY_MESSAGE:
                AUTOFILL_STATE.append_log(SESSION_RECOVERY_MESSAGE)
        else:
            AUTOFILL_STATE.append_log(
                SESSION_RECOVERY_MESSAGE
                or "No logged-in Chrome found. Click Open Indeed Login first, sign in, then run autofill again."
            )
        report = application_autofill.autofill_application(
            job["url"],
            profile,
            headless=False,
            user_data_dir=INDEED_PROFILE_DIR,
            cdp_port=cdp_port,
        )
        LIVE_AUTOFILL_REPORTS.append(report)
        public = application_autofill.public_report(report)
        filled_count = int(public.get("filled_count") or 0)
        stages = public.get("stages") or []
        if stages:
            AUTOFILL_STATE.append_log(f"Stages: {', '.join(stages)}")
        if public.get("status_reason"):
            AUTOFILL_STATE.append_log(str(public["status_reason"]))
        if public.get("navigation_error"):
            AUTOFILL_STATE.append_log(f"Navigation error: {public['navigation_error']}")
        if public.get("verification_frames"):
            AUTOFILL_STATE.append_log("Verification frames detected; manual review is required.")
        if public.get("background_verification_detected"):
            AUTOFILL_STATE.append_log("Background reCAPTCHA detected; continuing until a visible challenge appears.")
        if public.get("current_action"):
            AUTOFILL_STATE.append_log(f"Status: {public['current_action']}")
        if public.get("resume_uploaded"):
            AUTOFILL_STATE.append_log("Resume uploaded from applicant_profile.json.")
        if public.get("advanced_steps"):
            AUTOFILL_STATE.append_log(f"Advanced steps: {', '.join(public['advanced_steps'])}")
        if public.get("stopped_reason"):
            AUTOFILL_STATE.append_log(f"Stopped reason: {public['stopped_reason']}")
        AUTOFILL_STATE.append_log(f"Filled {filled_count} fields.")
        for item in public.get("filled", [])[:20]:
            AUTOFILL_STATE.append_log(f"Filled: {item}")
        for item in public.get("current_step_needs_review", [])[:20]:
            AUTOFILL_STATE.append_log(f"Current step review: {item}")
        for item in public.get("needs_review", [])[:20]:
            AUTOFILL_STATE.append_log(f"Review: {item}")
        if report_needs_login(public):
            AUTOFILL_STATE.append_log("Login or verification is required. Use Open Indeed Login, then Resume Autofill.")
        if filled_count > 0:
            upsert_application(job, "Autofilled")
        status = "failed" if public.get("error") or public.get("stopped_reason") in {"open_login_first", "navigation_error"} else "succeeded"
        AUTOFILL_STATE.finish(status, public)
    except Exception as exc:
        AUTOFILL_STATE.append_log(f"Autofill failed: {exc}")
        AUTOFILL_STATE.finish("failed", {"error": str(exc), "submitted": False})


def timestamp() -> str:
    """Return an ISO-ish local timestamp for application tracking."""
    return datetime.now().replace(microsecond=0).isoformat()


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
    """Write a JSON payload, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
        file.write("\n")


def load_applicant_profile() -> dict[str, Any]:
    """Load local applicant profile fields for copy/paste assistance."""
    profile = read_json_file(PROFILE_FILE, {})
    return profile if isinstance(profile, dict) else {}


def profile_missing_fields(profile: dict[str, Any]) -> list[str]:
    """Return friendly names for required profile fields that are missing."""
    missing = []
    first = clean_payload_text(profile.get("first_name"))
    last = clean_payload_text(profile.get("last_name"))
    full_name = clean_payload_text(profile.get("name"))
    if not full_name and not (first and last):
        missing.append(PROFILE_REQUIRED_LABELS["name"])
    for key in ("email", "phone", "city", "state", "availability"):
        if not clean_payload_text(profile.get(key)):
            missing.append(PROFILE_REQUIRED_LABELS[key])
    return missing


def build_setup_status() -> dict[str, Any]:
    """Return local-only setup readiness for the browser checklist."""
    profile = load_applicant_profile()
    profile_exists = PROFILE_FILE.exists()
    missing_fields = profile_missing_fields(profile)
    resume_issue = application_autofill.resume_path_issue(profile)
    indeed_ready = latest_login_port() is not None
    results_ready = RESULTS_FILE.exists()

    profile_ready = profile_exists and not missing_fields
    resume_ready = resume_issue is None
    checks = {
        "profile": {
            "ready": profile_ready,
            "label": "Profile info",
            "message": (
                "Profile basics are ready."
                if profile_ready
                else "Add " + ", ".join(missing_fields or ["profile info"]) + "."
            ),
            "action": "Edit profile",
        },
        "resume": {
            "ready": resume_ready,
            "label": "Resume file",
            "message": "Resume path is ready." if resume_ready else resume_issue,
            "action": "Fix resume path",
        },
        "indeed_login": {
            "ready": indeed_ready,
            "label": "Indeed login",
            "message": (
                "Chrome login session is ready."
                if indeed_ready
                else SESSION_RECOVERY_MESSAGE
                or "Open Indeed Login when you want assisted applications."
            ),
            "action": "Open Indeed Login",
        },
        "results": {
            "ready": results_ready,
            "label": "Job results",
            "message": (
                "Latest results page is available."
                if results_ready
                else "Run a search to generate job results."
            ),
            "action": "Search jobs",
        },
    }
    return {
        "all_ready": all(check["ready"] for check in checks.values()),
        "profile_exists": profile_exists,
        "checks": checks,
    }


def save_applicant_profile_updates(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Save friendly profile fields while preserving advanced local data."""
    profile = load_applicant_profile()
    for key in PROFILE_EDIT_FIELDS:
        if key in payload:
            profile[key] = clean_payload_text(payload.get(key), 5000)

    write_json_file(PROFILE_FILE, profile)
    warnings = []
    resume_issue = application_autofill.resume_path_issue(profile)
    if resume_issue:
        warnings.append(resume_issue)
    return profile, warnings


def common_answer_field_map() -> dict[str, dict[str, str]]:
    """Return metadata for editable common answers."""
    return {
        key: {"key": key, "label": label, "prompt": prompt}
        for key, label, prompt in COMMON_ANSWER_FIELDS
    }


def common_answers_from_profile(profile: dict[str, Any]) -> dict[str, str]:
    """Return common answers from the profile's advanced custom_answers map."""
    raw_answers = profile.get("custom_answers", {})
    answers = raw_answers if isinstance(raw_answers, dict) else {}
    result: dict[str, str] = {}
    field_map = common_answer_field_map()
    for key in field_map:
        prompts = (field_map[key]["prompt"], *COMMON_ANSWER_ALIASES.get(key, ()))
        result[key] = ""
        for prompt in prompts:
            value = clean_payload_text(answers.get(prompt), 5000)
            if value:
                result[key] = value
                break
    if not result.get("availability"):
        result["availability"] = clean_payload_text(profile.get("availability"), 5000)
    return result


def common_answers_payload(payload: dict[str, Any]) -> tuple[dict[str, str], Optional[str]]:
    """Normalize a common-answer save payload."""
    raw_answers = payload.get("answers", payload)
    if not isinstance(raw_answers, dict):
        return {}, "answers must be a JSON object."
    allowed = common_answer_field_map()
    result = {}
    for key in allowed:
        if key in raw_answers:
            result[key] = clean_payload_text(raw_answers.get(key), 5000)
    return result, None


def save_common_answers_updates(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str], Optional[str]]:
    """Save common answers while preserving other custom answers."""
    updates, error = common_answers_payload(payload)
    if error:
        return {}, {}, error
    profile = load_applicant_profile()
    existing = profile.get("custom_answers", {})
    custom = dict(existing) if isinstance(existing, dict) else {}
    field_map = common_answer_field_map()
    for key, value in updates.items():
        prompt = field_map[key]["prompt"]
        if value:
            custom[prompt] = value
        else:
            custom.pop(prompt, None)
    profile["custom_answers"] = custom
    write_json_file(PROFILE_FILE, profile)
    return profile, common_answers_from_profile(profile), None


def common_answers_response() -> dict[str, Any]:
    """Return editable common answers and field metadata for the browser."""
    profile = load_applicant_profile()
    provider = ai_provider_status()
    return {
        "fields": list(common_answer_field_map().values()),
        "answers": common_answers_from_profile(profile),
        "ai_enabled": bool(provider["enabled"]),
        "ai_provider": provider["provider"],
        "model": provider["model"],
        "ai_message": provider["message"],
    }


def ollama_base_url() -> str:
    """Return the configured Ollama base URL without a trailing slash."""
    return os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL).rstrip("/")


def ollama_model() -> str:
    """Return the configured Ollama model name."""
    return os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)


def ollama_available(timeout: float = 0.7) -> bool:
    """Whether the local Ollama API looks reachable."""
    try:
        response = requests.get(f"{ollama_base_url()}/api/tags", timeout=timeout)
    except requests.RequestException:
        return False
    return response.status_code < 400


def ai_provider_status() -> dict[str, Any]:
    """Return the currently available AI polish provider."""
    if os.getenv("OPENAI_API_KEY"):
        model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        return {
            "enabled": True,
            "provider": "openai",
            "model": model,
            "message": f"AI polish available through OpenAI ({model}).",
        }
    model = ollama_model()
    if ollama_available():
        return {
            "enabled": True,
            "provider": "ollama",
            "model": model,
            "message": f"AI polish available locally through Ollama ({model}).",
        }
    return {
        "enabled": False,
        "provider": "none",
        "model": model,
        "message": OLLAMA_SETUP_MESSAGE,
    }


def build_ai_polish_prompt(payload: dict[str, Any], profile: dict[str, Any]) -> tuple[str, Optional[str]]:
    """Build a safe prompt for review-first answer improvement."""
    answer_key = clean_payload_text(payload.get("key"), 80)
    field = common_answer_field_map().get(answer_key)
    if not field:
        return "", "Unknown common answer field."
    draft = clean_payload_text(payload.get("draft"), 3000)
    if not draft:
        return "", "Write an answer first, then ask AI to improve it."
    job = payload.get("job", {})
    if not isinstance(job, dict):
        job = {}
    title = clean_payload_text(job.get("title"), 200) or "Not specified"
    company = clean_payload_text(job.get("company"), 200) or "Not specified"
    description = clean_payload_text(job.get("description") or job.get("reason"), 1400) or "Not specified"
    student_context = clean_payload_text(profile.get("short_intro"), 700) or "High school student seeking entry-level work."
    prompt = f"""Improve this student job-application answer.

Question type: {field['label']}
Likely application question: {field['prompt']}
Current answer: {draft}

Selected job:
Title: {title}
Employer: {company}
Description/context: {description}

Student context: {student_context}

Rules:
- Return only one improved answer, 1-3 sentences.
- Keep it honest, natural, specific, and appropriate for a high school student.
- Do not invent experience, credentials, availability, transportation, legal eligibility, age, or work authorization.
- Do not answer sensitive/legal/demographic questions.
- Keep the student's meaning, but make wording clearer and more matched to the job."""
    return prompt, None


def extract_openai_text(payload: dict[str, Any]) -> str:
    """Extract text from a Responses API payload."""
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    chunks = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "\n".join(chunks).strip()


def extract_ollama_text(payload: dict[str, Any]) -> str:
    """Extract text from an Ollama generate response."""
    text = payload.get("response")
    return text.strip() if isinstance(text, str) else ""


def call_openai_polish(prompt: str) -> tuple[dict[str, str], int]:
    """Call OpenAI for one review-first suggestion."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {"error": "OPENAI_API_KEY is not set."}, 503
    model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    try:
        response = requests.post(
            OPENAI_RESPONSES_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "instructions": "You improve short job application answers for a student. Return only the revised answer.",
                "input": prompt,
                "max_output_tokens": 220,
                "temperature": 0.2,
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        return {"error": f"Could not reach OpenAI: {exc}"}, 502
    if response.status_code >= 400:
        return {"error": f"OpenAI request failed with status {response.status_code}."}, 502
    try:
        response_payload = response.json()
    except ValueError:
        return {"error": "OpenAI returned a non-JSON response."}, 502
    suggestion = extract_openai_text(response_payload)
    if not suggestion:
        return {"error": "OpenAI did not return a suggestion."}, 502
    return {"suggestion": suggestion, "model": model, "provider": "openai"}, 200


def call_ollama_polish(prompt: str) -> tuple[dict[str, str], int]:
    """Call local Ollama for one review-first suggestion."""
    model = ollama_model()
    try:
        response = requests.post(
            f"{ollama_base_url()}/api/generate",
            json={
                "model": model,
                "system": "You improve short job application answers for a student. Return only the revised answer.",
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2},
            },
            timeout=45,
        )
    except requests.RequestException as exc:
        return {"error": f"Could not reach Ollama: {exc}. {OLLAMA_SETUP_MESSAGE}"}, 502
    if response.status_code >= 400:
        return {
            "error": (
                f"Ollama request failed with status {response.status_code}. "
                f"Make sure `{model}` is installed with `ollama pull {model}`."
            )
        }, 502
    try:
        response_payload = response.json()
    except ValueError:
        return {"error": "Ollama returned a non-JSON response."}, 502
    suggestion = extract_ollama_text(response_payload)
    if not suggestion:
        return {"error": "Ollama did not return a suggestion."}, 502
    return {"suggestion": suggestion, "model": model, "provider": "ollama"}, 200


def improve_common_answer(payload: dict[str, Any]) -> tuple[dict[str, str], int]:
    """Return an AI-polished answer suggestion without saving it."""
    prompt, error = build_ai_polish_prompt(payload, load_applicant_profile())
    if error:
        return {"error": error}, 400
    provider = ai_provider_status()
    if provider["provider"] == "openai":
        return call_openai_polish(prompt)
    if provider["provider"] == "ollama":
        return call_ollama_polish(prompt)
    return {"error": provider["message"], "provider": "none"}, 503


def load_application_records() -> dict[str, dict[str, Any]]:
    """Load application records keyed by job URL."""
    payload = read_json_file(APPLICATIONS_FILE, {"applications": []})
    rows = payload.get("applications", []) if isinstance(payload, dict) else []
    records: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if url:
            records[url] = row
    return records


def save_application_records(records: dict[str, dict[str, Any]]) -> None:
    """Persist application records in a stable, readable shape."""
    rows = sorted(
        records.values(),
        key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""),
        reverse=True,
    )
    write_json_file(APPLICATIONS_FILE, {"applications": rows})


def clean_payload_text(value: Any, max_length: int = 500) -> str:
    """Normalize short browser-provided job fields."""
    text = "" if value is None else str(value).strip()
    return text[:max_length]


def normalize_job_payload(payload: dict[str, Any]) -> dict[str, str]:
    """Keep only the non-sensitive job fields needed for tracking."""
    return {
        "url": clean_payload_text(payload.get("url"), 1200),
        "title": clean_payload_text(payload.get("title")),
        "company": clean_payload_text(payload.get("company")),
        "source": clean_payload_text(payload.get("source")),
        "score": clean_payload_text(payload.get("score"), 50),
    }


def upsert_application(
    payload: dict[str, Any],
    status: str,
    notes: Optional[str] = None,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Create or update an application tracking row."""
    if status not in APPLICATION_STATUSES:
        return None, "Unknown application status."

    job = normalize_job_payload(payload)
    if not job["url"].startswith(("http://", "https://")):
        return None, "A valid apply URL is required."

    records = load_application_records()
    record = records.get(job["url"], {})
    now = timestamp()
    if not record:
        record = {
            "url": job["url"],
            "created_at": now,
        }
    for key, value in job.items():
        if value:
            record[key] = value
    record["status"] = status
    record["updated_at"] = now
    if status == "Opened" and not record.get("opened_at"):
        record["opened_at"] = now
    if status == "Applied" and not record.get("applied_at"):
        record["applied_at"] = now
    if notes is not None:
        record["notes"] = clean_payload_text(notes, 2000)

    records[job["url"]] = record
    save_application_records(records)
    return record, None


def app_config() -> dict[str, Any]:
    """Initial config the static frontend fetches to populate the search form."""
    return {
        "default_location": DEFAULT_LOCATION,
        "radius_choices": RADIUS_ORDER,
        "default_radius": DEFAULT_RADIUS,
        "default_min_score": DEFAULT_MIN_SCORE,
    }


def clean_form_value(data: dict[str, list[str]], key: str, default: str) -> str:
    """Return the first posted value for a form key."""
    value = data.get(key, [default])[0].strip()
    return value or default


def validate_form(data: dict[str, list[str]]) -> tuple[dict[str, str], Optional[str]]:
    """Validate browser form input."""
    location = clean_form_value(data, "location", DEFAULT_LOCATION)
    radius = clean_form_value(data, "radius", DEFAULT_RADIUS)
    mode = clean_form_value(data, "mode", DEFAULT_MODE)
    min_score = clean_form_value(data, "min_score", DEFAULT_MIN_SCORE)

    if len(location) > 120:
        return {}, "Location is too long."
    if radius not in RADIUS_CHOICES:
        return {}, "Radius must be one of: 5, 10, 15, 25, 35, 50."
    if mode not in MODE_CHOICES:
        return {}, "Mode must be fast or full."
    try:
        min_score_number = float(min_score)
    except ValueError:
        return {}, "Minimum score must be a number."
    if min_score_number < 0 or min_score_number > 100:
        return {}, "Minimum score must be between 0 and 100."

    return {
        "location": location,
        "radius": radius,
        "mode": mode,
        "min_score": f"{min_score_number:g}",
    }, None


def build_pipeline_command(options: dict[str, str]) -> list[str]:
    """Build the pipeline command for the selected UI options."""
    command = [
        sys.executable,
        str(PIPELINE_SCRIPT),
        "--location",
        options["location"],
        "--indeed-radius",
        options["radius"],
        "--clean-min-score",
        options["min_score"],
        "--preview-count",
        "10",
    ]

    # USAJOBS is intentionally not requested — it returns federal career roles,
    # not local entry-level jobs. Add "--include-usajobs" here if ever wanted.
    if options["mode"] == "fast":
        command.extend(
            [
                "--indeed-pages",
                "1",
                "--indeed-queries",
                *FAST_QUERIES,
            ]
        )
    else:
        command.extend(
            [
                "--indeed-pages",
                "3",
            ]
        )

    return command


def run_pipeline(location: str, command: list[str]) -> None:
    """Run the pipeline in a background thread and stream logs into state."""
    try:
        process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            RUN_STATE.append_log(line)
        returncode = process.wait()
    except Exception as exc:
        RUN_STATE.append_log(f"Server error while running pipeline: {exc}")
        returncode = 1

    RUN_STATE.append_log(
        f"Pipeline {'completed' if returncode == 0 else 'failed'} "
        f"for {location} with exit code {returncode}."
    )
    RUN_STATE.finish(returncode)


class SearchHandler(BaseHTTPRequestHandler):
    """HTTP routes for the local JobFind app."""

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in STATIC_FILES:
            name, content_type = STATIC_FILES[path]
            self.send_static_file(name, content_type)
            return
        if path == "/api/config":
            self.send_json(app_config())
            return
        if path == "/api/status":
            self.send_json(RUN_STATE.snapshot())
            return
        if path == "/api/applicant-profile":
            self.send_json(load_applicant_profile())
            return
        if path == "/api/common-answers":
            self.send_json(common_answers_response())
            return
        if path == "/api/setup-status":
            self.send_json(build_setup_status())
            return
        if path == "/api/applications":
            rows = list(load_application_records().values())
            rows.sort(
                key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""),
                reverse=True,
            )
            self.send_json({"applications": rows})
            return
        if path == "/api/autofill/status":
            payload = AUTOFILL_STATE.snapshot()
            payload["login"] = LOGIN_STATE.snapshot()
            self.send_json(payload)
            return
        if path == "/api/indeed-login/status":
            self.send_json(LOGIN_STATE.snapshot())
            return
        if path == "/jobs_clean.html":
            self.send_results_file()
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/run":
            self.handle_run_request()
            return
        if path == "/api/applicant-profile":
            payload, error = self.read_json_body()
            if error:
                self.send_json({"error": error}, status=400)
                return
            profile, warnings = save_applicant_profile_updates(payload)
            self.send_json({"profile": profile, "warnings": warnings})
            return
        if path == "/api/common-answers":
            payload, error = self.read_json_body()
            if error:
                self.send_json({"error": error}, status=400)
                return
            profile, answers, update_error = save_common_answers_updates(payload)
            if update_error:
                self.send_json({"error": update_error}, status=400)
                return
            self.send_json({"profile": profile, "answers": answers})
            return
        if path == "/api/common-answers/improve":
            payload, error = self.read_json_body()
            if error:
                self.send_json({"error": error}, status=400)
                return
            result, status = improve_common_answer(payload)
            self.send_json(result, status=status)
            return
        if path == "/api/applications/open":
            payload, error = self.read_json_body()
            if error:
                self.send_json({"error": error}, status=400)
                return
            application, update_error = upsert_application(payload, "Opened")
            if update_error:
                self.send_json({"error": update_error}, status=400)
                return
            self.send_json({"application": application})
            return
        if path == "/api/applications/status":
            payload, error = self.read_json_body()
            if error:
                self.send_json({"error": error}, status=400)
                return
            status_value = clean_payload_text(payload.get("status"), 80)
            application, update_error = upsert_application(
                payload,
                status_value,
                notes=payload.get("notes"),
            )
            if update_error:
                self.send_json({"error": update_error}, status=400)
                return
            self.send_json({"application": application})
            return
        if path == "/api/applications/autofill":
            payload, error = self.read_json_body()
            if error:
                self.send_json({"error": error}, status=400)
                return
            self.handle_autofill_request(payload, resume=False)
            return
        if path == "/api/applications/autofill/resume":
            payload, error = self.read_json_body()
            if error:
                self.send_json({"error": error}, status=400)
                return
            self.handle_autofill_request(payload, resume=True)
            return
        if path == "/api/indeed-login/open":
            if latest_login_port() is not None:
                self.send_json({"status": "already_open"})
                return
            if not LOGIN_STATE.start(
                {
                    "title": "Indeed login",
                    "url": application_autofill.INDEED_LOGIN_URL,
                    "source": "Indeed",
                    "company": "Indeed",
                    "score": "",
                }
            ):
                self.send_json({"error": "Indeed login browser is already opening."}, status=409)
                return
            thread = threading.Thread(target=run_open_indeed_login, daemon=True)
            thread.start()
            self.send_json({"status": "started"})
            return
        self.send_error(404, "Not found")

    def handle_autofill_request(self, payload: dict[str, Any], resume: bool = False) -> None:
        """Start or resume an autofill run for a selected job."""
        job = normalize_job_payload(payload)
        if not job["url"].startswith(("http://", "https://")):
            self.send_json({"error": "A valid apply URL is required."}, status=400)
            return
        application, update_error = upsert_application(job, "Opened")
        if update_error:
            self.send_json({"error": update_error}, status=400)
            return
        if not AUTOFILL_STATE.start(job):
            self.send_json({"error": "Autofill is already running."}, status=409)
            return
        thread = threading.Thread(target=run_autofill, args=(job, resume), daemon=True)
        thread.start()
        self.send_json({"status": "started", "application": application, "resume": resume})

    def handle_run_request(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw_body = self.rfile.read(length).decode("utf-8")
        options, error = validate_form(parse_qs(raw_body))
        if error:
            self.send_json({"error": error}, status=400)
            return

        command = build_pipeline_command(options)
        if not RUN_STATE.start(options["location"], command):
            self.send_json({"error": "A search is already running."}, status=409)
            return

        thread = threading.Thread(
            target=run_pipeline,
            args=(options["location"], command),
            daemon=True,
        )
        thread.start()
        self.send_json({"status": "started"})

    def read_json_body(self) -> tuple[dict[str, Any], Optional[str]]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 1_000_000:
            return {}, "Request body is too large."
        raw_body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            return {}, "Request body must be valid JSON."
        if not isinstance(payload, dict):
            return {}, "Request body must be a JSON object."
        return payload, None

    def send_static_file(self, name: str, content_type: str) -> None:
        file_path = STATIC_DIR / name
        if not file_path.is_file():
            self.send_error(404, "Not found")
            return
        content = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def send_results_file(self) -> None:
        if not RESULTS_FILE.exists():
            self.send_text(
                "<!doctype html><title>No Results</title><p>No results generated yet.</p>",
                "text/html; charset=utf-8",
                status=404,
            )
            return
        content = RESULTS_FILE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        content = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def send_text(
        self,
        payload: str,
        content_type: str,
        status: int = 200,
    ) -> None:
        content = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: Any) -> None:
        """Keep server logging compact."""
        sys.stderr.write(f"{self.address_string()} - {format % args}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local JobFind browser app.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), SearchHandler)
    print(f"JobFind local app: http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping JobFind local app.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
