#!/usr/bin/env python3
"""
Tool: notion_dashboard.py
Purpose: Create and update a Notion dashboard page with outreach stats and linked databases.

First run: Creates the dashboard page and stores its ID in .env
Subsequent runs: Updates stats callout blocks while preserving linked database views.

Usage:
    python tools/notion_dashboard.py          # Create or update dashboard
    python tools/notion_dashboard.py --force  # Recreate dashboard from scratch

Required environment variables:
    NOTION_API_KEY - Notion integration token
    NOTION_GAMES_DB - Games database ID
    NOTION_SCHOOLS_DB - Schools database ID
    NOTION_CONTACTS_DB - Contacts database ID
    NOTION_EMAIL_QUEUE_DB - Email Queue database ID (optional)
    NOTION_ORDERS_DB - Orders database ID (optional)
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

from notion_client import Client
from notion_client.errors import APIResponseError
from dotenv import load_dotenv

load_dotenv()


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


def compute_stats(notion, games_db, contacts_db, schools_db, orders_db=None,
                   email_queue_db=None):
    """Compute all dashboard statistics from Notion databases."""
    today = date.today().isoformat()
    next_week = (date.today() + timedelta(days=7)).isoformat()

    print("  Querying upcoming games...", file=sys.stderr)
    upcoming = query_all(notion, games_db, filter_obj={
        "property": "Game Date",
        "date": {"on_or_after": today}
    })

    with_contact = 0
    without_contact = 0
    by_status = defaultdict(int)

    for g in upcoming:
        props = g['properties']
        contact_rel = props.get('Contact', {}).get('relation', [])
        if contact_rel:
            with_contact += 1
        else:
            without_contact += 1

        status = props.get('Outreach Status', {}).get('select', {})
        by_status[status.get('name', 'Not Contacted')] += 1

    total_upcoming = len(upcoming)
    coverage_pct = round(with_contact / total_upcoming * 100) if total_upcoming else 0

    # This week's games needing contacts
    print("  Querying this week's games...", file=sys.stderr)
    this_week = query_all(notion, games_db, filter_obj={
        "and": [
            {"property": "Game Date", "date": {"on_or_after": today}},
            {"property": "Game Date", "date": {"before": next_week}},
        ]
    })
    this_week_missing = sum(
        1 for g in this_week
        if not g['properties'].get('Contact', {}).get('relation', [])
    )

    # Follow-ups due (emailed but follow-up date <= today)
    print("  Querying follow-ups due...", file=sys.stderr)
    followups_due = query_all(notion, games_db, filter_obj={
        "and": [
            {"property": "Outreach Status", "select": {"equals": "Email Sent"}},
            {"property": "Follow-up Date", "date": {"on_or_before": today}},
        ]
    })

    # Approved emails ready to send
    approved_emails = 0
    if email_queue_db:
        print("  Querying approved emails...", file=sys.stderr)
        approved = query_all(notion, email_queue_db, filter_obj={
            "property": "Status",
            "select": {"equals": "Approved"}
        })
        approved_emails = len(approved)

    # Missed and No Response counts (past games)
    print("  Querying missed/no response games...", file=sys.stderr)
    missed_games = query_all(notion, games_db, filter_obj={
        "property": "Outreach Status",
        "select": {"equals": "Missed"}
    })
    no_response_games = query_all(notion, games_db, filter_obj={
        "property": "Outreach Status",
        "select": {"equals": "No Response"}
    })

    # Orders / Revenue
    total_orders = 0
    total_revenue = 0.0
    revenue_paid = 0.0
    revenue_pending = 0.0

    if orders_db:
        print("  Querying orders...", file=sys.stderr)
        all_orders = query_all(notion, orders_db)
        total_orders = len(all_orders)
        for o in all_orders:
            props = o['properties']
            amount = props.get('Total Amount', {}).get('number', 0) or 0
            payment = props.get('Payment Status', {}).get('select', {}).get('name', 'Pending')
            total_revenue += amount
            if payment == 'Paid':
                revenue_paid += amount
            elif payment == 'Pending':
                revenue_pending += amount

    return {
        'total_upcoming': total_upcoming,
        'with_contact': with_contact,
        'without_contact': without_contact,
        'coverage_pct': coverage_pct,
        'not_contacted': by_status.get('Not Contacted', 0),
        'email_sent': by_status.get('Email Sent', 0),
        'responded': by_status.get('Responded', 0),
        'booked': by_status.get('Booked', 0),
        'missed': len(missed_games),
        'no_response': len(no_response_games),
        'this_week_total': len(this_week),
        'this_week_missing': this_week_missing,
        'followups_due': len(followups_due),
        'approved_emails': approved_emails,
        'total_orders': total_orders,
        'total_revenue': total_revenue,
        'revenue_paid': revenue_paid,
        'revenue_pending': revenue_pending,
    }


def build_stats_blocks(stats):
    """Build Notion callout blocks from computed stats.

    Two callouts:
    1. Action Items (yellow) - what needs attention right now
    2. Pipeline (blue) - overall funnel health
    """
    # Build action items text
    action_parts = []
    if stats['followups_due'] > 0:
        action_parts.append(f"{stats['followups_due']} follow-ups due")
    if stats['this_week_missing'] > 0:
        action_parts.append(f"{stats['this_week_missing']} games this week need contacts")
    if stats['approved_emails'] > 0:
        action_parts.append(f"{stats['approved_emails']} emails ready to send")
    if stats['without_contact'] > 0:
        action_parts.append(f"{stats['without_contact']} upcoming games need outreach")
    action_text = "  |  ".join(action_parts) if action_parts else "No action items right now"

    # Build pipeline text
    pipeline_parts = [
        f"{stats['total_upcoming']} upcoming",
        f"{stats['coverage_pct']}% contacted",
        f"{stats['responded']} responded",
        f"{stats['booked']} booked",
    ]
    if stats['missed'] > 0:
        pipeline_parts.append(f"{stats['missed']} missed")
    if stats['no_response'] > 0:
        pipeline_parts.append(f"{stats['no_response']} no response")
    if stats['total_revenue'] > 0:
        pipeline_parts.append(f"${stats['total_revenue']:,.0f} revenue")
    pipeline_text = "  |  ".join(pipeline_parts)

    return [
        {
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": "\u26a0\ufe0f"},
                "rich_text": [
                    {"type": "text", "text": {"content": "Action Items\n"}, "annotations": {"bold": True}},
                    {"type": "text", "text": {"content": action_text}},
                ],
                "color": "yellow_background"
            }
        },
        {
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": "\U0001f4ca"},
                "rich_text": [
                    {"type": "text", "text": {"content": "Pipeline\n"}, "annotations": {"bold": True}},
                    {"type": "text", "text": {"content": pipeline_text}},
                ],
                "color": "blue_background"
            }
        },
    ]


def build_linked_db_blocks(games_db, schools_db, email_queue_db, orders_db=None):
    """Build linked database view blocks.

    Three views (down from 6):
    1. This Week's Games - immediate action focus
    2. Email Queue - outreach pipeline
    3. Orders - revenue tracking
    """
    blocks = [{"type": "divider", "divider": {}}]

    if games_db:
        blocks.extend([
            {
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "This Week's Games"}}]
                }
            },
            {
                "type": "link_to_page",
                "link_to_page": {"type": "database_id", "database_id": games_db}
            },
        ])

    if email_queue_db:
        blocks.extend([
            {
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "Email Queue"}}]
                }
            },
            {
                "type": "link_to_page",
                "link_to_page": {"type": "database_id", "database_id": email_queue_db}
            },
        ])

    if orders_db:
        blocks.extend([
            {
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": "Orders"}}]
                }
            },
            {
                "type": "link_to_page",
                "link_to_page": {"type": "database_id", "database_id": orders_db}
            },
        ])

    return blocks


def find_parent_page(notion, games_db):
    """Find the parent page of the Games database to create the dashboard alongside it."""
    db = notion.databases.retrieve(database_id=games_db)
    parent = db.get('parent', {})
    if parent.get('type') == 'page_id':
        return parent['page_id']
    return None


def create_dashboard(notion, stats, games_db, contacts_db, schools_db, email_queue_db,
                     orders_db=None):
    """Create a new dashboard page in Notion."""
    stats_blocks = build_stats_blocks(stats)
    linked_blocks = build_linked_db_blocks(games_db, schools_db, email_queue_db, orders_db)

    parent_page_id = find_parent_page(notion, games_db)
    if not parent_page_id:
        print("Error: Could not determine parent page for dashboard.", file=sys.stderr)
        print("The Games database must be inside a page.", file=sys.stderr)
        sys.exit(1)

    page = notion.pages.create(
        parent={"page_id": parent_page_id},
        properties={
            "title": {"title": [{"type": "text", "text": {"content": "Sports Outreach Dashboard"}}]}
        },
        children=stats_blocks + linked_blocks
    )

    return page['id']


def update_dashboard(notion, page_id, stats):
    """Update stats callout blocks on an existing dashboard page."""
    # Get all children blocks
    children = []
    has_more = True
    start_cursor = None

    while has_more:
        kwargs = {"block_id": page_id}
        if start_cursor:
            kwargs["start_cursor"] = start_cursor
        response = notion.blocks.children.list(**kwargs)
        children.extend(response['results'])
        has_more = response.get('has_more', False)
        start_cursor = response.get('next_cursor')

    # Find existing callout blocks and update them with new stats
    callout_blocks = [b for b in children if b['type'] == 'callout']
    new_stats_blocks = build_stats_blocks(stats)
    new_callouts = [b for b in new_stats_blocks if b['type'] == 'callout']

    updated = 0
    for i, callout in enumerate(callout_blocks):
        if i < len(new_callouts):
            notion.blocks.update(
                block_id=callout['id'],
                callout=new_callouts[i]['callout']
            )
            updated += 1

    print(f"  Updated {updated} callout blocks", file=sys.stderr)


def save_dashboard_id(page_id):
    """Save NOTION_DASHBOARD_PAGE to .env file."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')

    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            content = f.read()
        if 'NOTION_DASHBOARD_PAGE' in content:
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if line.startswith('NOTION_DASHBOARD_PAGE='):
                    lines[i] = f'NOTION_DASHBOARD_PAGE={page_id}'
                    break
            with open(env_path, 'w') as f:
                f.write('\n'.join(lines))
            return

    with open(env_path, 'a') as f:
        f.write(f'\nNOTION_DASHBOARD_PAGE={page_id}\n')


def main():
    parser = argparse.ArgumentParser(description="Create/update Notion outreach dashboard")
    parser.add_argument("--force", action="store_true", help="Recreate dashboard from scratch")
    args = parser.parse_args()

    api_key = os.getenv('NOTION_API_KEY')
    if not api_key:
        print("Error: NOTION_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    notion = Client(auth=api_key)
    games_db = os.getenv('NOTION_GAMES_DB')
    schools_db = os.getenv('NOTION_SCHOOLS_DB')
    contacts_db = os.getenv('NOTION_CONTACTS_DB')
    email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB')
    orders_db = os.getenv('NOTION_ORDERS_DB')
    dashboard_page = os.getenv('NOTION_DASHBOARD_PAGE')

    if not all([games_db, schools_db, contacts_db]):
        print("Error: NOTION_GAMES_DB, NOTION_SCHOOLS_DB, and NOTION_CONTACTS_DB must be set", file=sys.stderr)
        sys.exit(1)

    # Compute stats
    print("Computing dashboard stats...", file=sys.stderr)
    stats = compute_stats(notion, games_db, contacts_db, schools_db, orders_db,
                          email_queue_db)

    print(f"\n  Action: {stats['followups_due']} follow-ups due | "
          f"{stats['this_week_missing']} games need contacts | "
          f"{stats['approved_emails']} ready to send", file=sys.stderr)
    print(f"  Pipeline: {stats['total_upcoming']} upcoming | "
          f"{stats['coverage_pct']}% contacted | "
          f"{stats['responded']} responded | {stats['booked']} booked | "
          f"{stats['missed']} missed", file=sys.stderr)
    if stats['total_revenue'] > 0:
        print(f"  Revenue: ${stats['total_revenue']:,.0f} total | "
              f"{stats['total_orders']} orders", file=sys.stderr)

    if dashboard_page and not args.force:
        # Update existing dashboard
        print(f"\nUpdating existing dashboard...", file=sys.stderr)
        try:
            update_dashboard(notion, dashboard_page, stats)
            page_url = f"https://notion.so/{dashboard_page.replace('-', '')}"
            print(f"Dashboard updated: {page_url}", file=sys.stderr)
        except APIResponseError as e:
            print(f"Error updating dashboard (may have been deleted): {e}", file=sys.stderr)
            print("Run with --force to recreate", file=sys.stderr)
            sys.exit(1)
    else:
        # Create new dashboard
        print(f"\nCreating new dashboard page...", file=sys.stderr)
        page_id = create_dashboard(notion, stats, games_db, contacts_db, schools_db, email_queue_db,
                                   orders_db)
        save_dashboard_id(page_id)
        dashboard_page = page_id
        page_url = f"https://notion.so/{page_id.replace('-', '')}"
        print(f"Dashboard created: {page_url}", file=sys.stderr)
        print(f"Page ID saved to .env as NOTION_DASHBOARD_PAGE", file=sys.stderr)

    result = {
        "success": True,
        "stats": stats,
        "dashboard_page": dashboard_page,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
