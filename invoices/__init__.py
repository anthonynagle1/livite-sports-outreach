"""Invoice Tracking Blueprint — vendor invoice management and purchase tracking."""

from flask import Blueprint

bp = Blueprint('invoices', __name__, url_prefix='/invoices')

from invoices import routes  # noqa: E402, F401
