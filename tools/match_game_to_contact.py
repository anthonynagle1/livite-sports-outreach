#!/usr/bin/env python3
"""
Tool: match_game_to_contact.py
Purpose: Match games to sport-specific coaching contacts using priority logic

Priority order (from user requirements):
1. Director of Operations (sport-specific)
2. First Assistant Coach
3. Assistant Coach (any)
4. Associate Head Coach
5. Head Coach

Usage:
    python tools/match_game_to_contact.py \
        --game-data bc_baseball_schedule.json \
        --staff-data cache/contacts/merrimack_baseball.json \
        --output matched_contacts.json

Output: JSON with games matched to specific coaching contacts
"""

import argparse
import json
import sys
import re
from datetime import datetime


def get_contact_priority_score(title):
    """
    Assign priority score to contact based on title.
    Lower score = higher priority.

    Args:
        title (str): Contact's job title

    Returns:
        int: Priority score (1-6, lower is better)
    """
    title_lower = title.lower()

    # Priority 1: Director of Operations
    if 'director of operations' in title_lower or 'dir' in title_lower and 'operations' in title_lower:
        return 1

    # Priority 2: First Assistant Coach (specifically labeled)
    if 'first assistant' in title_lower or '1st assistant' in title_lower:
        return 2

    # Priority 3: Assistant Coach (general)
    if 'assistant coach' in title_lower or 'asst coach' in title_lower or 'assistant' in title_lower:
        return 3

    # Priority 4: Associate Head Coach
    if 'associate head' in title_lower:
        return 4

    # Priority 5: Head Coach
    if 'head coach' in title_lower:
        return 5

    # Priority 6: Other staff (support, operations, etc.)
    return 6


def match_game_to_contact(game, staff_list, opponent_school):
    """
    Match a game to the best coaching contact using priority logic.

    Args:
        game (dict): Game info with opponent, date, time, venue
        staff_list (list): List of staff members from opponent's team
        opponent_school (str): School name from staff data

    Returns:
        dict: Game data + matched contact info
    """
    # Check if this game's opponent matches the staff school
    game_opponent = game.get('opponent', '').lower()
    staff_school = opponent_school.lower()

    # Simple check: if staff school appears in opponent name
    if staff_school not in game_opponent and game_opponent not in staff_school:
        return {
            **game,
            'contact_name': 'N/A - Different Opponent',
            'contact_title': 'N/A',
            'contact_email': 'N/A',
            'contact_phone': 'N/A',
            'match_status': 'opponent_mismatch',
        }

    # Filter for contacts with valid emails
    valid_contacts = [
        staff for staff in staff_list
        if staff.get('email') and staff['email'] != 'Not Found'
    ]

    if not valid_contacts:
        return {
            **game,
            'contact_name': 'Not Found',
            'contact_title': 'Not Found',
            'contact_email': 'Not Found',
            'contact_phone': 'Not Found',
            'match_status': 'no_valid_contacts',
        }

    # Score all contacts and sort by priority
    scored_contacts = []
    for contact in valid_contacts:
        score = get_contact_priority_score(contact.get('title', ''))
        scored_contacts.append((score, contact))

    # Sort by priority score (lower = better)
    scored_contacts.sort(key=lambda x: x[0])

    # Get best contact
    best_score, best_contact = scored_contacts[0]

    # Determine match quality
    if best_score == 1:
        match_quality = 'excellent'  # Director of Operations
    elif best_score == 2:
        match_quality = 'very_good'  # First Assistant
    elif best_score == 3:
        match_quality = 'good'  # Assistant Coach
    elif best_score == 4:
        match_quality = 'acceptable'  # Associate Head
    elif best_score == 5:
        match_quality = 'fallback'  # Head Coach
    else:
        match_quality = 'poor'  # Other staff

    return {
        **game,
        'contact_name': best_contact.get('name', 'Unknown'),
        'contact_title': best_contact.get('title', 'Unknown'),
        'contact_email': best_contact.get('email', 'Not Found'),
        'contact_phone': best_contact.get('phone', 'Not Found'),
        'match_quality': match_quality,
        'match_status': 'success',
    }


def main():
    parser = argparse.ArgumentParser(
        description="Match games to coaching contacts using priority logic"
    )
    parser.add_argument(
        "--game-data",
        required=True,
        help="Path to game schedule JSON file"
    )
    parser.add_argument(
        "--staff-data",
        required=True,
        help="Path to opponent staff JSON file"
    )
    parser.add_argument(
        "--output",
        help="Output JSON file path (default: stdout)"
    )

    args = parser.parse_args()

    # Load game data
    try:
        with open(args.game_data, 'r') as f:
            game_data = json.load(f)
    except Exception as e:
        print(f"Error loading game data: {e}", file=sys.stderr)
        sys.exit(1)

    # Load staff data
    try:
        with open(args.staff_data, 'r') as f:
            staff_data = json.load(f)
    except Exception as e:
        print(f"Error loading staff data: {e}", file=sys.stderr)
        sys.exit(1)

    # Extract games list
    games = game_data.get('games', [])
    staff_list = staff_data.get('staff', [])

    if not games:
        print("No games found in game data", file=sys.stderr)
        sys.exit(1)

    if not staff_list:
        print("No staff found in staff data", file=sys.stderr)
        sys.exit(1)

    # Match each game to a contact
    opponent_school = staff_data.get('school', 'Unknown')
    matched_games = []
    for game in games:
        matched_game = match_game_to_contact(game, staff_list, opponent_school)
        matched_games.append(matched_game)

    # Create result
    result = {
        "school": game_data.get('school', 'Unknown'),
        "sport": game_data.get('sport', 'Unknown'),
        "opponent_school": staff_data.get('school', 'Unknown'),
        "games_matched": len(matched_games),
        "matches": matched_games,
        "success": True,
        "timestamp": str(datetime.now()),
    }

    # Save to file if specified
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Matched {len(matched_games)} games to contacts", file=sys.stderr)
        print(f"Results saved to {args.output}", file=sys.stderr)

    # Always output JSON to stdout for pipeline processing
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
