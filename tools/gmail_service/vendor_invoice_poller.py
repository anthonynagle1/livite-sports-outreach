"""Vendor invoice email poller — daemon that polls Gmail for vendor invoices.

Checks for unread emails matching configured vendor rules (e.g., Baldor daily
invoices), downloads attachments, runs them through the price extraction pipeline,
and saves as pending batches for review in the vendor prices UI.

Modeled on the ForkablePoller pattern in tools/catering/forkable.py.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv("VENDOR_EMAIL_POLL_INTERVAL", "300"))  # 5 min default

UPLOAD_DIR = Path(".tmp/vp_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
PENDING_DIR = Path(".tmp/vp_pending")
PENDING_DIR.mkdir(parents=True, exist_ok=True)

# ── Config ──

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "vendor_prices" / "config.yaml"


def _load_email_rules() -> dict:
    """Load email_rules from vendor_prices/config.yaml."""
    try:
        with open(_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("email_rules", {})
    except Exception as e:
        logger.error("Failed to load email rules: %s", e)
        return {}


class VendorInvoicePoller:
    """Background poller that checks Gmail for vendor invoices."""

    def __init__(self):
        self._gmail = None
        self.processed_ids = set()
        self.activity_log = deque(maxlen=50)
        self.errors = deque(maxlen=20)
        self.last_poll = None
        self.running = False
        self._thread = None
        self.pending_count = 0  # invoices waiting for review

    @property
    def gmail(self):
        if self._gmail is None:
            from tools.gmail_service.gmail_client import GmailClient
            self._gmail = GmailClient()
        return self._gmail

    def _log(self, msg):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.activity_log.append("%s  %s" % (ts, msg))
        logger.info(msg)

    def _log_error(self, msg):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.errors.append("%s  %s" % (ts, msg))
        logger.error(msg)

    def _count_pending(self) -> int:
        """Count pending invoice batches (from email) waiting for review."""
        count = 0
        for f in PENDING_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                if data.get("source") == "email":
                    count += 1
            except Exception:
                pass
        return count

    def poll_once(self) -> int:
        """Run one poll cycle. Returns number of invoices found."""
        self.last_poll = datetime.now()
        rules = _load_email_rules()
        if not rules:
            self._log("No email rules configured")
            return 0

        total_found = 0
        for vendor_key, rule in rules.items():
            try:
                found = self._poll_vendor(vendor_key, rule)
                total_found += found
            except Exception as e:
                self._log_error("Error polling %s: %s" % (vendor_key, e))

        self.pending_count = self._count_pending()
        return total_found

    def _poll_vendor(self, vendor_key: str, rule: dict) -> int:
        """Poll for a single vendor's invoices."""
        sender = rule.get("sender", "")
        subject_contains = rule.get("subject_contains", "")
        attachment_types = rule.get("attachment_types", [])
        vendor_display = rule.get("display_name", vendor_key.title())

        if not sender:
            return 0

        emails = self.gmail.get_unread_from(
            sender, subject_contains=subject_contains, max_results=5
        )
        if not emails:
            return 0

        found = 0
        for email in emails:
            eid = email["id"]
            if eid in self.processed_ids:
                continue

            # Check for matching attachments
            attachments = email.get("attachments", [])
            matching = []
            for att in attachments:
                ext = Path(att["filename"]).suffix.lower().lstrip(".")
                if not attachment_types or ext in attachment_types:
                    matching.append(att)

            if not matching:
                # No matching attachments — mark as read and skip
                self.gmail.mark_as_read(eid)
                self.gmail.add_label(eid, "Livite/Vendor-Invoice-Skipped")
                self.processed_ids.add(eid)
                self._log("Skipped (no attachments): %s" % email["subject"][:60])
                continue

            try:
                self._process_invoice(vendor_key, vendor_display, email, matching)
                self.gmail.mark_as_read(eid)
                self.gmail.add_label(eid, "Livite/Vendor-Invoice")
                self.processed_ids.add(eid)
                found += 1
            except Exception as e:
                self._log_error("Error processing invoice from %s: %s" % (
                    vendor_display, e))

        return found

    def _process_invoice(self, vendor_key: str, vendor_display: str,
                         email: dict, attachments: list):
        """Download attachment, extract prices, save as pending batch."""
        from vendor_prices.tools import notion_sync, price_extractor
        from vendor_prices.tools.item_normalizer import ItemNormalizer
        from vendor_prices.tools.unit_normalizer import enrich_items_with_units

        items_db_id = os.getenv("NOTION_ITEMS_DB_ID", "")

        all_items = []
        filenames = []

        for att in attachments:
            # Download attachment
            att_bytes = self.gmail.download_attachment(email["id"], att["attachment_id"])
            if not att_bytes:
                self._log_error("Empty attachment: %s" % att["filename"])
                continue

            # Save to disk
            week = notion_sync.get_current_week()
            save_dir = UPLOAD_DIR / week / vendor_key
            save_dir.mkdir(parents=True, exist_ok=True)
            file_path = save_dir / att["filename"]
            file_path.write_bytes(att_bytes)

            # Extract prices
            try:
                extraction = price_extractor.extract_prices(str(file_path), vendor_display)
                items = extraction.get("items", [])
                items = enrich_items_with_units(items)

                # Normalize against master items
                master_items = notion_sync.get_all_items(items_db_id)
                normalizer = ItemNormalizer(master_items)
                normalized = normalizer.normalize(items, vendor_display)

                master_map = {m["id"]: m["name"] for m in master_items}
                for item in normalized:
                    mid = item.get("master_item_id")
                    if mid and mid in master_map:
                        item["master_item_name"] = master_map[mid]
                    item["source_file"] = att["filename"]

                all_items.extend(normalized)
                filenames.append(att["filename"])
                self._log("Extracted %d items from %s (%s)" % (
                    len(normalized), att["filename"], vendor_display))

            except Exception as e:
                self._log_error("Extraction failed for %s: %s" % (att["filename"], e))

        if not all_items:
            self._log("No items extracted from %s invoice" % vendor_display)
            return

        # Save as pending batch
        batch_id = str(uuid.uuid4())[:8]
        pending = {
            "batch_id": batch_id,
            "vendor": vendor_display,
            "vendor_key": vendor_key,
            "upload_type": "Purchase",
            "filenames": filenames,
            "items": all_items,
            "source": "email",
            "email_subject": email.get("subject", ""),
            "email_date": email.get("date", ""),
            "email_from": email.get("from_email", ""),
        }
        (PENDING_DIR / "%s.json" % batch_id).write_text(
            json.dumps(pending, default=str))

        self._log("Saved pending batch %s: %d items from %s (%s)" % (
            batch_id, len(all_items), vendor_display,
            email["subject"][:50]))

    def get_pending_invoices(self) -> list:
        """Get list of pending email-sourced batches."""
        pending = []
        for f in sorted(PENDING_DIR.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text())
                if data.get("source") == "email":
                    pending.append({
                        "batch_id": data["batch_id"],
                        "vendor": data.get("vendor", ""),
                        "vendor_key": data.get("vendor_key", ""),
                        "item_count": len(data.get("items", [])),
                        "filenames": data.get("filenames", []),
                        "email_subject": data.get("email_subject", ""),
                        "email_date": data.get("email_date", ""),
                    })
            except Exception:
                pass
        self.pending_count = len(pending)
        return pending

    def start(self):
        """Start the background polling thread."""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="vendor-invoice-poller")
        self._thread.start()
        self._log("Vendor invoice poller started (interval: %ds)" % POLL_INTERVAL)

    def stop(self):
        self.running = False
        self._log("Vendor invoice poller stopped")

    def _loop(self):
        # Count existing pending on startup
        self.pending_count = self._count_pending()
        while self.running:
            try:
                self.poll_once()
            except Exception as e:
                self._log_error("Poll cycle error: %s" % e)
            time.sleep(POLL_INTERVAL)


# Module-level singleton
poller = VendorInvoicePoller()
