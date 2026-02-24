#!/usr/bin/env python3
"""
Tool: notion_send_email.py
Purpose: Send approved emails from the Notion Email Queue via SendGrid

This tool:
1. Fetches email details from Notion Email Queue
2. Sends the email via SendGrid
3. Updates Email Queue status to "Sent"
4. Updates Game.Last Contacted and Game.Follow-up Date
5. Updates Contact.Last Emailed

Usage:
    # Send a specific email by page ID
    python tools/notion_send_email.py --email-id abc123

    # Process all approved emails in queue
    python tools/notion_send_email.py --process-approved

Required environment variables:
    NOTION_API_KEY - Notion integration token
    NOTION_EMAIL_QUEUE_DB - Email Queue database ID
    NOTION_GAMES_DB - Games database ID
    NOTION_CONTACTS_DB - Contacts database ID
    SENDGRID_API_KEY - SendGrid API key
    FROM_EMAIL - Sender email address
    FROM_NAME - Sender display name
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

try:
    from notion_client import Client
    from notion_client.errors import APIResponseError
except ImportError:
    print("Error: notion-client package not installed.", file=sys.stderr)
    print("Run: pip install notion-client", file=sys.stderr)
    sys.exit(1)

try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Email, To, Content
except ImportError:
    print("Error: sendgrid package not installed.", file=sys.stderr)
    print("Run: pip install sendgrid", file=sys.stderr)
    sys.exit(1)

from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def get_notion_client():
    """Initialize Notion client."""
    api_key = os.getenv('NOTION_API_KEY')
    if not api_key:
        print("Error: NOTION_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    return Client(auth=api_key)


def get_sendgrid_client():
    """Initialize SendGrid client."""
    api_key = os.getenv('SENDGRID_API_KEY')
    if not api_key:
        print("Error: SENDGRID_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    return SendGridAPIClient(api_key)


def get_database_ids():
    """Get required database IDs from environment."""
    email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB')
    games_db = os.getenv('NOTION_GAMES_DB')
    contacts_db = os.getenv('NOTION_CONTACTS_DB')

    missing = []
    if not email_queue_db:
        missing.append('NOTION_EMAIL_QUEUE_DB')
    if not games_db:
        missing.append('NOTION_GAMES_DB')
    if not contacts_db:
        missing.append('NOTION_CONTACTS_DB')

    if missing:
        print(f"Error: Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    return email_queue_db, games_db, contacts_db


def get_email_sender():
    """Get sender email and name from environment."""
    from_email = os.getenv('FROM_EMAIL', 'outreach@livite.com')
    from_name = os.getenv('FROM_NAME', 'Livite Sports Catering')
    return from_email, from_name


def extract_text_from_rich_text(rich_text_array):
    """Extract plain text from Notion rich text array."""
    if not rich_text_array:
        return ''
    return ''.join(item.get('plain_text', '') for item in rich_text_array)


def get_email_details(notion, email_queue_db, email_page_id):
    """
    Fetch email details from Notion Email Queue.

    Returns dict with: to_email, subject, body, game_id, contact_id
    """
    try:
        page = notion.pages.retrieve(page_id=email_page_id)
        properties = page['properties']

        # Extract subject
        subject = ''
        if 'Subject' in properties:
            subject = extract_text_from_rich_text(properties['Subject'].get('rich_text', []))

        # Extract body
        body = ''
        if 'Body' in properties:
            body = extract_text_from_rich_text(properties['Body'].get('rich_text', []))

        # Get contact relation to find email address
        contact_id = None
        to_email = ''
        if 'Contact' in properties and properties['Contact'].get('relation'):
            contact_id = properties['Contact']['relation'][0]['id']
            # Fetch contact to get email
            contact_page = notion.pages.retrieve(page_id=contact_id)
            if 'Email' in contact_page['properties']:
                to_email = contact_page['properties']['Email'].get('email', '')

        # Get game relation
        game_id = None
        if 'Game' in properties and properties['Game'].get('relation'):
            game_id = properties['Game']['relation'][0]['id']

        # Get current status
        status = ''
        if 'Status' in properties and properties['Status'].get('status'):
            status = properties['Status']['status'].get('name', '')

        return {
            'page_id': email_page_id,
            'to_email': to_email,
            'subject': subject,
            'body': body,
            'game_id': game_id,
            'contact_id': contact_id,
            'status': status
        }

    except APIResponseError as e:
        print(f"Error fetching email details: {e}", file=sys.stderr)
        return None


def send_email_via_sendgrid(sg_client, to_email, subject, body, from_email, from_name):
    """
    Send email via SendGrid.

    Returns True if successful, False otherwise.
    """
    try:
        message = Mail(
            from_email=Email(from_email, from_name),
            to_emails=To(to_email),
            subject=subject,
            plain_text_content=Content("text/plain", body)
        )

        response = sg_client.send(message)

        if response.status_code in (200, 201, 202):
            print(f"  Email sent successfully to {to_email}", file=sys.stderr)
            return True
        else:
            print(f"  SendGrid error: {response.status_code} - {response.body}", file=sys.stderr)
            return False

    except Exception as e:
        print(f"  Error sending email: {e}", file=sys.stderr)
        return False


def update_email_queue_status(notion, email_page_id, status, sent_at=None):
    """Update the Email Queue entry status."""
    try:
        properties = {
            "Status": {"status": {"name": status}}
        }

        if sent_at:
            properties["Sent At"] = {"date": {"start": sent_at}}

        notion.pages.update(
            page_id=email_page_id,
            properties=properties
        )
        print(f"  Updated Email Queue status: {status}", file=sys.stderr)
        return True

    except APIResponseError as e:
        print(f"  Error updating Email Queue: {e}", file=sys.stderr)
        return False


def update_game_outreach(notion, game_page_id):
    """Update the Game's Last Contacted and Follow-up Date."""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        followup = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')

        notion.pages.update(
            page_id=game_page_id,
            properties={
                "Last Contacted": {"date": {"start": today}},
                "Follow-up Date": {"date": {"start": followup}},
                "Outreach Status": {"select": {"name": "Email Sent"}}
            }
        )
        print(f"  Updated Game: Last Contacted={today}, Follow-up={followup}", file=sys.stderr)
        return True

    except APIResponseError as e:
        print(f"  Error updating Game: {e}", file=sys.stderr)
        return False


def update_contact_last_emailed(notion, contact_page_id):
    """Update the Contact's Last Emailed date."""
    try:
        today = datetime.now().strftime('%Y-%m-%d')

        notion.pages.update(
            page_id=contact_page_id,
            properties={
                "Last Emailed": {"date": {"start": today}}
            }
        )
        print(f"  Updated Contact: Last Emailed={today}", file=sys.stderr)
        return True

    except APIResponseError as e:
        print(f"  Error updating Contact: {e}", file=sys.stderr)
        return False


def process_single_email(notion, sg_client, email_queue_db, games_db, contacts_db, email_page_id):
    """
    Process and send a single email from the queue.
    """
    print(f"\nProcessing email: {email_page_id}", file=sys.stderr)

    # Get email details
    email_data = get_email_details(notion, email_queue_db, email_page_id)
    if not email_data:
        return False

    # Check status
    if email_data['status'] not in ('Approved', 'Draft'):
        print(f"  Skipping: Status is '{email_data['status']}' (need 'Approved')", file=sys.stderr)
        return False

    # Validate email data
    if not email_data['to_email']:
        print("  Error: No recipient email address", file=sys.stderr)
        update_email_queue_status(notion, email_page_id, 'Failed')
        return False

    if not email_data['subject']:
        print("  Error: No subject line", file=sys.stderr)
        update_email_queue_status(notion, email_page_id, 'Failed')
        return False

    # Get sender info
    from_email, from_name = get_email_sender()

    # Send the email
    print(f"  Sending to: {email_data['to_email']}", file=sys.stderr)
    print(f"  Subject: {email_data['subject']}", file=sys.stderr)

    success = send_email_via_sendgrid(
        sg_client,
        email_data['to_email'],
        email_data['subject'],
        email_data['body'],
        from_email,
        from_name
    )

    if success:
        # Update Email Queue
        sent_at = datetime.now().isoformat()
        update_email_queue_status(notion, email_page_id, 'Sent', sent_at)

        # Update Game
        if email_data['game_id']:
            update_game_outreach(notion, email_data['game_id'])

        # Update Contact
        if email_data['contact_id']:
            update_contact_last_emailed(notion, email_data['contact_id'])

        return True
    else:
        update_email_queue_status(notion, email_page_id, 'Failed')
        return False


def process_approved_emails(notion, sg_client, email_queue_db, games_db, contacts_db):
    """
    Process all emails in the queue with 'Approved' status.
    """
    print("Fetching approved emails from queue...", file=sys.stderr)

    try:
        response = notion.databases.query(
            database_id=email_queue_db,
            filter={
                "property": "Status",
                "status": {
                    "equals": "Approved"
                }
            }
        )

        emails = response['results']
        print(f"Found {len(emails)} approved emails", file=sys.stderr)

        if not emails:
            print("No approved emails to process.", file=sys.stderr)
            return {'sent': 0, 'failed': 0}

        stats = {'sent': 0, 'failed': 0}

        for email_page in emails:
            email_id = email_page['id']
            success = process_single_email(
                notion, sg_client, email_queue_db, games_db, contacts_db, email_id
            )
            if success:
                stats['sent'] += 1
            else:
                stats['failed'] += 1

        return stats

    except APIResponseError as e:
        print(f"Error querying Email Queue: {e}", file=sys.stderr)
        return {'sent': 0, 'failed': 0}


def main():
    parser = argparse.ArgumentParser(
        description="Send emails from Notion Email Queue via SendGrid"
    )
    parser.add_argument(
        "--email-id",
        help="Specific email page ID to send"
    )
    parser.add_argument(
        "--process-approved",
        action="store_true",
        help="Process all approved emails in queue"
    )

    args = parser.parse_args()

    if not args.email_id and not args.process_approved:
        print("Error: Must specify --email-id or --process-approved", file=sys.stderr)
        sys.exit(1)

    # Initialize clients
    notion = get_notion_client()
    sg_client = get_sendgrid_client()
    email_queue_db, games_db, contacts_db = get_database_ids()

    if args.email_id:
        # Process single email
        success = process_single_email(
            notion, sg_client, email_queue_db, games_db, contacts_db, args.email_id
        )
        result = {"success": success, "email_id": args.email_id}
        print(json.dumps(result, indent=2))
        sys.exit(0 if success else 1)

    elif args.process_approved:
        # Process all approved emails
        stats = process_approved_emails(
            notion, sg_client, email_queue_db, games_db, contacts_db
        )

        print(f"\n{'='*60}", file=sys.stderr)
        print("EMAIL PROCESSING COMPLETE", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        print(f"Sent: {stats['sent']}", file=sys.stderr)
        print(f"Failed: {stats['failed']}", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

        result = {"success": True, **stats}
        print(json.dumps(result, indent=2))
        sys.exit(0 if stats['failed'] == 0 else 1)


if __name__ == "__main__":
    main()
