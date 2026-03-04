"""Forkable email automation — polls Gmail, parses orders, creates Notion entries.

Background polling service that replaces the n8n + Railway FastAPI setup.
Runs as a daemon thread inside the Flask app.

Flow:
  1. Poll Gmail every 60s for unread Forkable emails
  2. Classify: weekly schedule or day-before order
  3. Parse with Claude (Sonnet for speed/cost)
  4. Create/update Notion Catering Orders rows
  5. Mark email as read + label it
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests as http_requests

logger = logging.getLogger(__name__)

# ── Config ──

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("FORKABLE_CLAUDE_MODEL", "claude-sonnet-4-20250514").strip()
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN", "").strip()
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "").strip()
POLL_INTERVAL = int(os.getenv("FORKABLE_POLL_INTERVAL", "60"))

FORKABLE_SENDER = "team@forkable.com"
NOTION_BASE = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": "Bearer %s" % NOTION_API_KEY,
    "Content-Type": "application/json",
    "Notion-Version": "2025-09-03",
}

# Production Catering Operations & Sales data source ID
ORDERS_DS_ID = "2ca42679-f1a3-80d2-8ae0-000b9d39643b"

# ── Claude Prompts ──

PARSE_WEEKLY_PROMPT = """You parse Forkable weekly order schedule emails into structured JSON.

These emails list upcoming order dates with approximate meal counts for a restaurant.

Respond with JSON only:
{
  "location": "Restaurant address",
  "orders": [
    {
      "date": "YYYY-MM-DD",
      "day_name": "Monday",
      "approximate_meals": 5
    }
  ]
}

Rules:
- Extract ALL order dates from the table
- Convert dates to YYYY-MM-DD format
- approximate_meals: parse the number from "~ X meals" or "~ X - Y meals" (use the higher number for ranges)
- location: the restaurant address shown in the email"""

PARSE_ORDER_PROMPT = """You parse Forkable catering order detail emails into structured JSON.

These emails contain specific order details with groups (companies), individual meal items, prices, and totals.

Respond with JSON only:
{
  "order_date": "YYYY-MM-DD",
  "pickup_time": "HH:MM AM/PM",
  "location": "Full address",
  "driver": "Driver name or null",
  "groups": [
    {
      "group_code": "Two-letter code (e.g., HE, KM)",
      "company_name": "Full company name if shown, else group code",
      "items": [
        {
          "person_name": "Name",
          "description": "Meal description",
          "price": 12.50
        }
      ],
      "subtotal": 85.00,
      "item_count": 4
    }
  ],
  "tax": 11.93,
  "total": 182.48,
  "meal_count": 11,
  "attachment_count": 2
}

Rules:
- Extract ALL groups -- each group starts with "Group XX" header
- For each group, extract every individual item with person name, description, and price
- Subtotal = sum of items in that group
- item_count = number of items in that group
- meal_count = total items across all groups
- If tax or total are shown, extract them exactly
- Dates should be in YYYY-MM-DD format
- If the email mentions attachments (PDFs, labels), count them in attachment_count"""


# ── Data Classes ──

@dataclass
class ScheduledOrder:
    date: str
    day_name: str
    approximate_meals: int


@dataclass
class WeeklySchedule:
    location: str
    orders: list


@dataclass
class OrderItem:
    person_name: str
    description: str
    price: float


@dataclass
class OrderGroup:
    group_code: str
    company_name: str
    items: list
    subtotal: float
    item_count: int


@dataclass
class ForkableOrder:
    order_date: str
    pickup_time: str
    location: str
    driver: str
    groups: list
    tax: float
    total: float
    meal_count: int
    attachment_names: list = field(default_factory=list)


# ── Gmail Client (shared module) ──

from tools.gmail_service.gmail_client import GmailClient  # noqa: E402


# Add backward-compatible get_unread_forkable method
_orig_gmail_init = GmailClient.__init__


def _patched_init(self):
    _orig_gmail_init(self)


def _get_unread_forkable(self, max_results=10):
    """Fetch unread emails from Forkable (backward-compatible wrapper)."""
    return self.get_unread_from(FORKABLE_SENDER, max_results=max_results)


GmailClient.get_unread_forkable = _get_unread_forkable


# ── Forkable Detection ──

def is_forkable_order(email_data):
    """Check if email is a Forkable order (not feedback/survey)."""
    subject = email_data.get("subject", "").lower()
    skip = ["feedback", "operating on", "are you?", "survey", "newsletter"]
    if any(p in subject for p in skip):
        return False
    order_patterns = ["pickup", "please confirm", "week of", "confirm changes", "order"]
    return any(p in subject for p in order_patterns)


def is_weekly_schedule(email_data):
    subject = email_data.get("subject", "").lower()
    return "please confirm" in subject or "week of" in subject


# ── Claude Parsing ──

def _parse_json(text):
    """Extract JSON from Claude response."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1:
        text = text[first:last + 1]
    return json.loads(text)


def parse_weekly(body_text):
    """Parse weekly schedule email with Claude."""
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package not installed")
        return None

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        message = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=1024,
            system=PARSE_WEEKLY_PROMPT,
            messages=[{"role": "user", "content": "Parse this Forkable weekly schedule:\n\n%s" % body_text}],
        )
        data = _parse_json(message.content[0].text)
        orders = [
            ScheduledOrder(
                date=o.get("date", ""),
                day_name=o.get("day_name", ""),
                approximate_meals=int(o.get("approximate_meals", 0)),
            )
            for o in data.get("orders", [])
        ]
        return WeeklySchedule(location=data.get("location", ""), orders=orders)
    except Exception as e:
        logger.error("Error parsing weekly schedule: %s", e, exc_info=True)
        return None


def parse_order(body_text, attachments=None):
    """Parse day-before order detail email with Claude."""
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package not installed")
        return None

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        message = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=8192,
            system=PARSE_ORDER_PROMPT,
            messages=[{"role": "user", "content": "Parse this Forkable order email:\n\n%s" % body_text}],
        )
        data = _parse_json(message.content[0].text)
        groups = []
        for g in data.get("groups", []):
            items = [
                OrderItem(
                    person_name=i.get("person_name", ""),
                    description=i.get("description", ""),
                    price=float(i.get("price", 0)),
                )
                for i in g.get("items", [])
            ]
            groups.append(OrderGroup(
                group_code=g.get("group_code", ""),
                company_name=g.get("company_name", g.get("group_code", "")),
                items=items,
                subtotal=float(g.get("subtotal", 0)),
                item_count=int(g.get("item_count", len(items))),
            ))

        att_names = []
        if attachments:
            att_names = [a.get("filename", "") for a in attachments if a.get("filename")]

        return ForkableOrder(
            order_date=data.get("order_date", ""),
            pickup_time=data.get("pickup_time", ""),
            location=data.get("location", ""),
            driver=data.get("driver") or "",
            groups=groups,
            tax=float(data.get("tax", 0)),
            total=float(data.get("total", 0)),
            meal_count=int(data.get("meal_count", 0)),
            attachment_names=att_names,
        )
    except Exception as e:
        logger.error("Error parsing order details: %s", e, exc_info=True)
        return None


# ── Notion Orders (Production Catering Operations & Sales DB) ──


def _find_scheduled_by_date(order_date):
    """Find Tentative Forkable orders for a date."""
    if not NOTION_API_KEY:
        return []
    try:
        resp = http_requests.post(
            "%s/data_sources/%s/query" % (NOTION_BASE, ORDERS_DS_ID),
            headers=NOTION_HEADERS,
            json={"filter": {"and": [
                {"property": "Delivery Date & Time", "date": {"equals": order_date}},
                {"property": "Order Status", "select": {"equals": "Tentative"}},
                {"property": "Order Platform", "select": {"equals": "Forkable"}},
            ]}},
            timeout=30,
        )
        if resp.status_code != 200:
            return []
        results = []
        for page in resp.json().get("results", []):
            props = page.get("properties", {})
            title = ""
            for v in props.values():
                if v.get("type") == "title":
                    tl = v.get("title", [])
                    title = tl[0]["text"]["content"] if tl else ""
                    break
            results.append({"id": page["id"], "title": title})
        return results
    except Exception as e:
        logger.error("Error finding scheduled orders: %s", e)
        return []


def _find_orders_by_date(order_date):
    """Find any Forkable orders for a date (dedup check)."""
    if not NOTION_API_KEY:
        return []
    try:
        resp = http_requests.post(
            "%s/data_sources/%s/query" % (NOTION_BASE, ORDERS_DS_ID),
            headers=NOTION_HEADERS,
            json={"filter": {"and": [
                {"property": "Delivery Date & Time", "date": {"equals": order_date}},
                {"property": "Order Platform", "select": {"equals": "Forkable"}},
            ]}},
            timeout=30,
        )
        if resp.status_code != 200:
            return []
        return resp.json().get("results", [])
    except Exception as e:
        logger.error("Error finding orders by date: %s", e)
        return []


def _create_scheduled_order(title, order_date, approx_meals=0, location="", gmail_id=""):
    """Create a Tentative placeholder row in production Catering DB."""
    if not NOTION_API_KEY:
        return None
    props = {
        "Order Name": {"title": [{"text": {"content": title[:200]}}]},
        "Order Status": {"select": {"name": "Tentative"}},
        "Order Platform": {"select": {"name": "Forkable"}},
        "Delivery Method": {"select": {"name": "Forkable"}},
    }
    if order_date:
        props["Delivery Date & Time"] = {"date": {"start": order_date}}
    notes_parts = []
    if approx_meals:
        notes_parts.append("~%d items" % approx_meals)
    if gmail_id:
        notes_parts.append("Gmail: %s" % gmail_id)
    if location:
        props["Delivery Address"] = {"rich_text": [{"text": {"content": location[:2000]}}]}
    if notes_parts:
        props["Notes"] = {"rich_text": [{"text": {"content": " | ".join(notes_parts)}}]}
    try:
        resp = http_requests.post(
            "%s/pages" % NOTION_BASE, headers=NOTION_HEADERS,
            json={"parent": {"data_source_id": ORDERS_DS_ID}, "properties": props},
            timeout=30,
        )
        if resp.status_code == 200:
            page_id = resp.json().get("id")
            logger.info("Created scheduled order: %s", title)
            return page_id
        logger.error("Notion error (scheduled): %s - %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.error("Error creating scheduled order: %s", e)
    return None


def _build_content_blocks(order):
    """Build Notion content blocks for order page body."""
    blocks = []
    blocks.append({
        "object": "block", "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Order Details"}}]},
    })

    parts = []
    if order.pickup_time:
        parts.append("Pickup: %s" % order.pickup_time)
    if order.driver:
        parts.append("Driver: %s" % order.driver)
    if order.location:
        parts.append("Location: %s" % order.location)
    if parts:
        blocks.append({
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": " | ".join(parts)}}]},
        })

    blocks.append({"object": "block", "type": "divider", "divider": {}})

    for group in order.groups:
        header = "Group %s" % group.group_code
        if group.company_name and group.company_name != group.group_code:
            header += " (%s)" % group.company_name
        header += " -- %d items -- $%s" % (group.item_count, "{:,.2f}".format(group.subtotal))
        blocks.append({
            "object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": header}}]},
        })
        for item in group.items:
            bullet = "%s -- %s -- $%s" % (item.person_name, item.description, "{:,.2f}".format(item.price))
            blocks.append({
                "object": "block", "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": bullet}}]},
            })

    blocks.append({"object": "block", "type": "divider", "divider": {}})
    totals = "Tax: $%s | Total: $%s | %d meals" % (
        "{:,.2f}".format(order.tax), "{:,.2f}".format(order.total), order.meal_count)
    blocks.append({
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": totals}, "annotations": {"bold": True}}]},
    })
    return blocks


def _order_props(title, order, gmail_id="", status="Comfirmed"):
    """Build production Catering DB property dict from a parsed order."""
    props = {
        "Order Name": {"title": [{"text": {"content": title[:200]}}]},
        "Order Status": {"select": {"name": status}},
        "Order Platform": {"select": {"name": "Forkable"}},
        "Delivery Method": {"select": {"name": "Forkable"}},
    }
    if order.pickup_time:
        props["Pick Up Time"] = {"rich_text": [{"text": {"content": order.pickup_time}}]}
    if order.location:
        props["Delivery Address"] = {"rich_text": [{"text": {"content": order.location[:2000]}}]}
    if order.driver:
        props["Driver Name"] = {"rich_text": [{"text": {"content": order.driver}}]}

    # Compute subtotal from groups (total minus tax)
    subtotal = round(order.total - order.tax, 2) if order.total and order.tax else 0
    if subtotal > 0:
        props["Subtotal"] = {"number": subtotal}
    if order.tax:
        props["Tax"] = {"number": order.tax}

    # Build delivery notes: company names, item count, gmail ref
    notes_parts = []
    company_names = []
    for g in order.groups:
        if g.company_name and g.company_name != g.group_code and g.company_name not in company_names:
            company_names.append(g.company_name)
    if company_names:
        notes_parts.append(", ".join(company_names))
    if order.meal_count:
        notes_parts.append("%d items" % order.meal_count)
    if gmail_id:
        notes_parts.append("Gmail: %s" % gmail_id)
    if notes_parts:
        props["Delivery Notes"] = {"rich_text": [{"text": {"content": " | ".join(notes_parts)[:2000]}}]}

    return props


def _update_order_with_details(page_id, title, order, gmail_id=""):
    """Update a Tentative row to Confirmed with full details + content blocks."""
    props = _order_props(title, order, gmail_id, status="Comfirmed")
    if order.order_date:
        props["Delivery Date & Time"] = {"date": {"start": order.order_date}}

    try:
        resp = http_requests.patch(
            "%s/pages/%s" % (NOTION_BASE, page_id),
            headers=NOTION_HEADERS,
            json={"properties": props},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error("Notion update error: %s - %s", resp.status_code, resp.text[:200])
            return False

        if order.groups:
            blocks = _build_content_blocks(order)
            http_requests.patch(
                "%s/blocks/%s/children" % (NOTION_BASE, page_id),
                headers=NOTION_HEADERS,
                json={"children": blocks},
                timeout=30,
            )
        logger.info("Updated order: %s", title)
        return True
    except Exception as e:
        logger.error("Error updating order: %s", e)
        return False


def _create_full_order(title, order, gmail_id=""):
    """Create a new Confirmed order row (no Tentative placeholder found)."""
    if not NOTION_API_KEY:
        return None
    props = _order_props(title, order, gmail_id, status="Comfirmed")
    if order.order_date:
        props["Delivery Date & Time"] = {"date": {"start": order.order_date}}

    try:
        resp = http_requests.post(
            "%s/pages" % NOTION_BASE, headers=NOTION_HEADERS,
            json={"parent": {"data_source_id": ORDERS_DS_ID}, "properties": props},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error("Notion error (full order): %s - %s", resp.status_code, resp.text[:200])
            return None
        page_id = resp.json().get("id")
        if order.groups:
            blocks = _build_content_blocks(order)
            http_requests.patch(
                "%s/blocks/%s/children" % (NOTION_BASE, page_id),
                headers=NOTION_HEADERS,
                json={"children": blocks},
                timeout=30,
            )
        logger.info("Created full order: %s", title)
        return page_id
    except Exception as e:
        logger.error("Error creating full order: %s", e)
        return None


# ── Polling Engine ──

class ForkablePoller:
    """Background poller that processes Forkable emails automatically."""

    def __init__(self):
        self.gmail = GmailClient()
        self.processed_ids = set()
        self.activity_log = deque(maxlen=50)
        self.last_poll = None
        self.errors = deque(maxlen=20)
        self.running = False
        self._thread = None

    def _log(self, msg):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = "%s  %s" % (ts, msg)
        self.activity_log.append(entry)
        logger.info(msg)

    def _log_error(self, msg):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = "%s  %s" % (ts, msg)
        self.errors.append(entry)
        logger.error(msg)

    def poll_once(self):
        """Run one poll cycle. Returns number of emails processed."""
        self.last_poll = datetime.now()
        emails = self.gmail.get_unread_forkable(max_results=10)
        if not emails:
            return 0

        count = 0
        for email in emails:
            eid = email["id"]
            if eid in self.processed_ids:
                continue

            if not is_forkable_order(email):
                self.gmail.mark_as_read(eid)
                self.gmail.add_label(eid, "Livite/Forkable-Skipped")
                self.processed_ids.add(eid)
                self._log("Skipped non-order: %s" % email["subject"][:60])
                continue

            try:
                if is_weekly_schedule(email):
                    self._handle_weekly(email)
                else:
                    self._handle_order(email)

                self.gmail.mark_as_read(eid)
                self.gmail.add_label(eid, "Livite/Forkable")
                self.processed_ids.add(eid)
                count += 1
            except Exception as e:
                self._log_error("Error processing %s: %s" % (email["subject"][:40], e))

        return count

    def _handle_weekly(self, email):
        """Process a weekly schedule email."""
        schedule = parse_weekly(email["body_text"])
        if not schedule:
            self._log_error("Failed to parse weekly: %s" % email["subject"][:60])
            return

        created = 0
        for o in schedule.orders:
            if not o.date:
                continue
            # Skip if already exists
            existing = _find_orders_by_date(o.date)
            if existing:
                continue
            title = "Forkable -- %s %s" % (o.day_name, o.date)
            page_id = _create_scheduled_order(
                title, o.date,
                approx_meals=o.approximate_meals,
                location=schedule.location,
                gmail_id=email["id"],
            )
            if page_id:
                created += 1

        self._log("Weekly schedule: %d dates, %d created -- %s" % (
            len(schedule.orders), created, email["subject"][:50]))

    def _handle_order(self, email):
        """Process a day-before order detail email."""
        order = parse_order(email["body_text"], email.get("attachments"))
        if not order:
            self._log_error("Failed to parse order: %s" % email["subject"][:60])
            return

        title = "Forkable -- %s" % order.order_date

        # Check for existing Scheduled placeholder
        scheduled = _find_scheduled_by_date(order.order_date)
        if scheduled:
            _update_order_with_details(
                scheduled[0]["id"], title, order, gmail_id=email["id"])
            self._log("Updated scheduled -> confirmed: %s (%d groups, $%s)" % (
                order.order_date, len(order.groups), "{:,.2f}".format(order.total)))
        else:
            # Check for any existing row to avoid duplicates
            existing = _find_orders_by_date(order.order_date)
            if existing:
                self._log("Order already exists for %s, skipping" % order.order_date)
                return
            _create_full_order(title, order, gmail_id=email["id"])
            self._log("Created new order: %s (%d groups, $%s)" % (
                order.order_date, len(order.groups), "{:,.2f}".format(order.total)))

    def start(self):
        """Start the background polling thread."""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="forkable-poller")
        self._thread.start()
        self._log("Forkable poller started (interval: %ds)" % POLL_INTERVAL)

    def stop(self):
        self.running = False
        self._log("Forkable poller stopped")

    def _loop(self):
        while self.running:
            try:
                self.poll_once()
            except Exception as e:
                self._log_error("Poll cycle error: %s" % e)
            time.sleep(POLL_INTERVAL)


# Module-level singleton
poller = ForkablePoller()
