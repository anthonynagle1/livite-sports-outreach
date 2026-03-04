"""Scheduled vs Actual — compare saved schedule to Toast TimeEntries.

Pulls actual clock-in/out data from Azure Blob for each day of a
published schedule week, then computes per-employee variance.
"""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_BASE_DIR, "tools"))

import yaml

logger = logging.getLogger(__name__)

_DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _load_aliases() -> dict:
    """Load employee alias map from config.yaml."""
    config_path = os.path.join(_BASE_DIR, "config.yaml")
    try:
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f)
        return cfg.get("employee_aliases", {})
    except Exception:
        return {}


def _normalize_name(name: str, aliases: dict) -> str:
    key = name.strip().lower()
    return aliases.get(key, key)


def get_actual_hours(dates: list[str]) -> dict:
    """Pull Toast TimeEntries for a list of dates.

    Args:
        dates: list of date strings in YYYY-MM-DD format

    Returns dict mapping employee -> list of actual shifts:
        {employee: [{date, start, end, hours}]}
    """
    from fetch_toast_data import get_daily_data
    from metrics.utils import parse_toast_datetime

    aliases = _load_aliases()
    employee_shifts = defaultdict(list)

    for date_str in dates:
        # Convert YYYY-MM-DD to YYYYMMDD for Azure fetch
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue

        blob_date = dt.strftime("%Y%m%d")

        try:
            day_data = get_daily_data(dt, quiet=True)
        except Exception as e:
            logger.warning("Failed to fetch TimeEntries for %s: %s", date_str, e)
            continue

        te = day_data.get("TimeEntries")
        if te is None or len(te) == 0:
            continue

        te = te.copy()
        te["_in_dt"] = te["In Date"].apply(parse_toast_datetime)
        te["_out_dt"] = te["Out Date"].apply(parse_toast_datetime)
        valid = te[te["_in_dt"].notna() & te["_out_dt"].notna()]

        for _, row in valid.iterrows():
            emp_name = _normalize_name(str(row["Employee"]), aliases)
            in_dt = row["_in_dt"]
            out_dt = row["_out_dt"]
            hours = (out_dt - in_dt).total_seconds() / 3600.0

            if hours < 0.5 or hours > 16.0:
                continue

            start_time = f"{in_dt.hour:02d}:{in_dt.minute:02d}"
            end_time = f"{out_dt.hour:02d}:{out_dt.minute:02d}"

            employee_shifts[emp_name].append({
                "date": date_str,
                "start": start_time,
                "end": end_time,
                "hours": round(hours, 1),
            })

    return dict(employee_shifts)


def compare_scheduled_vs_actual(schedule_data: dict) -> dict:
    """Compare a saved schedule against Toast TimeEntries actuals.

    Args:
        schedule_data: loaded schedule dict (from persistence.load_schedule)

    Returns:
        {
            employees: [
                {name, display_name, department,
                 scheduled_hrs, actual_hrs, variance, variance_pct,
                 scheduled_shifts, actual_shifts,
                 days: [{date, dow, sched_start, sched_end, sched_hrs,
                         actual_start, actual_end, actual_hrs}]}
            ],
            totals: {scheduled, actual, variance, variance_pct},
            week_label: str,
        }
    """
    days = schedule_data.get("days", [])
    if not days:
        return {"employees": [], "totals": {}, "week_label": ""}

    # Collect all dates from the schedule
    dates = [d["date"] for d in days if d.get("date")]

    # Build scheduled lookup: employee -> {date: shift}
    sched_by_emp = defaultdict(dict)
    emp_info = {}  # name -> {display_name, department}
    for d in days:
        date = d.get("date", "")
        for s in d.get("shifts", []):
            emp = s["employee"]
            sched_by_emp[emp][date] = {
                "start": s.get("start", ""),
                "end": s.get("end", ""),
                "hours": s.get("hours", 0),
            }
            if emp not in emp_info:
                emp_info[emp] = {
                    "display_name": s.get("display_name", emp),
                    "department": s.get("dept", ""),
                }

    # Get actual hours from Toast
    actual_by_emp = get_actual_hours(dates)

    # Merge all employees (union of scheduled + actual)
    all_employees = set(sched_by_emp.keys()) | set(actual_by_emp.keys())

    results = []
    total_sched = 0
    total_actual = 0

    for emp in sorted(all_employees):
        sched_dates = sched_by_emp.get(emp, {})
        actual_shifts = actual_by_emp.get(emp, [])

        # Group actual shifts by date
        actual_by_date = defaultdict(list)
        for a in actual_shifts:
            actual_by_date[a["date"]].append(a)

        scheduled_hrs = sum(s["hours"] for s in sched_dates.values())
        actual_hrs = sum(a["hours"] for a in actual_shifts)
        variance = round(actual_hrs - scheduled_hrs, 1)
        variance_pct = (
            round(variance / scheduled_hrs * 100, 1)
            if scheduled_hrs > 0 else (100.0 if actual_hrs > 0 else 0)
        )

        # Per-day breakdown
        day_details = []
        for d in days:
            date = d.get("date", "")
            dow = d.get("dow_name", "")
            sched = sched_dates.get(date)
            actuals = actual_by_date.get(date, [])

            # Take the longest actual shift for the day (primary shift)
            actual = max(actuals, key=lambda a: a["hours"]) if actuals else None

            day_details.append({
                "date": date,
                "dow": dow,
                "sched_start": sched["start"] if sched else None,
                "sched_end": sched["end"] if sched else None,
                "sched_hrs": sched["hours"] if sched else 0,
                "actual_start": actual["start"] if actual else None,
                "actual_end": actual["end"] if actual else None,
                "actual_hrs": round(sum(a["hours"] for a in actuals), 1),
            })

        info = emp_info.get(emp, {"display_name": emp, "department": ""})

        results.append({
            "name": emp,
            "display_name": info["display_name"],
            "department": info["department"],
            "scheduled_hrs": round(scheduled_hrs, 1),
            "actual_hrs": round(actual_hrs, 1),
            "variance": variance,
            "variance_pct": variance_pct,
            "scheduled_shifts": len(sched_dates),
            "actual_shifts": len(actual_shifts),
            "days": day_details,
        })

        total_sched += scheduled_hrs
        total_actual += actual_hrs

    total_variance = round(total_actual - total_sched, 1)
    total_var_pct = (
        round(total_variance / total_sched * 100, 1) if total_sched > 0 else 0
    )

    # Sort: biggest absolute variance first
    results.sort(key=lambda x: -abs(x["variance"]))

    return {
        "employees": results,
        "totals": {
            "scheduled": round(total_sched, 1),
            "actual": round(total_actual, 1),
            "variance": total_variance,
            "variance_pct": total_var_pct,
        },
        "week_label": schedule_data.get("week_label", ""),
    }
