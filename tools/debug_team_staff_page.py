#!/usr/bin/env python3
"""Debug team-specific staff pages to see if they have better sport assignment"""
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import sys

# Test URLs for BC Baseball and Merrimack Baseball
test_urls = [
    ("BC Baseball", "https://bceagles.com/sports/baseball/coaches"),
    ("BC Men's Soccer", "https://bceagles.com/sports/mens-soccer/coaches"),
    ("Merrimack Baseball", "https://merrimackathletics.com/sports/baseball/coaches"),
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    for name, url in test_urls:
        print(f"\n{'='*60}")
        print(f"Testing: {name}")
        print(f"URL: {url}")
        print('='*60)

        try:
            # Try to load the page
            print("Loading page...")
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(3000)

            html = page.content()
            soup = BeautifulSoup(html, 'html.parser')

            # Look for staff cards
            person_cards = soup.select('.s-person-card')
            print(f"Found {len(person_cards)} .s-person-card elements")

            if person_cards:
                print("\nFirst 3 staff members:")
                for i, card in enumerate(person_cards[:3], 1):
                    card_text = card.get_text(separator='|', strip=True)
                    parts = [p.strip() for p in card_text.split('|')]
                    name = parts[0] if parts else "Unknown"
                    title = parts[1] if len(parts) > 1 else "Unknown"
                    print(f"  {i}. {name} - {title}")

            # Also check for other common staff selectors
            other_selectors = [
                '.staff-member',
                '.coach',
                '.bio-card',
                'div[class*="staff"]',
                'div[class*="coach"]'
            ]

            for selector in other_selectors:
                elements = soup.select(selector)
                if elements:
                    print(f"\nFound {len(elements)} elements with selector: {selector}")

                    # Try to extract data from first few elements
                    print("Sample staff data:")
                    for i, elem in enumerate(elements[:3], 1):
                        # Try to find name
                        name = None
                        name_elem = elem.select_one('.sidearm-roster-player-name, h3, h4, .name')
                        if name_elem:
                            name = name_elem.get_text(strip=True)

                        # Try to find title
                        title = None
                        title_elem = elem.select_one('.sidearm-roster-player-position, .title, .position')
                        if title_elem:
                            title = title_elem.get_text(strip=True)

                        # Try to find email
                        email = None
                        email_link = elem.select_one('a[href^="mailto:"]')
                        if email_link:
                            email = email_link.get('href', '').replace('mailto:', '')

                        print(f"  {i}. Name: {name}, Title: {title}, Email: {email}")

                    # Save HTML sample for inspection
                    if elements:
                        sample_file = f".tmp/raw_scrapes/{name.replace(' ', '_')}_staff_sample.html"
                        with open(sample_file, 'w', encoding='utf-8') as f:
                            f.write(str(elements[0].prettify()))
                        print(f"  Saved sample HTML to: {sample_file}")

        except Exception as e:
            print(f"Error loading {name}: {str(e)}")

    browser.close()

print("\n" + "="*60)
print("Analysis complete")
print("="*60)
