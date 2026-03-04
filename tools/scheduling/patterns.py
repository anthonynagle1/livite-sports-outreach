"""Employee work pattern learning — mine Toast TimeEntries for scheduling intelligence.

Scans historical TimeEntries to learn each employee's typical work patterns:
which DOWs they work, what times, how long. Used by the scheduler to assign
shifts that match reality instead of generic greedy placement.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta

import yaml

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_CACHE_PATH = os.path.join(_BASE_DIR, ".tmp", "employee_patterns.json")
_CACHE_TTL = 86400  # 24 hours


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
    """Normalize a Toast employee name to match availability.yaml keys."""
    key = name.strip().lower()
    return aliases.get(key, key)


def build_employee_patterns(weeks_back: int = 8) -> dict:
    """Mine Toast TimeEntries to build per-employee work patterns.

    For each employee+DOW, computes:
      - freq: fraction of weeks the employee worked that DOW (0.0-1.0)
      - avg_start: average clock-in time as decimal hours
      - avg_end: average clock-out time as decimal hours
      - avg_hours: average shift length in hours
      - sample_count: number of data points
    """
    import sys
    sys.path.insert(0, os.path.join(_BASE_DIR, "tools"))

    from fetch_toast_data import get_daily_data
    from metrics.utils import parse_toast_datetime

    aliases = _load_aliases()

    # Accumulate: employee -> dow -> list of {start_hour, end_hour, hours}
    raw_data = defaultdict(lambda: defaultdict(list))
    # Track which DOWs had data (to compute frequency denominators)
    dow_weeks_with_data = defaultdict(int)  # dow_index -> count of weeks with data

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    for w in range(1, weeks_back + 1):
        week_start = today - timedelta(days=7 * w)
        for d in range(7):
            check_date = week_start + timedelta(days=d)
            if check_date >= today:
                continue

            dow_idx = check_date.weekday()
            dow_name = _DOW_NAMES[dow_idx]

            try:
                day_data = get_daily_data(check_date, quiet=True)
            except Exception:
                continue

            te = day_data.get("TimeEntries")
            if te is None or len(te) == 0:
                continue

            dow_weeks_with_data[dow_idx] += 1

            te = te.copy()
            te["_in_dt"] = te["In Date"].apply(parse_toast_datetime)
            te["_out_dt"] = te["Out Date"].apply(parse_toast_datetime)
            te_valid = te[te["_in_dt"].notna() & te["_out_dt"].notna()]

            for _, row in te_valid.iterrows():
                emp_name = _normalize_name(str(row["Employee"]), aliases)
                in_dt = row["_in_dt"]
                out_dt = row["_out_dt"]

                start_hour = in_dt.hour + in_dt.minute / 60.0
                end_hour = out_dt.hour + out_dt.minute / 60.0
                shift_hours = (out_dt - in_dt).total_seconds() / 3600.0

                if shift_hours < 1.0 or shift_hours > 16.0:
                    continue  # skip anomalous entries

                raw_data[emp_name][dow_name].append({
                    "start": start_hour,
                    "end": end_hour,
                    "hours": shift_hours,
                })

    # Compute averages and frequencies
    patterns = {}
    for emp_name, dow_map in raw_data.items():
        emp_patterns = {}
        for dow_name, shifts in dow_map.items():
            dow_idx = _DOW_NAMES.index(dow_name)
            total_weeks = dow_weeks_with_data.get(dow_idx, 1)
            count = len(shifts)

            avg_start = sum(s["start"] for s in shifts) / count
            avg_end = sum(s["end"] for s in shifts) / count
            avg_hours = sum(s["hours"] for s in shifts) / count

            # Snap to nearest half-hour
            avg_start = round(avg_start * 2) / 2
            avg_end = round(avg_end * 2) / 2

            emp_patterns[dow_name] = {
                "freq": round(count / total_weeks, 2),
                "avg_start": avg_start,
                "avg_end": avg_end,
                "avg_hours": round(avg_hours, 1),
                "sample_count": count,
            }

        patterns[emp_name] = emp_patterns

    # Cache to disk
    os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
    try:
        with open(_CACHE_PATH, "w") as f:
            json.dump({"ts": datetime.now().isoformat(), "patterns": patterns}, f)
    except Exception as e:
        logger.warning("Failed to cache patterns: %s", e)

    return patterns


def get_employee_patterns(weeks_back: int = 8) -> dict:
    """Get employee patterns, using cache when available."""
    if os.path.exists(_CACHE_PATH):
        try:
            with open(_CACHE_PATH, "r") as f:
                cached = json.load(f)
            ts = datetime.fromisoformat(cached["ts"])
            if (datetime.now() - ts).total_seconds() < _CACHE_TTL:
                return cached["patterns"]
        except Exception:
            pass

    return build_employee_patterns(weeks_back)


def get_employee_typical_shift(patterns: dict, employee_name: str,
                                dow: str) -> dict | None:
    """Look up a single employee's typical shift for a given DOW."""
    emp = patterns.get(employee_name, {})
    return emp.get(dow)
