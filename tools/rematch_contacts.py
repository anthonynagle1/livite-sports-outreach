#!/usr/bin/env python3
"""
Fast re-matching tool: Uses existing cached contacts without new scraping.
Re-runs the matching logic with fuzzy cache lookup.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from collections import defaultdict

def clean_opponent_name(name):
    """Strip schedule artifacts from opponent names."""
    cleaned = name
    cleaned = re.sub(r'\s*\(DH\)', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*\(Exh\.?\)', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^No\.\s*\d+\s*', '', cleaned)
    cleaned = re.sub(r'^RV\s+', '', cleaned)
    cleaned = re.sub(r'\s*\([A-Z][a-z.]+\)\s*$', '', cleaned)
    cleaned = re.sub(r'\s*\([A-Z]\.[A-Z]\.\)\s*$', '', cleaned)
    cleaned = re.sub(r'\s*\(Me\.\)\s*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*\(Maine\)\s*$', '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()

def normalize_school_name(name):
    """Normalize a school name for fuzzy matching."""
    normalized = name.lower()
    for suffix in ['university', 'college', 'institute of technology', 'state']:
        normalized = re.sub(rf'\s+{suffix}\s*$', '', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'\s*\([a-z.]+\)\s*', '', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'\s*\(dh\)\s*', '', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'[^\w\s-]', '', normalized)  # Keep hyphens
    normalized = re.sub(r'\s+', '_', normalized.strip())
    return normalized

# School name aliases for fuzzy matching
SCHOOL_ALIASES = {
    'vermont state lyndon': ['vtsu_lyndon', 'vermont_state_lyndon', 'lyndon_state'],
    'vermont state johnson': ['vtsu_johnson', 'vermont_state_johnson', 'johnson_state'],
    'vtsu lyndon': ['vtsu_lyndon', 'vermont_state_lyndon'],
    'vtsu johnson': ['vtsu_johnson', 'vermont_state_johnson'],
    'umaine-presque isle': ['umaine-presque_isle', 'maine_presque_isle', 'me-presque_isle'],
    'umaine presque isle': ['umaine-presque_isle', 'umaine_presque_isle', 'maine_presque_isle'],
    'umaine farmington': ['umaine_farmington', 'maine_farmington'],
    'me.-presque isle': ['umaine-presque_isle', 'me-presque_isle'],
    'me.-fort kent': ['umaine-fort_kent', 'fort_kent'],
    'suny cobleskill': ['suny_cobleskill', 'cobleskill'],
    'suny delhi': ['suny_delhi', 'delhi'],
    'western new eng.': ['western_new_england', 'wne'],
    'bay path': ['bay_path'],
    'bryant & stratton - albany': ['bryant_&_stratton', 'bryant_stratton'],
    'bryant & stratton': ['bryant_&_stratton', 'bryant_stratton'],
}

def is_real_opponent(name):
    """Filter out non-opponent entries."""
    skip_patterns = [
        'championship', 'ncaa', 'tournament', 'regional', 'semifinal',
        'quarterfinal', 'final four', 'first round', 'second round',
        'play-in', 'tbd', 'tba', 'to be determined', 'winner of',
        'loser of', 'consolation', 'exhibition', 'gnac', 'newmac',
        'conference', 'playoffs', 'all-star', 'scrimmage', 'ivy league'
    ]
    name_lower = name.lower()
    return not any(p in name_lower for p in skip_patterns)

def load_all_cached_contacts(cache_dir='.tmp/cache/contacts'):
    """Load ALL cached contact files into memory."""
    cache_path = Path(cache_dir)
    all_contacts = {}

    if not cache_path.exists():
        return all_contacts

    for f in cache_path.glob('*.json'):
        try:
            with open(f) as file:
                data = json.load(file)
                if data.get('staff_found', 0) > 0:
                    all_contacts[f.stem] = data
        except Exception as e:
            pass

    return all_contacts

def find_contact_in_cache(opponent_name, sport, gender, all_contacts):
    """Find contacts using multiple matching strategies."""
    opponent_norm = normalize_school_name(opponent_name)
    opponent_lower = opponent_name.lower()
    sport_norm = sport.lower().replace(' ', '_').replace('&', 'and')

    # Strategy 1: Exact key match patterns
    exact_patterns = [
        f"{opponent_name}_{gender}_{sport}".lower().replace(' ', '_'),
        f"{opponent_norm}_{gender.lower()}_{sport_norm}",
    ]
    for pattern in exact_patterns:
        if pattern in all_contacts:
            return all_contacts[pattern]

    # Strategy 2: Check known aliases
    aliases_to_try = []
    for alias_key, alias_values in SCHOOL_ALIASES.items():
        if alias_key in opponent_lower:
            aliases_to_try.extend(alias_values)

    for alias in aliases_to_try:
        for key, data in all_contacts.items():
            fname = key.lower()
            if alias in fname and sport_norm in fname:
                return data

    # Strategy 3: Substring match (opponent + sport)
    for key, data in all_contacts.items():
        fname = key.lower()
        if opponent_norm in fname and sport_norm in fname:
            if gender.lower() in fname or gender == 'Unknown':
                return data
        # Also try with hyphens replaced by underscores
        opponent_hyphen = opponent_norm.replace('-', '_')
        if opponent_hyphen in fname and sport_norm in fname:
            if gender.lower() in fname or gender == 'Unknown':
                return data

    # Strategy 4: Core name match (first significant word)
    core_words = [w for w in opponent_norm.replace('-', '_').split('_') if len(w) > 3]
    if core_words:
        primary_word = core_words[0]
        for key, data in all_contacts.items():
            fname = key.lower()
            if primary_word in fname and sport_norm in fname:
                return data

    return None

def match_contact_from_staff(staff):
    """Find best contact from staff list using priority matching."""
    if not staff:
        return None

    # Priority titles
    priority_titles = [
        'director of operations',
        'assistant coach',
        'associate head',
        'head coach'
    ]

    for priority_title in priority_titles:
        for member in staff:
            title = member.get('title', '').lower()
            email = member.get('email', '')
            if priority_title in title and email and email != 'Not Found' and '@' in email:
                return member

    # Fallback: any staff with email
    for member in staff:
        email = member.get('email', '')
        if email and email != 'Not Found' and '@' in email:
            return member

    return None

def rematch_school(school_name, games_file):
    """Re-match all games using cached contacts only."""

    # Load games
    with open(games_file, 'r') as f:
        games_data = json.load(f)

    games = games_data.get('games', [])
    print(f"Loaded {len(games)} games for {school_name}", file=sys.stderr)

    # Load ALL cached contacts
    all_contacts = load_all_cached_contacts()
    print(f"Loaded {len(all_contacts)} cached contact files", file=sys.stderr)

    # Match games to contacts
    validated_matches = []

    for game in games:
        opponent = game.get('opponent')
        sport = game.get('sport')
        gender = game.get('gender', 'Unknown')

        clean_name = clean_opponent_name(opponent)

        if not is_real_opponent(clean_name):
            # Skip tournament entries
            validated_matches.append({
                **game,
                'match_status': 'skip_tournament'
            })
            continue

        # Find contact in cache
        contact_data = find_contact_in_cache(clean_name, sport, gender, all_contacts)

        if contact_data:
            staff = contact_data.get('staff', [])
            best_contact = match_contact_from_staff(staff)

            if best_contact:
                validated_matches.append({
                    **game,
                    'contact_name': best_contact.get('name'),
                    'contact_title': best_contact.get('title'),
                    'contact_email': best_contact.get('email'),
                    'contact_phone': best_contact.get('phone', 'Not Found'),
                    'opponent_coaches_url': contact_data.get('coaches_url', ''),
                    'match_status': 'success'
                })
            else:
                validated_matches.append({
                    **game,
                    'opponent_coaches_url': contact_data.get('coaches_url', ''),
                    'match_status': 'no_contact'
                })
        else:
            validated_matches.append({
                **game,
                'match_status': 'no_opponent_data'
            })

    # Calculate stats (excluding tournament entries)
    real_matches = [m for m in validated_matches if m.get('match_status') != 'skip_tournament']
    total_games = len(real_matches)
    games_with_contacts = sum(1 for m in real_matches if m.get('match_status') == 'success')
    contact_rate = (games_with_contacts / total_games * 100) if total_games > 0 else 0

    print(f"Result: {games_with_contacts}/{total_games} ({contact_rate:.1f}%)", file=sys.stderr)

    # Save results
    output_file = f".tmp/{school_name.lower().replace(' ', '_')}_matched.json"
    output_data = {
        'school': school_name,
        'total_games': total_games,
        'games_with_contacts': games_with_contacts,
        'contact_rate': f"{contact_rate:.1f}%",
        'validated_matches': validated_matches
    }

    with open(output_file, 'w') as f:
        json.dump(output_data, f, indent=2)

    # Output JSON
    print(json.dumps(output_data, indent=2))

def main():
    parser = argparse.ArgumentParser(description="Fast re-matching using cached contacts only")
    parser.add_argument('--school', required=True, help="School name")
    parser.add_argument('--games-file', required=True, help="Games JSON file")

    args = parser.parse_args()
    rematch_school(args.school, args.games_file)

if __name__ == "__main__":
    main()
