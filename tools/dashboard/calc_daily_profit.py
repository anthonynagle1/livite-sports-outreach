"""
Calculate daily profit from Toast POS data.

Usage:
    python tools/calc_daily_profit.py                  # Yesterday
    python tools/calc_daily_profit.py 20260215          # Specific date
    python tools/calc_daily_profit.py 20260210 20260215 # Date range

Pulls data from Azure, calculates all automatable Tracker 2 columns,
and outputs a summary dict (or writes to Google Sheets if configured).
"""

import os
import sys
from datetime import datetime, timedelta

import yaml
import pandas as pd
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# Import the fetcher
sys.path.insert(0, os.path.dirname(__file__))
from fetch_toast_data import get_daily_data

# ─── Load config.yaml ───
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')

def _load_config():
    """Load business rules from config.yaml."""
    with open(_CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)

_cfg = _load_config()

MASTER_WAGES = _cfg.get('wages', {})
CHANNEL_MAP = _cfg.get('channel_map', {})
EMPLOYEE_ALIASES = _cfg.get('employee_aliases', {})
PAYROLL_TAX_RATE = _cfg.get('payroll_tax_rate', 3.00)
FOOD_COST_PCT = _cfg.get('food_cost_pct', 0.35)


def get_master_wage(employee_name: str) -> dict:
    """Look up employee in master wage table. Falls back to Toast wage if unknown."""
    key = employee_name.strip().lower()
    # Check alias table first
    key = EMPLOYEE_ALIASES.get(key, key)
    return MASTER_WAGES.get(key, None)


def calc_revenue(order_details: pd.DataFrame) -> dict:
    """
    Parse OrderDetails: filter voided, sum Amount by channel.
    Returns dict with toast_total and per-channel breakdowns.
    """
    od = order_details.copy()
    od = od[od['Voided'] != True]

    toast_total = od['Amount'].sum()

    # Uber discounts: Toast now auto-deducts BOGO from Amount. Add back
    # for profit calc so food cost and fee math stays consistent.
    uber_discount = 0.0
    if 'Discount Amount' in od.columns:
        uber_mask = od['Dining Options'].astype(str).str.contains('Uber', case=False, na=False)
        uber_discount = od.loc[uber_mask, 'Discount Amount'].fillna(0).sum()
    toast_total += uber_discount  # restore gross for profit calc
    bogo_discount = uber_discount

    # Channel breakdown
    channel_sums = od.groupby('Dining Options')['Amount'].sum().to_dict()
    channel_counts = od.groupby('Dining Options').size().to_dict()
    channels = {}
    unmapped = {}
    for toast_name, amount in channel_sums.items():
        tracker_name = CHANNEL_MAP.get(toast_name)
        if tracker_name:
            channels[tracker_name] = round(amount, 2)
        else:
            unmapped[toast_name] = round(amount, 2)

    # Online delivery order count (for Shipday fee calc)
    online_delivery_orders = channel_counts.get("Online Ordering - Delivery", 0)

    return {
        "toast_total": round(toast_total, 2),
        "channels": channels,
        "unmapped_channels": unmapped,
        "bogo_discount": round(bogo_discount, 2),
        "online_delivery_orders": online_delivery_orders,
    }


def calc_labor(time_entries: pd.DataFrame) -> dict:
    """
    Parse TimeEntries: apply master wages, handle salaried/owner employees.
    Returns labor breakdown.
    """
    te = time_entries.copy()

    hourly_records = []
    total_ot_pay = 0.0
    total_ot_hours = 0.0
    unknown_employees = []

    def _num(val, default=0.0):
        """Convert to float, treating NaN/None/empty as default."""
        if val is None:
            return default
        try:
            f = float(val)
            return default if f != f else f  # NaN check
        except (TypeError, ValueError):
            return default

    for _, row in te.iterrows():
        emp_name = str(row['Employee']).strip()
        master = get_master_wage(emp_name)

        regular_hours = _num(row.get('Regular Hours', 0))
        ot_hours = _num(row.get('Overtime Hours', 0))
        total_hours = _num(row.get('Total Hours', 0))
        toast_wage = _num(row.get('Wage', 0))

        if master is None:
            # Unknown employee — use Toast wage, flag for review
            unknown_employees.append(emp_name)
            wage = toast_wage
            reg_pay = regular_hours * wage
            ot_pay = ot_hours * wage * 1.5
            hourly_records.append({
                "employee": emp_name,
                "hours": total_hours,
                "regular_hours": regular_hours,
                "ot_hours": ot_hours,
                "wage": wage,
                "regular_pay": round(reg_pay, 2),
                "ot_pay": round(ot_pay, 2),
                "total_pay": round(reg_pay + ot_pay, 2),
                "source": "toast_fallback",
            })
            total_ot_pay += ot_pay
            total_ot_hours += ot_hours

        elif master["type"] == "owner":
            # Owners: skip — manual hours entered separately
            pass

        elif master["type"] == "manager":
            # Managers: 50-hr weekly OT threshold. Toast uses 40hr so we
            # reclassify ALL their daily hours as regular at 1x rate.
            # Weekly OT (if > 50hrs) is handled in aggregation.
            wage = master["wage"]
            reg_pay = total_hours * wage  # all hours at 1x
            ot_pay = 0
            hourly_records.append({
                "employee": emp_name,
                "hours": total_hours,
                "regular_hours": total_hours,
                "ot_hours": 0,
                "wage": wage,
                "regular_pay": round(reg_pay, 2),
                "ot_pay": 0,
                "total_pay": round(reg_pay, 2),
                "source": "master",
                "role": "manager",
            })

        else:
            # Hourly: use master wage, Toast hours
            wage = master["wage"]
            reg_pay = regular_hours * wage
            ot_pay = ot_hours * wage * 1.5
            hourly_records.append({
                "employee": emp_name,
                "hours": total_hours,
                "regular_hours": regular_hours,
                "ot_hours": ot_hours,
                "wage": wage,
                "regular_pay": round(reg_pay, 2),
                "ot_pay": round(ot_pay, 2),
                "total_pay": round(reg_pay + ot_pay, 2),
                "source": "master",
            })
            total_ot_pay += ot_pay
            total_ot_hours += ot_hours

    # Aggregate hourly
    hourly_regular_pay = sum(r["regular_pay"] for r in hourly_records)
    hourly_ot_pay = sum(r["ot_pay"] for r in hourly_records)
    hourly_total_pay = hourly_regular_pay + hourly_ot_pay
    hourly_total_hours = sum(r["hours"] for r in hourly_records)

    # Payroll tax: all hours × $3.00
    payroll_tax = round(hourly_total_hours * PAYROLL_TAX_RATE, 2)

    # Total labor = hourly pay + payroll tax
    total_labor = round(hourly_total_pay + payroll_tax, 2)

    return {
        "hourly_records": hourly_records,
        "hourly_regular_pay": round(hourly_regular_pay, 2),
        "hourly_ot_pay": round(hourly_ot_pay, 2),
        "hourly_total_pay": round(hourly_total_pay, 2),
        "hourly_total_hours": round(hourly_total_hours, 2),
        "ot_pay_total": round(total_ot_pay, 2),
        "ot_hours_total": round(total_ot_hours, 2),
        "payroll_tax": payroll_tax,
        "total_labor": total_labor,
        "total_hours": round(hourly_total_hours, 2),
        "unknown_employees": unknown_employees,
    }


def calc_fees(payment_details: pd.DataFrame) -> dict:
    """Parse PaymentDetails for card processing fees."""
    pd_df = payment_details.copy()
    tds_fees = pd_df['V/MC/D Fees'].sum()
    return {
        "toast_tds_fees": round(tds_fees, 2),
    }


def calc_daily_profit(date: datetime = None) -> dict:
    """
    Full daily profit calculation.
    Pulls data from Azure, computes all automatable fields.
    Returns a dict matching Tracker 2 columns.
    """
    if date is None:
        date = datetime.now() - timedelta(days=1)

    date_str = date.strftime("%Y%m%d")
    display_date = date.strftime("%m/%d/%Y")
    print(f"\n{'='*50}")
    print(f"  Daily Profit Calc: {display_date}")
    print(f"{'='*50}\n")

    # Step 1: Fetch data from Azure
    print("Fetching data from Azure...")
    data = get_daily_data(date)

    if 'OrderDetails' not in data:
        print("ERROR: OrderDetails not found. Cannot proceed.")
        return None

    # Step 2: Revenue
    print("\nCalculating revenue...")
    revenue = calc_revenue(data['OrderDetails'])
    print(f"  Toast Total: ${revenue['toast_total']:,.2f}")
    for ch, amt in sorted(revenue['channels'].items(), key=lambda x: -x[1]):
        print(f"    {ch}: ${amt:,.2f}")
    if revenue['unmapped_channels']:
        print(f"  UNMAPPED channels: {revenue['unmapped_channels']}")

    # Step 3: Labor
    print("\nCalculating labor...")
    if 'TimeEntries' in data:
        labor = calc_labor(data['TimeEntries'])
        print(f"  Hourly pay: ${labor['hourly_total_pay']:,.2f} ({labor['hourly_total_hours']:.1f} hrs)")
        print(f"  OT pay: ${labor['ot_pay_total']:,.2f} ({labor['ot_hours_total']:.1f} hrs)")
        print(f"  Payroll tax: ${labor['payroll_tax']:,.2f}")
        print(f"  Total labor: ${labor['total_labor']:,.2f}")
        if labor['unknown_employees']:
            print(f"  WARNING: Unknown employees (using Toast wage): {labor['unknown_employees']}")
    else:
        print("  WARNING: TimeEntries not found. Labor set to 0.")
        labor = {
            "hourly_total_pay": 0, "ot_pay_total": 0,
            "ot_hours_total": 0, "payroll_tax": 0, "total_labor": 0,
            "total_hours": 0, "hourly_total_hours": 0,
            "unknown_employees": [],
        }

    # Step 4: Fees
    print("\nCalculating fees...")
    if 'PaymentDetails' in data:
        fees = calc_fees(data['PaymentDetails'])
        print(f"  Toast TDS fees: ${fees['toast_tds_fees']:,.2f}")
    else:
        fees = {"toast_tds_fees": 0}

    # Step 5: Derived calculations
    toast_total = revenue['toast_total']
    bogo_discount = revenue.get('bogo_discount', 0)
    uber_cofund = round(bogo_discount * 0.40, 2)
    your_bogo_cost = round(bogo_discount * 0.60, 2)
    # Forkable and EzCater are manual — use 0 as placeholder
    total_sales = toast_total + uber_cofund  # + forkable + ezcater (manual)
    food_cost = round(total_sales * FOOD_COST_PCT, 2)

    # FTEs and rates (using auto hours only — excludes manual owner hours)
    total_hours = labor['total_hours']
    ftes = round(total_hours / 8, 2) if total_hours > 0 else 0
    blended_rate = round(labor['total_labor'] / total_hours, 2) if total_hours > 0 else 0

    # ─── Build Tracker 2 row ───
    tracker_row = {
        # Col A: Date
        "Date": display_date,
        # Col D: Toast Total
        "Toast Total": toast_total,
        # Col E: Labor (hourly + Zadith flat + payroll tax)
        "Labor": labor['total_labor'],
        # Col F: OT Labor
        "OT Labor": labor['ot_pay_total'],
        # Col G: OT Hours
        "OT Hours": labor['ot_hours_total'],
        # Col M: Food Cost
        "Food Cost": food_cost,
        # Cols P-Y: Channel breakdown
        "DD Delivery": revenue['channels'].get("DD Delivery", 0),
        "DD Takeout": revenue['channels'].get("DD Takeout", 0),
        "GH Takeout": revenue['channels'].get("GH Takeout", 0),
        "Online": revenue['channels'].get("Online", 0),
        "Phone": revenue['channels'].get("Phone", 0),
        "Online Ordering - Delivery": revenue['channels'].get("Online Ordering - Delivery", 0),
        "To Go": revenue['channels'].get("To Go", 0),
        "Uber Delivery": revenue['channels'].get("Uber Delivery", 0),
        "Uber Takeout": revenue['channels'].get("Uber Takeout", 0),
        "No Dining": revenue['channels'].get("No Dining", 0),
        # Col Z: Total Labor Hours (auto portion only)
        "TOTAL LABOR HOURS": total_hours,
        # Col AA: Payroll Taxes
        "Payroll Taxes": labor['payroll_tax'],
        # Col AC: FTEs
        "FTEs": ftes,
        # Col AH: Blended Rate
        "Blended Rate": blended_rate,
        # Col AY: Date (weather join key)
        "Date_weather": display_date,
        # Col AZ: Location
        "Location": "Brookline, MA",
        # Toast TDS fees (for service fee calc)
        "Toast TDS Fees": fees['toast_tds_fees'],
        # BOGO fields (Uber discount split)
        "BOGO Discount": bogo_discount,
        "Uber Co-fund": uber_cofund,
        "Your BOGO Cost": your_bogo_cost,
        # Online delivery order count (for Shipday fee calc)
        "Online Delivery Orders": revenue.get('online_delivery_orders', 0),
        # Per-employee labor breakdown
        "hourly_records": labor.get('hourly_records', []),
    }

    # Print summary
    print(f"\n{'='*50}")
    print(f"  SUMMARY: {display_date}")
    print(f"{'='*50}")
    print(f"  Toast Total:    ${toast_total:,.2f}")
    print(f"  Total Labor:    ${labor['total_labor']:,.2f}")
    print(f"  Food Cost:      ${food_cost:,.2f}")
    print(f"  TDS Fees:       ${fees['toast_tds_fees']:,.2f}")
    if bogo_discount > 0:
        print(f"  BOGO Discount:  ${bogo_discount:,.2f}")
        print(f"    Uber Co-fund: ${uber_cofund:,.2f} (40%)")
        print(f"    Your Cost:    ${your_bogo_cost:,.2f} (60%)")
    print(f"  Hours Worked:   {total_hours:.1f}")
    print(f"  FTEs:           {ftes:.1f}")
    print(f"  Blended Rate:   ${blended_rate:.2f}/hr")
    if labor.get('unknown_employees'):
        print(f"\n  WARNINGS:")
        print(f"    Unknown employees: {labor['unknown_employees']}")
    if revenue['unmapped_channels']:
        print(f"    Unmapped channels: {revenue['unmapped_channels']}")
    print()

    return tracker_row


if __name__ == "__main__":
    args = sys.argv[1:]

    if len(args) == 0:
        result = calc_daily_profit()
    elif len(args) == 1:
        dt = datetime.strptime(args[0], "%Y%m%d")
        result = calc_daily_profit(dt)
    elif len(args) == 2:
        start = datetime.strptime(args[0], "%Y%m%d")
        end = datetime.strptime(args[1], "%Y%m%d")
        results = []
        current = start
        while current <= end:
            row = calc_daily_profit(current)
            if row:
                results.append(row)
            current += timedelta(days=1)

        if results:
            df = pd.DataFrame(results)
            out_path = os.path.join(os.path.dirname(__file__), '..', '.tmp', 'tracker_output.csv')
            df.to_csv(out_path, index=False)
            print(f"\nSaved {len(results)} days to {out_path}")
    else:
        print("Usage: python tools/calc_daily_profit.py [YYYYMMDD] [YYYYMMDD]")
        sys.exit(1)
