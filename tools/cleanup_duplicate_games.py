#!/usr/bin/env python3
"""
Tool: cleanup_duplicate_games.py
Purpose: Delete duplicate game entries in Notion where the title starts with " vs "
         (missing home team) and a correct entry with the home team exists.

Pattern found: broken entries have titles like " vs Opponent - date" while correct
entries have "HomeTeam vs Opponent - date" for the same opponent+date combo.

Usage:
    # Dry run — show what would be deleted
    python tools/cleanup_duplicate_games.py --dry-run

    # Actually delete duplicates
    python tools/cleanup_duplicate_games.py
"""

import argparse
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

from dotenv import load_dotenv
from notion_client import Client

load_dotenv()


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Clean up duplicate games in Notion")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted")
    args = parser.parse_args()

    notion = Client(auth=os.getenv('NOTION_API_KEY'))
    games_db = os.getenv('NOTION_GAMES_DB')

    if not games_db:
        log("Error: NOTION_GAMES_DB must be set")
        sys.exit(1)

    # Load all games
    log("Loading all games from Notion...")
    all_games = []
    has_more = True
    start_cursor = None

    while has_more:
        kwargs = {'database_id': games_db}
        if start_cursor:
            kwargs['start_cursor'] = start_cursor
        response = notion.databases.query(**kwargs)
        all_games.extend(response['results'])
        has_more = response.get('has_more', False)
        start_cursor = response.get('next_cursor')

    log(f"Total games loaded: {len(all_games)}")

    # Extract titles and dates
    games_data = []
    for page in all_games:
        props = page['properties']
        title_parts = props.get('Game ID', {}).get('title', [])
        title = ''.join(t.get('plain_text', '') for t in title_parts)
        date_prop = props.get('Game Date', {}).get('date')
        game_date = date_prop.get('start', '') if date_prop else ''
        visiting = ''.join(t.get('plain_text', '') for t in props.get('Visiting Team', {}).get('rich_text', []))

        games_data.append({
            'id': page['id'],
            'title': title,
            'game_date': game_date,
            'visiting_team': visiting,
            'is_broken': title.strip().startswith('vs '),
        })

    broken = [g for g in games_data if g['is_broken']]
    correct = [g for g in games_data if not g['is_broken']]

    log(f"Broken entries (title starts with 'vs '): {len(broken)}")
    log(f"Correct entries: {len(correct)}")

    # Index correct games by (visiting_team, date) for matching
    correct_index = defaultdict(list)
    for g in correct:
        key = (g['visiting_team'].strip().lower(), g['game_date'])
        correct_index[key].append(g)

    # Find broken entries that have a matching correct entry
    to_delete = []
    no_match = []

    for g in broken:
        key = (g['visiting_team'].strip().lower(), g['game_date'])
        matches = correct_index.get(key, [])
        if matches:
            to_delete.append({
                'broken': g,
                'matching_correct': matches[0],
            })
        else:
            no_match.append(g)

    log(f"\nDuplicates to delete (broken + matching correct exists): {len(to_delete)}")
    log(f"Broken entries with NO matching correct entry (will skip): {len(no_match)}")

    if no_match:
        log("\n--- Broken entries WITHOUT a match (not deleting) ---")
        for g in no_match[:10]:
            log(f"  '{g['title']}' | {g['game_date']} | {g['visiting_team']}")
        if len(no_match) > 10:
            log(f"  ... and {len(no_match) - 10} more")

    if not to_delete:
        log("\nNo duplicates to clean up!")
        return

    # Show samples
    log("\n--- Sample duplicates ---")
    for pair in to_delete[:5]:
        b = pair['broken']
        c = pair['matching_correct']
        log(f"  DELETE: '{b['title']}' ({b['game_date']})")
        log(f"  KEEP:   '{c['title']}' ({c['game_date']})")
        log("")

    if args.dry_run:
        log(f"[DRY RUN] Would delete {len(to_delete)} broken duplicate entries.")
        return

    # Delete duplicates
    log(f"\nDeleting {len(to_delete)} broken duplicate entries...")
    deleted = 0
    errors = 0

    for pair in to_delete:
        page_id = pair['broken']['id']
        try:
            notion.pages.update(page_id=page_id, archived=True)
            deleted += 1
            if deleted % 25 == 0:
                log(f"  Progress: {deleted}/{len(to_delete)} deleted...")
            time.sleep(0.35)  # Rate limiting
        except Exception as e:
            log(f"  Error deleting {page_id}: {e}")
            errors += 1

    log(f"\n{'=' * 50}")
    log(f"CLEANUP COMPLETE")
    log(f"{'=' * 50}")
    log(f"Deleted: {deleted}")
    log(f"Errors: {errors}")
    log(f"Remaining games: ~{len(all_games) - deleted}")
    log(f"{'=' * 50}")


if __name__ == "__main__":
    main()
