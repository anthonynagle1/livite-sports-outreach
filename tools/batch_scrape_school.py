#!/usr/bin/env python3
"""
Batch scrape all teams for a school: teams → schedules → opponents → contacts → match
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

def run_command(cmd, desc):
    """Run a command and return JSON output."""
    print(f"\n{desc}...", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error: {result.stderr}", file=sys.stderr)
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"Failed to parse JSON output", file=sys.stderr)
        return None

def batch_scrape_school(school_name, athletics_url):
    """Scrape all data for a school."""

    # Step 1: Scrape team list
    teams_file = f".tmp/{school_name.lower().replace(' ', '_')}_teams.json"
    teams_data = run_command(
        ['python3', 'tools/scrape_team_list.py',
         '--url', athletics_url,
         '--school', school_name,
         '--output', teams_file],
        f"Scraping teams for {school_name}"
    )

    if not teams_data or not teams_data.get('success'):
        print(f"Failed to scrape teams", file=sys.stderr)
        return

    teams = teams_data.get('teams', [])
    print(f"Found {len(teams)} teams", file=sys.stderr)

    # Step 2: Scrape schedules for each team
    all_games = []

    for team in teams:
        sport = team.get('sport')
        gender = team.get('gender', 'Unknown')
        team_url = team.get('url')

        print(f"\nScraping {gender}'s {sport}...", file=sys.stderr)

        schedule_file = f".tmp/{school_name.lower().replace(' ', '_')}_{gender.lower()}_{sport.lower().replace(' ', '_')}_schedule.json"

        schedule_data = run_command(
            ['python3', 'tools/scrape_schedule.py',
             '--team-url', team_url,
             '--sport', sport,
             '--gender', gender,
             '--school', school_name,
             '--output', schedule_file],
            f"  Scraping schedule"
        )

        if schedule_data and schedule_data.get('success'):
            games = schedule_data.get('games', [])
            if games:
                print(f"  Found {len(games)} home games", file=sys.stderr)
                all_games.extend(games)
            else:
                print(f"  No home games found", file=sys.stderr)

    print(f"\n=== TOTAL: {len(all_games)} home games across all sports ===", file=sys.stderr)

    # Save aggregated games
    output_file = f".tmp/{school_name.lower().replace(' ', '_')}_all_games.json"
    with open(output_file, 'w') as f:
        json.dump({
            'school': school_name,
            'total_games': len(all_games),
            'games': all_games
        }, f, indent=2)

    print(f"\nSaved all games to {output_file}", file=sys.stderr)

    # Output JSON for pipeline
    print(json.dumps({
        'school': school_name,
        'total_games': len(all_games),
        'output_file': output_file,
        'success': True
    }, indent=2))

def main():
    parser = argparse.ArgumentParser(description="Batch scrape all teams for a school")
    parser.add_argument('--school', required=True, help="School name")
    parser.add_argument('--url', required=True, help="Athletics website URL")

    args = parser.parse_args()

    batch_scrape_school(args.school, args.url)

if __name__ == "__main__":
    main()
