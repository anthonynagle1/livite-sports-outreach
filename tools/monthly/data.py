"""Compute monthly P&L report metrics from aggregated daily data.

Uses the shared aggregation layer to pull Toast POS metrics for a full
calendar month, then enriches with catering data, channel breakdowns,
top/worst day rankings, and a trailing 6-month trend series.
"""

from __future__ import annotations

import logging
import sys
import os
from datetime import datetime, timedelta

# Ensure tools/ is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aggregation import aggregate_date_range, get_monthly_range

logger = logging.getLogger(__name__)

# Third-party channel identifiers (case-insensitive substrings)
_3P_KEYWORDS = ("uber", "doordash", "grubhub")


def _safe_div(a: float, b: float) -> float:
    """Safe division returning 0.0 on zero denominator."""
    if not b:
        return 0.0
    return a / b


def _safe_pct(part: float, whole: float) -> float:
    """Return part/whole * 100 safely."""
    if not whole:
        return 0.0
    return round(part / whole * 100, 1)


def _prev_month(month_str: str) -> str:
    """Return the month string for the month before month_str (YYYY-MM)."""
    year, month = map(int, month_str.split("-"))
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def _same_month_ly(month_str: str) -> str:
    """Return the same month one year prior."""
    year, month = map(int, month_str.split("-"))
    return f"{year - 1:04d}-{month:02d}"


def _month_label(month_str: str) -> str:
    """Convert 'YYYY-MM' to human-readable 'February 2026'."""
    try:
        dt = datetime.strptime(month_str, "%Y-%m")
        return dt.strftime("%B %Y")
    except ValueError:
        return month_str


def _extract_period(agg: dict | None, month_str: str) -> dict | None:
    """Extract standardised period metrics from an aggregate dict.

    Returns None if agg is None or empty.
    """
    if not agg:
        return None

    revenue = agg.get("toast_total", 0)
    labor = agg.get("total_labor", 0)
    orders = agg.get("total_orders", 0)
    days = agg.get("range_days", 1)

    # Channel breakdown
    rev_section = agg.get("revenue") or {}
    channels = rev_section.get("channels", {})
    channel_breakdown = {}
    if channels:
        for ch_name, ch_data in channels.items():
            channel_breakdown[ch_name] = ch_data.get("revenue", 0)
    else:
        # Fallback to walkin_3p_online grouping
        w3o = rev_section.get("walkin_3p_online", {})
        for key, val in w3o.items():
            channel_breakdown[key] = val.get("revenue", 0)

    # 3P revenue (channels matching uber/doordash/grubhub)
    third_party_revenue = 0
    for ch_name, ch_rev in channel_breakdown.items():
        if any(kw in ch_name.lower() for kw in _3P_KEYWORDS):
            third_party_revenue += ch_rev

    # 3P fees
    third_party_fees = agg.get("tds_fees", 0)
    third_party_pct = _safe_pct(third_party_fees, third_party_revenue)

    # Customer count
    customers = agg.get("customers") or {}
    customer_count = (
        customers.get("unique_customers", 0) or
        customers.get("unique_card_count", 0) or 0
    )

    # Catering revenue from aggregated data
    catering_revenue = 0.0
    try:
        from catering import get_catering_dashboard_data
        first_day, last_day = get_monthly_range(month_str)
        cdata = get_catering_dashboard_data(
            start_month=month_str, end_month=month_str
        )
        if cdata:
            monthly_totals = cdata.get("monthly_totals") or {}
            catering_revenue = monthly_totals.get(month_str, 0)
    except Exception as exc:
        logger.debug("Catering data unavailable for %s: %s", month_str, exc)

    return {
        "revenue": round(revenue, 2),
        "labor": round(labor, 2),
        "labor_pct": _safe_pct(labor, revenue),
        "prime_cost_pct": agg.get("prime_cost_pct", 0),
        "orders": orders,
        "avg_check": round(_safe_div(revenue, orders), 2),
        "avg_daily_revenue": round(_safe_div(revenue, days), 2),
        "customer_count": customer_count,
        "catering_revenue": round(catering_revenue, 2),
        "catering_growth_pct": 0.0,  # Filled in by caller
        "third_party_fees": round(third_party_fees, 2),
        "third_party_pct": third_party_pct,
        "third_party_revenue": round(third_party_revenue, 2),
        "channel_breakdown": channel_breakdown,
    }


def _compute_deltas(current: dict, prior: dict | None, ly: dict | None) -> dict:
    """Compute month-over-month and year-over-year deltas.

    Returns:
        {metric: {'mom': (diff, pct), 'yoy': (diff, pct)}}

    diff = current - prior
    pct = diff / abs(prior) * 100
    """
    metrics = [
        "revenue", "labor", "labor_pct", "prime_cost_pct", "orders",
        "avg_check", "avg_daily_revenue", "customer_count",
        "catering_revenue", "third_party_fees", "third_party_pct",
        "third_party_revenue",
    ]

    deltas = {}
    for key in metrics:
        cur_val = current.get(key, 0) or 0
        entry = {}

        # MoM
        if prior is not None:
            pr_val = prior.get(key, 0) or 0
            diff = cur_val - pr_val
            pct = round(diff / abs(pr_val) * 100, 1) if pr_val else None
            entry["mom"] = (round(diff, 2), pct)
        else:
            entry["mom"] = (None, None)

        # YoY
        if ly is not None:
            ly_val = ly.get(key, 0) or 0
            diff = cur_val - ly_val
            pct = round(diff / abs(ly_val) * 100, 1) if ly_val else None
            entry["yoy"] = (round(diff, 2), pct)
        else:
            entry["yoy"] = (None, None)

        deltas[key] = entry

    return deltas


def compute_monthly_report(month_str: str | None = None) -> dict:
    """Compute full monthly P&L report metrics.

    Args:
        month_str: Target month in 'YYYY-MM' format.
                   None = most recent completed month.

    Returns:
        Dict with keys: month_label, month_str, current, prior_month,
        same_month_ly, deltas, top_days, worst_days, monthly_trend,
        daily_breakdown.
    """
    # Resolve month
    first_day, last_day = get_monthly_range(month_str)
    resolved_month = first_day.strftime("%Y-%m")

    logger.info("Computing monthly report for %s (%s to %s)",
                resolved_month, first_day.strftime("%Y-%m-%d"),
                last_day.strftime("%Y-%m-%d"))

    # ── Current month ──
    agg = aggregate_date_range(first_day, last_day)
    current = _extract_period(agg, resolved_month)

    if current is None:
        logger.warning("No data available for %s", resolved_month)
        return {
            "month_label": _month_label(resolved_month),
            "month_str": resolved_month,
            "current": None,
            "prior_month": None,
            "same_month_ly": None,
            "deltas": {},
            "top_days": [],
            "worst_days": [],
            "monthly_trend": [],
            "daily_breakdown": [],
        }

    # ── Prior month ──
    prior_ms = _prev_month(resolved_month)
    p_first, p_last = get_monthly_range(prior_ms)
    prior_agg = aggregate_date_range(p_first, p_last)
    prior_month = _extract_period(prior_agg, prior_ms)

    # ── Same month last year ──
    ly_ms = _same_month_ly(resolved_month)
    ly_first, ly_last = get_monthly_range(ly_ms)
    ly_agg = aggregate_date_range(ly_first, ly_last)
    same_month_ly = _extract_period(ly_agg, ly_ms)

    # ── Catering growth ──
    if prior_month and prior_month.get("catering_revenue"):
        current["catering_growth_pct"] = round(
            (current["catering_revenue"] - prior_month["catering_revenue"])
            / abs(prior_month["catering_revenue"]) * 100, 1
        ) if prior_month["catering_revenue"] else 0.0

    # ── Deltas ──
    deltas = _compute_deltas(current, prior_month, same_month_ly)

    # ── Top / worst days ──
    daily_summary = agg.get("daily_summary", []) if agg else []
    valid_days = [d for d in daily_summary if d.get("revenue")]

    sorted_by_rev = sorted(valid_days, key=lambda d: d.get("revenue", 0), reverse=True)
    top_days = [
        {
            "date_str": d.get("date_str", ""),
            "day": d.get("day_of_week", ""),
            "revenue": d.get("revenue", 0),
        }
        for d in sorted_by_rev[:5]
    ]
    worst_days = [
        {
            "date_str": d.get("date_str", ""),
            "day": d.get("day_of_week", ""),
            "revenue": d.get("revenue", 0),
        }
        for d in sorted_by_rev[-5:][::-1]  # Lowest revenue, ascending
    ]

    # ── Monthly trend (last 6 months) ──
    monthly_trend = []
    for i in range(6, 0, -1):
        ref = first_day - timedelta(days=i * 30)  # Approximate
        t_ms = ref.strftime("%Y-%m")
        t_first, t_last = get_monthly_range(t_ms)
        t_agg = aggregate_date_range(t_first, t_last)
        if t_agg:
            t_rev = t_agg.get("toast_total", 0)
            t_labor = t_agg.get("total_labor", 0)
            t_orders = t_agg.get("total_orders", 0)
            monthly_trend.append({
                "month_label": _month_label(t_ms),
                "month_str": t_ms,
                "revenue": round(t_rev, 2),
                "labor_pct": _safe_pct(t_labor, t_rev),
                "orders": t_orders,
            })
        else:
            monthly_trend.append({
                "month_label": _month_label(t_ms),
                "month_str": t_ms,
                "revenue": 0,
                "labor_pct": 0,
                "orders": 0,
            })

    # Add current month to trend
    monthly_trend.append({
        "month_label": _month_label(resolved_month),
        "month_str": resolved_month,
        "revenue": current["revenue"],
        "labor_pct": current["labor_pct"],
        "orders": current["orders"],
    })

    # ── Daily breakdown ──
    daily_breakdown = []
    for d in daily_summary:
        ds = d.get("date_str", "")
        revenue = d.get("revenue", 0)
        orders = d.get("orders", 0)
        labor_pct = d.get("labor_pct", 0)
        daily_breakdown.append({
            "date_str": ds,
            "day": d.get("day_of_week", ""),
            "revenue": revenue,
            "orders": orders,
            "avg_check": round(_safe_div(revenue, orders), 2),
            "labor_pct": labor_pct or 0,
        })

    return {
        "month_label": _month_label(resolved_month),
        "month_str": resolved_month,
        "current": current,
        "prior_month": prior_month,
        "same_month_ly": same_month_ly,
        "deltas": deltas,
        "top_days": top_days,
        "worst_days": worst_days,
        "monthly_trend": monthly_trend,
        "daily_breakdown": daily_breakdown,
    }
