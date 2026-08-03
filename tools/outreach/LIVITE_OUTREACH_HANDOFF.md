# Livite Fall 2026 Sports Outreach — Full Session Handoff

> **Purpose:** Complete handoff document for a new Claude Projects session (claude.ai).
> Covers everything built, why it exists, how it works, and what's left to do.
> The new session should connect: Google Sheets, Gmail, and HubSpot connectors.

---

## 1. What This Is

Livite is a fast-casual restaurant in Brookline, MA that does team meals for visiting college sports programs. This project automates outreach to visiting teams — identifying when they're in Boston, finding the right staff contact (not always the head coach), and sending templated emails at the right time before each game.

**Target:** visiting teams playing at 14 Boston-area home schools, across all major fall/winter/spring sports.

**The ask:** "Hey, your team is in Boston on [date]. We do individually boxed team meals, here's our menu."

---

## 2. The Google Sheet

**Sheet ID:** `1iSun8hEAhlIoIRqdnJlI4wT1dhjNEV8QDlDA8l9dN9Q`
**Sheet name:** (open via Google Sheets with this ID)

### Tabs

| Tab | Purpose |
|-----|---------|
| `✈ Visiting Teams` | Main working tab — one row per visiting team game. 416+ rows. This is the source of truth for outreach. |
| `🏟 Home Teams` | Contacts for the 14 local home schools (for local outreach, separate from visiting). |
| `📅 Calendar` | Game schedule view (date-sorted). |
| `🚀 Outreach Queue` | Auto-generated: contacts who need action today (1st Email / Follow-up 1 / Follow-up 2). Rebuilt by running `build_outreach_queue.py`. |
| `✓ Past Sports Clients (2025)` | Historical client list — used to flag repeat visitors and suppress follow-up emails. |

### Visiting Teams — Column Reference

| Column | Description |
|--------|-------------|
| Home School | One of the 14 local schools |
| Game Date | `Sep 15, 2026` format |
| Sport | e.g. `Soccer`, `Field Hockey` |
| Gender | `Women` / `Men` |
| Visiting School | The away team — who we're emailing |
| Contact Name | Scraped from athletics site |
| Contact Title | Their role |
| Contact Email | The email we send to |
| Contact Phone | Optional |
| Contact Stage | Pipeline stage (see §6) |
| Last Contacted | ISO date when last email sent |
| Notes | Free text |
| Series Note | e.g. `Bundled — see row 47` for multi-game series |
| Client Type | `New` or `Past Client` |

---

## 3. The 14 Home Schools

These are the local Boston-area schools whose schedules we scrape for visiting opponents.

| School | Athletics URL |
|--------|--------------|
| Northeastern | https://gonu.com |
| MIT | https://mitathletics.com |
| Tufts | https://gotuftsjumbos.com |
| Brandeis | https://brandeisjudges.com |
| Simmons | https://athletics.simmons.edu |
| Babson | https://babsonathletics.com |
| Bentley | https://bentleyfalcons.com |
| Wellesley | https://wellesleyblue.com |
| Emerson | https://emersonlions.com |
| Stonehill | https://stonehillskyhawks.com |
| UMass Boston | https://beaconsathletics.com |
| Emmanuel | https://goecsaints.com |
| Suffolk | https://gosuffolkrams.com |
| Wentworth | https://wentworthathletics.com |

All use **Sidearm Sports** as their athletics web platform. URL pattern:
- Schedule: `{base_url}/sports/{sport-slug}/schedule`
- Staff: `{base_url}/sports/{sport-slug}`

---

## 4. The 16 Target Sports

These are the sports we track (chosen to exclude low-roster sports like golf, tennis, cross country, track).

```
Sport Slug              Display Name         Gender   Season
─────────────────────────────────────────────────────────────
womens-soccer           Soccer               Women    Fall
womens-volleyball       Volleyball           Women    Fall
field-hockey            Field Hockey         Women    Fall
womens-basketball       Basketball           Women    Winter
womens-ice-hockey       Ice Hockey           Women    Winter
softball                Softball             Women    Spring
womens-lacrosse         Lacrosse             Women    Spring
womens-rowing           Rowing               Women    Spring
lightweight-rowing      Lightweight Rowing   Women    Spring
mens-soccer             Soccer               Men      Fall
football                Football             Men      Fall
mens-basketball         Basketball           Men      Winter
mens-ice-hockey         Ice Hockey           Men      Winter
baseball                Baseball             Men      Spring
mens-lacrosse           Lacrosse             Men      Spring
mens-rowing             Rowing               Men      Spring
```

**Critical slug notes:**
- Volleyball is `womens-volleyball` NOT `volleyball` — most Sidearm sites use the gendered slug
- Football is just `football` (not `mens-football`)
- Ice hockey uses `mens-ice-hockey` / `womens-ice-hockey`

---

## 5. Contact Priority Logic

When scraping a team's staff page, we pick the **best contact** using this hierarchy:

1. Director of Operations (or "Dir. of Ops", "Operations")
2. Associate Head Coach
3. Assistant Coach / First Assistant
4. Head Coach
5. Anyone with a valid email (fallback)

**Why not always the head coach?** Head coaches are often too busy / not the logistics person. The Director of Operations or an assistant coach handles travel and meals. This contact prioritization is in `pick_best_contact()` in all scraper scripts.

---

## 6. Outreach Stage Pipeline

Each row in Visiting Teams has a `Contact Stage`:

| Stage | Meaning |
|-------|---------|
| `Not Started` | No contact yet |
| `1st Sent` | First email sent |
| `2nd Sent` | Follow-up 1 sent |
| `3rd Sent` | Follow-up 2 sent |
| `Responded` | They replied |
| `Booked` | Order confirmed |
| `Declined` | Said no |
| `No Response` | Game passed, no reply |
| `Local` | This is a home-school row, not a visitor — skip outreach |
| `Contact Needed` | Found the game but no staff email yet |
| `Bundled — see row N` | Secondary game in a series (see §7) |

---

## 7. Series / Bundled Game Logic

If a team plays **multiple games within 7 days** at the same home school (e.g. a tournament), it's treated as a **series**:

- The **first game** is the "primary" row — outreach happens on this row
- Subsequent games become **"Bundled — see row N"** rows — they're skipped in outreach
- This prevents spamming the same contact multiple times for one trip

The primary email covers the whole trip: "your team is in Boston for games on X and Y..."

---

## 8. Outreach Timing Rules

### New visiting teams (never ordered from Livite)

| Action | Trigger |
|--------|---------|
| 1st Email | ≤ 21 days before game, stage = Not Started |
| Follow-up 1 | ≤ 12 days before game, stage = 1st Sent |
| Follow-up 2 | ≤ 6 days before game, stage = 2nd Sent |

### Past clients (visited before)

| Action | Trigger |
|--------|---------|
| 1st Email | ≤ 21 days before game, stage = Not Started |
| ~~Follow-ups~~ | **Suppressed** — past clients don't need chasing |

**How past clients are detected:** Cross-reference the `✓ Past Sports Clients (2025)` tab using a CANONICAL name map. The lookup key is `(canonical_school_name, sport, gender)`.

---

## 9. Email Templates

### Subject line (all emails)
```
Team meal for {Visiting Team} at {Home School}, {Month Day}
```
Example: `Team meal for Rhode Island Soccer at BC, August 8`

### 1st Email (new teams)
```
Hi {First Name},

Saw {Team} is in Boston on {Date} to play {Opponent}. We're Livite, a fast casual restaurant in Brookline, and we regularly do team meals for programs at schools like BC, BU, and Harvard.

Everything is individually boxed and labeled for your players. You can place the order through our online catering link, or just send us a spreadsheet with each player's name and item and we'll handle the rest. We can deliver directly to the field, your hotel, or wherever works best.

Menu: livite.com/order
Catering order: toasttab.com/catering/livite

If you're not the best person for team meals, just let me know who is and I'll reach out to them instead.

Anthony
Livite
781-987-4704
```

### Follow-up 1 (≤12 days out, new teams only)
```
Hey {First Name},

Just following up — wanted to make sure this didn't get buried. {Team} is in Boston on {Date} and we'd love to help with team meals if it's something you're figuring out.

We keep it simple: individually boxed orders, delivery to the field or hotel, easy online ordering. No minimums, no contracts.

Menu: livite.com/order
Order: toasttab.com/catering/livite

Happy to answer any questions.

Anthony
Livite
781-987-4704
```

### Follow-up 2 (≤6 days out, new teams only)
```
Hey {First Name},

Last reach out before {Team}'s game on {Date} ({N} days out). If you're all set with meals, no worries at all. If not, we can still make it work — just let me know and we'll get you sorted quickly.

toasttab.com/catering/livite

Anthony
Livite
781-987-4704
```

---

## 10. The Scripts

All scripts live in:
```
/private/tmp/claude-501/[session-id]/scratchpad/
```

In a new session, these will need to be re-read from the conversation history or recreated. They should eventually live in the main repo under `tools/outreach/`.

### `build_outreach_queue.py`
**What it does:** Reads all Visiting Teams rows, applies the timing/stage logic, and writes the `🚀 Outreach Queue` tab with color-coded action items sorted by urgency (fewest days to game first).

**Run when:** Daily, or whenever you want a fresh view of what needs to go out today.

**Output:** One row per contact needing action today. Green = 1st Email, Yellow = Follow-up 1, Orange = Follow-up 2.

### `generate_drafts.py`
**What it does:** Reads the Outreach Queue tab and creates Gmail drafts (one per row) using the appropriate template. Drafts land in Anthony's Gmail for review before sending.

**Run after:** `build_outreach_queue.py`

**After running:** Go to Gmail, review drafts, hit send. Then tell Claude which ones were sent so it can update Contact Stage + Last Contacted in the sheet.

**Requires:** Gmail API access via `token.json` (already scoped with `https://mail.google.com/`).

### `populate_home_teams.py`
**What it does:** Scrapes all 14 home schools × 16 sports = up to 224 combinations. For each sport that exists at a school, finds the best staff contact and writes a row to the `🏟 Home Teams` tab.

**Run when:** Beginning of each season, or when a school hires new staff.

**How it works:**
1. Calls `scrape_team_staff.py` (from the main Livite repo) as a subprocess
2. Applies contact priority logic
3. Checks past clients tab and marks "Active Client" if applicable
4. Writes results — only rows where staff was actually found

**Important:** Only sports that exist at a school will appear. If Simmons doesn't have ice hockey, no row for Simmons ice hockey.

### `mark_sent.py` *(not yet built — pending)*
**What it needs to do:** Accept a list of sheet row numbers, update `Contact Stage` to the next stage and `Last Contacted` to today's date. Currently done manually by telling Claude which rows to update.

---

## 11. The Underlying Tools (Main Livite Repo)

Located in: `/Users/anthonynagle/Documents/Agentic Workflows/Livite Main Agent/tools/`

### `tools/scrape_team_staff.py`
Playwright-based scraper. Navigates to a Sidearm Sports team page and extracts all staff with their names, titles, emails, and phone numbers.

**Usage:**
```bash
python3 tools/scrape_team_staff.py \
  --team-url https://gonu.com/sports/womens-soccer \
  --sport "Soccer" \
  --school "Northeastern"
```

**Returns:** JSON with `{"staff": [{name, title, email, phone}, ...]}`

**Does NOT accept** `--gender` flag (that flag doesn't exist — caused errors early on).

### `tools/scrape_schedule.py`
Playwright-based scraper. Navigates to a Sidearm Sports schedule page and extracts home games with dates and opponents.

**Usage:**
```bash
python3 tools/scrape_schedule.py \
  --url https://gonu.com/sports/womens-soccer/schedule \
  --school "Northeastern" \
  --sport "Soccer" \
  --gender "Women"
```

### Authentication (`token.json`)
Located at: `/Users/anthonynagle/Documents/Agentic Workflows/Livite Main Agent/token.json`

Scopes include:
- `https://www.googleapis.com/auth/spreadsheets`
- `https://www.googleapis.com/auth/drive`
- `https://mail.google.com/` (full Gmail access — needed for draft creation)

---

## 12. Known Issues & Fixes

### Issue: `volleyball` slug returns 0 staff at most schools
**Fix:** Use `womens-volleyball` not `volleyball`. Most Sidearm sites use the gendered slug.

### Issue: `--gender` flag in `scrape_team_staff.py` causes returncode 2
**Fix:** Remove `--gender` from subprocess calls. The flag doesn't exist.

### Issue: UMass Boston / Suffolk / Wentworth women's schedules not posted
**Cause:** Normal — early August, schools haven't posted 2026 women's fall schedules yet.
**Fix:** Check back mid-August and re-run the schedule scraper for those schools.

### Issue: UMass Boston men's soccer IS posted (2026)
**URL:** `https://beaconsathletics.com/sports/mens-soccer/schedule`
5 home games confirmed for fall 2026.

### Issue: Sidearm golf classic splash page blocks WebFetch
**Fix:** Playwright scraper navigates through it fine. Don't use WebFetch for schedule pages directly.

### Issue: Wentworth timeouts
Wentworth's athletics site (`wentworthathletics.com`) times out on both soccer and volleyball. Staff pages may be slow or down. Try again separately before assuming no staff exists.

---

## 13. HubSpot Integration Plan

**Goal:** Email open/click tracking — the one feature the Sheet can't provide.

**Status:** User connected HubSpot to Claude via claude.ai connector. MCP tools not yet confirmed active.

### What HubSpot adds
- Open tracking on every email sent
- Click tracking on menu/catering links
- Deal pipeline matching our Contact Stage flow
- Contact history — see all emails to a contact over time

### Setup path (Private App token for scripted access)
1. HubSpot main account → Settings → Integrations → Private Apps → Create
2. Name: "Livite Outreach"
3. Scopes: `crm.objects.contacts.read/write`, `crm.objects.deals.read/write`, `crm.objects.notes.read/write`
4. Copy access token → add to `.env` as `HUBSPOT_API_KEY`

### HubSpot Deal Pipeline (to create)
Mirror the Contact Stage flow:
- Not Started → 1st Email Sent → Follow-up 1 Sent → Follow-up 2 Sent → Responded → Booked / Declined

### Script to build: `tools/hubspot_sync.py`
- Import all visiting team contacts from the Sheet as HubSpot Contacts
- Create a Deal per contact in the outreach pipeline
- After each email send: update Deal stage + log a Note with email body
- Nightly: sync Deal stages back to Sheet's Contact Stage column

---

## 14. Current State (as of session end)

### Outreach Queue
8 contacts ready for 1st emails. Most urgent: Rhode Island Soccer @ BC — game Aug 8 (5 days out).

**To send emails today:**
1. Run `build_outreach_queue.py` (refresh the queue — dates may have shifted)
2. Run `generate_drafts.py` (creates Gmail drafts)
3. Go to Gmail, review, hit send
4. Tell Claude which were sent → Claude updates sheet

### Home Teams Tab
Populated with contacts from the corrected 16-sport scrape. Some gaps:
- Volleyball at most D3 schools: `womens-volleyball` slug returned results at some, not others — check each school's Sidearm site manually if missing
- Wentworth: timeouts on soccer + volleyball — retry separately
- Emmanuel: only Women's Soccer found (small school, limited sports)
- Suffolk: scraper returned 0 for soccer and volleyball — investigate manually

### Background Job (be9mcr9q8)
A 30-slug background scrape (including track, tennis, cross country, swimming) was running at session end. If it completed, its data was from the old broader sport list and should be **discarded** — the corrected `populate_home_teams.py` (16 slugs) was run separately and its data is what's in the sheet now.

### Contacts Still Needing Manual Research
These visiting schools had no match found via automated scraping — someone needs to look up their athletic department contacts manually:

| School | Home | Sport | Issue |
|--------|------|-------|-------|
| Emmanuel (13 small D3 schools) | Multiple | Various | Small D3 schools with no Sidearm presence |
| Simmons | Various | Multiple | Limited online staff directories |
| Stonehill | Various | Multiple | Mercyhurst ×3, Howard ×1 |
| Bentley | Bentley | VB | Bridgeport VB |
| Tufts | Tufts | WSoc | Westfield State MSoc |
| Wellesley | Wellesley | VB | Westfield State VB, Salem State VB |

---

## 15. How to Continue in a New Session

### Connectors to connect in claude.ai Projects
- **Google Sheets** — to read/write the outreach sheet directly
- **Gmail** — to create drafts and eventually log sends
- **HubSpot** — for CRM and email tracking
- **Google Drive** — for accessing token files if needed

### First things to do in the new session
1. Open the Google Sheet: `https://docs.google.com/spreadsheets/d/1iSun8hEAhlIoIRqdnJlI4wT1dhjNEV8QDlDA8l9dN9Q`
2. Rebuild the Outreach Queue (run `build_outreach_queue.py` or replicate logic via Sheets connector)
3. Generate Gmail drafts for today's queue
4. Set up HubSpot Private App token and run `hubspot_sync.py` to import contacts

### Ongoing workflow (daily)
1. Claude checks Outreach Queue → generates Gmail drafts → Anthony reviews + sends
2. Anthony tells Claude who was sent to → Claude updates Contact Stage + Last Contacted
3. When a school responds → update to Responded → Claude notes in HubSpot
4. When booked → update to Booked

### Mid-August tasks
- Re-scrape UMass Boston women's soccer/volleyball/field hockey (schedules should be posted)
- Re-scrape Suffolk and Wentworth women's sports
- Rebuild Calendar tab with new games
- Rebuild Outreach Queue

---

## 16. Key Decisions Made

**Why semi-automated (drafts, not auto-send)?**
Anthony wants to review every email before it goes out — tone, accuracy, whether the contact name looks right. Auto-send risks firing to a wrong contact or with a bad date.

**Why not HubSpot sequences for the follow-ups?**
HubSpot sequences require you to enroll contacts manually and the timing is calendar-based, not game-date-based. Our follow-up logic is tied to days-until-game, which HubSpot can't natively compute. The Sheet stays as the scheduling brain; HubSpot is the tracking layer.

**Why contact priority prefers Ops Director over Head Coach?**
Head coaches are often non-responsive for logistics. The Director of Operations or an assistant coach is the person who actually handles team travel, hotels, and meals. This got better contact rates in past sessions.

**Why 7-day series threshold?**
If a team plays twice within a week (e.g. a tournament weekend), it's one trip — one email. Outside 7 days, they're separate trips that might have different meal needs.

**Why these 16 sports?**
Chosen to exclude low-roster sports (golf: 5-10 players, tennis: 12-15, cross country: varies, track: large but meals less common for track meets). Focus on team sports where group meals are standard: soccer, volleyball, basketball, hockey, lacrosse, rowing, baseball, softball, football.

---

## 17. File Locations Summary

| File | Location | Purpose |
|------|----------|---------|
| `build_outreach_queue.py` | Session scratchpad | Rebuild Outreach Queue tab |
| `generate_drafts.py` | Session scratchpad | Create Gmail drafts from queue |
| `populate_home_teams.py` | Session scratchpad | Scrape all home school contacts |
| `scrape_team_staff.py` | `tools/` in Livite Main Agent repo | Playwright staff scraper |
| `scrape_schedule.py` | `tools/` in Livite Main Agent repo | Playwright schedule scraper |
| `token.json` | `/Users/anthonynagle/Documents/Agentic Workflows/Livite Main Agent/` | Google OAuth token |
| `.env` | Same repo root | API keys (HubSpot, etc.) |

**Repo:** `anthonynagle1/livite-sports-outreach.git` (this is Livite Main Agent — the outreach/CRM tools repo, separate from the dashboard)

---

*Document generated: August 2026. Continue at claude.ai/projects with Google Sheets, Gmail, and HubSpot connectors enabled.*
