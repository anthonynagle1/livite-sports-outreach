"""Background writer for batch Notion price entry uploads.

Uses ThreadPoolExecutor to parallelize Notion API writes with progress tracking.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from vendor_prices.tools import notion_sync

logger = logging.getLogger(__name__)

PROGRESS_DIR = Path(".tmp/vp_progress")
PROGRESS_DIR.mkdir(parents=True, exist_ok=True)

MAX_WORKERS = 5  # ~3 req/sec to stay within Notion rate limits
MAX_RETRIES = 3
BASE_BACKOFF = 1.0  # seconds


def _progress_path(batch_id: str) -> Path:
    return PROGRESS_DIR / f"{batch_id}.json"


def _write_progress(batch_id: str, data: dict):
    _progress_path(batch_id).write_text(json.dumps(data, default=str))


def get_progress(batch_id: str) -> dict | None:
    """Read current progress for a batch."""
    path = _progress_path(batch_id)
    if path.exists():
        return json.loads(path.read_text())
    return None


def _write_one_item(
    item: dict,
    idx: int,
    vendor: str,
    upload_type: str,
    source_file: str,
    items_db_id: str,
    prices_db_id: str,
    master_items: list[dict],
    category_map: dict,
) -> dict:
    """Write a single item to Notion (create item if new + write price entry).

    Returns a result dict with status and counts.
    """
    result = {"idx": idx, "ok": False, "new": False, "matched": False, "error": ""}
    item_label = item.get("item_name", "unknown")

    master_id = item.get("master_item_id")
    status = item.get("status", "new")

    # Create new master item
    if status == "new" and not master_id:
        category = category_map.get(
            (item.get("suggested_category") or item.get("category_hint") or "other").lower(),
            "Other",
        )
        unit = item.get("unit", "case")
        unit_size = item.get("unit_detail", "")
        aliases = [item["item_name"]]
        for attempt in range(MAX_RETRIES):
            try:
                master_id = notion_sync.create_item(
                    items_db_id,
                    name=item.get("master_item_name") or item.get("suggested_name") or item["item_name"],
                    category=category,
                    unit=unit,
                    unit_size=unit_size,
                    aliases=aliases,
                )
                result["new"] = True
                break
            except Exception as exc:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BASE_BACKOFF * (2 ** attempt))
                else:
                    result["error"] = f"Failed to create '{item_label}': {exc}"
                    return result

    elif status in ("matched", "review") and master_id:
        # Add alias
        _add_alias_safe(master_id, item["item_name"], master_items)
        result["matched"] = True

    # Write price entry
    if master_id and item.get("price"):
        qty = item.get("quantity", 1) or 1
        if upload_type == "Price Update":
            qty = 1
        total_cost = item["price"] * qty

        for attempt in range(MAX_RETRIES):
            try:
                notion_sync.archive_duplicate_entries(
                    prices_db_id, master_id, vendor,
                    notion_sync.get_current_week(),
                )
                notion_sync.add_price_entry(
                    prices_db_id,
                    item_page_id=master_id,
                    vendor=vendor,
                    price=item["price"],
                    unit=item.get("unit_detail", "") or item.get("unit", ""),
                    price_per_unit=item.get("comparable_price", 0),
                    vendor_item_name=item.get("item_name", ""),
                    vendor_item_code=item.get("item_code", ""),
                    source_file=source_file,
                    quantity=qty,
                    total_cost=total_cost,
                    pack_qty=item.get("pack_qty", 0),
                    each_size=item.get("each_size", 0),
                    size_unit=item.get("size_unit", ""),
                    upload_type=upload_type,
                )
                result["ok"] = True
                break
            except Exception as exc:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BASE_BACKOFF * (2 ** attempt))
                else:
                    result["error"] = f"Failed to save price for '{item_label}': {exc}"
    elif not master_id:
        result["error"] = result["error"] or f"No master ID for '{item_label}'"
    else:
        # No price — still count as ok (item was created/matched)
        result["ok"] = True

    return result


def _add_alias_safe(master_id: str, vendor_item_name: str, master_items: list[dict]):
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
                try:
                    notion_sync.update_item_aliases(master_id, aliases)
                except Exception as exc:
                    logger.warning("Failed to add alias for %s: %s", master_id, exc)
            break


def run_batch_write(
    batch_id: str,
    approved_items: list[dict],
    vendor: str,
    upload_type: str,
    source_file: str,
    upload_id: str | None,
    items_db_id: str,
    prices_db_id: str,
    uploads_db_id: str,
    category_map: dict,
):
    """Write all approved items to Notion in parallel with progress tracking.

    This runs in a background thread — call via threading.Thread.
    """
    # Filter out excluded items
    active_items = [it for it in approved_items if not it.get("excluded")]
    total = len(active_items)

    progress = {
        "batch_id": batch_id,
        "total": total,
        "completed": 0,
        "written": 0,
        "matched": 0,
        "new": 0,
        "failed": 0,
        "errors": [],
        "status": "running",
    }
    _write_progress(batch_id, progress)

    master_items = notion_sync.get_all_items(items_db_id)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}
        for idx, item in enumerate(active_items):
            future = pool.submit(
                _write_one_item,
                item, idx, vendor, upload_type, source_file,
                items_db_id, prices_db_id, master_items, category_map,
            )
            futures[future] = idx

        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                result = {"idx": futures[future], "ok": False, "error": str(exc)}

            progress["completed"] += 1
            if result.get("ok"):
                progress["written"] += 1
            if result.get("new"):
                progress["new"] += 1
            if result.get("matched"):
                progress["matched"] += 1
            if result.get("error"):
                progress["failed"] += 1
                progress["errors"].append(result["error"])

            _write_progress(batch_id, progress)

    # Final status
    progress["status"] = "done" if progress["failed"] == 0 else "partial"
    _write_progress(batch_id, progress)

    # Update upload log
    if upload_id:
        try:
            notion_sync.update_upload_log(
                upload_id,
                items_extracted=total,
                items_matched=progress["matched"],
                items_new=progress["new"],
                status="Complete" if progress["failed"] == 0 else "Partial",
            )
        except Exception as exc:
            logger.error("Failed to update upload log: %s", exc)

    logger.info(
        "Batch %s complete: %d/%d written, %d new, %d matched, %d failed",
        batch_id, progress["written"], total, progress["new"],
        progress["matched"], progress["failed"],
    )


def start_batch_write(
    batch_id: str,
    approved_items: list[dict],
    vendor: str,
    upload_type: str,
    source_file: str,
    upload_id: str | None,
    items_db_id: str,
    prices_db_id: str,
    uploads_db_id: str,
    category_map: dict,
):
    """Start a background batch write. Returns immediately."""
    thread = threading.Thread(
        target=run_batch_write,
        args=(
            batch_id, approved_items, vendor, upload_type, source_file,
            upload_id, items_db_id, prices_db_id, uploads_db_id, category_map,
        ),
        daemon=True,
    )
    thread.start()
    return thread
