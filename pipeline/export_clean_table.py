"""
Export readable job results from ranked job data.

Default input: data/jobs_ranked.json
Default output: data/jobs_clean.html
"""

import argparse
import html
import json
import re
from datetime import datetime
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
      <article class="job-card"
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

    generated_at = generated_at or datetime.now().strftime("%b %d, %Y %I:%M %p")
    header_summary = html.escape(
        summary_text(len(shown_jobs), min_score, location, fromage, generated_at),
        quote=True,
    )
    header_title = html.escape(
        f"Jobs near {location}" if location else "Job Results", quote=True
    )

    # Controls + script are plain strings (regular braces) injected into the
    # f-string template, so no brace-escaping is needed for the JS below.
    controls_html = (
        ""
        if not shown_jobs
        else f"""
    <section class="controls">
      <label>Search
        <input id="job-search" type="search" placeholder="Title, employer, or city">
      </label>
      <label>Min pay
        <input id="min-pay" type="number" min="0" step="0.5" placeholder="$ / hour">
      </label>
      <label>Type
        <select id="job-type">
          <option value="">Any type</option>
          <option value="part-time">Part-time</option>
          <option value="full-time">Full-time</option>
          <option value="temporary">Temporary</option>
          <option value="contract">Contract</option>
          <option value="internship">Internship</option>
          <option value="not-specified">Not specified</option>
        </select>
      </label>
      <label>Source
        <select id="job-source">
          <option value="">Any source</option>
          {source_filter_options(shown_jobs)}
        </select>
      </label>
      <label>Max distance
        <select id="max-distance">
          <option value="">Any distance</option>
          <option value="5">5 miles</option>
          <option value="10">10 miles</option>
          <option value="15">15 miles</option>
          <option value="25">25 miles</option>
          <option value="35">35 miles</option>
          <option value="50">50 miles</option>
        </select>
      </label>
      <label>Sort
        <select id="job-sort">
          <option value="score">Best fit</option>
          <option value="pay">Highest pay</option>
          <option value="distance">Closest</option>
        </select>
      </label>
      <span class="count" id="job-count"></span>
    </section>"""
    )
    no_match_html = (
        ""
        if not shown_jobs
        else '\n    <p class="no-match" id="no-match" hidden>No jobs match these filters.</p>'
    )
    script_html = (
        ""
        if not shown_jobs
        else """
  <script>
    (function () {
      const search = document.getElementById('job-search');
      const minPay = document.getElementById('min-pay');
      const jobType = document.getElementById('job-type');
      const jobSource = document.getElementById('job-source');
      const maxDistance = document.getElementById('max-distance');
      const sortSelect = document.getElementById('job-sort');
      const section = document.querySelector('.jobs');
      const noMatch = document.getElementById('no-match');
      const countEl = document.getElementById('job-count');
      if (!section) return;
      const cards = Array.from(section.querySelectorAll('.job-card'));
      const num = (el, key) => parseFloat(el.getAttribute('data-' + key)) || 0;
      const hasValue = (el, key) => el.getAttribute('data-' + key + '-known') === '1';

      function apply() {
        const q = ((search && search.value) || '').trim().toLowerCase();
        const payValue = parseFloat((minPay && minPay.value) || '');
        const payFilterOn = Number.isFinite(payValue) && payValue > 0;
        const distanceValue = parseFloat((maxDistance && maxDistance.value) || '');
        const distanceFilterOn = Number.isFinite(distanceValue);
        const typeValue = (jobType && jobType.value) || '';
        const sourceValue = (jobSource && jobSource.value) || '';
        let visible = 0;
        cards.forEach(card => {
          const textHit = !q || (card.getAttribute('data-text') || '').includes(q);
          const payHit = !payFilterOn || (hasValue(card, 'pay') && num(card, 'pay') >= payValue);
          const typeHit = !typeValue || card.getAttribute('data-job-type') === typeValue;
          const sourceHit = !sourceValue || card.getAttribute('data-source') === sourceValue;
          const distanceHit = !distanceFilterOn || (
            hasValue(card, 'distance') && num(card, 'distance') <= distanceValue
          );
          const hit = textHit && payHit && typeHit && sourceHit && distanceHit;
          card.hidden = !hit;
          if (hit) visible++;
        });
        const key = sortSelect ? sortSelect.value : 'score';
        const ordered = cards.slice().sort((a, b) => {
          if (key === 'distance') return num(a, 'distance') - num(b, 'distance');
          if (key === 'pay') return num(b, 'pay') - num(a, 'pay');
          return num(b, 'score') - num(a, 'score');
        });
        ordered.forEach(card => section.appendChild(card));
        if (noMatch) noMatch.hidden = visible !== 0;
        if (countEl) countEl.textContent = visible + ' shown';
      }

      if (search) search.addEventListener('input', apply);
      if (minPay) minPay.addEventListener('input', apply);
      if (jobType) jobType.addEventListener('change', apply);
      if (jobSource) jobSource.addEventListener('change', apply);
      if (maxDistance) maxDistance.addEventListener('change', apply);
      if (sortSelect) sortSelect.addEventListener('change', apply);
      apply();
    })();
  </script>"""
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

    .controls {{
      display: grid;
      grid-template-columns: minmax(220px, 1.6fr) repeat(5, minmax(120px, 0.7fr)) auto;
      gap: 10px;
      align-items: end;
      margin: 16px 0 14px;
    }}

    .controls label {{
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.02em;
    }}

    .controls input, .controls select {{
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 10px;
      font: inherit;
      color: var(--text);
      background: #ffffff;
    }}

    .controls .count {{
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
      padding-bottom: 9px;
    }}

    .jobs {{
      display: grid;
      gap: 14px;
    }}

    .no-match {{
      color: var(--muted);
      padding: 16px;
      text-align: center;
    }}
    .no-match[hidden] {{ display: none; }}

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

      .controls {{
        grid-template-columns: 1fr;
      }}

      .controls .count {{
        padding-bottom: 0;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{header_title}</h1>
      <p class="summary">{header_summary}</p>
    </header>{controls_html}
    <section class="jobs">
{''.join(cards)}
    </section>{no_match_html}
  </main>{script_html}
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
