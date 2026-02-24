#!/usr/bin/env python3
"""
Cleanup Notion data:
1. Move tournament games to Tournaments database
2. Clean up school names (remove ranking prefixes like "No. 18")
3. Delete tournament entries from Games database
"""

import os
import re
import sys
from notion_client import Client
from notion_client.errors import APIResponseError
from dotenv import load_dotenv

load_dotenv()


def get_notion_client():
    return Client(auth=os.getenv('NOTION_API_KEY'))


def is_tournament(game_title):
    """Check if a game is a tournament/championship entry."""
    patterns = [
        r'NCAA', r'Championship', r'Tournament', r'Regional',
        r'\bTBD\b', r'\bTBA\b', r'Winner of', r'Loser of',
        r'GNAC\s+Champ', r'NEWMAC\s+Champ', r'Ivy\s+League\s+Champ',
        r'Consolation', r'Semifinal', r'Quarterfinal', r'Final Four'
    ]
    pattern = '|'.join(patterns)
    return bool(re.search(pattern, game_title, re.IGNORECASE))


def clean_school_name(name):
    """Remove ranking prefixes from school names."""
    # Remove "No. 18" or "RV" prefixes
    cleaned = re.sub(r'^No\.\s*\d+\s*', '', name)
    cleaned = re.sub(r'^RV\s+', '', cleaned)
    # Remove state suffixes like (Mass.), (Conn.)
    cleaned = re.sub(r'\s*\([A-Z][a-z.]+\)\s*$', '', cleaned)
    cleaned = re.sub(r'\s*\(DH\)\s*$', '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def extract_title(title_array):
    """Extract title text from Notion title property."""
    if not title_array:
        return ''
    return ''.join(item.get('plain_text', '') for item in title_array)


def migrate_tournaments(notion):
    """Move tournament games to Tournaments database."""
    games_db = os.getenv('NOTION_GAMES_DB')
    tournaments_db = os.getenv('NOTION_TOURNAMENTS_DB')

    print("Fetching all games...")
    games = []
    response = notion.databases.query(database_id=games_db)
    games.extend(response['results'])
    while response.get('has_more'):
        response = notion.databases.query(
            database_id=games_db,
            start_cursor=response['next_cursor']
        )
        games.extend(response['results'])

    print(f"Found {len(games)} total games")

    # Find tournament games
    tournament_games = []
    for game in games:
        title = extract_title(game['properties'].get('Game ID', {}).get('title', []))
        if is_tournament(title):
            tournament_games.append(game)

    print(f"Found {len(tournament_games)} tournament games to migrate")

    # Create tournament entries and delete from games
    migrated = 0
    deleted = 0

    for game in tournament_games:
        props = game['properties']
        title = extract_title(props.get('Game ID', {}).get('title', []))

        # Extract school from Home Team relation
        school_id = None
        if props.get('Home Team', {}).get('relation'):
            school_id = props['Home Team']['relation'][0]['id']

        # Get sport
        sport = props.get('Sport', {}).get('select', {}).get('name', '')

        # Get date
        game_date = None
        if props.get('Game Date', {}).get('date'):
            game_date = props['Game Date']['date']

        # Create tournament entry
        try:
            new_props = {
                'Tournament Name': {'title': [{'text': {'content': title}}]},
                'Outreach Status': {'select': {'name': 'Not Contacted'}},
            }

            if school_id:
                new_props['School'] = {'relation': [{'id': school_id}]}
            if sport:
                new_props['Sport'] = {'select': {'name': sport}}
            if game_date:
                new_props['Date Range'] = {'date': game_date}

            notion.pages.create(
                parent={'database_id': tournaments_db},
                properties=new_props
            )
            migrated += 1

            # Delete from games
            notion.pages.update(page_id=game['id'], archived=True)
            deleted += 1

            if migrated % 10 == 0:
                print(f"  Migrated {migrated}...")

        except APIResponseError as e:
            print(f"  Error with {title}: {e}")

    print(f"\nMigration complete: {migrated} tournaments created, {deleted} games archived")
    return migrated


def clean_school_names(notion):
    """Clean up school names to remove ranking prefixes."""
    schools_db = os.getenv('NOTION_SCHOOLS_DB')

    print("\nFetching all schools...")
    schools = []
    response = notion.databases.query(database_id=schools_db)
    schools.extend(response['results'])
    while response.get('has_more'):
        response = notion.databases.query(
            database_id=schools_db,
            start_cursor=response['next_cursor']
        )
        schools.extend(response['results'])

    print(f"Found {len(schools)} schools")

    # Find schools with dirty names
    to_clean = []
    for school in schools:
        name = extract_title(school['properties'].get('School Name', {}).get('title', []))
        cleaned = clean_school_name(name)
        if cleaned != name:
            to_clean.append((school['id'], name, cleaned))

    print(f"Found {len(to_clean)} schools to clean")

    # Clean names
    cleaned_count = 0
    for page_id, old_name, new_name in to_clean:
        print(f"  '{old_name}' → '{new_name}'")
        try:
            notion.pages.update(
                page_id=page_id,
                properties={
                    'School Name': {'title': [{'text': {'content': new_name}}]}
                }
            )
            cleaned_count += 1
        except APIResponseError as e:
            print(f"    Error: {e}")

    print(f"\nCleaned {cleaned_count} school names")
    return cleaned_count


def main():
    notion = get_notion_client()

    print("=" * 60)
    print("NOTION DATA CLEANUP")
    print("=" * 60)

    # Step 1: Migrate tournaments
    print("\n--- Step 1: Migrate Tournament Games ---")
    migrate_tournaments(notion)

    # Step 2: Clean school names
    print("\n--- Step 2: Clean School Names ---")
    clean_school_names(notion)

    print("\n" + "=" * 60)
    print("CLEANUP COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
