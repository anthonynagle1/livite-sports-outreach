#!/usr/bin/env python3
"""
Process all contacts for a school: discover opponents → scrape contacts → match → validate
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from collections import defaultdict

def run_command(cmd, desc, timeout=180):
    """Run command and return JSON output."""
    print(f"\n{desc}...", file=sys.stderr)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"  Command timed out after {timeout}s", file=sys.stderr)
        return None

    if result.returncode != 0:
        print(f"  Warning: {result.stderr[:200]}", file=sys.stderr)
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

def clean_opponent_name(name):
    """Strip schedule artifacts from opponent names like (DH), (Exh), rankings, state suffixes."""
    cleaned = name
    # Strip (DH), (Exh), etc. - can appear anywhere, not just end
    cleaned = re.sub(r'\s*\(DH\)', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*\(Exh\.?\)', '', cleaned, flags=re.IGNORECASE)
    # Strip ranking prefixes like "No. 8Quinnipiac", "No. 18 Princeton", "RV Harvard"
    cleaned = re.sub(r'^No\.\s*\d+\s*', '', cleaned)
    cleaned = re.sub(r'^RV\s+', '', cleaned)
    # Strip state suffixes like "(Mass.)", "(Conn.)", "(Pa.)", "(N.Y.)", "(Me.)", "(Maine)"
    cleaned = re.sub(r'\s*\([A-Z][a-z.]+\)\s*$', '', cleaned)  # (Mass.), (Conn.)
    cleaned = re.sub(r'\s*\([A-Z]\.[A-Z]\.\)\s*$', '', cleaned)  # (N.Y.)
    cleaned = re.sub(r'\s*\(Me\.\)\s*$', '', cleaned, flags=re.IGNORECASE)  # (Me.)
    cleaned = re.sub(r'\s*\(Maine\)\s*$', '', cleaned, flags=re.IGNORECASE)  # (Maine)
    return cleaned.strip()

def is_real_opponent(name):
    """Filter out non-opponent entries (championships, TBD, etc.)."""
    skip_patterns = [
        'championship', 'ncaa', 'tournament', 'regional', 'semifinal',
        'quarterfinal', 'final four', 'first round', 'second round',
        'play-in', 'tbd', 'tba', 'to be determined', 'winner of',
        'loser of', 'consolation', 'exhibition', 'nec tournament',
        'ivy league', 'ecac', 'patriot league', 'nescac', 'newmac',
        'conference', 'playoffs', 'all-star', 'scrimmage'
    ]
    name_lower = name.lower()
    return not any(p in name_lower for p in skip_patterns)

def normalize_school_name(name):
    """Normalize a school name for fuzzy matching."""
    normalized = name.lower()
    # Strip common suffixes
    for suffix in ['university', 'college', 'institute of technology', 'state']:
        normalized = re.sub(rf'\s+{suffix}\s*$', '', normalized, flags=re.IGNORECASE)
    # Strip state abbreviations like (Mass.), (Conn.), (Me.)
    normalized = re.sub(r'\s*\([a-z.]+\)\s*', '', normalized, flags=re.IGNORECASE)
    # Strip schedule artifacts
    normalized = re.sub(r'\s*\(dh\)\s*', '', normalized, flags=re.IGNORECASE)
    # Replace special chars
    normalized = re.sub(r'[^\w\s]', '', normalized)
    normalized = re.sub(r'\s+', '_', normalized.strip())
    return normalized

def find_cached_contacts(opponent_name, sport, gender, cache_dir='.tmp/cache/contacts'):
    """
    Search the cache directory for contacts matching the opponent.
    Returns (contact_data, cache_file) or (None, None) if not found.
    """
    cache_path = Path(cache_dir)
    if not cache_path.exists():
        return None, None

    # Normalize search terms
    opponent_norm = normalize_school_name(opponent_name)
    sport_norm = sport.lower().replace(' ', '_').replace('&', 'and')

    # Get all cache files
    cache_files = list(cache_path.glob('*.json'))

    # Strategy 1: Exact key match
    exact_patterns = [
        f"{opponent_name}_{gender}_{sport}".lower().replace(' ', '_'),
        f"{opponent_norm}_{gender.lower()}_{sport_norm}",
    ]
    for pattern in exact_patterns:
        for f in cache_files:
            if f.stem == pattern:
                with open(f) as file:
                    data = json.load(file)
                    if data.get('staff_found', 0) > 0:
                        return data, str(f)

    # Strategy 2: Substring match (opponent name in filename AND sport in filename)
    for f in cache_files:
        fname = f.stem.lower()
        # Check if normalized opponent name appears in filename
        if opponent_norm in fname and sport_norm in fname:
            with open(f) as file:
                data = json.load(file)
                if data.get('staff_found', 0) > 0:
                    # Prefer matching gender if possible
                    if gender.lower() in fname or gender == 'Unknown':
                        return data, str(f)

    # Strategy 3: More aggressive fuzzy - try any file with opponent name core
    # Extract core name (first significant word)
    core_words = [w for w in opponent_norm.split('_') if len(w) > 3]
    if core_words:
        primary_word = core_words[0]  # e.g., "curry", "lasell", "wentworth"
        for f in cache_files:
            fname = f.stem.lower()
            if primary_word in fname and sport_norm in fname:
                with open(f) as file:
                    data = json.load(file)
                    if data.get('staff_found', 0) > 0:
                        return data, str(f)

    return None, None

def extract_unique_opponents(games):
    """Extract unique opponent/sport combinations from games."""
    opponents = defaultdict(set)

    for game in games:
        opponent = game.get('opponent')
        sport = game.get('sport')
        gender = game.get('gender', 'Unknown')

        if opponent and sport:
            # Clean the opponent name
            clean_name = clean_opponent_name(opponent)
            if is_real_opponent(clean_name):
                opponents[clean_name].add((sport, gender))

    return opponents

def try_scrape_with_url_variants(opponent_url, sport, gender, opponent_name, cache_file):
    """
    Try multiple URL patterns to find the coaches page.
    Handles gender prefix variants when gender is Unknown.
    """
    sport_slug = sport.lower().replace(' ', '-').replace('&', 'and')

    # Build list of URL variants to try
    url_variants = []

    if gender == 'Men':
        url_variants.append(f"{opponent_url}/sports/mens-{sport_slug}")
    elif gender == 'Women':
        url_variants.append(f"{opponent_url}/sports/womens-{sport_slug}")
    else:
        # Unknown gender — try all variants
        url_variants.append(f"{opponent_url}/sports/womens-{sport_slug}")
        url_variants.append(f"{opponent_url}/sports/mens-{sport_slug}")
        url_variants.append(f"{opponent_url}/sports/{sport_slug}")

    for team_url in url_variants:
        print(f"    Trying URL: {team_url}", file=sys.stderr)
        contact_data = run_command(
            ['python3', 'tools/scrape_team_staff.py',
             '--team-url', team_url,
             '--sport', sport,
             '--school', opponent_name,
             '--output', cache_file],
            f"    Scraping {team_url}"
        )

        if contact_data and contact_data.get('success') and contact_data.get('staff_found', 0) > 0:
            return contact_data

        # If scrape returned 0 staff but succeeded, try next variant
        if contact_data and contact_data.get('success') and contact_data.get('staff_found', 0) == 0:
            # Delete the empty cache file so we don't reuse it
            try:
                Path(cache_file).unlink(missing_ok=True)
            except:
                pass
            continue

    # All variants failed — return last result (even if 0 staff)
    return contact_data

def construct_coaches_url(opponent_url, sport, gender):
    """Construct a coaches page URL even when scraping failed."""
    sport_slug = sport.lower().replace(' ', '-').replace('&', 'and')
    if gender == 'Women':
        return f"{opponent_url}/sports/womens-{sport_slug}/coaches"
    elif gender == 'Men':
        return f"{opponent_url}/sports/mens-{sport_slug}/coaches"
    else:
        return f"{opponent_url}/sports/{sport_slug}/coaches"

def process_school_contacts(school_name, games_file):
    """Process all contacts for a school."""

    # Load games
    print(f"\n=== Processing {school_name} ===", file=sys.stderr)
    with open(games_file, 'r') as f:
        games_data = json.load(f)

    games = games_data.get('games', [])
    print(f"Loaded {len(games)} games", file=sys.stderr)

    # Extract unique opponents (with cleaned names)
    opponents = extract_unique_opponents(games)
    print(f"Found {len(opponents)} unique opponents", file=sys.stderr)

    # Track opponent URLs for populating coaches links
    opponent_urls = {}

    # Process each opponent
    all_contacts = {}

    for opponent_name, sports in opponents.items():
        print(f"\n--- Processing {opponent_name} ---", file=sys.stderr)

        # Discover opponent URL
        url_result = run_command(
            ['python3', 'tools/discover_opponent_url.py',
             '--school', opponent_name],
            f"  Discovering URL for {opponent_name}"
        )

        if not url_result or not url_result.get('success'):
            print(f"  ✗ No URL found for {opponent_name}", file=sys.stderr)
            continue

        opponent_url = url_result.get('athletics_url')
        opponent_urls[opponent_name] = opponent_url
        print(f"  ✓ URL: {opponent_url}", file=sys.stderr)

        # Scrape contacts for each sport
        for sport, gender in sports:
            cache_key = f"{opponent_name}_{gender}_{sport}"
            cache_file = f".tmp/cache/contacts/{cache_key.lower().replace(' ', '_')}.json"

            # Check cache first - but verify it has staff
            if Path(cache_file).exists():
                with open(cache_file, 'r') as f:
                    contact_data = json.load(f)

                staff_count = contact_data.get('staff_found', 0)
                has_emails = any(
                    s.get('email', 'Not Found') != 'Not Found'
                    for s in contact_data.get('staff', [])
                )

                if staff_count > 0 and has_emails:
                    print(f"  ✓ Using cached contacts for {gender}'s {sport} ({staff_count} staff)", file=sys.stderr)
                else:
                    # Cache exists but is empty or has no emails — re-scrape
                    print(f"  ↻ Cache has {staff_count} staff but {'no emails' if not has_emails else 'empty'}, re-scraping...", file=sys.stderr)
                    Path(cache_file).unlink(missing_ok=True)
                    contact_data = try_scrape_with_url_variants(
                        opponent_url, sport, gender, opponent_name, cache_file
                    )
                    if not contact_data or not contact_data.get('success'):
                        print(f"  ✗ Failed to scrape contacts", file=sys.stderr)
                        continue
            else:
                contact_data = try_scrape_with_url_variants(
                    opponent_url, sport, gender, opponent_name, cache_file
                )
                if not contact_data or not contact_data.get('success'):
                    print(f"  ✗ Failed to scrape contacts", file=sys.stderr)
                    continue

            staff_count = contact_data.get('staff_found', 0)
            if staff_count > 0:
                print(f"  ✓ Found {staff_count} staff for {gender}'s {sport}", file=sys.stderr)
                all_contacts[cache_key] = contact_data
            else:
                print(f"  ✗ No staff found for {gender}'s {sport}", file=sys.stderr)

    # Match games to contacts
    print(f"\n=== Matching Games to Contacts ===", file=sys.stderr)

    validated_matches = []

    for game in games:
        opponent = game.get('opponent')
        sport = game.get('sport')
        gender = game.get('gender', 'Unknown')

        # Clean opponent name to match
        clean_name = clean_opponent_name(opponent)

        cache_key = f"{clean_name}_{gender}_{sport}"

        if cache_key not in all_contacts:
            # Try gendered variants if gender is Unknown
            if gender == 'Unknown':
                for try_gender in ['Women', 'Men']:
                    alt_key = f"{clean_name}_{try_gender}_{sport}"
                    if alt_key in all_contacts:
                        cache_key = alt_key
                        break

        if cache_key not in all_contacts:
            # Try fuzzy matching within current session's contacts
            for key in all_contacts.keys():
                if clean_name in key and sport in key:
                    cache_key = key
                    break

        # If still not found, search the FULL cache directory
        contact_data = None
        if cache_key not in all_contacts:
            contact_data, cache_file = find_cached_contacts(clean_name, sport, gender)
            if contact_data:
                print(f"  ✓ Found cached contacts for {clean_name} via fuzzy search: {cache_file}", file=sys.stderr)
                all_contacts[cache_key] = contact_data  # Add to session for future lookups

        # Build the coaches URL — always try to provide one
        opponent_coaches_url = ''
        if cache_key in all_contacts:
            contact_data = all_contacts[cache_key]
            opponent_coaches_url = contact_data.get('coaches_url', '')
            staff = contact_data.get('staff', [])

            # Priority matching
            best_contact = None
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
                        best_contact = member
                        break

                if best_contact:
                    break

            # FALLBACK: If no priority match, take ANY staff member with an email
            if not best_contact:
                for member in staff:
                    email = member.get('email', '')
                    if email and email != 'Not Found' and '@' in email:
                        best_contact = member
                        break

            if best_contact:
                validated_matches.append({
                    **game,
                    'contact_name': best_contact.get('name'),
                    'contact_title': best_contact.get('title'),
                    'contact_email': best_contact.get('email'),
                    'contact_phone': best_contact.get('phone', 'Not Found'),
                    'opponent_coaches_url': opponent_coaches_url,
                    'match_status': 'success'
                })
            else:
                validated_matches.append({
                    **game,
                    'opponent_coaches_url': opponent_coaches_url,
                    'match_status': 'no_contact'
                })
        else:
            # No contact data — still try to provide a coaches URL if we have the base URL
            if clean_name in opponent_urls:
                opponent_coaches_url = construct_coaches_url(
                    opponent_urls[clean_name], sport, gender
                )
            elif not is_real_opponent(clean_name):
                # Championship/playoff entry — skip silently
                pass

            validated_matches.append({
                **game,
                'opponent_coaches_url': opponent_coaches_url,
                'match_status': 'no_opponent_data'
            })

    # Calculate stats
    total_games = len(validated_matches)
    games_with_contacts = sum(1 for m in validated_matches if m.get('match_status') == 'success')
    contact_rate = (games_with_contacts / total_games * 100) if total_games > 0 else 0

    print(f"\n=== Results ===", file=sys.stderr)
    print(f"Total games: {total_games}", file=sys.stderr)
    print(f"Games with contacts: {games_with_contacts} ({contact_rate:.1f}%)", file=sys.stderr)

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

    print(f"Saved to {output_file}", file=sys.stderr)

    # Output JSON for pipeline
    print(json.dumps(output_data, indent=2))

def main():
    parser = argparse.ArgumentParser(description="Process contacts for a school")
    parser.add_argument('--school', required=True, help="School name")
    parser.add_argument('--games-file', required=True, help="Games JSON file")

    args = parser.parse_args()

    process_school_contacts(args.school, args.games_file)

if __name__ == "__main__":
    main()
