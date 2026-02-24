#!/usr/bin/env python3
"""
Tool: notion_local_dashboard.py
Purpose: Generate a summary of local team outreach activity

Shows upcoming games at local (Boston-area) venues grouped by home school,
with outreach status breakdowns and contact match rates.

Usage:
    python tools/notion_local_dashboard.py              # Console summary
    python tools/notion_local_dashboard.py --json       # JSON output

Required environment variables:
    NOTION_API_KEY - Notion integration token
    NOTION_GAMES_DB - Games database ID
    NOTION_SCHOOLS_DB - Schools database ID
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

from notion_client import Client
from notion_client.errors import APIResponseError
from dotenv import load_dotenv

load_dotenv()


def extract_title(title_array):
    if not title_array:
        return ''
    return ''.join(item.get('plain_text', '') for item in title_array)


def extract_text(rich_text_array):
    if not rich_text_array:
        return ''
    return ''.join(item.get('plain_text', '') for item in rich_text_array)


def get_local_games(notion, games_db):
    """Query all local games with Game Date >= today."""
    today = datetime.now().strftime('%Y-%m-%d')

    all_games = []
    has_more = True
    start_cursor = None

    while has_more:
        kwargs = {
            "database_id": games_db,
            "filter": {
                "and": [
                    {"property": "Local Game", "checkbox": {"equals": True}},
                    {"property": "Game Date", "date": {"on_or_after": today}},
                ]
            },
            "sorts": [{"property": "Game Date", "direction": "ascending"}],
        }
        if start_cursor:
            kwargs["start_cursor"] = start_cursor

        response = notion.databases.query(**kwargs)
        all_games.extend(response['results'])
        has_more = response.get('has_more', False)
        start_cursor = response.get('next_cursor')

    return all_games


def get_school_name(notion, school_id, cache):
    """Get school name by ID, with caching."""
    if school_id in cache:
        return cache[school_id]
    try:
        page = notion.pages.retrieve(page_id=school_id)
        name = extract_title(page['properties'].get('School Name', {}).get('title', []))
        cache[school_id] = name
        return name
    except APIResponseError:
        cache[school_id] = 'Unknown'
        return 'Unknown'


def build_dashboard(notion, games_db, schools_db):
    """Build the dashboard data structure."""
    games = get_local_games(notion, games_db)
    school_cache = {}

    # Group by home school
    by_school = defaultdict(lambda: {
        'games': [],
        'sports': set(),
        'statuses': defaultdict(int),
        'with_contact': 0,
        'without_contact': 0,
        'next_game': None,
    })

    for game in games:
        props = game['properties']

        # Home team
        home_rel = props.get('Home Team', {}).get('relation', [])
        if not home_rel:
            continue
        home_name = get_school_name(notion, home_rel[0]['id'], school_cache)

        entry = by_school[home_name]

        # Sport
        sport = props.get('Sport', {}).get('select', {}).get('name', '')
        if sport:
            entry['sports'].add(sport)

        # Outreach status
        status = props.get('Outreach Status', {}).get('select', {}).get('name', 'Not Contacted')
        entry['statuses'][status] += 1

        # Contact
        contact_rel = props.get('Contact', {}).get('relation', [])
        if contact_rel:
            entry['with_contact'] += 1
        else:
            entry['without_contact'] += 1

        # Game date
        game_date = props.get('Game Date', {}).get('date', {}).get('start', '')

        # Track next game
        if game_date and (entry['next_game'] is None or game_date < entry['next_game']):
            entry['next_game'] = game_date

        entry['games'].append({
            'game_id': extract_title(props.get('Game ID', {}).get('title', [])),
            'date': game_date,
            'sport': sport,
            'status': status,
            'has_contact': bool(contact_rel),
        })

    return dict(by_school), len(games)


def print_dashboard(by_school, total_games):
    """Print formatted console dashboard."""
    print(f"\n{'='*70}", file=sys.stderr)
    print(f"  LOCAL TEAMS DASHBOARD — {datetime.now().strftime('%b %d, %Y')}", file=sys.stderr)
    print(f"  {total_games} upcoming games at local venues", file=sys.stderr)
    print(f"{'='*70}", file=sys.stderr)

    # Sort by game count descending
    sorted_schools = sorted(by_school.items(), key=lambda x: len(x[1]['games']), reverse=True)

    for school, data in sorted_schools:
        total = len(data['games'])
        contact_rate = (data['with_contact'] / total * 100) if total else 0
        sports_str = ', '.join(sorted(data['sports']))
        next_game = data['next_game'] or 'N/A'

        print(f"\n  {school}", file=sys.stderr)
        print(f"  {'─'*50}", file=sys.stderr)
        print(f"  Games: {total}  |  Sports: {sports_str}", file=sys.stderr)
        print(f"  Next game: {next_game}  |  Contact rate: {contact_rate:.0f}%", file=sys.stderr)

        # Status breakdown
        statuses = data['statuses']
        status_parts = []
        for s in ['Not Contacted', 'Email Sent', 'Responded', 'Booked', 'Declined']:
            count = statuses.get(s, 0)
            if count > 0:
                status_parts.append(f"{s}: {count}")
        if status_parts:
            print(f"  Pipeline: {' | '.join(status_parts)}", file=sys.stderr)

    # Summary totals
    total_with_contact = sum(d['with_contact'] for d in by_school.values())
    total_without_contact = sum(d['without_contact'] for d in by_school.values())
    overall_rate = (total_with_contact / total_games * 100) if total_games else 0

    all_statuses = defaultdict(int)
    for d in by_school.values():
        for s, c in d['statuses'].items():
            all_statuses[s] += c

    print(f"\n{'='*70}", file=sys.stderr)
    print(f"  TOTALS", file=sys.stderr)
    print(f"  Schools: {len(by_school)}  |  Games: {total_games}  |  Contact rate: {overall_rate:.0f}%", file=sys.stderr)

    pipeline = []
    for s in ['Not Contacted', 'Email Sent', 'Responded', 'Booked', 'Declined']:
        c = all_statuses.get(s, 0)
        if c > 0:
            pipeline.append(f"{s}: {c}")
    if pipeline:
        print(f"  Pipeline: {' | '.join(pipeline)}", file=sys.stderr)

    print(f"{'='*70}\n", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Local teams dashboard")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of formatted console")
    args = parser.parse_args()

    notion = Client(auth=os.getenv('NOTION_API_KEY'))
    games_db = os.getenv('NOTION_GAMES_DB')
    schools_db = os.getenv('NOTION_SCHOOLS_DB')

    if not games_db or not schools_db:
        print("Error: NOTION_GAMES_DB and NOTION_SCHOOLS_DB must be set in .env", file=sys.stderr)
        sys.exit(1)

    by_school, total_games = build_dashboard(notion, games_db, schools_db)

    if args.json:
        # Convert sets to lists for JSON serialization
        output = {}
        for school, data in by_school.items():
            output[school] = {
                'game_count': len(data['games']),
                'sports': sorted(data['sports']),
                'next_game': data['next_game'],
                'contact_rate': round(data['with_contact'] / len(data['games']) * 100) if data['games'] else 0,
                'statuses': dict(data['statuses']),
                'games': data['games'],
            }
        print(json.dumps({"total_games": total_games, "schools": output}, indent=2))
    else:
        print_dashboard(by_school, total_games)


if __name__ == "__main__":
    main()
