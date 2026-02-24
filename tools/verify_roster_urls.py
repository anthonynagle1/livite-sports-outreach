#!/usr/bin/env python3
"""
Verify that generated roster URLs actually exist and return valid pages
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from urllib.parse import urlparse

def check_url(url, timeout=10):
    """
    Check if a URL returns a valid response.

    Returns:
        dict: Status information
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            try:
                response = page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
                status = response.status if response else None

                # Check if page has meaningful content
                body_text = page.inner_text('body')
                has_content = len(body_text.strip()) > 100

                # Check for common error indicators
                is_404_page = '404' in body_text.lower() or 'not found' in body_text.lower()

                browser.close()

                return {
                    'url': url,
                    'status': status,
                    'success': status == 200 and has_content and not is_404_page,
                    'has_content': has_content,
                    'is_error_page': is_404_page
                }
            except Exception as e:
                browser.close()
                return {
                    'url': url,
                    'status': None,
                    'success': False,
                    'error': str(e)
                }

    except ImportError:
        print("ERROR: Playwright not installed. Run: pip install playwright && playwright install chromium", file=sys.stderr)
        sys.exit(1)


def verify_roster_urls(matched_file, sample_size=None):
    """
    Verify roster URLs from a matched games JSON file.

    Args:
        matched_file (str): Path to matched games JSON
        sample_size (int): Optional - only test first N unique URLs
    """

    # Load matched data
    with open(matched_file, 'r') as f:
        data = json.load(f)

    matches = data.get('validated_matches', [])
    school = data.get('school', 'Unknown')

    print(f"\n=== Verifying Roster URLs for {school} ===\n", file=sys.stderr)

    # Extract unique roster URLs
    url_to_games = defaultdict(list)

    for match in matches:
        roster_url = match.get('opponent_roster_url')
        if roster_url:
            opponent = match.get('opponent', 'Unknown')
            sport = match.get('sport', 'Unknown')
            url_to_games[roster_url].append({
                'opponent': opponent,
                'sport': sport
            })

    unique_urls = list(url_to_games.keys())
    print(f"Found {len(unique_urls)} unique roster URLs to verify", file=sys.stderr)

    if sample_size:
        unique_urls = unique_urls[:sample_size]
        print(f"Testing sample of {len(unique_urls)} URLs", file=sys.stderr)

    # Test each URL
    results = {
        'working': [],
        'broken': [],
        'total': len(unique_urls)
    }

    for i, url in enumerate(unique_urls, 1):
        games = url_to_games[url]
        opponent = games[0]['opponent']

        print(f"\n[{i}/{len(unique_urls)}] Testing: {opponent}", file=sys.stderr)
        print(f"  URL: {url}", file=sys.stderr)

        result = check_url(url)

        if result['success']:
            print(f"  ✓ WORKING", file=sys.stderr)
            results['working'].append({
                'url': url,
                'opponent': opponent,
                'games': games
            })
        else:
            error_msg = result.get('error', 'Failed')
            status = result.get('status', 'N/A')
            print(f"  ✗ BROKEN (Status: {status}) - {error_msg}", file=sys.stderr)
            results['broken'].append({
                'url': url,
                'opponent': opponent,
                'games': games,
                'status': status,
                'error': error_msg
            })

        # Rate limiting
        time.sleep(0.5)

    # Summary
    print(f"\n=== Summary ===", file=sys.stderr)
    print(f"Total URLs tested: {results['total']}", file=sys.stderr)
    print(f"Working: {len(results['working'])} ({len(results['working'])/results['total']*100:.1f}%)", file=sys.stderr)
    print(f"Broken: {len(results['broken'])} ({len(results['broken'])/results['total']*100:.1f}%)", file=sys.stderr)

    if results['broken']:
        print(f"\n=== Broken URLs ===", file=sys.stderr)
        for item in results['broken']:
            print(f"\n{item['opponent']}:", file=sys.stderr)
            print(f"  URL: {item['url']}", file=sys.stderr)
            print(f"  Games: {len(item['games'])}", file=sys.stderr)

    return results


def main():
    parser = argparse.ArgumentParser(description="Verify roster URLs actually exist")
    parser.add_argument('--input', required=True, help="Path to matched games JSON file")
    parser.add_argument('--sample', type=int, help="Test only first N URLs (for quick testing)")
    parser.add_argument('--output', help="Save results to JSON file")

    args = parser.parse_args()

    results = verify_roster_urls(args.input, sample_size=args.sample)

    if args.output:
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to: {args.output}", file=sys.stderr)

    # Output JSON to stdout for pipeline processing
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
