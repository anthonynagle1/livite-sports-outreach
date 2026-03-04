"""Fetch catering order data from Notion's Catering Orders/Sales database.

Aggregates Forkable, EZCater, and other platform orders by month
for the financials dashboard.
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict

import requests

logger = logging.getLogger(__name__)

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_VERSION = "2025-09-03"
BASE_URL = "https://api.notion.com/v1"

# Catering Orders/Sales database in Notion (production)
CATERING_DB_ID = "2ca42679f1a380eb8638d2e61c6e6941"
# Pre-resolved data source ID (avoids extra API call)
CATERING_DS_ID = "2ca42679-f1a3-80d2-8ae0-000b9d39643b"

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

# Cache: fetched data with TTL
_cache = {"ts": 0, "data": None}
_CACHE_TTL = 3600  # 1 hour


def _resolve_ds_id(db_id):
    """Resolve a database page ID to its data_source_id."""
    try:
        resp = requests.get(
            f"{BASE_URL}/databases/{db_id}",
            headers=HEADERS,
            timeout=15,
        )
        if resp.status_code == 200:
            ds = resp.json().get("data_sources", [])
            if ds:
                return ds[0]["id"]
    except Exception as e:
        logger.warning("Failed to resolve DS for %s: %s", db_id, e)
    return db_id


# Normalize platform names for display
_PLATFORM_MAP = {
    "Forkable": "Forkable",
    "EZCater": "EZCater",
    "Cater2me": "Cater2me",
    "Direct \u2013 Inbound (Phone/Email)": "Direct",
    "Direct \u2013 Website/Toast": "Direct (Toast)",
}

# Platforms that represent non-Toast revenue (the "gap")
NON_TOAST_PLATFORMS = {"Forkable", "EZCater", "Cater2me"}


def _fetch_all_orders():
    """Fetch all completed catering orders from Notion.

    Returns list of dicts: platform, subtotal, date, name.
    """
    if not NOTION_API_KEY:
        logger.warning("NOTION_API_KEY not set, skipping catering data")
        return []

    ds_id = CATERING_DS_ID
    orders = []
    has_more = True
    next_cursor = None

    while has_more:
        payload = {"page_size": 100}
        if next_cursor:
            payload["start_cursor"] = next_cursor

        try:
            resp = requests.post(
                f"{BASE_URL}/data_sources/{ds_id}/query",
                headers=HEADERS,
                json=payload,
                timeout=30,
            )
        except Exception as e:
            logger.error("Notion catering query failed: %s", e)
            break

        if resp.status_code != 200:
            logger.error(
                "Notion catering query error %s: %s",
                resp.status_code,
                resp.text[:200],
            )
            break

        data = resp.json()
        for page in data.get("results", []):
            props = page.get("properties", {})

            # Order Status — only include Completed / Confirmed
            status_prop = props.get("Order Status", {})
            status = ""
            if status_prop.get("type") == "select" and status_prop.get("select"):
                status = status_prop["select"].get("name", "")
            if status not in ("Completed", "Confirmed", "Comfirmed"):
                continue

            # Platform
            plat_prop = props.get("Order Platform", {})
            platform = ""
            if plat_prop.get("type") == "select" and plat_prop.get("select"):
                platform = plat_prop["select"].get("name", "")

            # Subtotal (pre-tax revenue)
            sub_prop = props.get("Subtotal", {})
            subtotal = sub_prop.get("number", 0) or 0

            # Delivery date
            date_prop = props.get("Delivery Date & Time", {})
            delivery_date = ""
            if date_prop.get("type") == "date" and date_prop.get("date"):
                delivery_date = (date_prop["date"].get("start", "") or "")[:10]

            # Order name (title)
            name = ""
            for v in props.values():
                if v.get("type") == "title":
                    tl = v.get("title", [])
                    name = tl[0]["text"]["content"] if tl else ""
                    break

            if platform and delivery_date and subtotal > 0:
                orders.append({
                    "platform": _PLATFORM_MAP.get(platform, platform),
                    "subtotal": subtotal,
                    "date": delivery_date,
                    "name": name,
                })

        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

    return orders


def get_catering_for_date(iso_date: str) -> dict:
    """Get catering revenue by platform for a single date.

    Args:
        iso_date: "YYYY-MM-DD" format

    Returns dict like {"forkable": 150.0, "ezcater": 0, "cater2me": 85.0}
    """
    if not NOTION_API_KEY:
        return {}

    ds_id = CATERING_DS_ID
    result = {"forkable": 0, "ezcater": 0}

    payload = {
        "page_size": 50,
        "filter": {
            "property": "Delivery Date & Time",
            "date": {"equals": iso_date},
        },
    }

    try:
        resp = requests.post(
            f"{BASE_URL}/data_sources/{ds_id}/query",
            headers=HEADERS,
            json=payload,
            timeout=15,
        )
    except Exception as e:
        logger.error("Catering date query failed: %s", e)
        return result

    if resp.status_code != 200:
        logger.error("Catering date query error %s: %s",
                      resp.status_code, resp.text[:200])
        return result

    for page in resp.json().get("results", []):
        props = page.get("properties", {})

        # Only completed/confirmed
        status_prop = props.get("Order Status", {})
        status = ""
        if status_prop.get("type") == "select" and status_prop.get("select"):
            status = status_prop["select"].get("name", "")
        if status not in ("Completed", "Confirmed", "Comfirmed"):
            continue

        # Platform
        plat_prop = props.get("Order Platform", {})
        platform = ""
        if plat_prop.get("type") == "select" and plat_prop.get("select"):
            platform = plat_prop["select"].get("name", "")

        # Subtotal
        sub_prop = props.get("Subtotal", {})
        subtotal = sub_prop.get("number", 0) or 0

        # Map to our keys
        key = {"Forkable": "forkable", "EZCater": "ezcater"}.get(platform)
        if key and subtotal > 0:
            result[key] += subtotal

    # Round
    for k in result:
        result[k] = round(result[k], 2)

    return result


def fetch_upcoming_orders(from_date=None):
    """Fetch confirmed catering orders on or after from_date.

    Args:
        from_date: "YYYY-MM-DD" string, defaults to today

    Returns list of {platform, subtotal, date, name}.
    """
    if not NOTION_API_KEY:
        logger.warning("NOTION_API_KEY not set, skipping upcoming catering")
        return []

    if from_date is None:
        from datetime import datetime
        from_date = datetime.now().strftime("%Y-%m-%d")

    ds_id = CATERING_DS_ID
    orders = []
    has_more = True
    next_cursor = None

    while has_more:
        payload = {"page_size": 100}
        if next_cursor:
            payload["start_cursor"] = next_cursor

        try:
            resp = requests.post(
                f"{BASE_URL}/data_sources/{ds_id}/query",
                headers=HEADERS,
                json=payload,
                timeout=30,
            )
        except Exception as e:
            logger.error("Notion upcoming catering query failed: %s", e)
            break

        if resp.status_code != 200:
            logger.error(
                "Notion upcoming catering query error %s: %s",
                resp.status_code,
                resp.text[:200],
            )
            break

        data = resp.json()
        for page in data.get("results", []):
            props = page.get("properties", {})

            # Order Status — only Confirmed
            status_prop = props.get("Order Status", {})
            status = ""
            if status_prop.get("type") == "select" and status_prop.get("select"):
                status = status_prop["select"].get("name", "")
            if status not in ("Confirmed", "Comfirmed"):
                continue

            # Delivery date — must be >= from_date
            date_prop = props.get("Delivery Date & Time", {})
            delivery_date = ""
            if date_prop.get("type") == "date" and date_prop.get("date"):
                delivery_date = (date_prop["date"].get("start", "") or "")[:10]
            if not delivery_date or delivery_date < from_date:
                continue

            # Platform
            plat_prop = props.get("Order Platform", {})
            platform = ""
            if plat_prop.get("type") == "select" and plat_prop.get("select"):
                platform = plat_prop["select"].get("name", "")

            # Subtotal
            sub_prop = props.get("Subtotal", {})
            subtotal = sub_prop.get("number", 0) or 0

            # Order name (title)
            name = ""
            for v in props.values():
                if v.get("type") == "title":
                    tl = v.get("title", [])
                    name = tl[0]["text"]["content"] if tl else ""
                    break

            orders.append({
                "platform": _PLATFORM_MAP.get(platform, platform),
                "subtotal": subtotal,
                "date": delivery_date,
                "name": name,
            })

        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

    return orders


def get_catering_by_month():
    """Get catering revenue aggregated by month and platform.

    Returns dict with keys:
        months: list of "YYYY-MM" strings
        platforms: {platform_name: [monthly_subtotals]}
        totals: [monthly_totals]
        order_counts: {platform_name: [monthly_counts]}
    Returns None if data unavailable.
    """
    now = time.time()
    if _cache["data"] is not None and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["data"]

    orders = _fetch_all_orders()
    if not orders:
        return None

    # Aggregate by month and platform
    monthly = defaultdict(lambda: defaultdict(float))
    counts = defaultdict(lambda: defaultdict(int))

    for order in orders:
        month = order["date"][:7]  # YYYY-MM
        platform = order["platform"]
        monthly[month][platform] += order["subtotal"]
        counts[month][platform] += 1

    if not monthly:
        return None

    sorted_months = sorted(monthly.keys())

    # Collect and order platforms
    all_platforms = set()
    for m in sorted_months:
        all_platforms.update(monthly[m].keys())

    preferred_order = ["Forkable", "EZCater", "Cater2me", "Direct", "Direct (Toast)"]
    ordered_platforms = [p for p in preferred_order if p in all_platforms]
    for p in sorted(all_platforms):
        if p not in ordered_platforms:
            ordered_platforms.append(p)

    result = {
        "months": sorted_months,
        "platforms": {},
        "totals": [],
        "order_counts": {},
    }

    for platform in ordered_platforms:
        result["platforms"][platform] = [
            round(monthly[m].get(platform, 0), 2) for m in sorted_months
        ]
        result["order_counts"][platform] = [
            counts[m].get(platform, 0) for m in sorted_months
        ]

    result["totals"] = [
        round(sum(monthly[m].values()), 2) for m in sorted_months
    ]

    _cache["ts"] = now
    _cache["data"] = result
    return result
