"""Prompt for extracting vendor prices from various document formats."""

EXTRACT_PRICES_SYSTEM = """You extract vendor pricing data from restaurant supply price sheets, catalogs, and ordering portals.

The vendor is: {vendor}

Extract every item and its price. Respond with JSON only:
{{
  "items": [
    {{
      "item_name": "Full product description as shown on the sheet",
      "brand": "Brand/manufacturer name or null",
      "item_code": "Vendor product code/SKU or null",
      "price": 29.50,
      "quantity": 2,
      "unit": "case",
      "unit_detail": "10 lb case",
      "category_hint": "protein"
    }}
  ],
  "metadata": {{
    "effective_date": "YYYY-MM-DD or null",
    "total_items": 45,
    "notes": "any relevant notes about the price sheet"
  }}
}}

Rules:
- Extract EVERY item with a price — do not skip rows
- Preserve the vendor's exact product description in item_name
- Extract brand/manufacturer name separately in the "brand" field (e.g. "Driscoll's", "House Foods", "Tyson", "Sysco Classic"). The brand is the producer, NOT the vendor/distributor.
- If prices are listed as "per lb" or "per case", capture the unit correctly
- If a product has multiple price tiers (case vs. each), extract each as a separate row
- Item codes/SKUs are usually alphanumeric strings near the item name
- If the effective date is shown on the price sheet, extract it
- For percentage changes or "new price" columns, use the final current price
- category_hint should be one of: protein, produce, dairy, dry_goods, canned_goods, frozen, beverages, oils_condiments, bakery, paper_supplies, cleaning, equipment, other
- For unit, normalize to: case, lb, each, gallon, bag, box, dozen, pack
- unit_detail should describe the full pack size INCLUDING per-item size when present:
  - "10 lb case", "6/half gallon", "50 ct box", "4x5 lb", "6 can", "25 lb"
  - If the item name contains a per-unit size (e.g. "BLUEBERRY 18OZ" sold in a 3ct case), unit_detail should be "3/18oz" to capture BOTH the pack count AND per-item size
  - More examples: "YOGURT 32OZ" qty 4 → unit_detail "32oz", unit "each"; "OAT MILK 12/32OZ" → unit_detail "12/32oz"
  - For canned goods (e.g. "CORN WH KRL CQ #10"), use count notation: "6 can" means 6 cans per case
  - For produce by weight (e.g. "AVOCADO HASS" in a case), include total weight: "25 lb"
  - Always try to extract the pack count and contents from the item name or description (e.g. "#10" = #10 can, "5LB" = 5 lb bag, "1GAL" = 1 gallon)
- quantity: how many of this item were ordered (e.g. 2 cases). Use 1 if not shown or if it's a price sheet (no order quantity). For receipts/invoices, extract the actual quantity purchased.
- price: always the UNIT PRICE for one case/each/lb — NOT the extended total

Portal/catalog format handling:
- Data may come from an ordering portal with extra fields like cart quantity, totals, "Recently Purchased" labels
- IGNORE "$0.00" totals — use the listed unit PRICE only
- If a cart quantity is shown (e.g. "Qty: 3"), capture it in the "quantity" field
- IGNORE "Add to Cart" buttons, "Preorder for:" dates, and "Filters" / "Search" headers
- The pack size (e.g. "6 X 12 CT", "4 X 5 LB", "12 X 3 CT") IS the unit_detail
- The single price shown per item is the price for that pack

Receipt/photo handling:
- Photos may be of physical receipts, invoices, or handwritten price lists
- Receipts may be blurry, angled, crumpled, or partially cut off — extract what you can read
- IGNORE subtotals, tax lines, payment method, change due, and receipt footer info
- Extract BOTH the unit price AND quantity purchased
- If a receipt shows "2 x $5.99 = $11.98", price is $5.99 and quantity is 2
- If only a total is shown with quantity, divide to get unit price
- For Restaurant Depot receipts:
  - Item descriptions are abbreviated (e.g. "BNLS SKNLS CHKN BRST" = Boneless Skinless Chicken Breast)
  - Pack sizes appear in the description (e.g. "4/5LB", "6/HALF GAL", "50CT")
  - Item codes are 5-7 digit numbers — capture them
  - Prices may show member price vs non-member — use the member/charged price
  - Receipt paper is thermal and may be faded — extract what you can read
"""


def build_extraction_prompt(vendor: str) -> str:
    """Build the system prompt with vendor name injected."""
    return EXTRACT_PRICES_SYSTEM.format(vendor=vendor)


def build_user_prompt_text(vendor: str, text: str) -> str:
    """Build user prompt for text-based extraction."""
    return f"Extract all prices from this {vendor} price sheet:\n\n{text}"


def build_user_prompt_image(vendor: str) -> str:
    """Build the text portion of a vision-based extraction."""
    return f"Extract all prices from this {vendor} image. It may be a price sheet, receipt, invoice, or catalog screenshot. Extract every item and its unit price."
