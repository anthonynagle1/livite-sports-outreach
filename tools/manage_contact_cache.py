#!/usr/bin/env python3
"""
Tool: manage_contact_cache.py
Purpose: Manage cached team staff contact data

Cache structure: .tmp/cache/contacts/[school]_[sport].json
Cache duration: One academic year (Aug-July)

Usage:
    # Check if cache exists and is fresh
    python tools/manage_contact_cache.py \
        --check --school "Merrimack" --sport "Baseball"

    # Save staff data to cache
    python tools/manage_contact_cache.py \
        --save --input merrimack_baseball_staff.json

    # Load staff data from cache
    python tools/manage_contact_cache.py \
        --load --school "Merrimack" --sport "Baseball" --output staff.json

Output: Cache status or staff data
"""

import argparse
import json
import sys
import os
from datetime import datetime
from pathlib import Path


CACHE_DIR = ".tmp/cache/contacts"


def get_cache_filename(school, sport, gender=None):
    """
    Get cache filename for school+sport+gender.

    Args:
        school (str): School name
        sport (str): Sport name
        gender (str, optional): Gender (Men, Women, or None)

    Returns:
        str: Cache file path
    """
    # Normalize school and sport names for filename
    school_safe = school.lower().replace(' ', '_').replace("'", '')
    sport_safe = sport.lower().replace(' ', '_')

    # Include gender in filename if provided
    if gender and gender != 'Unknown':
        gender_safe = gender.lower()
        filename = f"{school_safe}_{gender_safe}_{sport_safe}.json"
    else:
        filename = f"{school_safe}_{sport_safe}.json"

    return os.path.join(CACHE_DIR, filename)


def get_current_academic_year():
    """
    Get current academic year start.

    Returns:
        int: Academic year start year (e.g., 2025 for 2025-2026 year)
    """
    now = datetime.now()

    # Academic year runs Aug-July
    # If currently Aug-Dec, academic year started this calendar year
    # If currently Jan-July, academic year started last calendar year
    if now.month >= 8:
        return now.year
    else:
        return now.year - 1


def is_cache_fresh(cache_file):
    """
    Check if cache file is from current academic year.

    Args:
        cache_file (str): Path to cache file

    Returns:
        bool: True if fresh, False if stale or missing
    """
    if not os.path.exists(cache_file):
        return False

    try:
        with open(cache_file, 'r') as f:
            data = json.load(f)

        # Check timestamp
        timestamp_str = data.get('timestamp', '')
        if not timestamp_str:
            return False

        # Parse timestamp
        timestamp = datetime.fromisoformat(timestamp_str.split('.')[0])  # Remove microseconds

        # Get academic year from timestamp
        if timestamp.month >= 8:
            cache_academic_year = timestamp.year
        else:
            cache_academic_year = timestamp.year - 1

        current_academic_year = get_current_academic_year()

        return cache_academic_year == current_academic_year

    except Exception as e:
        print(f"Error checking cache freshness: {e}", file=sys.stderr)
        return False


def check_cache(school, sport):
    """
    Check if cache exists and is fresh.

    Args:
        school (str): School name
        sport (str): Sport name

    Returns:
        dict: Cache status
    """
    cache_file = get_cache_filename(school, sport)
    exists = os.path.exists(cache_file)
    fresh = is_cache_fresh(cache_file) if exists else False

    return {
        "school": school,
        "sport": sport,
        "cache_file": cache_file,
        "exists": exists,
        "fresh": fresh,
        "status": "fresh" if fresh else ("stale" if exists else "missing"),
        "action_needed": "none" if fresh else "scrape",
    }


def save_to_cache(input_file):
    """
    Save staff data to cache.

    Args:
        input_file (str): Path to staff JSON file

    Returns:
        dict: Save status
    """
    try:
        # Load input data
        with open(input_file, 'r') as f:
            data = json.load(f)

        school = data.get('school', 'Unknown')
        sport = data.get('sport', 'Unknown')

        # Get cache file path
        cache_file = get_cache_filename(school, sport)

        # Ensure cache directory exists
        os.makedirs(CACHE_DIR, exist_ok=True)

        # Save to cache
        with open(cache_file, 'w') as f:
            json.dump(data, f, indent=2)

        return {
            "school": school,
            "sport": sport,
            "cache_file": cache_file,
            "saved": True,
            "staff_count": data.get('staff_found', 0),
        }

    except Exception as e:
        return {
            "error": f"Failed to save to cache: {str(e)}",
            "saved": False,
        }


def load_from_cache(school, sport):
    """
    Load staff data from cache.

    Args:
        school (str): School name
        sport (str): Sport name

    Returns:
        dict: Staff data or error
    """
    cache_file = get_cache_filename(school, sport)

    if not os.path.exists(cache_file):
        return {
            "error": f"Cache file not found: {cache_file}",
            "success": False,
        }

    try:
        with open(cache_file, 'r') as f:
            data = json.load(f)

        # Check freshness
        fresh = is_cache_fresh(cache_file)
        if not fresh:
            print(f"Warning: Cache is stale (from previous academic year)", file=sys.stderr)

        return {
            **data,
            "cache_fresh": fresh,
            "loaded_from_cache": True,
        }

    except Exception as e:
        return {
            "error": f"Failed to load from cache: {str(e)}",
            "success": False,
        }


def main():
    parser = argparse.ArgumentParser(
        description="Manage team staff contact cache"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if cache exists and is fresh"
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save staff data to cache"
    )
    parser.add_argument(
        "--load",
        action="store_true",
        help="Load staff data from cache"
    )
    parser.add_argument(
        "--school",
        help="School name (required for --check and --load)"
    )
    parser.add_argument(
        "--sport",
        help="Sport name (required for --check and --load)"
    )
    parser.add_argument(
        "--input",
        help="Input staff JSON file (required for --save)"
    )
    parser.add_argument(
        "--output",
        help="Output JSON file path (default: stdout)"
    )

    args = parser.parse_args()

    result = None

    if args.check:
        if not args.school or not args.sport:
            print("Error: --check requires --school and --sport", file=sys.stderr)
            sys.exit(1)
        result = check_cache(args.school, args.sport)

    elif args.save:
        if not args.input:
            print("Error: --save requires --input", file=sys.stderr)
            sys.exit(1)
        result = save_to_cache(args.input)

    elif args.load:
        if not args.school or not args.sport:
            print("Error: --load requires --school and --sport", file=sys.stderr)
            sys.exit(1)
        result = load_from_cache(args.school, args.sport)

    else:
        print("Error: Must specify one of --check, --save, or --load", file=sys.stderr)
        sys.exit(1)

    # Output results
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Results saved to {args.output}", file=sys.stderr)
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
