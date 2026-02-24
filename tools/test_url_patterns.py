#!/usr/bin/env python3
"""
Test different URL patterns to find what actually works
"""

import sys
from playwright.sync_api import sync_playwright

def test_url(url, timeout=10):
    """Quick URL test"""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            response = page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
            status = response.status if response else None
            browser.close()
            return status == 200
    except Exception as e:
        return False

# Test schools with different URL patterns
schools = [
    ('Wheaton College', 'https://wheatoncollegelyons.com', 'baseball'),
    ('Eastern Connecticut State', 'https://ecsusports.com', 'baseball'),
    ('Russell Sage', 'https://sagegators.com', 'baseball'),
    ('Johnson & Wales', 'https://jwuwildcats.com', 'baseball'),
]

patterns = [
    '/sports/{sport}/roster',
    '/sports/{sport}',
    '/sports/m-{sport}/roster',  # Some schools use m- prefix
    '/sports/m{sport}/roster',    # Some use msport
]

print("\nTesting URL patterns:\n")
for school_name, base_url, sport in schools:
    print(f"=== {school_name} ===")
    print(f"Base: {base_url}")

    for pattern in patterns:
        path = pattern.format(sport=sport)
        url = base_url + path
        works = test_url(url)
        status = "✓ WORKS" if works else "✗ BROKEN"
        print(f"  {status}: {path}")

    print()
