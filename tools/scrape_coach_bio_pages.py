#!/usr/bin/env python3
"""
Tool: scrape_coach_bio_pages.py
Purpose: Scrape coaching staff from individual bio pages (fallback for schools without table listings)

Some schools don't display staff on /coaches pages, but have individual bio pages
linked from the roster. This scraper handles that pattern.

Usage:
    python tools/scrape_coach_bio_pages.py \
        --roster-url "https://ramblinwreck.com/sports/m-basebl/roster/" \
        --sport "Baseball" \
        --school "Georgia Tech" \
        --output staff.json
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

load_dotenv()


def find_email_in_text(text, school_domain=None):
    """
    Find email address in text using regex.
    Filters out spam protection emails and prefers school domain.

    Args:
        text (str): Text to search
        school_domain (str, optional): Preferred school domain (e.g., "nd.edu")

    Returns:
        str: Best email address or None
    """
    # Pattern for email addresses
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    matches = re.findall(email_pattern, text)

    if not matches:
        return None

    # Filter out known spam/protection domains
    fake_domains = ['sentry.wmt.dev', 'example.com', 'domain.com', 'email.com']
    valid_matches = [m for m in matches if not any(fake in m.lower() for fake in fake_domains)]

    if not valid_matches:
        return None

    # If school domain provided, prefer emails from that domain
    if school_domain:
        school_matches = [m for m in valid_matches if school_domain.lower() in m.lower()]
        if school_matches:
            return school_matches[0]

    # Return first valid email
    return valid_matches[0]


def find_phone_in_text(text):
    """
    Find phone number in text using regex.

    Args:
        text (str): Text to search

    Returns:
        str: Phone number or None
    """
    # Pattern for phone numbers (various formats)
    phone_patterns = [
        r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',  # (123) 456-7890 or 123-456-7890
        r'\d{3}[-.\s]\d{4}',  # 456-7890
    ]

    for pattern in phone_patterns:
        matches = re.findall(pattern, text)
        if matches:
            return matches[0]

    return None


def extract_title_from_text(text, name):
    """
    Extract job title from bio page text.

    Args:
        text (str): Page text
        name (str): Person's name (to help locate title)

    Returns:
        str: Job title
    """
    text_lower = text.lower()

    # Common title patterns
    title_keywords = [
        'head coach',
        'assistant coach',
        'associate head coach',
        'director of operations',
        'director of baseball operations',
        'first assistant coach',
        'pitching coach',
        'hitting coach',
    ]

    for keyword in title_keywords:
        if keyword in text_lower:
            return keyword.title()

    return 'Unknown'


def scrape_bio_page(page, bio_url, sport_name):
    """
    Scrape a single coach bio page.

    Args:
        page: Playwright page object
        bio_url (str): Bio page URL
        sport_name (str): Sport name

    Returns:
        dict: Staff member data or None
    """
    try:
        page.goto(bio_url, wait_until='domcontentloaded', timeout=20000)
        page.wait_for_timeout(2000)

        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')

        # Get page text for searching
        page_text = soup.get_text()

        # Extract school domain from URL (e.g., "nd.edu" from "fightingirish.com")
        # Common patterns: nd.edu, stanford.edu, vt.edu, etc.
        url_domain = urlparse(bio_url).netloc
        school_domain = None
        if 'fightingirish.com' in url_domain:
            school_domain = 'nd.edu'
        elif 'gostanford.com' in url_domain:
            school_domain = 'stanford.edu'
        elif 'hokiesports.com' in url_domain:
            school_domain = 'vt.edu'
        elif 'hurricanesports.com' in url_domain:
            school_domain = 'miami.edu'
        elif 'virginiasports.com' in url_domain:
            school_domain = 'virginia.edu'
        elif 'ramblinwreck.com' in url_domain:
            school_domain = 'gatech.edu'
        # Add more mappings as needed

        # Extract name from page title or h1
        name = 'Unknown'
        h1 = soup.find('h1')
        if h1:
            name = h1.get_text(strip=True)
        else:
            # Try page title
            title_tag = soup.find('title')
            if title_tag:
                name = title_tag.get_text(strip=True).split('|')[0].strip()

        # Extract email with domain preference
        email = find_email_in_text(html, school_domain=school_domain)
        if not email:
            email = find_email_in_text(page_text, school_domain=school_domain)

        # Extract phone
        phone = find_phone_in_text(page_text)

        # Extract title
        title = extract_title_from_text(page_text, name)

        if email:  # Only return if we found an email
            return {
                'name': name,
                'title': title,
                'email': email if email else 'Not Found',
                'phone': phone if phone else 'Not Found',
                'sport': sport_name,
            }

        return None

    except Exception as e:
        print(f"  Error scraping {bio_url}: {e}", file=sys.stderr)
        return None


def discover_roster_url(page, team_url):
    """
    Try to discover the actual roster URL from a team page.

    Args:
        page: Playwright page object
        team_url (str): Team page URL

    Returns:
        str: Roster URL or constructed fallback
    """
    try:
        page.goto(team_url, wait_until='domcontentloaded', timeout=20000)
        page.wait_for_timeout(2000)

        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')

        # Get the sport path from team_url for matching
        sport_path = team_url.split('/sports/')[-1].split('/')[0] if '/sports/' in team_url else ''

        # Look for "Roster" link in navigation that matches this sport
        all_links = soup.find_all('a', href=True)
        for link in all_links:
            text = link.get_text(strip=True).lower()
            href = link.get('href', '')

            # Match roster links that contain the sport path
            if text == 'roster' and sport_path and sport_path in href:
                absolute_url = urljoin(team_url, href)
                print(f"  Discovered roster URL: {absolute_url}", file=sys.stderr)
                return absolute_url

    except Exception as e:
        print(f"  Could not auto-discover roster URL: {e}", file=sys.stderr)

    # Fallback: construct from team URL
    return f"{team_url}/roster/"


def find_coach_bio_links(page, roster_url):
    """
    Find coach bio page links from a roster page.

    Args:
        page: Playwright page object
        roster_url (str): Roster page URL

    Returns:
        list: List of bio page URLs
    """
    try:
        page.goto(roster_url, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(3000)

        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')

        # Find all links
        all_links = soup.find_all('a', href=True)

        bio_urls = set()
        for link in all_links:
            href = link.get('href', '')

            # Look for coach/staff bio URLs
            if '/coach/' in href or '/staff/' in href:
                # Make absolute URL
                absolute_url = urljoin(roster_url, href)
                bio_urls.add(absolute_url)

        return list(bio_urls)

    except Exception as e:
        print(f"Error finding bio links: {e}", file=sys.stderr)
        return []


def scrape_coach_bios(roster_url, sport_name, school_name, team_url=None):
    """
    Main function to scrape coaching staff from bio pages.

    Args:
        roster_url (str): Roster page URL (or team page URL if team_url is provided)
        sport_name (str): Sport name
        school_name (str): School name
        team_url (str, optional): Team page URL for auto-discovering roster URL

    Returns:
        dict: Result with staff list
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

            # If team_url provided, try to discover actual roster URL
            if team_url:
                roster_url = discover_roster_url(page, team_url)

            # Find coach bio links
            print(f"Finding coach bio links on roster page...", file=sys.stderr)
            bio_urls = find_coach_bio_links(page, roster_url)
            print(f"Found {len(bio_urls)} bio pages", file=sys.stderr)

            if not bio_urls:
                browser.close()
                return {
                    "school": school_name,
                    "sport": sport_name,
                    "roster_url": roster_url,
                    "staff_found": 0,
                    "staff": [],
                    "success": True,
                    "timestamp": str(datetime.now()),
                }

            # Scrape each bio page
            staff_members = []
            for i, bio_url in enumerate(bio_urls, 1):
                print(f"  Scraping bio {i}/{len(bio_urls)}: {bio_url}", file=sys.stderr)
                staff = scrape_bio_page(page, bio_url, sport_name)
                if staff:
                    staff_members.append(staff)
                    print(f"    ✓ Found: {staff['name']} ({staff['title']})", file=sys.stderr)

            browser.close()

        # Remove duplicates by name
        unique_staff = {}
        for staff in staff_members:
            if staff['name'] not in unique_staff:
                unique_staff[staff['name']] = staff

        staff_list = list(unique_staff.values())

        result = {
            "school": school_name,
            "sport": sport_name,
            "roster_url": roster_url,
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
            "error": "Timeout loading roster page",
            "success": False,
        }
    except Exception as e:
        return {
            "school": school_name,
            "sport": sport_name,
            "error": f"Error scraping coach bios: {str(e)}",
            "success": False,
        }


def main():
    parser = argparse.ArgumentParser(
        description="Scrape coaching staff from individual bio pages"
    )
    parser.add_argument(
        "--roster-url",
        help="Roster page URL (e.g., https://ramblinwreck.com/sports/m-basebl/roster/)"
    )
    parser.add_argument(
        "--team-url",
        help="Team page URL (will auto-discover roster URL)"
    )
    parser.add_argument(
        "--sport",
        required=True,
        help="Sport name (e.g., 'Baseball')"
    )
    parser.add_argument(
        "--school",
        required=True,
        help="School name (e.g., 'Georgia Tech')"
    )
    parser.add_argument(
        "--output",
        help="Output JSON file path (default: stdout)"
    )

    args = parser.parse_args()

    # Validate arguments
    if not args.roster_url and not args.team_url:
        parser.error("Either --roster-url or --team-url must be provided")

    # Scrape coach bios
    roster_url = args.roster_url if args.roster_url else args.team_url
    team_url = args.team_url if args.team_url else None
    result = scrape_coach_bios(roster_url, args.sport, args.school, team_url=team_url)

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
