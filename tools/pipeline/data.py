"""Pipeline data layer — queries Notion and computes funnel/pipeline metrics."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# ── Funnel stage order (left-to-right in the pipeline) ────────────────────

FUNNEL_STAGES = [
    "Not Contacted",
    "Introduction Email - Sent",
    "Follow-Up Email - Sent",
    "Responded",
    "In Conversation",
    "Interested",
    "Booked",
]

NON_FUNNEL_STATUSES = [
    "Not Interested",
    "No Response",
    "Out of Office",
    "Missed",
]

ALL_STATUSES = FUNNEL_STAGES + NON_FUNNEL_STATUSES

# Default average catering order value for pipeline-value estimation
_DEFAULT_AVG_ORDER = 500.0


def compute_pipeline_dashboard() -> dict:
    """Query Notion games + email queue and return pipeline metrics dict.

    Returns a dict matching the schema documented in the module docstring.
    All Notion calls are wrapped in try/except so a Notion outage
    returns an empty-but-valid structure instead of crashing.
    """
    empty: dict = _empty_result()

    # ── 1. Load games ─────────────────────────────────────────────────────
    games: list[dict] = []
    try:
        from outreach.lib.notion import (
            get_client,
            get_db_id,
            paginated_query,
            extract_game_props,
        )

        games_db = get_db_id('games')
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=90)).strftime('%Y-%m-%d')

        # Fetch games from the last 90 days OR any future game
        raw_pages = paginated_query(
            games_db,
            filter={
                "or": [
                    {
                        "property": "Game Date",
                        "date": {"on_or_after": cutoff},
                    },
                    {
                        "property": "Game Date",
                        "date": {"is_empty": True},
                    },
                ]
            },
        )
        games = [extract_game_props(p) for p in raw_pages]
        logger.info("Pipeline: loaded %d games from Notion", len(games))
    except Exception:
        logger.warning("Pipeline: failed to load games from Notion", exc_info=True)
        return empty

    if not games:
        return empty

    # ── 2. Count by outreach_status ───────────────────────────────────────
    status_counts: dict[str, int] = {}
    for g in games:
        st = g.get('outreach_status') or 'Not Contacted'
        status_counts[st] = status_counts.get(st, 0) + 1

    total_games = len(games)

    # ── 3. Build funnel ───────────────────────────────────────────────────
    funnel: list[dict] = []
    prev_count: int | None = None
    for stage in FUNNEL_STAGES:
        count = status_counts.get(stage, 0)
        pct_of_total = (count / total_games * 100) if total_games else 0.0
        conversion: float | None = None
        if prev_count is not None and prev_count > 0:
            conversion = round(count / prev_count * 100, 1)
        funnel.append({
            'stage': stage,
            'count': count,
            'pct_of_total': round(pct_of_total, 1),
            'conversion_from_prev': conversion,
        })
        prev_count = count

    # ── 4. Win rate ───────────────────────────────────────────────────────
    contacted_total = total_games - status_counts.get('Not Contacted', 0)
    booked = status_counts.get('Booked', 0)
    win_rate = (booked / contacted_total * 100) if contacted_total > 0 else 0.0

    # ── 5. Pipeline value estimate ────────────────────────────────────────
    avg_order = _DEFAULT_AVG_ORDER
    interested = status_counts.get('Interested', 0)
    pipeline_value = avg_order * (interested + booked)

    # ── 6. Non-funnel statuses ────────────────────────────────────────────
    non_funnel: dict[str, int] = {}
    for st in NON_FUNNEL_STATUSES:
        non_funnel[st] = status_counts.get(st, 0)

    # ── 7. Email queue counts ─────────────────────────────────────────────
    email_queue: dict[str, int] = {'Draft': 0, 'Approved': 0, 'Sent': 0}
    try:
        from outreach.lib.notion import get_db_id, paginated_query, extract_email_queue_props

        eq_db = get_db_id('email_queue')
        eq_pages = paginated_query(eq_db)
        for p in eq_pages:
            eq = extract_email_queue_props(p)
            st = eq.get('status', '')
            if st in email_queue:
                email_queue[st] += 1
        logger.info("Pipeline: email queue — %s", email_queue)
    except Exception:
        logger.warning("Pipeline: failed to load email queue", exc_info=True)

    # ── 8. Upcoming games (next 30 days) ──────────────────────────────────
    now_date = datetime.now(timezone.utc).date()
    cutoff_30 = now_date + timedelta(days=30)
    upcoming: list[dict] = []
    for g in games:
        gd = g.get('game_date', '')
        if not gd:
            continue
        try:
            game_dt = datetime.fromisoformat(gd).date()
        except (ValueError, TypeError):
            continue
        if now_date <= game_dt <= cutoff_30:
            upcoming.append({
                'date': g.get('game_date', ''),
                'date_display': g.get('game_date_display', ''),
                'sport': g.get('sport', ''),
                'gender': g.get('gender', ''),
                'visiting_team': g.get('visiting_team', ''),
                'status': g.get('outreach_status', ''),
            })
    upcoming.sort(key=lambda x: x.get('date', ''))

    # ── 9. Assemble result ────────────────────────────────────────────────
    return {
        'funnel': funnel,
        'total_games': total_games,
        'contacted_total': contacted_total,
        'win_rate': round(win_rate, 1),
        'pipeline_value': pipeline_value,
        'avg_order_estimate': avg_order,
        'email_queue': email_queue,
        'upcoming_games': upcoming,
        'status_counts': status_counts,
        'non_funnel': non_funnel,
    }


def _empty_result() -> dict:
    """Return a valid but empty metrics dict for error / no-data cases."""
    return {
        'funnel': [
            {'stage': s, 'count': 0, 'pct_of_total': 0.0, 'conversion_from_prev': None}
            for s in FUNNEL_STAGES
        ],
        'total_games': 0,
        'contacted_total': 0,
        'win_rate': 0.0,
        'pipeline_value': 0.0,
        'avg_order_estimate': _DEFAULT_AVG_ORDER,
        'email_queue': {'Draft': 0, 'Approved': 0, 'Sent': 0},
        'upcoming_games': [],
        'status_counts': {},
        'non_funnel': {s: 0 for s in NON_FUNNEL_STATUSES},
    }
