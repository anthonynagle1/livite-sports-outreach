#!/usr/bin/env python3
"""Debug staff directory HTML structure"""
from playwright.sync_api import sync_playwright

url = "https://bceagles.com/staff-directory"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    print(f"Loading {url}...")
    page.goto(url, wait_until='domcontentloaded', timeout=30000)
    page.wait_for_timeout(3000)

    html = page.content()

    # Save to file
    with open('.tmp/raw_scrapes/bc_staff_rendered.html', 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"Saved {len(html)} chars")

    # Check for staff indicators
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')

    # Look for common staff-related classes
    all_classes = set()
    for elem in soup.find_all(class_=True):
        for cls in elem.get('class', []):
            if any(word in cls.lower() for word in ['staff', 'coach', 'bio', 'person', 'card', 'member']):
                all_classes.add(cls)

    print(f"\nStaff-related classes found: {len(all_classes)}")
    for cls in sorted(all_classes)[:20]:
        print(f"  .{cls}")

    browser.close()
