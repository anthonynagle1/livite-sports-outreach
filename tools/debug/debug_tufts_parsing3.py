#!/usr/bin/env python3
"""
Debug script to test pipe-separated text parsing
"""

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

def debug_pipe_parsing():
    """Test pipe-separated parsing."""

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

    for i, card in enumerate(rows[:3]):  # Test first 3
        print(f"--- Game {i+1} ---")

        # Get pipe-separated text (like the scraper does)
        card_text = card.get_text(separator='|', strip=True)
        parts = [p.strip() for p in card_text.split('|')]

        print(f"Pipe-separated parts ({len(parts)} parts):")
        for j, part in enumerate(parts[:15]):  # Show first 15 parts
            print(f"  [{j}]: '{part}'")

        print(f"\nCheck modern Sidearm format:")
        if len(parts) >= 4:
            print(f"  parts[0] = '{parts[0]}' (vs/at check: {parts[0].lower() in ['vs', 'at', '@']})")
            print(f"  parts[1] = '{parts[1]}'")
            print(f"  parts[2] = '{parts[2]}'")
            print(f"  parts[3] = '{parts[3]}'")

        print()

if __name__ == "__main__":
    debug_pipe_parsing()
