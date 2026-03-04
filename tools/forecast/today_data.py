"""Today's operational prediction engine.

Generates a granular daily forecast with:
- Weather-aware revenue prediction + weather multiplier
- Hourly distribution curve from 4-week rolling average
- Peak detection and daypart breakdown
- Scheduled catering from Notion
- Full daily P&L using real expense ratios
- Explainable predictions (step-by-step reasoning)
- This-week context (actuals + remaining forecasts)
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DOW_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Daypart definitions (matches tools/metrics/utils.py)
_DAYPARTS = [
    (7, 11, "Morning"),
    (11, 14, "Lunch"),
    (14, 17, "Afternoon"),
    (17, 21, "Dinner"),
    (21, 23, "Late"),
]

# Average check size for cover estimation
_AVG_CHECK_SIZE = 18.50


# ══════════════════════════════════════════════════════════════════════════════
# Weather
# ══════════════════════════════════════════════════════════════════════════════


def _get_weather():
    """Fetch today's weather forecast."""
    try:
        from fetch_weather_data import get_today_weather, compute_weather_multiplier
        weather = get_today_weather()
        if weather:
            mult, reasons = compute_weather_multiplier(weather)
            weather["multiplier"] = mult
            weather["multiplier_reasons"] = reasons
        return weather
    except Exception as e:
        logger.warning("Failed to fetch weather: %s", e)
        return None


def _get_weather_for_date(date_str):
    """Fetch weather for a specific date (today or upcoming)."""
    try:
        from fetch_weather_data import get_forecast_weather, compute_weather_multiplier
        weather = get_forecast_weather(date_str)
        if weather:
            mult, reasons = compute_weather_multiplier(weather)
            weather["multiplier"] = mult
            weather["multiplier_reasons"] = reasons
        return weather
    except Exception as e:
        logger.warning("Failed to fetch weather for %s: %s", date_str, e)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Hourly Distribution (from 4-week rolling average)
# ══════════════════════════════════════════════════════════════════════════════


def _build_hourly_curve(today):
    """Build expected hourly revenue distribution from 4-week rolling average.

    Reuses compute_quarter_hourly_4wra() from daily_dashboard.py,
    then aggregates 15-min slots into hourly buckets.

    Returns:
        (hourly: list of dicts, quarter_hourly: dict, weeks_found: int)
    """
    try:
        from daily_dashboard import compute_quarter_hourly_4wra
        from fetch_toast_data import list_available_dates
        available = set(list_available_dates())
    except Exception as e:
        logger.warning("Could not load 4WRA dependencies: %s", e)
        return [], {}, 0

    try:
        slot_4wra, weeks_found = compute_quarter_hourly_4wra(today, available)
    except Exception as e:
        logger.warning("4WRA computation failed: %s", e)
        return [], {}, 0

    if not slot_4wra:
        return [], {}, 0

    # Aggregate 15-min slots into hourly buckets
    hourly_rev = defaultdict(float)
    hourly_orders = defaultdict(float)

    for slot_key, vals in slot_4wra.items():
        hour = int(slot_key.split(":")[0])
        hourly_rev[hour] += vals.get("avg_revenue", 0)
        hourly_orders[hour] += vals.get("avg_orders", 0)

    total_rev = sum(hourly_rev.values())

    hourly = []
    for h in range(7, 23):
        rev = round(hourly_rev.get(h, 0), 2)
        orders = round(hourly_orders.get(h, 0), 1)
        pct = round(rev / total_rev * 100, 1) if total_rev > 0 else 0
        label = datetime(2000, 1, 1, h).strftime("%I %p").lstrip("0")
        hourly.append({
            "hour": h,
            "label": label,
            "expected_revenue": rev,
            "expected_orders": orders,
            "pct_of_day": pct,
        })

    return hourly, slot_4wra, weeks_found


# ══════════════════════════════════════════════════════════════════════════════
# Peak Detection
# ══════════════════════════════════════════════════════════════════════════════


def _detect_peaks(hourly, quarter_hourly):
    """Identify peak hour, peak 15-min slot, and lunch vs dinner split."""
    peak_hour = None
    peak_15min = None

    if hourly:
        best_h = max(hourly, key=lambda x: x["expected_revenue"])
        peak_hour = {
            "hour": best_h["label"],
            "expected_revenue": best_h["expected_revenue"],
            "expected_orders": best_h["expected_orders"],
        }

    if quarter_hourly:
        best_slot = max(quarter_hourly.items(), key=lambda x: x[1].get("avg_revenue", 0))
        slot_key = best_slot[0]
        h = int(slot_key.split(":")[0])
        m = int(slot_key.split(":")[1])
        label = datetime(2000, 1, 1, h, m).strftime("%I:%M %p").lstrip("0")
        peak_15min = {
            "slot": label,
            "expected_revenue": round(best_slot[1].get("avg_revenue", 0), 2),
            "expected_orders": round(best_slot[1].get("avg_orders", 0), 1),
        }

    # Lunch vs dinner split
    lunch_rev = sum(h["expected_revenue"] for h in hourly if 11 <= h["hour"] < 14)
    dinner_rev = sum(h["expected_revenue"] for h in hourly if 17 <= h["hour"] < 21)
    total = sum(h["expected_revenue"] for h in hourly) if hourly else 0
    lunch_pct = round(lunch_rev / total * 100, 1) if total > 0 else 0
    dinner_pct = round(dinner_rev / total * 100, 1) if total > 0 else 0

    return {
        "peak_hour": peak_hour or {"hour": "N/A", "expected_revenue": 0},
        "peak_15min": peak_15min or {"slot": "N/A", "expected_revenue": 0},
        "lunch_pct": lunch_pct,
        "dinner_pct": dinner_pct,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Daypart Breakdown
# ══════════════════════════════════════════════════════════════════════════════


def _compute_daypart_breakdown(hourly):
    """Aggregate hourly slots into daypart summaries."""
    total_rev = sum(h["expected_revenue"] for h in hourly) if hourly else 0
    hourly_lookup = {h["hour"]: h for h in hourly}

    dayparts = []
    for start, end, name in _DAYPARTS:
        rev = sum(hourly_lookup.get(h, {}).get("expected_revenue", 0) for h in range(start, end))
        orders = sum(hourly_lookup.get(h, {}).get("expected_orders", 0) for h in range(start, end))
        pct = round(rev / total_rev * 100, 1) if total_rev > 0 else 0
        start_label = datetime(2000, 1, 1, start).strftime("%I %p").lstrip("0")
        end_label = datetime(2000, 1, 1, end).strftime("%I %p").lstrip("0")
        dayparts.append({
            "name": name,
            "start": start_label,
            "end": end_label,
            "expected_revenue": round(rev, 2),
            "expected_orders": round(orders, 1),
            "pct_of_day": pct,
        })

    return dayparts


# ══════════════════════════════════════════════════════════════════════════════
# Scheduled Catering
# ══════════════════════════════════════════════════════════════════════════════


def _get_scheduled_catering(date_str):
    """Fetch scheduled catering orders for a specific date.

    Sources:
    1. Notion — confirmed orders with delivery date matching date_str
    2. Toast WS CSV — orders with date matching date_str

    Args:
        date_str: "YYYY-MM-DD" format

    Returns list of {name, platform, subtotal, date}.
    """
    orders = []

    # Source 1: Notion upcoming orders
    try:
        from tools.catering.notion import fetch_upcoming_orders
        notion_orders = fetch_upcoming_orders(from_date=date_str)
        for o in notion_orders:
            if o.get("date") == date_str:
                orders.append({
                    "name": o.get("name", ""),
                    "platform": o.get("platform", ""),
                    "subtotal": o.get("subtotal", 0),
                    "date": date_str,
                })
    except Exception as e:
        logger.warning("Notion catering fetch failed: %s", e)

    # Source 2: Toast WS CSV — check for orders on this date
    try:
        from tools.catering.data import _parse_ws_csv
        ws_orders = _parse_ws_csv()
        for o in ws_orders:
            if o.get("date") == date_str:
                orders.append({
                    "name": o.get("event_name") or o.get("customer", ""),
                    "platform": "Toast",
                    "subtotal": o.get("subtotal", 0),
                    "date": date_str,
                })
    except Exception as e:
        logger.warning("Toast catering CSV fetch failed: %s", e)

    return orders


# ══════════════════════════════════════════════════════════════════════════════
# Explainable Prediction
# ══════════════════════════════════════════════════════════════════════════════


def _build_explanation(dt, trend, dow_indices, seasonal_indices,
                       weather_data, weather_mult, weather_reasons,
                       catering_boost, base_prediction, final_prediction,
                       ch_dow=None, ch_seasonal=None, ch_trends=None,
                       channel_breakdown=None):
    """Build the full explainability dict for a single day's prediction.

    Shows step-by-step how the prediction was derived, including
    per-channel breakdown when channel models are provided.
    """
    if not trend.get("start_date"):
        return {"narrative": "Insufficient data for prediction.", "steps": []}

    ref_date = datetime.strptime(trend["start_date"], "%Y-%m-%d")
    day_num = (dt - ref_date).days
    trend_val = trend["slope"] * day_num + trend["intercept"]
    dow_idx = dow_indices.get(dt.weekday(), 1.0)
    seas_idx = seasonal_indices.get(dt.month, 1.0)

    # Base daily = trend / 7 (DOW normalization)
    base_daily = trend_val / 7.0

    # DOW effect
    dow_effect_pct = (dow_idx / 1.0 - 1) * 100  # vs neutral (1.0)

    # Seasonal effect
    seas_effect_pct = (seas_idx - 1.0) * 100

    # Weather effect
    weather_effect_pct = (weather_mult - 1.0) * 100 if weather_mult != 1.0 else 0
    weather_reason = "; ".join(weather_reasons) if weather_reasons else "Clear conditions"

    # Build steps
    steps = []

    steps.append({
        "label": "Trend baseline",
        "detail": "Average daily revenue (trend day %d)" % day_num,
        "value": round(base_daily, 2),
        "delta": None,
        "delta_pct": None,
    })

    # DOW factor: base_daily * dow_idx gives the DOW-adjusted value
    dow_name = _DOW_FULL[dt.weekday()]
    dow_adjusted = base_daily * dow_idx
    steps.append({
        "label": "%s effect" % dow_name,
        "detail": "DOW index = %.3f (avg = 1.000)" % dow_idx,
        "value": None,
        "delta": round(dow_adjusted - base_daily, 2),
        "delta_pct": round(dow_effect_pct, 1),
    })

    month_name = dt.strftime("%B")
    steps.append({
        "label": "%s seasonal" % month_name,
        "detail": "Seasonal index = %.3f (avg = 1.000)" % seas_idx,
        "value": None,
        "delta": None,
        "delta_pct": round(seas_effect_pct, 1),
    })

    steps.append({
        "label": "Base prediction",
        "detail": "trend x DOW x seasonal",
        "value": round(base_prediction, 2),
        "delta": None,
        "delta_pct": None,
    })

    if weather_mult != 1.0:
        weather_delta = final_prediction - catering_boost - base_prediction
        steps.append({
            "label": "Weather adjustment",
            "detail": weather_reason,
            "value": None,
            "delta": round(weather_delta, 2),
            "delta_pct": round(weather_effect_pct, 1),
        })

    if catering_boost > 0:
        steps.append({
            "label": "Scheduled catering",
            "detail": "Confirmed orders for today",
            "value": None,
            "delta": round(catering_boost, 2),
            "delta_pct": None,
        })

    steps.append({
        "label": "Final prediction",
        "detail": "",
        "value": round(final_prediction, 2),
        "delta": None,
        "delta_pct": None,
    })

    # Confidence based on data quality
    confidence = "high"
    if weather_mult < 0.8:
        confidence = "low"
    elif weather_mult < 0.95:
        confidence = "medium"

    # Narrative
    parts = []
    parts.append("%s typically sees %.1f%% %s average revenue."
                 % (dow_name,
                    abs(dow_effect_pct),
                    "above" if dow_effect_pct >= 0 else "below"))
    parts.append("%s is %.1f%% %s the annual average."
                 % (month_name,
                    abs(seas_effect_pct),
                    "above" if seas_effect_pct >= 0 else "below"))
    if weather_mult != 1.0:
        parts.append("Weather (%s) expected to reduce revenue by %.0f%%."
                     % (weather_reason, abs(weather_effect_pct)))
    if catering_boost > 0:
        parts.append("$%s in scheduled catering adds to the base forecast."
                     % "{:,.0f}".format(catering_boost))

    # Per-channel DOW/seasonal effects
    channel_effects = {}
    if ch_dow and ch_seasonal and channel_breakdown:
        for ch in ("instore", "delivery"):
            ch_dow_idx = ch_dow.get(ch, {}).get(dt.weekday(), 1.0)
            ch_seas_idx = ch_seasonal.get(ch, {}).get(dt.month, 1.0)
            ch_dow_pct = (ch_dow_idx / 1.0 - 1) * 100
            ch_seas_pct = (ch_seas_idx - 1.0) * 100
            ch_label = "In-Store" if ch == "instore" else "Delivery"
            channel_effects[ch] = {
                "label": ch_label,
                "revenue": round(channel_breakdown.get(ch, 0), 2),
                "dow_index": round(ch_dow_idx, 3),
                "dow_effect": "%+.1f%%" % ch_dow_pct,
                "seasonal_index": round(ch_seas_idx, 3),
                "seasonal_effect": "%+.1f%%" % ch_seas_pct,
            }
        channel_effects["catering"] = {
            "label": "Catering",
            "revenue": round(channel_breakdown.get("catering", 0), 2),
            "dow_index": None,
            "dow_effect": "N/A",
            "seasonal_index": None,
            "seasonal_effect": "N/A",
        }

    return {
        "base_daily_avg": round(base_daily, 2),
        "trend_value": round(base_daily, 2),
        "dow_name": dow_name,
        "dow_effect": "%+.1f%%" % dow_effect_pct,
        "dow_index": round(dow_idx, 3),
        "seasonal_effect": "%+.1f%%" % seas_effect_pct,
        "seasonal_index": round(seas_idx, 3),
        "weather_effect": "%+.1f%%" % weather_effect_pct if weather_mult != 1.0 else "none",
        "weather_multiplier": weather_mult,
        "weather_reason": weather_reason,
        "catering_boost": round(catering_boost, 2),
        "base_prediction": round(base_prediction, 2),
        "final_prediction": round(final_prediction, 2),
        "confidence": confidence,
        "steps": steps,
        "narrative": " ".join(parts),
        "channel_effects": channel_effects,
    }


# ══════════════════════════════════════════════════════════════════════════════
# This-Week Context
# ══════════════════════════════════════════════════════════════════════════════


def _build_this_week(today, df, trend, dow_indices, seasonal_indices,
                     channel_mix, expense_ratios,
                     ch_trends=None, ch_dow=None, ch_seasonal=None,
                     catering_base=None, bogo_rate=0.0):
    """Build this-week context: actuals for past days + forecast for remaining.

    Week runs Mon-Sun. Uses per-channel models when available.
    bogo_rate applies delivery discount haircut to forecast days.
    """
    from .data import _forecast_channel_day

    bogo_mult = 1.0 - (bogo_rate / 100.0) if bogo_rate > 0 else 1.0
    dow = today.weekday()  # 0=Mon
    this_monday = today - timedelta(days=dow)

    days = []
    total_actual = 0.0
    total_forecast = 0.0

    # Build date lookup from timeseries (total + per-channel)
    date_lookup = {}
    if df is not None and not df.empty:
        for _, row in df.iterrows():
            entry = {"total": float(row["revenue_total"])}
            if "revenue_instore" in row:
                entry["instore"] = float(row.get("revenue_instore", 0))
            if "revenue_delivery" in row:
                entry["delivery"] = float(row.get("revenue_delivery", 0))
            if "revenue_catering" in row:
                entry["catering"] = float(row.get("revenue_catering", 0))
            date_lookup[row["date"]] = entry

    cat_avg = catering_base.get("daily_avg", 0) if catering_base else 0

    for i in range(7):
        dt = this_monday + timedelta(days=i)
        date_str = dt.strftime("%Y-%m-%d")
        is_past = dt.date() < today.date()
        is_today = dt.date() == today.date()

        actual = date_lookup.get(date_str)

        if is_past and actual is not None:
            total_actual += actual["total"]
            days.append({
                "date": date_str,
                "dow_name": _DOW_NAMES[dt.weekday()],
                "label": dt.strftime("%a %b %d"),
                "revenue": round(actual["total"], 2),
                "channel_instore": round(actual.get("instore", 0), 2),
                "channel_delivery": round(actual.get("delivery", 0), 2),
                "channel_catering": round(actual.get("catering", 0), 2),
                "is_actual": True,
                "is_today": False,
            })
        else:
            # Use per-channel models if available
            if ch_trends and ch_dow and ch_seasonal:
                f_in = _forecast_channel_day(dt, "instore", ch_trends, ch_dow, ch_seasonal)
                f_del = _forecast_channel_day(dt, "delivery", ch_trends, ch_dow, ch_seasonal)
                f_del = f_del * bogo_mult  # BOGO adjustment
                f_cat = cat_avg
                forecast_rev = f_in + f_del + f_cat
            else:
                f_in = 0
                f_del = 0
                f_cat = 0
                forecast_rev = _forecast_one_day_local(dt, trend, dow_indices, seasonal_indices)

            total_forecast += forecast_rev
            days.append({
                "date": date_str,
                "dow_name": _DOW_NAMES[dt.weekday()],
                "label": dt.strftime("%a %b %d"),
                "revenue": round(forecast_rev, 2),
                "channel_instore": round(f_in, 2),
                "channel_delivery": round(f_del, 2),
                "channel_catering": round(f_cat, 2),
                "is_actual": False,
                "is_today": is_today,
            })

    week_label = "%s - %s" % (
        this_monday.strftime("%b %d"),
        (this_monday + timedelta(days=6)).strftime("%b %d"),
    )

    return {
        "week_label": week_label,
        "days": days,
        "total_actual": round(total_actual, 2),
        "total_forecast": round(total_forecast, 2),
        "week_total_estimate": round(total_actual + total_forecast, 2),
    }


def _forecast_one_day_local(dt, trend, dow_indices, seasonal_indices):
    """Forecast revenue for a single day (local helper)."""
    if not trend.get("start_date"):
        return 0.0
    ref_date = datetime.strptime(trend["start_date"], "%Y-%m-%d")
    day_num = (dt - ref_date).days
    trend_val = trend["slope"] * day_num + trend["intercept"]
    dow_idx = dow_indices.get(dt.weekday(), 1.0)
    seas_idx = seasonal_indices.get(dt.month, 1.0)
    return max(trend_val * (dow_idx / 7.0) * seas_idx, 0)


# ══════════════════════════════════════════════════════════════════════════════
# Last-Week Accuracy Builder
# ══════════════════════════════════════════════════════════════════════════════


def _build_last_week_accuracy(today, df, ch_trends, ch_dow, ch_seasonal,
                               cat_avg, bogo_rate=0.0):
    """Build last week's prediction-vs-actual accuracy data for each day.

    Returns dict with week_label and days list, each day having:
        date, date_slug, dow_name, label, projected, actual, variance,
        variance_pct, has_actual, channel_predicted, channel_actual,
        interpretation
    """
    from .data import _forecast_channel_day
    from .interpret import generate_day_interpretation

    bogo_mult = 1.0 - (bogo_rate / 100.0) if bogo_rate > 0 else 1.0
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)

    date_lookup = {}
    if df is not None and not df.empty:
        for _, row in df.iterrows():
            date_lookup[row["date"]] = {
                "total": float(row["revenue_total"]),
                "instore": float(row.get("revenue_instore", 0)),
                "delivery": float(row.get("revenue_delivery", 0)),
                "catering": float(row.get("revenue_catering", 0)),
            }

    days = []
    for i in range(7):
        dt = last_monday + timedelta(days=i)
        date_str = dt.strftime("%Y-%m-%d")

        p_in = _forecast_channel_day(dt, "instore", ch_trends, ch_dow, ch_seasonal)
        p_del = _forecast_channel_day(dt, "delivery", ch_trends, ch_dow, ch_seasonal) * bogo_mult
        projected = round(p_in + p_del + cat_avg, 2)

        channel_predicted = {
            "instore": round(p_in, 2),
            "delivery": round(p_del, 2),
            "catering": round(cat_avg, 2),
        }

        actual_entry = date_lookup.get(date_str)
        if actual_entry:
            actual = round(actual_entry["total"], 2)
            variance = round(actual - projected, 2)
            variance_pct = round(variance / projected * 100, 1) if projected > 0 else 0
            has_actual = True
        else:
            actual = None
            variance = None
            variance_pct = None
            has_actual = False

        interpretation = generate_day_interpretation(
            projected, actual, channel_predicted, actual_entry
        ) if has_actual else None

        days.append({
            "date": date_str,
            "date_slug": date_str.replace("-", ""),
            "dow_name": _DOW_NAMES[dt.weekday()],
            "label": dt.strftime("%a %b %d"),
            "projected": projected,
            "actual": actual,
            "variance": variance,
            "variance_pct": variance_pct,
            "has_actual": has_actual,
            "channel_predicted": channel_predicted,
            "channel_actual": actual_entry or {},
            "interpretation": interpretation,
        })

    week_label = "%s - %s" % (
        last_monday.strftime("%b %d"),
        (last_monday + timedelta(days=6)).strftime("%b %d"),
    )
    return {"week_label": week_label, "days": days}


# ══════════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ══════════════════════════════════════════════════════════════════════════════


def generate_prediction_for_date(target_date):
    """Generate prediction for any specific date (past or future).

    For past dates, uses only data available *before* target_date so
    the prediction is what the model would have made at the time —
    no future data leakage, no retrofitting.

    Always includes last_week_accuracy for the accuracy review section.
    Returns the same structure as generate_today_prediction().
    """
    now = datetime.now()
    is_past = target_date.date() < now.date()
    is_today_flag = target_date.date() == now.date()

    date_iso = target_date.strftime("%Y-%m-%d")
    date_yyyymmdd = target_date.strftime("%Y%m%d")

    logger.info("Generating prediction for %s (%s)...",
                date_iso, _DOW_FULL[target_date.weekday()])

    from .timeseries import get_timeseries
    from .data import (
        compute_dow_indices, compute_seasonal_indices, compute_trend,
        compute_channel_mix, _compute_expense_ratios, _load_real_pl,
        _apply_expense_ratios,
        compute_channel_dow_indices, compute_channel_seasonal_indices,
        compute_channel_trends, compute_catering_baseline,
        _forecast_channel_day, compute_discount_metrics,
    )

    full_df = get_timeseries()
    if full_df.empty:
        return _empty_result(target_date)

    # ── As-of data: for past dates, only use data the model would have
    # had access to at the time the prediction was made. This ensures
    # honest forecasts with no look-ahead bias.
    if is_past:
        df = full_df[full_df["date"] < date_iso].copy()
        if len(df) < 14:
            # Not enough history to build a reliable model — show actuals
            # with an explanation rather than a bad prediction
            result = _empty_result(target_date)
            row_match = full_df[full_df["date"] == date_iso]
            if not row_match.empty:
                row = row_match.iloc[0]
                result["actual"] = {
                    "total": round(float(row["revenue_total"]), 2),
                    "instore": round(float(row.get("revenue_instore", 0)), 2),
                    "delivery": round(float(row.get("revenue_delivery", 0)), 2),
                    "catering": round(float(row.get("revenue_catering", 0)), 2),
                    "orders": int(row.get("orders_total", 0)),
                }
            result["is_past"] = True
            result["is_today"] = False
            result["model_info"]["insufficient_history"] = True
            return result
    else:
        df = full_df

    dow_indices = compute_dow_indices(df)
    seasonal_indices = compute_seasonal_indices(df)
    trend = compute_trend(df, dow_indices, seasonal_indices)
    channel_mix = compute_channel_mix(df, trailing_months=3)
    real_pl = _load_real_pl()
    expense_ratios = _compute_expense_ratios(real_pl, trailing_months=6)
    ch_dow = compute_channel_dow_indices(df)
    ch_seasonal = compute_channel_seasonal_indices(df)
    ch_trends = compute_channel_trends(df, ch_dow, ch_seasonal)
    catering_base = compute_catering_baseline(df)
    disc_metrics = compute_discount_metrics(df)

    # Weather: use live API for today, forecast API for future, skip for past
    if is_today_flag:
        weather = _get_weather()
    elif not is_past:
        weather = _get_weather_for_date(date_yyyymmdd)
    else:
        weather = None

    weather_mult = 1.0
    weather_reasons = []
    if weather:
        weather_mult = weather.get("multiplier", 1.0)
        weather_reasons = weather.get("multiplier_reasons", [])

    bogo_rate = disc_metrics.get("delivery_discount_rate_current", 0)
    rev_instore = _forecast_channel_day(target_date, "instore", ch_trends, ch_dow, ch_seasonal)
    rev_delivery = _forecast_channel_day(target_date, "delivery", ch_trends, ch_dow, ch_seasonal)
    if bogo_rate > 0:
        rev_delivery = rev_delivery * (1.0 - bogo_rate / 100.0)
    rev_catering_model = catering_base.get("daily_avg", 0)
    base_prediction = rev_instore + rev_delivery + rev_catering_model

    # Only fetch scheduled catering for today/future
    catering_orders = [] if is_past else _get_scheduled_catering(date_iso)
    catering_boost = sum(o.get("subtotal", 0) for o in catering_orders)

    rev_instore_final = rev_instore * weather_mult
    rev_delivery_final = rev_delivery * weather_mult
    rev_catering_final = rev_catering_model + catering_boost
    final_prediction = rev_instore_final + rev_delivery_final + rev_catering_final

    hourly, quarter_hourly, weeks_found = _build_hourly_curve(target_date)
    hourly_total = sum(h["expected_revenue"] for h in hourly) if hourly else 0
    if hourly_total > 0 and final_prediction > 0:
        scale = final_prediction / hourly_total
        for h in hourly:
            h["expected_revenue"] = round(h["expected_revenue"] * scale, 2)
            h["expected_orders"] = round(h["expected_orders"] * scale, 1)

    peaks = _detect_peaks(hourly, quarter_hourly)
    dayparts = _compute_daypart_breakdown(hourly)

    daily_pl = _apply_expense_ratios(
        final_prediction, channel_mix, expense_ratios,
        rev_instore=rev_instore_final,
        rev_delivery=rev_delivery_final,
        rev_catering=rev_catering_final,
    )

    channel_breakdown = {
        "instore": rev_instore_final,
        "delivery": rev_delivery_final,
        "catering": rev_catering_final,
    }
    explanation = _build_explanation(
        target_date, trend, dow_indices, seasonal_indices,
        weather, weather_mult, weather_reasons,
        catering_boost, base_prediction, final_prediction,
        ch_dow=ch_dow, ch_seasonal=ch_seasonal, ch_trends=ch_trends,
        channel_breakdown=channel_breakdown,
    )

    this_week = _build_this_week(
        target_date, df, trend, dow_indices, seasonal_indices,
        channel_mix, expense_ratios,
        ch_trends=ch_trends, ch_dow=ch_dow, ch_seasonal=ch_seasonal,
        catering_base=catering_base, bogo_rate=bogo_rate,
    )

    last_week_accuracy = _build_last_week_accuracy(
        target_date, df, ch_trends, ch_dow, ch_seasonal,
        catering_base.get("daily_avg", 0), bogo_rate=bogo_rate,
    )

    expected_covers = round(final_prediction / _AVG_CHECK_SIZE)
    peak_hour_rev = peaks["peak_hour"]["expected_revenue"] if peaks.get("peak_hour") else 0
    peak_hour_covers = round(peak_hour_rev / _AVG_CHECK_SIZE) if peak_hour_rev else 0

    # Actual data for past dates
    actual_data = None
    if is_past and not df.empty:
        row_match = df[df["date"] == date_iso]
        if not row_match.empty:
            row = row_match.iloc[0]
            actual_data = {
                "total": round(float(row["revenue_total"]), 2),
                "instore": round(float(row.get("revenue_instore", 0)), 2),
                "delivery": round(float(row.get("revenue_delivery", 0)), 2),
                "catering": round(float(row.get("revenue_catering", 0)), 2),
                "orders": int(row.get("orders_total", 0)),
            }

    variance = None
    if actual_data and final_prediction > 0:
        v = actual_data["total"] - final_prediction
        variance = {
            "amount": round(v, 2),
            "pct": round(v / final_prediction * 100, 1),
        }

    return {
        "date": date_iso,
        "date_yyyymmdd": date_yyyymmdd,
        "dow_name": _DOW_FULL[target_date.weekday()],
        "dow_short": _DOW_NAMES[target_date.weekday()],
        "is_past": is_past,
        "is_today": is_today_flag,
        "actual": actual_data,
        "variance": variance,
        "weather": weather,
        "prediction": {
            "revenue_total": round(final_prediction, 2),
            "revenue_by_channel": {
                "instore": round(rev_instore_final, 2),
                "delivery": round(rev_delivery_final, 2),
                "catering": round(rev_catering_final, 2),
            },
            "daily_pl": daily_pl,
            "explanation": explanation,
            "discount_metrics": disc_metrics,
        },
        "hourly_curve": hourly,
        "quarter_hourly_4wra_weeks": weeks_found,
        "peaks": peaks,
        "dayparts": dayparts,
        "scheduled_catering": catering_orders,
        "this_week": this_week,
        "last_week_accuracy": last_week_accuracy,
        "staffing_hint": {
            "expected_covers": expected_covers,
            "peak_hour_covers": peak_hour_covers,
            "splh_target": 55,
        },
        "model_info": {
            "trend_slope": trend.get("slope", 0),
            "trend_intercept": trend.get("intercept", 0),
            "trend_r_squared": trend.get("r_squared", 0),
            "data_days": len(df),
            "data_start": df["date"].min() if not df.empty else "",
            "data_end": df["date"].max() if not df.empty else "",
            "channel_trends": {
                ch: {"slope": t.get("slope", 0), "r_squared": t.get("r_squared", 0)}
                for ch, t in ch_trends.items()
            },
            "catering_baseline": catering_base,
        },
    }


def generate_today_prediction():
    """Generate today's operational prediction. Delegates to generate_prediction_for_date."""
    return generate_prediction_for_date(datetime.now())


def _empty_result(today):
    """Return empty result when no data available."""
    return {
        "date": today.strftime("%Y-%m-%d"),
        "date_yyyymmdd": today.strftime("%Y%m%d"),
        "dow_name": _DOW_FULL[today.weekday()],
        "dow_short": _DOW_NAMES[today.weekday()],
        "weather": None,
        "prediction": {
            "revenue_total": 0,
            "revenue_by_channel": {},
            "daily_pl": {},
            "explanation": {"narrative": "No data available.", "steps": []},
        },
        "hourly_curve": [],
        "quarter_hourly_4wra_weeks": 0,
        "peaks": {"peak_hour": {}, "peak_15min": {}, "lunch_pct": 0, "dinner_pct": 0},
        "dayparts": [],
        "scheduled_catering": [],
        "this_week": {"week_label": "", "days": [], "week_total_estimate": 0},
        "staffing_hint": {},
        "model_info": {},
    }


# ══════════════════════════════════════════════════════════════════════════════
# Weekly View — dedicated /week page
# ══════════════════════════════════════════════════════════════════════════════


def generate_week_view():
    """Generate the This Week dashboard.

    Shows this week (Mon-Sun): actuals for past days, today's forecast,
    remaining day forecasts. Also shows last week for comparison and
    a weekly P&L summary.

    Returns dict with this_week, last_week, comparison, and weekly P&L.
    """
    today = datetime.now()

    logger.info("Generating weekly view for week of %s...", today.strftime("%Y-%m-%d"))

    from .timeseries import get_timeseries
    from .data import (
        compute_dow_indices, compute_seasonal_indices, compute_trend,
        compute_channel_mix, _compute_expense_ratios, _load_real_pl,
        _apply_expense_ratios,
        compute_channel_dow_indices, compute_channel_seasonal_indices,
        compute_channel_trends, compute_catering_baseline,
        _forecast_channel_day, compute_discount_metrics,
    )

    df = get_timeseries()
    if df.empty:
        return _empty_week_result(today)

    # Models
    dow_indices = compute_dow_indices(df)
    seasonal_indices = compute_seasonal_indices(df)
    trend = compute_trend(df, dow_indices, seasonal_indices)
    channel_mix = compute_channel_mix(df, trailing_months=3)
    real_pl = _load_real_pl()
    expense_ratios = _compute_expense_ratios(real_pl, trailing_months=6)
    ch_dow = compute_channel_dow_indices(df)
    ch_seasonal = compute_channel_seasonal_indices(df)
    ch_trends = compute_channel_trends(df, ch_dow, ch_seasonal)
    catering_base = compute_catering_baseline(df)
    disc_metrics = compute_discount_metrics(df)

    # Weather for today + upcoming days
    weather_by_date = {}
    for i in range(7):
        dt = today + timedelta(days=i)
        date_iso = dt.strftime("%Y-%m-%d")
        date_yyyymmdd = dt.strftime("%Y%m%d")
        w = _get_weather_for_date(date_yyyymmdd)
        if w:
            weather_by_date[date_iso] = w

    # Build date lookup from timeseries
    date_lookup = {}
    if not df.empty:
        for _, row in df.iterrows():
            entry = {
                "total": float(row["revenue_total"]),
                "instore": float(row.get("revenue_instore", 0)),
                "delivery": float(row.get("revenue_delivery", 0)),
                "catering": float(row.get("revenue_catering", 0)),
                "orders": int(row.get("orders_total", 0)),
                "discount": float(row.get("discount_total", 0)),
            }
            date_lookup[row["date"]] = entry

    cat_avg = catering_base.get("daily_avg", 0)

    # BOGO rate for delivery haircut
    bogo_rate = disc_metrics.get("delivery_discount_rate_current", 0)

    # ── This week (Mon-Sun) ──
    this_dow = today.weekday()
    this_monday = today - timedelta(days=this_dow)
    this_week = _build_week_days(
        this_monday, today, date_lookup, ch_trends, ch_dow, ch_seasonal,
        cat_avg, weather_by_date, expense_ratios, channel_mix,
        bogo_rate=bogo_rate,
    )

    # ── Next week (Mon-Sun) ──
    next_monday = this_monday + timedelta(days=7)
    # Fetch weather for next week too
    for i in range(7):
        dt = next_monday + timedelta(days=i)
        date_iso = dt.strftime("%Y-%m-%d")
        date_yyyymmdd = dt.strftime("%Y%m%d")
        if date_iso not in weather_by_date:
            w = _get_weather_for_date(date_yyyymmdd)
            if w:
                weather_by_date[date_iso] = w
    next_week = _build_week_days(
        next_monday, today, date_lookup, ch_trends, ch_dow, ch_seasonal,
        cat_avg, weather_by_date, expense_ratios, channel_mix,
        bogo_rate=bogo_rate,
    )

    # ── Last week (Mon-Sun) ──
    last_monday = this_monday - timedelta(days=7)
    last_week = _build_week_days(
        last_monday, today, date_lookup, ch_trends, ch_dow, ch_seasonal,
        cat_avg, {}, expense_ratios, channel_mix,
    )

    # ── Comparison ──
    tw_actual = sum(d["revenue"] for d in this_week["days"] if d["is_actual"])
    lw_total = sum(d["revenue"] for d in last_week["days"])
    lw_thru_same_day = sum(
        d["revenue"] for d in last_week["days"]
        if _DOW_NAMES.index(d["dow_name"]) < this_dow
    )

    pacing = 0.0
    if lw_thru_same_day > 0:
        pacing = (tw_actual - lw_thru_same_day) / lw_thru_same_day * 100

    comparison = {
        "this_week_actual_so_far": round(tw_actual, 2),
        "last_week_thru_same_day": round(lw_thru_same_day, 2),
        "last_week_total": round(lw_total, 2),
        "pacing_pct": round(pacing, 1),
        "this_week_estimate": this_week["week_total"],
    }

    # ── Weekly P&L estimate (this week) ──
    weekly_pl = _apply_expense_ratios(
        this_week["week_total"], channel_mix, expense_ratios,
    )

    # ── Next week P&L estimate ──
    next_week_pl = _apply_expense_ratios(
        next_week["week_total"], channel_mix, expense_ratios,
    )

    # ── Next week vs this week comparison ──
    nw_total = next_week["week_total"]
    tw_total = this_week["week_total"]
    nw_vs_tw_pct = 0.0
    if tw_total > 0:
        nw_vs_tw_pct = (nw_total - tw_total) / tw_total * 100

    return {
        "generated_at": today.strftime("%Y-%m-%d %H:%M"),
        "this_week": this_week,
        "next_week": next_week,
        "last_week": last_week,
        "comparison": comparison,
        "next_week_comparison": {
            "next_week_estimate": round(nw_total, 2),
            "this_week_estimate": round(tw_total, 2),
            "wow_pct": round(nw_vs_tw_pct, 1),
            "next_week_net": round(next_week_pl.get("net_income", 0), 2),
        },
        "weekly_pl": weekly_pl,
        "next_week_pl": next_week_pl,
        "discount_metrics": disc_metrics,
        "model_info": {
            "data_days": len(df),
            "data_start": df["date"].min() if not df.empty else "",
            "data_end": df["date"].max() if not df.empty else "",
        },
    }


def _build_week_days(monday, today, date_lookup, ch_trends, ch_dow,
                     ch_seasonal, cat_avg, weather_by_date,
                     expense_ratios, channel_mix, bogo_rate=0.0):
    """Build 7-day array for a week starting on monday.

    For actual (past) days: includes what the model projected for comparison.
    For forecast days: uses scheduled catering when available instead of avg.

    bogo_rate: current delivery discount rate as pct (e.g. 9.3 = 9.3%).
               Applied as a haircut to delivery forecasts since the model is
               trained mostly on pre-BOGO data where net ≈ gross.
    """
    from .data import _forecast_channel_day, _apply_expense_ratios

    # BOGO multiplier: converts model delivery (pre-BOGO) to post-BOGO net
    bogo_mult = 1.0 - (bogo_rate / 100.0) if bogo_rate > 0 else 1.0

    days = []
    total_actual = 0.0
    total_forecast = 0.0

    for i in range(7):
        dt = monday + timedelta(days=i)
        date_str = dt.strftime("%Y-%m-%d")
        is_past = dt.date() < today.date()
        is_today = dt.date() == today.date()

        actual = date_lookup.get(date_str)

        # Weather for this day
        w = weather_by_date.get(date_str)
        weather_mult = w.get("multiplier", 1.0) if w else 1.0

        # Always compute model projection for comparison
        p_in = _forecast_channel_day(dt, "instore", ch_trends, ch_dow, ch_seasonal)
        p_del = _forecast_channel_day(dt, "delivery", ch_trends, ch_dow, ch_seasonal)
        p_del = p_del * bogo_mult  # BOGO adjustment
        p_cat = cat_avg
        p_in_wx = p_in * weather_mult
        p_del_wx = p_del * weather_mult
        projected_rev = p_in_wx + p_del_wx + p_cat

        if is_past and actual is not None:
            total_actual += actual["total"]
            # Compute P&L for actual day
            day_pl = _apply_expense_ratios(
                actual["total"], channel_mix, expense_ratios,
                rev_instore=actual.get("instore"),
                rev_delivery=actual.get("delivery"),
                rev_catering=actual.get("catering"),
            )
            # Variance: actual vs projected
            variance = actual["total"] - projected_rev
            days.append({
                "date": date_str,
                "dow_name": _DOW_NAMES[dt.weekday()],
                "label": dt.strftime("%a %b %d"),
                "revenue": round(actual["total"], 2),
                "channel_instore": round(actual.get("instore", 0), 2),
                "channel_delivery": round(actual.get("delivery", 0), 2),
                "channel_catering": round(actual.get("catering", 0), 2),
                "orders": actual.get("orders", 0),
                "discount": round(actual.get("discount", 0), 2),
                "net_income": round(day_pl.get("net_income", 0), 2),
                "projected": round(projected_rev, 2),
                "variance": round(variance, 2),
                "is_actual": True,
                "is_today": False,
                "weather": w,
            })
        else:
            # Forecast — use scheduled catering if available
            f_in = p_in_wx
            f_del = p_del_wx

            # Check for scheduled catering orders
            sched_catering = _get_scheduled_catering(date_str)
            sched_total = sum(o.get("subtotal", 0) for o in sched_catering)
            f_cat = sched_total if sched_total > 0 else cat_avg

            forecast_rev = f_in + f_del + f_cat

            day_pl = _apply_expense_ratios(
                forecast_rev, channel_mix, expense_ratios,
                rev_instore=f_in, rev_delivery=f_del, rev_catering=f_cat,
            )

            total_forecast += forecast_rev
            catering_source = "scheduled" if sched_total > 0 else "model avg"
            days.append({
                "date": date_str,
                "dow_name": _DOW_NAMES[dt.weekday()],
                "label": dt.strftime("%a %b %d"),
                "revenue": round(forecast_rev, 2),
                "channel_instore": round(f_in, 2),
                "channel_delivery": round(f_del, 2),
                "channel_catering": round(f_cat, 2),
                "orders": 0,
                "discount": 0,
                "net_income": round(day_pl.get("net_income", 0), 2),
                "projected": round(forecast_rev, 2),
                "variance": 0,
                "catering_source": catering_source,
                "is_actual": False,
                "is_today": is_today,
                "weather": w,
            })

    week_label = "%s - %s" % (
        monday.strftime("%b %d"),
        (monday + timedelta(days=6)).strftime("%b %d"),
    )

    return {
        "week_label": week_label,
        "days": days,
        "total_actual": round(total_actual, 2),
        "total_forecast": round(total_forecast, 2),
        "week_total": round(total_actual + total_forecast, 2),
    }


def _empty_week_result(today):
    """Return empty result when no data available."""
    return {
        "generated_at": today.strftime("%Y-%m-%d %H:%M"),
        "this_week": {"week_label": "", "days": [], "week_total": 0},
        "next_week": {"week_label": "", "days": [], "week_total": 0},
        "last_week": {"week_label": "", "days": [], "week_total": 0},
        "comparison": {},
        "next_week_comparison": {},
        "weekly_pl": {},
        "next_week_pl": {},
        "discount_metrics": {},
        "model_info": {},
    }
