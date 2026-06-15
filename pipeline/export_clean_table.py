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

import export_clean_template

DATA_DIR = Path(__file__).parent.parent / "data"
DEFAULT_INPUT_FILE = DATA_DIR / "jobs_ranked.json"
DEFAULT_OUTPUT_FILE = DATA_DIR / "jobs_clean.html"
from utils import NOT_SPECIFIED, clean_text
DESCRIPTION_LIMIT = 220
DEFAULT_MIN_SCORE = 50




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
    if source == "careeronestop":
        return "CareerOneStop"
    return NOT_SPECIFIED


def source_filter_value(job: dict[str, Any]) -> str:
    """Stable source value for client-side filters."""
    source = format_source(job)
    if source == NOT_SPECIFIED:
        return "not-specified"
    return source.lower()


def job_type_filter_value(job: dict[str, Any]) -> str:
    """Bucket job type into the simple filter choices shown in the UI."""
    job_type = clean_text(job.get("job_type")).lower()
    if job_type == NOT_SPECIFIED.lower():
        return "not-specified"
    if "part" in job_type:
        return "part-time"
    if "full" in job_type:
        return "full-time"
    if "temp" in job_type or "seasonal" in job_type:
        return "temporary"
    if "contract" in job_type:
        return "contract"
    if "intern" in job_type:
        return "internship"
    return "not-specified"


def format_distance(distance: Any) -> str:
    """Format miles from the search location for display."""
    if distance is None:
        return NOT_SPECIFIED
    try:
        return f"{float(distance):.1f} miles"
    except (TypeError, ValueError):
        return clean_text(distance)


def sortable_pay(job: dict[str, Any]) -> float:
    """Numeric hourly pay for client-side sorting (0 when unknown)."""
    try:
        return float(job.get("hourly_pay_estimate"))
    except (TypeError, ValueError):
        return 0.0


def has_sortable_pay(job: dict[str, Any]) -> bool:
    """Whether the ranked job has a usable hourly pay estimate."""
    try:
        float(job.get("hourly_pay_estimate"))
        return True
    except (TypeError, ValueError):
        return False


def sortable_distance(job: dict[str, Any]) -> float:
    """Numeric distance for client-side sorting (unknown sinks to the bottom)."""
    try:
        return float(job.get("distance_miles"))
    except (TypeError, ValueError):
        return 1_000_000.0


def has_sortable_distance(job: dict[str, Any]) -> bool:
    """Whether the ranked job has a usable distance value."""
    try:
        float(job.get("distance_miles"))
        return True
    except (TypeError, ValueError):
        return False


def search_blob(job: dict[str, Any]) -> str:
    """Lowercase title + employer + location for the client-side search box."""
    parts = [
        clean_text(job.get("title")),
        clean_text(job.get("company")),
        format_location(job),
    ]
    return " ".join(part for part in parts if part != NOT_SPECIFIED).lower()


def source_filter_options(jobs: list[dict[str, Any]]) -> str:
    """Build source filter options from the sources present in the visible jobs."""
    labels = {
        "indeed": "Indeed",
        "usajobs": "USAJOBS",
        "careeronestop": "CareerOneStop",
        "not-specified": "Not specified",
    }
    preferred_order = ["indeed", "usajobs", "careeronestop", "not-specified"]
    present = {source_filter_value(job) for job in jobs}
    ordered = [value for value in preferred_order if value in present]
    ordered.extend(sorted(present - set(ordered)))
    return "\n".join(
        f'<option value="{html.escape(value, quote=True)}">{html.escape(labels.get(value, value.title()), quote=True)}</option>'
        for value in ordered
    )


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


def format_apply_assistant_button(job: dict[str, Any]) -> str:
    """Return an Apply Assistant button for supported job sources."""
    url = clean_text(job.get("url"))
    if source_filter_value(job) != "indeed":
        return ""
    if url == NOT_SPECIFIED or not url.startswith(("http://", "https://")):
        return ""
    attrs = {
        "url": url,
        "title": clean_text(job.get("title")),
        "company": clean_text(job.get("company")),
        "source": format_source(job),
        "score": clean_text(job.get("student_fit_score")),
    }
    data_attrs = " ".join(
        f'data-{key}="{html.escape(value, quote=True)}"'
        for key, value in attrs.items()
    )
    return f'<button class="assistant-button" type="button" {data_attrs}>Apply Assistant</button>'


def job_url(job: dict[str, Any]) -> str:
    """Return the job apply URL when present."""
    url = clean_text(job.get("url"))
    if url == NOT_SPECIFIED:
        return ""
    return url


def summary_text(
    shown_count: int,
    min_score: Optional[float],
    location: Optional[str],
    fromage: Optional[int] = None,
    generated_at: Optional[str] = None,
) -> str:
    """Return a short summary for the clean output header."""
    where = f" near {location}" if location else ""
    rated = "" if (min_score is None or min_score <= 0) else f" rated {min_score:g}+"
    parts = [f"{shown_count} jobs{where}{rated}, sorted by student fit score."]
    if fromage and fromage > 0:
        parts.append(f"Postings from the last {fromage} days.")
    if generated_at:
        parts.append(f"Generated {generated_at}.")
    return " ".join(parts)


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
    fromage: Optional[int] = None,
    generated_at: Optional[str] = None,
) -> str:
    """Build a browser-friendly HTML page with one card per job."""
    shown_jobs = prepared_jobs(jobs, limit, min_score)
    cards = []

    for index, job in enumerate(shown_jobs, start=1):
        title = html_text(job.get("title"))
        source = html_text(format_source(job))
        assistant_button = format_apply_assistant_button(job)
        rows = "".join(
            [
                detail_row("Employer", html_text(job.get("company"))),
                detail_row("Location", html_text(format_location(job))),
                detail_row("Type", html_text(format_type_schedule(job))),
                detail_row("Job Description", html_text(clean_description(job))),
                detail_row("Why it fits", html_text(why_it_fits(job))),
                detail_row(
                    "Matches your keywords",
                    html_text(", ".join(job.get("matched_personal_keywords") or [])),
                ),
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
      <article class="job-card"
        data-url="{html.escape(job_url(job), quote=True)}"
        data-title="{html.escape(clean_text(job.get("title")), quote=True)}"
        data-company="{html.escape(clean_text(job.get("company")), quote=True)}"
        data-source-label="{html.escape(format_source(job), quote=True)}"
        data-application-status="need-to-apply"
        data-score="{job_score(job) or 0:g}"
        data-pay="{sortable_pay(job):g}"
        data-pay-known="{"1" if has_sortable_pay(job) else "0"}"
        data-distance="{sortable_distance(job):g}"
        data-distance-known="{"1" if has_sortable_distance(job) else "0"}"
        data-job-type="{html.escape(job_type_filter_value(job), quote=True)}"
        data-source="{html.escape(source_filter_value(job), quote=True)}"
        data-text="{html.escape(search_blob(job), quote=True)}">
        <div class="card-topline">
          <span class="rank">#{index}</span>
          {source_badge}
          <span class="application-status need-to-apply">Need to apply</span>
          <span class="score">Score {html_text(job.get("student_fit_score"))}</span>
        </div>
        <h2>{title}</h2>
        <dl>{rows}
        </dl>
        <div class="card-actions">{assistant_button}</div>
      </article>"""
        )

    if not cards:
        cards.append(
            '\n      <p class="empty">No jobs matched the current filters. '
            'Try lowering the minimum score (MIN_SCORE=0) or widening the search radius.</p>'
        )

    generated_at = generated_at or export_clean_template.generated_timestamp()
    header_summary = summary_text(len(shown_jobs), min_score, location, fromage, generated_at)
    header_title = f"Jobs near {location}" if location else "Job Results"
    controls_html = (
        ""
        if not shown_jobs
        else export_clean_template.build_controls_html(source_filter_options(shown_jobs))
    )
    no_match_html = (
        ""
        if not shown_jobs
        else '\n    <p class="no-match" id="no-match" hidden>No jobs match these filters.</p>'
    )
    script_html = "" if not shown_jobs else export_clean_template.PAGE_SCRIPT
    return export_clean_template.render_page(
        header_title=header_title,
        header_summary=header_summary,
        cards_html="".join(cards),
        controls_html=controls_html,
        no_match_html=no_match_html,
        script_html=script_html,
    )


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
    parser.add_argument(
        "--fromage",
        type=int,
        default=None,
        help="Freshness window (days) to show in the page header.",
    )
    args = parser.parse_args()

    jobs = load_jobs(args.input)
    limit = parse_limit(args.limit)
    min_score = parse_min_score(args.min_score)
    output = infer_output_path(args.output, args.format)

    if args.format == "markdown":
        content = build_markdown_table(jobs, limit, min_score)
    else:
        content = build_html_cards(jobs, limit, min_score, args.location, args.fromage)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"Saved clean jobs {args.format} to {output}")


if __name__ == "__main__":
    main()
