"""Pipeline API — aggregated outreach status counts and activity feed."""

import logging
import threading
from datetime import datetime, timezone, timedelta

from flask import Blueprint, jsonify

from .auth import login_required
from ..lib.cache import cache_get, cache_set, needs_refresh
from ..lib.notion import (
    get_client, get_db_id, paginated_query,
    extract_game_props, extract_email_queue_props, extract_select,
    extract_status, extract_date, extract_rich_text,
)

logger = logging.getLogger(__name__)

bp = Blueprint('pipeline', __name__)

CACHE_TTL = 600      # 10 min fresh
STALE_TTL = 1800     # 30 min stale-while-revalidate


def _fetch_pipeline(cache_key='pipeline'):
    """Fetch pipeline stats from Notion and cache them."""
    try:
        games_db = get_db_id('games')
        email_db = get_db_id('email_queue')

        status_counts = {
            'Not Contacted': 0,
            'Introduction Email - Sent': 0,
            'Follow-Up Email - Sent': 0,
            'Responded': 0,
            'In Conversation': 0,
            'Interested': 0,
            'Booked': 0,
            'Not Interested': 0,
            'No Response': 0,
            'Out of Office': 0,
            'Missed': 0,
        }
        total_games = 0
        for page in paginated_query(games_db):
            props = page['properties']
            s = extract_select(props.get('Outreach Status', {}))
            if s in status_counts:
                status_counts[s] += 1
            total_games += 1

        email_counts = {'Draft': 0, 'Approved': 0, 'Sent': 0}
        for page in paginated_query(email_db):
            props = page['properties']
            s = extract_select(props.get('Status', {}))
            if s in email_counts:
                email_counts[s] += 1

        result = {
            'games': status_counts,
            'games_total': total_games,
            'emails': email_counts,
        }
        cache_set(cache_key, result)
        return result
    except Exception:
        logger.error("Background pipeline refresh failed", exc_info=True)
        return None


@bp.route('/api/pipeline')
@login_required
def pipeline_stats():
    """Aggregated counts by outreach status + email queue stats."""
    cached, is_stale = cache_get('pipeline', CACHE_TTL, STALE_TTL)
    if cached is not None:
        if is_stale and needs_refresh('pipeline'):
            threading.Thread(target=_fetch_pipeline, daemon=True).start()
        return jsonify(cached)

    result = _fetch_pipeline()
    if result is None:
        return jsonify({'error': 'Failed to fetch pipeline stats'}), 500
    return jsonify(result)


def _fetch_activity(cache_key='activity'):
    """Fetch recent activity from Notion and cache it."""
    try:
        email_db = get_db_id('email_queue')
        recent = paginated_query(
            email_db,
            filter={
                'or': [
                    {'property': 'Status', 'select': {'equals': 'Sent'}},
                    {'property': 'Status', 'select': {'equals': 'Responded'}},
                ]
            },
            sorts=[{'property': 'Sent At', 'direction': 'descending'}],
            page_size=20,
        )

        activity = []
        for page in recent[:20]:
            e = extract_email_queue_props(page)
            activity.append({
                'id': e['id'],
                'subject': e['subject'],
                'status': e['status'],
                'school': e['school'],
                'sport': e['sport'],
                'to_email': e['to_email'],
                'sent_at': e['sent_at'],
                'game_date': e['game_date'],
            })

        result = {'activity': activity}
        cache_set(cache_key, result)
        return result
    except Exception:
        logger.error("Background activity refresh failed", exc_info=True)
        return None


@bp.route('/api/pipeline/activity')
@login_required
def pipeline_activity():
    """Recent activity: last 20 emails sent or responded."""
    cached, is_stale = cache_get('activity', CACHE_TTL, STALE_TTL)
    if cached is not None:
        if is_stale and needs_refresh('activity'):
            threading.Thread(target=_fetch_activity, daemon=True).start()
        return jsonify(cached)

    result = _fetch_activity()
    if result is None:
        return jsonify({'error': 'Failed to fetch activity'}), 500
    return jsonify(result)
