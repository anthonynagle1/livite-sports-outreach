"""Price change detection for invoice line items.

Compares current invoice prices against the most recent invoice for the
same vendor + item. Flags items where price changed by more than 10%.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ALERT_THRESHOLD = 0.10  # 10% change triggers an alert
HIGH_ALERT_THRESHOLD = 0.20  # 20% change is a high alert


def check_price_alerts(items: list[dict], vendor: str) -> list[dict]:
    """Add price_alert dicts to items where price changed significantly.

    Compares each item's unit_price against the last known price for the
    same vendor + item (matched by master_item_id or item_name).

    Args:
        items: List of extracted line item dicts
        vendor: Vendor name

    Returns:
        Same items list with price_alert added where applicable.
        price_alert = {direction: "up"|"down", pct_change: float, old_price: float}
    """
    from invoices.tools.invoice_store import list_invoices, get_invoice

    # Get recent invoices from this vendor
    recent, _ = list_invoices(vendor=vendor, limit=20)
    if not recent:
        return items

    # Build a lookup of last known prices: key → (unit_price, invoice_date)
    last_prices: dict[str, tuple[float, str]] = {}

    for entry in recent:
        inv = get_invoice(entry["id"])
        if not inv:
            continue
        for li in inv.get("line_items", []):
            # Use master_item_id as primary key, fall back to item_name
            key = li.get("master_item_id") or li.get("item_name", "").lower().strip()
            if not key:
                continue
            price = float(li.get("unit_price", 0) or 0)
            date = inv.get("invoice_date", "")
            if price > 0 and key not in last_prices:
                last_prices[key] = (price, date)

    if not last_prices:
        return items

    alert_count = 0
    for item in items:
        key = item.get("master_item_id") or item.get("item_name", "").lower().strip()
        if not key or key not in last_prices:
            continue

        current_price = float(item.get("unit_price", 0) or 0)
        old_price, _ = last_prices[key]

        if current_price <= 0 or old_price <= 0:
            continue

        pct_change = (current_price - old_price) / old_price

        if abs(pct_change) >= ALERT_THRESHOLD:
            item["price_alert"] = {
                "direction": "up" if pct_change > 0 else "down",
                "pct_change": round(pct_change * 100, 1),
                "old_price": old_price,
            }
            alert_count += 1

    if alert_count:
        logger.info("Price alerts: %d items flagged for %s", alert_count, vendor)

    return items
