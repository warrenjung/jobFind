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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs


REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "data"
PIPELINE_SCRIPT = REPO_ROOT / "pipeline" / "run_job_pipeline.py"
RESULTS_FILE = DATA_DIR / "jobs_clean.html"

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
      --bg: #f5f7f8;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #5e6b78;
      --line: #d8dee6;
      --accent: #0f6b5f;
      --accent-strong: #0a4f46;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      width: min(1180px, calc(100% - 28px));
      margin: 24px auto 36px;
    }}
    h1 {{ margin: 0 0 6px; font-size: 28px; }}
    .lede {{ margin: 0 0 18px; color: var(--muted); }}
    form {{
      display: grid;
      grid-template-columns: minmax(220px, 1.4fr) repeat(3, minmax(120px, 0.6fr)) auto;
      gap: 10px;
      align-items: end;
      padding: 14px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    label {{
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.02em;
    }}
    input, select {{
      width: 100%;
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      color: var(--text);
      background: #ffffff;
      font: inherit;
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
    }}
    button:disabled {{ opacity: 0.6; cursor: wait; }}
    button:hover:not(:disabled) {{ background: var(--accent-strong); }}
    .status {{
      margin: 14px 0;
      display: grid;
      grid-template-columns: 180px minmax(0, 1fr);
      gap: 12px;
      align-items: stretch;
    }}
    .status-box, .log-box {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }}
    .status-box strong {{ display: block; margin-bottom: 6px; }}
    pre {{
      margin: 0;
      max-height: 170px;
      overflow: auto;
      white-space: pre-wrap;
      color: #24313d;
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
    .result-actions a {{ color: var(--accent-strong); font-weight: 700; }}
    iframe {{
      width: 100%;
      height: 72vh;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
    }}
    @media (max-width: 900px) {{
      form, .status {{ grid-template-columns: 1fr; }}
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
    <iframe id="results-frame" src="/jobs_clean.html"></iframe>
  </main>
  <script>
    const form = document.querySelector("#search-form");
    const button = document.querySelector("#run-button");
    const statusText = document.querySelector("#status-text");
    const statusDetail = document.querySelector("#status-detail");
    const logs = document.querySelector("#logs");
    const frame = document.querySelector("#results-frame");
    let pollTimer = null;

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

    refreshStatus();
  </script>
</body>
</html>
"""


class SearchHandler(BaseHTTPRequestHandler):
    """HTTP routes for the local JobFind app."""

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            self.send_text(html_page(), "text/html; charset=utf-8")
            return
        if self.path.startswith("/api/status"):
            self.send_json(RUN_STATE.snapshot())
            return
        if self.path.startswith("/jobs_clean.html"):
            self.send_results_file()
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        if self.path != "/api/run":
            self.send_error(404, "Not found")
            return

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
