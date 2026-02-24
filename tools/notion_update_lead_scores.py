#!/usr/bin/env python3
"""
Tool: notion_update_lead_scores.py
Purpose: Calculate and update lead scores for games in Notion

Lead Score Algorithm (0-100):
- Base: 50 points
- Days Until Game: +5 to +20 (closer = higher)
- Past Orders: +25 if school has ordered before
- Contact Quality: +15 (Dir of Ops), +10 (any email), -20 (no contact)
- Response History: +20 (responded positively), -10 (declined)
- Sport Bonus: +10 (Football), +5 (Baseball/Softball)

Usage:
    # Update all games
    python tools/notion_update_lead_scores.py

    # Update specific sport only
    python tools/notion_update_lead_scores.py --sport Baseball

    # Dry run (calculate but don't update)
    python tools/notion_update_lead_scores.py --dry-run

    # Show top N leads
    python tools/notion_update_lead_scores.py --top 20

Required environment variables:
    NOTION_API_KEY - Notion integration token
    NOTION_GAMES_DB - Games database ID
    NOTION_SCHOOLS_DB - Schools database ID
    NOTION_CONTACTS_DB - Contacts database ID
    NOTION_ORDERS_DB - Orders database ID
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
    schools_db = os.getenv('NOTION_SCHOOLS_DB')
    contacts_db = os.getenv('NOTION_CONTACTS_DB')
    orders_db = os.getenv('NOTION_ORDERS_DB')

    if not games_db:
        print("Error: NOTION_GAMES_DB not set", file=sys.stderr)
        sys.exit(1)

    return games_db, schools_db, contacts_db, orders_db


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


def get_games_to_score(notion, games_db, sport_filter=None):
    """
    Get all games that need scoring.
    Filters to Not Contacted, Email Sent, and Responded status.
    """
    filter_conditions = {
        "or": [
            {"property": "Outreach Status", "select": {"equals": "Not Contacted"}},
            {"property": "Outreach Status", "select": {"equals": "Email Sent"}},
            {"property": "Outreach Status", "select": {"equals": "Responded"}}
        ]
    }

    if sport_filter:
        filter_conditions = {
            "and": [
                filter_conditions,
                {"property": "Sport", "select": {"equals": sport_filter}}
            ]
        }

    try:
        games = []
        response = notion.databases.query(
            database_id=games_db,
            filter=filter_conditions
        )
        games.extend(response['results'])

        while response.get('has_more'):
            response = notion.databases.query(
                database_id=games_db,
                filter=filter_conditions,
                start_cursor=response['next_cursor']
            )
            games.extend(response['results'])

        return games

    except APIResponseError as e:
        print(f"Error querying games: {e}", file=sys.stderr)
        return []


def get_schools_with_orders(notion, orders_db):
    """Get set of school IDs that have placed orders."""
    if not orders_db:
        return set()

    try:
        school_ids = set()
        response = notion.databases.query(database_id=orders_db)

        for order in response['results']:
            props = order['properties']
            if 'School' in props and props['School'].get('relation'):
                school_ids.add(props['School']['relation'][0]['id'])

        while response.get('has_more'):
            response = notion.databases.query(
                database_id=orders_db,
                start_cursor=response['next_cursor']
            )
            for order in response['results']:
                props = order['properties']
                if 'School' in props and props['School'].get('relation'):
                    school_ids.add(props['School']['relation'][0]['id'])

        return school_ids

    except APIResponseError as e:
        print(f"Warning: Could not fetch orders: {e}", file=sys.stderr)
        return set()


def get_declined_schools(notion, games_db):
    """Get set of school IDs that have declined."""
    try:
        school_ids = set()
        response = notion.databases.query(
            database_id=games_db,
            filter={
                "property": "Outreach Status",
                "select": {"equals": "Declined"}
            }
        )

        for game in response['results']:
            props = game['properties']
            if 'Away Team' in props and props['Away Team'].get('relation'):
                school_ids.add(props['Away Team']['relation'][0]['id'])

        return school_ids

    except APIResponseError as e:
        print(f"Warning: Could not fetch declined schools: {e}", file=sys.stderr)
        return set()


def get_responded_schools(notion, games_db):
    """Get set of school IDs that have responded positively."""
    try:
        school_ids = set()
        response = notion.databases.query(
            database_id=games_db,
            filter={
                "or": [
                    {"property": "Outreach Status", "select": {"equals": "Responded"}},
                    {"property": "Outreach Status", "select": {"equals": "Booked"}}
                ]
            }
        )

        for game in response['results']:
            props = game['properties']
            if 'Away Team' in props and props['Away Team'].get('relation'):
                school_ids.add(props['Away Team']['relation'][0]['id'])

        return school_ids

    except APIResponseError as e:
        print(f"Warning: Could not fetch responded schools: {e}", file=sys.stderr)
        return set()


def calculate_lead_score(game, schools_with_orders, declined_schools, responded_schools):
    """
    Calculate lead score for a game based on multiple factors.
    Returns (score, breakdown_text).
    """
    props = game['properties']
    breakdown = []

    # Base score
    score = 50
    breakdown.append("Base: 50")

    # 1. Days Until Game
    game_date = None
    if 'Game Date' in props and props['Game Date'].get('date'):
        date_str = props['Game Date']['date'].get('start', '')
        if date_str:
            try:
                game_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                if game_date.tzinfo:
                    game_date = game_date.replace(tzinfo=None)
            except:
                pass

    if game_date:
        days_until = (game_date - datetime.now()).days
        if days_until < 0:
            # Game already passed
            score -= 50
            breakdown.append(f"Past game: -50")
        elif days_until <= 7:
            score += 20
            breakdown.append(f"Within 7 days: +20")
        elif days_until <= 14:
            score += 15
            breakdown.append(f"Within 14 days: +15")
        elif days_until <= 30:
            score += 10
            breakdown.append(f"Within 30 days: +10")
        else:
            score += 5
            breakdown.append(f"30+ days: +5")
    else:
        breakdown.append("No date: +0")

    # 2. Past Orders (Away Team = the school we're reaching out to)
    away_school_id = None
    if 'Away Team' in props and props['Away Team'].get('relation'):
        away_school_id = props['Away Team']['relation'][0]['id']

    if away_school_id and away_school_id in schools_with_orders:
        score += 25
        breakdown.append("Past customer: +25")

    # 3. Contact Quality
    has_contact = 'Contact' in props and props['Contact'].get('relation')

    if has_contact:
        contact_id = props['Contact']['relation'][0]['id']
        # Check contact priority (would need to fetch contact to know title)
        # For now, just check if contact exists
        score += 10
        breakdown.append("Has contact: +10")
    else:
        score -= 20
        breakdown.append("No contact: -20")

    # 4. Response History
    if away_school_id:
        if away_school_id in responded_schools:
            score += 20
            breakdown.append("School responded before: +20")
        elif away_school_id in declined_schools:
            score -= 10
            breakdown.append("School declined before: -10")

    # 5. Sport Bonus
    sport = props.get('Sport', {}).get('select', {}).get('name', '')
    if sport.lower() == 'football':
        score += 10
        breakdown.append("Football: +10")
    elif sport.lower() in ('baseball', 'softball'):
        score += 5
        breakdown.append(f"{sport}: +5")

    # Cap score at 0-100
    score = max(0, min(100, score))

    return score, " | ".join(breakdown)


def update_game_lead_score(notion, game_id, score, breakdown, dry_run=False):
    """Update the Lead Score property on a game."""
    if dry_run:
        return True

    try:
        properties = {
            "Lead Score": {"number": score}
        }

        # Only add breakdown if rich_text property exists
        # Score Breakdown is optional
        try:
            properties["Score Breakdown"] = {
                "rich_text": [{"text": {"content": breakdown[:2000]}}]
            }
        except:
            pass

        notion.pages.update(
            page_id=game_id,
            properties=properties
        )
        return True

    except APIResponseError as e:
        # If Lead Score property doesn't exist, inform user
        if "Lead Score" in str(e):
            print(f"\nError: 'Lead Score' property not found in Games database.", file=sys.stderr)
            print("Please add a Number property called 'Lead Score' in Notion.", file=sys.stderr)
            return False
        print(f"  Error updating {game_id}: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Calculate and update lead scores for games"
    )
    parser.add_argument(
        "--sport",
        help="Only update games for this sport"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate scores but don't update Notion"
    )
    parser.add_argument(
        "--top",
        type=int,
        default=0,
        help="Show top N leads after scoring"
    )

    args = parser.parse_args()

    # Initialize
    notion = get_notion_client()
    games_db, schools_db, contacts_db, orders_db = get_database_ids()

    print("Loading reference data...", file=sys.stderr)

    # Get reference data for scoring
    schools_with_orders = get_schools_with_orders(notion, orders_db)
    print(f"  Schools with past orders: {len(schools_with_orders)}", file=sys.stderr)

    declined_schools = get_declined_schools(notion, games_db)
    print(f"  Schools that declined: {len(declined_schools)}", file=sys.stderr)

    responded_schools = get_responded_schools(notion, games_db)
    print(f"  Schools that responded: {len(responded_schools)}", file=sys.stderr)

    # Get games to score
    print(f"\nFetching games to score...", file=sys.stderr)
    games = get_games_to_score(notion, games_db, args.sport)
    print(f"Found {len(games)} games to score", file=sys.stderr)

    if not games:
        print("\nNo games to score.", file=sys.stderr)
        result = {"success": True, "games_scored": 0}
        print(json.dumps(result, indent=2))
        return

    # Calculate and update scores
    mode = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{mode}Calculating lead scores...", file=sys.stderr)

    scored_games = []
    errors = 0

    for i, game in enumerate(games, 1):
        game_id = game['id']
        props = game['properties']
        game_title = extract_title(props.get('Game ID', {}).get('title', []))

        score, breakdown = calculate_lead_score(
            game, schools_with_orders, declined_schools, responded_schools
        )

        scored_games.append({
            'id': game_id,
            'title': game_title,
            'score': score,
            'breakdown': breakdown
        })

        if not args.dry_run:
            success = update_game_lead_score(notion, game_id, score, breakdown)
            if not success:
                errors += 1
                if errors >= 3:
                    print("\nToo many errors. Please add 'Lead Score' property to Games database.", file=sys.stderr)
                    sys.exit(1)

        if i % 50 == 0:
            print(f"  Processed {i}/{len(games)}...", file=sys.stderr)

    # Sort by score descending
    scored_games.sort(key=lambda x: x['score'], reverse=True)

    # Print summary
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"LEAD SCORING COMPLETE {'(DRY RUN)' if args.dry_run else ''}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"Games scored: {len(scored_games)}", file=sys.stderr)
    print(f"Errors: {errors}", file=sys.stderr)

    # Score distribution
    hot = sum(1 for g in scored_games if g['score'] >= 80)
    warm = sum(1 for g in scored_games if 60 <= g['score'] < 80)
    cold = sum(1 for g in scored_games if g['score'] < 60)
    print(f"\nDistribution:", file=sys.stderr)
    print(f"  Hot (80+): {hot}", file=sys.stderr)
    print(f"  Warm (60-79): {warm}", file=sys.stderr)
    print(f"  Cold (<60): {cold}", file=sys.stderr)

    # Show top N if requested
    if args.top > 0:
        print(f"\nTop {args.top} Leads:", file=sys.stderr)
        for i, game in enumerate(scored_games[:args.top], 1):
            print(f"  {i}. [{game['score']}] {game['title'][:50]}", file=sys.stderr)
            print(f"      {game['breakdown']}", file=sys.stderr)

    print(f"{'='*60}\n", file=sys.stderr)

    # Output JSON result
    result = {
        "success": True,
        "games_scored": len(scored_games),
        "errors": errors,
        "distribution": {
            "hot": hot,
            "warm": warm,
            "cold": cold
        }
    }

    if args.top > 0:
        result["top_leads"] = scored_games[:args.top]

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
