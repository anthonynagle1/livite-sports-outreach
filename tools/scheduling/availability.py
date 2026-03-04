"""Employee availability — load, save, and query the YAML database."""

from __future__ import annotations

import os
import yaml
from typing import Optional

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_AVAIL_PATH = os.path.join(_BASE_DIR, "data", "availability.yaml")
_CONFIG_PATH = os.path.join(_BASE_DIR, "config.yaml")

_DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def load_availability() -> dict:
    """Load employee availability from YAML.

    Returns dict with 'employees' key mapping name -> employee record.
    Each employee has: display_name, department, max_hours_week,
    skills, availability (per-DOW), notes.
    """
    with open(_AVAIL_PATH, "r") as f:
        data = yaml.safe_load(f) or {}
    return data


def save_availability(data: dict) -> None:
    """Write employee availability back to YAML."""
    with open(_AVAIL_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False,
                  allow_unicode=True)


def _load_wages() -> dict:
    """Load master wage table from config.yaml."""
    try:
        with open(_CONFIG_PATH, "r") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("wages", {})
    except FileNotFoundError:
        return {}


def get_employee_wage(name: str) -> float:
    """Get effective hourly wage for an employee.

    Salaried (Zadith): returns flat_daily / 10 as pseudo-hourly for cost estimates.
    Owners: returns replacement_wage.
    Managers/Hourly: returns wage.
    """
    wages = _load_wages()
    rec = wages.get(name.lower())
    if not rec:
        return 0.0
    etype = rec.get("type", "hourly")
    if etype == "salaried":
        return rec.get("flat_daily", 300) / 10.0  # ~10h day
    elif etype == "owner":
        return rec.get("replacement_wage", 25.0)
    else:
        return float(rec.get("wage", 0))


def get_employee_type(name: str) -> str:
    """Get employee type from master wage table."""
    wages = _load_wages()
    rec = wages.get(name.lower())
    if not rec:
        return "unknown"
    return rec.get("type", "hourly")


def get_available_employees(day_name: str, start_hour: float = 7.0,
                            end_hour: float = 21.0,
                            department: Optional[str] = None) -> list[dict]:
    """Return employees available for a given day and time window.

    Args:
        day_name: "Mon", "Tue", etc.
        start_hour: shift start as decimal hour (e.g. 11.5 = 11:30)
        end_hour: shift end as decimal hour
        department: optional filter — "FOH", "BOH", or None for all

    Returns list of dicts: {name, display_name, department, avail_start,
    avail_end, max_hours_week, skills, wage}
    """
    data = load_availability()
    employees = data.get("employees", {})
    result = []

    for name, emp in employees.items():
        # Department filter
        emp_dept = emp.get("department", "")
        if department:
            dept_lower = department.lower()
            if emp_dept == "both":
                pass  # can work either
            elif emp_dept.lower() != dept_lower:
                continue

        avail = emp.get("availability", {})
        day_avail = avail.get(day_name)
        if day_avail is None:
            continue  # unavailable this day

        avail_start = _time_to_hours(day_avail.get("start", "07:00"))
        avail_end = _time_to_hours(day_avail.get("end", "21:00"))

        # Employee must be available for at least part of the requested window
        overlap_start = max(avail_start, start_hour)
        overlap_end = min(avail_end, end_hour)
        if overlap_end <= overlap_start:
            continue  # no overlap

        result.append({
            "name": name,
            "display_name": emp.get("display_name", name),
            "department": emp_dept,
            "avail_start": avail_start,
            "avail_end": avail_end,
            "max_hours_week": emp.get("max_hours_week", 40),
            "skills": emp.get("skills", []),
            "wage": get_employee_wage(name),
            "type": get_employee_type(name),
        })

    return result


def get_all_employees() -> list[dict]:
    """Return all employees with their availability and wage info."""
    data = load_availability()
    employees = data.get("employees", {})
    result = []

    for name, emp in employees.items():
        avail = emp.get("availability", {})
        available_days = []
        for dow in _DOW_NAMES:
            day_avail = avail.get(dow)
            if day_avail is not None:
                available_days.append({
                    "day": dow,
                    "start": day_avail.get("start", "07:00"),
                    "end": day_avail.get("end", "21:00"),
                })

        result.append({
            "name": name,
            "display_name": emp.get("display_name", name),
            "department": emp.get("department", ""),
            "max_hours_week": emp.get("max_hours_week", 40),
            "skills": emp.get("skills", []),
            "notes": emp.get("notes", ""),
            "wage": get_employee_wage(name),
            "type": get_employee_type(name),
            "available_days": available_days,
            "availability": avail,
        })

    return result


def add_employee(name: str, record: dict) -> None:
    """Add a new employee to availability.yaml."""
    data = load_availability()
    employees = data.setdefault("employees", {})
    employees[name] = record
    save_availability(data)


def remove_employee(name: str) -> bool:
    """Remove an employee from availability.yaml. Returns True if found."""
    data = load_availability()
    employees = data.get("employees", {})
    if name not in employees:
        return False
    del employees[name]
    save_availability(data)
    return True


def get_custom_roles() -> list:
    """Return custom_roles list from availability.yaml."""
    data = load_availability()
    return data.get("custom_roles", [])


def add_custom_role(role: str) -> None:
    """Append a new role to the custom_roles list."""
    data = load_availability()
    roles = data.setdefault("custom_roles", [])
    if role not in roles:
        roles.append(role)
        save_availability(data)


def remove_custom_role(role: str) -> bool:
    """Remove a role from the custom_roles list and all employees."""
    data = load_availability()
    roles = data.get("custom_roles", [])
    if role not in roles:
        return False
    roles.remove(role)
    # Also strip from all employees' skills
    for emp in data.get("employees", {}).values():
        skills = emp.get("skills", [])
        if role in skills:
            skills.remove(role)
    save_availability(data)
    return True


def update_wage(name: str, wage: float) -> None:
    """Update an employee's wage in config.yaml."""
    try:
        with open(_CONFIG_PATH, "r") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        cfg = {}
    wages = cfg.setdefault("wages", {})
    key = name.lower()
    if key in wages:
        wages[key]["wage"] = wage
    else:
        wages[key] = {"wage": wage, "type": "hourly"}
    with open(_CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False,
                  allow_unicode=True)


def _time_to_hours(time_str: str) -> float:
    """Convert "HH:MM" to decimal hours. e.g. "15:30" -> 15.5."""
    parts = time_str.split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    return h + m / 60.0


def _hours_to_time(hours: float) -> str:
    """Convert decimal hours to "HH:MM". e.g. 15.5 -> "15:30"."""
    h = int(hours)
    m = int((hours - h) * 60)
    return f"{h:02d}:{m:02d}"
