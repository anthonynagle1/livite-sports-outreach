"""Labor, staffing, and shift metrics."""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

import pandas as pd
import numpy as np
from statistics import median
from .utils import (
    _filter_voided_orders, _safe_numeric, _safe_div,
    parse_toast_datetime, _get_daypart, DAYPARTS,
    calc_labor, get_master_wage, FOOD_COST_PCT
)

def compute_labor_metrics(time_entries, order_details, revenue_total):
    """Compute comprehensive labor metrics.

    Uses calc_labor() for base calculations and adds staffing curves,
    daypart efficiency, OT detail, role breakdown, shift distributions,
    break violations, auto clockouts, and tip distribution.
    """
    try:
        te = time_entries.copy()
        od = _filter_voided_orders(order_details.copy())
        od['Amount'] = _safe_numeric(od, 'Amount')

        # ── Base labor from calc_daily_profit ──
        base_labor = calc_labor(te)
        total_labor = base_labor['total_labor']
        total_hours = base_labor['total_hours']
        total_orders = len(od)

        labor_pct = round(_safe_div(total_labor, revenue_total) * 100, 1)
        rev_per_labor_hr = round(_safe_div(revenue_total, total_hours), 2)
        orders_per_labor_hr = round(_safe_div(total_orders, total_hours), 2)
        ftes = round(_safe_div(total_hours, 8), 2)
        blended_rate = round(_safe_div(total_labor, total_hours), 2)

        # ── OT detail ──
        ot_detail = []
        for rec in base_labor['hourly_records']:
            if rec['ot_hours'] > 0:
                total_pay = rec['total_pay']
                ot_pct = round(_safe_div(rec['ot_pay'], total_pay) * 100, 1)
                ot_detail.append({
                    "employee": rec['employee'],
                    "role": "Unknown",  # TimeEntries has Job Title
                    "wage": rec['wage'],
                    "shift_hours": rec['hours'],
                    "reg_hrs": rec['regular_hours'],
                    "ot_hrs": rec['ot_hours'],
                    "ot_pay": rec['ot_pay'],
                    "ot_pct_of_pay": ot_pct,
                })

        # Enrich OT detail with Job Title from TimeEntries
        if 'Job Title' in te.columns:
            deduped = te.drop_duplicates('Employee')
            emp_roles = deduped.set_index(
                deduped['Employee'].str.strip()
            )['Job Title'].to_dict()
            for item in ot_detail:
                item['role'] = emp_roles.get(item['employee'], "Unknown")

        # ── Staffing by half-hour ──
        te['_in_dt'] = te['In Date'].apply(parse_toast_datetime)
        te['_out_dt'] = te['Out Date'].apply(parse_toast_datetime)
        od['_opened_dt'] = od['Opened'].apply(parse_toast_datetime)
        od['_hour'] = od['_opened_dt'].apply(lambda x: x.hour if x else None)

        staffing_by_halfhour = []
        te_valid = te[te['_in_dt'].notna() & te['_out_dt'].notna()]
        if len(te_valid) > 0:
            # Determine day boundaries from the data
            day_start = te_valid['_in_dt'].min().replace(hour=7, minute=0, second=0)
            day_end = day_start.replace(hour=22, minute=30)
            slot = day_start
            while slot <= day_end:
                slot_end = slot + timedelta(minutes=30)
                # Count staff on shift during this slot
                staff_count = len(te_valid[
                    (te_valid['_in_dt'] < slot_end) & (te_valid['_out_dt'] > slot)
                ])
                # Count orders in this slot
                order_count = len(od[
                    od['_opened_dt'].apply(
                        lambda x: x is not None and slot <= x < slot_end
                    )
                ])
                orders_per_staff = round(
                    _safe_div(order_count, staff_count), 2
                )
                staffing_by_halfhour.append({
                    "time": slot.strftime("%I:%M %p"),
                    "staff_count": staff_count,
                    "order_count": order_count,
                    "orders_per_staff": orders_per_staff,
                })
                slot = slot_end

        # ── Daypart efficiency ──
        daypart_efficiency = []
        try:
            for dp_start, dp_end, dp_name in DAYPARTS:
                dp_orders = od[od['_hour'].apply(
                    lambda h, _s=dp_start, _e=dp_end: h is not None and _s <= h < _e
                )]
                dp_revenue = round(dp_orders['Amount'].sum(), 2)
                dp_order_count = len(dp_orders)

                # Estimate labor cost for daypart: proportion of hours in that window
                dp_hours = 0
                for _, row in te_valid.iterrows():
                    shift_start = row['_in_dt']
                    shift_end = row['_out_dt']
                    if shift_start is None or shift_end is None:
                        continue
                    day_date = shift_start.date()
                    dp_window_start = datetime.combine(day_date, datetime.min.time()).replace(
                        hour=dp_start
                    )
                    dp_window_end = datetime.combine(day_date, datetime.min.time()).replace(
                        hour=dp_end
                    )
                    overlap_start = max(shift_start, dp_window_start)
                    overlap_end = min(shift_end, dp_window_end)
                    if overlap_start < overlap_end:
                        dp_hours += (overlap_end - overlap_start).total_seconds() / 3600.0

                dp_labor_cost = round(dp_hours * blended_rate, 2) if blended_rate > 0 else 0
                rev_per_labor_dollar = round(
                    _safe_div(dp_revenue, dp_labor_cost), 2
                )

                daypart_efficiency.append({
                    "daypart": dp_name,
                    "name": dp_name,
                    "hours": f"{dp_start}:00-{dp_end}:00",
                    "orders": dp_order_count,
                    "revenue": dp_revenue,
                    "labor_cost": dp_labor_cost,
                    "rev_per_labor_dollar": rev_per_labor_dollar,
                })
        except Exception as e:
            logger.warning("Daypart efficiency calculation failed: %s", e)

        # ── Role breakdown ──
        role_breakdown = []
        if 'Job Title' in te.columns:
            te['Total Hours'] = _safe_numeric(te, 'Total Hours')
            te['Total Pay'] = _safe_numeric(te, 'Total Pay')
            te['Wage'] = _safe_numeric(te, 'Wage')
            role_agg = te.groupby('Job Title').agg(
                headcount=('Employee', 'nunique'),
                hours=('Total Hours', 'sum'),
            ).reset_index()

            # Calculate cost using master wages where possible
            for _, row in role_agg.iterrows():
                role_employees = te[te['Job Title'] == row['Job Title']]
                role_cost = 0
                for _, emp_row in role_employees.iterrows():
                    emp_name = str(emp_row['Employee']).strip()
                    master = get_master_wage(emp_name)
                    emp_hours = float(emp_row.get('Total Hours', 0) or 0)
                    if master and master['type'] == 'owner':
                        role_cost += emp_hours * master.get('replacement_wage', 0)
                    elif master and master.get('wage'):
                        role_cost += emp_hours * master['wage']
                    else:
                        toast_wage = float(emp_row.get('Wage', 0) or 0)
                        role_cost += emp_hours * toast_wage

                role_breakdown.append({
                    "role": row['Job Title'],
                    "headcount": int(row['headcount']),
                    "hours": round(row['hours'], 2),
                    "cost": round(role_cost, 2),
                    "pct_total": round(_safe_div(role_cost, total_labor) * 100, 1),
                })
            role_breakdown.sort(key=lambda x: x['cost'], reverse=True)

        # ── Shift distribution ──
        te['_shift_hours'] = _safe_numeric(te, 'Total Hours')
        shift_hours = te['_shift_hours']
        shift_distribution = {
            "under_4h": int((shift_hours < 4).sum()),
            "4_6h": int(((shift_hours >= 4) & (shift_hours < 6)).sum()),
            "6_8h": int(((shift_hours >= 6) & (shift_hours < 8)).sum()),
            "8_10h": int(((shift_hours >= 8) & (shift_hours < 10)).sum()),
            "over_10h": int((shift_hours >= 10).sum()),
        }

        avg_shift_length = round(shift_hours.mean(), 2) if len(shift_hours) > 0 else 0
        median_shift_length = round(float(shift_hours.median()), 2) if len(shift_hours) > 0 else 0

        # ── Long shifts without clocked break (informational only) ──
        # Note: Livite uses untimed breaks — employees take breaks freely
        # without clocking out. This tracks long shifts for awareness only.
        break_violations = []
        te['Unpaid Break Time'] = _safe_numeric(te, 'Unpaid Break Time')
        te['Paid Break Time'] = _safe_numeric(te, 'Paid Break Time')
        te['_total_break'] = te['Unpaid Break Time'] + te['Paid Break Time']
        long_shifts = te[te['_shift_hours'] > 6]
        for _, row in long_shifts.iterrows():
            total_break_hrs = row['_total_break']
            if total_break_hrs < 0.5:
                break_violations.append({
                    "employee": str(row['Employee']).strip(),
                    "hours": round(row['_shift_hours'], 2),
                    "break_time": round(total_break_hrs * 60, 1),
                })

        # ── Auto clockouts ──
        auto_clockouts = []
        if 'Auto Clock-out' in te.columns:
            auto_co = te[te['Auto Clock-out'].astype(str).str.strip().str.lower().isin(
                ['true', '1', 'yes']
            )]
            auto_clockouts = [str(row['Employee']).strip() for _, row in auto_co.iterrows()]

        # ── Tip distribution ──
        tip_distribution = []
        for col in ['Cash Tips Declared', 'Non Cash Tips', 'Total Tips', 'Tips Withheld']:
            te[col] = _safe_numeric(te, col)

        emp_tips = te.groupby('Employee').agg(
            cash_tips=('Cash Tips Declared', 'sum'),
            non_cash_tips=('Non Cash Tips', 'sum'),
            total_tips=('Total Tips', 'sum'),
            withheld=('Tips Withheld', 'sum'),
        ).reset_index()
        emp_tips = emp_tips[emp_tips['total_tips'] > 0].sort_values('total_tips', ascending=False)
        for _, row in emp_tips.iterrows():
            tip_distribution.append({
                "employee": str(row['Employee']).strip(),
                "cash_tips": round(row['cash_tips'], 2),
                "non_cash_tips": round(row['non_cash_tips'], 2),
                "total_tips": round(row['total_tips'], 2),
                "withheld": round(row['withheld'], 2),
            })

        # ── Employee roster (full detail per employee) ──
        employee_roster = []
        for _, row in te.iterrows():
            emp_name = str(row['Employee']).strip()
            master = get_master_wage(emp_name)
            wage_source = "master" if master else "toast"
            if master and master['type'] == 'owner':
                effective_wage = master.get('replacement_wage', 0)
            elif master and master.get('wage'):
                effective_wage = master['wage']
            else:
                effective_wage = float(row.get('Wage', 0) or 0)

            employee_roster.append({
                "employee": emp_name,
                "job_title": str(row.get('Job Title', '')).strip(),
                "in_date": str(row.get('In Date', '')),
                "out_date": str(row.get('Out Date', '')),
                "total_hours": round(float(row.get('Total Hours', 0) or 0), 2),
                "regular_hours": round(float(row.get('Regular Hours', 0) or 0), 2),
                "overtime_hours": round(float(row.get('Overtime Hours', 0) or 0), 2),
                "payable_hours": round(float(row.get('Payable Hours', 0) or 0), 2),
                "toast_wage": round(float(row.get('Wage', 0) or 0), 2),
                "effective_wage": effective_wage,
                "wage_source": wage_source,
                "employee_type": master.get('type', 'unknown') if master else "unknown",
                "total_pay": round(float(row.get('Total Pay', 0) or 0), 2),
                "cash_tips": round(float(row.get('Cash Tips Declared', 0) or 0), 2),
                "non_cash_tips": round(float(row.get('Non Cash Tips', 0) or 0), 2),
                "auto_clockout": str(row.get('Auto Clock-out', '')).strip().lower() in (
                    'true', '1', 'yes'
                ),
            })

        return {
            **base_labor,
            "labor_pct": labor_pct,
            "rev_per_labor_hr": rev_per_labor_hr,
            "orders_per_labor_hr": orders_per_labor_hr,
            "ftes": ftes,
            "blended_rate": blended_rate,
            "ot_detail": ot_detail,
            "staffing_by_halfhour": staffing_by_halfhour,
            "daypart_efficiency": daypart_efficiency,
            "role_breakdown": role_breakdown,
            "shift_distribution": shift_distribution,
            "avg_shift_length": avg_shift_length,
            "median_shift_length": median_shift_length,
            "break_violations": break_violations,
            "auto_clockouts": auto_clockouts,
            "tip_distribution": tip_distribution,
            "employee_roster": employee_roster,
        }
    except Exception as e:
        return {
            "total_labor": 0, "total_hours": 0,
            "labor_pct": 0, "rev_per_labor_hr": 0,
            "orders_per_labor_hr": 0, "ftes": 0, "blended_rate": 0,
            "ot_detail": [], "staffing_by_halfhour": [],
            "daypart_efficiency": [], "role_breakdown": [],
            "shift_distribution": {}, "avg_shift_length": 0,
            "median_shift_length": 0, "break_violations": [],
            "auto_clockouts": [], "tip_distribution": [],
            "employee_roster": [],
            "_error": str(e),
        }


# ═══════════════════════════════════════════════════════════════
#  10. PAYMENT METRICS
# ═══════════════════════════════════════════════════════════════
