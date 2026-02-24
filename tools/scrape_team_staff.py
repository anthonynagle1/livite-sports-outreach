#!/usr/bin/env python3
"""
Tool: scrape_team_staff.py
Purpose: Extract coaching staff from a SPECIFIC TEAM's coaches page (not general directory)

This approach solves the "Unknown sport" problem by scraping team-specific pages where
we KNOW the sport from the URL.

Usage:
    python tools/scrape_team_staff.py --team-url "https://bceagles.com/sports/baseball" \
        --sport "Baseball" --school "Boston College" --output staff.json

Output: JSON of staff with name, title, email, phone, sport (from URL)
"""

import argparse
import json
import sys
from datetime import datetime
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import os
import re

load_dotenv()


def clean_phone(phone_text):
    """Extract a clean 10-digit phone number from text, handling duplicates."""
    if not phone_text or phone_text == 'Not Found':
        return 'Not Found'
    # Deduplicate doubled phone text (e.g., "207-786-6362207-786-6362")
    # by taking the first half if both halves match
    text = phone_text.strip()
    if len(text) >= 14:  # minimum doubled: "xxx-xxxxxxx-xxxx" = 24 chars
        half = len(text) // 2
        if text[:half] == text[half:]:
            text = text[:half]
    # Only match 10-digit phone numbers: (xxx) xxx-xxxx or xxx-xxx-xxxx
    match = re.search(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', text)
    if match:
        digits = re.sub(r'\D', '', match.group())
        if len(digits) == 10:
            return match.group()
    return 'Not Found'


def find_coaches_page_url(page, team_url):
    """
    Find the coaches/staff page for a team.

    Args:
        page: Playwright page object
        team_url (str): Team page URL (e.g., https://bceagles.com/sports/baseball)

    Returns:
        str: Coaches page URL
    """
    import re

    base_url = team_url.rstrip('/')

    # Strip trailing /index from PrestoSports URLs
    if base_url.endswith('/index'):
        base_url = base_url[:-6]

    # Check if PrestoSports URL with season year (e.g., /sports/bsb/2025-26)
    # Strip the season from the URL for the coaches page
    presto_match = re.search(r'(/sports/[a-z]+)/\d{4}-\d{2}$', base_url)
    if presto_match:
        base_url = base_url[:presto_match.end(1)]

    return f"{base_url}/coaches"


def extract_staff_from_table(table, sport_name):
    """
    Extract staff from table format (used by Sidearm team pages).

    Args:
        table: BeautifulSoup table element
        sport_name (str): Sport name from URL

    Returns:
        list: List of staff dictionaries
    """
    staff_members = []
    import re

    try:
        rows = table.find_all('tr')
        if not rows:
            return staff_members

        # Get header cells
        header_cells = rows[0].find_all(['th', 'td'])
        headers = [th.get_text(strip=True).lower() for th in header_cells]

        # Find column indices from header text first
        name_col = next((i for i, h in enumerate(headers) if 'name' in h), None)
        title_col = next((i for i, h in enumerate(headers) if 'title' in h or 'position' in h), None)
        email_col = next((i for i, h in enumerate(headers) if 'email' in h or 'e-mail' in h), None)
        phone_col = next((i for i, h in enumerate(headers) if 'phone' in h), None)

        # FALLBACK: If headers are empty, check th/td 'id' attributes
        # Sidearm uses ids like: col-coaches-fullname, col-coaches-staff_title, etc.
        if name_col is None or title_col is None:
            for i, th in enumerate(header_cells):
                th_id = (th.get('id') or '').lower()
                if 'fullname' in th_id or 'name' in th_id:
                    name_col = i
                elif 'title' in th_id or 'position' in th_id:
                    title_col = i
                elif 'email' in th_id:
                    email_col = i
                elif 'phone' in th_id:
                    phone_col = i

        # FALLBACK 2: If still can't find columns, infer from data patterns
        if name_col is None and len(rows) > 1:
            data_cells = rows[1].find_all(['td', 'th'])
            for i, cell in enumerate(data_cells):
                text = cell.get_text(strip=True)
                if '@' in text or cell.find('a', href=lambda x: x and 'mailto:' in str(x)):
                    email_col = i
                elif re.search(r'\d{3}[-.\s]?\d{3}[-.\s]?\d{4}', text):
                    phone_col = i
            # Assume first non-email, non-phone column is name, second is title
            used = {email_col, phone_col}
            remaining = [i for i in range(len(data_cells)) if i not in used]
            if len(remaining) >= 2:
                name_col = remaining[0]
                title_col = remaining[1]
            elif len(remaining) == 1:
                name_col = remaining[0]

        # Parse data rows (skip header)
        for row in rows[1:]:
            cells = row.find_all(['td', 'th'])

            if not cells or len(cells) == 0:
                continue

            try:
                name = cells[name_col].get_text(strip=True) if name_col is not None and len(cells) > name_col else None
                title = cells[title_col].get_text(strip=True) if title_col is not None and len(cells) > title_col else 'Unknown'

                # Email extraction - try multiple approaches
                email = 'Not Found'

                # Approach 1: Check the designated email column
                if email_col is not None and len(cells) > email_col:
                    email_cell = cells[email_col]
                    email_link = email_cell.find('a', href=lambda x: x and x.startswith('mailto:'))
                    if email_link:
                        email = email_link.get('href', '').replace('mailto:', '').strip()
                    else:
                        email_text = email_cell.get_text(strip=True)
                        if '@' in email_text:
                            email = email_text

                # Approach 2: Search ALL cells in the row for mailto links or email text
                if email == 'Not Found':
                    for cell in cells:
                        mailto = cell.find('a', href=lambda x: x and x.startswith('mailto:'))
                        if mailto:
                            email = mailto.get('href', '').replace('mailto:', '').strip()
                            break
                        cell_text = cell.get_text(strip=True)
                        if '@' in cell_text and re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', cell_text):
                            email = cell_text
                            break

                phone = clean_phone(cells[phone_col].get_text(strip=True)) if phone_col is not None and len(cells) > phone_col else 'Not Found'

                # Also search for phone in all cells if not found
                if phone == 'Not Found':
                    for cell in cells:
                        cell_text = cell.get_text(strip=True)
                        cleaned = clean_phone(cell_text)
                        if cleaned != 'Not Found':
                            phone = cleaned
                            break

                if name and name != 'Name':  # Skip header row if it appears again
                    staff_members.append({
                        'name': name,
                        'title': title,
                        'email': email,
                        'phone': phone if phone else 'Not Found',
                        'sport': sport_name,  # Assign sport from team URL
                    })

            except (IndexError, AttributeError) as e:
                print(f"Error parsing row: {e}", file=sys.stderr)
                continue

    except Exception as e:
        print(f"Error parsing table: {str(e)}", file=sys.stderr)

    return staff_members


def extract_staff_from_presto_cards(soup, sport_name):
    """
    Extract staff from PrestoSports card-based layout.

    PrestoSports coaches pages use Bootstrap cards instead of tables:
    - Card selector: div.card.flex-fill or .coaches-content .card
    - Name: h5.card-title > a
    - Title: p.card-text.m-0
    - Email: a[href^="mailto:"] in card (may not be present)
    - Phone: small with fa-phone icon

    Args:
        soup: BeautifulSoup object
        sport_name (str): Sport name from URL

    Returns:
        list: List of staff dictionaries
    """
    import re
    staff_members = []

    # Find coach cards
    cards = soup.select('div.card.flex-fill, .coaches-content .card, .staff-content .card')
    if not cards:
        # Try broader card selection
        cards = soup.select('.card')
        # Filter to only cards that look like coach cards (have card-title with a link)
        cards = [c for c in cards if c.select_one('h5.card-title a, h4.card-title a, .card-title a')]

    for card in cards:
        try:
            # Extract name
            name_elem = card.select_one('h5.card-title a, h4.card-title a, .card-title a')
            if not name_elem:
                continue
            name = name_elem.get_text(strip=True)
            if not name:
                continue

            # Extract title
            title_elem = card.select_one('p.card-text.m-0, p.card-text:not(.text-muted)')
            title = title_elem.get_text(strip=True) if title_elem else 'Unknown'

            # Extract email
            email = 'Not Found'
            email_link = card.find('a', href=lambda x: x and x.startswith('mailto:'))
            if email_link:
                email = email_link.get('href', '').replace('mailto:', '').strip()
            else:
                # Check for email text in card
                card_text = card.get_text()
                email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', card_text)
                if email_match:
                    email = email_match.group()

            # Extract phone
            phone = 'Not Found'
            phone_elem = card.select_one('small .fa-phone, .fa-phone')
            if phone_elem:
                phone_container = phone_elem.parent
                if phone_container:
                    phone_text = phone_container.get_text(strip=True)
                    phone_match = re.search(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', phone_text)
                    if phone_match:
                        phone = phone_match.group()
            if phone == 'Not Found':
                card_text = card.get_text()
                phone_match = re.search(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', card_text)
                if phone_match:
                    phone = phone_match.group()

            # Get bio link for enrichment
            bio_url = name_elem.get('href', '')

            staff_members.append({
                'name': name,
                'title': title,
                'email': email,
                'phone': phone,
                'sport': sport_name,
                'bio_url': bio_url,
            })

        except Exception:
            continue

    return staff_members


def scrape_roster_embedded_staff(team_url, sport_name, school_name):
    """
    Fallback 1: Scrape coaching staff embedded in roster page.
    Many softball/rowing teams list coaches directly on the roster page in a
    "COACHING STAFF" section rather than having a separate /coaches page.

    Args:
        team_url (str): Team page URL
        sport_name (str): Sport name
        school_name (str): School name

    Returns:
        list: Staff members or empty list
    """
    try:
        from playwright.sync_api import sync_playwright
        from bs4 import BeautifulSoup

        print(f"  Fallback 1: Checking roster page for embedded coaching staff...", file=sys.stderr)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            # Navigate to roster page
            roster_url = f"{team_url.rstrip('/')}/roster"
            try:
                # Use networkidle to wait for JS rendering (like schedule scraper)
                page.goto(roster_url, wait_until='networkidle', timeout=60000)
            except:
                # Fallback if networkidle times out
                page.goto(roster_url, wait_until='domcontentloaded', timeout=30000)
                page.wait_for_timeout(5000)  # Extra time for JS

            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, 'html.parser')

        # Look for "COACHING STAFF" section
        staff_members = []

        # Find the coaching staff heading
        coaching_heading = soup.find(['h2', 'h3', 'div'], string=lambda text: text and 'coaching staff' in text.lower())

        if coaching_heading:
            # Find the table after this heading
            current = coaching_heading.find_next()
            while current and current.name != 'table':
                current = current.find_next()
                if current and 'support staff' in current.get_text().lower():
                    break  # Stop before support staff section

            if current and current.name == 'table':
                staff_members = extract_staff_from_table(current, sport_name)

        if staff_members:
            print(f"  Found {len(staff_members)} staff in roster's coaching section", file=sys.stderr)

        return staff_members

    except Exception as e:
        print(f"  Roster embedded staff check failed: {e}", file=sys.stderr)
        return []


def scrape_bio_pages_fallback(team_url, sport_name, school_name):
    """
    Fallback 2: Scrape coaching staff from bio pages linked from roster.
    Used when table-based scraping and roster embedded staff both find 0 staff.

    Args:
        team_url (str): Team page URL
        sport_name (str): Sport name
        school_name (str): School name

    Returns:
        list: Staff members or empty list
    """
    try:
        import subprocess

        print(f"  Fallback 2: Trying bio page scraping from roster...", file=sys.stderr)

        # Call bio page scraper - pass team_url and it will auto-discover roster
        result = subprocess.run(
            [
                'python3', 'tools/scrape_coach_bio_pages.py',
                '--team-url', team_url,
                '--sport', sport_name,
                '--school', school_name
            ],
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data.get('success'):
                staff = data.get('staff', [])
                print(f"  Fallback found {len(staff)} staff from bio pages", file=sys.stderr)
                return staff

        return []

    except Exception as e:
        print(f"  Fallback bio scraping failed: {e}", file=sys.stderr)
        return []


def enrich_emails_from_bio_pages(staff_list, coaches_url, sport_name):
    """
    For staff members missing emails, visit their bio pages to find email addresses.

    Many Sidearm sites have bio page links like /sports/mens-lacrosse/roster/coaches/name/123
    The bio page often contains the email that's not shown in the table.

    Args:
        staff_list (list): Staff members (some may have 'Not Found' emails)
        coaches_url (str): The coaches page URL
        sport_name (str): Sport name

    Returns:
        list: Updated staff list with enriched emails
    """
    import re

    # Only enrich if there are staff with missing emails
    missing = [s for s in staff_list if s.get('email', 'Not Found') == 'Not Found']
    if not missing:
        return staff_list

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            # Navigate to coaches page to find bio links
            try:
                page.goto(coaches_url, wait_until='domcontentloaded', timeout=30000)
                page.wait_for_timeout(3000)
            except Exception:
                browser.close()
                return staff_list

            html = page.content()
            soup = BeautifulSoup(html, 'html.parser')

            # Find all bio page links
            bio_links = {}
            for link in soup.find_all('a', href=True):
                href = link.get('href', '')
                text = link.get_text(strip=True)
                if '/coaches/' in href or '/staff/' in href:
                    # Map name text to URL
                    if text:
                        bio_links[text.strip()] = urljoin(coaches_url, href)

            # Visit bio pages for staff missing emails
            for staff in staff_list:
                if staff.get('email', 'Not Found') != 'Not Found':
                    continue

                name = staff.get('name', '')
                bio_url = bio_links.get(name)

                # Fuzzy match if exact match fails
                if not bio_url:
                    name_lower = name.lower().strip()
                    for link_text, url in bio_links.items():
                        if name_lower in link_text.lower() or link_text.lower() in name_lower:
                            bio_url = url
                            break

                if not bio_url:
                    continue

                try:
                    print(f"  Checking bio page for {name}: {bio_url}", file=sys.stderr)
                    page.goto(bio_url, wait_until='domcontentloaded', timeout=15000)
                    page.wait_for_timeout(2000)

                    bio_html = page.content()

                    # Search for email in the bio page HTML
                    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                    emails_found = re.findall(email_pattern, bio_html)

                    # Filter out fake/tracking emails
                    fake_domains = ['sentry.wmt.dev', 'example.com', 'domain.com',
                                    'email.com', 'sidearmstats.com', 'sidearmtech.com']
                    valid_emails = [e for e in emails_found
                                    if not any(fake in e.lower() for fake in fake_domains)]

                    if valid_emails:
                        # Prefer .edu emails
                        edu_emails = [e for e in valid_emails if '.edu' in e.lower()]
                        best_email = edu_emails[0] if edu_emails else valid_emails[0]
                        staff['email'] = best_email
                        print(f"    Found email: {best_email}", file=sys.stderr)

                    # Also check for phone if missing
                    if staff.get('phone', 'Not Found') == 'Not Found':
                        bio_text = BeautifulSoup(bio_html, 'html.parser').get_text()
                        phone_match = re.search(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', bio_text)
                        if phone_match:
                            staff['phone'] = phone_match.group()

                except Exception as e:
                    print(f"    Bio page error: {e}", file=sys.stderr)
                    continue

            browser.close()

    except Exception as e:
        print(f"  Bio enrichment failed: {e}", file=sys.stderr)

    return staff_list


def enrich_emails_from_staff_directory(staff_list, team_url, sport_name):
    """
    For staff members still missing emails after bio page enrichment,
    check the school's general staff directory for matching names.

    The staff directory (/staff-directory) often lists all athletics employees
    with emails, even when the team coaches page doesn't show them.

    Args:
        staff_list (list): Staff members (some may have 'Not Found' emails)
        team_url (str): The team page URL (used to derive base athletics URL)
        sport_name (str): Sport name

    Returns:
        list: Updated staff list with enriched emails
    """
    import re

    missing = [s for s in staff_list if s.get('email', 'Not Found') == 'Not Found']
    if not missing:
        return staff_list

    # Derive base athletics URL from team URL
    parsed = urlparse(team_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    # Common staff directory URL patterns
    directory_urls = [
        f"{base_url}/staff-directory",
        f"{base_url}/staff",
        f"{base_url}/directory",
    ]

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            directory_entries = []

            for dir_url in directory_urls:
                try:
                    page.goto(dir_url, wait_until='networkidle', timeout=30000)
                except Exception:
                    try:
                        page.goto(dir_url, wait_until='domcontentloaded', timeout=15000)
                        page.wait_for_timeout(3000)
                    except Exception:
                        continue

                html = page.content()
                soup = BeautifulSoup(html, 'html.parser')

                # Strategy 1: Sidearm .s-person-card format
                sidearm_cards = soup.select('.s-person-card')
                if sidearm_cards:
                    for card in sidearm_cards:
                        card_text = card.get_text(separator='|', strip=True)
                        parts = [pt.strip() for pt in card_text.split('|')]
                        if len(parts) >= 2:
                            name = parts[0]
                            email = 'Not Found'
                            for part in parts[2:]:
                                if '@' in part and '.' in part:
                                    email = part
                                    break
                            if email != 'Not Found':
                                directory_entries.append({'name': name, 'email': email})
                    if directory_entries:
                        break

                # Strategy 2: Table-based directory
                tables = soup.find_all('table')
                for table in tables:
                    headers = [th.get_text(strip=True).lower() for th in table.find_all('th')]
                    name_col = None
                    email_col = None
                    for i, h in enumerate(headers):
                        if 'name' in h:
                            name_col = i
                        elif 'email' in h or 'e-mail' in h:
                            email_col = i
                    if name_col is not None and email_col is not None:
                        for row in table.find_all('tr')[1:]:
                            cells = row.find_all(['td', 'th'])
                            if len(cells) > max(name_col, email_col):
                                name = cells[name_col].get_text(strip=True)
                                # Check for mailto link first
                                email_link = cells[email_col].find('a', href=lambda h: h and 'mailto:' in h)
                                if email_link:
                                    email = email_link['href'].replace('mailto:', '').strip()
                                else:
                                    cell_text = cells[email_col].get_text(strip=True)
                                    email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', cell_text)
                                    email = email_match.group() if email_match else None
                                if name and email:
                                    directory_entries.append({'name': name, 'email': email})
                if directory_entries:
                    break

                # Strategy 3: Any mailto links paired with nearby text
                if not directory_entries:
                    mailto_links = soup.select('a[href^="mailto:"]')
                    for link in mailto_links:
                        email = link['href'].replace('mailto:', '').strip()
                        # Look for name in parent or sibling elements
                        parent = link.find_parent(['div', 'li', 'tr', 'td', 'p'])
                        if parent:
                            parent_text = parent.get_text(strip=True)
                            # Remove the email from text to get the name
                            name_text = parent_text.replace(email, '').replace(link.get_text(strip=True), '').strip(' ,-|')
                            if name_text and len(name_text) > 3:
                                directory_entries.append({'name': name_text, 'email': email})
                    if directory_entries:
                        break

            browser.close()

            if not directory_entries:
                print(f"  No staff directory entries found", file=sys.stderr)
                return staff_list

            print(f"  Found {len(directory_entries)} directory entries, matching names...", file=sys.stderr)

            # Match missing-email staff to directory entries
            matched = 0
            for staff in staff_list:
                if staff.get('email', 'Not Found') != 'Not Found':
                    continue

                name = staff.get('name', '').strip()
                name_lower = name.lower()
                # Strip year suffixes like "'13", "'19"
                name_clean = re.sub(r"\s*'\d{2}$", '', name_lower).strip()

                best_match = None
                for entry in directory_entries:
                    entry_name = entry['name'].strip().lower()
                    entry_clean = re.sub(r"\s*'\d{2}$", '', entry_name).strip()

                    # Exact match
                    if name_clean == entry_clean:
                        best_match = entry
                        break
                    # Substring containment (handles "John Smith" matching "John A. Smith")
                    if name_clean in entry_clean or entry_clean in name_clean:
                        best_match = entry
                        break
                    # Last name + first initial match
                    name_parts = name_clean.split()
                    entry_parts = entry_clean.split()
                    if len(name_parts) >= 2 and len(entry_parts) >= 2:
                        if name_parts[-1] == entry_parts[-1] and name_parts[0][0] == entry_parts[0][0]:
                            best_match = entry
                            break

                if best_match:
                    staff['email'] = best_match['email']
                    matched += 1
                    print(f"    Directory match: {name} → {best_match['email']}", file=sys.stderr)

            print(f"  Matched {matched} emails from staff directory", file=sys.stderr)

    except Exception as e:
        print(f"  Staff directory enrichment failed: {e}", file=sys.stderr)

    return staff_list


def scrape_team_staff(team_url, sport_name, school_name):
    """
    Main function to scrape team-specific coaching staff.

    Tries two approaches:
    1. Table-based scraping from /coaches page (fast, works for most schools)
    2. Bio page scraping from /roster page (fallback for Georgia Tech-style sites)

    Args:
        team_url (str): Team page URL
        sport_name (str): Sport name (e.g., "Baseball")
        school_name (str): School name

    Returns:
        dict: Result with staff list and metadata
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            # Find coaches page URL
            coaches_url = find_coaches_page_url(page, team_url)
            print(f"Navigating to coaches page: {coaches_url}", file=sys.stderr)

            # Navigate to coaches page (use networkidle for JS rendering)
            try:
                page.goto(coaches_url, wait_until='networkidle', timeout=60000)
                print("Page loaded (networkidle)", file=sys.stderr)
            except Exception as e:
                print(f"networkidle timeout, falling back: {e}", file=sys.stderr)
                try:
                    page.goto(coaches_url, wait_until='domcontentloaded', timeout=30000)
                    page.wait_for_timeout(5000)
                    print("Page loaded (domcontentloaded + wait)", file=sys.stderr)
                except Exception as e2:
                    print(f"Error loading page: {e2}", file=sys.stderr)
                    page.goto(coaches_url, wait_until='commit', timeout=30000)
                    page.wait_for_timeout(5000)

            # Get page content
            html_content = page.content()
            soup = BeautifulSoup(html_content, 'html.parser')

            # Close browser
            browser.close()

        # Extract staff from tables
        staff_members = []
        tables = soup.find_all('table')

        print(f"Found {len(tables)} tables", file=sys.stderr)

        for table in tables:
            # Check if it's a staff table - multiple detection strategies
            first_row = table.find('tr')
            is_staff_table = False
            if first_row:
                headers_text = first_row.get_text(strip=True).lower()
                # Strategy 1: Header text contains staff-related keywords
                if any(keyword in headers_text for keyword in ['name', 'title', 'email', 'coach', 'e-mail']):
                    is_staff_table = True
                # Strategy 2: Check th/td id attributes (Sidearm empty headers)
                if not is_staff_table:
                    header_ids = ' '.join((th.get('id') or '') for th in first_row.find_all(['th', 'td']))
                    if any(kw in header_ids.lower() for kw in ['fullname', 'staff_title', 'staff_email', 'coaches']):
                        is_staff_table = True
                # Strategy 3: Check for sidearm-table class
                if not is_staff_table:
                    table_classes = ' '.join(table.get('class', []))
                    if 'sidearm-table' in table_classes:
                        is_staff_table = True
                # Strategy 4: Caption text
                if not is_staff_table:
                    caption = table.find('caption')
                    if caption and 'staff' in caption.get_text(strip=True).lower():
                        is_staff_table = True

            if is_staff_table:
                table_staff = extract_staff_from_table(table, sport_name)
                staff_members.extend(table_staff)
                print(f"Extracted {len(table_staff)} staff from table", file=sys.stderr)

        # If no staff found from tables, try PrestoSports card-based layout
        if len(staff_members) == 0:
            print("No staff in tables, trying PrestoSports card layout...", file=sys.stderr)
            card_staff = extract_staff_from_presto_cards(soup, sport_name)
            if card_staff:
                print(f"Found {len(card_staff)} staff from PrestoSports cards", file=sys.stderr)
                staff_members.extend(card_staff)

        # Remove duplicates
        unique_staff = {}
        for staff in staff_members:
            if staff['name'] not in unique_staff:
                unique_staff[staff['name']] = staff

        staff_list = list(unique_staff.values())

        # ENRICHMENT: If we have staff but many lack emails, try bio page links
        missing_emails = sum(1 for s in staff_list if s.get('email', 'Not Found') == 'Not Found')
        if staff_list and missing_emails > 0:
            print(f"{missing_emails}/{len(staff_list)} staff missing emails, checking bio pages...", file=sys.stderr)
            staff_list = enrich_emails_from_bio_pages(staff_list, coaches_url, sport_name)

        # ENRICHMENT 2: If still missing emails, check school staff directory
        still_missing = sum(1 for s in staff_list if s.get('email', 'Not Found') == 'Not Found')
        if staff_list and still_missing > 0:
            print(f"  Still {still_missing} staff missing emails, checking staff directory...", file=sys.stderr)
            staff_list = enrich_emails_from_staff_directory(staff_list, team_url, sport_name)

        # FALLBACK 1: If no staff found via tables, check roster page for embedded coaching section
        if len(staff_list) == 0:
            print("No staff found in tables, checking roster page...", file=sys.stderr)
            roster_staff = scrape_roster_embedded_staff(team_url, sport_name, school_name)
            staff_list.extend(roster_staff)

        # FALLBACK 2: If still no staff, try bio page scraping
        if len(staff_list) == 0:
            print("No staff found in roster, trying bio page fallback...", file=sys.stderr)
            bio_staff = scrape_bio_pages_fallback(team_url, sport_name, school_name)
            staff_list.extend(bio_staff)

        result = {
            "school": school_name,
            "sport": sport_name,
            "coaches_url": coaches_url,
            "staff_found": len(staff_list),
            "staff": staff_list,
            "success": True,
            "timestamp": str(datetime.now()),
        }

        return result

    except PlaywrightTimeout:
        return {
            "school": school_name,
            "sport": sport_name,
            "error": "Timeout loading coaches page",
            "success": False,
        }
    except Exception as e:
        return {
            "school": school_name,
            "sport": sport_name,
            "error": f"Error scraping team staff: {str(e)}",
            "success": False,
        }


def main():
    parser = argparse.ArgumentParser(
        description="Scrape coaching staff from team-specific coaches page"
    )
    parser.add_argument(
        "--team-url",
        required=True,
        help="Team page URL (e.g., https://bceagles.com/sports/baseball)"
    )
    parser.add_argument(
        "--sport",
        required=True,
        help="Sport name (e.g., 'Baseball')"
    )
    parser.add_argument(
        "--school",
        required=True,
        help="School name (e.g., 'Boston College')"
    )
    parser.add_argument(
        "--output",
        help="Output JSON file path (default: stdout)"
    )

    args = parser.parse_args()

    # Scrape team staff
    result = scrape_team_staff(args.team_url, args.sport, args.school)

    # Save to file if specified
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Results saved to {args.output}", file=sys.stderr)

        if result['success']:
            print(f"Found {result['staff_found']} staff members for {args.sport}",
                  file=sys.stderr)

    # Always output JSON to stdout for pipeline processing
    print(json.dumps(result, indent=2))

    # Exit with error code if scraping failed
    if not result['success']:
        sys.exit(1)


if __name__ == "__main__":
    main()
