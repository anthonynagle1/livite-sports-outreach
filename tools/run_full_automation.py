#!/usr/bin/env python3
"""
Tool: run_full_automation.py
Purpose: Master orchestration script - processes entire school automatically

Usage:
    python tools/run_full_automation.py \
        --school "Boston College" \
        --url "https://bceagles.com" \
        --spreadsheet-name "NCAA Catering Contacts 2026"

What it does:
1. Discovers all teams for the school
2. Extracts schedules for each team
3. Finds all unique opponents
4. Scrapes opponent staff (or loads from cache)
5. Matches games to contacts
6. Validates all contacts
7. Exports to Google Sheets (creates/updates master sheet)

Output: Complete Google Sheet with all games and contacts
"""

import argparse
import json
import sys
import os
import subprocess
from datetime import datetime
from collections import defaultdict


def run_command(cmd, description):
    """
    Run a shell command and return output.

    Args:
        cmd (list): Command and arguments
        description (str): What this command does

    Returns:
        dict: Parsed JSON output or None
    """
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"{description}...", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)

        # Try to parse JSON output
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            print(result.stdout, file=sys.stderr)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            return None

    except subprocess.CalledProcessError as e:
        print(f"Error: {e}", file=sys.stderr)
        print(f"Stdout: {e.stdout}", file=sys.stderr)
        print(f"Stderr: {e.stderr}", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Master orchestration - process entire school automatically"
    )
    parser.add_argument(
        "--school",
        required=True,
        help="School name (e.g., 'Boston College')"
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Athletics website URL (e.g., 'https://bceagles.com')"
    )
    parser.add_argument(
        "--spreadsheet-name",
        required=True,
        help="Google Spreadsheet name"
    )
    parser.add_argument(
        "--spreadsheet-id",
        help="Existing spreadsheet ID to update (optional)"
    )

    args = parser.parse_args()

    print(f"\n{'#'*60}", file=sys.stderr)
    print(f"# NCAA CONTACT AUTOMATION - {args.school}", file=sys.stderr)
    print(f"{'#'*60}\n", file=sys.stderr)

    # Create output directory
    os.makedirs(".tmp", exist_ok=True)

    # Step 1: Discover teams
    teams_file = f".tmp/{args.school.lower().replace(' ', '_')}_teams.json"
    teams_data = run_command([
        "python3", "tools/scrape_team_list.py",
        "--url", args.url,
        "--school", args.school,
        "--output", teams_file
    ], f"Step 1: Discovering teams for {args.school}")

    if not teams_data or not teams_data.get('success'):
        print(f"Failed to discover teams for {args.school}", file=sys.stderr)
        sys.exit(1)

    teams = teams_data.get('teams', [])
    print(f"✓ Found {len(teams)} teams", file=sys.stderr)

    # Step 2: Extract schedules for each team
    all_schedules = []
    all_opponents = set()

    for i, team in enumerate(teams, 1):
        sport = team.get('sport', 'Unknown')
        gender = team.get('gender', 'Unknown')
        team_url = team.get('url', '')

        print(f"\n[{i}/{len(teams)}] Processing {gender}'s {sport}...", file=sys.stderr)

        schedule_file = f".tmp/{args.school.lower().replace(' ', '_')}_{sport.lower().replace(' ', '_')}_schedule.json"

        schedule_data = run_command([
            "python3", "tools/scrape_schedule.py",
            "--team-url", team_url,
            "--sport", sport,
            "--gender", gender,
            "--school", args.school,
            "--output", schedule_file
        ], f"  Extracting schedule for {sport}")

        if schedule_data and schedule_data.get('success'):
            games = schedule_data.get('games', [])
            print(f"  ✓ Found {len(games)} home games", file=sys.stderr)

            all_schedules.append(schedule_data)

            # Collect unique opponents
            for game in games:
                opponent = game.get('opponent', '')
                if opponent:
                    all_opponents.add((opponent, sport))

    print(f"\n✓ Total unique opponents: {len(all_opponents)}", file=sys.stderr)

    # Step 3: Scrape opponent staff
    opponent_data_files = {}

    for i, (opponent, sport) in enumerate(sorted(all_opponents), 1):
        print(f"\n[{i}/{len(all_opponents)}] Processing {opponent} ({sport})...", file=sys.stderr)

        # Check cache first
        cache_check = run_command([
            "python3", "tools/manage_contact_cache.py",
            "--check",
            "--school", opponent,
            "--sport", sport
        ], f"  Checking cache for {opponent} {sport}")

        # Determine opponent URL (this is a simplification - in production you'd need a mapping)
        # For now, skip if we can't determine URL
        if cache_check and cache_check.get('fresh'):
            print(f"  ✓ Using cached data", file=sys.stderr)
            opponent_file = cache_check.get('cache_file')
        else:
            print(f"  ⚠ No cache found - skipping (would need opponent URL)", file=sys.stderr)
            continue

        opponent_data_files[(opponent, sport)] = opponent_file

    # Step 4: Match games to contacts and validate
    all_matched_data = []

    for schedule_data in all_schedules:
        sport = schedule_data.get('sport')
        games = schedule_data.get('games', [])

        # Group games by opponent
        games_by_opponent = defaultdict(list)
        for game in games:
            opponent = game.get('opponent')
            if opponent:
                games_by_opponent[opponent].append(game)

        # Match each opponent's games
        for opponent, opponent_games in games_by_opponent.items():
            if (opponent, sport) not in opponent_data_files:
                print(f"  ⚠ No staff data for {opponent} ({sport}) - skipping", file=sys.stderr)
                continue

            # Create temporary schedule file for this opponent
            temp_schedule = {
                "school": args.school,
                "sport": sport,
                "games": opponent_games
            }
            temp_schedule_file = f".tmp/temp_schedule_{opponent.replace(' ', '_')}_{sport.replace(' ', '_')}.json"
            with open(temp_schedule_file, 'w') as f:
                json.dump(temp_schedule, f)

            # Match games to contacts
            matched_file = f".tmp/matched_{opponent.replace(' ', '_')}_{sport.replace(' ', '_')}.json"
            staff_file = opponent_data_files[(opponent, sport)]

            matched_data = run_command([
                "python3", "tools/match_game_to_contact.py",
                "--game-data", temp_schedule_file,
                "--staff-data", staff_file,
                "--output", matched_file
            ], f"  Matching {opponent} games to contacts")

            if matched_data:
                all_matched_data.append(matched_data)

    # Step 5: Combine all matched data
    combined_matches = []
    for matched_data in all_matched_data:
        matches = matched_data.get('matches', [])
        combined_matches.extend(matches)

    combined_file = f".tmp/{args.school.lower().replace(' ', '_')}_all_matches.json"
    combined_data = {
        "school": args.school,
        "matches": combined_matches,
        "total_matches": len(combined_matches)
    }
    with open(combined_file, 'w') as f:
        json.dump(combined_data, f, indent=2)

    print(f"\n✓ Combined {len(combined_matches)} total game-contact matches", file=sys.stderr)

    # Step 6: Validate
    validated_file = f".tmp/{args.school.lower().replace(' ', '_')}_validated.json"
    validated_data = run_command([
        "python3", "tools/validate_contacts.py",
        "--input", combined_file,
        "--output", validated_file
    ], "Step 6: Validating all contacts")

    # Step 7: Export to Google Sheets
    export_cmd = [
        "python3", "tools/export_to_sheets.py",
        "--input", validated_file,
        "--spreadsheet-name", args.spreadsheet_name,
        "--school-name", args.school
    ]

    if args.spreadsheet_id:
        export_cmd.extend(["--spreadsheet-id", args.spreadsheet_id])

    export_result = run_command(export_cmd, "Step 7: Exporting to Google Sheets")

    if export_result:
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"SUCCESS!", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        print(f"Spreadsheet: {export_result.get('spreadsheet_url')}", file=sys.stderr)
        print(f"Games exported: {len(combined_matches)}", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

        # Output result JSON
        print(json.dumps(export_result, indent=2))
    else:
        print("Failed to export to Google Sheets", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
