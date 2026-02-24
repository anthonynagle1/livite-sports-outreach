#!/usr/bin/env python3
"""Debug team staff pages with proper JS rendering wait"""
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import sys

test_url = "https://bceagles.com/sports/baseball/coaches"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    print(f"Loading: {test_url}")

    # Try networkidle like we do for schedules
    try:
        page.goto(test_url, wait_until='networkidle', timeout=60000)
        print("Loaded with networkidle wait")
    except Exception as e:
        print(f"networkidle failed: {e}")
        page.goto(test_url, wait_until='domcontentloaded', timeout=30000)
        page.wait_for_timeout(5000)
        print("Loaded with domcontentloaded + 5s wait")

    # Additional wait for content
    page.wait_for_timeout(3000)

    html = page.content()
    browser.close()

print(f"Got {len(html)} chars of HTML\n")

soup = BeautifulSoup(html, 'html.parser')

# Look for sidearm-roster-player cards (common in Sidearm)
roster_players = soup.select('.sidearm-roster-player')
print(f"Found {len(roster_players)} .sidearm-roster-player elements")

if roster_players:
    print("\nStaff members:")
    for i, player in enumerate(roster_players, 1):
        # Get name
        name_elem = player.select_one('.sidearm-roster-player-name')
        name = name_elem.get_text(strip=True) if name_elem else "Unknown"

        # Get position/title
        position_elem = player.select_one('.sidearm-roster-player-position')
        position = position_elem.get_text(strip=True) if position_elem else "Unknown"

        # Get email
        email_elem = player.select_one('a[href^="mailto:"]')
        email = email_elem.get('href', '').replace('mailto:', '') if email_elem else "Not Found"

        # Get phone
        phone_elem = player.select_one('a[href^="tel:"]')
        phone = phone_elem.get_text(strip=True) if phone_elem else "Not Found"

        print(f"\n{i}. {name}")
        print(f"   Position: {position}")
        print(f"   Email: {email}")
        print(f"   Phone: {phone}")

# Look for s-person-card format (like staff directory)
person_cards = soup.select('.s-person-card')
print(f"\n\nFound {len(person_cards)} .s-person-card elements")

if person_cards:
    print("\nPerson cards:")
    for i, card in enumerate(person_cards[:5], 1):
        card_text = card.get_text(separator='|', strip=True)
        parts = [p.strip() for p in card_text.split('|')]
        print(f"{i}. {' | '.join(parts[:5])}")  # First 5 parts

# Save HTML for inspection
output_file = ".tmp/raw_scrapes/bc_baseball_coaches_networkidle.html"
with open(output_file, 'w', encoding='utf-8') as f:
    f.write(html)
print(f"\nSaved to: {output_file}")
