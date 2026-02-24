#!/usr/bin/env python3
"""
Debug script to save rendered HTML from schedule page.
"""
from playwright.sync_api import sync_playwright

url = "https://bceagles.com/sports/baseball/schedule"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    print(f"Loading {url}...")
    try:
        page.goto(url, wait_until='networkidle', timeout=60000)
        print("Loaded with networkidle")
    except:
        print("networkidle timeout, using domcontentloaded")
        page.goto(url, wait_until='domcontentloaded', timeout=30000)
        page.wait_for_timeout(5000)

    # Save rendered HTML
    html = page.content()
    with open('.tmp/raw_scrapes/bc_baseball_rendered.html', 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"Saved rendered HTML ({len(html)} chars)")

    # Check for schedule indicators
    if 'vs' in html.lower() or 'sidearm-schedule' in html.lower():
        print("Found schedule content in HTML!")
    else:
        print("No schedule content found")

    browser.close()
