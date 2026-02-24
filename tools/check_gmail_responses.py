#!/usr/bin/env python3
"""
Tool: check_gmail_responses.py
Purpose: Monitor Gmail for replies to sent outreach emails and auto-update Notion

Workflow:
1. Query Notion Email Queue for all "Sent" emails with a Gmail Thread ID
2. For each thread, check Gmail API for additional messages
3. If a reply is found (message from someone other than us), update:
   - Email Queue: Status -> "Responded"/"Booked"/"Declined", Response Date set
   - Game: Outreach Status -> same
4. Auto-classification via keyword matching (defaults to "Responded")

Usage:
    python tools/check_gmail_responses.py              # Check all sent emails
    python tools/check_gmail_responses.py --dry-run    # Show what would be updated
    python tools/check_gmail_responses.py --backfill   # Backfill Thread IDs for old emails
"""

import argparse
import json
import os
import sys
from datetime import datetime

from notion_client import Client
from notion_client.errors import APIResponseError
from dotenv import load_dotenv

# Reuse Gmail auth from existing tool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notion_send_gmail import get_gmail_service

load_dotenv()


# Response Type classification keywords (checked in priority order)
# Maps to Notion "Response Type" select: Booked, Interested, Not Interested, Question, Out of Office
OUT_OF_OFFICE_KEYWORDS = [
    'out of office', 'out of the office', 'ooo', 'auto-reply', 'auto reply',
    'automatic reply', 'away from', 'on vacation', 'on leave', 'on holiday',
    'limited access to email', 'will be out', 'currently out', 'not in the office',
    'returning on', 'i am currently away', 'i will be away',
    'will respond when i return', 'no longer with',
]
BOOKED_KEYWORDS = [
    'place an order', 'place the order', 'love to order',
    'head count', 'headcount', 'how many players',
    'set that up', 'set it up', 'count us in', "we're in",
    'sign us up', 'confirmed', 'book it', 'reserve it',
    'go ahead and order', 'we would like to order',
]
NOT_INTERESTED_KEYWORDS = [
    'decline', 'pass on', 'not interested', 'no thanks', 'no thank',
    "won't need", 'already have', 'own catering', 'not this time',
    'not at this time', 'no need', "don't need", 'not looking',
    'all set', "we're good", 'we are good', 'not right now',
    'already arranged', 'taken care of', 'covered',
]
QUESTION_KEYWORDS = [
    'how much', 'pricing', 'menu', 'what do you offer', 'what are your',
    'options', 'can you send', 'send me', 'more info', 'more information',
    'what would', 'how does', 'how do you',
]
INTERESTED_KEYWORDS = [
    'interested', 'sounds interesting', 'sounds great', 'sounds good',
    'like to learn', 'tell me more', 'give me a call', 'call me',
    "let's chat", "let's talk", "let's do it", 'love to', 'love food',
    'love to have', 'go ahead', 'move forward', 'yes please',
    'reach out to', 'pass this along', 'forward this',
    'get back to you', 'let me know',
]


def log(message):
    """Log with timestamp."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}", file=sys.stderr)


def get_sent_emails_with_thread_id(notion, email_queue_db):
    """Get all sent emails that have a Gmail Thread ID for response checking.

    Handles Notion pagination (max 100 results per page).
    """
    all_results = []
    has_more = True
    start_cursor = None

    while has_more:
        kwargs = {
            'database_id': email_queue_db,
            'filter': {
                "and": [
                    {"property": "Status", "select": {"equals": "Sent"}},
                    {"property": "Gmail Thread ID", "rich_text": {"is_not_empty": True}}
                ]
            }
        }
        if start_cursor:
            kwargs['start_cursor'] = start_cursor

        try:
            response = notion.databases.query(**kwargs)
        except APIResponseError as e:
            log(f"Error querying Email Queue: {e}")
            return []

        for page in response['results']:
            props = page['properties']
            thread_id = ''.join(
                item.get('plain_text', '')
                for item in props.get('Gmail Thread ID', {}).get('rich_text', [])
            )
            if thread_id:
                message_id = ''.join(
                    item.get('plain_text', '')
                    for item in props.get('Gmail Message ID', {}).get('rich_text', [])
                )
                all_results.append({
                    'email_page_id': page['id'],
                    'thread_id': thread_id,
                    'message_id': message_id,
                    'subject': ''.join(
                        item.get('plain_text', '')
                        for item in props.get('Subject', {}).get('rich_text', [])
                    ),
                    'game_id': (
                        props.get('Game', {}).get('relation', [{}])[0].get('id')
                        if props.get('Game', {}).get('relation') else None
                    ),
                    'contact_id': (
                        props.get('Contact', {}).get('relation', [{}])[0].get('id')
                        if props.get('Contact', {}).get('relation') else None
                    ),
                })

        has_more = response.get('has_more', False)
        start_cursor = response.get('next_cursor')

    return all_results


def check_thread_for_replies(service, thread_id, our_email, our_message_id=None):
    """Check a Gmail thread for reply messages.

    Identifies replies by:
    1. If our_message_id is set: any message with a different ID is a reply
    2. Fallback: any message where From doesn't contain our_email

    This handles the case where someone replies from the same email address
    (e.g., testing with your own email).

    Returns list of reply dicts, or None if no replies found.
    """
    try:
        thread = service.users().threads().get(
            userId='me', id=thread_id, format='metadata',
            metadataHeaders=['From', 'Date', 'Subject']
        ).execute()

        messages = thread.get('messages', [])

        replies = []
        for msg in messages:
            headers = {
                h['name']: h['value']
                for h in msg.get('payload', {}).get('headers', [])
            }
            from_header = headers.get('From', '')

            # A message is a reply if:
            # - It has a different message ID than the one we sent, OR
            # - It's from someone other than us (fallback if no message_id stored)
            is_our_sent_message = False
            if our_message_id:
                is_our_sent_message = (msg['id'] == our_message_id)
            else:
                is_our_sent_message = (our_email.lower() in from_header.lower())

            if not is_our_sent_message:
                replies.append({
                    'message_id': msg['id'],
                    'from': from_header,
                    'date': headers.get('Date', ''),
                    'snippet': msg.get('snippet', ''),
                    'internal_date': msg.get('internalDate', ''),
                })

        return replies if replies else None

    except Exception as e:
        log(f"Error checking thread {thread_id}: {e}")
        return None


def classify_response(snippet):
    """Auto-classify response type based on keyword matching.

    Checks in priority order:
    1. Out of Office — auto-replies
    2. Booked — confirmed they want to order
    3. Not Interested — explicitly said no
    4. Question — asking about pricing, menus, details
    5. Interested — positive but not committed
    6. None — no strong signal (no Response Type set)

    Returns one of: "Out of Office", "Booked", "Not Interested", "Question", "Interested", or None
    These match the Notion "Response Type" select options.
    """
    snippet_lower = snippet.lower()

    # Check Out of Office first — most distinct pattern
    for keyword in OUT_OF_OFFICE_KEYWORDS:
        if keyword in snippet_lower:
            return 'Out of Office'

    # Check Booked — strongest positive signal
    for keyword in BOOKED_KEYWORDS:
        if keyword in snippet_lower:
            return 'Booked'

    # Check Not Interested — explicit no
    for keyword in NOT_INTERESTED_KEYWORDS:
        if keyword in snippet_lower:
            return 'Not Interested'

    # Check Question — asking for details
    for keyword in QUESTION_KEYWORDS:
        if keyword in snippet_lower:
            return 'Question'

    # Check Interested — positive but not committed
    for keyword in INTERESTED_KEYWORDS:
        if keyword in snippet_lower:
            return 'Interested'

    # No strong signal — leave Response Type blank
    return None


def update_email_queue_responded(notion, email_page_id, response_type, response_date, response_notes=None):
    """Update Email Queue: Status → Responded, Response Received ✓, Response Type, Response Date, Response Notes."""
    try:
        properties = {
            "Status": {"select": {"name": "Responded"}},
            "Response Received": {"checkbox": True},
            "Response Date": {"date": {"start": response_date}},
        }
        # Only set Response Type if we have a classification
        if response_type:
            properties["Response Type"] = {"select": {"name": response_type}}
        if response_notes:
            properties["Response Notes"] = {"rich_text": [{"text": {"content": response_notes}}]}

        notion.pages.update(page_id=email_page_id, properties=properties)
        return True
    except APIResponseError as e:
        log(f"Error updating Email Queue {email_page_id}: {e}")
        return False


def strip_quoted_reply(text):
    """Strip quoted original message from a reply snippet.

    Removes everything after common quote patterns like:
    - 'On Mon, Feb 9, 2026 at 10:38 PM <email> wrote:'
    - '---------- Forwarded message ----------'
    """
    import re
    # "On <date> <name/email> wrote:" pattern
    patterns = [
        r'\s*On\s+\w{3},\s+\w{3}\s+\d{1,2},\s+\d{4}\s+at\s+.*?wrote:.*',
        r'\s*On\s+\d{1,2}/\d{1,2}/\d{2,4}.*?wrote:.*',
        r'\s*-{5,}.*(?:Original|Forwarded).*-{5,}.*',
        r'\s*From:.*Sent:.*',
    ]
    for pattern in patterns:
        text = re.split(pattern, text, maxsplit=1, flags=re.IGNORECASE | re.DOTALL)[0]
    return text.strip()


def build_response_note(response_type, snippet, from_addr):
    """Build a concise note summarizing the response for the Game's Notes field."""
    # Extract just the name/email from "Name <email>" format
    sender = from_addr.split('<')[0].strip().strip('"') if '<' in from_addr else from_addr

    type_label = response_type or "Reply"
    # Strip quoted reply and truncate
    clean_snippet = strip_quoted_reply(snippet)[:200].strip()

    return f"[{type_label}] {sender}: \"{clean_snippet}\""


def update_game_responded(notion, game_page_id, status, notes=None):
    """Update Game's outreach status and optionally add response notes."""
    try:
        properties = {
            "Outreach Status": {"select": {"name": status}}
        }
        if notes:
            properties["Notes"] = {"rich_text": [{"text": {"content": notes}}]}

        notion.pages.update(page_id=game_page_id, properties=properties)
        return True
    except APIResponseError as e:
        log(f"Error updating Game {game_page_id}: {e}")
        return False


def backfill_thread_ids(notion, email_queue_db, service, our_email):
    """Backfill Gmail Thread IDs for emails sent before tracking was added.

    Searches Gmail by sender + recipient + subject to find matching threads.
    """
    log("Starting Thread ID backfill...")

    # Query Email Queue for Sent emails WITHOUT a Thread ID
    all_emails = []
    has_more = True
    start_cursor = None

    while has_more:
        kwargs = {
            'database_id': email_queue_db,
            'filter': {
                "and": [
                    {"property": "Status", "select": {"equals": "Sent"}},
                    {"property": "Gmail Thread ID", "rich_text": {"is_empty": True}}
                ]
            }
        }
        if start_cursor:
            kwargs['start_cursor'] = start_cursor

        response = notion.databases.query(**kwargs)
        all_emails.extend(response['results'])
        has_more = response.get('has_more', False)
        start_cursor = response.get('next_cursor')

    log(f"Found {len(all_emails)} sent emails without Thread ID")

    backfilled = 0
    for email_page in all_emails:
        props = email_page['properties']
        subject = ''.join(
            item.get('plain_text', '')
            for item in props.get('Subject', {}).get('rich_text', [])
        )

        # Get the recipient email from the linked Contact
        recipient_email = ''
        if props.get('Contact', {}).get('relation'):
            contact_id = props['Contact']['relation'][0]['id']
            try:
                contact = notion.pages.retrieve(page_id=contact_id)
                recipient_email = contact['properties'].get('Email', {}).get('email', '')
            except Exception:
                pass

        if not recipient_email or not subject:
            continue

        # Search Gmail for this email
        # Use first 50 chars of subject to avoid issues with long subjects
        query = f'from:{our_email} to:{recipient_email} subject:"{subject[:50]}"'
        try:
            search_result = service.users().messages().list(
                userId='me', q=query, maxResults=1
            ).execute()

            messages = search_result.get('messages', [])
            if messages:
                msg = service.users().messages().get(
                    userId='me', id=messages[0]['id'], format='minimal'
                ).execute()

                thread_id = msg.get('threadId', '')
                message_id = msg.get('id', '')

                if thread_id:
                    notion.pages.update(
                        page_id=email_page['id'],
                        properties={
                            "Gmail Thread ID": {
                                "rich_text": [{"text": {"content": thread_id}}]
                            },
                            "Gmail Message ID": {
                                "rich_text": [{"text": {"content": message_id}}]
                            }
                        }
                    )
                    log(f"  Backfilled: {subject[:40]}... -> Thread: {thread_id}")
                    backfilled += 1
        except Exception as e:
            log(f"  Search error for '{subject[:30]}...': {e}")

    return backfilled


def check_responses(notion, email_queue_db, games_db, service, our_email, dry_run=False):
    """Main response checking logic. Returns stats dict."""
    sent_emails = get_sent_emails_with_thread_id(notion, email_queue_db)
    log(f"Checking {len(sent_emails)} sent emails for responses...")

    stats = {'checked': 0, 'replies_found': 0, 'by_type': {}}

    for email_info in sent_emails:
        stats['checked'] += 1
        replies = check_thread_for_replies(
            service, email_info['thread_id'], our_email,
            our_message_id=email_info.get('message_id')
        )

        if replies:
            latest_reply = replies[-1]  # Most recent reply
            response_type = classify_response(latest_reply.get('snippet', ''))

            # Parse response date from internalDate (epoch ms)
            try:
                epoch_ms = int(latest_reply['internal_date'])
                response_date = datetime.fromtimestamp(epoch_ms / 1000).strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                response_date = datetime.now().strftime('%Y-%m-%d')

            type_label = response_type or 'Unclassified'
            log(f"  REPLY FOUND: {email_info['subject'][:50]}...")
            log(f"    From: {latest_reply['from']}")
            log(f"    Response Type: {type_label}")
            log(f"    Snippet: {latest_reply['snippet'][:80]}...")

            if not dry_run:
                # Build response note from reply
                note = build_response_note(
                    response_type, latest_reply.get('snippet', ''),
                    latest_reply.get('from', '')
                )

                # Update Email Queue: Status, Response Type, Response Notes, etc.
                update_email_queue_responded(
                    notion, email_info['email_page_id'], response_type, response_date,
                    response_notes=note
                )

                # Update Game Outreach Status → "Responded" + Notes with reply summary
                if email_info['game_id']:
                    update_game_responded(notion, email_info['game_id'], 'Responded', notes=note)

            stats['replies_found'] += 1
            stats['by_type'][type_label] = stats['by_type'].get(type_label, 0) + 1

    return stats


def main():
    parser = argparse.ArgumentParser(description="Check Gmail for email responses")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be updated without making changes")
    parser.add_argument("--backfill", action="store_true",
                        help="Backfill Thread IDs for previously sent emails")
    args = parser.parse_args()

    # Initialize
    notion = Client(auth=os.getenv('NOTION_API_KEY'))
    email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB')
    games_db = os.getenv('NOTION_GAMES_DB')

    if not email_queue_db:
        log("Error: NOTION_EMAIL_QUEUE_DB not set in .env")
        sys.exit(1)

    service = get_gmail_service()
    profile = service.users().getProfile(userId='me').execute()
    our_email = profile['emailAddress']
    log(f"Checking responses for: {our_email}")

    if args.backfill:
        count = backfill_thread_ids(notion, email_queue_db, service, our_email)
        log(f"Backfilled {count} Thread IDs")
        print(json.dumps({"success": True, "backfilled": count}, indent=2))
        return

    # Check for responses
    stats = check_responses(notion, email_queue_db, games_db, service, our_email,
                            dry_run=args.dry_run)

    # Summary
    mode = "[DRY RUN] " if args.dry_run else ""
    log(f"")
    log(f"{'='*60}")
    log(f"{mode}RESPONSE CHECK COMPLETE")
    log(f"{'='*60}")
    log(f"Emails checked: {stats['checked']}")
    log(f"Replies found: {stats['replies_found']}")
    if stats['by_type']:
        for rtype, count in stats['by_type'].items():
            log(f"  {rtype}: {count}")
    log(f"{'='*60}")

    print(json.dumps({"success": True, **stats}, indent=2))


if __name__ == "__main__":
    main()
