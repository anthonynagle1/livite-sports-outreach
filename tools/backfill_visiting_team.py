#!/usr/bin/env python3
"""
Tool: backfill_visiting_team.py
Purpose: Populate the "Visiting Team" field on all existing games.

Visiting Team = "{School Name} {Gender}'s {Sport}" (e.g. "Holy Cross Women's Basketball")
If gender is missing/Unknown, uses "{School Name} {Sport}"

Usage:
    python tools/backfill_visiting_team.py [--dry-run]
"""

import os
import signal
import sys
import time
from collections import defaultdict

try:
    from notion_client import Client
    from notion_client.errors import APIResponseError
except ImportError:
    print("Error: notion-client not installed. Run: pip install notion-client", file=sys.stderr)
    sys.exit(1)

from dotenv import load_dotenv
load_dotenv()

MAX_RETRIES = 3
SCRIPT_TIMEOUT = 900  # 15 minutes max runtime


def timeout_handler(signum, frame):
    print("\n\nScript timed out after {} seconds. Exiting.".format(SCRIPT_TIMEOUT), file=sys.stderr)
    sys.exit(1)


def with_retry(fn, label="API call"):
    """Retry a Notion API call up to MAX_RETRIES times with backoff."""
    for attempt in range(MAX_RETRIES):
        try:
            return fn()
        except APIResponseError as e:
            if e.status == 429 and attempt < MAX_RETRIES - 1:
                wait = 2 ** (attempt + 1)
                print("  Rate limited on {}, waiting {}s...".format(label, wait))
                time.sleep(wait)
            elif attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                print("  Retry {}/{} for {}: {}".format(attempt + 1, MAX_RETRIES, label, e))
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                print("  Retry {}/{} for {}: {}".format(attempt + 1, MAX_RETRIES, label, e))
                time.sleep(wait)
            else:
                raise


def get_school_name(notion, school_id, cache):
    """Resolve school page ID to name, with caching."""
    if school_id in cache:
        return cache[school_id]
    try:
        page = with_retry(lambda: notion.pages.retrieve(page_id=school_id), "get school")
        title_prop = page['properties'].get('School Name', {}).get('title', [])
        name = title_prop[0]['plain_text'] if title_prop else 'Unknown'
        cache[school_id] = name
        return name
    except Exception as e:
        print("  Warning: Could not resolve school {}: {}".format(school_id[:8], e))
        cache[school_id] = 'Unknown'
        return 'Unknown'


def build_visiting_team(school_name, gender, sport):
    """Build the Visiting Team string."""
    if gender and gender not in ('Unknown', ''):
        return "{} {}'s {}".format(school_name, gender, sport)
    return "{} {}".format(school_name, sport)


def main():
    dry_run = '--dry-run' in sys.argv

    # Set overall script timeout to prevent hanging forever
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(SCRIPT_TIMEOUT)

    api_key = os.getenv('NOTION_API_KEY')
    games_db = os.getenv('NOTION_GAMES_DB')
    if not api_key or not games_db:
        print("Error: NOTION_API_KEY and NOTION_GAMES_DB must be set", file=sys.stderr)
        sys.exit(1)

    notion = Client(auth=api_key, timeout_ms=15000)  # 15s timeout per request
    school_cache = {}
    stats = defaultdict(int)

    print("Querying all games...")
    all_games = []
    has_more = True
    start_cursor = None

    while has_more:
        query_kwargs = {"database_id": games_db}
        if start_cursor:
            query_kwargs["start_cursor"] = start_cursor
        # Capture kwargs by value to avoid closure issues
        frozen_kwargs = dict(query_kwargs)
        response = with_retry(lambda: notion.databases.query(**frozen_kwargs), "query games")
        all_games.extend(response['results'])
        has_more = response.get('has_more', False)
        start_cursor = response.get('next_cursor')
        print("  Fetched {} games so far...".format(len(all_games)))

    total = len(all_games)
    needs_update = 0
    for game in all_games:
        existing = game['properties'].get('Visiting Team', {}).get('rich_text', [])
        if not (existing and existing[0].get('plain_text', '')):
            needs_update += 1
    print("Found {} games total ({} need Visiting Team)".format(total, needs_update))

    if needs_update == 0:
        print("Nothing to update!")
        return

    for i, game in enumerate(all_games):
        props = game['properties']
        game_id_prop = props.get('Game ID', {}).get('title', [])
        game_id = game_id_prop[0]['plain_text'] if game_id_prop else '?'

        # Check if already populated
        existing = props.get('Visiting Team', {}).get('rich_text', [])
        existing_text = existing[0]['plain_text'] if existing else ''
        if existing_text:
            stats['already_set'] += 1
            continue

        # Get Away Team school name
        away_rel = props.get('Away Team', {}).get('relation', [])
        if not away_rel:
            stats['no_away_team'] += 1
            continue
        school_name = get_school_name(notion, away_rel[0]['id'], school_cache)

        # Get Gender and Sport
        gender_select = props.get('Gender', {}).get('select')
        gender = gender_select['name'] if gender_select else ''
        sport_select = props.get('Sport', {}).get('select')
        sport = sport_select['name'] if sport_select else ''

        if not sport:
            stats['no_sport'] += 1
            continue

        visiting_team = build_visiting_team(school_name, gender, sport)

        if dry_run:
            print("  [DRY RUN] {} -> {}".format(game_id, visiting_team))
            stats['would_update'] += 1
        else:
            try:
                # Capture by value for lambda
                pid = game['id']
                vt = visiting_team
                with_retry(
                    lambda: notion.pages.update(
                        page_id=pid,
                        properties={
                            "Visiting Team": {
                                "rich_text": [{"text": {"content": vt}}]
                            }
                        }
                    ),
                    "update {}".format(game_id)
                )
                stats['updated'] += 1
                print("  [{}/{}] {} -> {}".format(
                    stats['updated'], needs_update, game_id, visiting_team))
                # Rate limit: Notion allows ~3 requests/sec
                time.sleep(0.35)
            except Exception as e:
                print("  ERROR on {}: {} (skipping)".format(game_id, e), file=sys.stderr)
                stats['errors'] += 1

    # Cancel the alarm
    signal.alarm(0)

    print("\nDone!")
    for key, val in sorted(stats.items()):
        print("  {}: {}".format(key, val))


if __name__ == '__main__':
    main()
