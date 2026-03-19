"""Cron endpoint — triggered by external scheduler (Render cron, cron-job.org, etc.).

Runs the same steps as tools/notion_cron_runner.py but as an HTTP endpoint
so it works on hosted platforms without a local crontab.
"""

import logging
import os
import sys
import threading

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint('cron', __name__)

# Add tools directory to path so we can import cron runner modules
_tools_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'tools')
if _tools_dir not in sys.path:
    sys.path.insert(0, os.path.abspath(_tools_dir))

# Simple lock to prevent overlapping runs
_running = False
_lock = threading.Lock()


@bp.route('/api/cron', methods=['POST'])
def run_cron():
    """Run the CRM cron cycle. Secured by API key."""
    global _running

    # Auth: require API key
    api_key = os.getenv('OUTREACH_CRON_KEY', os.getenv('HUB_API_KEY', ''))
    provided = request.args.get('api_key') or request.headers.get('X-Api-Key', '')
    if not api_key or provided != api_key:
        return jsonify({'error': 'Unauthorized'}), 401

    with _lock:
        if _running:
            return jsonify({'status': 'skipped', 'reason': 'Already running'}), 200
        _running = True

    try:
        results = _run_cron_steps()
        return jsonify({'status': 'ok', **results})
    except Exception as e:
        logger.error('Cron run failed: %s', e, exc_info=True)
        return jsonify({'status': 'error', 'error': str(e)}), 500
    finally:
        with _lock:
            _running = False


def _run_cron_steps():
    """Execute all cron steps and return summary."""
    results = {
        'steps_completed': 0,
        'steps_total': 4,
        'sent': 0,
        'responses': 0,
        'issues': [],
    }

    # Step 1: Process approved emails → send via Gmail
    try:
        from notion_process_approved import (
            get_notion_client, get_database_ids, process_approved_emails
        )
        notion = get_notion_client()
        email_queue_db, games_db, contacts_db = get_database_ids()
        stats = process_approved_emails(notion, email_queue_db, games_db, contacts_db)
        results['sent'] = stats.get('sent', 0)
        if stats.get('failed', 0) == 0:
            results['steps_completed'] += 1
        else:
            results['issues'].append(f"Send: {stats['failed']} failed")
    except Exception as e:
        logger.error('Approved emails processor failed: %s', e)
        results['issues'].append(f"Send error: {e}")

    # Step 2: Check Gmail for responses
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

        resp_stats = check_responses(notion, email_queue_db, games_db, service, our_email)
        results['responses'] = resp_stats.get('replies_found', 0)
        results['steps_completed'] += 1
    except Exception as e:
        logger.error('Response checker failed: %s', e)
        results['issues'].append(f"Response check error: {e}")

    # Step 3: Mark past games as Missed
    try:
        from notion_cron_runner import run_missed_game_marker
        missed = run_missed_game_marker()
        results['missed_marked'] = missed.get('missed', 0) + missed.get('no_response', 0)
        results['steps_completed'] += 1
    except Exception as e:
        logger.error('Missed game marker failed: %s', e)
        results['issues'].append(f"Missed marker error: {e}")

    # Step 4: Clean up expired emails
    try:
        from notion_cron_runner import run_expired_game_cleanup
        cleanup = run_expired_game_cleanup()
        results['expired_archived'] = cleanup.get('archived', 0)
        results['steps_completed'] += 1
    except Exception as e:
        logger.error('Cleanup failed: %s', e)
        results['issues'].append(f"Cleanup error: {e}")

    return results
