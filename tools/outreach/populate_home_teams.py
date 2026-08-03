"""
Populate 🏟 Home Teams tab — all 14 home schools, all sports, all seasons.
Tries every common sport slug; only writes rows where staff is actually found.
Priority: Director of Operations → Asst Coach → Associate Head → Head Coach → any with email.
"""
import json, subprocess, sys
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ── Auth ──────────────────────────────────────────────────────────────────────
with open('/Users/anthonynagle/Documents/Agentic Workflows/Livite Main Agent/token.json') as f:
    t = json.load(f)
creds = Credentials(
    token=t.get('token'), refresh_token=t.get('refresh_token'),
    token_uri=t.get('token_uri', 'https://oauth2.googleapis.com/token'),
    client_id=t.get('client_id'), client_secret=t.get('client_secret'),
    scopes=t.get('scopes')
)
if not creds.valid:
    creds.refresh(Request())
gc = gspread.authorize(creds)
sh = gc.open_by_key('1iSun8hEAhlIoIRqdnJlI4wT1dhjNEV8QDlDA8l9dN9Q')

# ── All sports to try (slug, display name, gender, season) ───────────────────
ALL_SPORTS = [
    # Fall women's
    ('womens-soccer',       'Soccer',       'Women', 'Fall'),
    ('womens-volleyball',   'Volleyball',   'Women', 'Fall'),
    ('field-hockey',        'Field Hockey', 'Women', 'Fall'),
    # Winter women's
    ('womens-basketball',   'Basketball',   'Women', 'Winter'),
    ('womens-ice-hockey',   'Ice Hockey',   'Women', 'Winter'),
    # Spring women's
    ('softball',            'Softball',     'Women', 'Spring'),
    ('womens-lacrosse',     'Lacrosse',     'Women', 'Spring'),
    ('womens-rowing',       'Rowing',       'Women', 'Spring'),
    ('lightweight-rowing',  'Lightweight Rowing', 'Women', 'Spring'),
    # Fall men's
    ('mens-soccer',         'Soccer',       'Men',   'Fall'),
    ('football',            'Football',     'Men',   'Fall'),
    # Winter men's
    ('mens-basketball',     'Basketball',   'Men',   'Winter'),
    ('mens-ice-hockey',     'Ice Hockey',   'Men',   'Winter'),
    # Spring men's
    ('baseball',            'Baseball',     'Men',   'Spring'),
    ('mens-lacrosse',       'Lacrosse',     'Men',   'Spring'),
    ('mens-rowing',         'Rowing',       'Men',   'Spring'),
]

# ── Home schools ──────────────────────────────────────────────────────────────
HOME_SCHOOLS = [
    ('Northeastern',  'https://gonu.com'),
    ('MIT',           'https://mitathletics.com'),
    ('Tufts',         'https://gotuftsjumbos.com'),
    ('Brandeis',      'https://brandeisjudges.com'),
    ('Simmons',       'https://athletics.simmons.edu'),
    ('Babson',        'https://babsonathletics.com'),
    ('Bentley',       'https://bentleyfalcons.com'),
    ('Wellesley',     'https://wellesleyblue.com'),
    ('Emerson',       'https://emersonlions.com'),
    ('Stonehill',     'https://stonehillskyhawks.com'),
    ('UMass Boston',  'https://beaconsathletics.com'),
    ('Emmanuel',      'https://goecsaints.com'),
    ('Suffolk',       'https://gosuffolkrams.com'),
    ('Wentworth',     'https://wentworthathletics.com'),
]

# ── Contact priority ──────────────────────────────────────────────────────────
TITLE_PRIORITY = [
    'director of operations', 'dir. of ops', 'dir of ops', 'operations',
    'associate head coach', 'associate head',
    'assistant coach', 'first assistant',
    'head coach',
]

def pick_best_contact(staff_list):
    for priority in TITLE_PRIORITY:
        for m in staff_list:
            title = m.get('title', '').lower()
            email = m.get('email', '')
            if priority in title and email and email != 'Not Found' and '@' in email:
                return m
    for m in staff_list:
        email = m.get('email', '')
        if email and email != 'Not Found' and '@' in email:
            return m
    return None

def scrape_staff(team_url, sport, school):
    try:
        result = subprocess.run(
            ['python3', 'tools/scrape_team_staff.py',
             '--team-url', team_url,
             '--sport', sport,
             '--school', school],
            capture_output=True, text=True, timeout=60,
            cwd='/Users/anthonynagle/Documents/Agentic Workflows/Livite Main Agent'
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        return data.get('staff', [])
    except:
        return []

# ── Past clients for Outreach Status ─────────────────────────────────────────
import re

CANONICAL = {
    "brandeis university": "Brandeis University", "brandeis": "Brandeis University",
    "stonehill college": "Stonehill College", "stonehill": "Stonehill College",
    "bentley university": "Bentley University", "bentley": "Bentley University",
    "mit": "MIT", "massachusetts institute of technology": "MIT",
    "northeastern university": "Northeastern", "northeastern": "Northeastern",
    "tufts university": "Tufts", "tufts": "Tufts",
    "simmons university": "Simmons", "simmons": "Simmons",
    "babson college": "Babson", "babson": "Babson",
    "wellesley college": "Wellesley", "wellesley": "Wellesley",
    "emerson college": "Emerson", "emerson": "Emerson",
    "umass boston": "UMass Boston",
    "emmanuel college": "Emmanuel", "emmanuel": "Emmanuel",
    "suffolk university": "Suffolk", "suffolk": "Suffolk",
    "wentworth institute of technology": "Wentworth", "wentworth": "Wentworth",
}

def norms(s):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', s.lower().strip())).strip()

pc_ws = sh.worksheet('✓ Past Sports Clients (2025)')
pc_rows = pc_ws.get_all_values()[1:]
past_client_keys = set()
for row in pc_rows:
    if len(row) < 3:
        continue
    canonical = CANONICAL.get(norms(row[0]))
    if canonical:
        past_client_keys.add((canonical, row[2].strip(), row[1].strip()))

def get_outreach_status(school_name, sport, gender):
    return 'Active Client' if (school_name, sport, gender) in past_client_keys else 'Not Started'

# ── Main scrape loop ──────────────────────────────────────────────────────────
results = []
total_attempts = len(HOME_SCHOOLS) * len(ALL_SPORTS)
done = 0

for school_name, base_url in HOME_SCHOOLS:
    print(f"\n{'='*50}", file=sys.stderr)
    print(f"  {school_name}", file=sys.stderr)
    school_found = 0

    for slug, sport_name, gender, season in ALL_SPORTS:
        done += 1
        team_url = f"{base_url}/sports/{slug}"
        staff = scrape_staff(team_url, sport_name, school_name)

        if not staff:
            continue  # sport doesn't exist at this school

        contact = pick_best_contact(staff)
        status = get_outreach_status(school_name, sport_name, gender)
        school_found += 1

        print(f"  [{season}] {gender} {sport_name}: {len(staff)} staff → "
              f"{contact.get('name','—') if contact else '⚠ no email'} "
              f"({contact.get('title','') if contact else ''})", file=sys.stderr)

        results.append({
            'school': school_name,
            'sport': sport_name,
            'gender': gender,
            'season': season,
            'contact_name': contact.get('name', '') if contact else '',
            'contact_title': contact.get('title', '') if contact else '',
            'contact_email': contact.get('email', '') if contact else '',
            'contact_phone': contact.get('phone', '') if contact else '',
            'outreach_status': status,
        })

    print(f"  → {school_found} sports found", file=sys.stderr)

# ── Write to 🏟 Home Teams ────────────────────────────────────────────────────
print(f"\n\nWriting {len(results)} rows...", file=sys.stderr)

ht = sh.worksheet('🏟 Home Teams')
ht.clear()

HEADER = ['Home School', 'Season', 'Sport', 'Gender',
          'Contact Name', 'Title', 'Contact Email', 'Contact Phone',
          'Outreach Status']

rows_to_write = [HEADER]
for r in results:
    rows_to_write.append([
        r['school'], r['season'], r['sport'], r['gender'],
        r['contact_name'], r['contact_title'],
        r['contact_email'], r['contact_phone'],
        r['outreach_status'],
    ])

ht.update(rows_to_write, value_input_option='RAW')
ht.format('A1:I1', {'textFormat': {'bold': True}})
sh.batch_update({"requests": [{"autoResizeDimensions": {
    "dimensions": {"sheetId": ht.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 9}
}}]})

print(f"✅ {len(results)} rows written to 🏟 Home Teams", file=sys.stderr)
print(f"   Active Client: {sum(1 for r in results if r['outreach_status'] == 'Active Client')}", file=sys.stderr)
print(f"   Missing email: {sum(1 for r in results if not r['contact_email'])}", file=sys.stderr)
print("Done!", file=sys.stderr)
