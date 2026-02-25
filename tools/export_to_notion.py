#!/usr/bin/env python3
"""
Tool: export_to_notion.py
Purpose: Export validated game-contact data to Notion CRM databases

Creates/updates entries in:
1. Schools database - Master list of schools
2. Contacts database - Coaching staff with school relations
3. Games database - Games with Home Team, Away Team, and Contact relations

Usage:
    python tools/export_to_notion.py \
        --input .tmp/boston_college_matched.json \
        --home-school "Boston College"

Required environment variables:
    NOTION_API_KEY - Notion integration token
    NOTION_GAMES_DB - Games database ID
    NOTION_SCHOOLS_DB - Schools database ID
    NOTION_CONTACTS_DB - Contacts database ID
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

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
    """Initialize Notion client with API key from environment."""
    api_key = os.getenv('NOTION_API_KEY')
    if not api_key:
        print("Error: NOTION_API_KEY not set in environment.", file=sys.stderr)
        print("\nTo set up Notion API:", file=sys.stderr)
        print("1. Go to https://www.notion.so/my-integrations", file=sys.stderr)
        print("2. Create a new integration", file=sys.stderr)
        print("3. Copy the Internal Integration Token", file=sys.stderr)
        print("4. Add to .env: NOTION_API_KEY=secret_xxxxx", file=sys.stderr)
        print("5. Share your databases with the integration", file=sys.stderr)
        sys.exit(1)

    return Client(auth=api_key)


def get_database_ids():
    """Get database IDs from environment variables."""
    games_db = os.getenv('NOTION_GAMES_DB')
    schools_db = os.getenv('NOTION_SCHOOLS_DB')
    contacts_db = os.getenv('NOTION_CONTACTS_DB')

    missing = []
    if not games_db:
        missing.append('NOTION_GAMES_DB')
    if not schools_db:
        missing.append('NOTION_SCHOOLS_DB')
    if not contacts_db:
        missing.append('NOTION_CONTACTS_DB')

    if missing:
        print(f"Error: Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        print("\nAdd database IDs to .env file.", file=sys.stderr)
        print("To find a database ID:", file=sys.stderr)
        print("1. Open the database in Notion", file=sys.stderr)
        print("2. Copy the URL: notion.so/xxxxx?v=yyyyy", file=sys.stderr)
        print("3. The ID is the xxxxx part (32 chars, no dashes)", file=sys.stderr)
        sys.exit(1)

    return games_db, schools_db, contacts_db


def normalize_time(time_str):
    """Normalize time strings to standard 12hr format."""
    if not time_str:
        return ''
    time_str = time_str.strip()
    if time_str.upper() in ('TBA', 'TBD', ''):
        return 'TBA'

    # Strip timezone suffixes
    cleaned = re.sub(r'\s*\(.*?\)', '', time_str).strip()
    cleaned = re.sub(r'\s+(ET|EST|CST|CT|MT|MST|PT|PST)$', '', cleaned, flags=re.IGNORECASE).strip()

    # Detect AM/PM
    is_pm = bool(re.search(r'p\.?m\.?', cleaned, re.IGNORECASE))
    is_am = bool(re.search(r'a\.?m\.?', cleaned, re.IGNORECASE))

    # Strip AM/PM text
    cleaned = re.sub(r'\s*(a\.?m\.?|p\.?m\.?)', '', cleaned, flags=re.IGNORECASE).strip()

    # Parse hour:minute
    match = re.match(r'^(\d{1,2}):(\d{2})$', cleaned)
    if match:
        hour, minute = match.group(1), match.group(2)
    else:
        match = re.match(r'^(\d{1,2})$', cleaned)
        if match:
            hour, minute = match.group(1), '00'
        else:
            return time_str

    suffix = 'pm' if is_pm else ('am' if is_am else '')
    return f"{hour}:{minute}{suffix}"


_school_cache = {}  # name -> page_id


def find_or_create_school(notion, schools_db, school_name, athletics_url='', coaches_url=''):
    """
    Find existing school or create new one.
    Returns the page ID.  Results are cached to avoid redundant API calls.
    """
    if not school_name or not school_name.strip():
        return None

    # Check cache first
    if school_name in _school_cache:
        return _school_cache[school_name]

    # Search for existing school
    try:
        response = notion.databases.query(
            database_id=schools_db,
            filter={
                "property": "School Name",
                "title": {
                    "equals": school_name
                }
            }
        )

        if response['results']:
            page_id = response['results'][0]['id']
            print(f"  Found existing school: {school_name}", file=sys.stderr)
            _school_cache[school_name] = page_id
            return page_id
    except APIResponseError as e:
        print(f"  Warning: Could not search for school: {e}", file=sys.stderr)

    # Create new school
    try:
        properties = {
            "School Name": {
                "title": [{"text": {"content": school_name}}]
            }
        }

        if athletics_url:
            properties["Athletics URL"] = {"url": athletics_url}
        if coaches_url:
            properties["Coaches URL"] = {"url": coaches_url}

        response = notion.pages.create(
            parent={"database_id": schools_db},
            properties=properties
        )
        print(f"  Created new school: {school_name}", file=sys.stderr)
        _school_cache[school_name] = response['id']
        return response['id']

    except APIResponseError as e:
        print(f"  Error creating school {school_name}: {e}", file=sys.stderr)
        return None


def find_or_create_contact(notion, contacts_db, schools_db, contact_data, school_name):
    """
    Find existing contact or create new one.
    Returns the page ID.
    """
    name = contact_data.get('contact_name', '')
    email = contact_data.get('contact_email', '')

    if not name or not email:
        return None

    # Search for existing contact by email
    try:
        response = notion.databases.query(
            database_id=contacts_db,
            filter={
                "property": "Email",
                "email": {
                    "equals": email
                }
            }
        )

        if response['results']:
            page_id = response['results'][0]['id']
            return page_id
    except APIResponseError as e:
        print(f"  Warning: Could not search for contact: {e}", file=sys.stderr)

    # Find school page ID for relation
    school_page_id = find_or_create_school(notion, schools_db, school_name)

    # Create new contact
    try:
        properties = {
            "Name": {
                "title": [{"text": {"content": name}}]
            },
            "Email": {"email": email},
        }

        # Add optional fields
        title = contact_data.get('contact_title', '')
        if title:
            properties["Title"] = {"rich_text": [{"text": {"content": title}}]}

        phone = contact_data.get('contact_phone', '')
        if phone and phone != 'Not Found':
            properties["Phone"] = {"phone_number": phone}

        # Add school relation
        if school_page_id:
            properties["School"] = {"relation": [{"id": school_page_id}]}

        # Add sport
        sport = contact_data.get('sport', '')
        if sport:
            properties["Sport"] = {"select": {"name": sport}}

        response = notion.pages.create(
            parent={"database_id": contacts_db},
            properties=properties
        )
        print(f"  Created contact: {name} ({email})", file=sys.stderr)
        return response['id']

    except APIResponseError as e:
        print(f"  Error creating contact {name}: {e}", file=sys.stderr)
        return None


def create_game(notion, games_db, schools_db, contacts_db, game_data, home_school_name,
                home_school_id=None):
    """
    Create a game entry with relations to home team, away team, and contact.
    home_school_id can be pre-resolved to avoid redundant API lookups.
    """
    opponent = game_data.get('opponent', '')
    sport = game_data.get('sport', '')

    # Generate game ID
    date_str = game_data.get('parsed_date', '')[:10] if game_data.get('parsed_date') else ''
    game_id = f"{home_school_name} vs {opponent} - {date_str}"

    # Check if game already exists
    try:
        response = notion.databases.query(
            database_id=games_db,
            filter={
                "property": "Game ID",
                "title": {
                    "equals": game_id
                }
            }
        )

        if response['results']:
            print(f"  Game already exists: {game_id}", file=sys.stderr)
            return response['results'][0]['id']
    except APIResponseError as e:
        pass  # Continue to create

    # Find/create school pages (use pre-resolved ID if available)
    if not home_school_id:
        home_school_id = find_or_create_school(notion, schools_db, home_school_name)
    away_school_id = find_or_create_school(
        notion, schools_db, opponent,
        coaches_url=game_data.get('opponent_coaches_url', '')
    )

    # Find/create contact
    contact_id = None
    if game_data.get('match_status') == 'success':
        contact_id = find_or_create_contact(
            notion, contacts_db, schools_db,
            game_data, opponent
        )

    # Build game properties
    properties = {
        "Game ID": {
            "title": [{"text": {"content": game_id}}]
        }
    }

    # Home Team relation
    if home_school_id:
        properties["Home Team"] = {"relation": [{"id": home_school_id}]}

    # Away Team relation
    if away_school_id:
        properties["Away Team"] = {"relation": [{"id": away_school_id}]}

    # Game Date
    parsed_date = game_data.get('parsed_date', '')
    if parsed_date:
        date_only = parsed_date.split('T')[0]
        properties["Game Date"] = {"date": {"start": date_only}}

    # Sport
    if sport:
        properties["Sport"] = {"select": {"name": sport}}

    # Gender
    gender = game_data.get('gender', '')
    if gender and gender != 'Unknown':
        properties["Gender"] = {"select": {"name": gender}}

    # Venue
    venue = game_data.get('venue', '')
    if venue:
        properties["Venue"] = {"rich_text": [{"text": {"content": venue}}]}

    # Contact relation
    if contact_id:
        properties["Contact"] = {"relation": [{"id": contact_id}]}

    # Visiting Team (e.g. "Holy Cross Women's Basketball")
    if opponent and sport:
        if gender and gender != 'Unknown':
            visiting_team = "{} {}'s {}".format(opponent, gender, sport)
        else:
            visiting_team = "{} {}".format(opponent, sport)
        properties["Visiting Team"] = {"rich_text": [{"text": {"content": visiting_team}}]}

    # Outreach Status
    properties["Outreach Status"] = {"select": {"name": "Not Contacted"}}

    # Auto-set Local Game if home team is a local school
    if home_school_id:
        try:
            home_page = notion.pages.retrieve(page_id=home_school_id)
            if home_page['properties'].get('Local', {}).get('checkbox', False):
                properties["Local Game"] = {"checkbox": True}
        except Exception:
            pass

    # Create the game
    try:
        response = notion.pages.create(
            parent={"database_id": games_db},
            properties=properties
        )
        print(f"  Created game: {game_id}", file=sys.stderr)
        return response['id']

    except APIResponseError as e:
        print(f"  Error creating game {game_id}: {e}", file=sys.stderr)
        return None


def export_to_notion(input_file, home_school_name):
    """
    Export validated matches to Notion databases.
    """
    # Load validated data
    print(f"Loading data from {input_file}...", file=sys.stderr)
    try:
        with open(input_file, 'r') as f:
            validated_data = json.load(f)
    except Exception as e:
        print(f"Error loading input file: {e}", file=sys.stderr)
        sys.exit(1)

    matches = validated_data.get('validated_matches', [])
    print(f"Found {len(matches)} games to export", file=sys.stderr)

    # Initialize Notion client
    print("Connecting to Notion...", file=sys.stderr)
    notion = get_notion_client()
    games_db, schools_db, contacts_db = get_database_ids()

    # Track statistics
    stats = {
        'games_created': 0,
        'games_skipped': 0,
        'schools_created': 0,
        'contacts_created': 0,
        'errors': 0
    }

    # Resolve home school once and cache it
    print(f"\nEnsuring home school exists: {home_school_name}", file=sys.stderr)
    home_school_id = find_or_create_school(notion, schools_db, home_school_name)
    if not home_school_id:
        print(f"ERROR: Could not find or create home school '{home_school_name}'", file=sys.stderr)
        return False

    # Process each game
    print(f"\nExporting {len(matches)} games...", file=sys.stderr)

    for i, game in enumerate(matches, 1):
        print(f"\n[{i}/{len(matches)}] Processing: {game.get('opponent', 'Unknown')}", file=sys.stderr)

        game_id = create_game(
            notion, games_db, schools_db, contacts_db,
            game, home_school_name, home_school_id=home_school_id
        )

        if game_id:
            stats['games_created'] += 1
        else:
            stats['errors'] += 1

    # Print summary
    print(f"\n{'='*60}", file=sys.stderr)
    print("EXPORT COMPLETE", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"Games exported: {stats['games_created']}", file=sys.stderr)
    print(f"Errors: {stats['errors']}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    # Output JSON result
    result = {
        "success": True,
        "home_school": home_school_name,
        "games_exported": stats['games_created'],
        "errors": stats['errors']
    }
    print(json.dumps(result, indent=2))

    return stats['errors'] == 0


def main():
    parser = argparse.ArgumentParser(
        description="Export validated contacts to Notion CRM"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to validated matches JSON file"
    )
    parser.add_argument(
        "--home-school",
        required=True,
        help="Name of the home school (your school)"
    )

    args = parser.parse_args()

    success = export_to_notion(args.input, args.home_school)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
