#!/usr/bin/env python3
"""
Tool: backfill_contacts.py
Purpose: Find games without contacts and scrape/create/link contacts for them.

This fixes the contact gap by:
1. Querying all games that have an Away Team but no Contact
2. Grouping by (school, sport) to minimize duplicate scraping
3. For each combo: discover URL → scrape staff → pick best contact → create in Notion → link to games

Usage:
    # Dry run - show what would be fixed
    python tools/backfill_contacts.py --dry-run

    # Fix all (scrapes, creates contacts, links games)
    python tools/backfill_contacts.py

    # Fix specific school
    python tools/backfill_contacts.py --school "Curry College"

    # Skip scraping - only link existing Notion contacts to unlinked games
    python tools/backfill_contacts.py --link-only
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

try:
    from notion_client import Client
    from notion_client.errors import APIResponseError
except ImportError:
    print("Error: notion-client not installed", file=sys.stderr)
    sys.exit(1)

from discover_opponent_url import discover_athletics_url


# Sport name → URL path segment mapping for scrape_team_staff.py
SPORT_URL_MAP = {
    'Baseball': ('baseball', 'men'),
    'Basketball': None,  # Need gender context
    'Ice Hockey': None,  # Need gender context
    'Lacrosse': None,  # Need gender context
    'Softball': ('softball', 'women'),
    'Volleyball': None,  # Need gender context
    'Rowing': None,  # Need gender context
    'Swimming & Diving': None,
    'Water Polo': None,
}

# Priority order for contact selection (lower = better)
TITLE_PRIORITY = [
    ('director of operations', 1),
    ('dir. of ops', 1),
    ('operations', 1),
    ('assistant coach', 2),
    ('associate head coach', 2),
    ('first assistant', 2),
    ('head coach', 3),
]


def get_notion():
    """Get Notion client."""
    return Client(auth=os.getenv('NOTION_API_KEY'))


def get_db_ids():
    """Get database IDs."""
    return {
        'games': os.getenv('NOTION_GAMES_DB'),
        'contacts': os.getenv('NOTION_CONTACTS_DB'),
        'schools': os.getenv('NOTION_SCHOOLS_DB'),
    }


def get_school_name(notion, school_id):
    """Get school name from Notion."""
    try:
        page = notion.pages.retrieve(page_id=school_id)
        return ''.join(
            t.get('plain_text', '')
            for t in page['properties'].get('School Name', {}).get('title', [])
        )
    except:
        return ''


def get_unlinked_games(notion, games_db):
    """Get all games with Away Team but no Contact."""
    games = []
    has_more = True
    cursor = None
    while has_more:
        kwargs = {
            'database_id': games_db,
            'filter': {
                'and': [
                    {'property': 'Contact', 'relation': {'is_empty': True}},
                    {'property': 'Away Team', 'relation': {'is_not_empty': True}},
                ]
            }
        }
        if cursor:
            kwargs['start_cursor'] = cursor
        resp = notion.databases.query(**kwargs)
        games.extend(resp['results'])
        has_more = resp.get('has_more', False)
        cursor = resp.get('next_cursor')
    return games


def find_existing_contact(notion, contacts_db, school_id, sport):
    """Check if a contact already exists for this school + sport in Notion."""
    try:
        resp = notion.databases.query(
            database_id=contacts_db,
            filter={
                'and': [
                    {'property': 'School', 'relation': {'contains': school_id}},
                    {'property': 'Sport', 'select': {'equals': sport}},
                ]
            }
        )
        # Return the first contact with an email
        for contact in resp['results']:
            props = contact['properties']
            email = props.get('Email', {}).get('email', '')
            if email:
                return contact['id']
        return None
    except:
        return None


def scrape_presto_coaches(coaches_url, sport_name):
    """
    Scrape coaches from a PrestoSports coaches page.
    PrestoSports uses .card layout with bio page links for emails.
    """
    from playwright.sync_api import sync_playwright
    import re

    staff = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.goto(coaches_url, wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(3000)

            # First pass: collect names, titles, and bio URLs from cards
            coaches_data = []
            cards = page.query_selector_all('.card')
            for card in cards:
                name_el = card.query_selector('.card-title, a.h5, a.fw-bold')
                title_el = card.query_selector('.card-text, p.small')
                bio_link = card.query_selector('a[href*="/coaches/"]')

                if not name_el:
                    continue

                name = name_el.inner_text().strip()
                title = title_el.inner_text().strip() if title_el else ''

                if not name or name.lower() in ('coaching staff', sport_name.lower()):
                    continue

                bio_href = ''
                if bio_link:
                    bio_href = bio_link.get_attribute('href') or ''

                coaches_data.append({
                    'name': name,
                    'title': title,
                    'bio_href': bio_href,
                })

            # Second pass: visit each bio page in a new tab for emails
            base_url = coaches_url.split('/sports/')[0]
            for coach in coaches_data:
                person = {
                    'name': coach['name'],
                    'title': coach['title'],
                    'email': '',
                    'phone': '',
                    'sport': sport_name,
                }

                if coach['bio_href']:
                    bio_url = coach['bio_href'] if coach['bio_href'].startswith('http') \
                        else base_url + coach['bio_href']
                    bio_page = browser.new_page()
                    try:
                        bio_page.goto(bio_url, wait_until='domcontentloaded', timeout=15000)
                        bio_page.wait_for_timeout(2000)
                        bio_text = bio_page.inner_text('body')
                        email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', bio_text)
                        if email_match:
                            person['email'] = email_match.group()
                        phone_match = re.search(
                            r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', bio_text
                        )
                        if phone_match:
                            person['phone'] = phone_match.group()
                    except Exception:
                        pass
                    finally:
                        bio_page.close()

                staff.append(person)

            browser.close()
    except Exception as e:
        print(f"  Presto scrape error: {e}", file=sys.stderr)

    return staff


# PrestoSports sport abbreviation mapping
PRESTO_SPORT_CODES = {
    'Baseball': ['bsb'],
    'Basketball': ['mbkb', 'wbkb'],
    'Ice Hockey': ['mice', 'wice'],
    'Lacrosse': ['mlax', 'wlax'],
    'Softball': ['sball'],
    'Volleyball': ['mvball', 'wvball'],
    'Rowing': ['wrow', 'mrow', 'row'],
    'Swimming & Diving': ['mswim', 'wswim'],
    'Water Polo': ['mwpolo', 'wwpolo'],
}


def scrape_staff_for_sport(athletics_url, sport, school_name, gender=None):
    """Scrape coaching staff for a specific sport. Returns list of staff dicts."""
    try:
        from scrape_team_staff import scrape_team_staff
    except ImportError:
        scrape_team_staff = None

    # Map sport to URL path (Sidearm-style)
    sport_lower = sport.lower().replace(' & ', '-').replace(' ', '-')
    sport_paths = {
        'baseball': 'baseball',
        'basketball': 'basketball',
        'ice-hockey': 'ice-hockey',
        'lacrosse': 'lacrosse',
        'softball': 'softball',
        'volleyball': 'volleyball',
        'rowing': 'rowing',
        'swimming-diving': 'swimming-and-diving',
        'water-polo': 'water-polo',
    }

    sport_path = sport_paths.get(sport_lower, sport_lower)

    # Determine gender prefix variants to try
    gender_prefixes = []
    if gender and gender.lower() == 'women':
        gender_prefixes = ['womens-', 'women-', 'w', '']
    elif gender and gender.lower() == 'men':
        gender_prefixes = ['mens-', 'men-', 'm', '']
    else:
        if sport in ('Softball',):
            gender_prefixes = ['womens-', 'women-', '']
        elif sport in ('Baseball',):
            gender_prefixes = ['mens-', 'men-', '']
        else:
            gender_prefixes = ['womens-', 'mens-', '']

    # Strategy 1: Try PrestoSports-style URLs first (faster — no long timeouts)
    presto_codes = PRESTO_SPORT_CODES.get(sport, [])
    if gender and gender.lower() == 'women':
        presto_codes = [c for c in presto_codes if c.startswith('w') or c[0] not in ('m', 'w')]
    elif gender and gender.lower() == 'men':
        presto_codes = [c for c in presto_codes if c.startswith('m') or c[0] not in ('m', 'w')]

    for code in presto_codes:
        coaches_url = f"{athletics_url.rstrip('/')}/sports/{code}/coaches"
        print(f"  Trying PrestoSports: {coaches_url}", file=sys.stderr)
        staff = scrape_presto_coaches(coaches_url, sport)
        if staff:
            return staff

    # Strategy 2: Try Sidearm-style URLs with scrape_team_staff
    if scrape_team_staff:
        for prefix in gender_prefixes:
            team_url = f"{athletics_url.rstrip('/')}/sports/{prefix}{sport_path}"
            try:
                result = scrape_team_staff(team_url, sport, school_name)
                staff = result.get('staff', [])
                if staff:
                    return staff
            except Exception:
                continue

    return []


def pick_best_contact(staff_list):
    """Pick the best contact from a staff list using priority logic."""
    best = None
    best_priority = 99

    for person in staff_list:
        email = person.get('email', '')
        if not email or email == 'Not Found':
            continue

        title = (person.get('title', '') or '').lower()
        priority = 4  # Default

        for pattern, prio in TITLE_PRIORITY:
            if pattern in title:
                priority = prio
                break

        if priority < best_priority:
            best = person
            best_priority = priority

    # Fallback: any person with an email
    if not best:
        for person in staff_list:
            email = person.get('email', '')
            if email and email != 'Not Found':
                return person

    return best


def create_notion_contact(notion, contacts_db, school_id, staff_person, sport):
    """Create a contact in Notion. Returns page ID."""
    name = staff_person.get('name', '')
    email = staff_person.get('email', '')
    title = staff_person.get('title', '')
    phone = staff_person.get('phone', '')

    if not name or not email:
        return None

    # Check if contact already exists by email
    try:
        resp = notion.databases.query(
            database_id=contacts_db,
            filter={'property': 'Email', 'email': {'equals': email}}
        )
        if resp['results']:
            return resp['results'][0]['id']
    except:
        pass

    properties = {
        'Name': {'title': [{'text': {'content': name}}]},
        'Email': {'email': email},
        'Sport': {'select': {'name': sport}},
    }
    if title:
        properties['Title'] = {'rich_text': [{'text': {'content': title}}]}
    if phone and phone != 'Not Found':
        properties['Phone'] = {'phone_number': phone}
    if school_id:
        properties['School'] = {'relation': [{'id': school_id}]}

    try:
        resp = notion.pages.create(
            parent={'database_id': contacts_db},
            properties=properties
        )
        return resp['id']
    except APIResponseError as e:
        print(f"  Error creating contact: {e}", file=sys.stderr)
        return None


def link_contact_to_game(notion, game_id, contact_id):
    """Link a contact to a game."""
    try:
        notion.pages.update(
            page_id=game_id,
            properties={
                'Contact': {'relation': [{'id': contact_id}]}
            }
        )
        return True
    except APIResponseError as e:
        print(f"  Error linking contact to game {game_id}: {e}", file=sys.stderr)
        return False


def backfill(dry_run=False, link_only=False, school_filter=None):
    """Main backfill logic."""
    notion = get_notion()
    db = get_db_ids()

    print("Fetching unlinked games...", file=sys.stderr)
    games = get_unlinked_games(notion, db['games'])
    print(f"Found {len(games)} games without contacts", file=sys.stderr)

    # Group by (away_school_id, sport)
    groups = defaultdict(list)
    for game in games:
        props = game['properties']
        away_rel = props.get('Away Team', {}).get('relation', [])
        if not away_rel:
            continue
        away_id = away_rel[0]['id']
        sport_sel = props.get('Sport', {}).get('select', {})
        sport = sport_sel.get('name', '') if sport_sel else ''
        if not sport:
            continue

        # Get gender from game properties if available
        gender_sel = props.get('Gender', {}).get('select', {})
        gender = gender_sel.get('name', '') if gender_sel else ''

        groups[(away_id, sport)].append({
            'game_id': game['id'],
            'gender': gender,
        })

    print(f"Grouped into {len(groups)} (school, sport) combos", file=sys.stderr)

    # Resolve school names
    school_names = {}
    for (school_id, sport) in groups:
        if school_id not in school_names:
            school_names[school_id] = get_school_name(notion, school_id)

    # Apply school filter
    if school_filter:
        school_filter_lower = school_filter.lower()
        groups = {
            k: v for k, v in groups.items()
            if school_filter_lower in school_names.get(k[0], '').lower()
        }
        print(f"Filtered to {len(groups)} combos matching '{school_filter}'", file=sys.stderr)

    stats = {
        'combos': len(groups),
        'already_linked': 0,
        'scraped': 0,
        'scrape_failed': 0,
        'contacts_created': 0,
        'games_linked': 0,
        'no_url': 0,
        'no_email': 0,
    }

    sorted_groups = sorted(
        groups.items(),
        key=lambda x: len(x[1]),
        reverse=True
    )

    for (school_id, sport), game_list in sorted_groups:
        school_name = school_names.get(school_id, '?')
        game_count = len(game_list)
        gender = game_list[0].get('gender', '')

        print(f"\n{'='*60}", file=sys.stderr)
        print(f"{school_name} — {sport} ({game_count} games)", file=sys.stderr)

        # Step 1: Check for existing Notion contact
        existing = find_existing_contact(notion, db['contacts'], school_id, sport)
        if existing:
            print(f"  Found existing contact in Notion", file=sys.stderr)
            if dry_run:
                print(f"  [DRY RUN] Would link to {game_count} games", file=sys.stderr)
                stats['already_linked'] += game_count
            else:
                for g in game_list:
                    if link_contact_to_game(notion, g['game_id'], existing):
                        stats['games_linked'] += 1
                print(f"  Linked to {game_count} games", file=sys.stderr)
            continue

        if link_only:
            print(f"  No existing contact (--link-only, skipping scrape)", file=sys.stderr)
            continue

        # Step 2: Discover athletics URL
        url_result = discover_athletics_url(school_name)
        athletics_url = url_result.get('athletics_url')
        if not athletics_url:
            print(f"  No athletics URL found", file=sys.stderr)
            stats['no_url'] += 1
            continue

        print(f"  URL: {athletics_url}", file=sys.stderr)

        if dry_run:
            print(f"  [DRY RUN] Would scrape and link to {game_count} games", file=sys.stderr)
            stats['scraped'] += 1
            continue

        # Step 3: Scrape staff
        print(f"  Scraping coaches...", file=sys.stderr)
        staff = scrape_staff_for_sport(athletics_url, sport, school_name, gender=gender)

        if not staff:
            print(f"  No staff found", file=sys.stderr)
            stats['scrape_failed'] += 1
            time.sleep(1)
            continue

        stats['scraped'] += 1
        print(f"  Found {len(staff)} staff", file=sys.stderr)

        # Step 4: Pick best contact
        best = pick_best_contact(staff)
        if not best:
            print(f"  No staff with email found", file=sys.stderr)
            stats['no_email'] += 1
            continue

        print(f"  Best: {best.get('name')} ({best.get('title')}) — {best.get('email')}", file=sys.stderr)

        # Step 5: Create contact in Notion
        contact_id = create_notion_contact(
            notion, db['contacts'], school_id, best, sport
        )
        if not contact_id:
            print(f"  Failed to create contact", file=sys.stderr)
            continue

        stats['contacts_created'] += 1
        print(f"  Contact created: {contact_id}", file=sys.stderr)

        # Step 6: Link to all games
        for g in game_list:
            if link_contact_to_game(notion, g['game_id'], contact_id):
                stats['games_linked'] += 1

        print(f"  Linked to {game_count} games", file=sys.stderr)

        # Rate limit between scrapes
        time.sleep(2)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Backfill missing contacts for games")
    parser.add_argument('--dry-run', action='store_true', help="Show what would be done")
    parser.add_argument('--link-only', action='store_true', help="Only link existing contacts, no scraping")
    parser.add_argument('--school', help="Filter to specific school name")
    args = parser.parse_args()

    print(f"{'='*60}", file=sys.stderr)
    print(f"CONTACT BACKFILL {'(DRY RUN)' if args.dry_run else ''}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    stats = backfill(
        dry_run=args.dry_run,
        link_only=args.link_only,
        school_filter=args.school
    )

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"RESULTS", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"School/sport combos: {stats['combos']}", file=sys.stderr)
    print(f"Staff scraped: {stats['scraped']}", file=sys.stderr)
    print(f"Scrape failures: {stats['scrape_failed']}", file=sys.stderr)
    print(f"No URL available: {stats['no_url']}", file=sys.stderr)
    print(f"No email found: {stats['no_email']}", file=sys.stderr)
    print(f"Contacts created: {stats['contacts_created']}", file=sys.stderr)
    print(f"Games linked: {stats['games_linked']}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    print(json.dumps(stats, indent=2))


if __name__ == '__main__':
    main()
