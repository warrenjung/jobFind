---
name: indeed-job-scrape
description: >-
  Scrape Indeed job postings near a given city and produce a CSV of entry-level
  opportunities suitable for a high-school senior (≈18 years old) with no degree,
  license, or certification — e.g. cashier, retail associate, barista, host,
  warehouse, office/admin assistant, lifeguard, camp counselor, food service.
  Use this skill whenever the user gives a city (or city + state) and asks to
  "find jobs", "search Indeed", "find summer jobs", "find teen jobs", "what jobs
  can an 18-year-old get near X", "scrape job postings", or wants a list/CSV of
  local entry-level openings within some radius. Trigger even if the user does
  not say the word "Indeed" but clearly wants local entry-level/teen/summer job
  listings collected into a spreadsheet. Defaults to a 10-mile radius and the
  first ~5 result pages. Drives the browser through the Claude in Chrome MCP.
---

# Indeed Job Scrape

Collect local entry-level job postings from Indeed and turn them into a clean CSV
the user can sort and apply from. The target candidate is a high-school senior,
about 18, with **no degree, license, or professional certification** — so the
job that survives filtering is the kind of role someone can start with little or
no experience: cashier, retail/sales associate, barista, food prep, host/server
assistant, busser, dishwasher, grocery/stock clerk, warehouse/packer, office or
administrative assistant, receptionist, data entry, lifeguard, camp counselor,
tutor, library page, valet, car wash, etc.

This skill drives the user's real browser through the **Claude in Chrome MCP**
(`mcp__Claude_in_Chrome__*`). Using the user's live session is what makes Indeed
scraping reliable — Indeed aggressively blocks anonymous bots and direct HTTP
fetches, so do **not** try to `curl`/`requests` the site. Read the page through
the extension instead.

## Inputs to confirm

Before scraping, make sure you have:

- **City** (required). If the user gave only a city name, infer the state and
  confirm it (e.g. "Cupertino" → "Cupertino, CA"). Indeed needs the state to
  disambiguate.
- **Radius** — default **10 miles**. Indeed only accepts 0, 5, 10, 15, 25, 35,
  50; snap any other value to the nearest allowed one.
- **Depth** — default **up to 5 result pages** per search (≈75 listings each).
- **Output folder** — write the CSV to the user's working folder.

If the city is unambiguous and the user already specified everything, just go.

## Workflow

### 1. Confirm the browser is connected

Call `mcp__Claude_in_Chrome__list_connected_browsers`. If none is connected, ask
the user to open Chrome with the Claude extension and connect it — do not fall
back to HTTP scraping. If multiple browsers/tabs exist, use
`select_browser` / `tabs_create_mcp` to work in a dedicated tab.

### 2. Build the search URLs

Indeed search URL pattern (see `references/indeed-reference.md` for full detail):

```
https://www.indeed.com/jobs?q=<QUERY>&l=<CITY>%2C+<ST>&radius=10&start=<N>
```

- `q` = the search query (URL-encode spaces as `+`).
- `l` = location, e.g. `Cupertino%2C+CA`.
- `radius` = the chosen radius.
- `start` = pagination offset: `0, 10, 20, 30, 40` for pages 1–5.
- Append `&fromage=14` to limit to postings from the last 14 days (keeps results
  fresh; drop it if the user wants everything).

Run several **teen-friendly keyword searches** rather than one broad search —
this surfaces far more relevant roles than a single query. Use the curated list
in `references/indeed-reference.md` (cashier, retail associate, barista, food
service, warehouse, administrative assistant, receptionist, host, etc.). You do
**not** need every keyword; 6–10 well-chosen searches give good coverage without
taking forever.

### 3. Read each results page

For each search URL and each page offset (up to 5 pages):

1. `mcp__Claude_in_Chrome__navigate` to the URL.
2. Give the page a moment, then `mcp__Claude_in_Chrome__get_page_text` (or
   `read_page`) to pull the rendered listings. Prefer `get_page_text` — it's
   faster and returns the job cards as text.
3. Extract each job card into a record with these fields (use `Not specified`
   when a field is missing):
   - `title`
   - `company`
   - `location`
   - `pay` (salary/hourly range as shown)
   - `date_posted` (e.g. "3 days ago", "Just posted")
   - `job_type` (Part-time / Full-time / Seasonal / Temporary, if shown)
   - `schedule_hours` (any shift/hours text, e.g. "Evenings", "Weekends",
     "Monday to Friday", "Flexible")
   - `description_snippet` (the short blurb under the card)
   - `job_url` (absolute Indeed link to the posting; build from the card's
     job key if only a relative `/viewjob?jk=...` is present →
     `https://www.indeed.com/viewjob?jk=...`)

**Stopping early:** if a results page shows no job cards, or Indeed shows a
verification / "are you a human" challenge, stop paginating that query rather
than hammering it. If a human-verification page appears, tell the user and ask
them to complete it in their browser, then resume. Don't try to bypass it.

**Be polite:** these are real page loads in the user's browser. Move at a human
pace and don't fire dozens of navigations in a tight loop.

### 4. Filter, dedupe, and write the CSV

Collect every extracted record into a single JSON array and save it to
`jobs_scraped.json` in the working folder. Then run the bundled helper:

```bash
python3 scripts/build_csv.py jobs_scraped.json --output indeed_jobs_<city>.csv
```

The helper (see `scripts/build_csv.py`):

- **Dedupes** on job URL (falling back to title+company).
- **Filters out roles that need credentials the candidate doesn't have** —
  postings whose title or description require a degree, license, or
  certification (RN/LPN, CDL, cosmetology license, security clearance, "X+
  years experience required", "bachelor's required", etc.). It also drops
  **management/senior titles** (manager, director, supervisor, head/sous chef,
  "senior …") and **career-salaried postings** (annual pay ≥ $50k), since those
  aren't a typical 18-year-old's summer job even when no certificate is named.
  This is the core filter the user asked for: an 18-year-old with no
  certifications should only see jobs they could actually be hired for.
- Adds a **`teen_fit_reason`** column explaining why each surviving job fits
  (e.g. "Entry-level, no experience required", "No certification mentioned").
- Writes the final CSV with these columns in order:
  `title, company, location, pay, job_type, schedule_hours, date_posted,
  teen_fit_reason, description_snippet, job_url`.
- Prints how many were kept vs. filtered out, and writes the excluded ones to
  `indeed_jobs_<city>_excluded.csv` so nothing is silently lost.

Review the keep/exclude counts. If almost everything got filtered out, the
extraction probably missed description text — re-check a couple of cards.

### 5. Hand off the result

Present the CSV to the user (`present_files`) with a one-line summary: how many
opportunities were found, the city/radius searched, and how many were excluded
for needing certifications. Offer to widen the radius, add keywords, or schedule
the search to re-run (e.g. weekly) if they want fresh listings.

## Notes

- **No HTTP scraping.** Indeed blocks it. Everything goes through the Chrome
  extension on the user's live session.
- **Indeed markup changes.** If `get_page_text` stops yielding clean cards,
  fall back to `read_page` for structured DOM, or `find` for specific elements.
  See the reference file for current selectors and the JSON shape the helper
  expects.
- The helper is pure stdlib Python — no install needed.
