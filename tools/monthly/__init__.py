"""Monthly P&L Report module.

Aggregates a full calendar month of Toast POS + catering data into a
self-contained HTML report with KPI grid, channel charts, trend lines,
top/worst days, and daily breakdown table.

Usage:
    from monthly import compute_monthly_report, build_monthly_page

    metrics = compute_monthly_report("2026-02")
    html = build_monthly_page(metrics, logo_b64="...")
"""

from .data import compute_monthly_report
from .html import build_monthly_page

__all__ = ["compute_monthly_report", "build_monthly_page"]
