#!/usr/bin/env python3
"""Test corrected URL patterns"""
import sys
from playwright.sync_api import sync_playwright

def test_url(url):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            response = page.goto(url, wait_until='domcontentloaded', timeout=10000)
            status = response.status if response else None
            browser.close()
            return status == 200
    except:
        return False

# Test corrected URLs
tests = [
    ("Eastern CT (corrected)", "https://gowarriorathletics.com/sports/baseball/roster"),
    ("Eastern CT (no roster)", "https://gowarriorathletics.com/sports/baseball"),
    ("Johnson & Wales (prov)", "https://providence.jwuathletics.com/sports/baseball/roster"),
    ("Johnson & Wales (prov no roster)", "https://providence.jwuathletics.com/sports/baseball"),
    ("Russell Sage (no roster)", "https://sagegators.com/sports/baseball"),
    ("Wheaton (corrected)", "https://wheatoncollegelyons.com/sports/baseball/roster"),
]

print("\nTesting corrected URLs:\n")
for name, url in tests:
    works = test_url(url)
    status = "✓ WORKS" if works else "✗ BROKEN"
    print(f"{status}: {name}")
    print(f"       {url}\n")
