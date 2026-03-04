#!/usr/bin/env python3
"""
Debug script to test Tufts schedule parsing
"""

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

def debug_tufts_parsing():
    """Test parsing of Tufts baseball schedule."""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # Navigate to schedule
        schedule_url = "https://gotuftsjumbos.com/sports/baseball/schedule"
        print(f"Loading {schedule_url}")

        try:
            page.goto(schedule_url, wait_until='networkidle', timeout=60000)
            print("Page loaded")
        except:
            page.goto(schedule_url, wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(5000)
            print("Page loaded (fallback)")

        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, 'html.parser')

    # Test different selectors
    selectors = [
        '.s-game-card',
        '.sidearm-schedule-game-row',
        '.sidearm-schedule-game',
        '.schedule-game',
        'li.sidearm-schedule-game-item',
        '.game-item',
    ]

    print("\n=== Testing Selectors ===")
    for selector in selectors:
        found = soup.select(selector)
        print(f"{selector}: {len(found)} elements")
        if found and len(found) > 0:
            print(f"  First element preview:")
            print(f"  {found[0].get_text(strip=True)[:200]}...")
            print()

    # Check for sidearm-schedule-game-row specifically
    rows = soup.select('.sidearm-schedule-game-row')
    print(f"\n=== Analyzing .sidearm-schedule-game-row ({len(rows)} found) ===")

    if rows:
        for i, row in enumerate(rows[:3]):  # Show first 3 games
            print(f"\n--- Game {i+1} ---")

            # Date
            date_elem = row.select_one('.sidearm-schedule-game-opponent-date')
            if date_elem:
                date_spans = date_elem.find_all('span')
                print(f"Date element found: {len(date_spans)} spans")
                for j, span in enumerate(date_spans):
                    print(f"  Span {j}: {span.get_text(strip=True)}")

            # Opponent
            opp_elem = row.select_one('.sidearm-schedule-game-opponent-name')
            print(f"Opponent: {opp_elem.get_text(strip=True) if opp_elem else 'NOT FOUND'}")

            # Home/Away indicator
            vs_elem = row.select_one('.sidearm-schedule-game-conference-vs')
            print(f"Home/Away indicator: {vs_elem.get_text(strip=True) if vs_elem else 'NOT FOUND'}")

            # Result
            result_elem = row.select_one('.sidearm-schedule-game-result')
            print(f"Result: {result_elem.get_text(strip=True) if result_elem else 'NOT FOUND'}")

            # Full text
            print(f"Full row text preview: {row.get_text(strip=True)[:150]}...")

if __name__ == "__main__":
    debug_tufts_parsing()
