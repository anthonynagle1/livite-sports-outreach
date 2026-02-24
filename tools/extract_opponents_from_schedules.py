#!/usr/bin/env python3
"""
Tool: extract_opponents_from_schedules.py
Purpose: Extract unique opponents from schedule files WITH gender information

This ensures we scrape the correct gender-specific coaching staff.

Usage:
    python tools/extract_opponents_from_schedules.py \
        --schedules-pattern ".tmp/boston_college_*_schedule.json" \
        --output opponents.json

Output: JSON array of unique opponents with school, sport, and gender
"""

import argparse
import json
import sys
import glob


def main():
    parser = argparse.ArgumentParser(
        description="Extract unique opponents from schedule files"
    )
    parser.add_argument(
        "--schedules-pattern",
        required=True,
        help="Glob pattern for schedule files (e.g., '.tmp/boston_college_*_schedule.json')"
    )
    parser.add_argument(
        "--output",
        help="Output JSON file path (default: stdout)"
    )

    args = parser.parse_args()

    # Find all schedule files
    schedule_files = glob.glob(args.schedules_pattern)
    print(f"Found {len(schedule_files)} schedule files", file=sys.stderr)

    # Extract opponents
    opponents_set = set()  # Use set to deduplicate
    opponents_list = []

    for schedule_file in schedule_files:
        try:
            with open(schedule_file, 'r') as f:
                data = json.load(f)

            if not data.get('success') or not data.get('games'):
                continue

            sport = data.get('sport', 'Unknown')
            gender = data.get('gender', 'Unknown')

            for game in data['games']:
                opponent = game.get('opponent', '').strip()
                if not opponent:
                    continue

                # Create unique key
                key = (opponent, sport, gender)
                if key not in opponents_set:
                    opponents_set.add(key)
                    opponents_list.append({
                        'school': opponent,
                        'sport': sport,
                        'gender': gender
                    })

        except Exception as e:
            print(f"Error reading {schedule_file}: {e}", file=sys.stderr)

    # Sort by school name
    opponents_list.sort(key=lambda x: (x['school'], x['sport'], x['gender']))

    print(f"\nExtracted {len(opponents_list)} unique opponents\n", file=sys.stderr)

    # Save to file if specified
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(opponents_list, f, indent=2)
        print(f"Results saved to {args.output}", file=sys.stderr)

    # Always output JSON to stdout for pipeline processing
    print(json.dumps(opponents_list, indent=2))


if __name__ == "__main__":
    main()
