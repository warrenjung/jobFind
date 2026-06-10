"""
Scrape entry-level Indeed job postings using a headless Playwright browser.

Outputs a JSON file in the format expected by indeed-job-scrape/scripts/build_csv.py.

Usage:
    python3 scrape_indeed.py --location "Cupertino, CA" --output jobs_scraped.json
    python3 scrape_indeed.py --location "Cupertino, CA" --pages 5 --radius 15

Requires:
    pip install playwright
    playwright install chromium
"""

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

DATA_DIR = Path(__file__).parent.parent / "data"

# Teen-friendly search queries — broad coverage, no degree/license needed
SEARCH_QUERIES = [
    "cashier",
    "retail associate",
    "food service",
    "barista",
    "warehouse",
    "customer service",
    "camp counselor",
    "lifeguard",
    "entry level no experience",
]

NOT_SPECIFIED = "Not specified"

# Rotated per query so consecutive searches don't share one fingerprint.
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_1) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


def make_search_url(
    query: str, location: str, radius: int, start: int, fromage: int = 14
) -> str:
    url = (
        f"https://www.indeed.com/jobs"
        f"?q={quote_plus(query)}"
        f"&l={quote_plus(location)}"
        f"&radius={radius}"
        f"&start={start}"
    )
    # fromage limits to postings from the last N days; 0 means no limit.
    if fromage and fromage > 0:
        url += f"&fromage={fromage}"
    return url


def freshness_label(fromage: int) -> str:
    """Human-readable freshness for a scraped listing (Indeed cards have no date)."""
    if fromage and fromage > 0:
        return f"Within last {fromage} days"
    return NOT_SPECIFIED


def parse_cards(page_text: str, location: str) -> list[dict]:
    """
    Fallback parser for plain page text.

    The live scraper uses DOM extraction in scrape_with_playwright(), which is
    more reliable. Keep this only as a debugging fallback if selectors break.
    """
    cards = []
    # Split on job key links to find individual cards
    # The page text won't have URLs, so we use heuristics on the raw text
    lines = [l.strip() for l in page_text.splitlines() if l.strip()]
    i = 0
    while i < len(lines):
        # Heuristic: a title line is followed by company / location block
        # We look for lines that look like job titles (mixed case, not too long)
        line = lines[i]
        if is_likely_title(line) and i + 2 < len(lines):
            title = line
            company = lines[i + 1] if i + 1 < len(lines) else NOT_SPECIFIED
            loc = lines[i + 2] if i + 2 < len(lines) else location
            pay = NOT_SPECIFIED
            job_type = NOT_SPECIFIED
            schedule = NOT_SPECIFIED
            snippet_parts = []
            posted = NOT_SPECIFIED
            j = i + 3
            while j < len(lines) and j < i + 12:
                l2 = lines[j]
                if is_pay_line(l2):
                    pay = l2
                elif is_job_type(l2):
                    job_type = l2
                elif is_schedule(l2):
                    schedule = l2
                elif is_posted_line(l2):
                    posted = l2
                    j += 1
                    break
                else:
                    snippet_parts.append(l2)
                j += 1
            snippet = " ".join(snippet_parts)[:300] if snippet_parts else NOT_SPECIFIED
            cards.append({
                "title": title,
                "company": company,
                "location": loc,
                "pay": pay,
                "job_type": job_type,
                "schedule_hours": schedule,
                "date_posted": posted,
                "description_snippet": snippet,
                "job_url": NOT_SPECIFIED,  # will be enriched from DOM
            })
            i = j
        else:
            i += 1
    return cards


def is_likely_title(line: str) -> bool:
    if len(line) > 80 or len(line) < 3:
        return False
    # Titles are typically title-cased or mixed-case, not all-caps sentences
    if line.isupper() and len(line) > 20:
        return False
    if line.startswith(("$", "http", "Posted", "Apply", "Easy", "New", "Hiring")):
        return False
    return bool(re.match(r"[A-Z]", line))


# Matches a real pay phrase, e.g. "$18.70 an hour", "$21 - $25 an hour",
# "From $18.45 an hour", "$50,000 - $60,000 a year". A pay-unit suffix is
# required so stray "$" tokens inside CSS/JS/config blobs are not mistaken
# for pay.
PAY_RE = re.compile(
    r"(?:from\s+|up\s+to\s+|starting\s+at\s+)?"
    r"\$\d[\d,]*(?:\.\d{1,2})?"
    r"(?:\s*[-–to]+\s*\$?\d[\d,]*(?:\.\d{1,2})?)?"
    r"\s*(?:an?|per|/)\s*(?:hour|hr|year|yr|week|wk|day|month|mo)",
    re.IGNORECASE,
)

# Characters/markers that signal the text is markup or injected config, not a
# human-readable pay chip.
_MARKUP_MARKERS = ("{", "}", "<", ">", "mosaic", "function", "px", "@media")


def looks_like_markup(text: str) -> bool:
    lowered = text.lower()
    return len(text) > 80 or any(marker in lowered for marker in _MARKUP_MARKERS)


def extract_pay(text: str) -> Optional[str]:
    """Return a clean pay phrase from text, or None if none is present."""
    if not text or looks_like_markup(text):
        return None
    match = PAY_RE.search(text)
    return match.group(0).strip() if match else None


def is_pay_line(line: str) -> bool:
    return extract_pay(line) is not None


def is_job_type(line: str) -> bool:
    keywords = ("full-time", "part-time", "contract", "temporary", "internship")
    return any(k in line.lower() for k in keywords)


def is_schedule(line: str) -> bool:
    keywords = (
        "morning",
        "evening",
        "weekend",
        "night",
        "shift",
        "flexible",
        "hour",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )
    return any(k in line.lower() for k in keywords)


def is_posted_line(line: str) -> bool:
    return bool(re.search(r"(just posted|\d+ days? ago|\d+ hours? ago)", line, re.I))


def clean_text(value: Optional[str]) -> str:
    """Normalize browser text into one-line CSV/JSON-safe text."""
    if not value:
        return NOT_SPECIFIED
    text = value.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or NOT_SPECIFIED


def classify_attributes(attributes: list[str]) -> tuple[str, str, str]:
    """Split Indeed metadata chips into pay, job type, and schedule fields."""
    pay = NOT_SPECIFIED
    job_type = NOT_SPECIFIED
    schedules = []

    for raw_attribute in attributes:
        attribute = clean_text(raw_attribute)
        if attribute == NOT_SPECIFIED or looks_like_markup(attribute):
            continue
        pay_phrase = extract_pay(attribute)
        if pay == NOT_SPECIFIED and pay_phrase:
            pay = pay_phrase
        elif job_type == NOT_SPECIFIED and is_job_type(attribute):
            job_type = attribute
        elif is_schedule(attribute):
            schedules.append(attribute)

    schedule = "; ".join(dict.fromkeys(schedules)) if schedules else NOT_SPECIFIED
    return pay, job_type, schedule


def dedupe_key(job_url: str, title: str, company: str, location: str) -> str:
    """Prefer a real Indeed URL; fall back to title/company/location."""
    if job_url != NOT_SPECIFIED:
        return job_url
    return "|".join(part.lower() for part in (title, company, location))


# Extracts one record per Indeed result card. Kept as a constant so the scraper
# loop stays readable and the JS can be reused by the retry helper.
CARD_EXTRACTION_JS = """
() => {
    const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
    const firstText = (el, selectors) => {
        for (const selector of selectors) {
            const found = el.querySelector(selector);
            const text = clean(found ? (found.innerText || found.textContent || '') : '');
            if (text) {
                return text;
            }
        }
        return '';
    };
    const cards = [];
    document.querySelectorAll('.job_seen_beacon').forEach(el => {
        const jkEl = el.querySelector('a[data-jk]');
        const jk = jkEl ? jkEl.getAttribute('data-jk') : '';
        const postedEl = el.querySelector('[data-testid="myJobsStateDate"], .date');
        const attributes = Array.from(
            el.querySelectorAll('[data-testid="attribute_snippet_testid"], .salary-snippet-container, .metadata.salary-snippet')
        ).map(attr => clean(attr.innerText || attr.textContent || ''))
         .filter(Boolean);

        cards.push({
            jk: jk,
            title: firstText(el, ['span[title]', 'a.jcs-JobTitle span', 'a[data-jk]']),
            company: firstText(el, ['[data-testid="company-name"]']),
            location: firstText(el, ['[data-testid="text-location"]']),
            attributes: Array.from(new Set(attributes)),
            snippet: firstText(el, [
                '[data-testid="belowJobSnippet"]',
                '[data-testid="job-snippet"]',
                '.job-snippet'
            ]),
            posted: clean(postedEl ? (postedEl.innerText || postedEl.textContent || '') : ''),
            cardText: clean(el.innerText || el.textContent || ''),
        });
    });
    return cards;
}
"""


def scrape_with_playwright(
    location: str, radius: int, pages: int, queries: list[str], fromage: int = 14
) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print("ERROR: playwright is not installed. Run: pip install playwright && playwright install chromium", file=sys.stderr)
        sys.exit(1)

    all_jobs: list[dict] = []
    seen_urls: set[str] = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        # Indeed soft-blocks a reused context after the first page load, so each
        # query gets a fresh context (clean cookies + rotated user-agent) and the
        # queries are spaced out. This is what lets all queries return results
        # instead of only the first one.
        for query_index, query in enumerate(queries):
            print(f"  Searching: {query!r} near {location}")
            context = browser.new_context(
                user_agent=USER_AGENTS[query_index % len(USER_AGENTS)],
                viewport={"width": 1280, "height": 900},
            )

            def fetch_cards(target_url: str) -> tuple[list, str]:
                """Navigate and extract cards; status is 'ok'/'timeout'/'challenge'."""
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
                except PWTimeout:
                    return [], "timeout"
                time.sleep(random.uniform(1.5, 3.0))
                content = page.content()
                if "cf-challenge" in content or "unusual traffic" in content.lower():
                    return [], "challenge"
                return page.evaluate(CARD_EXTRACTION_JS), "ok"

            try:
                page = context.new_page()

                for page_num in range(pages):
                    start = page_num * 10
                    url = make_search_url(query, location, radius, start, fromage)
                    cards, status = fetch_cards(url)

                    # The soft block is intermittent: retry an empty/blocked first
                    # page once after a longer back-off before giving up.
                    if page_num == 0 and (status != "ok" or not cards):
                        print(f"    First page empty ({status}); retrying once after backoff...")
                        time.sleep(random.uniform(8.0, 12.0))
                        cards, status = fetch_cards(url)

                    if status == "timeout":
                        print(f"    Timeout on page {page_num + 1}, skipping this query")
                        break
                    if status == "challenge":
                        print("    Bot challenge detected — skipping this query")
                        break
                    if not cards:
                        print(f"    No cards found on page {page_num + 1}, stopping this query")
                        break

                    for c in cards:
                        if not c.get("title"):
                            continue
                        title = clean_text(c.get("title"))
                        company = clean_text(c.get("company"))
                        job_location = clean_text(c.get("location")) if c.get("location") else location
                        job_url = f"https://www.indeed.com/viewjob?jk={c['jk']}" if c.get("jk") else NOT_SPECIFIED
                        key = dedupe_key(job_url, title, company, job_location)
                        if key in seen_urls:
                            continue
                        seen_urls.add(key)

                        pay, job_type, schedule = classify_attributes(c.get("attributes") or [])
                        snippet = clean_text(c.get("snippet"))
                        card_text = clean_text(c.get("cardText"))
                        search_text = " ".join(
                            text for text in (snippet, card_text) if text != NOT_SPECIFIED
                        ).lower()
                        for jt in ("full-time", "part-time", "temporary", "contract", "internship"):
                            if job_type == NOT_SPECIFIED and jt in search_text:
                                job_type = jt.title()
                                break
                        for sc in ("evenings", "weekends", "mornings", "nights", "flexible", "shift"):
                            if schedule == NOT_SPECIFIED and sc in search_text:
                                schedule = sc.title()
                                break

                        # Indeed result cards no longer carry a posting date, so
                        # fall back to the freshness window the search guarantees.
                        posted = clean_text(c.get("posted"))
                        if posted == NOT_SPECIFIED:
                            posted = freshness_label(fromage)

                        all_jobs.append({
                            "title": title,
                            "company": company,
                            "location": job_location,
                            "pay": pay,
                            "job_type": job_type,
                            "schedule_hours": schedule,
                            "date_posted": posted,
                            "description_snippet": snippet[:300],
                            "job_url": job_url,
                            "search_query": query,
                            "search_page": page_num + 1,
                            "search_start": start,
                        })

                    print(f"    Page {page_num + 1}: {len(cards)} cards ({len(all_jobs)} total so far)")
                    time.sleep(random.uniform(1.0, 2.5))
            finally:
                context.close()

            # Space queries apart to avoid Indeed's soft block (skip after last).
            if query_index < len(queries) - 1:
                time.sleep(random.uniform(5.0, 9.0))

        browser.close()

    return all_jobs


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Indeed for entry-level jobs.")
    parser.add_argument("--location", default="Cupertino, CA", help="City, State")
    parser.add_argument("--radius", type=int, default=10, choices=[0, 5, 10, 15, 25, 35, 50])
    parser.add_argument("--pages", type=int, default=3, help="Pages per search query (10 results each)")
    parser.add_argument("--output", default=str(DATA_DIR / "jobs_scraped.json"), help="Output JSON file")
    parser.add_argument(
        "--queries",
        nargs="+",
        default=None,
        help="Search queries (default: built-in teen-friendly list)",
    )
    parser.add_argument(
        "--fromage",
        type=int,
        default=14,
        help="Only include postings from the last N days. Default: 14. Use 0 for no limit.",
    )
    args = parser.parse_args()

    queries = args.queries or SEARCH_QUERIES
    print(f"Scraping Indeed: {args.location}, radius={args.radius}mi, {args.pages} pages × {len(queries)} queries")

    jobs = scrape_with_playwright(args.location, args.radius, args.pages, queries, args.fromage)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(jobs, indent=2))
    print(f"\nSaved {len(jobs)} jobs to {out}")


if __name__ == "__main__":
    main()
