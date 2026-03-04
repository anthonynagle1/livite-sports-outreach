"""
HTML Dashboard Template Engine for Livite Daily Analysis.

This module is a backwards-compatible re-export layer.
The actual implementation lives in the html/ package:

  html/components.py  — chart helpers, formatters, UI components
  html/sections.py    — per-section render functions (14 sections)
  html/builder.py     — build_dashboard() orchestrator

Usage:
    from dashboard_html import build_dashboard
    html = build_dashboard(metrics, comparisons, anomalies, ...)
"""

from htmlrender import build_dashboard

__all__ = ['build_dashboard']
