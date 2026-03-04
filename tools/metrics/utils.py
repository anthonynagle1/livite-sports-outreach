"""Shared utilities, constants, and helpers for metrics computation."""

import re
import warnings
from datetime import datetime, timedelta
from statistics import median

import numpy as np
import pandas as pd

from calc_daily_profit import (
    MASTER_WAGES, CHANNEL_MAP, EMPLOYEE_ALIASES, PAYROLL_TAX_RATE, FOOD_COST_PCT,
    get_master_wage, calc_revenue, calc_labor, calc_fees,
)

warnings.filterwarnings("ignore", category=FutureWarning)

# ─── Reverse lookup: tracker name -> list of Toast Dining Options ───
TRACKER_TO_TOAST = {}
for toast_name, tracker_name in CHANNEL_MAP.items():
    TRACKER_TO_TOAST.setdefault(tracker_name, []).append(toast_name)

# ─── Channel groupings for walk-in / 3P / online ───
WALKIN_CHANNELS = {"To Go"}
THIRD_PARTY_CHANNELS = {
    "Uber Eats - Delivery", "Uber Eats - Takeout",
    "DoorDash - Delivery", "DoorDash - Takeout",
    "Grubhub - Delivery", "Grubhub - Takeout",
}
ONLINE_CHANNELS = {"Online Ordering - Takeout", "Online Ordering - Delivery"}

# ─── Menu group categories for cross-sell analysis ───
WRAP_KEYWORDS = ["wrap", "burrito", "bowl"]
SMOOTHIE_KEYWORDS = ["smoothie", "shake"]
SALAD_KEYWORDS = ["salad"]
JUICE_KEYWORDS = ["juice", "lemonade", "agua"]
SNACK_KEYWORDS = ["cookie", "chips", "snack", "brownie", "bar", "muffin", "granola"]
SOUP_KEYWORDS = ["soup"]

# ─── Daypart definitions (hour boundaries) ───
DAYPARTS = [
    (7, 11, "Morning"),
    (11, 14, "Lunch"),
    (14, 17, "Afternoon"),
    (17, 21, "Dinner"),
    (21, 23, "Late"),
]

# ─── Alt milk keywords ───
ALT_MILK_KEYWORDS = ["oat", "almond", "coconut"]
ALT_MILK_RECOMMENDED_PRICE = 0.75

def parse_toast_datetime(s):
    """Parse Toast timestamp to datetime.

    Handles multiple formats:
      'Feb 15, 2026 10:32:05 AM'   — long month name
      '2/15/26 9:02 AM'            — short numeric (2-digit year)
      '2/15/2026 9:02 AM'          — short numeric (4-digit year)
      '2026-02-15 10:32:05'        — ISO
    Returns None if parsing fails.
    """
    if pd.isna(s) or not isinstance(s, str) or not s.strip():
        return None
    s = s.strip()
    for fmt in (
        "%b %d, %Y %I:%M:%S %p",
        "%b %d, %Y %I:%M %p",
        "%m/%d/%y %I:%M %p",
        "%m/%d/%y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %I:%M:%S %p",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_fulfillment_time(s):
    """Parse '16 minutes and 12 seconds' to float minutes.

    Also handles single-unit strings like '3 minutes' or '45 seconds'.
    Returns None if parsing fails.
    """
    if pd.isna(s) or not isinstance(s, str) or not s.strip():
        return None
    s = s.strip().lower()
    minutes = 0.0
    min_match = re.search(r'(\d+)\s*minute', s)
    sec_match = re.search(r'(\d+)\s*second', s)
    hr_match = re.search(r'(\d+)\s*hour', s)
    if hr_match:
        minutes += int(hr_match.group(1)) * 60
    if min_match:
        minutes += int(min_match.group(1))
    if sec_match:
        minutes += int(sec_match.group(1)) / 60.0
    if not min_match and not sec_match and not hr_match:
        return None
    return round(minutes, 2)


def parse_duration(s):
    """Parse '0:08:23' (H:MM:SS) to float minutes.

    Returns None if parsing fails.
    """
    if pd.isna(s) or not isinstance(s, str) or not s.strip():
        return None
    s = s.strip()
    parts = s.split(":")
    try:
        if len(parts) == 3:
            h, m, sec = int(parts[0]), int(parts[1]), int(parts[2])
            return round(h * 60 + m + sec / 60.0, 2)
        elif len(parts) == 2:
            m, sec = int(parts[0]), int(parts[1])
            return round(m + sec / 60.0, 2)
    except (ValueError, TypeError):
        pass
    return None


# ═══════════════════════════════════════════════════════════════
#  INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════

def _safe_numeric(df, col):
    """Convert column to numeric, coercing errors, filling NaN with 0."""
    if col not in df.columns:
        return pd.Series(0, index=df.index)
    return pd.to_numeric(df[col], errors='coerce').fillna(0)


def _safe_div(a, b, default=0):
    """Safe division returning default when denominator is 0."""
    if b == 0 or pd.isna(b):
        return default
    return a / b


def _filter_voided_orders(df):
    """Filter out voided orders from OrderDetails."""
    df = df.copy()
    if 'Voided' in df.columns:
        df['Voided'] = df['Voided'].astype(str).str.strip().str.lower()
        df = df[~df['Voided'].isin(['true', '1', 'yes'])]
    return df


def _filter_voided_items(df):
    """Filter out voided items from ItemSelectionDetails."""
    df = df.copy()
    if 'Void?' in df.columns:
        df['Void?'] = df['Void?'].astype(str).str.strip().str.lower()
        df = df[~df['Void?'].isin(['true', '1', 'yes'])]
    return df


def _filter_voided_modifiers(df):
    """Filter out voided modifiers from ModifiersSelectionDetails."""
    df = df.copy()
    if 'Void?' in df.columns:
        df['Void?'] = df['Void?'].astype(str).str.strip().str.lower()
        df = df[~df['Void?'].isin(['true', '1', 'yes'])]
    return df


def _get_channel(dining_option):
    """Map a Toast Dining Option to its tracker channel name."""
    if pd.isna(dining_option):
        return "Unknown"
    return CHANNEL_MAP.get(str(dining_option).strip(), str(dining_option).strip())


def _get_channel_group(dining_option):
    """Classify a Toast Dining Option into walk-in / 3P / online."""
    if pd.isna(dining_option):
        return "Other"
    opt = str(dining_option).strip()
    if opt in WALKIN_CHANNELS:
        return "Walk-In"
    if opt in THIRD_PARTY_CHANNELS:
        return "3P"
    if opt in ONLINE_CHANNELS:
        return "Online"
    return "Other"


def _classify_menu_group(group_name):
    """Classify a Menu Group into a cross-sell category."""
    if pd.isna(group_name):
        return None
    g = str(group_name).strip().lower()
    for kw in WRAP_KEYWORDS:
        if kw in g:
            return "Wrap"
    for kw in SMOOTHIE_KEYWORDS:
        if kw in g:
            return "Smoothie"
    for kw in SALAD_KEYWORDS:
        if kw in g:
            return "Salad"
    for kw in JUICE_KEYWORDS:
        if kw in g:
            return "Juice"
    for kw in SNACK_KEYWORDS:
        if kw in g:
            return "Snack"
    for kw in SOUP_KEYWORDS:
        if kw in g:
            return "Soup"
    return None


def _get_daypart(hour):
    """Return daypart name for a given hour."""
    for start, end, name in DAYPARTS:
        if start <= hour < end:
            return name
    return "Other"

