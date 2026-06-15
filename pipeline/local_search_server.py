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
import saved_answers
import server_ai
import server_profile
import utils


REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "data"
STATIC_DIR = Path(__file__).parent / "app_static"
PIPELINE_SCRIPT = REPO_ROOT / "pipeline" / "run_job_pipeline.py"
RESULTS_FILE = DATA_DIR / "jobs_clean.html"
PROFILE_FILE = REPO_ROOT / "applicant_profile.json"
RESUMES_DIR = REPO_ROOT / "resumes"
RESUME_MAX_BYTES = 5_000_000
ALLOWED_RESUME_EXTS = {".pdf", ".doc", ".docx", ".txt", ".rtf"}
APPLICATIONS_FILE = DATA_DIR / "applications_status.json"
SAVED_ANSWERS_FILE = DATA_DIR / "saved_answers.json"
SAVED_ANSWERS_MD_FILE = DATA_DIR / "saved_answers.md"
INDEED_PROFILE_DIR = application_autofill.persistent_profile_path(DATA_DIR, "indeed")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_LOCATION = "Cupertino, CA"
DEFAULT_RADIUS = "10"
DEFAULT_MODE = "fast"
DEFAULT_MIN_SCORE = "50"
MAX_PERSONAL_KEYWORDS_LENGTH = 240
APP_BASE_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"

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
AUTO_OVERLAY_AI_LIMIT = 2
AUTO_OVERLAY_AI_TIMEOUT = 12
APPLICATION_STATUSES = {
    "Not started",
    "Opened",
    "Autofilled",
    "Applied",
    "Skipped",
    "Needs follow-up",
}
PRESERVED_APPLICATION_STATUSES = {
    "Applied",
    "Skipped",
    "Needs follow-up",
}
OVERLAY_MUTATION_ROUTES = {
    "/api/applications/autofill/overlay-resume",
    "/api/saved-answers",
}
OVERLAY_ALLOWED_ORIGIN_PREFIXES = (
    "https://www.indeed.com",
    "https://smartapply.indeed.com",
    "https://apply.indeed.com",
    "http://127.0.0.1",
    "http://localhost",
)

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
    **server_profile.DEFAULT_PROFILE_REQUIRED_LABELS
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
    *server_profile.DEFAULT_COMMON_ANSWER_FIELDS
]
COMMON_ANSWER_ALIASES = {
    **server_profile.DEFAULT_COMMON_ANSWER_ALIASES
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


def jobfind_resource_snapshot(limit: int = 8) -> list[dict[str, Any]]:
    """Return a read-only snapshot of JobFind-related local processes."""
    try:
        output = subprocess.check_output(["ps", "-axo", "pid=,pcpu=,pmem=,command="], text=True)
    except (OSError, subprocess.SubprocessError):
        return []

    rows: list[dict[str, Any]] = []
    markers = (
        "data/browser_profiles/indeed",
        "local_search_server.py",
        "run_job_pipeline.py",
        "scrape_indeed.py",
        "ollama",
        "llama-server",
    )
    for line in output.splitlines():
        if not any(marker in line for marker in markers):
            continue
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        pid, cpu, mem, command = parts
        kind = "other"
        lowered = command.lower()
        if "ollama" in lowered or "llama-server" in lowered:
            kind = "ollama"
        elif "data/browser_profiles/indeed" in command:
            kind = "managed_chrome"
        elif "python" in lowered:
            kind = "python"
        try:
            cpu_value = float(cpu)
            mem_value = float(mem)
        except ValueError:
            cpu_value = 0.0
            mem_value = 0.0
        rows.append(
            {
                "pid": pid,
                "cpu": cpu_value,
                "mem": mem_value,
                "kind": kind,
                "command": command[:220],
            }
        )
    rows.sort(key=lambda row: row["cpu"], reverse=True)
    return rows[:limit]


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
        report = application_autofill.open_indeed_login(INDEED_PROFILE_DIR)
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
        profile = profile_with_saved_answers(load_applicant_profile())
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
        resources = jobfind_resource_snapshot()
        if any(row.get("kind") == "ollama" for row in resources):
            AUTOFILL_STATE.append_log("Ollama is running locally and may slow the laptop while generating suggestions.")
        report = application_autofill.autofill_application(
            job["url"],
            profile,
            headless=False,
            user_data_dir=INDEED_PROFILE_DIR,
            cdp_port=cdp_port,
        )
        enriched_items = enrich_review_items_for_overlay(report, job, profile)
        if application_autofill.inject_review_overlay(
            report.get("_page"),
            report,
            enriched_items,
            job=job,
            app_base_url=APP_BASE_URL,
        ):
            AUTOFILL_STATE.append_log("Added JobFind review helper to the Indeed page.")
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
    return server_profile.read_json_file(path, fallback)


def write_json_file(path: Path, payload: Any) -> None:
    """Write a JSON payload, creating parent directories as needed."""
    server_profile.write_json_file(path, payload)


def load_applicant_profile() -> dict[str, Any]:
    """Load local applicant profile fields for copy/paste assistance."""
    return server_profile.load_profile(PROFILE_FILE, read_json_file)


def load_saved_answers() -> dict[str, Any]:
    """Load local saved-answer memory."""
    return saved_answers.read_saved_answers(SAVED_ANSWERS_FILE)


def profile_with_saved_answers(profile: dict[str, Any]) -> dict[str, Any]:
    """Attach saved answer memory to the profile used by autofill."""
    hydrated = dict(profile)
    hydrated["_saved_answers"] = saved_answers.saved_answer_map(load_saved_answers())
    return hydrated


def save_overlay_answer(payload: dict[str, Any]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Save an accepted/edited overlay answer for future exact-match reuse."""
    return saved_answers.upsert_saved_answer(
        SAVED_ANSWERS_FILE,
        SAVED_ANSWERS_MD_FILE,
        payload,
    )


def update_saved_answer(payload: dict[str, Any]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Update a saved answer from the local manager UI."""
    return saved_answers.update_saved_answer(
        SAVED_ANSWERS_FILE,
        SAVED_ANSWERS_MD_FILE,
        payload,
    )


def delete_saved_answer(payload: dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Delete a saved answer from the local manager UI."""
    return saved_answers.delete_saved_answer(
        SAVED_ANSWERS_FILE,
        SAVED_ANSWERS_MD_FILE,
        payload,
    )


def profile_missing_fields(profile: dict[str, Any]) -> list[str]:
    """Return friendly names for required profile fields that are missing."""
    return server_profile.profile_missing_fields(profile, clean_payload_text, PROFILE_REQUIRED_LABELS)


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
    return server_profile.save_profile_updates(
        PROFILE_FILE,
        payload,
        PROFILE_EDIT_FIELDS,
        clean_payload_text,
        application_autofill.resume_path_issue,
        load_applicant_profile,
        write_json_file,
    )


def common_answer_field_map() -> dict[str, dict[str, str]]:
    """Return metadata for editable common answers."""
    return server_profile.common_answer_field_map(COMMON_ANSWER_FIELDS)


def common_answers_from_profile(profile: dict[str, Any]) -> dict[str, str]:
    """Return common answers from the profile's advanced custom_answers map."""
    return server_profile.common_answers_from_profile(
        profile,
        common_answer_field_map(),
        COMMON_ANSWER_ALIASES,
        clean_payload_text,
    )


def common_answers_payload(payload: dict[str, Any]) -> tuple[dict[str, str], Optional[str]]:
    """Normalize a common-answer save payload."""
    return server_profile.common_answers_payload(payload, common_answer_field_map(), clean_payload_text)


def save_common_answers_updates(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str], Optional[str]]:
    """Save common answers while preserving other custom answers."""
    return server_profile.save_common_answers_updates(
        PROFILE_FILE,
        payload,
        common_answer_field_map(),
        COMMON_ANSWER_ALIASES,
        clean_payload_text,
        load_applicant_profile,
        write_json_file,
    )


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
    return server_ai.ollama_base_url(DEFAULT_OLLAMA_BASE_URL)


def ollama_model() -> str:
    """Return the configured Ollama model name."""
    return server_ai.ollama_model(DEFAULT_OLLAMA_MODEL)


def ollama_available(timeout: float = 0.7) -> bool:
    """Whether the local Ollama API looks reachable."""
    return server_ai.ollama_available(ollama_base_url(), requests, timeout)


def ai_provider_status() -> dict[str, Any]:
    """Return the currently available AI polish provider."""
    return server_ai.ai_provider_status(
        os.getenv("OPENAI_API_KEY"),
        os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        ollama_available(),
        ollama_model(),
        OLLAMA_SETUP_MESSAGE,
    )


def build_ai_polish_prompt(payload: dict[str, Any], profile: dict[str, Any]) -> tuple[str, Optional[str]]:
    """Build a safe prompt for review-first answer improvement."""
    return server_ai.build_ai_polish_prompt(
        payload,
        profile,
        common_answer_field_map(),
        clean_payload_text,
    )


def build_question_suggestion_prompt(payload: dict[str, Any], profile: dict[str, Any]) -> tuple[str, Optional[str]]:
    """Build a safe prompt for a review-field answer suggestion."""
    return server_ai.build_question_suggestion_prompt(
        payload,
        profile,
        common_answers_from_profile(profile),
        common_answer_field_map(),
        clean_payload_text,
        application_autofill.is_sensitive_prompt,
        application_autofill.is_legal_prompt,
    )


def extract_openai_text(payload: dict[str, Any]) -> str:
    """Extract text from a Responses API payload."""
    return server_ai.extract_openai_text(payload)


def extract_ollama_text(payload: dict[str, Any]) -> str:
    """Extract text from an Ollama generate response."""
    return server_ai.extract_ollama_text(payload)


def call_openai_polish(prompt: str) -> tuple[dict[str, str], int]:
    """Call OpenAI for one review-first suggestion."""
    return server_ai.call_openai_polish(
        prompt,
        os.getenv("OPENAI_API_KEY"),
        os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        OPENAI_RESPONSES_URL,
        requests,
    )


def call_ollama_polish(prompt: str, timeout: float = 45) -> tuple[dict[str, str], int]:
    """Call local Ollama for one review-first suggestion."""
    return server_ai.call_ollama_polish(
        prompt,
        ollama_base_url(),
        ollama_model(),
        OLLAMA_SETUP_MESSAGE,
        requests,
        timeout=timeout,
    )


def suggest_with_configured_provider(prompt: str, ollama_timeout: float = 45) -> tuple[dict[str, str], int]:
    """Route a prompt through the configured AI provider order."""
    provider = ai_provider_status()
    if provider["provider"] == "openai":
        return call_openai_polish(prompt)
    if provider["provider"] == "ollama":
        return call_ollama_polish(prompt, timeout=ollama_timeout)
    return {"error": provider["message"], "provider": "none"}, 503


def improve_common_answer(payload: dict[str, Any]) -> tuple[dict[str, str], int]:
    """Return an AI-polished answer suggestion without saving it."""
    prompt, error = build_ai_polish_prompt(payload, load_applicant_profile())
    if error:
        return {"error": error}, 400
    return suggest_with_configured_provider(prompt)


def suggest_application_answer(payload: dict[str, Any], ollama_timeout: float = 45) -> tuple[dict[str, str], int]:
    """Return a review-first answer suggestion for an application question."""
    prompt, error = build_question_suggestion_prompt(payload, load_applicant_profile())
    if error:
        return {"error": error}, 400
    return suggest_with_configured_provider(prompt, ollama_timeout=ollama_timeout)


def common_answer_suggestion_for_item(item: dict[str, Any], profile: dict[str, Any]) -> tuple[str, str]:
    """Return a saved exact answer or Common Answer suggestion for a review item."""
    question = clean_payload_text(item.get("question"), 1000)
    if not question:
        return "", ""
    answer, reason = application_autofill.answer_for_prompt(question, profile)
    if answer and (reason == "saved answer" or reason == "custom answer" or reason.startswith("common answer")):
        return answer, reason
    return "", ""


def enrich_review_items_for_overlay(
    report: dict[str, Any],
    job: dict[str, Any],
    profile: dict[str, Any],
    ai_limit: int = AUTO_OVERLAY_AI_LIMIT,
    ollama_timeout: float = AUTO_OVERLAY_AI_TIMEOUT,
) -> list[dict[str, Any]]:
    """Add copy-ready suggestions to review items before injecting the page overlay."""
    items = report.get("current_step_review_items") or report.get("review_items") or []
    if not isinstance(items, list):
        return []

    ai_calls = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        common_answer, common_reason = common_answer_suggestion_for_item(item, profile)
        if common_answer:
            item["suggestion"] = common_answer
            item["suggestion_source"] = common_reason
            continue
        if not item.get("suggestable"):
            continue
        if clean_payload_text(item.get("kind"), 80).lower() != "text":
            continue
        if ai_calls >= ai_limit:
            item["suggestion_error"] = "AI suggestions were limited to keep JobFind responsive."
            continue
        ai_calls += 1
        payload, status = suggest_application_answer(
            {
                "question": item.get("question"),
                "review_item": item,
                "job": job,
            },
            ollama_timeout=ollama_timeout,
        )
        if status == 200 and payload.get("suggestion"):
            item["suggestion"] = payload["suggestion"]
            item["suggestion_source"] = payload.get("provider") or "ai"
            if payload.get("model"):
                item["suggestion_model"] = payload["model"]
        elif payload.get("error"):
            item["suggestion_error"] = payload["error"]
    return items


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


def application_queue_group(status: Any) -> str:
    """Return the dashboard queue group for an application status."""
    value = str(status or "").strip()
    if value == "Applied":
        return "done"
    if value == "Skipped":
        return "skipped"
    if value == "Needs follow-up":
        return "follow"
    if value in {"Opened", "Autofilled"}:
        return "progress"
    return "next"


def group_application_records(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group application rows into the dashboard queue buckets."""
    grouped = {key: [] for key in ("next", "progress", "follow", "done", "skipped")}
    for row in rows:
        if isinstance(row, dict):
            grouped[application_queue_group(row.get("status"))].append(row)
    return grouped


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
    if status == "Opened" and record.get("status") in PRESERVED_APPLICATION_STATUSES:
        record["updated_at"] = now
        if notes is not None:
            record["notes"] = clean_payload_text(notes, 2000)
        records[job["url"]] = record
        save_application_records(records)
        return record, None
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
    personal_keywords = clean_form_value(data, "personal_keywords", "")

    if len(location) > 120:
        return {}, "Location is too long."
    if len(personal_keywords) > MAX_PERSONAL_KEYWORDS_LENGTH:
        return {}, "Preferred job keywords are too long."
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
        "personal_keywords": personal_keywords,
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
    if options.get("personal_keywords"):
        command.extend(["--personal-keywords", options["personal_keywords"]])

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

    def do_OPTIONS(self) -> None:
        path = urlparse(self.path).path
        if path in OVERLAY_MUTATION_ROUTES:
            self.send_response(204)
            self.add_overlay_cors_headers()
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            return
        self.send_error(404, "Not found")

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
        if path == "/api/saved-answers":
            self.send_json(load_saved_answers())
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
            payload = self.json_body()
            if payload is None:
                return
            profile, warnings = save_applicant_profile_updates(payload)
            self.send_json({"profile": profile, "warnings": warnings})
            return
        if path == "/api/applicant-profile/upload-resume":
            self.handle_resume_upload()
            return
        if path == "/api/common-answers":
            payload = self.json_body()
            if payload is None:
                return
            profile, answers, update_error = save_common_answers_updates(payload)
            if update_error:
                self.send_json({"error": update_error}, status=400)
                return
            self.send_json({"profile": profile, "answers": answers})
            return
        if path == "/api/common-answers/improve":
            payload = self.json_body()
            if payload is None:
                return
            result, status = improve_common_answer(payload)
            self.send_json(result, status=status)
            return
        if path == "/api/application-questions/suggest":
            payload = self.json_body()
            if payload is None:
                return
            result, status = suggest_application_answer(payload)
            self.send_json(result, status=status)
            return
        if path == "/api/saved-answers":
            payload = self.json_body()
            if payload is None:
                return
            saved, save_error = save_overlay_answer(payload)
            if save_error:
                self.send_json({"error": save_error}, status=400)
                return
            self.send_json({"saved_answer": saved})
            return
        if path == "/api/saved-answers/update":
            payload = self.json_body()
            if payload is None:
                return
            saved, update_error = update_saved_answer(payload)
            if update_error:
                self.send_json({"error": update_error}, status=400)
                return
            self.send_json({"saved_answer": saved})
            return
        if path == "/api/saved-answers/delete":
            payload = self.json_body()
            if payload is None:
                return
            deleted, delete_error = delete_saved_answer(payload)
            if delete_error:
                self.send_json({"error": delete_error}, status=400)
                return
            self.send_json({"deleted": deleted})
            return
        if path == "/api/applications/open":
            payload = self.json_body()
            if payload is None:
                return
            application, update_error = upsert_application(payload, "Opened")
            if update_error:
                self.send_json({"error": update_error}, status=400)
                return
            self.send_json({"application": application})
            return
        if path == "/api/applications/status":
            payload = self.json_body()
            if payload is None:
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
            payload = self.json_body()
            if payload is None:
                return
            self.handle_autofill_request(payload, resume=False)
            return
        if path == "/api/applications/autofill/resume":
            payload = self.json_body()
            if payload is None:
                return
            self.handle_autofill_request(payload, resume=True)
            return
        if path == "/api/applications/autofill/overlay-resume":
            payload = self.json_body()
            if payload is None:
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
        try:
            raw_body = self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError:
            return {}, "Request body must be UTF-8 encoded."
        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            return {}, "Request body must be valid JSON."
        if not isinstance(payload, dict):
            return {}, "Request body must be a JSON object."
        return payload, None

    def json_body(self) -> Optional[dict[str, Any]]:
        """Return the parsed JSON body, or None after sending a 400 on error."""
        payload, error = self.read_json_body()
        if error:
            self.send_json({"error": error}, status=400)
            return None
        return payload

    def read_binary_body(self, max_bytes: int) -> tuple[bytes, Optional[str]]:
        """Read a raw binary request body (e.g. an uploaded file)."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return b"", "Request body is empty."
        if length > max_bytes:
            return b"", "Uploaded file is too large."
        data = self.rfile.read(length)
        if not data:
            return b"", "Request body is empty."
        return data, None

    def handle_resume_upload(self) -> None:
        """Save an uploaded resume file and point the profile at it."""
        query = parse_qs(urlparse(self.path).query)
        raw_name = (query.get("filename") or [""])[0]
        filename = utils.safe_resume_filename(raw_name, ALLOWED_RESUME_EXTS)
        if not filename:
            self.send_json(
                {"error": "Unsupported file. Use PDF, DOC, DOCX, TXT, or RTF."},
                status=400,
            )
            return
        data, error = self.read_binary_body(RESUME_MAX_BYTES)
        if error:
            self.send_json({"error": error}, status=400)
            return
        destination = RESUMES_DIR / filename
        utils.atomic_write_bytes(destination, data)
        resume_path = str(destination.resolve())
        profile, warnings = save_applicant_profile_updates({"resume_path": resume_path})
        self.send_json(
            {
                "profile": profile,
                "resume_path": resume_path,
                "resume_filename": filename,
                "warnings": warnings,
            }
        )

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
        self.add_overlay_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def add_overlay_cors_headers(self) -> None:
        """Allow only the injected Indeed overlay to call narrow helper endpoints."""
        path = urlparse(self.path).path
        if path not in OVERLAY_MUTATION_ROUTES:
            return
        origin = self.headers.get("Origin", "")
        if any(origin.startswith(prefix) for prefix in OVERLAY_ALLOWED_ORIGIN_PREFIXES):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

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
    global APP_BASE_URL
    parser = argparse.ArgumentParser(description="Run the local JobFind browser app.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    APP_BASE_URL = f"http://{args.host}:{args.port}"
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
