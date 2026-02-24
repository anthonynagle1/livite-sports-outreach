#!/usr/bin/env python3
"""
Tool: backfill_sent_history.py
Purpose: Cross-reference Gmail sent history with Notion Contacts to mark
         who has already been contacted manually (before the CRM system).

This prevents the system from sending "first time" outreach to coaches
who were already emailed manually.

What it does:
1. Loads all contacts from Notion Contacts DB (email addresses)
2. Searches Gmail sent folder for emails TO each contact
3. For matches: updates Contact's "Last Emailed" date
4. For matches: updates associated Games' Outreach Status → "Email Sent"

Usage:
    # Dry run — show what would be updated
    python tools/backfill_sent_history.py --dry-run

    # Actually update Notion
    python tools/backfill_sent_history.py

    # Only check specific sport
    python tools/backfill_sent_history.py --sport Baseball
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

try:
    from notion_client import Client
    from notion_client.errors import APIResponseError
except ImportError:
    print("Error: notion-client not installed", file=sys.stderr)
    sys.exit(1)

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notion_send_gmail import get_gmail_service

load_dotenv()


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr)


def get_all_contacts(notion, contacts_db, sport_filter=None):
    """Get all contacts with email addresses from Notion."""
    contacts = []
    has_more = True
    start_cursor = None

    while has_more:
        kwargs = {
            'database_id': contacts_db,
            'filter': {
                "property": "Email",
                "email": {"is_not_empty": True}
            }
        }
        if start_cursor:
            kwargs['start_cursor'] = start_cursor

        response = notion.databases.query(**kwargs)

        for page in response['results']:
            props = page['properties']
            email = props.get('Email', {}).get('email', '')
            name = ''.join(t.get('plain_text', '') for t in props.get('Name', {}).get('title', []))
            sport_select = props.get('Sport', {}).get('select')
            sport = sport_select.get('name', '') if sport_select else ''

            if sport_filter and sport != sport_filter:
                continue

            if email:
                contacts.append({
                    'id': page['id'],
                    'name': name,
                    'email': email.lower().strip(),
                    'sport': sport,
                    'last_emailed': props.get('Last Emailed', {}).get('date', {}).get('start') if props.get('Last Emailed', {}).get('date') else None
                })

        has_more = response.get('has_more', False)
        start_cursor = response.get('next_cursor')

    return contacts


def search_gmail_sent(service, to_email):
    """Search Gmail sent folder for emails to a specific address.

    Returns dict with most recent and earliest sent dates, or None if never emailed.
    """
    try:
        query = f"to:{to_email} in:sent"
        # Get all messages to find both newest and oldest
        all_msg_ids = []
        response = service.users().messages().list(
            userId='me', q=query, maxResults=100
        ).execute()
        all_msg_ids.extend(response.get('messages', []))

        if not all_msg_ids:
            return None

        # Most recent is first in the list (Gmail default order)
        newest_msg = service.users().messages().get(
            userId='me', id=all_msg_ids[0]['id'],
            format='metadata', metadataHeaders=['Date', 'Subject']
        ).execute()

        newest_date = None
        internal_date = newest_msg.get('internalDate', '')
        if internal_date:
            dt = datetime.fromtimestamp(int(internal_date) / 1000)
            newest_date = dt.strftime('%Y-%m-%d')

        # Oldest is last in the list
        oldest_date = newest_date  # Default to same if only 1 message
        if len(all_msg_ids) > 1:
            oldest_msg = service.users().messages().get(
                userId='me', id=all_msg_ids[-1]['id'],
                format='metadata', metadataHeaders=['Date']
            ).execute()
            oldest_internal = oldest_msg.get('internalDate', '')
            if oldest_internal:
                dt = datetime.fromtimestamp(int(oldest_internal) / 1000)
                oldest_date = dt.strftime('%Y-%m-%d')

        if newest_date:
            return {
                'date': newest_date,           # Most recent email
                'first_date': oldest_date,     # Earliest email ever
                'total_emails': len(all_msg_ids),
                'message_id': newest_msg['id'],
                'thread_id': newest_msg.get('threadId', ''),
            }

        return None

    except Exception as e:
        log(f"  Gmail search error for {to_email}: {e}")
        return None


def get_games_for_contact(notion, games_db, contact_id):
    """Find all games linked to a contact."""
    try:
        response = notion.databases.query(
            database_id=games_db,
            filter={
                "property": "Contact",
                "relation": {"contains": contact_id}
            }
        )
        return response['results']
    except APIResponseError as e:
        log(f"  Error querying games: {e}")
        return []


def update_contact_and_games(notion, games_db, contact, sent_info, dry_run=False):
    """Update a contact's dates and Relationship status. Does NOT touch game Outreach Status."""
    updated = {'contact': False, 'games': 0}

    first_date = sent_info.get('first_date', sent_info['date'])
    total = sent_info.get('total_emails', 1)

    if dry_run:
        log(f"  [DRY RUN] Would update Contact '{contact['name']}' → Previously Contacted, "
            f"First Emailed → {first_date}, Last Emailed → {sent_info['date']} ({total} emails total)")
    else:
        try:
            notion.pages.update(
                page_id=contact['id'],
                properties={
                    "Last Emailed": {"date": {"start": sent_info['date']}},
                    "First Emailed": {"date": {"start": first_date}},
                    "Relationship": {"select": {"name": "Previously Contacted"}},
                }
            )
            updated['contact'] = True
        except APIResponseError as e:
            log(f"  Error updating contact: {e}")

    return updated


def main():
    parser = argparse.ArgumentParser(description="Backfill sent email history from Gmail")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated")
    parser.add_argument("--sport", help="Only check contacts for a specific sport")
    args = parser.parse_args()

    notion = Client(auth=os.getenv('NOTION_API_KEY'))
    contacts_db = os.getenv('NOTION_CONTACTS_DB')
    games_db = os.getenv('NOTION_GAMES_DB')

    if not contacts_db or not games_db:
        log("Error: NOTION_CONTACTS_DB and NOTION_GAMES_DB must be set")
        sys.exit(1)

    # Get Gmail service
    log("Connecting to Gmail...")
    service = get_gmail_service()
    profile = service.users().getProfile(userId='me').execute()
    gmail_account = profile['emailAddress']
    log(f"Scanning sent folder for: {gmail_account}")

    # Load all contacts
    log("Loading contacts from Notion...")
    contacts = get_all_contacts(notion, contacts_db, sport_filter=args.sport)
    log(f"Found {len(contacts)} contacts with email addresses")

    # Skip contacts already marked as emailed
    not_yet_emailed = [c for c in contacts if not c['last_emailed']]
    already_emailed = len(contacts) - len(not_yet_emailed)
    log(f"  Already marked as emailed: {already_emailed}")
    log(f"  Need to check: {len(not_yet_emailed)}")

    if not not_yet_emailed:
        log("No contacts to check!")
        return

    # Cross-reference with Gmail
    stats = {'checked': 0, 'found': 0, 'contacts_updated': 0, 'games_updated': 0}

    for contact in not_yet_emailed:
        stats['checked'] += 1
        if stats['checked'] % 25 == 0:
            log(f"  Progress: {stats['checked']}/{len(not_yet_emailed)} checked, {stats['found']} matches found...")

        sent_info = search_gmail_sent(service, contact['email'])

        if sent_info:
            stats['found'] += 1
            log(f"  MATCH: {contact['name']} ({contact['email']}) — last emailed {sent_info['date']}")

            result = update_contact_and_games(notion, games_db, contact, sent_info, dry_run=args.dry_run)
            if result['contact']:
                stats['contacts_updated'] += 1
            stats['games_updated'] += result['games']

            time.sleep(0.5)  # Gmail rate limiting
        else:
            time.sleep(0.2)  # Light rate limiting for searches

    # Summary
    mode = "[DRY RUN] " if args.dry_run else ""
    log("")
    log("=" * 50)
    log(f"{mode}BACKFILL COMPLETE")
    log("=" * 50)
    log(f"Gmail account scanned: {gmail_account}")
    log(f"Contacts checked: {stats['checked']}")
    log(f"Previously emailed: {stats['found']}")
    log(f"Contacts updated: {stats['contacts_updated']}")
    log(f"Games updated: {stats['games_updated']}")
    log("=" * 50)

    print(json.dumps({"success": True, **stats}, indent=2))


if __name__ == "__main__":
    main()
