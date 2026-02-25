#!/usr/bin/env python3
"""
Tool: notion_convert_to_order.py
Purpose: Convert responded emails into Catering Operations orders when
         "Convert to Order" checkbox is checked.

Workflow:
1. Query Email Queue for entries with "Convert to Order" checked
2. Clear the checkbox immediately (anti-duplicate)
3. Check Catering Operations DB for existing order (duplicate protection)
4. Create order in Catering Operations & Sales (data_source_id API)
5. Update Email Queue Status → "Booked", Game Outreach Status → "Booked"

Undo:
- "Undo Order" on Email Queue → archives Catering Ops order, reverts statuses
- "Undo Outreach" on Email Queue → full reversal including contact cleanup
- "Undo Order" on Catering Ops order → archives order, reverts statuses

Usage:
    python tools/notion_convert_to_order.py              # Process all flagged
    python tools/notion_convert_to_order.py --dry-run    # Preview without changes
"""

import argparse
import json
import os
import sys
from datetime import datetime

import requests as http_requests
from notion_client import Client
from notion_client.errors import APIResponseError
from dotenv import load_dotenv

load_dotenv()


def log(message):
    """Log with timestamp."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}", file=sys.stderr)


def extract_text(rich_text_array):
    """Extract plain text from Notion rich text array."""
    if not rich_text_array:
        return ''
    return ''.join(item.get('plain_text', '') for item in rich_text_array)


def extract_title(title_array):
    """Extract title text from Notion title property."""
    if not title_array:
        return ''
    return ''.join(item.get('plain_text', '') for item in title_array)


def get_flagged_emails(notion, email_queue_db):
    """Get all emails with Convert to Order checked."""
    try:
        response = notion.databases.query(
            database_id=email_queue_db,
            filter={
                "property": "Convert to Order",
                "checkbox": {"equals": True}
            }
        )
        return response['results']
    except APIResponseError as e:
        log(f"Error querying Email Queue: {e}")
        return []


def get_game_details(notion, game_page_id):
    """Fetch game details for order creation."""
    try:
        game = notion.pages.retrieve(page_id=game_page_id)
        props = game['properties']

        data = {
            'game_id': game_page_id,
            'sport': props.get('Sport', {}).get('select', {}).get('name', ''),
            'venue': extract_text(props.get('Venue', {}).get('rich_text', [])),
            'game_date': '',
            'home_school_id': None,
            'home_school': '',
            'away_school_id': None,
            'away_school': '',
        }

        # Game date
        if props.get('Game Date', {}).get('date'):
            data['game_date'] = props['Game Date']['date'].get('start', '')

        # Home team
        if props.get('Home Team', {}).get('relation'):
            data['home_school_id'] = props['Home Team']['relation'][0]['id']
            home = notion.pages.retrieve(page_id=data['home_school_id'])
            data['home_school'] = extract_title(
                home['properties'].get('School Name', {}).get('title', [])
            )

        # Away team
        if props.get('Away Team', {}).get('relation'):
            data['away_school_id'] = props['Away Team']['relation'][0]['id']
            away = notion.pages.retrieve(page_id=data['away_school_id'])
            data['away_school'] = extract_title(
                away['properties'].get('School Name', {}).get('title', [])
            )

        return data
    except APIResponseError as e:
        log(f"Error fetching game {game_page_id}: {e}")
        return None


def create_order(notion, orders_db, email_props, game_data):
    """Create an Order record from email and game data."""
    # Build order ID: school abbreviation + game date
    school_name = game_data.get('away_school') or game_data.get('home_school') or 'Unknown'
    game_date = game_data.get('game_date', '')
    order_id = f"ORD-{school_name[:20]}-{game_date}" if game_date else f"ORD-{school_name[:20]}"

    # Get response notes from email
    notes = extract_text(email_props.get('Response Notes', {}).get('rich_text', []))

    properties = {
        "Order ID": {"title": [{"text": {"content": order_id}}]},
        "Order Date": {"date": {"start": datetime.now().strftime('%Y-%m-%d')}},
        "Payment Status": {"select": {"name": "Pending"}},
    }

    # Delivery date from game date
    if game_date:
        properties["Delivery Date"] = {"date": {"start": game_date}}

    # Delivery location from venue
    if game_data.get('venue'):
        properties["Delivery Location"] = {"rich_text": [{"text": {"content": game_data['venue']}}]}

    # Notes from response
    if notes:
        properties["Notes"] = {"rich_text": [{"text": {"content": notes}}]}

    # School relation (away team = the school we're catering for)
    school_id = game_data.get('away_school_id') or game_data.get('home_school_id')
    if school_id:
        properties["School"] = {"relation": [{"id": school_id}]}

    # Contact relation
    contact_rel = email_props.get('Contact', {}).get('relation', [])
    if contact_rel:
        properties["Contact"] = {"relation": [{"id": contact_rel[0]['id']}]}

    # Game relation
    if game_data.get('game_id'):
        properties["Game"] = {"relation": [{"id": game_data['game_id']}]}

    try:
        response = notion.pages.create(
            parent={"database_id": orders_db},
            properties=properties
        )
        return response['id'], order_id
    except APIResponseError as e:
        log(f"Error creating order: {e}")
        return None, None


def update_email_booked(notion, email_page_id):
    """Update Email Queue Status to Booked and clear Convert to Order flag."""
    try:
        notion.pages.update(
            page_id=email_page_id,
            properties={
                "Status": {"select": {"name": "Booked"}},
                "Convert to Order": {"checkbox": False},
            }
        )
        return True
    except APIResponseError as e:
        log(f"Error updating email {email_page_id}: {e}")
        return False


def update_game_booked(notion, game_page_id):
    """Update Game Outreach Status to Booked."""
    try:
        notion.pages.update(
            page_id=game_page_id,
            properties={
                "Outreach Status": {"select": {"name": "Booked"}}
            }
        )
        return True
    except APIResponseError as e:
        log(f"Error updating game {game_page_id}: {e}")
        return False


def clear_convert_flag(notion, email_page_id):
    """Clear the Convert to Order checkbox (fallback if update_email_booked fails partially)."""
    try:
        notion.pages.update(
            page_id=email_page_id,
            properties={
                "Convert to Order": {"checkbox": False}
            }
        )
    except APIResponseError:
        pass


def find_dashboard_contact_by_email(notion, contact_email):
    """Find a Dashboard Contact by email match. Returns page ID or None."""
    dashboard_contacts_db = os.getenv('NOTION_DASHBOARD_CONTACTS_DB')
    if not dashboard_contacts_db or not contact_email:
        return None

    try:
        response = notion.databases.query(
            database_id=dashboard_contacts_db,
            filter={"property": "Email Address", "email": {"equals": contact_email.lower().strip()}}
        )
        if response['results']:
            return response['results'][0]['id']
    except APIResponseError:
        pass
    return None


def save_pending_catering_order(order_data):
    """Save pending catering order to JSON file for MCP-based creation."""
    pending_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                '.tmp', 'pending_catering_orders.json')
    os.makedirs(os.path.dirname(pending_file), exist_ok=True)

    pending = []
    if os.path.exists(pending_file):
        try:
            with open(pending_file, 'r') as f:
                pending = json.load(f)
        except (json.JSONDecodeError, IOError):
            pending = []

    pending.append(order_data)
    with open(pending_file, 'w') as f:
        json.dump(pending, f, indent=2)
    return pending_file


def save_catering_order_mapping(game_id, page_id):
    """Save game_id -> catering_order_page_id mapping for undo support."""
    mapping_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                '.tmp', 'catering_order_mapping.json')
    os.makedirs(os.path.dirname(mapping_file), exist_ok=True)
    mapping = {}
    if os.path.exists(mapping_file):
        try:
            with open(mapping_file, 'r') as f:
                mapping = json.load(f)
        except (json.JSONDecodeError, IOError):
            mapping = {}
    mapping[game_id] = page_id
    with open(mapping_file, 'w') as f:
        json.dump(mapping, f, indent=2)


def create_dashboard_catering_order(notion, email_props, game_data):
    """Create Dashboard Catering Order directly via Notion API using data_source_id.

    The Catering Orders DB is a multi-source Notion database. We use a raw HTTP
    call with data_source_id in the parent object, which the standard SDK doesn't
    support. Falls back to pending file if the API call fails.
    """
    catering_ds = os.getenv('NOTION_CATERING_ORDERS_DS')
    log(f"    Dashboard order: NOTION_CATERING_ORDERS_DS={'set (' + catering_ds[:8] + '...)' if catering_ds else 'NOT SET'}")
    if not catering_ds:
        log("    WARNING: NOTION_CATERING_ORDERS_DS not set — skipping Dashboard Catering Order")
        return None

    # Build order name: "School MM.DD"
    school = game_data.get('away_school') or game_data.get('home_school') or 'Unknown'
    game_date = game_data.get('game_date', '')
    if game_date:
        try:
            dt = datetime.strptime(game_date, '%Y-%m-%d')
            order_name = f"{school} {dt.strftime('%m.%d')}"
        except ValueError:
            order_name = f"{school} {game_date}"
    else:
        order_name = school

    # Get contact email for Dashboard Contact matching
    contact_email = ''
    contact_rel = email_props.get('Contact', {}).get('relation', [])
    dashboard_contact_id = None
    if contact_rel:
        try:
            contact = notion.pages.retrieve(page_id=contact_rel[0]['id'])
            contact_email = contact['properties'].get('Email', {}).get('email', '') or ''
        except APIResponseError:
            pass

    if contact_email:
        dashboard_contact_id = find_dashboard_contact_by_email(notion, contact_email)

    # Get response notes
    notes = extract_text(email_props.get('Response Notes', {}).get('rich_text', []))

    game_id = game_data.get('game_id', '')

    # Try direct API call with data_source_id
    api_key = os.getenv('NOTION_API_KEY')
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'Notion-Version': '2022-06-28'
    }

    properties = {
        'Order Name': {'title': [{'text': {'content': order_name}}]},
        'Order Status': {'select': {'name': 'New Lead'}},
        'Order Type': {'select': {'name': 'New Client'}},
        'Order Platform': {'select': {'name': 'Sports Auto Outreach'}},
        'Delivery Method': {'select': {'name': 'Not Assigned Yet'}},
    }

    if game_date:
        properties['Delivery Date & Time'] = {'date': {'start': game_date}}
    if game_data.get('venue'):
        properties['Delivery Address'] = {'rich_text': [{'text': {'content': game_data['venue']}}]}
    if notes:
        properties['Notes'] = {'rich_text': [{'text': {'content': notes[:2000]}}]}
    if dashboard_contact_id:
        properties['Contacts'] = {'relation': [{'id': dashboard_contact_id}]}

    payload = {
        'parent': {'type': 'data_source_id', 'data_source_id': catering_ds},
        'properties': properties
    }

    log(f"    Calling Notion API: POST /v1/pages with data_source_id={catering_ds[:8]}...")
    try:
        response = http_requests.post(
            'https://api.notion.com/v1/pages',
            headers=headers,
            json=payload,
            timeout=30
        )

        log(f"    API response: {response.status_code}")
        if response.status_code in (200, 201):
            page_id = response.json()['id']
            save_catering_order_mapping(game_id, page_id)
            log(f"    Created Dashboard Catering Order: {order_name} ({page_id})")
            return {'page_id': page_id, 'order_name': order_name, 'direct': True}
        else:
            log(f"    Direct API failed ({response.status_code}): {response.text[:500]}")
            log(f"    Falling back to pending file...")
    except Exception as e:
        log(f"    Direct API error: {type(e).__name__}: {e}")
        log(f"    Falling back to pending file...")

    # Fallback: save to pending file for MCP creation
    order_data = {
        'data_source_id': catering_ds,
        'order_name': order_name,
        'delivery_date': game_date,
        'delivery_address': game_data.get('venue', ''),
        'notes': notes,
        'dashboard_contact_id': dashboard_contact_id,
        'contact_email': contact_email,
        'school': school,
        'game_id': game_id,
        'order_platform': 'Sports Auto Outreach',
        'created_at': datetime.now().isoformat(),
    }
    pending_file = save_pending_catering_order(order_data)
    log(f"    Saved pending Dashboard Catering Order: {order_name}")
    log(f"    (Fallback — run /create-catering-orders. File: {pending_file})")
    return {'pending': True, 'order_name': order_name}


def find_orders_for_game(notion, orders_db, game_id):
    """Find Sports Automation Order(s) linked to a game. Returns list of page IDs."""
    try:
        response = notion.databases.query(
            database_id=orders_db,
            filter={"property": "Game", "relation": {"contains": game_id}}
        )
        return [r['id'] for r in response['results']]
    except APIResponseError as e:
        log(f"    Error finding orders for game {game_id}: {e}")
        return []


def remove_pending_catering_order(game_date, school):
    """Remove a pending catering order from the JSON file by matching school + date."""
    pending_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                '.tmp', 'pending_catering_orders.json')
    if not os.path.exists(pending_file):
        return False

    try:
        with open(pending_file, 'r') as f:
            pending = json.load(f)
    except (json.JSONDecodeError, IOError):
        return False

    original_count = len(pending)
    pending = [
        p for p in pending
        if not (p.get('delivery_date') == game_date and p.get('school') == school)
    ]

    if len(pending) < original_count:
        with open(pending_file, 'w') as f:
            json.dump(pending, f, indent=2)
        return True
    return False


def find_catering_order_by_query(notion, game_data):
    """Find a Catering Operations order by searching for the expected order name.

    Uses Notion search API since the Catering Operations DB is multi-source
    and databases.query doesn't work on it. Validates results by checking
    parent database and Order Platform property.
    """
    catering_db = os.getenv('NOTION_CATERING_ORDERS_DB')
    if not catering_db or not game_data:
        return None

    school = game_data.get('away_school') or game_data.get('home_school') or ''
    game_date = game_data.get('game_date', '')
    if not school or not game_date:
        return None

    try:
        dt = datetime.strptime(game_date, '%Y-%m-%d')
        expected_name = "{} {}".format(school, dt.strftime('%m.%d'))
    except ValueError:
        expected_name = school

    # Normalize the catering DB ID (remove dashes for comparison)
    catering_db_norm = catering_db.replace('-', '')

    try:
        response = notion.search(
            query=expected_name,
            filter={"property": "object", "value": "page"},
        )
        for page in response['results']:
            if page.get('archived', False):
                continue
            # Check parent is our Catering Operations DB
            parent_db = page.get('parent', {}).get('database_id', '').replace('-', '')
            if parent_db != catering_db_norm:
                continue
            # Check title matches exactly
            title = ''
            for pval in page['properties'].values():
                if pval.get('type') == 'title':
                    title = ''.join(t.get('plain_text', '') for t in pval.get('title', []))
                    break
            if title == expected_name:
                # Verify it's a Sports Auto Outreach order
                platform = page['properties'].get('Order Platform', {}).get('select', {})
                if platform and platform.get('name') == 'Sports Auto Outreach':
                    return page['id']
    except APIResponseError as e:
        log(f"    Error searching for Catering Operations order: {e}")

    return None


def archive_dashboard_catering_order(notion, game_id, game_data=None):
    """Archive a Catering Operations order. Tries mapping file first, then DB query."""
    # Try 1: mapping file (fast, works locally)
    mapping_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                '.tmp', 'catering_order_mapping.json')
    page_id = None

    if os.path.exists(mapping_file):
        try:
            with open(mapping_file, 'r') as f:
                mapping = json.load(f)
            page_id = mapping.get(game_id)
        except (json.JSONDecodeError, IOError):
            pass

    # Try 2: query DB by order name (reliable fallback for Render)
    if not page_id and game_data:
        page_id = find_catering_order_by_query(notion, game_data)

    if not page_id:
        log(f"    No Catering Operations order found to archive")
        return False

    try:
        notion.pages.update(page_id=page_id, archived=True)
        log(f"    Archived Catering Operations order: {page_id}")
        # Clean up mapping file
        if os.path.exists(mapping_file):
            try:
                with open(mapping_file, 'r') as f:
                    mapping = json.load(f)
                mapping.pop(game_id, None)
                with open(mapping_file, 'w') as f:
                    json.dump(mapping, f, indent=2)
            except (json.JSONDecodeError, IOError):
                pass
        return True
    except APIResponseError as e:
        log(f"    Error archiving Catering Operations order {page_id}: {e}")
        return False


def process_undo_orders(notion, email_queue_db, orders_db, games_db):
    """Process emails with 'Undo Order' checked. Reverts Booked → Responded.

    Archives the Catering Operations order and reverts email/game statuses.
    Note: orders_db param kept for backward compat but is no longer used.
    """
    try:
        response = notion.databases.query(
            database_id=email_queue_db,
            filter={"property": "Undo Order", "checkbox": {"equals": True}}
        )
        flagged = response['results']
    except APIResponseError as e:
        log(f"Error querying Undo Order: {e}")
        return {'processed': 0, 'undone': 0, 'failed': 0}

    if not flagged:
        return {'processed': 0, 'undone': 0, 'failed': 0}

    log(f"Found {len(flagged)} email(s) flagged for order undo")
    stats = {'processed': 0, 'undone': 0, 'failed': 0}

    for email_page in flagged:
        stats['processed'] += 1
        props = email_page['properties']
        subject = extract_text(props.get('Subject', {}).get('rich_text', []))
        log(f"  Undoing order: {subject[:60]}")

        game_rel = props.get('Game', {}).get('relation', [])
        if not game_rel:
            log(f"    No game linked — clearing flag")
            try:
                notion.pages.update(page_id=email_page['id'],
                                    properties={"Undo Order": {"checkbox": False}})
            except APIResponseError:
                pass
            stats['failed'] += 1
            continue

        game_id = game_rel[0]['id']

        # 1. Get game data for DB query fallback
        game_data = get_game_details(notion, game_id)

        # 2. Remove pending catering order from JSON
        if game_data:
            school = game_data.get('away_school') or game_data.get('home_school') or ''
            game_date = game_data.get('game_date', '')
            if remove_pending_catering_order(game_date, school):
                log(f"    Removed pending Catering Order")

        # 3. Archive Catering Operations order (mapping file → DB query fallback)
        archive_dashboard_catering_order(notion, game_id, game_data=game_data)

        # 4. Revert Email Queue Status → Responded + clear checkbox
        try:
            notion.pages.update(
                page_id=email_page['id'],
                properties={
                    "Status": {"select": {"name": "Responded"}},
                    "Undo Order": {"checkbox": False},
                }
            )
            log(f"    Email status → Responded")
        except APIResponseError as e:
            log(f"    Error reverting email status: {e}")

        # 5. Revert Game Outreach Status → Responded
        try:
            notion.pages.update(
                page_id=game_id,
                properties={"Outreach Status": {"select": {"name": "Responded"}}}
            )
            log(f"    Game status → Responded")
        except APIResponseError as e:
            log(f"    Error reverting game status: {e}")

        stats['undone'] += 1

    return stats


def process_undo_outreach(notion, email_queue_db, orders_db, games_db, contacts_db):
    """Process emails with 'Undo Outreach' checked. Removes email, resets game to Not Contacted."""
    try:
        response = notion.databases.query(
            database_id=email_queue_db,
            filter={"property": "Undo Outreach", "checkbox": {"equals": True}}
        )
        flagged = response['results']
    except APIResponseError as e:
        log(f"Error querying Undo Outreach: {e}")
        return {'processed': 0, 'undone': 0, 'failed': 0}

    if not flagged:
        return {'processed': 0, 'undone': 0, 'failed': 0}

    log(f"Found {len(flagged)} email(s) flagged for outreach undo")
    stats = {'processed': 0, 'undone': 0, 'failed': 0}

    for email_page in flagged:
        stats['processed'] += 1
        props = email_page['properties']
        subject = extract_text(props.get('Subject', {}).get('rich_text', []))
        status = props.get('Status', {}).get('select', {}).get('name', '')
        log(f"  Undoing outreach: {subject[:60]} (status: {status})")

        game_rel = props.get('Game', {}).get('relation', [])
        game_id = game_rel[0]['id'] if game_rel else None

        # If Booked, undo the order first
        if status == 'Booked' and game_id:
            log(f"    Status is Booked — undoing order first")
            game_data = get_game_details(notion, game_id)
            if game_data:
                school = game_data.get('away_school') or game_data.get('home_school') or ''
                game_date = game_data.get('game_date', '')
                remove_pending_catering_order(game_date, school)

            archive_dashboard_catering_order(notion, game_id, game_data=game_data)

        # Clear Contact's Last Emailed so dedup doesn't block re-outreach
        contact_rel = props.get('Contact', {}).get('relation', [])
        if contact_rel:
            try:
                notion.pages.update(
                    page_id=contact_rel[0]['id'],
                    properties={"Last Emailed": {"date": None}}
                )
                log(f"    Cleared contact Last Emailed")
            except APIResponseError as e:
                log(f"    Error clearing Last Emailed: {e}")

        # Reset Game Outreach Status → Not Contacted
        if game_id:
            try:
                notion.pages.update(
                    page_id=game_id,
                    properties={"Outreach Status": {"select": {"name": "Not Contacted"}}}
                )
                log(f"    Game status → Not Contacted")
            except APIResponseError as e:
                log(f"    Error resetting game status: {e}")

        # Archive the Email Queue entry
        try:
            notion.pages.update(page_id=email_page['id'], archived=True)
            log(f"    Email queue entry archived")
        except APIResponseError as e:
            log(f"    Error archiving email: {e}")

        stats['undone'] += 1

    return stats


def process_sports_order_undo(notion, orders_db, games_db, email_queue_db):
    """DEPRECATED — Sports Orders DB is no longer used for new orders.

    Kept for backward compat: if any old Sports Orders still have Undo Order checked,
    this will archive them. No new orders are created in this DB.
    """
    if not orders_db:
        return {'processed': 0, 'undone': 0, 'failed': 0}

    try:
        response = notion.databases.query(
            database_id=orders_db,
            filter={"property": "Undo Order", "checkbox": {"equals": True}}
        )
        flagged = response['results']
    except APIResponseError as e:
        log(f"Error querying Sports Orders for Undo Order: {e}")
        return {'processed': 0, 'undone': 0, 'failed': 0}

    if not flagged:
        return {'processed': 0, 'undone': 0, 'failed': 0}

    log(f"Found {len(flagged)} legacy Sports Order(s) flagged for undo")
    stats = {'processed': 0, 'undone': 0, 'failed': 0}

    for order_page in flagged:
        stats['processed'] += 1
        props = order_page['properties']
        order_id = extract_title(props.get('Order ID', {}).get('title', []))
        log(f"  Legacy Sports Order undo: {order_id}")

        # Archive the legacy Sports Order
        try:
            notion.pages.update(page_id=order_page['id'], archived=True)
            log(f"    Archived legacy Sports Order")
        except APIResponseError as e:
            log(f"    Error archiving Sports Order: {e}")
            stats['failed'] += 1
            continue

        stats['undone'] += 1

    return stats


def process_dashboard_undo_orders(notion, orders_db, games_db, email_queue_db):
    """Process 'Undo Order' checked on Catering Operations orders.

    Iterates the mapping file to find catering orders created by Sports Auto Outreach,
    checks if their Undo Order checkbox is set, and reverses the full flow.
    """
    mapping_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                '.tmp', 'catering_order_mapping.json')
    if not os.path.exists(mapping_file):
        return {'processed': 0, 'undone': 0, 'failed': 0}

    try:
        with open(mapping_file, 'r') as f:
            mapping = json.load(f)
    except (json.JSONDecodeError, IOError):
        return {'processed': 0, 'undone': 0, 'failed': 0}

    if not mapping:
        return {'processed': 0, 'undone': 0, 'failed': 0}

    stats = {'processed': 0, 'undone': 0, 'failed': 0}
    to_remove = []

    for game_id, catering_page_id in mapping.items():
        # Check if catering order has Undo Order checked
        try:
            page = notion.pages.retrieve(page_id=catering_page_id)
        except APIResponseError:
            continue

        if page.get('archived', False):
            to_remove.append(game_id)
            continue

        undo_checked = page.get('properties', {}).get('Undo Order', {}).get('checkbox', False)
        if not undo_checked:
            continue

        order_name = extract_title(
            page.get('properties', {}).get('Order Name', {}).get('title', []))
        log(f"  Catering undo: {order_name} (game: {game_id[:8]}...)")
        stats['processed'] += 1

        # 1. Archive the Catering Operations order
        try:
            notion.pages.update(page_id=catering_page_id, archived=True)
            log(f"    Archived Catering Operations order")
            to_remove.append(game_id)
        except APIResponseError as e:
            log(f"    Error archiving catering order: {e}")
            stats['failed'] += 1
            continue

        # 2. Revert Game Outreach Status → Responded
        try:
            notion.pages.update(
                page_id=game_id,
                properties={"Outreach Status": {"select": {"name": "Responded"}}}
            )
            log(f"    Game status -> Responded")
        except APIResponseError as e:
            log(f"    Error reverting game status: {e}")

        # 3. Revert Email Queue entry (find by Game relation)
        try:
            eq_response = notion.databases.query(
                database_id=email_queue_db,
                filter={"property": "Game", "relation": {"contains": game_id}}
            )
            for eq in eq_response['results']:
                eq_status = eq['properties'].get('Status', {}).get('select', {})
                if eq_status and eq_status.get('name') == 'Booked':
                    notion.pages.update(
                        page_id=eq['id'],
                        properties={
                            "Status": {"select": {"name": "Responded"}},
                            "Undo Order": {"checkbox": False}
                        }
                    )
                    log(f"    Email status -> Responded")
        except APIResponseError as e:
            log(f"    Error reverting email status: {e}")

        stats['undone'] += 1

    # Clean up mapping file
    if to_remove:
        for gid in to_remove:
            mapping.pop(gid, None)
        with open(mapping_file, 'w') as f:
            json.dump(mapping, f, indent=2)

    return stats


def process_flagged_conversions(notion, email_queue_db, orders_db, games_db, dry_run=False):
    """Main processing loop. Find flagged emails, create Catering Operations orders.

    Flow:
    1. Clear Convert to Order flag immediately (anti-duplicate)
    2. Check Catering Operations DB for existing order (duplicate protection)
    3. Create order in Catering Operations & Sales (the ONLY order target)
    4. Mark Email → Booked, Game → Booked

    Note: orders_db param kept for backward compat but is no longer used.
    All orders go directly to Catering Operations & Sales.
    """
    flagged = get_flagged_emails(notion, email_queue_db)
    log(f"Found {len(flagged)} email(s) flagged for order conversion")

    stats = {'processed': 0, 'created': 0, 'failed': 0, 'skipped_duplicate': 0}

    for email_page in flagged:
        stats['processed'] += 1
        props = email_page['properties']
        subject = extract_text(props.get('Subject', {}).get('rich_text', []))
        log(f"  Processing: {subject[:60]}")

        # IMMEDIATELY clear the Convert to Order flag to prevent duplicate processing
        # on the next cron cycle. This is the single most important anti-duplicate step.
        clear_convert_flag(notion, email_page['id'])

        # Get linked game
        game_rel = props.get('Game', {}).get('relation', [])
        if not game_rel:
            log(f"    No game linked — skipping")
            stats['failed'] += 1
            continue

        game_id = game_rel[0]['id']
        game_data = get_game_details(notion, game_id)
        if not game_data:
            log(f"    Could not fetch game details — skipping")
            stats['failed'] += 1
            continue

        school = game_data.get('away_school') or game_data.get('home_school') or 'Unknown'
        log(f"    School: {school}, Date: {game_data.get('game_date', 'TBA')}")

        # DUPLICATE CHECK: Does a Catering Operations order already exist?
        existing_catering = find_catering_order_by_query(notion, game_data)
        if existing_catering:
            log(f"    DUPLICATE: Catering order already exists ({existing_catering[:8]}...) — skipping")
            # Still mark as booked in case a previous run failed mid-way
            update_email_booked(notion, email_page['id'])
            update_game_booked(notion, game_id)
            stats['skipped_duplicate'] += 1
            continue

        if dry_run:
            log(f"    [DRY RUN] Would create Catering Operations order and update statuses")
            continue

        # Create order in Catering Operations & Sales (the ONLY order target)
        catering_result = create_dashboard_catering_order(notion, props, game_data)
        if not catering_result or not catering_result.get('direct'):
            log(f"    Failed to create Catering Operations order")
            stats['failed'] += 1
            continue

        log(f"    Created Catering Operations order: {catering_result.get('order_name', '?')}")

        # Update email status to Booked
        update_email_booked(notion, email_page['id'])

        # Update game outreach status to Booked
        update_game_booked(notion, game_id)

        stats['created'] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(description="Convert emails to Catering Operations orders")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be created without making changes")
    args = parser.parse_args()

    notion = Client(auth=os.getenv('NOTION_API_KEY'))
    email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB')
    orders_db = os.getenv('NOTION_ORDERS_DB')  # Legacy, optional
    games_db = os.getenv('NOTION_GAMES_DB')

    if not all([email_queue_db, games_db]):
        log("Error: Missing required database IDs (NOTION_EMAIL_QUEUE_DB, NOTION_GAMES_DB)")
        sys.exit(1)

    if not os.getenv('NOTION_CATERING_ORDERS_DS'):
        log("Error: NOTION_CATERING_ORDERS_DS not set — required for order creation")
        sys.exit(1)

    stats = process_flagged_conversions(notion, email_queue_db, orders_db, games_db,
                                         dry_run=args.dry_run)

    log(f"")
    log(f"{'='*50}")
    mode = "[DRY RUN] " if args.dry_run else ""
    log(f"{mode}ORDER CONVERSION COMPLETE")
    log(f"Processed: {stats['processed']}")
    log(f"Created: {stats['created']}")
    if stats.get('skipped_duplicate', 0) > 0:
        log(f"Duplicates skipped: {stats['skipped_duplicate']}")
    log(f"Failed: {stats['failed']}")
    log(f"{'='*50}")

    print(json.dumps({"success": True, **stats}, indent=2))


if __name__ == "__main__":
    main()
