"""Extract structured price data from vendor documents.

Handles PDFs (text extraction via pdfplumber, vision fallback for scans),
screenshots/images (Claude Vision API), and raw text/CSV.

Features:
- Multi-image batching: sends all PDF pages in one API call
- Duplicate detection: SHA-256 hash cache avoids re-extracting same file
- Optional Tesseract pre-pass: free local OCR for clean text images
- Image preprocessing: auto-crop, deskew, enhance (via image_preprocessor)
"""
from __future__ import annotations

import base64
import csv
import io
import json
import logging
import os
import re
import time as _time
from pathlib import Path

import anthropic

from vendor_prices.prompts.extract_prices import (
    build_extraction_prompt,
    build_user_prompt_image,
    build_user_prompt_text,
)

logger = logging.getLogger(__name__)

CLAUDE_MODEL = os.getenv("PRICE_EXTRACT_MODEL", "claude-haiku-4-5-20251001")
MAX_TOKENS = 16384
MAX_RETRIES = 3

# Duplicate detection cache
_CACHE_DIR = Path(".tmp/vp_cache")
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_MAX_AGE = 24 * 3600  # 24 hours


# ── JSON Parsing ──


def _parse_json_response(text: str) -> dict:
    """Robustly parse JSON from Claude's response."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    raise ValueError("No JSON found in response: %s" % text[:200])


# ── Claude API ──


def _call_claude(system: str, messages: list, attempt: int = 0) -> str:
    """Call Claude API with retry on transient errors."""
    client = anthropic.Anthropic()
    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
        )
        return message.content[0].text
    except (anthropic.InternalServerError, anthropic.APIConnectionError) as e:
        if attempt < MAX_RETRIES - 1:
            wait = 2 ** (attempt + 1)
            logger.warning("API error (attempt %d), retrying in %ds: %s",
                           attempt + 1, wait, e)
            _time.sleep(wait)
            return _call_claude(system, messages, attempt + 1)
        raise


# ── Duplicate Detection Cache ──


def _check_cache(file_hash: str) -> dict | None:
    """Check if we already extracted this exact file recently."""
    cache_path = _CACHE_DIR / ("%s.json" % file_hash)
    if not cache_path.exists():
        return None

    # Check age
    age = _time.time() - cache_path.stat().st_mtime
    if age > _CACHE_MAX_AGE:
        cache_path.unlink(missing_ok=True)
        return None

    try:
        data = json.loads(cache_path.read_text())
        logger.info("Cache hit for hash %s (age: %dm)", file_hash[:12],
                     int(age / 60))
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(file_hash: str, result: dict) -> None:
    """Save extraction result to cache."""
    try:
        cache_path = _CACHE_DIR / ("%s.json" % file_hash)
        cache_path.write_text(json.dumps(result, default=str))
    except OSError as e:
        logger.debug("Failed to write cache: %s", e)


# ── Optional Tesseract OCR ──


def _try_tesseract(image_bytes: bytes) -> str | None:
    """Try local OCR with Tesseract. Returns text if successful, None otherwise.

    Only used as a pre-pass — if Tesseract gets enough readable text,
    we can send it as text instead of an image (much cheaper).
    """
    try:
        import pytesseract
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img)

        # Quality check: need at least 3 lines containing numbers
        # (prices should have numbers)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        lines_with_numbers = [
            l for l in lines if any(c.isdigit() for c in l)
        ]

        if len(lines_with_numbers) >= 3 and len(text.strip()) > 50:
            logger.info("Tesseract extracted %d lines (%d with numbers)",
                        len(lines), len(lines_with_numbers))
            return text

        logger.debug("Tesseract output insufficient (%d lines, %d with numbers)",
                      len(lines), len(lines_with_numbers))
    except ImportError:
        pass  # pytesseract not installed — skip silently
    except Exception as e:
        logger.debug("Tesseract failed: %s", e)

    return None


# ── Extraction Methods ──


def extract_from_text(text: str, vendor: str) -> dict:
    """Extract prices from raw text using Claude."""
    system = build_extraction_prompt(vendor)
    user = build_user_prompt_text(vendor, text)
    resp = _call_claude(system, [{"role": "user", "content": user}])
    return _parse_json_response(resp)


def extract_from_image(image_bytes: bytes, media_type: str,
                       vendor: str) -> dict:
    """Extract prices from a single image using Claude Vision API."""
    system = build_extraction_prompt(vendor)
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    resp = _call_claude(system, [{
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64,
                },
            },
            {"type": "text", "text": build_user_prompt_image(vendor)},
        ],
    }])
    return _parse_json_response(resp)


def extract_from_images_batch(image_list: list[tuple[bytes, str]],
                              vendor: str) -> dict:
    """Extract prices from multiple images in a single API call.

    Sends all images in one message — Claude sees them as consecutive
    pages of the same document.  Saves overhead vs N separate calls.

    Args:
        image_list: List of (image_bytes, media_type) tuples
        vendor: Vendor name for extraction prompt

    Returns:
        Combined extraction result with all items merged
    """
    if len(image_list) == 1:
        return extract_from_image(image_list[0][0], image_list[0][1], vendor)

    system = build_extraction_prompt(vendor)

    content_blocks = []
    for i, (img_bytes, media_type) in enumerate(image_list):
        b64 = base64.standard_b64encode(img_bytes).decode("utf-8")
        content_blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": b64,
            },
        })

    prompt_text = (
        "Extract all prices from these %d %s pages. "
        "They are consecutive pages of the same document. "
        "Combine all items into a single list. "
        "Extract every item and its unit price."
    ) % (len(image_list), vendor)
    content_blocks.append({"type": "text", "text": prompt_text})

    logger.info("Batch Vision API: %d images in one call", len(image_list))

    resp = _call_claude(system, [{
        "role": "user",
        "content": content_blocks,
    }])
    return _parse_json_response(resp)


def extract_from_pdf(pdf_path: str, vendor: str) -> dict:
    """Extract prices from a PDF.

    Tries text extraction first (free). Falls back to Vision API
    with multi-image batching if text extraction fails.
    """
    import pdfplumber

    # Phase 1: text extraction
    all_text = []
    pdf_images = []
    with pdfplumber.open(pdf_path) as pdf:
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
        result = extract_from_text(combined_text, vendor)
        items = result.get("items", [])
        if len(items) >= 3:
            return result
        logger.info("Text extraction got only %d items, falling back to Vision",
                    len(items))

    # Phase 2: Vision fallback — batch all pages into one API call
    from vendor_prices.tools.image_preprocessor import preprocess_receipt

    processed_pages = []
    for i, img_bytes in enumerate(pdf_images):
        logger.info("  Preprocessing page %d/%d", i + 1, len(pdf_images))
        processed_bytes, media_type = preprocess_receipt(img_bytes)
        processed_pages.append((processed_bytes, media_type))

    # Claude supports up to 20 images per message; batch if within limit
    if len(processed_pages) <= 20:
        result = extract_from_images_batch(processed_pages, vendor)
        result.setdefault("metadata", {})["total_items"] = len(
            result.get("items", []))
        return result

    # More than 20 pages: split into batches of 20
    all_items = []
    metadata = {}
    for batch_start in range(0, len(processed_pages), 20):
        batch = processed_pages[batch_start:batch_start + 20]
        logger.info("  Vision API batch: pages %d-%d",
                    batch_start + 1, batch_start + len(batch))
        batch_result = extract_from_images_batch(batch, vendor)
        all_items.extend(batch_result.get("items", []))
        if not metadata and batch_result.get("metadata"):
            metadata = batch_result["metadata"]

    metadata["total_items"] = len(all_items)
    return {"items": all_items, "metadata": metadata}


def extract_from_csv(csv_text: str, vendor: str) -> dict:
    """Extract prices from CSV/tab-delimited text."""
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)

    if len(rows) < 2:
        return extract_from_text(csv_text, vendor)

    header = [c.lower().strip() for c in rows[0]]
    has_price_col = any("price" in h or "cost" in h for h in header)
    has_item_col = any(
        "item" in h or "product" in h or "description" in h for h in header
    )

    if has_price_col and has_item_col:
        pass  # structured but still send to Claude for clean extraction

    return extract_from_text(csv_text, vendor)


# ── File Type Detection ──


def detect_file_type(filename: str) -> str:
    """Detect file type from extension."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic"):
        return "image"
    if ext in (".csv", ".tsv", ".xlsx"):
        return "csv"
    return "text"


# ── Main Entry Point ──


def extract_prices(file_path: str, vendor: str,
                   manual_crop: tuple = None) -> dict:
    """Main entry point: extract prices from any file format.

    Features:
    - Duplicate detection via SHA-256 hash (skips re-extraction)
    - Optional Tesseract pre-pass for images (free local OCR)
    - Image preprocessing (auto-crop, deskew, enhance, resize)
    - Multi-image batching for PDFs (all pages in one API call)

    Args:
        file_path: Path to the file to extract from
        vendor: Vendor name
        manual_crop: Optional (x1, y1, x2, y2) for manual image crop

    Returns:
        dict with 'items' list and 'metadata' dict
    """
    file_type = detect_file_type(file_path)

    # Duplicate detection: hash file and check cache
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    from vendor_prices.tools.image_preprocessor import compute_file_hash
    file_hash = compute_file_hash(file_bytes)

    # Only use cache if no manual crop override
    if not manual_crop:
        cached = _check_cache(file_hash)
        if cached:
            cached["_cached"] = True
            return cached

    if file_type == "pdf":
        result = extract_from_pdf(file_path, vendor)
        _save_cache(file_hash, result)
        return result

    if file_type == "image":
        from vendor_prices.tools.image_preprocessor import preprocess_receipt

        ext = Path(file_path).suffix.lower()

        # HEIC (iPhone photos) — convert via PIL first
        if ext == ".heic":
            from PIL import Image
            img = Image.open(io.BytesIO(file_bytes))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=90)
            raw_bytes = buf.getvalue()
        else:
            raw_bytes = file_bytes

        # Try Tesseract first (free) — only for non-manual-crop scenarios
        if not manual_crop:
            ocr_text = _try_tesseract(raw_bytes)
            if ocr_text:
                logger.info("Using Tesseract OCR output instead of Vision API")
                result = extract_from_text(ocr_text, vendor)
                items = result.get("items", [])
                if len(items) >= 3:
                    result["_method"] = "tesseract"
                    _save_cache(file_hash, result)
                    return result
                logger.info("Tesseract extraction got only %d items, "
                            "falling back to Vision", len(items))

        # Preprocess and extract via Vision API
        processed_bytes, media_type = preprocess_receipt(
            raw_bytes, manual_crop=manual_crop)
        result = extract_from_image(processed_bytes, media_type, vendor)
        if not manual_crop:
            _save_cache(file_hash, result)
        return result

    # Text or CSV
    text = file_bytes.decode("utf-8", errors="replace")
    if file_type == "csv":
        result = extract_from_csv(text, vendor)
    else:
        result = extract_from_text(text, vendor)
    _save_cache(file_hash, result)
    return result
