"""Vendor Prices Blueprint routes."""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import date
from pathlib import Path

import re

from flask import redirect, render_template_string, request, session, url_for
from werkzeug.utils import secure_filename

from vendor_prices import bp
from vendor_prices.tools import notion_sync, price_extractor
from vendor_prices.tools.background_writer import get_progress, start_batch_write
from vendor_prices.tools.item_normalizer import ItemNormalizer
from vendor_prices.tools.unit_normalizer import enrich_items_with_units

logger = logging.getLogger(__name__)


@bp.app_template_filter("from_json_or_empty")
def from_json_or_empty(value):
    """Parse JSON string to list, return empty list on failure."""
    if not value:
        return []
    try:
        result = json.loads(value)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


API_SERVICE_SECRET = os.getenv("API_SERVICE_SECRET", "")


@bp.before_request
def require_auth():
    """Redirect to login if not authenticated.

    Exempts the telegram-upload API endpoint when a valid API key is provided.
    """
    if request.endpoint == "vendor_prices.api_telegram_upload":
        api_key = request.headers.get("X-API-Key", "")
        if API_SERVICE_SECRET and api_key == API_SERVICE_SECRET:
            return None
        return {"error": "Unauthorized"}, 401
    if not session.get('authenticated'):
        return redirect(url_for('login'))


@bp.context_processor
def inject_role():
    """Make user role available in all vendor-prices templates."""
    return {'role': session.get('role', 'manager')}

UPLOAD_DIR = Path(".tmp/vp_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
PENDING_DIR = Path(".tmp/vp_pending")
PENDING_DIR.mkdir(parents=True, exist_ok=True)

ITEMS_DB_ID = os.getenv("NOTION_ITEMS_DB_ID", "")
PRICES_DB_ID = os.getenv("NOTION_PRICES_DB_ID", "")
UPLOADS_DB_ID = os.getenv("NOTION_UPLOADS_DB_ID", "")

VENDORS = {
    "sysco": "Sysco",
    "baldor": "Baldor",
    "freshpoint": "FreshPoint",
    "restaurant_depot": "Restaurant Depot",
}

CATEGORY_MAP = {
    "protein": "Protein",
    "produce": "Produce",
    "dairy": "Dairy",
    "dry_goods": "Dry Goods",
    "dry goods": "Dry Goods",
    "canned_goods": "Canned Goods",
    "canned goods": "Canned Goods",
    "frozen": "Frozen",
    "beverages": "Beverages",
    "oils_condiments": "Oils & Condiments",
    "oils & condiments": "Oils & Condiments",
    "bakery": "Bakery",
    "supplies": "Paper & Supplies",
    "paper_supplies": "Paper & Supplies",
    "paper & supplies": "Paper & Supplies",
    "cleaning": "Cleaning",
    "equipment": "Equipment",
    "other": "Other",
}


# ── Routes ──


@bp.route("/")
def index():
    pending_invoices = 0
    try:
        from tools.gmail_service.vendor_invoice_poller import poller as vip
        pending_invoices = vip.pending_count
    except Exception as e:
        logger.debug("vendor_invoice_poller unavailable: %s", e)
    return render_template_string(INDEX_HTML, vendors=VENDORS,
                                  pending_invoices=pending_invoices)


@bp.route("/upload", methods=["GET"])
def upload_page():

    return render_template_string(UPLOAD_HTML, vendors=VENDORS)


@bp.route("/api/upload", methods=["POST"])
def api_upload():
    """Extract prices from uploaded files → return for review."""

    vendor_key = request.form.get("vendor", "")
    vendor = VENDORS.get(vendor_key, "")
    if not vendor:
        return {"error": f"Unknown vendor: {vendor_key}"}, 400

    upload_type = request.form.get("upload_type", "Purchase")
    if upload_type not in ("Purchase", "Price Update"):
        upload_type = "Purchase"

    files = request.files.getlist("files")
    if not files or all(f.filename == "" for f in files):
        return {"error": "No files uploaded"}, 400

    all_items = []
    filenames = []
    file_paths_saved = []
    master_items_list = []
    failed_files = []
    cached_count = 0
    for f in files:
        if not f.filename:
            continue

        week = notion_sync.get_current_week()
        save_dir = UPLOAD_DIR / week / vendor_key
        save_dir.mkdir(parents=True, exist_ok=True)
        safe_name = secure_filename(f.filename) or f"upload_{uuid.uuid4().hex}"
        file_path = save_dir / safe_name
        f.save(str(file_path))

        extracted = _extract_and_normalize(vendor, f.filename, str(file_path))
        if extracted.get("error"):
            failed_files.append({"file": f.filename, "error": extracted["error"]})
            continue  # skip this file, keep going with others
        all_items.extend(extracted["items"])
        master_items_list = extracted.get("master_items", [])
        filenames.append(f.filename)
        file_paths_saved.append(str(file_path))
        if extracted.get("_cached"):
            cached_count += 1

    # If ALL files failed, return error
    if not all_items and failed_files:
        errors = "; ".join(f"{ff['file']}: {ff['error']}" for ff in failed_files)
        return {"error": f"Extraction failed: {errors}"}, 500

    # Deduplicate items by name — keep highest-confidence match for each
    all_items, dupes_merged = _deduplicate_items(all_items)

    # Save pending batch for review (include file_paths for crop re-extract)
    batch_id = str(uuid.uuid4())[:8]
    pending = {
        "batch_id": batch_id,
        "vendor": vendor,
        "vendor_key": vendor_key,
        "upload_type": upload_type,
        "filenames": filenames,
        "file_paths": file_paths_saved,
        "items": all_items,
    }
    (PENDING_DIR / f"{batch_id}.json").write_text(json.dumps(pending, default=str))

    result = {"batch_id": batch_id, "vendor": vendor, "items": all_items,
              "master_items": master_items_list, "duplicates_merged": dupes_merged,
              "upload_type": upload_type}
    if failed_files:
        result["warnings"] = [ff["file"] + " failed: " + ff["error"]
                              for ff in failed_files]
    if cached_count:
        result["cached_files"] = cached_count
    return result


@bp.route("/api/paste", methods=["POST"])
def api_paste():
    """Extract prices from pasted text → return for review."""

    vendor_key = request.form.get("vendor", "")
    vendor = VENDORS.get(vendor_key, "")
    if not vendor:
        return {"error": f"Unknown vendor: {vendor_key}"}, 400

    upload_type = request.form.get("upload_type", "Purchase")
    if upload_type not in ("Purchase", "Price Update"):
        upload_type = "Purchase"

    text = request.form.get("text", "").strip()
    if not text:
        return {"error": "No text provided"}, 400

    week = notion_sync.get_current_week()
    save_dir = UPLOAD_DIR / week / vendor_key
    save_dir.mkdir(parents=True, exist_ok=True)
    filename = f"paste_{date.today().isoformat()}_{vendor_key}.txt"
    file_path = save_dir / filename
    file_path.write_text(text)

    extracted = _extract_and_normalize(vendor, filename, str(file_path))
    if extracted.get("error"):
        return {"error": extracted["error"]}, 500

    batch_id = str(uuid.uuid4())[:8]
    pending = {
        "batch_id": batch_id,
        "vendor": vendor,
        "vendor_key": vendor_key,
        "upload_type": upload_type,
        "filenames": [filename],
        "items": extracted["items"],
    }
    (PENDING_DIR / f"{batch_id}.json").write_text(json.dumps(pending, default=str))

    return {"batch_id": batch_id, "vendor": vendor, "items": extracted["items"],
            "master_items": extracted.get("master_items", []), "upload_type": upload_type}


@bp.route("/api/confirm", methods=["POST"])
def api_confirm():
    """Confirm reviewed items and start background write to Notion.

    Returns immediately with batch_id for progress polling.
    """

    data = request.get_json(silent=True) or {}
    batch_id = data.get("batch_id", "")
    approved_items = data.get("items", [])

    if not re.match(r'^[a-zA-Z0-9_-]+$', batch_id):
        return {"error": "Invalid batch_id"}, 400
    # Load pending batch
    pending_path = PENDING_DIR / f"{batch_id}.json"
    if not pending_path.exists():
        return {"error": "Batch not found or expired"}, 404

    try:
        pending = json.loads(pending_path.read_text())
        vendor = pending["vendor"]
        filenames = pending["filenames"]
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Corrupt pending batch %s: %s", batch_id, e)
        return {"error": "Batch data is corrupt or incomplete"}, 500
    upload_type = data.get("upload_type") or pending.get("upload_type", "Purchase")
    if upload_type not in ("Purchase", "Price Update"):
        upload_type = "Purchase"
    source_file = ", ".join(filenames)

    # Log the upload
    file_type = "Text"
    if filenames:
        ft = price_extractor.detect_file_type(filenames[0])
        file_type = {"pdf": "PDF", "image": "Screenshot", "csv": "CSV", "text": "Text"}.get(ft, "Text")

    upload_id = notion_sync.log_upload(
        UPLOADS_DB_ID, vendor, source_file, file_type, upload_type=upload_type,
    )

    # Re-enrich units
    approved_items = enrich_items_with_units(approved_items)

    # Start background write — returns immediately
    start_batch_write(
        batch_id=batch_id,
        approved_items=approved_items,
        vendor=vendor,
        upload_type=upload_type,
        source_file=source_file,
        upload_id=upload_id,
        items_db_id=ITEMS_DB_ID,
        prices_db_id=PRICES_DB_ID,
        uploads_db_id=UPLOADS_DB_ID,
        category_map=CATEGORY_MAP,
    )

    # Clean up pending file (batch data is now in the background thread)
    pending_path.unlink(missing_ok=True)

    return {
        "status": "processing",
        "batch_id": batch_id,
        "total": len([it for it in approved_items if not it.get("excluded")]),
    }


@bp.route("/api/confirm/status/<batch_id>")
def api_confirm_status(batch_id):
    """Poll background write progress."""
    progress = get_progress(batch_id)
    if not progress:
        return {"error": "Batch not found"}, 404
    return progress


@bp.route("/api/confirm/retry/<batch_id>", methods=["POST"])
def api_confirm_retry(batch_id):
    """Retry failed items from a completed batch.

    Re-reads the progress file and re-runs only the items that failed.
    Currently returns a message; full retry requires storing the original
    items in the progress file (future enhancement).
    """
    progress = get_progress(batch_id)
    if not progress:
        return {"error": "Batch not found"}, 404
    if progress.get("status") == "running":
        return {"error": "Batch still running"}, 409
    if progress.get("failed", 0) == 0:
        return {"status": "ok", "message": "No failed items to retry"}
    return {"status": "info", "message": f"{progress['failed']} items failed — re-upload the invoice to retry"}


@bp.route("/api/preprocess", methods=["POST"])
def api_preprocess():
    """Preview preprocessing result for an image file.

    Returns blur score, processed image preview, and size info.
    Used by the frontend to show what the AI will see before extraction.
    """
    f = request.files.get("file")
    if not f or not f.filename:
        return {"error": "No file"}, 400

    raw_bytes = f.read()
    ft = price_extractor.detect_file_type(f.filename)
    if ft != "image":
        return {"error": "Only image files can be previewed"}, 400

    from vendor_prices.tools.image_preprocessor import preprocess_with_metadata
    try:
        meta = preprocess_with_metadata(raw_bytes)
        return meta
    except Exception as e:
        logger.error("Preprocess failed: %s", e)
        return {"error": str(e)}, 500


@bp.route("/api/crop-extract", methods=["POST"])
def api_crop_extract():
    """Re-extract prices using manual crop coordinates.

    Expects JSON with batch_id + crop coordinates relative to original
    image dimensions.
    """
    data = request.get_json(silent=True) or {}
    batch_id = data.get("batch_id", "")
    crop = data.get("crop")  # {x, y, w, h}

    if not re.match(r'^[a-zA-Z0-9_-]+$', batch_id):
        return {"error": "Invalid batch_id"}, 400
    pending_path = PENDING_DIR / ("%s.json" % batch_id)
    if not pending_path.exists():
        return {"error": "Batch not found"}, 404

    try:
        pending = json.loads(pending_path.read_text())
        vendor = pending["vendor"]
        vendor_key = pending["vendor_key"]
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Corrupt pending batch %s: %s", batch_id, e)
        return {"error": "Batch data is corrupt or incomplete"}, 500
    file_paths = pending.get("file_paths", [])

    if not file_paths:
        return {"error": "Original files not available for re-crop"}, 400

    # Convert crop dict to tuple
    manual_crop = None
    if crop:
        manual_crop = (
            int(crop.get("x", 0)),
            int(crop.get("y", 0)),
            int(crop.get("x", 0)) + int(crop.get("w", 0)),
            int(crop.get("y", 0)) + int(crop.get("h", 0)),
        )

    all_items = []
    master_items_list = []
    for fp in file_paths:
        if not os.path.exists(fp):
            continue
        extracted = _extract_and_normalize(
            vendor, os.path.basename(fp), fp, manual_crop=manual_crop)
        if not extracted.get("error"):
            all_items.extend(extracted["items"])
            master_items_list = extracted.get("master_items", [])

    if not all_items:
        return {"error": "Re-extraction failed"}, 500

    all_items, dupes_merged = _deduplicate_items(all_items)

    # Update pending batch
    pending["items"] = all_items
    pending_path.write_text(json.dumps(pending, default=str))

    return {
        "batch_id": batch_id,
        "vendor": vendor,
        "items": all_items,
        "master_items": master_items_list,
        "duplicates_merged": dupes_merged,
        "upload_type": pending.get("upload_type", "Purchase"),
    }


def _extract_and_normalize(vendor: str, filename: str, file_path: str,
                           manual_crop: tuple = None) -> dict:
    """Extract prices and normalize — returns items for review, does NOT write to Notion."""
    try:
        logger.info("Extracting prices from %s (%s)", filename, vendor)
        extraction = price_extractor.extract_prices(
            file_path, vendor, manual_crop=manual_crop)
        items = extraction.get("items", [])
        items = enrich_items_with_units(items)
        logger.info("  Extracted %d items", len(items))

        cached = extraction.get("_cached", False)

        master_items = notion_sync.get_all_items(ITEMS_DB_ID)
        normalizer = ItemNormalizer(master_items)
        normalized = normalizer.normalize(items, vendor)

        # Fetch master item names for the review UI
        master_map = {m["id"]: m["name"] for m in master_items}
        for item in normalized:
            mid = item.get("master_item_id")
            if mid and mid in master_map:
                item["master_item_name"] = master_map[mid]
            item["source_file"] = filename

        # Price change detection: compare vs latest prices in Notion
        _detect_price_changes(normalized, master_map)

        master_list = [{"id": m["id"], "name": m["name"]} for m in master_items]
        result = {"items": normalized, "master_items": master_list}
        if cached:
            result["_cached"] = True
        return result

    except Exception as e:
        logger.error("Error extracting %s: %s", filename, e, exc_info=True)
        return {"error": str(e)}


def _detect_price_changes(items: list[dict], master_map: dict) -> None:
    """Compare extracted prices against latest Notion prices.

    Adds 'price_change' dict to items that have changed:
    {direction: "up"/"down", old_price, pct_change}
    """
    if not PRICES_DB_ID:
        return

    try:
        entries = notion_sync.get_price_entries(PRICES_DB_ID)
    except Exception as e:
        logger.warning("_detect_price_changes: failed to load price entries: %s", e)
        return

    # Build lookup: master_item_id -> latest price
    latest_prices = {}
    latest_dates = {}
    for e in entries:
        item_id = e.get("item_relation_id", "")
        if not item_id:
            continue
        entry_date = e.get("date", "")
        if item_id not in latest_dates or entry_date > latest_dates[item_id]:
            latest_prices[item_id] = e.get("price", 0) or 0
            latest_dates[item_id] = entry_date

    for item in items:
        mid = item.get("master_item_id")
        if not mid or mid not in latest_prices:
            continue

        old_price = latest_prices[mid]
        new_price = item.get("price", 0) or 0

        if old_price <= 0 or new_price <= 0:
            continue

        # Only flag if price actually changed (> 1% difference)
        pct_change = ((new_price - old_price) / old_price) * 100
        if abs(pct_change) < 1:
            continue

        direction = "up" if new_price > old_price else "down"
        item["price_change"] = {
            "direction": direction,
            "old_price": round(old_price, 2),
            "pct_change": round(pct_change, 1),
        }


@bp.route("/compare")
@bp.route("/compare/<week>")
def compare(week=None):
    """Side-by-side price comparison across vendors."""


    weeks = notion_sync.get_available_weeks(PRICES_DB_ID)
    if not week and weeks:
        week = weeks[0]

    entries = notion_sync.get_price_entries(PRICES_DB_ID, week=week or "") if week else []
    items = notion_sync.get_all_items(ITEMS_DB_ID)
    item_map = {i["id"]: i for i in items}

    # Build comparison table: item -> vendor -> price data
    # If duplicates exist for the same (item, vendor, week), keep the most recent by date
    comparison = {}
    seen_dates = {}  # (key, vendor) -> date string
    for entry in entries:
        item_id = entry["item_relation_id"]
        item = item_map.get(item_id, {})
        item_name = item.get("name", entry.get("vendor_item_name", "Unknown"))
        category = item.get("category", "Other")
        vendor = entry["vendor"]
        entry_date = entry.get("date", "")

        key = item_id or entry["vendor_item_name"]
        if key not in comparison:
            comparison[key] = {
                "name": item_name,
                "category": category,
                "vendors": {},
            }

        pair = (key, vendor)
        prev_date = seen_dates.get(pair, "")
        if entry_date >= prev_date:
            comparison[key]["vendors"][vendor] = {
                "price": entry["price"],
                "price_per_unit": entry["price_per_unit"],
                "unit": entry["unit"],
            }
            seen_dates[pair] = entry_date

    # Sort by category then name
    sorted_items = sorted(comparison.values(), key=lambda x: (x["category"], x["name"]))

    vendor_list = list(VENDORS.values())
    return render_template_string(
        COMPARE_HTML,
        items=sorted_items,
        vendors=VENDORS,
        vendor_list=vendor_list,
        weeks=weeks,
        current_week=week,
    )


@bp.route("/compare/all")
def compare_all():
    """View All — latest price for every item across all vendors."""

    all_entries = notion_sync.get_price_entries(PRICES_DB_ID)
    items = notion_sync.get_all_items(ITEMS_DB_ID)
    item_map = {i["id"]: i for i in items}

    # For each (item, vendor), keep only the most recent entry (by week desc)
    latest = {}  # (item_key, vendor) -> entry with best week
    for entry in all_entries:
        item_id = entry["item_relation_id"]
        item = item_map.get(item_id, {})
        item_name = item.get("name", entry.get("vendor_item_name", "Unknown"))
        category = item.get("category", "Other")
        vendor = entry["vendor"]
        week = entry.get("week", "")

        key = item_id or entry["vendor_item_name"]
        pair = (key, vendor)

        if pair not in latest or week > latest[pair]["week"]:
            latest[pair] = {
                "key": key,
                "name": item_name,
                "category": category,
                "vendor": vendor,
                "week": week,
                "price": entry["price"],
                "price_per_unit": entry["price_per_unit"],
                "unit": entry["unit"],
            }

    # Build comparison table
    comparison = {}
    for entry in latest.values():
        k = entry["key"]
        if k not in comparison:
            comparison[k] = {
                "name": entry["name"],
                "category": entry["category"],
                "vendors": {},
            }
        comparison[k]["vendors"][entry["vendor"]] = {
            "price": entry["price"],
            "price_per_unit": entry["price_per_unit"],
            "unit": entry["unit"],
            "week": entry["week"],
        }

    sorted_items = sorted(comparison.values(), key=lambda x: (x["category"], x["name"]))
    vendor_list = list(VENDORS.values())

    return render_template_string(
        COMPARE_ALL_HTML,
        items=sorted_items,
        vendors=VENDORS,
        vendor_list=vendor_list,
        now=date.today().strftime("%B %d, %Y"),
    )


@bp.route("/review")
def review_page():
    """Item mapping review — view and manage all master items and their aliases."""


    items = notion_sync.get_all_items(ITEMS_DB_ID)
    items.sort(key=lambda x: (x["category"], x["name"]))

    return render_template_string(REVIEW_HTML, items=items, vendors=VENDORS)


@bp.route("/api/item/<item_id>", methods=["PATCH"])
def update_item_api(item_id):
    """Update an item's properties."""

    data = request.get_json(silent=True) or {}
    kwargs = {}
    for field in ("name", "category", "unit", "unit_size", "preferred_vendor",
                  "active", "notes", "par_level"):
        if field in data:
            kwargs[field] = data[field]

    try:
        if "aliases" in data:
            notion_sync.update_item_aliases(item_id, data["aliases"])
        if kwargs:
            notion_sync.update_item(item_id, **kwargs)
    except Exception as e:
        logger.error("update_item_api failed for %s: %s", item_id, e)
        return {"error": str(e)}, 500

    return {"status": "ok"}


@bp.route("/api/item/<item_id>/detail")
def api_item_detail(item_id):
    """Get item info + latest prices per vendor with pack breakdown."""

    items = notion_sync.get_all_items(ITEMS_DB_ID)
    item = next((i for i in items if i["id"] == item_id), None)
    if not item:
        return {"error": "Item not found"}, 404

    price_entries = notion_sync.get_item_price_details(PRICES_DB_ID, item_id)

    prices = []
    for e in price_entries:
        total_units = (e.get("pack_qty") or 0) * (e.get("each_size") or 0)
        per_unit = round(e["price"] / total_units, 4) if e["price"] and total_units > 0 else 0
        prices.append({
            "entry_id": e["id"],
            "vendor": e["vendor"],
            "price": e["price"],
            "unit": e["unit"],
            "week": e["week"],
            "date": e["date"],
            "pack_qty": e.get("pack_qty") or 0,
            "each_size": e.get("each_size") or 0,
            "size_unit": e.get("size_unit") or "",
            "total_units": total_units,
            "per_unit_price": per_unit,
            "vendor_item_name": e.get("vendor_item_name", ""),
        })

    return {
        "item": {
            "id": item["id"],
            "name": item["name"],
            "category": item["category"],
            "unit": item["unit"],
            "unit_size": item["unit_size"],
            "preferred_vendor": item["preferred_vendor"],
            "aliases": item["aliases"],
        },
        "prices": prices,
    }


@bp.route("/api/price-entry/<entry_id>/pack", methods=["PATCH"])
def api_update_pack(entry_id):
    """Update pack breakdown fields on a price entry."""

    data = request.get_json(silent=True) or {}
    try:
        pack_qty = int(data.get("pack_qty", 0))
        each_size = float(data.get("each_size", 0))
    except (ValueError, TypeError):
        return {"error": "pack_qty and each_size must be numbers"}, 400
    size_unit = str(data.get("size_unit", ""))

    try:
        notion_sync.update_price_entry_pack(entry_id, pack_qty, each_size, size_unit)
        return {"status": "ok"}
    except Exception as e:
        return {"error": str(e)}, 500


@bp.route("/spending")
def spending_page():
    """Spending dashboard — aggregate spending by vendor and category."""
    return render_template_string(SPENDING_HTML, vendors=VENDORS)


@bp.route("/api/spending/data")
def spending_data_api():
    """Return aggregated spending data as JSON."""
    try:
        data = notion_sync.get_spending_summary(PRICES_DB_ID, ITEMS_DB_ID)
        return data
    except Exception as e:
        logger.error("Spending data error: %s", e)
        return {"error": str(e)}, 500


@bp.route("/trends")
def trends_page():
    """Historical price trends across weeks."""


    all_entries = notion_sync.get_price_entries(PRICES_DB_ID)
    items = notion_sync.get_all_items(ITEMS_DB_ID)
    item_map = {i["id"]: i for i in items}

    # Build trends: item_id -> {name, category, weeks: {week -> {vendor -> price}}}
    trends = {}
    for entry in all_entries:
        item_id = entry["item_relation_id"]
        if not item_id:
            continue
        item = item_map.get(item_id, {})
        if item_id not in trends:
            trends[item_id] = {
                "name": item.get("name", "Unknown"),
                "category": item.get("category", "Other"),
                "weeks": {},
            }
        week = entry["week"]
        if week not in trends[item_id]["weeks"]:
            trends[item_id]["weeks"][week] = {}
        trends[item_id]["weeks"][week][entry["vendor"]] = {
            "price": entry["price"],
            "price_per_unit": entry["price_per_unit"],
        }

    # Sort by category and name
    sorted_trends = sorted(trends.values(), key=lambda x: (x["category"], x["name"]))

    # Get all weeks sorted
    all_weeks = sorted(set(e["week"] for e in all_entries if e["week"]))

    return render_template_string(
        TRENDS_HTML,
        trends=sorted_trends,
        weeks=all_weeks,
        vendors=VENDORS,
        vendor_list=list(VENDORS.values()),
    )


@bp.route("/history")
def history_page():
    """Upload history — view all uploads and their price entries."""

    uploads = notion_sync.get_upload_log(UPLOADS_DB_ID)
    current_week = notion_sync.get_current_week()

    # Summary stats
    this_week_uploads = [u for u in uploads if u["week"] == current_week]
    total_items_extracted = sum(int(u["items_extracted"]) for u in uploads)

    return render_template_string(
        HISTORY_HTML,
        uploads=uploads,
        total_uploads=len(uploads),
        this_week_count=len(this_week_uploads),
        total_items=total_items_extracted,
        current_week=current_week,
        vendors=VENDORS,
    )


@bp.route("/api/upload-entries/<vendor>/<week>")
def api_upload_entries(vendor, week):
    """Get price entries for a specific vendor + week."""

    entries = notion_sync.get_price_entries(PRICES_DB_ID, week=week)
    items = notion_sync.get_all_items(ITEMS_DB_ID)
    item_map = {i["id"]: i for i in items}

    vendor_entries = [e for e in entries if e["vendor"] == vendor]
    result = []
    for e in vendor_entries:
        item = item_map.get(e["item_relation_id"], {})
        result.append({
            "id": e["id"],
            "item_name": item.get("name", e.get("vendor_item_name", "Unknown")),
            "vendor_item_name": e.get("vendor_item_name", ""),
            "price": e["price"],
            "unit": e["unit"],
            "price_per_unit": e["price_per_unit"],
            "category": item.get("category", "Other"),
        })
    result.sort(key=lambda x: (x["category"], x["item_name"]))
    return {"entries": result}


@bp.route("/api/price-entry/<entry_id>", methods=["DELETE"])
def delete_price_entry(entry_id):
    """Delete (archive) a price entry."""

    try:
        ok = notion_sync.delete_page(entry_id)
    except Exception as e:
        logger.error("delete_price_entry failed for %s: %s", entry_id, e)
        return {"error": str(e)}, 500
    if ok:
        return {"status": "ok"}
    return {"error": "Failed to delete"}, 500


@bp.route("/api/pending/<batch_id>")
def api_pending(batch_id):
    """Load a pending batch by ID for the review UI.

    If the batch has no items yet (Telegram upload), returns source='telegram'
    so the UI can show a vendor picker before extraction.
    """
    pending_path = PENDING_DIR / f"{batch_id}.json"
    if not pending_path.exists():
        return {"error": "Batch not found or expired"}, 404
    try:
        pending = json.loads(pending_path.read_text())
        _ = pending["batch_id"]  # validate required key present
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Corrupt pending batch %s: %s", batch_id, e)
        return {"error": "Batch data is corrupt"}, 500

    result = {
        "batch_id": pending["batch_id"],
        "vendor": pending.get("vendor", ""),
        "items": pending.get("items", []),
        "source": pending.get("source", "web"),
    }

    # Only fetch master items if we have extracted items to review
    if result["items"]:
        master_items = notion_sync.get_all_items(ITEMS_DB_ID)
        result["master_items"] = [{"id": m["id"], "name": m["name"]} for m in master_items]

    return result


@bp.route("/api/pending/<batch_id>/extract", methods=["POST"])
def api_pending_extract(batch_id):
    """Run extraction on a pending Telegram upload after user selects vendor."""
    pending_path = PENDING_DIR / f"{batch_id}.json"
    if not pending_path.exists():
        return {"error": "Batch not found or expired"}, 404

    vendor_key = request.form.get("vendor", "")
    vendor = VENDORS.get(vendor_key, "")
    if not vendor:
        return {"error": f"Unknown vendor: {vendor_key}"}, 400

    try:
        pending = json.loads(pending_path.read_text())
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Corrupt pending batch %s: %s", batch_id, e)
        return {"error": "Batch data is corrupt"}, 500
    file_path = pending.get("file_path", "")
    if not file_path or not Path(file_path).exists():
        return {"error": "Image file not found — it may have expired"}, 404

    filenames = pending.get("filenames") or []
    filename = filenames[0] if filenames else "telegram.jpg"
    extracted = _extract_and_normalize(vendor, filename, file_path)
    if extracted.get("error"):
        return {"error": extracted["error"]}, 500

    # Update pending batch with extraction results
    pending["vendor"] = vendor
    pending["vendor_key"] = vendor_key
    pending["items"] = extracted["items"]
    pending_path.write_text(json.dumps(pending, default=str))

    master_items = extracted.get("master_items", [])
    return {
        "batch_id": batch_id,
        "vendor": vendor,
        "items": extracted["items"],
        "master_items": master_items,
    }


@bp.route("/api/telegram-upload", methods=["POST"])
def api_telegram_upload():
    """Accept a photo from the Telegram invoice bot and store it for review.

    Auth: X-API-Key header (checked in require_auth).
    Expects multipart/form-data with 'file' (image).
    Stores the file and returns a review URL. Extraction happens on the
    web dashboard when the user selects a vendor.
    """
    f = request.files.get("file")
    if not f or not f.filename:
        return {"error": "No file uploaded"}, 400

    week = notion_sync.get_current_week()
    save_dir = UPLOAD_DIR / week / "telegram"
    save_dir.mkdir(parents=True, exist_ok=True)

    batch_id = str(uuid.uuid4())[:8]
    filename = f"telegram_{date.today().isoformat()}_{batch_id}.jpg"
    file_path = save_dir / filename
    f.save(str(file_path))

    # Store as pending — no extraction yet, user picks vendor on dashboard
    pending = {
        "batch_id": batch_id,
        "vendor": "",
        "vendor_key": "",
        "filenames": [filename],
        "file_path": str(file_path),
        "source": "telegram",
        "items": [],
    }
    (PENDING_DIR / f"{batch_id}.json").write_text(json.dumps(pending, default=str))

    return {
        "batch_id": batch_id,
        "review_url": f"/prices/upload?batch={batch_id}",
    }


# ── Helpers ──


def _deduplicate_items(items: list[dict]) -> tuple:
    """Deduplicate extracted items by name.

    When multiple files contain the same item, keep the one with the
    highest confidence score. Returns (deduped_items, count_merged).
    """
    if not items:
        return items, 0

    seen: dict = {}  # normalized name -> (index, confidence)
    result = []
    dupes = 0

    for item in items:
        name = (item.get("item_name") or "").strip().lower()
        if not name:
            result.append(item)
            continue

        conf = item.get("confidence", 0) or 0

        if name in seen:
            idx, prev_conf = seen[name]
            dupes += 1
            # Keep the higher-confidence version
            if conf > prev_conf:
                result[idx] = item
                seen[name] = (idx, conf)
        else:
            seen[name] = (len(result), conf)
            result.append(item)

    return result, dupes


def _add_alias_if_new(master_id: str, vendor_item_name: str, master_items: list[dict]):
    """Add vendor item name to aliases if not already present."""
    for item in master_items:
        if item["id"] == master_id:
            aliases_raw = item.get("aliases", "")
            try:
                aliases = json.loads(aliases_raw) if aliases_raw else []
            except (json.JSONDecodeError, TypeError):
                aliases = []

            lower_aliases = [a.lower() for a in aliases]
            if vendor_item_name.lower() not in lower_aliases:
                aliases.append(vendor_item_name)
                notion_sync.update_item_aliases(master_id, aliases)
            break


@bp.route("/order-check")
def order_check_page():
    """Par level order check — see what needs to be ordered."""
    items = notion_sync.get_all_items(ITEMS_DB_ID)
    # Only show active items that have a par level set
    items = [i for i in items if i.get("active", True) and i.get("par_level")]
    items.sort(key=lambda x: (x.get("category", "Other"), x["name"]))
    return render_template_string(ORDER_CHECK_HTML, items=items)


# ── HTML Templates ──

STYLE = """
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: 'DM Sans', -apple-system, sans-serif;
        background: #F5EDDC;
        color: #333;
        min-height: 100vh;
    }
    .nav {
        background: #475417;
        color: white;
        padding: 1rem 2rem;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .nav a { color: #F5EDDC; text-decoration: none; margin-left: 1.5rem; font-size: 0.9rem; }
    .nav a:hover { text-decoration: underline; }
    .nav h1 { font-size: 1.3rem; font-weight: 600; }
    .container { max-width: 1100px; margin: 2rem auto; padding: 0 1.5rem; }
    .card {
        background: white;
        border-radius: 12px;
        padding: 2rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    }
    h2 { color: #475417; margin-bottom: 1rem; }
    .vendor-tabs { display: flex; gap: 0.5rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
    .vendor-tab {
        padding: 0.6rem 1.2rem;
        border: 2px solid #ccc;
        border-radius: 8px;
        cursor: pointer;
        font-weight: 500;
        transition: all 0.2s;
    }
    .vendor-tab.active { border-color: #4a7c1f; background: #4a7c1f; color: white; }
    .vendor-tab:hover { border-color: #4a7c1f; }
    .drop-zone {
        border: 2px dashed #ccc;
        border-radius: 12px;
        padding: 3rem;
        text-align: center;
        cursor: pointer;
        transition: all 0.2s;
        color: #666;
    }
    .drop-zone:hover, .drop-zone.dragover { border-color: #4a7c1f; background: #f0f7e6; }
    .drop-zone input[type="file"] { display: none; }
    .btn {
        background: #4a7c1f;
        color: white;
        border: none;
        padding: 0.7rem 1.5rem;
        border-radius: 8px;
        font-size: 1rem;
        cursor: pointer;
        font-weight: 500;
    }
    .btn:hover { background: #3a6216; }
    .btn:disabled { background: #999; cursor: not-allowed; }
    .results { margin-top: 1.5rem; }
    .result-item { padding: 1rem; border-left: 4px solid #4a7c1f; margin-bottom: 0.5rem; background: #f9f9f9; border-radius: 0 8px 8px 0; }
    .result-item.error { border-left-color: #c0392b; }
    .stat { display: inline-block; margin-right: 1.5rem; }
    .stat-num { font-size: 1.5rem; font-weight: 700; color: #475417; }
    .stat-label { font-size: 0.8rem; color: #666; }
    .login-box { max-width: 400px; margin: 5rem auto; }
    .login-box input[type="password"] {
        width: 100%;
        padding: 0.8rem;
        border: 2px solid #ddd;
        border-radius: 8px;
        font-size: 1rem;
        margin-bottom: 1rem;
    }
    #spinner { display: none; }
    #spinner.active { display: block; text-align: center; padding: 2rem; color: #666; }
    .mode-tabs { display: flex; gap: 0; margin-bottom: 1.5rem; border-bottom: 2px solid #e0e0e0; }
    .mode-tab {
        padding: 0.6rem 1.5rem;
        cursor: pointer;
        font-weight: 500;
        color: #666;
        border-bottom: 2px solid transparent;
        margin-bottom: -2px;
        transition: all 0.2s;
    }
    .mode-tab.active { color: #475417; border-bottom-color: #475417; }
    .mode-tab:hover { color: #475417; }
    .paste-area {
        width: 100%;
        min-height: 250px;
        padding: 1rem;
        border: 2px solid #ddd;
        border-radius: 8px;
        font-family: 'SF Mono', 'Consolas', monospace;
        font-size: 0.85rem;
        resize: vertical;
        line-height: 1.5;
    }
    .paste-area:focus { border-color: #4a7c1f; outline: none; }
    .mode-content { display: none; }
    .mode-content.active { display: block; }
    .review-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-top: 1rem; }
    .review-table th { background: #475417; color: white; padding: 0.5rem; text-align: left; position: sticky; top: 0; }
    .review-table td { padding: 0.4rem 0.5rem; border-bottom: 1px solid #e8e8e8; }
    .review-table tr:hover { background: #f5f5f5; }
    .review-table tr.excluded { opacity: 0.4; text-decoration: line-through; }
    .badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 10px; font-size: 0.75rem; font-weight: 600; }
    .badge-matched { background: #e8f5e9; color: #2e7d32; }
    .badge-review { background: #fff3e0; color: #e65100; }
    .badge-new { background: #e3f2fd; color: #1565c0; }
    .review-actions { display: flex; gap: 0.5rem; margin-top: 1rem; align-items: center; }
    .review-summary { display: flex; gap: 1.5rem; margin: 1rem 0; }
    .btn-danger { background: #c0392b; }
    .btn-danger:hover { background: #a93226; }
    .btn-secondary { background: #666; }
    .btn-secondary:hover { background: #555; }
    .confidence-bar { display: inline-block; width: 40px; height: 6px; background: #eee; border-radius: 3px; overflow: hidden; vertical-align: middle; }
    .confidence-fill { height: 100%; border-radius: 3px; }
    .mapping-input {
        width: 100%;
        border: 1px solid #ddd;
        padding: 0.3rem 0.5rem;
        border-radius: 4px;
        font-size: 0.85rem;
        background: white;
    }
    .mapping-input:focus, .price-input:focus, .unit-select:focus, .size-input:focus, .size-unit-input:focus, .qty-input:focus { border-color: #4a7c1f; outline: none; }
    .price-input {
        width: 80px;
        border: 1px solid #ddd;
        padding: 0.3rem 0.4rem;
        border-radius: 4px;
        font-size: 0.85rem;
        background: white;
        text-align: right;
    }
    .unit-select {
        border: 1px solid #ddd;
        padding: 0.25rem 0.3rem;
        border-radius: 4px;
        font-size: 0.8rem;
        background: white;
    }
    .qty-input {
        width: 50px;
        border: 1px solid #ddd;
        padding: 0.3rem 0.4rem;
        border-radius: 4px;
        font-size: 0.85rem;
        background: white;
        text-align: center;
    }
    .total-cell {
        font-weight: 600;
        white-space: nowrap;
    }
    .size-input {
        width: 55px;
        border: 1px solid #ddd;
        padding: 0.3rem 0.3rem;
        border-radius: 4px;
        font-size: 0.8rem;
        background: white;
        text-align: right;
    }
    .size-unit-input {
        width: 55px;
        border: 1px solid #ddd;
        padding: 0.2rem 0.3rem;
        border-radius: 4px;
        font-size: 0.75rem;
        background: white;
        margin-left: 2px;
    }
    .upload-type-row {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        margin-bottom: 1.5rem;
    }
    .upload-type-row label { font-weight: 500; color: #475417; margin-right: 0.5rem; }
    .type-btn {
        padding: 0.45rem 1rem;
        border: 2px solid #ccc;
        border-radius: 8px;
        cursor: pointer;
        font-weight: 500;
        font-size: 0.9rem;
        background: white;
        transition: all 0.2s;
    }
    .type-btn.active { border-color: #4a7c1f; background: #4a7c1f; color: white; }
    .type-btn:hover { border-color: #4a7c1f; }
</style>
"""

INDEX_HTML = f"""<!DOCTYPE html>
<html>
<head><title>Livite Vendor Prices</title>{STYLE}</head>
<body>
<div class="nav">
    <h1>Livite Vendor Prices</h1>
    <div>
        <a href="/prices/">Prices</a>
        <a href="/prices/upload">Upload</a>
        <a href="/prices/compare">Compare</a>
        <a href="/prices/trends">Trends</a>
        <a href="/prices/review">Items</a>
        <a href="/prices/history">History</a>
        <a href="/prices/spending">Spending</a>
        <a href="/prices/order-check">Order</a>
        <a href="/prices/invoices" style="position:relative;">Invoices{{%- if pending_invoices > 0 %}}<span style="position:absolute;top:-6px;right:-10px;background:#d9342b;color:#fff;border-radius:50%;width:18px;height:18px;font-size:11px;display:flex;align-items:center;justify-content:center;">{{{{ pending_invoices }}}}</span>{{%- endif %}}</a>
        {{%- if role == 'owner' %}} <a href="/">Sales</a>{{%- endif %}}
        <a href="/logout">Logout</a>
    </div>
</div>
<div class="container">
    {{%- if pending_invoices > 0 %}}
    <div class="card" style="border-left:4px solid #e67e22;margin-bottom:1rem;">
        <div style="display:flex;align-items:center;gap:0.75rem;">
            <span style="font-size:1.5rem;color:#e67e22;font-weight:700;">{{{{ pending_invoices }}}}</span>
            <div>
                <div style="font-weight:600;">Pending Email Invoices</div>
                <div style="font-size:0.85rem;color:#666;">Auto-detected from vendor emails. Click to review.</div>
            </div>
            <a href="/prices/invoices" class="btn" style="margin-left:auto;background:#e67e22;">Review</a>
        </div>
    </div>
    {{%- endif %}}
    <div class="card">
        <h2>Weekly Price Comparison</h2>
        <p style="color:#666; margin-bottom:1.5rem;">
            Upload vendor price sheets to compare prices and generate optimal shopping lists.
        </p>
        <div style="display:flex; gap:0.75rem; flex-wrap:wrap;">
            <a href="/prices/upload" class="btn">Upload Prices</a>
            <a href="/prices/compare" class="btn" style="background:#1a5276;">Compare Vendors</a>
            <a href="/prices/trends" class="btn" style="background:#e67e22;">Price Trends</a>
            <a href="/prices/review" class="btn" style="background:#7d3c98;">Item Mapping</a>
        </div>
    </div>
</div>
</body>
</html>"""

UPLOAD_HTML = """<!DOCTYPE html>
<html>
<head><title>Upload — Livite Vendor Prices</title>""" + STYLE + """
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/cropperjs/1.6.2/cropper.min.css">
<style>
    .preview-grid { display:flex; gap:1rem; flex-wrap:wrap; margin:1rem 0; }
    .preview-card {
        background:#f9f9f9; border-radius:8px; padding:0.75rem;
        border:1px solid #e0e0e0; max-width:220px; position:relative;
    }
    .preview-card img { width:100%; border-radius:4px; }
    .preview-meta { font-size:0.75rem; color:#666; margin-top:0.5rem; }
    .blur-warn { color:#c0392b; font-weight:600; font-size:0.8rem; }
    .blur-ok { color:#27ae60; font-size:0.8rem; }
    .price-up { color:#c0392b; font-weight:600; }
    .price-down { color:#27ae60; font-weight:600; }
    .price-change-badge { font-size:0.75rem; padding:1px 6px; border-radius:10px; margin-left:4px; }
    .price-change-badge.up { background:#fde8e8; color:#c0392b; }
    .price-change-badge.down { background:#e8f5e9; color:#27ae60; }
    .cached-badge { background:#e3f2fd; color:#1565c0; font-size:0.75rem;
                    padding:2px 8px; border-radius:10px; font-weight:600; }
    #cropModal {
        display:none; position:fixed; top:0; left:0; width:100%; height:100%;
        background:rgba(0,0,0,0.7); z-index:999; align-items:center; justify-content:center;
    }
    #cropModal.active { display:flex; }
    .crop-container {
        background:white; border-radius:12px; padding:1.5rem; max-width:90vw;
        max-height:90vh; overflow:auto;
    }
    .crop-container img { max-width:100%; max-height:60vh; display:block; }
    .crop-actions { display:flex; gap:0.5rem; margin-top:1rem; justify-content:flex-end; }
</style>
</head>
<body>
<div class="nav">
    <h1>Livite Vendor Prices</h1>
    <div>
        <a href="/prices/">Prices</a>
        <a href="/prices/upload">Upload</a>
        <a href="/prices/compare">Compare</a>
        <a href="/prices/trends">Trends</a>
        <a href="/prices/review">Items</a>
        <a href="/prices/history">History</a>
        <a href="/prices/spending">Spending</a>
        <a href="/prices/order-check">Order</a>
        {% if role == 'owner' %}<a href="/">Sales</a>{% endif %}
        <a href="/logout">Logout</a>
    </div>
</div>
<div class="container">
    <div class="card">
        <h2>Upload Price Sheets</h2>

        <div style="display:flex;gap:8px;margin-bottom:1rem;flex-wrap:wrap;">
            <button class="btn" style="background:#c0392b;font-size:0.85rem;padding:8px 14px;"
                    onclick="quickUpload('restaurant_depot')">RD Quick Upload</button>
            <button class="btn" style="background:#27ae60;font-size:0.85rem;padding:8px 14px;"
                    onclick="quickUpload('baldor')">Baldor Quick Upload</button>
            <button class="btn" style="background:#1a5276;font-size:0.85rem;padding:8px 14px;"
                    onclick="quickUpload('sysco')">Sysco Quick Upload</button>
        </div>

        <div class="vendor-tabs" id="vendorTabs">
            {% for key, name in vendors.items() %}
            <div class="vendor-tab {% if loop.first %}active{% endif %}"
                 data-vendor="{{ key }}" onclick="selectVendor('{{ key }}', this)">
                {{ name }}
            </div>
            {% endfor %}
        </div>

        <div class="upload-type-row">
            <label>Upload Type:</label>
            <div class="type-btn active" onclick="setUploadType('Purchase', this)">Purchase</div>
            <div class="type-btn" onclick="setUploadType('Price Update', this)">Price Update</div>
        </div>

        <div class="mode-tabs">
            <div class="mode-tab active" onclick="switchMode('file', this)">File Upload</div>
            <div class="mode-tab" onclick="switchMode('paste', this)">Paste Text</div>
        </div>

        <input type="hidden" id="vendorInput" value="sysco">

        <div class="mode-content active" id="fileMode">
            <form id="uploadForm" enctype="multipart/form-data">
                <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
                    <p style="font-size:1.1rem; margin-bottom:0.5rem;">Drop files here or click to browse</p>
                    <p style="font-size:0.85rem;">PDF, photos, receipts, screenshots, CSV, or text files</p>
                    <input type="file" id="fileInput" name="files" multiple
                           accept=".pdf,.png,.jpg,.jpeg,.heic,.webp,.csv,.txt,.xlsx"
                           capture="environment">
                </div>
                <div id="fileList" style="margin-top:1rem;"></div>
                <button type="submit" class="btn" style="margin-top:1rem;" id="submitBtn" disabled>
                    Extract Prices
                </button>
            </form>
        </div>

        <div class="mode-content" id="pasteMode">
            <form id="pasteForm">
                <textarea class="paste-area" id="pasteText" placeholder="Paste vendor price list here...&#10;&#10;Example:&#10;Chicken Breast Boneless 10lb  $42.50/case&#10;Atlantic Salmon Fillet  $18.99/lb&#10;Mixed Greens 3lb  $12.75/case"></textarea>
                <button type="submit" class="btn" style="margin-top:1rem;" id="pasteBtn" disabled>
                    Extract Prices
                </button>
            </form>
        </div>

        <div id="previewSection" style="display:none;">
            <h3 style="margin:1rem 0 0.5rem; color:#475417; font-size:1rem;">Image Preview</h3>
            <div class="preview-grid" id="previewGrid"></div>
            <div style="display:flex; gap:0.5rem; align-items:center;">
                <button class="btn" style="font-size:0.85rem;padding:6px 14px;background:#7d3c98;"
                        onclick="openCropTool()">Adjust Crop</button>
                <span style="font-size:0.8rem;color:#666;">Not happy with auto-crop? Draw your own.</span>
            </div>
        </div>

        <div id="spinner">
            <p>Extracting prices... this may take 15-30 seconds.</p>
        </div>

        <div class="results" id="results"></div>
    </div>
</div>

<div id="cropModal">
    <div class="crop-container">
        <h3 style="margin-bottom:1rem; color:#475417;">Manual Crop</h3>
        <p style="font-size:0.85rem; color:#666; margin-bottom:0.75rem;">
            Drag to select the area containing price data.
        </p>
        <img id="cropImage" src="">
        <div class="crop-actions">
            <button class="btn btn-secondary" onclick="closeCropModal()">Cancel</button>
            <button class="btn" onclick="applyCrop()">Apply Crop & Re-extract</button>
        </div>
    </div>
</div>

<script>
let selectedVendor = 'sysco';
let currentMode = 'file';
let uploadType = 'Purchase';
let pendingBatchId = null;
let pendingItems = [];
let masterItemsList = [];
const VENDORS_MAP = {sysco:'Sysco', baldor:'Baldor', freshpoint:'FreshPoint', restaurant_depot:'Restaurant Depot'};

function setUploadType(type, el) {
    uploadType = type;
    document.querySelectorAll('.type-btn').forEach(b => b.classList.remove('active'));
    el.classList.add('active');
    // Show/hide Qty & Total columns if review table is visible
    const table = document.querySelector('.review-table');
    if (table) {
        const hide = type === 'Price Update';
        // Qty is col 4, Total is col 5 (0-indexed)
        table.querySelectorAll('tr').forEach(row => {
            const cells = row.children;
            if (cells[4]) cells[4].style.display = hide ? 'none' : '';
            if (cells[5]) cells[5].style.display = hide ? 'none' : '';
        });
    }
}

function quickUpload(vendorKey) {
    // Pre-select vendor and open camera directly
    const el = document.querySelector('[data-vendor="' + vendorKey + '"]');
    if (el) selectVendor(vendorKey, el);
    // Ensure file mode is active
    if (currentMode !== 'file') switchMode('file', document.querySelector('.mode-tab'));
    // Create a temporary input that opens camera on mobile
    const tempInput = document.createElement('input');
    tempInput.type = 'file';
    tempInput.accept = 'image/*';
    tempInput.capture = 'environment';
    tempInput.multiple = true;
    tempInput.onchange = function() {
        // Transfer files to the main file input form
        const dt = new DataTransfer();
        for (let i = 0; i < tempInput.files.length; i++) {
            dt.items.add(tempInput.files[i]);
        }
        document.getElementById('fileInput').files = dt.files;
        document.getElementById('fileInput').dispatchEvent(new Event('change'));
    };
    tempInput.click();
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function selectVendor(key, el) {
    // If review results are showing for a different vendor, clear them
    if (pendingItems.length > 0 && key !== selectedVendor) {
        resetUpload();
    }
    selectedVendor = key;
    document.getElementById('vendorInput').value = key;
    document.querySelectorAll('.vendor-tab').forEach(t => t.classList.remove('active'));
    el.classList.add('active');
}

function switchMode(mode, el) {
    currentMode = mode;
    document.querySelectorAll('.mode-tab').forEach(t => t.classList.remove('active'));
    el.classList.add('active');
    document.querySelectorAll('.mode-content').forEach(c => c.classList.remove('active'));
    document.getElementById(mode + 'Mode').classList.add('active');
}

// Drag and drop
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const fileList = document.getElementById('fileList');
const submitBtn = document.getElementById('submitBtn');

['dragenter', 'dragover'].forEach(e => {
    dropZone.addEventListener(e, ev => { ev.preventDefault(); dropZone.classList.add('dragover'); });
});
['dragleave', 'drop'].forEach(e => {
    dropZone.addEventListener(e, ev => { ev.preventDefault(); dropZone.classList.remove('dragover'); });
});
dropZone.addEventListener('drop', ev => {
    fileInput.files = ev.dataTransfer.files;
    updateFileList();
});
fileInput.addEventListener('change', updateFileList);

function updateFileList() {
    const files = fileInput.files;
    if (files.length === 0) { fileList.innerHTML = ''; submitBtn.disabled = true; return; }
    let html = '<p style="font-weight:500; margin-bottom:0.5rem;">Selected files:</p>';
    for (const f of files) {
        html += '<div style="padding:0.3rem 0; color:#555;" id="file-info-' + f.name.replace(/[^a-z0-9]/gi,'_') + '">' +
                f.name + ' (' + (f.size/1024).toFixed(1) + ' KB)</div>';
    }
    fileList.innerHTML = html;
    submitBtn.disabled = false;

    // Client-side blur check + preview for image files
    checkBlurForFiles(files);
}

let uploadedFileData = {};  // filename -> {dataUrl, width, height}

function checkBlurForFiles(files) {
    const imageExts = ['.png','.jpg','.jpeg','.webp','.gif','.heic'];
    document.getElementById('previewSection').style.display = 'none';
    const grid = document.getElementById('previewGrid');
    grid.innerHTML = '';
    let hasImages = false;

    for (const f of files) {
        const ext = '.' + f.name.split('.').pop().toLowerCase();
        if (!imageExts.includes(ext)) continue;
        hasImages = true;

        const reader = new FileReader();
        reader.onload = function(e) {
            const dataUrl = e.target.result;
            const img = new Image();
            img.onload = function() {
                uploadedFileData[f.name] = {dataUrl, width: img.width, height: img.height};
                const score = computeBlurScore(img);
                const isBlurry = score < 80;
                const safeId = f.name.replace(/[^a-z0-9]/gi, '_');

                let card = '<div class="preview-card">';
                card += '<img src="' + dataUrl + '">';
                card += '<div class="preview-meta">';
                card += f.name.substring(0, 20) + '<br>';
                card += img.width + 'x' + img.height + '<br>';
                if (isBlurry) {
                    card += '<span class="blur-warn">Blurry (score: ' + Math.round(score) + ')</span>';
                    // Also warn on file list
                    const fi = document.getElementById('file-info-' + safeId);
                    if (fi) fi.innerHTML += ' <span class="blur-warn">-- Blurry! Consider retaking</span>';
                } else {
                    card += '<span class="blur-ok">Sharp (' + Math.round(score) + ')</span>';
                }
                card += '</div></div>';
                grid.innerHTML += card;
            };
            img.src = dataUrl;
        };
        reader.readAsDataURL(f);
    }
    if (hasImages) {
        document.getElementById('previewSection').style.display = 'block';
    }
}

function computeBlurScore(img) {
    // Client-side Laplacian variance for blur detection
    const canvas = document.createElement('canvas');
    const maxSize = 400;
    const scale = Math.min(maxSize / img.width, maxSize / img.height, 1);
    canvas.width = Math.round(img.width * scale);
    canvas.height = Math.round(img.height * scale);
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    const w = canvas.width;
    const h = canvas.height;

    // Convert to grayscale
    const gray = new Float64Array(w * h);
    for (let i = 0; i < w * h; i++) {
        const idx = i * 4;
        gray[i] = 0.299 * data[idx] + 0.587 * data[idx+1] + 0.114 * data[idx+2];
    }

    // Laplacian variance
    let sum = 0, sum2 = 0, n = 0;
    for (let y = 1; y < h - 1; y++) {
        for (let x = 1; x < w - 1; x++) {
            const i = y * w + x;
            const lap = gray[i-w] + gray[i+w] + gray[i-1] + gray[i+1] - 4*gray[i];
            sum += lap;
            sum2 += lap * lap;
            n++;
        }
    }
    if (n === 0) return 999;
    const mean = sum / n;
    return sum2 / n - mean * mean;
}

// Crop tool
let cropperInstance = null;

function openCropTool() {
    // Use the first uploaded image file
    const names = Object.keys(uploadedFileData);
    if (names.length === 0) { alert('No image files to crop.'); return; }

    const fileData = uploadedFileData[names[0]];
    const cropImg = document.getElementById('cropImage');
    cropImg.src = fileData.dataUrl;
    document.getElementById('cropModal').classList.add('active');

    // Initialize Cropper.js after image loads
    cropImg.onload = function() {
        if (cropperInstance) cropperInstance.destroy();
        cropperInstance = new Cropper(cropImg, {
            viewMode: 1,
            dragMode: 'crop',
            autoCropArea: 0.8,
            responsive: true,
        });
    };
}

function closeCropModal() {
    document.getElementById('cropModal').classList.remove('active');
    if (cropperInstance) { cropperInstance.destroy(); cropperInstance = null; }
}

async function applyCrop() {
    if (!cropperInstance || !pendingBatchId) {
        alert('No crop data or pending batch.');
        closeCropModal();
        return;
    }

    const cropData = cropperInstance.getData(true);
    closeCropModal();

    document.getElementById('spinner').classList.add('active');
    document.getElementById('results').innerHTML = '<div class="result-item">Re-extracting with manual crop...</div>';

    try {
        const resp = await fetch('/prices/api/crop-extract', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                batch_id: pendingBatchId,
                crop: {x: cropData.x, y: cropData.y, w: cropData.width, h: cropData.height}
            }),
        });
        showReview(await resp.json());
    } catch (err) {
        document.getElementById('results').innerHTML = '<div class="result-item error">Crop re-extract failed: ' + err.message + '</div>';
    }
    document.getElementById('spinner').classList.remove('active');
}

// Enable/disable paste button
const pasteText = document.getElementById('pasteText');
const pasteBtn = document.getElementById('pasteBtn');
pasteText.addEventListener('input', () => {
    pasteBtn.disabled = pasteText.value.trim().length === 0;
});

// Show review table
function showReview(data) {
    if (data.error) {
        document.getElementById('results').innerHTML =
            '<div class="result-item error">Error: ' + data.error +
            '<br><button class="btn" style="margin-top:0.75rem;" onclick="retryExtraction()">Retry</button></div>';
        return;
    }

    pendingBatchId = data.batch_id;
    pendingItems = data.items;
    masterItemsList = data.master_items || [];

    const matched = pendingItems.filter(i => i.status === 'matched').length;
    const review = pendingItems.filter(i => i.status === 'review').length;
    const newItems = pendingItems.filter(i => i.status === 'new').length;
    const dupesMerged = data.duplicates_merged || 0;
    const warnings = data.warnings || [];
    const priceUps = pendingItems.filter(i => i.price_change && i.price_change.direction === 'up').length;
    const priceDowns = pendingItems.filter(i => i.price_change && i.price_change.direction === 'down').length;
    const cachedFiles = data.cached_files || 0;

    let html = '';
    if (warnings.length > 0) {
        html += '<div class="result-item error" style="margin-bottom:1rem;">';
        html += '<strong>Some files failed:</strong><br>';
        warnings.forEach(w => { html += escapeHtml(w) + '<br>'; });
        html += 'Items from other files were extracted successfully.</div>';
    }

    if (cachedFiles > 0) {
        html += '<div class="result-item" style="margin-bottom:1rem;border-left-color:#1565c0;">';
        html += '<span class="cached-badge">Cached</span> ';
        html += cachedFiles + ' file(s) matched a recent upload — used cached results (no API cost).</div>';
    }

    html += '<div class="review-summary">';
    html += '<span class="stat"><span class="stat-num">' + pendingItems.length + '</span><br><span class="stat-label">Extracted</span></span>';
    html += '<span class="stat"><span class="stat-num">' + matched + '</span><br><span class="stat-label">Matched</span></span>';
    html += '<span class="stat"><span class="stat-num">' + review + '</span><br><span class="stat-label">Needs Review</span></span>';
    html += '<span class="stat"><span class="stat-num">' + newItems + '</span><br><span class="stat-label">New Items</span></span>';
    if (dupesMerged > 0) {
        html += '<span class="stat"><span class="stat-num">' + dupesMerged + '</span><br><span class="stat-label">Duplicates Merged</span></span>';
    }
    if (priceUps > 0) {
        html += '<span class="stat"><span class="stat-num price-up">' + priceUps + '</span><br><span class="stat-label">Price Increases</span></span>';
    }
    if (priceDowns > 0) {
        html += '<span class="stat"><span class="stat-num price-down">' + priceDowns + '</span><br><span class="stat-label">Price Decreases</span></span>';
    }
    html += '</div>';

    html += '<datalist id="masterItemsList">';
    masterItemsList.forEach(m => { html += '<option value="' + escapeHtml(m.name) + '">'; });
    html += '</datalist>';
    html += '<datalist id="sizeUnitOpts">';
    ['oz','lb','g','kg','gallon','quart','pint','liter','ml','fl oz','can','each','piece','ct','wrap','slice','portion','bag','bottle'].forEach(u => { html += '<option value="' + u + '">'; });
    html += '</datalist>';
    const unitOptions = ['case','lb','each','gallon','bag','box','dozen','pack','oz','kg'];
    html += '<datalist id="unitOptionsList">';
    unitOptions.forEach(u => { html += '<option value="' + u + '">'; });
    html += '</datalist>';

    const isPriceUpdate = uploadType === 'Price Update';
    const qtyDisplay = isPriceUpdate ? 'none' : '';

    html += '<div style="overflow-x:auto;"><table class="review-table"><thead><tr>';
    html += '<th style="width:30px;"><input type="checkbox" checked onchange="toggleAll(this)"></th>';
    html += '<th>Vendor Item</th><th>Matched To</th><th>Price</th>';
    html += '<th style="display:' + qtyDisplay + ';">Qty</th><th style="display:' + qtyDisplay + ';">Total</th>';
    html += '<th>Unit</th><th>Pack</th><th>Each Size</th><th>$/Unit</th><th>Status</th>';
    html += '</tr></thead><tbody>';

    pendingItems.forEach((item, idx) => {
        const conf = item.confidence || 0;
        const confPct = Math.round(conf * 100);
        const confColor = conf >= 0.85 ? '#27ae60' : conf >= 0.5 ? '#e67e22' : '#c0392b';

        let statusBadge = '';
        if (item.status === 'matched') statusBadge = '<span class="badge badge-matched">Matched</span>';
        else if (item.status === 'review') statusBadge = '<span class="badge badge-review">Review</span>';
        else statusBadge = '<span class="badge badge-new">New</span>';

        const currentName = item.master_item_name || item.suggested_name || item.item_name;
        const curUnit = (item.unit || 'case').toLowerCase();
        const curUoM = item.unit_detail || '';

        const curQty = item.quantity || 1;
        const totalCost = ((item.price || 0) * curQty).toFixed(2);

        html += '<tr id="row-' + idx + '">';
        html += '<td><input type="checkbox" checked onchange="toggleItem(' + idx + ', this)"></td>';
        html += '<td><strong>' + (item.item_name || '') + '</strong></td>';
        html += '<td><input type="text" list="masterItemsList" value="' + escapeHtml(currentName) + '" class="mapping-input" onchange="updateMapping(' + idx + ', this)"></td>';
        let priceHtml = '<input type="number" step="0.01" min="0" value="' + (item.price || 0).toFixed(2) + '" class="price-input" onchange="updatePrice(' + idx + ', this)">';
        if (item.price_change) {
            const pc = item.price_change;
            const arrow = pc.direction === 'up' ? 'Up' : 'Down';
            const cls = pc.direction === 'up' ? 'up' : 'down';
            priceHtml += '<span class="price-change-badge ' + cls + '" title="Was $' + pc.old_price.toFixed(2) + '">';
            priceHtml += arrow + ' ' + Math.abs(pc.pct_change).toFixed(0) + '%</span>';
        }
        html += '<td>' + priceHtml + '</td>';
        html += '<td style="display:' + qtyDisplay + ';"><input type="number" step="1" min="1" value="' + curQty + '" class="qty-input" onchange="updateQty(' + idx + ', this)"></td>';
        html += '<td class="total-cell" id="total-' + idx + '" style="display:' + qtyDisplay + ';">$' + totalCost + '</td>';
        html += '<td><input type="text" list="unitOptionsList" class="unit-select" value="' + curUnit + '" onchange="updateUnit(' + idx + ', this)"></td>';
        const packQty = item.pack_qty || 1;
        const eachSize = item.each_size || 0;
        const sizeUnit = (item.size_unit || '').toLowerCase();
        html += '<td><input type="number" step="1" min="1" value="' + packQty + '" class="qty-input" onchange="updatePackQty(' + idx + ', this)"></td>';
        html += '<td style="white-space:nowrap;"><input type="number" step="0.1" min="0" value="' + (eachSize || '') + '" class="size-input" placeholder="0" onchange="updateEachSize(' + idx + ', this)">';
        html += '<input type="text" list="sizeUnitOpts" class="size-unit-input" value="' + sizeUnit + '" placeholder="unit" onchange="updateSizeUnit(' + idx + ', this)"></td>';
        const compPrice = item.comparable_price || 0;
        const compUnit = item.comparable_unit || '';
        const compLabel = compPrice > 0 && compPrice !== item.price ? ('$' + compPrice.toFixed(2) + '/' + compUnit) : '';
        html += '<td style="font-size:0.8rem; color:#4a7c1f; white-space:nowrap;">' + compLabel + '</td>';
        html += '<td>' + statusBadge + '</td>';
        html += '</tr>';
    });

    html += '</tbody></table></div>';
    html += '<div class="review-actions">';
    html += '<button class="btn" onclick="confirmItems()">Confirm & Save to Notion</button>';
    html += '<button class="btn btn-secondary" onclick="resetUpload()">Cancel</button>';
    html += '</div>';

    document.getElementById('results').innerHTML = html;
}

function toggleAll(checkbox) {
    const checked = checkbox.checked;
    pendingItems.forEach((item, idx) => {
        item.excluded = !checked;
        const row = document.getElementById('row-' + idx);
        row.classList.toggle('excluded', !checked);
        row.querySelector('input[type=checkbox]').checked = checked;
    });
}

function toggleItem(idx, checkbox) {
    pendingItems[idx].excluded = !checkbox.checked;
    document.getElementById('row-' + idx).classList.toggle('excluded', !checkbox.checked);
}

function updateMapping(idx, input) {
    const newName = input.value.trim();
    if (!newName) return;

    const match = masterItemsList.find(m => m.name.toLowerCase() === newName.toLowerCase());
    const row = document.getElementById('row-' + idx);
    const statusCell = row.querySelectorAll('td')[10];

    if (match) {
        pendingItems[idx].master_item_id = match.id;
        pendingItems[idx].master_item_name = match.name;
        pendingItems[idx].status = 'matched';
        input.value = match.name;
        statusCell.innerHTML = '<span class="badge badge-matched">Matched</span>';
    } else {
        pendingItems[idx].master_item_id = null;
        pendingItems[idx].master_item_name = newName;
        pendingItems[idx].status = 'new';
        statusCell.innerHTML = '<span class="badge badge-new">New</span>';
    }
}

function recalcTotal(idx) {
    const price = pendingItems[idx].price || 0;
    const qty = pendingItems[idx].quantity || 1;
    document.getElementById('total-' + idx).textContent = '$' + (price * qty).toFixed(2);
}

function updatePrice(idx, input) {
    pendingItems[idx].price = parseFloat(input.value) || 0;
    recalcTotal(idx);
}

function updateQty(idx, input) {
    pendingItems[idx].quantity = parseInt(input.value) || 1;
    recalcTotal(idx);
}

function updateUnit(idx, select) {
    pendingItems[idx].unit = select.value;
}

function updatePackQty(idx, input) {
    pendingItems[idx].pack_qty = parseInt(input.value) || 1;
    recalcPerUnit(idx);
}

function updateEachSize(idx, input) {
    pendingItems[idx].each_size = parseFloat(input.value) || 0;
    recalcPerUnit(idx);
}

function updateSizeUnit(idx, select) {
    pendingItems[idx].size_unit = select.value;
    recalcPerUnit(idx);
}

function recalcPerUnit(idx) {
    const item = pendingItems[idx];
    const price = item.price || 0;
    const packQty = item.pack_qty || 1;
    const eachSize = item.each_size || 0;
    const sizeUnit = item.size_unit || '';
    const cell = document.getElementById('row-' + idx).querySelectorAll('td')[8];
    const countUnits = ['can','each','piece','ct'];
    if (countUnits.includes(sizeUnit) && packQty > 0 && price > 0) {
        // Count-based: pack of 6 cans → $/can
        cell.textContent = '$' + (price / packQty).toFixed(2) + '/' + sizeUnit;
        item.comparable_price = parseFloat((price / packQty).toFixed(2));
        item.comparable_unit = sizeUnit;
    } else if (eachSize > 0 && sizeUnit && price > 0) {
        const totalSize = packQty * eachSize;
        const perUnit = (price / totalSize).toFixed(2);
        cell.textContent = '$' + perUnit + '/' + sizeUnit;
        item.comparable_price = parseFloat(perUnit);
        item.comparable_unit = sizeUnit;
    } else if (packQty > 1 && price > 0) {
        cell.textContent = '$' + (price / packQty).toFixed(2) + '/each';
        item.comparable_price = parseFloat((price / packQty).toFixed(2));
        item.comparable_unit = 'each';
    } else {
        cell.textContent = '';
    }
}

async function confirmItems() {
    const approved = pendingItems.filter(i => !i.excluded);
    if (approved.length === 0) { alert('No items selected.'); return; }

    const btn = document.querySelector('.review-actions .btn');
    btn.disabled = true;
    btn.textContent = 'Saving...';

    try {
        const resp = await fetch('/prices/api/confirm', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ batch_id: pendingBatchId, items: approved, upload_type: uploadType }),
        });
        const data = await resp.json();
        if (data.status === 'processing') {
            pollProgress(data.batch_id, data.total);
        } else if (data.error) {
            document.getElementById('results').innerHTML = '<div class="result-item error">Error: ' + data.error + '</div>';
            btn.disabled = false;
            btn.textContent = 'Confirm & Save to Notion';
        }
    } catch (err) {
        document.getElementById('results').innerHTML = '<div class="result-item error">Save failed: ' + err.message + '</div>';
        btn.disabled = false;
        btn.textContent = 'Confirm & Save to Notion';
    }
}

function pollProgress(batchId, total) {
    var results = document.getElementById('results');
    results.innerHTML = '<div class="result-item">' +
        '<strong>Writing to Notion...</strong>' +
        '<div style="margin-top:0.5rem;">' +
        '<div style="background:#e0e0e0;border-radius:6px;height:20px;overflow:hidden;margin-bottom:0.5rem;">' +
        '<div id="progressBar" style="background:#475417;height:100%;width:0%;transition:width 0.3s;border-radius:6px;"></div></div>' +
        '<span id="progressText">0 / ' + total + ' items</span>' +
        '</div></div>';

    var pollInterval = setInterval(async function() {
        try {
            var resp = await fetch('/prices/api/confirm/status/' + batchId);
            var prog = await resp.json();
            var pct = total > 0 ? Math.round((prog.completed / total) * 100) : 0;
            document.getElementById('progressBar').style.width = pct + '%';
            document.getElementById('progressText').textContent = prog.completed + ' / ' + total + ' items';

            if (prog.status === 'done' || prog.status === 'partial') {
                clearInterval(pollInterval);
                showConfirmResult(prog);
            }
        } catch (err) {
            clearInterval(pollInterval);
            results.innerHTML = '<div class="result-item error">Progress check failed: ' + err.message + '</div>';
        }
    }, 2000);
}

function showConfirmResult(prog) {
    var btn = document.querySelector('.review-actions .btn');
    if (btn) { btn.disabled = false; btn.textContent = 'Confirm & Save to Notion'; }

    var html = '<div class="result-item">' +
        '<strong>' + (prog.status === 'partial' ? 'Partially Saved' : 'Saved to Notion') + '</strong><br>' +
        '<div style="margin-top:0.5rem;">' +
        '<span class="stat"><span class="stat-num">' + prog.written + '</span><br><span class="stat-label">Written</span></span>' +
        '<span class="stat"><span class="stat-num">' + prog.matched + '</span><br><span class="stat-label">Matched</span></span>' +
        '<span class="stat"><span class="stat-num">' + (prog.new || 0) + '</span><br><span class="stat-label">New Items</span></span>' +
        '</div>';
    if (prog.errors && prog.errors.length > 0) {
        html += '<div style="margin-top:0.75rem;color:#b91c1c;font-size:0.85rem;">' +
            '<strong>' + prog.errors.length + ' item(s) failed:</strong><ul style="margin:0.25rem 0 0 1rem;">';
        prog.errors.forEach(function(e) { html += '<li>' + e + '</li>'; });
        html += '</ul></div>';
    }
    html += '</div>';
    document.getElementById('results').innerHTML = html;
}

function resetUpload() {
    pendingBatchId = null;
    pendingItems = [];
    document.getElementById('results').innerHTML = '';
    fileInput.value = '';
    fileList.innerHTML = '';
    submitBtn.disabled = true;
}

async function retryExtraction() {
    document.getElementById('results').innerHTML = '';
    document.getElementById('spinner').classList.add('active');
    if (currentMode === 'paste') {
        const formData = new FormData();
        formData.set('vendor', selectedVendor);
        formData.set('upload_type', uploadType);
        formData.set('text', pasteText.value);
        try {
            const resp = await fetch('/prices/api/paste', { method: 'POST', body: formData });
            showReview(await resp.json());
        } catch (err) {
            document.getElementById('results').innerHTML = '<div class="result-item error">Retry failed: ' + err.message + '</div>';
        }
    } else {
        // Re-submit the file form
        const formData = new FormData(document.getElementById('uploadForm'));
        formData.set('vendor', selectedVendor);
        formData.set('upload_type', uploadType);
        try {
            const resp = await fetch('/prices/api/upload', { method: 'POST', body: formData });
            showReview(await resp.json());
        } catch (err) {
            document.getElementById('results').innerHTML = '<div class="result-item error">Retry failed: ' + err.message + '</div>';
        }
    }
    document.getElementById('spinner').classList.remove('active');
}

// File upload form
document.getElementById('uploadForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    submitBtn.disabled = true;
    submitBtn.textContent = 'Extracting...';
    document.getElementById('spinner').classList.add('active');
    document.getElementById('results').innerHTML = '';

    const formData = new FormData(e.target);
    formData.set('vendor', selectedVendor);
    formData.set('upload_type', uploadType);
    try {
        const resp = await fetch('/prices/api/upload', { method: 'POST', body: formData });
        showReview(await resp.json());
    } catch (err) {
        document.getElementById('results').innerHTML = '<div class="result-item error">Upload failed: ' + err.message + '</div>';
    }

    document.getElementById('spinner').classList.remove('active');
    submitBtn.disabled = false;
    submitBtn.textContent = 'Extract Prices';
});

// Paste form
document.getElementById('pasteForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    pasteBtn.disabled = true;
    pasteBtn.textContent = 'Extracting...';
    document.getElementById('spinner').classList.add('active');
    document.getElementById('results').innerHTML = '';

    const formData = new FormData();
    formData.set('vendor', selectedVendor);
    formData.set('upload_type', uploadType);
    formData.set('text', pasteText.value);
    try {
        const resp = await fetch('/prices/api/paste', { method: 'POST', body: formData });
        showReview(await resp.json());
    } catch (err) {
        document.getElementById('results').innerHTML = '<div class="result-item error">Extraction failed: ' + err.message + '</div>';
    }

    document.getElementById('spinner').classList.remove('active');
    pasteBtn.disabled = false;
    pasteBtn.textContent = 'Extract Prices';
});

// Auto-load pending batch from ?batch= query param (Telegram uploads)
(async () => {
    const params = new URLSearchParams(window.location.search);
    const batchId = params.get('batch');
    if (!batchId) return;

    document.getElementById('spinner').classList.add('active');
    document.getElementById('results').innerHTML = '<div class="result-item">Loading...</div>';

    try {
        const resp = await fetch('/prices/api/pending/' + encodeURIComponent(batchId));
        const data = await resp.json();

        if (data.source === 'telegram' && (!data.items || data.items.length === 0)) {
            // Telegram upload — needs vendor selection before extraction
            let html = '<div class="card" style="text-align:center; padding:2rem;">';
            html += '<h3 style="margin-bottom:1rem;">Photo received from Telegram</h3>';
            html += '<p style="margin-bottom:1.5rem; color:#666;">Select a vendor to extract prices:</p>';
            html += '<div style="display:flex; gap:0.75rem; justify-content:center; flex-wrap:wrap;">';
            for (const [key, name] of Object.entries(VENDORS_MAP)) {
                html += '<button class="btn" style="padding:0.75rem 1.5rem; font-size:1rem;" ';
                html += 'onclick="extractTelegram(\\'' + batchId + '\\', \\'' + key + '\\')">' + name + '</button>';
            }
            html += '</div></div>';
            document.getElementById('results').innerHTML = html;
        } else if (data.items && data.items.length > 0) {
            // Already extracted — show review table
            if (data.vendor) {
                const entry = Object.entries(VENDORS_MAP).find(([k,v]) => v === data.vendor);
                if (entry) {
                    const el = document.querySelector('[data-vendor="' + entry[0] + '"]');
                    if (el) selectVendor(entry[0], el);
                }
            }
            showReview(data);
        } else {
            document.getElementById('results').innerHTML = '<div class="result-item error">' + (data.error || 'Batch not found') + '</div>';
        }
    } catch (err) {
        document.getElementById('results').innerHTML = '<div class="result-item error">Failed to load: ' + err.message + '</div>';
    }
    document.getElementById('spinner').classList.remove('active');
})();

// Load Cropper.js dynamically
(function() {
    var s = document.createElement('script');
    s.src = 'https://cdnjs.cloudflare.com/ajax/libs/cropperjs/1.6.2/cropper.min.js';
    document.head.appendChild(s);
})();

async function extractTelegram(batchId, vendorKey) {
    document.getElementById('spinner').classList.add('active');
    document.getElementById('results').innerHTML = '<div class="result-item">Extracting prices... this takes a moment.</div>';

    // Select the vendor in the UI
    const el = document.querySelector('[data-vendor="' + vendorKey + '"]');
    if (el) selectVendor(vendorKey, el);

    try {
        const formData = new FormData();
        formData.set('vendor', vendorKey);
        const resp = await fetch('/prices/api/pending/' + encodeURIComponent(batchId) + '/extract', {
            method: 'POST', body: formData
        });
        showReview(await resp.json());
    } catch (err) {
        document.getElementById('results').innerHTML = '<div class="result-item error">Extraction failed: ' + err.message + '</div>';
    }
    document.getElementById('spinner').classList.remove('active');
}
</script>
</body>
</html>"""


COMPARE_HTML = """<!DOCTYPE html>
<html>
<head><title>Compare — Livite Vendor Prices</title>""" + STYLE + """
<style>
    .compare-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
    .compare-table th { background: #475417; color: white; padding: 0.6rem 0.8rem; text-align: left; position: sticky; top: 0; }
    .compare-table td { padding: 0.5rem 0.8rem; border-bottom: 1px solid #e0e0e0; }
    .compare-table tr:hover { background: #f5f5f5; }
    .cheapest { background: #e8f5e9 !important; font-weight: 600; color: #2e7d32; }
    .category-header { background: #f0ebe0; font-weight: 600; color: #475417; }
    .week-selector { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 1.5rem; }
    .week-selector select { padding: 0.5rem; border: 2px solid #ddd; border-radius: 8px; font-size: 0.95rem; }
    .no-price { color: #bbb; font-style: italic; }
    .unit-label { font-size: 0.75rem; color: #888; }
</style>
</head>
<body>
<div class="nav">
    <h1>Livite Vendor Prices</h1>
    <div>
        <a href="/prices/">Prices</a>
        <a href="/prices/upload">Upload</a>
        <a href="/prices/compare">Compare</a>
        <a href="/prices/trends">Trends</a>
        <a href="/prices/review">Items</a>
        <a href="/prices/history">History</a>
        <a href="/prices/spending">Spending</a>
        <a href="/prices/order-check">Order</a>
        {% if role == 'owner' %}<a href="/">Sales</a>{% endif %}
        <a href="/logout">Logout</a>
    </div>
</div>
<div class="container">
    <div class="card">
        <h2>Price Comparison</h2>

        <div class="week-selector">
            <label><strong>Week:</strong></label>
            <select onchange="window.location='/prices/compare/'+this.value">
                {% for w in weeks %}
                <option value="{{ w }}" {% if w == current_week %}selected{% endif %}>{{ w }}</option>
                {% endfor %}
            </select>
            <a href="/prices/compare/all" class="btn" style="margin-left:1rem;font-size:0.85rem;padding:0.5rem 1rem;">View All (Latest)</a>
        </div>

        {% if not items %}
        <p style="color:#666;">No price data for this week. <a href="/prices/upload">Upload some prices</a> to get started.</p>
        {% else %}
        <div style="overflow-x:auto;">
        <table class="compare-table">
            <thead>
                <tr>
                    <th>Item</th>
                    {% for v in vendor_list %}
                    <th>{{ v }}</th>
                    {% endfor %}
                    <th>Best</th>
                </tr>
            </thead>
            <tbody>
                {% set current_category = namespace(val='') %}
                {% for item in items %}
                    {% if item.category != current_category.val %}
                        {% set current_category.val = item.category %}
                        <tr><td class="category-header" colspan="{{ vendor_list|length + 2 }}">{{ item.category }}</td></tr>
                    {% endif %}
                    <tr>
                        <td><strong>{{ item.name }}</strong></td>
                        {% set prices = [] %}
                        {% for v in vendor_list %}
                            {% if v in item.vendors %}
                                {% set p = item.vendors[v] %}
                                {% set _ = prices.append((v, p.price, p.price_per_unit)) %}
                            {% endif %}
                        {% endfor %}
                        {% set min_price = namespace(val=999999, vendor='') %}
                        {% for v, price, ppu in prices %}
                            {% if price > 0 and price < min_price.val %}
                                {% set min_price.val = price %}
                                {% set min_price.vendor = v %}
                            {% endif %}
                        {% endfor %}
                        {% for v in vendor_list %}
                            {% if v in item.vendors %}
                                {% set p = item.vendors[v] %}
                                <td class="{% if v == min_price.vendor and prices|length > 1 %}cheapest{% endif %}">
                                    ${{ "%.2f"|format(p.price) }}
                                    {% if p.price_per_unit and p.price_per_unit != p.price %}
                                    <br><span class="unit-label">${{ "%.2f"|format(p.price_per_unit) }}/unit</span>
                                    {% endif %}
                                </td>
                            {% else %}
                                <td class="no-price">—</td>
                            {% endif %}
                        {% endfor %}
                        <td>
                            {% if min_price.vendor %}
                                <strong>{{ min_price.vendor }}</strong>
                            {% else %}
                                —
                            {% endif %}
                        </td>
                    </tr>
                {% endfor %}
            </tbody>
        </table>
        </div>
        {% endif %}
    </div>
</div>
</body>
</html>"""

COMPARE_ALL_HTML = """<!DOCTYPE html>
<html>
<head><title>View All Prices — Livite Vendor Prices</title>""" + STYLE + """
<style>
    .compare-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
    .compare-table th { background: #475417; color: white; padding: 0.6rem 0.8rem; text-align: left; position: sticky; top: 0; z-index: 2; }
    .compare-table td { padding: 0.5rem 0.8rem; border-bottom: 1px solid #e0e0e0; }
    .compare-table tr:hover { background: #f5f5f5; }
    .cheapest { background: #e8f5e9 !important; font-weight: 600; color: #2e7d32; }
    .category-header { background: #f0ebe0; font-weight: 600; color: #475417; }
    .no-price { color: #bbb; font-style: italic; }
    .unit-label { font-size: 0.75rem; color: #888; }
    .week-label { font-size: 0.7rem; color: #aaa; }
    .filter-bar { display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap; align-items: center; }
    .filter-bar input { padding: 0.5rem; border: 2px solid #ddd; border-radius: 8px; font-size: 0.95rem; flex: 1; min-width: 200px; }
    .filter-bar select { padding: 0.5rem; border: 2px solid #ddd; border-radius: 8px; }
    .item-count { color: #666; font-size: 0.9rem; }
    .print-header { display: none; }

    @media print {
        body { background: white !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
        .nav, .no-print { display: none !important; }
        .container { max-width: 100%; margin: 0; padding: 0; }
        .card { box-shadow: none; padding: 0.5rem; margin: 0; border-radius: 0; }
        .print-header { display: block; text-align: center; margin-bottom: 0.5rem; }
        .print-header h1 { font-size: 1.4rem; color: #475417; margin-bottom: 0.2rem; }
        .print-header p { font-size: 0.8rem; color: #666; }
        .compare-table { font-size: 0.75rem; }
        .compare-table th { padding: 0.3rem 0.4rem; font-size: 0.75rem; }
        .compare-table td { padding: 0.25rem 0.4rem; }
        .unit-label { font-size: 0.65rem; }
        .week-label { display: none; }
        .cheapest { background: #e8f5e9 !important; }
        .category-header { background: #f0ebe0 !important; }
        h2, .filter-bar { display: none; }
        @page { size: landscape; margin: 0.5cm; }
    }
</style>
</head>
<body>
<div class="nav">
    <h1>Livite Vendor Prices</h1>
    <div>
        <a href="/prices/">Prices</a>
        <a href="/prices/upload">Upload</a>
        <a href="/prices/compare">Compare</a>
        <a href="/prices/trends">Trends</a>
        <a href="/prices/review">Items</a>
        <a href="/prices/history">History</a>
        <a href="/prices/spending">Spending</a>
        <a href="/prices/order-check">Order</a>
        {% if role == 'owner' %}<a href="/">Sales</a>{% endif %}
        <a href="/logout">Logout</a>
    </div>
</div>
<div class="container">
    <div class="card">
        <div class="print-header">
            <h1>Livite Vendor Price Comparison</h1>
            <p>Latest prices across all vendors — printed {{ now }}</p>
        </div>

        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
            <h2>All Items — Latest Prices</h2>
            <div style="display:flex; gap:0.5rem; align-items:center;">
                <span class="item-count no-print" id="itemCount">{{ items|length }} items</span>
                <a href="/prices/compare" class="btn no-print" style="font-size:0.85rem;padding:0.5rem 1rem;background:#666;">Back to Weekly</a>
                <button class="btn no-print" onclick="window.print()" style="font-size:0.85rem;padding:0.5rem 1rem;">Print</button>
            </div>
        </div>

        <div class="filter-bar no-print">
            <input type="text" id="searchBox" placeholder="Search items..." oninput="filterItems()">
            <select id="categoryFilter" onchange="filterItems()">
                <option value="">All Categories</option>
                <option value="Protein">Protein</option>
                <option value="Produce">Produce</option>
                <option value="Dairy">Dairy</option>
                <option value="Dry Goods">Dry Goods</option>
                <option value="Canned Goods">Canned Goods</option>
                <option value="Bakery">Bakery</option>
                <option value="Beverages">Beverages</option>
                <option value="Frozen">Frozen</option>
                <option value="Oils & Condiments">Oils & Condiments</option>
                <option value="Paper & Supplies">Paper & Supplies</option>
                <option value="Cleaning">Cleaning</option>
                <option value="Other">Other</option>
            </select>
        </div>

        {% if not items %}
        <p style="color:#666;">No price data found. <a href="/prices/upload">Upload some prices</a> to get started.</p>
        {% else %}
        <div style="overflow-x:auto;">
        <table class="compare-table" id="compareTable">
            <thead>
                <tr>
                    <th>Item</th>
                    {% for v in vendor_list %}
                    <th>{{ v }}</th>
                    {% endfor %}
                    <th>Best</th>
                </tr>
            </thead>
            <tbody>
                {% set current_category = namespace(val='') %}
                {% for item in items %}
                    {% if item.category != current_category.val %}
                        {% set current_category.val = item.category %}
                        <tr class="cat-row" data-cat="{{ item.category }}"><td class="category-header" colspan="{{ vendor_list|length + 2 }}">{{ item.category }}</td></tr>
                    {% endif %}
                    <tr class="item-row" data-name="{{ item.name|lower }}" data-cat="{{ item.category }}">
                        <td><strong>{{ item.name }}</strong></td>
                        {% set prices = [] %}
                        {% for v in vendor_list %}
                            {% if v in item.vendors %}
                                {% set p = item.vendors[v] %}
                                {% set _ = prices.append((v, p.price, p.price_per_unit)) %}
                            {% endif %}
                        {% endfor %}
                        {% set min_price = namespace(val=999999, vendor='') %}
                        {% for v, price, ppu in prices %}
                            {% if price > 0 and price < min_price.val %}
                                {% set min_price.val = price %}
                                {% set min_price.vendor = v %}
                            {% endif %}
                        {% endfor %}
                        {% for v in vendor_list %}
                            {% if v in item.vendors %}
                                {% set p = item.vendors[v] %}
                                <td class="{% if v == min_price.vendor and prices|length > 1 %}cheapest{% endif %}">
                                    ${{ "%.2f"|format(p.price) }}
                                    {% if p.price_per_unit and p.price_per_unit != p.price %}
                                    <br><span class="unit-label">${{ "%.2f"|format(p.price_per_unit) }}/unit</span>
                                    {% endif %}
                                    {% if p.week %}
                                    <br><span class="week-label">{{ p.week }}</span>
                                    {% endif %}
                                </td>
                            {% else %}
                                <td class="no-price">—</td>
                            {% endif %}
                        {% endfor %}
                        <td>
                            {% if min_price.vendor %}
                                <strong>{{ min_price.vendor }}</strong>
                            {% else %}
                                —
                            {% endif %}
                        </td>
                    </tr>
                {% endfor %}
            </tbody>
        </table>
        </div>
        {% endif %}
    </div>
</div>
<script>
function filterItems() {
    const q = document.getElementById('searchBox').value.toLowerCase();
    const cat = document.getElementById('categoryFilter').value;
    const rows = document.querySelectorAll('#compareTable tbody tr.item-row');
    const catRows = document.querySelectorAll('#compareTable tbody tr.cat-row');
    let visible = 0;
    const visibleCats = new Set();

    rows.forEach(row => {
        const name = row.getAttribute('data-name') || '';
        const rowCat = row.getAttribute('data-cat') || '';
        const matchQ = !q || name.includes(q);
        const matchCat = !cat || rowCat === cat;
        const show = matchQ && matchCat;
        row.style.display = show ? '' : 'none';
        if (show) {
            visible++;
            visibleCats.add(rowCat);
        }
    });

    catRows.forEach(row => {
        const rowCat = row.getAttribute('data-cat') || '';
        row.style.display = visibleCats.has(rowCat) ? '' : 'none';
    });

    document.getElementById('itemCount').textContent = visible + ' items';
}
</script>
</body>
</html>"""

REVIEW_HTML = """<!DOCTYPE html>
<html>
<head><title>Items — Livite Vendor Prices</title>""" + STYLE + """
<style>
    .items-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
    .items-table th { background: #475417; color: white; padding: 0.6rem 0.8rem; text-align: left; position: sticky; top: 0; z-index: 2; }
    .items-table td { padding: 0.5rem 0.8rem; border-bottom: 1px solid #e0e0e0; vertical-align: top; }
    .items-table tr:hover { background: #f5f5f5; cursor: pointer; }
    .alias-tag {
        display: inline-block; background: #e8e8e8; padding: 0.15rem 0.5rem;
        border-radius: 12px; font-size: 0.8rem; margin: 0.1rem 0.2rem; color: #555;
    }
    .alias-tag .remove { cursor: pointer; margin-left: 0.3rem; color: #c0392b; font-weight: bold; }
    .filter-bar { display: flex; gap: 0.5rem; margin-bottom: 1.5rem; flex-wrap: wrap; align-items: center; }
    .filter-bar input { padding: 0.5rem; border: 2px solid #ddd; border-radius: 8px; font-size: 0.95rem; flex: 1; min-width: 200px; }
    .filter-bar select { padding: 0.5rem; border: 2px solid #ddd; border-radius: 8px; }
    .item-count { color: #666; font-size: 0.9rem; }
    .edit-text {
        border: 1px solid transparent; padding: 0.2rem 0.4rem; border-radius: 4px;
        font-size: 0.85rem; background: transparent; width: 100%; box-sizing: border-box;
    }
    .edit-text:hover { border-color: #ccc; background: #fafafa; }
    .edit-text:focus { border-color: #475417; outline: none; background: white; }
    .edit-select {
        border: 1px solid transparent; padding: 0.2rem; border-radius: 4px;
        cursor: pointer; background: transparent; font-size: 0.85rem;
    }
    .edit-select:hover { border-color: #ccc; }
    .edit-select:focus { border-color: #475417; outline: none; }
    .save-flash { animation: flash 0.6s ease; }
    @keyframes flash { 0%{background:#d4edda;} 100%{background:transparent;} }
    .par-input {
        width: 50px; border: 1px solid transparent; padding: 0.2rem 0.3rem;
        border-radius: 4px; text-align: center; font-size: 0.85rem; background: transparent;
    }
    .par-input:hover { border-color: #ccc; }
    .par-input:focus { border-color: #475417; outline: none; background: white; }

    /* Item Detail Modal */
    .modal-overlay {
        display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
        background: rgba(0,0,0,0.5); z-index: 100; justify-content: center; align-items: center;
    }
    .modal-overlay.active { display: flex; }
    .modal-content {
        background: white; border-radius: 12px; width: 90%; max-width: 640px;
        max-height: 85vh; overflow-y: auto; padding: 0; box-shadow: 0 8px 32px rgba(0,0,0,0.2);
    }
    .modal-header {
        display: flex; justify-content: space-between; align-items: center;
        padding: 1.2rem 1.5rem; border-bottom: 1px solid #eee;
    }
    .modal-header h2 { margin: 0; font-size: 1.2rem; color: #2d2a24; }
    .modal-close { background: none; border: none; font-size: 1.5rem; cursor: pointer; color: #999; padding: 0 0.3rem; }
    .modal-close:hover { color: #333; }
    .modal-body { padding: 1.5rem; }
    .modal-meta { color: #666; font-size: 0.85rem; margin-bottom: 1.2rem; }
    .modal-section { margin-bottom: 1.5rem; }
    .modal-section h3 { font-size: 0.95rem; color: #475417; margin: 0 0 0.8rem 0; border-bottom: 1px solid #eee; padding-bottom: 0.4rem; }
    .pack-row {
        display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;
    }
    .pack-field { text-align: center; }
    .pack-field label { display: block; font-size: 0.75rem; color: #888; margin-bottom: 0.2rem; }
    .pack-field input, .pack-field select {
        width: 70px; padding: 0.4rem; border: 2px solid #ddd; border-radius: 6px;
        text-align: center; font-size: 0.95rem;
    }
    .pack-field input:focus, .pack-field select:focus { border-color: #475417; outline: none; }
    .pack-operator { font-size: 1.2rem; color: #888; padding-top: 1rem; }
    .pack-total {
        background: #f0f7e6; padding: 0.5rem 0.8rem; border-radius: 6px;
        font-weight: 600; color: #475417; margin-top: 0.2rem; font-size: 0.95rem;
    }
    .price-row {
        display: flex; justify-content: space-between; align-items: center;
        padding: 0.6rem 0; border-bottom: 1px solid #f0f0f0;
    }
    .price-row:last-child { border-bottom: none; }
    .price-vendor { font-weight: 600; min-width: 100px; }
    .price-case { color: #666; }
    .price-unit { font-weight: 600; color: #475417; }
    .modal-footer {
        padding: 1rem 1.5rem; border-top: 1px solid #eee; text-align: right;
    }
    .modal-footer .btn {
        padding: 0.6rem 1.5rem; border: none; border-radius: 8px;
        font-size: 0.9rem; cursor: pointer; font-weight: 600;
    }
    .btn-save { background: #475417; color: white; }
    .btn-save:hover { background: #5a6b1e; }
    .btn-save:disabled { background: #ccc; cursor: not-allowed; }
    .modal-loading { text-align: center; padding: 2rem; color: #888; }
    .no-prices { color: #999; font-style: italic; font-size: 0.9rem; }
</style>
</head>
<body>
<div class="nav">
    <h1>Livite Vendor Prices</h1>
    <div>
        <a href="/prices/">Prices</a>
        <a href="/prices/upload">Upload</a>
        <a href="/prices/compare">Compare</a>
        <a href="/prices/trends">Trends</a>
        <a href="/prices/review" style="font-weight:700;text-decoration:underline;">Items</a>
        <a href="/prices/history">History</a>
        <a href="/prices/spending">Spending</a>
        {% if role == 'owner' %}<a href="/">Sales</a>{% endif %}
        <a href="/logout">Logout</a>
    </div>
</div>
<div class="container">
    <div class="card">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
            <h2>Items Master</h2>
            <span class="item-count">{{ items|length }} items — click row for details, edit fields inline</span>
        </div>

        <div class="filter-bar">
            <input type="text" id="searchBox" placeholder="Search items..." oninput="filterItems()">
            <select id="categoryFilter" onchange="filterItems()">
                <option value="">All Categories</option>
                <option value="Protein">Protein</option>
                <option value="Produce">Produce</option>
                <option value="Dairy">Dairy</option>
                <option value="Dry Goods">Dry Goods</option>
                <option value="Bakery">Bakery</option>
                <option value="Beverages">Beverages</option>
                <option value="Frozen">Frozen</option>
                <option value="Sauces & Condiments">Sauces & Condiments</option>
                <option value="Paper & Packaging">Paper & Packaging</option>
                <option value="Chemicals & Cleaning">Chemicals & Cleaning</option>
                <option value="Other">Other</option>
            </select>
        </div>

        <div style="overflow-x:auto;">
        <table class="items-table" id="itemsTable">
            <thead>
                <tr>
                    <th>Item Name</th>
                    <th>Category</th>
                    <th>Unit</th>
                    <th>Unit Size</th>
                    <th>Par</th>
                    <th>Vendor</th>
                    <th>Aliases</th>
                </tr>
            </thead>
            <tbody>
                {% for item in items %}
                <tr data-id="{{ item.id }}" data-category="{{ item.category }}" data-name="{{ item.name|lower }}" onclick="openItemDetail(this)">
                    <td>
                        <input type="text" class="edit-text"
                               data-field="name" data-id="{{ item.id }}"
                               data-original="{{ item.name }}"
                               value="{{ item.name }}"
                               onchange="saveField(this)" onclick="event.stopPropagation()">
                    </td>
                    <td>
                        <select class="edit-select" data-field="category" data-id="{{ item.id }}"
                                data-original="{{ item.category }}" onchange="saveField(this)" onclick="event.stopPropagation()">
                            {% for cat in ['Protein','Produce','Dairy','Dry Goods','Bakery','Beverages','Frozen','Sauces & Condiments','Paper & Packaging','Chemicals & Cleaning','Other'] %}
                            <option value="{{ cat }}" {{ 'selected' if item.category == cat else '' }}>{{ cat }}</option>
                            {% endfor %}
                        </select>
                    </td>
                    <td>
                        <select class="edit-select" data-field="unit" data-id="{{ item.id }}"
                                data-original="{{ item.unit }}" onchange="saveField(this)" onclick="event.stopPropagation()">
                            {% for u in ['case','each','lb','oz','gallon','bag','box','pack'] %}
                            <option value="{{ u }}" {{ 'selected' if item.unit == u else '' }}>{{ u }}</option>
                            {% endfor %}
                        </select>
                    </td>
                    <td>
                        <input type="text" class="edit-text" style="width:80px;"
                               data-field="unit_size" data-id="{{ item.id }}"
                               data-original="{{ item.unit_size }}"
                               value="{{ item.unit_size or '' }}"
                               onchange="saveField(this)" onclick="event.stopPropagation()">
                    </td>
                    <td>
                        <input type="number" class="par-input"
                               data-field="par_level" data-id="{{ item.id }}"
                               data-original="{{ item.par_level or '' }}"
                               value="{{ item.par_level or '' }}"
                               min="0" step="1"
                               onchange="saveField(this)" onclick="event.stopPropagation()">
                    </td>
                    <td>
                        <select class="edit-select" data-field="preferred_vendor" data-id="{{ item.id }}"
                                data-original="{{ item.preferred_vendor }}" onchange="saveField(this)" onclick="event.stopPropagation()">
                            <option value="">--</option>
                            {% for v in vendors %}
                            <option value="{{ v }}" {{ 'selected' if item.preferred_vendor == v else '' }}>{{ v }}</option>
                            {% endfor %}
                        </select>
                    </td>
                    <td onclick="event.stopPropagation()">
                        <div class="aliases-container" data-id="{{ item.id }}">
                        {% set aliases = item.aliases %}
                        {% if aliases %}
                            {% for alias in aliases|from_json_or_empty %}
                            <span class="alias-tag">{{ alias }}<span class="remove" onclick="removeAlias('{{ item.id }}', '{{ alias|e }}')">&times;</span></span>
                            {% endfor %}
                        {% endif %}
                        </div>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        </div>
    </div>
</div>

<!-- Item Detail Modal -->
<div class="modal-overlay" id="itemModal" onclick="closeModal(event)">
    <div class="modal-content" onclick="event.stopPropagation()">
        <div class="modal-header">
            <h2 id="modalTitle">Item Detail</h2>
            <button class="modal-close" onclick="closeModal()">&times;</button>
        </div>
        <div class="modal-body" id="modalBody">
            <div class="modal-loading">Loading...</div>
        </div>
        <div class="modal-footer" id="modalFooter" style="display:none;">
            <button class="btn btn-save" id="modalSave" onclick="savePackData()">Save Changes</button>
        </div>
    </div>
</div>

<script>
var currentItemId = null;
var currentPrices = [];

function filterItems() {
    var search = document.getElementById('searchBox').value.toLowerCase();
    var category = document.getElementById('categoryFilter').value;
    var rows = document.querySelectorAll('#itemsTable tbody tr');
    rows.forEach(function(row) {
        var name = row.dataset.name || '';
        var cat = row.dataset.category || '';
        var matchSearch = !search || name.includes(search) || row.textContent.toLowerCase().includes(search);
        var matchCat = !category || cat === category;
        row.style.display = (matchSearch && matchCat) ? '' : 'none';
    });
}

async function saveField(el) {
    var field = el.dataset.field;
    var itemId = el.dataset.id;
    var original = el.dataset.original || '';

    var value;
    if (el.tagName === 'SELECT') {
        value = el.value;
    } else if (el.tagName === 'INPUT' && el.type === 'number') {
        value = el.value ? parseFloat(el.value) : null;
    } else if (el.tagName === 'INPUT') {
        value = el.value;
    } else {
        value = el.textContent.trim();
    }

    if (String(value || '') === String(original)) return;

    var body = {};
    body[field] = value;

    try {
        var resp = await fetch('/prices/api/item/' + itemId, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        if (resp.ok) {
            el.dataset.original = String(value || '');
            var row = el.closest('tr');
            if (row) {
                row.classList.add('save-flash');
                setTimeout(function() { row.classList.remove('save-flash'); }, 600);
            }
            if (field === 'name' && row) row.dataset.name = (value || '').toLowerCase();
            if (field === 'category' && row) row.dataset.category = value || '';
        } else {
            alert('Save failed');
            if (el.tagName === 'SELECT') el.value = original;
            else if (el.tagName === 'INPUT') el.value = original;
            else el.textContent = original;
        }
    } catch(err) {
        alert('Save error: ' + err.message);
    }
}

async function removeAlias(itemId, alias) {
    if (!confirm('Remove alias "' + alias + '"?')) return;

    var container = document.querySelector('.aliases-container[data-id="' + itemId + '"]');
    var tags = container.querySelectorAll('.alias-tag');
    var remaining = [];
    tags.forEach(function(tag) {
        var text = tag.textContent.replace(/\\s*\\u00d7\\s*$/, '').trim();
        if (text !== alias) remaining.push(text);
    });

    var resp = await fetch('/prices/api/item/' + itemId, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({aliases: remaining}),
    });
    if (resp.ok) location.reload();
}

/* ── Item Detail Modal ── */

async function openItemDetail(rowEl) {
    var itemId = rowEl.dataset.id;
    if (!itemId) return;
    currentItemId = itemId;

    var modal = document.getElementById('itemModal');
    var body = document.getElementById('modalBody');
    var footer = document.getElementById('modalFooter');
    var title = document.getElementById('modalTitle');

    body.innerHTML = '<div class="modal-loading">Loading item details...</div>';
    footer.style.display = 'none';
    modal.classList.add('active');

    try {
        var resp = await fetch('/prices/api/item/' + itemId + '/detail');
        if (!resp.ok) throw new Error('Failed to load');
        var data = await resp.json();

        currentPrices = data.prices || [];
        title.textContent = data.item.name;

        var html = '<div class="modal-meta">' +
            'Category: <strong>' + (data.item.category || 'None') + '</strong>' +
            ' &nbsp; Preferred: <strong>' + (data.item.preferred_vendor || 'None') + '</strong>' +
            ' &nbsp; Unit: <strong>' + (data.item.unit || '?') + '</strong>' +
            '</div>';

        /* Pack breakdown section */
        var packQty = 0, eachSize = 0, sizeUnit = '';
        if (currentPrices.length > 0) {
            var pref = data.item.preferred_vendor;
            var src = currentPrices.find(function(p) { return p.vendor === pref; }) || currentPrices[0];
            packQty = src.pack_qty || 0;
            eachSize = src.each_size || 0;
            sizeUnit = src.size_unit || '';
        }

        html += '<div class="modal-section"><h3>Unit Breakdown</h3>';
        html += '<div class="pack-row">';
        html += '<div class="pack-field"><label>Pack Qty</label><input type="number" id="packQty" value="' + (packQty || '') + '" min="0" step="1" oninput="recalcPack()"></div>';
        html += '<span class="pack-operator">&times;</span>';
        html += '<div class="pack-field"><label>Each Qty</label><input type="number" id="eachSize" value="' + (eachSize || '') + '" min="0" step="0.01" oninput="recalcPack()"></div>';
        html += '<span class="pack-operator">=</span>';
        html += '<div class="pack-field"><label>Total</label><div class="pack-total" id="packTotal">0</div></div>';
        html += '</div>';
        html += '<div style="margin-top:0.6rem;">';
        html += '<div class="pack-field" style="display:inline-block;"><label>Size Unit</label>';
        html += '<input type="text" id="sizeUnit" list="sizeUnitList" style="width:100px;" value="' + sizeUnit + '" oninput="recalcPack()">';
        html += '<datalist id="sizeUnitList">';
        var unitOpts = ['each','wrap','piece','can','oz','lb','fl oz','gallon','ct','slice','portion','bag','bottle'];
        for (var i = 0; i < unitOpts.length; i++) {
            html += '<option value="' + unitOpts[i] + '">';
        }
        html += '</datalist></div>';
        html += '</div></div>';

        /* Latest prices section */
        html += '<div class="modal-section"><h3>Latest Prices</h3>';
        if (currentPrices.length === 0) {
            html += '<p class="no-prices">No price entries found for this item.</p>';
        } else {
            for (var j = 0; j < currentPrices.length; j++) {
                var p = currentPrices[j];
                html += '<div class="price-row">';
                html += '<span class="price-vendor">' + p.vendor + '</span>';
                html += '<span class="price-case">$' + p.price.toFixed(2) + '/' + (data.item.unit || 'case') + '</span>';
                html += '<span class="price-unit" data-price-idx="' + j + '">—</span>';
                html += '<span style="color:#999;font-size:0.8rem;">' + (p.week || '') + '</span>';
                html += '</div>';
            }
        }
        html += '</div>';

        /* Aliases */
        var aliases = data.item.aliases || '';
        if (aliases) {
            try {
                var aliasList = JSON.parse(aliases);
                if (aliasList.length > 0) {
                    html += '<div class="modal-section"><h3>Aliases</h3>';
                    html += '<div>' + aliasList.map(function(a) { return '<span class="alias-tag">' + a + '</span>'; }).join(' ') + '</div>';
                    html += '</div>';
                }
            } catch(e) {}
        }

        body.innerHTML = html;
        footer.style.display = '';
        recalcPack();

    } catch(err) {
        body.innerHTML = '<div style="color:#c0392b;padding:1rem;">Error loading item: ' + err.message + '</div>';
    }
}

function recalcPack() {
    var pq = parseFloat(document.getElementById('packQty').value) || 0;
    var es = parseFloat(document.getElementById('eachSize').value) || 0;
    var su = document.getElementById('sizeUnit').value || 'units';
    var total = pq * es;

    document.getElementById('packTotal').textContent = total ? (total + ' ' + su) : '0';

    /* Update per-unit prices */
    for (var i = 0; i < currentPrices.length; i++) {
        var el = document.querySelector('[data-price-idx="' + i + '"]');
        if (!el) continue;
        if (total > 0 && currentPrices[i].price) {
            var perUnit = currentPrices[i].price / total;
            el.textContent = '$' + perUnit.toFixed(perUnit < 0.1 ? 4 : 2) + '/' + su;
            el.style.color = '#475417';
        } else {
            el.textContent = '\\u2014';
            el.style.color = '#999';
        }
    }
}

async function savePackData() {
    var btn = document.getElementById('modalSave');
    btn.disabled = true;
    btn.textContent = 'Saving...';

    var packQty = parseInt(document.getElementById('packQty').value) || 0;
    var eachSize = parseFloat(document.getElementById('eachSize').value) || 0;
    var sizeUnit = document.getElementById('sizeUnit').value || '';

    var saved = 0;
    var errors = 0;

    for (var i = 0; i < currentPrices.length; i++) {
        try {
            var resp = await fetch('/prices/api/price-entry/' + currentPrices[i].entry_id + '/pack', {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({pack_qty: packQty, each_size: eachSize, size_unit: sizeUnit}),
            });
            if (resp.ok) saved++;
            else errors++;
        } catch(e) { errors++; }
    }

    /* Also update the item unit_size field with structured text */
    if (packQty && eachSize) {
        var unitSizeText = packQty + 'x' + eachSize + (sizeUnit ? ' ' + sizeUnit : '');
        await fetch('/prices/api/item/' + currentItemId, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({unit_size: unitSizeText}),
        });
        /* Update the table row too */
        var row = document.querySelector('tr[data-id="' + currentItemId + '"]');
        if (row) {
            var usInput = row.querySelector('input[data-field="unit_size"]');
            if (usInput) { usInput.value = unitSizeText; usInput.dataset.original = unitSizeText; }
        }
    }

    btn.disabled = false;
    btn.textContent = 'Save Changes';

    if (errors === 0) {
        btn.textContent = 'Saved!';
        setTimeout(function() { btn.textContent = 'Save Changes'; }, 1500);
    } else {
        alert('Saved ' + saved + ' entries, ' + errors + ' failed.');
    }
}

function closeModal(evt) {
    if (evt && evt.target && !evt.target.classList.contains('modal-overlay')) return;
    document.getElementById('itemModal').classList.remove('active');
    currentItemId = null;
    currentPrices = [];
}
</script>
</body>
</html>"""

TRENDS_HTML = """<!DOCTYPE html>
<html>
<head>
<title>Trends — Livite Vendor Prices</title>""" + STYLE + """
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
    .trend-card { margin-bottom: 1.5rem; }
    .trend-card h3 { margin-bottom: 0.5rem; color: #475417; }
    .trend-card canvas { max-height: 200px; }
    .category-header-trends { color: #475417; border-bottom: 2px solid #475417; padding-bottom: 0.5rem; margin: 2rem 0 1rem; }
    .trend-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(450px, 1fr)); gap: 1rem; }
    .filter-bar { display: flex; gap: 0.5rem; margin-bottom: 1.5rem; flex-wrap: wrap; align-items: center; }
    .filter-bar input { padding: 0.5rem; border: 2px solid #ddd; border-radius: 8px; font-size: 0.95rem; flex: 1; min-width: 200px; }
    .filter-bar select { padding: 0.5rem; border: 2px solid #ddd; border-radius: 8px; }
</style>
</head>
<body>
<div class="nav">
    <h1>Livite Vendor Prices</h1>
    <div>
        <a href="/prices/">Prices</a>
        <a href="/prices/upload">Upload</a>
        <a href="/prices/compare">Compare</a>
        <a href="/prices/trends">Trends</a>
        <a href="/prices/review">Items</a>
        <a href="/prices/history">History</a>
        <a href="/prices/spending">Spending</a>
        <a href="/prices/order-check">Order</a>
        {% if role == 'owner' %}<a href="/">Sales</a>{% endif %}
        <a href="/logout">Logout</a>
    </div>
</div>
<div class="container">
    <div class="card">
        <h2>Price Trends</h2>

        {% if not trends %}
        <p style="color:#666;">No trend data yet. Upload prices from multiple weeks to see trends.</p>
        {% else %}

        <div class="filter-bar">
            <input type="text" id="trendSearch" placeholder="Search items..." oninput="filterTrends()">
            <select id="trendCategory" onchange="filterTrends()">
                <option value="">All Categories</option>
                <option value="Protein">Protein</option>
                <option value="Produce">Produce</option>
                <option value="Dairy">Dairy</option>
                <option value="Dry Goods">Dry Goods</option>
                <option value="Beverages">Beverages</option>
                <option value="Paper & Supplies">Supplies</option>
                <option value="Cleaning">Cleaning</option>
                <option value="Equipment">Equipment</option>
                <option value="Other">Other</option>
            </select>
        </div>

        <div class="trend-grid" id="trendGrid">
            {% for item in trends %}
            <div class="card trend-card" data-name="{{ item.name|lower }}" data-category="{{ item.category }}">
                <h3>{{ item.name }}</h3>
                <p style="font-size:0.8rem; color:#888;">{{ item.category }}</p>
                <canvas id="chart-{{ loop.index }}"></canvas>
            </div>
            {% endfor %}
        </div>

        {% endif %}
    </div>
</div>

{% if trends %}
<script>
const VENDOR_COLORS = {
    'Sysco': '#1a5276',
    'Baldor': '#27ae60',
    'FreshPoint': '#e67e22',
    'Restaurant Depot': '#c0392b',
};
const weeks = {{ weeks|tojson }};
const trendsData = {{ trends|tojson }};

trendsData.forEach((item, idx) => {
    const ctx = document.getElementById('chart-' + (idx + 1));
    if (!ctx) return;

    const datasets = [];
    const vendorsSeen = new Set();
    Object.values(item.weeks).forEach(wk => Object.keys(wk).forEach(v => vendorsSeen.add(v)));

    vendorsSeen.forEach(vendor => {
        const data = weeks.map(w => {
            const wk = item.weeks[w];
            if (wk && wk[vendor]) return wk[vendor].price;
            return null;
        });
        datasets.push({
            label: vendor,
            data: data,
            borderColor: VENDOR_COLORS[vendor] || '#999',
            backgroundColor: 'transparent',
            tension: 0.3,
            spanGaps: true,
            pointRadius: 3,
        });
    });

    new Chart(ctx, {
        type: 'line',
        data: { labels: weeks, datasets },
        options: {
            responsive: true,
            plugins: { legend: { position: 'bottom', labels: { boxWidth: 12 } } },
            scales: {
                y: { beginAtZero: false, ticks: { callback: v => '$' + v.toFixed(2) } },
                x: { ticks: { maxRotation: 45 } },
            },
        },
    });
});

function filterTrends() {
    const search = document.getElementById('trendSearch').value.toLowerCase();
    const category = document.getElementById('trendCategory').value;
    document.querySelectorAll('.trend-card').forEach(card => {
        const name = card.dataset.name || '';
        const cat = card.dataset.category || '';
        const matchSearch = !search || name.includes(search);
        const matchCat = !category || cat === category;
        card.style.display = (matchSearch && matchCat) ? '' : 'none';
    });
}
</script>
{% endif %}
</body>
</html>"""

HISTORY_HTML = """<!DOCTYPE html>
<html>
<head><title>History — Livite Vendor Prices</title>""" + STYLE + """
<style>
    .stats-row { display: flex; gap: 1.5rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
    .stat-card {
        background: white; border-radius: 10px; padding: 1.2rem 1.5rem;
        box-shadow: 0 2px 6px rgba(0,0,0,0.06); min-width: 160px; flex: 1;
    }
    .stat-card .num { font-size: 1.8rem; font-weight: 700; color: #475417; }
    .stat-card .label { font-size: 0.85rem; color: #888; margin-top: 0.2rem; }
    .upload-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
    .upload-table th {
        background: #475417; color: white; padding: 0.6rem 0.8rem;
        text-align: left; position: sticky; top: 0;
    }
    .upload-table td { padding: 0.5rem 0.8rem; border-bottom: 1px solid #e0e0e0; }
    .upload-table tr:hover { background: #f5f5f5; cursor: pointer; }
    .upload-table tr.expanded { background: #f0f7e6; }
    .status-badge {
        display: inline-block; padding: 0.15rem 0.5rem; border-radius: 10px;
        font-size: 0.75rem; font-weight: 600;
    }
    .status-complete { background: #e8f5e9; color: #2e7d32; }
    .status-error { background: #ffebee; color: #c62828; }
    .status-processing { background: #fff3e0; color: #e65100; }
    .type-badge {
        display: inline-block; padding: 0.15rem 0.5rem; border-radius: 10px;
        font-size: 0.75rem; font-weight: 500; background: #e3f2fd; color: #1565c0;
    }
    .type-telegram { background: #e8eaf6; color: #283593; }
    .detail-row { display: none; }
    .detail-row.open { display: table-row; }
    .detail-row td { padding: 0; }
    .detail-content {
        padding: 1rem 1.5rem; background: #fafafa;
        border-left: 4px solid #475417; margin: 0;
    }
    .detail-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-top: 0.5rem; }
    .detail-table th { background: #f0f0f0; color: #333; padding: 0.4rem 0.6rem; text-align: left; font-weight: 600; }
    .detail-table td { padding: 0.35rem 0.6rem; border-bottom: 1px solid #eee; }
    .detail-table tr:hover { background: #fff; }
    .btn-sm {
        padding: 0.25rem 0.6rem; font-size: 0.75rem; border: none; border-radius: 4px;
        cursor: pointer; font-weight: 500;
    }
    .btn-sm-danger { background: #ffebee; color: #c62828; }
    .btn-sm-danger:hover { background: #ffcdd2; }
    .empty-state { text-align: center; padding: 3rem; color: #888; }
    .loading-detail { padding: 1rem; color: #888; font-style: italic; }
    .filter-bar { display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap; align-items: center; }
    .filter-bar select { padding: 0.5rem; border: 2px solid #ddd; border-radius: 8px; }
</style>
</head>
<body>
<div class="nav">
    <h1>Livite Vendor Prices</h1>
    <div>
        <a href="/prices/">Prices</a>
        <a href="/prices/upload">Upload</a>
        <a href="/prices/compare">Compare</a>
        <a href="/prices/trends">Trends</a>
        <a href="/prices/review">Items</a>
        <a href="/prices/history">History</a>
        <a href="/prices/spending">Spending</a>
        <a href="/prices/order-check">Order</a>
        {% if role == 'owner' %}<a href="/">Sales</a>{% endif %}
        <a href="/logout">Logout</a>
    </div>
</div>

<div class="container">
    <div class="stats-row">
        <div class="stat-card">
            <div class="num">{{ total_uploads }}</div>
            <div class="label">Total Uploads</div>
        </div>
        <div class="stat-card">
            <div class="num">{{ this_week_count }}</div>
            <div class="label">This Week</div>
        </div>
        <div class="stat-card">
            <div class="num">{{ total_items }}</div>
            <div class="label">Items Extracted</div>
        </div>
    </div>

    <div class="card">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
            <h2>Upload History</h2>
        </div>

        <div class="filter-bar">
            <select id="vendorFilter" onchange="filterUploads()">
                <option value="">All Vendors</option>
                {% for key, name in vendors.items() %}
                <option value="{{ name }}">{{ name }}</option>
                {% endfor %}
            </select>
            <select id="typeFilter" onchange="filterUploads()">
                <option value="">All Sources</option>
                <option value="web">Web Uploads</option>
                <option value="telegram">Telegram</option>
            </select>
        </div>

        {% if not uploads %}
        <div class="empty-state">
            <p>No uploads yet. <a href="/prices/upload">Upload your first price sheet</a>.</p>
        </div>
        {% else %}
        <div style="overflow-x:auto;">
        <table class="upload-table" id="uploadTable">
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Vendor</th>
                    <th>Source</th>
                    <th>Items</th>
                    <th>Matched</th>
                    <th>New</th>
                    <th>Status</th>
                    <th>Week</th>
                </tr>
            </thead>
            <tbody>
                {% for upload in uploads %}
                <tr class="upload-row" data-vendor="{{ upload.vendor }}" data-week="{{ upload.week }}"
                    data-type="{{ 'telegram' if 'Telegram' in (upload.file_type or '') else 'web' }}"
                    onclick="toggleDetail(this)">
                    <td>{{ upload.date }}</td>
                    <td><strong>{{ upload.vendor }}</strong></td>
                    <td>
                        {% if 'Telegram' in (upload.file_type or '') %}
                        <span class="type-badge type-telegram">{{ upload.file_type }}</span>
                        {% else %}
                        <span class="type-badge">{{ upload.file_type }}</span>
                        {% endif %}
                    </td>
                    <td>{{ upload.items_extracted|int }}</td>
                    <td>{{ upload.items_matched|int }}</td>
                    <td>{{ upload.items_new|int }}</td>
                    <td>
                        {% if upload.status == 'Complete' %}
                        <span class="status-badge status-complete">Complete</span>
                        {% elif upload.status == 'Error' %}
                        <span class="status-badge status-error">Error</span>
                        {% else %}
                        <span class="status-badge status-processing">{{ upload.status }}</span>
                        {% endif %}
                    </td>
                    <td>{{ upload.week }}</td>
                </tr>
                <tr class="detail-row" id="detail-{{ loop.index }}">
                    <td colspan="8">
                        <div class="detail-content">
                            <div class="loading-detail">Loading entries...</div>
                        </div>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        </div>
        {% endif %}
    </div>
</div>

<script>
function filterUploads() {
    const vendor = document.getElementById('vendorFilter').value;
    const type = document.getElementById('typeFilter').value;
    document.querySelectorAll('.upload-row').forEach(row => {
        const rowVendor = row.dataset.vendor || '';
        const rowType = row.dataset.type || '';
        const matchVendor = !vendor || rowVendor === vendor;
        const matchType = !type || rowType === type;
        row.style.display = (matchVendor && matchType) ? '' : 'none';
        // Also hide detail rows
        const nextRow = row.nextElementSibling;
        if (nextRow && nextRow.classList.contains('detail-row')) {
            nextRow.classList.remove('open');
        }
    });
}

async function toggleDetail(row) {
    const detailRow = row.nextElementSibling;
    if (!detailRow || !detailRow.classList.contains('detail-row')) return;

    // Toggle open/close
    if (detailRow.classList.contains('open')) {
        detailRow.classList.remove('open');
        row.classList.remove('expanded');
        return;
    }

    // Close other open details
    document.querySelectorAll('.detail-row.open').forEach(r => {
        r.classList.remove('open');
        r.previousElementSibling.classList.remove('expanded');
    });

    detailRow.classList.add('open');
    row.classList.add('expanded');

    const vendor = row.dataset.vendor;
    const week = row.dataset.week;
    const content = detailRow.querySelector('.detail-content');

    if (content.dataset.loaded === 'true') return;

    // Fetch entries for this vendor + week
    try {
        const resp = await fetch(`/prices/api/upload-entries/${encodeURIComponent(vendor)}/${encodeURIComponent(week)}`);
        const data = await resp.json();

        if (!data.entries || data.entries.length === 0) {
            content.innerHTML = '<p style="color:#888;">No price entries found for this upload.</p>';
            content.dataset.loaded = 'true';
            return;
        }

        let html = '<table class="detail-table"><thead><tr>' +
            '<th>Item</th><th>Vendor Name</th><th>Price</th><th>Unit</th><th>Category</th><th></th>' +
            '</tr></thead><tbody>';

        data.entries.forEach(e => {
            html += `<tr id="entry-${e.id}">` +
                `<td><strong>${esc(e.item_name)}</strong></td>` +
                `<td style="color:#888; font-size:0.8rem;">${esc(e.vendor_item_name)}</td>` +
                `<td>$${Number(e.price).toFixed(2)}</td>` +
                `<td>${esc(e.unit)}</td>` +
                `<td>${esc(e.category)}</td>` +
                `<td><button class="btn-sm btn-sm-danger" onclick="event.stopPropagation(); deleteEntry('${e.id}')">Delete</button></td>` +
                '</tr>';
        });

        html += '</tbody></table>';
        content.innerHTML = html;
        content.dataset.loaded = 'true';
    } catch (err) {
        content.innerHTML = '<p style="color:#c62828;">Failed to load entries.</p>';
    }
}

async function deleteEntry(entryId) {
    if (!confirm('Delete this price entry?')) return;
    try {
        const resp = await fetch(`/prices/api/price-entry/${entryId}`, { method: 'DELETE' });
        if (resp.ok) {
            const row = document.getElementById('entry-' + entryId);
            if (row) row.style.display = 'none';
        } else {
            alert('Failed to delete entry.');
        }
    } catch (err) {
        alert('Error: ' + err.message);
    }
}

function esc(str) {
    if (!str) return '';
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}
</script>
</body>
</html>"""


SPENDING_HTML = """<!DOCTYPE html>
<html>
<head><title>Spending — Livite Vendor Prices</title>""" + STYLE + """
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
    .kpi-row { display: flex; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
    .kpi-card {
        background: white; border-radius: 10px; padding: 1.2rem 1.5rem;
        box-shadow: 0 2px 6px rgba(0,0,0,0.06); flex: 1; min-width: 150px; text-align: center;
    }
    .kpi-card .num { font-size: 1.8rem; font-weight: 700; color: #475417; }
    .kpi-card .label { font-size: 0.8rem; color: #666; margin-top: 4px; }
    .chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-bottom: 1.5rem; }
    @media(max-width:768px) { .chart-grid { grid-template-columns: 1fr; } }
    .chart-card { background: white; border-radius: 12px; padding: 1.5rem; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
    .chart-card h3 { color: #475417; font-size: 1rem; margin-bottom: 1rem; }
    .spend-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-top: 1rem; }
    .spend-table th { background: #475417; color: white; padding: 0.5rem; text-align: left; position: sticky; top: 0; }
    .spend-table td { padding: 0.4rem 0.5rem; border-bottom: 1px solid #e8e8e8; }
    .spend-table tr:hover { background: #f5f5f5; }
    .loading-msg { text-align: center; padding: 3rem; color: #666; }
</style>
</head>
<body>
<div class="nav">
    <h1>Livite Vendor Prices</h1>
    <div>
        <a href="/prices/">Prices</a>
        <a href="/prices/upload">Upload</a>
        <a href="/prices/compare">Compare</a>
        <a href="/prices/trends">Trends</a>
        <a href="/prices/review">Items</a>
        <a href="/prices/history">History</a>
        <a href="/prices/spending" style="font-weight:700;text-decoration:underline;">Spending</a>
        {%- if role == 'owner' %} <a href="/">Sales</a>{%- endif %}
        <a href="/logout">Logout</a>
    </div>
</div>
<div class="container">
    <div id="loading" class="loading-msg">Loading spending data...</div>
    <div id="content" style="display:none;">
        <div id="kpi-row" class="kpi-row"></div>
        <div class="chart-grid">
            <div class="chart-card"><h3>Weekly Total Spend</h3><canvas id="weeklyChart" height="220"></canvas></div>
            <div class="chart-card"><h3>Spend by Category</h3><canvas id="categoryChart" height="220"></canvas></div>
        </div>
        <div class="card" style="margin-bottom:1.5rem;">
            <h3 style="color:#475417;margin-bottom:1rem;">Spend by Vendor (Stacked)</h3>
            <canvas id="vendorChart" height="250"></canvas>
        </div>
        <div class="card">
            <h3 style="color:#475417;margin-bottom:1rem;">Weekly Breakdown</h3>
            <div style="overflow-x:auto;">
                <table class="spend-table" id="breakdown-table">
                    <thead><tr><th>Week</th><th>Total</th><th>Items</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>
    </div>
</div>
<script>
const COLORS = ['#475417','#8cb82e','#4a9cd8','#e8a830','#e86040','#9b72c4','#2d8a6e','#c4725b',
                '#6b8e23','#2196f3','#ff9800','#e91e63','#607d8b'];

async function loadSpending(){
    try {
        const resp = await fetch('/prices/api/spending/data');
        if (!resp.ok) throw new Error('Server returned ' + resp.status);
        const data = await resp.json();
        if (data.error) throw new Error(data.error);
        document.getElementById('loading').style.display = 'none';
        document.getElementById('content').style.display = 'block';
        renderKPIs(data);
        renderWeeklyChart(data);
        renderCategoryChart(data);
        renderVendorChart(data);
        renderBreakdownTable(data);
    } catch(err) {
        document.getElementById('loading').textContent = 'Error: ' + err.message;
        document.getElementById('loading').style.color = '#e86040';
    }
}

function renderKPIs(data){
    const weeks = data.weekly_totals || [];
    const total = data.grand_total || 0;
    const avgWeekly = weeks.length > 0 ? total / weeks.length : 0;

    // Top vendor
    const vendors = data.by_vendor || {};
    let topVendor = '--', topVendorAmt = 0;
    for (const [v, wks] of Object.entries(vendors)){
        const vTotal = wks.reduce((s,w) => s + w.total, 0);
        if (vTotal > topVendorAmt) { topVendor = v; topVendorAmt = vTotal; }
    }

    // Top category
    const cats = data.by_category || {};
    let topCat = '--', topCatAmt = 0;
    for (const [c, wks] of Object.entries(cats)){
        const cTotal = wks.reduce((s,w) => s + w.total, 0);
        if (cTotal > topCatAmt) { topCat = c; topCatAmt = cTotal; }
    }

    const last4 = weeks.slice(-4);
    const last4Total = last4.reduce((s,w) => s + w.total, 0);

    const kpis = [
        {l: 'Last 4 Weeks', v: '$' + Math.round(last4Total).toLocaleString()},
        {l: 'Avg Weekly', v: '$' + Math.round(avgWeekly).toLocaleString()},
        {l: 'Top Vendor', v: topVendor},
        {l: 'Top Category', v: topCat},
    ];
    let h = '';
    for (const k of kpis) h += '<div class="kpi-card"><div class="num">' + k.v + '</div><div class="label">' + k.l + '</div></div>';
    document.getElementById('kpi-row').innerHTML = h;
}

function renderWeeklyChart(data){
    const weeks = data.weekly_totals || [];
    new Chart(document.getElementById('weeklyChart'), {
        type: 'line',
        data: {
            labels: weeks.map(w => w.week),
            datasets: [{
                label: 'Total Spend',
                data: weeks.map(w => w.total),
                borderColor: '#475417',
                backgroundColor: 'rgba(71,84,23,0.1)',
                fill: true,
                tension: 0.3,
                pointRadius: 4,
            }]
        },
        options: {
            responsive: true,
            scales: { y: { beginAtZero: true, ticks: { callback: v => '$' + v.toLocaleString() } } },
            plugins: { legend: { display: false } }
        }
    });
}

function renderCategoryChart(data){
    const cats = data.by_category || {};
    const labels = [], values = [];
    for (const [cat, wks] of Object.entries(cats)){
        const total = wks.reduce((s,w) => s + w.total, 0);
        if (total > 0) { labels.push(cat); values.push(Math.round(total)); }
    }
    // Sort descending
    const sorted = labels.map((l,i) => ({l, v: values[i]})).sort((a,b) => b.v - a.v);
    new Chart(document.getElementById('categoryChart'), {
        type: 'doughnut',
        data: {
            labels: sorted.map(s => s.l),
            datasets: [{ data: sorted.map(s => s.v), backgroundColor: COLORS.slice(0, sorted.length) }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'right', labels: { font: { size: 11 } } },
                tooltip: { callbacks: { label: ctx => ctx.label + ': $' + ctx.raw.toLocaleString() } }
            }
        }
    });
}

function renderVendorChart(data){
    const vendors = data.by_vendor || {};
    const weeks = data.weeks || [];
    const datasets = [];
    let ci = 0;
    for (const [vendor, wks] of Object.entries(vendors)){
        datasets.push({
            label: vendor,
            data: wks.map(w => w.total),
            backgroundColor: COLORS[ci % COLORS.length],
        });
        ci++;
    }
    new Chart(document.getElementById('vendorChart'), {
        type: 'bar',
        data: { labels: weeks, datasets },
        options: {
            responsive: true,
            scales: {
                x: { stacked: true },
                y: { stacked: true, beginAtZero: true, ticks: { callback: v => '$' + v.toLocaleString() } }
            },
            plugins: { legend: { display: true, position: 'top' } }
        }
    });
}

function renderBreakdownTable(data){
    const weeks = data.weekly_totals || [];
    const tbody = document.querySelector('#breakdown-table tbody');
    let rows = '';
    // Show most recent first
    for (let i = weeks.length - 1; i >= 0; i--){
        const w = weeks[i];
        rows += '<tr><td>' + w.week + '</td><td style="font-weight:600;">$' + w.total.toLocaleString() + '</td><td>' + w.count + '</td></tr>';
    }
    tbody.innerHTML = rows;
}

loadSpending();
</script>
</body>
</html>"""


ORDER_CHECK_HTML = """<!DOCTYPE html>
<html>
<head><title>Order Check — Livite</title>""" + STYLE + """
<style>
    .order-table { width:100%; border-collapse:collapse; }
    .order-table th { text-align:left; padding:0.6rem 0.8rem; border-bottom:2px solid #475417;
        font-size:0.75rem; text-transform:uppercase; letter-spacing:0.5px; color:#475417; }
    .order-table td { padding:0.5rem 0.8rem; border-bottom:1px solid #eee; font-size:0.85rem; }
    .order-table tr.need-order { background:#fff8f0; }
    .order-table tr.good { opacity:0.5; }
    .on-hand-input { width:60px; padding:0.3rem 0.5rem; border:1.5px solid #ddd;
        border-radius:6px; font-size:0.85rem; text-align:center; font-family:inherit; }
    .on-hand-input:focus { outline:none; border-color:#475417; box-shadow:0 0 0 2px rgba(71,84,23,0.1); }
    .order-qty { font-weight:700; color:#475417; font-size:0.95rem; }
    .order-qty.zero { color:#999; font-weight:400; }
    .cat-header td { background:#f5f0e5; font-weight:600; color:#475417; font-size:0.8rem;
        text-transform:uppercase; letter-spacing:0.5px; padding:0.7rem 0.8rem; }
    .summary-bar { display:flex; gap:1.5rem; margin-bottom:1.5rem; flex-wrap:wrap; }
    .summary-stat { background:#f5f0e5; padding:0.8rem 1.2rem; border-radius:8px; text-align:center; }
    .summary-stat .num { font-size:1.5rem; font-weight:700; color:#475417; }
    .summary-stat .label { font-size:0.7rem; color:#888; text-transform:uppercase; letter-spacing:0.5px; }
    .order-list-btn { background:#475417; color:white; border:none; padding:0.7rem 1.5rem;
        border-radius:8px; font-size:0.85rem; font-weight:600; cursor:pointer; font-family:inherit; }
    .order-list-btn:hover { background:#5a6e1e; }
    .order-list { display:none; background:#f9f7f2; border-radius:8px; padding:1.2rem;
        margin-top:1rem; font-size:0.85rem; white-space:pre-wrap; font-family:'JetBrains Mono',monospace; }
</style>
</head>
<body>
<div class="nav">
    <h1>Livite Vendor Prices</h1>
    <div>
        <a href="/prices/">Prices</a>
        <a href="/prices/upload">Upload</a>
        <a href="/prices/compare">Compare</a>
        <a href="/prices/trends">Trends</a>
        <a href="/prices/review">Items</a>
        <a href="/prices/history">History</a>
        <a href="/prices/spending">Spending</a>
        <a href="/prices/order-check">Order</a>
        {%- if session.get('role') == 'owner' %} <a href="/">Sales</a>{%- endif %}
        <a href="/logout">Logout</a>
    </div>
</div>
<div class="container">
    <div class="card">
        <h2>Order Check</h2>
        <p style="color:#666;margin-bottom:1rem;font-size:0.85rem;">
            Enter what you have on hand. Items below par level will show how many to order.
        </p>

        <div class="summary-bar">
            <div class="summary-stat">
                <div class="num" id="needCount">0</div>
                <div class="label">Items to Order</div>
            </div>
            <div class="summary-stat">
                <div class="num" id="totalItems">{{ items|length }}</div>
                <div class="label">Items Tracked</div>
            </div>
            <div>
                <button class="order-list-btn" onclick="generateList()">Generate Order List</button>
                <button class="order-list-btn" onclick="clearAll()" style="background:#999;">Clear All</button>
            </div>
        </div>

        <div class="order-list" id="orderList"></div>

        <table class="order-table">
            <thead>
                <tr>
                    <th>Item</th>
                    <th>Unit</th>
                    <th>Par</th>
                    <th>On Hand</th>
                    <th>Order</th>
                    <th>Vendor</th>
                </tr>
            </thead>
            <tbody id="orderBody">
            {% set current_cat = namespace(val='') %}
            {% for item in items %}
                {% if item.category != current_cat.val %}
                    {% set current_cat.val = item.category %}
                    <tr class="cat-header"><td colspan="6">{{ item.category or 'Other' }}</td></tr>
                {% endif %}
                <tr data-par="{{ item.par_level }}" data-name="{{ item.name }}"
                    data-unit="{{ item.unit or '' }}" data-vendor="{{ item.preferred_vendor or '' }}">
                    <td>{{ item.name }}</td>
                    <td style="color:#888;">{{ item.unit_size or '' }} {{ item.unit or '' }}</td>
                    <td style="font-weight:600;">{{ item.par_level }}</td>
                    <td><input type="number" class="on-hand-input" min="0" step="1"
                        placeholder="-" oninput="calcRow(this)"></td>
                    <td class="order-qty zero">-</td>
                    <td style="color:#888;font-size:0.8rem;">{{ item.preferred_vendor or '' }}</td>
                </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
</div>

<script>
function calcRow(input) {
    const tr = input.closest('tr');
    const par = parseFloat(tr.dataset.par) || 0;
    const onHand = parseFloat(input.value);
    const orderCell = tr.querySelector('.order-qty');

    if (isNaN(onHand) || input.value === '') {
        orderCell.textContent = '-';
        orderCell.classList.add('zero');
        tr.classList.remove('need-order', 'good');
    } else {
        const need = Math.max(0, par - onHand);
        if (need > 0) {
            orderCell.textContent = need;
            orderCell.classList.remove('zero');
            tr.classList.add('need-order');
            tr.classList.remove('good');
        } else {
            orderCell.textContent = '0';
            orderCell.classList.add('zero');
            tr.classList.remove('need-order');
            tr.classList.add('good');
        }
    }
    updateSummary();
}

function updateSummary() {
    const rows = document.querySelectorAll('tr[data-par]');
    let needCount = 0;
    rows.forEach(tr => {
        if (tr.classList.contains('need-order')) needCount++;
    });
    document.getElementById('needCount').textContent = needCount;
}

function generateList() {
    const rows = document.querySelectorAll('tr.need-order');
    if (rows.length === 0) {
        alert('No items need ordering. Enter on-hand counts first.');
        return;
    }
    // Group by vendor
    const byVendor = {};
    rows.forEach(tr => {
        const vendor = tr.dataset.vendor || 'No Vendor';
        const name = tr.dataset.name;
        const unit = tr.dataset.unit;
        const qty = tr.querySelector('.order-qty').textContent;
        if (!byVendor[vendor]) byVendor[vendor] = [];
        byVendor[vendor].push(name + ' — ' + qty + (unit ? ' ' + unit : ''));
    });

    let text = '';
    for (const [vendor, items] of Object.entries(byVendor)) {
        text += vendor.toUpperCase() + ':\\n';
        items.forEach(item => { text += '  ' + item + '\\n'; });
        text += '\\n';
    }

    const el = document.getElementById('orderList');
    el.textContent = text.trim();
    el.style.display = 'block';
}

function clearAll() {
    document.querySelectorAll('.on-hand-input').forEach(input => {
        input.value = '';
        calcRow(input);
    });
    document.getElementById('orderList').style.display = 'none';
}
</script>
</body>
</html>"""


# ── Email Invoice Routes ──

@bp.route("/invoices")
def invoices_page():
    """Pending email invoices + poller status."""
    try:
        from tools.gmail_service.vendor_invoice_poller import poller as vip
    except Exception as e:
        return render_template_string(
            f"""<!DOCTYPE html><html><head><title>Invoices</title>{STYLE}</head>
            <body><div class="nav"><h1>Livite Vendor Prices</h1></div>
            <div class="container"><div class="card">
            <h2>Email Invoices</h2>
            <p style="color:#d9342b;">Module error: {e}</p>
            </div></div></body></html>""")

    pending = vip.get_pending_invoices()
    last_poll = vip.last_poll.strftime("%Y-%m-%d %H:%M:%S") if vip.last_poll else "Never"
    status_color = "#4a7c1f" if vip.running else "#d9342b"
    status_text = "Running" if vip.running else "Stopped"

    log_html = ""
    for entry in reversed(vip.activity_log):
        log_html += "<div style='padding:4px 0;border-bottom:1px solid #eee;font-size:0.85rem;'>%s</div>" % entry

    error_html = ""
    for entry in reversed(vip.errors):
        error_html += "<div style='padding:4px 0;border-bottom:1px solid #eee;font-size:0.85rem;color:#d9342b;'>%s</div>" % entry

    invoice_rows = ""
    for inv in pending:
        invoice_rows += """
        <tr>
            <td style="font-weight:600;">%s</td>
            <td>%s</td>
            <td>%d items</td>
            <td style="font-size:0.85rem;color:#666;">%s</td>
            <td>
                <a href="/prices/invoices/%s/review" class="btn" style="font-size:0.8rem;padding:4px 12px;">Review</a>
                <button onclick="dismissInvoice('%s')" class="btn" style="font-size:0.8rem;padding:4px 12px;background:#999;">Dismiss</button>
            </td>
        </tr>""" % (
            inv["vendor"], ", ".join(inv["filenames"]), inv["item_count"],
            inv["email_date"][:16] if inv["email_date"] else "",
            inv["batch_id"], inv["batch_id"])

    return render_template_string(INVOICES_HTML,
                                  pending=pending,
                                  invoice_rows=invoice_rows,
                                  status_color=status_color,
                                  status_text=status_text,
                                  last_poll=last_poll,
                                  log_html=log_html,
                                  error_html=error_html,
                                  running=vip.running,
                                  processed=len(vip.processed_ids))


@bp.route("/invoices/<batch_id>/review")
def invoice_review(batch_id):
    """Redirect to upload page with pending email invoice pre-loaded."""
    pending_file = PENDING_DIR / "%s.json" % batch_id
    if not pending_file.exists():
        return redirect(url_for("vendor_prices.invoices_page"))
    return redirect(url_for("vendor_prices.upload_page", batch=batch_id))


@bp.route("/api/invoices/<batch_id>/dismiss", methods=["POST"])
def api_dismiss_invoice(batch_id):
    """Remove a pending email invoice batch."""
    pending_file = PENDING_DIR / "%s.json" % batch_id
    if pending_file.exists():
        pending_file.unlink()
    try:
        from tools.gmail_service.vendor_invoice_poller import poller as vip
        vip.pending_count = vip._count_pending()
    except Exception as e:
        logger.debug("Could not refresh invoice poller count: %s", e)
    return {"ok": True}


@bp.route("/invoices/poll", methods=["POST"])
def invoices_poll_now():
    """Trigger immediate poll."""
    from tools.gmail_service.vendor_invoice_poller import poller as vip
    try:
        vip.poll_once()
    except Exception as e:
        logger.warning("Manual invoice poll failed: %s", e)
    return redirect(url_for("vendor_prices.invoices_page"))


@bp.route("/invoices/start", methods=["POST"])
def invoices_start():
    """Start the vendor invoice poller."""
    from tools.gmail_service.vendor_invoice_poller import poller as vip
    vip.start()
    return redirect(url_for("vendor_prices.invoices_page"))


@bp.route("/invoices/stop", methods=["POST"])
def invoices_stop():
    """Stop the vendor invoice poller."""
    from tools.gmail_service.vendor_invoice_poller import poller as vip
    vip.stop()
    return redirect(url_for("vendor_prices.invoices_page"))


INVOICES_HTML = """<!DOCTYPE html>
<html>
<head><title>Email Invoices — Livite Vendor Prices</title>""" + STYLE + """</head>
<body>
<div class="nav">
    <h1>Livite Vendor Prices</h1>
    <div>
        <a href="/prices/">Prices</a>
        <a href="/prices/upload">Upload</a>
        <a href="/prices/compare">Compare</a>
        <a href="/prices/trends">Trends</a>
        <a href="/prices/review">Items</a>
        <a href="/prices/history">History</a>
        <a href="/prices/spending">Spending</a>
        <a href="/prices/order-check">Order</a>
        <a href="/prices/invoices" style="font-weight:700;">Invoices</a>
        {% if role == 'owner' %}<a href="/">Sales</a>{% endif %}
        <a href="/logout">Logout</a>
    </div>
</div>
<div class="container">
    <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;">
            <h2 style="margin:0;">Email Invoice Poller</h2>
            <div style="display:flex;align-items:center;gap:1rem;">
                <span style="display:inline-flex;align-items:center;gap:6px;">
                    <span style="width:10px;height:10px;border-radius:50%;background:{{ status_color }};display:inline-block;"></span>
                    <span style="font-weight:600;">{{ status_text }}</span>
                </span>
                <span style="font-size:0.85rem;color:#666;">Last poll: {{ last_poll }}</span>
                <span style="font-size:0.85rem;color:#666;">Processed: {{ processed }}</span>
            </div>
        </div>

        <div style="display:flex;gap:0.5rem;margin-bottom:1.5rem;">
            <form method="POST" action="/prices/invoices/poll" style="display:inline;">
                <button type="submit" class="btn" style="font-size:0.85rem;">Poll Now</button>
            </form>
            {% if not running %}
            <form method="POST" action="/prices/invoices/start" style="display:inline;">
                <button type="submit" class="btn" style="font-size:0.85rem;background:#475417;">Start Poller</button>
            </form>
            {% endif %}
            {% if running %}
            <form method="POST" action="/prices/invoices/stop" style="display:inline;">
                <button type="submit" class="btn" style="font-size:0.85rem;background:#d9342b;">Stop Poller</button>
            </form>
            {% endif %}
        </div>
    </div>

    {% if pending|length > 0 %}
    <div class="card">
        <h2>Pending Invoices ({{ pending|length }})</h2>
        <table style="width:100%;border-collapse:collapse;">
            <thead>
                <tr style="text-align:left;border-bottom:2px solid #ddd;">
                    <th style="padding:8px;">Vendor</th>
                    <th style="padding:8px;">File</th>
                    <th style="padding:8px;">Items</th>
                    <th style="padding:8px;">Date</th>
                    <th style="padding:8px;">Actions</th>
                </tr>
            </thead>
            <tbody>{{ invoice_rows|safe }}</tbody>
        </table>
    </div>
    {% else %}
    <div class="card" style="text-align:center;color:#666;padding:3rem;">
        No pending email invoices. The poller checks your inbox for vendor invoices and queues them here for review.
    </div>
    {% endif %}

    {% if log_html %}
    <div class="card">
        <h2 style="font-size:1rem;">Activity Log</h2>
        <div style="max-height:300px;overflow-y:auto;">{{ log_html|safe }}</div>
    </div>
    {% endif %}

    {% if error_html %}
    <div class="card">
        <h2 style="font-size:1rem;color:#d9342b;">Errors</h2>
        <div style="max-height:200px;overflow-y:auto;">{{ error_html|safe }}</div>
    </div>
    {% endif %}
</div>

<script>
async function dismissInvoice(batchId) {
    if (!confirm('Dismiss this invoice? It will be permanently removed.')) return;
    await fetch('/prices/api/invoices/' + batchId + '/dismiss', {method: 'POST'});
    location.reload();
}
</script>
</body>
</html>"""
