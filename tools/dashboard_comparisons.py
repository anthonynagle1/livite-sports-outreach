"""
Comparison period logic for daily dashboard.

Resolves WoW/MoM/SWLY dates, fetches comparison data,
and computes deltas between current and comparison periods.
"""

import os
import sys
from datetime import datetime, timedelta
from calendar import monthrange

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from fetch_toast_data import get_toast_csv_cached, list_available_dates
from calc_daily_profit import calc_revenue, calc_labor

EARLIEST_DATE = datetime(2024, 11, 7)


def resolve_comparison_dates(target_date: datetime) -> dict:
    """
    Resolve WoW, MoM, and SWLY comparison dates.
    Returns {'wow': datetime|None, 'mom': datetime|None, 'swly': datetime|None}.
    """
    # WoW: same weekday, 7 days ago
    wow_date = target_date - timedelta(days=7)
    wow = wow_date if wow_date >= EARLIEST_DATE else None

    # MoM: same date last month (capped to month-end)
    year = target_date.year
    month = target_date.month - 1
    day = target_date.day
    if month < 1:
        month = 12
        year -= 1
    max_day = monthrange(year, month)[1]
    day = min(day, max_day)
    mom_date = datetime(year, month, day)
    mom = mom_date if mom_date >= EARLIEST_DATE else None

    # SWLY: 364 days ago (preserves day-of-week)
    swly_date = target_date - timedelta(days=364)
    swly = swly_date if swly_date >= EARLIEST_DATE else None

    # YoY: 365 days ago (same calendar date, different weekday)
    yoy_date = target_date - timedelta(days=365)
    yoy = yoy_date if yoy_date >= EARLIEST_DATE else None

    return {'wow': wow, 'mom': mom, 'swly': swly, 'yoy': yoy}


def validate_date_available(date: datetime, available_dates: set) -> str:
    """
    Check if date exists in Azure. If not, try ±1 day (but only same weekday for WoW).
    Returns YYYYMMDD string or None.
    """
    date_str = date.strftime("%Y%m%d")
    if date_str in available_dates:
        return date_str

    # Try +1 and -1 day
    for offset in [1, -1, 2, -2]:
        alt = (date + timedelta(days=offset)).strftime("%Y%m%d")
        if alt in available_dates:
            return alt

    return None


def fetch_comparison_metrics(date_str: str) -> dict:
    """
    Fetch OrderDetails + TimeEntries + CheckDetails for a comparison date.
    Compute top-line metrics for delta comparison.
    Returns dict with key metrics or None on failure.
    """
    try:
        od = get_toast_csv_cached(date_str, "OrderDetails.csv")
    except Exception:
        return None

    # Revenue
    od_filtered = od[od['Voided'] != True].copy()
    revenue = od_filtered['Amount'].sum()
    orders = len(od_filtered)
    avg_check = revenue / orders if orders > 0 else 0

    # Guests
    guests = pd.to_numeric(od_filtered.get('# of Guests', pd.Series(dtype='float64')), errors='coerce').fillna(0).sum()

    # Channel breakdown
    rev_result = calc_revenue(od)
    channels = rev_result.get('channels', {})

    # Labor
    try:
        te = get_toast_csv_cached(date_str, "TimeEntries.csv")
        labor_result = calc_labor(te)
        labor_total = labor_result['total_labor']
        labor_hours = labor_result['total_hours']
    except Exception:
        labor_total = 0
        labor_hours = 0

    labor_pct = (labor_total / revenue * 100) if revenue > 0 else 0

    # Customers
    try:
        cd = get_toast_csv_cached(date_str, "CheckDetails.csv")
        unique_customers = cd['Customer Id'].dropna().nunique()
    except Exception:
        unique_customers = 0

    return {
        'date_str': date_str,
        'revenue': round(revenue, 2),
        'orders': orders,
        'avg_check': round(avg_check, 2),
        'guests': int(guests),
        'labor_total': round(labor_total, 2),
        'labor_hours': round(labor_hours, 2),
        'labor_pct': round(labor_pct, 1),
        'channels': channels,
        'unique_customers': unique_customers,
    }


def fetch_all_comparisons(comp_dates: dict, available_dates: set) -> dict:
    """
    For each comparison period, fetch data and compute metrics.
    Returns {'wow': {metrics}|None, 'mom': {metrics}|None, 'swly': {metrics}|None}.
    """
    result = {}
    for period, date in comp_dates.items():
        if date is None:
            result[period] = None
            continue

        date_str = validate_date_available(date, available_dates)
        if date_str is None:
            print(f"  {period.upper()}: No data available for {date.strftime('%Y%m%d')}")
            result[period] = None
            continue

        print(f"  {period.upper()}: Fetching {date_str}...")
        metrics = fetch_comparison_metrics(date_str)
        if metrics:
            result[period] = metrics
        else:
            result[period] = None

    return result


def compute_delta(current_val, comparison_val):
    """
    Compute delta between current and comparison values.
    Returns (diff, pct_change, direction) or (None, None, None) if unavailable.
    """
    if comparison_val is None or current_val is None:
        return (None, None, None)
    if comparison_val == 0:
        if current_val == 0:
            return (0, 0, 'flat')
        return (current_val, None, 'up')

    diff = current_val - comparison_val
    pct = (diff / abs(comparison_val)) * 100
    if diff > 0:
        direction = 'up'
    elif diff < 0:
        direction = 'down'
    else:
        direction = 'flat'

    return (round(diff, 2), round(pct, 1), direction)


def compute_all_deltas(current_metrics: dict, comparisons: dict) -> dict:
    """
    For each top-line metric, compute deltas vs WoW/MoM/SWLY.
    Returns nested dict: {metric_name: {wow: (diff, pct, dir), mom: ..., swly: ...}}
    """
    metrics_to_compare = ['revenue', 'orders', 'avg_check', 'guests',
                          'labor_total', 'labor_pct', 'unique_customers']

    deltas = {}
    for metric in metrics_to_compare:
        deltas[metric] = {}
        current_val = current_metrics.get(metric)
        for period in ['wow', 'mom', 'swly', 'yoy']:
            comp = comparisons.get(period)
            if comp is None:
                deltas[metric][period] = (None, None, None)
            else:
                comp_val = comp.get(metric)
                deltas[metric][period] = compute_delta(current_val, comp_val)

    return deltas
