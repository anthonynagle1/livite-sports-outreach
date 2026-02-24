#!/usr/bin/env python3
"""
Tool: scrape_schedule.py
Purpose: Extract home game schedules for a team from athletics website

Usage:
    python tools/scrape_schedule.py --team-url "https://bceagles.com/sports/mens-soccer" \
        --sport "Soccer" --gender "Men" --school "Boston College"

Output: JSON list of home games with date, time, opponent, venue
Filters: Home games only, current academic year (Aug-May), future dates only
"""

import argparse
import json
import sys
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import os

load_dotenv()

# Academic year configuration
ACADEMIC_YEAR_START_MONTH = int(os.getenv('ACADEMIC_YEAR_START_MONTH', 8))  # August
ACADEMIC_YEAR_END_MONTH = int(os.getenv('ACADEMIC_YEAR_END_MONTH', 5))      # May


def get_academic_year_range():
    """
    Calculate the current academic year date range (Aug-May).

    Returns:
        tuple: (start_date, end_date) as datetime objects
    """
    today = datetime.now()
    current_year = today.year

    # If we're before August, academic year started last year
    if today.month < ACADEMIC_YEAR_START_MONTH:
        start_year = current_year - 1
        end_year = current_year
    else:
        start_year = current_year
        end_year = current_year + 1

    start_date = datetime(start_year, ACADEMIC_YEAR_START_MONTH, 1)
    # End date is last day of May
    end_date = datetime(end_year, ACADEMIC_YEAR_END_MONTH + 1, 1) - timedelta(days=1)

    return start_date, end_date


def find_schedule_page(page, team_url):
    """
    Navigate to the schedule page from a team's main page.

    Args:
        page: Playwright page object
        team_url (str): Team's main page URL

    Returns:
        str: Schedule page URL or None
    """
    # CRITICAL FIX: Don't search for "schedule" links - navigation menus have
    # links to OTHER sports' schedules. Instead, construct the URL directly
    # from the team URL, which is specific to THIS sport.

    base_url = team_url.rstrip('/')

    # Strip trailing /index from PrestoSports URLs
    if base_url.endswith('/index'):
        base_url = base_url[:-6]

    # Handle PrestoSports boxscore/release URLs (Lasell bug: team discovery returns these)
    # e.g., /sports/bsb/2025-26/boxscores/20260228_eoph.xml -> /sports/bsb/2025-26
    # e.g., /sports/mlax/2025-26/releases/20260221_4vsh -> /sports/mlax/2025-26
    boxscore_match = re.search(r'(/sports/[a-z]+/\d{4}-\d{2})/(boxscores|releases|stats|recap)/', base_url)
    if boxscore_match:
        base_url = base_url[:boxscore_match.end(1)]

    # Check if this is a PrestoSports URL pattern (e.g., /sports/bsb or /sports/bsb/2025-26)
    # PrestoSports uses short sport codes (2-5 chars): bsb, sball, mbkb, wbkb, etc.
    # Sidearm uses descriptive names: baseball, softball, mens-soccer, etc.
    presto_match = re.search(r'/sports/([a-z]+)(?:/(\d{4}-\d{2}))?$', base_url)
    # Only treat as PrestoSports if sport code is short (<=5 chars) or already has a season year
    presto_sport_codes = {'bsb', 'sball', 'mbkb', 'wbkb', 'msoc', 'wsoc', 'mlax', 'wlax',
                          'mten', 'wten', 'mvball', 'wvball', 'mih', 'wih', 'fh', 'fball',
                          'base', 'soft', 'golf', 'mgolf', 'wgolf', 'mswim', 'wswim',
                          'track', 'xc', 'mxc', 'wxc', 'wres', 'row', 'crew'}
    if presto_match:
        sport_code = presto_match.group(1)
        season = presto_match.group(2)
        if not season and sport_code not in presto_sport_codes and len(sport_code) > 5:
            # Long sport name = Sidearm, just append /schedule
            return f"{base_url}/schedule"
        if not season:
            # Infer current season (academic year format: 2025-26)
            today = datetime.now()
            if today.month >= 8:
                season = f"{today.year}-{str(today.year + 1)[-2:]}"
            else:
                season = f"{today.year - 1}-{str(today.year)[-2:]}"
            # Return PrestoSports URL with season
            parsed = urlparse(base_url)
            return f"{parsed.scheme}://{parsed.netloc}/sports/{sport_code}/{season}/schedule"

    # Default: Sidearm Sports pattern (just append /schedule)
    return f"{base_url}/schedule"


def extract_schedule_from_scripts(soup, base_url):
    """
    Extract schedule data from embedded JSON/JavaScript (for dynamically rendered pages).
    Handles both standard JSON script tags and Sidearm nextgen window.sidearmComponents.

    Args:
        soup: BeautifulSoup object
        base_url (str): Base URL

    Returns:
        list: List of game dictionaries
    """
    games = []

    # === Strategy 1: Sidearm nextgen window.sidearmComponents ===
    # Modern Sidearm sites store schedule data as JS objects in script tags
    all_scripts = soup.find_all('script')
    for script in all_scripts:
        script_text = script.string or ''
        if 'sidearmComponents' not in script_text and 'window.__NEXT_DATA__' not in script_text:
            continue

        # Try to extract JSON from window.sidearmComponents
        try:
            # Strategy: Find "events": and use bracket counting to extract the full array
            events_list = []
            search_start = 0
            while True:
                idx = script_text.find('"events":', search_start)
                if idx == -1:
                    break
                # Find the opening bracket
                bracket_start = script_text.find('[', idx)
                if bracket_start == -1:
                    break
                # Count brackets to find the end
                depth = 0
                pos = bracket_start
                while pos < len(script_text):
                    if script_text[pos] == '[':
                        depth += 1
                    elif script_text[pos] == ']':
                        depth -= 1
                        if depth == 0:
                            break
                    pos += 1
                if depth == 0:
                    array_text = script_text[bracket_start:pos+1]
                    try:
                        events_list.extend(json.loads(array_text))
                    except json.JSONDecodeError:
                        pass
                search_start = pos + 1

            for event in events_list:
                try:
                    if not isinstance(event, dict):
                        continue

                    # Sidearm nextgen format
                    opponent_data = event.get('opponent', {})
                    if isinstance(opponent_data, dict):
                        opponent = opponent_data.get('name', '')
                    elif isinstance(opponent_data, str):
                        opponent = opponent_data
                    else:
                        opponent = ''

                    date_str = event.get('date', '')
                    time_str = event.get('time', '')
                    location_indicator = event.get('location_indicator', '').upper()
                    is_home = location_indicator == 'H'

                    # Check result - skip completed games
                    result_data = event.get('result', {})
                    if isinstance(result_data, dict) and result_data.get('status'):
                        continue  # Has a result, game is completed

                    # Get facility/venue
                    facility = event.get('game_facility', {})
                    venue = ''
                    if isinstance(facility, dict):
                        venue = facility.get('title', '')
                    location = event.get('location', '')
                    if location and venue:
                        venue = f"{venue}, {location}"
                    elif location:
                        venue = location

                    if opponent and is_home:
                        # Parse ISO date format if present
                        if 'T' in date_str:
                            try:
                                dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                                date_display = dt.strftime('%b %d')
                            except:
                                date_display = date_str
                        else:
                            date_display = date_str

                        games.append({
                            'date': date_display,
                            'time': time_str,
                            'opponent': clean_opponent_name(str(opponent)),
                            'venue': venue,
                            'is_home': True,
                        })

                except (TypeError, KeyError):
                    continue

        except Exception:
            pass

    if games:
        return games

    # === Strategy 2: Standard JSON script tags ===
    script_tags = soup.find_all('script', type='application/json')

    for script in script_tags:
        try:
            script_content = script.string
            if not script_content:
                continue

            data = json.loads(script_content)

            # Look for schedule data in various possible locations
            schedule_data = None

            # Common patterns in Sidearm and other platforms
            if isinstance(data, dict):
                # Try common keys
                for key in ['schedule', 'games', 'events', 'calendar', 'items']:
                    if key in data:
                        schedule_data = data[key]
                        break

                # Nested structures
                if not schedule_data and 'data' in data:
                    for key in ['schedule', 'games', 'events']:
                        if key in data['data']:
                            schedule_data = data['data'][key]
                            break

            if schedule_data and isinstance(schedule_data, list):
                for game_data in schedule_data:
                    if not isinstance(game_data, dict):
                        continue

                    # Extract fields (names vary by platform)
                    opponent = (game_data.get('opponent') or
                               game_data.get('opponent_name') or
                               game_data.get('team') or '')

                    date = (game_data.get('date') or
                           game_data.get('game_date') or
                           game_data.get('event_date') or '')

                    time = (game_data.get('time') or
                           game_data.get('game_time') or '')

                    location = (game_data.get('location') or
                               game_data.get('venue') or
                               game_data.get('site') or '')

                    # Check if home game
                    at_vs = (game_data.get('at_vs') or
                            game_data.get('location_indicator') or
                            game_data.get('type') or '').lower()

                    is_home = at_vs in ['vs', 'home', 'h']

                    # Check result to skip completed games
                    result = game_data.get('result') or game_data.get('score') or ''

                    if opponent and is_home and not result:
                        games.append({
                            'date': str(date),
                            'time': str(time),
                            'opponent': clean_opponent_name(str(opponent)),
                            'venue': str(location),
                            'is_home': True,
                        })

        except (json.JSONDecodeError, KeyError, AttributeError):
            continue

    return games


def parse_schedule_table(soup, base_url):
    """
    Parse schedule from HTML table format.

    Args:
        soup: BeautifulSoup object
        base_url (str): Base URL for resolving relative links

    Returns:
        list: List of game dictionaries
    """
    games = []

    # Find tables that might contain schedule
    tables = soup.find_all('table')

    for table in tables:
        # Check if this looks like a schedule table
        headers = [th.get_text(strip=True).lower() for th in table.find_all('th')]

        if not any(h in headers for h in ['date', 'opponent', 'time']):
            continue

        # Find column indices
        date_col = None
        time_col = None
        opponent_col = None
        location_col = None
        result_col = None

        for i, header in enumerate(headers):
            if 'date' in header:
                date_col = i
            elif 'time' in header or header == 'status':
                time_col = i
            elif 'opponent' in header or 'team' in header:
                opponent_col = i
            elif 'location' in header or 'site' in header or 'venue' in header:
                location_col = i
            elif 'result' in header or 'score' in header:
                result_col = i

        # Parse rows
        for row in table.find_all('tr')[1:]:  # Skip header row
            cols = row.find_all(['td', 'th'])

            if len(cols) <= max(filter(None, [date_col, opponent_col])):
                continue

            try:
                # Extract game data
                date_text = cols[date_col].get_text(strip=True) if date_col is not None else ""
                time_text = cols[time_col].get_text(strip=True) if time_col is not None else ""
                opponent_text = cols[opponent_col].get_text(strip=True) if opponent_col is not None else ""
                location_text = cols[location_col].get_text(strip=True) if location_col is not None else ""

                # Skip if no opponent
                if not opponent_text:
                    continue

                # Skip completed games (if result column exists and has data)
                if result_col is not None and len(cols) > result_col:
                    result_text = cols[result_col].get_text(strip=True)
                    if result_text and any(c in result_text for c in ['W', 'L', '-']):
                        continue  # Skip completed games

                # Determine if it's a home game
                is_home = is_home_game(opponent_text, location_text, row.get_text())

                if is_home:
                    game = {
                        'date': date_text,
                        'time': time_text,
                        'opponent': clean_opponent_name(opponent_text),
                        'venue': location_text,
                        'is_home': True,
                    }
                    games.append(game)

            except (IndexError, AttributeError) as e:
                continue

    return games


def parse_schedule_list(soup, base_url):
    """
    Parse schedule from list/card format (common in modern Sidearm sites).

    Args:
        soup: BeautifulSoup object
        base_url (str): Base URL for resolving relative links

    Returns:
        list: List of game dictionaries
    """
    games = []

    # Look for game containers/cards
    game_selectors = [
        '.s-game-card',  # Modern Sidearm (BC uses this)
        'li.sidearm-schedule-game',  # Classic Sidearm <li> (Simmons etc.)
        '.sidearm-schedule-game',  # Classic Sidearm (fallback to any element)
        '.sidearm-schedule-game-row',  # Tufts Sidearm variant (inner row)
        '.schedule-game',
        'li.sidearm-schedule-game-item',
        '.game-item',
        '.schedule-item',
        '[data-game-id]',
    ]

    game_cards = []
    for selector in game_selectors:
        found = soup.select(selector)
        if found:
            game_cards = found
            break

    for card in game_cards:
        try:
            # Modern Sidearm (.s-game-card) uses pipe-separated text structure
            # Example: "vs|Merrimack|Harrington Athletics Village|Brighton, Mass.|Feb 24|(Tue)|2:00 PM|..."
            card_text = card.get_text(separator='|', strip=True)
            parts = [p.strip() for p in card_text.split('|')]

            # Check if this is a modern Sidearm card format
            if len(parts) >= 4 and (parts[0].lower() in ['vs', 'at', '@']):
                # Modern format
                at_vs_indicator = parts[0].lower()
                is_home = at_vs_indicator == 'vs'

                opponent_text = parts[1] if len(parts) > 1 else ''
                venue_text = parts[2] if len(parts) > 2 else ''
                location_text = parts[3] if len(parts) > 3 else ''

                # Find date and time (usually after location)
                date_text = ''
                time_text = ''
                for i, part in enumerate(parts[4:], start=4):
                    # Date is usually something like "Feb 24" or contains numbers
                    if any(month in part for month in ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']):
                        date_text = part
                    # Time contains ':'
                    if ':' in part and ('AM' in part.upper() or 'PM' in part.upper()):
                        time_text = part
                        break

                full_location = f"{venue_text}, {location_text}".strip(', ')

            else:
                # Fallback to traditional parsing for other formats
                opponent_elem = card.select_one('.sidearm-schedule-game-opponent-name, .opponent-name, .opponent')
                opponent_text = opponent_elem.get_text(strip=True) if opponent_elem else ''

                date_elem = card.select_one('.sidearm-schedule-game-opponent-date, .sidearm-schedule-game-date, .game-date, .date, time')
                # Tufts format has date and time in same element with spans
                if date_elem:
                    spans = date_elem.find_all('span')
                    if len(spans) >= 2:
                        date_text = spans[0].get_text(strip=True)
                        time_text = spans[1].get_text(strip=True)
                    else:
                        date_text = date_elem.get_text(strip=True)
                        time_text = ''
                else:
                    date_text = ''
                    time_text = ''

                # Fallback to separate time element if not found in date element
                if not time_text:
                    time_elem = card.select_one('.sidearm-schedule-game-time, .game-time, .time')
                    time_text = time_elem.get_text(strip=True) if time_elem else ''

                location_elem = card.select_one('.sidearm-schedule-game-location, .game-location, .location, .venue')
                location_text = location_elem.get_text(strip=True) if location_elem else ''
                full_location = location_text

                # Check for home/away
                # Classic Sidearm uses CSS classes on the card element
                card_classes = ' '.join(card.get('class', []))
                if 'sidearm-schedule-home-game' in card_classes:
                    is_home = True
                elif 'sidearm-schedule-away-game' in card_classes:
                    is_home = False
                elif 'sidearm-schedule-neutral-game' in card_classes:
                    is_home = False
                else:
                    # For Tufts/Sidearm-row format, check for vs/at indicator in specific element
                    vs_elem = card.select_one('.sidearm-schedule-game-conference-vs')
                    if vs_elem:
                        vs_text = vs_elem.get_text(strip=True).lower()
                        is_home = vs_text == 'vs'
                    else:
                        card_text_lower = card.get_text().lower()
                        at_vs = card.get('data-at-vs', '').lower()
                        if at_vs:
                            is_home = at_vs == 'vs' or at_vs == 'home'
                        else:
                            is_home = is_home_game(opponent_text, location_text, card_text_lower)

            # Skip if no opponent or if away game
            if not opponent_text or not is_home:
                continue

            # Skip completed games (look for result indicators)
            result_elem = card.select_one('.sidearm-schedule-game-result, .game-result, .result, .score')
            if result_elem:
                result_text = result_elem.get_text(strip=True)
                if result_text and any(indicator in result_text.upper() for indicator in ['W', 'L', 'T', '-']):
                    continue

            # Also check if 'W' or 'L' appears in the text
            if any(indicator in card_text.upper() for indicator in [' W ', ' L ', 'WIN', 'LOSS']):
                continue

            game = {
                'date': date_text,
                'time': time_text,
                'opponent': clean_opponent_name(opponent_text),
                'venue': full_location,
                'is_home': True,
            }
            games.append(game)

        except Exception as e:
            continue

    return games


def parse_prestosports_schedule(soup, base_url):
    """
    Parse schedule from PrestoSports sites.

    PrestoSports has multiple themes:
    - Classic div-based (e.g., Suffolk): .inner-wrap inside .event-row divs
    - Table-based (e.g., Fisher): .event-row as <tr> with month-title rows
    - Modern Bootstrap 5 (e.g., Wentworth): .card-body inside .event-row

    All use .event-row as the main container and .team-name for opponent.

    Args:
        soup: BeautifulSoup object
        base_url (str): Base URL

    Returns:
        list: List of game dictionaries
    """
    games = []

    # Find all event rows
    event_rows = soup.select('.event-row')
    if not event_rows:
        return games

    # Build month context map for table-based format (Fisher, Lasell, etc.)
    # month-title rows provide the month for subsequent event-rows
    month_titles = soup.select('tr.month-title')
    month_context = {}  # Maps event-row index to month name
    if month_titles:
        # For table-based format, build a mapping of each event-row to its month
        all_trs = soup.select('table tr')
        current_month = ''
        for tr in all_trs:
            tr_classes = ' '.join(tr.get('class', []))
            if 'month-title' in tr_classes:
                current_month = tr.get_text(strip=True)
            elif 'event-row' in tr_classes and current_month:
                # Store the month for this specific element by its id or position
                month_context[id(tr)] = current_month

    last_date_text = ''  # For doubleheader date inheritance (table format)

    for row in event_rows:
        try:
            # Skip exhibition games
            row_classes = ' '.join(row.get('class', []))
            if 'exhibition' in row_classes:
                continue

            # Determine if home game
            # Method 1: CSS class (Suffolk classic uses "home" class)
            is_home = 'home' in row_classes.split()

            # Method 2: Check vs/at text indicator
            if not is_home:
                va_elem = row.select_one('.va, .event-location-badge')
                if va_elem:
                    va_text = va_elem.get_text(strip=True).lower().rstrip('.')
                    is_home = va_text == 'vs'

            # Method 3: Check for neutral site (negates "vs" home indicator)
            neutralsite = row.select_one('.neutralsite')
            if neutralsite and is_home:
                is_home = False  # Neutral site games are not true home games

            if not is_home:
                continue  # Skip away/neutral games

            # Extract opponent name
            team_name_elem = row.select_one('.team-name')
            opponent = team_name_elem.get_text(strip=True) if team_name_elem else ''
            if not opponent:
                opponent_elem = row.select_one('.opponent, .event-opponent-name')
                opponent = opponent_elem.get_text(strip=True) if opponent_elem else ''

            if not opponent:
                continue

            # Extract date - try multiple approaches
            date_text = ''

            # Approach 1: Classic theme .date with title attribute (Suffolk)
            date_elem = row.select_one('.date')
            if date_elem:
                title_date = date_elem.get('title', '')
                if title_date:
                    date_text = title_date
                else:
                    spans = date_elem.find_all('span')
                    if spans:
                        date_text = ' '.join(s.get_text(strip=True) for s in spans)
                    else:
                        date_text = date_elem.get_text(strip=True)

            # Approach 2: Table-based .e_date with month context (Fisher)
            if not date_text:
                e_date_elem = row.select_one('.e_date')
                if e_date_elem:
                    day_text = e_date_elem.get_text(strip=True)
                    # Strip day-of-week prefix: "Sun. 8" -> "8"
                    day_num = re.sub(r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\.?\s*', '', day_text, flags=re.IGNORECASE).strip()
                    # Get month from context map
                    month_name = month_context.get(id(row), '')
                    if month_name and day_num:
                        date_text = f"{month_name} {day_num}"
                    elif day_num:
                        date_text = day_num

            # Approach 3: Modern theme .event-dateinfo
            if not date_text:
                date_info = row.select_one('.event-dateinfo .date span')
                if date_info:
                    date_text = date_info.get_text(strip=True)

            # Doubleheader date inheritance: if this row has no date, use the last one
            if date_text:
                last_date_text = date_text
            elif last_date_text:
                date_text = last_date_text

            # Extract time
            time_text = ''
            # Try multiple selectors: .status (Suffolk), .e_status (Fisher), .event-time (modern)
            time_elem = row.select_one('.status span, .status, .e_status, .event-time')
            if time_elem:
                time_text = time_elem.get_text(strip=True)
                # Remove timezone suffixes like "EST"
                time_text = re.sub(r'\s*(EST|CST|MST|PST|EDT|CDT|MDT|PDT)\s*$', '', time_text).strip()

            # Extract venue/location
            venue_text = ''
            venue_elem = row.select_one('.venue, .event-neutralsite, .location, .e_notes')
            if venue_elem:
                venue_text = venue_elem.get_text(strip=True)
                # Clean up venue prefix
                venue_text = re.sub(r'^@\s*', '', venue_text)

            # Skip completed games - check for result
            result_elem = row.select_one('.result, .event-result, .e_result')
            if result_elem:
                result_text = result_elem.get_text(strip=True)
                if result_text and any(c in result_text.upper() for c in ['W', 'L', 'T']):
                    continue
                # Also check for score patterns like "5-3"
                if re.search(r'\d+\s*-\s*\d+', result_text):
                    continue

            game = {
                'date': date_text,
                'time': time_text,
                'opponent': clean_opponent_name(opponent),
                'venue': venue_text,
                'is_home': True,
            }
            games.append(game)

        except Exception:
            continue

    return games


def is_home_game(opponent_text, location_text, full_row_text):
    """
    Determine if a game is a home game based on various indicators.

    Args:
        opponent_text (str): Opponent column text
        location_text (str): Location column text
        full_row_text (str): Full row text for additional context

    Returns:
        bool: True if home game
    """
    combined = f"{opponent_text} {location_text} {full_row_text}".lower()

    # Home game indicators
    home_indicators = [' vs ', ' vs.', 'home']

    # Away game indicators (stronger - if present, it's NOT a home game)
    away_indicators = ['@', ' at ', 'away']

    # Check for away indicators first (stronger signal)
    if any(ind in combined for ind in away_indicators):
        return False

    # Check for home indicators
    if any(ind in combined for ind in home_indicators):
        return True

    # Default to False if unclear (conservative approach)
    return False


def clean_opponent_name(opponent_text):
    """
    Clean opponent name by removing prefixes like 'vs', '@', etc.

    Args:
        opponent_text (str): Raw opponent text

    Returns:
        str: Cleaned opponent name
    """
    # Remove common prefixes
    cleaned = re.sub(r'^(vs\.?|@|at)\s*', '', opponent_text, flags=re.IGNORECASE)

    # Remove rankings (e.g., "#5 Duke" -> "Duke")
    cleaned = re.sub(r'^#?\d+\s+', '', cleaned)

    # Remove extra whitespace
    cleaned = ' '.join(cleaned.split())

    return cleaned.strip()


def parse_game_date(date_text, time_text=None):
    """
    Parse date text into datetime object.
    Handles dates with or without years (infers year based on academic calendar).

    Args:
        date_text (str): Date string (e.g., "Feb 24" or "Feb 24, 2026" or "Feb 24 (Sat)")
        time_text (str): Optional time string

    Returns:
        datetime or None: Parsed datetime
    """
    # Clean date text - remove day of week in parentheses (e.g., "Feb 28 (Sat)" -> "Feb 28")
    date_text = re.sub(r'\s*\([^)]+\)', '', date_text).strip()

    # Strip day-of-week prefix (e.g., "Sat. February 28, 2026" -> "February 28, 2026")
    date_text = re.sub(r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\.?\s+', '', date_text, flags=re.IGNORECASE).strip()

    # Common date formats in NCAA schedules
    date_formats_with_year = [
        "%m/%d/%Y",
        "%m/%d/%y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%m-%d-%Y",
        "%Y-%m-%d",
    ]

    # Try parsing with explicit year first
    for fmt in date_formats_with_year:
        try:
            parsed_date = datetime.strptime(date_text, fmt)
            return parsed_date
        except ValueError:
            continue

    # If no year in date, try formats without year and infer year
    date_formats_no_year = [
        "%b %d",    # "Feb 24"
        "%B %d",    # "February 24"
        "%m/%d",    # "2/24"
        "%m-%d",    # "2-24"
    ]

    for fmt in date_formats_no_year:
        try:
            # Parse without year (will default to 1900)
            parsed_date = datetime.strptime(date_text, fmt)

            # Infer year based on academic calendar
            # If month is Aug-Dec, use current year; if Jan-May, use next year
            today = datetime.now()
            current_year = today.year

            if parsed_date.month >= 8:  # Aug-Dec (fall semester)
                # If we're currently in spring (Jan-July), this refers to next fall
                if today.month < 8:
                    year = current_year
                else:
                    year = current_year
            else:  # Jan-July (spring semester)
                # If we're currently in fall (Aug-Dec), this refers to next spring
                if today.month >= 8:
                    year = current_year + 1
                else:
                    year = current_year

            parsed_date = parsed_date.replace(year=year)
            return parsed_date

        except ValueError:
            continue

    return None


def filter_games_by_academic_year(games):
    """
    Filter games to only include those in current academic year and future dates.

    Args:
        games (list): List of game dictionaries

    Returns:
        list: Filtered games
    """
    start_date, end_date = get_academic_year_range()
    today = datetime.now()

    filtered_games = []

    for game in games:
        # Parse game date
        game_date = parse_game_date(game['date'], game.get('time'))

        if game_date:
            # Check if in academic year range and future
            if start_date <= game_date <= end_date and game_date >= today:
                game['parsed_date'] = game_date.isoformat()
                filtered_games.append(game)

    return filtered_games


def scrape_schedule(team_url, sport, gender, school):
    """
    Main function to scrape schedule for a team.

    Args:
        team_url (str): Team page URL
        sport (str): Sport name
        gender (str): Gender (Men/Women)
        school (str): School name

    Returns:
        dict: Result with games list and metadata
    """
    try:
        with sync_playwright() as p:
            # Launch browser
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            # Navigate to team page
            print(f"Loading {team_url}...", file=sys.stderr)
            try:
                # Use networkidle for JavaScript-heavy sites (Sidearm Sports, etc.)
                # This waits for all network requests to finish (like Puppeteer's networkidle0)
                page.goto(team_url, wait_until='networkidle', timeout=60000)
            except Exception as e:
                # Fallback to domcontentloaded if networkidle times out
                print(f"networkidle timeout, trying domcontentloaded: {str(e)}", file=sys.stderr)
                page.goto(team_url, wait_until='domcontentloaded', timeout=30000)
                page.wait_for_timeout(5000)  # Give JS time to render

            # Find schedule page
            schedule_url = find_schedule_page(page, team_url)

            if schedule_url and schedule_url != team_url:
                print(f"Navigating to schedule: {schedule_url}", file=sys.stderr)
                try:
                    # Critical: Use networkidle to wait for JavaScript rendering
                    page.goto(schedule_url, wait_until='networkidle', timeout=60000)
                    print("Page fully loaded (networkidle)", file=sys.stderr)
                except Exception as e:
                    print(f"networkidle timeout, trying domcontentloaded: {str(e)}", file=sys.stderr)
                    page.goto(schedule_url, wait_until='domcontentloaded', timeout=30000)
                    page.wait_for_timeout(5000)
            else:
                schedule_url = team_url

            # Get page content (after JS rendering)
            html_content = page.content()
            soup = BeautifulSoup(html_content, 'html.parser')

            # Try to find schedule data in script tags (for JS-rendered pages)
            games = extract_schedule_from_scripts(soup, schedule_url)

            # If no games found, try parsing HTML table format
            if not games:
                games = parse_schedule_table(soup, schedule_url)

            # If still no games, try list/card format (Sidearm)
            if not games:
                games = parse_schedule_list(soup, schedule_url)

            # If still no games, try PrestoSports div-based format
            if not games:
                games = parse_prestosports_schedule(soup, schedule_url)

            # Filter for academic year and future games
            filtered_games = filter_games_by_academic_year(games)

            # Add metadata to each game
            for game in filtered_games:
                game['school'] = school
                game['sport'] = sport
                game['gender'] = gender

            # Close browser
            browser.close()

        result = {
            "school": school,
            "sport": sport,
            "gender": gender,
            "team_url": team_url,
            "schedule_url": schedule_url,
            "games_found": len(filtered_games),
            "games": filtered_games,
            "success": True,
        }

        return result

    except PlaywrightTimeout:
        return {
            "school": school,
            "sport": sport,
            "error": "Timeout loading schedule page",
            "success": False,
        }
    except Exception as e:
        return {
            "school": school,
            "sport": sport,
            "error": f"Error scraping schedule: {str(e)}",
            "success": False,
        }


def main():
    parser = argparse.ArgumentParser(
        description="Scrape team schedule from athletics website"
    )
    parser.add_argument(
        "--team-url",
        required=True,
        help="Team page URL (e.g., https://bceagles.com/sports/mens-soccer)"
    )
    parser.add_argument(
        "--sport",
        required=True,
        help="Sport name (e.g., 'Soccer')"
    )
    parser.add_argument(
        "--gender",
        required=True,
        help="Gender (Men/Women)"
    )
    parser.add_argument(
        "--school",
        required=True,
        help="School name (e.g., 'Boston College')"
    )
    parser.add_argument(
        "--output",
        help="Output JSON file path (default: stdout)"
    )

    args = parser.parse_args()

    # Scrape schedule
    result = scrape_schedule(args.team_url, args.sport, args.gender, args.school)

    # Save to file if specified
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Results saved to {args.output}", file=sys.stderr)

        if result['success']:
            print(f"Found {result['games_found']} home games for "
                  f"{args.gender}'s {args.sport}", file=sys.stderr)

    # Always output JSON to stdout for pipeline processing
    print(json.dumps(result, indent=2))

    # Exit with error code if scraping failed
    if not result['success']:
        sys.exit(1)


if __name__ == "__main__":
    main()
