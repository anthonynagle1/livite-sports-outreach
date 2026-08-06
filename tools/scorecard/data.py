"""
Weekly Scorecard data layer.

Computes a single-week metrics snapshot with comparisons to prior week
and same-week-last-year, plus 12-week sparkline series.

Usage:
    from scorecard import compute_weekly_scorecard
    sc = compute_weekly_scorecard()          # current week
    sc = compute_weekly_scorecard(date(2026, 3, 10))  # specific week
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from metrics_cache import get_cached_metrics, batch_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _monday_of(d: date) -> date:
    """Return the Monday of the week containing *d*."""
    return d - timedelta(days=d.weekday())


def _sunday_of(d: date) -> date:
    """Return the Sunday of the week containing *d*."""
    return d + timedelta(days=6 - d.weekday())


def _week_label(mon: date, sun: date) -> str:
    """Format a human-readable week label like 'Mar 10-16, 2026'."""
    if mon.month == sun.month:
        return f"{mon.strftime('%b')} {mon.day}-{sun.day}, {mon.year}"
    return f"{mon.strftime('%b')} {mon.day} - {sun.strftime('%b')} {sun.day}, {mon.year}"


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(round(_safe_float(val, default)))
    except (TypeError, ValueError):
        return default


def _safe_div(a: float, b: float) -> float:
    return round(a / b, 2) if b else 0.0


def _pct_change(current: float, prior: float) -> float | None:
    """Return percent change, or None if prior is zero."""
    if prior == 0:
        return None
    return round((current - prior) / abs(prior) * 100, 1)


# ---------------------------------------------------------------------------
# Aggregate a date range from the metrics cache
# ---------------------------------------------------------------------------

def _aggregate_date_range(start: date, end: date) -> dict | None:
    """Pull cached daily metrics for [start, end] and aggregate key KPIs.

    Returns a flat dict with weekly totals/averages, or None if no data.
    Uses dashboard_aggregation.aggregate_metrics when possible for
    consistency with the main dashboard, but falls back to lightweight
    manual aggregation from cached daily metrics.
    """
    daily_metrics = []
    current = start
    while current <= end:
        ds = current.strftime("%Y%m%d")
        m = get_cached_metrics(ds)
        if m:
            daily_metrics.append(m)
        current += timedelta(days=1)

    if not daily_metrics:
        return None

    # Try using the full aggregation engine first
    try:
        from dashboard_aggregation import aggregate_metrics
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")
        num_days = (end - start).days + 1
        agg = aggregate_metrics(daily_metrics, start_str, end_str, num_days)
        return _extract_kpis(agg, daily_metrics)
    except Exception:
        logger.debug("Full aggregation unavailable, falling back to lightweight agg")

    # Lightweight fallback: sum/derive from daily cache entries
    return _lightweight_aggregate(daily_metrics)


def _extract_kpis(agg: dict, daily_metrics: list) -> dict:
    """Extract scorecard-relevant KPIs from a full aggregate_metrics result."""
    rev = agg.get("revenue") or {}
    lab = agg.get("labor") or {}
    cust = agg.get("customers") or {}
    pay = agg.get("payments") or {}

    revenue = _safe_float(rev.get("toast_total"))
    labor = _safe_float(lab.get("total_labor"))
    total_orders = _safe_int(rev.get("total_orders"))
    avg_check = _safe_float(rev.get("avg_check"))

    labor_pct = _safe_div(labor, revenue) * 100 if revenue else 0.0

    food_cost = _safe_float(agg.get("food_cost"))
    prime_cost_pct = _safe_div(food_cost + labor, revenue) * 100 if revenue else 0.0

    # Customer count
    customer_count = _safe_int(
        cust.get("unique_customers", cust.get("unique_card_count", 0))
    )

    # 3P fees
    third_party_fees = _safe_float(agg.get("tds_fees", 0))

    # 3P revenue: sum channels containing Uber/DoorDash/Grubhub
    channels = rev.get("channels", {})
    third_party_revenue = 0.0
    _3p_names = {"uber", "doordash", "grubhub", "uber eats", "caviar"}
    for ch_name, ch_data in channels.items():
        if any(kw in ch_name.lower() for kw in _3p_names):
            if isinstance(ch_data, dict):
                third_party_revenue += _safe_float(ch_data.get("revenue", 0))
            else:
                third_party_revenue += _safe_float(ch_data)

    # Catering revenue
    catering_revenue = _get_catering_revenue(agg, daily_metrics)

    # Daily breakdown
    daily_breakdown = []
    for ds in agg.get("daily_summary", []):
        daily_breakdown.append({
            "date_str": ds.get("date_str", ""),
            "day": (ds.get("day_of_week", "") or "")[:3],
            "revenue": _safe_float(ds.get("revenue")),
            "orders": _safe_int(ds.get("orders")),
            "avg_check": _safe_float(ds.get("avg_check")),
            "labor_pct": _safe_float(ds.get("labor_pct")),
        })

    # Anomalies
    anomalies = _detect_scorecard_anomalies(
        revenue=revenue, labor_pct=labor_pct, prime_cost_pct=prime_cost_pct,
        avg_check=avg_check, total_orders=total_orders,
    )

    return {
        "revenue": round(revenue, 2),
        "labor": round(labor, 2),
        "labor_pct": round(labor_pct, 1),
        "prime_cost_pct": round(prime_cost_pct, 1),
        "orders": total_orders,
        "avg_check": round(avg_check, 2),
        "customer_count": customer_count,
        "catering_revenue": round(catering_revenue, 2),
        "third_party_fees": round(third_party_fees, 2),
        "third_party_revenue": round(third_party_revenue, 2),
        "daily_breakdown": daily_breakdown,
        "anomalies": anomalies,
    }


def _lightweight_aggregate(daily_metrics: list) -> dict:
    """Lightweight KPI aggregation from cached daily dicts."""
    revenue = 0.0
    labor = 0.0
    orders = 0
    food_cost = 0.0
    third_party_fees = 0.0
    third_party_revenue = 0.0
    catering_revenue = 0.0
    customer_ids: set = set()
    customer_count_sum = 0
    daily_breakdown = []

    _3p_names = {"uber", "doordash", "grubhub", "uber eats", "caviar"}

    for m in daily_metrics:
        rev = m.get("revenue") or {}
        lab = m.get("labor") or {}
        cust = m.get("customers") or {}
        pay = m.get("payments") or {}

        day_rev = _safe_float(rev.get("toast_total"))
        day_labor = _safe_float(lab.get("total_labor"))
        day_orders = _safe_int(rev.get("total_orders"))
        day_check = _safe_float(rev.get("avg_check"))
        day_labor_pct = _safe_float(lab.get("labor_pct"))

        revenue += day_rev
        labor += day_labor
        orders += day_orders
        food_cost += _safe_float(m.get("food_cost"))

        # 3P fees
        third_party_fees += _safe_float(pay.get("tds_fees", 0))

        # 3P revenue from channels
        channels = rev.get("channels", {})
        for ch_name, ch_data in channels.items():
            if any(kw in ch_name.lower() for kw in _3p_names):
                if isinstance(ch_data, dict):
                    third_party_revenue += _safe_float(ch_data.get("revenue", 0))
                else:
                    third_party_revenue += _safe_float(ch_data)

        # Catering
        cat_orders = m.get("orders", {}).get("catering", {})
        if isinstance(cat_orders, dict):
            catering_revenue += _safe_float(cat_orders.get("total", cat_orders.get("revenue", 0)))

        # Customers
        uc = cust.get("unique_customers", cust.get("unique_card_count", 0))
        customer_count_sum += _safe_int(uc)

        daily_breakdown.append({
            "date_str": m.get("date_str", ""),
            "day": (m.get("day_of_week", "") or "")[:3],
            "revenue": round(day_rev, 2),
            "orders": day_orders,
            "avg_check": round(day_check, 2),
            "labor_pct": round(day_labor_pct, 1),
        })

    avg_check = _safe_div(revenue, orders)
    labor_pct = _safe_div(labor, revenue) * 100 if revenue else 0.0
    prime_cost_pct = _safe_div(food_cost + labor, revenue) * 100 if revenue else 0.0

    anomalies = _detect_scorecard_anomalies(
        revenue=revenue, labor_pct=labor_pct, prime_cost_pct=prime_cost_pct,
        avg_check=avg_check, total_orders=orders,
    )

    return {
        "revenue": round(revenue, 2),
        "labor": round(labor, 2),
        "labor_pct": round(labor_pct, 1),
        "prime_cost_pct": round(prime_cost_pct, 1),
        "orders": orders,
        "avg_check": round(avg_check, 2),
        "customer_count": customer_count_sum,
        "catering_revenue": round(catering_revenue, 2),
        "third_party_fees": round(third_party_fees, 2),
        "third_party_revenue": round(third_party_revenue, 2),
        "daily_breakdown": daily_breakdown,
        "anomalies": anomalies,
    }


def _get_catering_revenue(agg: dict, daily_metrics: list) -> float:
    """Try to pull catering revenue from the catering module, then fallback."""
    # First: try dedicated catering module
    try:
        from catering import get_catering_dashboard_data
        cat_data = get_catering_dashboard_data()
        if cat_data and cat_data.get("total_revenue"):
            # This gives all-time; we only want this week.
            # Fall through to per-day extraction.
            pass
    except Exception:
        pass

    # Per-day extraction from aggregated orders section
    cat_orders = agg.get("orders", {}).get("catering", {})
    if isinstance(cat_orders, dict):
        val = _safe_float(cat_orders.get("total", cat_orders.get("revenue", 0)))
        if val > 0:
            return val

    # Fallback: sum from daily metrics
    total = 0.0
    for m in daily_metrics:
        co = (m.get("orders") or {}).get("catering", {})
        if isinstance(co, dict):
            total += _safe_float(co.get("total", co.get("revenue", 0)))
    return total


# ---------------------------------------------------------------------------
# Scorecard-level anomaly detection (lightweight)
# ---------------------------------------------------------------------------

def _detect_scorecard_anomalies(**kw) -> list[dict]:
    """Return top 3 anomalies based on simple threshold rules."""
    alerts: list[dict] = []

    labor_pct = kw.get("labor_pct", 0)
    prime_cost_pct = kw.get("prime_cost_pct", 0)
    avg_check = kw.get("avg_check", 0)
    total_orders = kw.get("total_orders", kw.get("orders", 0))

    if labor_pct > 35:
        alerts.append({
            "text": f"Labor at {labor_pct:.1f}% of revenue — above 35% threshold",
            "severity": "red",
        })
    elif labor_pct > 30:
        alerts.append({
            "text": f"Labor at {labor_pct:.1f}% of revenue — approaching 35% ceiling",
            "severity": "amber",
        })

    if prime_cost_pct > 65:
        alerts.append({
            "text": f"Prime cost at {prime_cost_pct:.1f}% — above 65% target",
            "severity": "red",
        })
    elif prime_cost_pct > 60:
        alerts.append({
            "text": f"Prime cost at {prime_cost_pct:.1f}% — watch for drift above 65%",
            "severity": "amber",
        })

    if avg_check > 0 and avg_check < 12:
        alerts.append({
            "text": f"Average check ${avg_check:.2f} is below $12 — review pricing/upsells",
            "severity": "amber",
        })

    if total_orders < 200:
        alerts.append({
            "text": f"Only {total_orders} orders this week — below 200 weekly minimum",
            "severity": "amber",
        })

    # Sort: red first, then amber
    severity_order = {"red": 0, "amber": 1, "green": 2}
    alerts.sort(key=lambda a: severity_order.get(a["severity"], 9))
    return alerts[:3]


# ---------------------------------------------------------------------------
# Sparkline series (12 weeks)
# ---------------------------------------------------------------------------

def _collect_weekly_series(num_weeks: int = 12, anchor: date | None = None) -> dict:
    """Collect weekly totals for the past *num_weeks* weeks.

    Returns {metric_name: [oldest_week ... newest_week]} with num_weeks values.
    """
    if anchor is None:
        anchor = date.today()

    # Start from the most recent completed Monday
    latest_monday = _monday_of(anchor)
    # If today is before Sunday, the current week is incomplete —
    # anchor on last week instead
    if anchor < _sunday_of(latest_monday):
        latest_monday = latest_monday - timedelta(weeks=1)

    labels: list[str] = []
    revenue_vals: list[float] = []
    labor_pct_vals: list[float] = []
    orders_vals: list[float] = []
    avg_check_vals: list[float] = []

    for i in range(num_weeks - 1, -1, -1):  # oldest first
        mon = latest_monday - timedelta(weeks=i)
        sun = mon + timedelta(days=6)
        labels.append(f"{mon.strftime('%m/%d')}")

        week_rev = 0.0
        week_labor = 0.0
        week_orders = 0
        day_count = 0

        current = mon
        while current <= sun:
            ds = current.strftime("%Y%m%d")
            m = get_cached_metrics(ds)
            if m:
                rev = m.get("revenue") or {}
                lab = m.get("labor") or {}
                week_rev += _safe_float(rev.get("toast_total"))
                week_labor += _safe_float(lab.get("total_labor"))
                week_orders += _safe_int(rev.get("total_orders"))
                day_count += 1
            current += timedelta(days=1)

        revenue_vals.append(round(week_rev, 2))
        labor_pct_vals.append(
            round(_safe_div(week_labor, week_rev) * 100, 1) if week_rev else 0.0
        )
        orders_vals.append(week_orders)
        avg_check_vals.append(
            round(_safe_div(week_rev, week_orders), 2) if week_orders else 0.0
        )

    return {
        "labels": labels,
        "revenue": revenue_vals,
        "labor_pct": labor_pct_vals,
        "orders": [float(o) for o in orders_vals],
        "avg_check": avg_check_vals,
    }


# ---------------------------------------------------------------------------
# Delta computation
# ---------------------------------------------------------------------------

def _compute_deltas(current: dict, prior: dict | None, swly: dict | None) -> dict:
    """Compute WoW and YoY deltas for each metric.

    Returns {metric_name: {'wow': (diff, pct), 'yoy': (diff, pct)}}.
    """
    metrics_keys = [
        "revenue", "labor", "labor_pct", "prime_cost_pct", "orders",
        "avg_check", "customer_count", "catering_revenue",
        "third_party_fees", "third_party_revenue",
    ]
    deltas: dict = {}
    for key in metrics_keys:
        cur_val = _safe_float(current.get(key))
        wow_diff, wow_pct = None, None
        yoy_diff, yoy_pct = None, None

        if prior is not None:
            pri_val = _safe_float(prior.get(key))
            wow_diff = round(cur_val - pri_val, 2)
            wow_pct = _pct_change(cur_val, pri_val)

        if swly is not None:
            ly_val = _safe_float(swly.get(key))
            yoy_diff = round(cur_val - ly_val, 2)
            yoy_pct = _pct_change(cur_val, ly_val)

        deltas[key] = {
            "wow": (wow_diff, wow_pct),
            "yoy": (yoy_diff, yoy_pct),
        }
    return deltas


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_weekly_scorecard(target_date: date | None = None) -> dict:
    """Compute the full weekly scorecard data structure.

    Args:
        target_date: Any date within the target week. Defaults to today.

    Returns a dict ready to pass to build_scorecard_page().
    """
    if target_date is None:
        target_date = date.today()
    if isinstance(target_date, datetime):
        target_date = target_date.date()

    monday = _monday_of(target_date)
    sunday = _sunday_of(target_date)

    prior_monday = monday - timedelta(weeks=1)
    prior_sunday = sunday - timedelta(weeks=1)

    swly_monday = monday - timedelta(days=364)
    swly_sunday = sunday - timedelta(days=364)

    # Fetch all three periods inside one batch connection
    with batch_connection():
        current = _aggregate_date_range(monday, sunday)
        prior_week = _aggregate_date_range(prior_monday, prior_sunday)
        same_week_ly = _aggregate_date_range(swly_monday, swly_sunday)

        # Sparklines
        sparklines = _collect_weekly_series(12, anchor=target_date)

    # Handle no data
    if current is None:
        current = {
            "revenue": 0, "labor": 0, "labor_pct": 0, "prime_cost_pct": 0,
            "orders": 0, "avg_check": 0, "customer_count": 0,
            "catering_revenue": 0, "third_party_fees": 0,
            "third_party_revenue": 0, "daily_breakdown": [], "anomalies": [],
        }

    deltas = _compute_deltas(current, prior_week, same_week_ly)

    # Pull anomalies from current period aggregation
    top_anomalies = current.get("anomalies", [])[:3]
    # Also try full anomaly detection from the metrics module
    if not top_anomalies:
        top_anomalies = _detect_scorecard_anomalies(
            revenue=_safe_float(current.get("revenue")),
            labor_pct=_safe_float(current.get("labor_pct")),
            prime_cost_pct=_safe_float(current.get("prime_cost_pct")),
            avg_check=_safe_float(current.get("avg_check")),
            total_orders=_safe_int(current.get("orders")),
        )

    return {
        "week_label": _week_label(monday, sunday),
        "week_start": monday,
        "week_end": sunday,
        "current": {
            "revenue": current.get("revenue", 0),
            "labor": current.get("labor", 0),
            "labor_pct": current.get("labor_pct", 0),
            "prime_cost_pct": current.get("prime_cost_pct", 0),
            "orders": current.get("orders", 0),
            "avg_check": current.get("avg_check", 0),
            "customer_count": current.get("customer_count", 0),
            "catering_revenue": current.get("catering_revenue", 0),
            "third_party_fees": current.get("third_party_fees", 0),
            "third_party_revenue": current.get("third_party_revenue", 0),
        },
        "prior_week": {
            "revenue": prior_week.get("revenue", 0),
            "labor": prior_week.get("labor", 0),
            "labor_pct": prior_week.get("labor_pct", 0),
            "prime_cost_pct": prior_week.get("prime_cost_pct", 0),
            "orders": prior_week.get("orders", 0),
            "avg_check": prior_week.get("avg_check", 0),
            "customer_count": prior_week.get("customer_count", 0),
            "catering_revenue": prior_week.get("catering_revenue", 0),
            "third_party_fees": prior_week.get("third_party_fees", 0),
            "third_party_revenue": prior_week.get("third_party_revenue", 0),
        } if prior_week else None,
        "same_week_ly": {
            "revenue": same_week_ly.get("revenue", 0),
            "labor": same_week_ly.get("labor", 0),
            "labor_pct": same_week_ly.get("labor_pct", 0),
            "prime_cost_pct": same_week_ly.get("prime_cost_pct", 0),
            "orders": same_week_ly.get("orders", 0),
            "avg_check": same_week_ly.get("avg_check", 0),
            "customer_count": same_week_ly.get("customer_count", 0),
            "catering_revenue": same_week_ly.get("catering_revenue", 0),
            "third_party_fees": same_week_ly.get("third_party_fees", 0),
            "third_party_revenue": same_week_ly.get("third_party_revenue", 0),
        } if same_week_ly else None,
        "deltas": deltas,
        "top_anomalies": top_anomalies,
        "sparklines": sparklines,
        "daily_breakdown": current.get("daily_breakdown", []),
    }
