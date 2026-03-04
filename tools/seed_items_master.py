"""Seed the Notion Items Master from historical price entries.

Reads all existing Price Entries from Notion, extracts unique vendor item names,
groups them by likely common name (using Claude), and creates Items Master entries
for any that don't already exist.

Usage:
    python tools/seed_items_master.py [--dry-run]

Options:
    --dry-run   Print what would be created without writing to Notion.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

DRY_RUN = "--dry-run" in sys.argv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from vendor_prices.tools.notion_sync import (
    get_all_items,
    get_price_entries,
    create_item,
)

ITEMS_DB_ID = os.getenv("NOTION_ITEMS_DB_ID", "")
PRICES_DB_ID = os.getenv("NOTION_PRICES_DB_ID", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

CATEGORY_MAP = {
    "protein": "Protein",
    "produce": "Produce",
    "dairy": "Dairy",
    "dry_goods": "Dry Goods",
    "canned_goods": "Canned Goods",
    "frozen": "Frozen",
    "beverages": "Beverages",
    "oils_condiments": "Oils & Condiments",
    "bakery": "Bakery",
    "paper_supplies": "Paper Supplies",
    "cleaning": "Cleaning",
    "equipment": "Equipment",
    "other": "Other",
}

NORMALIZE_PROMPT = """You are a restaurant ingredient expert. You'll receive a list of raw vendor item names
from food distributor invoices. Your job is to group them into clean, standardized common names
suitable for a restaurant inventory system.

Rules:
- Strip vendor codes, sizes, brand prefixes, and pack info from the name
- Use generic common names (e.g. "Tomatoes, Bulk" not "BULK GRP TOMATO 10/10 LB")
- If the same product appears multiple times with slightly different names, pick the best common name
- Infer the category from context
- Each entry must have a unique common_name

Respond with JSON only:
{
  "items": [
    {
      "common_name": "Bulk Grape Tomatoes",
      "category": "produce",
      "unit": "case",
      "vendor_aliases": ["BULK GRP TOMATO 10/10 LB", "GRP TOMATO BULK"]
    }
  ]
}

Category options: protein, produce, dairy, dry_goods, canned_goods, frozen, beverages, oils_condiments, bakery, paper_supplies, cleaning, equipment, other
Unit options: case, lb, each, gallon, bag, box, dozen, pack, oz, kg
"""


def _call_claude(vendor_names: list[str]) -> list[dict]:
    """Use Claude to normalize a batch of vendor item names."""
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    names_text = "\n".join(f"- {n}" for n in vendor_names)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        system=NORMALIZE_PROMPT,
        messages=[{"role": "user", "content":
            f"Normalize these {len(vendor_names)} vendor item names into clean common names:\n\n{names_text}"}],
    )
    text = message.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        data = json.loads(text[start:end])
        return data.get("items", [])
    logger.warning("Claude returned no parseable JSON")
    return []


def main():
    if not ITEMS_DB_ID or not PRICES_DB_ID:
        logger.error("Missing NOTION_ITEMS_DB_ID or NOTION_PRICES_DB_ID in .env")
        sys.exit(1)
    if not ANTHROPIC_API_KEY:
        logger.error("Missing ANTHROPIC_API_KEY in .env")
        sys.exit(1)

    logger.info("Fetching existing Items Master...")
    existing_items = get_all_items(ITEMS_DB_ID)
    existing_names = {i["name"].strip().lower() for i in existing_items}
    logger.info("  %d items already in master", len(existing_items))

    # Also collect all known aliases so we don't re-create under a different name
    existing_aliases: set[str] = set()
    for item in existing_items:
        aliases_raw = item.get("aliases", "")
        if aliases_raw:
            try:
                aliases = json.loads(aliases_raw)
                for a in aliases:
                    existing_aliases.add(a.strip().lower())
            except (json.JSONDecodeError, TypeError):
                pass

    logger.info("Fetching all price entries...")
    entries = get_price_entries(PRICES_DB_ID)
    logger.info("  %d price entries found", len(entries))

    # Collect unique vendor item names not already in master
    all_vendor_names: set[str] = set()
    for e in entries:
        name = (e.get("vendor_item_name") or "").strip()
        if name and name.lower() not in existing_names and name.lower() not in existing_aliases:
            all_vendor_names.add(name)

    logger.info("  %d unique vendor item names not in master", len(all_vendor_names))

    if not all_vendor_names:
        logger.info("Items Master is already up to date. Nothing to seed.")
        return

    # Process in batches of 40
    name_list = sorted(all_vendor_names)
    BATCH = 40
    all_normalized: list[dict] = []

    for i in range(0, len(name_list), BATCH):
        batch = name_list[i:i + BATCH]
        logger.info("Normalizing batch %d (%d items)...", i // BATCH + 1, len(batch))
        try:
            normalized = _call_claude(batch)
            all_normalized.extend(normalized)
            time.sleep(1)  # Rate limit courtesy
        except Exception as e:
            logger.error("Batch failed: %s", e)

    logger.info("Claude produced %d common names", len(all_normalized))

    # Deduplicate by common_name
    seen: dict[str, dict] = {}
    for item in all_normalized:
        key = item["common_name"].strip().lower()
        if key not in seen:
            seen[key] = item
        else:
            # Merge vendor_aliases
            seen[key]["vendor_aliases"] = list(set(
                seen[key].get("vendor_aliases", []) + item.get("vendor_aliases", [])
            ))

    # Filter out items that already exist (by common name)
    to_create = [v for k, v in seen.items() if k not in existing_names]
    logger.info("%d new items to create in master (skipping %d already existing)", len(to_create), len(seen) - len(to_create))

    if DRY_RUN:
        logger.info("=== DRY RUN — no writes ===")
        for item in to_create:
            logger.info("  Would create: %s [%s] | aliases: %s", item['common_name'], item.get('category'), item.get('vendor_aliases', []))
        return

    created = 0
    failed = 0
    for item in to_create:
        common_name = item["common_name"].strip()
        category = CATEGORY_MAP.get(item.get("category", "other"), "Other")
        unit = item.get("unit", "case")
        aliases = item.get("vendor_aliases", [])

        try:
            create_item(
                ITEMS_DB_ID,
                name=common_name,
                category=category,
                unit=unit,
                aliases=aliases,
            )
            created += 1
            logger.info("  Created: %s [%s]", common_name, category)
            time.sleep(0.4)  # Respect Notion rate limits
        except Exception as e:
            logger.error("  Failed to create %s: %s", common_name, e)
            failed += 1

    logger.info("\nDone. Created: %d, Failed: %d, Skipped (existing): %d", created, failed, len(seen) - len(to_create))


if __name__ == "__main__":
    main()
