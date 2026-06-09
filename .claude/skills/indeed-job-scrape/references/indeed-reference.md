# Indeed reference

Details for driving Indeed through the Claude in Chrome MCP and for the JSON
shape the `build_csv.py` helper expects.

## Search URL parameters

Base: `https://www.indeed.com/jobs`

| Param     | Meaning                          | Example / allowed values                     |
|-----------|----------------------------------|----------------------------------------------|
| `q`       | Search query (keywords)          | `cashier`, `office+assistant` (`+` for space)|
| `l`       | Location                         | `Cupertino%2C+CA` (`%2C` = comma)            |
| `radius`  | Miles from location              | `0, 5, 10, 15, 25, 35, 50` only              |
| `start`   | Pagination offset                | `0, 10, 20, 30, 40` → pages 1–5              |
| `fromage` | Max age of posting, in days      | `1, 3, 7, 14` (optional; keeps results fresh)|
| `sort`    | Sort order                       | `date` for newest first (optional)           |

Example (page 2 of a cashier search near Cupertino, last 14 days):

```
https://www.indeed.com/jobs?q=cashier&l=Cupertino%2C+CA&radius=10&start=10&fromage=14
```

## Teen-friendly keyword list

Run a handful of these as separate `q` searches. They map to roles that
typically need no degree, license, or certification and often hire 16–18 year
olds. Pick 6–10 for good coverage; you don't need all of them.

- `cashier`
- `retail associate`
- `sales associate`
- `barista`
- `food service`
- `crew member`
- `host` (restaurant)
- `busser` / `dishwasher`
- `grocery clerk` / `stock associate`
- `warehouse` / `package handler`
- `administrative assistant`
- `office assistant`
- `receptionist`
- `data entry`
- `customer service`
- `lifeguard`
- `camp counselor`
- `tutor`
- `valet` / `car wash`
- `library page`

A broad fallback query is `entry level no experience`.

## Reading the page

Preferred: `mcp__Claude_in_Chrome__get_page_text` after `navigate`. Each job
card on the results page typically yields, in order: title, company name,
location, salary (if posted), a short description blurb, and a "posted X days
ago" line. Parse cards by these repeating blocks.

If text parsing gets messy, fall back to `read_page` for structured DOM or
`find` to target specific nodes. Card-level anchors usually look like
`a.jcs-JobTitle` with an `href` of `/rc/clk?jk=<key>` or `/viewjob?jk=<key>`.
Build the absolute posting URL as `https://www.indeed.com/viewjob?jk=<key>`.

### Common obstacles

- **Human-verification / Cloudflare challenge.** Don't try to bypass it. Tell
  the user, let them clear it in their browser, then resume.
- **"Did you mean" / location not found.** Add or correct the state in `l`.
- **Sponsored cards repeated across pages.** De-duplication on `job_url` in the
  helper handles these.
- **Empty page.** If a page has zero cards, stop paginating that query.

## JSON shape for build_csv.py

Save extracted cards as a JSON list. Each record (all values strings; use
`"Not specified"` when absent):

```json
[
  {
    "title": "Cashier",
    "company": "Target",
    "location": "Cupertino, CA",
    "pay": "$18 - $20 an hour",
    "job_type": "Part-time",
    "schedule_hours": "Evenings, Weekends",
    "date_posted": "2 days ago",
    "description_snippet": "No experience required. We will train. Must be 18+.",
    "job_url": "https://www.indeed.com/viewjob?jk=abc123"
  }
]
```

The helper accepts either a bare list or `{"jobs": [...]}`.

## What the filter excludes

`build_csv.py` drops a posting when its title or description signals a
credential the candidate lacks: a degree (bachelor's/master's/PhD), a
professional license/certification (RN, LPN, CNA, CDL, EMT, CPA, HVAC,
cosmetology, notary, security clearance, "licensed", "certified"), or
substantial prior experience ("3+ years", etc.). It also screens out
**management/senior titles** (manager, director, supervisor, chef de cuisine,
"senior …") and **career-salaried postings** (annual pay ≥ $50,000) — those
aren't the "typical summer job" the candidate is after, even when no certificate
is named. A plain **driver's license** is treated as fine — it's not a barrier for an 18-year-old applying to delivery
or valet roles. Excluded postings are written to a separate `_excluded.csv` so
the user can review the calls and nothing disappears silently.
