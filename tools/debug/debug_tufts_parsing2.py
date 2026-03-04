#!/usr/bin/env python3
"""
Debug script to test full parsing flow including date filtering
"""

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from datetime import datetime
import sys
import os

# Add tools directory to path to import parsing functions
sys.path.insert(0, os.path.dirname(__file__))

def get_academic_year_range():
    """Get current academic year date range."""
    today = datetime.now()
    current_year = today.year

    # Academic year runs Aug 1 - May 31
    if today.month >= 8:  # Aug-Dec (fall semester)
        start_date = datetime(current_year, 8, 1)
        end_date = datetime(current_year + 1, 5, 31)
    else:  # Jan-July (spring semester or summer)
        start_date = datetime(current_year - 1, 8, 1)
        end_date = datetime(current_year, 5, 31)

    return start_date, end_date

def parse_game_date(date_text, time_text=None):
    """Parse date text."""
    import re

    # Clean date text - remove day of week in parentheses
    date_text = re.sub(r'\s*\([^)]+\)', '', date_text).strip()

    # Common date formats
    date_formats_no_year = [
        "%b %d",    # "Feb 28"
        "%B %d",    # "February 28"
    ]

    for fmt in date_formats_no_year:
        try:
            parsed_date = datetime.strptime(date_text, fmt)

            # Infer year
            today = datetime.now()
            current_year = today.year

            if parsed_date.month >= 8:  # Aug-Dec
                if today.month < 8:
                    year = current_year
                else:
                    year = current_year
            else:  # Jan-July
                if today.month >= 8:
                    year = current_year + 1
                else:
                    year = current_year

            parsed_date = parsed_date.replace(year=year)
            return parsed_date

        except ValueError:
            continue

    return None

def debug_full_parsing():
    """Test complete parsing flow."""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        schedule_url = "https://gotuftsjumbos.com/sports/baseball/schedule"
        print(f"Loading {schedule_url}")

        try:
            page.goto(schedule_url, wait_until='networkidle', timeout=60000)
        except:
            page.goto(schedule_url, wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(5000)

        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, 'html.parser')

    # Parse games
    rows = soup.select('.sidearm-schedule-game-row')
    print(f"\nFound {len(rows)} game rows\n")

    start_date, end_date = get_academic_year_range()
    today = datetime.now()

    print(f"Academic year: {start_date.date()} to {end_date.date()}")
    print(f"Today: {today.date()}\n")

    games_parsed = 0
    games_home = 0
    games_in_range = 0

    for i, card in enumerate(rows[:10]):  # Test first 10
        print(f"--- Game {i+1} ---")

        # Extract data
        date_elem = card.select_one('.sidearm-schedule-game-opponent-date')
        opponent_elem = card.select_one('.sidearm-schedule-game-opponent-name')
        vs_elem = card.select_one('.sidearm-schedule-game-conference-vs')
        result_elem = card.select_one('.sidearm-schedule-game-result')

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

        opponent_text = opponent_elem.get_text(strip=True) if opponent_elem else ''
        vs_text = vs_elem.get_text(strip=True) if vs_elem else ''
        result_text = result_elem.get_text(strip=True) if result_elem else ''

        is_home = vs_text.lower() == 'vs' or 'vs' in card.get_text().lower()

        print(f"Date: {date_text}")
        print(f"Time: {time_text}")
        print(f"Opponent: {opponent_text}")
        print(f"Home/Away: {vs_text} (is_home={is_home})")
        print(f"Result: {result_text if result_text else 'None'}")

        if opponent_text:
            games_parsed += 1

        if is_home:
            games_home += 1

        # Check date filtering
        if date_text:
            game_date = parse_game_date(date_text, time_text)
            if game_date:
                print(f"Parsed date: {game_date.date()}")
                in_range = start_date <= game_date <= end_date
                in_future = game_date >= today
                print(f"In academic year: {in_range}")
                print(f"In future: {in_future}")

                if in_range and in_future and is_home:
                    games_in_range += 1
                    print("✓ WOULD BE INCLUDED")
                else:
                    reasons = []
                    if not in_range:
                        reasons.append("not in academic year")
                    if not in_future:
                        reasons.append("not in future")
                    if not is_home:
                        reasons.append("not home game")
                    print(f"✗ EXCLUDED: {', '.join(reasons)}")

        print()

    print(f"\n=== SUMMARY ===")
    print(f"Games parsed: {games_parsed}")
    print(f"Home games: {games_home}")
    print(f"Games that would be included: {games_in_range}")

if __name__ == "__main__":
    debug_full_parsing()
