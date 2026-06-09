"""
Export readable job results from ranked job data.

Default input: data/jobs_ranked.json
Default output: data/jobs_clean.html
"""

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any, Optional


DATA_DIR = Path(__file__).parent.parent / "data"
DEFAULT_INPUT_FILE = DATA_DIR / "jobs_ranked.json"
DEFAULT_OUTPUT_FILE = DATA_DIR / "jobs_clean.html"
NOT_SPECIFIED = "Not specified"
DESCRIPTION_LIMIT = 220
DEFAULT_MIN_SCORE = 50


def clean_text(value: Any) -> str:
    """Normalize generated job values for display."""
    if value is None:
        return NOT_SPECIFIED
    text = str(value).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text if text else NOT_SPECIFIED


def truncate(text: str, max_length: int = DESCRIPTION_LIMIT) -> str:
    """Keep long descriptions readable."""
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3].rstrip()}..."


def clean_description(job: dict[str, Any]) -> str:
    """Return the real job description (USAJOBS); empty for Indeed listings."""
    description = clean_text(job.get("description"))
    if description != NOT_SPECIFIED:
        return truncate(description)
    return NOT_SPECIFIED


def why_it_fits(job: dict[str, Any]) -> str:
    """Return the teen-fit reason.

    Indeed's results page no longer exposes a description snippet, but the
    filter step always records why a listing suits an entry-level candidate.
    """
    return truncate(clean_text(job.get("teen_fit_reason")))


def format_type_schedule(job: dict[str, Any]) -> str:
    """Combine job type and schedule into one display value."""
    job_type = clean_text(job.get("job_type"))
    schedule = clean_text(job.get("schedule"))
    parts = [part for part in (job_type, schedule) if part != NOT_SPECIFIED]
    return " · ".join(dict.fromkeys(parts)) if parts else NOT_SPECIFIED


def format_location(job: dict[str, Any]) -> str:
    """Return a concise city/state for display."""
    city = clean_text(job.get("city"))
    state = clean_text(job.get("state"))
    parts = [part for part in (city, state) if part != NOT_SPECIFIED]
    if parts:
        return ", ".join(parts)
    return clean_text(job.get("location"))


def format_source(job: dict[str, Any]) -> str:
    """Human-friendly label for the job source."""
    source = clean_text(job.get("source")).lower()
    if source == "indeed":
        return "Indeed"
    if source == "usajobs":
        return "USAJOBS"
    return NOT_SPECIFIED


def format_distance(distance: Any) -> str:
    """Format miles from the search location for display."""
    if distance is None:
        return NOT_SPECIFIED
    try:
        return f"{float(distance):.1f} miles"
    except (TypeError, ValueError):
        return clean_text(distance)


def load_jobs(filename: Path) -> list[dict[str, Any]]:
    """Load ranked jobs from JSON."""
    with filename.open("r", encoding="utf-8") as file:
        jobs = json.load(file)
    if not isinstance(jobs, list):
        raise SystemExit(f"{filename} must contain a list of ranked jobs.")
    return [job for job in jobs if isinstance(job, dict)]


def limited_jobs(jobs: list[dict[str, Any]], limit: Optional[int]) -> list[dict[str, Any]]:
    """Apply an optional top-N limit."""
    if limit is None:
        return jobs
    return jobs[:limit]


def job_score(job: dict[str, Any]) -> Optional[float]:
    """Return a numeric student fit score when one is available."""
    value = job.get("student_fit_score")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sorted_by_score(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort jobs by score descending so output is ranked even if input isn't."""
    return sorted(jobs, key=lambda job: (job_score(job) is not None, job_score(job) or 0), reverse=True)


def filtered_jobs(
    jobs: list[dict[str, Any]],
    min_score: Optional[float],
) -> list[dict[str, Any]]:
    """Hide jobs below the visible-score threshold."""
    if min_score is None:
        return jobs
    return [job for job in jobs if (job_score(job) or 0) >= min_score]


def prepared_jobs(
    jobs: list[dict[str, Any]],
    limit: Optional[int],
    min_score: Optional[float],
) -> list[dict[str, Any]]:
    """Filter, sort, and limit jobs for display."""
    return limited_jobs(sorted_by_score(filtered_jobs(jobs, min_score)), limit)


def parse_limit(value: Optional[int]) -> Optional[int]:
    """Validate optional row/card limit."""
    if value is None:
        return None
    if value < 1:
        raise SystemExit("--limit must be greater than 0.")
    return value


def parse_min_score(value: Optional[float]) -> Optional[float]:
    """Validate optional minimum score filter."""
    if value is None:
        return None
    if value < 0 or value > 100:
        raise SystemExit("--min-score must be between 0 and 100.")
    return value


def escape_markdown_table_cell(value: Any) -> str:
    """Escape text that would break a Markdown table row."""
    text = clean_text(value)
    text = text.replace("\\", "\\\\")
    text = text.replace("|", "\\|")
    return text


def format_markdown_apply_link(url: Any) -> str:
    """Return a clickable Markdown apply link when a valid URL exists."""
    text = clean_text(url)
    if text == NOT_SPECIFIED or not text.startswith(("http://", "https://")):
        return NOT_SPECIFIED
    safe_url = text.replace(")", "%29").replace("(", "%28")
    return f"[Apply]({safe_url})"


def build_markdown_table(
    jobs: list[dict[str, Any]],
    limit: Optional[int],
    min_score: Optional[float],
) -> str:
    """Build the clean Markdown table."""
    shown_jobs = prepared_jobs(jobs, limit, min_score)
    headers = [
        "Job",
        "Employer",
        "Source",
        "Location",
        "Type",
        "Why it fits",
        "Pay",
        "Distance",
        "Score",
        "Link to Apply",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]

    for job in shown_jobs:
        # Prefer a real description (USAJOBS); fall back to the fit reason (Indeed).
        highlight = clean_description(job)
        if highlight == NOT_SPECIFIED:
            highlight = why_it_fits(job)
        row = [
            escape_markdown_table_cell(job.get("title")),
            escape_markdown_table_cell(job.get("company")),
            escape_markdown_table_cell(format_source(job)),
            escape_markdown_table_cell(format_location(job)),
            escape_markdown_table_cell(format_type_schedule(job)),
            escape_markdown_table_cell(highlight),
            escape_markdown_table_cell(job.get("pay")),
            escape_markdown_table_cell(format_distance(job.get("distance_miles"))),
            escape_markdown_table_cell(job.get("student_fit_score")),
            format_markdown_apply_link(job.get("url")),
        ]
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines) + "\n"


def html_text(value: Any) -> str:
    """Escape plain text for HTML."""
    return html.escape(clean_text(value), quote=True)


def format_html_apply_link(url: Any) -> str:
    """Return an HTML apply button/link when a valid URL exists."""
    text = clean_text(url)
    if text == NOT_SPECIFIED or not text.startswith(("http://", "https://")):
        return '<span class="missing">Not specified</span>'
    safe_url = html.escape(text, quote=True)
    return f'<a class="apply-link" href="{safe_url}" target="_blank" rel="noopener">Apply</a>'


def summary_text(shown_count: int, min_score: Optional[float], location: Optional[str]) -> str:
    """Return a short summary for the clean output header."""
    where = f" near {location}" if location else ""
    if min_score is None or min_score <= 0:
        return f"{shown_count} jobs{where}, sorted by student fit score."
    return f"{shown_count} jobs{where} rated {min_score:g}+ and sorted by student fit score."


def detail_row(label: str, value: str) -> str:
    """Render one definition row, skipping empty values."""
    if value == NOT_SPECIFIED:
        return ""
    return f"""
          <div>
            <dt>{html.escape(label, quote=True)}</dt>
            <dd>{value}</dd>
          </div>"""


def build_html_cards(
    jobs: list[dict[str, Any]],
    limit: Optional[int],
    min_score: Optional[float],
    location: Optional[str] = None,
) -> str:
    """Build a browser-friendly HTML page with one card per job."""
    shown_jobs = prepared_jobs(jobs, limit, min_score)
    cards = []

    for index, job in enumerate(shown_jobs, start=1):
        title = html_text(job.get("title"))
        source = html_text(format_source(job))
        rows = "".join(
            [
                detail_row("Employer", html_text(job.get("company"))),
                detail_row("Location", html_text(format_location(job))),
                detail_row("Type", html_text(format_type_schedule(job))),
                detail_row("Job Description", html_text(clean_description(job))),
                detail_row("Why it fits", html_text(why_it_fits(job))),
                detail_row("Pay", html_text(job.get("pay"))),
                detail_row("Distance", html_text(format_distance(job.get("distance_miles")))),
                detail_row("Link to Apply", format_html_apply_link(job.get("url"))),
            ]
        )
        source_badge = (
            f'<span class="source">{source}</span>' if source != NOT_SPECIFIED else ""
        )

        cards.append(
            f"""
      <article class="job-card">
        <div class="card-topline">
          <span class="rank">#{index}</span>
          {source_badge}
          <span class="score">Score {html_text(job.get("student_fit_score"))}</span>
        </div>
        <h2>{title}</h2>
        <dl>{rows}
        </dl>
      </article>"""
        )

    if not cards:
        cards.append(
            '\n      <p class="empty">No jobs matched the current filters. '
            'Try lowering the minimum score (MIN_SCORE=0) or widening the search radius.</p>'
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Job Results</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --card: #ffffff;
      --text: #17202a;
      --muted: #5e6b78;
      --line: #d9dee5;
      --accent: #0f6b5f;
      --accent-strong: #0a4f46;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }}

    main {{
      width: min(1080px, calc(100% - 32px));
      margin: 32px auto 48px;
    }}

    header {{
      margin-bottom: 20px;
    }}

    h1 {{
      margin: 0 0 6px;
      font-size: 28px;
      font-weight: 700;
    }}

    .summary {{
      margin: 0;
      color: var(--muted);
      font-size: 15px;
    }}

    .jobs {{
      display: grid;
      gap: 14px;
    }}

    .job-card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
    }}

    .card-topline {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 13px;
    }}

    .score {{
      color: var(--accent-strong);
      font-weight: 700;
      margin-left: auto;
    }}

    .source {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--line);
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}

    .empty {{
      background: var(--card);
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 24px;
      color: var(--muted);
      text-align: center;
    }}

    h2 {{
      margin: 0 0 14px;
      font-size: 20px;
      line-height: 1.25;
    }}

    dl {{
      display: grid;
      gap: 10px;
      margin: 0;
    }}

    dl > div {{
      display: grid;
      grid-template-columns: 150px minmax(0, 1fr);
      gap: 14px;
      padding-top: 10px;
      border-top: 1px solid var(--line);
    }}

    dt {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.02em;
    }}

    dd {{
      margin: 0;
      overflow-wrap: anywhere;
    }}

    .apply-link {{
      display: inline-flex;
      align-items: center;
      min-height: 34px;
      padding: 6px 12px;
      border-radius: 6px;
      background: var(--accent);
      color: #ffffff;
      font-weight: 700;
      text-decoration: none;
    }}

    .apply-link:hover {{
      background: var(--accent-strong);
    }}

    .missing {{
      color: var(--muted);
    }}

    @media (max-width: 640px) {{
      main {{
        width: min(100% - 20px, 1080px);
        margin-top: 20px;
      }}

      dl > div {{
        grid-template-columns: 1fr;
        gap: 4px;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{html.escape(f"Jobs near {location}" if location else "Job Results", quote=True)}</h1>
      <p class="summary">{html.escape(summary_text(len(shown_jobs), min_score, location), quote=True)}</p>
    </header>
    <section class="jobs">
{''.join(cards)}
    </section>
  </main>
</body>
</html>
"""


def infer_output_path(output: Optional[Path], output_format: str) -> Path:
    """Choose the default output file for the selected format."""
    if output is not None:
        return output
    if output_format == "markdown":
        return DATA_DIR / "jobs_clean.md"
    return DEFAULT_OUTPUT_FILE


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export ranked jobs to a clean readable file."
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT_FILE,
        type=Path,
        help=f"Ranked jobs JSON file. Default: {DEFAULT_INPUT_FILE}.",
    )
    parser.add_argument(
        "--output",
        default=None,
        type=Path,
        help=f"Output file. Default: {DEFAULT_OUTPUT_FILE}.",
    )
    parser.add_argument(
        "--format",
        choices=["html", "markdown"],
        default="html",
        help="Output format. Default: html.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional number of top jobs to include.",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=DEFAULT_MIN_SCORE,
        help=(
            "Minimum student fit score to include. "
            f"Default: {DEFAULT_MIN_SCORE}. Use 0 to show all jobs."
        ),
    )
    parser.add_argument(
        "--location",
        default=None,
        help="Search location to show in the page header (e.g. 'Cupertino, CA').",
    )
    args = parser.parse_args()

    jobs = load_jobs(args.input)
    limit = parse_limit(args.limit)
    min_score = parse_min_score(args.min_score)
    output = infer_output_path(args.output, args.format)

    if args.format == "markdown":
        content = build_markdown_table(jobs, limit, min_score)
    else:
        content = build_html_cards(jobs, limit, min_score, args.location)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"Saved clean jobs {args.format} to {output}")


if __name__ == "__main__":
    main()
