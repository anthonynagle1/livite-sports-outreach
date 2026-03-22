"""Shared Notion client + helper functions for extracting data from Notion responses."""

import os
import logging
from datetime import datetime

from notion_client import Client

logger = logging.getLogger(__name__)

_client = None


def get_client():
    """Get or create a shared Notion client singleton."""
    global _client
    if _client is None:
        api_key = os.getenv('NOTION_API_KEY')
        if not api_key:
            raise RuntimeError('NOTION_API_KEY not set')
        _client = Client(auth=api_key)
    return _client


def get_db_id(name):
    """Get a Notion database ID from env vars.

    Accepts: 'games', 'schools', 'contacts', 'email_queue', 'templates', 'orders'
    """
    mapping = {
        'games': 'NOTION_GAMES_DB',
        'schools': 'NOTION_SCHOOLS_DB',
        'contacts': 'NOTION_CONTACTS_DB',
        'email_queue': 'NOTION_EMAIL_QUEUE_DB',
        'templates': 'NOTION_TEMPLATES_DB',
        'orders': 'NOTION_ORDERS_DB',
    }
    env_var = mapping.get(name)
    if not env_var:
        raise ValueError(f'Unknown database: {name}')
    val = os.getenv(env_var)
    if not val:
        raise RuntimeError(f'{env_var} not set')
    return val


# ── Property extraction helpers ──────────────────────────


def extract_title(title_list):
    """Extract plain text from a Notion title property."""
    if not title_list:
        return ''
    return ''.join(t.get('plain_text', '') for t in title_list)


def extract_rich_text(prop):
    """Extract plain text from a rich_text property dict."""
    rt = prop.get('rich_text', [])
    return ''.join(t.get('plain_text', '') for t in rt)


def extract_select(prop):
    """Extract name from a select property dict."""
    sel = prop.get('select')
    return sel.get('name', '') if sel else ''


def extract_status(prop):
    """Extract name from a status property dict."""
    st = prop.get('status')
    return st.get('name', '') if st else ''


def extract_date(prop):
    """Extract start date string from a date property dict."""
    d = prop.get('date')
    return d.get('start', '') if d else ''


def extract_number(prop):
    """Extract number value."""
    return prop.get('number')


def extract_checkbox(prop):
    """Extract checkbox boolean."""
    return prop.get('checkbox', False)


def extract_relation_ids(prop):
    """Extract list of related page IDs from a relation property."""
    return [r['id'] for r in prop.get('relation', [])]


def extract_email(prop):
    """Extract email string."""
    return prop.get('email', '') or ''


def extract_phone(prop):
    """Extract phone number string."""
    return prop.get('phone_number', '') or ''


def extract_url(prop):
    """Extract URL string."""
    return prop.get('url', '') or ''


# ── Date formatting ──────────────────────────────────────


def format_game_date(date_str):
    """Format ISO date string to human-readable: 'Mon, Mar 15'."""
    if not date_str:
        return ''
    try:
        dt = datetime.fromisoformat(date_str)
        return dt.strftime('%a, %b %-d')
    except (ValueError, TypeError):
        return date_str


def format_date_long(date_str):
    """Format ISO date string to 'March 15th'."""
    if not date_str:
        return ''
    try:
        dt = datetime.fromisoformat(date_str)
        day = dt.day
        suffix = 'th' if 11 <= day <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
        return f'{dt.strftime("%B")} {day}{suffix}'
    except (ValueError, TypeError):
        return date_str


# ── Paginated query ──────────────────────────────────────


def paginated_query(database_id, **kwargs):
    """Query a Notion database with automatic pagination. Returns all results."""
    client = get_client()
    results = []
    has_more = True
    start_cursor = None

    while has_more:
        if start_cursor:
            kwargs['start_cursor'] = start_cursor
        response = client.databases.query(database_id=database_id, **kwargs)
        results.extend(response.get('results', []))
        has_more = response.get('has_more', False)
        start_cursor = response.get('next_cursor')

    return results


# ── Game property extraction (convenience) ───────────────


def extract_game_props(page):
    """Extract a flat dict of game properties from a Notion page."""
    props = page['properties']
    return {
        'id': page['id'],
        'game_id_title': extract_title(props.get('Game ID', {}).get('title', [])),
        'game_date': extract_date(props.get('Game Date', {})),
        'game_date_display': format_game_date(extract_date(props.get('Game Date', {}))),
        'sport': extract_select(props.get('Sport', {})),
        'gender': extract_select(props.get('Gender', {})),
        'venue': extract_rich_text(props.get('Venue', {})),
        'outreach_status': extract_select(props.get('Outreach Status', {})),
        'visiting_team': extract_rich_text(props.get('Visiting Team', {})),
        'lead_score': extract_number(props.get('Lead Score', {})),
        'last_contacted': extract_date(props.get('Last Contacted', {})),
        'follow_up_date': extract_date(props.get('Follow-up Date', {})),
        'notes': extract_rich_text(props.get('Notes', {})),
        'local_game': extract_checkbox(props.get('Local Game', {})),
        'home_team_ids': extract_relation_ids(props.get('Home Team', {})),
        'away_team_ids': extract_relation_ids(props.get('Away Team', {})),
        'contact_ids': extract_relation_ids(props.get('Contact', {})),
    }


def extract_contact_props(page):
    """Extract a flat dict of contact properties from a Notion page."""
    props = page['properties']
    return {
        'id': page['id'],
        'name': extract_title(props.get('Name', {}).get('title', [])),
        'email': extract_email(props.get('Email', {})),
        'phone': extract_phone(props.get('Phone', {})),
        'title': extract_rich_text(props.get('Title', {})),
        'sport': extract_select(props.get('Sport', {})),
        'priority': extract_number(props.get('Priority', {})),
        'last_emailed': extract_date(props.get('Last Emailed', {})),
        'school_ids': extract_relation_ids(props.get('School', {})),
    }


def extract_school_props(page):
    """Extract a flat dict of school properties from a Notion page."""
    props = page['properties']
    return {
        'id': page['id'],
        'name': extract_title(props.get('School Name', {}).get('title', [])),
        'athletics_url': extract_url(props.get('Athletics URL', {})),
        'coaches_url': extract_url(props.get('Coaches URL', {})),
        'conference': extract_select(props.get('Conference', {})),
        'division': extract_select(props.get('Division', {})),
        'local': extract_checkbox(props.get('Local', {})),
    }


def extract_email_queue_props(page):
    """Extract a flat dict of email queue properties from a Notion page."""
    props = page['properties']
    return {
        'id': page['id'],
        'email_id': extract_title(props.get('Email ID', {}).get('title', [])),
        'subject': extract_rich_text(props.get('Subject', {})),
        'body': extract_rich_text(props.get('Body', {})),
        'status': extract_select(props.get('Status', {})),
        'to_email': extract_email(props.get('To Email', {})),
        'school': extract_rich_text(props.get('School', {})),
        'sport': extract_rich_text(props.get('Sport', {})),
        'sent_at': extract_date(props.get('Sent At', {})),
        'game_date': extract_date(props.get('Game Date', {})),
        'game_ids': extract_relation_ids(props.get('Game', {})),
        'contact_ids': extract_relation_ids(props.get('Contact', {})),
        'template_ids': extract_relation_ids(props.get('Template Used', {})),
        'gmail_thread_id': extract_rich_text(props.get('Gmail Thread ID', {})),
        'gmail_message_id': extract_rich_text(props.get('Gmail Message ID', {})),
        'response_date': extract_date(props.get('Response Date', {})),
        'response_type': extract_select(props.get('Response Type', {})),
        'response_notes': extract_rich_text(props.get('Response Notes', {})),
        'response_received': extract_checkbox(props.get('Response Received', {})),
        'created': page.get('created_time', ''),
    }


def resolve_school_name(school_id):
    """Fetch a school name by page ID. Returns '' on failure."""
    try:
        page = get_client().pages.retrieve(page_id=school_id)
        return extract_title(page['properties'].get('School Name', {}).get('title', []))
    except Exception:
        return ''


def resolve_contact_summary(contact_id):
    """Fetch a contact's name and email by page ID."""
    try:
        page = get_client().pages.retrieve(page_id=contact_id)
        props = page['properties']
        return {
            'id': contact_id,
            'name': extract_title(props.get('Name', {}).get('title', [])),
            'email': extract_email(props.get('Email', {})),
            'title': extract_rich_text(props.get('Title', {})),
        }
    except Exception:
        return {'id': contact_id, 'name': '', 'email': '', 'title': ''}


def resolve_contact_full(contact_id):
    """Fetch full contact details including response tracking fields."""
    try:
        page = get_client().pages.retrieve(page_id=contact_id)
        props = page['properties']
        return {
            'id': contact_id,
            'name': extract_title(props.get('Name', {}).get('title', [])),
            'email': extract_email(props.get('Email', {})),
            'title': extract_rich_text(props.get('Title', {})),
            'sport': extract_select(props.get('Sport', {})),
            'relationship': extract_select(props.get('Relationship', {})),
            'last_response_type': extract_select(props.get('Last Response Type', {})),
            'do_not_contact': extract_checkbox(props.get('Do Not Contact', {})),
            'response_notes': extract_rich_text(props.get('Response Notes', {})),
            'last_emailed': extract_date(props.get('Last Emailed', {})),
            'first_emailed': extract_date(props.get('First Emailed', {})),
        }
    except Exception:
        return {
            'id': contact_id, 'name': '', 'email': '', 'title': '',
            'sport': '', 'relationship': '', 'last_response_type': '',
            'do_not_contact': False, 'response_notes': '',
            'last_emailed': '', 'first_emailed': '',
        }
