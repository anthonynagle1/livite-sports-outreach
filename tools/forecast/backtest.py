"""Honest backtesting engine for the Livite forecast model.

For each day D in a date range:
- Build the model using ONLY data from before D (strict as-of cutoff)
- Generate the prediction the model would have made on D-1 for day D
- Compare against the actual revenue recorded for D

No data from D or later ever enters the model used to predict D.
This prevents look-ahead bias and overfitting.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .interpret import generate_day_interpretation

logger = logging.getLogger(__name__)

_DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MIN_HISTORY_DAYS = 21  # minimum data to build a usable model


def generate_backtest_range(start_date: datetime, end_date: datetime) -> dict:
    """Run honest backtesting over a date range.

    For each day D in [start_date, end_date]:
    - Trains on df[date < D] — strictly no future data
    - Generates prediction for D
    - Records actual revenue for D

    Returns dict with per-day results and aggregate accuracy stats.
    """
    from .timeseries import get_timeseries
    from .data import (
        compute_dow_indices, compute_seasonal_indices, compute_trend,
        compute_channel_mix, _compute_expense_ratios, _load_real_pl,
        compute_channel_dow_indices, compute_channel_seasonal_indices,
        compute_channel_trends, compute_catering_baseline,
        _forecast_channel_day, compute_discount_metrics,
    )

    full_df = get_timeseries()
    if full_df.empty:
        return _empty_backtest(start_date, end_date)

    real_pl = _load_real_pl()

    # Build full actuals lookup once (we only READ this, never train on it)
    actuals = {}
    for _, row in full_df.iterrows():
        actuals[row["date"]] = {
            "total": round(float(row["revenue_total"]), 2),
            "instore": round(float(row.get("revenue_instore", 0)), 2),
            "delivery": round(float(row.get("revenue_delivery", 0)), 2),
            "catering": round(float(row.get("revenue_catering", 0)), 2),
            "orders": int(row.get("orders_total", 0)),
        }

    days = []
    current = start_date
    while current.date() <= end_date.date():
        date_str = current.strftime("%Y-%m-%d")
        date_slug = current.strftime("%Y%m%d")

        # Strict as-of cutoff: data strictly before this date
        df = full_df[full_df["date"] < date_str].copy()
        actual_entry = actuals.get(date_str)

        if len(df) < _MIN_HISTORY_DAYS:
            days.append({
                "date": date_str,
                "date_slug": date_slug,
                "dow_name": _DOW_NAMES[current.weekday()],
                "label": current.strftime("%a %b %d"),
                "predicted": None,
                "actual": actual_entry["total"] if actual_entry else None,
                "variance": None,
                "variance_pct": None,
                "data_days_used": len(df),
                "skipped": True,
                "skip_reason": "Only %d days of history (need %d)" % (len(df), _MIN_HISTORY_DAYS),
            })
            current += timedelta(days=1)
            continue

        # Rebuild model from scratch with as-of data
        try:
            dow_indices = compute_dow_indices(df)
            seasonal_indices = compute_seasonal_indices(df)
            trend = compute_trend(df, dow_indices, seasonal_indices)
            ch_dow = compute_channel_dow_indices(df)
            ch_seasonal = compute_channel_seasonal_indices(df)
            ch_trends = compute_channel_trends(df, ch_dow, ch_seasonal)
            catering_base = compute_catering_baseline(df)
            disc_metrics = compute_discount_metrics(df)

            bogo_rate = disc_metrics.get("delivery_discount_rate_current", 0)
            bogo_mult = 1.0 - (bogo_rate / 100.0) if bogo_rate > 0 else 1.0

            p_in = _forecast_channel_day(current, "instore", ch_trends, ch_dow, ch_seasonal)
            p_del = _forecast_channel_day(current, "delivery", ch_trends, ch_dow, ch_seasonal)
            p_del_adj = p_del * bogo_mult
            p_cat = catering_base.get("daily_avg", 0)
            predicted = round(p_in + p_del_adj + p_cat, 2)

            channel_predicted = {
                "instore": round(p_in, 2),
                "delivery": round(p_del_adj, 2),
                "catering": round(p_cat, 2),
            }
        except Exception as e:
            logger.warning("Backtest model failed for %s: %s", date_str, e)
            days.append({
                "date": date_str,
                "date_slug": date_slug,
                "dow_name": _DOW_NAMES[current.weekday()],
                "label": current.strftime("%a %b %d"),
                "predicted": None,
                "actual": actual_entry["total"] if actual_entry else None,
                "variance": None,
                "variance_pct": None,
                "data_days_used": len(df),
                "skipped": True,
                "skip_reason": "Model error: %s" % str(e)[:60],
            })
            current += timedelta(days=1)
            continue

        actual = actual_entry["total"] if actual_entry else None

        if predicted is not None and actual is not None:
            variance = round(actual - predicted, 2)
            variance_pct = round(variance / predicted * 100, 1) if predicted > 0 else 0
        else:
            variance = None
            variance_pct = None

        interpretation = generate_day_interpretation(
            predicted, actual, channel_predicted, actual_entry or {}
        )

        days.append({
            "date": date_str,
            "date_slug": date_slug,
            "dow_name": _DOW_NAMES[current.weekday()],
            "label": current.strftime("%a %b %d"),
            "predicted": predicted,
            "actual": actual,
            "variance": variance,
            "variance_pct": variance_pct,
            "data_days_used": len(df),
            "skipped": False,
            "channel_predicted": channel_predicted,
            "channel_actual": actual_entry or {},
            "interpretation": interpretation,
        })

        current += timedelta(days=1)

    # Aggregate accuracy stats (only days with both predicted + actual)
    valid = [d for d in days if not d.get("skipped") and d["predicted"] and d["actual"]]
    summary = _compute_summary(valid) if valid else {}

    return {
        "start": start_date.strftime("%Y-%m-%d"),
        "end": end_date.strftime("%Y-%m-%d"),
        "range_label": "%s – %s" % (
            start_date.strftime("%b %d"),
            end_date.strftime("%b %d, %Y"),
        ),
        "days": days,
        "summary": summary,
        "note": "Each day's prediction uses only data available before that day.",
    }


def _compute_summary(valid_days: list) -> dict:
    """Compute aggregate accuracy metrics from a list of valid backtest days."""
    errors = [abs(d["variance"]) for d in valid_days]
    variances = [d["variance"] for d in valid_days]

    mae = sum(errors) / len(errors)
    mse = sum(e ** 2 for e in errors) / len(errors)
    rmse = mse ** 0.5
    bias = sum(variances) / len(variances)  # + = we underpredict, - = overpredict

    total_predicted = sum(d["predicted"] for d in valid_days)
    total_actual = sum(d["actual"] for d in valid_days)
    total_variance = total_actual - total_predicted
    total_variance_pct = (total_variance / total_predicted * 100) if total_predicted > 0 else 0

    # Directional accuracy: % of days within ±15% of actual
    within_15 = sum(1 for d in valid_days if abs(d.get("variance_pct") or 0) <= 15)
    within_10 = sum(1 for d in valid_days if abs(d.get("variance_pct") or 0) <= 10)

    over_days = sum(1 for d in valid_days if (d["variance"] or 0) > 0)
    under_days = len(valid_days) - over_days

    # Best and worst days
    best = max(valid_days, key=lambda d: d["actual"], default=None)
    worst_miss = max(valid_days, key=lambda d: abs(d.get("variance") or 0), default=None)

    return {
        "days_evaluated": len(valid_days),
        "total_predicted": round(total_predicted, 2),
        "total_actual": round(total_actual, 2),
        "total_variance": round(total_variance, 2),
        "total_variance_pct": round(total_variance_pct, 1),
        "mae": round(mae, 2),
        "rmse": round(rmse, 2),
        "bias": round(bias, 2),
        "within_15pct": round(within_15 / len(valid_days) * 100, 1),
        "within_10pct": round(within_10 / len(valid_days) * 100, 1),
        "over_days": over_days,
        "under_days": under_days,
        "best_revenue_day": best["label"] if best else "",
        "worst_miss_day": worst_miss["label"] if worst_miss else "",
        "worst_miss_variance": worst_miss["variance"] if worst_miss else 0,
    }


def _empty_backtest(start_date, end_date):
    return {
        "start": start_date.strftime("%Y-%m-%d"),
        "end": end_date.strftime("%Y-%m-%d"),
        "range_label": "%s – %s" % (
            start_date.strftime("%b %d"),
            end_date.strftime("%b %d, %Y"),
        ),
        "days": [],
        "summary": {},
        "note": "No data available.",
    }
