#!/usr/bin/env python3
"""
Tool: notion_daily_digest.py
Purpose: Generate daily summary of CRM activity and what needs attention

Digest Sections:
1. Follow-ups Due Today - Games where Follow-up Date <= Today
2. Games This Week - Upcoming games (delivery opportunities)
3. Pending Deliveries - Confirmed orders awaiting delivery
4. Hot Leads - Games with Lead Score >= 80
5. Weekly Stats - Emails sent, responses, bookings

Usage:
    # Print digest to console
    python tools/notion_daily_digest.py

    # Send to Telegram
    python tools/notion_daily_digest.py --send-telegram

    # Send to email
    python tools/notion_daily_digest.py --send-email you@livite.com

    # Generate for specific date
    python tools/notion_daily_digest.py --date 2026-02-10

Required environment variables:
    NOTION_API_KEY - Notion integration token
    NOTION_GAMES_DB - Games database ID
    NOTION_ORDERS_DB - Orders database ID
    NOTION_EMAIL_QUEUE_DB - Email Queue database ID
    TELEGRAM_BOT_TOKEN - (optional) Telegram bot token
    TELEGRAM_CHAT_ID - (optional) Telegram chat ID
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

try:
    from notion_client import Client
    from notion_client.errors import APIResponseError
except ImportError:
    print("Error: notion-client package not installed.", file=sys.stderr)
    print("Run: pip install notion-client", file=sys.stderr)
    sys.exit(1)

try:
    import requests
except ImportError:
    requests = None

from dotenv import load_dotenv

load_dotenv()


def get_notion_client():
    """Initialize Notion client."""
    api_key = os.getenv('NOTION_API_KEY')
    if not api_key:
        print("Error: NOTION_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    return Client(auth=api_key)


def get_database_ids():
    """Get required database IDs from environment."""
    return {
        'games': os.getenv('NOTION_GAMES_DB'),
        'orders': os.getenv('NOTION_ORDERS_DB'),
        'email_queue': os.getenv('NOTION_EMAIL_QUEUE_DB'),
        'contacts': os.getenv('NOTION_CONTACTS_DB'),
        'schools': os.getenv('NOTION_SCHOOLS_DB')
    }


def extract_title(title_array):
    """Extract title text from Notion title property."""
    if not title_array:
        return ''
    return ''.join(item.get('plain_text', '') for item in title_array)


def format_date(date_str):
    """Format ISO date string for display."""
    if not date_str:
        return 'TBA'
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return dt.strftime('%b %d')
    except:
        return date_str


def get_followups_due(notion, games_db, target_date):
    """Get games where Follow-up Date <= target_date and status is Email Sent."""
    try:
        response = notion.databases.query(
            database_id=games_db,
            filter={
                "and": [
                    {
                        "property": "Follow-up Date",
                        "date": {"on_or_before": target_date}
                    },
                    {
                        "property": "Outreach Status",
                        "select": {"equals": "Email Sent"}
                    }
                ]
            },
            sorts=[
                {"property": "Game Date", "direction": "ascending"}
            ]
        )
        return response['results']
    except APIResponseError as e:
        print(f"Warning: Could not fetch follow-ups: {e}", file=sys.stderr)
        return []


def get_games_this_week(notion, games_db, start_date, end_date):
    """Get games happening this week."""
    try:
        response = notion.databases.query(
            database_id=games_db,
            filter={
                "and": [
                    {
                        "property": "Game Date",
                        "date": {"on_or_after": start_date}
                    },
                    {
                        "property": "Game Date",
                        "date": {"on_or_before": end_date}
                    }
                ]
            },
            sorts=[
                {"property": "Game Date", "direction": "ascending"}
            ]
        )
        return response['results']
    except APIResponseError as e:
        print(f"Warning: Could not fetch games: {e}", file=sys.stderr)
        return []


def get_pending_deliveries(notion, orders_db, start_date, end_date):
    """Get confirmed orders with delivery this week."""
    if not orders_db:
        return []

    try:
        response = notion.databases.query(
            database_id=orders_db,
            filter={
                "and": [
                    {
                        "property": "Delivery Date",
                        "date": {"on_or_after": start_date}
                    },
                    {
                        "property": "Delivery Date",
                        "date": {"on_or_before": end_date}
                    }
                ]
            },
            sorts=[
                {"property": "Delivery Date", "direction": "ascending"}
            ]
        )
        return response['results']
    except APIResponseError as e:
        print(f"Warning: Could not fetch orders: {e}", file=sys.stderr)
        return []


def get_hot_leads(notion, games_db, min_score=80):
    """Get games with Lead Score >= min_score."""
    try:
        response = notion.databases.query(
            database_id=games_db,
            filter={
                "and": [
                    {
                        "property": "Lead Score",
                        "number": {"greater_than_or_equal_to": min_score}
                    },
                    {
                        "or": [
                            {"property": "Outreach Status", "select": {"equals": "Not Contacted"}},
                            {"property": "Outreach Status", "select": {"equals": "Email Sent"}}
                        ]
                    }
                ]
            },
            sorts=[
                {"property": "Lead Score", "direction": "descending"}
            ]
        )
        return response['results'][:10]  # Top 10
    except APIResponseError as e:
        # Lead Score property might not exist
        return []


def get_weekly_stats(notion, email_queue_db, games_db, start_date, end_date):
    """Get stats for the week."""
    stats = {
        'emails_sent': 0,
        'responses': 0,
        'bookings': 0,
        'not_contacted': 0
    }

    try:
        # Emails sent this week
        response = notion.databases.query(
            database_id=email_queue_db,
            filter={
                "and": [
                    {"property": "Status", "status": {"equals": "Sent"}},
                    {"property": "Sent At", "date": {"on_or_after": start_date}},
                    {"property": "Sent At", "date": {"on_or_before": end_date}}
                ]
            }
        )
        stats['emails_sent'] = len(response['results'])

        # Responses this week (if Response Date property exists)
        try:
            response = notion.databases.query(
                database_id=email_queue_db,
                filter={
                    "and": [
                        {"property": "Response Received", "checkbox": {"equals": True}},
                        {"property": "Response Date", "date": {"on_or_after": start_date}},
                        {"property": "Response Date", "date": {"on_or_before": end_date}}
                    ]
                }
            )
            stats['responses'] = len(response['results'])
        except:
            pass

        # Bookings this week
        response = notion.databases.query(
            database_id=games_db,
            filter={
                "property": "Outreach Status",
                "select": {"equals": "Booked"}
            }
        )
        stats['bookings'] = len(response['results'])

        # Games not contacted
        response = notion.databases.query(
            database_id=games_db,
            filter={
                "property": "Outreach Status",
                "select": {"equals": "Not Contacted"}
            }
        )
        stats['not_contacted'] = len(response['results'])

    except APIResponseError as e:
        print(f"Warning: Could not calculate stats: {e}", file=sys.stderr)

    return stats


def generate_digest(notion, db_ids, target_date):
    """Generate the full daily digest."""
    games_db = db_ids['games']
    orders_db = db_ids['orders']
    email_queue_db = db_ids['email_queue']

    # Calculate date ranges
    target = datetime.strptime(target_date, '%Y-%m-%d')
    week_start = target_date
    week_end = (target + timedelta(days=7)).strftime('%Y-%m-%d')

    digest = []
    digest.append("=" * 50)
    digest.append(f"DAILY DIGEST - {target.strftime('%A, %B %d, %Y')}")
    digest.append("=" * 50)

    # Section 1: Follow-ups Due
    followups = get_followups_due(notion, games_db, target_date)
    digest.append("")
    digest.append(f"FOLLOW-UPS DUE ({len(followups)})")
    digest.append("-" * 30)

    if followups:
        for i, game in enumerate(followups[:10], 1):
            props = game['properties']
            title = extract_title(props.get('Game ID', {}).get('title', []))
            game_date = format_date(props.get('Game Date', {}).get('date', {}).get('start', ''))
            last_contacted = format_date(props.get('Last Contacted', {}).get('date', {}).get('start', ''))
            lead_score = props.get('Lead Score', {}).get('number', 0) or 0
            digest.append(f"  {i}. {title[:45]}")
            digest.append(f"     Game: {game_date} | Last contacted: {last_contacted} | Score: {lead_score}")
        if len(followups) > 10:
            digest.append(f"  ... and {len(followups) - 10} more")
    else:
        digest.append("  No follow-ups due today")

    # Section 2: Games This Week
    games = get_games_this_week(notion, games_db, week_start, week_end)
    digest.append("")
    digest.append(f"GAMES THIS WEEK ({len(games)})")
    digest.append("-" * 30)

    if games:
        # Group by date
        games_by_date = {}
        for game in games:
            props = game['properties']
            date_str = props.get('Game Date', {}).get('date', {}).get('start', '')
            if date_str:
                date_key = date_str[:10]
                if date_key not in games_by_date:
                    games_by_date[date_key] = []
                games_by_date[date_key].append(game)

        for date_key in sorted(games_by_date.keys())[:5]:  # First 5 days
            dt = datetime.strptime(date_key, '%Y-%m-%d')
            day_name = dt.strftime('%A, %b %d')
            if date_key == target_date:
                day_name = f"TODAY ({day_name})"
            elif date_key == (target + timedelta(days=1)).strftime('%Y-%m-%d'):
                day_name = f"TOMORROW ({day_name})"

            digest.append(f"  {day_name}:")
            for game in games_by_date[date_key][:3]:
                props = game['properties']
                title = extract_title(props.get('Game ID', {}).get('title', []))[:40]
                status = props.get('Outreach Status', {}).get('select', {}).get('name', 'Unknown')
                digest.append(f"    - {title} [{status}]")
            if len(games_by_date[date_key]) > 3:
                digest.append(f"    ... and {len(games_by_date[date_key]) - 3} more")
    else:
        digest.append("  No games scheduled this week")

    # Section 3: Pending Deliveries
    deliveries = get_pending_deliveries(notion, orders_db, week_start, week_end)
    digest.append("")
    digest.append(f"PENDING DELIVERIES ({len(deliveries)})")
    digest.append("-" * 30)

    if deliveries:
        for order in deliveries[:5]:
            props = order['properties']
            order_id = extract_title(props.get('Order ID', {}).get('title', []))
            delivery_date = format_date(props.get('Delivery Date', {}).get('date', {}).get('start', ''))
            amount = props.get('Total Amount', {}).get('number', 0) or 0
            digest.append(f"  - {order_id} | {delivery_date} | ${amount:.2f}")
    else:
        digest.append("  No pending deliveries")

    # Section 4: Hot Leads
    hot_leads = get_hot_leads(notion, games_db)
    digest.append("")
    digest.append(f"HOT LEADS - Score 80+ ({len(hot_leads)})")
    digest.append("-" * 30)

    if hot_leads:
        for game in hot_leads[:5]:
            props = game['properties']
            title = extract_title(props.get('Game ID', {}).get('title', []))[:40]
            score = props.get('Lead Score', {}).get('number', 0) or 0
            status = props.get('Outreach Status', {}).get('select', {}).get('name', 'Unknown')
            digest.append(f"  [{score}] {title}")
            digest.append(f"       Status: {status}")
    else:
        digest.append("  No hot leads (add Lead Score property to Games)")

    # Section 5: Weekly Stats
    stats = get_weekly_stats(notion, email_queue_db, games_db, week_start, week_end)
    digest.append("")
    digest.append("THIS WEEK'S STATS")
    digest.append("-" * 30)
    digest.append(f"  Emails Sent: {stats['emails_sent']}")
    digest.append(f"  Responses: {stats['responses']}")
    if stats['emails_sent'] > 0:
        rate = (stats['responses'] / stats['emails_sent']) * 100
        digest.append(f"  Response Rate: {rate:.1f}%")
    digest.append(f"  Bookings: {stats['bookings']}")
    digest.append(f"  Games Not Contacted: {stats['not_contacted']}")

    # Section 6: All-Time Conversion Funnel
    try:
        from notion_record_metrics import compute_metrics
        funnel = compute_metrics(notion, email_queue_db, games_db, db_ids.get('orders'))
        digest.append("")
        digest.append("CONVERSION FUNNEL (ALL TIME)")
        digest.append("-" * 30)
        digest.append(f"  Emails Sent:    {funnel['total_sent']}")
        digest.append(f"  Responses:      {funnel['total_responded']} ({funnel['response_rate']}%)")
        digest.append(f"  Bookings:       {funnel['total_booked']} ({funnel['booking_rate']}%)")
        if funnel.get('total_revenue', 0) > 0:
            digest.append(f"  Revenue:        ${funnel['total_revenue']:,.0f}")

        # Per-sport breakdown (top 5 by emails sent)
        by_sport = funnel.get('by_sport', {})
        if by_sport:
            sorted_sports = sorted(by_sport.items(), key=lambda x: x[1]['sent'], reverse=True)[:5]
            digest.append("")
            digest.append("  By Sport:")
            for sport, counts in sorted_sports:
                resp_pct = round(counts['responded'] / counts['sent'] * 100) if counts['sent'] > 0 else 0
                digest.append(f"    {sport}: {counts['sent']} sent, {counts['responded']} resp ({resp_pct}%), {counts['booked']} booked")
    except Exception:
        pass  # Metrics module not available or failed — skip silently

    # Section 7: 7-Day Trend (from Metrics DB if available)
    metrics_db = os.getenv('NOTION_METRICS_DB')
    if metrics_db:
        try:
            seven_days_ago = (target - timedelta(days=7)).strftime('%Y-%m-%d')
            recent = notion.databases.query(
                database_id=metrics_db,
                filter={"property": "Snapshot Date", "date": {"on_or_after": seven_days_ago}},
                sorts=[{"property": "Snapshot Date", "direction": "ascending"}]
            )
            results = recent.get('results', [])
            if len(results) >= 2:
                first_props = results[0]['properties']
                last_props = results[-1]['properties']
                sent_first = first_props.get('Emails Sent', {}).get('number', 0) or 0
                sent_last = last_props.get('Emails Sent', {}).get('number', 0) or 0
                resp_first = first_props.get('Responses', {}).get('number', 0) or 0
                resp_last = last_props.get('Responses', {}).get('number', 0) or 0
                book_first = first_props.get('Bookings', {}).get('number', 0) or 0
                book_last = last_props.get('Bookings', {}).get('number', 0) or 0

                digest.append("")
                digest.append("7-DAY TREND")
                digest.append("-" * 30)
                digest.append(f"  Emails: +{sent_last - sent_first}")
                digest.append(f"  Responses: +{resp_last - resp_first}")
                digest.append(f"  Bookings: +{book_last - book_first}")
        except Exception:
            pass  # Metrics DB not set up yet — skip silently

    digest.append("")
    digest.append("=" * 50)

    return "\n".join(digest)


def send_telegram(message, bot_token, chat_id):
    """Send message via Telegram."""
    if not requests:
        print("Error: requests package not installed for Telegram", file=sys.stderr)
        return False

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, data=data)
        if response.status_code == 200:
            print("Telegram message sent successfully", file=sys.stderr)
            return True
        else:
            print(f"Telegram error: {response.text}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"Telegram error: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Generate daily CRM digest"
    )
    parser.add_argument(
        "--date",
        default=datetime.now().strftime('%Y-%m-%d'),
        help="Date for digest (YYYY-MM-DD, default: today)"
    )
    parser.add_argument(
        "--send-telegram",
        action="store_true",
        help="Send digest via Telegram"
    )
    parser.add_argument(
        "--send-email",
        help="Email address to send digest to"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON instead of text"
    )

    args = parser.parse_args()

    # Initialize
    notion = get_notion_client()
    db_ids = get_database_ids()

    if not db_ids['games']:
        print("Error: NOTION_GAMES_DB not set", file=sys.stderr)
        sys.exit(1)

    # Generate digest
    print(f"Generating digest for {args.date}...", file=sys.stderr)
    digest = generate_digest(notion, db_ids, args.date)

    # Output
    if args.json:
        result = {
            "success": True,
            "date": args.date,
            "digest": digest
        }
        print(json.dumps(result, indent=2))
    else:
        print(digest)

    # Send via Telegram
    if args.send_telegram:
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        chat_id = os.getenv('TELEGRAM_CHAT_ID')

        if not bot_token or not chat_id:
            print("\nError: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID required", file=sys.stderr)
        else:
            # Convert to HTML-safe format for Telegram
            telegram_msg = digest.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            # Wrap in pre for monospace
            telegram_msg = f"<pre>{telegram_msg}</pre>"
            send_telegram(telegram_msg, bot_token, chat_id)

    # Send via email (placeholder)
    if args.send_email:
        print(f"\nEmail sending to {args.send_email} not implemented yet", file=sys.stderr)
        print("Use SendGrid integration from notion_send_email.py", file=sys.stderr)


if __name__ == "__main__":
    main()
