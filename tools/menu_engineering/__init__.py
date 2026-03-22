"""Menu Engineering Matrix — classify menu items into Stars, Plowhorses, Puzzles, Dogs.

Reads Toast POS ItemSelectionDetails CSVs, aggregates per item, and plots
a scatter chart with quadrant lines at median popularity / median avg price.

Usage:
    from menu_engineering import compute_menu_engineering, build_menu_engineering_page

    data = compute_menu_engineering(days=30)
    html = build_menu_engineering_page(data, logo_b64="...")
"""

from .data import compute_menu_engineering  # noqa: F401
from .html import build_menu_engineering_page  # noqa: F401
