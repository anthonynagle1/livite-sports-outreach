#!/usr/bin/env python3
"""
Tool: export_to_sheets.py
Purpose: Export validated game-contact data to Google Sheets

Creates 3 sheets:
1. Game-by-Game Contact List (primary view)
2. Master Contacts Cache (all staff discovered)
3. Chronological Aggregate View (sorted by date)

Usage:
    python tools/export_to_sheets.py \
        --input validated_contacts.json \
        --spreadsheet-name "BC Baseball Catering Contacts 2026" \
        --credentials credentials.json

Output: Google Sheets URL
"""

import argparse
import json
import sys
import os
from datetime import datetime
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pickle

# Google Sheets API scopes
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']


def normalize_time(time_str):
    """
    Normalize time strings to standard 12hr format: "2:00pm"

    Handles: "2:00 P.M. ET", "3 PM", "12 P.M.", "7:00 p.m. ET",
             "3:00 PM (EST)", "TBA", etc.
    """
    import re
    if not time_str:
        return ''
    time_str = time_str.strip()
    if time_str.upper() in ('TBA', 'TBD', ''):
        return 'TBA'

    # Strip timezone suffixes: ET, EST, CST, etc. and parenthetical notes
    cleaned = re.sub(r'\s*\(.*?\)', '', time_str).strip()
    cleaned = re.sub(r'\s+(ET|EST|CST|CT|MT|MST|PT|PST)$', '', cleaned, flags=re.IGNORECASE).strip()

    # Detect AM/PM
    is_pm = bool(re.search(r'p\.?m\.?', cleaned, re.IGNORECASE))
    is_am = bool(re.search(r'a\.?m\.?', cleaned, re.IGNORECASE))

    # Strip AM/PM text
    cleaned = re.sub(r'\s*(a\.?m\.?|p\.?m\.?)', '', cleaned, flags=re.IGNORECASE).strip()

    # Parse hour:minute
    match = re.match(r'^(\d{1,2}):(\d{2})$', cleaned)
    if match:
        hour, minute = match.group(1), match.group(2)
    else:
        # Handle "3 PM" or "12" (no minutes)
        match = re.match(r'^(\d{1,2})$', cleaned)
        if match:
            hour, minute = match.group(1), '00'
        else:
            return time_str  # Can't parse, return as-is

    suffix = 'pm' if is_pm else ('am' if is_am else '')
    return f"{hour}:{minute}{suffix}"


def get_credentials(credentials_path='credentials.json', token_path='token.pickle'):
    """
    Get Google Sheets API credentials.

    Args:
        credentials_path (str): Path to credentials.json
        token_path (str): Path to token.pickle

    Returns:
        Credentials object
    """
    creds = None

    # Check if token already exists
    if os.path.exists(token_path):
        with open(token_path, 'rb') as token:
            creds = pickle.load(token)

    # If no valid credentials, authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                print(f"Error: Credentials file not found: {credentials_path}", file=sys.stderr)
                print("\nTo set up Google Sheets API:", file=sys.stderr)
                print("1. Go to https://console.cloud.google.com/", file=sys.stderr)
                print("2. Create a new project (or select existing)", file=sys.stderr)
                print("3. Enable Google Sheets API", file=sys.stderr)
                print("4. Create OAuth 2.0 credentials", file=sys.stderr)
                print("5. Download credentials.json", file=sys.stderr)
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save credentials for next run
        with open(token_path, 'wb') as token:
            pickle.dump(creds, token)

    return creds


def get_spreadsheet(service, spreadsheet_id):
    """
    Get existing spreadsheet metadata.

    Args:
        service: Google Sheets API service
        spreadsheet_id (str): Spreadsheet ID

    Returns:
        dict: Spreadsheet metadata with sheet info
    """
    try:
        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()

        # Extract sheet IDs
        sheet_ids = {}
        for sheet in spreadsheet.get('sheets', []):
            title = sheet['properties']['title']
            sheet_id = sheet['properties']['sheetId']
            sheet_ids[title] = sheet_id

        return {
            'spreadsheet_id': spreadsheet_id,
            'spreadsheet_url': spreadsheet.get('spreadsheetUrl'),
            'sheet_ids': sheet_ids,
            'sheets': [s['properties']['title'] for s in spreadsheet.get('sheets', [])]
        }
    except HttpError as e:
        print(f"Error getting spreadsheet: {e}", file=sys.stderr)
        return None


def create_sheet_if_missing(service, spreadsheet_id, sheet_name, existing_sheets):
    """
    Create a new sheet/tab if it doesn't exist.

    Args:
        service: Google Sheets API service
        spreadsheet_id (str): Spreadsheet ID
        sheet_name (str): Name for the new sheet
        existing_sheets (dict): Dictionary of existing sheet names to IDs

    Returns:
        int: Sheet ID (existing or newly created)
    """
    if sheet_name in existing_sheets:
        return existing_sheets[sheet_name]

    # Create new sheet
    request = {
        'addSheet': {
            'properties': {
                'title': sheet_name
            }
        }
    }

    response = service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={'requests': [request]}
    ).execute()

    new_sheet_id = response['replies'][0]['addSheet']['properties']['sheetId']
    print(f"Created new sheet: {sheet_name} (ID: {new_sheet_id})", file=sys.stderr)

    return new_sheet_id


def create_spreadsheet(service, title):
    """
    Create a new Google Spreadsheet.

    Args:
        service: Google Sheets API service
        title (str): Spreadsheet title

    Returns:
        str: Spreadsheet ID
    """
    spreadsheet = {
        'properties': {
            'title': title
        },
        'sheets': [
            {'properties': {'title': 'Master - All Schools'}},
        ]
    }

    spreadsheet = service.spreadsheets().create(body=spreadsheet, fields='spreadsheetId,spreadsheetUrl,sheets').execute()

    # Extract sheet IDs
    sheet_ids = {}
    for sheet in spreadsheet.get('sheets', []):
        title = sheet['properties']['title']
        sheet_id = sheet['properties']['sheetId']
        sheet_ids[title] = sheet_id

    return spreadsheet.get('spreadsheetId'), spreadsheet.get('spreadsheetUrl'), sheet_ids


def export_school_specific_sheet(service, spreadsheet_id, validated_data, school_name, sheet_id):
    """
    Export school-specific game data to dedicated tab.

    Args:
        service: Google Sheets API service
        spreadsheet_id (str): Spreadsheet ID
        validated_data (dict): Validated matches data
        school_name (str): Name of school for this tab
        sheet_id (int): Sheet ID for this tab
    """
    matches = validated_data.get('validated_matches', [])

    # Show ALL games, regardless of whether contacts were found
    # User needs visibility into every game
    all_games = matches

    # Create header row
    headers = [
        'Date', 'Time', 'Sport', 'Gender', 'Opponent', 'Venue',
        'Contact Name', 'Contact Title', 'Contact Email', 'Contact Phone',
        'Coaches Page URL',
        'Match Quality'
    ]

    # Create data rows
    rows = [headers]
    for match in all_games:
        # Use parsed_date if available (ISO format), otherwise fall back to original date
        date_value = match.get('parsed_date', '')
        if date_value:
            # Convert ISO format to clean date (2026-02-28T00:00:00 -> 2026-02-28)
            date_value = date_value.split('T')[0]
        else:
            date_value = match.get('date', '')

        # Use the real coaches page URL (the page we actually scraped contacts from)
        coaches_url = match.get('opponent_coaches_url', '')

        row = [
            date_value,
            normalize_time(match.get('time', '')),
            match.get('sport', ''),
            match.get('gender', ''),
            match.get('opponent', ''),
            match.get('venue', ''),
            match.get('contact_name', ''),
            match.get('contact_title', ''),
            match.get('contact_email', ''),
            match.get('contact_phone', ''),
            coaches_url,
            match.get('match_quality', ''),
        ]
        rows.append(row)

    # Clear existing data first to avoid column mismatches
    clear_range = f'{school_name}!A:Z'
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=clear_range
    ).execute()

    # Write to sheet (RAW prevents Google Sheets from converting dates/times)
    range_name = f'{school_name}!A1'
    body = {'values': rows}

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption='RAW',
        body=body
    ).execute()

    # Format header row (bold)
    requests = [{
        'repeatCell': {
            'range': {
                'sheetId': sheet_id,
                'startRowIndex': 0,
                'endRowIndex': 1
            },
            'cell': {
                'userEnteredFormat': {
                    'textFormat': {'bold': True},
                    'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}
                }
            },
            'fields': 'userEnteredFormat(textFormat,backgroundColor)'
        }
    }]

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={'requests': requests}
    ).execute()

    return len(all_games)


def export_game_by_game_sheet(service, spreadsheet_id, validated_data, sheet_id):
    """
    Export game-by-game contact list to Sheet 1.

    Args:
        service: Google Sheets API service
        spreadsheet_id (str): Spreadsheet ID
        validated_data (dict): Validated matches data
        sheet_id (int): Sheet ID for this tab
    """
    matches = validated_data.get('validated_matches', [])

    # Filter for successful matches only
    successful_matches = [m for m in matches if m.get('match_status') == 'success']

    # Create header row
    headers = [
        'Date', 'Time', 'Opponent', 'Sport', 'Venue',
        'Contact Name', 'Contact Title', 'Contact Email', 'Contact Phone',
        'Match Quality', 'Validation Status'
    ]

    # Create data rows
    rows = [headers]
    for match in successful_matches:
        row = [
            match.get('date', ''),
            match.get('time', ''),
            match.get('opponent', ''),
            match.get('sport', ''),
            match.get('venue', ''),
            match.get('contact_name', ''),
            match.get('contact_title', ''),
            match.get('contact_email', ''),
            match.get('contact_phone', ''),
            match.get('match_quality', ''),
            match.get('validation_status', ''),
        ]
        rows.append(row)

    # Write to sheet
    range_name = 'Game-by-Game Contacts!A1'
    body = {'values': rows}

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption='USER_ENTERED',  # Process HYPERLINK formulas
        body=body
    ).execute()

    # Format header row (bold)
    requests = [{
        'repeatCell': {
            'range': {
                'sheetId': sheet_id,
                'startRowIndex': 0,
                'endRowIndex': 1
            },
            'cell': {
                'userEnteredFormat': {
                    'textFormat': {'bold': True},
                    'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}
                }
            },
            'fields': 'userEnteredFormat(textFormat,backgroundColor)'
        }
    }]

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={'requests': requests}
    ).execute()

    return len(successful_matches)


def export_master_contacts_sheet(service, spreadsheet_id, cache_files, sheet_id):
    """
    Export master contacts cache to Sheet 2.

    Args:
        service: Google Sheets API service
        spreadsheet_id (str): Spreadsheet ID
        cache_files (list): List of cache file paths
        sheet_id (int): Sheet ID for this tab
    """
    # Load all cached contacts
    all_contacts = []

    for cache_file in cache_files:
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r') as f:
                    data = json.load(f)
                    school = data.get('school', 'Unknown')
                    sport = data.get('sport', 'Unknown')
                    staff = data.get('staff', [])

                    for member in staff:
                        all_contacts.append({
                            'school': school,
                            'sport': sport,
                            **member
                        })
            except Exception as e:
                print(f"Error loading {cache_file}: {e}", file=sys.stderr)

    # Create header row
    headers = ['School', 'Sport', 'Name', 'Title', 'Email', 'Phone']

    # Create data rows
    rows = [headers]
    for contact in all_contacts:
        row = [
            contact.get('school', ''),
            contact.get('sport', ''),
            contact.get('name', ''),
            contact.get('title', ''),
            contact.get('email', ''),
            contact.get('phone', ''),
        ]
        rows.append(row)

    # Write to sheet
    range_name = 'Master Contacts Cache!A1'
    body = {'values': rows}

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption='USER_ENTERED',  # Process HYPERLINK formulas
        body=body
    ).execute()

    # Format header row
    requests = [{
        'repeatCell': {
            'range': {
                'sheetId': sheet_id,
                'startRowIndex': 0,
                'endRowIndex': 1
            },
            'cell': {
                'userEnteredFormat': {
                    'textFormat': {'bold': True},
                    'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}
                }
            },
            'fields': 'userEnteredFormat(textFormat,backgroundColor)'
        }
    }]

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={'requests': requests}
    ).execute()

    return len(all_contacts)


def update_master_aggregate_sheet(service, spreadsheet_id, sheet_names, sheet_id):
    """
    Update Master - All Schools tab with aggregated data from all school tabs.

    Args:
        service: Google Sheets API service
        spreadsheet_id (str): Spreadsheet ID
        sheet_names (list): List of all sheet names
        sheet_id (int): Sheet ID for Master tab
    """
    all_games = []

    # Read data from all school tabs (skip Master and old tabs)
    school_tabs = [s for s in sheet_names if s not in ['Master - All Schools', 'Master Contacts Cache', 'Chronological View', 'Game-by-Game Contacts']]

    for school_tab in school_tabs:
        try:
            # Read all data from this school tab (A-L: Date through Match Quality)
            range_name = f'{school_tab}!A2:L'  # Skip header row, includes all columns
            result = service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=range_name
            ).execute()

            rows = result.get('values', [])

            for row in rows:
                if len(row) >= 5:  # Must have at least date, time, sport, gender, opponent
                    all_games.append({
                        'school': school_tab,
                        'date': row[0] if len(row) > 0 else '',
                        'time': row[1] if len(row) > 1 else '',
                        'sport': row[2] if len(row) > 2 else '',
                        'gender': row[3] if len(row) > 3 else '',
                        'opponent': row[4] if len(row) > 4 else '',
                        'venue': row[5] if len(row) > 5 else '',
                        'contact_name': row[6] if len(row) > 6 else '',
                        'contact_title': row[7] if len(row) > 7 else '',
                        'contact_email': row[8] if len(row) > 8 else '',
                        'contact_phone': row[9] if len(row) > 9 else '',
                        'coaches_page_url': row[10] if len(row) > 10 else '',
                        'match_quality': row[11] if len(row) > 11 else '',
                    })

        except Exception as e:
            print(f"Warning: Could not read {school_tab}: {e}", file=sys.stderr)

    # Sort chronologically by date
    def parse_date_for_sort(date_str):
        try:
            from datetime import datetime
            import re
            date_str = date_str.strip()
            if not date_str:
                return datetime.min
            # ISO format: "2026-02-24"
            if re.match(r'^\d{4}-\d{2}-\d{2}', date_str):
                return datetime.fromisoformat(date_str.split('T')[0])
            # Google Sheets formatted date: "2/28/2026" or "02/28/2026"
            if '/' in date_str:
                parts = date_str.split('/')
                if len(parts) == 3:
                    return datetime(int(parts[2]), int(parts[0]), int(parts[1]))
            # "Feb 6" or "Feb 6 (Fri)" format
            clean = re.sub(r'\s*\(.*?\)', '', date_str).strip()
            current_year = datetime.now().year
            return datetime.strptime(f"{clean} {current_year}", "%b %d %Y")
        except:
            return datetime.min

    all_games.sort(key=lambda x: parse_date_for_sort(x.get('date', '')))

    # Create header row
    headers = [
        'School', 'Date', 'Time', 'Sport', 'Gender', 'Opponent', 'Venue',
        'Contact Name', 'Contact Title', 'Contact Email', 'Contact Phone',
        'Coaches Page URL', 'Match Quality'
    ]

    # Create data rows
    rows = [headers]
    for game in all_games:
        # Plain URL from school tab (column K) - no formula wrapping
        coaches_url = game.get('coaches_page_url', '')

        row = [
            game.get('school', ''),
            game.get('date', ''),
            game.get('time', ''),
            game.get('sport', ''),
            game.get('gender', ''),
            game.get('opponent', ''),
            game.get('venue', ''),
            game.get('contact_name', ''),
            game.get('contact_title', ''),
            game.get('contact_email', ''),
            game.get('contact_phone', ''),
            coaches_url,
            game.get('match_quality', ''),
        ]
        rows.append(row)

    # Clear existing data
    clear_request = service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range='Master - All Schools!A:Z'
    ).execute()

    # Write to sheet (RAW prevents Google Sheets from converting dates/times)
    range_name = 'Master - All Schools!A1'
    body = {'values': rows}

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption='RAW',
        body=body
    ).execute()

    # Format header row
    requests = [{
        'repeatCell': {
            'range': {
                'sheetId': sheet_id,
                'startRowIndex': 0,
                'endRowIndex': 1
            },
            'cell': {
                'userEnteredFormat': {
                    'textFormat': {'bold': True},
                    'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}
                }
            },
            'fields': 'userEnteredFormat(textFormat,backgroundColor)'
        }
    }]

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={'requests': requests}
    ).execute()

    return len(all_games)


def export_chronological_sheet(service, spreadsheet_id, validated_data, sheet_id):
    """
    Export chronological aggregate view to Sheet 3.

    Args:
        service: Google Sheets API service
        spreadsheet_id (str): Spreadsheet ID
        validated_data (dict): Validated matches data
        sheet_id (int): Sheet ID for this tab
    """
    matches = validated_data.get('validated_matches', [])

    # Filter for successful matches and sort by parsed_date
    successful_matches = [m for m in matches if m.get('match_status') == 'success']
    successful_matches.sort(key=lambda x: x.get('parsed_date', ''))

    # Create header row
    headers = [
        'Date', 'Time', 'Sport', 'Opponent', 'Venue',
        'Contact Name', 'Contact Email', 'Contact Phone'
    ]

    # Create data rows
    rows = [headers]
    for match in successful_matches:
        row = [
            match.get('date', ''),
            match.get('time', ''),
            match.get('sport', ''),
            match.get('opponent', ''),
            match.get('venue', ''),
            match.get('contact_name', ''),
            match.get('contact_email', ''),
            match.get('contact_phone', ''),
        ]
        rows.append(row)

    # Write to sheet
    range_name = 'Chronological View!A1'
    body = {'values': rows}

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption='USER_ENTERED',  # Process HYPERLINK formulas
        body=body
    ).execute()

    # Format header row
    requests = [{
        'repeatCell': {
            'range': {
                'sheetId': sheet_id,
                'startRowIndex': 0,
                'endRowIndex': 1
            },
            'cell': {
                'userEnteredFormat': {
                    'textFormat': {'bold': True},
                    'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}
                }
            },
            'fields': 'userEnteredFormat(textFormat,backgroundColor)'
        }
    }]

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={'requests': requests}
    ).execute()

    return len(successful_matches)


def main():
    parser = argparse.ArgumentParser(
        description="Export validated contacts to Google Sheets"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to validated contacts JSON file"
    )
    parser.add_argument(
        "--spreadsheet-name",
        required=True,
        help="Name for the Google Spreadsheet"
    )
    parser.add_argument(
        "--credentials",
        default="credentials.json",
        help="Path to Google API credentials file (default: credentials.json)"
    )
    parser.add_argument(
        "--cache-dir",
        default=".tmp/cache/contacts",
        help="Directory containing cached contact files (default: .tmp/cache/contacts)"
    )
    parser.add_argument(
        "--spreadsheet-id",
        help="Existing spreadsheet ID to update (creates new if not provided)"
    )
    parser.add_argument(
        "--school-name",
        help="School name for this data (used for school-specific tab)"
    )

    args = parser.parse_args()

    # Load validated data
    try:
        with open(args.input, 'r') as f:
            validated_data = json.load(f)
    except Exception as e:
        print(f"Error loading input data: {e}", file=sys.stderr)
        sys.exit(1)

    # Get school name from validated data if not provided
    school_name = args.school_name
    if not school_name:
        school_name = validated_data.get('school', 'Unknown School')

    # Get credentials
    print("Authenticating with Google...", file=sys.stderr)
    creds = get_credentials(args.credentials)

    # Build service
    service = build('sheets', 'v4', credentials=creds)

    spreadsheet_id = None
    spreadsheet_url = None
    sheet_ids = {}

    # Check if updating existing spreadsheet or creating new
    if args.spreadsheet_id:
        # Get existing spreadsheet
        print(f"Opening existing spreadsheet: {args.spreadsheet_id}...", file=sys.stderr)
        spreadsheet_info = get_spreadsheet(service, args.spreadsheet_id)

        if not spreadsheet_info:
            print("Error: Could not access spreadsheet. Creating new one instead.", file=sys.stderr)
            spreadsheet_id, spreadsheet_url, sheet_ids = create_spreadsheet(service, args.spreadsheet_name)
        else:
            spreadsheet_id = spreadsheet_info['spreadsheet_id']
            spreadsheet_url = spreadsheet_info['spreadsheet_url']
            sheet_ids = spreadsheet_info['sheet_ids']
            print(f"  Found {len(sheet_ids)} existing sheets", file=sys.stderr)

    else:
        # Create new spreadsheet
        print(f"Creating new spreadsheet: {args.spreadsheet_name}...", file=sys.stderr)
        spreadsheet_id, spreadsheet_url, sheet_ids = create_spreadsheet(service, args.spreadsheet_name)

    # Ensure Master tab exists
    master_sheet_id = create_sheet_if_missing(
        service, spreadsheet_id, 'Master - All Schools', sheet_ids
    )
    sheet_ids['Master - All Schools'] = master_sheet_id

    # Ensure school-specific tab exists
    school_sheet_id = create_sheet_if_missing(
        service, spreadsheet_id, school_name, sheet_ids
    )
    sheet_ids[school_name] = school_sheet_id

    # Export to school-specific tab
    print(f"Exporting data to {school_name} tab...", file=sys.stderr)
    games_count = export_school_specific_sheet(
        service, spreadsheet_id, validated_data, school_name, school_sheet_id
    )
    print(f"  Exported {games_count} games", file=sys.stderr)

    # Update Master - All Schools tab with aggregated data
    print("Updating Master - All Schools tab...", file=sys.stderr)
    # Get updated list of all sheets
    spreadsheet_info = get_spreadsheet(service, spreadsheet_id)
    all_sheet_names = spreadsheet_info['sheets']

    master_games_count = update_master_aggregate_sheet(
        service, spreadsheet_id, all_sheet_names, master_sheet_id
    )
    print(f"  Master tab now contains {master_games_count} total games", file=sys.stderr)

    # Output result
    result = {
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_url": spreadsheet_url,
        "school_tab": school_name,
        "school_games": games_count,
        "master_total_games": master_games_count,
        "success": True
    }

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"SUCCESS! Spreadsheet updated:", file=sys.stderr)
    print(f"{spreadsheet_url}", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"School tab: {school_name} ({games_count} games)", file=sys.stderr)
    print(f"Master tab: {master_games_count} total games from all schools", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
