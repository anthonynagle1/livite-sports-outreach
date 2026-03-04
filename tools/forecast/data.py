"""P&L forecast engine — Steps 2-5 of the rolling forecast.

Takes the daily time series (Step 1) and produces:
- DOW indices (7 values summing to 7.0)
- Monthly seasonal indices (12 values averaging ~1.0)
- Linear trend via OLS on deseasonalized data
- Monthly P&L actuals + forecast with REAL line-by-line cost projections
- Next-week daily projections and last-week review

Uses real accounting P&L data (from parse_all_pl) where available,
falling back to ratio-based estimates for forecast periods.

No external ML libraries — uses numpy + pandas only.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .timeseries import get_timeseries

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TMP_DIR = os.path.join(_BASE_DIR, ".tmp")

_DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

# ── Expense group mapping (display name → dict key) ──
# Matches EXPENSE_GROUPS in tools/financials/metrics.py

EXPENSE_GROUP_KEYS = {
    "Labor": "labor",
    "Third Party Fees": "third_party_fees",
    "Rent & Occupancy": "rent_occupancy",
    "Professional & Admin": "professional_admin",
    "Marketing": "marketing",
    "Technology": "technology",
    "Capital & Depreciation": "capital_depreciation",
    "Other": "other_opex",
}

# Ordered list for P&L display: (dict_key, display_label)
EXPENSE_GROUP_ORDER = [
    ("labor", "Labor"),
    ("third_party_fees", "Third Party Fees"),
    ("rent_occupancy", "Rent & Occupancy"),
    ("professional_admin", "Professional & Admin"),
    ("marketing", "Marketing"),
    ("technology", "Technology"),
    ("capital_depreciation", "Capital & Depreciation"),
    ("other_opex", "Other Operating"),
]

# All cost keys every P&L row must carry
_COST_KEYS = [
    "cogs", "gross_profit",
    "labor", "third_party_fees", "rent_occupancy", "professional_admin",
    "marketing", "technology", "capital_depreciation", "other_opex",
    "total_opex", "operating_income", "other_income", "net_income",
]

# Default expense ratios if accounting data unavailable
_DEFAULT_RATIOS = {
    "cogs": 0.239,
    "labor": 0.240,
    "third_party_fees": 0.232,
    "rent_occupancy": 0.028,
    "professional_admin": 0.020,
    "marketing": 0.007,
    "technology": 0.002,
    "capital_depreciation": 0.007,
    "other_opex": 0.014,
    "other_income": 0.0,
}


# ══════════════════════════════════════════════════════════════════════════════
# Real P&L data loading
# ══════════════════════════════════════════════════════════════════════════════


def _load_real_pl() -> dict:
    """Load real accounting P&L data grouped by month with expense groups.

    Returns dict keyed by 'YYYY-MM' with all P&L cost line items.
    Returns empty dict if financials data is unavailable.
    """
    try:
        from tools.financials.data import parse_all_pl
        from tools.financials.metrics import _OWNER_COMP_ACCOUNTS
    except ImportError:
        logger.warning("Financials module not available")
        return {}

    try:
        pl = parse_all_pl()
    except Exception as e:
        logger.warning("Failed to load P&L data: %s", e)
        return {}

    months = pl.get("months", [])
    n = len(months)
    if n == 0:
        return {}

    # Build reverse lookup for expense grouping
    try:
        from tools.financials.metrics import EXPENSE_GROUPS
    except ImportError:
        EXPENSE_GROUPS = {}
        _OWNER_COMP_ACCOUNTS = set()

    acct_to_group = {}
    for group, accts in EXPENSE_GROUPS.items():
        for acct in accts:
            acct_to_group[acct] = EXPENSE_GROUP_KEYS.get(group, "other_opex")

    result = {}
    for i, month in enumerate(months):
        total_income = pl["total_income"][i]

        # Skip months with negligible activity
        if total_income is None or abs(total_income) < 1000:
            continue

        cogs = pl["cogs"][i]
        gross_profit = pl["gross_profit"][i]

        # Group OpEx into 8 categories (excluding owner comp)
        opex_groups = {key: 0.0 for key in EXPENSE_GROUP_KEYS.values()}
        for acct, vals in pl["opex"].items():
            if acct in _OWNER_COMP_ACCOUNTS:
                continue
            snake_key = acct_to_group.get(acct, "other_opex")
            opex_groups[snake_key] += vals[i]

        total_opex = sum(opex_groups.values())
        operating_income = gross_profit - total_opex
        other_income = pl.get("total_other_income", [0.0] * n)[i]
        net_income = operating_income + other_income

        row = {
            "total_income": total_income,
            "cogs": cogs,
            "gross_profit": gross_profit,
            "total_opex": total_opex,
            "operating_income": operating_income,
            "other_income": other_income,
            "net_income": net_income,
        }
        row.update(opex_groups)
        result[month] = row

    logger.info("Loaded real P&L data for %d months", len(result))
    return result


def _compute_expense_ratios(real_pl: dict, trailing_months: int = 6) -> dict:
    """Compute trailing average expense ratios as pct of total income.

    Returns dict with ratio for each P&L cost line (e.g. cogs: 0.24).
    """
    if not real_pl:
        return dict(_DEFAULT_RATIOS)

    sorted_months = sorted(real_pl.keys())
    recent = sorted_months[-trailing_months:]

    total_income = 0.0
    sums = defaultdict(float)

    for month in recent:
        pl = real_pl[month]
        ti = pl.get("total_income", 0)
        if ti is None or ti <= 0:
            continue
        total_income += ti
        for key in list(EXPENSE_GROUP_KEYS.values()) + ["cogs", "other_income"]:
            sums[key] += pl.get(key, 0)

    if total_income <= 0:
        return dict(_DEFAULT_RATIOS)

    ratios = {}
    for key, total in sums.items():
        ratios[key] = round(abs(total) / total_income, 4)

    # Fill in any missing keys with defaults
    for key, default_val in _DEFAULT_RATIOS.items():
        if key not in ratios:
            ratios[key] = default_val

    return ratios


def _apply_expense_ratios(
    revenue: float, channel_mix: dict, expense_ratios: dict,
    rev_instore: float = None, rev_delivery: float = None,
    rev_catering: float = None,
) -> dict:
    """Build a complete P&L row from forecasted revenue using expense ratios.

    If per-channel revenues are provided, uses them directly.
    Otherwise falls back to splitting total by channel_mix (legacy behavior).
    """
    total_income = revenue

    cogs = total_income * expense_ratios.get("cogs", 0.24)
    gross_profit = total_income - cogs

    total_opex = 0.0
    opex = {}
    for key, _ in EXPENSE_GROUP_ORDER:
        val = total_income * expense_ratios.get(key, 0)
        opex[key] = round(val, 2)
        total_opex += val

    operating_income = gross_profit - total_opex
    other_income = total_income * expense_ratios.get("other_income", 0)
    net_income = operating_income + other_income

    # Use per-channel values if provided, else fall back to mix ratio
    r_in = rev_instore if rev_instore is not None else revenue * channel_mix.get("instore", 0.33)
    r_del = rev_delivery if rev_delivery is not None else revenue * channel_mix.get("delivery", 0.64)
    r_cat = rev_catering if rev_catering is not None else revenue * channel_mix.get("catering", 0.02)

    return {
        "revenue_instore": round(r_in, 2),
        "revenue_delivery": round(r_del, 2),
        "revenue_catering": round(r_cat, 2),
        "revenue_total": round(revenue, 2),
        "total_income": round(total_income, 2),
        "cogs": round(cogs, 2),
        "gross_profit": round(gross_profit, 2),
        **opex,
        "total_opex": round(total_opex, 2),
        "operating_income": round(operating_income, 2),
        "other_income": round(other_income, 2),
        "net_income": round(net_income, 2),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Step 2: DOW Indices
# ══════════════════════════════════════════════════════════════════════════════


def compute_dow_indices(df: pd.DataFrame) -> dict:
    """Compute day-of-week indices from daily revenue.

    Returns dict {0: index, 1: index, ...} where indices sum to 7.0.
    Monday=0, Sunday=6.
    """
    if df.empty or "dow" not in df.columns or "revenue_total" not in df.columns:
        return {i: 1.0 for i in range(7)}

    dow_avg = df.groupby("dow")["revenue_total"].mean()
    grand_avg = df["revenue_total"].mean()

    if grand_avg == 0:
        return {i: 1.0 for i in range(7)}

    indices = {}
    for i in range(7):
        if i in dow_avg.index:
            indices[i] = dow_avg[i] / grand_avg
        else:
            indices[i] = 1.0

    # Normalize so they sum to exactly 7.0
    total = sum(indices.values())
    if total > 0:
        factor = 7.0 / total
        indices = {k: v * factor for k, v in indices.items()}

    return indices


# ══════════════════════════════════════════════════════════════════════════════
# Step 3: Monthly Seasonal Indices
# ══════════════════════════════════════════════════════════════════════════════


def compute_seasonal_indices(df: pd.DataFrame) -> dict:
    """Compute monthly seasonal indices from daily revenue.

    Returns dict {1: index, ..., 12: index} averaging ~1.0.
    """
    if df.empty or "date" not in df.columns or "revenue_total" not in df.columns:
        return {m: 1.0 for m in range(1, 13)}

    df = df.copy()
    df["cal_month"] = pd.to_datetime(df["date"]).dt.month
    month_avg = df.groupby("cal_month")["revenue_total"].mean()
    grand_avg = df["revenue_total"].mean()

    if grand_avg == 0:
        return {m: 1.0 for m in range(1, 13)}

    indices = {}
    for m in range(1, 13):
        if m in month_avg.index:
            indices[m] = month_avg[m] / grand_avg
        else:
            indices[m] = 1.0

    # Normalize to average 1.0
    avg = sum(indices.values()) / 12
    if avg > 0:
        indices = {k: v / avg for k, v in indices.items()}

    return indices


# ══════════════════════════════════════════════════════════════════════════════
# Step 4: Trend Extraction
# ══════════════════════════════════════════════════════════════════════════════


def compute_trend(
    df: pd.DataFrame, dow_indices: dict, seasonal_indices: dict,
) -> dict:
    """Extract linear trend from deseasonalized daily revenue.

    Returns dict with slope, intercept, r_squared, start_date.
    """
    if df.empty or len(df) < 30:
        return {"slope": 0, "intercept": 0, "r_squared": 0, "start_date": ""}

    df = df.copy()
    dates = pd.to_datetime(df["date"])
    start_date = dates.min()
    df["day_num"] = (dates - start_date).dt.days
    df["cal_month"] = dates.dt.month

    # Deseasonalize: divide by (dow_index/7 * seasonal_index)
    deseasonalized = []
    day_nums = []
    for _, row in df.iterrows():
        dow_idx = dow_indices.get(int(row["dow"]), 1.0)
        seas_idx = seasonal_indices.get(int(row["cal_month"]), 1.0)
        factor = (dow_idx / 7.0) * seas_idx
        if factor > 0 and row["revenue_total"] > 0:
            deseasonalized.append(row["revenue_total"] / factor)
            day_nums.append(row["day_num"])

    if len(deseasonalized) < 30:
        return {"slope": 0, "intercept": 0, "r_squared": 0, "start_date": ""}

    x = np.array(day_nums, dtype=float)
    y = np.array(deseasonalized, dtype=float)

    coeffs = np.polyfit(x, y, 1)
    slope, intercept = coeffs[0], coeffs[1]

    # R-squared
    y_pred = slope * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

    return {
        "slope": round(float(slope), 4),
        "intercept": round(float(intercept), 2),
        "r_squared": round(float(r_squared), 4),
        "start_date": start_date.strftime("%Y-%m-%d"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Channel Mix
# ══════════════════════════════════════════════════════════════════════════════


def compute_channel_mix(df: pd.DataFrame, trailing_months: int = 3) -> dict:
    """Compute channel revenue mix from trailing N months."""
    if df.empty:
        return {"instore": 0.33, "delivery": 0.64, "catering": 0.02, "other": 0.01}

    df = df.copy()
    df["_dt"] = pd.to_datetime(df["date"])
    cutoff = df["_dt"].max() - pd.Timedelta(days=trailing_months * 30)
    recent = df[df["_dt"] >= cutoff]

    if recent.empty:
        recent = df

    total = recent["revenue_total"].sum()
    if total == 0:
        return {"instore": 0.33, "delivery": 0.64, "catering": 0.02, "other": 0.01}

    return {
        "instore": round(recent["revenue_instore"].sum() / total, 4),
        "delivery": round(recent["revenue_delivery"].sum() / total, 4),
        "catering": round(recent["revenue_catering"].sum() / total, 4),
        "other": round(recent["revenue_other"].sum() / total, 4),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Channel-Independent Forecast Model
# ══════════════════════════════════════════════════════════════════════════════

_FORECAST_CHANNELS = ["instore", "delivery"]  # Catering handled separately


def compute_channel_dow_indices(df: pd.DataFrame) -> dict:
    """Compute DOW indices for each channel independently.

    Returns {"instore": {0: idx, ...}, "delivery": {0: idx, ...},
             "total": {0: idx, ...}}.
    Each channel's 7 indices sum to 7.0.
    Catering excluded (too noisy with many zero days).
    """
    result = {}
    col_map = {
        "instore": "revenue_instore",
        "delivery": "revenue_delivery",
        "total": "revenue_total",
    }

    for ch, col in col_map.items():
        if col not in df.columns:
            result[ch] = {i: 1.0 for i in range(7)}
            continue

        dow_avg = df.groupby("dow")[col].mean()
        grand_avg = df[col].mean()

        if grand_avg == 0:
            result[ch] = {i: 1.0 for i in range(7)}
            continue

        indices = {}
        for i in range(7):
            indices[i] = dow_avg[i] / grand_avg if i in dow_avg.index else 1.0

        # Normalize to sum to 7.0
        total = sum(indices.values())
        if total > 0:
            factor = 7.0 / total
            indices = {k: v * factor for k, v in indices.items()}

        result[ch] = indices

    return result


def compute_channel_seasonal_indices(df: pd.DataFrame) -> dict:
    """Compute monthly seasonal indices for each channel independently.

    Returns {"instore": {1: idx, ...}, "delivery": {1: idx, ...},
             "total": {1: idx, ...}}.
    Each channel's 12 indices average ~1.0.
    Catering uses total seasonal index as proxy.
    """
    df = df.copy()
    df["cal_month"] = pd.to_datetime(df["date"]).dt.month

    result = {}
    col_map = {
        "instore": "revenue_instore",
        "delivery": "revenue_delivery",
        "total": "revenue_total",
    }

    for ch, col in col_map.items():
        if col not in df.columns:
            result[ch] = {m: 1.0 for m in range(1, 13)}
            continue

        month_avg = df.groupby("cal_month")[col].mean()
        grand_avg = df[col].mean()

        if grand_avg == 0:
            result[ch] = {m: 1.0 for m in range(1, 13)}
            continue

        indices = {}
        for m in range(1, 13):
            indices[m] = month_avg[m] / grand_avg if m in month_avg.index else 1.0

        # Normalize to average 1.0
        avg = sum(indices.values()) / 12
        if avg > 0:
            indices = {k: v / avg for k, v in indices.items()}

        result[ch] = indices

    return result


def compute_channel_trends(
    df: pd.DataFrame,
    channel_dow: dict,
    channel_seasonal: dict,
) -> dict:
    """Compute separate OLS trends for in-store and delivery.

    Returns {"instore": {slope, intercept, r_squared, start_date},
             "delivery": {slope, intercept, r_squared, start_date}}.
    """
    if df.empty or len(df) < 30:
        empty = {"slope": 0, "intercept": 0, "r_squared": 0, "start_date": ""}
        return {"instore": empty.copy(), "delivery": empty.copy()}

    df = df.copy()
    dates = pd.to_datetime(df["date"])
    start_date = dates.min()
    df["day_num"] = (dates - start_date).dt.days
    df["cal_month"] = dates.dt.month

    col_map = {"instore": "revenue_instore", "delivery": "revenue_delivery"}
    result = {}

    for ch, col in col_map.items():
        if col not in df.columns:
            result[ch] = {"slope": 0, "intercept": 0, "r_squared": 0, "start_date": ""}
            continue

        dow_idx = channel_dow.get(ch, {i: 1.0 for i in range(7)})
        seas_idx = channel_seasonal.get(ch, {m: 1.0 for m in range(1, 13)})

        deseasonalized = []
        day_nums = []
        for _, row in df.iterrows():
            d = dow_idx.get(int(row["dow"]), 1.0)
            s = seas_idx.get(int(row["cal_month"]), 1.0)
            factor = (d / 7.0) * s
            if factor > 0 and row[col] > 0:
                deseasonalized.append(row[col] / factor)
                day_nums.append(row["day_num"])

        if len(deseasonalized) < 30:
            result[ch] = {"slope": 0, "intercept": 0, "r_squared": 0, "start_date": ""}
            continue

        x = np.array(day_nums, dtype=float)
        y = np.array(deseasonalized, dtype=float)
        coeffs = np.polyfit(x, y, 1)
        slope, intercept = coeffs[0], coeffs[1]

        y_pred = slope * x + intercept
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r_sq = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        result[ch] = {
            "slope": round(float(slope), 4),
            "intercept": round(float(intercept), 2),
            "r_squared": round(float(r_sq), 4),
            "start_date": start_date.strftime("%Y-%m-%d"),
        }

    return result


def compute_catering_baseline(df: pd.DataFrame, trailing_days: int = 90) -> dict:
    """Compute catering baseline from trailing average (no DOW/seasonal).

    Catering is lumpy and pipeline-driven. Instead of multiplicative
    decomposition, use trailing average including zeros.

    Returns {"daily_avg": float, "frequency": float, "avg_nonzero": float}.
    """
    if df.empty or "revenue_catering" not in df.columns:
        return {"daily_avg": 0, "frequency": 0, "avg_nonzero": 0}

    df = df.copy()
    df["_dt"] = pd.to_datetime(df["date"])
    cutoff = df["_dt"].max() - pd.Timedelta(days=trailing_days)
    recent = df[df["_dt"] >= cutoff]

    if recent.empty:
        recent = df

    total = recent["revenue_catering"].sum()
    n_days = len(recent)
    nonzero = recent[recent["revenue_catering"] > 0]

    return {
        "daily_avg": round(total / n_days, 2) if n_days else 0,
        "frequency": round(len(nonzero) / n_days, 4) if n_days else 0,
        "avg_nonzero": round(nonzero["revenue_catering"].mean(), 2) if len(nonzero) else 0,
    }


def _forecast_channel_day(
    dt: datetime,
    channel: str,
    channel_trends: dict,
    channel_dow: dict,
    channel_seasonal: dict,
) -> float:
    """Forecast one channel's revenue for a single day."""
    trend = channel_trends.get(channel)
    if not trend or not trend.get("start_date"):
        return 0.0

    ref_date = datetime.strptime(trend["start_date"], "%Y-%m-%d")
    day_num = (dt - ref_date).days
    trend_val = trend["slope"] * day_num + trend["intercept"]
    dow_idx = channel_dow.get(channel, {}).get(dt.weekday(), 1.0)
    seas_idx = channel_seasonal.get(channel, {}).get(dt.month, 1.0)
    return max(trend_val * (dow_idx / 7.0) * seas_idx, 0)


def compute_discount_metrics(df: pd.DataFrame) -> dict:
    """Compute discount health metrics for the forecast output."""
    if df.empty or "discount_delivery" not in df.columns:
        return {}

    df = df.copy()
    df["_dt"] = pd.to_datetime(df["date"])

    # Trailing 30 days
    cutoff_30 = df["_dt"].max() - pd.Timedelta(days=30)
    last_30 = df[df["_dt"] >= cutoff_30]

    # 3 months ago window (90-120 days back)
    cutoff_120 = df["_dt"].max() - pd.Timedelta(days=120)
    cutoff_90 = df["_dt"].max() - pd.Timedelta(days=90)
    three_mo_ago = df[(df["_dt"] >= cutoff_120) & (df["_dt"] < cutoff_90)]

    def _disc_rate(subset):
        rev = subset["revenue_delivery"].sum()
        disc = subset["discount_delivery"].sum()
        if rev <= 0:
            return 0.0
        # discount rate as pct of gross (revenue + discount)
        gross = rev + disc
        return round(disc / gross * 100, 1) if gross > 0 else 0.0

    rate_current = _disc_rate(last_30)
    rate_3mo = _disc_rate(three_mo_ago)

    if rate_current > rate_3mo + 2:
        trend = "rising"
    elif rate_current < rate_3mo - 2:
        trend = "falling"
    else:
        trend = "stable"

    # BOGO annualized from trailing 30 days
    daily_disc = last_30["discount_delivery"].sum() / max(len(last_30), 1)
    bogo_annual = round(daily_disc * 365, 0)

    return {
        "delivery_discount_rate_current": rate_current,
        "delivery_discount_rate_3mo_ago": rate_3mo,
        "delivery_discount_trend": trend,
        "bogo_annualized": bogo_annual,
        "daily_delivery_discount": round(daily_disc, 0),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Monthly Actuals — merges POS time series with real accounting P&L
# ══════════════════════════════════════════════════════════════════════════════


def build_monthly_actuals(
    df: pd.DataFrame, real_pl: dict, expense_ratios: dict,
) -> list:
    """Aggregate daily time series into monthly P&L rows.

    Uses real accounting data where available for cost lines.
    Falls back to expense ratios for months without accounting data.
    """
    if df.empty:
        return []

    df = df.copy()
    df["_month"] = pd.to_datetime(df["date"]).dt.to_period("M")

    months = []
    for period, grp in df.groupby("_month"):
        month_str = str(period)
        dt = period.to_timestamp()
        label = dt.strftime("%b '%y")

        rev_total = grp["revenue_total"].sum()
        rev_instore = grp["revenue_instore"].sum()
        rev_delivery = grp["revenue_delivery"].sum()
        rev_catering = grp["revenue_catering"].sum()

        row = {
            "month": month_str,
            "label": label,
            "days": len(grp),
            "is_forecast": False,
            "revenue_instore": round(rev_instore, 2),
            "revenue_delivery": round(rev_delivery, 2),
            "revenue_catering": round(rev_catering, 2),
            "revenue_total": round(rev_total, 2),
            "orders_total": int(grp["orders_total"].sum()),
            "avg_daily_revenue": round(rev_total / len(grp), 2),
        }

        # Merge real accounting data if available
        pl = real_pl.get(month_str)
        if pl:
            row["total_income"] = round(pl["total_income"], 2)
            row["cogs"] = round(pl["cogs"], 2)
            row["gross_profit"] = round(pl["gross_profit"], 2)
            for key, _ in EXPENSE_GROUP_ORDER:
                row[key] = round(pl.get(key, 0), 2)
            row["total_opex"] = round(pl["total_opex"], 2)
            row["operating_income"] = round(pl["operating_income"], 2)
            row["other_income"] = round(pl.get("other_income", 0), 2)
            row["net_income"] = round(pl["net_income"], 2)
            row["has_accounting"] = True
        else:
            # No accounting data — apply ratios to POS revenue
            row["total_income"] = round(rev_total, 2)
            cogs = rev_total * expense_ratios.get("cogs", 0.24)
            gross_profit = rev_total - cogs
            row["cogs"] = round(cogs, 2)
            row["gross_profit"] = round(gross_profit, 2)

            total_opex = 0.0
            for key, _ in EXPENSE_GROUP_ORDER:
                val = rev_total * expense_ratios.get(key, 0)
                row[key] = round(val, 2)
                total_opex += val

            row["total_opex"] = round(total_opex, 2)
            row["operating_income"] = round(gross_profit - total_opex, 2)
            row["other_income"] = round(
                rev_total * expense_ratios.get("other_income", 0), 2,
            )
            row["net_income"] = round(
                row["operating_income"] + row["other_income"], 2,
            )
            row["has_accounting"] = False

        months.append(row)

    return sorted(months, key=lambda m: m["month"])


# ══════════════════════════════════════════════════════════════════════════════
# Step 5: Recompose Daily Forecast
# ══════════════════════════════════════════════════════════════════════════════


def _generate_daily_forecast(
    trend: dict,
    dow_indices: dict,
    seasonal_indices: dict,
    start_date: str,
    forecast_start: str,
    forecast_end: str,
    channel_trends: dict = None,
    channel_dow: dict = None,
    channel_seasonal: dict = None,
    catering_baseline: dict = None,
) -> list:
    """Generate daily revenue forecasts for a date range.

    If channel models are provided, forecasts each channel independently
    and sums for total. Otherwise falls back to aggregate model.
    """
    ref_date = datetime.strptime(start_date, "%Y-%m-%d")
    current = datetime.strptime(forecast_start, "%Y-%m-%d")
    end = datetime.strptime(forecast_end, "%Y-%m-%d")

    use_channels = (
        channel_trends is not None
        and channel_dow is not None
        and channel_seasonal is not None
    )
    catering_daily = (catering_baseline or {}).get("daily_avg", 0)

    slope = trend["slope"]
    intercept = trend["intercept"]

    daily = []
    while current <= end:
        if use_channels:
            rev_in = _forecast_channel_day(
                current, "instore", channel_trends, channel_dow, channel_seasonal,
            )
            rev_del = _forecast_channel_day(
                current, "delivery", channel_trends, channel_dow, channel_seasonal,
            )
            rev_cat = catering_daily
            forecast_rev = rev_in + rev_del + rev_cat
        else:
            day_num = (current - ref_date).days
            trend_val = slope * day_num + intercept
            dow_idx = dow_indices.get(current.weekday(), 1.0)
            seas_idx = seasonal_indices.get(current.month, 1.0)
            forecast_rev = max(trend_val * (dow_idx / 7.0) * seas_idx, 0)
            rev_in = None
            rev_del = None
            rev_cat = None

        daily.append({
            "date": current.strftime("%Y-%m-%d"),
            "dow": current.weekday(),
            "month": current.strftime("%Y-%m"),
            "revenue_forecast": round(forecast_rev, 2),
            "revenue_instore": round(rev_in, 2) if rev_in is not None else None,
            "revenue_delivery": round(rev_del, 2) if rev_del is not None else None,
            "revenue_catering": round(rev_cat, 2) if rev_cat is not None else None,
        })
        current += timedelta(days=1)

    return daily


def _forecast_one_day(
    dt: datetime, trend: dict, dow_indices: dict, seasonal_indices: dict,
) -> float:
    """Forecast revenue for a single day."""
    if not trend.get("start_date"):
        return 0.0
    ref_date = datetime.strptime(trend["start_date"], "%Y-%m-%d")
    day_num = (dt - ref_date).days
    trend_val = trend["slope"] * day_num + trend["intercept"]
    dow_idx = dow_indices.get(dt.weekday(), 1.0)
    seas_idx = seasonal_indices.get(dt.month, 1.0)
    return max(trend_val * (dow_idx / 7.0) * seas_idx, 0)


# ══════════════════════════════════════════════════════════════════════════════
# Aggregate Forecast → Months
# ══════════════════════════════════════════════════════════════════════════════


def _aggregate_forecast_months(
    daily_forecast: list,
    channel_mix: dict,
    expense_ratios: dict,
) -> list:
    """Aggregate daily forecasts into monthly P&L rows with full line items.

    Uses per-channel revenues from daily forecast if available,
    otherwise falls back to channel_mix split.
    """
    by_month = defaultdict(list)
    for day in daily_forecast:
        by_month[day["month"]].append(day)

    months = []
    for month_str in sorted(by_month.keys()):
        days = by_month[month_str]
        rev_total = sum(d["revenue_forecast"] for d in days)

        # Sum per-channel if available
        has_channels = days[0].get("revenue_instore") is not None
        if has_channels:
            r_in = sum(d.get("revenue_instore", 0) or 0 for d in days)
            r_del = sum(d.get("revenue_delivery", 0) or 0 for d in days)
            r_cat = sum(d.get("revenue_catering", 0) or 0 for d in days)
        else:
            r_in = r_del = r_cat = None

        dt = datetime.strptime(month_str + "-01", "%Y-%m-%d")
        label = dt.strftime("%b '%y")

        row = _apply_expense_ratios(
            rev_total, channel_mix, expense_ratios,
            rev_instore=r_in, rev_delivery=r_del, rev_catering=r_cat,
        )
        row.update({
            "month": month_str,
            "label": label,
            "days": len(days),
            "orders_total": 0,
            "avg_daily_revenue": round(rev_total / len(days), 2),
            "is_forecast": True,
            "has_accounting": False,
        })
        months.append(row)

    return months


# ══════════════════════════════════════════════════════════════════════════════
# Aggregate Forecast → Weeks
# ══════════════════════════════════════════════════════════════════════════════


def _aggregate_forecast_weeks(
    daily_forecast: list,
    channel_mix: dict,
    expense_ratios: dict,
) -> list:
    """Aggregate daily forecasts into weekly P&L rows with full line items."""
    if not daily_forecast:
        return []

    by_week = defaultdict(list)
    for day in daily_forecast:
        dt = datetime.strptime(day["date"], "%Y-%m-%d")
        week_start = dt - timedelta(days=dt.weekday())
        key = week_start.strftime("%Y-%m-%d")
        by_week[key].append(day)

    weeks = []
    for week_key in sorted(by_week.keys()):
        days = by_week[week_key]
        rev_total = sum(d["revenue_forecast"] for d in days)

        has_channels = days[0].get("revenue_instore") is not None
        if has_channels:
            r_in = sum(d.get("revenue_instore", 0) or 0 for d in days)
            r_del = sum(d.get("revenue_delivery", 0) or 0 for d in days)
            r_cat = sum(d.get("revenue_catering", 0) or 0 for d in days)
        else:
            r_in = r_del = r_cat = None

        week_start = datetime.strptime(week_key, "%Y-%m-%d")
        week_end = week_start + timedelta(days=len(days) - 1)
        label = "%s - %s" % (
            week_start.strftime("%b %d"),
            week_end.strftime("%b %d"),
        )
        month = week_start.strftime("%Y-%m")

        row = _apply_expense_ratios(
            rev_total, channel_mix, expense_ratios,
            rev_instore=r_in, rev_delivery=r_del, rev_catering=r_cat,
        )
        row.update({
            "week_start": week_key,
            "label": label,
            "month": month,
            "days": len(days),
        })
        weeks.append(row)

    return weeks


# ══════════════════════════════════════════════════════════════════════════════
# Next-Week Daily Projection
# ══════════════════════════════════════════════════════════════════════════════


def _fetch_week_weather(next_monday):
    """Fetch weather for 7 days starting from next_monday.

    Uses the Open-Meteo forecast API (up to 7 days ahead).
    Returns dict of {YYYYMMDD: weather_dict}.
    """
    try:
        from fetch_weather_data import get_forecast_weather, compute_weather_multiplier
    except ImportError:
        return {}

    weather_by_date = {}
    for i in range(7):
        dt = next_monday + timedelta(days=i)
        date_str = dt.strftime("%Y%m%d")
        try:
            w = get_forecast_weather(date_str)
            if w:
                mult, reasons = compute_weather_multiplier(w)
                w["multiplier"] = mult
                w["multiplier_reasons"] = reasons
                weather_by_date[date_str] = w
        except Exception:
            pass

    return weather_by_date


def _generate_next_week(
    trend: dict,
    dow_indices: dict,
    seasonal_indices: dict,
    channel_mix: dict,
    expense_ratios: dict,
    channel_trends: dict = None,
    channel_dow: dict = None,
    channel_seasonal: dict = None,
    catering_baseline: dict = None,
) -> dict:
    """Generate day-by-day P&L projection for the upcoming Mon-Sun.

    Uses per-channel models if provided, otherwise falls back to aggregate.
    Includes weather forecast for each day (Open-Meteo forecast API).
    Returns dict with week_label, days (list of 7 daily P&L rows), and totals.
    """
    today = datetime.now()
    dow = today.weekday()  # 0=Mon

    # Find the next Monday (if today is Mon, show the NEXT week)
    if dow == 0:
        next_monday = today + timedelta(days=7)
    else:
        next_monday = today + timedelta(days=(7 - dow))

    next_sunday = next_monday + timedelta(days=6)

    if not trend.get("start_date"):
        return {"week_label": "", "days": [], "totals": {}}

    use_channels = (
        channel_trends is not None
        and channel_dow is not None
        and channel_seasonal is not None
    )
    catering_daily = (catering_baseline or {}).get("daily_avg", 0)

    # Fetch weather for the whole week
    week_weather = _fetch_week_weather(next_monday)

    days = []
    for i in range(7):
        dt = next_monday + timedelta(days=i)

        # Forecast per-channel or aggregate
        if use_channels:
            rev_in = _forecast_channel_day(
                dt, "instore", channel_trends, channel_dow, channel_seasonal,
            )
            rev_del = _forecast_channel_day(
                dt, "delivery", channel_trends, channel_dow, channel_seasonal,
            )
            rev_cat = catering_daily
            base_rev = rev_in + rev_del + rev_cat
        else:
            base_rev = _forecast_one_day(dt, trend, dow_indices, seasonal_indices)
            rev_in = rev_del = rev_cat = None

        # Apply weather multiplier if available
        date_str = dt.strftime("%Y%m%d")
        w = week_weather.get(date_str)
        weather_mult = 1.0
        weather_reasons = []
        if w:
            weather_mult = w.get("multiplier", 1.0)
            weather_reasons = w.get("multiplier_reasons", [])

        forecast_rev = base_rev * weather_mult

        # Scale per-channel by weather too
        if rev_in is not None:
            rev_in *= weather_mult
            rev_del *= weather_mult
            rev_cat *= weather_mult

        row = _apply_expense_ratios(
            forecast_rev, channel_mix, expense_ratios,
            rev_instore=rev_in, rev_delivery=rev_del, rev_catering=rev_cat,
        )

        # Build explanation with weather
        expl = build_day_explanation(dt, trend, dow_indices, seasonal_indices)

        # Add per-channel info to explanation
        if rev_in is not None:
            expl["channel_instore"] = round(rev_in, 0)
            expl["channel_delivery"] = round(rev_del, 0)
            expl["channel_catering"] = round(rev_cat, 0)

        if weather_mult != 1.0:
            weather_effect_pct = round((weather_mult - 1.0) * 100, 1)
            reason_str = "; ".join(weather_reasons) if weather_reasons else ""
            expl["weather_multiplier"] = weather_mult
            expl["weather_effect_pct"] = weather_effect_pct
            expl["weather_reason"] = reason_str
            expl["narrative"] += " Weather (%s): %+.0f%%." % (reason_str, weather_effect_pct)
            expl["prediction"] = round(forecast_rev, 2)

        # Include weather summary in the day row
        weather_summary = None
        if w:
            weather_summary = {
                "conditions": w.get("conditions", ""),
                "temp_high": w.get("temp_high"),
                "temp_low": w.get("temp_low"),
                "multiplier": weather_mult,
            }

        row.update({
            "date": dt.strftime("%Y-%m-%d"),
            "dow": dt.weekday(),
            "dow_name": _DOW_NAMES[dt.weekday()],
            "label": dt.strftime("%a %b %d"),
            "is_forecast": True,
            "explanation": expl,
            "weather": weather_summary,
        })
        # Add per-channel values to the day row for HTML rendering
        if rev_in is not None:
            row["channel_instore"] = round(rev_in, 2)
            row["channel_delivery"] = round(rev_del, 2)
            row["channel_catering"] = round(rev_cat, 2)
        days.append(row)

    # Compute week totals
    totals = {}
    sum_keys = [
        "revenue_instore", "revenue_delivery", "revenue_catering",
        "revenue_total", "total_income", "cogs", "gross_profit",
    ] + [k for k, _ in EXPENSE_GROUP_ORDER] + [
        "total_opex", "operating_income", "other_income", "net_income",
    ]
    for key in sum_keys:
        totals[key] = round(sum(d.get(key, 0) for d in days), 2)

    week_label = "%s - %s" % (
        next_monday.strftime("%b %d"),
        next_sunday.strftime("%b %d"),
    )

    return {"week_label": week_label, "days": days, "totals": totals}


# ══════════════════════════════════════════════════════════════════════════════
# Last-Week Review — Actual vs Predicted
# ══════════════════════════════════════════════════════════════════════════════


def _generate_week_review(
    df: pd.DataFrame,
    trend: dict,
    dow_indices: dict,
    seasonal_indices: dict,
) -> dict:
    """Compare last week's actual revenue with model predictions.

    Returns dict with week_label, days, and summary stats.
    """
    today = datetime.now()
    dow = today.weekday()

    # Last complete Mon-Sun before today
    # If today is Monday: last Mon = 7 days ago
    # If today is Saturday (5): last Mon = 5+7 = 12 days ago? No...
    # We want the most recent *complete* week.
    # Current week started on Monday = today - dow days.
    # Last week started on Monday = today - dow - 7 days.
    this_monday = today - timedelta(days=dow)
    last_monday = this_monday - timedelta(days=7)
    last_sunday = last_monday + timedelta(days=6)

    if not trend.get("start_date"):
        return {"week_label": "", "days": [], "summary": {}}

    # Get actual data from time series
    df = df.copy()
    df["_dt"] = pd.to_datetime(df["date"])
    week_data = df[
        (df["_dt"] >= pd.Timestamp(last_monday.strftime("%Y-%m-%d")))
        & (df["_dt"] <= pd.Timestamp(last_sunday.strftime("%Y-%m-%d")))
    ]

    actuals_by_date = {}
    for _, row in week_data.iterrows():
        actuals_by_date[row["date"]] = float(row["revenue_total"])

    days = []
    total_actual = 0.0
    total_predicted = 0.0
    days_with_data = 0

    for i in range(7):
        dt = last_monday + timedelta(days=i)
        date_str = dt.strftime("%Y-%m-%d")

        predicted = _forecast_one_day(dt, trend, dow_indices, seasonal_indices)
        actual = actuals_by_date.get(date_str)

        diff = None
        error_pct = None
        if actual is not None:
            diff = predicted - actual
            error_pct = (diff / actual * 100) if actual else 0
            total_actual += actual
            total_predicted += predicted
            days_with_data += 1

        days.append({
            "date": date_str,
            "dow_name": _DOW_NAMES[dt.weekday()],
            "label": dt.strftime("%a %b %d"),
            "actual": round(actual, 2) if actual is not None else None,
            "predicted": round(predicted, 2),
            "diff": round(diff, 2) if diff is not None else None,
            "error_pct": round(error_pct, 1) if error_pct is not None else None,
        })

    total_error_pct = None
    if total_actual > 0:
        total_error_pct = round(
            (total_predicted - total_actual) / total_actual * 100, 1,
        )

    week_label = "%s - %s" % (
        last_monday.strftime("%b %d"),
        last_sunday.strftime("%b %d"),
    )

    return {
        "week_label": week_label,
        "days": days,
        "summary": {
            "total_actual": round(total_actual, 2),
            "total_predicted": round(total_predicted, 2),
            "total_error_pct": total_error_pct,
            "days_with_data": days_with_data,
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2026 Annual Summary
# ══════════════════════════════════════════════════════════════════════════════


def _compute_annual_2026(actuals: list, forecast_months: list) -> dict:
    """Build the full 2026 annual P&L: actuals where available, forecast for rest."""
    actual_2026 = [m for m in actuals if m["month"].startswith("2026")]
    actual_months_set = {m["month"] for m in actual_2026}

    forecast_2026 = [
        m for m in forecast_months
        if m["month"].startswith("2026") and m["month"] not in actual_months_set
    ]

    all_2026 = sorted(actual_2026 + forecast_2026, key=lambda m: m["month"])

    if not all_2026:
        return {"months": [], "annual_total": {}}

    # Sum all P&L lines for annual total
    sum_keys = [
        "revenue_total", "revenue_instore", "revenue_delivery", "revenue_catering",
        "total_income", "cogs", "gross_profit",
    ] + [k for k, _ in EXPENSE_GROUP_ORDER] + [
        "total_opex", "operating_income", "other_income", "net_income",
    ]

    annual_total = {}
    for key in sum_keys:
        annual_total[key] = round(sum(m.get(key, 0) for m in all_2026), 2)

    annual_total["months_actual"] = len(actual_2026)
    annual_total["months_forecast"] = len(forecast_2026)

    return {"months": all_2026, "annual_total": annual_total}


# ══════════════════════════════════════════════════════════════════════════════
# KPIs
# ══════════════════════════════════════════════════════════════════════════════


def _compute_kpis(
    actuals: list,
    forecast: list,
    trend: dict,
    annual_2026: dict,
    expense_ratios: dict,
) -> dict:
    """Compute top-line KPIs from actuals and forecast."""
    full_months = [m for m in actuals if m["days"] >= 25]

    # Trailing 3-month run rate
    if len(full_months) >= 3:
        run_rate = sum(m["revenue_total"] for m in full_months[-3:]) / 3
    elif full_months:
        run_rate = sum(m["revenue_total"] for m in full_months) / len(full_months)
    else:
        run_rate = 0

    # Forecast averages
    forecast_3mo = 0
    if forecast:
        f3 = forecast[:3]
        forecast_3mo = sum(m["revenue_total"] for m in f3) / len(f3)

    # Monthly growth rate from trend
    monthly_growth = 0
    if trend["slope"] and trend["intercept"]:
        daily_base = trend["intercept"] + trend["slope"] * 200
        if daily_base > 0:
            monthly_growth = (trend["slope"] * 30) / daily_base

    # Best/worst actual months
    if full_months:
        best = max(full_months, key=lambda m: m["revenue_total"])
        worst = min(full_months, key=lambda m: m["revenue_total"])
    else:
        best = worst = {"label": "N/A", "revenue_total": 0}

    # Projected next quarter profit
    next_q_profit = sum(m["net_income"] for m in forecast[:3]) if forecast else 0

    # 2026 annual totals
    annual_rev = 0
    annual_net = 0
    if annual_2026 and annual_2026.get("annual_total"):
        at = annual_2026["annual_total"]
        annual_rev = at.get("revenue_total", 0)
        annual_net = at.get("net_income", 0)

    # Gross margin and operating margin from recent actuals
    recent = full_months[-3:] if len(full_months) >= 3 else full_months
    total_inc = sum(m.get("total_income", m["revenue_total"]) for m in recent)
    total_gp = sum(m.get("gross_profit", 0) for m in recent)
    total_oi = sum(m.get("operating_income", 0) for m in recent)
    gross_margin = (total_gp / total_inc * 100) if total_inc else 0
    operating_margin = (total_oi / total_inc * 100) if total_inc else 0

    return {
        "run_rate": round(run_rate, 2),
        "forecast_3mo_avg": round(forecast_3mo, 2),
        "monthly_growth_pct": round(monthly_growth * 100, 2),
        "best_month": best["label"],
        "best_month_rev": best["revenue_total"],
        "worst_month": worst["label"],
        "worst_month_rev": worst["revenue_total"],
        "next_quarter_profit": round(next_q_profit, 2),
        "annual_2026_revenue": round(annual_rev, 2),
        "annual_2026_net_income": round(annual_net, 2),
        "gross_margin_pct": round(gross_margin, 1),
        "operating_margin_pct": round(operating_margin, 1),
        "cogs_pct": round(expense_ratios.get("cogs", 0) * 100, 1),
        "labor_pct": round(expense_ratios.get("labor", 0) * 100, 1),
        "third_party_pct": round(expense_ratios.get("third_party_fees", 0) * 100, 1),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Backtest
# ══════════════════════════════════════════════════════════════════════════════


def generate_backtest(holdout_start: str = "2026-01-01") -> dict:
    """Backtest: train on data before holdout, predict holdout period, compare."""
    logger.info("Running backtest with holdout from %s...", holdout_start)

    df = get_timeseries()
    if df.empty:
        return {"weeks": [], "months": [], "summary": {}}

    df["_dt"] = pd.to_datetime(df["date"])
    cutoff = pd.Timestamp(holdout_start)

    train = df[df["_dt"] < cutoff].copy()
    holdout = df[df["_dt"] >= cutoff].copy()

    if train.empty or holdout.empty:
        return {"weeks": [], "months": [], "summary": {}}

    dow_indices = compute_dow_indices(train)
    seasonal_indices = compute_seasonal_indices(train)
    trend = compute_trend(train, dow_indices, seasonal_indices)

    if not trend["start_date"]:
        return {"weeks": [], "months": [], "summary": {}}

    holdout_end = holdout["_dt"].max().strftime("%Y-%m-%d")
    daily_pred = _generate_daily_forecast(
        trend, dow_indices, seasonal_indices,
        trend["start_date"], holdout_start, holdout_end,
    )
    pred_by_date = {d["date"]: d["revenue_forecast"] for d in daily_pred}

    # Weekly comparison
    holdout = holdout.copy()
    holdout["_week_start"] = holdout["_dt"].dt.to_period("W-SUN").apply(
        lambda p: p.start_time
    )
    weeks = []
    for week_start, grp in holdout.groupby("_week_start"):
        actual_rev = grp["revenue_total"].sum()
        pred_rev = sum(
            pred_by_date.get(d, 0) for d in grp["date"].tolist()
        )
        week_end = grp["_dt"].max()
        label = "%s - %s" % (
            week_start.strftime("%b %d"),
            week_end.strftime("%b %d"),
        )
        error_pct = ((pred_rev - actual_rev) / actual_rev * 100) if actual_rev else 0
        weeks.append({
            "label": label,
            "week_start": week_start.strftime("%Y-%m-%d"),
            "days": len(grp),
            "actual": round(actual_rev, 2),
            "predicted": round(pred_rev, 2),
            "diff": round(pred_rev - actual_rev, 2),
            "error_pct": round(error_pct, 1),
        })

    # Monthly comparison
    holdout["_period"] = holdout["_dt"].dt.to_period("M")
    bt_months = []
    for period, grp in holdout.groupby("_period"):
        actual_rev = grp["revenue_total"].sum()
        pred_rev = sum(
            pred_by_date.get(d, 0) for d in grp["date"].tolist()
        )
        label = period.to_timestamp().strftime("%b '%y")
        error_pct = ((pred_rev - actual_rev) / actual_rev * 100) if actual_rev else 0
        bt_months.append({
            "label": label,
            "month": str(period),
            "days": len(grp),
            "actual": round(actual_rev, 2),
            "predicted": round(pred_rev, 2),
            "diff": round(pred_rev - actual_rev, 2),
            "error_pct": round(error_pct, 1),
        })

    total_actual = sum(w["actual"] for w in weeks)
    total_pred = sum(w["predicted"] for w in weeks)
    mape = np.mean([abs(w["error_pct"]) for w in weeks]) if weeks else 0

    summary = {
        "holdout_start": holdout_start,
        "holdout_end": holdout_end,
        "holdout_days": len(holdout),
        "total_actual": round(total_actual, 2),
        "total_predicted": round(total_pred, 2),
        "total_error_pct": round(
            (total_pred - total_actual) / total_actual * 100, 1,
        ) if total_actual else 0,
        "mape_weekly": round(mape, 1),
        "training_days": len(train),
    }

    return {"weeks": weeks, "months": bt_months, "summary": summary}


# ══════════════════════════════════════════════════════════════════════════════
# Catering Growth Model
# ══════════════════════════════════════════════════════════════════════════════

# New catering manager — aggressive 3-month ramp to $15-20K/month
CATERING_TARGET_MONTHLY = 17500  # midpoint of $15-20K range
CATERING_RAMP_MONTHS = 3


def _apply_catering_growth(forecast_months, actuals, expense_ratios):
    """Apply catering growth ramp to forecast months.

    Calculates current catering level from trailing 3 actual months,
    then linearly ramps to target over CATERING_RAMP_MONTHS.
    Adds incremental revenue (catering is additive, not cannibalistic).
    """
    # Current catering from trailing 3 actual months
    recent = [m for m in actuals if m.get("days", 0) >= 25][-3:]
    if not recent:
        return forecast_months

    current_catering = sum(m.get("revenue_catering", 0) for m in recent) / len(recent)
    gap = CATERING_TARGET_MONTHLY - current_catering
    if gap <= 0:
        return forecast_months  # already at or above target

    for i, month in enumerate(forecast_months):
        if i < CATERING_RAMP_MONTHS:
            ramp_pct = (i + 1) / CATERING_RAMP_MONTHS
            catering_add = gap * ramp_pct
        else:
            catering_add = gap

        # Add incremental catering revenue
        old_rev = month["revenue_total"]
        new_rev = old_rev + catering_add
        month["revenue_catering"] = round(month.get("revenue_catering", 0) + catering_add, 2)
        month["revenue_total"] = round(new_rev, 2)
        month["total_income"] = round(new_rev, 2)

        # Recalculate costs with new revenue
        cogs = new_rev * expense_ratios.get("cogs", 0.24)
        gross_profit = new_rev - cogs
        total_opex = 0.0
        for key, _ in EXPENSE_GROUP_ORDER:
            val = new_rev * expense_ratios.get(key, 0)
            month[key] = round(val, 2)
            total_opex += val

        month["cogs"] = round(cogs, 2)
        month["gross_profit"] = round(gross_profit, 2)
        month["total_opex"] = round(total_opex, 2)
        month["operating_income"] = round(gross_profit - total_opex, 2)
        other_inc = new_rev * expense_ratios.get("other_income", 0)
        month["other_income"] = round(other_inc, 2)
        month["net_income"] = round(gross_profit - total_opex + other_inc, 2)

        if month.get("days"):
            month["avg_daily_revenue"] = round(new_rev / month["days"], 2)

    return forecast_months


# ══════════════════════════════════════════════════════════════════════════════
# Explainability Helpers
# ══════════════════════════════════════════════════════════════════════════════

_DOW_FULL_NAMES = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
]


def build_day_explanation(dt, trend, dow_indices, seasonal_indices):
    """Build explanation dict for a forecasted day.

    Returns dict with structured explanation data.
    """
    if not trend.get("start_date"):
        return {}

    ref_date = datetime.strptime(trend["start_date"], "%Y-%m-%d")
    day_num = (dt - ref_date).days
    trend_val = trend["slope"] * day_num + trend["intercept"]
    dow_idx = dow_indices.get(dt.weekday(), 1.0)
    seas_idx = seasonal_indices.get(dt.month, 1.0)
    prediction = max(trend_val * (dow_idx / 7.0) * seas_idx, 0)

    # Base daily = trend / 7 (the DOW index normalization)
    base_daily = trend_val / 7.0
    dow_effect_pct = (dow_idx / 1.0 - 1) * 100
    seas_effect_pct = (seas_idx - 1.0) * 100
    dow_name = _DOW_FULL_NAMES[dt.weekday()]
    month_name = dt.strftime("%B")

    parts = []
    parts.append("Base: $%s/day." % "{:,.0f}".format(base_daily))
    if abs(dow_effect_pct) > 1:
        parts.append("%s: %+.0f%% vs average." % (dow_name, dow_effect_pct))
    if abs(seas_effect_pct) > 1:
        parts.append("%s: %+.0f%% seasonal." % (month_name, seas_effect_pct))

    return {
        "trend_value": round(base_daily, 2),
        "dow_name": dow_name,
        "dow_index": round(dow_idx, 3),
        "dow_effect_pct": round(dow_effect_pct, 1),
        "seasonal_index": round(seas_idx, 3),
        "seasonal_effect_pct": round(seas_effect_pct, 1),
        "prediction": round(prediction, 2),
        "narrative": " ".join(parts),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ══════════════════════════════════════════════════════════════════════════════


def generate_forecast(months_ahead: int = 6) -> dict:
    """Generate complete P&L forecast with real line items.

    Returns dict with actuals, forecast (monthly + weekly), backtest,
    2026 annual summary, next-week projection, last-week review,
    indices, trend, and KPIs.
    """
    logger.info("Generating P&L forecast (%d months ahead)...", months_ahead)

    # Step 1: Get time series
    df = get_timeseries()
    if df.empty:
        return _empty_result()

    # Load real P&L data and compute expense ratios
    real_pl = _load_real_pl()
    expense_ratios = _compute_expense_ratios(real_pl, trailing_months=6)

    # Step 2: DOW indices (aggregate — kept for backtest/review)
    dow_indices = compute_dow_indices(df)

    # Step 3: Seasonal indices (aggregate)
    seasonal_indices = compute_seasonal_indices(df)

    # Step 4: Trend (aggregate)
    trend = compute_trend(df, dow_indices, seasonal_indices)

    # Channel mix from trailing 3 months (fallback for legacy paths)
    channel_mix = compute_channel_mix(df, trailing_months=3)

    # Per-channel models (in-store + delivery independently, catering as baseline)
    channel_dow = compute_channel_dow_indices(df)
    channel_seasonal = compute_channel_seasonal_indices(df)
    channel_trends = compute_channel_trends(df, channel_dow, channel_seasonal)
    catering_baseline = compute_catering_baseline(df, trailing_days=90)

    # Discount health metrics
    disc_metrics = compute_discount_metrics(df)

    # Build monthly actuals (merges POS + real accounting)
    actuals = build_monthly_actuals(df, real_pl, expense_ratios)

    # Determine forecast period — project through Dec 2026
    last_date = pd.to_datetime(df["date"]).max()
    forecast_start = (last_date + timedelta(days=1)).strftime("%Y-%m-%d")
    forecast_end = "2026-12-31"

    # Step 5: Generate forecast (per-channel)
    forecast_months = []
    forecast_weeks = []
    if trend["start_date"]:
        daily_forecast = _generate_daily_forecast(
            trend, dow_indices, seasonal_indices,
            trend["start_date"], forecast_start, forecast_end,
            channel_trends=channel_trends,
            channel_dow=channel_dow,
            channel_seasonal=channel_seasonal,
            catering_baseline=catering_baseline,
        )
        forecast_months = _aggregate_forecast_months(
            daily_forecast, channel_mix, expense_ratios,
        )
        forecast_weeks = _aggregate_forecast_weeks(
            daily_forecast, channel_mix, expense_ratios,
        )

    # Apply catering growth ramp
    if forecast_months:
        forecast_months = _apply_catering_growth(
            forecast_months, actuals, expense_ratios,
        )

    # Backtest
    backtest = generate_backtest(holdout_start="2026-01-01")

    # 2026 annual summary
    annual_2026 = _compute_annual_2026(actuals, forecast_months)

    # Next-week daily projection (per-channel + weather)
    next_week = _generate_next_week(
        trend, dow_indices, seasonal_indices, channel_mix, expense_ratios,
        channel_trends=channel_trends,
        channel_dow=channel_dow,
        channel_seasonal=channel_seasonal,
        catering_baseline=catering_baseline,
    )

    # Last-week review (actual vs predicted)
    week_review = _generate_week_review(df, trend, dow_indices, seasonal_indices)

    # KPIs
    kpis = _compute_kpis(actuals, forecast_months, trend, annual_2026, expense_ratios)

    # Assumptions
    assumptions = {
        "data_start": df["date"].min(),
        "data_end": df["date"].max(),
        "data_days": len(df),
        "forecast_months": months_ahead,
        "has_accounting": bool(real_pl),
        "accounting_months": len(real_pl),
    }

    result = {
        "actuals": actuals,
        "forecast": forecast_months,
        "forecast_weeks": forecast_weeks,
        "backtest": backtest,
        "annual_2026": annual_2026,
        "next_week": next_week,
        "week_review": week_review,
        "dow_indices": {_DOW_NAMES[i]: round(v, 3) for i, v in dow_indices.items()},
        "seasonal_indices": {
            _MONTH_NAMES[m - 1]: round(v, 3) for m, v in seasonal_indices.items()
        },
        "trend": trend,
        "channel_mix": channel_mix,
        "expense_ratios": expense_ratios,
        "assumptions": assumptions,
        "kpis": kpis,
        "channel_dow_indices": {
            ch: {_DOW_NAMES[i]: round(v, 3) for i, v in idx.items()}
            for ch, idx in channel_dow.items()
        },
        "channel_seasonal_indices": {
            ch: {_MONTH_NAMES[m - 1]: round(v, 3) for m, v in idx.items()}
            for ch, idx in channel_seasonal.items()
        },
        "channel_trends": channel_trends,
        "catering_baseline": catering_baseline,
        "discount_metrics": disc_metrics,
    }

    logger.info(
        "Forecast complete: %d actual months, %d forecast months, %d weeks",
        len(actuals), len(forecast_months), len(forecast_weeks),
    )
    return result


def _empty_result() -> dict:
    return {
        "actuals": [],
        "forecast": [],
        "forecast_weeks": [],
        "backtest": {"weeks": [], "months": [], "summary": {}},
        "annual_2026": {"months": [], "annual_total": {}},
        "next_week": {"week_label": "", "days": [], "totals": {}},
        "week_review": {"week_label": "", "days": [], "summary": {}},
        "dow_indices": {_DOW_NAMES[i]: 1.0 for i in range(7)},
        "seasonal_indices": {_MONTH_NAMES[m]: 1.0 for m in range(12)},
        "trend": {"slope": 0, "intercept": 0, "r_squared": 0, "start_date": ""},
        "channel_mix": {"instore": 0.33, "delivery": 0.64, "catering": 0.02, "other": 0.01},
        "expense_ratios": dict(_DEFAULT_RATIOS),
        "assumptions": {},
        "kpis": {},
    }
