"""Fetch and aggregate driver delivery data from Notion Catering Orders/Sales.

Computes weekly Mon-Sun breakdowns for Mustafa and Metrobi deliveries
to determine tip pool adjustments for payroll.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from .notion import (
    NOTION_API_KEY,
    HEADERS,
    BASE_URL,
    CATERING_DS_ID,
)

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger(__name__)

# Delivery methods we track for tip adjustments
TRACKED_METHODS = {"Mustafa", "Metrobi"}

# Mustafa payout: guaranteed $45/delivery.
# Normally $20 from fee + tips; extra fee goes to reserve.
# Low-tip orders use more of the fee (up to full amount) to hit guarantee.
MUSTAFA_GUARANTEE = 45
MUSTAFA_DEFAULT_FEE_PORTION = 20

# Diagnostic info from last fetch (for debugging empty results)
_last_diag = {}


def _monday_of(date_str):
    """Return the Monday (ISO week start) for a given YYYY-MM-DD date."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime("%Y-%m-%d")


def fetch_driver_orders():
    """Fetch all completed/confirmed orders delivered by Mustafa or Metrobi.

    Returns list of dicts with keys:
        date, method, name, subtotal, tips, delivery_fee, driver_cost
    Also sets module-level _last_diag with diagnostic info.
    """
    global _last_diag
    _last_diag = {"api_key_set": bool(NOTION_API_KEY), "ds_id": CATERING_DS_ID,
                  "status": None, "error": None, "total_pages": 0}

    if not NOTION_API_KEY:
        logger.warning("NOTION_API_KEY not set, skipping driver data")
        _last_diag["error"] = "NOTION_API_KEY not set"
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
            logger.error("Driver data query failed: %s", e)
            _last_diag["error"] = str(e)
            break

        _last_diag["status"] = resp.status_code
        if resp.status_code != 200:
            logger.error("Driver data query error %s: %s",
                         resp.status_code, resp.text[:200])
            _last_diag["error"] = resp.text[:200]
            break

        data = resp.json()
        _last_diag["total_pages"] += len(data.get("results", []))
        for page in data.get("results", []):
            props = page.get("properties", {})

            # Order Status — only Completed/Confirmed
            status_prop = props.get("Order Status", {})
            status = ""
            if status_prop.get("type") == "select" and status_prop.get("select"):
                status = status_prop["select"].get("name", "")
            if status not in ("Completed", "Confirmed", "Comfirmed"):
                continue

            # Delivery Method
            method_prop = props.get("Delivery Method", {})
            method = ""
            if method_prop.get("type") == "select" and method_prop.get("select"):
                method = method_prop["select"].get("name", "")
            if method not in TRACKED_METHODS:
                continue

            # Delivery date
            date_prop = props.get("Delivery Date & Time", {})
            delivery_date = ""
            if date_prop.get("type") == "date" and date_prop.get("date"):
                delivery_date = (date_prop["date"].get("start", "") or "")[:10]
            if not delivery_date:
                continue

            # Tips
            tips_prop = props.get("Tips", {})
            tips = tips_prop.get("number", 0) or 0

            # Platform Delivery Fee (what customer paid)
            fee_prop = props.get("Platform Delivery Fee ", {})
            delivery_fee = fee_prop.get("number", 0) or 0

            # Delivery Cost (what we pay the service, e.g. Metrobi)
            cost_prop = props.get("Delivery Cost", {})
            driver_cost = cost_prop.get("number", 0) or 0

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
                "date": delivery_date,
                "method": method,
                "name": name,
                "subtotal": round(subtotal, 2),
                "tips": round(tips, 2),
                "delivery_fee": round(delivery_fee, 2),
                "driver_cost": round(driver_cost, 2),
            })

        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

    return orders


def aggregate_by_week(orders):
    """Group orders into Mon-Sun weeks with per-driver totals.

    Returns list of week dicts sorted most recent first.
    """
    weeks = defaultdict(lambda: {"mustafa": [], "metrobi": []})

    for o in orders:
        monday = _monday_of(o["date"])
        key = "mustafa" if o["method"] == "Mustafa" else "metrobi"
        weeks[monday][key].append(o)

    result = []
    for monday_str in sorted(weeks.keys(), reverse=True):
        w = weeks[monday_str]
        monday = datetime.strptime(monday_str, "%Y-%m-%d")
        sunday = monday + timedelta(days=6)

        # Mustafa aggregation — guarantee $45/delivery
        # Normal: $20 from fee + tips. Low tips: use more fee to hit $45.
        # Excess fee goes to reserve for low-tip days.
        m_orders = sorted(w["mustafa"], key=lambda x: x["date"])
        m_fees = 0
        m_tips = 0
        m_fee_used = 0
        m_reserve = 0
        m_payout = 0
        m_shortfall = 0
        for o in m_orders:
            fee = o["delivery_fee"]
            tips = o["tips"]
            # How much of the fee goes to Mustafa for this delivery
            fee_to_driver = min(fee, max(MUSTAFA_DEFAULT_FEE_PORTION,
                                         MUSTAFA_GUARANTEE - tips))
            payout = fee_to_driver + tips
            reserve = fee - fee_to_driver
            shortfall = max(0, MUSTAFA_GUARANTEE - payout)
            o["fee_to_driver"] = round(fee_to_driver, 2)
            o["payout"] = round(payout, 2)
            o["reserve"] = round(reserve, 2)
            o["shortfall"] = round(shortfall, 2)
            m_fees += fee
            m_tips += tips
            m_fee_used += fee_to_driver
            m_reserve += reserve
            m_payout += payout
            m_shortfall += shortfall

        # Metrobi aggregation — use delivery fee first to cover cost,
        # remainder from tips. Maximizes excess tips for the team.
        mb_orders = sorted(w["metrobi"], key=lambda x: x["date"])
        mb_fees = sum(o["delivery_fee"] for o in mb_orders)
        mb_tips = sum(o["tips"] for o in mb_orders)
        mb_cost = sum(o["driver_cost"] for o in mb_orders)

        # Per-order breakdown
        mb_cost_from_tips = 0
        mb_tips_to_team = 0
        for o in mb_orders:
            covered_by_fee = min(o["delivery_fee"], o["driver_cost"])
            from_tips = max(0, o["driver_cost"] - o["delivery_fee"])
            to_team = o["tips"] - from_tips
            o["covered_by_fee"] = round(covered_by_fee, 2)
            o["cost_from_tips"] = round(from_tips, 2)
            o["tips_to_team"] = round(to_team, 2)
            mb_cost_from_tips += from_tips
            mb_tips_to_team += o["tips"] - from_tips

        # Tip adjustment = amount deducted from team tip pool
        # Mustafa tips go to Mustafa; Metrobi cost_from_tips pays Metrobi
        tip_adjustment = m_tips + mb_cost_from_tips

        result.append({
            "week_label": "%s - %s" % (
                monday.strftime("%b %d"), sunday.strftime("%b %d")),
            "start": monday_str,
            "end": sunday.strftime("%Y-%m-%d"),
            "mustafa": {
                "count": len(m_orders),
                "fees": round(m_fees, 2),
                "tips": round(m_tips, 2),
                "fee_used": round(m_fee_used, 2),
                "reserve": round(m_reserve, 2),
                "payout": round(m_payout, 2),
                "shortfall": round(m_shortfall, 2),
                "orders": m_orders,
            },
            "metrobi": {
                "count": len(mb_orders),
                "fees_collected": round(mb_fees, 2),
                "tips": round(mb_tips, 2),
                "driver_cost": round(mb_cost, 2),
                "cost_from_tips": round(mb_cost_from_tips, 2),
                "tips_to_team": round(mb_tips_to_team, 2),
                "orders": mb_orders,
            },
            "total_tip_adjustment": round(tip_adjustment, 2),
        })

    return result


def get_driver_weekly_data():
    """Main entry point — fetch and aggregate all driver delivery data.

    Returns dict with:
        weeks: list of weekly aggregates (most recent first)
        this_week: current week summary
        last_week: previous week summary
        mtd: month-to-date totals
    """
    orders = fetch_driver_orders()
    weeks = aggregate_by_week(orders)

    # Identify this week and last week
    today = datetime.now()
    this_monday = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    last_monday = (today - timedelta(days=today.weekday() + 7)).strftime("%Y-%m-%d")
    this_month = today.strftime("%Y-%m")

    this_week = None
    last_week = None
    mtd_mustafa_tips = 0
    mtd_metrobi_tip_cost = 0
    mtd_total = 0

    for w in weeks:
        if w["start"] == this_monday:
            this_week = w
        elif w["start"] == last_monday:
            last_week = w

        # MTD: weeks that overlap with current month
        if w["start"][:7] == this_month or w["end"][:7] == this_month:
            mtd_mustafa_tips += w["mustafa"]["tips"]
            mtd_metrobi_tip_cost += w["metrobi"]["cost_from_tips"]
            mtd_total += w["total_tip_adjustment"]

    return {
        "weeks": weeks,
        "this_week": this_week,
        "last_week": last_week,
        "mtd": {
            "mustafa_tips": round(mtd_mustafa_tips, 2),
            "metrobi_tip_cost": round(mtd_metrobi_tip_cost, 2),
            "total": round(mtd_total, 2),
        },
    }
