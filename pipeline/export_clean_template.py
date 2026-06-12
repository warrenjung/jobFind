"""HTML template helpers for clean job results output."""

from __future__ import annotations

import html
from datetime import datetime


PAGE_CSS = """
    :root {
      color-scheme: light;
      --bg: #eef3f4;
      --card: #ffffff;
      --card-soft: #f7faf9;
      --text: #16211f;
      --muted: #64716f;
      --line: #d8e0df;
      --line-strong: #c6d0ce;
      --accent: #126b62;
      --accent-strong: #0b4f49;
      --accent-soft: #e8f3f1;
      --shadow: 0 10px 24px rgba(24, 38, 36, 0.08);
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      background:
        linear-gradient(180deg, #f7faf9 0, var(--bg) 310px);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }

    main {
      width: min(1080px, calc(100% - 28px));
      margin: 24px auto 42px;
      min-width: 0;
    }

    header {
      margin-bottom: 16px;
    }

    h1 {
      margin: 0 0 6px;
      font-size: clamp(24px, 3vw, 32px);
      font-weight: 700;
      line-height: 1.12;
      letter-spacing: 0;
    }

    .summary {
      margin: 0;
      color: var(--muted);
      font-size: 15px;
    }

    .controls {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
      gap: 10px;
      align-items: end;
      margin: 16px 0 16px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--card);
      box-shadow: 0 4px 14px rgba(24, 38, 36, 0.06);
      min-width: 0;
    }

    .controls label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
      min-width: 0;
    }

    .controls label:first-child {
      grid-column: span 2;
    }

    .controls input, .controls select {
      width: 100%;
      min-width: 0;
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      font: inherit;
      color: var(--text);
      background: #ffffff;
      outline: none;
      transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }

    .controls input:focus, .controls select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(18, 107, 98, 0.14);
    }

    .controls .count {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 32px;
      padding: 4px 10px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent-strong);
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
      align-self: end;
    }

    .jobs {
      display: grid;
      gap: 12px;
      min-width: 0;
    }

    .no-match {
      color: var(--muted);
      padding: 16px;
      text-align: center;
    }
    .no-match[hidden] { display: none; }

    .job-card {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      box-shadow: var(--shadow);
      min-width: 0;
    }

    .card-topline {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 10px;
      color: var(--muted);
      font-size: 13px;
    }

    .rank {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 2px 8px;
      border-radius: 999px;
      background: #eef2f1;
      color: var(--text);
      font-weight: 700;
    }

    .score {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent-strong);
      font-weight: 700;
      margin-left: auto;
    }

    .source {
      display: inline-block;
      padding: 3px 8px;
      border-radius: 999px;
      background: #eef2f1;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
    }

    .empty {
      background: var(--card);
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 24px;
      color: var(--muted);
      text-align: center;
    }

    h2 {
      margin: 0 0 14px;
      font-size: 19px;
      line-height: 1.25;
      letter-spacing: 0;
      overflow-wrap: anywhere;
    }

    dl {
      display: grid;
      gap: 8px;
      margin: 0;
      min-width: 0;
    }

    dl > div {
      display: grid;
      grid-template-columns: 132px minmax(0, 1fr);
      gap: 12px;
      padding-top: 8px;
      border-top: 1px solid var(--line);
      min-width: 0;
    }

    .card-actions {
      display: flex;
      justify-content: flex-end;
      margin-top: 14px;
    }

    .card-actions:empty {
      display: none;
    }

    dt {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
    }

    dd {
      margin: 0;
      overflow-wrap: anywhere;
    }

    .apply-link {
      display: inline-flex;
      align-items: center;
      min-height: 34px;
      padding: 7px 13px;
      border-radius: 6px;
      background: var(--accent);
      color: #ffffff;
      font-weight: 700;
      text-decoration: none;
      transition: background 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease;
    }

    .apply-link:hover {
      background: var(--accent-strong);
      box-shadow: 0 6px 14px rgba(18, 107, 98, 0.18);
      transform: translateY(-1px);
    }

    .assistant-button {
      min-height: 34px;
      border: 1px solid var(--line-strong);
      border-radius: 6px;
      padding: 7px 13px;
      background: #f4f8f7;
      color: var(--accent-strong);
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      transition: background 0.15s ease, border-color 0.15s ease;
    }

    .assistant-button:hover {
      border-color: var(--accent);
      background: var(--accent-soft);
    }

    .missing {
      color: var(--muted);
    }

    @media (max-width: 640px) {
      main {
        width: min(100% - 20px, 1080px);
        margin-top: 20px;
      }

      dl > div {
        grid-template-columns: 1fr;
        gap: 4px;
      }

      .controls {
        grid-template-columns: 1fr;
      }

      .controls label:first-child {
        grid-column: auto;
      }

      .controls .count {
        justify-content: flex-start;
        width: fit-content;
      }
    }
"""

PAGE_SCRIPT = """
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
      const assistantButtons = Array.from(document.querySelectorAll('.assistant-button'));
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
      assistantButtons.forEach(button => {
        button.addEventListener('click', () => {
          const job = {
            url: button.dataset.url || '',
            title: button.dataset.title || '',
            company: button.dataset.company || '',
            source: button.dataset.source || '',
            score: button.dataset.score || ''
          };
          if (window.parent && window.parent !== window) {
            window.parent.postMessage({ type: 'jobfind:apply-assistant', job }, window.location.origin);
          } else if (job.url) {
            window.open(job.url, '_blank', 'noopener');
          }
        });
      });
      apply();
    })();
  </script>"""


def build_controls_html(source_options_html: str) -> str:
    """Return the filter/sort control toolbar."""
    return f"""
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
          {source_options_html}
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


def render_page(
    *,
    header_title: str,
    header_summary: str,
    cards_html: str,
    controls_html: str,
    no_match_html: str,
    script_html: str,
) -> str:
    """Return the full HTML page for clean results."""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Job Results</title>
  <style>{PAGE_CSS}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{html.escape(header_title, quote=True)}</h1>
      <p class="summary">{html.escape(header_summary, quote=True)}</p>
    </header>{controls_html}
    <section class="jobs">
{cards_html}
    </section>{no_match_html}
  </main>{script_html}
</body>
</html>
"""


def generated_timestamp() -> str:
    """Return the default timestamp string for page summaries."""
    return datetime.now().strftime("%b %d, %Y %I:%M %p")
