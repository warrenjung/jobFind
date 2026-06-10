"""
Run a local browser UI for refreshing job results.

The server is intentionally localhost-only. It lets a browser form start the
existing pipeline without exposing API keys to the page.
"""

import argparse
import html
import json
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


REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "data"
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


def html_page() -> str:
    """Return the local app shell."""
    location = html.escape(RUN_STATE.snapshot().get("location") or DEFAULT_LOCATION)
    radius_options = "\n".join(
        f'<option value="{radius}"{" selected" if radius == DEFAULT_RADIUS else ""}>{radius} miles</option>'
        for radius in ["5", "10", "15", "25", "35", "50"]
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>JobFind Local Search</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #eef3f4;
      --panel: #ffffff;
      --panel-soft: #f7faf9;
      --text: #16211f;
      --muted: #64716f;
      --line: #d8e0df;
      --line-strong: #c6d0ce;
      --accent: #126b62;
      --accent-strong: #0b4f49;
      --accent-soft: #e8f3f1;
      --warn: #9a4526;
      --shadow: 0 12px 28px rgba(24, 38, 36, 0.08);
      --shadow-soft: 0 4px 14px rgba(24, 38, 36, 0.06);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background:
        linear-gradient(180deg, #f7faf9 0, var(--bg) 360px);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    main {{
      width: min(1240px, calc(100% - 32px));
      margin: 22px auto 36px;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: clamp(26px, 3vw, 34px);
      line-height: 1.1;
      letter-spacing: 0;
    }}
    .lede {{
      margin: 0 0 18px;
      max-width: 680px;
      color: var(--muted);
      font-size: 15px;
    }}
    form {{
      display: grid;
      grid-template-columns: minmax(220px, 1.4fr) repeat(3, minmax(120px, 0.6fr)) auto;
      gap: 10px;
      align-items: end;
      padding: 12px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow-soft);
    }}
    label {{
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    input, select, textarea {{
      width: 100%;
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      color: var(--text);
      background: #ffffff;
      font: inherit;
      outline: none;
      transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
    }}
    input:focus, select:focus, textarea:focus {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(18, 107, 98, 0.14);
    }}
    button {{
      min-height: 40px;
      border: 0;
      border-radius: 6px;
      padding: 8px 16px;
      background: var(--accent);
      color: #ffffff;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      transition: background 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease;
    }}
    button:disabled {{ opacity: 0.55; cursor: wait; }}
    button:hover:not(:disabled) {{
      background: var(--accent-strong);
      box-shadow: 0 6px 14px rgba(18, 107, 98, 0.18);
      transform: translateY(-1px);
    }}
    .status {{
      margin: 14px 0;
      display: grid;
      grid-template-columns: 190px minmax(0, 1fr);
      gap: 12px;
      align-items: stretch;
    }}
    .status-box, .log-box {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      box-shadow: var(--shadow-soft);
    }}
    .status-box {{
      display: grid;
      gap: 6px;
      align-content: start;
    }}
    .status-box strong {{
      display: inline-flex;
      width: fit-content;
      align-items: center;
      min-height: 26px;
      padding: 3px 9px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent-strong);
      font-size: 12px;
      letter-spacing: 0;
    }}
    .status-box span {{
      color: var(--muted);
      font-size: 13px;
    }}
    pre {{
      margin: 0;
      max-height: 170px;
      overflow: auto;
      white-space: pre-wrap;
      color: #243432;
      font-size: 12px;
      line-height: 1.4;
    }}
    .result-actions {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      margin: 12px 0 8px;
      color: var(--muted);
      font-size: 14px;
    }}
    .result-actions a {{
      color: var(--accent-strong);
      font-weight: 700;
      text-decoration: none;
    }}
    .result-actions a:hover {{ text-decoration: underline; }}
    .workspace {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 340px;
      gap: 14px;
      align-items: stretch;
    }}
    iframe {{
      width: 100%;
      height: 100%;
      min-height: 74vh;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      box-shadow: var(--shadow);
    }}
    .apply-panel {{
      position: sticky;
      top: 12px;
      display: grid;
      gap: 12px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      box-shadow: var(--shadow);
    }}
    .apply-panel h2 {{
      margin: 0;
      font-size: 16px;
      line-height: 1.2;
    }}
    .apply-panel p {{
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.4;
    }}
    .apply-panel section {{
      display: grid;
      gap: 9px;
      padding-top: 12px;
      border-top: 1px solid var(--line);
    }}
    .selected-job {{
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-soft);
    }}
    .selected-job strong {{
      display: block;
      margin-bottom: 4px;
    }}
    .assistant-actions {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }}
    .assistant-actions button {{
      padding: 8px 10px;
      font-size: 13px;
    }}
    .assistant-actions button:first-child {{
      grid-column: 1 / -1;
    }}
    .autofill-log {{
      margin: 0;
      max-height: 150px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      background: #f8fbfa;
    }}
    textarea {{ min-height: 74px; resize: vertical; }}
    .section-heading {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }}
    .secondary-button {{
      min-height: 30px;
      padding: 5px 9px;
      border: 1px solid var(--line-strong);
      background: #f4f8f7;
      color: var(--accent-strong);
      font-size: 12px;
    }}
    .secondary-button:hover:not(:disabled), .copy-button:hover:not(:disabled) {{
      background: var(--accent-soft);
      box-shadow: none;
    }}
    .profile-editor {{
      display: grid;
      gap: 9px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: var(--panel-soft);
    }}
    #profile-fields {{
      display: grid;
      gap: 9px;
    }}
    .profile-editor.hidden {{
      display: none;
    }}
    .profile-editor label {{
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    .profile-editor input, .profile-editor textarea {{
      padding: 7px 9px;
      font-size: 13px;
      font-weight: 400;
      text-transform: none;
    }}
    .profile-editor textarea {{
      min-height: 68px;
    }}
    .profile-form-actions {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }}
    .profile-message {{
      margin: 0;
      min-height: 18px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }}
    .profile-list, .application-list {{
      display: grid;
      gap: 7px;
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .profile-item {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 8px;
      background: #fbfcfc;
      font-size: 13px;
    }}
    .profile-item span, .application-list li {{
      overflow-wrap: anywhere;
    }}
    .copy-button {{
      min-height: 28px;
      padding: 4px 8px;
      border: 1px solid var(--line-strong);
      background: #f4f8f7;
      color: var(--accent-strong);
      font-size: 12px;
    }}
    .application-list li {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }}
    @media (max-width: 900px) {{
      form, .status, .workspace {{ grid-template-columns: 1fr; }}
      .apply-panel {{ position: static; }}
      iframe {{ height: 68vh; min-height: 68vh; }}
    }}
    @media (max-width: 560px) {{
      main {{ width: min(100% - 20px, 1240px); margin-top: 16px; }}
      .assistant-actions, .profile-form-actions {{ grid-template-columns: 1fr; }}
      .result-actions {{ align-items: flex-start; flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>JobFind</h1>
    <p class="lede">Run a local search, then view the refreshed job results below.</p>
    <form id="search-form">
      <label>Location
        <input name="location" value="{location}" autocomplete="postal-code" required>
      </label>
      <label>Radius
        <select name="radius">{radius_options}</select>
      </label>
      <label>Mode
        <select name="mode">
          <option value="fast" selected>Fast</option>
          <option value="full">Full</option>
        </select>
      </label>
      <label>Min Score
        <input name="min_score" value="{DEFAULT_MIN_SCORE}" inputmode="numeric">
      </label>
      <button id="run-button" type="submit">Search</button>
    </form>
    <section class="status">
      <div class="status-box">
        <strong id="status-text">Idle</strong>
        <span id="status-detail">Ready to search.</span>
      </div>
      <div class="log-box"><pre id="logs"></pre></div>
    </section>
    <div class="result-actions">
      <span>Latest generated result page</span>
      <a href="/jobs_clean.html" target="_blank" rel="noopener">Open in new tab</a>
    </div>
    <section class="workspace">
      <iframe id="results-frame" src="/jobs_clean.html"></iframe>
      <aside class="apply-panel" aria-label="Apply Assistant">
        <h2>Apply Assistant</h2>
        <p>Use this to open Indeed jobs, copy your saved info, and track progress. You still review and submit every application yourself.</p>
        <div class="selected-job" id="selected-job">
          <strong>No job selected</strong>
          <p>Click Apply Assistant on an Indeed job card.</p>
        </div>
        <label>Notes
          <textarea id="application-notes" placeholder="Anything to remember for this application"></textarea>
        </label>
        <div class="assistant-actions">
          <button type="button" id="indeed-login-button">Open Indeed Login</button>
          <button type="button" id="autofill-button" disabled>Autofill Application</button>
          <button type="button" id="resume-autofill-button" disabled>Resume Autofill</button>
          <button type="button" data-application-status="Applied" disabled>Mark Applied</button>
          <button type="button" data-application-status="Needs follow-up" disabled>Follow Up</button>
          <button type="button" data-application-status="Skipped" disabled>Skip</button>
        </div>
        <section>
          <h2>Autofill</h2>
          <p id="autofill-summary">Select a job to start autofill.</p>
          <pre class="autofill-log" id="autofill-log"></pre>
        </section>
        <section>
          <div class="section-heading">
            <h2>Profile</h2>
            <button class="secondary-button" type="button" id="edit-profile-button">Edit Profile</button>
          </div>
          <p class="profile-message" id="profile-message"></p>
          <form class="profile-editor hidden" id="profile-form">
            <div id="profile-fields"></div>
            <div class="profile-form-actions">
              <button type="submit">Save Profile</button>
              <button class="secondary-button" type="button" id="cancel-profile-button">Cancel</button>
            </div>
          </form>
          <ul class="profile-list" id="profile-list">
            <li class="application-list">Loading local profile...</li>
          </ul>
        </section>
        <section>
          <h2>Recent</h2>
          <ul class="application-list" id="application-list">
            <li>No applications tracked yet.</li>
          </ul>
        </section>
      </aside>
    </section>
  </main>
  <script>
    const form = document.querySelector("#search-form");
    const button = document.querySelector("#run-button");
    const statusText = document.querySelector("#status-text");
    const statusDetail = document.querySelector("#status-detail");
    const logs = document.querySelector("#logs");
    const frame = document.querySelector("#results-frame");
    const selectedJobEl = document.querySelector("#selected-job");
    const notesEl = document.querySelector("#application-notes");
    const profileList = document.querySelector("#profile-list");
    const profileForm = document.querySelector("#profile-form");
    const profileFields = document.querySelector("#profile-fields");
    const profileMessage = document.querySelector("#profile-message");
    const editProfileButton = document.querySelector("#edit-profile-button");
    const cancelProfileButton = document.querySelector("#cancel-profile-button");
    const applicationList = document.querySelector("#application-list");
    const applicationButtons = Array.from(document.querySelectorAll("[data-application-status]"));
    const indeedLoginButton = document.querySelector("#indeed-login-button");
    const autofillButton = document.querySelector("#autofill-button");
    const resumeAutofillButton = document.querySelector("#resume-autofill-button");
    const autofillSummary = document.querySelector("#autofill-summary");
    const autofillLog = document.querySelector("#autofill-log");
    let pollTimer = null;
    let autofillPollTimer = null;
    let selectedJob = null;
    let currentProfile = {{}};
    const profileFieldDefs = [
      {{ key: "name", label: "Name" }},
      {{ key: "first_name", label: "First name" }},
      {{ key: "last_name", label: "Last name" }},
      {{ key: "preferred_name", label: "Preferred name" }},
      {{ key: "email", label: "Email" }},
      {{ key: "phone", label: "Phone" }},
      {{ key: "city", label: "City" }},
      {{ key: "state", label: "State" }},
      {{ key: "school", label: "School" }},
      {{ key: "graduation_year", label: "Graduation year" }},
      {{ key: "education_level", label: "Education level" }},
      {{ key: "availability", label: "Availability", multiline: true }},
      {{ key: "available_start_date", label: "Available start date" }},
      {{ key: "desired_hours", label: "Desired hours" }},
      {{ key: "age_or_work_permit", label: "Age/work permit answer", multiline: true }},
      {{ key: "work_eligibility", label: "Work eligibility" }},
      {{ key: "resume_path", label: "Resume path" }},
      {{ key: "short_intro", label: "Short intro", multiline: true }}
    ];

    async function refreshStatus() {{
      const response = await fetch("/api/status", {{ cache: "no-store" }});
      const data = await response.json();
      statusText.textContent = data.status.toUpperCase();
      statusDetail.textContent = data.location ? `Location: ${{data.location}}` : "Ready to search.";
      logs.textContent = data.logs || "";
      logs.scrollTop = logs.scrollHeight;
      button.disabled = data.status === "running";
      if (data.status === "succeeded") {{
        frame.src = `/jobs_clean.html?ts=${{Date.now()}}`;
      }}
      if (data.status !== "running" && pollTimer) {{
        clearInterval(pollTimer);
        pollTimer = null;
      }}
    }}

    function escapeText(value) {{
      return String(value || "").replace(/[&<>"']/g, char => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }}[char]));
    }}

    function profileValueText(value) {{
      if (value && typeof value === "object") return JSON.stringify(value);
      return String(value || "");
    }}

    function renderProfileForm(profile) {{
      if (!profileFields) return;
      profileFields.innerHTML = profileFieldDefs.map(field => {{
        const value = escapeText(profileValueText(profile[field.key]));
        const input = field.multiline
          ? `<textarea name="${{field.key}}">${{value}}</textarea>`
          : `<input name="${{field.key}}" value="${{value}}">`;
        return `<label>${{escapeText(field.label)}}${{input}}</label>`;
      }}).join("");
    }}

    function showProfileMessage(message, isError = false) {{
      if (!profileMessage) return;
      profileMessage.textContent = message || "";
      profileMessage.style.color = isError ? "#a33b2f" : "var(--muted)";
    }}

    async function loadProfile() {{
      const response = await fetch("/api/applicant-profile", {{ cache: "no-store" }});
      const profile = await response.json();
      currentProfile = profile;
      renderProfileForm(currentProfile);
      const rows = Object.entries(profile).filter(([, value]) => profileValueText(value).trim());
      if (!rows.length) {{
        profileList.innerHTML = "<li>Add your info to applicant_profile.json.</li>";
        return;
      }}
      profileList.innerHTML = rows.map(([key, value]) => `
        <li class="profile-item">
          <span><strong>${{escapeText(key.replaceAll("_", " "))}}</strong><br>${{escapeText(profileValueText(value))}}</span>
          <button class="copy-button" type="button" data-copy="${{escapeText(profileValueText(value))}}">Copy</button>
        </li>
      `).join("");
    }}

    async function saveProfile(event) {{
      event.preventDefault();
      const payload = Object.fromEntries(new FormData(profileForm).entries());
      const response = await fetch("/api/applicant-profile", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(payload)
      }});
      const result = await response.json().catch(() => ({{ error: "Could not save profile." }}));
      if (!response.ok) {{
        showProfileMessage(result.error || "Could not save profile.", true);
        return;
      }}
      currentProfile = result.profile || {{}};
      await loadProfile();
      const warnings = result.warnings || [];
      showProfileMessage(warnings.length ? `Saved. ${{warnings.join(" ")}}` : "Profile saved.");
      if (profileForm) profileForm.classList.add("hidden");
    }}

    async function loadApplications() {{
      const response = await fetch("/api/applications", {{ cache: "no-store" }});
      const payload = await response.json();
      const rows = payload.applications || [];
      applicationList.innerHTML = rows.length
        ? rows.slice(0, 8).map(row => `
            <li><strong>${{escapeText(row.status)}}</strong> · ${{escapeText(row.title || "Untitled job")}}<br>${{escapeText(row.company || "")}}</li>
          `).join("")
        : "<li>No applications tracked yet.</li>";
    }}

    function renderSelectedJob(record) {{
      selectedJob = record;
      applicationButtons.forEach(button => button.disabled = !selectedJob);
      if (autofillButton) autofillButton.disabled = !selectedJob;
      if (!selectedJob) {{
        selectedJobEl.innerHTML = "<strong>No job selected</strong><p>Click Apply Assistant on an Indeed job card.</p>";
        return;
      }}
      selectedJobEl.innerHTML = `
        <strong>${{escapeText(selectedJob.title || "Untitled job")}}</strong>
        <p>${{escapeText(selectedJob.company || "Unknown employer")}} · Score ${{escapeText(selectedJob.score || "")}}</p>
        <p>Status: ${{escapeText(selectedJob.status || "Opened")}}</p>
      `;
      if (notesEl && selectedJob.notes !== undefined) {{
        notesEl.value = selectedJob.notes || "";
      }}
    }}

    async function refreshAutofillStatus() {{
      const response = await fetch("/api/autofill/status", {{ cache: "no-store" }});
      const data = await response.json();
      const report = data.report || {{}};
      const filled = report.filled_count || 0;
      const stages = report.stages || [];
      const stoppedReason = String(report.stopped_reason || "");
      const needsLogin = stages.includes("login_required") || stoppedReason === "login_required" || String(report.status_reason || "").toLowerCase().includes("login");
      const needsVerification = stages.includes("verification_required") || stoppedReason === "verification_required";
      if (autofillSummary) {{
        if (data.status === "running") autofillSummary.textContent = report.current_action ? `Autofill: ${{report.current_action}}.` : "Autofill is running in a visible browser.";
        else if (needsVerification) autofillSummary.textContent = "Verification is required. Handle it manually in Chrome, then click Resume Autofill.";
        else if (needsLogin) autofillSummary.textContent = "Login or verification is required. Log in with Open Indeed Login, then click Resume Autofill.";
        else if (stoppedReason === "stopped_before_submit") autofillSummary.textContent = `Stopped before submit. Filled ${{filled}} fields. Review and submit manually.`;
        else if (stoppedReason === "open_login_first") autofillSummary.textContent = "Open Indeed Login first, log in, then run Autofill Application again.";
        else if (stoppedReason === "navigation_error") autofillSummary.textContent = "Chrome did not open the selected job. Close JobFind Chrome windows, click Open Indeed Login, then try again.";
        else if (stoppedReason === "resume_needs_review") autofillSummary.textContent = "Resume needs review. Fix resume_path or upload manually, then Resume Autofill.";
        else if (stoppedReason === "needs_review") autofillSummary.textContent = "Stopped for review. Check the highlighted fields, then Resume Autofill.";
        else if (data.status === "succeeded") autofillSummary.textContent = `Autofill finished. Filled ${{filled}} fields. Review before submitting.`;
        else if (data.status === "failed") autofillSummary.textContent = report.error || "Autofill failed.";
        else autofillSummary.textContent = selectedJob ? "Ready to autofill the selected job." : "Select a job to start autofill.";
      }}
      if (autofillLog) {{
        const reviewItems = (report.current_step_needs_review || []).map(item => `Needs review: ${{item}}`).join("\\n");
        autofillLog.textContent = [data.logs || "", reviewItems].filter(Boolean).join("\\n");
        autofillLog.scrollTop = autofillLog.scrollHeight;
      }}
      if (autofillButton) autofillButton.disabled = !selectedJob || data.status === "running";
      const canResume = needsLogin || needsVerification || ["needs_review", "resume_needs_review", "no_safe_advance"].includes(stoppedReason);
      if (resumeAutofillButton) resumeAutofillButton.disabled = !selectedJob || data.status === "running" || !canResume;
      if (data.status !== "running" && autofillPollTimer) {{
        clearInterval(autofillPollTimer);
        autofillPollTimer = null;
        await loadApplications();
      }}
    }}

    async function saveApplicationStatus(status) {{
      if (!selectedJob) return;
      const response = await fetch("/api/applications/status", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{
          ...selectedJob,
          status,
          notes: notesEl ? notesEl.value : ""
        }})
      }});
      const payload = await response.json();
      if (!response.ok) {{
        renderSelectedJob({{ ...selectedJob, status: payload.error || "Error" }});
        return;
      }}
      renderSelectedJob(payload.application);
      await loadApplications();
    }}

    async function openApplyAssistant(job) {{
      selectedJob = job;
      renderSelectedJob({{ ...job, status: "Opened" }});
      window.open(job.url, "_blank", "noopener");
      const response = await fetch("/api/applications/open", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(job)
      }});
      const payload = await response.json();
      if (response.ok) {{
        renderSelectedJob(payload.application);
        await loadApplications();
      }}
      await refreshAutofillStatus();
    }}

    async function startAutofill() {{
      return startAutofillRequest(false);
    }}

    async function resumeAutofill() {{
      return startAutofillRequest(true);
    }}

    async function startAutofillRequest(resume) {{
      if (!selectedJob) return;
      const response = await fetch(resume ? "/api/applications/autofill/resume" : "/api/applications/autofill", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(selectedJob)
      }});
      const payload = await response.json();
      if (!response.ok) {{
        if (autofillSummary) autofillSummary.textContent = payload.error || "Could not start autofill.";
        return;
      }}
      await refreshAutofillStatus();
      autofillPollTimer = setInterval(refreshAutofillStatus, 1500);
    }}

    async function openIndeedLogin() {{
      const response = await fetch("/api/indeed-login/open", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{}})
      }});
      const payload = await response.json();
      if (!response.ok) {{
        if (autofillSummary) autofillSummary.textContent = payload.error || "Could not open Indeed login.";
        return;
      }}
      if (autofillSummary) autofillSummary.textContent = payload.status === "already_open"
        ? "Recovered existing JobFind Chrome session. Continue in that Chrome window."
        : "Indeed login browser opened. Log in there, then return here.";
      if (autofillLog) autofillLog.textContent = "Open Indeed Login started. This stores only browser session files under local ignored data/.";
    }}

    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      button.disabled = true;
      const body = new URLSearchParams(new FormData(form));
      const response = await fetch("/api/run", {{
        method: "POST",
        headers: {{ "Content-Type": "application/x-www-form-urlencoded" }},
        body
      }});
      if (!response.ok) {{
        const error = await response.json().catch(() => ({{ error: "Could not start search." }}));
        statusText.textContent = "ERROR";
        statusDetail.textContent = error.error || "Could not start search.";
        button.disabled = false;
        return;
      }}
      await refreshStatus();
      pollTimer = setInterval(refreshStatus, 1500);
    }});

    document.addEventListener("click", async (event) => {{
      const copyButton = event.target.closest("[data-copy]");
      if (copyButton) {{
        await navigator.clipboard.writeText(copyButton.dataset.copy || "");
        copyButton.textContent = "Copied";
        setTimeout(() => {{ copyButton.textContent = "Copy"; }}, 1200);
      }}
    }});

    if (editProfileButton) {{
      editProfileButton.addEventListener("click", () => {{
        renderProfileForm(currentProfile);
        profileForm.classList.toggle("hidden");
        showProfileMessage(profileForm.classList.contains("hidden") ? "" : "Editing local applicant_profile.json.");
      }});
    }}
    if (cancelProfileButton) {{
      cancelProfileButton.addEventListener("click", () => {{
        renderProfileForm(currentProfile);
        profileForm.classList.add("hidden");
        showProfileMessage("");
      }});
    }}
    if (profileForm) profileForm.addEventListener("submit", saveProfile);

    applicationButtons.forEach(button => {{
      button.addEventListener("click", () => saveApplicationStatus(button.dataset.applicationStatus));
    }});
    if (autofillButton) autofillButton.addEventListener("click", startAutofill);
    if (resumeAutofillButton) resumeAutofillButton.addEventListener("click", resumeAutofill);
    if (indeedLoginButton) indeedLoginButton.addEventListener("click", openIndeedLogin);

    window.addEventListener("message", event => {{
      if (!event.data || event.data.type !== "jobfind:apply-assistant") return;
      openApplyAssistant(event.data.job);
    }});

    refreshStatus();
    refreshAutofillStatus();
    loadProfile();
    loadApplications();
  </script>
</body>
</html>
"""


class SearchHandler(BaseHTTPRequestHandler):
    """HTTP routes for the local JobFind app."""

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self.send_text(html_page(), "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self.send_json(RUN_STATE.snapshot())
            return
        if path == "/api/applicant-profile":
            self.send_json(load_applicant_profile())
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
