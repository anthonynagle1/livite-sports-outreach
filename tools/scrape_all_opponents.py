#!/usr/bin/env python3
"""
Tool: scrape_all_opponents.py
Purpose: Automatically discover and scrape staff contacts for all opponents

Takes a list of opponents (school + sport), discovers their athletics URLs,
scrapes staff contacts, and caches the results.

Usage:
    python tools/scrape_all_opponents.py \
        --opponents-file .tmp/opponents_to_scrape.json \
        --cache-dir .tmp/cache/contacts/

Output: Cached staff data for each opponent
"""

import argparse
import json
import sys
import subprocess
import os
from pathlib import Path


def normalize_school_name(school_name):
    """Normalize school name for cache file naming."""
    return school_name.lower().replace(' ', '_').replace("'", '')


def normalize_sport_name(sport_name):
    """Normalize sport name for cache file naming."""
    return sport_name.lower().replace(' ', '_').replace('&', 'and')


def normalize_gender(gender):
    """Normalize gender for cache file naming."""
    if not gender or gender == 'Unknown':
        return ''
    return gender.lower()


def discover_url(school_name):
    """
    Discover athletics URL for a school.

    Args:
        school_name (str): School name

    Returns:
        str: Athletics URL or None
    """
    try:
        result = subprocess.run(
            ['python3', 'tools/discover_opponent_url.py', '--school', school_name],
            capture_output=True,
            text=True,
            check=True
        )
        data = json.loads(result.stdout)
        if data.get('success'):
            return data.get('athletics_url')
        return None
    except Exception as e:
        print(f"  ✗ Error discovering URL for {school_name}: {e}", file=sys.stderr)
        return None


def scrape_team_staff(school_url, sport, gender, school_name, cache_dir):
    """
    Scrape team staff and cache the results.

    Args:
        school_url (str): Athletics website URL
        sport (str): Sport name
        gender (str): Gender (Men, Women, or Unknown)
        school_name (str): School name
        cache_dir (str): Cache directory path

    Returns:
        bool: True if successful
    """
    # Construct cache file path (include gender to avoid collisions)
    normalized_school = normalize_school_name(school_name)
    normalized_sport = normalize_sport_name(sport)
    normalized_gender = normalize_gender(gender)

    if normalized_gender:
        cache_file = os.path.join(cache_dir, f"{normalized_school}_{normalized_gender}_{normalized_sport}.json")
    else:
        cache_file = os.path.join(cache_dir, f"{normalized_school}_{normalized_sport}.json")

    # Construct team-specific URL with gender awareness
    sport_lower = sport.lower()

    # Build sport path based on gender + sport
    if gender == 'Men':
        gender_prefix = 'mens'
    elif gender == 'Women':
        gender_prefix = 'womens'
    else:
        # Unknown gender - use defaults
        if sport_lower in ['baseball', 'football']:
            gender_prefix = 'mens'  # Traditionally men's sports
        elif sport_lower in ['softball', 'field hockey', 'volleyball']:
            gender_prefix = 'womens'  # Traditionally women's sports
        else:
            gender_prefix = 'mens'  # Default to men's

    # Construct URL path
    sport_path_base = sport_lower.replace(' ', '-').replace('&', '')

    # Special cases for sports with non-standard URL patterns
    if sport_lower == 'ice hockey':
        sport_path = f"{gender_prefix}-ice-hockey"
    elif sport_lower == 'basketball':
        sport_path = f"{gender_prefix}-basketball"
    elif sport_lower == 'soccer':
        sport_path = f"{gender_prefix}-soccer"
    elif sport_lower == 'lacrosse':
        sport_path = f"{gender_prefix}-lacrosse"
    elif sport_lower in ['baseball', 'softball', 'football']:
        # These don't typically have gender prefix
        sport_path = sport_path_base
    else:
        # General pattern
        sport_path = f"{gender_prefix}-{sport_path_base}"

    team_url = f"{school_url}/sports/{sport_path}"

    try:
        result = subprocess.run(
            [
                'python3', 'tools/scrape_team_staff.py',
                '--team-url', team_url,
                '--sport', sport,
                '--school', school_name,
                '--output', cache_file
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=120  # 2 minute timeout per scrape
        )

        # Parse stdout to check success
        data = json.loads(result.stdout)
        if data.get('success'):
            staff_count = data.get('total_staff', 0)
            print(f"    ✓ Scraped {staff_count} staff for {school_name} {sport}", file=sys.stderr)
            return True
        else:
            print(f"    ✗ Failed to scrape {school_name} {sport}", file=sys.stderr)
            return False

    except subprocess.TimeoutExpired:
        print(f"    ✗ Timeout scraping {school_name} {sport}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"    ✗ Error scraping {school_name} {sport}: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Scrape all opponent staff contacts"
    )
    parser.add_argument(
        "--opponents-file",
        required=True,
        help="JSON file with list of opponents to scrape"
    )
    parser.add_argument(
        "--cache-dir",
        default=".tmp/cache/contacts/",
        help="Cache directory for staff data"
    )

    args = parser.parse_args()

    # Create cache directory if needed
    Path(args.cache_dir).mkdir(parents=True, exist_ok=True)

    # Load opponents list
    try:
        with open(args.opponents_file, 'r') as f:
            opponents = json.load(f)
    except Exception as e:
        print(f"Error loading opponents file: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(opponents, list):
        print("Opponents file must contain a JSON array", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Scraping {len(opponents)} opponent schools...", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    successful = 0
    failed = 0
    skipped = 0

    for i, opponent in enumerate(opponents, 1):
        school = opponent.get('school', 'Unknown')
        sport = opponent.get('sport', 'Unknown')
        gender = opponent.get('gender', 'Unknown')

        gender_display = f" {gender}'s" if gender != 'Unknown' else ''
        print(f"[{i}/{len(opponents)}] {school} ({gender_display} {sport})", file=sys.stderr)

        # Check if already cached
        normalized_school = normalize_school_name(school)
        normalized_sport = normalize_sport_name(sport)
        normalized_gender = normalize_gender(gender)

        if normalized_gender:
            cache_file = os.path.join(args.cache_dir, f"{normalized_school}_{normalized_gender}_{normalized_sport}.json")
        else:
            cache_file = os.path.join(args.cache_dir, f"{normalized_school}_{normalized_sport}.json")

        if os.path.exists(cache_file):
            print(f"  ⚠ Already cached - skipping", file=sys.stderr)
            skipped += 1
            continue

        # Discover URL
        print(f"  → Discovering athletics URL...", file=sys.stderr)
        url = discover_url(school)

        if not url:
            print(f"  ✗ Could not find athletics URL", file=sys.stderr)
            failed += 1
            continue

        print(f"  → Found: {url}", file=sys.stderr)

        # Scrape staff
        print(f"  → Scraping{gender_display} {sport} staff...", file=sys.stderr)
        if scrape_team_staff(url, sport, gender, school, args.cache_dir):
            successful += 1
        else:
            failed += 1

        print()  # Blank line between schools

    # Summary
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"SCRAPING COMPLETE", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"Successful: {successful}", file=sys.stderr)
    print(f"Skipped (cached): {skipped}", file=sys.stderr)
    print(f"Failed: {failed}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    # Output JSON result
    result = {
        'total_opponents': len(opponents),
        'successful': successful,
        'skipped': skipped,
        'failed': failed,
        'success': True
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
