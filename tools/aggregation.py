"""
Shared aggregation utilities for Livite dashboards.

Provides reusable functions to aggregate cached daily metrics over
arbitrary date ranges. Used by scorecard, monthly report, menu
engineering, and cash flow modules.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from metrics_cache import get_cached_metrics, cache_metrics, is_today, batch_connection
from fetch_toast_data import get_daily_data
from dashboard_metrics import compute_all_metrics
from dashboard_aggregation import aggregate_metrics

logger = logging.getLogger(__name__)


def aggregate_date_range(start: datetime, end: datetime,
                         skip_weather: bool = True) -> dict | None:
    """Aggregate cached daily metrics over a date range.

    Args:
        start: First date (inclusive).
        end: Last date (inclusive).
        skip_weather: If True, skip weather API calls for uncached days.

    Returns:
        Aggregated metrics dict (same shape as compute_all_metrics output)
        with extra fields: is_range, range_days, daily_summary, daily_*_series.
        Returns None if no data is available for any day in the range.
    """
    start_str = start.strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")
    num_days = (end - start).days + 1

    daily_metrics = []
    current = start
    with batch_connection():
        while current <= end:
            ds = current.strftime("%Y%m%d")
            use_cache = not is_today(ds)

            metrics = get_cached_metrics(ds) if use_cache else None

            if metrics is None:
                try:
                    data = get_daily_data(current, quiet=True)
                    if "OrderDetails" in data:
                        metrics = compute_all_metrics(data, current,
                                                      skip_weather=skip_weather)
                        if use_cache and metrics:
                            cache_metrics(ds, metrics)
                except Exception as e:
                    logger.debug("Skip %s: %s", ds, e)

            if metrics:
                daily_metrics.append(metrics)
            current += timedelta(days=1)

    if not daily_metrics:
        return None

    agg = aggregate_metrics(daily_metrics, start_str, end_str, num_days)
    return agg


def get_weekly_range(target_date: datetime | None = None):
    """Return (monday, sunday) for the week containing target_date.

    If target_date is None, uses the most recent completed week
    (the week before the current week).
    """
    if target_date is None:
        today = datetime.now()
        # Most recent completed week = last Monday through Sunday
        days_since_monday = today.weekday()
        last_monday = today - timedelta(days=days_since_monday + 7)
    else:
        days_since_monday = target_date.weekday()
        last_monday = target_date - timedelta(days=days_since_monday)

    monday = last_monday.replace(hour=0, minute=0, second=0, microsecond=0)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def get_monthly_range(month_str: str | None = None):
    """Return (first_day, last_day) for a month.

    Args:
        month_str: 'YYYY-MM' format. If None, uses the most recent
                   completed month.
    """
    if month_str:
        year, month = map(int, month_str.split("-"))
    else:
        today = datetime.now()
        # Previous completed month
        first_of_this_month = today.replace(day=1)
        last_month_end = first_of_this_month - timedelta(days=1)
        year, month = last_month_end.year, last_month_end.month

    first_day = datetime(year, month, 1)
    # Last day of month
    if month == 12:
        next_month_first = datetime(year + 1, 1, 1)
    else:
        next_month_first = datetime(year, month + 1, 1)
    last_day = next_month_first - timedelta(days=1)

    return first_day, last_day


def collect_weekly_series(num_weeks: int = 12,
                          end_date: datetime | None = None) -> list[dict]:
    """Collect weekly aggregated totals for sparkline data.

    Returns a list of dicts (oldest first):
        [{week_label, revenue, labor, labor_pct, orders, avg_check, prime_cost_pct}, ...]
    """
    if end_date is None:
        end_date = datetime.now()

    series = []
    for i in range(num_weeks, 0, -1):
        # Week ending i weeks ago
        ref = end_date - timedelta(weeks=i)
        monday, sunday = get_weekly_range(ref)
        agg = aggregate_date_range(monday, sunday)
        if agg:
            revenue = agg.get("toast_total", 0)
            labor = agg.get("total_labor", 0)
            orders = agg.get("total_orders", 0)
            series.append({
                "week_label": monday.strftime("%b %d"),
                "revenue": revenue,
                "labor": labor,
                "labor_pct": round(labor / revenue * 100, 1) if revenue else 0,
                "orders": orders,
                "avg_check": round(revenue / orders, 2) if orders else 0,
                "prime_cost_pct": agg.get("prime_cost_pct", 0),
            })
        else:
            series.append({
                "week_label": monday.strftime("%b %d"),
                "revenue": 0, "labor": 0, "labor_pct": 0,
                "orders": 0, "avg_check": 0, "prime_cost_pct": 0,
            })
    return series
