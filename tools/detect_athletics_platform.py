#!/usr/bin/env python3
"""
Tool: detect_athletics_platform.py
Purpose: Identify the athletics website platform (Sidearm Sports, PrestoSports, custom, etc.)
         to optimize scraping strategy

Usage:
    python tools/detect_athletics_platform.py --url "https://bceagles.com"
    python tools/detect_athletics_platform.py --school "Boston College"

Output: JSON with platform type, navigation patterns, and recommended selectors
"""

import argparse
import json
import sys
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup


# Platform detection signatures
PLATFORM_SIGNATURES = {
    "sidearm": {
        "indicators": [
            "sidearm.sites",
            "sidearmstats",
            "sidearm.nextgen",
            "sidearm-legacy",
            "data-sidearm",
        ],
        "meta_tags": ["generator:Sidearm Sports"],
        "common_domains": ["sidearmstats.com", "sidearmdev.com"],
    },
    "presto": {
        "indicators": [
            "prestosports.com",
            "presto-sports",
            "prestoapi",
            "prestocms",
        ],
        "meta_tags": ["generator:PrestoSports"],
        "common_domains": ["prestosports.com"],
    },
    "jumpforward": {
        "indicators": ["jumpforward", "jfw-sports"],
        "meta_tags": [],
        "common_domains": ["jumpforward.com"],
    },
    "neulion": {
        "indicators": ["neulion", "nln-sports"],
        "meta_tags": [],
        "common_domains": ["neulion.com"],
    },
    "custom": {
        "indicators": [],  # Fallback category
        "meta_tags": [],
        "common_domains": [],
    },
}


def detect_platform(url):
    """
    Analyze the athletics website to determine which platform it uses.

    Args:
        url (str): Athletics website URL

    Returns:
        dict: Platform information including type, confidence, and navigation hints
    """
    try:
        # Fetch the page
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        html_content = response.text.lower()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Extract meta tags
        meta_tags = []
        for meta in soup.find_all('meta'):
            name = meta.get('name', '').lower()
            content = meta.get('content', '').lower()
            if name:
                meta_tags.append(f"{name}:{content}")

        # Check for platform signatures
        detected_platforms = []

        for platform_name, signatures in PLATFORM_SIGNATURES.items():
            if platform_name == "custom":
                continue  # Skip custom, it's the fallback

            confidence = 0
            matches = []

            # Check HTML content for indicators
            for indicator in signatures["indicators"]:
                if indicator.lower() in html_content:
                    confidence += 20
                    matches.append(f"Found '{indicator}' in HTML")

            # Check meta tags
            for meta_pattern in signatures["meta_tags"]:
                if any(meta_pattern.lower() in tag for tag in meta_tags):
                    confidence += 30
                    matches.append(f"Found meta tag: {meta_pattern}")

            # Check domain
            parsed_url = urlparse(url)
            for domain in signatures["common_domains"]:
                if domain in parsed_url.netloc:
                    confidence += 50
                    matches.append(f"Domain matches: {domain}")

            if confidence > 0:
                detected_platforms.append({
                    "platform": platform_name,
                    "confidence": min(confidence, 100),
                    "matches": matches,
                })

        # Sort by confidence and return top match
        if detected_platforms:
            detected_platforms.sort(key=lambda x: x["confidence"], reverse=True)
            top_platform = detected_platforms[0]
        else:
            top_platform = {
                "platform": "custom",
                "confidence": 50,
                "matches": ["No known platform signatures detected"],
            }

        # Add navigation hints based on platform
        navigation_hints = get_navigation_hints(top_platform["platform"])

        result = {
            "url": url,
            "platform": top_platform["platform"],
            "confidence": top_platform["confidence"],
            "matches": top_platform["matches"],
            "navigation_hints": navigation_hints,
            "all_detections": detected_platforms,
        }

        return result

    except requests.RequestException as e:
        return {
            "error": f"Failed to fetch URL: {str(e)}",
            "url": url,
        }
    except Exception as e:
        return {
            "error": f"Error during platform detection: {str(e)}",
            "url": url,
        }


def get_navigation_hints(platform):
    """
    Provide platform-specific navigation hints for scraping.

    Args:
        platform (str): Detected platform name

    Returns:
        dict: Navigation selectors and patterns
    """
    hints = {
        "sidearm": {
            "team_nav_selector": "nav.main-nav, nav#main-navigation",
            "team_link_pattern": "/sports/",
            "schedule_selector": ".sidearm-schedule, table.schedule",
            "staff_directory_path": "/staff-directory/",
            "sport_menu_selector": "ul.sport-nav, nav.sports-nav",
        },
        "presto": {
            "team_nav_selector": "nav#sports-nav, .sports-menu",
            "team_link_pattern": "/sports/",
            "schedule_selector": "table.schedule-table",
            "staff_directory_path": "/coaches",
            "sport_menu_selector": ".sport-navigation",
        },
        "custom": {
            "team_nav_selector": "nav, header nav",
            "team_link_pattern": "/sport|/team|/athletics",
            "schedule_selector": "table, .schedule, .games",
            "staff_directory_path": "/staff|/coaches|/directory",
            "sport_menu_selector": "nav, ul.menu",
        },
    }

    return hints.get(platform, hints["custom"])


def find_athletics_url(school_name):
    """
    Attempt to find the athletics website URL for a given school name.
    Uses a simple heuristic (can be enhanced with web search later).

    Args:
        school_name (str): Name of the school

    Returns:
        str: Guessed athletics URL
    """
    # Simple heuristic: most schools use their mascot or abbreviation
    # This is a placeholder - ideally would use Google search or a database

    common_patterns = [
        f"https://{school_name.lower().replace(' ', '')}athletics.com",
        f"https://{school_name.lower().replace(' ', '')}sports.com",
        f"https://go{school_name.lower().replace(' ', '')}.com",
    ]

    # For now, return the first pattern (user should provide actual URL)
    return common_patterns[0]


def main():
    parser = argparse.ArgumentParser(
        description="Detect athletics website platform type"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--url",
        help="Athletics website URL to analyze"
    )
    group.add_argument(
        "--school",
        help="School name (will attempt to find athletics URL)"
    )
    parser.add_argument(
        "--output",
        help="Output JSON file path (default: stdout)"
    )

    args = parser.parse_args()

    # Determine URL to analyze
    if args.url:
        url = args.url
    else:
        # Try to find URL from school name
        url = find_athletics_url(args.school)
        print(f"Attempting to detect platform for: {url}", file=sys.stderr)
        print(f"(Note: URL guessed from school name. Provide --url for accuracy)\n",
              file=sys.stderr)

    # Detect platform
    result = detect_platform(url)

    # Output results
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Results saved to {args.output}", file=sys.stderr)
    else:
        print(json.dumps(result, indent=2))

    # Exit with error code if detection failed
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
