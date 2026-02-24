#!/usr/bin/env python3
"""
Tool: cleanup_schools_phase2.py
Purpose: Remove tournaments and fix bad URLs in Schools database

What it does:
1. Deletes tournament/championship entries (not real schools)
2. Clears incorrect Coaches URLs (team-specific or wrong domain)
3. Reports statistics

Usage:
    # Dry run - show what would change
    python tools/cleanup_schools_phase2.py --dry-run

    # Actually run the cleanup
    python tools/cleanup_schools_phase2.py --execute
"""

import argparse
import os
import re
import sys

try:
    from notion_client import Client
    from notion_client.errors import APIResponseError
except ImportError:
    print("Error: notion-client package not installed.", file=sys.stderr)
    sys.exit(1)

from dotenv import load_dotenv

load_dotenv()


def get_notion_client():
    api_key = os.getenv('NOTION_API_KEY')
    if not api_key:
        print("Error: NOTION_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    return Client(auth=api_key)


def extract_title(title_array):
    if not title_array:
        return ''
    return ''.join(item.get('plain_text', '') for item in title_array)


# Tournament patterns - these are NOT real schools
TOURNAMENT_PATTERNS = [
    r'tournament', r'championship', r'regional', r'semifinal', r'final',
    r'playoff', r'\bNCAA\b', r'\bNEC\b.*champion', r'\bGNAC\b.*champion',
    r'\bNEWMAC\b.*champion', r'round of', r'quarter', r'bracket',
    r'elite eight', r'sweet 16', r'final four', r'first four', r'first round',
    r'second round', r'ecac'
]


def is_tournament(name):
    """Check if this is a tournament/championship entry."""
    name_lower = name.lower()
    for pattern in TOURNAMENT_PATTERNS:
        if re.search(pattern, name_lower):
            return True
    return False


def is_bad_coaches_url(school_name, coaches_url):
    """
    Check if coaches URL is problematic:
    1. Points to a specific team (/sports/xyz/coaches)
    2. Domain doesn't match school name (wrong school's URL)
    """
    if not coaches_url:
        return False, None

    # Check for team-specific URL
    if '/sports/' in coaches_url:
        return True, "Team-specific URL (should be main athletics page)"

    # Known domain mismatches (wrong school's URL assigned)
    mismatches = {
        'notre dame': ['unhwildcats'],
        'connecticut college': ['uconnhuskies'],
        'umass dartmouth': ['dartmouthsports'],
        'rhode island college': ['gorhody'],  # gorhody is URI, not RIC
        'southern connecticut': ['uconnhuskies'],
        'eastern connecticut': ['uconnhuskies'],
        'western connecticut': ['uconnhuskies'],
        'central connecticut': ['uconnhuskies'],
        'maine farmington': ['unhwildcats'],
        'maine fort kent': ['jwuathletics'],
        'maine augusta': ['jwuathletics'],
        'southern me': ['unhwildcats'],
        'hartford': ['unhwildcats'],
        'saint joseph': ['goblackbears', 'unhwildcats'],
        'mitchell': ['mitathletics'],  # Mitchell College != MIT
    }

    school_lower = school_name.lower()
    for school_key, bad_domains in mismatches.items():
        if school_key in school_lower:
            for bad_domain in bad_domains:
                if bad_domain in coaches_url.lower():
                    return True, f"Wrong domain ({bad_domain} is not {school_name})"

    return False, None


def get_all_schools(notion, schools_db):
    """Get all schools from database."""
    schools = []
    response = notion.databases.query(database_id=schools_db, page_size=100)
    schools.extend(response['results'])

    while response.get('has_more'):
        response = notion.databases.query(
            database_id=schools_db,
            start_cursor=response['next_cursor'],
            page_size=100
        )
        schools.extend(response['results'])

    return schools


def run_cleanup(dry_run=True):
    """Run the phase 2 cleanup."""
    notion = get_notion_client()

    schools_db = os.getenv('NOTION_SCHOOLS_DB')
    if not schools_db:
        print("Error: NOTION_SCHOOLS_DB not set", file=sys.stderr)
        sys.exit(1)

    print(f"{'[DRY RUN] ' if dry_run else ''}Starting phase 2 school cleanup...")
    print("=" * 60)

    schools = get_all_schools(notion, schools_db)
    print(f"Total schools: {len(schools)}")

    stats = {
        'tournaments_deleted': 0,
        'urls_cleared': 0,
    }

    # 1. Delete tournament entries
    print("\n--- TOURNAMENT DELETION ---")
    for school in schools:
        props = school['properties']
        name = extract_title(props.get('School Name', {}).get('title', []))

        if is_tournament(name):
            print(f"  Deleting: {name}")
            stats['tournaments_deleted'] += 1

            if not dry_run:
                try:
                    notion.pages.update(
                        page_id=school['id'],
                        archived=True
                    )
                except APIResponseError as e:
                    print(f"    Warning: Could not delete: {e}")

    # 2. Clear bad Coaches URLs
    print("\n--- BAD URL CLEANUP ---")
    for school in schools:
        props = school['properties']
        name = extract_title(props.get('School Name', {}).get('title', []))
        coaches_url = props.get('Coaches URL', {}).get('url', '')

        # Skip already deleted tournaments
        if is_tournament(name):
            continue

        is_bad, reason = is_bad_coaches_url(name, coaches_url)
        if is_bad:
            print(f"  {name}")
            print(f"    URL: {coaches_url}")
            print(f"    Reason: {reason}")
            stats['urls_cleared'] += 1

            if not dry_run:
                try:
                    notion.pages.update(
                        page_id=school['id'],
                        properties={
                            'Coaches URL': {'url': None}
                        }
                    )
                except APIResponseError as e:
                    print(f"    Warning: Could not clear URL: {e}")

    # Summary
    print("\n" + "=" * 60)
    print(f"{'[DRY RUN] ' if dry_run else ''}CLEANUP SUMMARY")
    print("=" * 60)
    print(f"Tournaments deleted: {stats['tournaments_deleted']}")
    print(f"Bad URLs cleared: {stats['urls_cleared']}")

    if dry_run:
        print("\nThis was a dry run. Run with --execute to make changes.")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Clean up Schools database - Phase 2")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without making changes"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually execute the cleanup"
    )

    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        print("Must specify --dry-run or --execute")
        sys.exit(1)

    run_cleanup(dry_run=not args.execute)


if __name__ == "__main__":
    main()
