"""Cash Flow Forecast Engine.

Projects daily cash in/out for the next N days using:
- Revenue predictions from the forecast model (or historical day-of-week averages)
- Labor costs from historical Toast POS data
- Vendor spend from Notion price entries (or estimated from food-cost ratios)
- Upcoming catering orders from the catering dashboard

All external data calls are wrapped in try/except with sensible fallbacks
so the forecast degrades gracefully when any source is unavailable.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Default expense ratios when real data is unavailable
_DEFAULT_LABOR_PCT = 0.24   # 24% of revenue
_DEFAULT_FOOD_COST_PCT = 0.24  # 24% of revenue (COGS)
_BALANCE_THRESHOLD = 5000.0


# ══════════════════════════════════════════════════════════════════════════════
# Historical Revenue from Timeseries
# ══════════════════════════════════════════════════════════════════════════════


def _get_historical_daily(lookback_days: int = 28) -> dict:
    """Pull last N days of revenue data from the forecast timeseries.

    Returns:
        {
            'total_revenue': float,
            'days': int,
            'avg_daily_revenue': float,
            'dow_averages': {0: float, ..., 6: float},  # Mon=0 ... Sun=6
            'total_labor': float,  # estimated from ratio if not available
            'avg_daily_labor': float,
        }
    """
    result = {
        "total_revenue": 0.0,
        "days": 0,
        "avg_daily_revenue": 0.0,
        "dow_averages": {},
        "total_labor": 0.0,
        "avg_daily_labor": 0.0,
    }

    try:
        from forecast.timeseries import get_timeseries
        df = get_timeseries()
        if df is None or df.empty:
            logger.warning("Timeseries empty; using zero baseline")
            return result

        # Filter to last N days
        cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        recent = df[df["date"] >= cutoff].copy()

        if recent.empty:
            logger.warning("No data in last %d days", lookback_days)
            return result

        total_rev = float(recent["revenue_total"].sum())
        num_days = len(recent)

        # Day-of-week averages
        recent["dow"] = recent["date"].apply(
            lambda d: datetime.strptime(d, "%Y-%m-%d").weekday()
        )
        dow_groups = recent.groupby("dow")["revenue_total"].mean()
        dow_averages = {int(k): round(float(v), 2) for k, v in dow_groups.items()}

        result["total_revenue"] = round(total_rev, 2)
        result["days"] = num_days
        result["avg_daily_revenue"] = round(total_rev / num_days, 2) if num_days > 0 else 0.0
        result["dow_averages"] = dow_averages

        # Labor estimate: use expense ratio since labor isn't in timeseries
        labor_total = total_rev * _DEFAULT_LABOR_PCT
        result["total_labor"] = round(labor_total, 2)
        result["avg_daily_labor"] = round(labor_total / num_days, 2) if num_days > 0 else 0.0

    except Exception as e:
        logger.warning("Failed to load historical daily data: %s", e)

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Vendor Spend from Notion
# ══════════════════════════════════════════════════════════════════════════════


def _get_avg_weekly_vendor() -> tuple[float, bool]:
    """Fetch average weekly vendor spend from Notion price entries.

    Returns:
        (avg_weekly_spend, is_real_data)
    """
    try:
        prices_db = os.environ.get("NOTION_PRICES_DB_ID", "")
        items_db = os.environ.get("NOTION_ITEMS_DB_ID", "")
        if not prices_db or not items_db:
            logger.info("Notion DB IDs not set; skipping vendor spend lookup")
            return 0.0, False

        from vendor_prices.tools.notion_sync import get_spending_summary
        summary = get_spending_summary(prices_db, items_db, max_weeks=8)

        weekly_totals = summary.get("weekly_totals", [])
        if not weekly_totals:
            return 0.0, False

        totals = [w.get("total", 0) for w in weekly_totals if w.get("total", 0) > 0]
        if not totals:
            return 0.0, False

        avg = sum(totals) / len(totals)
        return round(avg, 2), True

    except Exception as e:
        logger.warning("Failed to get vendor spend from Notion: %s", e)
        return 0.0, False


# ══════════════════════════════════════════════════════════════════════════════
# Upcoming Catering
# ══════════════════════════════════════════════════════════════════════════════


def _get_upcoming_catering(days_ahead: int = 14) -> list[dict]:
    """Fetch upcoming catering orders from the catering dashboard.

    Returns list of {'date': 'YYYY-MM-DD', 'name': str, 'subtotal': float}.
    """
    catering_orders = []
    today = datetime.now().date()
    cutoff = today + timedelta(days=days_ahead)

    try:
        from catering import get_catering_dashboard_data
        data = get_catering_dashboard_data()
        recent = data.get("recent_orders", [])

        for order in recent:
            order_date_str = order.get("date", "")
            if not order_date_str:
                continue
            try:
                order_date = datetime.strptime(order_date_str, "%Y-%m-%d").date()
            except ValueError:
                # Try alternate formats
                try:
                    order_date = datetime.strptime(order_date_str, "%m/%d/%Y").date()
                except ValueError:
                    continue

            if today <= order_date <= cutoff:
                catering_orders.append({
                    "date": order_date.strftime("%Y-%m-%d"),
                    "name": order.get("customer", "") or order.get("name", ""),
                    "subtotal": float(order.get("subtotal", 0) or order.get("revenue", 0) or 0),
                    "platform": order.get("platform", ""),
                })

    except Exception as e:
        logger.warning("Failed to get upcoming catering: %s", e)

    # Also try Notion upcoming orders directly
    try:
        from catering.notion import fetch_upcoming_orders
        today_str = today.strftime("%Y-%m-%d")
        notion_orders = fetch_upcoming_orders(from_date=today_str)

        existing_dates_names = {(o["date"], o["name"]) for o in catering_orders}

        for o in notion_orders:
            od = o.get("date", "")
            name = o.get("name", "")
            if not od:
                continue
            try:
                order_date = datetime.strptime(od, "%Y-%m-%d").date()
            except ValueError:
                continue
            if today <= order_date <= cutoff and (od, name) not in existing_dates_names:
                catering_orders.append({
                    "date": od,
                    "name": name,
                    "subtotal": float(o.get("subtotal", 0) or 0),
                    "platform": o.get("platform", ""),
                })

    except Exception as e:
        logger.debug("Notion upcoming catering not available: %s", e)

    return catering_orders


# ══════════════════════════════════════════════════════════════════════════════
# Revenue Predictions per Day
# ══════════════════════════════════════════════════════════════════════════════


def _get_daily_predictions(days_ahead: int, dow_averages: dict) -> list[dict]:
    """Get revenue prediction for each future day.

    Tries the forecast model first; falls back to day-of-week averages.

    Returns list of {'date': str, 'revenue': float, 'source': 'forecast'|'estimate'}.
    """
    predictions = []
    today = datetime.now()
    forecast_available = False

    for i in range(1, days_ahead + 1):
        target = today + timedelta(days=i)
        date_str = target.strftime("%Y-%m-%d")
        dow = target.weekday()

        revenue = None
        source = "estimate"

        # Try forecast model
        try:
            from forecast.today_data import generate_prediction_for_date
            result = generate_prediction_for_date(target)
            if result and result.get("predicted_revenue"):
                pred_rev = result["predicted_revenue"]
                if isinstance(pred_rev, dict):
                    revenue = float(pred_rev.get("total", 0))
                else:
                    revenue = float(pred_rev)
                if revenue > 0:
                    source = "forecast"
                    forecast_available = True
        except Exception as e:
            logger.debug("Forecast unavailable for %s: %s", date_str, e)

        # Fallback to day-of-week average
        if revenue is None or revenue <= 0:
            revenue = dow_averages.get(dow, 0.0)
            source = "estimate"

        predictions.append({
            "date": date_str,
            "revenue": round(revenue, 2),
            "source": source,
            "dow": dow,
        })

    return predictions, forecast_available


# ══════════════════════════════════════════════════════════════════════════════
# Payroll Day Detection
# ══════════════════════════════════════════════════════════════════════════════


def _is_payroll_day(date: datetime, reference_payroll: datetime | None = None) -> bool:
    """Check if a date falls on a biweekly payroll day (every other Friday).

    Uses a reference payroll date to establish the cadence.
    Default reference: a known payroll Friday.
    """
    if reference_payroll is None:
        # Use a known biweekly payroll Friday as anchor
        reference_payroll = datetime(2026, 3, 13)  # a Friday

    if date.weekday() != 4:  # Not a Friday
        return False

    delta_days = abs((date - reference_payroll).days)
    return delta_days % 14 == 0


# ══════════════════════════════════════════════════════════════════════════════
# Main Forecast Engine
# ══════════════════════════════════════════════════════════════════════════════


def compute_cashflow_forecast(
    days_ahead: int = 14,
    starting_balance: float = 0.0,
    balance_threshold: float = _BALANCE_THRESHOLD,
) -> dict:
    """Compute a daily cash flow projection for the next N days.

    Args:
        days_ahead: Number of days to project (default 14)
        starting_balance: Current cash position
        balance_threshold: Alert when balance drops below this

    Returns:
        Complete forecast dict with daily_projection, totals, danger_zones,
        and assumptions. See module docstring for full schema.
    """
    logger.info("Computing cash flow forecast for %d days ahead", days_ahead)

    # ── 1. Historical averages (last 4 weeks) ──
    historical = _get_historical_daily(lookback_days=28)
    avg_daily_revenue = historical["avg_daily_revenue"]
    dow_averages = historical["dow_averages"]
    avg_daily_labor = historical["avg_daily_labor"]
    total_revenue_hist = historical["total_revenue"]
    hist_days = historical["days"]

    # Compute avg weekly labor
    avg_weekly_labor = avg_daily_labor * 7 if avg_daily_labor > 0 else 0.0

    # ── 2. Vendor spend ──
    avg_weekly_vendor, vendor_is_real = _get_avg_weekly_vendor()
    if avg_weekly_vendor <= 0 and avg_daily_revenue > 0:
        # Estimate vendor spend as food cost % of revenue
        avg_weekly_vendor = round(avg_daily_revenue * 7 * _DEFAULT_FOOD_COST_PCT, 2)
        vendor_is_real = False

    avg_daily_vendor = round(avg_weekly_vendor / 7, 2) if avg_weekly_vendor > 0 else 0.0

    # ── 3. Revenue predictions ──
    predictions, forecast_available = _get_daily_predictions(days_ahead, dow_averages)

    # ── 4. Upcoming catering ──
    catering_orders = _get_upcoming_catering(days_ahead)
    # Index by date for fast lookup
    catering_by_date = defaultdict(list)
    for co in catering_orders:
        catering_by_date[co["date"]].append(co)

    # ── 5. Payroll estimate ──
    # Estimate biweekly payroll: 2 weeks of labor cost
    biweekly_payroll = round(avg_weekly_labor * 2, 2) if avg_weekly_labor > 0 else 0.0

    # ── 6. Build daily projection ──
    daily_projection = []
    running_balance = starting_balance
    total_rev = 0.0
    total_exp = 0.0
    danger_zones = []
    today = datetime.now()

    for pred in predictions:
        date_str = pred["date"]
        target = datetime.strptime(date_str, "%Y-%m-%d")
        dow = pred["dow"]
        day_name = _DOW_NAMES[dow]
        date_display = target.strftime("%b %d")

        # Revenue
        revenue = pred["revenue"]

        # Catering (additive to projections — these are booked orders)
        day_catering = catering_by_date.get(date_str, [])
        catering_total = sum(c["subtotal"] for c in day_catering)

        # Expenses: labor + vendor
        labor_daily = round(avg_daily_labor, 2)
        vendor_daily = round(avg_daily_vendor, 2)

        # Payroll spike on payroll day
        is_payroll = _is_payroll_day(target)
        payroll_amount = None
        if is_payroll:
            payroll_amount = biweekly_payroll
            # On payroll day, the full biweekly payroll hits, so replace
            # the spread daily labor for this day with the lump sum
            labor_daily = round(biweekly_payroll, 2)

        total_expenses = round(labor_daily + vendor_daily, 2)

        # Net flow
        net_flow = round(revenue + catering_total - total_expenses, 2)
        running_balance = round(running_balance + net_flow, 2)

        # Track totals
        total_rev += revenue + catering_total
        total_exp += total_expenses

        # Danger zone check
        if running_balance < balance_threshold:
            danger_zones.append({
                "date": date_str,
                "date_display": date_display,
                "balance": running_balance,
                "shortfall": round(balance_threshold - running_balance, 2),
            })

        daily_projection.append({
            "date": date_str,
            "date_display": date_display,
            "day_of_week": day_name,
            "revenue_projected": revenue,
            "revenue_source": pred["source"],
            "labor_projected": labor_daily,
            "vendor_projected": vendor_daily,
            "catering_expected": catering_total,
            "catering_orders": day_catering,
            "total_expenses": total_expenses,
            "net_flow": net_flow,
            "running_balance": running_balance,
            "is_payroll_day": is_payroll,
            "payroll_amount": payroll_amount,
        })

    total_net = round(total_rev - total_exp, 2)
    ending_balance = round(starting_balance + total_net, 2)

    return {
        "daily_projection": daily_projection,
        "starting_balance": starting_balance,
        "ending_balance": ending_balance,
        "total_revenue": round(total_rev, 2),
        "total_expenses": round(total_exp, 2),
        "total_net": total_net,
        "danger_zones": danger_zones,
        "assumptions": {
            "avg_daily_revenue": avg_daily_revenue,
            "avg_weekly_labor": avg_weekly_labor,
            "avg_weekly_vendor": avg_weekly_vendor,
            "vendor_data_source": "notion" if vendor_is_real else "estimated",
            "balance_threshold": balance_threshold,
            "forecast_available": forecast_available,
            "lookback_days": 28,
            "hist_days_found": hist_days,
            "biweekly_payroll": biweekly_payroll,
        },
        "upcoming_catering": catering_orders,
    }
