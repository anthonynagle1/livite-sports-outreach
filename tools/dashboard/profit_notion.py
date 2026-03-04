"""Sync daily profit data with Notion database.

One row per date. Auto-calculated fields from Toast + manual entries
are both stored so historical data is queryable.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date

import requests

logger = logging.getLogger(__name__)

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_VERSION = "2025-09-03"
BASE_URL = "https://api.notion.com/v1"

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

_ds_cache: dict[str, str] = {}


def _get_ds_id(db_id: str) -> str:
    if db_id not in _ds_cache:
        try:
            resp = requests.get(f"{BASE_URL}/databases/{db_id}", headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                sources = resp.json().get("data_sources", [])
                _ds_cache[db_id] = sources[0]["id"] if sources else db_id
            else:
                _ds_cache[db_id] = db_id
        except Exception:
            _ds_cache[db_id] = db_id
    return _ds_cache[db_id]


def upsert_daily_profit(db_id: str, data: dict) -> str | None:
    """Create or update a daily profit row in Notion.

    Args:
        db_id: Notion database ID for the Profit Tracker
        data: Dict with all profit fields (auto + manual)

    Returns:
        Page ID on success, None on failure.
    """
    date_str = data.get("date", "")  # YYYYMMDD
    if not date_str or not db_id:
        return None

    # Format date for Notion
    iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    # Check if row already exists for this date
    existing_id = _find_by_date(db_id, iso_date)

    properties = _build_properties(data, iso_date)

    if existing_id:
        # Update existing row
        resp = requests.patch(
            f"{BASE_URL}/pages/{existing_id}",
            headers=HEADERS,
            json={"properties": properties},
            timeout=15,
        )
        if resp.status_code == 200:
            logger.info("Updated profit row for %s", iso_date)
            return existing_id
        logger.error("Failed to update profit %s: %s %s", iso_date, resp.status_code, resp.text[:200])
        return None
    else:
        # Create new row
        ds_id = _get_ds_id(db_id)
        resp = requests.post(
            f"{BASE_URL}/pages",
            headers=HEADERS,
            json={"parent": {"data_source_id": ds_id}, "properties": properties},
            timeout=15,
        )
        if resp.status_code == 200:
            page_id = resp.json()["id"]
            logger.info("Created profit row for %s: %s", iso_date, page_id)
            return page_id
        logger.error("Failed to create profit %s: %s %s", iso_date, resp.status_code, resp.text[:200])
        return None


def get_daily_profit(db_id: str, date_str: str) -> dict | None:
    """Load a single day's profit data from Notion.

    Args:
        db_id: Notion database ID
        date_str: YYYYMMDD format

    Returns:
        Dict with all stored fields, or None if not found.
    """
    iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    page_id = _find_by_date(db_id, iso_date)
    if not page_id:
        return None

    resp = requests.get(f"{BASE_URL}/pages/{page_id}", headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        return None

    props = resp.json().get("properties", {})
    return _parse_properties(props)


def get_profit_range(db_id: str, start_str: str, end_str: str) -> list[dict]:
    """Load profit data for a date range from Notion.

    Returns list of dicts sorted by date ascending.
    """
    iso_start = f"{start_str[:4]}-{start_str[4:6]}-{start_str[6:8]}"
    iso_end = f"{end_str[:4]}-{end_str[4:6]}-{end_str[6:8]}"

    ds_id = _get_ds_id(db_id)
    results = []
    has_more = True
    next_cursor = None

    while has_more:
        payload = {
            "page_size": 100,
            "filter": {
                "and": [
                    {"property": "Date", "date": {"on_or_after": iso_start}},
                    {"property": "Date", "date": {"on_or_before": iso_end}},
                ]
            },
            "sorts": [{"property": "Date", "direction": "ascending"}],
        }
        if next_cursor:
            payload["start_cursor"] = next_cursor

        resp = requests.post(
            f"{BASE_URL}/data_sources/{ds_id}/query",
            headers=HEADERS,
            json=payload,
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error("Profit range query error: %s", resp.status_code)
            break

        data = resp.json()
        for page in data.get("results", []):
            props = page.get("properties", {})
            parsed = _parse_properties(props)
            if parsed:
                results.append(parsed)

        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

    return results


def _find_by_date(db_id: str, iso_date: str) -> str | None:
    """Find the Notion page ID for a specific date."""
    ds_id = _get_ds_id(db_id)
    resp = requests.post(
        f"{BASE_URL}/data_sources/{ds_id}/query",
        headers=HEADERS,
        json={
            "page_size": 1,
            "filter": {"property": "Date", "date": {"equals": iso_date}},
        },
        timeout=15,
    )
    if resp.status_code == 200:
        results = resp.json().get("results", [])
        if results:
            return results[0]["id"]
    return None


def _build_properties(data: dict, iso_date: str) -> dict:
    """Build Notion properties from profit data dict."""
    display_date = data.get("display_date", iso_date)

    props = {
        "Day": {"title": [{"text": {"content": display_date}}]},
        "Date": {"date": {"start": iso_date}},
    }

    # Number fields — auto + manual
    number_fields = {
        "Toast Total": "toast_total",
        "Labor": "labor",
        "Food Cost": "food_cost",
        "TDS Fees": "tds_fees",
        "OT Hours": "ot_hours",
        "OT Pay": "ot_pay",
        "Total Hours": "total_hours",
        "FTEs": "ftes",
        "Blended Rate": "blended_rate",
        "Payroll Taxes": "payroll_taxes",
        # Manual fields
        "Forkable": "forkable",
        "EZ Cater": "ezcater",
        "Fixed": "fixed",
        "Vacation": "vacation",
        "Sick": "sick",
        "Misc": "misc",
        "GH Fees": "gh_fees",
        "DD Fees": "dd_fees",
        "Uber Fees": "uber_fees",
        "Uber Ads": "uber_ads",
        "Catering Fees": "catering_fees",
        "Shipday Fees": "shipday_fees",
        # Derived
        "Total Sales": "total_sales",
        "Service Fees": "service_fees",
        "Profit": "profit",
        "Profit Pct": "profit_pct",
        "Toast Discounts": "toast_discounts",
    }

    # Check manual dict too
    manual = data.get("manual", {})

    for notion_name, key in number_fields.items():
        val = data.get(key) or manual.get(key)
        # Skip list values (vacation/sick entries) — those are stored as totals
        if isinstance(val, list):
            continue
        if val is not None and val != 0:
            try:
                props[notion_name] = {"number": round(float(val), 2)}
            except (TypeError, ValueError):
                pass

    # Text fields
    if data.get("day_of_week") or data.get("manual", {}).get("day_of_week"):
        dow = data.get("day_of_week") or manual.get("day_of_week", "")
        if dow:
            props["Day of Week"] = {"select": {"name": dow}}

    notes = manual.get("notes", "")
    if notes:
        props["Notes"] = {"rich_text": [{"text": {"content": str(notes)[:2000]}}]}

    # Channel breakdown as JSON in rich text
    channels = data.get("channels", {})
    if channels:
        props["Channels"] = {"rich_text": [{"text": {"content": json.dumps(channels)[:2000]}}]}

    # Vacation/sick detail as JSON in rich text
    for detail_key, notion_name in [("vacation_detail", "Vacation Detail"), ("sick_detail", "Sick Detail")]:
        detail = data.get(detail_key, [])
        if detail:
            props[notion_name] = {"rich_text": [{"text": {"content": json.dumps(detail)[:2000]}}]}

    return props


def _parse_properties(props: dict) -> dict | None:
    """Parse Notion properties back into a profit data dict."""
    # Get date
    date_prop = props.get("Date", {})
    if date_prop.get("type") == "date" and date_prop.get("date"):
        iso_date = date_prop["date"].get("start", "")
    else:
        return None

    # Convert to YYYYMMDD
    date_str = iso_date.replace("-", "")

    result = {"date": date_str, "iso_date": iso_date}

    # Number fields
    number_fields = {
        "Toast Total": "toast_total",
        "Labor": "labor",
        "Food Cost": "food_cost",
        "TDS Fees": "tds_fees",
        "OT Hours": "ot_hours",
        "OT Pay": "ot_pay",
        "Total Hours": "total_hours",
        "FTEs": "ftes",
        "Blended Rate": "blended_rate",
        "Payroll Taxes": "payroll_taxes",
        "Forkable": "forkable",
        "EZ Cater": "ezcater",
        "Fixed": "fixed",
        "Vacation": "vacation",
        "Sick": "sick",
        "Misc": "misc",
        "GH Fees": "gh_fees",
        "DD Fees": "dd_fees",
        "Uber Fees": "uber_fees",
        "Uber Ads": "uber_ads",
        "Catering Fees": "catering_fees",
        "Shipday Fees": "shipday_fees",
        "Total Sales": "total_sales",
        "Service Fees": "service_fees",
        "Profit": "profit",
        "Profit Pct": "profit_pct",
        "Toast Discounts": "toast_discounts",
    }

    for notion_name, key in number_fields.items():
        prop = props.get(notion_name, {})
        if prop.get("type") == "number":
            result[key] = prop.get("number", 0) or 0

    # Select fields
    dow_prop = props.get("Day of Week", {})
    if dow_prop.get("type") == "select" and dow_prop.get("select"):
        result["day_of_week"] = dow_prop["select"].get("name", "")

    # Rich text fields
    for notion_name, key in [("Notes", "notes"), ("Channels", "channels_json"),
                              ("Vacation Detail", "vacation_detail_json"), ("Sick Detail", "sick_detail_json")]:
        prop = props.get(notion_name, {})
        if prop.get("type") == "rich_text":
            rt = prop.get("rich_text", [])
            result[key] = rt[0]["text"]["content"] if rt else ""

    # Parse channels JSON
    if result.get("channels_json"):
        try:
            result["channels"] = json.loads(result["channels_json"])
        except (json.JSONDecodeError, TypeError):
            result["channels"] = {}
        del result["channels_json"]

    # Parse vacation/sick detail JSON
    for json_key, target_key in [("vacation_detail_json", "vacation_detail"), ("sick_detail_json", "sick_detail")]:
        if result.get(json_key):
            try:
                result[target_key] = json.loads(result[json_key])
            except (json.JSONDecodeError, TypeError):
                result[target_key] = []
            del result[json_key]

    return result
