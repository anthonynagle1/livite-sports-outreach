#!/usr/bin/env python3
"""
Tool: backfill_email_tracking.py
Purpose: Backfill Last Emailed and Total Outreach Count on Dashboard Contacts
         from Email Queue send history.

This bridges the gap between Sports Automation (where emails are sent) and
the Livite Dashboard (where contacts are tracked for sales).

Usage:
    python tools/backfill_email_tracking.py                  # Dry-run (default)
    python tools/backfill_email_tracking.py --execute         # Full backfill
    python tools/backfill_email_tracking.py --execute --limit 10  # Test with 10

Required environment variables:
    NOTION_API_KEY              - Notion integration token
    NOTION_EMAIL_QUEUE_DB       - Email Queue database ID
    NOTION_DASHBOARD_CONTACTS_DB - Livite Dashboard Contacts DB
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

from notion_client import Client
from notion_client.errors import APIResponseError
from dotenv import load_dotenv

load_dotenv()


def query_all(notion, database_id, filter_obj=None):
    """Query all pages from a Notion database, handling pagination."""
    all_results = []
    has_more = True
    start_cursor = None
    while has_more:
        kwargs = {"database_id": database_id}
        if filter_obj:
            kwargs["filter"] = filter_obj
        if start_cursor:
            kwargs["start_cursor"] = start_cursor
        response = notion.databases.query(**kwargs)
        all_results.extend(response['results'])
        has_more = response.get('has_more', False)
        start_cursor = response.get('next_cursor')
    return all_results


def build_email_history(notion, email_queue_db):
    """Build a map of email_address -> {count, last_date} from all sent emails."""
    print("Loading Email Queue (sent emails)...", file=sys.stderr)
    sent_emails = query_all(notion, email_queue_db, filter_obj={
        "property": "Status",
        "select": {"equals": "Sent"}
    })

    # Also include Responded/Booked (these were sent too)
    for status in ["Responded", "Booked"]:
        extras = query_all(notion, email_queue_db, filter_obj={
            "property": "Status",
            "select": {"equals": status}
        })
        sent_emails.extend(extras)

    print(f"  Found {len(sent_emails)} sent/responded/booked emails", file=sys.stderr)

    history = {}  # email_addr -> {count, last_date, dates}
    for entry in sent_emails:
        props = entry['properties']
        to_email = (props.get('To Email', {}).get('email') or '').lower().strip()
        sent_at = (props.get('Sent At', {}).get('date') or {}).get('start', '')

        if not to_email:
            continue

        if to_email not in history:
            history[to_email] = {'count': 0, 'last_date': '', 'dates': []}

        history[to_email]['count'] += 1
        if sent_at:
            history[to_email]['dates'].append(sent_at)
            if sent_at > history[to_email]['last_date']:
                history[to_email]['last_date'] = sent_at

    print(f"  {len(history)} unique email addresses with send history", file=sys.stderr)
    return history


def backfill(notion, email_queue_db, dashboard_contacts_db, execute=False, limit=None):
    """Backfill Last Emailed and Total Outreach Count on Dashboard Contacts."""
    # Phase 1: Build email history from Email Queue
    history = build_email_history(notion, email_queue_db)

    # Phase 2: Load Dashboard Contacts
    print("Loading Dashboard Contacts...", file=sys.stderr)
    contacts = query_all(notion, dashboard_contacts_db)
    print(f"  Found {len(contacts)} contacts", file=sys.stderr)

    # Phase 3: Match and update
    matched = 0
    updated = 0
    skipped_no_history = 0
    errors = 0

    contacts_to_update = []
    for c in contacts:
        email = (c['properties'].get('Email Address', {}).get('email') or '').lower().strip()
        if not email or email not in history:
            skipped_no_history += 1
            continue

        h = history[email]
        matched += 1
        contacts_to_update.append({
            'id': c['id'],
            'email': email,
            'count': h['count'],
            'last_date': h['last_date'],
        })

    if limit:
        contacts_to_update = contacts_to_update[:limit]

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"BACKFILL {'PREVIEW' if not execute else 'EXECUTING'}", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)
    print(f"Dashboard contacts with email history: {matched}", file=sys.stderr)
    print(f"Dashboard contacts without history:    {skipped_no_history}", file=sys.stderr)
    print(f"To update: {len(contacts_to_update)}", file=sys.stderr)

    if not execute:
        print(f"\nPREVIEW (first 20):", file=sys.stderr)
        for c in contacts_to_update[:20]:
            print(f"  {c['email']:<40} count={c['count']}  last={c['last_date']}", file=sys.stderr)
        if len(contacts_to_update) > 20:
            print(f"  ... and {len(contacts_to_update) - 20} more", file=sys.stderr)
        print(f"\nTo execute: python tools/backfill_email_tracking.py --execute", file=sys.stderr)
        return {'matched': matched, 'updated': 0}

    # Execute updates
    for i, c in enumerate(contacts_to_update):
        properties = {
            "Total Outreach Count": {"number": c['count']},
        }
        if c['last_date']:
            properties["Last Emailed"] = {"date": {"start": c['last_date']}}

        try:
            notion.pages.update(page_id=c['id'], properties=properties)
            updated += 1
            time.sleep(0.35)
            if (i + 1) % 25 == 0 or (i + 1) == len(contacts_to_update):
                print(f"  Progress: {i + 1}/{len(contacts_to_update)} ({updated} updated, {errors} errors)", file=sys.stderr)
        except APIResponseError as e:
            errors += 1
            print(f"  ERROR updating {c['email']}: {e}", file=sys.stderr)

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"BACKFILL COMPLETE", file=sys.stderr)
    print(f"  Updated: {updated}", file=sys.stderr)
    print(f"  Errors: {errors}", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)

    return {'matched': matched, 'updated': updated, 'errors': errors}


def main():
    parser = argparse.ArgumentParser(description="Backfill email tracking on Dashboard Contacts")
    parser.add_argument('--execute', action='store_true', help="Actually update contacts (default is dry-run)")
    parser.add_argument('--limit', type=int, help="Limit number of contacts to update")
    args = parser.parse_args()

    required = ['NOTION_API_KEY', 'NOTION_EMAIL_QUEUE_DB', 'NOTION_DASHBOARD_CONTACTS_DB']
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        print(f"Error: Missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    notion = Client(auth=os.getenv('NOTION_API_KEY'))
    email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB')
    dashboard_contacts_db = os.getenv('NOTION_DASHBOARD_CONTACTS_DB')

    result = backfill(notion, email_queue_db, dashboard_contacts_db,
                      execute=args.execute, limit=args.limit)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
