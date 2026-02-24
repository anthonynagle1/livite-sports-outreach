#!/usr/bin/env python3
"""
Tool: notion_sync_followups.py
Purpose: Check for games needing follow-up and create draft emails

This tool:
1. Queries games where Follow-up Date <= Today
2. Filters to games with status "Email Sent" (no response yet)
3. Creates follow-up email drafts using next sequence step template
4. Optionally updates Follow-up Date to next interval

Usage:
    # Check and create follow-up drafts
    python tools/notion_sync_followups.py

    # Dry run (show what would be done without creating drafts)
    python tools/notion_sync_followups.py --dry-run

    # Show games needing follow-up (report only)
    python tools/notion_sync_followups.py --report

Required environment variables:
    NOTION_API_KEY - Notion integration token
    NOTION_GAMES_DB - Games database ID
    NOTION_CONTACTS_DB - Contacts database ID
    NOTION_TEMPLATES_DB - Email Templates database ID
    NOTION_EMAIL_QUEUE_DB - Email Queue database ID
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

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Default follow-up interval in days (used if template has no Days After Previous)
FOLLOWUP_INTERVAL_DAYS = 7

# Maximum number of emails to send per game (initial + follow-ups)
MAX_SEQUENCE_STEPS = 3


def get_notion_client():
    """Initialize Notion client."""
    api_key = os.getenv('NOTION_API_KEY')
    if not api_key:
        print("Error: NOTION_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    return Client(auth=api_key)


def get_database_ids():
    """Get required database IDs from environment."""
    games_db = os.getenv('NOTION_GAMES_DB')
    contacts_db = os.getenv('NOTION_CONTACTS_DB')
    templates_db = os.getenv('NOTION_TEMPLATES_DB')
    email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB')

    missing = []
    if not games_db:
        missing.append('NOTION_GAMES_DB')
    if not contacts_db:
        missing.append('NOTION_CONTACTS_DB')
    if not templates_db:
        missing.append('NOTION_TEMPLATES_DB')
    if not email_queue_db:
        missing.append('NOTION_EMAIL_QUEUE_DB')

    if missing:
        print(f"Error: Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    return games_db, contacts_db, templates_db, email_queue_db


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


def get_games_needing_followup(notion, games_db):
    """
    Query games where Follow-up Date <= Today and Outreach Status is 'Email Sent'.
    """
    today = datetime.now().strftime('%Y-%m-%d')

    try:
        response = notion.databases.query(
            database_id=games_db,
            filter={
                "and": [
                    {
                        "property": "Follow-up Date",
                        "date": {
                            "on_or_before": today
                        }
                    },
                    {
                        "property": "Outreach Status",
                        "select": {
                            "equals": "Email Sent"
                        }
                    }
                ]
            }
        )

        return response['results']

    except APIResponseError as e:
        print(f"Error querying games: {e}", file=sys.stderr)
        return []


def count_emails_sent_to_game(notion, email_queue_db, game_id):
    """
    Count how many emails have been sent for this game.
    Used to determine which sequence step template to use.
    """
    try:
        response = notion.databases.query(
            database_id=email_queue_db,
            filter={
                "and": [
                    {
                        "property": "Game",
                        "relation": {
                            "contains": game_id
                        }
                    },
                    {
                        "or": [
                            {"property": "Status", "select": {"equals": "Sent"}},
                            {"property": "Status", "select": {"equals": "Responded"}},
                            {"property": "Status", "select": {"equals": "Booked"}},
                        ]
                    }
                ]
            }
        )

        return len(response['results'])

    except APIResponseError as e:
        print(f"Warning: Could not count emails: {e}", file=sys.stderr)
        return 1  # Assume at least 1 email sent


def has_pending_email(notion, email_queue_db, game_id):
    """Check if game already has a pending draft or approved email in queue."""
    try:
        response = notion.databases.query(
            database_id=email_queue_db,
            filter={
                "and": [
                    {"property": "Game", "relation": {"contains": game_id}},
                    {
                        "or": [
                            {"property": "Status", "select": {"equals": "Draft"}},
                            {"property": "Status", "select": {"equals": "Approved"}},
                        ]
                    }
                ]
            }
        )
        return len(response['results']) > 0
    except APIResponseError:
        return False


def find_followup_template(notion, templates_db, sport, sequence_step):
    """
    Find a follow-up template for the given sequence step.
    """
    try:
        # Try sport-specific template at this sequence step
        response = notion.databases.query(
            database_id=templates_db,
            filter={
                "and": [
                    {"property": "Sport", "select": {"equals": sport}},
                    {"property": "Sequence Step", "number": {"equals": sequence_step}}
                ]
            }
        )

        if response['results']:
            return response['results'][0]

        # Try any template at this sequence step
        response = notion.databases.query(
            database_id=templates_db,
            filter={
                "property": "Sequence Step",
                "number": {"equals": sequence_step}
            }
        )

        if response['results']:
            return response['results'][0]

        # Fall back to highest available sequence step
        response = notion.databases.query(
            database_id=templates_db,
            sorts=[
                {"property": "Sequence Step", "direction": "descending"}
            ]
        )

        if response['results']:
            return response['results'][0]

        return None

    except APIResponseError as e:
        print(f"Error finding template: {e}", file=sys.stderr)
        return None


def get_days_after_previous(template):
    """Get the Days After Previous value from a template, defaulting to FOLLOWUP_INTERVAL_DAYS."""
    if not template:
        return FOLLOWUP_INTERVAL_DAYS
    props = template.get('properties', {})
    days = props.get('Days After Previous', {}).get('number')
    return days if days and days > 0 else FOLLOWUP_INTERVAL_DAYS


def get_game_details(notion, game_page):
    """
    Extract relevant details from a game page.
    """
    props = game_page['properties']

    game_data = {
        'game_id': game_page['id'],
        'game_title': extract_title(props.get('Game ID', {}).get('title', [])),
        'sport': props.get('Sport', {}).get('select', {}).get('name', ''),
        'venue': extract_text_from_rich_text(props.get('Venue', {}).get('rich_text', [])),
    }

    # Get follow-up date
    if 'Follow-up Date' in props and props['Follow-up Date'].get('date'):
        game_data['followup_date'] = props['Follow-up Date']['date'].get('start', '')
    else:
        game_data['followup_date'] = ''

    # Get game date
    if 'Game Date' in props and props['Game Date'].get('date'):
        game_data['game_date'] = props['Game Date']['date'].get('start', '')
    else:
        game_data['game_date'] = ''

    # Get contact
    game_data['contact_id'] = None
    if 'Contact' in props and props['Contact'].get('relation'):
        game_data['contact_id'] = props['Contact']['relation'][0]['id']

    # Get home/away teams
    game_data['home_school'] = ''
    game_data['away_school'] = ''

    if 'Home Team' in props and props['Home Team'].get('relation'):
        home_id = props['Home Team']['relation'][0]['id']
        try:
            home_page = notion.pages.retrieve(page_id=home_id)
            game_data['home_school'] = extract_title(
                home_page['properties'].get('School Name', {}).get('title', [])
            )
        except:
            pass

    if 'Away Team' in props and props['Away Team'].get('relation'):
        away_id = props['Away Team']['relation'][0]['id']
        try:
            away_page = notion.pages.retrieve(page_id=away_id)
            game_data['away_school'] = extract_title(
                away_page['properties'].get('School Name', {}).get('title', [])
            )
        except:
            pass

    return game_data


def render_template(template_text, variables):
    """Replace {{variable}} placeholders with actual values."""
    result = template_text
    for key, value in variables.items():
        placeholder = '{{' + key + '}}'
        result = result.replace(placeholder, str(value) if value else '')
    return result


def create_followup_draft(notion, email_queue_db, game_data, template, contact_data):
    """
    Create a follow-up email draft in the Email Queue.
    """
    try:
        # Get template content
        props = template['properties']
        subject_template = extract_text_from_rich_text(
            props.get('Subject Line', {}).get('rich_text', [])
        )
        body_template = extract_text_from_rich_text(
            props.get('Body', {}).get('rich_text', [])
        )

        # Use default follow-up templates if not set
        if not subject_template:
            subject_template = "Following up: Catering for {{away_school}} {{sport}}"

        if not body_template:
            body_template = """Hi {{contact_name}},

I wanted to follow up on my previous email about catering for your {{sport}} game against {{home_school}}.

Would you have a few minutes to discuss your team's meal needs?

Best regards,
Livite Sports Catering"""

        # Build variables
        contact_name = contact_data.get('name', '')
        contact_first = contact_name.split()[0] if contact_name else ''
        variables = {
            'contact_name': contact_name,
            'contact_first_name': contact_first,
            'contact_title': contact_data.get('title', ''),
            'home_school': game_data['home_school'],
            'away_school': game_data['away_school'],
            'school_name': game_data['away_school'],
            'opponent_school': game_data['home_school'],
            'game_date': game_data['game_date'],
            'sport': game_data['sport'],
            'venue': game_data.get('venue', ''),
            'our_company': 'Livite Sports Catering',
        }

        # Render
        subject = render_template(subject_template, variables)
        body = render_template(body_template, variables)

        # Create email queue entry
        email_id = f"Followup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        properties = {
            "Email ID": {"title": [{"text": {"content": email_id}}]},
            "Subject": {"rich_text": [{"text": {"content": subject}}]},
            "Body": {"rich_text": [{"text": {"content": body[:2000]}}]},
            "Status": {"select": {"name": "Draft"}},
            "Game": {"relation": [{"id": game_data['game_id']}]},
        }

        # Template Used relation (skip if template has no id, e.g. default)
        if template.get('id'):
            properties["Template Used"] = {"relation": [{"id": template['id']}]}

        if game_data['contact_id']:
            properties["Contact"] = {"relation": [{"id": game_data['contact_id']}]}

        # Properties needed by downstream tools (send, response check, etc.)
        if contact_data.get('email'):
            properties["To Email"] = {"email": contact_data['email']}
        if game_data.get('game_date'):
            properties["Game Date"] = {"date": {"start": game_data['game_date']}}
        if game_data.get('away_school'):
            properties["School"] = {"rich_text": [{"text": {"content": game_data['away_school']}}]}
        if game_data.get('sport'):
            properties["Sport"] = {"rich_text": [{"text": {"content": game_data['sport']}}]}

        response = notion.pages.create(
            parent={"database_id": email_queue_db},
            properties=properties
        )

        return response['id']

    except APIResponseError as e:
        print(f"Error creating follow-up draft: {e}", file=sys.stderr)
        return None


def update_followup_date(notion, game_id, days=FOLLOWUP_INTERVAL_DAYS):
    """
    Push the follow-up date forward by the specified number of days.
    """
    try:
        new_date = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')

        notion.pages.update(
            page_id=game_id,
            properties={
                "Follow-up Date": {"date": {"start": new_date}}
            }
        )

        return True

    except APIResponseError as e:
        print(f"Error updating follow-up date: {e}", file=sys.stderr)
        return False


def get_contact_details(notion, contact_id):
    """Fetch contact name and details."""
    try:
        page = notion.pages.retrieve(page_id=contact_id)
        props = page['properties']

        return {
            'name': extract_title(props.get('Name', {}).get('title', [])),
            'title': extract_text_from_rich_text(props.get('Title', {}).get('rich_text', [])),
            'email': props.get('Email', {}).get('email', '')
        }

    except APIResponseError as e:
        return {'name': '', 'title': '', 'email': ''}


def sync_followups(notion, games_db, contacts_db, templates_db, email_queue_db,
                   dry_run=False, report_only=False):
    """
    Main function to check for and create follow-up email drafts.
    """
    print("Checking for games needing follow-up...", file=sys.stderr)

    games = get_games_needing_followup(notion, games_db)
    print(f"Found {len(games)} games needing follow-up", file=sys.stderr)

    if not games:
        return {'drafts_created': 0, 'skipped': 0, 'failed': 0}

    stats = {'drafts_created': 0, 'skipped': 0, 'failed': 0}

    today = datetime.now().strftime('%Y-%m-%d')

    for game_page in games:
        game_data = get_game_details(notion, game_page)

        print(f"\n[{game_data['game_title']}]", file=sys.stderr)
        print(f"  Follow-up due: {game_data['followup_date']}", file=sys.stderr)

        if report_only:
            continue

        # Check if game date has passed (no point following up)
        if game_data['game_date'] and game_data['game_date'] < today:
            print("  Skipping: Game date has passed", file=sys.stderr)
            stats['skipped'] += 1
            continue

        # Check if contact exists
        if not game_data['contact_id']:
            print("  Skipping: No contact linked", file=sys.stderr)
            stats['skipped'] += 1
            continue

        # Get contact details
        contact_data = get_contact_details(notion, game_data['contact_id'])
        if not contact_data['email']:
            print("  Skipping: Contact has no email", file=sys.stderr)
            stats['skipped'] += 1
            continue

        # Determine sequence step (how many emails already sent + 1)
        emails_sent = count_emails_sent_to_game(notion, email_queue_db, game_data['game_id'])
        next_step = emails_sent + 1
        print(f"  Emails sent: {emails_sent}, next step: {next_step}", file=sys.stderr)

        # Max sequence cap — don't send more than MAX_SEQUENCE_STEPS emails per game
        if emails_sent >= MAX_SEQUENCE_STEPS:
            print(f"  Skipping: Max follow-ups reached ({MAX_SEQUENCE_STEPS})", file=sys.stderr)
            stats['skipped'] += 1
            continue

        # Dedup — don't create follow-up if one is already pending in queue
        if has_pending_email(notion, email_queue_db, game_data['game_id']):
            print("  Skipping: Pending draft/approved email already exists", file=sys.stderr)
            stats['skipped'] += 1
            continue

        # Find appropriate follow-up template
        template = find_followup_template(notion, templates_db, game_data['sport'], next_step)
        if not template:
            print("  Warning: No template found, using default", file=sys.stderr)
            # Create a minimal template-like dict
            template = {
                'id': None,
                'properties': {}
            }

        if dry_run:
            print(f"  [DRY RUN] Would create follow-up draft (step {next_step})", file=sys.stderr)
            stats['drafts_created'] += 1
            continue

        # Create the follow-up draft
        draft_id = create_followup_draft(
            notion, email_queue_db, game_data, template, contact_data
        )

        if draft_id:
            print(f"  Created follow-up draft (step {next_step}): {draft_id}", file=sys.stderr)
            stats['drafts_created'] += 1

            # Update follow-up date using template's Days After Previous
            interval = get_days_after_previous(template)
            update_followup_date(notion, game_data['game_id'], days=interval)
        else:
            print("  Failed to create draft", file=sys.stderr)
            stats['failed'] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Check for and create follow-up email drafts"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without creating drafts"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Only show games needing follow-up (no drafts created)"
    )

    args = parser.parse_args()

    # Initialize
    notion = get_notion_client()
    games_db, contacts_db, templates_db, email_queue_db = get_database_ids()

    # Run sync
    stats = sync_followups(
        notion, games_db, contacts_db, templates_db, email_queue_db,
        dry_run=args.dry_run,
        report_only=args.report
    )

    # Print summary
    mode = "REPORT" if args.report else ("DRY RUN" if args.dry_run else "SYNC")
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"FOLLOW-UP {mode} COMPLETE", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"Drafts created: {stats['drafts_created']}", file=sys.stderr)
    print(f"Skipped: {stats['skipped']}", file=sys.stderr)
    print(f"Failed: {stats['failed']}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    result = {"success": True, **stats}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
