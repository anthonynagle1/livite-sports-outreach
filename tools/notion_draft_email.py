#!/usr/bin/env python3
"""
Tool: notion_draft_email.py
Purpose: Create email drafts in the Notion Email Queue from game data

This tool:
1. Fetches game details from Notion Games database
2. Gets contact info from the linked Contact
3. Checks for duplicate outreach (skip if recently contacted)
4. Finds appropriate email template
5. Renders template with variable substitution
6. Creates Email Queue entry with status "Draft"

Duplicate Prevention:
- Same Contact Rule: Skip if emailed within 7 days
- Same School Rule: Skip if ANY contact at school emailed within 3 days for same sport
- Doubleheader Rule: Skip if game same day vs same opponent already has draft

Usage:
    # Create draft for a specific game
    python tools/notion_draft_email.py --game-id abc123

    # Create drafts for all games without outreach
    python tools/notion_draft_email.py --process-not-contacted

    # Skip duplicate checking
    python tools/notion_draft_email.py --process-not-contacted --no-duplicate-check

    # Specify a template to use
    python tools/notion_draft_email.py --game-id abc123 --template-id xyz789

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
import re
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


def extract_text_from_rich_text(rich_text_array):
    """Extract plain text from Notion rich text array."""
    if not rich_text_array:
        return ''
    return ''.join(item.get('plain_text', '') for item in rich_text_array)


def extract_title(title_array):
    """Extract title text from Notion title property."""
    if not title_array:
        return ''
    return ''.join(item.get('plain_text', '') for item in title_array)


def get_game_details(notion, game_page_id):
    """
    Fetch game details and related data from Notion.

    Returns dict with game info, contact info, and school names.
    """
    try:
        game = notion.pages.retrieve(page_id=game_page_id)
        props = game['properties']

        # Extract basic game info
        game_data = {
            'game_id': game_page_id,
            'game_title': extract_title(props.get('Game ID', {}).get('title', [])),
            'sport': props.get('Sport', {}).get('select', {}).get('name', ''),
            'venue': extract_text_from_rich_text(props.get('Venue', {}).get('rich_text', [])),
        }

        # Extract date
        if 'Game Date' in props and props['Game Date'].get('date'):
            date_obj = props['Game Date']['date']
            game_data['game_date'] = date_obj.get('start', '')
            # Format date nicely
            try:
                dt = datetime.fromisoformat(game_data['game_date'])
                day = dt.day
                suffix = 'th' if 11 <= day <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
                game_data['game_date_formatted'] = '{} {}{}'.format(dt.strftime('%B'), day, suffix)
            except:
                game_data['game_date_formatted'] = game_data['game_date']
        else:
            game_data['game_date'] = ''
            game_data['game_date_formatted'] = 'TBA'

        # Get Home Team name
        game_data['home_school'] = ''
        if 'Home Team' in props and props['Home Team'].get('relation'):
            home_id = props['Home Team']['relation'][0]['id']
            home_page = notion.pages.retrieve(page_id=home_id)
            game_data['home_school'] = extract_title(
                home_page['properties'].get('School Name', {}).get('title', [])
            )

        # Get Away Team name
        game_data['away_school'] = ''
        if 'Away Team' in props and props['Away Team'].get('relation'):
            away_id = props['Away Team']['relation'][0]['id']
            away_page = notion.pages.retrieve(page_id=away_id)
            game_data['away_school'] = extract_title(
                away_page['properties'].get('School Name', {}).get('title', [])
            )

        # Get Contact info
        game_data['contact_id'] = None
        game_data['contact_name'] = ''
        game_data['contact_title'] = ''
        game_data['contact_email'] = ''

        if 'Contact' in props and props['Contact'].get('relation'):
            contact_id = props['Contact']['relation'][0]['id']
            game_data['contact_id'] = contact_id

            contact_page = notion.pages.retrieve(page_id=contact_id)
            contact_props = contact_page['properties']

            game_data['contact_name'] = extract_title(
                contact_props.get('Name', {}).get('title', [])
            )
            game_data['contact_title'] = extract_text_from_rich_text(
                contact_props.get('Title', {}).get('rich_text', [])
            )
            game_data['contact_email'] = contact_props.get('Email', {}).get('email', '')

            # Check if this is a returning contact via Relationship field
            relationship = contact_props.get('Relationship', {}).get('select')
            relationship_name = relationship.get('name', '') if relationship else ''
            game_data['is_returning'] = relationship_name in (
                'Previously Contacted', 'Previously Responded', 'Previous Customer'
            )
            game_data['relationship'] = relationship_name

        return game_data

    except APIResponseError as e:
        print(f"Error fetching game details: {e}", file=sys.stderr)
        return None


def find_template(notion, templates_db, sport=None, sequence_step=1, sequence_type='Cold'):
    """
    Find an appropriate email template.

    Matches sequence_type (Cold vs Returning), then sport, then falls back.
    """
    try:
        # Try sport-specific + sequence type match
        if sport:
            response = notion.databases.query(
                database_id=templates_db,
                filter={
                    "and": [
                        {"property": "Sport", "select": {"equals": sport}},
                        {"property": "Sequence Step", "number": {"equals": sequence_step}},
                        {"property": "Sequence Type", "select": {"equals": sequence_type}},
                    ]
                }
            )
            if response['results']:
                return response['results'][0]

        # Try sequence type + step (any sport)
        response = notion.databases.query(
            database_id=templates_db,
            filter={
                "and": [
                    {"property": "Sequence Step", "number": {"equals": sequence_step}},
                    {"property": "Sequence Type", "select": {"equals": sequence_type}},
                ]
            }
        )
        if response['results']:
            return response['results'][0]

        # Fall back to Cold template if Returning not found
        if sequence_type == 'Returning':
            print(f"  No Returning template for step {sequence_step}, falling back to Cold", file=sys.stderr)
            return find_template(notion, templates_db, sport=sport,
                                sequence_step=sequence_step, sequence_type='Cold')

        # Last resort: any template with matching step
        response = notion.databases.query(
            database_id=templates_db,
            filter={
                "property": "Sequence Step",
                "number": {"equals": sequence_step}
            }
        )
        if response['results']:
            return response['results'][0]

        return None

    except APIResponseError as e:
        print(f"Error finding template: {e}", file=sys.stderr)
        return None


def get_template_content(notion, template_page_id):
    """
    Get template subject and body from a template page.
    """
    try:
        page = notion.pages.retrieve(page_id=template_page_id)
        props = page['properties']

        subject = extract_text_from_rich_text(
            props.get('Subject Line', {}).get('rich_text', [])
        )
        body = extract_text_from_rich_text(
            props.get('Body', {}).get('rich_text', [])
        )

        return subject, body

    except APIResponseError as e:
        print(f"Error getting template content: {e}", file=sys.stderr)
        return '', ''


def render_template(template_text, variables):
    """
    Replace {{variable}} placeholders with actual values.
    """
    result = template_text
    for key, value in variables.items():
        placeholder = '{{' + key + '}}'
        result = result.replace(placeholder, str(value) if value else '')
    return result


def check_duplicate_outreach(notion, contacts_db, email_queue_db, contact_id, school_id, sport, game_date):
    """
    Check if this outreach would be a duplicate.

    Rules:
    - Same Contact Rule: Skip if emailed within 7 days
    - Pending Email Rule: Skip if email already in queue for this contact
    - Same School Rule: Skip if any contact at school emailed within 3 days for same sport

    Returns: (is_duplicate: bool, reason: str)
    """
    try:
        # Check 0: Email Queue ground truth (catches stale/missing Last Emailed)
        if contact_id:
            contact = notion.pages.retrieve(page_id=contact_id)
            contact_props = contact['properties']
            contact_email = contact_props.get('Email', {}).get('email', '')

            if contact_email:
                seven_days_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
                eq_response = notion.databases.query(
                    database_id=email_queue_db,
                    filter={
                        "and": [
                            {"property": "To Email", "email": {"equals": contact_email}},
                            {"property": "Status", "select": {"equals": "Sent"}},
                            {"property": "Sent At", "date": {"on_or_after": seven_days_ago}},
                        ]
                    }
                )
                if eq_response['results']:
                    return True, f"Email Queue confirms {contact_email} emailed within 7 days"

        # Check 1: Contact emailed recently? (Last Emailed property)
        if contact_id:
            if not contact_props:
                contact = notion.pages.retrieve(page_id=contact_id)
                contact_props = contact['properties']

            if 'Last Emailed' in contact_props and contact_props['Last Emailed'].get('date'):
                last_emailed_str = contact_props['Last Emailed']['date'].get('start', '')
                if last_emailed_str:
                    last_emailed = datetime.fromisoformat(last_emailed_str.replace('Z', '+00:00'))
                    if last_emailed.tzinfo:
                        last_emailed = last_emailed.replace(tzinfo=None)
                    days_since = (datetime.now() - last_emailed).days
                    if days_since < 7:
                        return True, f"Contact emailed {days_since} days ago (within 7 day window)"

        # Check 2: Email already pending in queue for this contact?
        if contact_id:
            response = notion.databases.query(
                database_id=email_queue_db,
                filter={
                    "and": [
                        {"property": "Contact", "relation": {"contains": contact_id}},
                        {"property": "Status", "select": {"does_not_equal": "Sent"}}
                    ]
                }
            )
            if response['results']:
                return True, "Email already in queue for this contact"

        # Check 3: Another contact at same school emailed recently for same sport?
        # This requires finding all contacts at the school
        if school_id and sport:
            # Find all contacts at this school
            response = notion.databases.query(
                database_id=contacts_db,
                filter={
                    "and": [
                        {"property": "School", "relation": {"contains": school_id}},
                        {"property": "Sport", "select": {"equals": sport}}
                    ]
                }
            )

            for contact in response['results']:
                if contact['id'] == contact_id:
                    continue  # Skip the current contact (already checked above)

                contact_props = contact['properties']
                if 'Last Emailed' in contact_props and contact_props['Last Emailed'].get('date'):
                    last_emailed_str = contact_props['Last Emailed']['date'].get('start', '')
                    if last_emailed_str:
                        last_emailed = datetime.fromisoformat(last_emailed_str.replace('Z', '+00:00'))
                        if last_emailed.tzinfo:
                            last_emailed = last_emailed.replace(tzinfo=None)
                        days_since = (datetime.now() - last_emailed).days
                        if days_since < 3:
                            contact_name = extract_title(contact_props.get('Name', {}).get('title', []))
                            return True, f"Another {sport} contact ({contact_name}) emailed {days_since} days ago"

        return False, "OK to email"

    except APIResponseError as e:
        print(f"  Warning: Error checking duplicates: {e}", file=sys.stderr)
        return False, "Check failed - proceeding"


def create_email_draft(notion, email_queue_db, game_id, contact_id, template_id, subject, body,
                       game_date=None, school=None, sport=None, to_email=None):
    """
    Create an Email Queue entry with Draft status.
    """
    try:
        # Generate email ID
        email_id = f"Draft-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        properties = {
            "Email ID": {
                "title": [{"text": {"content": email_id}}]
            },
            "Subject": {
                "rich_text": [{"text": {"content": subject}}]
            },
            "Body": {
                "rich_text": [{"text": {"content": body[:2000]}}]  # Notion limit
            },
            "Status": {
                "select": {"name": "Draft"}
            }
        }

        # Add relations
        if game_id:
            properties["Game"] = {"relation": [{"id": game_id}]}
        if contact_id:
            properties["Contact"] = {"relation": [{"id": contact_id}]}
        if template_id:
            properties["Template Used"] = {"relation": [{"id": template_id}]}
        if game_date:
            properties["Game Date"] = {"date": {"start": game_date}}
        if school:
            properties["School"] = {"rich_text": [{"text": {"content": school}}]}
        if sport:
            properties["Sport"] = {"rich_text": [{"text": {"content": sport}}]}
        if to_email:
            properties["To Email"] = {"email": to_email}

        response = notion.pages.create(
            parent={"database_id": email_queue_db},
            properties=properties
        )

        return response['id']

    except APIResponseError as e:
        print(f"Error creating email draft: {e}", file=sys.stderr)
        return None


def create_draft_for_game(notion, games_db, contacts_db, templates_db, email_queue_db,
                          game_page_id, template_page_id=None, check_duplicates=True):
    """
    Create an email draft for a specific game.
    Returns: draft_id on success, None on skip/error, "duplicate" if skipped due to duplicate
    """
    print(f"\nCreating draft for game: {game_page_id}", file=sys.stderr)

    # Skip if draft already created for this game
    try:
        game_check = notion.pages.retrieve(page_id=game_page_id)
        draft_created = game_check['properties'].get('Draft Created', {}).get('date')
        if draft_created:
            print(f"  Skipping: Draft already created on {draft_created['start']}", file=sys.stderr)
            return "duplicate"
    except:
        pass

    # Get game details
    game_data = get_game_details(notion, game_page_id)
    if not game_data:
        print("  Error: Could not fetch game details", file=sys.stderr)
        return None

    # Check if we have a contact
    if not game_data['contact_id']:
        print("  Error: No contact linked to this game", file=sys.stderr)
        return None

    if not game_data['contact_email']:
        print("  Error: Contact has no email address", file=sys.stderr)
        return None

    print(f"  Game: {game_data['home_school']} vs {game_data['away_school']}", file=sys.stderr)
    print(f"  Contact: {game_data['contact_name']} ({game_data['contact_email']})", file=sys.stderr)

    # Check for duplicates
    if check_duplicates:
        # Get school ID from the away team (the school we're reaching out to)
        school_id = None
        try:
            game = notion.pages.retrieve(page_id=game_page_id)
            props = game['properties']
            if 'Away Team' in props and props['Away Team'].get('relation'):
                school_id = props['Away Team']['relation'][0]['id']
        except:
            pass

        is_duplicate, reason = check_duplicate_outreach(
            notion, contacts_db, email_queue_db,
            game_data['contact_id'],
            school_id,
            game_data.get('sport', ''),
            game_data.get('game_date', '')
        )

        if is_duplicate:
            print(f"  Skipping: {reason}", file=sys.stderr)
            return "duplicate"

    # Determine if returning customer
    is_returning = game_data.get('is_returning', False)
    sequence_type = 'Returning' if is_returning else 'Cold'
    if is_returning:
        print(f"  Returning customer detected — using Returning template", file=sys.stderr)

    # Find template
    template = None
    if template_page_id:
        template = notion.pages.retrieve(page_id=template_page_id)
    else:
        template = find_template(notion, templates_db, sport=game_data['sport'],
                                sequence_type=sequence_type)

    if not template:
        print("  Warning: No template found, using default", file=sys.stderr)
        # Use default template
        subject_template = "Catering for {{away_school}} {{sport}} vs {{home_school}}"
        body_template = """Hi {{contact_name}},

I'm reaching out about your upcoming {{sport}} game against {{home_school}} on {{game_date_formatted}}.

We specialize in team catering and would love to provide meals for your players and staff.

Would you have a few minutes to discuss your team's catering needs?

Best regards,
Livite Sports Catering"""
        template_id = None
    else:
        template_id = template['id']
        subject_template, body_template = get_template_content(notion, template_id)

        if not subject_template or not body_template:
            print("  Error: Template has no subject or body", file=sys.stderr)
            return None

    # Build variables for template rendering
    full_name = game_data['contact_name']
    first_name = full_name.split()[0] if full_name else ''
    variables = {
        'contact_name': full_name,
        'contact_first_name': first_name,
        'contact_title': game_data['contact_title'],
        'home_school': game_data['home_school'],
        'away_school': game_data['away_school'],
        'school_name': game_data['away_school'],  # Alias for away_school
        'opponent_school': game_data['home_school'],  # From away team's perspective
        'game_date': game_data['game_date'],
        'game_date_formatted': game_data['game_date_formatted'],
        'sport': game_data['sport'],
        'venue': game_data['venue'],
        'our_company': 'Livite Sports Catering',
    }

    # Render templates
    subject = render_template(subject_template, variables)
    body = render_template(body_template, variables)

    # Validate: catch any unresolved {{placeholders}} before creating draft
    unresolved = set(re.findall(r'\{\{[^}]+\}\}', subject + body))
    if unresolved:
        placeholders_str = ', '.join(sorted(unresolved))
        print(f"  Error: Unresolved placeholders: {placeholders_str}", file=sys.stderr)
        return None

    print(f"  Subject: {subject}", file=sys.stderr)

    # Create the draft
    draft_id = create_email_draft(
        notion, email_queue_db,
        game_data['game_id'],
        game_data['contact_id'],
        template_id,
        subject, body,
        game_date=game_data.get('game_date'),
        school=game_data.get('away_school'),
        sport=game_data.get('sport'),
        to_email=game_data.get('contact_email'),
    )

    if draft_id:
        print(f"  Created draft: {draft_id}", file=sys.stderr)
        # Stamp Draft Created date so we don't create another
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            notion.pages.update(
                page_id=game_data['game_id'],
                properties={
                    "Draft Created": {"date": {"start": today}}
                }
            )
        except:
            pass
        return draft_id
    else:
        print("  Error: Failed to create draft", file=sys.stderr)
        return None


def clear_create_draft_flag(notion, game_page_id):
    """Clear the 'Create Draft' checkbox after processing."""
    try:
        notion.pages.update(
            page_id=game_page_id,
            properties={
                "Create Draft": {"checkbox": False}
            }
        )
        return True
    except APIResponseError as e:
        print(f"  Warning: Could not clear Create Draft flag: {e}", file=sys.stderr)
        return False


def set_game_draft_error(notion, game_page_id, error_message):
    """Write a draft error message to the game's Notes so the user sees why it failed."""
    try:
        timestamp = datetime.now().strftime('%m/%d %H:%M')
        note = f"[Draft Error {timestamp}] {error_message}"
        notion.pages.update(
            page_id=game_page_id,
            properties={
                "Notes": {"rich_text": [{"text": {"content": note}}]}
            }
        )
        print(f"  Set error note: {error_message}", file=sys.stderr)
    except APIResponseError as e:
        print(f"  Warning: Could not set error note: {e}", file=sys.stderr)


def process_flagged_games(notion, games_db, contacts_db, templates_db, email_queue_db,
                          check_duplicates=True):
    """
    Create drafts for all games where 'Create Draft' checkbox is checked.
    Clears the checkbox after processing.
    """
    print("Fetching games with 'Create Draft' checked...", file=sys.stderr)

    try:
        games = []
        has_more = True
        start_cursor = None
        while has_more:
            kwargs = {
                "database_id": games_db,
                "filter": {
                    "property": "Create Draft",
                    "checkbox": {"equals": True}
                }
            }
            if start_cursor:
                kwargs["start_cursor"] = start_cursor
            response = notion.databases.query(**kwargs)
            games.extend(response['results'])
            has_more = response.get('has_more', False)
            start_cursor = response.get('next_cursor')
        print(f"Found {len(games)} games to process", file=sys.stderr)

        if not games:
            print("No games flagged for draft creation.", file=sys.stderr)
            return {'created': 0, 'skipped': 0, 'duplicates': 0, 'failed': 0}

        stats = {'created': 0, 'skipped': 0, 'duplicates': 0, 'failed': 0}

        for game_page in games:
            game_id = game_page['id']
            props = game_page['properties']

            # Get game title for logging
            game_title = extract_title(props.get('Game ID', {}).get('title', []))
            print(f"\n[Processing] {game_title}", file=sys.stderr)

            # Check if game has a contact
            if not props.get('Contact', {}).get('relation'):
                print(f"  [Skip] No contact linked", file=sys.stderr)
                stats['skipped'] += 1
                clear_create_draft_flag(notion, game_id)
                set_game_draft_error(notion, game_id, "No contact linked — assign a contact first")
                continue

            draft_id = create_draft_for_game(
                notion, games_db, contacts_db, templates_db, email_queue_db,
                game_id,
                check_duplicates=check_duplicates
            )

            if draft_id == "duplicate":
                stats['duplicates'] += 1
                clear_create_draft_flag(notion, game_id)
            elif draft_id:
                stats['created'] += 1
                clear_create_draft_flag(notion, game_id)
            else:
                stats['failed'] += 1
                # Leave checkbox checked so user knows it didn't work
                set_game_draft_error(notion, game_id, "Draft failed — check contact has email")

        return stats

    except APIResponseError as e:
        print(f"Error querying Games: {e}", file=sys.stderr)
        return {'created': 0, 'skipped': 0, 'duplicates': 0, 'failed': 0}


def process_not_contacted_games(notion, games_db, contacts_db, templates_db, email_queue_db,
                                 check_duplicates=True):
    """
    Create drafts for all games with "Not Contacted" status.
    """
    print("Fetching games with 'Not Contacted' status...", file=sys.stderr)

    try:
        games = []
        has_more = True
        start_cursor = None
        while has_more:
            kwargs = {
                "database_id": games_db,
                "filter": {
                    "property": "Outreach Status",
                    "select": {"equals": "Not Contacted"}
                }
            }
            if start_cursor:
                kwargs["start_cursor"] = start_cursor
            response = notion.databases.query(**kwargs)
            games.extend(response['results'])
            has_more = response.get('has_more', False)
            start_cursor = response.get('next_cursor')
        print(f"Found {len(games)} games to process", file=sys.stderr)
        if check_duplicates:
            print("Duplicate checking: ENABLED", file=sys.stderr)
        else:
            print("Duplicate checking: DISABLED", file=sys.stderr)

        if not games:
            print("No games need drafts.", file=sys.stderr)
            return {'created': 0, 'skipped': 0, 'duplicates': 0, 'failed': 0}

        stats = {'created': 0, 'skipped': 0, 'duplicates': 0, 'failed': 0}

        for game_page in games:
            game_id = game_page['id']

            # Check if game has a contact
            props = game_page['properties']
            if not props.get('Contact', {}).get('relation'):
                print(f"\n[Skip] Game {game_id}: No contact linked", file=sys.stderr)
                stats['skipped'] += 1
                continue

            draft_id = create_draft_for_game(
                notion, games_db, contacts_db, templates_db, email_queue_db,
                game_id,
                check_duplicates=check_duplicates
            )

            if draft_id == "duplicate":
                stats['duplicates'] += 1
            elif draft_id:
                stats['created'] += 1
            else:
                stats['failed'] += 1

        return stats

    except APIResponseError as e:
        print(f"Error querying Games: {e}", file=sys.stderr)
        return {'created': 0, 'skipped': 0, 'duplicates': 0, 'failed': 0}


def main():
    parser = argparse.ArgumentParser(
        description="Create email drafts from game data"
    )
    parser.add_argument(
        "--game-id",
        help="Specific game page ID to create draft for"
    )
    parser.add_argument(
        "--template-id",
        help="Specific template page ID to use"
    )
    parser.add_argument(
        "--process-not-contacted",
        action="store_true",
        help="Create drafts for all games with 'Not Contacted' status"
    )
    parser.add_argument(
        "--process-flagged",
        action="store_true",
        help="Create drafts for games where 'Create Draft' checkbox is checked"
    )
    parser.add_argument(
        "--no-duplicate-check",
        action="store_true",
        help="Skip duplicate checking (email even if recently contacted)"
    )

    args = parser.parse_args()

    if not args.game_id and not args.process_not_contacted and not args.process_flagged:
        print("Error: Must specify --game-id or --process-not-contacted", file=sys.stderr)
        sys.exit(1)

    check_duplicates = not args.no_duplicate_check

    # Initialize client
    notion = get_notion_client()
    games_db, contacts_db, templates_db, email_queue_db = get_database_ids()

    if args.game_id:
        # Process single game
        draft_id = create_draft_for_game(
            notion, games_db, contacts_db, templates_db, email_queue_db,
            args.game_id, args.template_id,
            check_duplicates=check_duplicates
        )

        is_duplicate = draft_id == "duplicate"
        result = {
            "success": draft_id is not None and not is_duplicate,
            "game_id": args.game_id,
            "draft_id": None if is_duplicate else draft_id,
            "duplicate_skipped": is_duplicate
        }
        print(json.dumps(result, indent=2))
        sys.exit(0 if draft_id and not is_duplicate else 1)

    elif args.process_not_contacted:
        # Process all not-contacted games
        stats = process_not_contacted_games(
            notion, games_db, contacts_db, templates_db, email_queue_db,
            check_duplicates=check_duplicates
        )

        print(f"\n{'='*60}", file=sys.stderr)
        print("DRAFT CREATION COMPLETE", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        print(f"Drafts created: {stats['created']}", file=sys.stderr)
        print(f"Skipped (no contact): {stats['skipped']}", file=sys.stderr)
        print(f"Skipped (duplicate): {stats['duplicates']}", file=sys.stderr)
        print(f"Failed: {stats['failed']}", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

        result = {"success": True, **stats}
        print(json.dumps(result, indent=2))
        sys.exit(0)

    elif args.process_flagged:
        # Process games with "Create Draft" checkbox checked
        stats = process_flagged_games(
            notion, games_db, contacts_db, templates_db, email_queue_db,
            check_duplicates=check_duplicates
        )

        print(f"\n{'='*60}", file=sys.stderr)
        print("FLAGGED GAMES PROCESSED", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        print(f"Drafts created: {stats['created']}", file=sys.stderr)
        print(f"Skipped (no contact): {stats['skipped']}", file=sys.stderr)
        print(f"Skipped (duplicate): {stats['duplicates']}", file=sys.stderr)
        print(f"Failed: {stats['failed']}", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

        result = {"success": True, **stats}
        print(json.dumps(result, indent=2))
        sys.exit(0)


if __name__ == "__main__":
    main()
