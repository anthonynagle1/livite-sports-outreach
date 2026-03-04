"""Invoice storage — JSON-based CRUD for invoice records.

Storage layout:
    .tmp/invoices/_index.json           Lightweight browse index
    .tmp/invoices/{id}.json             Individual invoice records
    .tmp/invoices/files/{week}/{vendor}/ Uploaded originals (PDFs, images)
    .tmp/invoices/archive/              Invoices older than 6 months
"""

import json
import logging
import os
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_INVOICES_DIR = _ROOT / ".tmp" / "invoices"
_FILES_DIR = _INVOICES_DIR / "files"
_ARCHIVE_DIR = _INVOICES_DIR / "archive"
_INDEX_PATH = _INVOICES_DIR / "_index.json"


def _ensure_dirs():
    _INVOICES_DIR.mkdir(parents=True, exist_ok=True)
    _FILES_DIR.mkdir(parents=True, exist_ok=True)


def _generate_id():
    return "inv_" + uuid.uuid4().hex[:8]


def _iso_week(date_str):
    """Convert YYYY-MM-DD date string to ISO week string like 2026-W09."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    except (ValueError, TypeError):
        return ""


def _load_index():
    if _INDEX_PATH.exists():
        try:
            with open(_INDEX_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return []


def _save_index(index):
    _ensure_dirs()
    with open(_INDEX_PATH, "w") as f:
        json.dump(index, f, indent=2)


def _index_entry(inv):
    """Build a lightweight index entry from a full invoice record."""
    return {
        "id": inv["id"],
        "vendor": inv.get("vendor", ""),
        "invoice_number": inv.get("invoice_number", ""),
        "invoice_date": inv.get("invoice_date", ""),
        "due_date": inv.get("due_date"),
        "total": inv.get("total", 0),
        "status": inv.get("status", "unpaid"),
        "location": inv.get("location", "brookline"),
        "week": inv.get("week", ""),
        "line_item_count": len(inv.get("line_items", [])),
        "has_alerts": any(
            li.get("price_alert") for li in inv.get("line_items", [])
        ),
    }


# ── CRUD ──────────────────────────────────────────────────────────


def create_invoice(data):
    """Create a new invoice record.

    Args:
        data: dict with keys: vendor, vendor_key, invoice_number, invoice_date,
              due_date, total, calculated_total, line_items, source_files,
              notes, location, status, payment_method

    Returns:
        Full invoice dict with generated id and timestamps.
    """
    _ensure_dirs()
    now = datetime.now().isoformat(timespec="seconds")
    inv = {
        "id": _generate_id(),
        "vendor": data.get("vendor", ""),
        "vendor_key": data.get("vendor_key", ""),
        "invoice_number": data.get("invoice_number", ""),
        "invoice_date": data.get("invoice_date", ""),
        "due_date": data.get("due_date"),
        "received_date": now[:10],
        "total": float(data.get("total", 0) or 0),
        "calculated_total": float(data.get("calculated_total", 0) or 0),
        "status": data.get("status", "unpaid"),
        "paid_date": None,
        "payment_method": data.get("payment_method"),
        "location": data.get("location", "brookline"),
        "week": _iso_week(data.get("invoice_date", "")),
        "source_files": data.get("source_files", []),
        "line_items": data.get("line_items", []),
        "notes": data.get("notes", ""),
        "created_at": now,
        "updated_at": now,
    }

    # Save invoice file
    path = _INVOICES_DIR / f"{inv['id']}.json"
    with open(path, "w") as f:
        json.dump(inv, f, indent=2)

    # Update index
    index = _load_index()
    index.insert(0, _index_entry(inv))
    _save_index(index)

    logger.info("Created invoice %s: %s %s $%.2f",
                inv["id"], inv["vendor"], inv["invoice_number"], inv["total"])
    return inv


def get_invoice(invoice_id):
    """Load a single invoice by ID. Returns dict or None."""
    path = _INVOICES_DIR / f"{invoice_id}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def update_invoice(invoice_id, **kwargs):
    """Partial update of an invoice record.

    Common updates: status, paid_date, payment_method, notes, line_items.
    Returns updated invoice or None if not found.
    """
    inv = get_invoice(invoice_id)
    if not inv:
        return None

    for key, value in kwargs.items():
        if key in inv:
            inv[key] = value

    inv["updated_at"] = datetime.now().isoformat(timespec="seconds")

    # Re-derive week if invoice_date changed
    if "invoice_date" in kwargs:
        inv["week"] = _iso_week(inv["invoice_date"])

    # Save
    path = _INVOICES_DIR / f"{invoice_id}.json"
    with open(path, "w") as f:
        json.dump(inv, f, indent=2)

    # Update index
    index = _load_index()
    index = [e for e in index if e["id"] != invoice_id]
    index.insert(0, _index_entry(inv))
    _save_index(index)

    return inv


def delete_invoice(invoice_id):
    """Soft-delete: remove from index, keep file for audit trail.

    Returns True if deleted, False if not found.
    """
    index = _load_index()
    before = len(index)
    index = [e for e in index if e["id"] != invoice_id]
    if len(index) == before:
        return False

    _save_index(index)

    # Mark as deleted in the invoice file
    inv = get_invoice(invoice_id)
    if inv:
        inv["status"] = "deleted"
        inv["updated_at"] = datetime.now().isoformat(timespec="seconds")
        path = _INVOICES_DIR / f"{invoice_id}.json"
        with open(path, "w") as f:
            json.dump(inv, f, indent=2)

    return True


def list_invoices(vendor=None, status=None, week=None, location=None,
                  limit=50, offset=0):
    """List invoices from the index with optional filters.

    Returns (items, total_count) tuple.
    """
    index = _load_index()

    if vendor:
        index = [e for e in index if e.get("vendor", "").lower() == vendor.lower()]
    if status:
        index = [e for e in index if e.get("status") == status]
    if week:
        index = [e for e in index if e.get("week") == week]
    if location:
        index = [e for e in index if e.get("location") == location]

    total = len(index)
    # Sort by invoice_date descending
    index.sort(key=lambda e: e.get("invoice_date", ""), reverse=True)
    items = index[offset:offset + limit]
    return items, total


# ── File Storage ──────────────────────────────────────────────────


def save_upload_file(file_obj, vendor_key, week):
    """Save an uploaded file (PDF/image) to the files directory.

    Args:
        file_obj: Flask FileStorage object
        vendor_key: e.g. "sysco"
        week: ISO week string e.g. "2026-W09"

    Returns:
        dict with filename, path, file_type, size_bytes
    """
    dest_dir = _FILES_DIR / week / vendor_key
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize filename
    original = file_obj.filename or "invoice"
    safe_name = "".join(c for c in original if c.isalnum() or c in ".-_ ").strip()
    if not safe_name:
        safe_name = "invoice"

    # Avoid collisions
    dest = dest_dir / safe_name
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        safe_name = f"{stem}_{uuid.uuid4().hex[:4]}{suffix}"
        dest = dest_dir / safe_name

    file_obj.save(str(dest))
    size = dest.stat().st_size

    # Relative path from project root for portability
    rel_path = str(dest.relative_to(_ROOT))

    ext = dest.suffix.lower().lstrip(".")
    file_type = "pdf" if ext == "pdf" else "image"

    return {
        "filename": safe_name,
        "path": rel_path,
        "file_type": file_type,
        "size_bytes": size,
    }


def get_file_path(invoice_id, file_idx):
    """Get the absolute path to an invoice's uploaded file.

    Returns Path or None.
    """
    inv = get_invoice(invoice_id)
    if not inv:
        return None

    files = inv.get("source_files", [])
    if file_idx < 0 or file_idx >= len(files):
        return None

    rel = files[file_idx].get("path", "")
    if not rel:
        return None

    full = _ROOT / rel
    return full if full.exists() else None


# ── Aggregation ───────────────────────────────────────────────────


def get_weekly_totals(weeks=12):
    """Aggregate invoice totals by ISO week.

    Returns dict: {week_str: {total, invoice_count, vendors: [...]}}
    Sorted newest first, limited to last N weeks.
    """
    index = _load_index()
    by_week = {}

    for entry in index:
        if entry.get("status") == "deleted":
            continue
        week = entry.get("week", "")
        if not week:
            continue
        if week not in by_week:
            by_week[week] = {"total": 0, "invoice_count": 0, "vendors": set()}
        by_week[week]["total"] += entry.get("total", 0)
        by_week[week]["invoice_count"] += 1
        by_week[week]["vendors"].add(entry.get("vendor", ""))

    # Sort and limit
    sorted_weeks = sorted(by_week.keys(), reverse=True)[:weeks]
    result = {}
    for w in sorted_weeks:
        data = by_week[w]
        result[w] = {
            "total": round(data["total"], 2),
            "invoice_count": data["invoice_count"],
            "vendors": sorted(data["vendors"]),
        }
    return result


def get_week_total(week_str):
    """Get purchase total for a specific ISO week.

    Returns dict: {total, invoice_count, vendors}
    """
    totals = get_weekly_totals(weeks=52)
    return totals.get(week_str, {"total": 0, "invoice_count": 0, "vendors": []})


# ── Duplicate Detection ──────────────────────────────────────────


def find_duplicate(vendor, invoice_number, invoice_date):
    """Check if an invoice with the same vendor + number + date already exists.

    Returns the existing index entry or None.
    """
    if not invoice_number:
        return None

    index = _load_index()
    for entry in index:
        if (entry.get("vendor", "").lower() == vendor.lower()
                and entry.get("invoice_number", "") == invoice_number
                and entry.get("invoice_date", "") == invoice_date
                and entry.get("status") != "deleted"):
            return entry
    return None


# ── Storage Management ────────────────────────────────────────────


def get_storage_stats():
    """Calculate storage usage for the invoices directory.

    Returns dict: {total_files, total_size_mb, invoice_count, oldest_date}
    """
    total_files = 0
    total_size = 0
    oldest = None

    if _FILES_DIR.exists():
        for f in _FILES_DIR.rglob("*"):
            if f.is_file():
                total_files += 1
                total_size += f.stat().st_size

    index = _load_index()
    active_count = sum(1 for e in index if e.get("status") != "deleted")

    dates = [e.get("invoice_date", "") for e in index
             if e.get("invoice_date") and e.get("status") != "deleted"]
    if dates:
        oldest = min(dates)

    return {
        "total_files": total_files,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "invoice_count": active_count,
        "oldest_date": oldest,
    }


def archive_old_invoices(months=6):
    """Move invoices older than N months to archive directory.

    Keeps index entries (marked as archived) but moves JSON and files
    to save space. Returns count of archived invoices.
    """
    cutoff = (datetime.now() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    index = _load_index()
    archived = 0

    for entry in index:
        if entry.get("status") == "deleted":
            continue
        inv_date = entry.get("invoice_date", "")
        if not inv_date or inv_date >= cutoff:
            continue

        inv_id = entry["id"]
        src = _INVOICES_DIR / f"{inv_id}.json"
        dst = _ARCHIVE_DIR / f"{inv_id}.json"

        if src.exists():
            shutil.move(str(src), str(dst))
            archived += 1
            logger.info("Archived invoice %s (date: %s)", inv_id, inv_date)

    if archived:
        logger.info("Archived %d invoice(s) older than %s", archived, cutoff)

    return archived


def rebuild_index():
    """Rebuild the index from all invoice JSON files on disk.

    Useful if the index gets corrupted or out of sync.
    """
    _ensure_dirs()
    index = []

    for f in _INVOICES_DIR.glob("inv_*.json"):
        try:
            with open(f) as fh:
                inv = json.load(fh)
            if inv.get("status") != "deleted":
                index.append(_index_entry(inv))
        except (json.JSONDecodeError, IOError):
            continue

    index.sort(key=lambda e: e.get("invoice_date", ""), reverse=True)
    _save_index(index)
    logger.info("Rebuilt invoice index: %d entries", len(index))
    return len(index)
