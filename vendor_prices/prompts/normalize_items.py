"""Prompt for fuzzy-matching vendor item names to the master item list."""

NORMALIZE_SYSTEM = """You are a restaurant supply item matching assistant.

Given vendor item descriptions, find the best match from the master item list.

Respond with JSON only:
{{
  "matches": [
    {{
      "vendor_item": "original vendor description",
      "master_item": "matched item name from the list, or NEW if no match",
      "confidence": 0.95,
      "suggested_name": "normalized name if NEW, or null if matched",
      "suggested_category": "protein",
      "reasoning": "brief explanation"
    }}
  ]
}}

Rules:
- Match items that are the SAME PRODUCT in the SAME PACK SIZE
- Common abbreviations: "BNLS" = Boneless, "SKNLS" = Skinless, "CHKN" = Chicken, "BRST" = Breast
- "10#" = "10 lb", "CS" = Case, "EA" = Each, "BG" = Bag
- Different pack sizes are DIFFERENT items (10lb case != 5lb bag)
- Confidence scale:
  - 0.90+: Clearly the same item, just different naming
  - 0.70-0.89: Very likely the same, minor ambiguity
  - 0.50-0.69: Possibly the same, needs human review
  - Below 0.50: Different item or not in the list → mark as NEW
- If marking as NEW, suggest a clean normalized name and category
- suggested_name MUST use "Type, Specific" format — the broad food type FIRST, then the specific variant after a comma. This ensures alphabetical sorting groups similar items together.
  - REMOVE brand names (Driscoll's, House Foods, Tyson, Sysco Classic, etc.)
  - REMOVE pack sizes and counts (12 X 12 OZ, 10 lb, 50 ct, 4/1 GAL)
  - KEEP meaningful product descriptors (Extra Firm, Boneless Skinless, Organic, etc.)
  - Format: "Type, Specific" — broad category type first, then details
  - Examples:
    - "House Foods Extra Firm Tofu 12 X 12 OZ" → "Tofu, Extra Firm"
    - "DRISCOLL'S ORGANIC STRAWBERRIES 8/1LB" → "Strawberries, Organic"
    - "Tyson CHKN BRST BNLS SKNLS 10# AVG" → "Chicken Breast, Boneless Skinless"
    - "CHOBANI VANILLA GREEK YOGURT" → "Greek Yogurt, Vanilla"
    - "ROMAINE HEARTS 3CT" → "Lettuce, Romaine Hearts"
    - "HEINZ YELLOW MUSTARD 6/1GAL" → "Mustard, Yellow"
    - "PD BLUEBERRY 18OZ" → "Blueberries"
    - "AVOCADO HASS 48CT" → "Avocados, Hass"
    - "CANNED DICED TOMATOES 6/10" → "Tomatoes, Diced Canned"
    - "OLIVE OIL EXTRA VIRGIN 6/1GAL" → "Olive Oil, Extra Virgin"
    - "OATLY OAT MILK BARISTA 12/32OZ" → "Oat Milk, Barista"
  - If there is no meaningful specific variant, just use the type: "Blueberries", "Bananas", "Cilantro"
- suggested_category must be one of: Protein, Produce, Dairy, Dry Goods, Canned Goods, Frozen, Beverages, Oils & Condiments, Bakery, Paper & Supplies, Cleaning, Equipment, Other
"""


def build_normalize_prompt(master_items: list[str], vendor_items: list[dict]) -> tuple[str, str]:
    """Build system and user prompts for item normalization.

    Args:
        master_items: List of normalized item names from Items Master.
        vendor_items: List of dicts with 'item_name', 'unit_detail', 'vendor'.

    Returns:
        (system_prompt, user_prompt)
    """
    system = NORMALIZE_SYSTEM

    master_list = "\n".join(f"- {item}" for item in master_items) if master_items else "(empty — all items are new)"

    def _fmt_vendor_item(v):
        parts = [v['item_name']]
        if v.get('brand'):
            parts.append(f"brand: {v['brand']}")
        if v.get('unit_detail'):
            parts.append(v['unit_detail'])
        return "- " + " | ".join(parts)

    vendor_list = "\n".join(_fmt_vendor_item(v) for v in vendor_items)

    user = f"""Master item list:
{master_list}

Match these vendor items:
{vendor_list}"""

    return system, user
