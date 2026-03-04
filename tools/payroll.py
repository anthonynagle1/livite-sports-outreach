"""
Payroll computation engine for Gusto CSV export.

Aggregates employee hours from Toast TimeEntries (or Homebase CSV override),
calculates tip distribution, tracks manager OT balances, and builds
Gusto-ready payroll rows.
"""
import csv
import io
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import yaml

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_TMP = _ROOT / ".tmp"
_PAYROLL_DIR = _TMP / "payroll"
_BALANCES_FILE = _TMP / "manager_ot_balances.json"

# ── Config ──────────────────────────────────────────────────────────

def _load_config():
    with open(_ROOT / "config.yaml") as f:
        return yaml.safe_load(f)

def _get_wages(cfg):
    return cfg.get("wages", {})

def _get_aliases(cfg):
    return cfg.get("employee_aliases", {})

def _normalize_name(name, aliases):
    """Lowercase, strip, resolve aliases."""
    n = str(name).strip().lower()
    return aliases.get(n, n)


# ── Toast Hours Aggregation ─────────────────────────────────────────

def aggregate_toast_hours(start_date: str, end_date: str) -> dict:
    """
    Pull TimeEntries from Toast for start_date..end_date (YYYYMMDD format).
    Returns dict: {employee_name: {regular: X, ot: Y, total: Z}}

    Applies 40-hour weekly OT threshold for hourly staff.
    Managers get raw total hours (OT handled by balance system).
    Owners are excluded.
    """
    from tools.fetch_toast_data import get_date_range

    cfg = _load_config()
    wages = _get_wages(cfg)
    aliases = _get_aliases(cfg)

    try:
        te = get_date_range(start_date, end_date, "TimeEntries.csv")
    except Exception as e:
        logger.warning("Failed to fetch TimeEntries: %s", e)
        return {}

    if te is None or te.empty:
        return {}

    # Aggregate total hours per employee across the week
    totals = {}  # name -> total_hours
    for _, row in te.iterrows():
        emp = _normalize_name(row.get("Employee", ""), aliases)
        master = wages.get(emp)
        if not master or master.get("type") == "owner":
            continue

        hrs = float(row.get("Total Hours", 0) or 0)
        totals[emp] = totals.get(emp, 0.0) + hrs

    # Split into regular/OT using weekly thresholds
    result = {}
    for emp, total in totals.items():
        master = wages.get(emp, {})
        emp_type = master.get("type", "hourly")

        if emp_type == "manager":
            # Managers: report total hours, OT handled by balance system
            result[emp] = {
                "regular": round(total, 2),
                "ot": 0.0,
                "total": round(total, 2),
            }
        else:
            # Hourly: 40-hour weekly OT threshold
            if total > 40:
                result[emp] = {
                    "regular": 40.0,
                    "ot": round(total - 40, 2),
                    "total": round(total, 2),
                }
            else:
                result[emp] = {
                    "regular": round(total, 2),
                    "ot": 0.0,
                    "total": round(total, 2),
                }
    return result


def detect_auto_clockouts(start_date: str, end_date: str) -> list:
    """Find shifts where Toast auto-clocked out the employee.

    Returns list of dicts:
        [{employee, date, clock_in, clock_out, hours, auto}]
    """
    from tools.fetch_toast_data import get_toast_csv_cached
    from datetime import datetime as _dt, timedelta as _td

    cfg = _load_config()
    aliases = _get_aliases(cfg)
    alerts = []

    start = _dt.strptime(start_date, "%Y%m%d")
    end = _dt.strptime(end_date, "%Y%m%d")
    d = start
    while d <= end:
        ds = d.strftime("%Y%m%d")
        try:
            df = get_toast_csv_cached(ds, "TimeEntries.csv")
        except Exception:
            d += _td(days=1)
            continue
        if df is None or df.empty:
            d += _td(days=1)
            continue

        for _, row in df.iterrows():
            auto = str(row.get("Auto Clock-out", "")).strip().lower()
            if auto != "yes":
                continue
            emp = _normalize_name(row.get("Employee", ""), aliases)
            hrs = float(row.get("Total Hours", 0) or 0)
            alerts.append({
                "employee": emp,
                "date": d.strftime("%a %b %d"),
                "clock_in": str(row.get("In Date", "")),
                "clock_out": str(row.get("Out Date", "")),
                "hours": round(hrs, 2),
            })
        d += _td(days=1)

    return alerts


# ── Homebase CSV Parser ─────────────────────────────────────────────

def parse_homebase_csv(file_bytes: bytes) -> dict:
    """
    Parse Homebase Payroll Summary CSV/Excel upload.
    Columns: First name, Last name, Payroll ID, Regular hours, OT hours

    Returns dict: {employee_name: {regular: X, ot: Y, total: Z}}
    Name format: "last, first" (lowercase) to match config.yaml keys.
    """
    cfg = _load_config()
    aliases = _get_aliases(cfg)

    # Try reading as CSV first, then Excel
    try:
        text = file_bytes.decode("utf-8", errors="replace")
        lines = text.strip().splitlines()

        # Find header row (look for "First name" or "Regular hours")
        header_idx = 0
        for i, line in enumerate(lines):
            if "regular" in line.lower() and "hours" in line.lower():
                header_idx = i
                break

        reader = csv.DictReader(lines[header_idx:])
        rows = list(reader)
    except Exception:
        # Try as Excel
        try:
            df = pd.read_excel(io.BytesIO(file_bytes))
            rows = df.to_dict("records")
        except Exception as e:
            logger.error("Cannot parse Homebase file: %s", e)
            return {}

    result = {}
    for row in rows:
        # Flexible column name matching
        first = str(row.get("First name", row.get("first_name", ""))).strip()
        last = str(row.get("Last name", row.get("last_name", ""))).strip()
        if not first or not last:
            continue

        name = _normalize_name(f"{last}, {first}", aliases)

        reg_raw = row.get("Regular hours", row.get("regular_hours", 0))
        ot_raw = row.get("OT hours", row.get("overtime_hours", 0))

        # Handle dashes and non-numeric values
        try:
            reg = float(reg_raw) if reg_raw not in ("-", "", None) else 0.0
        except (ValueError, TypeError):
            reg = 0.0
        try:
            ot = float(ot_raw) if ot_raw not in ("-", "", None) else 0.0
        except (ValueError, TypeError):
            ot = 0.0

        result[name] = {
            "regular": round(reg, 2),
            "ot": round(ot, 2),
            "total": round(reg + ot, 2),
        }

    return result


# ── Tip Distribution ────────────────────────────────────────────────

def calculate_tips(pool_total: float, employee_hours: dict, cfg: dict) -> tuple:
    """
    Distribute tip pool across tip-eligible employees proportional to hours.

    Args:
        pool_total: Total tip pool $ for the period
        employee_hours: {name: {regular, ot, total}} from aggregation
        cfg: Full config dict

    Returns:
        (tip_map, rate):
        tip_map = {name: tip_amount}
        rate = $/hr tip rate
    """
    wages = _get_wages(cfg)

    # Calculate total tipped hours
    total_tipped_hours = 0.0
    tipped_employees = {}
    for emp, hours in employee_hours.items():
        master = wages.get(emp, {})
        if master.get("tips", False):
            tip_hours = hours.get("total", 0.0)
            if tip_hours > 0:
                tipped_employees[emp] = tip_hours
                total_tipped_hours += tip_hours

    if total_tipped_hours <= 0 or pool_total <= 0:
        return {}, 0.0

    rate = pool_total / total_tipped_hours

    tip_map = {}
    for emp, hrs in tipped_employees.items():
        tip_map[emp] = round(hrs * rate, 2)

    return tip_map, round(rate, 2)


# ── Toast Tip Aggregation ─────────────────────────────────────────

def aggregate_toast_tips(start_date: str, end_date: str) -> dict:
    """
    Pull tip totals from Toast OrderDetails for a date range.

    Uses OrderDetails (non-voided) for canonical tip totals.
    ShipDay tips identified via Dining Options = "Online Ordering - Delivery".

    Catering orders paid within the period but missing from PaymentDetails
    are excluded — their payment was captured in a prior data export and
    Toast already counted them in a previous Sales Summary.

    Args:
        start_date: "YYYYMMDD" format (blob folder name)
        end_date: "YYYYMMDD" format

    Returns:
        {
            "gross_tips": float,
            "shipday_tips": float,
            "catering_tips": float,
            "catering_excluded": float,
            "net_tips": float,  (gross - shipday)
        }
    """
    from tools.fetch_toast_data import get_date_range

    result = {
        "gross_tips": 0.0,
        "shipday_tips": 0.0,
        "catering_tips": 0.0,
        "catering_excluded": 0.0,
        "net_tips": 0.0,
    }

    try:
        ord_df = get_date_range(start_date, end_date, "OrderDetails.csv")
    except Exception as e:
        logger.warning("Failed to fetch OrderDetails: %s", e)
        return result

    if ord_df is None or ord_df.empty:
        return result

    # Exclude voided orders
    ord_df = ord_df[ord_df["Voided"] == False].copy()

    # Fetch PaymentDetails to detect pre-captured catering payments
    try:
        pay_df = get_date_range(start_date, end_date, "PaymentDetails.csv")
        paid_oids = set(pay_df["Order Id"].astype(str).unique()) if pay_df is not None else set()
    except Exception:
        paid_oids = set()

    # Exclude catering orders paid within the period but missing from
    # PaymentDetails (already captured in a prior Toast data export).
    period_start = datetime.strptime(start_date, "%Y%m%d")
    catering_excluded = 0.0
    exclude_mask = pd.Series(False, index=ord_df.index)

    if paid_oids:
        catering_mask = ord_df["Revenue Center"] == "Catering"
        for idx, row in ord_df[catering_mask].iterrows():
            if row["Tip"] <= 0:
                continue
            oid = str(row["Order Id"])
            if oid in paid_oids:
                continue  # has payment this period — keep it
            # Parse the Paid timestamp to check if it falls within period
            try:
                paid_dt = datetime.strptime(
                    str(row["Paid"]).strip(), "%m/%d/%y %I:%M %p"
                )
            except (ValueError, TypeError):
                continue
            if paid_dt >= period_start:
                # Paid within period but no PaymentDetails → already captured
                exclude_mask[idx] = True
                catering_excluded += row["Tip"]

    # Apply exclusions
    ord_df = ord_df[~exclude_mask]

    gross = float(ord_df["Tip"].sum())
    shipday = float(
        ord_df.loc[
            ord_df["Dining Options"] == "Online Ordering - Delivery", "Tip"
        ].sum()
    )
    catering = float(
        ord_df.loc[
            ord_df["Revenue Center"] == "Catering", "Tip"
        ].sum()
    )

    result["gross_tips"] = round(gross, 2)
    result["shipday_tips"] = round(shipday, 2)
    result["catering_tips"] = round(catering, 2)
    result["catering_excluded"] = round(catering_excluded, 2)
    result["net_tips"] = round(gross - shipday, 2)
    return result


# ── Employee Cash Tips (from /tips entry page) ─────────────────────

_CASH_TIPS_DIR = _ROOT / ".tmp" / "cash_tips"

def get_employee_cash_tips(start_date: str, end_date: str) -> dict:
    """
    Read employee-submitted cash tips for a date range.

    Args:
        start_date: "YYYYMMDD" format
        end_date: "YYYYMMDD" format

    Returns:
        {employee_name: total_cash_tips} across the range
    """
    from datetime import datetime as _dt, timedelta as _td
    start = _dt.strptime(start_date, "%Y%m%d")
    end = _dt.strptime(end_date, "%Y%m%d")
    totals = {}

    d = start
    while d <= end:
        iso = d.strftime("%Y-%m-%d")
        path = _CASH_TIPS_DIR / f"{iso}.json"
        if path.exists():
            try:
                entries = json.load(open(path))
                for entry in entries:
                    emp = entry.get("employee", "")
                    amt = float(entry.get("amount", 0))
                    if emp and amt > 0:
                        totals[emp] = round(totals.get(emp, 0) + amt, 2)
            except (json.JSONDecodeError, ValueError):
                pass
        d += _td(days=1)

    return totals


# ── Meire Commission (Notion) ──────────────────────────────────────

_NOTION_VERSION = "2025-09-03"
_NOTION_BASE = "https://api.notion.com/v1"
# Catering Orders/Sales data source ID
_CATERING_DS_ID = "2ca42679-f1a3-80d2-8ae0-000b9d39643b"

# Commission rates by Order Type (Direct Inbound only)
_COMMISSION_RATES = {
    "New Client": 0.05,
    "Repeat Client": 0.03,
}


def fetch_meire_commission(start_date: str, end_date: str) -> dict:
    """
    Calculate Meire's commission from Notion catering orders for a date range.

    Queries the Catering Orders database for completed Direct Inbound orders
    with delivery dates in [start_date, end_date] and calculates commission:
      - New Client: 5% of Subtotal
      - Repeat Client: 3% of Subtotal

    Args:
        start_date: "YYYY-MM-DD" (Monday of payroll week)
        end_date: "YYYY-MM-DD" (Sunday of payroll week)

    Returns:
        {
            "commission": float,
            "total_sales": float,
            "orders": [{"name", "subtotal", "order_type", "commission"}],
            "error": str or None,
        }
    """
    result = {"commission": 0.0, "total_sales": 0.0, "orders": [], "error": None}

    api_key = os.getenv("NOTION_API_KEY", "")
    if not api_key:
        result["error"] = "NOTION_API_KEY not set"
        return result

    headers = {
        "Authorization": "Bearer %s" % api_key,
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
    }

    # Query catering orders with delivery date in range
    payload = {
        "page_size": 100,
        "filter": {
            "and": [
                {
                    "property": "Delivery Date & Time",
                    "date": {"on_or_after": start_date},
                },
                {
                    "property": "Delivery Date & Time",
                    "date": {"on_or_before": end_date},
                },
            ]
        },
    }

    try:
        resp = requests.post(
            "%s/data_sources/%s/query" % (_NOTION_BASE, _CATERING_DS_ID),
            headers=headers,
            json=payload,
            timeout=20,
        )
    except Exception as e:
        result["error"] = "Notion API request failed: %s" % e
        return result

    if resp.status_code != 200:
        result["error"] = "Notion API error %s" % resp.status_code
        return result

    total_commission = 0.0
    total_sales = 0.0

    for page in resp.json().get("results", []):
        props = page.get("properties", {})

        # Order Status — only Completed/Confirmed
        status_prop = props.get("Order Status", {})
        status = ""
        if status_prop.get("type") == "select" and status_prop.get("select"):
            status = status_prop["select"].get("name", "")
        if status not in ("Completed", "Confirmed", "Comfirmed"):
            continue

        # Order Platform — only Direct Inbound gets commission
        plat_prop = props.get("Order Platform", {})
        platform = ""
        if plat_prop.get("type") == "select" and plat_prop.get("select"):
            platform = plat_prop["select"].get("name", "")

        # Subtotal
        sub_prop = props.get("Subtotal", {})
        subtotal = sub_prop.get("number", 0) or 0

        # Order Type (New Client / Repeat Client)
        type_prop = props.get("Order Type", {})
        order_type = ""
        if type_prop.get("type") == "select" and type_prop.get("select"):
            order_type = type_prop["select"].get("name", "")

        # Order name (title)
        name = ""
        for v in props.values():
            if v.get("type") == "title":
                tl = v.get("title", [])
                name = tl[0]["text"]["content"] if tl else ""
                break

        # Commission: only Direct Inbound with Order Type
        commission = 0.0
        if "Inbound" in platform and order_type in _COMMISSION_RATES and subtotal > 0:
            rate = _COMMISSION_RATES[order_type]
            commission = round(subtotal * rate, 2)

        if subtotal > 0 and "Inbound" in platform:
            total_sales += subtotal
            total_commission += commission
            result["orders"].append({
                "name": name,
                "subtotal": subtotal,
                "order_type": order_type or "N/A",
                "commission": commission,
            })

    result["commission"] = round(total_commission, 2)
    result["total_sales"] = round(total_sales, 2)
    return result


# ── Manager OT Balances ────────────────────────────────────────────

def get_manager_ot_balances() -> dict:
    """
    Read persistent manager OT balance file.
    Returns: {name: {balance: float, history: [{week, diff, balance, payout}]}}
    """
    if _BALANCES_FILE.exists():
        with open(_BALANCES_FILE) as f:
            return json.load(f)
    return {}


def save_manager_ot_balances(balances: dict):
    """Save manager OT balances to persistent file."""
    _TMP.mkdir(parents=True, exist_ok=True)
    with open(_BALANCES_FILE, "w") as f:
        json.dump(balances, f, indent=2)


def compute_manager_ot(name: str, hours_worked: float, week_end: str,
                       ot_rate: float, balances: dict) -> dict:
    """
    Compute manager OT balance update for a given week.
    Does NOT persist — call save_manager_ot_balances() separately.

    Args:
        name: Employee name (config key)
        hours_worked: Actual hours worked this week
        week_end: Week ending date string (e.g. "2026-02-15")
        ot_rate: Manager OT rate (e.g. 31.00 for Zadith)
        balances: Current balances dict (will be mutated)

    Returns: {diff, old_balance, new_balance, payout}
    """
    threshold = 50.0
    diff = round(hours_worked - threshold, 2)

    entry = balances.get(name, {"balance": 0.0, "history": []})
    old_balance = entry.get("balance", 0.0)
    new_balance = round(old_balance + diff, 2)

    payout = 0.0
    if new_balance > 0:
        payout = round(new_balance * ot_rate, 2)
        new_balance = 0.0

    entry["balance"] = new_balance
    entry["history"] = entry.get("history", [])
    # Only add if this week isn't already recorded
    if not any(h.get("week") == week_end for h in entry["history"]):
        entry["history"].append({
            "week": week_end,
            "hours": hours_worked,
            "diff": diff,
            "balance": new_balance,
            "payout": payout,
        })
    balances[name] = entry

    return {
        "diff": diff,
        "old_balance": old_balance,
        "new_balance": new_balance,
        "payout": payout,
    }


# ── Gusto CSV Builder ──────────────────────────────────────────────

GUSTO_COLUMNS = [
    "last_name", "first_name", "title", "gusto_employee_id",
    "regular_hours", "overtime_hours", "double_overtime_hours",
    "sick_hours", "bonus", "commission", "paycheck_tips",
    "cash_tips", "correction_payment", "reimbursement", "personal_note",
]


def _name_parts(config_key: str) -> tuple:
    """
    Convert config key "last, first" to (last_name, first_name) for Gusto.
    Capitalize properly.
    """
    parts = config_key.split(",", 1)
    last = parts[0].strip().title()
    first = parts[1].strip().title() if len(parts) > 1 else ""
    return last, first


def build_gusto_rows(employee_hours: dict, tip_map: dict, tip_rate: float,
                     manager_payouts: dict, meire_commission: float,
                     danielle_sm_hours: float, danielle_sm_rate: float,
                     sick_hours_map: dict, cfg: dict,
                     cash_tips_map: dict = None) -> list:
    """
    Build Gusto CSV rows for all employees.

    Args:
        employee_hours: {name: {regular, ot, total}} — active employees
        tip_map: {name: tip_amount}
        tip_rate: Tip $/hr rate
        manager_payouts: {name: payout_amount}
        meire_commission: Commission $ for Meire
        danielle_sm_hours: Social media hours for Danielle
        danielle_sm_rate: SM $/hr rate
        sick_hours_map: {name: sick_hours}
        cfg: Full config dict
        cash_tips_map: {name: cash_tip_amount} — optional cash tips per employee

    Returns: list of dicts with GUSTO_COLUMNS keys
    """
    if cash_tips_map is None:
        cash_tips_map = {}
    wages = _get_wages(cfg)
    payroll_cfg = cfg.get("payroll", {})
    rows = []

    # Track who we've added
    added = set()

    # All employees from config (sorted by last name)
    all_employees = sorted(wages.keys())

    for emp in all_employees:
        master = wages[emp]
        last, first = _name_parts(emp)
        gusto_id = str(master.get("gusto_id", ""))
        emp_type = master.get("type", "hourly")

        hours = employee_hours.get(emp, {})
        has_hours = hours.get("total", 0) > 0

        row = {
            "last_name": last,
            "first_name": first,
            "title": None,
            "gusto_employee_id": gusto_id,
            "regular_hours": None,
            "overtime_hours": None,
            "double_overtime_hours": None,
            "sick_hours": None,
            "bonus": None,
            "commission": None,
            "paycheck_tips": None,
            "cash_tips": None,
            "correction_payment": None,
            "reimbursement": None,
            "personal_note": None,
        }

        if emp_type == "owner":
            row["regular_hours"] = 40.0
        elif emp_type == "manager":
            row["regular_hours"] = 50.0
            # Manager OT payout as bonus
            payout = manager_payouts.get(emp, 0)
            if payout > 0:
                row["bonus"] = payout
        elif has_hours:
            row["regular_hours"] = hours.get("regular", 0)
            ot = hours.get("ot", 0)
            row["overtime_hours"] = ot if ot > 0 else 0
        else:
            # Inactive — show dashes (represented as "-" in Gusto)
            row["regular_hours"] = "-"
            row["overtime_hours"] = "-"

        # Sick hours
        sick = sick_hours_map.get(emp, 0)
        if sick > 0:
            row["sick_hours"] = sick

        # Tips
        tips = tip_map.get(emp, 0)
        if tips > 0:
            row["paycheck_tips"] = tips
            row["personal_note"] = f"Tips ${tip_rate:.2f} per hour"

        # Cash tips
        cash = cash_tips_map.get(emp, 0)
        if cash > 0:
            row["cash_tips"] = cash

        # Danielle social media bonus
        if emp == "cohen, danielle" and danielle_sm_hours > 0:
            sm_bonus = round(danielle_sm_hours * danielle_sm_rate, 2)
            existing_bonus = row.get("bonus") or 0
            try:
                existing_bonus = float(existing_bonus)
            except (ValueError, TypeError):
                existing_bonus = 0
            row["bonus"] = round(existing_bonus + sm_bonus, 2)
            note = row.get("personal_note") or ""
            sm_note = f"SM {danielle_sm_hours}hrs@${danielle_sm_rate:.0f}"
            row["personal_note"] = f"{note}; {sm_note}" if note else sm_note

        # Clean up: 0 → None for optional fields
        for field in ("overtime_hours", "bonus", "commission", "paycheck_tips",
                       "cash_tips", "sick_hours"):
            if row.get(field) == 0:
                row[field] = None

        rows.append(row)
        added.add(emp)

    # Add Meire (not in wages config)
    meire_cfg = payroll_cfg.get("meire", {})
    if meire_cfg:
        meire_row = {
            "last_name": meire_cfg.get("last_name", "Medeiros"),
            "first_name": meire_cfg.get("first_name", "Meire"),
            "title": None,
            "gusto_employee_id": meire_cfg.get("gusto_id", ""),
            "regular_hours": meire_cfg.get("hours", 40),
            "overtime_hours": None,
            "double_overtime_hours": None,
            "sick_hours": None,
            "bonus": None,
            "commission": meire_commission if meire_commission > 0 else None,
            "paycheck_tips": None,
            "cash_tips": None,
            "correction_payment": None,
            "reimbursement": None,
            "personal_note": None,
        }
        rows.append(meire_row)

    return rows


def rows_to_csv(rows: list) -> str:
    """Convert Gusto rows to CSV string."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=GUSTO_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


# ── Payroll State Persistence ──────────────────────────────────────

def save_payroll_state(week_end: str, state: dict):
    """Save payroll state for a specific week."""
    _PAYROLL_DIR.mkdir(parents=True, exist_ok=True)
    path = _PAYROLL_DIR / f"{week_end}.json"
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def load_payroll_state(week_end: str) -> dict:
    """Load saved payroll state for a specific week, or empty dict."""
    path = _PAYROLL_DIR / f"{week_end}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


# ── Week Helpers ───────────────────────────────────────────────────

def get_week_bounds(offset: int = 0) -> tuple:
    """
    Get Monday-Sunday bounds for a week.
    offset=0 → this week, offset=1 → last week, etc.

    Returns: (monday_date, sunday_date) as datetime.date objects
    """
    today = datetime.now().date()
    # Monday of this week
    monday = today - timedelta(days=today.weekday())
    # Apply offset
    monday = monday - timedelta(weeks=offset)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def date_to_blob_format(d) -> str:
    """Convert date to YYYYMMDD format for Azure blob."""
    return d.strftime("%Y%m%d")
