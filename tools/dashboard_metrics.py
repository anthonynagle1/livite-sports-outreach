"""
Dashboard Metrics Engine for Livite Daily Analysis.

This module is a backwards-compatible re-export layer.
The actual implementation lives in the metrics/ package,
split into domain modules:

  metrics/utils.py        — constants, helpers, parsers
  metrics/revenue.py      — compute_revenue_metrics()
  metrics/orders.py       — compute_order_intelligence()
  metrics/baskets.py      — compute_basket_analysis()
  metrics/modifiers.py    — compute_modifier_analysis()
  metrics/kitchen.py      — compute_kitchen_metrics()
  metrics/labor.py        — compute_labor_metrics()
  metrics/payments.py     — compute_payment_metrics()
  metrics/customers.py    — compute_customer_metrics()
  metrics/insights.py     — detect_anomalies(), compute_analyst_insights()
  metrics/orchestrator.py — compute_all_metrics()

Usage:
    from dashboard_metrics import compute_all_metrics
    metrics = compute_all_metrics(data, date)
"""

# Re-export public API from metrics package
from metrics import (
    compute_all_metrics,
    detect_anomalies,
    compute_analyst_insights,
    parse_toast_datetime,
)

__all__ = [
    'compute_all_metrics',
    'detect_anomalies',
    'compute_analyst_insights',
    'parse_toast_datetime',
]
