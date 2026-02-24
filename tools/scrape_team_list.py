#!/usr/bin/env python3
"""
Tool: scrape_team_list.py
Purpose: Discover all varsity athletics teams for a school by scraping the athletics website

Usage:
    python tools/scrape_team_list.py --url "https://bceagles.com" --school "Boston College"
    python tools/scrape_team_list.py --url "https://bceagles.com" --school "Boston College" --output teams.json

Output: JSON list of teams with sport name, gender, schedule URL
"""

import argparse
import json
import sys
import os
import re
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# Sports to exclude (no on-site games or very small participation)
EXCLUDED_SPORTS = {
    "skiing", "ski", "sailing", "golf", "tennis", "cross country",
    "cross-country", "track", "track and field", "track & field"
}


def extract_teams_from_navigation(page, base_url, platform_hints):
    """
    Extract team links from the athletics website navigation.

    Args:
        page: Playwright page object
        base_url (str): Base URL of the athletics site
        platform_hints (dict): Platform-specific selectors

    Returns:
        list: List of team dictionaries with name, url, sport, gender
    """
    teams = []

    try:
        # Get page content (page should already be loaded)
        html_content = page.content()
        soup = BeautifulSoup(html_content, 'html.parser')

        # Find all navigation links that might be sports
        sport_links = []

        # Try multiple selectors to find sport links
        selectors = [
            'a[href*="/sports/"]',
            'a[href*="/sport/"]',
            'a[href*="/teams/"]',
            'nav a',
            '.sport-nav a',
            '.sports-menu a',
        ]

        for selector in selectors:
            links = soup.select(selector)
            sport_links.extend(links)

        # Remove duplicates by href
        unique_links = {}
        for link in sport_links:
            href = link.get('href', '')
            if href and href not in unique_links:
                unique_links[href] = link

        # Process each potential sport link
        for href, link in unique_links.items():
            # Skip non-sport links
            if any(skip in href.lower() for skip in [
                '/tickets', '/news', '/schedule', '/roster', '/stats',
                '/video', '/camps', '/facilities', '/staff', 'recruiting'
            ]):
                continue

            # Skip if it's not a sport-related path
            if not any(pattern in href.lower() for pattern in [
                '/sport', '/team', 'baseball', 'basketball', 'football',
                'soccer', 'hockey', 'lacrosse', 'volleyball', 'softball',
                'field-hockey'
            ]):
                continue

            # Get link text
            link_text = link.get_text(strip=True)

            # Extract sport and gender from link text or URL
            sport_info = parse_sport_name(link_text, href)

            if sport_info and not is_excluded_sport(sport_info['sport']):
                full_url = urljoin(base_url, href)

                teams.append({
                    'sport': sport_info['sport'],
                    'gender': sport_info['gender'],
                    'name': link_text,
                    'url': full_url,
                    'schedule_url': None,  # Will be found by scrape_schedule.py
                })

        return teams

    except Exception as e:
        print(f"Error extracting teams from navigation: {str(e)}", file=sys.stderr)
        return []


def parse_sport_name(text, url):
    """
    Parse sport name and gender from link text or URL.

    Args:
        text (str): Link text
        url (str): URL path

    Returns:
        dict: {'sport': str, 'gender': str} or None
    """
    combined = f"{text} {url}".lower()

    # Gender detection
    gender = "Unknown"
    if any(w in combined for w in ["women", "women's", "womens", "w-", "wsoc", "wlax", "/wbkb", "/wvball", "/wih", "wsb"]):
        gender = "Women"
    elif any(w in combined for w in ["men", "men's", "mens", "m-", "msoc", "mlax", "/mbkb", "/mvball", "/mih", "/bsb", "/fball"]):
        gender = "Men"

    # Sport detection (common NCAA sports)
    sport_patterns = {
        "Baseball": ["baseball", "/bsb/", "/bsb"],
        "Basketball": ["basketball", "bball", "hoops", "/mbkb/", "/wbkb/", "/mbkb", "/wbkb"],
        "Field Hockey": ["field-hockey", "field hockey", "fhockey", "/fh/", "/fh"],
        "Football": ["football", "/fball/", "/fball"],
        "Ice Hockey": ["ice-hockey", "ice hockey", "hockey", "/mih/", "/wih/"],
        "Lacrosse": ["lacrosse", "lax"],
        "Soccer": ["soccer", "soc"],
        "Softball": ["softball", "sball"],
        "Volleyball": ["volleyball", "vball", "volley", "/mvball/", "/wvball/"],
        "Swimming & Diving": ["swimming", "swim", "diving"],
        "Rowing": ["rowing", "crew"],
        "Water Polo": ["water-polo", "waterpolo", "wpolo"],
        "Wrestling": ["wrestling"],
        "Gymnastics": ["gymnastics"],
        "Fencing": ["fencing"],
    }

    detected_sport = None
    for sport_name, patterns in sport_patterns.items():
        if any(pattern in combined for pattern in patterns):
            detected_sport = sport_name
            break

    if detected_sport:
        return {
            'sport': detected_sport,
            'gender': gender
        }

    return None


def is_excluded_sport(sport_name):
    """
    Check if sport should be excluded (no home games or small participation).

    Args:
        sport_name (str): Sport name

    Returns:
        bool: True if sport should be excluded
    """
    return sport_name.lower() in EXCLUDED_SPORTS


def scrape_team_list(url, school_name, platform=None):
    """
    Main function to scrape team list from athletics website.

    Args:
        url (str): Athletics website URL
        school_name (str): School name
        platform (str): Optional platform type for optimized scraping

    Returns:
        dict: Result with teams list and metadata
    """
    teams = []

    try:
        with sync_playwright() as p:
            # Launch browser
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            # Navigate to athletics site
            print(f"Loading {url}...", file=sys.stderr)
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=30000)
                # Wait a bit for additional content to load
                page.wait_for_timeout(3000)
            except Exception as e:
                # Fallback: try with just commit
                print(f"DOMContentLoaded wait failed, using commit: {str(e)}", file=sys.stderr)
                page.goto(url, wait_until='commit', timeout=30000)
                page.wait_for_timeout(5000)

            # Get platform hints (could be enhanced with detect_athletics_platform.py)
            platform_hints = {}

            # Extract teams
            teams = extract_teams_from_navigation(page, url, platform_hints)

            # Close browser
            browser.close()

        # Deduplicate teams (same sport + gender)
        unique_teams = {}
        for team in teams:
            key = f"{team['sport']}_{team['gender']}"
            if key not in unique_teams:
                unique_teams[key] = team

        teams_list = list(unique_teams.values())

        result = {
            "school": school_name,
            "athletics_url": url,
            "teams_found": len(teams_list),
            "teams": teams_list,
            "success": True,
        }

        return result

    except PlaywrightTimeout:
        return {
            "school": school_name,
            "athletics_url": url,
            "error": "Timeout loading athletics website",
            "success": False,
        }
    except Exception as e:
        return {
            "school": school_name,
            "athletics_url": url,
            "error": f"Error scraping team list: {str(e)}",
            "success": False,
        }


def main():
    parser = argparse.ArgumentParser(
        description="Scrape athletics team list from school website"
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Athletics website URL (e.g., https://bceagles.com)"
    )
    parser.add_argument(
        "--school",
        required=True,
        help="School name (e.g., 'Boston College')"
    )
    parser.add_argument(
        "--platform",
        help="Platform type (sidearm, presto, custom) for optimized scraping"
    )
    parser.add_argument(
        "--output",
        help="Output JSON file path (default: stdout)"
    )

    args = parser.parse_args()

    # Scrape teams
    result = scrape_team_list(args.url, args.school, args.platform)

    # Save to file if specified
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Results saved to {args.output}", file=sys.stderr)

        if result['success']:
            print(f"Found {result['teams_found']} teams for {args.school}",
                  file=sys.stderr)

    # Always output JSON to stdout for pipeline processing
    print(json.dumps(result, indent=2))

    # Exit with error code if scraping failed
    if not result['success']:
        sys.exit(1)


if __name__ == "__main__":
    main()
