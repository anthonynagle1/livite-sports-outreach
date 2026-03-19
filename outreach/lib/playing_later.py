"""Batch 'playing later in season' detection.

Given a list of games, annotates each with sibling games (same visiting team).
This avoids N+1 Notion queries by doing a single pass over all future games.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone

from .notion import (
    get_client, get_db_id, paginated_query,
    extract_rich_text, extract_date, extract_relation_ids,
    extract_title, format_game_date,
)

logger = logging.getLogger(__name__)


def _today_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def get_playing_later_for_game(game_id):
    """For a single game, find all other UPCOMING games the same visiting team plays.

    Returns dict with visiting_team, total_games, other_games list.
    Only includes future games (today or later), excludes past ones.
    """
    client = get_client()
    games_db = get_db_id('games')
    today = _today_iso()

    page = client.pages.retrieve(page_id=game_id)
    props = page['properties']
    visiting_team = extract_rich_text(props.get('Visiting Team', {}))

    if not visiting_team:
        return {'visiting_team': '', 'total_games': 1, 'other_games': []}

    response = client.databases.query(
        database_id=games_db,
        filter={
            'and': [
                {'property': 'Visiting Team', 'rich_text': {'equals': visiting_team}},
                {'property': 'Game Date', 'date': {'on_or_after': today}},
            ]
        },
        sorts=[{'property': 'Game Date', 'direction': 'ascending'}],
    )

    other_games = []
    for p in response['results']:
        if p['id'] == game_id:
            continue
        gprops = p['properties']
        date_str = extract_date(gprops.get('Game Date', {}))

        # Resolve home school name
        home_name = ''
        home_rel = extract_relation_ids(gprops.get('Home Team', {}))
        if home_rel:
            try:
                hp = client.pages.retrieve(page_id=home_rel[0])
                home_name = extract_title(hp['properties'].get('School Name', {}).get('title', []))
            except Exception:
                pass

        other_games.append({
            'game_id': p['id'],
            'game_date': date_str,
            'game_date_display': format_game_date(date_str),
            'home_school': home_name,
        })

    return {
        'visiting_team': visiting_team,
        'total_games': len(other_games) + 1,
        'other_games': other_games,
    }


def batch_annotate_playing_later(games):
    """Annotate a list of game dicts with playing-later info.

    Expects each game dict to have 'visiting_team' and 'id' keys.
    Adds '_playing_later' key with {total, others: [{game_id, game_date, game_date_display}]}.

    Optimized: queries all future games once and groups by visiting team.
    """
    games_db = get_db_id('games')

    # Get all future games in one paginated query
    all_future = paginated_query(
        games_db,
        filter={'property': 'Game Date', 'date': {'on_or_after': _today_iso()}},
        sorts=[{'property': 'Game Date', 'direction': 'ascending'}],
    )

    # Group by Visiting Team
    team_games = defaultdict(list)
    for page in all_future:
        props = page['properties']
        vt = extract_rich_text(props.get('Visiting Team', {}))
        if vt:
            team_games[vt].append({
                'game_id': page['id'],
                'game_date': extract_date(props.get('Game Date', {})),
                'game_date_display': format_game_date(extract_date(props.get('Game Date', {}))),
            })

    # Annotate each input game
    for game in games:
        vt = game.get('visiting_team', '')
        siblings = team_games.get(vt, [])
        others = [s for s in siblings if s['game_id'] != game['id']]
        game['_playing_later'] = {
            'total': len(siblings),
            'others': others,
        }

    return games


def get_recommendation(game):
    """Generate a smart recommendation based on game timing and playing-later data.

    Returns a string recommendation or empty string.
    """
    playing_later = game.get('_playing_later', {})
    others = playing_later.get('others', [])
    game_date = game.get('game_date', '')

    if not game_date:
        return ''

    try:
        dt = datetime.fromisoformat(game_date)
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days_until = (dt - now).days
    except (ValueError, TypeError):
        return ''

    if days_until < 0:
        return ''

    parts = []

    # Multi-game recommendation with specifics
    if others:
        game_details = []
        for g in others[:3]:  # Show up to 3 upcoming games
            display = g.get('game_date_display', '')
            school = g.get('home_school', '')
            if display and school:
                game_details.append(f'{display} at {school}')
            elif display:
                game_details.append(display)
        extra = len(others) - 3
        detail_str = ', '.join(game_details)
        if extra > 0:
            detail_str += f' +{extra} more'
        parts.append(f'This game + {len(others)} more upcoming — offer multi-game deal ({detail_str})')

    if days_until <= 7:
        parts.append('Game soon — reach out today')
    elif days_until <= 30:
        parts.append('Good time to connect')

    return ' | '.join(parts) if parts else ''
