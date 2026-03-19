"""Games API — list, filter, detail, playing-later."""

import logging

from flask import Blueprint, jsonify, request

from .auth import login_required
from ..lib.cache import cache_get, cache_set, cache_clear
from ..lib.notion import (
    get_client, get_db_id, paginated_query,
    extract_game_props, resolve_school_name, resolve_contact_summary,
    resolve_contact_full,
)
from ..lib.playing_later import (
    batch_annotate_playing_later, get_playing_later_for_game, get_recommendation,
)

logger = logging.getLogger(__name__)

bp = Blueprint('games', __name__)

CACHE_TTL = 120  # 2 minutes


def _build_filter(args):
    """Build a Notion filter from query params."""
    conditions = []

    sport = args.get('sport')
    if sport:
        conditions.append({'property': 'Sport', 'select': {'equals': sport}})

    status = args.get('status')
    if status:
        conditions.append({'property': 'Outreach Status', 'select': {'equals': status}})

    gender = args.get('gender')
    if gender:
        conditions.append({'property': 'Gender', 'select': {'equals': gender}})

    date_from = args.get('date_from')
    if date_from:
        conditions.append({'property': 'Game Date', 'date': {'on_or_after': date_from}})

    date_to = args.get('date_to')
    if date_to:
        conditions.append({'property': 'Game Date', 'date': {'on_or_before': date_to}})

    local_only = args.get('local')
    if local_only and local_only.lower() == 'true':
        conditions.append({'property': 'Local Game', 'checkbox': {'equals': True}})

    has_contact = args.get('has_contact')
    if has_contact == 'true':
        conditions.append({'property': 'Contact', 'relation': {'is_not_empty': True}})
    elif has_contact == 'false':
        conditions.append({'property': 'Contact', 'relation': {'is_empty': True}})

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {'and': conditions}


@bp.route('/api/games')
@login_required
def list_games():
    """List games with optional filters. Returns enriched game data with playing-later annotations."""
    cache_key = f'games:{request.query_string.decode()}'
    cached = cache_get(cache_key, CACHE_TTL)
    if cached is not None:
        return jsonify(cached)

    games_db = get_db_id('games')
    notion_filter = _build_filter(request.args)

    kwargs = {
        'sorts': [{'property': 'Game Date', 'direction': 'ascending'}],
    }
    if notion_filter:
        kwargs['filter'] = notion_filter

    pages = paginated_query(games_db, **kwargs)
    games = [extract_game_props(p) for p in pages]

    # Batch resolve school names for home teams
    school_cache = {}
    for g in games:
        for sid in g.get('home_team_ids', []):
            if sid not in school_cache:
                school_cache[sid] = resolve_school_name(sid)
        g['home_school'] = school_cache.get(
            g['home_team_ids'][0], '') if g.get('home_team_ids') else ''

    # Batch annotate playing-later
    games = batch_annotate_playing_later(games)

    # Add recommendations
    for g in games:
        g['recommendation'] = get_recommendation(g)

    result = {'games': games, 'count': len(games)}
    cache_set(cache_key, result)
    return jsonify(result)


@bp.route('/api/games/<game_id>')
@login_required
def get_game(game_id):
    """Get full game detail with contact info and playing-later data."""
    client = get_client()

    try:
        page = client.pages.retrieve(page_id=game_id)
    except Exception as e:
        logger.error('Failed to retrieve game %s: %s', game_id, e)
        return jsonify({'error': 'Game not found'}), 404

    game = extract_game_props(page)

    # Resolve home school
    if game.get('home_team_ids'):
        game['home_school'] = resolve_school_name(game['home_team_ids'][0])
    else:
        game['home_school'] = ''

    # Resolve contact with response tracking fields
    if game.get('contact_ids'):
        game['contact'] = resolve_contact_full(game['contact_ids'][0])
    else:
        game['contact'] = None

    # Get playing-later data
    playing_later = get_playing_later_for_game(game_id)
    game['_playing_later'] = {
        'total': playing_later['total_games'],
        'others': playing_later['other_games'],
    }
    game['recommendation'] = get_recommendation(game)

    return jsonify(game)


@bp.route('/api/games/<game_id>', methods=['PUT'])
@login_required
def update_game(game_id):
    """Update game properties (status, notes)."""
    client = get_client()
    data = request.get_json(silent=True) or {}

    properties = {}

    status = data.get('outreach_status')
    if status:
        properties['Outreach Status'] = {'select': {'name': status}}

    notes = data.get('notes')
    if notes is not None:
        properties['Notes'] = {'rich_text': [{'text': {'content': notes}}]}

    if not properties:
        return jsonify({'error': 'No valid fields to update'}), 400

    try:
        client.pages.update(page_id=game_id, properties=properties)
        cache_clear()  # Invalidate game caches
        return jsonify({'ok': True})
    except Exception as e:
        logger.error('Failed to update game %s: %s', game_id, e)
        return jsonify({'error': str(e)}), 500


@bp.route('/api/games/<game_id>/emails')
@login_required
def get_game_emails(game_id):
    """Get all emails associated with a game."""
    from ..lib.notion import extract_email_queue_props

    email_db = get_db_id('email_queue')
    pages = paginated_query(email_db, filter={
        'property': 'Game',
        'relation': {'contains': game_id},
    }, sorts=[{'timestamp': 'created_time', 'direction': 'descending'}])

    emails = [extract_email_queue_props(p) for p in pages]
    return jsonify({'emails': emails, 'count': len(emails)})


@bp.route('/api/contacts/<contact_id>/emails')
@login_required
def get_contact_emails(contact_id):
    """Get ALL emails for a contact across all games — full history."""
    from ..lib.notion import extract_email_queue_props

    email_db = get_db_id('email_queue')
    pages = paginated_query(email_db, filter={
        'property': 'Contact',
        'relation': {'contains': contact_id},
    }, sorts=[{'timestamp': 'created_time', 'direction': 'descending'}])

    emails = [extract_email_queue_props(p) for p in pages]
    return jsonify({'emails': emails, 'count': len(emails)})


@bp.route('/api/games/<game_id>/draft', methods=['POST'])
@login_required
def create_draft(game_id):
    """Create a draft outreach email for a game using the style guide templates."""
    import uuid
    from ..lib.notion import (
        extract_game_props, resolve_contact_full, resolve_school_name,
        format_date_long, extract_email_queue_props,
    )

    client = get_client()

    # Load game
    try:
        page = client.pages.retrieve(page_id=game_id)
    except Exception as e:
        logger.error('Failed to retrieve game %s: %s', game_id, e)
        return jsonify({'error': 'Game not found'}), 404

    game = extract_game_props(page)

    # Resolve home school
    home_school = ''
    if game.get('home_team_ids'):
        home_school = resolve_school_name(game['home_team_ids'][0])
    game['home_school'] = home_school

    # Resolve contact
    contact = None
    if game.get('contact_ids'):
        contact = resolve_contact_full(game['contact_ids'][0])

    if not contact or not contact.get('email'):
        return jsonify({'error': 'No contact with email found for this game'}), 400

    # Check if contact is flagged
    if contact.get('do_not_contact'):
        return jsonify({'error': 'Contact is flagged Do Not Contact'}), 400
    if contact.get('last_response_type') == 'Not Interested':
        return jsonify({'error': 'Contact previously declined'}), 400

    # Check email history for this contact — overrides relationship field
    # Only count sport-related outreach (must be linked to a game)
    email_db = get_db_id('email_queue')
    prior_emails = paginated_query(email_db, filter={
        'and': [
            {'property': 'Contact', 'relation': {'contains': contact['id']}},
            {'property': 'Status', 'select': {'does_not_equal': 'Draft'}},
            {'property': 'Game', 'relation': {'is_not_empty': True}},
        ]
    }, sorts=[{'timestamp': 'created_time', 'direction': 'descending'}])

    prior = [extract_email_queue_props(p) for p in prior_emails]
    has_prior_emails = len(prior) > 0
    has_response = any(e.get('response_date') for e in prior)

    warnings = []
    if has_prior_emails:
        latest = prior[0]
        latest_date = latest.get('sent_at') or latest.get('created', '')[:10]
        if has_response:
            warnings.append(f"Previously emailed {latest_date} — responded. Using returning customer template.")
        else:
            warnings.append(f"Previously emailed {latest_date} — no response. Using follow-up template.")

    # Determine template: actual email history takes priority over relationship field
    relationship = contact.get('relationship', '')
    if has_prior_emails and has_response:
        template_type = 'returning'
    elif has_prior_emails:
        template_type = 'follow_up'
    elif relationship == 'Previous Customer':
        template_type = 'returning'
    elif relationship in ('Previously Contacted', 'Previously Responded'):
        template_type = 'follow_up'
    else:
        template_type = 'cold'

    first_name = contact['name'].split()[0] if contact.get('name') else 'Coach'

    # Format game date
    game_date_fmt = format_date_long(game.get('game_date', ''))

    # Build subject: "{School} {Sport} at {Home School}"
    subject = f"{game.get('visiting_team', 'Your team')} {game.get('sport', '')} at {home_school}"

    # Build body based on template type (determined by email history + relationship)
    if template_type == 'returning':
        body = (
            f"Hi {first_name},\n\n"
            f"Hope all is well — I wanted to reach out again as {game.get('visiting_team', 'your team')} "
            f"{game.get('sport', '')} season gets going. We really enjoyed working with your team and "
            f"would love to be a part of things again this year.\n\n"
            f"I saw you have a game at {home_school} on {game_date_fmt} and wanted to see if you'd like "
            f"us to set up pre-game or post-game meals for that trip. Same easy process — we deliver "
            f"right to your bus on your schedule.\n\n"
            f"Here's our updated catering menu: https://www.livite.com/catering/livite\n\n"
            f"Let me know if you'd like to get something on the books — always happy to work with your team.\n\n"
            f"Meire Medeiros\n"
            f"Catering Manager, Livite\n"
            f"508-768-5086\n"
            f"catering@livite.com\n"
            f"www.livite.com"
        )
    elif template_type == 'follow_up':
        body = (
            f"Hi {first_name},\n\n"
            f"Just following up — I know the season keeps you busy.\n\n"
            f"If you still need meal options for {game.get('visiting_team', 'your team')}'s trip to "
            f"{home_school}, I can put together a quick quote based on your roster size. "
            f"Most teams we work with order for 30-50 people, but we customize to fit.\n\n"
            f"Happy to jump on a quick call or just send options over by email — whatever's easier for you.\n\n"
            f"Meire Medeiros\n"
            f"Catering Manager, Livite\n"
            f"508-768-5086\n"
            f"catering@livite.com"
        )
    else:
        # Cold outreach (default)
        body = (
            f"Hi {first_name},\n\n"
            f"I saw that {game.get('visiting_team', 'your team')} {game.get('sport', '')} is playing "
            f"at {home_school} on {game_date_fmt} and wanted to reach out.\n\n"
            f"I'm Meire, the Catering Manager at Livite — we're a fast-casual restaurant in the "
            f"Boston area that works with a lot of college teams on game days.\n\n"
            f"We handle pre-game and post-game meals delivered right to your bus so your staff "
            f"doesn't have to deal with the logistics.\n\n"
            f"If you're looking for meal support for that trip, I'd love to send over our catering "
            f"menu with pricing.\n\n"
            f"Here's our catering menu: https://www.livite.com/catering/livite\n\n"
            f"Feel free to reach out anytime — I'd love the chance to support your team.\n\n"
            f"Meire Medeiros\n"
            f"Catering Manager, Livite\n"
            f"508-768-5086\n"
            f"catering@livite.com\n"
            f"www.livite.com"
        )

    # Create Email Queue entry as Draft
    email_id = f"draft-{uuid.uuid4().hex[:8]}"

    properties = {
        "Email ID": {"title": [{"text": {"content": email_id}}]},
        "Subject": {"rich_text": [{"text": {"content": subject}}]},
        "Body": {"rich_text": [{"text": {"content": body[:2000]}}]},
        "Status": {"select": {"name": "Draft"}},
        "To Email": {"email": contact['email']},
        "Sport": {"rich_text": [{"text": {"content": game.get('sport', '')}}]},
        "Game": {"relation": [{"id": game_id}]},
        "Contact": {"relation": [{"id": contact['id']}]},
    }

    if game.get('game_date'):
        properties["Game Date"] = {"date": {"start": game['game_date']}}

    try:
        new_page = client.pages.create(
            parent={"database_id": email_db},
            properties=properties,
        )
        cache_clear()
        draft = extract_email_queue_props(new_page)
        result = {'ok': True, 'email': draft, 'template_type': template_type}
        if warnings:
            result['warnings'] = warnings
        return jsonify(result)
    except Exception as e:
        logger.error('Failed to create draft for game %s: %s', game_id, e)
        return jsonify({'error': str(e)}), 500


@bp.route('/api/games/<game_id>/order', methods=['POST'])
@login_required
def create_order(game_id):
    """Create an Order in Notion from a game — pre-fills date, contact, school."""
    import uuid

    client = get_client()

    # Load game
    try:
        page = client.pages.retrieve(page_id=game_id)
    except Exception as e:
        logger.error('Failed to retrieve game %s: %s', game_id, e)
        return jsonify({'error': 'Game not found'}), 404

    game = extract_game_props(page)
    data = request.get_json(silent=True) or {}

    # Resolve contact
    contact_id = None
    if game.get('contact_ids'):
        contact_id = game['contact_ids'][0]

    # Resolve school (away team = visiting team = the customer)
    school_id = None
    if game.get('away_team_ids'):
        school_id = game['away_team_ids'][0]

    # Resolve home school name for delivery location
    home_school = ''
    if game.get('home_team_ids'):
        home_school = resolve_school_name(game['home_team_ids'][0])

    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"

    properties = {
        "Order ID": {"title": [{"text": {"content": order_id}}]},
        "Game": {"relation": [{"id": game_id}]},
        "Payment Status": {"select": {"name": "Pending"}},
    }

    # Delivery date = game date
    if game.get('game_date'):
        properties["Delivery Date"] = {"date": {"start": game['game_date']}}
        properties["Order Date"] = {"date": {"start": game['game_date']}}

    # Delivery location from home school + venue
    location = home_school
    if game.get('venue'):
        location = f"{location} — {game['venue']}" if location else game['venue']
    if location:
        properties["Delivery Location"] = {"rich_text": [{"text": {"content": location}}]}

    if contact_id:
        properties["Contact"] = {"relation": [{"id": contact_id}]}
    if school_id:
        properties["School"] = {"relation": [{"id": school_id}]}

    # Optional fields from request body
    if data.get('notes'):
        properties["Notes"] = {"rich_text": [{"text": {"content": data['notes']}}]}
    if data.get('dietary_notes'):
        properties["Dietary Notes"] = {"rich_text": [{"text": {"content": data['dietary_notes']}}]}
    if data.get('total_amount') is not None:
        properties["Total Amount"] = {"number": data['total_amount']}

    orders_db = get_db_id('orders')

    try:
        new_page = client.pages.create(
            parent={"database_id": orders_db},
            properties=properties,
        )

        # Update game status to Booked
        try:
            client.pages.update(
                page_id=game_id,
                properties={"Outreach Status": {"select": {"name": "Booked"}}}
            )
        except Exception as e:
            logger.warning('Created order but failed to update game status: %s', e)

        cache_clear()
        return jsonify({
            'ok': True,
            'order_id': order_id,
            'notion_id': new_page['id'],
        })
    except Exception as e:
        logger.error('Failed to create order for game %s: %s', game_id, e)
        return jsonify({'error': str(e)}), 500


@bp.route('/api/games/refresh', methods=['POST'])
@login_required
def refresh_games():
    """Clear game caches."""
    cache_clear()
    return jsonify({'ok': True})
