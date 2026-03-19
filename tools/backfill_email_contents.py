#!/usr/bin/env python3
"""
Tool: backfill_email_contents.py
Purpose: Pull actual email subject/body from Gmail for pre-CRM outreach games
         and create Email Queue entries in Notion with the real content.

The previous backfill_sent_history.py only updated Contact dates.
This script goes further — it creates actual Email Queue records so the
CRM UI shows the email content when you click into a game.

Flow:
1. Query Notion for games with outreach status indicating email was sent
2. Filter to games that have NO linked Email Queue entries
3. For each game's contact, search Gmail sent folder for matching emails
4. Create Email Queue entries with real subject, body, dates, thread/message IDs
5. Link entries to the correct Game and Contact in Notion

Usage:
    # Dry run — show what would be created
    python tools/backfill_email_contents.py --dry-run

    # Actually create Email Queue entries
    python tools/backfill_email_contents.py

    # Limit to N games (useful for testing)
    python tools/backfill_email_contents.py --dry-run --limit 10
"""

import argparse
import base64
import json
import os
import sys
import time
import uuid
from datetime import datetime

try:
    from notion_client import Client
    from notion_client.errors import APIResponseError
except ImportError:
    print("Error: notion-client not installed", file=sys.stderr)
    sys.exit(1)

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

load_dotenv()


def get_catering_gmail_service():
    """Get Gmail API service authenticated as catering@livite.com."""
    token_path = os.getenv('CATERING_TOKEN_PATH', 'catering_token.json')
    if not os.path.exists(token_path):
        print(f"Error: {token_path} not found. Run tools/auth_catering_gmail.py first.",
              file=sys.stderr)
        sys.exit(1)
    creds = Credentials.from_authorized_user_file(token_path)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, 'w') as f:
            json.dump({
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes or []),
            }, f, indent=2)
    return build('gmail', 'v1', credentials=creds)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr)


def paginated_query(notion, database_id, **kwargs):
    """Query Notion DB with pagination."""
    results = []
    has_more = True
    start_cursor = None
    while has_more:
        if start_cursor:
            kwargs['start_cursor'] = start_cursor
        response = notion.databases.query(database_id=database_id, **kwargs)
        results.extend(response.get('results', []))
        has_more = response.get('has_more', False)
        start_cursor = response.get('next_cursor')
    return results


def get_pre_crm_games(notion, games_db):
    """Find games that were emailed but may not have Email Queue entries."""
    emailed_statuses = [
        'Introduction Email - Sent',
        'Follow-Up Email - Sent',
        'Responded',
        'In Conversation',
        'Interested',
        'Booked',
        'Not Interested',
        'No Response',
    ]

    # Query games with any emailed status
    conditions = [
        {'property': 'Outreach Status', 'select': {'equals': s}}
        for s in emailed_statuses
    ]

    pages = paginated_query(notion, games_db, filter={
        'or': conditions
    })

    games = []
    for page in pages:
        props = page['properties']
        contact_ids = [r['id'] for r in props.get('Contact', {}).get('relation', [])]
        if not contact_ids:
            continue  # Skip games without contacts

        game_date_prop = props.get('Game Date', {}).get('date')
        game_date = game_date_prop.get('start', '') if game_date_prop else ''

        visiting = ''.join(
            t.get('plain_text', '') for t in props.get('Visiting Team', {}).get('rich_text', [])
        )
        status_sel = props.get('Outreach Status', {}).get('select')
        status = status_sel.get('name', '') if status_sel else ''

        games.append({
            'id': page['id'],
            'visiting_team': visiting,
            'game_date': game_date,
            'outreach_status': status,
            'contact_ids': contact_ids,
        })

    return games


def get_games_with_emails(notion, email_db):
    """Get set of game IDs that already have Email Queue entries."""
    pages = paginated_query(notion, email_db)
    game_ids = set()
    for page in pages:
        for rel in page['properties'].get('Game', {}).get('relation', []):
            game_ids.add(rel['id'])
    return game_ids


def get_contact_email(notion, contact_id):
    """Fetch contact's email address and name."""
    try:
        page = notion.pages.retrieve(page_id=contact_id)
        props = page['properties']
        email = props.get('Email', {}).get('email', '') or ''
        name = ''.join(
            t.get('plain_text', '') for t in props.get('Name', {}).get('title', [])
        )
        sport_sel = props.get('Sport', {}).get('select')
        sport = sport_sel.get('name', '') if sport_sel else ''
        return {'email': email.lower().strip(), 'name': name, 'sport': sport}
    except Exception as e:
        log(f"  Error fetching contact {contact_id}: {e}")
        return None


def search_gmail_for_contact(service, to_email, game_date=None):
    """Search Gmail sent folder for emails to a contact.

    Returns list of email dicts with subject, body, dates, thread/message IDs.
    If game_date is provided, tries to find emails closest to that date.
    """
    try:
        query = f"to:{to_email} in:sent"
        response = service.users().messages().list(
            userId='me', q=query, maxResults=50
        ).execute()

        messages = response.get('messages', [])
        if not messages:
            return []

        results = []
        for msg_ref in messages:
            msg = service.users().messages().get(
                userId='me', id=msg_ref['id'], format='full'
            ).execute()

            # Extract headers
            headers = {h['name'].lower(): h['value'] for h in msg.get('payload', {}).get('headers', [])}
            subject = headers.get('subject', '')
            to_addr = headers.get('to', '')
            date_str = headers.get('date', '')

            # Parse internal date
            internal_date = msg.get('internalDate', '')
            sent_date = None
            if internal_date:
                dt = datetime.fromtimestamp(int(internal_date) / 1000)
                sent_date = dt.strftime('%Y-%m-%d')

            # Extract body
            body = extract_body(msg.get('payload', {}))

            results.append({
                'message_id': msg['id'],
                'thread_id': msg.get('threadId', ''),
                'subject': subject,
                'body': body,
                'to_email': to_addr,
                'sent_date': sent_date,
                'date_header': date_str,
            })

            time.sleep(0.1)  # Light rate limiting

        return results

    except Exception as e:
        log(f"  Gmail search error for {to_email}: {e}")
        return []


def extract_body(payload):
    """Extract plain text body from Gmail message payload."""
    # Direct body
    if payload.get('mimeType') == 'text/plain':
        data = payload.get('body', {}).get('data', '')
        if data:
            return base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')

    # Multipart — look for text/plain first, then text/html
    parts = payload.get('parts', [])
    plain_text = ''
    html_text = ''

    for part in parts:
        mime = part.get('mimeType', '')
        if mime == 'text/plain':
            data = part.get('body', {}).get('data', '')
            if data:
                plain_text = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
        elif mime == 'text/html':
            data = part.get('body', {}).get('data', '')
            if data:
                html_text = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
        elif mime.startswith('multipart/'):
            # Nested multipart
            nested = extract_body(part)
            if nested:
                plain_text = plain_text or nested

    if plain_text:
        return plain_text
    if html_text:
        # Strip HTML tags for a rough plain text version
        import re
        text = re.sub(r'<br\s*/?>', '\n', html_text)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'&amp;', '&', text)
        text = re.sub(r'&lt;', '<', text)
        text = re.sub(r'&gt;', '>', text)
        return text.strip()

    return ''


def find_best_email_for_game(emails, game_date):
    """Pick the email most likely to be the outreach for a specific game.

    Strategy: find the email sent closest to (but before) the game date.
    If no game_date, return the most recent email.
    """
    if not emails:
        return None

    if not game_date:
        return emails[0]  # Most recent

    # Parse game date
    try:
        gd = datetime.strptime(game_date, '%Y-%m-%d')
    except ValueError:
        return emails[0]

    # Find emails sent before the game date, closest to it
    best = None
    best_diff = None

    for email in emails:
        if not email.get('sent_date'):
            continue
        try:
            sd = datetime.strptime(email['sent_date'], '%Y-%m-%d')
        except ValueError:
            continue

        diff = (gd - sd).days
        if diff >= -1:  # Allow emails sent up to 1 day after game (timezone issues)
            if best_diff is None or diff < best_diff:
                best = email
                best_diff = diff

    return best or emails[0]


def get_school_name(notion, school_id):
    """Resolve school name from ID."""
    try:
        page = notion.pages.retrieve(page_id=school_id)
        return ''.join(
            t.get('plain_text', '')
            for t in page['properties'].get('School Name', {}).get('title', [])
        )
    except Exception:
        return ''


def create_email_queue_entry(notion, email_db, email_data, game, contact_id, contact_info):
    """Create an Email Queue entry in Notion."""
    email_id = f"backfill-{uuid.uuid4().hex[:8]}"

    # Truncate body to Notion's 2000 char limit for rich_text
    body = email_data.get('body', '')
    if len(body) > 2000:
        body = body[:1997] + '...'

    subject = email_data.get('subject', '')
    if len(subject) > 2000:
        subject = subject[:2000]

    properties = {
        "Email ID": {"title": [{"text": {"content": email_id}}]},
        "Subject": {"rich_text": [{"text": {"content": subject}}]},
        "Body": {"rich_text": [{"text": {"content": body}}]},
        "Status": {"select": {"name": "Sent"}},
        "To Email": {"email": contact_info.get('email', '')},
        "Sport": {"rich_text": [{"text": {"content": contact_info.get('sport', '')}}]},
        "Game": {"relation": [{"id": game['id']}]},
        "Contact": {"relation": [{"id": contact_id}]},
    }

    # Set sent date
    if email_data.get('sent_date'):
        properties["Sent At"] = {"date": {"start": email_data['sent_date']}}

    # Set game date
    if game.get('game_date'):
        properties["Game Date"] = {"date": {"start": game['game_date']}}

    # Set Gmail IDs
    if email_data.get('thread_id'):
        properties["Gmail Thread ID"] = {"rich_text": [{"text": {"content": email_data['thread_id']}}]}
    if email_data.get('message_id'):
        properties["Gmail Message ID"] = {"rich_text": [{"text": {"content": email_data['message_id']}}]}

    try:
        notion.pages.create(
            parent={"database_id": email_db},
            properties=properties,
        )
        return True
    except APIResponseError as e:
        log(f"  Error creating Email Queue entry: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Backfill email contents from Gmail into Notion Email Queue")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be created")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of games to process")
    args = parser.parse_args()

    notion = Client(auth=os.getenv('NOTION_API_KEY'))
    games_db = os.getenv('NOTION_GAMES_DB')
    email_db = os.getenv('NOTION_EMAIL_QUEUE_DB')

    if not games_db or not email_db:
        log("Error: NOTION_GAMES_DB and NOTION_EMAIL_QUEUE_DB must be set")
        sys.exit(1)

    # Connect to Gmail
    log("Connecting to Gmail (catering@livite.com)...")
    service = get_catering_gmail_service()
    profile = service.users().getProfile(userId='me').execute()
    gmail_account = profile['emailAddress']
    log(f"Gmail account: {gmail_account}")

    # Step 1: Find all games that were emailed
    log("Loading emailed games from Notion...")
    all_emailed_games = get_pre_crm_games(notion, games_db)
    log(f"Found {len(all_emailed_games)} games with emailed status")

    # Step 2: Find which games already have Email Queue entries
    log("Loading existing Email Queue entries...")
    games_with_entries = get_games_with_emails(notion, email_db)
    log(f"Found {len(games_with_entries)} games with existing Email Queue entries")

    # Step 3: Filter to games needing backfill
    games_to_backfill = [g for g in all_emailed_games if g['id'] not in games_with_entries]
    log(f"Games needing email content backfill: {len(games_to_backfill)}")

    if args.limit > 0:
        games_to_backfill = games_to_backfill[:args.limit]
        log(f"Limited to {args.limit} games")

    if not games_to_backfill:
        log("Nothing to backfill!")
        return

    # Step 4: Process each game
    stats = {'processed': 0, 'found': 0, 'created': 0, 'no_match': 0, 'errors': 0}
    contact_cache = {}  # contact_id -> {email, name, sport}
    gmail_cache = {}    # email_address -> [email_results]

    for i, game in enumerate(games_to_backfill, 1):
        log(f"[{i}/{len(games_to_backfill)}] {game['visiting_team']} — {game['game_date']} ({game['outreach_status']})")
        stats['processed'] += 1

        # Get contact info
        contact_id = game['contact_ids'][0]
        if contact_id not in contact_cache:
            contact_info = get_contact_email(notion, contact_id)
            if contact_info:
                contact_cache[contact_id] = contact_info
            time.sleep(0.35)

        contact_info = contact_cache.get(contact_id)
        if not contact_info or not contact_info.get('email'):
            log(f"  No email for contact {contact_id}, skipping")
            stats['no_match'] += 1
            continue

        email_addr = contact_info['email']

        # Search Gmail (with caching per email address)
        if email_addr not in gmail_cache:
            log(f"  Searching Gmail for emails to {email_addr}...")
            gmail_cache[email_addr] = search_gmail_for_contact(service, email_addr)
            time.sleep(0.3)

        gmail_emails = gmail_cache[email_addr]
        if not gmail_emails:
            log(f"  No sent emails found in Gmail for {email_addr}")
            stats['no_match'] += 1
            continue

        # Find the best matching email for this game
        best = find_best_email_for_game(gmail_emails, game.get('game_date'))
        if not best:
            log(f"  No suitable email match found")
            stats['no_match'] += 1
            continue

        stats['found'] += 1
        subject_preview = best['subject'][:60] if best['subject'] else '(no subject)'
        log(f"  MATCH: \"{subject_preview}\" sent {best.get('sent_date', '?')}")

        if args.dry_run:
            log(f"  [DRY RUN] Would create Email Queue entry")
        else:
            success = create_email_queue_entry(
                notion, email_db, best, game, contact_id, contact_info
            )
            if success:
                stats['created'] += 1
                log(f"  Created Email Queue entry")
            else:
                stats['errors'] += 1
            time.sleep(0.5)  # Notion rate limiting

    # Summary
    mode = "[DRY RUN] " if args.dry_run else ""
    log("")
    log("=" * 55)
    log(f"{mode}EMAIL CONTENT BACKFILL COMPLETE")
    log("=" * 55)
    log(f"Gmail account: {gmail_account}")
    log(f"Games processed: {stats['processed']}")
    log(f"Gmail matches found: {stats['found']}")
    log(f"Email Queue entries created: {stats['created']}")
    log(f"No Gmail match: {stats['no_match']}")
    log(f"Errors: {stats['errors']}")
    log("=" * 55)

    print(json.dumps({"success": True, **stats}, indent=2))


if __name__ == "__main__":
    main()
