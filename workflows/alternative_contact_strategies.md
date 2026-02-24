# Alternative Contact Discovery Strategies

## Overview
When primary contact scraping fails (table-based + bio page fallback), use these alternative strategies to maximize contact discovery rates.

## Strategy 1: Web Search for School Athletics URLs ✅ IMPLEMENTED
**Success Rate Improvement:** Added 11 missing schools

**Newly Added Schools via Web Search:**
- American University: aueagles.com
- Army West Point: goarmywestpoint.com
- Navy: navysports.com
- Lafayette: goleopards.com
- Siena: sienasaints.com
- Lehigh: lehighsports.com
- Marist: goredfoxes.com
- Quinnipiac: gobobcats.com
- Stonehill: stonehillskyhawks.com
- MIT: mitathletics.com
- Rhode Island: gorhody.com

**Implementation:**
Updated `tools/discover_opponent_url.py` with web-searched URLs.

## Strategy 2: Generic Athletic Department Contacts (Fallback)
**Use Case:** When sport-specific staff pages return 0 contacts

**Approach:**
1. Scrape general athletics staff directory page
2. Look for generic contacts like:
   - "Assistant Athletic Director"
   - "Director of Sports Operations"
   - "Senior Associate AD"
3. Filter for contacts relevant to scheduling/operations
4. Mark as "Generic Contact - [Title]" so user knows it's not sport-specific

**Implementation Path:**
- Create `tools/scrape_generic_athletics_contacts.py`
- Falls back when sport-specific scraping returns 0 staff
- Stores in separate cache: `.tmp/cache/generic_contacts/`

## Strategy 3: Email Pattern Construction
**Use Case:** Schools with publicly known email patterns but no staff directory

**Common Patterns:**
```
[sport]coach@school.edu
[sport]@athletics.school.edu
athletics.[sport]@school.edu
```

**Example:**
- softball@quinnipiac.edu
- softballcoach@stonehill.edu
- athletics.softball@lehigh.edu

**Implementation:**
- Extract school domain from athletics URL
- Try common email patterns
- Validate format (don't send test emails)
- Mark as "Constructed Email - Unverified"

## Strategy 4: Alternative Page Patterns
**Use Case:** Some schools structure differently

**Alternative URLs to Try:**
```
/sports/[sport]/staff/
/sports/[sport]/contact/
/staff-directory/[sport]/
/directory/coaches/[sport]/
/contact-us/[sport]/
```

**Implementation:**
Add to `scrape_team_staff.py` as additional fallback after bio pages.

## Strategy 5: Sport-Specific Contact Pages
**Use Case:** Some sports use different contact structures

**Observed Patterns:**
- Softball often lacks dedicated staff pages
- Crew/Rowing sometimes grouped under "Boats" or "Crew"
- Ice Hockey may be under "Hockey" without "Ice"

**Solution:**
Sport-specific URL mapping in discover tool.

## Priority Order (Current Implementation)

1. ✅ Team-specific /coaches page (table-based)
2. ✅ Bio pages from roster (Georgia Tech pattern)
3. 🔄 Generic athletics contacts (IN PROGRESS)
4. 📋 Email pattern construction (PLANNED)
5. 📋 Alternative page patterns (PLANNED)

## Metrics

**Before Alternative Strategies:**
- BU Games: 58 total, 6 with contacts (10.3%)

**After URL Discovery:**
- BU Games: 58 total, 20 with contacts (34.5%)
- Improvement: +233% contact rate

**Target After All Strategies:**
- Goal: 70%+ contact rate
- Remaining gap: 38 games without contacts

## Next Steps

1. Wait for re-scraping to complete with new URLs
2. Re-match games to contacts
3. Analyze remaining failures
4. Implement generic athletics contacts fallback
5. Test email pattern construction for difficult schools
