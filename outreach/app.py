"""Livite Outreach — NCAA Sports Outreach CRM Webapp.

Flask app serving the React frontend + REST API backed by Notion.
"""

import logging
import os
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, send_from_directory

# Load env from repo root
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s %(message)s',
)

DIST_DIR = os.path.join(os.path.dirname(__file__), 'frontend', 'dist')

app = Flask(
    __name__,
    static_folder=None,  # Disable built-in static handler — serve_react handles everything
)

app.secret_key = os.getenv('OUTREACH_SECRET_KEY', os.getenv('SECRET_KEY', 'dev-secret-change-me'))
app.permanent_session_lifetime = timedelta(hours=8)

# Cookie settings
if os.getenv('RENDER'):
    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Register API blueprints
from outreach.api import all_blueprints  # noqa: E402

for blueprint in all_blueprints:
    app.register_blueprint(blueprint)


# ── Serve React frontend ─────────────────────────────────


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_react(path):
    """Serve React SPA — any non-API route gets index.html."""
    if path and path.startswith('api/'):
        return {'error': 'Not found'}, 404

    if path and os.path.isfile(os.path.join(DIST_DIR, path)):
        return send_from_directory(DIST_DIR, path)

    # Fallback to index.html for SPA routing
    if os.path.isfile(os.path.join(DIST_DIR, 'index.html')):
        return send_from_directory(DIST_DIR, 'index.html')

    # Dev mode: no build yet, show helpful message
    return (
        '<h1>Livite Outreach</h1>'
        '<p>Frontend not built yet. Run <code>cd frontend && npm run build</code></p>'
        '<p>Or use the Vite dev server: <code>cd frontend && npm run dev</code></p>'
        '<p><a href="/api/auth/me">Check API →</a></p>'
    ), 200


# ── Health check ─────────────────────────────────────────


@app.route('/api/health')
def health():
    return {'status': 'ok', 'app': 'livite-outreach'}


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5002))
    app.run(host='0.0.0.0', port=port, debug=True)
