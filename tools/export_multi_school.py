#!/usr/bin/env python3
"""
Export multiple schools to a single master spreadsheet with tabs for each school
"""

import argparse
import json
import subprocess
import sys

def export_multi_school(school_files, spreadsheet_name):
    """
    Export multiple schools to Google Sheets with tabs for each.

    Args:
        school_files (dict): School name -> JSON file path
        spreadsheet_name (str): Name for the master spreadsheet
    """

    # First school creates the spreadsheet
    first_school = list(school_files.keys())[0]
    first_file = school_files[first_school]

    print(f"\n=== Creating Master Spreadsheet: {spreadsheet_name} ===", file=sys.stderr)
    print(f"Starting with {first_school}...", file=sys.stderr)

    # Create spreadsheet with first school
    result = subprocess.run(
        ['python3', 'tools/export_to_sheets.py',
         '--input', first_file,
         '--spreadsheet-name', spreadsheet_name,
         '--school-name', first_school],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(f"Error creating spreadsheet: {result.stderr}", file=sys.stderr)
        return None

    # Extract spreadsheet ID from output
    output_lines = result.stderr.split('\n')
    spreadsheet_id = None
    spreadsheet_url = None

    for line in output_lines:
        if 'Spreadsheet ID:' in line:
            spreadsheet_id = line.split('Spreadsheet ID:')[1].strip()
        if 'https://docs.google.com/spreadsheets' in line:
            spreadsheet_url = line.strip()

    if not spreadsheet_id:
        print("Could not extract spreadsheet ID", file=sys.stderr)
        return None

    print(f"✓ Created spreadsheet: {spreadsheet_id}", file=sys.stderr)

    # Add remaining schools as new tabs
    for school_name in list(school_files.keys())[1:]:
        file_path = school_files[school_name]
        print(f"\nAdding {school_name}...", file=sys.stderr)

        result = subprocess.run(
            ['python3', 'tools/export_to_sheets.py',
             '--input', file_path,
             '--spreadsheet-id', spreadsheet_id,
             '--school-name', school_name],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            print(f"✓ Added {school_name}", file=sys.stderr)
        else:
            print(f"✗ Failed to add {school_name}: {result.stderr}", file=sys.stderr)

    print(f"\n=== Master Spreadsheet Complete ===", file=sys.stderr)
    print(f"URL: {spreadsheet_url}", file=sys.stderr)

    return {
        'spreadsheet_id': spreadsheet_id,
        'spreadsheet_url': spreadsheet_url,
        'schools_added': list(school_files.keys())
    }

def main():
    parser = argparse.ArgumentParser(description="Export multiple schools to master spreadsheet")
    parser.add_argument('--schools', required=True, nargs='+', help="School names")
    parser.add_argument('--files', required=True, nargs='+', help="Matched JSON files (same order as schools)")
    parser.add_argument('--spreadsheet-name', required=True, help="Master spreadsheet name")

    args = parser.parse_args()

    if len(args.schools) != len(args.files):
        print("Error: Number of schools must match number of files", file=sys.stderr)
        sys.exit(1)

    school_files = dict(zip(args.schools, args.files))

    result = export_multi_school(school_files, args.spreadsheet_name)

    if result:
        print(json.dumps(result, indent=2))
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
