#!/usr/bin/env python3
"""
Setup script to create all Notion CRM databases.

Creates 5 databases with proper relations:
1. Schools - Master list of schools
2. Contacts - Coach/staff contacts (linked to Schools)
3. Email Templates - Email templates with variables
4. Games - Games with Home/Away teams (linked to Schools, Contacts)
5. Email Queue - Email drafts and sent emails (linked to Games, Contacts, Templates)

Usage:
    python tools/setup_notion_databases.py --parent-page-id <PAGE_ID>

To find a parent page ID:
1. Open any page in Notion where you want the databases
2. Click Share → Copy link
3. The URL will be: notion.so/Page-Name-abc123def456...
4. The page ID is the last part after the dash (32 characters)
"""

import argparse
import json
import os
import sys
import re

try:
    from notion_client import Client
    from notion_client.errors import APIResponseError
except ImportError:
    print("Error: notion-client not installed. Run: pip install notion-client", file=sys.stderr)
    sys.exit(1)

from dotenv import load_dotenv

load_dotenv()


def get_notion_client():
    api_key = os.getenv('NOTION_API_KEY')
    if not api_key:
        print("Error: NOTION_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)
    return Client(auth=api_key)


def create_schools_database(notion, parent_id):
    """Create the Schools database."""
    print("Creating Schools database...", file=sys.stderr)

    try:
        response = notion.databases.create(
            parent={"type": "page_id", "page_id": parent_id},
            title=[{"type": "text", "text": {"content": "Schools"}}],
            icon={"type": "emoji", "emoji": "🏫"},
            properties={
                "School Name": {"title": {}},
                "Athletics URL": {"url": {}},
                "Coaches URL": {"url": {}},
                "Conference": {
                    "select": {
                        "options": [
                            {"name": "NEWMAC", "color": "blue"},
                            {"name": "NEC", "color": "green"},
                            {"name": "Patriot", "color": "red"},
                            {"name": "NESCAC", "color": "purple"},
                            {"name": "Ivy League", "color": "brown"},
                            {"name": "ACC", "color": "orange"},
                            {"name": "Big East", "color": "yellow"},
                            {"name": "Other", "color": "gray"},
                        ]
                    }
                },
                "Division": {
                    "select": {
                        "options": [
                            {"name": "D1", "color": "red"},
                            {"name": "D2", "color": "blue"},
                            {"name": "D3", "color": "green"},
                            {"name": "NAIA", "color": "purple"},
                        ]
                    }
                },
            }
        )
        print(f"  Created: {response['id']}", file=sys.stderr)
        return response['id']
    except APIResponseError as e:
        print(f"  Error: {e}", file=sys.stderr)
        return None


def create_contacts_database(notion, parent_id, schools_db_id):
    """Create the Contacts database with relation to Schools."""
    print("Creating Contacts database...", file=sys.stderr)

    try:
        response = notion.databases.create(
            parent={"type": "page_id", "page_id": parent_id},
            title=[{"type": "text", "text": {"content": "Contacts"}}],
            icon={"type": "emoji", "emoji": "👤"},
            properties={
                "Name": {"title": {}},
                "Email": {"email": {}},
                "Phone": {"phone_number": {}},
                "Title": {"rich_text": {}},
                "Sport": {
                    "select": {
                        "options": [
                            {"name": "Baseball", "color": "red"},
                            {"name": "Softball", "color": "pink"},
                            {"name": "Lacrosse", "color": "blue"},
                            {"name": "Soccer", "color": "green"},
                            {"name": "Basketball", "color": "orange"},
                            {"name": "Football", "color": "brown"},
                            {"name": "Hockey", "color": "purple"},
                            {"name": "Tennis", "color": "yellow"},
                            {"name": "Golf", "color": "gray"},
                            {"name": "Volleyball", "color": "default"},
                        ]
                    }
                },
                "School": {
                    "relation": {
                        "database_id": schools_db_id,
                        "single_property": {}
                    }
                },
                "Priority": {"number": {"format": "number"}},
                "Last Emailed": {"date": {}},
            }
        )
        print(f"  Created: {response['id']}", file=sys.stderr)
        return response['id']
    except APIResponseError as e:
        print(f"  Error: {e}", file=sys.stderr)
        return None


def create_templates_database(notion, parent_id):
    """Create the Email Templates database."""
    print("Creating Email Templates database...", file=sys.stderr)

    try:
        response = notion.databases.create(
            parent={"type": "page_id", "page_id": parent_id},
            title=[{"type": "text", "text": {"content": "Email Templates"}}],
            icon={"type": "emoji", "emoji": "📝"},
            properties={
                "Template Name": {"title": {}},
                "Sport": {
                    "select": {
                        "options": [
                            {"name": "All Sports", "color": "default"},
                            {"name": "Baseball", "color": "red"},
                            {"name": "Softball", "color": "pink"},
                            {"name": "Lacrosse", "color": "blue"},
                            {"name": "Soccer", "color": "green"},
                            {"name": "Basketball", "color": "orange"},
                        ]
                    }
                },
                "Subject Line": {"rich_text": {}},
                "Body": {"rich_text": {}},
                "Sequence Step": {"number": {"format": "number"}},
                "Days After Previous": {"number": {"format": "number"}},
            }
        )
        print(f"  Created: {response['id']}", file=sys.stderr)
        return response['id']
    except APIResponseError as e:
        print(f"  Error: {e}", file=sys.stderr)
        return None


def create_games_database(notion, parent_id, schools_db_id, contacts_db_id):
    """Create the Games database with relations."""
    print("Creating Games database...", file=sys.stderr)

    try:
        response = notion.databases.create(
            parent={"type": "page_id", "page_id": parent_id},
            title=[{"type": "text", "text": {"content": "Games"}}],
            icon={"type": "emoji", "emoji": "🏟️"},
            properties={
                "Game ID": {"title": {}},
                "Home Team": {
                    "relation": {
                        "database_id": schools_db_id,
                        "single_property": {}
                    }
                },
                "Away Team": {
                    "relation": {
                        "database_id": schools_db_id,
                        "single_property": {}
                    }
                },
                "Game Date": {"date": {}},
                "Sport": {
                    "select": {
                        "options": [
                            {"name": "Baseball", "color": "red"},
                            {"name": "Softball", "color": "pink"},
                            {"name": "Lacrosse", "color": "blue"},
                            {"name": "Soccer", "color": "green"},
                            {"name": "Basketball", "color": "orange"},
                            {"name": "Football", "color": "brown"},
                            {"name": "Hockey", "color": "purple"},
                        ]
                    }
                },
                "Gender": {
                    "select": {
                        "options": [
                            {"name": "Men", "color": "blue"},
                            {"name": "Women", "color": "pink"},
                        ]
                    }
                },
                "Venue": {"rich_text": {}},
                "Outreach Status": {
                    "select": {
                        "options": [
                            {"name": "Not Contacted", "color": "gray"},
                            {"name": "Email Sent", "color": "yellow"},
                            {"name": "Responded", "color": "blue"},
                            {"name": "Booked", "color": "green"},
                            {"name": "Declined", "color": "red"},
                        ]
                    }
                },
                "Last Contacted": {"date": {}},
                "Follow-up Date": {"date": {}},
                "Contact": {
                    "relation": {
                        "database_id": contacts_db_id,
                        "single_property": {}
                    }
                },
                "Notes": {"rich_text": {}},
            }
        )
        print(f"  Created: {response['id']}", file=sys.stderr)
        return response['id']
    except APIResponseError as e:
        print(f"  Error: {e}", file=sys.stderr)
        return None


def create_email_queue_database(notion, parent_id, games_db_id, contacts_db_id, templates_db_id):
    """Create the Email Queue database with relations."""
    print("Creating Email Queue database...", file=sys.stderr)

    try:
        response = notion.databases.create(
            parent={"type": "page_id", "page_id": parent_id},
            title=[{"type": "text", "text": {"content": "Email Queue"}}],
            icon={"type": "emoji", "emoji": "📧"},
            properties={
                "Email ID": {"title": {}},
                "Subject": {"rich_text": {}},
                "Body": {"rich_text": {}},
                "Status": {"status": {}},
                "Game": {
                    "relation": {
                        "database_id": games_db_id,
                        "single_property": {}
                    }
                },
                "Contact": {
                    "relation": {
                        "database_id": contacts_db_id,
                        "single_property": {}
                    }
                },
                "Template Used": {
                    "relation": {
                        "database_id": templates_db_id,
                        "single_property": {}
                    }
                },
                "Sent At": {"date": {}},
                "Created": {"created_time": {}},
            }
        )
        print(f"  Created: {response['id']}", file=sys.stderr)
        return response['id']
    except APIResponseError as e:
        print(f"  Error: {e}", file=sys.stderr)
        return None


def create_initial_templates(notion, templates_db_id):
    """Create initial email templates."""
    print("\nCreating initial email templates...", file=sys.stderr)

    templates = [
        {
            "name": "Initial Outreach",
            "sport": "All Sports",
            "sequence_step": 1,
            "days_after": 0,
            "subject": "Catering for {{away_school}} {{sport}} vs {{home_school}}",
            "body": """Hi {{contact_name}},

I'm reaching out about your upcoming {{sport}} game against {{home_school}} on {{game_date_formatted}}.

We specialize in team catering and would love to provide meals for your players and staff. We've worked with several NCAA programs in the Boston area.

Would you have a few minutes to discuss your team's catering needs?

Best regards,
Livite Sports Catering"""
        },
        {
            "name": "Follow-up #1",
            "sport": "All Sports",
            "sequence_step": 2,
            "days_after": 7,
            "subject": "Following up: Catering for {{away_school}} {{sport}}",
            "body": """Hi {{contact_name}},

I wanted to follow up on my previous email about catering for your {{sport}} game against {{home_school}}.

We offer flexible menu options that work great for away games - from boxed lunches to full team meals.

Would you have a few minutes to chat this week?

Best regards,
Livite Sports Catering"""
        },
        {
            "name": "Follow-up #2",
            "sport": "All Sports",
            "sequence_step": 3,
            "days_after": 7,
            "subject": "One more try: {{away_school}} {{sport}} catering",
            "body": """Hi {{contact_name}},

I know you're busy with the season, so I'll keep this brief.

If you ever need catering for away games in the Boston area, we'd love to help. Just reply to this email when you're ready.

Good luck this season!

Best,
Livite Sports Catering"""
        },
    ]

    for template in templates:
        try:
            notion.pages.create(
                parent={"database_id": templates_db_id},
                properties={
                    "Template Name": {"title": [{"text": {"content": template["name"]}}]},
                    "Sport": {"select": {"name": template["sport"]}},
                    "Sequence Step": {"number": template["sequence_step"]},
                    "Days After Previous": {"number": template["days_after"]},
                    "Subject Line": {"rich_text": [{"text": {"content": template["subject"]}}]},
                    "Body": {"rich_text": [{"text": {"content": template["body"]}}]},
                }
            )
            print(f"  Created template: {template['name']}", file=sys.stderr)
        except APIResponseError as e:
            print(f"  Error creating template {template['name']}: {e}", file=sys.stderr)


def update_env_file(db_ids):
    """Update .env file with database IDs."""
    env_path = ".env"

    try:
        with open(env_path, 'r') as f:
            content = f.read()

        # Update each database ID
        for key, value in db_ids.items():
            if value:
                # Replace the line
                pattern = rf'^{key}=.*$'
                replacement = f'{key}={value}'
                content = re.sub(pattern, replacement, content, flags=re.MULTILINE)

        with open(env_path, 'w') as f:
            f.write(content)

        print(f"\nUpdated {env_path} with database IDs", file=sys.stderr)
        return True
    except Exception as e:
        print(f"Error updating .env: {e}", file=sys.stderr)
        return False


def add_response_tracking_properties(notion, email_queue_db_id):
    """Add Gmail response tracking properties to Email Queue database.

    One-time migration to add:
    - Gmail Message ID (rich_text)
    - Gmail Thread ID (rich_text)
    - Response Date (date)
    """
    print("Adding response tracking properties to Email Queue...", file=sys.stderr)

    try:
        notion.databases.update(
            database_id=email_queue_db_id,
            properties={
                "Gmail Message ID": {"rich_text": {}},
                "Gmail Thread ID": {"rich_text": {}},
                "Response Date": {"date": {}},
            }
        )
        print("  Added: Gmail Message ID, Gmail Thread ID, Response Date", file=sys.stderr)
        return True
    except APIResponseError as e:
        print(f"  Error: {e}", file=sys.stderr)
        return False


def add_game_date_property(notion, email_queue_db_id):
    """Add Game Date property to Email Queue database for calendar views."""
    print("Adding Game Date to Email Queue...", file=sys.stderr)

    try:
        notion.databases.update(
            database_id=email_queue_db_id,
            properties={
                "Game Date": {"date": {}},
            }
        )
        print("  Added: Game Date (date)", file=sys.stderr)
        return True
    except APIResponseError as e:
        print(f"  Error: {e}", file=sys.stderr)
        return False


def add_convert_to_order_property(notion, email_queue_db_id):
    """Add Convert to Order checkbox to Email Queue database."""
    print("Adding Convert to Order checkbox to Email Queue...", file=sys.stderr)

    try:
        notion.databases.update(
            database_id=email_queue_db_id,
            properties={
                "Convert to Order": {"checkbox": {}},
            }
        )
        print("  Added: Convert to Order (checkbox)", file=sys.stderr)
        return True
    except APIResponseError as e:
        print(f"  Error: {e}", file=sys.stderr)
        return False


def add_local_property(notion, schools_db_id):
    """Add Local checkbox to Schools database for territory filtering."""
    print("Adding Local checkbox to Schools...", file=sys.stderr)

    try:
        notion.databases.update(
            database_id=schools_db_id,
            properties={
                "Local": {"checkbox": {}},
            }
        )
        print("  Added: Local (checkbox)", file=sys.stderr)
        return True
    except APIResponseError as e:
        print(f"  Error: {e}", file=sys.stderr)
        return False


def add_local_game_property(notion, games_db_id):
    """Add Local Game checkbox to Games database."""
    print("Adding Local Game checkbox to Games...", file=sys.stderr)

    try:
        notion.databases.update(
            database_id=games_db_id,
            properties={
                "Local Game": {"checkbox": {}},
            }
        )
        print("  Added: Local Game (checkbox)", file=sys.stderr)
        return True
    except APIResponseError as e:
        print(f"  Error: {e}", file=sys.stderr)
        return False


def mark_local_schools(notion, schools_db_id):
    """Set Local=True for Boston-area schools."""
    LOCAL_SCHOOLS = [
        "Boston College", "Boston University", "Northeastern", "Harvard",
        "Tufts", "MIT", "Bentley", "Babson", "Brandeis", "Emerson",
        "Emmanuel", "Simmons", "Wentworth", "Stonehill", "Suffolk",
        "Regis", "Wellesley", "UMass Boston", "Fisher College",
    ]

    print(f"Marking {len(LOCAL_SCHOOLS)} Boston-area schools as Local...", file=sys.stderr)

    # Query all schools
    all_schools = []
    has_more = True
    start_cursor = None
    while has_more:
        kwargs = {"database_id": schools_db_id}
        if start_cursor:
            kwargs["start_cursor"] = start_cursor
        response = notion.databases.query(**kwargs)
        all_schools.extend(response['results'])
        has_more = response.get('has_more', False)
        start_cursor = response.get('next_cursor')

    print(f"  Found {len(all_schools)} schools total", file=sys.stderr)
    marked = 0

    for school in all_schools:
        props = school['properties']
        name = ''
        if props.get('School Name', {}).get('title'):
            name = ''.join(t.get('plain_text', '') for t in props['School Name']['title'])

        if any(local.lower() in name.lower() for local in LOCAL_SCHOOLS):
            try:
                notion.pages.update(
                    page_id=school['id'],
                    properties={"Local": {"checkbox": True}}
                )
                print(f"  Marked: {name}", file=sys.stderr)
                marked += 1
            except APIResponseError as e:
                print(f"  Error marking {name}: {e}", file=sys.stderr)

    print(f"  Marked {marked} schools as Local", file=sys.stderr)
    return marked


def backfill_local_games(notion, games_db_id, schools_db_id):
    """Set Local Game=True for games where Home Team is a local school."""
    print("Backfilling Local Game flag on Games...", file=sys.stderr)

    # First, get all local school IDs
    local_response = notion.databases.query(
        database_id=schools_db_id,
        filter={"property": "Local", "checkbox": {"equals": True}}
    )
    local_school_ids = {s['id'] for s in local_response['results']}
    print(f"  Found {len(local_school_ids)} local schools", file=sys.stderr)

    if not local_school_ids:
        print("  No local schools found — run --mark-local-schools first", file=sys.stderr)
        return 0

    # Query all games
    all_games = []
    has_more = True
    start_cursor = None
    while has_more:
        kwargs = {"database_id": games_db_id}
        if start_cursor:
            kwargs["start_cursor"] = start_cursor
        response = notion.databases.query(**kwargs)
        all_games.extend(response['results'])
        has_more = response.get('has_more', False)
        start_cursor = response.get('next_cursor')

    print(f"  Found {len(all_games)} games total", file=sys.stderr)
    marked = 0

    for game in all_games:
        props = game['properties']
        home_rel = props.get('Home Team', {}).get('relation', [])
        if home_rel and home_rel[0]['id'] in local_school_ids:
            try:
                notion.pages.update(
                    page_id=game['id'],
                    properties={"Local Game": {"checkbox": True}}
                )
                marked += 1
            except APIResponseError as e:
                print(f"  Error updating game {game['id']}: {e}", file=sys.stderr)

    print(f"  Marked {marked} games as Local Game", file=sys.stderr)
    return marked


def main():
    parser = argparse.ArgumentParser(description="Set up Notion CRM databases")
    parser.add_argument(
        "--parent-page-id",
        help="Notion page ID where databases will be created"
    )
    parser.add_argument(
        "--add-response-tracking",
        action="store_true",
        help="Add Gmail response tracking properties to Email Queue (one-time migration)"
    )
    parser.add_argument(
        "--add-convert-to-order",
        action="store_true",
        help="Add Convert to Order checkbox to Email Queue (one-time migration)"
    )
    parser.add_argument(
        "--add-game-date",
        action="store_true",
        help="Add Game Date property to Email Queue (one-time migration)"
    )
    parser.add_argument(
        "--add-local",
        action="store_true",
        help="Add Local checkbox to Schools DB (one-time migration)"
    )
    parser.add_argument(
        "--add-local-game",
        action="store_true",
        help="Add Local Game checkbox to Games DB (one-time migration)"
    )
    parser.add_argument(
        "--mark-local-schools",
        action="store_true",
        help="Set Local=True for Boston-area schools"
    )
    parser.add_argument(
        "--backfill-local-games",
        action="store_true",
        help="Set Local Game=True for games where Home Team is local"
    )

    args = parser.parse_args()

    # Handle response tracking migration
    if args.add_response_tracking:
        email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB')
        if not email_queue_db:
            print("Error: NOTION_EMAIL_QUEUE_DB not set in .env", file=sys.stderr)
            sys.exit(1)
        notion = get_notion_client()
        success = add_response_tracking_properties(notion, email_queue_db)
        sys.exit(0 if success else 1)

    # Handle convert to order migration
    if args.add_convert_to_order:
        email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB')
        if not email_queue_db:
            print("Error: NOTION_EMAIL_QUEUE_DB not set in .env", file=sys.stderr)
            sys.exit(1)
        notion = get_notion_client()
        success = add_convert_to_order_property(notion, email_queue_db)
        sys.exit(0 if success else 1)

    # Handle game date migration
    if args.add_game_date:
        email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB')
        if not email_queue_db:
            print("Error: NOTION_EMAIL_QUEUE_DB not set in .env", file=sys.stderr)
            sys.exit(1)
        notion = get_notion_client()
        success = add_game_date_property(notion, email_queue_db)
        sys.exit(0 if success else 1)

    # Handle Local property migration
    if args.add_local:
        schools_db = os.getenv('NOTION_SCHOOLS_DB')
        if not schools_db:
            print("Error: NOTION_SCHOOLS_DB not set in .env", file=sys.stderr)
            sys.exit(1)
        notion = get_notion_client()
        success = add_local_property(notion, schools_db)
        sys.exit(0 if success else 1)

    # Handle Local Game property migration
    if args.add_local_game:
        games_db = os.getenv('NOTION_GAMES_DB')
        if not games_db:
            print("Error: NOTION_GAMES_DB not set in .env", file=sys.stderr)
            sys.exit(1)
        notion = get_notion_client()
        success = add_local_game_property(notion, games_db)
        sys.exit(0 if success else 1)

    # Handle mark local schools
    if args.mark_local_schools:
        schools_db = os.getenv('NOTION_SCHOOLS_DB')
        if not schools_db:
            print("Error: NOTION_SCHOOLS_DB not set in .env", file=sys.stderr)
            sys.exit(1)
        notion = get_notion_client()
        mark_local_schools(notion, schools_db)
        sys.exit(0)

    # Handle backfill local games
    if args.backfill_local_games:
        games_db = os.getenv('NOTION_GAMES_DB')
        schools_db = os.getenv('NOTION_SCHOOLS_DB')
        if not games_db or not schools_db:
            print("Error: NOTION_GAMES_DB and NOTION_SCHOOLS_DB must be set in .env", file=sys.stderr)
            sys.exit(1)
        notion = get_notion_client()
        backfill_local_games(notion, games_db, schools_db)
        sys.exit(0)

    if not args.parent_page_id:
        parser.error("--parent-page-id is required (unless using a migration flag)")

    # Clean up page ID (remove dashes if present)
    parent_id = args.parent_page_id.replace("-", "")

    # Validate format
    if len(parent_id) != 32:
        print("Error: Page ID should be 32 characters (without dashes)", file=sys.stderr)
        print("Example: abc123def456789012345678901234", file=sys.stderr)
        sys.exit(1)

    # Format with dashes for API
    parent_id = f"{parent_id[:8]}-{parent_id[8:12]}-{parent_id[12:16]}-{parent_id[16:20]}-{parent_id[20:]}"

    print(f"Setting up Notion CRM databases...", file=sys.stderr)
    print(f"Parent page: {parent_id}", file=sys.stderr)
    print("", file=sys.stderr)

    notion = get_notion_client()

    # Create databases in order (respecting dependencies)
    schools_db_id = create_schools_database(notion, parent_id)
    if not schools_db_id:
        print("\nFailed to create Schools database. Aborting.", file=sys.stderr)
        sys.exit(1)

    contacts_db_id = create_contacts_database(notion, parent_id, schools_db_id)
    if not contacts_db_id:
        print("\nFailed to create Contacts database. Aborting.", file=sys.stderr)
        sys.exit(1)

    templates_db_id = create_templates_database(notion, parent_id)
    if not templates_db_id:
        print("\nFailed to create Templates database. Aborting.", file=sys.stderr)
        sys.exit(1)

    games_db_id = create_games_database(notion, parent_id, schools_db_id, contacts_db_id)
    if not games_db_id:
        print("\nFailed to create Games database. Aborting.", file=sys.stderr)
        sys.exit(1)

    email_queue_db_id = create_email_queue_database(
        notion, parent_id, games_db_id, contacts_db_id, templates_db_id
    )
    if not email_queue_db_id:
        print("\nFailed to create Email Queue database. Aborting.", file=sys.stderr)
        sys.exit(1)

    # Create initial templates
    create_initial_templates(notion, templates_db_id)

    # Update .env file
    db_ids = {
        "NOTION_SCHOOLS_DB": schools_db_id,
        "NOTION_CONTACTS_DB": contacts_db_id,
        "NOTION_TEMPLATES_DB": templates_db_id,
        "NOTION_GAMES_DB": games_db_id,
        "NOTION_EMAIL_QUEUE_DB": email_queue_db_id,
    }
    update_env_file(db_ids)

    # Print summary
    print(f"\n{'='*60}", file=sys.stderr)
    print("SETUP COMPLETE!", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"Schools DB:      {schools_db_id}", file=sys.stderr)
    print(f"Contacts DB:     {contacts_db_id}", file=sys.stderr)
    print(f"Templates DB:    {templates_db_id}", file=sys.stderr)
    print(f"Games DB:        {games_db_id}", file=sys.stderr)
    print(f"Email Queue DB:  {email_queue_db_id}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print("\nAll database IDs have been saved to .env", file=sys.stderr)
    print("\nNext steps:", file=sys.stderr)
    print("1. Open Notion and verify the databases were created", file=sys.stderr)
    print("2. Run: python tools/export_to_notion.py --input .tmp/boston_college_matched.json --home-school 'Boston College'", file=sys.stderr)

    # Output JSON
    result = {
        "success": True,
        "databases": db_ids
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
