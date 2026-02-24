#!/usr/bin/env python3
"""
Tool: sync_contacts_to_dashboard.py
Purpose: Safely sync Sports Automation contacts into the Livite Dashboard Contacts DB.

Creates new contacts only — never modifies existing records.
Deduplicates by email. Supports dry-run, execute, limit, and rollback.

Usage:
    python tools/sync_contacts_to_dashboard.py                     # Dry-run (default)
    python tools/sync_contacts_to_dashboard.py --execute --limit 5 # Test with 5
    python tools/sync_contacts_to_dashboard.py --execute           # Full sync
    python tools/sync_contacts_to_dashboard.py --rollback .tmp/sync_rollback_*.json

Required environment variables:
    NOTION_API_KEY              - Notion integration token
    NOTION_CONTACTS_DB          - Sports Automation Contacts DB
    NOTION_SCHOOLS_DB           - Sports Automation Schools DB
    NOTION_DASHBOARD_CONTACTS_DB - Livite Dashboard Contacts DB
    NOTION_ACCOUNTS_DB          - Livite Dashboard Accounts DB
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from notion_client import Client
from notion_client.errors import APIResponseError, RequestTimeoutError
from dotenv import load_dotenv

load_dotenv()


# === Title → Contact Role mapping (first match wins) ===
TITLE_TO_ROLE = [
    ("director of operations", "Director of Operations"),
    ("dir. of ops", "Director of Operations"),
    ("dir of ops", "Director of Operations"),
    ("operations coordinator", "Operations Coordinator"),
    ("operations manager", "Operations Manager"),
    ("team operations", "Team Operations"),
    ("sport supervisor", "Sport Supervisor"),
    ("sport admin", "Sport Admin"),
    ("associate head coach", "Associate Head Coach"),
    ("assistant coach/director of operations", "Assistant Coach/Director of Operations"),
    ("assistant coach / director of operations", "Assistant Coach / Director of Operations"),
    ("assistant coach/recruiting coordinator", "Assistant Coach/Recruiting Coordinator"),
    ("assistant coach - pitching", "Assistant Coach - Pitching"),
    ("head coach", "Head Coach"),
    ("assistant coach", "Assistant Coach"),
    ("graduate assistant", "Graduate Assistant"),
    ("volunteer assistant", "Volunteer Assistant"),
    ("coach", "Coach"),
]

# === Sport name mapping (Sports Automation → Dashboard) ===
SPORT_MAP = {
    "Baseball": "Baseball",
    "Softball": "Softball",
    "Lacrosse": "Lacrosse",
    "Soccer": "Soccer",
    "Basketball": "Basketball",
    "Football": "Football",
    "Hockey": "Ice Hockey",
    "Ice Hockey": "Ice Hockey",
    "Tennis": "Tennis",
    "Golf": "Golf",
    "Volleyball": "Volleyball",
    "Rowing": "Rowing",
    "Water Polo": "Water Polo",
    "Unknown": None,
}


def query_all(notion, database_id, filter_obj=None):
    """Query all pages from a Notion database, handling pagination."""
    all_results = []
    has_more = True
    start_cursor = None
    while has_more:
        kwargs = {"database_id": database_id}
        if filter_obj:
            kwargs["filter"] = filter_obj
        if start_cursor:
            kwargs["start_cursor"] = start_cursor
        response = notion.databases.query(**kwargs)
        all_results.extend(response['results'])
        has_more = response.get('has_more', False)
        start_cursor = response.get('next_cursor')
    return all_results


def get_title(props):
    """Extract title text from any title property."""
    for v in props.values():
        if v.get('type') == 'title':
            return ''.join(t.get('plain_text', '') for t in v.get('title', [])).strip()
    return ''


def map_title_to_role(title_text):
    """Map freeform title string to Contact Role select option.
    Returns (role_name, is_new_option)."""
    if not title_text:
        return None, False
    lower = title_text.lower().strip()
    for keyword, role in TITLE_TO_ROLE:
        if keyword in lower:
            return role, False
    # No match — use raw title as new select option
    return title_text.strip(), True


def map_sport(sport_name):
    """Map Sports Automation sport to Dashboard Sports Team name."""
    if not sport_name:
        return None
    return SPORT_MAP.get(sport_name, sport_name)


def normalize_name(name):
    """Normalize school/account name for fuzzy matching."""
    return name.lower().strip()


def bold_paragraph(text):
    """Create a paragraph block with bold text."""
    return {
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}, "annotations": {"bold": True}}]}
    }


def italic_paragraph(text):
    """Create a paragraph block with italic text."""
    return {
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}, "annotations": {"italic": True}}]}
    }


def empty_paragraph():
    """Create an empty paragraph block."""
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}}


def contact_page_template():
    """Build the standard contact page template matching existing Dashboard contacts."""
    return [
        bold_paragraph("Activity Date: "),
        italic_paragraph("Type Date"),
        empty_paragraph(),
        bold_paragraph("Channel Method: "),
        italic_paragraph("Email"),
        italic_paragraph("Phone Call"),
        italic_paragraph("Voice Mail"),
        italic_paragraph("LinkedIn Message"),
        italic_paragraph("In Person"),
        empty_paragraph(),
        bold_paragraph("Activity Type:"),
        italic_paragraph("Initial Outreach "),
        italic_paragraph("Follow Up"),
        italic_paragraph("Intro"),
        italic_paragraph("Referral"),
        empty_paragraph(),
        bold_paragraph("Outcome/Response:"),
        italic_paragraph("No Response "),
        italic_paragraph("Replied - Positive Interest"),
        italic_paragraph("Replied - Not Interested at This Time"),
        italic_paragraph("Replied - Already Covered "),
        italic_paragraph("Email Bounced"),
        italic_paragraph("No answer"),
        italic_paragraph("Bad Number"),
        italic_paragraph("Left Voice Mail"),
        italic_paragraph("Contact No Longer with the Company "),
        italic_paragraph("Asked to Follow Up Later"),
        italic_paragraph("Do Not Contact"),
        empty_paragraph(),
        bold_paragraph("Next Follow-Up Date:"),
        empty_paragraph(),
        bold_paragraph("Notes:"),
        empty_paragraph(),
        empty_paragraph(),
    ]


CONTACT_ICON = {
    "type": "external",
    "external": {"url": "https://www.notion.so/icons/arrow-right_gray.svg"}
}


def safe_create_page(notion, parent_db, properties, children=None, icon=None, max_retries=5):
    """Create a Notion page with rate limiting, timeout handling, and retry."""
    for attempt in range(max_retries):
        try:
            kwargs = {
                "parent": {"database_id": parent_db},
                "properties": properties,
            }
            if children:
                kwargs["children"] = children
            if icon:
                kwargs["icon"] = icon
            response = notion.pages.create(**kwargs)
            time.sleep(0.35)
            return response
        except APIResponseError as e:
            if e.status == 429:
                wait = 2 ** attempt
                print(f"  Rate limited, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
            else:
                raise
        except RequestTimeoutError:
            wait = 3 * (attempt + 1)
            print(f"  Timeout (attempt {attempt + 1}/{max_retries}), waiting {wait}s...", file=sys.stderr)
            time.sleep(wait)
    return None


def build_manifest(notion):
    """Phase 1: Read all data and build a sync manifest (read-only)."""
    sports_db = os.getenv('NOTION_CONTACTS_DB')
    schools_db = os.getenv('NOTION_SCHOOLS_DB')
    dashboard_db = os.getenv('NOTION_DASHBOARD_CONTACTS_DB')
    accounts_db = os.getenv('NOTION_ACCOUNTS_DB')

    print("Loading Sports Automation contacts...", file=sys.stderr)
    sports_contacts = query_all(notion, sports_db)
    print(f"  Found {len(sports_contacts)} contacts", file=sys.stderr)

    print("Loading Dashboard contacts (for dedup)...", file=sys.stderr)
    dashboard_contacts = query_all(notion, dashboard_db)
    existing_emails = set()
    existing_names = set()
    for c in dashboard_contacts:
        email = (c['properties'].get('Email Address', {}).get('email') or '').lower().strip()
        if email:
            existing_emails.add(email)
        name = get_title(c['properties']).lower().strip()
        if name:
            existing_names.add(name)
    print(f"  Found {len(dashboard_contacts)} contacts ({len(existing_emails)} with email, {len(existing_names)} unique names)", file=sys.stderr)

    print("Loading Schools (for name resolution)...", file=sys.stderr)
    schools = query_all(notion, schools_db)
    school_map = {}
    for s in schools:
        name = get_title(s['properties'])
        school_map[s['id']] = name
    print(f"  Found {len(school_map)} schools", file=sys.stderr)

    print("Loading Accounts (for matching)...", file=sys.stderr)
    accounts = query_all(notion, accounts_db)
    account_map = {}  # normalized_name -> {id, name}
    for a in accounts:
        name = get_title(a['properties'])
        if name:
            account_map[normalize_name(name)] = {'id': a['id'], 'name': name}
    print(f"  Found {len(account_map)} accounts", file=sys.stderr)

    # Build contact records
    to_create = []
    skipped_dup_email = 0
    skipped_dup_name = 0
    skipped_no_email = 0
    skipped_no_name = 0
    new_roles = []
    accounts_to_create = {}  # school_name -> True

    for c in sports_contacts:
        props = c['properties']
        name = get_title(props)
        email = (props.get('Email', {}).get('email') or '').lower().strip()
        phone = props.get('Phone', {}).get('phone_number') or ''
        title = ''.join(t.get('plain_text', '') for t in props.get('Title', {}).get('rich_text', []))
        sport = (props.get('Sport', {}).get('select') or {}).get('name', '')
        school_ids = [r['id'] for r in props.get('School', {}).get('relation', [])]
        school_name = school_map.get(school_ids[0], '') if school_ids else ''

        if not name:
            skipped_no_name += 1
            continue
        if not email:
            skipped_no_email += 1
            continue
        if email in existing_emails:
            skipped_dup_email += 1
            continue
        if name.lower().strip() in existing_names:
            skipped_dup_name += 1
            continue

        role, is_new = map_title_to_role(title)
        if is_new and role:
            new_roles.append(role)

        mapped_sport = map_sport(sport)

        # Check account match
        account_match = account_map.get(normalize_name(school_name)) if school_name else None
        if school_name and not account_match:
            accounts_to_create[school_name] = True

        to_create.append({
            'name': name,
            'email': email,
            'phone': phone if phone and phone != 'Not Found' else '',
            'title_raw': title,
            'role': role,
            'role_is_new': is_new,
            'sport': mapped_sport,
            'school_name': school_name,
            'account_id': account_match['id'] if account_match else None,
            'account_name': account_match['name'] if account_match else None,
            'needs_new_account': bool(school_name and not account_match),
        })

    manifest = {
        'created_at': datetime.now().isoformat(),
        'source_db': sports_db,
        'target_db': dashboard_db,
        'accounts_db': accounts_db,
        'stats': {
            'total_source': len(sports_contacts),
            'existing_dashboard': len(dashboard_contacts),
            'to_create': len(to_create),
            'skipped_dup_email': skipped_dup_email,
            'skipped_dup_name': skipped_dup_name,
            'skipped_no_email': skipped_no_email,
            'skipped_no_name': skipped_no_name,
            'new_accounts': len(accounts_to_create),
            'new_role_options': len(set(new_roles)),
        },
        'contacts': to_create,
        'accounts_to_create': list(accounts_to_create.keys()),
        'new_roles': sorted(set(new_roles)),
    }

    # Save manifest
    Path('.tmp').mkdir(exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    manifest_path = f'.tmp/sync_manifest_{ts}.json'
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    print(f"\nManifest saved to {manifest_path}", file=sys.stderr)

    return manifest, manifest_path


def dry_run(manifest):
    """Phase 2: Print preview report (no API calls)."""
    stats = manifest['stats']

    print("\n" + "=" * 60)
    print("SYNC PREVIEW (DRY RUN)")
    print("=" * 60)
    print(f"Source: Sports Automation Contacts ({stats['total_source']} total)")
    print(f"Target: Livite Dashboard Contacts ({stats['existing_dashboard']} existing)")
    print(f"")
    print(f"Contacts to create:       {stats['to_create']}")
    print(f"Skipped (dup email):      {stats['skipped_dup_email']}")
    print(f"Skipped (dup name):       {stats['skipped_dup_name']}")
    print(f"Skipped (no email):       {stats['skipped_no_email']}")
    print(f"Skipped (no name):        {stats['skipped_no_name']}")
    print(f"New accounts needed:  {stats['new_accounts']}")
    print(f"New role options:     {stats['new_role_options']}")

    if manifest['new_roles']:
        print(f"\nNEW CONTACT ROLE OPTIONS (will be auto-created):")
        for r in manifest['new_roles']:
            print(f"  - {r}")

    if manifest['accounts_to_create']:
        print(f"\nNEW ACCOUNTS TO CREATE ({len(manifest['accounts_to_create'])}):")
        for name in sorted(manifest['accounts_to_create']):
            print(f"  - {name} (School/University, New Lead)")

    print(f"\nCONTACT PREVIEW (first 15):")
    print(f"{'Name':<30} {'Role':<30} {'Sport':<15} {'Account'}")
    print("-" * 100)
    for c in manifest['contacts'][:15]:
        acct = c['account_name'] or f"{c['school_name']} [NEW]"
        role_flag = " *NEW*" if c['role_is_new'] else ""
        print(f"{c['name']:<30} {(c['role'] or '(empty)'):<30} {(c['sport'] or '(empty)'):<15} {acct}{role_flag}")

    if len(manifest['contacts']) > 15:
        print(f"  ... and {len(manifest['contacts']) - 15} more")

    print(f"\nSAFETY CHECKS:")
    print(f"  [x] Email dedup: {stats['skipped_dup_email']} duplicates skipped")
    print(f"  [x] Name dedup: {stats['skipped_dup_name']} duplicates skipped")
    print(f"  [x] Existing contacts: NEVER modified")
    print(f"  [x] Catering Orders: left empty on all new contacts")
    print(f"  [x] Rollback file: will be created on execute")
    print(f"\nTo execute: python tools/sync_contacts_to_dashboard.py --execute")
    print(f"To test:    python tools/sync_contacts_to_dashboard.py --execute --limit 5")


def execute_sync(notion, manifest, limit=None):
    """Phase 3: Create contacts in Dashboard DB."""
    dashboard_db = manifest['target_db']
    accounts_db = manifest['accounts_db']
    contacts = manifest['contacts']

    if limit:
        contacts = contacts[:limit]
        print(f"\nLimited to {limit} contacts", file=sys.stderr)

    # Track created pages for rollback
    rollback = {
        'sync_started': datetime.now().isoformat(),
        'created_contacts': [],
        'created_accounts': [],
    }

    # Phase 3a: Create missing accounts
    account_cache = {}  # school_name -> page_id
    accounts_needed = set(c['school_name'] for c in contacts if c['needs_new_account'])

    if accounts_needed:
        print(f"\nCreating {len(accounts_needed)} new accounts...", file=sys.stderr)
        for school_name in sorted(accounts_needed):
            properties = {
                "Account Name": {"title": [{"text": {"content": school_name}}]},
                "Account Type": {"select": {"name": "School/University"}},
                "Status ": {"select": {"name": "New Lead"}},
                "Lead Source": {"select": {"name": "Email"}},
                "Industry": {"select": {"name": "Education – Higher Ed"}},
            }
            try:
                resp = safe_create_page(notion, accounts_db, properties)
                if resp:
                    account_cache[school_name] = resp['id']
                    rollback['created_accounts'].append({
                        'id': resp['id'],
                        'name': school_name,
                    })
                    print(f"  Created account: {school_name}", file=sys.stderr)
            except APIResponseError as e:
                print(f"  ERROR creating account {school_name}: {e}", file=sys.stderr)

    # Phase 3b: Create contacts
    created = 0
    errors = 0
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    rollback_path = f'.tmp/sync_rollback_{ts}.json'

    def save_rollback():
        """Save rollback file incrementally."""
        rollback['stats'] = {'created': created, 'errors': errors}
        with open(rollback_path, 'w') as f:
            json.dump(rollback, f, indent=2)

    # Save initial rollback with accounts
    save_rollback()

    print(f"\nCreating {len(contacts)} contacts...", file=sys.stderr)

    for i, c in enumerate(contacts):
        properties = {
            "Name": {"title": [{"text": {"content": c['name']}}]},
            "Contact Type": {"select": {"name": "Client"}},
            "Preferred Channel": {"select": {"name": "Email"}},
            "Relationship Type": {"multi_select": [{"name": "Main Point of Contact"}]},
        }

        if c['email']:
            properties["Email Address"] = {"email": c['email']}

        if c['phone']:
            properties["Phone Number"] = {"phone_number": c['phone']}

        if c['role']:
            properties["Contact Role"] = {"select": {"name": c['role']}}

        if c['sport']:
            properties["Sports Team"] = {"multi_select": [{"name": c['sport']}]}

        # Link to account
        account_id = c['account_id'] or account_cache.get(c['school_name'])
        if account_id:
            properties["Account"] = {"relation": [{"id": account_id}]}

        try:
            resp = safe_create_page(notion, dashboard_db, properties, children=contact_page_template(), icon=CONTACT_ICON)
            if resp:
                created += 1
                rollback['created_contacts'].append({
                    'id': resp['id'],
                    'name': c['name'],
                    'email': c['email'],
                })
                if (i + 1) % 25 == 0 or (i + 1) == len(contacts):
                    print(f"  Progress: {i + 1}/{len(contacts)} ({created} created, {errors} errors)", file=sys.stderr)
                    save_rollback()  # Save every 25 contacts
        except (APIResponseError, RequestTimeoutError) as e:
            errors += 1
            print(f"  ERROR creating {c['name']}: {e}", file=sys.stderr)

    # Final save
    rollback['sync_completed'] = datetime.now().isoformat()
    save_rollback()

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"SYNC COMPLETE", file=sys.stderr)
    print(f"  Contacts created: {created}", file=sys.stderr)
    print(f"  Accounts created: {len(rollback['created_accounts'])}", file=sys.stderr)
    print(f"  Errors: {errors}", file=sys.stderr)
    print(f"  Rollback file: {rollback_path}", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)

    return rollback_path


def do_rollback(notion, rollback_file):
    """Archive (soft-delete) all pages from a rollback file."""
    with open(rollback_file) as f:
        data = json.load(f)

    total = len(data.get('created_contacts', [])) + len(data.get('created_accounts', []))
    print(f"Rolling back {total} pages from {rollback_file}...", file=sys.stderr)

    archived = 0
    for item in data.get('created_contacts', []):
        try:
            notion.pages.update(page_id=item['id'], archived=True)
            archived += 1
            time.sleep(0.35)
        except APIResponseError as e:
            print(f"  ERROR archiving contact {item.get('name', item['id'])}: {e}", file=sys.stderr)

    for item in data.get('created_accounts', []):
        try:
            notion.pages.update(page_id=item['id'], archived=True)
            archived += 1
            time.sleep(0.35)
        except APIResponseError as e:
            print(f"  ERROR archiving account {item.get('name', item['id'])}: {e}", file=sys.stderr)

    print(f"Rollback complete: {archived}/{total} pages archived", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Sync Sports Automation contacts to Livite Dashboard")
    parser.add_argument('--execute', action='store_true', help="Actually create contacts (default is dry-run)")
    parser.add_argument('--limit', type=int, help="Limit number of contacts to sync")
    parser.add_argument('--rollback', type=str, help="Path to rollback JSON file")
    parser.add_argument('--manifest', type=str, help="Path to existing manifest (skip data loading)")
    args = parser.parse_args()

    # Validate env
    required = ['NOTION_API_KEY', 'NOTION_CONTACTS_DB', 'NOTION_SCHOOLS_DB',
                'NOTION_DASHBOARD_CONTACTS_DB', 'NOTION_ACCOUNTS_DB']
    missing = [v for v in required if not os.getenv(v)]
    if missing and not args.rollback:
        print(f"Error: Missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    notion = Client(auth=os.getenv('NOTION_API_KEY'))

    # Rollback mode
    if args.rollback:
        do_rollback(notion, args.rollback)
        return

    # Load or build manifest
    if args.manifest:
        with open(args.manifest) as f:
            manifest = json.load(f)
        print(f"Loaded manifest from {args.manifest}", file=sys.stderr)
    else:
        manifest, manifest_path = build_manifest(notion)

    # Dry-run or execute
    if args.execute:
        dry_run(manifest)
        print(f"\n>>> EXECUTING SYNC <<<\n", file=sys.stderr)
        execute_sync(notion, manifest, limit=args.limit)
    else:
        dry_run(manifest)


if __name__ == '__main__':
    main()
