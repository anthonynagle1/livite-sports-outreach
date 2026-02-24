# Contact Finding Strategies — Complete Reference

> Consolidated guide to every strategy the system uses to find coaching staff contacts for NCAA athletics programs. This is the single source of truth — supersedes `alternative_contact_strategies.md`.

---

## Overview

The system finds contacts through a **cascading fallback chain**. Each layer tries progressively broader strategies until a valid contact (someone with an email) is found for a given school + sport combination.

**Current system-wide contact rate: ~82% across 11 schools.**

---

## Step 0: URL Discovery

Before scraping anything, the system needs the school's official athletics website URL.

**Tool:** `tools/discover_opponent_url.py`

**How it works:**
1. **Exact match** — Looks up the school name (lowercase) in a 500+ entry dictionary
   - Example: `"boston college"` → `https://bceagles.com`
   - Includes common variants: `"bc"`, `"boston college"`, `"boston college eagles"`
2. **Partial match** — If exact fails, checks if either string contains the other
   - Example: `"boston u"` matches `"boston university"` → `https://goterriers.com`
   - Marked as "medium" confidence

**When a school is missing:** Web search for `"[school name] official athletics"`, add the URL to the dictionary in `discover_opponent_url.py`, and re-process. This is a standing rule — never skip a missing URL.

---

## Step 1: Platform Detection

**Tool:** `tools/detect_athletics_platform.py`

Identifies the website platform to determine which scraping strategy to use:

| Platform | Identifying Signal | Schools |
|----------|-------------------|---------|
| **Sidearm Sports** | `sidearm` in page source, Vue.js rendering | BC, BU, Tufts, Bentley, Harvard, Northeastern, MIT, Simmons, Emerson, Emmanuel, Merrimack |
| **PrestoSports** | `prestosports` in source, card-based layouts | Wentworth, Suffolk, Curry, Lasell, Regis |
| **Custom** | None of the above | Varies |

---

## Step 2: Construct the Coaches URL

The URL to the coaches page depends on the platform, sport, and gender.

### Sidearm Sports
**Pattern:** `{athletics_url}/sports/{prefix}{sport_path}/coaches`

**Sport path mapping:**
| Sport | Path |
|-------|------|
| Baseball | `baseball` |
| Softball | `softball` |
| Basketball | `basketball` |
| Soccer | `soccer` |
| Lacrosse | `lacrosse` |
| Ice Hockey | `ice-hockey` |
| Field Hockey | `field-hockey` |
| Swimming | `swimming-and-diving` |
| Track & Field | `track-and-field` |
| Cross Country | `cross-country` |
| Tennis | `tennis` |
| Volleyball | `volleyball` |
| Golf | `golf` |
| Rowing | `rowing` |
| Sailing | `sailing` |

**Gender prefix logic (when gender is known):**
- Women's sport → try: `womens-{path}`, `women-{path}`, `w{path}`, `{path}`
- Men's sport → try: `mens-{path}`, `men-{path}`, `m{path}`, `{path}`
- Unknown gender → try all variants

### PrestoSports
**Pattern:** `{athletics_url}/sports/{code}/coaches`

**Sport code mapping:**
| Sport | Code(s) |
|-------|---------|
| Baseball | `bsb` |
| Softball | `sball` |
| M Basketball | `mbkb` |
| W Basketball | `wbkb` |
| M Soccer | `msoc` |
| W Soccer | `wsoc` |
| M Lacrosse | `mlax` |
| W Lacrosse | `wlax` |
| M Ice Hockey | `mice` |
| W Ice Hockey | `wice` |
| Football | `fball` |
| M Tennis | `mten` |
| W Tennis | `wten` |
| Volleyball | `wvball` |
| Field Hockey | `fhockey` |
| M Swimming | `mswim` |
| W Swimming | `wswim` |
| M Track | `mtrack` |
| W Track | `wtrack` |
| M Golf | `mgolf` |
| W Golf | `wgolf` |
| M XC | `mxc` |
| W XC | `wxc` |
| Rowing | `wcrew` |

---

## Step 3: Scrape Staff — The Fallback Chain

**Primary tool:** `tools/scrape_team_staff.py`
**Bulk tool:** `tools/backfill_contacts.py`

The system tries these strategies in order, stopping when staff with emails are found:

### Layer 1: Table-Based Scraping (Sidearm)
**What:** Parse HTML tables on the `/coaches` page for Name, Title, Email, Phone columns.

**Header detection (4 methods):**
1. Header text contains keywords: `name`, `title`, `email`, `coach`, `phone`
2. Header `id` attributes match Sidearm patterns: `col-coaches-fullname`, `col-coaches-staff_title`, `col-coaches-staff_email`
3. Table has `sidearm-table` CSS class
4. Table `<caption>` contains "staff"

**Email extraction within tables:**
- Primary: Designated email column with `mailto:` links or `@` text
- Fallback: Scan ALL cells in each row for `mailto:` links or email regex
- Also extracts phone numbers from all cells

### Layer 2: Card-Based Layout (PrestoSports)
**What:** Extract staff from card-style HTML layouts when tables aren't present.

**Selectors tried (in order):**
1. `.card.flex-fill`
2. `.coaches-content .card`
3. `.staff-content .card`
4. `.card` (broad fallback)

**Data extraction from cards:**
- Name: `h5.card-title a` or `h4.card-title a`
- Title: `p.card-text.m-0`
- Email: `a[href^="mailto:"]` or regex in card text
- Phone: `.fa-phone` icon parent text or regex

### Layer 3: Bio Page Email Enrichment
**What:** When staff are found but lack emails, visit their individual bio pages to extract contact info.

**Triggered when:** Staff found with names/titles but 0 emails.

**Process:**
1. Find bio page links in the staff listing (`a[href*="/coaches/"]`)
2. Visit each bio page
3. Extract email via regex from page HTML
4. Filter fake/tracking domains: `sentry.wmt.dev`, `example.com`, `sidearmstats.com`, `sidearmtech.com`
5. Prefer `.edu` emails over others

### Layer 4: Roster Embedded Coaching Section
**What:** Some schools embed coaching staff in the team roster page.

**Triggered when:** `/coaches` page yields 0 staff.

**Process:**
1. Navigate to `/roster` page
2. Find "COACHING STAFF" heading (`h2`, `h3`, or `div` with matching text)
3. Extract the table below that heading
4. Stop at "SUPPORT STAFF" heading if found (only want coaches)

### Layer 5: Bio Page Scraping (Full Fallback)
**What:** Last resort — scrape individual coach bio pages discovered from any listing.

**Tool:** `tools/scrape_coach_bio_pages.py`

**Triggered when:** All previous layers return 0 staff.

**Process:**
1. Find any coach links on the page
2. Visit each bio page individually
3. Extract name, title, email, phone from bio page content

---

## Step 4: Select Best Contact

**Tool:** `tools/match_game_to_contact.py`

From the scraped staff list, the system picks ONE best contact per game using priority scoring:

| Priority | Score | Title Pattern |
|----------|-------|---------------|
| Best | 1 | Director of Operations / Dir. of Ops |
| Very Good | 2 | First Assistant / 1st Assistant Coach |
| Good | 3 | Assistant Coach / Asst Coach |
| Acceptable | 4 | Associate Head Coach |
| Fallback | 5 | Head Coach |
| Poor | 6 | Any other staff with email |

**Why Dir of Ops is #1:** They handle logistics and scheduling — most likely to respond to catering inquiries. Head Coach is deprioritized because they're least likely to engage with vendor outreach.

**Match quality flags:**
- Score 1 = "excellent"
- Score 2 = "very_good"
- Score 3 = "good"
- Score 4 = "acceptable"
- Score 5 = "fallback"
- Score 6+ = "poor"

**Ultimate fallback:** If no priority title matches, takes ANY staff member with a valid email.

---

## Step 5: Validate Contact

**Tool:** `tools/validate_contacts.py`

### Critical (fails validation):
- No email address
- No contact name

### Warnings (passes but flagged):
- Email domain doesn't match school (e.g., gmail.com for a .edu school)
- No contact title
- No phone number

### Domain guessing:
- Hardcoded: `"boston college"` → `bc.edu`, `"merrimack"` → `merrimack.edu`
- Pattern: `"University of XYZ"` → `xyz.edu`, `"XYZ College"` → `xyz.edu`
- Fallback: first word of school name + `.edu`

---

## Edge Cases & Lessons Learned

### Sidearm Empty Headers
Some Sidearm tables have empty `<th>` tags but set `id` attributes like `col-coaches-fullname`. The system detects these via header detection method #2.

### Gender-Unknown Sports
When gender isn't specified (e.g., just "Basketball"), the system tries ALL prefix variants:
`womens-basketball`, `mens-basketball`, `basketball` — uses whichever returns staff.

### PrestoSports Sport Codes
PrestoSports uses short codes (`bsb`, `mbkb`) instead of full names. The mapping handles gender automatically based on the code prefix (`m` or `w`).

### Stale Cache Handling
Cache files with 0 staff or 0 emails are treated as stale and automatically re-scraped. Fresh cache is valid for one academic year (Aug-May).

### Opponent Name Cleaning
Before URL lookup, strips suffixes: `(DH)`, `(Exh)`, `(Exhibition)`. These appear in schedule data but aren't part of the school name.

### Coaches URL Always Populated
Even when scraping fails, the system constructs and stores the coaches URL. This lets users manually check the page and understand why scraping failed.

### Sites That Block Scraping
Some schools actively block automated browsers. Signs: empty page content, CAPTCHA, Cloudflare challenge. Current approach: skip and flag. No workaround implemented.

---

## Platform-Specific Notes

### Sidearm Sports (Majority of Schools)
- Uses Vue.js client-side rendering — must wait for JavaScript
- Wait strategy: `networkidle` with 60s timeout, `domcontentloaded` fallback
- Staff pages: clean table structure at `/sports/{sport}/coaches`
- Schedule pages: `.s-game-card` selector, pipe-separated text
- **Do NOT use** `/staff-directory` — it has 59-76% "Unknown" sport assignments

### PrestoSports (Smaller Schools)
- Card-based HTML layout, no Vue.js
- Sport codes instead of full names in URLs
- Bio pages accessible from coach cards
- Sometimes requires visiting individual bio pages for emails

---

## Cache Structure

```
.tmp/cache/contacts/{school_name}_{sport}.json
```

Each cache file contains:
```json
{
  "school": "Boston College",
  "sport": "Baseball",
  "staff": [
    {
      "name": "John Smith",
      "title": "Director of Baseball Operations",
      "email": "john.smith@bc.edu",
      "phone": "(617) 555-1234"
    }
  ],
  "coaches_url": "https://bceagles.com/sports/baseball/coaches",
  "scraped_at": "2026-02-05T10:30:00"
}
```

---

## Metrics & Targets

- **Target contact rate:** 80%+ per school
- **Current system average:** ~82% across 11 schools
- **Best performers:** BC (95%+), Tufts, Bentley
- **Toughest:** MIT (67% — many obscure opponent schools)

**If contact rate is below 80%:**
1. Check which opponents are missing from `discover_opponent_url.py`
2. Web search for their athletics URLs and add them
3. Re-process the school
4. If still low, check if the school uses a non-standard platform
