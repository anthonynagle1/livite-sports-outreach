"""Register all API blueprints."""

from .auth import bp as auth_bp
from .games import bp as games_bp
from .pipeline import bp as pipeline_bp
from .emails import bp as emails_bp
from .cron import bp as cron_bp

all_blueprints = [auth_bp, games_bp, pipeline_bp, emails_bp, cron_bp]
