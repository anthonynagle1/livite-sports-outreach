"""Extract structured invoice data from uploaded files (PDFs, photos).

Wraps the existing vendor_prices extraction pipeline but adds
invoice-specific header extraction (invoice number, date, due date, total)
and optional item normalization against the Items Master.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re

import anthropic

logger = logging.getLogger(__name__)

CLAUDE_MODEL = os.getenv("PRICE_EXTRACT_MODEL", "claude-haiku-4-5-20251001")
MAX_TOKENS = 16384

INVOICE_SYSTEM_PROMPT = """You extract structured data from restaurant vendor invoices.

The vendor is: {vendor}

Extract the invoice header AND every line item. Respond with JSON only:
{{
  "invoice_number": "INV-12345 or null",
  "invoice_date": "YYYY-MM-DD or null",
  "due_date": "YYYY-MM-DD or null",
  "vendor_total": 1847.50,
  "items": [
    {{
      "item_name": "Full product description as shown",
      "item_code": "Vendor SKU/product code or null",
      "quantity": 2,
      "unit": "case",
      "unit_price": 29.50,
      "extended_price": 59.00,
      "category": "protein"
    }}
  ]
}}

Rules:
- Extract EVERY line item with a price — do not skip rows
- invoice_number: the invoice or receipt number printed on the document
- invoice_date: the date of the invoice (convert to YYYY-MM-DD)
- due_date: payment due date if shown, otherwise null
- vendor_total: the document's stated total (bottom line total)
- unit_price: price for ONE unit (case, lb, each, etc.) — NOT the extended total
- extended_price: quantity × unit_price (the line total). Negative for credits/returns.
- If only extended_price is shown, divide by quantity to get unit_price
- category: one of protein, produce, dairy, dry_goods, canned_goods, frozen, beverages, oils_condiments, bakery, paper_supplies, cleaning, equipment, other
- unit: normalize to case, lb, each, gallon, bag, box, dozen, pack
- INCLUDE credits and returns as negative-quantity items (quantity=-1 means credit/return)
- IGNORE subtotals, tax, delivery charges, and footer totals only
- For Restaurant Depot receipts: descriptions are abbreviated, item codes are 5-7 digits
- For Sysco invoices: look for "Invoice No" in the header area
- Photos may be blurry or angled — extract what you can read
"""


def _parse_json(text: str) -> dict:
    """Robustly parse JSON from Claude's response."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    raise ValueError("No JSON found in response")


def _call_claude(system: str, messages: list) -> str:
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=messages,
    )
    return message.content[0].text


def _extract_from_text(text: str, vendor: str) -> dict:
    system = INVOICE_SYSTEM_PROMPT.format(vendor=vendor)
    resp = _call_claude(system, [{"role": "user", "content":
        f"Extract invoice header and all line items from this {vendor} invoice:\n\n{text}"}])
    return _parse_json(resp)


def _extract_from_images(image_list: list[tuple[bytes, str]], vendor: str) -> dict:
    system = INVOICE_SYSTEM_PROMPT.format(vendor=vendor)

    content_blocks = []
    for img_bytes, media_type in image_list:
        b64 = base64.standard_b64encode(img_bytes).decode("utf-8")
        content_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        })

    page_word = "page" if len(image_list) == 1 else f"{len(image_list)} pages"
    content_blocks.append({"type": "text", "text":
        f"Extract the invoice header and all line items from this {vendor} invoice ({page_word})."})

    resp = _call_claude(system, [{"role": "user", "content": content_blocks}])
    return _parse_json(resp)


def _dedup_items(items: list[dict]) -> list[dict]:
    """Merge duplicate line items (same name + unit) by summing quantities.

    Credits (negative quantity) are kept separate and not merged into positives.
    Items with the same name+unit and same-sign quantity are merged.
    """
    groups: dict[tuple, list[dict]] = {}
    order: list[tuple] = []

    for item in items:
        name = (item.get("item_name") or "").strip().upper()
        unit = (item.get("unit") or "").strip().lower()
        qty = float(item.get("quantity", 1) or 1)
        # Separate positive from negative so credits don't cancel purchases
        sign = "neg" if qty < 0 else "pos"
        key = (name, unit, sign)

        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    merged = []
    for key in order:
        group = groups[key]
        if len(group) == 1:
            merged.append(group[0])
            continue

        # Merge: sum quantity and extended_price, keep unit_price from first
        base = dict(group[0])
        total_qty = sum(float(g.get("quantity", 1) or 1) for g in group)
        total_ext = sum(float(g.get("extended_price", 0) or 0) for g in group)
        base["quantity"] = total_qty
        base["extended_price"] = round(total_ext, 2)
        # Recalculate unit_price if needed
        if total_qty != 0:
            base["unit_price"] = round(abs(total_ext / total_qty), 2)
        merged.append(base)

    return merged


def extract_invoice(file_path: str, vendor: str) -> dict:
    """Main entry point: extract invoice data from any supported file.

    Args:
        file_path: Absolute path to PDF or image file
        vendor: Vendor display name (e.g. "Sysco")

    Returns:
        dict with keys: invoice_number, invoice_date, due_date, vendor_total,
        items (list of line item dicts). Items may include master_item_id and
        master_item_name if normalization succeeds.
    """
    from pathlib import Path
    ext = Path(file_path).suffix.lower()

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    # PDF handling
    if ext == ".pdf":
        result = _extract_from_pdf(file_path, file_bytes, vendor)
    elif ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        media_types = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif",
        }
        result = _extract_from_images(
            [(file_bytes, media_types.get(ext, "image/jpeg"))], vendor)
    elif ext == ".heic":
        from PIL import Image
        img = Image.open(io.BytesIO(file_bytes))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=90)
        result = _extract_from_images([(buf.getvalue(), "image/jpeg")], vendor)
    else:
        text = file_bytes.decode("utf-8", errors="replace")
        result = _extract_from_text(text, vendor)

    # Post-process: ensure extended_price is computed
    for item in result.get("items", []):
        qty = float(item.get("quantity", 1) or 1)
        up = float(item.get("unit_price", 0) or 0)
        ep = float(item.get("extended_price", 0) or 0)
        if ep == 0 and up > 0:
            item["extended_price"] = round(qty * up, 2)
        elif up == 0 and ep != 0 and qty != 0:
            item["unit_price"] = round(abs(ep / qty), 2)

    # Deduplicate: merge same-name+unit items, keep credits separate
    result["items"] = _dedup_items(result.get("items", []))

    # Try item normalization against Items Master
    result["items"] = _try_normalize(result.get("items", []), vendor)

    # Try price alerts
    result["items"] = _try_price_alerts(result.get("items", []), vendor)

    logger.info("Extracted invoice: %s items, total=$%.2f",
                len(result.get("items", [])), float(result.get("vendor_total", 0) or 0))
    return result


def _extract_from_pdf(file_path: str, file_bytes: bytes, vendor: str) -> dict:
    """Extract from PDF: try text first, fall back to Vision."""
    import pdfplumber

    all_text = []
    pdf_images = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            all_text.append(text)
            img = page.to_image(resolution=200)
            buf = io.BytesIO()
            img.original.save(buf, format="PNG")
            pdf_images.append(buf.getvalue())

    combined_text = "\n".join(all_text)

    # If we got reasonable text, use text extraction
    if len(combined_text.strip()) > 100:
        result = _extract_from_text(combined_text, vendor)
        if len(result.get("items", [])) >= 3:
            return result
        logger.info("Text extraction got only %d items, falling back to Vision",
                    len(result.get("items", [])))

    # Vision fallback
    pages = [(img_bytes, "image/png") for img_bytes in pdf_images[:20]]
    return _extract_from_images(pages, vendor)


def _try_normalize(items: list[dict], vendor: str) -> list[dict]:
    """Try to match extracted items against Items Master. Non-fatal on failure."""
    try:
        from vendor_prices.tools.notion_sync import get_items_master
        master_items = get_items_master()
        if not master_items:
            return items

        from vendor_prices.tools.item_normalizer import ItemNormalizer
        normalizer = ItemNormalizer(master_items)
        normalized = normalizer.normalize(items, vendor)
        return normalized
    except Exception as e:
        logger.info("Item normalization skipped: %s", e)
        return items


def _try_price_alerts(items: list[dict], vendor: str) -> list[dict]:
    """Try to add price alerts to items. Non-fatal on failure."""
    try:
        from invoices.tools.price_alerts import check_price_alerts
        return check_price_alerts(items, vendor)
    except Exception as e:
        logger.info("Price alerts skipped: %s", e)
        return items
