#!/usr/bin/env python3
"""
Tool: notion_cron_runner.py
Purpose: Combined polling script for automated Notion CRM operations

Run via cron every 5 minutes:
    */5 * * * * cd /path/to/project && python3 tools/notion_cron_runner.py

What it does:
1. Processes games with "Create Draft" checkbox checked → creates email drafts
2. Processes emails in "Approved" status → sends via Gmail
3. Updates all related CRM fields

This is the automation backbone of the drag-to-send workflow.
"""

import atexit
import fcntl
import os
import sys
from datetime import datetime, date, timedelta

# Add tools directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

LOCK_FILE = '/tmp/livite-cron.lock'


def log(message):
    """Log with timestamp."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}", file=sys.stderr)


def acquire_lock():
    """Acquire exclusive lock to prevent overlapping cron runs. Returns lock file or None."""
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        atexit.register(release_lock, lock_fd)
        return lock_fd
    except (IOError, OSError):
        return None


def release_lock(lock_fd):
    """Release the cron lock."""
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
        os.unlink(LOCK_FILE)
    except (IOError, OSError):
        pass


def run_flagged_games_processor():
    """Process games with Create Draft checked."""
    log("Checking for flagged games...")

    try:
        from notion_client import Client
        from notion_draft_email import process_flagged_games, get_database_ids, get_notion_client

        notion = get_notion_client()
        games_db, contacts_db, templates_db, email_queue_db = get_database_ids()

        stats = process_flagged_games(
            notion, games_db, contacts_db, templates_db, email_queue_db,
            check_duplicates=True
        )

        if stats['created'] > 0:
            log(f"Created {stats['created']} draft(s)")
        if stats['duplicates'] > 0:
            log(f"Skipped {stats['duplicates']} duplicate(s)")
        if stats['skipped'] > 0:
            log(f"Skipped {stats['skipped']} (no contact)")

        return stats

    except Exception as e:
        log(f"Error in flagged games processor: {e}")
        return {'created': 0, 'skipped': 0, 'duplicates': 0, 'failed': 1}


def run_followup_processor():
    """Create follow-up email drafts for games past their follow-up date."""
    log("Checking for follow-up emails due...")

    try:
        from notion_sync_followups import (
            get_notion_client, get_database_ids, sync_followups
        )

        notion = get_notion_client()
        games_db, contacts_db, templates_db, email_queue_db = get_database_ids()

        stats = sync_followups(
            notion, games_db, contacts_db, templates_db, email_queue_db
        )

        if stats['drafts_created'] > 0:
            log(f"Follow-up drafts created: {stats['drafts_created']}")
        if stats['skipped'] > 0:
            log(f"Follow-ups skipped: {stats['skipped']}")

        return stats

    except Exception as e:
        log(f"Error in follow-up processor: {e}")
        return {'drafts_created': 0, 'skipped': 0, 'failed': 1}


def run_approved_emails_processor():
    """Process approved emails and send via Gmail (with rate limiting)."""
    log("Checking for approved emails...")

    try:
        from notion_process_approved import (
            get_notion_client, get_database_ids, process_approved_emails
        )

        notion = get_notion_client()
        email_queue_db, games_db, contacts_db = get_database_ids()

        stats = process_approved_emails(notion, email_queue_db, games_db, contacts_db)

        if stats['sent'] > 0:
            log(f"Sent {stats['sent']} email(s)")
        if stats['failed'] > 0:
            log(f"Failed to send {stats['failed']} email(s)")

        return stats

    except Exception as e:
        log(f"Error in approved emails processor: {e}")
        return {'processed': 0, 'sent': 0, 'failed': 1, 'deferred': 0}


def run_response_checker():
    """Check Gmail for responses to sent emails."""
    log("Checking for email responses...")

    try:
        from check_gmail_responses import check_responses
        from notion_send_gmail import get_gmail_service
        from notion_client import Client

        notion = Client(auth=os.getenv('NOTION_API_KEY'))
        email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB')
        games_db = os.getenv('NOTION_GAMES_DB')

        service = get_gmail_service()
        profile = service.users().getProfile(userId='me').execute()
        our_email = profile['emailAddress']

        stats = check_responses(notion, email_queue_db, games_db, service, our_email)

        if stats.get('replies_found', 0) > 0:
            type_summary = ', '.join(f"{k}: {v}" for k, v in stats.get('by_type', {}).items())
            log(f"Found {stats['replies_found']} response(s) ({type_summary})")

        return stats

    except Exception as e:
        log(f"Error in response checker: {e}")
        return {'checked': 0, 'responded': 0, 'booked': 0, 'declined': 0, 'error': str(e)}


def run_undo_processors():
    """Process Undo Order and Undo Outreach checkboxes (runs before order conversion)."""
    log("Checking for undo requests...")

    try:
        from notion_convert_to_order import (
            process_undo_orders, process_undo_outreach,
            process_dashboard_undo_orders, process_sports_order_undo
        )
        from notion_client import Client

        notion = Client(auth=os.getenv('NOTION_API_KEY'))
        email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB')
        orders_db = os.getenv('NOTION_ORDERS_DB')
        games_db = os.getenv('NOTION_GAMES_DB')
        contacts_db = os.getenv('NOTION_CONTACTS_DB')

        undo_order_stats = process_undo_orders(notion, email_queue_db, orders_db, games_db)
        undo_outreach_stats = process_undo_outreach(
            notion, email_queue_db, orders_db, games_db, contacts_db
        )

        # Also check Dashboard Catering Orders for Undo Order checkbox
        dashboard_undo_stats = process_dashboard_undo_orders(
            notion, orders_db, games_db, email_queue_db
        )

        # Also check Sports Orders DB for Undo Order checkbox
        sports_undo_stats = process_sports_order_undo(
            notion, orders_db, games_db, email_queue_db
        )

        total_undone = (undo_order_stats.get('undone', 0)
                        + undo_outreach_stats.get('undone', 0)
                        + dashboard_undo_stats.get('undone', 0)
                        + sports_undo_stats.get('undone', 0))
        if total_undone > 0:
            parts = []
            if undo_order_stats.get('undone', 0) > 0:
                parts.append(f"{undo_order_stats['undone']} order(s) undone")
            if undo_outreach_stats.get('undone', 0) > 0:
                parts.append(f"{undo_outreach_stats['undone']} outreach(es) undone")
            if dashboard_undo_stats.get('undone', 0) > 0:
                parts.append(f"{dashboard_undo_stats['undone']} dashboard order(s) undone")
            if sports_undo_stats.get('undone', 0) > 0:
                parts.append(f"{sports_undo_stats['undone']} sports order(s) undone")
            log(', '.join(parts))

        return {
            'undo_orders': undo_order_stats,
            'undo_outreach': undo_outreach_stats,
            'undo_dashboard': dashboard_undo_stats,
            'undo_sports': sports_undo_stats
        }

    except Exception as e:
        log(f"Error in undo processors: {e}")
        return {'undo_orders': {}, 'undo_outreach': {}}


def run_convert_to_order_processor():
    """Process emails flagged for order conversion."""
    log("Checking for order conversions...")

    try:
        from notion_convert_to_order import process_flagged_conversions
        from notion_client import Client

        notion = Client(auth=os.getenv('NOTION_API_KEY'))
        email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB')
        orders_db = os.getenv('NOTION_ORDERS_DB')
        games_db = os.getenv('NOTION_GAMES_DB')

        stats = process_flagged_conversions(notion, email_queue_db, orders_db, games_db)

        if stats.get('created', 0) > 0:
            log(f"Created {stats['created']} Catering Operations order(s)")
        if stats.get('skipped_duplicate', 0) > 0:
            log(f"Duplicates skipped: {stats['skipped_duplicate']}")

        return stats

    except Exception as e:
        log(f"Error in order conversion processor: {e}")
        return {'processed': 0, 'created': 0, 'failed': 0, 'dashboard_created': 0, 'dashboard_pending': 0}


def run_expired_game_cleanup():
    """Archive emails from queue where game date has passed and email wasn't sent."""
    log("Checking for expired game emails...")

    try:
        from notion_client import Client
        from notion_client.errors import APIResponseError

        notion = Client(auth=os.getenv('NOTION_API_KEY'))
        email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB')
        today = date.today().isoformat()

        archived = 0
        for status in ['Draft', 'Approved']:
            response = notion.databases.query(
                database_id=email_queue_db,
                filter={
                    "and": [
                        {"property": "Status", "select": {"equals": status}},
                        {"property": "Game Date", "date": {"before": today}},
                    ]
                }
            )

            for page in response['results']:
                try:
                    notion.pages.update(page_id=page['id'], archived=True)
                    archived += 1
                except APIResponseError as e:
                    log(f"  Error archiving {page['id']}: {e}")

        if archived > 0:
            log(f"Archived {archived} expired email(s)")

        return {'archived': archived}

    except Exception as e:
        log(f"Error in expired game cleanup: {e}")
        return {'archived': 0}


def run_missed_game_marker():
    """Mark past games as Missed (never contacted) or No Response (emailed 14+ days, no reply)."""
    log("Checking for missed/unresponsive games...")

    try:
        from notion_client import Client
        from notion_client.errors import APIResponseError
        import time

        notion = Client(auth=os.getenv('NOTION_API_KEY'))
        games_db = os.getenv('NOTION_GAMES_DB')
        today = date.today().isoformat()
        cutoff_14d = (date.today() - timedelta(days=14)).isoformat()

        stats = {'missed': 0, 'no_response': 0}

        # 1. Past games still "Not Contacted" → "Missed"
        has_more = True
        start_cursor = None
        while has_more:
            kwargs = {
                "database_id": games_db,
                "filter": {
                    "and": [
                        {"property": "Game Date", "date": {"before": today}},
                        {"property": "Outreach Status", "select": {"equals": "Not Contacted"}},
                    ]
                }
            }
            if start_cursor:
                kwargs["start_cursor"] = start_cursor
            response = notion.databases.query(**kwargs)
            for page in response['results']:
                try:
                    notion.pages.update(
                        page_id=page['id'],
                        properties={"Outreach Status": {"select": {"name": "Missed"}}}
                    )
                    stats['missed'] += 1
                    time.sleep(0.35)
                except APIResponseError as e:
                    log(f"  Error marking missed: {e}")
            has_more = response.get('has_more', False)
            start_cursor = response.get('next_cursor')

        # 2. Past games "Email Sent" with Last Contacted 14+ days ago → "No Response"
        has_more = True
        start_cursor = None
        while has_more:
            kwargs = {
                "database_id": games_db,
                "filter": {
                    "and": [
                        {"property": "Game Date", "date": {"before": today}},
                        {"property": "Outreach Status", "select": {"equals": "Email Sent"}},
                        {"property": "Last Contacted", "date": {"before": cutoff_14d}},
                    ]
                }
            }
            if start_cursor:
                kwargs["start_cursor"] = start_cursor
            response = notion.databases.query(**kwargs)
            for page in response['results']:
                try:
                    notion.pages.update(
                        page_id=page['id'],
                        properties={"Outreach Status": {"select": {"name": "No Response"}}}
                    )
                    stats['no_response'] += 1
                    time.sleep(0.35)
                except APIResponseError as e:
                    log(f"  Error marking no response: {e}")
            has_more = response.get('has_more', False)
            start_cursor = response.get('next_cursor')

        if stats['missed'] > 0 or stats['no_response'] > 0:
            log(f"Marked {stats['missed']} missed, {stats['no_response']} no response")

        return stats

    except Exception as e:
        log(f"Error in missed game marker: {e}")
        return {'missed': 0, 'no_response': 0}


def check_oauth_health():
    """Verify Gmail OAuth token is valid or can be refreshed."""
    try:
        from notion_send_gmail import get_gmail_credentials
        creds = get_gmail_credentials()
        if creds and creds.valid:
            return True
        log("WARNING: Gmail OAuth token could not be refreshed!")
        return False
    except Exception as e:
        log(f"CRITICAL: Gmail OAuth error: {e}")
        log("Token may need to be re-generated on Mac and copied to Pi")
        return False


def run_daily_digest():
    """Send daily digest email once per day (first cron run after 8 AM)."""
    now = datetime.now()
    marker_file = '/tmp/livite-digest-sent.date'

    # Check if already sent today
    today_str = now.strftime('%Y-%m-%d')
    if os.path.exists(marker_file):
        try:
            with open(marker_file, 'r') as f:
                if f.read().strip() == today_str:
                    return  # Already sent today
        except Exception:
            pass

    # Only send after 8 AM
    if now.hour < 8:
        return

    log("Sending daily digest...")
    try:
        from notion_daily_digest import (
            generate_digest, get_notion_client as digest_notion,
            get_database_ids as digest_db_ids
        )
        from notion_send_gmail import send_email as send_gmail

        notion = digest_notion()
        db_ids = digest_db_ids()
        digest_text = generate_digest(notion, db_ids, today_str)

        to_email = os.getenv('FROM_EMAIL', '')
        if to_email and digest_text:
            result = send_gmail(
                to_email=to_email,
                subject=f"Livite Daily Digest - {now.strftime('%B %d')}",
                body=digest_text
            )
            if result.get('success'):
                log("Daily digest sent")
            else:
                log(f"Daily digest send failed: {result.get('error')}")

        # Mark as sent regardless (avoid spam on failure)
        with open(marker_file, 'w') as f:
            f.write(today_str)

        # Record daily metrics snapshot
        metrics_db = os.getenv('NOTION_METRICS_DB')
        if metrics_db:
            try:
                from notion_record_metrics import compute_metrics, record_metrics
                from notion_client import Client as MetricsClient
                m_notion = MetricsClient(auth=os.getenv('NOTION_API_KEY'))
                metrics = compute_metrics(
                    m_notion,
                    os.getenv('NOTION_EMAIL_QUEUE_DB'),
                    os.getenv('NOTION_GAMES_DB'),
                    os.getenv('NOTION_ORDERS_DB')
                )
                record_metrics(m_notion, metrics_db, metrics)
                log("Daily metrics snapshot recorded")
            except Exception as me:
                log(f"Error recording metrics: {me}")

    except Exception as e:
        log(f"Error sending daily digest: {e}")


def main():
    # Acquire lock to prevent overlapping runs
    lock_fd = acquire_lock()
    if lock_fd is None:
        log("Skipping: previous cron cycle still running")
        return

    log("=" * 50)
    log("NOTION CRM CRON RUNNER")
    log("=" * 50)

    # Track health across all steps
    steps_completed = 0
    steps_total = 8
    health_issues = []

    # Pre-flight: check OAuth health
    oauth_ok = check_oauth_health()
    if not oauth_ok:
        log("Skipping Gmail-dependent tasks due to OAuth failure")
        health_issues.append("Gmail OAuth failed")

    # Step 1: Process flagged games (create drafts)
    draft_stats = run_flagged_games_processor()
    if draft_stats.get('failed', 0) == 0:
        steps_completed += 1
    else:
        health_issues.append("Draft creation had failures")

    # Step 2: Create follow-up drafts for unanswered emails
    followup_stats = run_followup_processor()
    if followup_stats.get('failed', 0) == 0:
        steps_completed += 1
    else:
        health_issues.append("Follow-up processor had failures")

    if oauth_ok:
        # Step 3: Process approved emails (send via Gmail)
        send_stats = run_approved_emails_processor()
        if send_stats.get('failed', 0) == 0:
            steps_completed += 1
        else:
            health_issues.append(f"Email send: {send_stats['failed']} failed")

        # Step 4: Check for email responses
        response_stats = run_response_checker()
        if 'error' not in response_stats:
            steps_completed += 1
        else:
            health_issues.append("Response check errored")
    else:
        send_stats = {'processed': 0, 'sent': 0, 'failed': 0, 'deferred': 0}
        response_stats = {'replies_found': 0}

    # Step 5: Process undo requests (before order conversion)
    undo_stats = run_undo_processors()
    steps_completed += 1

    # Step 6: Process order conversions
    order_stats = run_convert_to_order_processor()
    steps_completed += 1

    # Step 7: Clean up expired game emails
    cleanup_stats = run_expired_game_cleanup()
    steps_completed += 1

    # Step 8: Mark past games as Missed / No Response
    missed_stats = run_missed_game_marker()
    steps_completed += 1

    # Daily digest (once per day, not counted as a step)
    # PAUSED per user request 2026-02-25 — uncomment to re-enable
    # if oauth_ok:
    #     run_daily_digest()

    # Summary
    log("-" * 50)
    log(f"Drafts created: {draft_stats.get('created', 0)}")
    followups_created = followup_stats.get('drafts_created', 0)
    if followups_created > 0:
        log(f"Follow-up drafts: {followups_created}")
    deferred = send_stats.get('deferred', 0)
    sent_msg = f"Emails sent: {send_stats.get('sent', 0)}"
    if deferred > 0:
        sent_msg += f" ({deferred} deferred to next cycle)"
    log(sent_msg)
    log(f"Responses found: {response_stats.get('replies_found', 0)}")
    undo_order_count = undo_stats.get('undo_orders', {}).get('undone', 0)
    undo_outreach_count = undo_stats.get('undo_outreach', {}).get('undone', 0)
    undo_dashboard_count = undo_stats.get('undo_dashboard', {}).get('undone', 0)
    undo_sports_count = undo_stats.get('undo_sports', {}).get('undone', 0)
    total_undone = undo_order_count + undo_outreach_count + undo_dashboard_count + undo_sports_count
    if total_undone > 0:
        log(f"Undone: {undo_order_count} order(s), {undo_outreach_count} outreach(es), {undo_dashboard_count} dashboard, {undo_sports_count} sports")
    log(f"Orders created: {order_stats.get('created', 0)}")
    if cleanup_stats.get('archived', 0) > 0:
        log(f"Expired emails archived: {cleanup_stats['archived']}")
    if missed_stats.get('missed', 0) > 0 or missed_stats.get('no_response', 0) > 0:
        log(f"Games marked: {missed_stats['missed']} missed, {missed_stats['no_response']} no response")

    # Health status
    if not health_issues:
        log(f"HEALTH: OK ({steps_completed}/{steps_total} steps completed)")
    else:
        issues_str = '; '.join(health_issues)
        log(f"HEALTH: DEGRADED ({steps_completed}/{steps_total} steps — {issues_str})")
    log("=" * 50)


if __name__ == "__main__":
    main()
