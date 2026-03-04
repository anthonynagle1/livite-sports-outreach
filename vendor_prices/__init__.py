"""Vendor Prices Blueprint — price comparison across restaurant vendors."""

from flask import Blueprint

bp = Blueprint('vendor_prices', __name__, url_prefix='/prices')

from vendor_prices import routes  # noqa: E402, F401
