"""
Rebuild 🚀 Outreach Queue tab.

Rules:
  New visiting team:   1st email ≥ 21d out → follow-up 1 ≥ 10d out → follow-up 2 ≥ 5d out
  Past client (repeat): 1st email ≥ 21d out → NO auto follow-ups
  Bundled (secondary): skip — handled via primary row
  Local / Contact Needed / Responded / Booked / Declined / No Response: skip
"""
import json, re
import datetime
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

TODAY = datetime.date.today()

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

# ── Load Visiting Teams ───────────────────────────────────────────────────────
vt = sh.worksheet('✈ Visiting Teams')
all_rows = vt.get_all_values()
vt_header = all_rows[0]
vt_data = all_rows[1:]

# Column indices (0-based)
COL = {h: i for i, h in enumerate(vt_header)}

def col(row, name, default=''):
    i = COL.get(name)
    return row[i].strip() if i is not None and i < len(row) else default

def parse_date(s):
    s = s.strip()
    for fmt in ('%b %d, %Y', '%b %-d, %Y', '%B %d, %Y', '%B %-d, %Y'):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except:
            continue
    return None

def parse_last_contacted(s):
    s = s.strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%b %d, %Y', '%B %d, %Y'):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except:
            continue
    return None

# ── Load Past Clients to flag repeat visitors ─────────────────────────────────
CANONICAL = {
    "adelphi university": "Adelphi University", "adelphi": "Adelphi University",
    "american international college": "American International College",
    "american international": "American International College",
    "boston college": "Boston College", "boston university": "Boston University",
    "brandeis university": "Brandeis University", "brandeis": "Brandeis University",
    "brown university": "Brown University", "brown": "Brown University",
    "connecticut college": "Connecticut College",
    "cornell university": "Cornell University", "cornell": "Cornell University",
    "emory university": "Emory University", "emory": "Emory University",
    "harvard university": "Harvard University", "harvard": "Harvard University",
    "holy cross": "Holy Cross", "college of the holy cross": "Holy Cross",
    "mount holyoke college": "Mount Holyoke College", "mount holyoke": "Mount Holyoke College",
    "russell sage college": "Russell Sage College", "russell sage": "Russell Sage College",
    "springfield college": "Springfield College", "springfield": "Springfield College",
    "stonehill college": "Stonehill College", "stonehill": "Stonehill College",
    "university at albany": "University at Albany",
    "washington state university": "Washington State University",
    "bentley university": "Bentley University", "bentley": "Bentley University",
    "hofstra university": "Hofstra University", "hofstra": "Hofstra University",
    "james madison university": "James Madison University", "james madison": "James Madison University",
    "smith college": "Smith College",
    "umass dartmouth": "UMass Dartmouth",
    "university of massachusetts dartmouth": "UMass Dartmouth",
    "university of chicago": "University of Chicago",
    "florida state university": "Florida State University", "florida state": "Florida State University",
    "university of hartford": "University of Hartford", "hartford": "University of Hartford",
}

def norms(s):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', s.lower().strip())).strip()

pc_ws = sh.worksheet('✓ Past Sports Clients (2025)')
pc_rows = pc_ws.get_all_values()[1:]
past_client_keys = set()  # (canonical_name, sport, gender)
for row in pc_rows:
    if len(row) < 3:
        continue
    canonical = CANONICAL.get(norms(row[0]))
    if canonical:
        past_client_keys.add((canonical, row[2].strip(), row[1].strip()))

def is_past_client(visiting, sport, gender):
    canonical = CANONICAL.get(norms(visiting))
    if not canonical:
        return False
    return (canonical, sport, gender) in past_client_keys

# ── Skip stages ───────────────────────────────────────────────────────────────
SKIP_STAGES = {'local', 'contact needed', 'responded', 'booked', 'declined', 'no response'}

def should_skip(stage):
    s = stage.lower().strip()
    if s.startswith('bundled'):
        return True
    return s in SKIP_STAGES

# ── Determine action needed ───────────────────────────────────────────────────
def get_action(stage, game_date, last_contacted_str, is_repeat):
    """
    Returns (action_label, urgency_days) or (None, None) if no action needed.
    urgency_days = days until game (lower = more urgent)
    """
    if game_date <= TODAY:
        return None, None  # game already passed

    days_to_game = (game_date - TODAY).days
    stage_lower = stage.lower().strip()
    last_contacted = parse_last_contacted(last_contacted_str) if last_contacted_str else None

    if stage_lower in ('not started', ''):
        # Send 1st email when within 21 days of game
        if days_to_game <= 21:
            return '1st Email', days_to_game
        return None, None

    if stage_lower == '1st sent':
        if is_repeat:
            return None, None  # past clients: no auto follow-ups
        # Follow-up 1: when within 12 days of game
        if days_to_game <= 12:
            return 'Follow-up 1', days_to_game
        return None, None

    if stage_lower == '2nd sent':
        # Follow-up 2: when within 6 days of game
        if days_to_game <= 6:
            return 'Follow-up 2', days_to_game
        return None, None

    return None, None

# ── Build queue rows ──────────────────────────────────────────────────────────
queue_rows = []

for i, row in enumerate(vt_data, start=2):  # 2 = sheet row number
    stage = col(row, 'Contact Stage')
    if should_skip(stage):
        continue

    email = col(row, 'Contact Email')
    if not email or '@' not in email:
        continue  # no email = can't draft

    game_date_str = col(row, 'Game Date')
    game_date = parse_date(game_date_str)
    if not game_date:
        continue

    visiting = col(row, 'Visiting School')
    home = col(row, 'Home School')
    sport = col(row, 'Sport')
    gender = col(row, 'Gender')
    contact_name = col(row, 'Contact Name')
    contact_title = col(row, 'Contact Title')
    last_contacted = col(row, 'Last Contacted')

    repeat = is_past_client(visiting, sport, gender)
    action, days_to_game = get_action(stage, game_date, last_contacted, repeat)

    if not action:
        continue

    # Extract first name from contact name
    first_name = contact_name.split()[0] if contact_name else ''

    # Series note: check if this is a primary with bundled secondaries
    # (We don't need to do anything special — the bundled rows are skipped)

    queue_rows.append({
        'sheet_row': i,
        'action': action,
        'days_to_game': days_to_game,
        'game_date': game_date_str,
        'game_date_parsed': game_date,
        'home_school': home,
        'visiting_team': visiting,
        'sport': sport,
        'gender': gender,
        'contact_name': contact_name,
        'first_name': first_name,
        'contact_title': contact_title,
        'contact_email': email,
        'contact_stage': stage,
        'last_contacted': last_contacted,
        'client_type': 'Past Client' if repeat else 'New',
    })

# Sort: most urgent first (fewest days to game)
queue_rows.sort(key=lambda r: (r['days_to_game'], r['game_date_parsed']))

# ── Write to 🚀 Outreach Queue tab ────────────────────────────────────────────
QUEUE_HEADER = [
    'Action', 'Days to Game', 'Game Date', 'Home School', 'Visiting Team',
    'Sport', 'Gender', 'Client Type',
    'Contact Name', 'Title', 'Contact Email',
    'Current Stage', 'Last Contacted', 'Sheet Row'
]

try:
    q_ws = sh.worksheet('🚀 Outreach Queue')
    q_ws.clear()
except gspread.exceptions.WorksheetNotFound:
    q_ws = sh.add_worksheet(title='🚀 Outreach Queue', rows='200', cols='15')

write_rows = [QUEUE_HEADER]
for r in queue_rows:
    write_rows.append([
        r['action'],
        r['days_to_game'],
        r['game_date'],
        r['home_school'],
        r['visiting_team'],
        r['sport'],
        r['gender'],
        r['client_type'],
        r['contact_name'],
        r['contact_title'],
        r['contact_email'],
        r['contact_stage'],
        r['last_contacted'],
        r['sheet_row'],
    ])

q_ws.update(write_rows, value_input_option='RAW')
q_ws.format('A1:N1', {'textFormat': {'bold': True}})

# Color-code Action column: 1st Email=green, Follow-up 1=yellow, Follow-up 2=orange
color_batch = []
for idx, r in enumerate(queue_rows, start=2):
    if r['action'] == '1st Email':
        bg = {'red': 0.85, 'green': 0.93, 'blue': 0.83}
    elif r['action'] == 'Follow-up 1':
        bg = {'red': 1.0, 'green': 0.95, 'blue': 0.7}
    else:
        bg = {'red': 1.0, 'green': 0.85, 'blue': 0.6}
    color_batch.append({
        'range': f'A{idx}',
        'format': {'backgroundColor': bg}
    })

if color_batch:
    q_ws.batch_format(color_batch)

sh.batch_update({"requests": [{"autoResizeDimensions": {
    "dimensions": {"sheetId": q_ws.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 14}
}}]})

# ── Summary ───────────────────────────────────────────────────────────────────
action_counts = {}
for r in queue_rows:
    action_counts[r['action']] = action_counts.get(r['action'], 0) + 1

print(f"Outreach Queue built: {len(queue_rows)} contacts need action")
for action, count in sorted(action_counts.items()):
    print(f"  {action}: {count}")
print(f"\nAs of today ({TODAY})")
if queue_rows:
    print("\nTop 10 most urgent:")
    for r in queue_rows[:10]:
        print(f"  [{r['action']}] {r['visiting_team']} ({r['sport']}) @ {r['home_school']} — {r['game_date']} ({r['days_to_game']}d) — {r['contact_name']} <{r['contact_email']}>")
