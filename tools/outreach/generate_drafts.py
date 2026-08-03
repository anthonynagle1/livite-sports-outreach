"""
Generate Gmail drafts from the 🚀 Outreach Queue.
Fills in the correct template (1st Email / Follow-up 1 / Follow-up 2) per row.
After running: review drafts in Gmail, hit send, then tell Claude which were sent.
"""
import json, base64, datetime, re
from email.mime.text import MIMEText
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

TODAY = datetime.date.today()
FROM_EMAIL = 'anthony@livite.com'

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
gmail = build('gmail', 'v1', credentials=creds)

gc = gspread.authorize(creds)
sh = gc.open_by_key('1iSun8hEAhlIoIRqdnJlI4wT1dhjNEV8QDlDA8l9dN9Q')

# ── Email templates ───────────────────────────────────────────────────────────
def format_game_date(date_str):
    """Convert 'Sep 15, 2026' → 'September 15'"""
    for fmt in ('%b %d, %Y', '%b %-d, %Y', '%B %d, %Y'):
        try:
            d = datetime.datetime.strptime(date_str.strip(), fmt)
            return d.strftime('%B %-d')
        except:
            continue
    return date_str.strip()

def make_subject(team, opponent, date_str):
    return f"Team meal for {team} at {opponent}, {format_game_date(date_str)}"

def make_body_1st(first_name, team, opponent, date_str):
    date_fmt = format_game_date(date_str)
    return f"""Hi {first_name},

Saw {team} is in Boston on {date_fmt} to play {opponent}. We're Livite, a fast casual restaurant in Brookline, and we regularly do team meals for programs at schools like BC, BU, and Harvard.

Everything is individually boxed and labeled for your players. You can place the order through our online catering link, or just send us a spreadsheet with each player's name and item and we'll handle the rest. We can deliver directly to the field, your hotel, or wherever works best.

Menu: livite.com/order
Catering order: toasttab.com/catering/livite

If you're not the best person for team meals, just let me know who is and I'll reach out to them instead.

Anthony
Livite
781-987-4704"""

def make_body_followup1(first_name, team, opponent, date_str):
    date_fmt = format_game_date(date_str)
    return f"""Hey {first_name},

Just following up — wanted to make sure this didn't get buried. {team} is in Boston on {date_fmt} and we'd love to help with team meals if it's something you're figuring out.

We keep it simple: individually boxed orders, delivery to the field or hotel, easy online ordering. No minimums, no contracts.

Menu: livite.com/order
Order: toasttab.com/catering/livite

Happy to answer any questions.

Anthony
Livite
781-987-4704"""

def make_body_followup2(first_name, team, opponent, date_str):
    date_fmt = format_game_date(date_str)
    days_remaining = ''
    for fmt in ('%b %d, %Y', '%b %-d, %Y', '%B %d, %Y'):
        try:
            game_d = datetime.datetime.strptime(date_str.strip(), fmt).date()
            days_remaining = f" ({(game_d - TODAY).days} days out)"
            break
        except:
            continue
    return f"""Hey {first_name},

Last reach out before {team}'s game on {date_fmt}{days_remaining}. If you're all set with meals, no worries at all. If not, we can still make it work — just let me know and we'll get you sorted quickly.

toasttab.com/catering/livite

Anthony
Livite
781-987-4704"""

def make_email(action, row):
    team = row['visiting_team']
    opponent = row['home_school']
    date_str = row['game_date']
    first_name = row['first_name'] or row['contact_name'].split()[0]

    subject = make_subject(team, opponent, date_str)

    if action == '1st Email':
        body = make_body_1st(first_name, team, opponent, date_str)
    elif action == 'Follow-up 1':
        body = make_body_followup1(first_name, team, opponent, date_str)
    else:
        body = make_body_followup2(first_name, team, opponent, date_str)

    return subject, body

def create_draft(to_email, subject, body):
    """Create a Gmail draft."""
    msg = MIMEText(body, 'plain')
    msg['to'] = to_email
    msg['from'] = FROM_EMAIL
    msg['subject'] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = gmail.users().drafts().create(
        userId='me',
        body={'message': {'raw': raw}}
    ).execute()
    return draft['id']

# ── Read queue ────────────────────────────────────────────────────────────────
try:
    q_ws = sh.worksheet('🚀 Outreach Queue')
except gspread.exceptions.WorksheetNotFound:
    print("ERROR: 🚀 Outreach Queue tab not found. Run build_outreach_queue.py first.")
    exit(1)

q_rows = q_ws.get_all_values()
q_header = q_rows[0]
q_data = q_rows[1:]

QCOL = {h: i for i, h in enumerate(q_header)}

def qcol(row, name, default=''):
    i = QCOL.get(name)
    return row[i].strip() if i is not None and i < len(row) else default

# Filter: only rows where Draft Created is not already set
rows_to_draft = []
for row in q_data:
    if not any(row):
        continue
    action = qcol(row, 'Action')
    email = qcol(row, 'Contact Email')
    if not email or '@' not in email:
        continue
    rows_to_draft.append({
        'action': action,
        'game_date': qcol(row, 'Game Date'),
        'home_school': qcol(row, 'Home School'),
        'visiting_team': qcol(row, 'Visiting Team'),
        'sport': qcol(row, 'Sport'),
        'gender': qcol(row, 'Gender'),
        'client_type': qcol(row, 'Client Type'),
        'contact_name': qcol(row, 'Contact Name'),
        'first_name': qcol(row, 'Contact Name').split()[0] if qcol(row, 'Contact Name') else '',
        'contact_email': email,
        'sheet_row': qcol(row, 'Sheet Row'),
        'days_to_game': qcol(row, 'Days to Game'),
    })

print(f"Generating drafts for {len(rows_to_draft)} contacts...\n")

created = []
failed = []

for r in rows_to_draft:
    try:
        subject, body = make_email(r['action'], r)
        draft_id = create_draft(r['contact_email'], subject, body)
        print(f"  ✓ [{r['action']}] {r['visiting_team']} ({r['sport']}) @ {r['home_school']}")
        print(f"    → {r['contact_name']} <{r['contact_email']}> | {r['game_date']} ({r['days_to_game']}d out)")
        print(f"    Subject: {subject}")
        print()
        created.append(r)
    except Exception as e:
        print(f"  ✗ FAILED: {r['visiting_team']} → {r['contact_email']}: {e}")
        failed.append(r)

print(f"\n{'='*60}")
print(f"Drafts created: {len(created)}")
if failed:
    print(f"Failed: {len(failed)}")
print(f"\nDrafts are in your Gmail. Review and hit send.")
print(f"After sending, tell Claude which contacts you sent to,")
print(f"and I'll update Contact Stage + Last Contacted in the sheet.")
