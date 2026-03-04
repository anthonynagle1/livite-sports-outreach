"""Schedule persistence — save/load/publish weekly schedules as YAML files.

Schedules are stored in data/schedules/<week>.yaml (e.g. 2026-W08.yaml).
Each file contains the full schedule data dict plus metadata:
  status: draft | published
  created_at: ISO timestamp
  published_at: ISO timestamp (when published)
  days: list of daily schedules with shifts
"""

from __future__ import annotations

import os
from datetime import datetime

import yaml

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCHEDULES_DIR = os.path.join(_BASE_DIR, "data", "schedules")


def _ensure_dir():
    os.makedirs(_SCHEDULES_DIR, exist_ok=True)


def _week_path(week_str: str) -> str:
    """Return file path for a week string like '2026-W08'."""
    safe = week_str.replace("/", "").replace("..", "")
    return os.path.join(_SCHEDULES_DIR, f"{safe}.yaml")


def _extract_saveable(schedule_data: dict) -> dict:
    """Extract the parts of schedule data worth persisting.

    Strips bulky demand/coverage arrays that can be regenerated,
    keeps shifts, employee_summary, and weekly totals.
    """
    days = []
    for d in schedule_data.get("days", []):
        days.append({
            "date": d.get("date", ""),
            "dow_name": d.get("dow_name", ""),
            "label": d.get("label", ""),
            "revenue_forecast": d.get("revenue_forecast", 0),
            "shifts": d.get("shifts", []),
            "total_hours": d.get("total_hours", 0),
            "labor_cost": d.get("labor_cost", 0),
            "headcount": d.get("headcount", 0),
            "foh_count": d.get("foh_count", 0),
            "boh_count": d.get("boh_count", 0),
        })

    return {
        "days": days,
        "weekly_hours": schedule_data.get("weekly_hours", {}),
        "employee_summary": schedule_data.get("employee_summary", []),
        "labor_cost_estimate": schedule_data.get("labor_cost_estimate", 0),
        "total_labor_hours": schedule_data.get("total_labor_hours", 0),
        "coverage_score": schedule_data.get("coverage_score", 0),
        "total_revenue_forecast": schedule_data.get("total_revenue_forecast", 0),
        "projected_splh": schedule_data.get("projected_splh", 0),
        "week_label": schedule_data.get("week_label", ""),
        "week": schedule_data.get("week", "this"),
    }


def save_schedule(week_str: str, schedule_data: dict,
                  status: str = "draft") -> dict:
    """Save schedule to YAML file.

    Args:
        week_str: ISO week like '2026-W08'
        schedule_data: full schedule dict from generate_weekly_schedule()
        status: 'draft' or 'published'

    Returns metadata dict with status and timestamps.
    """
    _ensure_dir()
    path = _week_path(week_str)

    # Load existing metadata if file exists
    existing = _load_raw(week_str)
    created_at = (existing or {}).get("created_at", datetime.now().isoformat())

    record = {
        "status": status,
        "created_at": created_at,
        "updated_at": datetime.now().isoformat(),
    }
    if status == "published":
        record["published_at"] = (
            (existing or {}).get("published_at") or datetime.now().isoformat()
        )

    record["schedule"] = _extract_saveable(schedule_data)

    with open(path, "w") as f:
        yaml.dump(record, f, default_flow_style=False, sort_keys=False,
                  allow_unicode=True)

    return {
        "status": record["status"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "published_at": record.get("published_at"),
        "week": week_str,
    }


def _load_raw(week_str: str) -> dict | None:
    """Load raw YAML data for a week. Returns None if not found."""
    path = _week_path(week_str)
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return yaml.safe_load(f) or None


def load_schedule(week_str: str) -> dict | None:
    """Load a saved schedule for a given week.

    Returns the full schedule dict (same shape as generate_weekly_schedule output)
    plus metadata fields: _status, _created_at, _updated_at, _published_at.
    Returns None if no saved schedule exists.
    """
    raw = _load_raw(week_str)
    if not raw:
        return None

    schedule = raw.get("schedule", {})
    schedule["_status"] = raw.get("status", "draft")
    schedule["_created_at"] = raw.get("created_at", "")
    schedule["_updated_at"] = raw.get("updated_at", "")
    schedule["_published_at"] = raw.get("published_at", "")
    return schedule


def publish_schedule(week_str: str) -> dict | None:
    """Mark a saved schedule as published.

    Returns updated metadata, or None if no schedule found.
    """
    raw = _load_raw(week_str)
    if not raw:
        return None

    raw["status"] = "published"
    raw["published_at"] = datetime.now().isoformat()
    raw["updated_at"] = datetime.now().isoformat()

    path = _week_path(week_str)
    with open(path, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, sort_keys=False,
                  allow_unicode=True)

    return {
        "status": "published",
        "created_at": raw.get("created_at", ""),
        "updated_at": raw["updated_at"],
        "published_at": raw["published_at"],
        "week": week_str,
    }


def list_schedules() -> list[dict]:
    """Return list of saved schedules with metadata.

    Returns list of dicts: {week, status, created_at, updated_at, published_at}
    sorted by week descending.
    """
    _ensure_dir()
    results = []
    for fname in os.listdir(_SCHEDULES_DIR):
        if not fname.endswith(".yaml"):
            continue
        week_str = fname.replace(".yaml", "")
        raw = _load_raw(week_str)
        if raw:
            results.append({
                "week": week_str,
                "status": raw.get("status", "draft"),
                "created_at": raw.get("created_at", ""),
                "updated_at": raw.get("updated_at", ""),
                "published_at": raw.get("published_at"),
                "week_label": (raw.get("schedule") or {}).get("week_label", ""),
            })
    results.sort(key=lambda x: x["week"], reverse=True)
    return results


def delete_schedule(week_str: str) -> bool:
    """Delete a saved schedule file. Returns True if found and deleted."""
    path = _week_path(week_str)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False
