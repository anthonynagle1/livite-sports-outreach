# Workflow: NCAA Contact Automation - Complete System

## Overview
This is the main orchestration workflow for the NCAA Athletics Contact Automation system. It coordinates all tools to automatically discover schedules, extract coaching contacts, and export to Google Sheets for catering outreach.

## System Architecture (WAT Framework)

**Workflows** (Instructions) → **Agents** (You/AI) → **Tools** (Python scripts)

- **Workflows:** Living documentation in `workflows/` that defines the process
- **Agents:** Intelligent coordination and decision-making
- **Tools:** Deterministic Python scripts that execute reliably

## Required Setup (One-Time)

### 1. Install Dependencies
```bash
pip install -r requirements.txt
playwright install
```

### 2. Set Up Google Sheets API
Follow [GOOGLE_SHEETS_SETUP.md](../GOOGLE_SHEETS_SETUP.md) to:
- Create Google Cloud project
- Enable Google Sheets API
- Download `credentials.json`
- Place in project root

## Complete Workflow: School → Spreadsheet

### Objective
For a given school (e.g., Boston College), extract all home game schedules and match each opponent to their sport-specific coaching contact for catering outreach coordination.

### Required Inputs
- **School name**: e.g., "Boston College"
- **Athletics website URL**: e.g., "https://bceagles.com"
- **Academic year**: Current year (automatically filtered Aug-May)

### Step-by-Step Process

---

## Phase 1: Scraping (Data Collection)

### Step 1: Discover All Teams

```bash
python3 tools/scrape_team_list.py \
  --url "https://bceagles.com" \
  --school "Boston College" \
  --output .tmp/bc_teams.json
```

**What happens:**
- Navigates athletics website navigation
- Discovers all varsity teams
- Filters out excluded sports (golf, tennis, track, etc.)
- Outputs: 15-25 teams with sport, gender, and team URLs

**Example output:**
```json
{
  "school": "Boston College",
  "teams_found": 15,
  "teams": [
    {"sport": "Baseball", "gender": "Men", "url": "https://bceagles.com/sports/baseball"},
    {"sport": "Soccer", "gender": "Men", "url": "https://bceagles.com/sports/mens-soccer"}
  ]
}
```

---

### Step 2: Extract Schedules for Each Team

For **each team** from Step 1:

```bash
python3 tools/scrape_schedule.py \
  --team-url "https://bceagles.com/sports/baseball" \
  --sport "Baseball" \
  --gender "Men" \
  --school "Boston College" \
  --output .tmp/bc_baseball_schedule.json
```

**What happens:**
- Finds schedule page for the team
- Extracts games with date, time, opponent, venue
- **Filters for HOME games only** (vs, home indicators)
- **Filters for current academic year** (Aug-May)
- **Filters for future games** (from today forward)

**Example output:**
```json
{
  "school": "Boston College",
  "sport": "Baseball",
  "games_found": 27,
  "games": [
    {
      "date": "Feb 24",
      "time": "2:00 PM",
      "opponent": "Merrimack",
      "venue": "Harrington Athletics Village",
      "is_home": true,
      "parsed_date": "2026-02-24T00:00:00"
    }
  ]
}
```

**Key filtering logic:**
- Only "vs" games (not "at" or "@")
- Dates between Aug (current year) and May (next year)
- Games in the future (not past)

---

### Step 3: Scrape Opponent Staff (Team-Specific Pages)

For **each unique opponent** from Step 2:

```bash
python3 tools/scrape_team_staff.py \
  --team-url "https://merrimackathletics.com/sports/baseball" \
  --sport "Baseball" \
  --school "Merrimack" \
  --output .tmp/cache/contacts/merrimack_baseball.json
```

**What happens:**
- Navigates to opponent's **team-specific coaches page** (not general directory)
- Parses clean table structure: Name | Title | Phone | Email
- **Sport is KNOWN from URL** (100% accurate assignment)
- Saves to cache for reuse

**Example output:**
```json
{
  "school": "Merrimack",
  "sport": "Baseball",
  "staff_found": 5,
  "staff": [
    {
      "name": "Brian Murphy",
      "title": "Head Baseball Coach",
      "email": "murphybp@merrimack.edu",
      "phone": "Not Found",
      "sport": "Baseball"
    },
    {
      "name": "Patrick McKenna",
      "title": "Assistant Baseball Coach",
      "email": "mckennap@merrimack.edu",
      "sport": "Baseball"
    }
  ]
}
```

**Critical learning:**
- ✅ **Use team-specific pages** (`/sports/[sport]/coaches`)
- ❌ **DON'T use general directory** (`/staff-directory`) - 59-76% "Unknown" sports

---

### Step 4: Cache Management (Save for Reuse)

```bash
python3 tools/manage_contact_cache.py \
  --save \
  --input .tmp/cache/contacts/merrimack_baseball.json
```

**What happens:**
- Saves staff data to cache directory
- Cache is valid for current academic year
- Future runs will reuse cached data if fresh

**Check cache status:**
```bash
python3 tools/manage_contact_cache.py \
  --check \
  --school "Merrimack" \
  --sport "Baseball"
```

**Load from cache:**
```bash
python3 tools/manage_contact_cache.py \
  --load \
  --school "Merrimack" \
  --sport "Baseball" \
  --output merrimack_baseball.json
```

---

## Phase 2: Processing (Data Matching)

### Step 5: Match Games to Contacts

```bash
python3 tools/match_game_to_contact.py \
  --game-data .tmp/bc_baseball_schedule.json \
  --staff-data .tmp/cache/contacts/merrimack_baseball.json \
  --output .tmp/matched_bc_vs_merrimack.json
```

**What happens:**
- Matches each game to opponent's staff using **priority logic**:
  1. Director of Operations (sport-specific)
  2. First Assistant Coach
  3. Assistant Coach
  4. Associate Head Coach
  5. Head Coach
- Filters to only valid email addresses
- Skips opponent mismatches (games vs different schools)

**Example output:**
```json
{
  "school": "Boston College",
  "sport": "Baseball",
  "opponent_school": "Merrimack",
  "games_matched": 27,
  "matches": [
    {
      "date": "Feb 24",
      "time": "2:00 PM",
      "opponent": "Merrimack",
      "venue": "Harrington Athletics Village",
      "contact_name": "Patrick McKenna",
      "contact_title": "Assistant Baseball Coach",
      "contact_email": "mckennap@merrimack.edu",
      "match_quality": "good",
      "match_status": "success"
    }
  ]
}
```

**Match quality levels:**
- `excellent`: Director of Operations
- `very_good`: First Assistant
- `good`: Assistant Coach
- `acceptable`: Associate Head
- `fallback`: Head Coach

---

### Step 6: Validate Contacts

```bash
python3 tools/validate_contacts.py \
  --input .tmp/matched_bc_vs_merrimack.json \
  --output .tmp/validated_bc_vs_merrimack.json
```

**What happens:**
- Validates email domains match school
- Checks contact information completeness
- Flags issues for manual review

**Example output:**
```json
{
  "validation_summary": {
    "total_matches": 27,
    "passed": 2,
    "warnings": 0,
    "failed": 0,
    "skipped": 25
  },
  "validated_matches": [...]
}
```

**Validation checks:**
- Email domain matches expected school domain
- Contact name and title present
- Phone number present (warning only)

---

## Phase 3: Export (Google Sheets)

### Step 7: Export to Google Sheets

```bash
python3 tools/export_to_sheets.py \
  --input .tmp/validated_bc_vs_merrimack.json \
  --spreadsheet-name "BC Baseball Catering Contacts 2026"
```

**What happens:**
- Authenticates with Google (browser opens first time)
- Creates spreadsheet with **3 tabs**:
  1. **Game-by-Game Contacts** - Primary view with all details
  2. **Master Contacts Cache** - All staff discovered
  3. **Chronological View** - Games sorted by date
- Formats headers (bold, gray background)
- Returns spreadsheet URL

**Example output:**
```
SUCCESS! Spreadsheet created:
https://docs.google.com/spreadsheets/d/ABC123.../edit

Exported:
- Game-by-Game Contacts: 2 games
- Master Contacts Cache: 430 contacts
- Chronological View: 2 games
```

---

## Multi-Sport Workflow

To process **all sports** for a school:

### 1. Discover all teams
```bash
python3 tools/scrape_team_list.py \
  --url "https://bceagles.com" \
  --school "Boston College" \
  --output .tmp/bc_teams.json
```

### 2. Loop through each team

```bash
# Read teams from bc_teams.json
# For each team:

# Extract schedule
python3 tools/scrape_schedule.py \
  --team-url $TEAM_URL \
  --sport $SPORT \
  --gender $GENDER \
  --school "Boston College" \
  --output .tmp/bc_${SPORT}_schedule.json

# For each unique opponent in schedule:
  # Check cache
  python3 tools/manage_contact_cache.py \
    --check --school $OPPONENT --sport $SPORT

  # If not cached or stale:
  python3 tools/scrape_team_staff.py \
    --team-url $OPPONENT_URL \
    --sport $SPORT \
    --school $OPPONENT \
    --output .tmp/cache/contacts/${OPPONENT}_${SPORT}.json

  # Match games to contacts
  python3 tools/match_game_to_contact.py \
    --game-data .tmp/bc_${SPORT}_schedule.json \
    --staff-data .tmp/cache/contacts/${OPPONENT}_${SPORT}.json \
    --output .tmp/matched_bc_vs_${OPPONENT}_${SPORT}.json

# Aggregate all matches
# (Combine JSON files from all sports)

# Validate
python3 tools/validate_contacts.py \
  --input .tmp/all_bc_matches.json \
  --output .tmp/validated_all_bc.json

# Export to Sheets
python3 tools/export_to_sheets.py \
  --input .tmp/validated_all_bc.json \
  --spreadsheet-name "BC Athletics Catering Contacts 2026"
```

---

## Data Flow Diagram

```
Input: School name + Athletics URL
    ↓
[scrape_team_list.py] → List of all teams
    ↓
FOR EACH TEAM:
    [scrape_schedule.py] → Home games (current academic year)
    ↓
    FOR EACH UNIQUE OPPONENT:
        [manage_contact_cache.py] → Check if cached
        ↓
        IF NOT CACHED:
            [scrape_team_staff.py] → Team-specific staff
            [Cache results]
        ↓
        [match_game_to_contact.py] → Match with priority logic
    ↓
[validate_contacts.py] → Validate emails and data
    ↓
[export_to_sheets.py] → Create Google Spreadsheet
    ↓
Output: Game-by-game contact list ready for outreach
```

---

## Success Criteria

- ✅ All teams discovered (15-25 per school)
- ✅ Only home games in current academic year
- ✅ 100% accurate sport assignment for staff
- ✅ 90%+ valid email addresses
- ✅ Contact priority logic applied correctly
- ✅ Google Spreadsheet created with 3 tabs
- ✅ Minimal manual intervention required

---

## Common Issues & Solutions

### "Timeout loading page"
- **Cause:** Site is slow or has heavy JavaScript
- **Solution:** Tool uses 60s timeout with fallback. If still failing, site may be down.

### "No teams found"
- **Cause:** Unusual navigation structure
- **Solution:** Manually inspect site, may need custom scraping approach

### "Sport = Unknown for many staff"
- **Cause:** Using general staff directory instead of team pages
- **Solution:** Always use team-specific pages (`/sports/[sport]/coaches`)

### "Email domain doesn't match school"
- **Cause:** Staff member from different institution or validation logic issue
- **Solution:** Review flagged entries manually

### "OAuth authorization failed"
- **Cause:** Credentials not set up correctly
- **Solution:** Follow [GOOGLE_SHEETS_SETUP.md](../GOOGLE_SHEETS_SETUP.md)

---

## Performance & Caching

### Academic Year Cache Duration
- **Valid:** Aug (current year) through July (next year)
- **Auto-refresh:** Cache automatically invalidated at start of new academic year
- **Manual refresh:** Delete cached file and re-scrape

### Rate Limiting
- Tools process sequentially (not parallel)
- 1-3 second delays between requests
- Cache reduces redundant scraping

### Expected Processing Time
- **Team discovery:** 5-10 seconds per school
- **Schedule extraction:** 10-30 seconds per team
- **Staff scraping:** 15-45 seconds per opponent
- **Contact matching:** < 1 second
- **Google Sheets export:** 5-10 seconds

**Total for one sport:** ~2-5 minutes
**Total for all sports (15 teams):** ~30-60 minutes

---

## File Organization

```
NCAA Contact Automation/
├── tools/                      # Python scripts (execution)
│   ├── scrape_team_list.py
│   ├── scrape_schedule.py
│   ├── scrape_team_staff.py
│   ├── match_game_to_contact.py
│   ├── validate_contacts.py
│   ├── manage_contact_cache.py
│   └── export_to_sheets.py
├── workflows/                  # Markdown SOPs (instructions)
│   ├── ncaa_contact_automation.md  (this file)
│   ├── scrape_athletics_site.md
│   └── extract_coaching_contacts.md
├── .tmp/                       # Temporary processing files
│   ├── cache/
│   │   └── contacts/          # Cached staff by school+sport
│   └── raw_scrapes/           # Debug HTML saves
├── requirements.txt           # Python dependencies
├── credentials.json           # Google OAuth (gitignored)
└── GOOGLE_SHEETS_SETUP.md     # Setup guide
```

---

## Next Steps

1. **First run:** Test on one school, one sport
2. **Verify output:** Check Google Spreadsheet accuracy
3. **Scale up:** Process all sports for the school
4. **Add schools:** Repeat for additional schools
5. **Automate:** Create shell script or Python orchestrator for batch processing

---

## Platform-Specific Notes

### Sidearm Sports (Most Common)
- **Detection:** Look for "Sidearm" in page source
- **Team pages:** `/sports/[sport-name]`
- **Schedules:** `.s-game-card` elements with Vue.js rendering
- **Staff pages:** `/sports/[sport]/coaches` with table structure
- **Known schools:** Boston College, Merrimack

### PrestoSports
- **Detection:** Look for "Presto" or "SIDEARM" in different format
- **Team pages:** Usually `/landing/`
- **Schedules:** Table-based but different column names
- **Staff pages:** `/coaches` with card layout

### Custom Platforms
- Require case-by-case adaptation
- Document quirks in `workflows/scrape_athletics_site.md`

---

## Maintenance

### Weekly
- Monitor for scraping failures
- Update cache for new opponents

### Monthly
- Review validation warnings
- Update contact priority logic if needed

### Academic Year Start (August)
- Clear all cache
- Re-scrape all staff directories
- Verify schedule data for new season

---

## Support & Troubleshooting

For detailed platform-specific guidance:
- See [workflows/scrape_athletics_site.md](scrape_athletics_site.md)

For Google Sheets setup:
- See [GOOGLE_SHEETS_SETUP.md](../GOOGLE_SHEETS_SETUP.md)

For tool-specific usage:
- Run any tool with `--help` flag
- Example: `python3 tools/scrape_schedule.py --help`
