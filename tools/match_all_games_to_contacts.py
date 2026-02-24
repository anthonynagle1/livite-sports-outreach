#!/usr/bin/env python3
"""
Match all games to contacts using gender-aware cache lookup.

Reads schedule files (with gender data in each game), looks up opponent contacts
from cache using gender-aware filenames, and matches using priority logic.

Usage:
    python tools/match_all_games_to_contacts.py \
        --schedules-pattern ".tmp/boston_college_*_schedule.json" \
        --cache-dir ".tmp/cache/contacts/" \
        --output all_games_with_contacts.json
"""

import argparse
import json
import sys
import glob
import os


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


def get_contact_priority_score(title):
    """
    Assign priority score to contact based on title.
    Lower score = higher priority.
    """
    title_lower = title.lower()

    # Priority 1: Director of Operations
    if 'director of operations' in title_lower or ('dir' in title_lower and 'operations' in title_lower):
        return 1

    # Priority 2: First Assistant Coach
    if 'first assistant' in title_lower or '1st assistant' in title_lower:
        return 2

    # Priority 3: Assistant Coach
    if 'assistant coach' in title_lower or 'asst coach' in title_lower or 'assistant' in title_lower:
        return 3

    # Priority 4: Associate Head Coach
    if 'associate head' in title_lower:
        return 4

    # Priority 5: Head Coach
    if 'head coach' in title_lower:
        return 5

    # Priority 6: Other staff
    return 6


def load_opponent_contacts(opponent_school, sport, gender, cache_dir):
    """
    Load opponent contacts from cache using gender-aware filename.

    Args:
        opponent_school (str): Opponent school name
        sport (str): Sport name
        gender (str): Gender (Men, Women, or Unknown)
        cache_dir (str): Cache directory path

    Returns:
        list: Staff members or empty list
    """
    normalized_school = normalize_school_name(opponent_school)
    normalized_sport = normalize_sport_name(sport)
    normalized_gender = normalize_gender(gender)

    # Try cache filename with gender first
    if normalized_gender:
        cache_file = os.path.join(cache_dir, f"{normalized_school}_{normalized_gender}_{normalized_sport}.json")
    else:
        cache_file = os.path.join(cache_dir, f"{normalized_school}_{normalized_sport}.json")

    # If not found, try without gender (for backward compatibility)
    if not os.path.exists(cache_file):
        cache_file_no_gender = os.path.join(cache_dir, f"{normalized_school}_{normalized_sport}.json")
        if os.path.exists(cache_file_no_gender):
            cache_file = cache_file_no_gender

    if not os.path.exists(cache_file):
        return []

    try:
        with open(cache_file, 'r') as f:
            data = json.load(f)
        return data.get('staff', [])
    except:
        return []


def infer_gender_from_sport(sport, current_gender):
    """
    Infer gender from sport name if not explicitly known.

    Args:
        sport (str): Sport name
        current_gender (str): Current gender value

    Returns:
        str: Inferred gender (Men or Women)
    """
    if current_gender and current_gender != 'Unknown':
        return current_gender

    sport_lower = sport.lower()

    # Men's sports (no women's equivalent or traditionally men's)
    if sport_lower in ['baseball', 'football']:
        return 'Men'

    # Women's sports (no men's equivalent or traditionally women's)
    if sport_lower in ['softball', 'field hockey', 'volleyball']:
        return 'Women'

    # For other sports, default to Unknown
    return 'Unknown'


def match_game_to_contact(game, cache_dir):
    """
    Match a game to the best coaching contact.

    Args:
        game (dict): Game data with opponent, sport, gender
        cache_dir (str): Cache directory path

    Returns:
        dict: Game data with contact info added
    """
    opponent = game.get('opponent', '')
    sport = game.get('sport', '')
    current_gender = game.get('gender', 'Unknown')

    # Infer gender if unknown
    gender = infer_gender_from_sport(sport, current_gender)

    # Update game with inferred gender
    game['gender'] = gender

    # Load opponent contacts
    staff_list = load_opponent_contacts(opponent, sport, gender, cache_dir)

    if not staff_list:
        return {
            **game,
            'contact_name': 'No Contact Found',
            'contact_title': '',
            'contact_email': '',
            'contact_phone': '',
            'match_quality': '',
            'match_status': 'no_contacts'
        }

    # Filter for valid emails
    valid_contacts = [
        staff for staff in staff_list
        if staff.get('email') and staff['email'] != 'Not Found'
    ]

    if not valid_contacts:
        return {
            **game,
            'contact_name': 'No Contact Found',
            'contact_title': '',
            'contact_email': '',
            'contact_phone': '',
            'match_quality': '',
            'match_status': 'no_valid_emails'
        }

    # Score and sort by priority
    scored_contacts = []
    for contact in valid_contacts:
        score = get_contact_priority_score(contact.get('title', ''))
        scored_contacts.append((score, contact))

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
        'match_status': 'success'
    }


def main():
    parser = argparse.ArgumentParser(
        description="Match all games to contacts using gender-aware cache"
    )
    parser.add_argument(
        "--schedules-pattern",
        required=True,
        help="Glob pattern for schedule files"
    )
    parser.add_argument(
        "--cache-dir",
        default=".tmp/cache/contacts/",
        help="Cache directory for opponent contacts"
    )
    parser.add_argument(
        "--output",
        help="Output JSON file path (default: stdout)"
    )

    args = parser.parse_args()

    # Find all schedule files
    schedule_files = glob.glob(args.schedules_pattern)
    print(f"Found {len(schedule_files)} schedule files", file=sys.stderr)

    # Collect all games
    all_games = []
    for schedule_file in schedule_files:
        try:
            with open(schedule_file, 'r') as f:
                data = json.load(f)

            if data.get('success') and data.get('games'):
                all_games.extend(data['games'])

        except Exception as e:
            print(f"Error reading {schedule_file}: {e}", file=sys.stderr)

    print(f"Total games: {len(all_games)}", file=sys.stderr)

    # Match each game to a contact
    matched_games = []
    for game in all_games:
        matched_game = match_game_to_contact(game, args.cache_dir)
        matched_games.append(matched_game)

    # Sort by parsed_date
    matched_games.sort(key=lambda x: x.get('parsed_date', ''))

    # Count successes
    games_with_contacts = len([g for g in matched_games if g.get('contact_email') and g['contact_email'] != 'Not Found'])

    print(f"Games with contacts: {games_with_contacts}/{len(matched_games)}", file=sys.stderr)

    # Output
    result = {
        'school': matched_games[0].get('school', 'Unknown') if matched_games else 'Unknown',
        'total_games': len(matched_games),
        'games_with_contacts': games_with_contacts,
        'validated_matches': matched_games,
        'success': True
    }

    # Save to file if specified
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Results saved to {args.output}", file=sys.stderr)

    # Always output JSON to stdout
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
