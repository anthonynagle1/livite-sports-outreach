# Workflow: Scrape Athletics Site

## Objective
Extract team lists, schedules, and coaching staff from NCAA athletics websites for catering outreach coordination.

## Required Inputs
- School name (e.g., "Boston College")
- Athletics website URL (e.g., "https://bceagles.com")

## Tools Needed
1. `tools/detect_athletics_platform.py` - Identify website platform type
2. `tools/scrape_team_list.py` - Discover all varsity teams
3. `tools/scrape_schedule.py` - Extract home game schedules
4. `tools/scrape_staff_directory.py` - Extract coaching staff contacts

## Steps

### 1. Detect Platform Type (Optional but Recommended)
```bash
python tools/detect_athletics_platform.py \
  --url "https://bceagles.com" \
  --output .tmp/platform_detection.json
```

**Purpose:** Identifies which platform the site uses (Sidearm, PrestoSports, custom) to optimize scraping strategy.

**Output:** JSON with platform type, confidence score, and navigation hints.

### 2. Scrape Team List
```bash
python tools/scrape_team_list.py \
  --url "https://bceagles.com" \
  --school "Boston College" \
  --output .tmp/teams.json
```

**What it does:**
- Navigates athletics site navigation to find all team pages
- Extracts sport name, gender, and team URLs
- Filters out excluded sports (skiing, sailing, golf, tennis, cross country, track)
- Returns 15-25 teams per school typically

**Expected output format:**
```json
{
  "school": "Boston College",
  "teams_found": 18,
  "teams": [
    {
      "sport": "Soccer",
      "gender": "Men",
      "name": "Men's Soccer",
      "url": "https://bceagles.com/sports/mens-soccer"
    }
  ]
}
```

**Common issues:**
- **Timeout errors:** Some sites are slow to load. Tool has 30s timeout.
- **Missing teams:** If team count seems low, manually check if navigation structure is unusual.
- **Wrong sport names:** Tool uses pattern matching; may misclassify unusual sport names.

### 3. Scrape Schedules for Each Team
For each team discovered in step 2, extract the schedule:

```bash
python tools/scrape_schedule.py \
  --team-url "https://bceagles.com/sports/mens-soccer" \
  --sport "Soccer" \
  --gender "Men" \
  --school "Boston College" \
  --output .tmp/schedule_soccer_mens.json
```

**What it does:**
- Finds schedule page from team page
- Parses schedule tables (various formats)
- **Filters for HOME games only** (vs, home indicators)
- **Filters for current academic year** (Aug-May)
- **Filters for future games** (from today forward)
- Extracts date, time, opponent, venue

**Expected output format:**
```json
{
  "school": "Boston College",
  "sport": "Soccer",
  "gender": "Men",
  "games_found": 12,
  "games": [
    {
      "date": "09/15/2025",
      "time": "7:00 PM",
      "opponent": "Harvard",
      "venue": "Newton Campus Field",
      "is_home": true
    }
  ]
}
```

**Common issues:**
- **Home vs Away confusion:** Some sites use inconsistent formatting. Tool looks for "vs", "home", "H" indicators.
- **Date parsing failures:** If dates in unusual format, may fail to parse. Check raw schedule page.
- **No games returned:** Could mean no future home games, or schedule not yet published.

### 4. Scrape Team-Specific Staff (NEW APPROACH - Feb 2026)
For each opponent+sport identified in schedules, scrape their team-specific staff page:

```bash
python tools/scrape_team_staff.py \
  --team-url "https://gocrimson.com/sports/mens-soccer" \
  --sport "Soccer" \
  --school "Harvard" \
  --output .tmp/cache/contacts/harvard_soccer.json
```

**What it does:**
- Navigates to team-specific coaches page: `/sports/[sport]/coaches`
- Extracts staff from clean TABLE structure with Name|Title|Phone|Email columns
- **Sport is KNOWN from URL** (no guessing needed!)
- Handles both coaching staff and support staff
- Returns 5-15 staff members per sport typically

**Expected output format:**
```json
{
  "school": "Harvard",
  "sport": "Soccer",
  "coaches_url": "https://gocrimson.com/sports/mens-soccer/coaches",
  "staff_found": 8,
  "staff": [
    {
      "name": "John Smith",
      "title": "Head Coach",
      "email": "jsmith@harvard.edu",
      "phone": "617-555-0123",
      "sport": "Soccer"
    },
    {
      "name": "Jane Doe",
      "title": "Assistant Coach",
      "email": "jdoe@harvard.edu",
      "phone": "617-555-0124",
      "sport": "Soccer"
    }
  ]
}
```

**Why this approach vs general staff directory:**
- ✅ **100% accurate sport assignment** (from URL, not guessed from title)
- ✅ **Clean table structure** (easier to parse than general directory)
- ✅ **Sport-specific staff only** (no irrelevant support staff)
- ✅ **No "Unknown" sports** (this was 59-76% of general directory data)

**Common issues:**
- **Page structure varies:** Some schools use different URL patterns. Try `/roster/coaches` if `/coaches` fails.
- **Some emails missing:** Student assistants may not have emails listed.

## Expected Outputs
- **Team list JSON:** All teams for the school
- **Schedule JSONs:** One per team, containing home games
- **Staff directory JSON:** Cached for future reuse, containing all coaching staff

## Edge Cases

### 1. JavaScript-Heavy Sites
**Problem:** Many sites render emails via JavaScript to prevent scraping.

**Solution:** Tools use Playwright (full browser automation) with wait times to ensure JS execution.

**Workaround:** If emails still missing, try increasing wait time in tool or manually inspect directory.

### 2. Unusual Schedule Formats
**Problem:** Not all schedules use tables; some use lists, cards, or custom formats.

**Current coverage:** Tool handles tables (most common format).

**Future work:** Add support for list/card formats as we encounter them.

### 3. Platform-Specific Quirks

#### Sidearm Sports
- **Navigation:** Usually has clear sport menu in header - ✅ Works well
- **Team discovery:** Clear sport links in navigation - ✅ Works perfectly
- **Schedules:** ✅ **WORKING!** (Fixed Feb 2026)
  - Modern Sidearm sites use Vue.js to render schedules client-side
  - **Key selector:** `.s-game-card` for individual games
  - **Wait strategy:** Use `networkidle` (60s timeout) + `domcontentloaded` fallback
  - **Format:** Pipe-separated text structure: `vs|Opponent|Venue|City|Date|Day|Time|Links`
  - **Home game indicator:** Text starts with "vs" (away games start with "at")
  - **Date format quirk:** No year included ("Feb 24" not "Feb 24, 2026") - must infer year
  - **Tested successfully:** BC Baseball (27 games), dates correctly inferred as 2026
- **Staff scraping:** ✅ **SOLVED!** (Feb 4, 2026)
  - **CRITICAL:** Use team-specific pages `/sports/[sport]/coaches` NOT `/staff-directory`
  - **Why:** General directory has 59-76% "Unknown" sports (unusable)
  - **Format:** Clean TABLE structure with Name|Title|Phone|Email columns
  - **Sport assignment:** Known from URL (100% accurate)
  - **Tested successfully:** BC Baseball (12 staff), Merrimack Baseball (5 staff)
- **Known issues:** All major issues RESOLVED!

#### PrestoSports
- **Navigation:** Sport links often in sidebar
- **Schedules:** Table format but with different column names
- **Staff directory:** Often at `/coaches`
- **Known issues:** None yet

#### Custom Sites
- **Navigation:** Highly variable
- **Schedules:** Can be any format
- **Staff directory:** Must find manually first time
- **Known issues:** Requires case-by-case adaptation

### 4. Rate Limiting
**Problem:** Scraping 100s of pages may trigger rate limits.

**Mitigation:**
- Sequential processing (no parallel scraping)
- 1-second delay between requests (configurable in .env)
- Cache aggressively to avoid re-scraping

**If rate limited:**
- Increase `SCRAPE_DELAY_MS` in .env
- Split processing across multiple sessions

### 5. Academic Year Edge Cases
**Current rule:** August through May of current academic year.

**Example:** If run in February 2025, get Feb-May 2025 games only.

**No retroactive inclusion** of previous fall season for spring sports.

## Success Criteria
- Team list contains 15-25 teams per school
- Each team schedule returns 10-20 home games (varies by sport)
- Staff directory returns 30+ contacts with valid emails
- 90%+ of emails have correct domain (match school)
- Process completes without manual intervention

## Known School-Specific Notes
This section will evolve as we test on different schools.

### Boston College (bceagles.com)
- Platform: **Sidearm Sports** (60% confidence)
- Teams found: **15 teams** (Baseball, M/W Basketball, Fencing, Field Hockey, Football, M/W Ice Hockey, W Lacrosse, W Rowing, M/W Soccer, Softball, Swimming & Diving, W Volleyball)
- Team discovery: ✅ **Works perfectly**
- Schedule parsing: ✅ **WORKING!** (Fixed Feb 2026)
  - **Format:** JavaScript-rendered Vue.js with `.s-game-card` elements
  - **Solution:** Parse rendered DOM after JS execution with `networkidle` wait (60s timeout)
  - **Structure:** Pipe-separated text: `vs|Opponent|Venue|Location|Date|Time|...`
  - **Date handling:** Dates lack years ("Feb 24" not "Feb 24, 2026") - parser infers year based on academic calendar
  - **Tested:** BC Baseball - extracted 27 home games successfully
- Staff directory: [To be tested]
- **Key Learning:** Sidearm's modern implementation requires `networkidle` wait + enhanced DOM parsing (not JSON extraction)

### Harvard (gocrimson.com)
- Platform: [To be determined]
- Teams expected: ~18
- Schedule format: [To be determined]
- Staff directory: [To be determined]

*Add notes as we discover platform quirks and workarounds*

## Next Steps After Scraping
1. Cache all staff directory data in `.tmp/cache/contacts/[school].json`
2. Pass scraped data to Phase 2 tools:
   - `tools/match_game_to_contact.py` - Match games to sport-specific contacts
   - `tools/validate_contacts.py` - Validate email domains and detect issues
   - `tools/manage_contact_cache.py` - Manage cached contact data

## Troubleshooting

### "Timeout loading page"
- Check if URL is correct and site is accessible
- Some sites are very slow; may need to increase timeout in tool

### "No teams found"
- Manually inspect athletics site to verify navigation structure
- Site may have unusual menu layout; tool may need customization

### "Staff directory not found"
- Try manually finding staff directory URL on site
- Pass direct URL using `--directory-url` parameter

### "Emails showing as 'Not Found'"
- Site may use heavy email obfuscation
- Try running tool with `headless=False` (edit script) to debug visually
- May need to implement more sophisticated email extraction

## Future Enhancements
- Auto-detect and adapt to more platform types
- Handle list/card schedule formats (not just tables)
- Smarter sport assignment for staff (ML-based classification)
- Parallel scraping with rate limit respect
- Schedule change detection (alert when games added/removed)
