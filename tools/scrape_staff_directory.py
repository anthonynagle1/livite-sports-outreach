#!/usr/bin/env python3
"""
Tool: scrape_staff_directory.py
Purpose: Extract ALL athletics coaching staff from a school's staff directory

Usage:
    python tools/scrape_staff_directory.py --url "https://bceagles.com" --school "Boston College"
    python tools/scrape_staff_directory.py --url "https://bceagles.com/staff-directory" \
        --school "Boston College" --output contacts.json

Output: JSON of all staff with name, title, email, phone, sport assignment
Note: Extracts ALL staff for caching, not just coaches for a specific sport
"""

import argparse
import json
import sys
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import os

load_dotenv()


def find_staff_directory_url(page, base_url):
    """
    Find the staff directory page from the athletics site.

    Args:
        page: Playwright page object
        base_url (str): Base athletics URL

    Returns:
        str: Staff directory URL or None
    """
    try:
        # Look for staff/directory links
        links = page.query_selector_all('a')

        for link in links:
            text = link.inner_text().lower().strip()
            href = link.get_attribute('href')

            if any(keyword in text for keyword in ['staff', 'coaches', 'directory', 'personnel']):
                if href:
                    full_url = urljoin(base_url, href)
                    return full_url

        # Try common URL patterns
        parsed = urlparse(base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        common_patterns = [
            f"{base}/staff-directory",
            f"{base}/staff",
            f"{base}/coaches",
            f"{base}/directory",
            f"{base}/sports/staff",
        ]

        return common_patterns[0]

    except Exception as e:
        print(f"Error finding staff directory: {str(e)}", file=sys.stderr)
        return None


def extract_staff_from_page(soup, page_url):
    """
    Extract staff members from the directory page.

    Args:
        soup: BeautifulSoup object
        page_url (str): Current page URL

    Returns:
        list: List of staff dictionaries
    """
    staff_members = []

    # Try multiple parsing strategies

    # Strategy 0: Modern Sidearm format (.s-person-card) - try this first
    sidearm_cards = soup.select('.s-person-card')
    if sidearm_cards:
        for card in sidearm_cards:
            staff_info = extract_staff_from_sidearm_card(card)
            if staff_info:
                staff_members.append(staff_info)
        if staff_members:
            return staff_members

    # Strategy 1: Look for staff cards/blocks (common in modern sites)
    staff_cards = soup.select('.staff-member, .coach, .staff-card, .bio-card, .person')

    for card in staff_cards:
        staff_info = extract_staff_from_card(card)
        if staff_info:
            staff_members.append(staff_info)

    # Strategy 2: Look for table-based directories
    if not staff_members:
        tables = soup.find_all('table')
        for table in tables:
            table_staff = extract_staff_from_table(table)
            staff_members.extend(table_staff)

    # Strategy 3: Look for list-based directories
    if not staff_members:
        lists = soup.select('ul.staff-list, ol.staff-list, .directory-list')
        for lst in lists:
            list_items = lst.find_all('li')
            for item in list_items:
                staff_info = extract_staff_from_card(item)
                if staff_info:
                    staff_members.append(staff_info)

    return staff_members


def extract_staff_from_sidearm_card(card):
    """
    Extract staff from modern Sidearm .s-person-card format.
    Format: Name|Title|"Phone"|PhoneNumber|Email|...

    Args:
        card: BeautifulSoup element

    Returns:
        dict: Staff information or None
    """
    try:
        # Get pipe-separated text
        card_text = card.get_text(separator='|', strip=True)
        parts = [p.strip() for p in card_text.split('|')]

        if len(parts) < 3:
            return None

        # Parse structure: Name|Title|"Phone"|PhoneNumber|Email|...
        name = parts[0] if len(parts) > 0 else None
        title = parts[1] if len(parts) > 1 else 'Unknown'

        # Find email (usually after phone number)
        email = 'Not Found'
        phone = 'Not Found'

        for i, part in enumerate(parts[2:], start=2):
            # Email pattern
            if '@' in part and '.' in part:
                email = part
            # Phone number (comes after "Phone" label)
            elif i > 0 and parts[i-1].lower() == 'phone':
                # Clean phone number
                phone = part.replace('Phone', '').replace(':', '').strip()

        # Try to extract sport from title
        sport = extract_sport_from_text(title)

        if name:
            return {
                'name': name,
                'title': title,
                'email': email,
                'phone': phone,
                'sport': sport or 'Unknown',
            }

    except Exception as e:
        return None

    return None


def extract_staff_from_card(card):
    """
    Extract staff information from a card/block element.

    Args:
        card: BeautifulSoup element

    Returns:
        dict: Staff information or None
    """
    try:
        # Extract name
        name = None
        name_selectors = [
            '.name', '.staff-name', '.coach-name', 'h2', 'h3', 'h4',
            '.person-name', '.bio-name'
        ]
        for selector in name_selectors:
            name_elem = card.select_one(selector)
            if name_elem:
                name = name_elem.get_text(strip=True)
                break

        if not name:
            # Try to find any text that looks like a name
            all_text = card.get_text(strip=True)
            if all_text:
                lines = all_text.split('\n')
                if lines:
                    name = lines[0].strip()

        # Extract title/role
        title = None
        title_selectors = [
            '.title', '.staff-title', '.job-title', '.position',
            '.role', '.coach-title'
        ]
        for selector in title_selectors:
            title_elem = card.select_one(selector)
            if title_elem:
                title = title_elem.get_text(strip=True)
                break

        # Extract email
        email = None
        email_links = card.select('a[href^="mailto:"]')
        if email_links:
            email_href = email_links[0].get('href', '')
            email = email_href.replace('mailto:', '').strip()
        else:
            # Look for email pattern in text
            card_text = card.get_text()
            email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
            email_matches = re.findall(email_pattern, card_text)
            if email_matches:
                email = email_matches[0]

        # Extract phone
        phone = None
        phone_selectors = ['.phone', '.telephone', '.tel']
        for selector in phone_selectors:
            phone_elem = card.select_one(selector)
            if phone_elem:
                phone = phone_elem.get_text(strip=True)
                break

        if not phone:
            # Look for phone links
            phone_links = card.select('a[href^="tel:"]')
            if phone_links:
                phone = phone_links[0].get_text(strip=True)

        # Extract sport assignment (if available)
        sport = extract_sport_from_text(card.get_text())

        if name:
            return {
                'name': name,
                'title': title or 'Unknown',
                'email': email or 'Not Found',
                'phone': phone or 'Not Found',
                'sport': sport or 'Unknown',
            }

    except Exception as e:
        print(f"Error extracting staff from card: {str(e)}", file=sys.stderr)

    return None


def extract_staff_from_table(table):
    """
    Extract staff from table format.

    Args:
        table: BeautifulSoup table element

    Returns:
        list: List of staff dictionaries
    """
    staff_members = []

    try:
        # Find header row to identify columns
        headers = [th.get_text(strip=True).lower() for th in table.find_all('th')]

        # Find column indices
        name_col = None
        title_col = None
        email_col = None
        phone_col = None
        sport_col = None

        for i, header in enumerate(headers):
            if 'name' in header:
                name_col = i
            elif 'title' in header or 'position' in header or 'role' in header:
                title_col = i
            elif 'email' in header or 'e-mail' in header:
                email_col = i
            elif 'phone' in header or 'tel' in header:
                phone_col = i
            elif 'sport' in header or 'team' in header:
                sport_col = i

        # Parse rows
        for row in table.find_all('tr')[1:]:  # Skip header
            cols = row.find_all(['td', 'th'])

            if not cols:
                continue

            try:
                name = cols[name_col].get_text(strip=True) if name_col is not None and len(cols) > name_col else None
                title = cols[title_col].get_text(strip=True) if title_col is not None and len(cols) > title_col else 'Unknown'

                # Email extraction
                email = 'Not Found'
                if email_col is not None and len(cols) > email_col:
                    email_cell = cols[email_col]
                    email_link = email_cell.find('a', href=re.compile('^mailto:'))
                    if email_link:
                        email = email_link.get('href', '').replace('mailto:', '').strip()
                    else:
                        email_text = email_cell.get_text(strip=True)
                        if '@' in email_text:
                            email = email_text

                phone = cols[phone_col].get_text(strip=True) if phone_col is not None and len(cols) > phone_col else 'Not Found'
                sport = cols[sport_col].get_text(strip=True) if sport_col is not None and len(cols) > sport_col else None

                if not sport:
                    sport = extract_sport_from_text(row.get_text())

                if name:
                    staff_members.append({
                        'name': name,
                        'title': title,
                        'email': email,
                        'phone': phone,
                        'sport': sport or 'Unknown',
                    })

            except (IndexError, AttributeError):
                continue

    except Exception as e:
        print(f"Error parsing table: {str(e)}", file=sys.stderr)

    return staff_members


def extract_sport_from_text(text):
    """
    Try to identify sport assignment from text.

    Args:
        text (str): Text to analyze

    Returns:
        str: Sport name or None
    """
    text_lower = text.lower()

    sport_keywords = {
        "Baseball": ["baseball"],
        "Basketball": ["basketball", "men's basketball", "women's basketball"],
        "Field Hockey": ["field hockey"],
        "Football": ["football"],
        "Ice Hockey": ["ice hockey", "hockey"],
        "Lacrosse": ["lacrosse", "men's lacrosse", "women's lacrosse"],
        "Soccer": ["soccer", "men's soccer", "women's soccer"],
        "Softball": ["softball"],
        "Volleyball": ["volleyball"],
        "Swimming & Diving": ["swimming", "diving"],
        "Rowing": ["rowing", "crew"],
    }

    for sport, keywords in sport_keywords.items():
        if any(keyword in text_lower for keyword in keywords):
            return sport

    return None


def scrape_staff_directory(base_url, school_name, directory_url=None):
    """
    Main function to scrape staff directory.

    Args:
        base_url (str): Athletics website base URL
        school_name (str): School name
        directory_url (str): Optional direct URL to staff directory

    Returns:
        dict: Result with staff list and metadata
    """
    try:
        with sync_playwright() as p:
            # Launch browser (headless=False for debugging if needed)
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            # Navigate to base URL first
            print(f"Loading {base_url}...", file=sys.stderr)
            try:
                page.goto(base_url, wait_until='domcontentloaded', timeout=30000)
                page.wait_for_timeout(2000)
            except Exception:
                page.goto(base_url, wait_until='commit', timeout=30000)
                page.wait_for_timeout(3000)

            # Find staff directory if not provided
            if not directory_url:
                directory_url = find_staff_directory_url(page, base_url)
                print(f"Found staff directory: {directory_url}", file=sys.stderr)

            # Navigate to staff directory
            if directory_url != base_url:
                print(f"Navigating to {directory_url}...", file=sys.stderr)
                try:
                    page.goto(directory_url, wait_until='domcontentloaded', timeout=30000)
                    page.wait_for_timeout(2000)
                except Exception:
                    page.goto(directory_url, wait_until='commit', timeout=30000)
                    page.wait_for_timeout(3000)

            # Additional wait for any lazy-loaded content
            page.wait_for_timeout(2000)

            # Get page content
            html_content = page.content()
            soup = BeautifulSoup(html_content, 'html.parser')

            # Extract staff
            staff_members = extract_staff_from_page(soup, directory_url)

            # Close browser
            browser.close()

        # Filter out duplicates (same name)
        unique_staff = {}
        for staff in staff_members:
            if staff['name'] not in unique_staff:
                unique_staff[staff['name']] = staff

        staff_list = list(unique_staff.values())

        result = {
            "school": school_name,
            "directory_url": directory_url,
            "staff_found": len(staff_list),
            "staff": staff_list,
            "success": True,
            "timestamp": str(datetime.now()),
        }

        return result

    except PlaywrightTimeout:
        return {
            "school": school_name,
            "error": "Timeout loading staff directory",
            "success": False,
        }
    except Exception as e:
        return {
            "school": school_name,
            "error": f"Error scraping staff directory: {str(e)}",
            "success": False,
        }


def main():
    parser = argparse.ArgumentParser(
        description="Scrape athletics staff directory"
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Athletics website base URL (e.g., https://bceagles.com)"
    )
    parser.add_argument(
        "--school",
        required=True,
        help="School name (e.g., 'Boston College')"
    )
    parser.add_argument(
        "--directory-url",
        help="Optional direct URL to staff directory page"
    )
    parser.add_argument(
        "--output",
        help="Output JSON file path (default: stdout)"
    )

    args = parser.parse_args()

    # Scrape staff directory
    result = scrape_staff_directory(args.url, args.school, args.directory_url)

    # Output results
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Results saved to {args.output}", file=sys.stderr)

        if result['success']:
            print(f"Found {result['staff_found']} staff members for {args.school}",
                  file=sys.stderr)
    else:
        print(json.dumps(result, indent=2))

    # Exit with error code if scraping failed
    if not result['success']:
        sys.exit(1)


if __name__ == "__main__":
    main()
