"""Sync vendor price data with Notion databases.

Handles reading/writing Items Master, Price Entries, and Upload Log.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime

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


def _resolve_ds_id(db_id: str) -> str:
    """Resolve a database ID to its data_source_id for queries."""
    if not db_id:
        return ""
    try:
        resp = requests.get(f"{BASE_URL}/databases/{db_id}", headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data_sources = resp.json().get("data_sources", [])
            if data_sources:
                return data_sources[0]["id"]
    except Exception as e:
        logger.warning("Failed to resolve DS for %s: %s", db_id, e)
    return db_id


# Lazy-resolved data source IDs
_ds_cache: dict[str, str] = {}


def _get_ds_id(db_id: str) -> str:
    if db_id not in _ds_cache:
        _ds_cache[db_id] = _resolve_ds_id(db_id)
    return _ds_cache[db_id]


def get_current_week() -> str:
    """Get current ISO week string like '2026-W08'."""
    today = date.today()
    return f"{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}"


# ── Items Master ──


def get_all_items(items_db_id: str) -> list[dict]:
    """Fetch all items from Items Master.

    Returns list of dicts with 'id', 'name', 'category', 'unit',
    'unit_size', 'active', 'preferred_vendor', 'aliases'.
    """
    ds_id = _get_ds_id(items_db_id)
    items = []
    has_more = True
    next_cursor = None

    while has_more:
        payload = {"page_size": 100}
        if next_cursor:
            payload["start_cursor"] = next_cursor

        resp = requests.post(
            f"{BASE_URL}/data_sources/{ds_id}/query",
            headers=HEADERS,
            json=payload,
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error("Items query error: %s", resp.status_code)
            break

        data = resp.json()
        for page in data.get("results", []):
            props = page.get("properties", {})
            items.append({
                "id": page["id"],
                "name": _get_title(props),
                "category": _get_select(props, "Category"),
                "unit": _get_select(props, "Unit"),
                "unit_size": _get_rich_text(props, "Unit Size"),
                "active": _get_checkbox(props, "Active"),
                "preferred_vendor": _get_select(props, "Preferred Vendor"),
                "aliases": _get_rich_text(props, "Aliases"),
                "par_level": _get_number(props, "Par Level"),
                "notes": _get_rich_text(props, "Notes"),
            })

        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

    return items


def create_item(items_db_id: str, name: str, category: str = "Other",
                unit: str = "case", unit_size: str = "",
                aliases: list[str] | None = None) -> str | None:
    """Create a new item in Items Master. Returns page ID."""
    ds_id = _get_ds_id(items_db_id)
    properties = {
        "Item": {"title": [{"text": {"content": name}}]},
        "Category": {"select": {"name": category.title()}},
        "Unit": {"select": {"name": unit}},
        "Active": {"checkbox": True},
    }
    if unit_size:
        properties["Unit Size"] = {"rich_text": [{"text": {"content": unit_size}}]}
    if aliases:
        properties["Aliases"] = {"rich_text": [{"text": {"content": json.dumps(aliases)}}]}

    resp = requests.post(
        f"{BASE_URL}/pages",
        headers=HEADERS,
        json={
            "parent": {"data_source_id": ds_id},
            "properties": properties,
        },
        timeout=15,
    )
    if resp.status_code == 200:
        page_id = resp.json()["id"]
        logger.info("Created item: %s → %s", name, page_id)
        return page_id
    error_detail = resp.text[:300]
    logger.error("Failed to create item %s: %s %s", name, resp.status_code, error_detail)
    raise RuntimeError(f"Notion API {resp.status_code}: {error_detail}")


def update_item_aliases(page_id: str, aliases: list[str]):
    """Update the Aliases field on an Items Master entry."""
    resp = requests.patch(
        f"{BASE_URL}/pages/{page_id}",
        headers=HEADERS,
        json={
            "properties": {
                "Aliases": {"rich_text": [{"text": {"content": json.dumps(aliases)}}]},
            }
        },
        timeout=15,
    )
    if resp.status_code != 200:
        logger.error("Failed to update aliases for %s: %s", page_id, resp.status_code)


# ── Price Entries ──


def add_price_entry(
    prices_db_id: str,
    item_page_id: str,
    vendor: str,
    price: float,
    unit: str = "",
    price_per_unit: float = 0,
    vendor_item_name: str = "",
    vendor_item_code: str = "",
    source_file: str = "",
    week: str = "",
    entry_date: str = "",
    quantity: int = 1,
    total_cost: float = 0,
    pack_qty: int = 0,
    each_size: float = 0,
    size_unit: str = "",
    upload_type: str = "Purchase",
) -> str | None:
    """Add a price entry to the Price Entries database."""
    ds_id = _get_ds_id(prices_db_id)
    if not week:
        week = get_current_week()
    if not entry_date:
        entry_date = date.today().isoformat()

    # Build title
    item_short = vendor_item_name[:40] if vendor_item_name else "Item"
    title = f"{vendor} - {item_short} - {week}"

    properties = {
        "Entry": {"title": [{"text": {"content": title}}]},
        "Item": {"relation": [{"id": item_page_id}]},
        "Vendor": {"select": {"name": vendor}},
        "Price": {"number": price},
        "Week": {"rich_text": [{"text": {"content": week}}]},
        "Date": {"date": {"start": entry_date}},
    }
    if unit:
        properties["Unit"] = {"rich_text": [{"text": {"content": unit}}]}
    if price_per_unit:
        properties["Price Per Unit"] = {"number": price_per_unit}
    if vendor_item_name:
        properties["Vendor Item Name"] = {"rich_text": [{"text": {"content": vendor_item_name}}]}
    if vendor_item_code:
        properties["Vendor Item Code"] = {"rich_text": [{"text": {"content": vendor_item_code}}]}
    if source_file:
        properties["Source File"] = {"rich_text": [{"text": {"content": source_file}}]}
    if quantity and quantity > 1:
        properties["Quantity"] = {"number": quantity}
    if total_cost:
        properties["Total Cost"] = {"number": round(total_cost, 2)}
    if pack_qty and pack_qty > 0:
        properties["Pack Qty"] = {"number": pack_qty}
    if each_size and each_size > 0:
        properties["Each Size"] = {"number": each_size}
    if size_unit:
        properties["Size Unit"] = {"rich_text": [{"text": {"content": size_unit}}]}
    if upload_type:
        properties["Upload Type"] = {"select": {"name": upload_type}}

    resp = requests.post(
        f"{BASE_URL}/pages",
        headers=HEADERS,
        json={"parent": {"data_source_id": ds_id}, "properties": properties},
        timeout=15,
    )
    if resp.status_code == 200:
        return resp.json()["id"]
    error_detail = resp.text[:300]
    logger.error("Failed to add price entry: %s %s", resp.status_code, error_detail)
    raise RuntimeError(f"Notion API {resp.status_code}: {error_detail}")


def archive_duplicate_entries(
    prices_db_id: str,
    item_page_id: str,
    vendor: str,
    week: str,
) -> int:
    """Archive existing price entries for the same (item, vendor, week).

    Call this BEFORE add_price_entry to prevent duplicates across uploads.
    Returns the number of entries archived.
    """
    entries = get_price_entries(prices_db_id, week=week)
    archived = 0
    for entry in entries:
        if entry["item_relation_id"] == item_page_id and entry["vendor"] == vendor:
            try:
                delete_page(entry["id"])
                archived += 1
                logger.info(
                    "Archived duplicate price entry %s (%s / %s / %s)",
                    entry["id"], vendor, entry.get("vendor_item_name", ""), week,
                )
            except Exception as exc:
                logger.warning("Failed to archive duplicate %s: %s", entry["id"], exc)
    return archived


def get_price_entries(prices_db_id: str, week: str = "") -> list[dict]:
    """Fetch price entries, optionally filtered by week.

    Returns list of dicts with 'id', 'vendor', 'price', 'price_per_unit',
    'unit', 'week', 'date', 'item_relation_id', 'vendor_item_name', 'source_file'.
    """
    ds_id = _get_ds_id(prices_db_id)
    entries = []
    has_more = True
    next_cursor = None

    while has_more:
        payload = {"page_size": 100}
        if next_cursor:
            payload["start_cursor"] = next_cursor

        resp = requests.post(
            f"{BASE_URL}/data_sources/{ds_id}/query",
            headers=HEADERS,
            json=payload,
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error("Price entries query error: %s", resp.status_code)
            break

        data = resp.json()
        for page in data.get("results", []):
            props = page.get("properties", {})
            entry_week = _get_rich_text(props, "Week")

            if week and entry_week != week:
                continue

            # Get item relation
            item_rel = props.get("Item", {})
            item_ids = []
            if item_rel.get("type") == "relation":
                item_ids = [r["id"] for r in item_rel.get("relation", [])]

            date_prop = props.get("Date", {})
            entry_date = ""
            if date_prop.get("type") == "date" and date_prop.get("date"):
                entry_date = date_prop["date"].get("start", "")

            entries.append({
                "id": page["id"],
                "vendor": _get_select(props, "Vendor"),
                "price": _get_number(props, "Price"),
                "price_per_unit": _get_number(props, "Price Per Unit"),
                "unit": _get_rich_text(props, "Unit"),
                "week": entry_week,
                "date": entry_date,
                "item_relation_id": item_ids[0] if item_ids else "",
                "vendor_item_name": _get_rich_text(props, "Vendor Item Name"),
                "vendor_item_code": _get_rich_text(props, "Vendor Item Code"),
                "source_file": _get_rich_text(props, "Source File"),
                "upload_type": _get_select(props, "Upload Type"),
                "quantity": _get_number(props, "Quantity"),
                "total_cost": _get_number(props, "Total Cost"),
                "pack_qty": _get_number(props, "Pack Qty"),
                "each_size": _get_number(props, "Each Size"),
                "size_unit": _get_rich_text(props, "Size Unit"),
            })

        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

    return entries


def get_available_weeks(prices_db_id: str) -> list[str]:
    """Get all unique weeks that have price entries, sorted descending."""
    entries = get_price_entries(prices_db_id)
    weeks = sorted(set(e["week"] for e in entries if e["week"]), reverse=True)
    return weeks


def update_item(page_id: str, **kwargs):
    """Update fields on an Items Master entry."""
    properties = {}
    field_map = {
        "category": ("Category", "select"),
        "unit": ("Unit", "select"),
        "unit_size": ("Unit Size", "rich_text"),
        "active": ("Active", "checkbox"),
        "preferred_vendor": ("Preferred Vendor", "select"),
        "notes": ("Notes", "rich_text"),
        "par_level": ("Par Level", "number"),
    }
    # Handle name (title) separately
    if "name" in kwargs:
        properties["Item"] = {"title": [{"text": {"content": str(kwargs["name"])[:200]}}]}

    for key, (prop_name, prop_type) in field_map.items():
        if key in kwargs:
            val = kwargs[key]
            if prop_type == "select":
                properties[prop_name] = {"select": {"name": val}}
            elif prop_type == "rich_text":
                properties[prop_name] = {"rich_text": [{"text": {"content": str(val)[:2000]}}]}
            elif prop_type == "checkbox":
                properties[prop_name] = {"checkbox": bool(val)}
            elif prop_type == "number":
                properties[prop_name] = {"number": float(val) if val else None}

    if properties:
        requests.patch(
            f"{BASE_URL}/pages/{page_id}",
            headers=HEADERS,
            json={"properties": properties},
            timeout=15,
        )


# ── Upload Log ──


def log_upload(
    uploads_db_id: str,
    vendor: str,
    filename: str,
    file_type: str,
    items_extracted: int = 0,
    items_matched: int = 0,
    items_new: int = 0,
    status: str = "Processing",
    error_details: str = "",
    upload_type: str = "Purchase",
) -> str | None:
    """Create an upload log entry."""
    ds_id = _get_ds_id(uploads_db_id)
    week = get_current_week()
    today = date.today().isoformat()
    title = f"{vendor} - {today} - {filename}"

    properties = {
        "Upload": {"title": [{"text": {"content": title}}]},
        "Vendor": {"select": {"name": vendor}},
        "Date Uploaded": {"date": {"start": today}},
        "Week": {"rich_text": [{"text": {"content": week}}]},
        "File Type": {"select": {"name": file_type}},
        "Items Extracted": {"number": items_extracted},
        "Items Matched": {"number": items_matched},
        "Items New": {"number": items_new},
        "Status": {"select": {"name": status}},
    }
    if error_details:
        properties["Error Details"] = {"rich_text": [{"text": {"content": error_details[:2000]}}]}
    if upload_type:
        properties["Upload Type"] = {"select": {"name": upload_type}}

    resp = requests.post(
        f"{BASE_URL}/pages",
        headers=HEADERS,
        json={"parent": {"data_source_id": ds_id}, "properties": properties},
        timeout=15,
    )
    if resp.status_code == 200:
        return resp.json()["id"]
    logger.error("Failed to log upload: %s", resp.status_code)
    return None


def update_upload_log(page_id: str, **kwargs):
    """Update fields on an upload log entry."""
    properties = {}
    field_map = {
        "items_extracted": ("Items Extracted", "number"),
        "items_matched": ("Items Matched", "number"),
        "items_new": ("Items New", "number"),
        "status": ("Status", "select"),
        "error_details": ("Error Details", "rich_text"),
    }
    for key, (prop_name, prop_type) in field_map.items():
        if key in kwargs:
            val = kwargs[key]
            if prop_type == "number":
                properties[prop_name] = {"number": val}
            elif prop_type == "select":
                properties[prop_name] = {"select": {"name": val}}
            elif prop_type == "rich_text":
                properties[prop_name] = {"rich_text": [{"text": {"content": str(val)[:2000]}}]}

    if properties:
        requests.patch(
            f"{BASE_URL}/pages/{page_id}",
            headers=HEADERS,
            json={"properties": properties},
            timeout=15,
        )


# ── Upload Log queries ──


def get_upload_log(uploads_db_id: str) -> list[dict]:
    """Fetch all upload log entries, sorted by date descending.

    Returns list of dicts with 'id', 'title', 'vendor', 'date', 'week',
    'file_type', 'items_extracted', 'items_matched', 'items_new', 'status'.
    """
    ds_id = _get_ds_id(uploads_db_id)
    entries = []
    has_more = True
    next_cursor = None

    while has_more:
        payload = {"page_size": 100}
        if next_cursor:
            payload["start_cursor"] = next_cursor

        resp = requests.post(
            f"{BASE_URL}/data_sources/{ds_id}/query",
            headers=HEADERS,
            json=payload,
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error("Upload log query error: %s", resp.status_code)
            break

        data = resp.json()
        for page in data.get("results", []):
            props = page.get("properties", {})
            date_prop = props.get("Date Uploaded", {})
            entry_date = ""
            if date_prop.get("type") == "date" and date_prop.get("date"):
                entry_date = date_prop["date"].get("start", "")

            entries.append({
                "id": page["id"],
                "title": _get_title(props),
                "vendor": _get_select(props, "Vendor"),
                "date": entry_date,
                "week": _get_rich_text(props, "Week"),
                "file_type": _get_select(props, "File Type"),
                "items_extracted": _get_number(props, "Items Extracted"),
                "items_matched": _get_number(props, "Items Matched"),
                "items_new": _get_number(props, "Items New"),
                "status": _get_select(props, "Status"),
                "error_details": _get_rich_text(props, "Error Details"),
            })

        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

    # Sort by date descending
    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries


def delete_page(page_id: str) -> bool:
    """Archive (delete) a Notion page by ID."""
    resp = requests.patch(
        f"{BASE_URL}/pages/{page_id}",
        headers=HEADERS,
        json={"archived": True},
        timeout=15,
    )
    if resp.status_code == 200:
        logger.info("Archived page: %s", page_id)
        return True
    logger.error("Failed to archive %s: %s", page_id, resp.status_code)
    return False


# ── Spending Aggregation ──


def get_spending_summary(prices_db_id: str, items_db_id: str,
                         max_weeks: int = 12) -> dict:
    """Aggregate spending data from Price Entries joined with Items Master.

    Returns:
        {
            by_vendor: {vendor: [{week, total, count}]},
            by_category: {category: [{week, total, count}]},
            weekly_totals: [{week, total, count}],
            grand_total: float,
            weeks: [str],
        }
    """
    from collections import defaultdict

    entries = get_price_entries(prices_db_id)
    items = get_all_items(items_db_id)

    # Build item lookup: page_id -> category
    item_cats = {}
    for item in items:
        item_cats[item["id"]] = item.get("category", "Other") or "Other"

    # Get unique weeks (most recent N)
    all_weeks = sorted(set(e["week"] for e in entries if e["week"]),
                       reverse=True)
    target_weeks = set(all_weeks[:max_weeks])

    vendor_week = defaultdict(lambda: defaultdict(lambda: {"total": 0, "count": 0}))
    cat_week = defaultdict(lambda: defaultdict(lambda: {"total": 0, "count": 0}))
    week_totals = defaultdict(lambda: {"total": 0, "count": 0})

    for e in entries:
        week = e.get("week", "")
        if not week or week not in target_weeks:
            continue

        # Only count purchases for spending (not price-only catalog refreshes)
        if e.get("upload_type") == "Price Update":
            continue

        vendor = e.get("vendor", "Unknown")
        # Use total_cost (qty * price) when available, fall back to price
        amount = e.get("total_cost") or e.get("price", 0) or 0
        category = item_cats.get(e.get("item_relation_id", ""), "Other")

        vendor_week[vendor][week]["total"] += amount
        vendor_week[vendor][week]["count"] += 1
        cat_week[category][week]["total"] += amount
        cat_week[category][week]["count"] += 1
        week_totals[week]["total"] += amount
        week_totals[week]["count"] += 1

    weeks_sorted = sorted(target_weeks)

    def _serialize(d):
        result = {}
        for key, week_map in d.items():
            result[key] = []
            for w in weeks_sorted:
                entry = week_map.get(w, {"total": 0, "count": 0})
                result[key].append({
                    "week": w,
                    "total": round(entry["total"], 2),
                    "count": entry["count"],
                })
        return result

    weekly_list = []
    for w in weeks_sorted:
        wt = week_totals.get(w, {"total": 0, "count": 0})
        weekly_list.append({
            "week": w,
            "total": round(wt["total"], 2),
            "count": wt["count"],
        })

    grand = round(sum(wt["total"] for wt in week_totals.values()), 2)

    return {
        "by_vendor": _serialize(vendor_week),
        "by_category": _serialize(cat_week),
        "weekly_totals": weekly_list,
        "grand_total": grand,
        "weeks": weeks_sorted,
    }


# ── Item Detail ──


def get_item_price_details(prices_db_id: str, item_page_id: str) -> list[dict]:
    """Get latest price entries per vendor for a specific item.

    Returns list of dicts (one per vendor) with price + pack breakdown fields.
    """
    entries = get_price_entries(prices_db_id)
    latest: dict[str, dict] = {}
    for e in entries:
        if e["item_relation_id"] != item_page_id:
            continue
        vendor = e["vendor"]
        if vendor not in latest or (e["week"] or "") > (latest[vendor]["week"] or ""):
            latest[vendor] = e
    return list(latest.values())


def update_price_entry_pack(entry_id: str, pack_qty: int, each_size: float,
                            size_unit: str):
    """Update pack breakdown fields on an existing price entry."""
    properties: dict = {}
    properties["Pack Qty"] = {"number": pack_qty if pack_qty else None}
    properties["Each Size"] = {"number": each_size if each_size else None}
    if size_unit:
        properties["Size Unit"] = {"rich_text": [{"text": {"content": size_unit}}]}
    else:
        properties["Size Unit"] = {"rich_text": []}

    # Recalculate price_per_unit if we have the data
    resp = requests.get(
        f"{BASE_URL}/pages/{entry_id}",
        headers=HEADERS,
        timeout=15,
    )
    if resp.status_code == 200:
        props = resp.json().get("properties", {})
        price = _get_number(props, "Price")
        total_units = (pack_qty or 0) * (each_size or 0)
        if price and total_units > 0:
            properties["Price Per Unit"] = {"number": round(price / total_units, 4)}

    resp = requests.patch(
        f"{BASE_URL}/pages/{entry_id}",
        headers=HEADERS,
        json={"properties": properties},
        timeout=15,
    )
    if resp.status_code != 200:
        logger.error("Failed to update pack for %s: %s", entry_id, resp.status_code)
        raise RuntimeError(f"Notion API {resp.status_code}")


# ── Property helpers ──


def _get_title(props: dict) -> str:
    for k, v in props.items():
        if v.get("type") == "title":
            tl = v.get("title", [])
            return tl[0]["text"]["content"] if tl else ""
    return ""


def _get_rich_text(props: dict, name: str) -> str:
    v = props.get(name, {})
    if v.get("type") == "rich_text":
        rt = v.get("rich_text", [])
        return rt[0]["text"]["content"] if rt else ""
    return ""


def _get_select(props: dict, name: str) -> str:
    v = props.get(name, {})
    if v.get("type") == "select" and v.get("select"):
        return v["select"].get("name", "")
    return ""


def _get_checkbox(props: dict, name: str) -> bool:
    v = props.get(name, {})
    return v.get("checkbox", False)


def _get_number(props: dict, name: str) -> float:
    v = props.get(name, {})
    return v.get("number", 0) or 0
