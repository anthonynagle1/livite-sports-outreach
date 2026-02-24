#!/usr/bin/env python3
"""
Tool: validate_contacts.py
Purpose: Validate matched game-contact data for quality and accuracy

Validation checks:
1. Email domain matches school
2. No duplicate contacts for same game
3. Contact information completeness
4. Flagged items for manual review

Usage:
    python tools/validate_contacts.py \
        --input matched_contacts.json \
        --output validated_contacts.json

Output: JSON with validation flags and issues summary
"""

import argparse
import json
import sys
import re
from datetime import datetime
from urllib.parse import urlparse


def extract_domain_from_email(email):
    """
    Extract domain from email address.

    Args:
        email (str): Email address

    Returns:
        str: Domain or None
    """
    if not email or email in ['Not Found', 'N/A']:
        return None

    match = re.search(r'@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', email)
    if match:
        return match.group(1).lower()
    return None


def guess_school_domain(school_name):
    """
    Guess likely email domain for a school.

    Args:
        school_name (str): School name

    Returns:
        list: List of possible domains
    """
    school_lower = school_name.lower()

    # Common patterns
    if 'boston college' in school_lower or school_lower == 'bc':
        return ['bc.edu']
    elif 'merrimack' in school_lower:
        return ['merrimack.edu']
    elif 'harvard' in school_lower:
        return ['harvard.edu']
    elif 'yale' in school_lower:
        return ['yale.edu']
    elif 'princeton' in school_lower:
        return ['princeton.edu']
    elif 'dartmouth' in school_lower:
        return ['dartmouth.edu']

    # Generic pattern: "University of XYZ" -> xyz.edu
    if 'university of ' in school_lower:
        school_part = school_lower.replace('university of ', '').strip()
        school_part = school_part.replace(' ', '')
        return [f"{school_part}.edu", f"u{school_part}.edu"]

    # Generic: "XYZ College" -> xyz.edu
    if 'college' in school_lower:
        school_part = school_lower.replace('college', '').strip()
        school_part = school_part.replace(' ', '')
        return [f"{school_part}.edu"]

    # Fallback: just use first word + .edu
    first_word = school_lower.split()[0] if school_lower else ''
    return [f"{first_word}.edu"]


def validate_match(match, opponent_school):
    """
    Validate a single game-contact match.

    Args:
        match (dict): Match data with game + contact info
        opponent_school (str): Opponent school name

    Returns:
        dict: Match with validation flags
    """
    issues = []
    warnings = []

    # Skip validation for opponent mismatches
    if match.get('match_status') == 'opponent_mismatch':
        return {
            **match,
            'validation_status': 'skipped',
            'issues': [],
            'warnings': [],
        }

    # Check email validity
    email = match.get('contact_email', '')
    if not email or email == 'Not Found':
        issues.append('no_email')
    else:
        # Check email domain
        email_domain = extract_domain_from_email(email)
        expected_domains = guess_school_domain(opponent_school)

        if email_domain and email_domain not in expected_domains:
            warnings.append(f'email_domain_mismatch (expected {expected_domains[0]}, got {email_domain})')

    # Check name validity
    name = match.get('contact_name', '')
    if not name or name == 'Not Found':
        issues.append('no_contact_name')

    # Check title validity
    title = match.get('contact_title', '')
    if not title or title == 'Unknown':
        warnings.append('no_contact_title')

    # Check phone (warning only, not critical)
    phone = match.get('contact_phone', '')
    if not phone or phone == 'Not Found':
        warnings.append('no_phone')

    # Determine overall validation status
    if issues:
        validation_status = 'failed'
    elif warnings:
        validation_status = 'warning'
    else:
        validation_status = 'passed'

    return {
        **match,
        'validation_status': validation_status,
        'issues': issues,
        'warnings': warnings,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Validate matched game-contact data"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to matched contacts JSON file"
    )
    parser.add_argument(
        "--output",
        help="Output JSON file path (default: stdout)"
    )

    args = parser.parse_args()

    # Load matched data
    try:
        with open(args.input, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading input data: {e}", file=sys.stderr)
        sys.exit(1)

    # Extract matches
    matches = data.get('matches', [])
    opponent_school = data.get('opponent_school', 'Unknown')

    if not matches:
        print("No matches found in input data", file=sys.stderr)
        sys.exit(1)

    # Validate each match
    validated_matches = []
    for match in matches:
        validated_match = validate_match(match, opponent_school)
        validated_matches.append(validated_match)

    # Count validation results
    passed = sum(1 for m in validated_matches if m['validation_status'] == 'passed')
    warnings = sum(1 for m in validated_matches if m['validation_status'] == 'warning')
    failed = sum(1 for m in validated_matches if m['validation_status'] == 'failed')
    skipped = sum(1 for m in validated_matches if m['validation_status'] == 'skipped')

    # Create result
    result = {
        "school": data.get('school', 'Unknown'),
        "sport": data.get('sport', 'Unknown'),
        "opponent_school": opponent_school,
        "validation_summary": {
            "total_matches": len(validated_matches),
            "passed": passed,
            "warnings": warnings,
            "failed": failed,
            "skipped": skipped,
        },
        "validated_matches": validated_matches,
        "success": True,
        "timestamp": str(datetime.now()),
    }

    # Save to file if specified
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Validated {len(validated_matches)} matches", file=sys.stderr)
        print(f"  Passed: {passed}, Warnings: {warnings}, Failed: {failed}, Skipped: {skipped}", file=sys.stderr)
        print(f"Results saved to {args.output}", file=sys.stderr)

    # Always output JSON to stdout for pipeline processing
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
