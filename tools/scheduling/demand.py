"""Labor demand forecast — convert revenue projections to staffing needs.

Uses historical staffing curves (from TimeEntries) scaled by the revenue
forecast to produce per-half-hour staffing demand.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# SPLH target: $55/labor-hour is the current staffing efficiency goal.
SPLH_TARGET = 55.0

# Minimum staff at all times (during open hours 7a-9p)
MIN_FOH = 2
MIN_BOH = 2

# Dayparts for aggregation
_DAYPARTS = [
    (7, 11, "Morning"),
    (11, 14, "Lunch"),
    (14, 17, "Afternoon"),
    (17, 21, "Dinner"),
]


def _get_historical_staffing(target_date: datetime, weeks_back: int = 4):
    """Pull same-DOW historical staffing curves from TimeEntries.

    Returns (staffing_curve, avg_revenue) where:
    - staffing_curve: list of {time, foh_count, boh_count, total_count}
      averaged over the historical same-DOW samples.
    - avg_revenue: average daily revenue for those historical days.
    """
    import sys
    sys.path.insert(0, os.path.join(_BASE_DIR, "tools"))

    from fetch_toast_data import get_daily_data
    from metrics.utils import parse_toast_datetime, _safe_numeric

    import pandas as pd

    target_dow = target_date.weekday()

    # Collect same-DOW data from past weeks
    slot_totals = defaultdict(lambda: {"foh": [], "boh": [], "total": []})
    revenues = []

    for w in range(1, weeks_back + 1):
        hist_date = target_date - timedelta(days=7 * w)
        try:
            day_data = get_daily_data(hist_date, quiet=True)
        except Exception:
            continue

        te = day_data.get("TimeEntries")
        od = day_data.get("OrderDetails")
        if te is None or od is None:
            continue

        # Revenue
        od = od.copy()
        if "Voided" in od.columns:
            od = od[od["Voided"] != True]
        od["Amount"] = pd.to_numeric(od.get("Amount", pd.Series(dtype="float64")),
                                     errors="coerce").fillna(0)
        rev = od["Amount"].sum()
        revenues.append(rev)

        # Parse TimeEntries clock-in/out
        te = te.copy()
        te["_in_dt"] = te["In Date"].apply(parse_toast_datetime)
        te["_out_dt"] = te["Out Date"].apply(parse_toast_datetime)
        te_valid = te[te["_in_dt"].notna() & te["_out_dt"].notna()]
        if len(te_valid) == 0:
            continue

        # Determine BOH vs FOH from Job Title
        if "Job Title" in te_valid.columns:
            te_valid = te_valid.copy()
            te_valid["_is_boh"] = te_valid["Job Title"].str.lower().str.contains(
                "prep|grill|smoothie|wrap|kitchen|cook|dish|boh",
                na=False
            )
        else:
            te_valid = te_valid.copy()
            te_valid["_is_boh"] = False

        # Build half-hour staffing counts
        day_start = te_valid["_in_dt"].min().replace(hour=7, minute=0, second=0)
        slot = day_start
        while slot.hour < 21 or (slot.hour == 21 and slot.minute == 0):
            slot_end = slot + timedelta(minutes=30)
            on_shift = te_valid[
                (te_valid["_in_dt"] < slot_end) & (te_valid["_out_dt"] > slot)
            ]
            foh_count = len(on_shift[~on_shift["_is_boh"]])
            boh_count = len(on_shift[on_shift["_is_boh"]])
            total_count = len(on_shift)

            time_key = slot.strftime("%H:%M")
            slot_totals[time_key]["foh"].append(foh_count)
            slot_totals[time_key]["boh"].append(boh_count)
            slot_totals[time_key]["total"].append(total_count)
            slot = slot_end

    if not revenues:
        return [], 0.0

    # Average the staffing curves
    avg_revenue = sum(revenues) / len(revenues)

    staffing_curve = []
    for h in range(7, 21):
        for m in (0, 30):
            time_key = f"{h:02d}:{m:02d}"
            data = slot_totals.get(time_key, {"foh": [0], "boh": [0], "total": [0]})
            foh_vals = data["foh"] or [0]
            boh_vals = data["boh"] or [0]
            total_vals = data["total"] or [0]
            staffing_curve.append({
                "time": time_key,
                "foh_count": round(sum(foh_vals) / len(foh_vals), 1),
                "boh_count": round(sum(boh_vals) / len(boh_vals), 1),
                "total_count": round(sum(total_vals) / len(total_vals), 1),
            })

    return staffing_curve, avg_revenue


def compute_labor_demand(target_date: datetime, revenue_forecast: float,
                         weeks_back: int = 4) -> dict:
    """Compute labor demand for a target date given a revenue forecast.

    Returns dict with:
    - halfhour_demand: list of {time, foh_needed, boh_needed, total_needed}
    - daypart_demand: aggregated demand by daypart
    - total_labor_hours: total hours needed across all positions
    - foh_hours: FOH labor hours needed
    - boh_hours: BOH labor hours needed
    - estimated_labor_cost: hours * blended wage
    - splh_target: the target SPLH used
    - revenue_forecast: the input revenue
    """
    staffing_curve, avg_revenue = _get_historical_staffing(target_date, weeks_back)

    if not staffing_curve or avg_revenue <= 0:
        # Fallback: derive from SPLH target directly
        total_hours = revenue_forecast / SPLH_TARGET if revenue_forecast > 0 else 0
        return _fallback_demand(target_date, revenue_forecast, total_hours)

    # Scale factor: how much more/less revenue vs historical average
    scale = revenue_forecast / avg_revenue if avg_revenue > 0 else 1.0

    halfhour_demand = []
    total_foh_hours = 0.0
    total_boh_hours = 0.0

    for slot in staffing_curve:
        # Scale historical counts by revenue ratio
        foh_raw = slot["foh_count"] * scale
        boh_raw = slot["boh_count"] * scale

        # Apply minimums (during 8a-9p open hours)
        hour = int(slot["time"].split(":")[0])
        if 8 <= hour < 21:
            foh_needed = max(round(foh_raw), MIN_FOH)
            boh_needed = max(round(boh_raw), MIN_BOH)
        elif 7 <= hour < 8:
            # Pre-open: prep crew only
            foh_needed = max(round(foh_raw), 1)
            boh_needed = max(round(boh_raw), MIN_BOH)
        else:
            foh_needed = round(foh_raw)
            boh_needed = round(boh_raw)

        total_needed = foh_needed + boh_needed
        halfhour_demand.append({
            "time": slot["time"],
            "foh_needed": foh_needed,
            "boh_needed": boh_needed,
            "total_needed": total_needed,
            "historical_foh": slot["foh_count"],
            "historical_boh": slot["boh_count"],
        })

        # Each half-hour slot = 0.5 hours
        total_foh_hours += foh_needed * 0.5
        total_boh_hours += boh_needed * 0.5

    total_hours = total_foh_hours + total_boh_hours

    # Daypart aggregation
    daypart_demand = []
    for dp_start, dp_end, dp_name in _DAYPARTS:
        dp_foh = 0.0
        dp_boh = 0.0
        dp_slots = 0
        for slot in halfhour_demand:
            h = int(slot["time"].split(":")[0])
            if dp_start <= h < dp_end:
                dp_foh += slot["foh_needed"]
                dp_boh += slot["boh_needed"]
                dp_slots += 1

        # Average headcount during this daypart
        avg_foh = round(dp_foh / dp_slots, 1) if dp_slots > 0 else 0
        avg_boh = round(dp_boh / dp_slots, 1) if dp_slots > 0 else 0
        daypart_demand.append({
            "daypart": dp_name,
            "hours": f"{dp_start}:00-{dp_end}:00",
            "avg_foh": avg_foh,
            "avg_boh": avg_boh,
            "avg_total": round(avg_foh + avg_boh, 1),
            "foh_hours": round(dp_foh * 0.5, 1),
            "boh_hours": round(dp_boh * 0.5, 1),
        })

    # Estimate blended wage
    blended_wage = _estimate_blended_wage()
    labor_cost = round(total_hours * blended_wage, 2)

    return {
        "halfhour_demand": halfhour_demand,
        "daypart_demand": daypart_demand,
        "total_labor_hours": round(total_hours, 1),
        "foh_hours": round(total_foh_hours, 1),
        "boh_hours": round(total_boh_hours, 1),
        "estimated_labor_cost": labor_cost,
        "blended_wage": round(blended_wage, 2),
        "splh_target": SPLH_TARGET,
        "revenue_forecast": round(revenue_forecast, 2),
        "avg_historical_revenue": round(avg_revenue, 2),
        "scale_factor": round(scale, 3),
    }


def _fallback_demand(target_date: datetime, revenue_forecast: float,
                     total_hours: float) -> dict:
    """Simple demand estimate when historical data is unavailable."""
    # Split 55% BOH / 45% FOH
    foh_hours = round(total_hours * 0.45, 1)
    boh_hours = round(total_hours * 0.55, 1)
    blended_wage = _estimate_blended_wage()

    return {
        "halfhour_demand": [],
        "daypart_demand": [],
        "total_labor_hours": round(total_hours, 1),
        "foh_hours": foh_hours,
        "boh_hours": boh_hours,
        "estimated_labor_cost": round(total_hours * blended_wage, 2),
        "blended_wage": round(blended_wage, 2),
        "splh_target": SPLH_TARGET,
        "revenue_forecast": round(revenue_forecast, 2),
        "avg_historical_revenue": 0,
        "scale_factor": 1.0,
    }


def _estimate_blended_wage() -> float:
    """Compute average hourly wage across all employees in availability YAML."""
    from .availability import get_all_employees
    employees = get_all_employees()
    wages = [e["wage"] for e in employees if e["wage"] > 0]
    if wages:
        return sum(wages) / len(wages)
    return 20.0  # reasonable default


def compute_weekly_demand(week_days: list[dict]) -> list[dict]:
    """Compute labor demand for each day in a week.

    Args:
        week_days: list of dicts from generate_week_view()['this_week']['days']
                   Each has 'date', 'revenue', 'dow_name', etc.

    Returns list of daily demand dicts, one per day.
    """
    results = []
    for day in week_days:
        dt = datetime.strptime(day["date"], "%Y-%m-%d")
        demand = compute_labor_demand(dt, day["revenue"])
        demand["date"] = day["date"]
        demand["dow_name"] = day.get("dow_name", _DOW_NAMES[dt.weekday()])
        demand["is_actual"] = day.get("is_actual", False)
        results.append(demand)
    return results
