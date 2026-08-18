"""
hubspot_sync.py — Sync Livite sports outreach between the Google Sheet and HubSpot.

The Sheet stays the scheduling brain (game-date-based timing). HubSpot is the
tracking layer: contacts, deals, notes, open/click tracking on sent emails.

Commands:
  python3 tools/outreach/hubspot_sync.py setup-pipeline
      Create the "Livite Outreach" deal pipeline with stages mirroring Contact Stage.
      If the API rejects pipeline creation (scope issue), create it manually in the
      HubSpot UI with the same stage labels, then everything else still works.

  python3 tools/outreach/hubspot_sync.py import
      Read ✈ Visiting Teams → upsert a HubSpot Company per visiting school (so all
      contacts/deals for that team roll up together across seasons), a Contact per
      row with a valid email, and a Deal per game (named "{Visiting} {Sport}
      ({Gender}) @ {Home}", no date in the name — deals are matched internally by a
      hidden "Livite Game Key" text property (visiting|sport|gender|home|date), NOT
      the sheet row number — row numbers shift whenever rows are inserted/reordered
      in the sheet, which silently corrupts row-number-based matching. The game-key
      is content-based so it survives any amount of sheet reordering. Deal stage is
      mapped from the sheet's Contact Stage; Close Date is set to the game date
      (noon UTC, so it doesn't roll back a day in US time zones) giving a native,
      sortable "game date" column. Safe to re-run (dedupes).

  python3 tools/outreach/hubspot_sync.py log-send --row N --body "email text"
      After an email goes out: advance the deal stage in HubSpot and attach a Note
      with the email body to the contact. (Sheet stage update stays with mark_sent.)

  python3 tools/outreach/hubspot_sync.py sync-back
      Pull deal stages from HubSpot and write any changes back to the sheet's
      Contact Stage column (e.g. a deal moved to Responded/Booked in HubSpot).
      Run nightly. Only overwrites when HubSpot stage is FURTHER along.

Auth: HUBSPOT_API_KEY in .env at repo root (Private App token, pat-na1-...).
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

import requests

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHEET_KEY = '1iSun8hEAhlIoIRqdnJlI4wT1dhjNEV8QDlDA8l9dN9Q'
VISITING_TAB = '✈ Visiting Teams'
PIPELINE_LABEL = 'Livite Sports Outreach'
HS = 'https://api.hubapi.com'

# Sheet Contact Stage → HubSpot deal stage label, with an ordering rank so
# sync-back never moves a deal/sheet row backwards.
# NOTE: stage labels must exactly match what's configured in HubSpot
# (Settings → Objects → Deals → Pipelines → Livite Sports Outreach).
STAGE_MAP = [
    # (sheet stage, pipeline stage label, rank)
    ('Not Started',  'Not Started',        0),
    ('1st Sent',     '1st Email Sent',     1),
    ('2nd Sent',     'Follow Up 1 Sent',   2),
    ('3rd Sent',     'Follow Up 2 Sent',   3),
    ('Responded',    'Responded',          4),
    ('Booked',       'Booked',             5),
    ('Delivered',    'Delivered',          6),  # closed-won final stage; clears "Overdue" label
    ('Declined',     'Declined',           5),
    ('No Response',  'No Response',        5),
]
SHEET_TO_LABEL = {s: l for s, l, _ in STAGE_MAP}
LABEL_TO_SHEET = {l: s for s, l, _ in STAGE_MAP}
RANK = {s: r for s, _, r in STAGE_MAP}
SKIP_STAGES = {'Local', 'Contact Needed'}  # plus "Bundled — see row N" (prefix check)


# ── Past clients — "have we worked with this school before?" ──────────────────
# The sheet's '✓ Past Sports Clients (2025)' tab is the ground truth for which
# schools/teams have actually ordered from Livite. School names there don't
# always match the spelling used in Visiting/Home Teams (e.g. "Brandeis
# University" vs "Brandeis"), so we canonicalize both sides before comparing.
# Ported from build_outreach_queue.py's CANONICAL dict (kept in sync manually —
# add a new school here whenever it's added to the Past Clients tab).
PAST_CLIENTS_TAB = '✓ Past Sports Clients (2025)'
CANONICAL = {
    "adelphi university": "Adelphi University", "adelphi": "Adelphi University",
    "american international college": "American International College",
    "american international": "American International College", "aic": "American International College",
    "boston college": "Boston College", "boston university": "Boston University",
    "brandeis university": "Brandeis University", "brandeis": "Brandeis University",
    "brown university": "Brown University", "brown": "Brown University",
    "connecticut college": "Connecticut College", "conn college": "Connecticut College",
    "cornell university": "Cornell University", "cornell": "Cornell University",
    "duke university": "Duke University", "duke": "Duke University",
    "emory university": "Emory University", "emory": "Emory University",
    "harvard university": "Harvard University", "harvard": "Harvard University",
    "holy cross": "Holy Cross", "college of the holy cross": "Holy Cross",
    "mount holyoke college": "Mount Holyoke College", "mount holyoke": "Mount Holyoke College",
    "russell sage college": "Russell Sage College", "russell sage": "Russell Sage College",
    "springfield college": "Springfield College", "springfield": "Springfield College",
    "stonehill college": "Stonehill College", "stonehill": "Stonehill College",
    "university at albany": "University at Albany", "albany": "University at Albany",
    "washington state university": "Washington State University", "washington state": "Washington State University",
    "bentley university": "Bentley University", "bentley": "Bentley University",
    "hofstra university": "Hofstra University", "hofstra": "Hofstra University",
    "james madison university": "James Madison University", "james madison": "James Madison University", "jmu": "James Madison University",
    "smith college": "Smith College", "smith": "Smith College",
    "umass dartmouth": "UMass Dartmouth",
    "university of massachusetts dartmouth": "UMass Dartmouth",
    "university of chicago": "University of Chicago", "chicago": "University of Chicago",
    "florida state university": "Florida State University", "florida state": "Florida State University",
    "university of hartford": "University of Hartford", "hartford": "University of Hartford",
}


def norms(s):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', s.lower().strip())).strip()


def canonical_school(name):
    return CANONICAL.get(norms(name or ''))


def _parse_client_date(s):
    for fmt in ('%b %d, %Y', '%B %d, %Y'):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def date_only_iso(date_str):
    """Midnight-UTC ISO string for HubSpot 'date' (date-only, not datetime)
    properties — these reject anything but exact midnight UTC. This is the
    opposite of game_date_iso()'s noon-UTC trick, which is only needed for
    true datetime properties like closedate."""
    d = _parse_client_date(date_str)
    return d.strftime('%Y-%m-%dT00:00:00.000Z') if d else None


def load_past_clients(sh):
    """Cross-reference the Past Sports Clients tab. Returns:
      school_totals: {canonical_school: {'orders', 'revenue', 'last_order'
                       (raw sheet string), 'sports' (set of 'Sport (Gender)')}}
        — used to tag the Company (school-level, persists across seasons).
      exact_keys: {(canonical_school, sport_lower, gender_lower): True}
        — used to tag the specific Deal when this exact team has ordered before.
    Schools not found in CANONICAL are skipped (add them to CANONICAL above)."""
    ws = sh.worksheet(PAST_CLIENTS_TAB)
    rows = ws.get_all_values()[1:]
    school_totals, exact_keys = {}, {}
    for r in rows:
        if len(r) < 8 or not r[0].strip():
            continue
        canonical = canonical_school(r[0])
        if not canonical:
            continue
        gender, sport = r[1].strip(), r[2].strip()
        try:
            orders = int(r[5].strip() or 0)
        except ValueError:
            orders = 0
        try:
            revenue = float(re.sub(r'[^0-9.]', '', r[6]) or 0)
        except ValueError:
            revenue = 0.0
        last_order = r[7].strip()

        agg = school_totals.setdefault(canonical, {'orders': 0, 'revenue': 0.0,
                                                     'last_order': '', 'sports': set()})
        agg['orders'] += orders
        agg['revenue'] += revenue
        if sport:
            agg['sports'].add(f'{sport} ({gender})' if gender and gender != '—' else sport)
        new_d, cur_d = _parse_client_date(last_order), _parse_client_date(agg['last_order'])
        if new_d and (not cur_d or new_d > cur_d):
            agg['last_order'] = last_order

        if sport:
            exact_keys[(canonical, sport.lower(), gender.lower())] = True
    return school_totals, exact_keys


# ── Auth helpers ──────────────────────────────────────────────────────────────
def hubspot_key():
    env_path = os.path.join(REPO, '.env')
    if os.path.exists(env_path):
        for line in open(env_path):
            m = re.match(r'\s*HUBSPOT_API_KEY\s*=\s*(\S+)', line)
            if m:
                return m.group(1)
    key = os.environ.get('HUBSPOT_API_KEY')
    if key:
        return key
    sys.exit('HUBSPOT_API_KEY not found in .env or environment. '
             'Create a Private App in HubSpot and add the token to .env.')


def hs_request(method, path, key, **kwargs):
    headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
    last_exc = None
    for attempt in range(5):
        try:
            r = requests.request(method, HS + path, headers=headers, timeout=30, **kwargs)
            if r.status_code == 429:
                time.sleep(11)
                continue
            if not r.ok:
                raise RuntimeError(f'{method} {path} → {r.status_code}: {r.text[:400]}')
            return r.json() if r.text else {}
        except (requests.exceptions.SSLError,
                requests.exceptions.ConnectionError) as exc:
            last_exc = exc
            wait = 2 ** attempt  # 1s, 2s, 4s, 8s, 16s
            print(f'  [retry {attempt+1}/5] connection error, waiting {wait}s …', flush=True)
            time.sleep(wait)
    raise RuntimeError(f'{method} {path} failed after 5 attempts: {last_exc}')


def open_sheet():
    import gspread
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    with open(os.path.join(REPO, 'token.json')) as f:
        t = json.load(f)
    creds = Credentials(
        token=t.get('token'), refresh_token=t.get('refresh_token'),
        token_uri=t.get('token_uri', 'https://oauth2.googleapis.com/token'),
        client_id=t.get('client_id'), client_secret=t.get('client_secret'),
        scopes=t.get('scopes'))
    if not creds.valid:
        creds.refresh(Request())
    return gspread.authorize(creds).open_by_key(SHEET_KEY)


# ── Pipeline ──────────────────────────────────────────────────────────────────
def get_pipeline(key, label=None):
    """Return (pipeline_id, {stage_label: stage_id}) for the given pipeline label
    (defaults to the Visiting Teams pipeline, PIPELINE_LABEL)."""
    label = label or PIPELINE_LABEL
    data = hs_request('GET', '/crm/v3/pipelines/deals', key)
    for p in data.get('results', []):
        if p['label'] == label:
            return p['id'], {s['label']: s['id'] for s in p['stages']}
    return None, {}


def setup_pipeline(key):
    pid, stages = get_pipeline(key)
    if pid:
        print(f'Pipeline "{PIPELINE_LABEL}" already exists (id {pid}) with '
              f'{len(stages)} stages.')
        return
    labels = ['Not Started', '1st Email Sent', 'Follow Up 1 Sent', 'Follow Up 2 Sent',
              'Responded', 'Booked', 'Delivered', 'Declined', 'No Response']
    closed_won = {'Booked', 'Delivered'}
    closed_lost = {'Declined', 'No Response'}
    body = {'label': PIPELINE_LABEL, 'displayOrder': 5, 'stages': [
        {'label': l, 'displayOrder': i,
         'metadata': {'probability': '1.0' if l in closed_won
                      else '0.0' if l in closed_lost
                      else str(round(0.1 + 0.15 * i, 2))}}
        for i, l in enumerate(labels)]}
    p = hs_request('POST', '/crm/v3/pipelines/deals', key, json=body)
    print(f'Created pipeline "{PIPELINE_LABEL}" (id {p["id"]}) with {len(labels)} stages.')


# ── Sheet rows ────────────────────────────────────────────────────────────────
def _load_calendar_index(sh):
    """Load the Calendar tab and return a dict keyed by game_key → visit fields."""
    try:
        ws = sh.worksheet('📅 Calendar')
    except Exception:
        return {}
    values = ws.get_all_values()
    if not values:
        return {}
    header = values[0]
    col = {h: i for i, h in enumerate(header)}

    def get(row, name):
        i = col.get(name)
        return row[i].strip() if i is not None and i < len(row) else ''

    index = {}
    for r in values[1:]:
        school  = get(r, 'Visiting School')
        sport   = get(r, 'Sport')
        gender  = get(r, 'Gender')
        home    = get(r, 'Home School')
        date    = get(r, 'Game Date')
        if not school or not date:
            continue
        from datetime import datetime
        date_part = ''
        for fmt in ('%b %d, %Y', '%B %d, %Y'):
            try:
                date_part = datetime.strptime(date.strip(), fmt).strftime('%Y-%m-%d')
                break
            except ValueError:
                continue
        key = '|'.join([school, sport, gender, home, date_part]).lower()
        index[key] = {
            'visit_number':      get(r, 'Visit #'),
            'other_boston_dates': get(r, 'Other Boston Dates'),
            'visit_pattern':     get(r, 'Visit Pattern'),
            'days_to_next_visit': get(r, 'Days to Next Visit'),
            'livite_history':    get(r, 'Livite History'),
        }
    return index


def load_rows(sh):
    ws = sh.worksheet(VISITING_TAB)
    values = ws.get_all_values()
    header = values[0]
    col = {h: i for i, h in enumerate(header)}
    calendar = _load_calendar_index(sh)

    def get(row, name):
        i = col.get(name)
        return row[i].strip() if i is not None and i < len(row) else ''

    rows = []
    for n, r in enumerate(values[1:], start=2):  # n = actual sheet row number
        stage = get(r, 'Contact Stage')
        if not stage or stage in SKIP_STAGES or stage.startswith('Bundled'):
            continue
        email = get(r, 'Contact Email').lower()
        if '@' not in email:
            continue
        school = get(r, 'Visiting School')
        sport  = get(r, 'Sport')
        gender = get(r, 'Gender')
        home   = get(r, 'Home School')
        date   = get(r, 'Game Date')
        # Look up visit metadata from Calendar tab
        from datetime import datetime
        date_part = ''
        for fmt in ('%b %d, %Y', '%B %d, %Y'):
            try:
                date_part = datetime.strptime(date.strip(), fmt).strftime('%Y-%m-%d')
                break
            except ValueError:
                continue
        cal_key = '|'.join([school, sport, gender, home, date_part]).lower()
        cal = calendar.get(cal_key, {})
        rows.append({
            'row': n, 'email': email, 'stage': stage,
            'name': get(r, 'Contact Name'), 'title': get(r, 'Contact Title'),
            'phone': get(r, 'Contact Phone'), 'school': school,
            'home': home, 'sport': sport, 'gender': gender, 'date': date,
            'client_type': get(r, 'Client Type'),
            # Calendar visit fields
            'visit_number':       cal.get('visit_number', ''),
            'other_boston_dates': cal.get('other_boston_dates', ''),
            'visit_pattern':      cal.get('visit_pattern', ''),
            'days_to_next_visit': cal.get('days_to_next_visit', ''),
            'livite_history':     cal.get('livite_history', ''),
        })
    return ws, col, rows


def deal_name(row):
    # No date in the name (Close Date carries that, sortable natively) — the
    # name leads with team identity so repeat visits by the same team group
    # together alphabetically/by search across seasons.
    return f"{row['school']} {row['sport']} ({row['gender']}) @ {row['home']}"


# ── Home Teams (relationship-based outreach to Livite's own 14 home schools —
# NOT per-game. One deal per (school, sport, gender) that persists across
# seasons/years, tracking the ongoing relationship: reached out, ordered,
# repeat customer. Season-start timing is a judgment call for now; a 45-day
# no-response timer is tracked via the Next Recontact Due property.) ─────────
HOME_TAB = '🏟 Home Teams'
HOME_PIPELINE_LABEL = 'Livite Home Teams'
HOME_STAGES = ['Not Started', 'Reached Out', 'Engaged', 'Ordered',
               'Repeat Customer', 'Paused / Not Interested']
RECONTACT_DAYS = 45


def load_home_rows(sh):
    ws = sh.worksheet(HOME_TAB)
    values = ws.get_all_values()
    header = values[0]
    col = {h: i for i, h in enumerate(header)}

    def get(row, name):
        i = col.get(name)
        return row[i].strip() if i is not None and i < len(row) else ''

    rows = []
    for n, r in enumerate(values[1:], start=2):
        email = get(r, 'Contact Email').lower()
        if '@' not in email:
            continue
        stage = get(r, 'Outreach Status') or 'Not Started'
        if stage not in HOME_STAGES:
            stage = 'Not Started'
        rows.append({
            'row': n, 'email': email, 'stage': stage,
            'name': get(r, 'Contact Name'), 'title': get(r, 'Title'),
            'phone': get(r, 'Contact Phone'), 'school': get(r, 'Home School'),
            'sport': get(r, 'Sport'), 'gender': get(r, 'Gender'),
            'season': get(r, 'Season'),
        })
    return ws, col, rows


def home_deal_name(row):
    return f"{row['school']} {row['sport']} ({row['gender']})"


def home_game_key(row):
    # No season/date in the key — the relationship persists across seasons,
    # that's the whole point (per Anthony: "regardless of the season").
    parts = [row['school'], row['sport'], row['gender']]
    return 'home|' + '|'.join(p.strip().lower() for p in parts)


# ── HubSpot objects ───────────────────────────────────────────────────────────
def find_contact(key, email):
    res = hs_request('POST', '/crm/v3/objects/contacts/search', key, json={
        'filterGroups': [{'filters': [
            {'propertyName': 'email', 'operator': 'EQ', 'value': email}]}],
        'properties': ['email'], 'limit': 1})
    hits = res.get('results', [])
    return hits[0]['id'] if hits else None


def associate(key, from_type, from_id, to_type, to_id, assoc_type_id):
    """Idempotently create a HubSpot association (safe to call repeatedly)."""
    hs_request('PUT', f'/crm/v3/objects/{from_type}/{from_id}/associations/'
                       f'{to_type}/{to_id}/{assoc_type_id}', key)


def upsert_contact(key, row, company_id=None):
    first, *rest = (row['name'] or row['email']).split(' ', 1)
    props = {'email': row['email'], 'firstname': first,
             'lastname': rest[0] if rest else '',
             'jobtitle': row['title'], 'phone': row['phone'],
             'company': row['school']}
    cid = find_contact(key, row['email'])
    if cid:
        hs_request('PATCH', f'/crm/v3/objects/contacts/{cid}', key,
                   json={'properties': props})
        if company_id:
            associate(key, 'contacts', cid, 'companies', company_id, 1)
        return cid, False
    associations = [{'to': {'id': company_id}, 'types': [
        {'associationCategory': 'HUBSPOT_DEFINED', 'associationTypeId': 1}]}] \
        if company_id else []
    c = hs_request('POST', '/crm/v3/objects/contacts', key,
                   json={'properties': props, 'associations': associations})
    return c['id'], True


# ── Company (the visiting team/school — rolls up every contact + deal across
# seasons, giving the "have we dealt with this team before" view) ─────────────
# Custom Company properties that flag "have we worked with this school
# before?" (school-level, persists across seasons — this is the season-over-
# season view Anthony wants). Populated from load_past_clients() in
# hubspot_sync.py's Past Clients cross-reference, not the old sheet
# 'Client Type' column (that column no longer exists on the current sheet).
PAST_CLIENT_PROPERTY = 'livite_past_client'
PAST_CLIENT_ORDERS_PROPERTY = 'livite_past_client_orders'
PAST_CLIENT_REVENUE_PROPERTY = 'livite_past_client_revenue'
PAST_CLIENT_LAST_ORDER_PROPERTY = 'livite_past_client_last_order'
PAST_CLIENT_SPORTS_PROPERTY = 'livite_past_client_sports'
_ensured_company_props = set()
_COMPANY_PROP_MARKER = os.path.join(REPO, '.hubspot_past_client_company_props_created')
COMPANY_SEARCH_PROPS = ['name', 'description', PAST_CLIENT_PROPERTY,
                        PAST_CLIENT_ORDERS_PROPERTY, PAST_CLIENT_REVENUE_PROPERTY,
                        PAST_CLIENT_LAST_ORDER_PROPERTY, PAST_CLIENT_SPORTS_PROPERTY]


def ensure_company_properties(key):
    if 'done' in _ensured_company_props or os.path.exists(_COMPANY_PROP_MARKER):
        return
    specs = [
        (PAST_CLIENT_PROPERTY, 'Past Sports Client', 'bool', 'booleancheckbox',
         'Checked if this school has ordered from Livite before (per the '
         "'Past Sports Clients' sheet tab)."),
        (PAST_CLIENT_ORDERS_PROPERTY, 'Past Client — Total Orders', 'number', 'number',
         'Sum of past orders across all sports/teams at this school.'),
        (PAST_CLIENT_REVENUE_PROPERTY, 'Past Client — Total Revenue', 'number', 'number',
         'Sum of past order revenue ($) across all sports/teams at this school.'),
        (PAST_CLIENT_LAST_ORDER_PROPERTY, 'Past Client — Last Order Date', 'date', 'date',
         'Most recent past order date for this school, any sport.'),
        (PAST_CLIENT_SPORTS_PROPERTY, 'Past Client — Sports Ordered', 'string', 'text',
         'Which team(s) at this school have ordered before, e.g. '
         '"Soccer (Women), Field Hockey (Women)".'),
    ]
    for name, label, ptype, field_type, desc in specs:
        r = requests.get(f'{HS}/crm/v3/properties/companies/{name}',
                         headers={'Authorization': f'Bearer {key}'}, timeout=30)
        if r.status_code == 404:
            body = {'name': name, 'label': label, 'type': ptype, 'fieldType': field_type,
                    'groupName': 'companyinformation', 'description': desc}
            if field_type == 'booleancheckbox':
                body['options'] = [{'label': 'True', 'value': 'true', 'displayOrder': 0},
                                   {'label': 'False', 'value': 'false', 'displayOrder': 1}]
            hs_request('POST', '/crm/v3/properties/companies', key, json=body)
    open(_COMPANY_PROP_MARKER, 'w').close()
    _ensured_company_props.add('done')


def find_company(key, name):
    res = hs_request('POST', '/crm/v3/objects/companies/search', key, json={
        'filterGroups': [{'filters': [
            {'propertyName': 'name', 'operator': 'EQ', 'value': name}]}],
        'properties': COMPANY_SEARCH_PROPS, 'limit': 1})
    hits = res.get('results', [])
    return hits[0] if hits else None


def upsert_company(key, row, school_totals=None):
    """Returns (company_id, was_new). school_totals is the school-level dict
    from load_past_clients(); when this school matches, the Company gets
    tagged as a past client with aggregate order/revenue/sports history."""
    name = row['school']
    if not name:
        return None, False
    ensure_company_properties(key)
    existing = find_company(key, name)
    past = (school_totals or {}).get(canonical_school(name))
    props = {}
    note = ''
    if past:
        note = 'Past Sports Client'
        props[PAST_CLIENT_PROPERTY] = 'true'
        props[PAST_CLIENT_ORDERS_PROPERTY] = str(past['orders'])
        props[PAST_CLIENT_REVENUE_PROPERTY] = str(round(past['revenue'], 2))
        props[PAST_CLIENT_SPORTS_PROPERTY] = ', '.join(sorted(past['sports']))
        last_iso = date_only_iso(past['last_order']) if past['last_order'] else None
        if last_iso:
            props[PAST_CLIENT_LAST_ORDER_PROPERTY] = last_iso
    if existing:
        changes = {}
        cur_desc = existing['properties'].get('description') or ''
        if note and note not in cur_desc:
            changes['description'] = note
        for k, v in props.items():
            if str(existing['properties'].get(k) or '') != str(v):
                changes[k] = v
        if changes:
            hs_request('PATCH', f"/crm/v3/objects/companies/{existing['id']}", key,
                       json={'properties': changes})
        return existing['id'], False
    all_props = {'name': name, 'description': note, **props}
    c = hs_request('POST', '/crm/v3/objects/companies', key,
                   json={'properties': all_props})
    return c['id'], True


def game_date_iso(date_str):
    """Convert sheet game date ('Sep 13, 2026') to a full ISO datetime at
    NOON UTC for HubSpot's closedate property (repurposed as the game date
    so it's a native, sortable/filterable column in every deal view).
    Noon UTC (not midnight) is deliberate: HubSpot stores datetime properties
    as UTC instants, and midnight UTC displays as the previous evening in US
    time zones. Noon UTC stays on the correct calendar day everywhere in the US."""
    for fmt in ('%b %d, %Y', '%B %d, %Y'):
        try:
            d = datetime.datetime.strptime(date_str.strip(), fmt)
            return d.strftime('%Y-%m-%dT12:00:00.000Z')
        except ValueError:
            continue
    return None


def game_date_display(date_str):
    """Convert 'Sep 13, 2026' → 'September 13' (no year, casual use in email body)."""
    for fmt in ('%b %d, %Y', '%B %d, %Y'):
        try:
            d = datetime.datetime.strptime(date_str.strip(), fmt)
            return d.strftime('%B %-d')
        except ValueError:
            continue
    return date_str


def game_date_display_full(date_str):
    """Convert 'Sep 13, 2026' → 'September 13, 2026' (with year, for formal use)."""
    for fmt in ('%b %d, %Y', '%B %d, %Y'):
        try:
            d = datetime.datetime.strptime(date_str.strip(), fmt)
            return d.strftime('%B %-d, %Y')
        except ValueError:
            continue
    return date_str


def livite_deal_extra_props(row):
    """Return the custom template-token properties for a visiting-team deal.
    Gender and sport are stored separately so templates can combine them freely,
    e.g. "{{ deal.livite_gender }}'s {{ deal.livite_sport }}" → "Women's Field Hockey".
    """
    props = {
        'livite_visiting_school':   row['school'],
        'livite_home_school':       row['home'],
        'livite_sport':             row['sport'].strip(),
        'livite_gender':            row['gender'].strip(),
        'livite_game_date_display': game_date_display(row.get('date', '')),
        'livite_game_date_full':    game_date_display_full(row.get('date', '')),
    }
    # Visit metadata from Calendar tab (only set if populated)
    for src, dest in [
        ('visit_number',       'livite_visit_number'),
        ('other_boston_dates', 'livite_other_boston_dates'),
        ('visit_pattern',      'livite_visit_pattern'),
        ('days_to_next_visit', 'livite_days_to_next_visit'),
        ('livite_history',     'livite_history'),
    ]:
        if row.get(src):
            props[dest] = row[src]
    return props


# ── Contact history deal property ────────────────────────────────────────────
# One-line summary of all OTHER deals for the same contact, visible at a
# glance in the deal panel without clicking into the contact. Example:
#   "Aug 13 @ BU: 1st Sent | Sep 6 @ Harvard: Responded"
CONTACT_HISTORY_PROPERTY = 'livite_contact_history'
_ensured_contact_history_prop = set()
_CONTACT_HISTORY_PROP_MARKER = os.path.join(REPO, '.hubspot_contact_history_property_created')


def ensure_contact_history_property(key):
    if 'done' in _ensured_contact_history_prop or os.path.exists(_CONTACT_HISTORY_PROP_MARKER):
        return
    r = requests.get(f'{HS}/crm/v3/properties/deals/{CONTACT_HISTORY_PROPERTY}',
                     headers={'Authorization': f'Bearer {key}'}, timeout=30)
    if r.status_code == 404:
        hs_request('POST', '/crm/v3/properties/deals', key, json={
            'name': CONTACT_HISTORY_PROPERTY,
            'label': 'Contact Outreach History',
            'type': 'string', 'fieldType': 'text',
            'groupName': 'dealinformation',
            'description': 'Auto-populated by nightly sync: other games where '
                           'this contact has been reached out to, e.g. '
                           '"Aug 13 @ BU: 1st Sent | Sep 6 @ Harvard: Responded". '
                           'Lets you see cross-game history at a glance without '
                           'clicking into the full contact record.'})
    open(_CONTACT_HISTORY_PROP_MARKER, 'w').close()
    _ensured_contact_history_prop.add('done')


def build_contact_history_string(current_game_key, all_rows_for_contact):
    """Build a pipe-separated summary of other games for the same contact.

    Returns '' when there are no other games (clears stale values).
    """
    parts = []
    for r in all_rows_for_contact:
        if game_key(r) == current_game_key:
            continue
        # Date display: "Aug 13"
        date_display = r['date']
        iso = game_date_iso(r['date'])
        for fmt in ('%b %d, %Y', '%B %d, %Y'):
            try:
                d = datetime.datetime.strptime(r['date'].strip(), fmt)
                date_display = d.strftime('%b %-d')
                break
            except ValueError:
                continue
        sort_key = iso[:10] if iso else r['date']
        parts.append((sort_key, f"{date_display} @ {r['home']}: {r['stage']}"))
    parts.sort(key=lambda x: x[0])
    return ' | '.join(p[1] for p in parts)


# Custom deal property used as a stable identity key so dedup doesn't depend
# on sheet row position OR the display name. Row numbers shift whenever rows
# are inserted/reordered in the sheet — that broke an earlier version of this
# script badly (a mid-sheet insertion cascaded and silently misassigned ~90%
# of deals to the wrong contact/date). The game key is content-based
# (which teams, which sport, which date) so it survives any amount of sheet
# reordering. Created once, lazily, the first time it's needed.
GAME_KEY_PROPERTY = 'livite_game_key'
_ensured_props = set()


_PROP_MARKER = os.path.join(REPO, '.hubspot_game_key_property_created')

# Deal-level "have we fed THIS exact team before" flag (vs the Company-level
# past-client tag, which is school-wide). True when (school, sport, gender)
# exactly matches a row in the Past Clients tab.
PAST_CLIENT_DEAL_PROPERTY = 'livite_past_client_deal'
_ensured_deal_pc_prop = set()
_DEAL_PC_PROP_MARKER = os.path.join(REPO, '.hubspot_past_client_deal_property_created')


def ensure_past_client_deal_property(key):
    if 'done' in _ensured_deal_pc_prop or os.path.exists(_DEAL_PC_PROP_MARKER):
        return
    r = requests.get(f'{HS}/crm/v3/properties/deals/{PAST_CLIENT_DEAL_PROPERTY}',
                     headers={'Authorization': f'Bearer {key}'}, timeout=30)
    if r.status_code == 404:
        hs_request('POST', '/crm/v3/properties/deals', key, json={
            'name': PAST_CLIENT_DEAL_PROPERTY, 'label': 'Past Client (this team)',
            'type': 'bool', 'fieldType': 'booleancheckbox',
            'groupName': 'dealinformation',
            'options': [{'label': 'True', 'value': 'true', 'displayOrder': 0},
                       {'label': 'False', 'value': 'false', 'displayOrder': 1}],
            'description': 'Checked if this exact school+sport+gender has '
                            "ordered from Livite before (per the 'Past Sports "
                            "Clients' sheet tab). See the Company for the "
                            'school-wide past-client history.'})
    open(_DEAL_PC_PROP_MARKER, 'w').close()
    _ensured_deal_pc_prop.add('done')


def is_past_client_deal(row, exact_keys):
    canonical = canonical_school(row['school'])
    if not canonical or not exact_keys:
        return False
    return (canonical, row['sport'].strip().lower(),
            row['gender'].strip().lower()) in exact_keys


def game_key(row):
    date_part = (game_date_iso(row['date']) or row['date'])[:10]
    parts = [row['school'], row['sport'], row['gender'], row['home'], date_part]
    return '|'.join(p.strip().lower() for p in parts)


def ensure_deal_property(key):
    if GAME_KEY_PROPERTY in _ensured_props or os.path.exists(_PROP_MARKER):
        return
    r = requests.get(f'{HS}/crm/v3/properties/deals/{GAME_KEY_PROPERTY}',
                     headers={'Authorization': f'Bearer {key}'}, timeout=30)
    if r.status_code == 200:
        open(_PROP_MARKER, 'w').close()
    if r.status_code == 404:
        hs_request('POST', '/crm/v3/properties/deals', key, json={
            'name': GAME_KEY_PROPERTY, 'label': 'Livite Game Key',
            'type': 'string', 'fieldType': 'text',
            'groupName': 'dealinformation',
            'description': 'Stable content-based identity for this game '
                            '(visiting|sport|gender|home|date, lowercased) — '
                            'used internally to match deals across syncs. '
                            'Immune to sheet row insertions/reordering, '
                            'unlike matching on row number.'})
    open(_PROP_MARKER, 'w').close()
    _ensured_props.add(GAME_KEY_PROPERTY)


def find_deal(key, row, pipeline_id):
    ensure_deal_property(key)
    res = hs_request('POST', '/crm/v3/objects/deals/search', key, json={
        'filterGroups': [{'filters': [
            {'propertyName': GAME_KEY_PROPERTY, 'operator': 'EQ',
             'value': game_key(row)},
            {'propertyName': 'pipeline', 'operator': 'EQ', 'value': pipeline_id}]}],
        'properties': ['dealname', 'dealstage', 'closedate',
                       PAST_CLIENT_DEAL_PROPERTY,
                       'livite_visiting_school', 'livite_home_school',
                       'livite_sport', 'livite_gender', 'livite_game_date_display',
                       'livite_game_date_full',
                       'livite_visit_number', 'livite_other_boston_dates',
                       'livite_visit_pattern', 'livite_days_to_next_visit',
                       'livite_history', CONTACT_HISTORY_PROPERTY], 'limit': 1})
    hits = res.get('results', [])
    return hits[0] if hits else None


def reconcile_deal_contact(key, deal_id, contact_id):
    """Make sure the deal's associated Contact matches the sheet's current
    contact for that row — repoints the association (and drops stale ones)
    if the outreach contact for a game changed (e.g. corrected to the real
    head coach with a different email). Returns True if anything changed."""
    if not contact_id:
        return False
    current = hs_request('GET', f'/crm/v4/objects/deals/{deal_id}/associations/contacts', key)
    current_ids = {str(r['toObjectId']) for r in current.get('results', [])}
    if current_ids == {str(contact_id)}:
        return False
    if str(contact_id) not in current_ids:
        associate(key, 'deals', deal_id, 'contacts', contact_id, 3)
    stale = current_ids - {str(contact_id)}
    for stale_id in stale:
        # /3 = HUBSPOT_DEFINED deal→contact association type; required by v3 endpoint
        hs_request('DELETE',
                   f'/crm/v3/objects/deals/{deal_id}/associations/contacts/{stale_id}/3',
                   key)
    return True


def upsert_deal(key, row, contact_id, company_id, pipeline_id, stage_ids,
                exact_keys=None, contact_rows=None):
    ensure_past_client_deal_property(key)
    ensure_contact_history_property(key)
    name = deal_name(row)
    label = SHEET_TO_LABEL.get(row['stage'], 'Not Started')
    stage_id = stage_ids[label]
    game_date = game_date_iso(row['date'])
    is_past = 'true' if is_past_client_deal(row, exact_keys) else 'false'
    existing = find_deal(key, row, pipeline_id)
    extra = livite_deal_extra_props(row)
    # Build cross-game contact history from other sheet rows for the same contact
    if contact_rows is not None:
        history_str = build_contact_history_string(game_key(row), contact_rows)
        extra[CONTACT_HISTORY_PROPERTY] = history_str
    if existing:
        changes = {}
        if existing['properties'].get('dealname') != name:
            changes['dealname'] = name
        existing_stage_id = existing['properties'].get('dealstage')
        if existing_stage_id != stage_id:
            # Never roll back a stage that was manually advanced in HubSpot.
            # Only update if the sheet stage is >= the current HubSpot stage.
            id_to_label = {v: k for k, v in stage_ids.items()}
            existing_label = id_to_label.get(existing_stage_id, '')
            existing_sheet_stage = LABEL_TO_SHEET.get(existing_label, 'Not Started')
            if RANK.get(row['stage'], 0) >= RANK.get(existing_sheet_stage, 0):
                changes['dealstage'] = stage_id
        existing_date = (existing['properties'].get('closedate') or '')[:10]
        if game_date and existing_date != game_date[:10]:
            changes['closedate'] = game_date
        cur_past = str(existing['properties'].get(PAST_CLIENT_DEAL_PROPERTY) or 'false').lower()
        if cur_past != is_past:
            changes[PAST_CLIENT_DEAL_PROPERTY] = is_past
        # Always sync template-token properties (re-push in case values changed)
        for k, v in extra.items():
            if existing['properties'].get(k) != v:
                changes[k] = v
        contact_changed = reconcile_deal_contact(key, existing['id'], contact_id)
        if changes:
            hs_request('PATCH', f"/crm/v3/objects/deals/{existing['id']}", key,
                       json={'properties': changes})
            return existing['id'], 'updated'
        return existing['id'], ('updated' if contact_changed else 'unchanged')
    props = {'dealname': name, 'pipeline': pipeline_id, 'dealstage': stage_id,
              GAME_KEY_PROPERTY: game_key(row),
              PAST_CLIENT_DEAL_PROPERTY: is_past,
              **extra}
    if game_date:
        props['closedate'] = game_date
    associations = [{'to': {'id': contact_id}, 'types': [
        {'associationCategory': 'HUBSPOT_DEFINED', 'associationTypeId': 3}]}]
    if company_id:
        associations.append({'to': {'id': company_id}, 'types': [
            {'associationCategory': 'HUBSPOT_DEFINED', 'associationTypeId': 5}]})
    d = hs_request('POST', '/crm/v3/objects/deals', key, json={
        'properties': props, 'associations': associations})
    return d['id'], 'created'


# ── Home Teams deal upsert (relationship pipeline, no game date) ──────────────
LAST_OUTREACH_PROPERTY = 'livite_last_outreach_date'
NEXT_RECONTACT_PROPERTY = 'livite_next_recontact_due'
_ensured_home_props = set()
_HOME_PROP_MARKER = os.path.join(REPO, '.hubspot_home_props_created')


def ensure_home_deal_properties(key):
    if 'done' in _ensured_home_props or os.path.exists(_HOME_PROP_MARKER):
        return
    for name, label, desc in [
        (LAST_OUTREACH_PROPERTY, 'Last Outreach Date',
         'Date we last reached out to this team about catering.'),
        (NEXT_RECONTACT_PROPERTY, 'Next Recontact Due',
         f'Auto-set to {RECONTACT_DAYS} days after Last Outreach Date when a '
         f'team enters Reached Out with no response yet — use this to know '
         f'who to follow up with.'),
    ]:
        r = requests.get(f'{HS}/crm/v3/properties/deals/{name}',
                         headers={'Authorization': f'Bearer {key}'}, timeout=30)
        if r.status_code == 404:
            hs_request('POST', '/crm/v3/properties/deals', key, json={
                'name': name, 'label': label, 'type': 'date',
                'fieldType': 'date', 'groupName': 'dealinformation',
                'description': desc})
    open(_HOME_PROP_MARKER, 'w').close()
    _ensured_home_props.add('done')


def find_home_deal(key, row, pipeline_id):
    ensure_deal_property(key)
    res = hs_request('POST', '/crm/v3/objects/deals/search', key, json={
        'filterGroups': [{'filters': [
            {'propertyName': GAME_KEY_PROPERTY, 'operator': 'EQ',
             'value': home_game_key(row)},
            {'propertyName': 'pipeline', 'operator': 'EQ', 'value': pipeline_id}]}],
        'properties': ['dealname', 'dealstage', LAST_OUTREACH_PROPERTY,
                       NEXT_RECONTACT_PROPERTY, PAST_CLIENT_DEAL_PROPERTY], 'limit': 1})
    hits = res.get('results', [])
    return hits[0] if hits else None


def upsert_home_deal(key, row, contact_id, company_id, pipeline_id, stage_ids,
                     exact_keys=None):
    ensure_home_deal_properties(key)
    ensure_past_client_deal_property(key)
    name = home_deal_name(row)
    stage_id = stage_ids[row['stage']]
    today = datetime.date.today()
    is_past = 'true' if is_past_client_deal(row, exact_keys) else 'false'
    existing = find_home_deal(key, row, pipeline_id)
    if existing:
        changes = {}
        if existing['properties'].get('dealname') != name:
            changes['dealname'] = name
        prev_stage_id = existing['properties'].get('dealstage')
        if prev_stage_id != stage_id:
            changes['dealstage'] = stage_id
            # entering "Reached Out": start the 45-day no-response clock
            if row['stage'] == 'Reached Out':
                changes[LAST_OUTREACH_PROPERTY] = today.isoformat()
                changes[NEXT_RECONTACT_PROPERTY] = \
                    (today + datetime.timedelta(days=RECONTACT_DAYS)).isoformat()
            else:
                # left Reached Out (responded, ordered, paused, etc.) — no
                # recontact countdown needed anymore
                changes[NEXT_RECONTACT_PROPERTY] = ''
        cur_past = str(existing['properties'].get(PAST_CLIENT_DEAL_PROPERTY) or 'false').lower()
        if cur_past != is_past:
            changes[PAST_CLIENT_DEAL_PROPERTY] = is_past
        contact_changed = reconcile_deal_contact(key, existing['id'], contact_id)
        if changes:
            hs_request('PATCH', f"/crm/v3/objects/deals/{existing['id']}", key,
                       json={'properties': changes})
            return existing['id'], 'updated'
        return existing['id'], ('updated' if contact_changed else 'unchanged')
    props = {'dealname': name, 'pipeline': pipeline_id, 'dealstage': stage_id,
              GAME_KEY_PROPERTY: home_game_key(row),
              PAST_CLIENT_DEAL_PROPERTY: is_past}
    if row['stage'] == 'Reached Out':
        props[LAST_OUTREACH_PROPERTY] = today.isoformat()
        props[NEXT_RECONTACT_PROPERTY] = \
            (today + datetime.timedelta(days=RECONTACT_DAYS)).isoformat()
    associations = [{'to': {'id': contact_id}, 'types': [
        {'associationCategory': 'HUBSPOT_DEFINED', 'associationTypeId': 3}]}]
    if company_id:
        associations.append({'to': {'id': company_id}, 'types': [
            {'associationCategory': 'HUBSPOT_DEFINED', 'associationTypeId': 5}]})
    d = hs_request('POST', '/crm/v3/objects/deals', key, json={
        'properties': props, 'associations': associations})
    return d['id'], 'created'


def log_note(key, contact_id, body):
    hs_request('POST', '/crm/v3/objects/notes', key, json={
        'properties': {'hs_note_body': body[:65000],
                       'hs_timestamp': datetime.datetime.utcnow()
                       .strftime('%Y-%m-%dT%H:%M:%SZ')},
        'associations': [{'to': {'id': contact_id}, 'types': [
            {'associationCategory': 'HUBSPOT_DEFINED', 'associationTypeId': 202}]}]})


def setup_home_pipeline(key):
    pid, stages = get_pipeline(key, HOME_PIPELINE_LABEL)
    if pid:
        print(f'Pipeline "{HOME_PIPELINE_LABEL}" already exists (id {pid}) with '
              f'{len(stages)} stages.')
        return
    closed_won = {'Ordered', 'Repeat Customer'}
    closed_lost = {'Paused / Not Interested'}
    body = {'label': HOME_PIPELINE_LABEL, 'displayOrder': 6, 'stages': [
        {'label': l, 'displayOrder': i,
         'metadata': {'probability': '1.0' if l in closed_won
                      else '0.0' if l in closed_lost
                      else str(round(0.15 + 0.2 * i, 2))}}
        for i, l in enumerate(HOME_STAGES)]}
    p = hs_request('POST', '/crm/v3/pipelines/deals', key, json=body)
    print(f'Created pipeline "{HOME_PIPELINE_LABEL}" (id {p["id"]}) with '
          f'{len(HOME_STAGES)} stages.')


def cmd_import_home(key, start=0, limit=None, sleep_s=0.25):
    pipeline_id, stage_ids = get_pipeline(key, HOME_PIPELINE_LABEL)
    if not pipeline_id:
        sys.exit(f'Pipeline "{HOME_PIPELINE_LABEL}" not found — run '
                 f'setup-home-pipeline first.')
    sh = open_sheet()
    _, _, rows = load_home_rows(sh)
    school_totals, exact_keys = load_past_clients(sh)
    total = len(rows)
    chunk = rows[start:start + limit] if limit else rows[start:]
    end = start + len(chunk)
    print(f'{total} Home Teams rows with valid emails total. Processing rows '
          f'{start}..{end - 1} ({len(chunk)} rows) this run.')
    stats = {'created': 0, 'updated': 0, 'unchanged': 0, 'contacts_new': 0,
             'companies_new': 0}
    company_cache = {}
    for row in chunk:
        school = row['school']
        if school not in company_cache:
            was_new = find_company(key, school) is None if school else False
            cid_co, _ = upsert_company(key, row, school_totals)
            company_cache[school] = cid_co
            if was_new:
                stats['companies_new'] += 1
        company_id = company_cache.get(school)
        cid, new = upsert_contact(key, row, company_id)
        if new:
            stats['contacts_new'] += 1
        _, what = upsert_home_deal(key, row, cid, company_id, pipeline_id,
                                   stage_ids, exact_keys)
        stats[what] += 1
        time.sleep(sleep_s)
    if end < total:
        print(f'NEXT_START={end}')
    print(f"Done batch. Companies created: {stats['companies_new']}. Contacts "
          f"created: {stats['contacts_new']}. Deals — created: {stats['created']}, "
          f"stage-updated: {stats['updated']}, unchanged: {stats['unchanged']}.")


# ── Commands ──────────────────────────────────────────────────────────────────
def cmd_import(key, start=0, limit=None, sleep_s=0.25):
    pipeline_id, stage_ids = get_pipeline(key)
    if not pipeline_id:
        sys.exit(f'Pipeline "{PIPELINE_LABEL}" not found — run setup-pipeline first.')
    sh = open_sheet()
    _, _, rows = load_rows(sh)
    school_totals, exact_keys = load_past_clients(sh)
    # Index all rows by contact email so each deal can see the coach's other games.
    contact_rows_index = {}
    for r in rows:
        contact_rows_index.setdefault(r['email'], []).append(r)
    total = len(rows)
    chunk = rows[start:start + limit] if limit else rows[start:]
    end = start + len(chunk)
    print(f'{total} sheet rows with valid emails total. Processing rows '
          f'{start}..{end - 1} ({len(chunk)} rows) this run.')
    stats = {'created': 0, 'updated': 0, 'unchanged': 0, 'contacts_new': 0,
             'companies_new': 0}
    company_cache = {}
    for row in chunk:
        school = row['school']
        if school not in company_cache:
            cid_co, was_new = upsert_company(key, row, school_totals)
            company_cache[school] = cid_co
            if was_new:
                stats['companies_new'] += 1
        company_id = company_cache.get(school)
        cid, new = upsert_contact(key, row, company_id)
        if new:
            stats['contacts_new'] += 1
        _, what = upsert_deal(key, row, cid, company_id, pipeline_id, stage_ids,
                              exact_keys,
                              contact_rows=contact_rows_index.get(row['email'], []))
        stats[what] += 1
        time.sleep(sleep_s)  # stay under rate limits
    if end < total:
        print(f'NEXT_START={end}')  # resume marker for the caller
    print(f"Done batch. Companies created: {stats['companies_new']}. Contacts "
          f"created: {stats['contacts_new']}. Deals — created: {stats['created']}, "
          f"stage-updated: {stats['updated']}, unchanged: {stats['unchanged']}.")


def cmd_log_send(key, row_num, body):
    pipeline_id, stage_ids = get_pipeline(key)
    sh = open_sheet()
    _, _, rows = load_rows(sh)
    school_totals, exact_keys = load_past_clients(sh)
    row = next((r for r in rows if r['row'] == row_num), None)
    if not row:
        sys.exit(f'Sheet row {row_num} not found / not syncable.')
    company_id, _ = upsert_company(key, row, school_totals)
    cid, _ = upsert_contact(key, row, company_id)
    upsert_deal(key, row, cid, company_id, pipeline_id, stage_ids, exact_keys)
    if body:
        log_note(key, cid, f'Outreach email sent ({row["stage"]}) — '
                 f'{deal_name(row)}\n\n{body}')
    print(f'Logged send for row {row_num} ({row["email"]}).')


def cmd_sync_back(key):
    pipeline_id, stage_ids = get_pipeline(key)
    if not pipeline_id:
        sys.exit(f'Pipeline "{PIPELINE_LABEL}" not found.')
    id_to_label = {v: k for k, v in stage_ids.items()}
    sh = open_sheet()
    ws, col, rows = load_rows(sh)
    stage_col = col['Contact Stage'] + 1  # 1-based for gspread
    changed = 0
    for row in rows:
        deal = find_deal(key, row, pipeline_id)
        if not deal:
            continue
        hs_label = id_to_label.get(deal['properties'].get('dealstage'))
        hs_sheet_stage = LABEL_TO_SHEET.get(hs_label)
        if not hs_sheet_stage or hs_sheet_stage == row['stage']:
            continue
        # only move forward
        if RANK.get(hs_sheet_stage, 0) > RANK.get(row['stage'], 0):
            ws.update_cell(row['row'], stage_col, hs_sheet_stage)
            print(f"Row {row['row']}: {row['stage']} → {hs_sheet_stage} "
                  f"({row['email']})")
            changed += 1
        time.sleep(0.25)
    print(f'Sync-back complete. {changed} rows updated.')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('setup-pipeline')
    sub.add_parser('setup-home-pipeline')
    imp = sub.add_parser('import')
    imp.add_argument('--start', type=int, default=0)
    imp.add_argument('--limit', type=int, default=None)
    imp.add_argument('--sleep', type=float, default=0.25)
    imh = sub.add_parser('import-home')
    imh.add_argument('--start', type=int, default=0)
    imh.add_argument('--limit', type=int, default=None)
    imh.add_argument('--sleep', type=float, default=0.25)
    ls = sub.add_parser('log-send')
    ls.add_argument('--row', type=int, required=True)
    ls.add_argument('--body', default='')
    sub.add_parser('sync-back')
    args = ap.parse_args()

    key = hubspot_key()
    if args.cmd == 'setup-pipeline':
        setup_pipeline(key)
    elif args.cmd == 'setup-home-pipeline':
        setup_home_pipeline(key)
    elif args.cmd == 'import':
        cmd_import(key, start=args.start, limit=args.limit, sleep_s=args.sleep)
    elif args.cmd == 'import-home':
        cmd_import_home(key, start=args.start, limit=args.limit, sleep_s=args.sleep)
    elif args.cmd == 'log-send':
        cmd_log_send(key, args.row, args.body)
    elif args.cmd == 'sync-back':
        cmd_sync_back(key)


if __name__ == '__main__':
    main()
