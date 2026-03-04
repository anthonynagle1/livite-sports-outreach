"""Schedule generator — auto-assign employees to shifts based on demand.

Pattern-aware algorithm:
1. Mine Toast TimeEntries to learn each employee's typical work patterns
2. Managers first — pattern-aware start times
3. Score remaining staff: pattern_freq × demand_coverage × hours_ratio
4. Schedule in score order, using historical shift times when available
5. Stop when all demand slots are filled — no over-scheduling
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta

from .availability import (
    get_available_employees, get_all_employees,
    get_employee_wage, get_employee_type, _hours_to_time, _time_to_hours,
)
from .demand import compute_labor_demand, compute_weekly_demand, SPLH_TARGET
from .patterns import get_employee_patterns, get_employee_typical_shift

logger = logging.getLogger(__name__)

_DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Managers always get scheduled first
_MANAGERS = {"interiano, pedro", "lakomski, leah", "delcid, zadith"}

# Max shift length (realistic cap — nobody works 14h)
_MAX_SHIFT_HOURS = 10.0

# Standard shift templates (prefer these over arbitrary lengths)
_SHIFT_TEMPLATES = [
    (8.0, "Full Day"),      # 8h shift
    (7.0, "Long Shift"),    # 7h
    (6.0, "Standard"),      # 6h
    (5.0, "Half Plus"),     # 5h
    (4.0, "Half Day"),      # 4h
    (3.5, "Short"),         # 3.5h
]


def generate_weekly_schedule(week: str = "this") -> dict:
    """Generate a complete weekly schedule.

    Args:
        week: "this" or "next"

    Returns dict with:
        days: list of daily schedules (shifts, demand, coverage)
        weekly_hours: per-employee weekly total
        labor_cost_estimate: total $ for the week
        coverage_score: % of demand slots filled
        week_label: "Feb 17 - Feb 23" style label
        total_revenue_forecast: sum of daily forecasts
    """
    import sys
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "tools"
    ))

    from tools.forecast import generate_week_view
    week_data = generate_week_view()

    if week == "next":
        week_info = week_data.get("next_week", {})
    else:
        week_info = week_data.get("this_week", {})

    week_days = week_info.get("days", [])
    if not week_days:
        return _empty_schedule()

    # Compute demand for each day
    daily_demands = compute_weekly_demand(week_days)

    # Track weekly hours per employee
    weekly_hours = defaultdict(float)
    all_employees = {e["name"]: e for e in get_all_employees()}

    # Load historical work patterns for smart scheduling
    try:
        employee_patterns = get_employee_patterns()
    except Exception as e:
        logger.warning("Failed to load employee patterns: %s", e)
        employee_patterns = {}

    # Pre-compute available days per employee (for even distribution)
    emp_avail_days = {}
    for name, emp in all_employees.items():
        avail = emp.get("availability", {})
        days_available = []
        for j, di in enumerate(week_days):
            dow = di.get("dow_name", _DOW_NAMES[j % 7])
            if avail.get(dow) is not None:
                days_available.append(j)
        emp_avail_days[name] = days_available

    scheduled_days = []
    total_demand_slots = 0
    total_filled_slots = 0

    for i, day_info in enumerate(week_days):
        dow_name = day_info.get("dow_name", _DOW_NAMES[i % 7])
        date_str = day_info["date"]
        demand = daily_demands[i]
        revenue = day_info.get("revenue", 0)

        # Compute daily hour target per employee (even distribution)
        daily_targets = {}
        for name, emp in all_employees.items():
            remaining_days = [d for d in emp_avail_days.get(name, []) if d >= i]
            if not remaining_days:
                continue
            max_weekly = emp.get("max_hours_week", 40)
            used = weekly_hours.get(name, 0)
            remaining_hours = max(0, max_weekly - used)
            daily_targets[name] = remaining_hours / len(remaining_days)

        # Assign shifts for this day
        shifts, coverage = _assign_day_shifts(
            dow_name, date_str, demand, weekly_hours, all_employees,
            daily_targets=daily_targets,
            employee_patterns=employee_patterns,
        )

        # Track coverage
        day_demand_slots = len(demand.get("halfhour_demand", []))
        day_filled = sum(1 for s in coverage if s["filled"])
        total_demand_slots += day_demand_slots
        total_filled_slots += day_filled

        # Sum day's labor cost
        day_labor_cost = sum(s["cost"] for s in shifts)
        day_hours = sum(s["hours"] for s in shifts)

        scheduled_days.append({
            "date": date_str,
            "dow_name": dow_name,
            "label": day_info.get("label", date_str),
            "revenue_forecast": round(revenue, 2),
            "is_actual": day_info.get("is_actual", False),
            "shifts": shifts,
            "demand": demand,
            "coverage": coverage,
            "total_hours": round(day_hours, 1),
            "labor_cost": round(day_labor_cost, 2),
            "headcount": len(shifts),
            "foh_count": len([s for s in shifts if s["dept"] in ("FOH", "both")]),
            "boh_count": len([s for s in shifts if s["dept"] in ("BOH", "both")]),
        })

    # Weekly summary
    total_labor_cost = sum(d["labor_cost"] for d in scheduled_days)
    total_hours_sum = sum(d["total_hours"] for d in scheduled_days)
    total_revenue = sum(d["revenue_forecast"] for d in scheduled_days)
    coverage_score = (total_filled_slots / total_demand_slots * 100
                      if total_demand_slots > 0 else 0)

    # Build employee summary
    employee_summary = []
    for name, hours in sorted(weekly_hours.items(), key=lambda x: -x[1]):
        emp = all_employees.get(name, {})
        max_hrs = emp.get("max_hours_week", 40)
        wage = emp.get("wage", 0)
        shifts_count = sum(
            1 for d in scheduled_days
            for s in d["shifts"] if s["employee"] == name
        )
        cost = 0
        etype = emp.get("type", "hourly")
        if etype == "salaried":
            cost = sum(
                300.0 for d in scheduled_days
                for s in d["shifts"] if s["employee"] == name
            ) / max(shifts_count, 1) * shifts_count
        else:
            cost = hours * wage

        employee_summary.append({
            "name": name,
            "display_name": emp.get("display_name", name),
            "department": emp.get("department", ""),
            "weekly_hours": round(hours, 1),
            "max_hours": max_hrs,
            "shifts": shifts_count,
            "cost": round(cost, 2),
            "wage": wage,
            "type": etype,
            "pct_max": round(hours / max_hrs * 100, 1) if max_hrs > 0 else 0,
            "over_max": hours > max_hrs,
        })

    return {
        "days": scheduled_days,
        "weekly_hours": dict(weekly_hours),
        "employee_summary": sorted(employee_summary,
                                   key=lambda x: (x["department"], -x["weekly_hours"])),
        "labor_cost_estimate": round(total_labor_cost, 2),
        "total_labor_hours": round(total_hours_sum, 1),
        "coverage_score": round(coverage_score, 1),
        "total_revenue_forecast": round(total_revenue, 2),
        "projected_splh": round(total_revenue / total_hours_sum, 2)
                          if total_hours_sum > 0 else 0,
        "week_label": week_info.get("week_label", ""),
        "week": week,
    }


def _assign_day_shifts(dow_name: str, date_str: str, demand: dict,
                       weekly_hours: dict, all_employees: dict,
                       daily_targets: dict = None,
                       employee_patterns: dict = None) -> tuple:
    """Assign employee shifts for a single day.

    Args:
        daily_targets: per-employee target hours for this day (even distribution)
        employee_patterns: historical work patterns from Toast TimeEntries

    Returns (shifts, coverage) where:
    - shifts: list of {employee, display_name, dept, start, end, hours, cost}
    - coverage: list of {time, needed, assigned, filled}
    """
    halfhour_demand = demand.get("halfhour_demand", [])
    if not halfhour_demand:
        return [], []

    if daily_targets is None:
        daily_targets = {}
    if employee_patterns is None:
        employee_patterns = {}

    # Track which half-hour slots need coverage
    slot_needs = {}
    for slot in halfhour_demand:
        slot_needs[slot["time"]] = {
            "foh_needed": slot["foh_needed"],
            "boh_needed": slot["boh_needed"],
            "foh_assigned": 0,
            "boh_assigned": 0,
        }

    shifts = []

    # Phase 1: Schedule managers (pattern-aware start times)
    _schedule_managers(dow_name, shifts, slot_needs, weekly_hours,
                       all_employees, daily_targets, employee_patterns)

    # Phase 2: Score and schedule staff — stops when demand is met
    _schedule_staff(dow_name, shifts, slot_needs, weekly_hours,
                    all_employees, daily_targets, employee_patterns)

    # Build coverage report
    coverage = []
    for slot in halfhour_demand:
        needs = slot_needs.get(slot["time"], {})
        total_needed = slot.get("total_needed", 0)
        total_assigned = needs.get("foh_assigned", 0) + needs.get("boh_assigned", 0)
        coverage.append({
            "time": slot["time"],
            "needed": total_needed,
            "assigned": total_assigned,
            "filled": total_assigned >= total_needed,
            "gap": max(0, total_needed - total_assigned),
        })

    return shifts, coverage


def _schedule_managers(dow_name, shifts, slot_needs, weekly_hours,
                       all_employees, daily_targets=None,
                       employee_patterns=None):
    """Schedule managers first — they get full-day shifts with pattern-aware start times."""
    if daily_targets is None:
        daily_targets = {}
    if employee_patterns is None:
        employee_patterns = {}

    for mgr_name in _MANAGERS:
        emp = all_employees.get(mgr_name)
        if not emp:
            continue

        avail = emp.get("availability", {}).get(dow_name)
        if avail is None:
            continue  # unavailable this day

        avail_start = _time_to_hours(avail.get("start", "07:00"))
        avail_end = _time_to_hours(avail.get("end", "21:00"))

        # Max hours check
        current_weekly = weekly_hours.get(mgr_name, 0)
        max_weekly = emp.get("max_hours_week", 52)
        remaining = max_weekly - current_weekly
        if remaining <= 0:
            continue

        # Managers get capped shifts — use daily target for even distribution
        target = daily_targets.get(mgr_name, _MAX_SHIFT_HOURS)
        # Managers get at least 8h if available (they're key staff)
        mgr_target = max(target, 8.0)
        shift_hours = min(avail_end - avail_start, remaining, mgr_target, _MAX_SHIFT_HOURS)
        if shift_hours < 4:
            continue  # not worth a short shift

        # Use historical pattern for start time if available
        pattern = get_employee_typical_shift(employee_patterns, mgr_name, dow_name)
        if pattern and pattern.get("sample_count", 0) >= 2:
            actual_start = max(avail_start, pattern["avg_start"])
            actual_start = min(actual_start, avail_end - shift_hours)
        else:
            actual_start = avail_start
        actual_end = actual_start + shift_hours
        dept = emp.get("department", "BOH")
        wage = emp.get("wage", 0)
        etype = emp.get("type", "hourly")

        if etype == "salaried":
            cost = 300.0  # Zadith flat daily
        else:
            cost = shift_hours * wage

        shifts.append({
            "employee": mgr_name,
            "display_name": emp.get("display_name", mgr_name),
            "dept": dept,
            "start": _hours_to_time(actual_start),
            "end": _hours_to_time(actual_end),
            "hours": round(shift_hours, 1),
            "cost": round(cost, 2),
            "type": etype,
            "is_manager": True,
        })

        weekly_hours[mgr_name] = current_weekly + shift_hours

        # Update slot coverage
        _fill_slots(slot_needs, actual_start, actual_end, dept)


def _schedule_staff(dow_name, shifts, slot_needs, weekly_hours,
                    all_employees, daily_targets=None,
                    employee_patterns=None):
    """Schedule non-manager staff using scored ranking.

    Single pass: score candidates by historical pattern frequency, demand
    coverage, and remaining hours ratio. Process in score order and STOP
    when all demand slots are filled. No Phase B over-scheduling.
    """
    if daily_targets is None:
        daily_targets = {}
    if employee_patterns is None:
        employee_patterns = {}

    # Build scored candidate list
    candidates = []
    for name, emp in all_employees.items():
        if name in _MANAGERS:
            continue

        avail = emp.get("availability", {}).get(dow_name)
        if avail is None:
            continue

        avail_start = _time_to_hours(avail.get("start", "07:00"))
        avail_end = _time_to_hours(avail.get("end", "21:00"))
        max_weekly = emp.get("max_hours_week", 40)
        current_weekly = weekly_hours.get(name, 0)
        remaining = max_weekly - current_weekly

        if remaining < 3:
            continue

        dept = emp.get("department", "")
        target = daily_targets.get(name, remaining)

        # --- Score components ---
        # 1. Pattern frequency: how often this person historically works this DOW
        pattern = get_employee_typical_shift(employee_patterns, name, dow_name)
        pattern_freq = pattern["freq"] if pattern else 0.3

        # 2. Demand coverage: heavy bonus if their dept has unmet demand
        has_demand = _has_remaining_demand(slot_needs, dept, avail_start, avail_end)
        demand_cov = 1.0 if has_demand else 0.1

        # 3. Hours ratio: prefer people with more remaining capacity
        hours_ratio = remaining / max_weekly if max_weekly > 0 else 0.5

        score = pattern_freq * demand_cov * hours_ratio

        candidates.append({
            "name": name,
            "emp": emp,
            "avail_start": avail_start,
            "avail_end": avail_end,
            "remaining": remaining,
            "daily_target": target,
            "dept": dept,
            "wage": emp.get("wage", 0),
            "pattern": pattern,
            "score": score,
        })

    # Sort by score descending — high-value candidates first
    candidates.sort(key=lambda x: -x["score"])

    for candidate in candidates:
        # Stop when ALL demand is met (both FOH and BOH)
        if _all_demand_met(slot_needs):
            break

        dept = candidate["dept"]
        pattern = candidate["pattern"]

        # Determine shift start/end using pattern data or gap-based fallback
        if pattern and pattern.get("sample_count", 0) >= 2:
            # Use historical pattern: typical start time + shift length
            shift_start = max(candidate["avail_start"], pattern["avg_start"])
            shift_hours = min(
                pattern["avg_hours"],
                candidate["remaining"],
                candidate["daily_target"],
                candidate["avail_end"] - shift_start,
                _MAX_SHIFT_HOURS,
            )
            shift_end = shift_start + shift_hours

            # Verify pattern shift actually overlaps remaining demand
            if not _has_remaining_demand(slot_needs, dept, shift_start, shift_end):
                # Pattern shift misses the gaps — fall back to gap-based placement
                gap_start, gap_end = _find_best_shift(slot_needs, candidate, dept)
                if gap_start is None:
                    continue
                shift_start = gap_start
                shift_hours = min(
                    gap_end - gap_start, candidate["remaining"],
                    candidate["daily_target"], _MAX_SHIFT_HOURS,
                )
                shift_end = shift_start + shift_hours
        else:
            # No pattern data — use gap-based placement
            gap_start, gap_end = _find_best_shift(slot_needs, candidate, dept)
            if gap_start is None:
                continue
            shift_start = gap_start
            shift_hours = min(
                gap_end - gap_start, candidate["remaining"],
                candidate["daily_target"], _MAX_SHIFT_HOURS,
            )
            shift_end = shift_start + shift_hours

        if shift_hours < 3:
            continue

        wage = candidate["wage"]
        cost = shift_hours * wage

        shifts.append({
            "employee": candidate["name"],
            "display_name": candidate["emp"].get("display_name", candidate["name"]),
            "dept": dept,
            "start": _hours_to_time(shift_start),
            "end": _hours_to_time(shift_end),
            "hours": round(shift_hours, 1),
            "cost": round(cost, 2),
            "type": candidate["emp"].get("type", "hourly"),
            "is_manager": False,
        })

        weekly_hours[candidate["name"]] = (
            weekly_hours.get(candidate["name"], 0) + shift_hours
        )

        _fill_slots(slot_needs, shift_start, shift_end, dept)


def _has_remaining_demand(slot_needs, dept, start_hour, end_hour):
    """Check if any slots in the given window have unmet demand for dept."""
    for time_key, needs in slot_needs.items():
        h, m = time_key.split(":")
        slot_hour = int(h) + int(m) / 60.0

        if slot_hour < start_hour or slot_hour >= end_hour:
            continue

        if dept in ("BOH", "both"):
            if needs["boh_needed"] > needs["boh_assigned"]:
                return True
        if dept in ("FOH", "both"):
            if needs["foh_needed"] > needs["foh_assigned"]:
                return True

    return False


def _all_demand_met(slot_needs):
    """Check if ALL demand slots are fully covered (both FOH and BOH)."""
    for needs in slot_needs.values():
        if needs["foh_needed"] > needs["foh_assigned"]:
            return False
        if needs["boh_needed"] > needs["boh_assigned"]:
            return False
    return True


def _find_best_shift(slot_needs, candidate, dept):
    """Find the best shift window for a candidate to cover demand gaps.

    Returns (start_hour, end_hour) or (None, None) if no useful shift.
    """
    avail_start = candidate["avail_start"]
    avail_end = candidate["avail_end"]
    remaining = candidate["remaining"]

    # Scan slots within availability for gaps
    gap_scores = []
    for time_key, needs in sorted(slot_needs.items()):
        h, m = time_key.split(":")
        slot_hour = int(h) + int(m) / 60.0

        if slot_hour < avail_start or slot_hour >= avail_end:
            continue

        # How much gap exists in this slot for this department?
        if dept in ("BOH", "both"):
            boh_gap = max(0, needs["boh_needed"] - needs["boh_assigned"])
        else:
            boh_gap = 0

        if dept in ("FOH", "both"):
            foh_gap = max(0, needs["foh_needed"] - needs["foh_assigned"])
        else:
            foh_gap = 0

        total_gap = foh_gap + boh_gap
        if total_gap > 0:
            gap_scores.append((slot_hour, total_gap))

    if not gap_scores:
        return None, None

    # Find the contiguous stretch with the most total gap
    # Start from the slot with the biggest gap
    gap_scores.sort(key=lambda x: -x[1])
    peak_hour = gap_scores[0][0]

    # Expand around the peak to build a reasonable shift
    target_hours = min(remaining, avail_end - avail_start, 8.0)

    # Try to center the shift around the peak demand
    ideal_start = max(avail_start, peak_hour - target_hours / 2)
    ideal_end = min(avail_end, ideal_start + target_hours)
    ideal_start = max(avail_start, ideal_end - target_hours)

    # Snap to half-hour boundaries
    ideal_start = round(ideal_start * 2) / 2
    ideal_end = round(ideal_end * 2) / 2

    if ideal_end - ideal_start < 3:
        return None, None

    return ideal_start, ideal_end


def _fill_slots(slot_needs, start_hour, end_hour, dept):
    """Mark slots as covered by a shift."""
    for time_key, needs in slot_needs.items():
        h, m = time_key.split(":")
        slot_hour = int(h) + int(m) / 60.0

        if slot_hour < start_hour or slot_hour >= end_hour:
            continue

        if dept in ("BOH", "both"):
            needs["boh_assigned"] += 1
        if dept in ("FOH", "both"):
            needs["foh_assigned"] += 1


def _empty_schedule() -> dict:
    """Return empty schedule structure."""
    return {
        "days": [],
        "weekly_hours": {},
        "employee_summary": [],
        "labor_cost_estimate": 0,
        "total_labor_hours": 0,
        "coverage_score": 0,
        "total_revenue_forecast": 0,
        "projected_splh": 0,
        "week_label": "",
        "week": "this",
    }
