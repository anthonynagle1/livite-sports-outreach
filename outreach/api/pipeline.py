"""Pipeline API — aggregated outreach status counts and activity feed."""

import logging
from datetime import datetime, timezone, timedelta

from flask import Blueprint, jsonify

from .auth import login_required
from ..lib.cache import cache_get, cache_set
from ..lib.notion import (
    get_client, get_db_id, paginated_query,
    extract_game_props, extract_email_queue_props, extract_select,
    extract_status, extract_date, extract_rich_text,
)

logger = logging.getLogger(__name__)

bp = Blueprint('pipeline', __name__)


@bp.route('/api/pipeline')
@login_required
def pipeline_stats():
    """Aggregated counts by outreach status + email queue stats."""
    cached = cache_get('pipeline', 300)
    if cached is not None:
        return jsonify(cached)

    games_db = get_db_id('games')
    email_db = get_db_id('email_queue')

    # Count games by outreach status
    all_games = paginated_query(games_db)
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
    for page in all_games:
        props = page['properties']
        status = extract_select(props.get('Outreach Status', {}))
        if status in status_counts:
            status_counts[status] += 1
        total_games += 1

    # Count emails by status
    all_emails = paginated_query(email_db)
    email_counts = {
        'Draft': 0,
        'Approved': 0,
        'Sent': 0,
    }
    for page in all_emails:
        props = page['properties']
        status = extract_select(props.get('Status', {}))
        if status in email_counts:
            email_counts[status] += 1

    result = {
        'games': status_counts,
        'games_total': total_games,
        'emails': email_counts,
    }
    cache_set('pipeline', result)
    return jsonify(result)


@bp.route('/api/pipeline/activity')
@login_required
def pipeline_activity():
    """Recent activity: last 20 emails sent or responded."""
    cached = cache_get('activity', 120)
    if cached is not None:
        return jsonify(cached)

    email_db = get_db_id('email_queue')

    # Get recent sent/responded emails
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
    cache_set('activity', result)
    return jsonify(result)
