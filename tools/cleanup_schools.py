#!/usr/bin/env python3
"""
Tool: cleanup_schools.py
Purpose: Clean up duplicate and junk entries in Schools database

What it does:
1. Merges duplicate schools (keeps best name, moves contacts/games)
2. Removes ranking prefixes (#1/1 Tufts → Tufts)
3. Deletes true junk entries (TBA, multi-school combos)
4. Preserves real schools with & in name (Johnson & Wales, William & Mary)

Usage:
    # Dry run - show what would change
    python tools/cleanup_schools.py --dry-run

    # Actually run the cleanup
    python tools/cleanup_schools.py --execute
"""

import argparse
import os
import re
import sys
from collections import defaultdict

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


def normalize_name(name):
    """Normalize school name for duplicate detection."""
    # Remove rankings like #1/1
    name = re.sub(r'^#\d+/\d+\s*', '', name)
    # Lowercase
    name = name.lower().strip()
    # Remove common suffixes for comparison
    name = re.sub(r'\s+(university|college|u\.|univ\.?)\s*$', '', name)
    name = re.sub(r'^(university|college)\s+of\s+', '', name)
    return name.strip()


# Schools that should NEVER be merged even if they normalize similarly
# Key = normalized name, Value = list of full names that are distinct schools
DISTINCT_SCHOOLS = {
    'boston': ['Boston College', 'Boston University'],
    'connecticut': ['University of Connecticut', 'Connecticut College'],
    'rhode island': ['University of Rhode Island', 'Rhode Island College'],
    'new york': ['New York University', 'City College of New York'],
    'washington': ['Washington University', 'University of Washington', 'Washington College'],
    'miami': ['University of Miami', 'Miami University'],
    'st. joseph': ['Saint Joseph\'s University', 'University of Saint Joseph'],
    'saint joseph': ['Saint Joseph\'s University', 'University of Saint Joseph'],
}


def should_skip_merge(norm_name, entries):
    """Check if this normalized group contains distinct schools that shouldn't merge."""
    if norm_name not in DISTINCT_SCHOOLS:
        return False

    # Get the actual school names in this group
    actual_names = [e['name'] for e in entries]

    # Check if multiple distinct schools are in this group
    distinct_found = []
    for distinct in DISTINCT_SCHOOLS[norm_name]:
        for actual in actual_names:
            if distinct.lower() in actual.lower() or actual.lower() in distinct.lower():
                if distinct not in distinct_found:
                    distinct_found.append(distinct)
                break

    # If we found 2+ distinct schools, don't merge
    return len(distinct_found) > 1


def pick_canonical_name(names):
    """Pick the best canonical name from a list of duplicates."""
    # Prefer names with 'University' or 'College'
    for name in names:
        if 'University' in name or 'College' in name:
            # But not if it has rankings
            if not name.startswith('#'):
                return name

    # Otherwise pick the longest one (usually most complete)
    return max(names, key=len)


def is_junk_entry(name):
    """Check if this is a junk entry that should be deleted."""
    # True junk patterns (multi-school combos, TBA)
    junk_patterns = [
        r',\s*\w+.*College',  # "School A, School B College"
        r'&\s*\w+.*College',  # But not "Johnson & Wales"
        r'&\s*\w+.*University',
        r'\bTBA\b',
        r'\bTBD\b',
        r'Winner of',
        r'Loser of',
    ]

    # Known real schools with & that should NOT be deleted
    real_schools = [
        'Johnson & Wales',
        'William & Mary',
        'Texas A&M',
        'Bryant & Stratton',
    ]

    # Check if it's a known real school
    for real in real_schools:
        if real.lower() in name.lower():
            return False

    # Check junk patterns
    for pattern in junk_patterns:
        if re.search(pattern, name, re.IGNORECASE):
            # Make sure it's truly a combo (has multiple schools)
            if ', ' in name and any(x in name for x in ['College', 'University', 'Academy']):
                return True

    # TBA/TBD entries
    if name.strip().upper() in ['TBA', 'TBD']:
        return True

    return False


def needs_ranking_cleanup(name):
    """Check if name has a ranking prefix that should be removed."""
    return bool(re.match(r'^#\d+/\d+\s+', name))


def clean_ranking(name):
    """Remove ranking prefix from name."""
    return re.sub(r'^#\d+/\d+\s*', '', name)


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


def update_relations(notion, db_id, property_name, old_id, new_id, dry_run=True):
    """Update all relations pointing to old_id to point to new_id."""
    try:
        response = notion.databases.query(
            database_id=db_id,
            filter={
                "property": property_name,
                "relation": {"contains": old_id}
            }
        )

        count = 0
        for page in response['results']:
            count += 1
            if not dry_run:
                # Get current relations
                current = page['properties'].get(property_name, {}).get('relation', [])
                # Replace old_id with new_id
                new_relations = []
                for rel in current:
                    if rel['id'] == old_id:
                        new_relations.append({'id': new_id})
                    else:
                        new_relations.append(rel)

                notion.pages.update(
                    page_id=page['id'],
                    properties={
                        property_name: {'relation': new_relations}
                    }
                )

        return count
    except APIResponseError:
        return 0


def run_cleanup(dry_run=True):
    """Run the school cleanup."""
    notion = get_notion_client()

    schools_db = os.getenv('NOTION_SCHOOLS_DB')
    contacts_db = os.getenv('NOTION_CONTACTS_DB')
    games_db = os.getenv('NOTION_GAMES_DB')

    if not schools_db:
        print("Error: NOTION_SCHOOLS_DB not set", file=sys.stderr)
        sys.exit(1)

    print(f"{'[DRY RUN] ' if dry_run else ''}Starting school cleanup...")
    print("=" * 60)

    schools = get_all_schools(notion, schools_db)
    print(f"Total schools: {len(schools)}")

    # Group by normalized name for duplicate detection
    by_normalized = defaultdict(list)
    for school in schools:
        props = school['properties']
        name = extract_title(props.get('School Name', {}).get('title', []))
        norm = normalize_name(name)
        by_normalized[norm].append({
            'id': school['id'],
            'name': name,
            'page': school
        })

    # Stats
    stats = {
        'duplicates_merged': 0,
        'rankings_cleaned': 0,
        'junk_deleted': 0,
        'contacts_moved': 0,
        'games_updated': 0
    }

    # 1. Handle duplicates
    print("\n--- DUPLICATE MERGING ---")
    for norm, entries in by_normalized.items():
        if len(entries) > 1:
            # Skip if this group contains distinct schools that shouldn't merge
            if should_skip_merge(norm, entries):
                names = [e['name'] for e in entries]
                print(f"\nSKIPPING '{norm}' - contains distinct schools: {names}")
                continue

            names = [e['name'] for e in entries]
            canonical = pick_canonical_name(names)
            canonical_entry = next(e for e in entries if e['name'] == canonical)

            print(f"\nMerging '{norm}' → '{canonical}'")
            for entry in entries:
                if entry['id'] != canonical_entry['id']:
                    print(f"  - Removing: {entry['name']}")

                    # Move contacts
                    if contacts_db:
                        moved = update_relations(
                            notion, contacts_db, 'School',
                            entry['id'], canonical_entry['id'],
                            dry_run=dry_run
                        )
                        if moved:
                            print(f"    Moved {moved} contacts")
                            stats['contacts_moved'] += moved

                    # Update games (Home Team)
                    if games_db:
                        moved = update_relations(
                            notion, games_db, 'Home Team',
                            entry['id'], canonical_entry['id'],
                            dry_run=dry_run
                        )
                        if moved:
                            print(f"    Updated {moved} home games")
                            stats['games_updated'] += moved

                        # Update games (Away Team)
                        moved = update_relations(
                            notion, games_db, 'Away Team',
                            entry['id'], canonical_entry['id'],
                            dry_run=dry_run
                        )
                        if moved:
                            print(f"    Updated {moved} away games")
                            stats['games_updated'] += moved

                    # Delete duplicate
                    if not dry_run:
                        try:
                            notion.pages.update(
                                page_id=entry['id'],
                                archived=True
                            )
                        except APIResponseError as e:
                            print(f"    Warning: Could not delete: {e}")

                    stats['duplicates_merged'] += 1

    # 2. Clean ranking prefixes
    print("\n--- RANKING CLEANUP ---")
    for school in schools:
        props = school['properties']
        name = extract_title(props.get('School Name', {}).get('title', []))

        if needs_ranking_cleanup(name):
            clean_name = clean_ranking(name)
            print(f"  {name} → {clean_name}")

            if not dry_run:
                try:
                    notion.pages.update(
                        page_id=school['id'],
                        properties={
                            'School Name': {'title': [{'text': {'content': clean_name}}]}
                        }
                    )
                except APIResponseError as e:
                    print(f"    Warning: Could not update: {e}")

            stats['rankings_cleaned'] += 1

    # 3. Delete junk entries
    print("\n--- JUNK DELETION ---")
    for school in schools:
        props = school['properties']
        name = extract_title(props.get('School Name', {}).get('title', []))

        if is_junk_entry(name):
            print(f"  Deleting: {name}")

            if not dry_run:
                try:
                    notion.pages.update(
                        page_id=school['id'],
                        archived=True
                    )
                except APIResponseError as e:
                    print(f"    Warning: Could not delete: {e}")

            stats['junk_deleted'] += 1

    # Summary
    print("\n" + "=" * 60)
    print(f"{'[DRY RUN] ' if dry_run else ''}CLEANUP SUMMARY")
    print("=" * 60)
    print(f"Duplicates merged: {stats['duplicates_merged']}")
    print(f"Rankings cleaned: {stats['rankings_cleaned']}")
    print(f"Junk entries deleted: {stats['junk_deleted']}")
    print(f"Contacts moved: {stats['contacts_moved']}")
    print(f"Games updated: {stats['games_updated']}")

    if dry_run:
        print("\nThis was a dry run. Run with --execute to make changes.")


def main():
    parser = argparse.ArgumentParser(description="Clean up Schools database")
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
