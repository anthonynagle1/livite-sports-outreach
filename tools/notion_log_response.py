#!/usr/bin/env python3
"""
Tool: notion_log_response.py
Purpose: Log responses to emails and update CRM status

Simple workflow:
1. List recent sent emails to find which one got a response
2. Update status to Responded/Booked/Declined
3. Updates Game outreach status automatically

Usage:
    # List recent sent emails (to find which one got a response)
    python tools/notion_log_response.py --list

    # Log a response by email ID
    python tools/notion_log_response.py --email-id abc123 --status Responded
    python tools/notion_log_response.py --email-id abc123 --status Booked --notes "Confirmed 35 players"
    python tools/notion_log_response.py --email-id abc123 --status Declined --notes "Using their own catering"

    # Quick search by school name and log response
    python tools/notion_log_response.py --school "Boston College" --status Responded
"""

import argparse
import json
import os
import sys
from datetime import datetime

try:
    from notion_client import Client
    from notion_client.errors import APIResponseError
except ImportError:
    print("Error: notion-client not installed", file=sys.stderr)
    sys.exit(1)

from dotenv import load_dotenv
load_dotenv()


def get_notion_client():
    api_key = os.getenv('NOTION_API_KEY')
    if not api_key:
        print("Error: NOTION_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    return Client(auth=api_key)


def extract_title(title_array):
    if not title_array:
        return ''
    return ''.join(item.get('plain_text', '') for item in title_array)


def extract_text(rich_text_array):
    if not rich_text_array:
        return ''
    return ''.join(item.get('plain_text', '') for item in rich_text_array)


def list_sent_emails(notion, email_queue_db, limit=20):
    """List recently sent emails."""
    try:
        response = notion.databases.query(
            database_id=email_queue_db,
            filter={
                "property": "Status",
                "select": {"equals": "Sent"}
            },
            sorts=[
                {"property": "Sent At", "direction": "descending"}
            ],
            page_size=limit
        )

        emails = response['results']
        if not emails:
            print("No sent emails found.")
            return

        print(f"\n{'='*70}")
        print("RECENTLY SENT EMAILS")
        print(f"{'='*70}\n")

        for i, email in enumerate(emails, 1):
            props = email['properties']
            email_id = email['id'][:8]  # Short ID for display
            subject = extract_text(props.get('Subject', {}).get('rich_text', []))[:50]
            sent_at = props.get('Sent At', {}).get('date', {})
            sent_date = sent_at.get('start', 'Unknown') if sent_at else 'Unknown'

            # Get contact name if linked
            contact_name = "Unknown"
            if props.get('Contact', {}).get('relation'):
                try:
                    contact_id = props['Contact']['relation'][0]['id']
                    contact = notion.pages.retrieve(page_id=contact_id)
                    contact_name = extract_title(contact['properties'].get('Name', {}).get('title', []))
                except:
                    pass

            print(f"{i}. [{email_id}] {contact_name}")
            print(f"   Subject: {subject}...")
            print(f"   Sent: {sent_date}")
            print(f"   Full ID: {email['id']}")
            print()

        print("Use: --email-id <ID> --status <Responded|Booked|Declined>")

    except APIResponseError as e:
        print(f"Error querying emails: {e}", file=sys.stderr)


def find_email_by_school(notion, email_queue_db, school_name):
    """Find most recent sent email to a school."""
    try:
        response = notion.databases.query(
            database_id=email_queue_db,
            filter={
                "property": "Status",
                "select": {"equals": "Sent"}
            },
            sorts=[
                {"property": "Sent At", "direction": "descending"}
            ],
            page_size=50
        )

        school_lower = school_name.lower()

        for email in response['results']:
            props = email['properties']
            subject = extract_text(props.get('Subject', {}).get('rich_text', []))

            # Check if school name appears in subject
            if school_lower in subject.lower():
                return email

            # Check contact's school
            if props.get('Contact', {}).get('relation'):
                try:
                    contact_id = props['Contact']['relation'][0]['id']
                    contact = notion.pages.retrieve(page_id=contact_id)

                    if contact['properties'].get('School', {}).get('relation'):
                        school_id = contact['properties']['School']['relation'][0]['id']
                        school = notion.pages.retrieve(page_id=school_id)
                        school_name_db = extract_title(school['properties'].get('School Name', {}).get('title', []))

                        if school_lower in school_name_db.lower():
                            return email
                except:
                    continue

        return None

    except APIResponseError as e:
        print(f"Error searching: {e}", file=sys.stderr)
        return None


def log_response(notion, email_queue_db, games_db, email_id, status, notes=None):
    """Log a response and update all related records."""
    valid_statuses = ['Responded', 'Booked', 'Declined']
    if status not in valid_statuses:
        print(f"Error: Status must be one of {valid_statuses}", file=sys.stderr)
        return False

    try:
        # Get the email
        email = notion.pages.retrieve(page_id=email_id)
        props = email['properties']

        subject = extract_text(props.get('Subject', {}).get('rich_text', []))
        print(f"\nUpdating: {subject[:50]}...")

        # Update Email Queue status
        notion.pages.update(
            page_id=email_id,
            properties={
                "Status": {"select": {"name": status}}
            }
        )
        print(f"  Email status --> {status}")

        # Update linked Game's outreach status
        if props.get('Game', {}).get('relation'):
            game_id = props['Game']['relation'][0]['id']

            game_updates = {
                "Outreach Status": {"select": {"name": status}}
            }

            # Add notes to game if provided
            if notes:
                game_updates["Notes"] = {"rich_text": [{"text": {"content": notes}}]}

            notion.pages.update(page_id=game_id, properties=game_updates)
            print(f"  Game status --> {status}")

            if notes:
                print(f"  Added notes to game")

        print(f"\nResponse logged successfully!")
        return True

    except APIResponseError as e:
        print(f"Error: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Log email responses")
    parser.add_argument("--list", action="store_true", help="List recent sent emails")
    parser.add_argument("--email-id", help="Email ID to update")
    parser.add_argument("--school", help="Find email by school name")
    parser.add_argument("--status", choices=['Responded', 'Booked', 'Declined'],
                        help="Response status")
    parser.add_argument("--notes", help="Optional notes about the response")

    args = parser.parse_args()

    notion = get_notion_client()
    email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB')
    games_db = os.getenv('NOTION_GAMES_DB')

    if not email_queue_db:
        print("Error: NOTION_EMAIL_QUEUE_DB not set", file=sys.stderr)
        sys.exit(1)

    if args.list:
        list_sent_emails(notion, email_queue_db)
        return

    # Find email by school name
    if args.school and not args.email_id:
        email = find_email_by_school(notion, email_queue_db, args.school)
        if email:
            subject = extract_text(email['properties'].get('Subject', {}).get('rich_text', []))
            print(f"Found email: {subject[:50]}...")
            args.email_id = email['id']
        else:
            print(f"No sent email found for school: {args.school}")
            return

    if args.email_id and args.status:
        success = log_response(notion, email_queue_db, games_db, args.email_id, args.status, args.notes)
        result = {"success": success, "status": args.status}
        print(json.dumps(result, indent=2))
    elif not args.list:
        parser.print_help()


if __name__ == "__main__":
    main()
