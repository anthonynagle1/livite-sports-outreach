"""Compute vendor price trend alerts from Notion price entries.

Detects week-over-week and month-over-month price increases, identifies
multi-vendor items with savings potential, and aggregates spend data for
the alerts dashboard.
"""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()

# Add project root to path so vendor_prices.tools is importable
# __file__ → tools/vendor_alerts/data.py → tools/vendor_alerts → tools → project root
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from vendor_prices.tools.notion_sync import (
    get_all_items,
    get_price_entries,
    get_spending_summary,
)

logger = logging.getLogger(__name__)

# Thresholds
WOW_THRESHOLD = 0.10   # 10 % week-over-week increase → amber
MOM_THRESHOLD = 0.20   # 20 % month-over-month increase → red


def compute_vendor_alerts() -> dict:
    """Analyse price entries and return alerts + spend metrics.

    Returns a dict with keys:
        alerts, vendor_spend_trend, category_spend, multi_vendor_items,
        weekly_totals, summary
    """
    prices_db = os.getenv("NOTION_PRICES_DB_ID", "")
    items_db = os.getenv("NOTION_ITEMS_DB_ID", "")

    if not prices_db:
        logger.warning("NOTION_PRICES_DB_ID not set — returning empty alerts")
        return _empty_result()

    # ------------------------------------------------------------------
    # 1. Fetch raw data
    # ------------------------------------------------------------------
    try:
        all_entries = get_price_entries(prices_db)
    except Exception:
        logger.error("Failed to fetch price entries", exc_info=True)
        return _empty_result()

    # Filter to purchases only
    entries = [e for e in all_entries if e.get("upload_type") == "Purchase"]

    if not entries:
        return _empty_result()

    # Fetch items master for category + name lookup
    items_map: dict[str, dict] = {}
    if items_db:
        try:
            items_list = get_all_items(items_db)
            for it in items_list:
                items_map[it["id"]] = it
        except Exception:
            logger.warning("Failed to load Items Master", exc_info=True)

    # ------------------------------------------------------------------
    # 2. Build price alerts (WoW + MoM)
    # ------------------------------------------------------------------
    alerts = _compute_price_alerts(entries, items_map)

    # ------------------------------------------------------------------
    # 3. Multi-vendor comparison
    # ------------------------------------------------------------------
    multi_vendor = _compute_multi_vendor(entries, items_map)

    # ------------------------------------------------------------------
    # 4. Spend aggregations (reuse get_spending_summary when possible)
    # ------------------------------------------------------------------
    vendor_spend_trend: dict[str, list[dict]] = {}
    category_spend: dict[str, float] = {}
    weekly_totals: list[dict] = []
    grand_total = 0.0

    if prices_db and items_db:
        try:
            summary = get_spending_summary(prices_db, items_db, max_weeks=12)
            # vendor_spend_trend: {vendor: [{week, total}]}
            for vendor, weeks_data in summary.get("by_vendor", {}).items():
                vendor_spend_trend[vendor] = [
                    {"week": w["week"], "total": w["total"]} for w in weeks_data
                ]
            # category_spend: sum totals per category across all weeks
            for cat, weeks_data in summary.get("by_category", {}).items():
                category_spend[cat] = round(
                    sum(w["total"] for w in weeks_data), 2
                )
            weekly_totals = [
                {"week": w["week"], "total": w["total"]}
                for w in summary.get("weekly_totals", [])
            ]
            grand_total = summary.get("grand_total", 0.0)
        except Exception:
            logger.warning("Spending summary failed; deriving from entries", exc_info=True)
            vendor_spend_trend, category_spend, weekly_totals, grand_total = (
                _derive_spend_from_entries(entries, items_map)
            )
    else:
        vendor_spend_trend, category_spend, weekly_totals, grand_total = (
            _derive_spend_from_entries(entries, items_map)
        )

    # ------------------------------------------------------------------
    # 5. Summary
    # ------------------------------------------------------------------
    red_count = sum(1 for a in alerts if a["severity"] == "red")
    amber_count = sum(1 for a in alerts if a["severity"] == "amber")

    # Count unique items tracked (items that appear in entries)
    tracked_items = set()
    for e in entries:
        key = e.get("item_relation_id") or e.get("vendor_item_name", "")
        if key:
            tracked_items.add(key)

    return {
        "alerts": alerts,
        "vendor_spend_trend": vendor_spend_trend,
        "category_spend": category_spend,
        "multi_vendor_items": multi_vendor,
        "weekly_totals": weekly_totals,
        "summary": {
            "total_alerts": len(alerts),
            "red_count": red_count,
            "amber_count": amber_count,
            "grand_total": grand_total,
            "items_tracked": len(tracked_items),
        },
    }


# ======================================================================
# Internal helpers
# ======================================================================

def _empty_result() -> dict:
    return {
        "alerts": [],
        "vendor_spend_trend": {},
        "category_spend": {},
        "multi_vendor_items": [],
        "weekly_totals": [],
        "summary": {
            "total_alerts": 0,
            "red_count": 0,
            "amber_count": 0,
            "grand_total": 0.0,
            "items_tracked": 0,
        },
    }


def _item_display_name(entry: dict, items_map: dict[str, dict]) -> str:
    """Get best human-readable name for a price entry."""
    rel_id = entry.get("item_relation_id", "")
    if rel_id and rel_id in items_map:
        return items_map[rel_id].get("name", "") or entry.get("vendor_item_name", "Unknown")
    return entry.get("vendor_item_name", "") or "Unknown"


def _parse_date(d: str) -> str:
    """Normalise date string to YYYY-MM-DD (pass-through if already OK)."""
    if not d:
        return ""
    return d[:10]


def _compute_price_alerts(entries: list[dict], items_map: dict[str, dict]) -> list[dict]:
    """Detect WoW and MoM price increases.

    Groups entries by (item_key, vendor), sorts by date desc, and compares
    consecutive prices.
    """
    # Group by (item identifier, vendor)
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in entries:
        price = e.get("price")
        if price is None or price <= 0:
            continue
        item_key = e.get("item_relation_id") or e.get("vendor_item_name", "")
        vendor = e.get("vendor", "")
        if not item_key or not vendor:
            continue
        groups[(item_key, vendor)].append(e)

    alerts: list[dict] = []

    for (item_key, vendor), group in groups.items():
        # Sort by date descending (most recent first)
        sorted_group = sorted(
            group,
            key=lambda x: _parse_date(x.get("date", "")),
            reverse=True,
        )

        if len(sorted_group) < 2:
            continue

        latest = sorted_group[0]
        prev = sorted_group[1]
        current_price = latest.get("price", 0)
        prev_price = prev.get("price", 0)

        if prev_price <= 0 or current_price <= 0:
            continue

        item_name = _item_display_name(latest, items_map)

        # WoW alert (compare two most recent entries)
        wow_change = (current_price - prev_price) / prev_price
        if wow_change >= WOW_THRESHOLD:
            alerts.append({
                "item_name": item_name,
                "vendor": vendor,
                "current_price": round(current_price, 2),
                "prev_price": round(prev_price, 2),
                "pct_change": round(wow_change * 100, 1),
                "timeframe": "wow",
                "severity": "amber",
                "current_date": _parse_date(latest.get("date", "")),
                "prev_date": _parse_date(prev.get("date", "")),
            })

        # MoM alert — find entry 4+ weeks ago
        mom_entry = _find_entry_n_weeks_ago(sorted_group, 4)
        if mom_entry:
            old_price = mom_entry.get("price", 0)
            if old_price > 0:
                mom_change = (current_price - old_price) / old_price
                if mom_change >= MOM_THRESHOLD:
                    alerts.append({
                        "item_name": item_name,
                        "vendor": vendor,
                        "current_price": round(current_price, 2),
                        "prev_price": round(old_price, 2),
                        "pct_change": round(mom_change * 100, 1),
                        "timeframe": "mom",
                        "severity": "red",
                        "current_date": _parse_date(latest.get("date", "")),
                        "prev_date": _parse_date(mom_entry.get("date", "")),
                    })

    # Sort: red first, then amber, then by pct_change descending
    severity_order = {"red": 0, "amber": 1}
    alerts.sort(key=lambda a: (severity_order.get(a["severity"], 2), -a["pct_change"]))

    return alerts


def _find_entry_n_weeks_ago(sorted_entries: list[dict], n_weeks: int) -> dict | None:
    """Find the first entry that is at least n_weeks before the latest entry.

    sorted_entries must be sorted by date descending.
    """
    if len(sorted_entries) < 2:
        return None

    from datetime import datetime, timedelta

    latest_date_str = _parse_date(sorted_entries[0].get("date", ""))
    if not latest_date_str:
        return None

    try:
        latest_dt = datetime.strptime(latest_date_str, "%Y-%m-%d")
    except ValueError:
        return None

    cutoff = latest_dt - timedelta(weeks=n_weeks)

    for entry in sorted_entries[1:]:
        entry_date_str = _parse_date(entry.get("date", ""))
        if not entry_date_str:
            continue
        try:
            entry_dt = datetime.strptime(entry_date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if entry_dt <= cutoff:
            return entry

    return None


def _compute_multi_vendor(entries: list[dict], items_map: dict[str, dict]) -> list[dict]:
    """Find items available from multiple vendors and compute savings.

    Groups by item_relation_id (only items that exist in Items Master).
    """
    # Group latest price per (item_relation_id, vendor)
    latest_by_item_vendor: dict[tuple[str, str], dict] = {}
    for e in entries:
        rel_id = e.get("item_relation_id", "")
        vendor = e.get("vendor", "")
        price = e.get("price")
        if not rel_id or not vendor or not price or price <= 0:
            continue

        key = (rel_id, vendor)
        existing = latest_by_item_vendor.get(key)
        if existing is None:
            latest_by_item_vendor[key] = e
        else:
            if _parse_date(e.get("date", "")) > _parse_date(existing.get("date", "")):
                latest_by_item_vendor[key] = e

    # Group by item_relation_id
    items_vendors: dict[str, list[dict]] = defaultdict(list)
    for (rel_id, vendor), entry in latest_by_item_vendor.items():
        items_vendors[rel_id].append(entry)

    multi: list[dict] = []
    for rel_id, vendor_entries in items_vendors.items():
        if len(vendor_entries) < 2:
            continue

        item_name = items_map.get(rel_id, {}).get("name", "") or "Unknown"

        vendors_detail = []
        for ve in vendor_entries:
            vendors_detail.append({
                "vendor": ve.get("vendor", ""),
                "price": round(ve.get("price", 0), 2),
                "unit": ve.get("unit", ""),
                "date": _parse_date(ve.get("date", "")),
            })

        # Sort by price ascending
        vendors_detail.sort(key=lambda v: v["price"])
        cheapest = vendors_detail[0]
        most_expensive = vendors_detail[-1]
        spread = round(most_expensive["price"] - cheapest["price"], 2)

        # Estimate savings: use typical quantity from entries (default 1)
        quantities = [
            ve.get("quantity") or 1
            for ve in vendor_entries
            if (ve.get("quantity") or 0) > 0
        ]
        avg_qty = sum(quantities) / len(quantities) if quantities else 1
        savings = round(spread * avg_qty, 2)

        multi.append({
            "item_name": item_name,
            "vendors": vendors_detail,
            "cheapest_vendor": cheapest["vendor"],
            "price_spread": spread,
            "savings_potential": savings,
        })

    # Sort by savings potential descending
    multi.sort(key=lambda m: -m["savings_potential"])
    return multi


def _derive_spend_from_entries(
    entries: list[dict],
    items_map: dict[str, dict],
) -> tuple[dict, dict, list[dict], float]:
    """Fallback: build spend aggregations directly from entries when
    get_spending_summary is unavailable.
    """
    vendor_week_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    cat_totals: dict[str, float] = defaultdict(float)
    week_totals: dict[str, float] = defaultdict(float)

    for e in entries:
        week = e.get("week", "")
        if not week:
            continue
        vendor = e.get("vendor", "Unknown")
        amount = e.get("total_cost") or e.get("price", 0) or 0
        rel_id = e.get("item_relation_id", "")
        category = items_map.get(rel_id, {}).get("category", "Other") or "Other"

        vendor_week_totals[vendor][week] += amount
        cat_totals[category] += amount
        week_totals[week] += amount

    weeks_sorted = sorted(week_totals.keys())
    # Keep only last 12 weeks
    weeks_sorted = weeks_sorted[-12:]
    target_weeks = set(weeks_sorted)

    vendor_spend: dict[str, list[dict]] = {}
    for vendor, wt in vendor_week_totals.items():
        vendor_spend[vendor] = [
            {"week": w, "total": round(wt.get(w, 0), 2)} for w in weeks_sorted
        ]

    category_spend = {k: round(v, 2) for k, v in cat_totals.items()}

    wt_list = [
        {"week": w, "total": round(week_totals.get(w, 0), 2)} for w in weeks_sorted
    ]

    grand = round(sum(week_totals.get(w, 0) for w in target_weeks), 2)

    return vendor_spend, category_spend, wt_list, grand
