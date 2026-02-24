#!/usr/bin/env python3
"""
Tool: notion_process_approved.py
Purpose: Poll Notion Email Queue for approved emails and send them via Gmail

This is the core of the drag-to-send workflow:
1. User creates draft in Email Queue
2. User reviews and drags card from "Draft" to "Approved"
3. This script (via cron) picks up approved emails and sends them
4. Updates Email Queue status to "Sent"
5. Updates Game and Contact records

Run via cron every 5 minutes:
    */5 * * * * cd /path/to/project && python tools/notion_process_approved.py

Usage:
    # Process all approved emails
    python tools/notion_process_approved.py

    # Dry run (show what would be sent)
    python tools/notion_process_approved.py --dry-run

    # Process single email by ID
    python tools/notion_process_approved.py --email-id abc123

Required environment variables:
    NOTION_API_KEY - Notion integration token
    NOTION_EMAIL_QUEUE_DB - Email Queue database ID
    NOTION_GAMES_DB - Games database ID
    NOTION_CONTACTS_DB - Contacts database ID
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

try:
    from notion_client import Client
    from notion_client.errors import APIResponseError
except ImportError:
    print("Error: notion-client package not installed.", file=sys.stderr)
    print("Run: pip install notion-client", file=sys.stderr)
    sys.exit(1)

from dotenv import load_dotenv

# Import Gmail sender from same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notion_send_gmail import send_email as send_gmail

load_dotenv()

# Rate limiting: cap sends per cron cycle to avoid Gmail throttling
MAX_SENDS_PER_CYCLE = 10
DELAY_BETWEEN_SENDS = 3  # seconds


def get_notion_client():
    """Initialize Notion client."""
    api_key = os.getenv('NOTION_API_KEY')
    if not api_key:
        print("Error: NOTION_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    return Client(auth=api_key)


def get_database_ids():
    """Get required database IDs from environment."""
    email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB')
    games_db = os.getenv('NOTION_GAMES_DB')
    contacts_db = os.getenv('NOTION_CONTACTS_DB')

    if not email_queue_db:
        print("Error: NOTION_EMAIL_QUEUE_DB not set", file=sys.stderr)
        sys.exit(1)

    return email_queue_db, games_db, contacts_db


def extract_title(title_array):
    """Extract title text from Notion title property."""
    if not title_array:
        return ''
    return ''.join(item.get('plain_text', '') for item in title_array)


def extract_text_from_rich_text(rich_text_array):
    """Extract plain text from Notion rich text array."""
    if not rich_text_array:
        return ''
    return ''.join(item.get('plain_text', '') for item in rich_text_array)


def get_approved_emails(notion, email_queue_db):
    """Query Email Queue for emails with status 'Approved' (with pagination)."""
    try:
        all_results = []
        has_more = True
        start_cursor = None
        while has_more:
            kwargs = {
                "database_id": email_queue_db,
                "filter": {
                    "property": "Status",
                    "select": {"equals": "Approved"}
                }
            }
            if start_cursor:
                kwargs["start_cursor"] = start_cursor
            response = notion.databases.query(**kwargs)
            all_results.extend(response['results'])
            has_more = response.get('has_more', False)
            start_cursor = response.get('next_cursor')
        return all_results
    except APIResponseError as e:
        print(f"Error querying Email Queue: {e}", file=sys.stderr)
        return []


def get_email_details(notion, email_page):
    """Extract all details needed to send an email."""
    props = email_page['properties']

    details = {
        'email_id': email_page['id'],
        'email_title': extract_title(props.get('Email ID', {}).get('title', [])),
        'subject': extract_text_from_rich_text(props.get('Subject', {}).get('rich_text', [])),
        'body': extract_text_from_rich_text(props.get('Body', {}).get('rich_text', [])),
        'to_email': '',
        'contact_id': None,
        'contact_name': '',
        'game_id': None
    }

    # Get recipient email: try To Email property first, then fall back to Contact relation
    details['to_email'] = props.get('To Email', {}).get('email', '') or ''

    if 'Contact' in props and props['Contact'].get('relation'):
        details['contact_id'] = props['Contact']['relation'][0]['id']
        try:
            contact = notion.pages.retrieve(page_id=details['contact_id'])
            contact_props = contact['properties']
            if not details['to_email']:
                details['to_email'] = contact_props.get('Email', {}).get('email', '')
            details['contact_name'] = extract_title(contact_props.get('Name', {}).get('title', []))
        except:
            pass

    # Get game ID
    if 'Game' in props and props['Game'].get('relation'):
        details['game_id'] = props['Game']['relation'][0]['id']

    return details


def update_email_queue_sent(notion, email_page_id, gmail_message_id=None, gmail_thread_id=None):
    """Update Email Queue entry to Sent status with Gmail tracking IDs."""
    try:
        now = datetime.now().isoformat()
        properties = {
            "Status": {"select": {"name": "Sent"}},
            "Sent At": {"date": {"start": now}}
        }

        if gmail_message_id:
            properties["Gmail Message ID"] = {
                "rich_text": [{"text": {"content": gmail_message_id}}]
            }
        if gmail_thread_id:
            properties["Gmail Thread ID"] = {
                "rich_text": [{"text": {"content": gmail_thread_id}}]
            }

        notion.pages.update(
            page_id=email_page_id,
            properties=properties
        )
        return True
    except APIResponseError as e:
        print(f"  Error updating Email Queue: {e}", file=sys.stderr)
        return False


def update_email_queue_failed(notion, email_page_id, error_message):
    """Update Email Queue entry to Failed status."""
    try:
        notion.pages.update(
            page_id=email_page_id,
            properties={
                "Status": {"select": {"name": "Failed"}}
            }
        )
        return True
    except APIResponseError as e:
        print(f"  Error updating Email Queue: {e}", file=sys.stderr)
        return False


def update_game_outreach(notion, game_page_id):
    """Update Game's outreach tracking fields."""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        followup = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')

        props_to_update = {
            "Last Contacted": {"date": {"start": today}},
            "Follow-up Date": {"date": {"start": followup}},
            "Outreach Status": {"select": {"name": "Email Sent"}}
        }

        # Set First Contacted only if not already set
        game = notion.pages.retrieve(page_id=game_page_id)
        first_contacted = game['properties'].get('First Contacted', {}).get('date')
        if not first_contacted:
            props_to_update["First Contacted"] = {"date": {"start": today}}

        notion.pages.update(page_id=game_page_id, properties=props_to_update)
        print(f"  Updated Game: Last Contacted={today}, Follow-up={followup}", file=sys.stderr)
        return True
    except APIResponseError as e:
        print(f"  Error updating Game: {e}", file=sys.stderr)
        return False


def update_contact_last_emailed(notion, contact_page_id):
    """Update Contact's Last Emailed and First Emailed dates."""
    try:
        today = datetime.now().strftime('%Y-%m-%d')

        props_to_update = {
            "Last Emailed": {"date": {"start": today}}
        }

        # Set First Emailed only if not already set
        contact = notion.pages.retrieve(page_id=contact_page_id)
        first_emailed = contact['properties'].get('First Emailed', {}).get('date')
        if not first_emailed:
            props_to_update["First Emailed"] = {"date": {"start": today}}

        notion.pages.update(page_id=contact_page_id, properties=props_to_update)
        print(f"  Updated Contact: Last Emailed={today}", file=sys.stderr)
        return True
    except APIResponseError as e:
        print(f"  Error updating Contact: {e}", file=sys.stderr)
        return False


def update_dashboard_contact_tracking(notion, sports_contact_id, to_email=None):
    """Find and update the matching Dashboard Contact's Last Emailed + Total Outreach Count."""
    dashboard_contacts_db = os.getenv('NOTION_DASHBOARD_CONTACTS_DB')
    if not dashboard_contacts_db:
        return False

    try:
        # Get email from sports contact if not provided
        email = to_email
        if not email and sports_contact_id:
            contact = notion.pages.retrieve(page_id=sports_contact_id)
            email = (contact['properties'].get('Email', {}).get('email') or '').lower().strip()
        if not email:
            return False

        # Find Dashboard Contact by email
        response = notion.databases.query(
            database_id=dashboard_contacts_db,
            filter={"property": "Email Address", "email": {"equals": email}}
        )
        if not response['results']:
            return False

        dash_contact = response['results'][0]
        current_count = dash_contact['properties'].get('Total Outreach Count', {}).get('number', 0) or 0

        today = datetime.now().strftime('%Y-%m-%d')
        notion.pages.update(
            page_id=dash_contact['id'],
            properties={
                "Last Emailed": {"date": {"start": today}},
                "Total Outreach Count": {"number": current_count + 1},
            }
        )
        print(f"  Updated Dashboard Contact: Last Emailed={today}, Count={current_count + 1}", file=sys.stderr)
        return True
    except APIResponseError as e:
        print(f"  Warning: Could not update Dashboard Contact: {e}", file=sys.stderr)
        return False


def process_single_email(notion, email_page, dry_run=False):
    """Process a single approved email - send it and update records."""
    details = get_email_details(notion, email_page)

    print(f"\nProcessing: {details['email_title']}", file=sys.stderr)
    print(f"  To: {details['to_email']}", file=sys.stderr)
    print(f"  Subject: {details['subject'][:50]}...", file=sys.stderr)

    # Validate
    if not details['to_email']:
        print("  Error: No recipient email address", file=sys.stderr)
        if not dry_run:
            update_email_queue_failed(notion, details['email_id'], "No recipient email")
        return {'success': False, 'error': 'No recipient email'}

    if not details['subject']:
        print("  Error: No subject line", file=sys.stderr)
        if not dry_run:
            update_email_queue_failed(notion, details['email_id'], "No subject line")
        return {'success': False, 'error': 'No subject line'}

    if not details['body']:
        print("  Error: No email body", file=sys.stderr)
        if not dry_run:
            update_email_queue_failed(notion, details['email_id'], "No email body")
        return {'success': False, 'error': 'No email body'}

    if dry_run:
        print("  [DRY RUN] Would send this email", file=sys.stderr)
        return {'success': True, 'dry_run': True}

    # Send via Gmail
    print("  Sending via Gmail...", file=sys.stderr)
    result = send_gmail(
        to_email=details['to_email'],
        subject=details['subject'],
        body=details['body']
    )

    if result['success']:
        print(f"  Email sent! Message ID: {result.get('message_id', 'N/A')}, Thread ID: {result.get('thread_id', 'N/A')}", file=sys.stderr)

        # Update Email Queue with Gmail tracking IDs
        update_email_queue_sent(
            notion, details['email_id'],
            gmail_message_id=result.get('message_id'),
            gmail_thread_id=result.get('thread_id')
        )

        # Update Game
        if details['game_id']:
            update_game_outreach(notion, details['game_id'])

        # Update Contact
        if details['contact_id']:
            update_contact_last_emailed(notion, details['contact_id'])

        # Update Dashboard Contact (Last Emailed + Total Outreach Count)
        update_dashboard_contact_tracking(notion, details['contact_id'], to_email=details['to_email'])

        return {
            'success': True,
            'email_id': details['email_id'],
            'to': details['to_email'],
            'message_id': result.get('message_id')
        }
    else:
        print(f"  Send failed: {result.get('error', 'Unknown error')}", file=sys.stderr)
        update_email_queue_failed(notion, details['email_id'], result.get('error', 'Send failed'))
        return {
            'success': False,
            'email_id': details['email_id'],
            'error': result.get('error')
        }


def process_approved_emails(notion, email_queue_db, games_db, contacts_db, dry_run=False):
    """Main function to process all approved emails with rate limiting."""
    print(f"Checking for approved emails at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...", file=sys.stderr)

    emails = get_approved_emails(notion, email_queue_db)
    print(f"Found {len(emails)} approved email(s)", file=sys.stderr)

    if not emails:
        return {'processed': 0, 'sent': 0, 'failed': 0, 'deferred': 0}

    stats = {'processed': 0, 'sent': 0, 'failed': 0, 'deferred': 0}

    for email_page in emails:
        # Rate limit: stop after MAX_SENDS_PER_CYCLE successful sends
        if stats['sent'] >= MAX_SENDS_PER_CYCLE:
            stats['deferred'] = len(emails) - stats['processed']
            print(f"  Throttled: {stats['deferred']} email(s) deferred to next cycle", file=sys.stderr)
            break

        stats['processed'] += 1
        result = process_single_email(notion, email_page, dry_run=dry_run)

        if result.get('success'):
            stats['sent'] += 1
            # Delay between sends (skip after last one)
            if not dry_run and stats['sent'] < MAX_SENDS_PER_CYCLE:
                time.sleep(DELAY_BETWEEN_SENDS)
        else:
            stats['failed'] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Process approved emails and send via Gmail"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be sent without actually sending"
    )
    parser.add_argument(
        "--email-id",
        help="Process a specific email by page ID"
    )

    args = parser.parse_args()

    # Initialize
    notion = get_notion_client()
    email_queue_db, games_db, contacts_db = get_database_ids()

    if args.email_id:
        # Process single email
        try:
            email_page = notion.pages.retrieve(page_id=args.email_id)
            result = process_single_email(notion, email_page, dry_run=args.dry_run)
            print(json.dumps(result, indent=2))
            sys.exit(0 if result['success'] else 1)
        except APIResponseError as e:
            print(f"Error: Could not find email {args.email_id}: {e}", file=sys.stderr)
            sys.exit(1)

    # Process all approved
    stats = process_approved_emails(
        notion, email_queue_db, games_db, contacts_db,
        dry_run=args.dry_run
    )

    # Print summary
    mode = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"{mode}EMAIL PROCESSING COMPLETE", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"Processed: {stats['processed']}", file=sys.stderr)
    print(f"Sent: {stats['sent']}", file=sys.stderr)
    print(f"Failed: {stats['failed']}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    result = {"success": True, **stats}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
