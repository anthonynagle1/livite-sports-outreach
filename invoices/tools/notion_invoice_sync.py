"""Sync invoices to a Notion database for persistent, queryable storage.

Stores each invoice as a Notion page with line items as a child database.
Runs alongside the local .json store (not a replacement — both are kept in sync).

Setup:
    Set NOTION_INVOICES_DB_ID in .env after creating the DB (run setup_notion_invoice_db.py).
    Set NOTION_INVOICE_LINES_DB_ID for line items (optional — improves queryability).

Usage:
    from invoices.tools.notion_invoice_sync import sync_invoice_to_notion, update_invoice_status_notion
"""
from __future__ import annotations

import logging
import os
from datetime import date

import requests

logger = logging.getLogger(__name__)

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_VERSION = "2025-09-03"
BASE_URL = "https://api.notion.com/v1"
INVOICES_DB_ID = os.getenv("NOTION_INVOICES_DB_ID", "")
INVOICE_LINES_DB_ID = os.getenv("NOTION_INVOICE_LINES_DB_ID", "")

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


def _ds_id(db_id: str) -> str:
    """Resolve DB ID to data_source_id."""
    try:
        resp = requests.get(f"{BASE_URL}/databases/{db_id}", headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            sources = resp.json().get("data_sources", [])
            if sources:
                return sources[0]["id"]
    except Exception as e:
        logger.warning("Failed to resolve DS for %s: %s", db_id, e)
    return db_id


# Lazy cache for data source IDs
_ds_cache: dict[str, str] = {}


def _get_ds(db_id: str) -> str:
    if db_id not in _ds_cache:
        _ds_cache[db_id] = _ds_id(db_id)
    return _ds_cache[db_id]


def sync_invoice_to_notion(inv: dict) -> str | None:
    """Create or update an invoice record in Notion.

    Returns the Notion page ID or None on failure.
    """
    if not INVOICES_DB_ID or not NOTION_API_KEY:
        return None

    # Check if already synced (look for existing page by local ID)
    existing_id = _find_by_local_id(inv["id"])
    if existing_id:
        _update_invoice_page(existing_id, inv)
        return existing_id

    return _create_invoice_page(inv)


def _create_invoice_page(inv: dict) -> str | None:
    ds_id = _get_ds(INVOICES_DB_ID)
    props = _build_invoice_properties(inv)

    resp = requests.post(
        f"{BASE_URL}/pages",
        headers=HEADERS,
        json={"parent": {"data_source_id": ds_id}, "properties": props},
        timeout=15,
    )
    if resp.status_code == 200:
        page_id = resp.json()["id"]
        logger.info("Created Notion invoice page %s for %s", page_id, inv["id"])
        # Optionally sync line items
        if INVOICE_LINES_DB_ID and inv.get("line_items"):
            _sync_line_items(page_id, inv)
        return page_id

    logger.error("Failed to create Notion invoice: %d %s", resp.status_code, resp.text[:200])
    return None


def _update_invoice_page(page_id: str, inv: dict) -> None:
    props = _build_invoice_properties(inv)
    resp = requests.patch(
        f"{BASE_URL}/pages/{page_id}",
        headers=HEADERS,
        json={"properties": props},
        timeout=15,
    )
    if resp.status_code != 200:
        logger.warning("Failed to update Notion invoice %s: %d", page_id, resp.status_code)


def _build_invoice_properties(inv: dict) -> dict:
    """Build Notion property payload for an invoice."""
    vendor = inv.get("vendor", "Unknown")
    inv_num = inv.get("invoice_number", "")
    title_text = f"{vendor} — {inv_num}" if inv_num else f"{vendor} — {inv.get('invoice_date', '')}"

    props: dict = {
        "Invoice": {"title": [{"text": {"content": title_text[:200]}}]},
        "Local ID": {"rich_text": [{"text": {"content": inv["id"]}}]},
        "Vendor": {"select": {"name": vendor}},
        "Status": {"select": {"name": inv.get("status", "unpaid").title()}},
        "Week": {"rich_text": [{"text": {"content": inv.get("week", "")}}]},
        "Line Items": {"number": len(inv.get("line_items", []))},
        "Calculated Total": {"number": round(float(inv.get("calculated_total", 0) or 0), 2)},
    }

    # Optional string fields
    if inv.get("invoice_number"):
        props["Invoice Number"] = {"rich_text": [{"text": {"content": str(inv["invoice_number"])[:200]}}]}
    if inv.get("location"):
        props["Location"] = {"select": {"name": inv["location"].title()}}
    if inv.get("notes"):
        props["Notes"] = {"rich_text": [{"text": {"content": inv["notes"][:2000]}}]}

    # Date fields
    if inv.get("invoice_date"):
        props["Invoice Date"] = {"date": {"start": inv["invoice_date"]}}
    if inv.get("due_date"):
        props["Due Date"] = {"date": {"start": inv["due_date"]}}
    if inv.get("paid_date"):
        props["Paid Date"] = {"date": {"start": inv["paid_date"]}}

    # Total (use vendor-stated total as primary)
    total = float(inv.get("total", 0) or 0)
    if total > 0:
        props["Total"] = {"number": round(total, 2)}

    return props


def _sync_line_items(invoice_page_id: str, inv: dict) -> None:
    """Write each line item to the Invoice Lines DB, linked to the invoice page."""
    if not INVOICE_LINES_DB_ID:
        return
    ds_id = _get_ds(INVOICE_LINES_DB_ID)

    import time
    for li in inv.get("line_items", []):
        unit_price = float(li.get("unit_price", 0) or 0)
        extended = float(li.get("extended_price", 0) or 0)
        qty = float(li.get("quantity", 1) or 1)
        is_credit = qty < 0 or unit_price < 0

        props: dict = {
            "Item": {"title": [{"text": {"content": (li.get("item_name") or "Unknown")[:200]}}]},
            "Invoice": {"relation": [{"id": invoice_page_id}]},
            "Unit Price": {"number": round(unit_price, 4)},
            "Quantity": {"number": qty},
            "Extended Price": {"number": round(extended, 2)},
            "Is Credit": {"checkbox": is_credit},
        }
        if li.get("unit"):
            props["Unit"] = {"rich_text": [{"text": {"content": li["unit"][:100]}}]}
        if li.get("item_code"):
            props["Item Code"] = {"rich_text": [{"text": {"content": str(li["item_code"])[:100]}}]}
        if li.get("category"):
            props["Category"] = {"select": {"name": li["category"].replace("_", " ").title()}}
        if li.get("master_item_name"):
            props["Common Name"] = {"rich_text": [{"text": {"content": li["master_item_name"][:200]}}]}

        try:
            resp = requests.post(
                f"{BASE_URL}/pages",
                headers=HEADERS,
                json={"parent": {"data_source_id": ds_id}, "properties": props},
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning("Failed to create line item %s: %d", li.get("item_name"), resp.status_code)
            time.sleep(0.2)  # Rate limit courtesy
        except Exception as e:
            logger.warning("Line item sync error for %s: %s", li.get("item_name"), e)


def update_invoice_status_notion(local_invoice_id: str, new_status: str) -> bool:
    """Update the status of an invoice in Notion by local ID."""
    if not INVOICES_DB_ID or not NOTION_API_KEY:
        return False
    page_id = _find_by_local_id(local_invoice_id)
    if not page_id:
        return False
    resp = requests.patch(
        f"{BASE_URL}/pages/{page_id}",
        headers=HEADERS,
        json={"properties": {"Status": {"select": {"name": new_status.title()}}}},
        timeout=15,
    )
    return resp.status_code == 200


def _find_by_local_id(local_id: str) -> str | None:
    """Find an existing Notion invoice page by its Local ID property."""
    if not INVOICES_DB_ID:
        return None
    try:
        ds_id = _get_ds(INVOICES_DB_ID)
        resp = requests.post(
            f"{BASE_URL}/data_sources/{ds_id}/query",
            headers=HEADERS,
            json={
                "filter": {
                    "property": "Local ID",
                    "rich_text": {"equals": local_id},
                },
                "page_size": 1,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                return results[0]["id"]
    except Exception as e:
        logger.warning("Failed to find invoice by local ID %s: %s", local_id, e)
    return None
