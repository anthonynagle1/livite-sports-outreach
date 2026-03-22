"""Email Queue API — list, detail, approve, edit, draft, send."""

import logging

from flask import Blueprint, jsonify, request

from .auth import login_required
from ..lib.cache import cache_get, cache_set, cache_clear
from ..lib.notion import (
    get_client, get_db_id, paginated_query,
    extract_email_queue_props, extract_select,
)

logger = logging.getLogger(__name__)

bp = Blueprint('emails', __name__)

CACHE_TTL = 60  # 1 minute


@bp.route('/api/emails')
@login_required
def list_emails():
    """List email queue entries, filterable by status."""
    status_filter = request.args.get('status')
    cache_key = f'emails:{status_filter or "all"}'
    cached = cache_get(cache_key, CACHE_TTL)
    if cached is not None:
        return jsonify(cached)

    email_db = get_db_id('email_queue')

    kwargs = {
        'sorts': [{'timestamp': 'created_time', 'direction': 'descending'}],
    }

    if status_filter:
        kwargs['filter'] = {
            'property': 'Status',
            'select': {'equals': status_filter},
        }

    pages = paginated_query(email_db, **kwargs)
    emails = [extract_email_queue_props(p) for p in pages]

    result = {'emails': emails, 'count': len(emails)}
    cache_set(cache_key, result)
    return jsonify(result)


@bp.route('/api/emails/<email_id>')
@login_required
def get_email(email_id):
    """Get full email detail."""
    client = get_client()

    try:
        page = client.pages.retrieve(page_id=email_id)
    except Exception as e:
        logger.error('Failed to retrieve email %s: %s', email_id, e)
        return jsonify({'error': 'Email not found'}), 404

    email = extract_email_queue_props(page)
    return jsonify(email)


@bp.route('/api/emails/<email_id>', methods=['PUT'])
@login_required
def update_email(email_id):
    """Edit email draft (subject, body). Only works on Draft status."""
    client = get_client()
    data = request.get_json(silent=True) or {}

    # Verify it's a draft
    try:
        page = client.pages.retrieve(page_id=email_id)
        status = extract_select(page['properties'].get('Status', {}))
        if status not in ('Draft', 'Approved'):
            return jsonify({'error': f'Cannot edit email with status "{status}"'}), 400
    except Exception as e:
        return jsonify({'error': 'Email not found'}), 404

    properties = {}

    subject = data.get('subject')
    if subject is not None:
        properties['Subject'] = {'rich_text': [{'text': {'content': subject}}]}

    body = data.get('body')
    if body is not None:
        # Notion rich_text has 2000 char limit per block
        # Split body into chunks if needed
        chunks = []
        remaining = body
        while remaining:
            chunk = remaining[:2000]
            chunks.append({'text': {'content': chunk}})
            remaining = remaining[2000:]
        properties['Body'] = {'rich_text': chunks}

    if not properties:
        return jsonify({'error': 'No valid fields to update'}), 400

    try:
        client.pages.update(page_id=email_id, properties=properties)
        cache_clear()
        return jsonify({'ok': True})
    except Exception as e:
        logger.error('Failed to update email %s: %s', email_id, e)
        return jsonify({'error': str(e)}), 500


@bp.route('/api/emails/<email_id>/approve', methods=['POST'])
@login_required
def approve_email(email_id):
    """Move email from Draft to Approved."""
    client = get_client()

    try:
        page = client.pages.retrieve(page_id=email_id)
        status = extract_select(page['properties'].get('Status', {}))
        if status != 'Draft':
            return jsonify({'error': f'Cannot approve email with status "{status}"'}), 400
    except Exception as e:
        return jsonify({'error': 'Email not found'}), 404

    try:
        client.pages.update(
            page_id=email_id,
            properties={'Status': {'select': {'name': 'Approved'}}}
        )
        cache_clear()
        return jsonify({'ok': True, 'new_status': 'Approved'})
    except Exception as e:
        logger.error('Failed to approve email %s: %s', email_id, e)
        return jsonify({'error': str(e)}), 500


RESPONSE_TYPES = ['Interested', 'Not Interested', 'Booked', 'Question', 'Out of Office']


@bp.route('/api/emails/<email_id>/response-type', methods=['PUT'])
@login_required
def update_response_type(email_id):
    """Update the response type classification on a responded email."""
    client = get_client()
    data = request.get_json(silent=True) or {}

    response_type = data.get('response_type', '').strip()
    if response_type and response_type not in RESPONSE_TYPES:
        return jsonify({'error': f'Invalid response type. Must be one of: {RESPONSE_TYPES}'}), 400

    try:
        properties = {}
        if response_type:
            properties['Response Type'] = {'select': {'name': response_type}}
        else:
            # Clear the response type
            properties['Response Type'] = {'select': None}

        client.pages.update(page_id=email_id, properties=properties)

        # Also update the linked game's outreach status based on response type
        page = client.pages.retrieve(page_id=email_id)
        game_ids = [r['id'] for r in page['properties'].get('Game', {}).get('relation', [])]
        contact_ids = [r['id'] for r in page['properties'].get('Contact', {}).get('relation', [])]

        if game_ids and response_type:
            game_status = 'Responded'
            if response_type == 'Booked':
                game_status = 'Booked'
            elif response_type == 'Not Interested':
                game_status = 'Not Interested'
            elif response_type == 'Out of Office':
                game_status = 'Out of Office'
            try:
                client.pages.update(
                    page_id=game_ids[0],
                    properties={'Outreach Status': {'select': {'name': game_status}}}
                )
            except Exception as e:
                logger.warning('Failed to update game status: %s', e)

        # Update contact's last response type
        if contact_ids and response_type:
            try:
                contact_props = {}
                contact_props['Last Response Type'] = {'select': {'name': response_type}}
                if response_type == 'Booked':
                    contact_props['Relationship'] = {'select': {'name': 'Previous Customer'}}
                elif response_type in ('Interested', 'Question'):
                    contact_props['Relationship'] = {'select': {'name': 'Previously Responded'}}
                client.pages.update(page_id=contact_ids[0], properties=contact_props)
            except Exception as e:
                logger.warning('Failed to update contact response type: %s', e)

        cache_clear()
        return jsonify({'ok': True, 'response_type': response_type})
    except Exception as e:
        logger.error('Failed to update response type for %s: %s', email_id, e)
        return jsonify({'error': str(e)}), 500


@bp.route('/api/emails/approve-batch', methods=['POST'])
@login_required
def approve_batch():
    """Bulk approve multiple email drafts."""
    client = get_client()
    data = request.get_json(silent=True) or {}
    email_ids = data.get('email_ids', [])

    if not email_ids:
        return jsonify({'error': 'No email IDs provided'}), 400

    results = {'approved': 0, 'skipped': 0, 'errors': 0}

    for eid in email_ids:
        try:
            page = client.pages.retrieve(page_id=eid)
            status = extract_select(page['properties'].get('Status', {}))
            if status != 'Draft':
                results['skipped'] += 1
                continue
            client.pages.update(
                page_id=eid,
                properties={'Status': {'select': {'name': 'Approved'}}}
            )
            results['approved'] += 1
        except Exception as e:
            logger.error('Failed to approve email %s: %s', eid, e)
            results['errors'] += 1

    cache_clear()
    return jsonify(results)
