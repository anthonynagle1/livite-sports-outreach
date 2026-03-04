"""Normalize vendor item names against the Items Master list.

Multi-step pipeline:
1. Exact match (vendor item name + vendor already seen)
2. Alias match (check Items Master Aliases field)
3. Claude AI fuzzy match (batch, with confidence scores)
4. Auto-learn aliases from confirmed matches
"""
from __future__ import annotations

import json
import logging
import os
import re

import anthropic

from vendor_prices.prompts.normalize_items import build_normalize_prompt

logger = logging.getLogger(__name__)

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
BATCH_SIZE = 20
AUTO_MATCH_THRESHOLD = 0.85
REVIEW_THRESHOLD = 0.50


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
    """Matches vendor items to the master item list."""

    def __init__(self, master_items: list[dict]):
        """Initialize with master items from Notion.

        Args:
            master_items: List of dicts with 'id', 'name', 'aliases' (JSON string).
        """
        self.master_items = master_items
        self._name_to_id: dict[str, str] = {}
        self._alias_to_id: dict[str, str] = {}

        for item in master_items:
            name = item["name"].strip().lower()
            self._name_to_id[name] = item["id"]

            aliases_raw = item.get("aliases", "")
            if aliases_raw:
                try:
                    aliases = json.loads(aliases_raw)
                    for alias in aliases:
                        self._alias_to_id[alias.strip().lower()] = item["id"]
                except (json.JSONDecodeError, TypeError):
                    pass

    def _exact_match(self, vendor_item_name: str) -> str | None:
        """Check for exact name or alias match. Returns master item ID or None."""
        key = vendor_item_name.strip().lower()
        return self._name_to_id.get(key) or self._alias_to_id.get(key)

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

            # Step 1 & 2: Exact / alias match
            mid = self._exact_match(name)
            if mid:
                # Find the master item name
                master_name = next(
                    (m["name"] for m in self.master_items if m["id"] == mid), name
                )
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
            "Normalization: %s matched, %s review, %s new (out of %s items)",
            matched, review, new, len(results)
        )

        return results
