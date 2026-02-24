#!/usr/bin/env python3
"""
Tool: notion_record_metrics.py
Purpose: Record daily metrics snapshot to Notion Metrics database

Creates or updates a single row per day with:
- Cumulative email/response/booking counts
- Conversion rates
- Pipeline health metrics
- Per-sport breakdown

Usage:
    python tools/notion_record_metrics.py           # Record today's metrics
    python tools/notion_record_metrics.py --report  # Print metrics without saving

Required environment variables:
    NOTION_API_KEY - Notion integration token
    NOTION_METRICS_DB - Metrics database ID
    NOTION_EMAIL_QUEUE_DB - Email Queue database ID
    NOTION_GAMES_DB - Games database ID
    NOTION_ORDERS_DB - Orders database ID
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date

from notion_client import Client
from notion_client.errors import APIResponseError
from dotenv import load_dotenv

load_dotenv()

# Add tools directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def log(message):
    """Log to stderr."""
    print(f"  [metrics] {message}", file=sys.stderr)


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


def extract_text(rich_text_array):
    """Extract plain text from Notion rich text array."""
    if not rich_text_array:
        return ''
    return ''.join(item.get('plain_text', '') for item in rich_text_array)


def compute_metrics(notion, email_queue_db, games_db, orders_db):
    """Compute all metrics from current Notion data."""
    today = date.today().isoformat()

    # Count emails by status (sent includes Sent, Responded, Booked)
    log("Querying email queue...")
    sent_statuses = ['Sent', 'Responded', 'Booked']
    all_sent = []
    for status in sent_statuses:
        results = query_all(notion, email_queue_db, filter_obj={
            "property": "Status", "select": {"equals": status}
        })
        all_sent.extend(results)

    total_sent = len(all_sent)

    # Break down by response status
    responded_emails = [e for e in all_sent
                        if e['properties'].get('Status', {}).get('select', {}).get('name') in ('Responded', 'Booked')]
    booked_emails = [e for e in all_sent
                     if e['properties'].get('Status', {}).get('select', {}).get('name') == 'Booked']

    total_responded = len(responded_emails)
    total_booked = len(booked_emails)

    # Per-sport breakdown
    by_sport = defaultdict(lambda: {'sent': 0, 'responded': 0, 'booked': 0})
    for email in all_sent:
        sport = extract_text(email['properties'].get('Sport', {}).get('rich_text', []))
        if not sport:
            sport = 'Unknown'
        by_sport[sport]['sent'] += 1

    for email in responded_emails:
        sport = extract_text(email['properties'].get('Sport', {}).get('rich_text', []))
        if not sport:
            sport = 'Unknown'
        by_sport[sport]['responded'] += 1

    for email in booked_emails:
        sport = extract_text(email['properties'].get('Sport', {}).get('rich_text', []))
        if not sport:
            sport = 'Unknown'
        by_sport[sport]['booked'] += 1

    # Game pipeline counts
    log("Querying games pipeline...")
    not_contacted = query_all(notion, games_db, filter_obj={
        "and": [
            {"property": "Game Date", "date": {"on_or_after": today}},
            {"property": "Outreach Status", "select": {"equals": "Not Contacted"}},
        ]
    })
    email_sent_games = query_all(notion, games_db, filter_obj={
        "and": [
            {"property": "Game Date", "date": {"on_or_after": today}},
            {"property": "Outreach Status", "select": {"equals": "Email Sent"}},
        ]
    })

    # Orders and revenue
    total_orders = 0
    total_revenue = 0.0
    if orders_db:
        log("Querying orders...")
        all_orders = query_all(notion, orders_db)
        total_orders = len(all_orders)
        for o in all_orders:
            amount = o['properties'].get('Total Amount', {}).get('number', 0) or 0
            total_revenue += amount

    # Compute rates
    response_rate = round(total_responded / total_sent * 100, 1) if total_sent > 0 else 0
    booking_rate = round(total_booked / total_responded * 100, 1) if total_responded > 0 else 0

    return {
        'total_sent': total_sent,
        'total_responded': total_responded,
        'total_booked': total_booked,
        'response_rate': response_rate,
        'booking_rate': booking_rate,
        'not_contacted': len(not_contacted),
        'email_sent': len(email_sent_games),
        'total_orders': total_orders,
        'total_revenue': total_revenue,
        'by_sport': dict(by_sport),
    }


def record_metrics(notion, metrics_db, metrics):
    """Create or update today's metrics row in Notion."""
    today = date.today().isoformat()

    # Check if today's row already exists
    existing = notion.databases.query(
        database_id=metrics_db,
        filter={"property": "Date", "title": {"equals": today}}
    )

    properties = {
        "Date": {"title": [{"text": {"content": today}}]},
        "Snapshot Date": {"date": {"start": today}},
        "Emails Sent": {"number": metrics['total_sent']},
        "Responses": {"number": metrics['total_responded']},
        "Bookings": {"number": metrics['total_booked']},
        "Response Rate %": {"number": metrics['response_rate']},
        "Booking Rate %": {"number": metrics['booking_rate']},
        "Games Not Contacted": {"number": metrics['not_contacted']},
        "Games Email Sent": {"number": metrics['email_sent']},
        "By Sport": {"rich_text": [{"text": {"content": json.dumps(metrics['by_sport'])[:2000]}}]},
    }

    if existing['results']:
        page_id = existing['results'][0]['id']
        notion.pages.update(page_id=page_id, properties=properties)
        log(f"Updated metrics for {today}")
    else:
        notion.pages.create(
            parent={"database_id": metrics_db},
            properties=properties
        )
        log(f"Created metrics for {today}")


def main():
    parser = argparse.ArgumentParser(description="Record daily metrics snapshot")
    parser.add_argument("--report", action="store_true",
                        help="Print metrics without saving to Notion")
    args = parser.parse_args()

    api_key = os.getenv('NOTION_API_KEY')
    metrics_db = os.getenv('NOTION_METRICS_DB')
    email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB')
    games_db = os.getenv('NOTION_GAMES_DB')
    orders_db = os.getenv('NOTION_ORDERS_DB')

    if not api_key:
        print("Error: NOTION_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    if not metrics_db and not args.report:
        print("Error: NOTION_METRICS_DB not set in .env", file=sys.stderr)
        print("Create a Metrics database in Notion and add its ID to .env", file=sys.stderr)
        sys.exit(1)

    notion = Client(auth=api_key)
    metrics = compute_metrics(notion, email_queue_db, games_db, orders_db)

    if args.report:
        print(json.dumps(metrics, indent=2))
        return

    record_metrics(notion, metrics_db, metrics)
    print(json.dumps({"success": True, **metrics}, indent=2))


if __name__ == "__main__":
    main()
