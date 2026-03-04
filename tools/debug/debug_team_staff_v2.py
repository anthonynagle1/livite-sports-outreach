#!/usr/bin/env python3
"""Debug team-specific staff pages - save full HTML and analyze structure"""
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import sys
import os

# Test URLs
test_url = "https://bceagles.com/sports/baseball/coaches"
output_file = ".tmp/raw_scrapes/bc_baseball_coaches.html"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    print(f"Loading: {test_url}")
    page.goto(test_url, wait_until='domcontentloaded', timeout=30000)
    page.wait_for_timeout(3000)

    html = page.content()
    browser.close()

# Save full HTML
os.makedirs(os.path.dirname(output_file), exist_ok=True)
with open(output_file, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"Saved {len(html)} chars to {output_file}")

# Parse and analyze
soup = BeautifulSoup(html, 'html.parser')

# Look for all divs with "coach" in class
coach_divs = soup.select('div[class*="coach"]')
print(f"\nFound {len(coach_divs)} divs with 'coach' in class name")

# Print all unique class names containing "coach"
all_coach_classes = set()
for div in coach_divs:
    classes = div.get('class', [])
    for cls in classes:
        if 'coach' in cls.lower():
            all_coach_classes.add(cls)

print(f"\nUnique coach-related classes:")
for cls in sorted(all_coach_classes):
    print(f"  .{cls}")

# Look for roster-related classes (Sidearm often uses "roster" for staff)
roster_divs = soup.select('div[class*="roster"]')
print(f"\nFound {len(roster_divs)} divs with 'roster' in class name")

# Print first coach div HTML for inspection
if coach_divs:
    print(f"\n{'='*60}")
    print("First coach div HTML:")
    print('='*60)
    print(coach_divs[0].prettify()[:1000])  # First 1000 chars

# Look for links and emails
all_links = soup.select('a[href^="mailto:"]')
print(f"\n{'='*60}")
print(f"Found {len(all_links)} mailto links")
print('='*60)
for i, link in enumerate(all_links[:10], 1):
    email = link.get('href', '').replace('mailto:', '')
    context = link.parent.get_text(strip=True)[:100] if link.parent else ""
    print(f"{i}. {email}")
    print(f"   Context: {context}")
