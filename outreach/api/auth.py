"""Authentication — reuses config.yaml from the main dashboard."""

import os
import logging
from functools import wraps
from hmac import compare_digest

import yaml
from flask import Blueprint, jsonify, request, session

logger = logging.getLogger(__name__)

bp = Blueprint('auth', __name__)

# Path to shared config.yaml (repo root)
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _load_users():
    # First try env vars (for Render/production)
    env_user = os.getenv('OUTREACH_USERNAME')
    env_pass = os.getenv('OUTREACH_PASSWORD')
    if env_user and env_pass:
        return {env_user: {'password': env_pass, 'name': env_user.title(), 'role': 'manager'}}

    # Fall back to config.yaml (local dev)
    config_path = os.path.join(_PROJECT_ROOT, 'config.yaml')
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get('users', {})
    except FileNotFoundError:
        logger.warning('config.yaml not found at %s', config_path)
        return {}


def login_required(f):
    """Decorator: require session auth or API key."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Session auth
        if session.get('authenticated'):
            return f(*args, **kwargs)
        # API key auth
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        if api_key and api_key == os.getenv('HUB_API_KEY', ''):
            return f(*args, **kwargs)
        return jsonify({'error': 'Unauthorized'}), 401
    return decorated


@bp.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip().lower()
    password = data.get('password', '')

    users = _load_users()
    user = users.get(username)

    if user and compare_digest(str(user.get('password', '')), password):
        session['authenticated'] = True
        session['role'] = user.get('role', 'manager')
        session['name'] = user.get('name', username)
        session.permanent = True
        return jsonify({
            'name': user.get('name', username),
            'role': user.get('role', 'manager'),
        })

    return jsonify({'error': 'Invalid credentials'}), 401


@bp.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})


@bp.route('/api/auth/me')
@login_required
def me():
    return jsonify({
        'name': session.get('name', ''),
        'role': session.get('role', ''),
        'authenticated': True,
    })
