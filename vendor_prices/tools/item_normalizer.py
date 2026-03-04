"""Normalize vendor item names against the Items Master list.

Multi-step pipeline:
1. Alias store match (vendor_item_name previously confirmed by user)
2. Exact match (Items Master name or Notion Aliases field)
3. Claude AI fuzzy match (batch, with confidence scores)
4. Auto-learn: save confirmed mappings back to alias store
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

import anthropic

from vendor_prices.prompts.normalize_items import build_normalize_prompt

logger = logging.getLogger(__name__)

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
BATCH_SIZE = 20
AUTO_MATCH_THRESHOLD = 0.85
REVIEW_THRESHOLD = 0.50

# ── Alias store: persists vendor_item_name → master_item_id/name across sessions ──

_ALIAS_STORE_PATH = Path(__file__).resolve().parent.parent.parent / ".tmp" / "vendor_aliases.json"
_alias_store_lock = threading.Lock()


def _load_alias_store() -> dict:
    """Load the persisted alias store from disk. Returns {vendor_item_name_lower: {master_item_id, master_item_name}}."""
    try:
        if _ALIAS_STORE_PATH.exists():
            with open(_ALIAS_STORE_PATH) as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("Failed to load alias store: %s", e)
    return {}


def _save_alias_store(store: dict) -> None:
    """Persist the alias store to disk."""
    try:
        _ALIAS_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_ALIAS_STORE_PATH, "w") as f:
            json.dump(store, f, indent=2)
    except IOError as e:
        logger.warning("Failed to save alias store: %s", e)


def learn_alias(vendor_item_name: str, master_item_id: str, master_item_name: str) -> None:
    """Record a confirmed mapping (vendor item name → master item) in the alias store.

    Call this when a user confirms or manually maps an item during review.
    """
    with _alias_store_lock:
        store = _load_alias_store()
        key = vendor_item_name.strip().lower()
        store[key] = {
            "master_item_id": master_item_id,
            "master_item_name": master_item_name,
        }
        _save_alias_store(store)
        logger.info("Learned alias: %r → %r (%s)", vendor_item_name, master_item_name, master_item_id)


def forget_alias(vendor_item_name: str) -> bool:
    """Remove a learned alias from the store. Returns True if it existed."""
    with _alias_store_lock:
        store = _load_alias_store()
        key = vendor_item_name.strip().lower()
        if key in store:
            del store[key]
            _save_alias_store(store)
            return True
    return False


def get_all_aliases() -> dict:
    """Return the full alias store for inspection."""
    with _alias_store_lock:
        return _load_alias_store()


def _parse_json_response(text: str) -> dict:
    """Robustly parse JSON from Claude's response."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    raise ValueError(f"No JSON found in response: {text[:200]}")


class ItemNormalizer:
    """Matches vendor items to the master item list.

    Match priority:
    1. Alias store (user-confirmed mappings from previous sessions)
    2. Items Master exact name match
    3. Items Master Notion Aliases field match
    4. Claude AI fuzzy match
    """

    def __init__(self, master_items: list[dict]):
        """Initialize with master items from Notion.

        Args:
            master_items: List of dicts with 'id', 'name', 'aliases' (JSON string).
        """
        self.master_items = master_items
        self._name_to_id: dict[str, str] = {}
        self._alias_to_id: dict[str, str] = {}
        self._id_to_name: dict[str, str] = {}

        for item in master_items:
            name = item["name"].strip().lower()
            self._name_to_id[name] = item["id"]
            self._id_to_name[item["id"]] = item["name"]

            aliases_raw = item.get("aliases", "")
            if aliases_raw:
                try:
                    aliases = json.loads(aliases_raw)
                    for alias in aliases:
                        self._alias_to_id[alias.strip().lower()] = item["id"]
                except (json.JSONDecodeError, TypeError):
                    pass

        # Load persisted alias store (user-confirmed mappings)
        self._learned_aliases = _load_alias_store()

    def _exact_match(self, vendor_item_name: str) -> tuple[str, str] | tuple[None, None]:
        """Check alias store, then Items Master name/aliases. Returns (master_item_id, master_item_name) or (None, None)."""
        key = vendor_item_name.strip().lower()

        # Priority 1: user-confirmed alias store
        if key in self._learned_aliases:
            entry = self._learned_aliases[key]
            mid = entry.get("master_item_id", "")
            mname = entry.get("master_item_name", vendor_item_name)
            # Validate the ID still exists in master
            if mid and mid in self._id_to_name:
                return mid, self._id_to_name[mid]
            # Name may have been renamed — fall through to Notion lookup
            mid2 = self._name_to_id.get(mname.strip().lower())
            if mid2:
                return mid2, self._id_to_name[mid2]

        # Priority 2: Items Master exact name
        mid = self._name_to_id.get(key)
        if mid:
            return mid, self._id_to_name[mid]

        # Priority 3: Notion Aliases field
        mid = self._alias_to_id.get(key)
        if mid:
            return mid, self._id_to_name.get(mid, vendor_item_name)

        return None, None

    def _ai_match_batch(self, vendor_items: list[dict]) -> list[dict]:
        """Use Claude to fuzzy-match a batch of items.

        Returns list of dicts with:
            vendor_item, master_item, master_item_id, confidence,
            suggested_name, suggested_category, reasoning, status
        """
        master_names = [item["name"] for item in self.master_items]
        system, user = build_normalize_prompt(master_names, vendor_items)

        client = anthropic.Anthropic()
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )

        if not message.content:
            raise ValueError("Claude returned empty response content")
        result = _parse_json_response(message.content[0].text)
        matches = result.get("matches", [])

        # Enrich with master item IDs and status
        for match in matches:
            master_name = match.get("master_item", "")
            confidence = match.get("confidence", 0)

            if master_name == "NEW" or confidence < REVIEW_THRESHOLD:
                match["master_item_id"] = None
                match["status"] = "new"
            else:
                # Find the master item ID
                mid = self._name_to_id.get(master_name.strip().lower())
                match["master_item_id"] = mid
                if confidence >= AUTO_MATCH_THRESHOLD:
                    match["status"] = "matched"
                else:
                    match["status"] = "review"

        return matches

    def normalize(self, extracted_items: list[dict], vendor: str) -> list[dict]:
        """Normalize a list of extracted items.

        Args:
            extracted_items: List from price_extractor with 'item_name', 'price', etc.
            vendor: Vendor name string.

        Returns list of dicts, each with:
            - All original fields from extracted_items
            - master_item_id: matched Items Master page ID or None
            - master_item_name: matched name or suggested new name
            - status: 'matched', 'review', or 'new'
            - confidence: float
        """
        results = []
        unmatched = []

        for item in extracted_items:
            name = item.get("item_name", "")

            # Steps 1-3: alias store → exact name → Notion alias
            mid, master_name = self._exact_match(name)
            if mid:
                results.append({
                    **item,
                    "master_item_id": mid,
                    "master_item_name": master_name,
                    "status": "matched",
                    "confidence": 1.0,
                })
                continue

            unmatched.append(item)

        # Step 3: AI fuzzy match in batches
        if unmatched:
            for i in range(0, len(unmatched), BATCH_SIZE):
                batch = unmatched[i : i + BATCH_SIZE]
                batch_input = [
                    {
                        "item_name": item["item_name"],
                        "brand": item.get("brand", ""),
                        "unit_detail": item.get("unit_detail", ""),
                        "vendor": vendor,
                    }
                    for item in batch
                ]

                try:
                    ai_matches = self._ai_match_batch(batch_input)
                except Exception as e:
                    logger.error("AI normalization failed for batch: %s", e)
                    # Fall back to marking all as new
                    ai_matches = [
                        {
                            "vendor_item": item["item_name"],
                            "master_item": "NEW",
                            "master_item_id": None,
                            "confidence": 0,
                            "suggested_name": item["item_name"],
                            "suggested_category": item.get("category_hint", "other"),
                            "status": "new",
                        }
                        for item in batch
                    ]

                # Merge AI results back with original items
                for item, match in zip(batch, ai_matches):
                    results.append({
                        **item,
                        "master_item_id": match.get("master_item_id"),
                        "master_item_name": match.get("master_item")
                        if match.get("status") == "matched"
                        else match.get("suggested_name", item["item_name"]),
                        "status": match.get("status", "new"),
                        "confidence": match.get("confidence", 0),
                        "suggested_name": match.get("suggested_name"),
                        "suggested_category": match.get("suggested_category"),
                    })

        matched = sum(1 for r in results if r["status"] == "matched")
        review = sum(1 for r in results if r["status"] == "review")
        new = sum(1 for r in results if r["status"] == "new")
        logger.info(
            f"Normalization: {matched} matched, {review} review, {new} new "
            f"(out of {len(results)} items)"
        )

        return results
