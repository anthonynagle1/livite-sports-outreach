"""
Livite Dashboard HTML Package.

Split from the monolithic dashboard_html.py into:
  htmlrender/components.py  — chart helpers, formatters, UI components
  htmlrender/sections.py    — per-section render functions (14 sections)
  htmlrender/builder.py     — build_dashboard() orchestrator
"""

from .builder import build_dashboard

__all__ = ['build_dashboard']
