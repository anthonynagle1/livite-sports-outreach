"""Invoice Tracking Blueprint routes."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from flask import jsonify, redirect, render_template_string, request, send_file, session

from invoices import bp
from invoices.tools.invoice_store import (
    create_invoice,
    delete_invoice,
    find_duplicate,
    get_file_path,
    get_invoice,
    get_storage_stats,
    get_weekly_totals,
    list_invoices,
    update_invoice,
)

logger = logging.getLogger(__name__)

VENDORS = {
    "sysco": "Sysco",
    "baldor": "Baldor",
    "freshpoint": "FreshPoint",
    "restaurant_depot": "Restaurant Depot",
    "other": "Other",
}


@bp.before_request
def require_auth():
    if not session.get("authenticated"):
        return redirect("/login")
    if session.get("role") != "owner":
        return redirect("/")


# ── Helpers ───────────────────────────────────────────────────────


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _current_week():
    dt = datetime.now()
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _fmt_money(val):
    try:
        return f"${float(val):,.2f}"
    except (ValueError, TypeError):
        return "$0.00"


# ── Pages ─────────────────────────────────────────────────────────


@bp.route("/")
def index_page():
    """Invoice list with filters and summary cards."""
    vendor_filter = request.args.get("vendor", "")
    status_filter = request.args.get("status", "")
    invoices, total = list_invoices(
        vendor=vendor_filter or None,
        status=status_filter or None,
        limit=100,
    )

    # Summary stats
    all_inv, _ = list_invoices(limit=500)
    unpaid_total = sum(e["total"] for e in all_inv if e.get("status") == "unpaid")
    current_week = _current_week()
    week_total = sum(e["total"] for e in all_inv if e.get("week") == current_week)
    alert_count = sum(1 for e in all_inv if e.get("has_alerts"))

    # Vendor options for filter
    vendor_opts = "".join(
        '<option value="%s"%s>%s</option>'
        % (k, ' selected' if vendor_filter == k else '', v)
        for k, v in VENDORS.items()
    )

    # Invoice table rows
    rows = ""
    for inv in invoices:
        status = inv.get("status", "unpaid")
        badge_color = {"unpaid": "#e67e22", "paid": "#27ae60", "disputed": "#c0392b"}.get(status, "#999")
        alert_dot = '<span style="color:#e67e22;font-weight:700;" title="Price alerts">!</span> ' if inv.get("has_alerts") else ""
        rows += """<tr onclick="window.location='/invoices/%s'" style="cursor:pointer;">
  <td>%s</td>
  <td style="font-weight:500;">%s</td>
  <td style="font-family:'JetBrains Mono',monospace;font-size:0.85rem;">%s</td>
  <td style="text-align:center;">%s%d</td>
  <td style="text-align:right;font-family:'JetBrains Mono',monospace;font-weight:600;">%s</td>
  <td style="text-align:center;"><span style="background:%s;color:#fff;padding:2px 10px;border-radius:12px;font-size:0.75rem;font-weight:600;text-transform:uppercase;">%s</span></td>
</tr>""" % (
            inv["id"],
            inv.get("invoice_date", ""),
            inv.get("vendor", ""),
            inv.get("invoice_number", ""),
            alert_dot,
            inv.get("line_item_count", 0),
            _fmt_money(inv.get("total", 0)),
            badge_color,
            status,
        )

    if not rows:
        rows = '<tr><td colspan="6" style="text-align:center;color:#999;padding:2rem;">No invoices yet. <a href="/invoices/new" style="color:#475417;">Add your first invoice</a></td></tr>'

    # Storage stats
    stats = get_storage_stats()
    storage_note = ""
    if stats["total_size_mb"] > 0:
        storage_note = '<span style="font-size:0.75rem;color:#999;">%d files, %.1f MB</span>' % (
            stats["total_files"], stats["total_size_mb"]
        )

    return render_template_string(PAGE_TEMPLATE, **{
        "title": "Invoices",
        "content": LIST_HTML % {
            "unpaid_total": _fmt_money(unpaid_total),
            "week_total": _fmt_money(week_total),
            "alert_count": alert_count,
            "vendor_opts": vendor_opts,
            "status_filter": status_filter,
            "vendor_filter": vendor_filter,
            "rows": rows,
            "total": total,
            "storage_note": storage_note,
        },
    })


@bp.route("/new")
def new_page():
    """Upload or manual entry form."""
    vendor_tabs = "".join(
        '<div class="vendor-tab" data-key="%s" onclick="selectVendor(this)">%s</div>'
        % (k, v)
        for k, v in VENDORS.items()
    )

    return render_template_string(PAGE_TEMPLATE, **{
        "title": "New Invoice",
        "content": NEW_HTML % {"vendor_tabs": vendor_tabs, "today": _today()},
    })


@bp.route("/<invoice_id>")
def detail_page(invoice_id):
    """Invoice detail view with line items and file viewer."""
    inv = get_invoice(invoice_id)
    if not inv:
        return redirect("/invoices/")

    # Line item rows
    li_rows = ""
    for i, li in enumerate(inv.get("line_items", [])):
        alert_html = ""
        alert = li.get("price_alert")
        if alert:
            direction = alert.get("direction", "up")
            arrow = "&#9650;" if direction == "up" else "&#9660;"
            color = "#c0392b" if direction == "up" else "#27ae60"
            pct = alert.get("pct_change", 0)
            old = alert.get("old_price", 0)
            alert_html = '<span style="color:%s;font-size:0.8rem;font-weight:600;" title="Was $%.2f">%s %.1f%%</span>' % (
                color, old, arrow, abs(pct)
            )

        match_badge = ""
        if li.get("master_item_name"):
            match_badge = '<div style="font-size:0.7rem;color:#475417;">%s</div>' % li["master_item_name"]

        li_rows += """<tr>
  <td>%s%s</td>
  <td style="text-align:center;font-family:'JetBrains Mono',monospace;">%s</td>
  <td style="font-size:0.85rem;">%s</td>
  <td style="text-align:right;font-family:'JetBrains Mono',monospace;">%s</td>
  <td style="text-align:right;font-family:'JetBrains Mono',monospace;font-weight:600;">%s</td>
  <td style="text-align:center;">%s</td>
</tr>""" % (
            li.get("item_name", ""),
            match_badge,
            li.get("quantity", ""),
            li.get("unit", ""),
            _fmt_money(li.get("unit_price", 0)),
            _fmt_money(li.get("extended_price", 0)),
            alert_html,
        )

    if not li_rows:
        li_rows = '<tr><td colspan="6" style="text-align:center;color:#999;padding:1rem;">No line items</td></tr>'

    # File links
    file_links = ""
    for i, sf in enumerate(inv.get("source_files", [])):
        icon = "PDF" if sf.get("file_type") == "pdf" else "IMG"
        size = sf.get("size_bytes", 0)
        size_label = "%.1f KB" % (size / 1024) if size > 0 else ""
        file_links += '<a href="/invoices/api/%s/file/%d" target="_blank" style="display:inline-block;padding:6px 14px;background:#f5f0e4;border:1px solid #e0d5bf;border-radius:8px;color:#475417;font-size:0.85rem;font-weight:500;text-decoration:none;margin-right:8px;">%s: %s %s</a>' % (
            invoice_id, i, icon, sf.get("filename", ""), size_label
        )

    if not file_links:
        file_links = '<span style="color:#999;font-size:0.85rem;">No files attached</span>'

    status = inv.get("status", "unpaid")
    badge_color = {"unpaid": "#e67e22", "paid": "#27ae60", "disputed": "#c0392b"}.get(status, "#999")

    calc_total = inv.get("calculated_total", 0)
    vendor_total = inv.get("total", 0)
    diff = abs(vendor_total - calc_total)
    diff_note = ""
    if calc_total > 0 and diff > 2:
        diff_note = '<div style="color:#e67e22;font-size:0.8rem;margin-top:4px;">Line items total %s (diff: %s)</div>' % (
            _fmt_money(calc_total), _fmt_money(diff)
        )

    return render_template_string(PAGE_TEMPLATE, **{
        "title": "%s — %s" % (inv.get("vendor", ""), inv.get("invoice_number", "")),
        "content": DETAIL_HTML % {
            "id": invoice_id,
            "vendor": inv.get("vendor", ""),
            "invoice_number": inv.get("invoice_number", ""),
            "invoice_date": inv.get("invoice_date", ""),
            "due_date": inv.get("due_date") or "—",
            "total": _fmt_money(vendor_total),
            "diff_note": diff_note,
            "status": status,
            "badge_color": badge_color,
            "location": inv.get("location", "brookline").title(),
            "notes": inv.get("notes", ""),
            "file_links": file_links,
            "li_rows": li_rows,
            "li_count": len(inv.get("line_items", [])),
            "hide_paid": "display:none;" if status == "paid" else "",
            "hide_unpaid": "display:none;" if status == "unpaid" else "",
        },
    })


# ── API Endpoints ─────────────────────────────────────────────────


@bp.route("/api/upload", methods=["POST"])
def api_upload():
    """Upload invoice file(s) and optionally extract with AI.

    Accepts multipart form: file, vendor_key.
    If extract=true, runs AI extraction and returns line items for review.
    Otherwise just saves the file and returns file info.
    """
    from invoices.tools.invoice_store import save_upload_file

    file = request.files.get("file")
    vendor_key = request.form.get("vendor_key", "other")
    do_extract = request.form.get("extract", "false").lower() == "true"
    week = _current_week()

    if not file:
        return jsonify({"ok": False, "error": "No file uploaded"})

    # Save file
    file_info = save_upload_file(file, vendor_key, week)

    result = {"ok": True, "file": file_info}

    # AI extraction
    if do_extract:
        try:
            from invoices.tools.invoice_extractor import extract_invoice
            vendor_name = VENDORS.get(vendor_key, vendor_key)
            full_path = str(Path(__file__).resolve().parent.parent / file_info["path"])
            extracted = extract_invoice(full_path, vendor_name)
            result["extracted"] = extracted
        except Exception as e:
            logger.warning("Invoice extraction failed: %s", e)
            result["extraction_error"] = str(e)

    return jsonify(result)


@bp.route("/api/save", methods=["POST"])
def api_save():
    """Save a reviewed invoice to storage."""
    data = request.get_json(silent=True) or {}

    vendor = data.get("vendor", "")
    invoice_number = data.get("invoice_number", "")
    invoice_date = data.get("invoice_date", "")

    if not vendor or not invoice_date:
        return jsonify({"ok": False, "error": "Vendor and date are required"})

    # Duplicate check
    dup = find_duplicate(vendor, invoice_number, invoice_date)
    if dup:
        return jsonify({
            "ok": False,
            "error": "Duplicate invoice exists",
            "duplicate": dup,
        })

    inv = create_invoice(data)

    # Optionally sync prices to vendor price tracker
    if data.get("sync_prices") and inv.get("line_items"):
        try:
            _sync_prices_to_notion(inv)
        except Exception as e:
            logger.warning("Price sync failed: %s", e)

    return jsonify({"ok": True, "invoice": {"id": inv["id"], "vendor": inv["vendor"]}})


@bp.route("/api/<invoice_id>/status", methods=["POST"])
def api_update_status(invoice_id):
    """Update invoice status (unpaid/paid/disputed)."""
    data = request.get_json(silent=True) or {}
    new_status = data.get("status", "")

    if new_status not in ("unpaid", "paid", "disputed"):
        return jsonify({"ok": False, "error": "Invalid status"})

    updates = {"status": new_status}
    if new_status == "paid":
        updates["paid_date"] = _today()
        updates["payment_method"] = data.get("payment_method")
    elif new_status == "unpaid":
        updates["paid_date"] = None

    inv = update_invoice(invoice_id, **updates)
    if not inv:
        return jsonify({"ok": False, "error": "Invoice not found"})

    return jsonify({"ok": True, "status": new_status})


@bp.route("/api/<invoice_id>/notes", methods=["POST"])
def api_update_notes(invoice_id):
    """Update invoice notes."""
    data = request.get_json(silent=True) or {}
    inv = update_invoice(invoice_id, notes=data.get("notes", ""))
    if not inv:
        return jsonify({"ok": False, "error": "Invoice not found"})
    return jsonify({"ok": True})


@bp.route("/api/<invoice_id>/delete", methods=["POST"])
def api_delete(invoice_id):
    """Soft-delete an invoice."""
    ok = delete_invoice(invoice_id)
    return jsonify({"ok": ok, "error": None if ok else "Not found"})


@bp.route("/api/<invoice_id>/file/<int:file_idx>")
def api_serve_file(invoice_id, file_idx):
    """Serve an uploaded invoice file (PDF/image)."""
    path = get_file_path(invoice_id, file_idx)
    if not path:
        return "File not found", 404
    return send_file(str(path))


@bp.route("/api/weekly-totals")
def api_weekly_totals():
    """Return invoice totals aggregated by ISO week."""
    weeks = int(request.args.get("weeks", "12"))
    return jsonify(get_weekly_totals(weeks))


# ── Price Sync Helper ─────────────────────────────────────────────


def _sync_prices_to_notion(inv):
    """Push line item prices to the vendor price tracker Notion DB.

    Invoice date takes priority: if the invoice date is more recent than any
    existing price update for that item+vendor, the invoice price is used as
    the current price (it will be the most recent dated entry in the DB).
    Credits (negative quantity) are skipped — they don't reflect a purchase price.
    """
    from vendor_prices.tools import notion_sync

    prices_db = os.getenv("NOTION_PRICES_DB_ID", "")
    items_db = os.getenv("NOTION_ITEMS_DB_ID", "")
    if not prices_db or not items_db:
        return

    vendor = inv.get("vendor", "")
    week = inv.get("week", "")
    invoice_date = inv.get("invoice_date", "")

    for li in inv.get("line_items", []):
        master_id = li.get("master_item_id")
        # Skip unmatched items and credits (negative qty = return, not a purchase price)
        if not master_id or not li.get("unit_price"):
            continue
        if li.get("is_credit") or float(li.get("quantity", 1) or 1) < 0:
            continue

        try:
            notion_sync.add_price_entry(
                prices_db_id=prices_db,
                item_page_id=master_id,
                vendor=vendor,
                price=li["unit_price"],
                unit=li.get("unit", ""),
                week=week,
                entry_date=invoice_date,  # Invoice date = when the price was actually paid
                vendor_item_name=li.get("item_name", ""),
                vendor_item_code=li.get("item_code", ""),
                source_file=inv.get("source_files", [{}])[0].get("filename", ""),
                quantity=int(float(li.get("quantity", 1) or 1)),
                total_cost=li.get("extended_price"),
                upload_type="Purchase",
            )
        except Exception as e:
            logger.warning("Price entry sync failed for %s: %s", li.get("item_name"), e)


# ── HTML Templates ────────────────────────────────────────────────


PAGE_TEMPLATE = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} - Livite</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Serif+Display&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'DM Sans',-apple-system,sans-serif;background:#F5EDDC;color:#333;min-height:100vh;}
.topbar{display:flex;align-items:center;justify-content:space-between;padding:14px 32px;border-bottom:1px solid rgba(0,0,0,0.08);background:rgba(245,237,220,0.85);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);position:sticky;top:0;z-index:100;}
.topbar-brand{display:flex;align-items:center;gap:10px;text-decoration:none;}
.topbar-logo{width:28px;height:28px;border-radius:8px;background:#475417;display:flex;align-items:center;justify-content:center;color:white;font-family:'DM Serif Display',serif;font-size:15px;}
.topbar-title{font-family:'DM Serif Display',serif;font-size:18px;font-weight:400;color:#292524;letter-spacing:0.3px;}
.topbar-nav{display:flex;gap:4px;}
.topbar-nav a{font-size:13px;font-weight:500;color:#a8a29e;text-decoration:none;padding:6px 14px;border-radius:8px;transition:all 0.2s ease;}
.topbar-nav a:hover{color:#292524;background:rgba(0,0,0,0.03);}
.topbar-nav a.active{color:#475417;background:rgba(71,84,23,0.08);}
.container{max-width:1100px;margin:1.5rem auto;padding:0 1.5rem;}
.card{background:white;border-radius:12px;padding:1.5rem;margin-bottom:1rem;box-shadow:0 2px 8px rgba(0,0,0,0.06);}
h2{color:#475417;margin-bottom:0.8rem;font-size:1.1rem;}
.btn{background:#475417;color:white;border:none;padding:8px 18px;border-radius:8px;font-size:0.9rem;cursor:pointer;font-weight:500;text-decoration:none;display:inline-block;}
.btn:hover{background:#3d6819;}
.btn-outline{background:transparent;color:#475417;border:1px solid #475417;}
.btn-outline:hover{background:#f0edd4;}
.btn-sm{padding:4px 12px;font-size:0.8rem;}
.btn-danger{background:#c0392b;}
.btn-danger:hover{background:#a93226;}
table{width:100%%;border-collapse:collapse;font-size:0.9rem;}
th{text-align:left;font-size:0.75rem;font-weight:600;color:#7a7265;text-transform:uppercase;letter-spacing:0.5px;padding:8px 6px;border-bottom:2px solid #e0d5bf;}
td{padding:8px 6px;border-bottom:1px solid #f0e8d6;}
tr:hover{background:#faf6ee;}
.kpi-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem;margin-bottom:1rem;}
.kpi{background:white;border-radius:12px;padding:1.2rem;box-shadow:0 2px 8px rgba(0,0,0,0.06);text-align:center;}
.kpi-val{font-size:1.6rem;font-weight:700;color:#475417;font-family:'JetBrains Mono',monospace;}
.kpi-label{font-size:0.75rem;color:#7a7265;margin-top:4px;text-transform:uppercase;letter-spacing:0.5px;}
.drop-zone{border:2px dashed #ccc;border-radius:12px;padding:2.5rem;text-align:center;cursor:pointer;transition:all 0.2s;color:#666;}
.drop-zone:hover,.drop-zone.dragover{border-color:#475417;background:rgba(71,84,23,0.05);}
.drop-zone input[type="file"]{display:none;}
.vendor-tabs{display:flex;gap:0.5rem;margin-bottom:1rem;flex-wrap:wrap;}
.vendor-tab{padding:0.5rem 1rem;border:2px solid #ccc;border-radius:8px;cursor:pointer;font-weight:500;transition:all 0.2s;font-size:0.85rem;}
.vendor-tab.active{border-color:#475417;background:#475417;color:white;}
.vendor-tab:hover{border-color:#475417;}
.tabs{display:flex;gap:0;margin-bottom:1.5rem;}
.tab{padding:0.6rem 1.5rem;border:1px solid #e0d5bf;cursor:pointer;font-weight:500;font-size:0.85rem;background:#f5f0e4;transition:all 0.2s;}
.tab:first-child{border-radius:8px 0 0 8px;}
.tab:last-child{border-radius:0 8px 8px 0;}
.tab.active{background:#475417;color:white;border-color:#475417;}
.tab-content{display:none;}
.tab-content.active{display:block;}
select,input[type="text"],input[type="number"],input[type="date"],textarea{padding:6px 10px;border:1px solid #e0d5bf;border-radius:6px;font-family:'DM Sans',sans-serif;font-size:0.85rem;}
select:focus,input:focus,textarea:focus{outline:none;border-color:#475417;}
.filter-bar{display:flex;gap:0.8rem;align-items:center;margin-bottom:1rem;flex-wrap:wrap;}
.filter-bar label{font-size:0.8rem;font-weight:600;color:#475417;}
#status{font-size:0.8rem;color:#7a7265;margin-top:6px;}
@media(max-width:768px){.container{padding:0 0.8rem;}.kpi-row{grid-template-columns:1fr 1fr;}}
</style>
</head><body>
<header class="topbar">
    <a class="topbar-brand" href="/">
        <div class="topbar-logo">L</div>
        <span class="topbar-title">Livite</span>
    </a>
    <nav class="topbar-nav">
        <a href="/">Home</a>
        <a href="/dashboard">Dashboard</a>
        <a href="/invoices/" class="active">Invoices</a>
        <a href="/prices/">Prices</a>
    </nav>
</header>
<div class="container">
{{ content | safe }}
</div>
</body></html>"""


LIST_HTML = """
<div class="kpi-row">
  <div class="kpi">
    <div class="kpi-val">%(unpaid_total)s</div>
    <div class="kpi-label">Unpaid Total</div>
  </div>
  <div class="kpi">
    <div class="kpi-val">%(week_total)s</div>
    <div class="kpi-label">This Week</div>
  </div>
  <div class="kpi">
    <div class="kpi-val">%(alert_count)s</div>
    <div class="kpi-label">Price Alerts</div>
  </div>
</div>

<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h2 style="margin:0;">Invoices (%(total)s)</h2>
    <div>%(storage_note)s <a href="/invoices/new" class="btn btn-sm" style="margin-left:8px;">+ New Invoice</a></div>
  </div>

  <div class="filter-bar">
    <label>Vendor:</label>
    <select onchange="applyFilter()" id="vendorFilter">
      <option value="">All</option>
      %(vendor_opts)s
    </select>
    <label>Status:</label>
    <select onchange="applyFilter()" id="statusFilter">
      <option value="">All</option>
      <option value="unpaid">Unpaid</option>
      <option value="paid">Paid</option>
      <option value="disputed">Disputed</option>
    </select>
  </div>

  <table>
    <thead><tr>
      <th>Date</th>
      <th>Vendor</th>
      <th>Invoice #</th>
      <th style="text-align:center;">Items</th>
      <th style="text-align:right;">Total</th>
      <th style="text-align:center;">Status</th>
    </tr></thead>
    <tbody>%(rows)s</tbody>
  </table>
</div>

<script>
function applyFilter() {
  var v = document.getElementById('vendorFilter').value;
  var s = document.getElementById('statusFilter').value;
  var params = new URLSearchParams();
  if (v) params.set('vendor', v);
  if (s) params.set('status', s);
  window.location = '/invoices/' + (params.toString() ? '?' + params : '');
}
(function() {
  var vf = '%(vendor_filter)s';
  var sf = '%(status_filter)s';
  if (vf) document.getElementById('vendorFilter').value = vf;
  if (sf) document.getElementById('statusFilter').value = sf;
})();
</script>
"""


NEW_HTML = """
<div class="card">
  <h2>Select Vendor</h2>
  <div class="vendor-tabs" id="vendorTabs">
    %(vendor_tabs)s
  </div>
</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('upload')">Upload Invoice</div>
  <div class="tab" onclick="switchTab('manual')">Manual Entry</div>
</div>

<div id="tab-upload" class="tab-content active">
  <div class="card">
    <h2>Upload Invoice</h2>
    <p style="font-size:0.85rem;color:#666;margin-bottom:12px;">Take a photo or upload a PDF. AI will extract line items for review.</p>
    <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
      <input type="file" id="fileInput" accept="image/*,application/pdf" onchange="handleFile(this)">
      <div style="font-size:1.1rem;margin-bottom:4px;">Drop invoice here or tap to upload</div>
      <div style="font-size:0.8rem;">PDF, JPG, PNG, HEIC</div>
    </div>
    <div id="uploadStatus" style="margin-top:8px;"></div>
    <div id="previewArea" style="margin-top:12px;display:none;"></div>
  </div>

  <div id="reviewCard" class="card" style="display:none;">
    <h2>Review Extracted Items</h2>
    <div style="display:flex;gap:1rem;margin-bottom:12px;flex-wrap:wrap;">
      <div><label style="font-size:0.8rem;font-weight:600;color:#475417;">Invoice #</label><br>
        <input type="text" id="extractedInvNum" style="width:160px;"></div>
      <div><label style="font-size:0.8rem;font-weight:600;color:#475417;">Invoice Date</label><br>
        <input type="date" id="extractedDate" value="%(today)s"></div>
      <div><label style="font-size:0.8rem;font-weight:600;color:#475417;">Due Date</label><br>
        <input type="date" id="extractedDueDate"></div>
      <div><label style="font-size:0.8rem;font-weight:600;color:#475417;">Total</label><br>
        <input type="number" id="extractedTotal" step="0.01" style="width:120px;font-family:'JetBrains Mono',monospace;"></div>
    </div>
    <table id="reviewTable">
      <thead><tr>
        <th>Vendor Item</th>
        <th>Common Name</th>
        <th style="text-align:center;">Qty</th>
        <th>Unit</th>
        <th style="text-align:right;">Unit Price</th>
        <th style="text-align:right;">Extended</th>
        <th>Pack</th>
        <th>Each Size</th>
        <th>$/Unit</th>
        <th style="text-align:center;">Status</th>
      </tr></thead>
      <tbody id="reviewBody"></tbody>
    </table>
    <div style="margin-top:12px;display:flex;gap:8px;align-items:center;">
      <label style="font-size:0.85rem;"><input type="checkbox" id="syncPrices" checked> Also update price tracker</label>
    </div>
    <div style="margin-top:12px;display:flex;gap:8px;">
      <button class="btn" onclick="saveInvoice()">Save Invoice</button>
      <span id="saveStatus" style="font-size:0.85rem;color:#7a7265;align-self:center;"></span>
    </div>
  </div>
</div>

<div id="tab-manual" class="tab-content">
  <div class="card">
    <h2>Manual Entry</h2>
    <div style="display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:16px;">
      <div><label style="font-size:0.8rem;font-weight:600;color:#475417;">Invoice #</label><br>
        <input type="text" id="manualInvNum" style="width:160px;"></div>
      <div><label style="font-size:0.8rem;font-weight:600;color:#475417;">Invoice Date</label><br>
        <input type="date" id="manualDate" value="%(today)s"></div>
      <div><label style="font-size:0.8rem;font-weight:600;color:#475417;">Due Date</label><br>
        <input type="date" id="manualDueDate"></div>
      <div><label style="font-size:0.8rem;font-weight:600;color:#475417;">Total ($)</label><br>
        <input type="number" id="manualTotal" step="0.01" style="width:120px;font-family:'JetBrains Mono',monospace;"></div>
    </div>
    <div><label style="font-size:0.8rem;font-weight:600;color:#475417;">Notes</label><br>
      <textarea id="manualNotes" rows="2" style="width:100%%;max-width:500px;"></textarea></div>
    <div style="margin-top:12px;">
      <button class="btn" onclick="saveManualInvoice()">Save Invoice</button>
      <span id="manualSaveStatus" style="font-size:0.85rem;color:#7a7265;margin-left:8px;"></span>
    </div>
  </div>
</div>

<script>
var selectedVendor = '';
var uploadedFileInfo = null;
var extractedItems = [];

function selectVendor(el) {
  document.querySelectorAll('.vendor-tab').forEach(function(t) { t.classList.remove('active'); });
  el.classList.add('active');
  selectedVendor = el.dataset.key;
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active'); });
  document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
  document.querySelector('.tab-content#tab-' + name).classList.add('active');
  event.target.classList.add('active');
}

// Drag & drop
var dz = document.getElementById('dropZone');
['dragenter','dragover'].forEach(function(e) {
  dz.addEventListener(e, function(ev) { ev.preventDefault(); dz.classList.add('dragover'); });
});
['dragleave','drop'].forEach(function(e) {
  dz.addEventListener(e, function(ev) { ev.preventDefault(); dz.classList.remove('dragover'); });
});
dz.addEventListener('drop', function(ev) {
  if (ev.dataTransfer.files.length) {
    document.getElementById('fileInput').files = ev.dataTransfer.files;
    handleFile(document.getElementById('fileInput'));
  }
});

function handleFile(input) {
  if (!input.files[0]) return;
  if (!selectedVendor) {
    document.getElementById('uploadStatus').innerHTML = '<span style="color:#c0392b;">Please select a vendor first</span>';
    return;
  }

  var file = input.files[0];
  document.getElementById('uploadStatus').innerHTML = '<span style="color:#475417;">Uploading and extracting... this may take 30-60 seconds</span>';

  // Show preview for images
  if (file.type.startsWith('image/')) {
    var reader = new FileReader();
    reader.onload = function(e) {
      document.getElementById('previewArea').innerHTML = '<img src="' + e.target.result + '" style="max-width:100%%;max-height:300px;border-radius:8px;border:1px solid #e0d5bf;">';
      document.getElementById('previewArea').style.display = 'block';
    };
    reader.readAsDataURL(file);
  }

  var form = new FormData();
  form.append('file', file);
  form.append('vendor_key', selectedVendor);
  form.append('extract', 'true');

  fetch('/invoices/api/upload', {method: 'POST', body: form})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (!d.ok) {
        document.getElementById('uploadStatus').innerHTML = '<span style="color:#c0392b;">Error: ' + d.error + '</span>';
        return;
      }
      uploadedFileInfo = d.file;

      if (d.extracted) {
        showExtractedReview(d.extracted);
      } else if (d.extraction_error) {
        document.getElementById('uploadStatus').innerHTML = '<span style="color:#e67e22;">File saved. Extraction failed: ' + d.extraction_error + '. You can enter details manually.</span>';
        showManualReview();
      } else {
        document.getElementById('uploadStatus').innerHTML = '<span style="color:#27ae60;">File saved. Enter invoice details below.</span>';
        showManualReview();
      }
    })
    .catch(function(e) {
      document.getElementById('uploadStatus').innerHTML = '<span style="color:#c0392b;">Upload failed: ' + e + '</span>';
    });
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function updateMappedName(idx, val) {
  extractedItems[idx]._mapped_name = val;
}

function updateInvPackQty(idx, val) {
  extractedItems[idx]._pack_qty = parseInt(val) || 1;
  recalcRowPerUnit(idx);
}

function updateInvEachSize(idx, val) {
  extractedItems[idx]._each_size = parseFloat(val) || 0;
  recalcRowPerUnit(idx);
}

function updateInvSizeUnit(idx, val) {
  extractedItems[idx]._size_unit = val;
  recalcRowPerUnit(idx);
}

function recalcRowPerUnit(idx) {
  var item = extractedItems[idx];
  var price = parseFloat(item.unit_price || item.price || 0);
  var packQty = item._pack_qty || 1;
  var eachSize = item._each_size || 0;
  var sizeUnit = item._size_unit || '';
  var cell = document.getElementById('inv-perunit-' + idx);
  if (!cell) return;
  var countUnits = ['can','piece','ct'];
  if (countUnits.includes(sizeUnit) && packQty > 0 && price > 0) {
    cell.textContent = '$' + (price / packQty).toFixed(2) + '/' + sizeUnit;
  } else if (eachSize > 0 && sizeUnit && price > 0) {
    var totalSize = packQty * eachSize;
    cell.textContent = '$' + (price / totalSize).toFixed(2) + '/' + sizeUnit;
  } else if (packQty > 1 && price > 0) {
    cell.textContent = '$' + (price / packQty).toFixed(2) + '/each';
  } else {
    cell.textContent = '';
  }
}

function showExtractedReview(data) {
  extractedItems = data.items || [];
  var matched = extractedItems.filter(function(i){ return i.master_item_name; }).length;
  var credits = extractedItems.filter(function(i){ return parseFloat(i.quantity||0) < 0; }).length;
  var msg = 'Extracted ' + extractedItems.length + ' items';
  if (matched) msg += ' · ' + matched + ' matched';
  if (credits) msg += ' · <span style="color:#c0392b;">' + credits + ' credit(s)</span>';
  document.getElementById('uploadStatus').innerHTML = '<span style="color:#27ae60;">' + msg + '</span>';

  if (data.invoice_number) document.getElementById('extractedInvNum').value = data.invoice_number;
  if (data.invoice_date) document.getElementById('extractedDate').value = data.invoice_date;
  if (data.due_date) document.getElementById('extractedDueDate').value = data.due_date;
  if (data.vendor_total) document.getElementById('extractedTotal').value = data.vendor_total;

  var body = document.getElementById('reviewBody');
  body.innerHTML = '';
  extractedItems.forEach(function(item, i) {
    var qty = parseFloat(item.quantity || 0);
    var isCredit = qty < 0;
    var up = parseFloat(item.unit_price || item.price || 0);
    var ext = parseFloat(item.extended_price || 0);
    var rowBg = isCredit ? 'background:#fef2f2;' : '';
    var qtyColor = isCredit ? 'color:#c0392b;font-weight:700;' : '';
    var mappedName = item.master_item_name || '';
    var statusHtml = item.master_item_name
      ? '<span style="color:#27ae60;font-size:0.75rem;">&#10003; matched</span>'
      : '<span style="color:#e67e22;font-size:0.75rem;">new item</span>';
    if (isCredit) statusHtml = '<span style="color:#c0392b;font-size:0.75rem;font-weight:600;">credit</span>';

    var packQty = item.pack_qty || 1;
    var eachSize = item.each_size || 0;
    var sizeUnit = (item.size_unit || '').toLowerCase();
    extractedItems[i]._pack_qty = packQty;
    extractedItems[i]._each_size = eachSize;
    extractedItems[i]._size_unit = sizeUnit;

    var iStyle = 'font-size:0.8rem;padding:3px 5px;border:1px solid #d4c9b5;border-radius:5px;';
    body.innerHTML += '<tr style="' + rowBg + '">' +
      '<td style="font-size:0.85rem;">' + escHtml(item.item_name || '') + '</td>' +
      '<td><input type="text" style="width:140px;' + iStyle + '" ' +
        'value="' + escHtml(mappedName) + '" placeholder="common name..." ' +
        'oninput="updateMappedName(' + i + ',this.value)"></td>' +
      '<td style="text-align:center;font-family:\\'JetBrains Mono\\',monospace;' + qtyColor + '">' + qty + '</td>' +
      '<td style="font-size:0.85rem;">' + escHtml(item.unit || '') + '</td>' +
      '<td style="text-align:right;font-family:\\'JetBrains Mono\\',monospace;font-size:0.85rem;">$' + up.toFixed(2) + '</td>' +
      '<td style="text-align:right;font-family:\\'JetBrains Mono\\',monospace;font-weight:600;' + (isCredit?'color:#c0392b;':'') + '">' +
        (isCredit && ext > 0 ? '-' : '') + '$' + Math.abs(ext).toFixed(2) + '</td>' +
      '<td><input type="number" step="1" min="1" value="' + packQty + '" style="width:52px;' + iStyle + '" oninput="updateInvPackQty(' + i + ',this.value)"></td>' +
      '<td style="white-space:nowrap;">' +
        '<input type="number" step="any" min="0" value="' + (eachSize||'') + '" placeholder="qty" style="width:48px;' + iStyle + '" oninput="updateInvEachSize(' + i + ',this.value)">' +
        '<input type="text" value="' + escHtml(sizeUnit) + '" placeholder="unit" style="width:44px;' + iStyle + 'margin-left:3px;" oninput="updateInvSizeUnit(' + i + ',this.value)">' +
      '</td>' +
      '<td id="inv-perunit-' + i + '" style="font-size:0.8rem;color:#475417;white-space:nowrap;"></td>' +
      '<td style="text-align:center;">' + statusHtml + '</td>' +
      '</tr>';
  });

  // Compute initial $/Unit for any items that already have pack/each_size
  extractedItems.forEach(function(_, i) { recalcRowPerUnit(i); });

  document.getElementById('reviewCard').style.display = 'block';
}

function showManualReview() {
  document.getElementById('reviewCard').style.display = 'block';
}

function saveInvoice() {
  var vendor = selectedVendor;
  if (!vendor) { alert('Select a vendor'); return; }

  var vendorNames = %(vendor_names_json)s;
  var lineItems = extractedItems.map(function(item) {
    // Use manually mapped name if provided, else fall back to master match
    var commonName = (item._mapped_name !== undefined ? item._mapped_name : item.master_item_name) || '';
    return {
      item_name: item.item_name || '',
      common_name: commonName,
      master_item_id: item.master_item_id || null,
      master_item_name: commonName,
      item_code: item.item_code || '',
      quantity: parseFloat(item.quantity) || 0,
      unit: item.unit || '',
      unit_price: parseFloat(item.unit_price || item.price) || 0,
      extended_price: parseFloat(item.extended_price) || 0,
      pack_qty: item._pack_qty || 1,
      each_size: item._each_size || 0,
      size_unit: item._size_unit || '',
      category: item.category || '',
      is_credit: parseFloat(item.quantity || 0) < 0,
    };
  });

  var calcTotal = lineItems.reduce(function(s, li) { return s + li.extended_price; }, 0);

  var payload = {
    vendor: vendorNames[vendor] || vendor,
    vendor_key: vendor,
    invoice_number: document.getElementById('extractedInvNum').value,
    invoice_date: document.getElementById('extractedDate').value,
    due_date: document.getElementById('extractedDueDate').value || null,
    total: parseFloat(document.getElementById('extractedTotal').value) || calcTotal,
    calculated_total: calcTotal,
    line_items: lineItems,
    source_files: uploadedFileInfo ? [uploadedFileInfo] : [],
    sync_prices: document.getElementById('syncPrices').checked,
  };

  document.getElementById('saveStatus').textContent = 'Saving...';
  fetch('/invoices/api/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.ok) {
      document.getElementById('saveStatus').innerHTML = '<span style="color:#27ae60;">Saved!</span>';
      setTimeout(function() { window.location = '/invoices/'; }, 1000);
    } else {
      document.getElementById('saveStatus').innerHTML = '<span style="color:#c0392b;">' + d.error + '</span>';
    }
  });
}

function saveManualInvoice() {
  var vendor = selectedVendor;
  if (!vendor) { alert('Select a vendor'); return; }

  var vendorNames = %(vendor_names_json)s;
  var payload = {
    vendor: vendorNames[vendor] || vendor,
    vendor_key: vendor,
    invoice_number: document.getElementById('manualInvNum').value,
    invoice_date: document.getElementById('manualDate').value,
    due_date: document.getElementById('manualDueDate').value || null,
    total: parseFloat(document.getElementById('manualTotal').value) || 0,
    calculated_total: 0,
    line_items: [],
    source_files: [],
    notes: document.getElementById('manualNotes').value,
  };

  document.getElementById('manualSaveStatus').textContent = 'Saving...';
  fetch('/invoices/api/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.ok) {
      document.getElementById('manualSaveStatus').innerHTML = '<span style="color:#27ae60;">Saved!</span>';
      setTimeout(function() { window.location = '/invoices/'; }, 1000);
    } else {
      document.getElementById('manualSaveStatus').innerHTML = '<span style="color:#c0392b;">' + d.error + '</span>';
    }
  });
}
</script>
""".replace("%(vendor_names_json)s", json.dumps(VENDORS))


DETAIL_HTML = """
<div style="margin-bottom:12px;">
  <a href="/invoices/" style="color:#475417;font-size:0.85rem;text-decoration:none;">&larr; All Invoices</a>
</div>

<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px;">
    <div>
      <div style="font-size:1.3rem;font-weight:700;color:#475417;">%(vendor)s</div>
      <div style="font-size:0.9rem;color:#7a7265;">Invoice #%(invoice_number)s</div>
    </div>
    <div style="text-align:right;">
      <div style="font-size:1.5rem;font-weight:700;font-family:'JetBrains Mono',monospace;color:#2d2a24;">%(total)s</div>
      %(diff_note)s
      <div style="margin-top:4px;">
        <span style="background:%(badge_color)s;color:#fff;padding:3px 14px;border-radius:12px;font-size:0.8rem;font-weight:600;text-transform:uppercase;">%(status)s</span>
      </div>
    </div>
  </div>

  <div style="display:flex;gap:2rem;margin-top:16px;font-size:0.85rem;color:#7a7265;flex-wrap:wrap;">
    <div><strong>Date:</strong> %(invoice_date)s</div>
    <div><strong>Due:</strong> %(due_date)s</div>
    <div><strong>Location:</strong> %(location)s</div>
  </div>

  <div style="margin-top:16px;display:flex;gap:8px;flex-wrap:wrap;">
    <button class="btn btn-sm" onclick="setStatus('paid')" style="%(hide_paid)s">Mark Paid</button>
    <button class="btn btn-sm btn-outline" onclick="setStatus('unpaid')" style="%(hide_unpaid)s">Mark Unpaid</button>
    <button class="btn btn-sm btn-outline" onclick="setStatus('disputed')">Dispute</button>
    <button class="btn btn-sm btn-danger" onclick="if(confirm('Delete this invoice?'))deleteInvoice()">Delete</button>
  </div>

  <div style="margin-top:16px;">
    <strong style="font-size:0.8rem;color:#475417;">Files:</strong><br>
    <div style="margin-top:6px;">%(file_links)s</div>
  </div>
</div>

<div class="card">
  <h2>Line Items (%(li_count)s)</h2>
  <table>
    <thead><tr>
      <th>Item</th>
      <th style="text-align:center;">Qty</th>
      <th>Unit</th>
      <th style="text-align:right;">Unit Price</th>
      <th style="text-align:right;">Extended</th>
      <th style="text-align:center;">Price Change</th>
    </tr></thead>
    <tbody>%(li_rows)s</tbody>
  </table>
</div>

<div class="card">
  <h2>Notes</h2>
  <textarea id="notesField" rows="3" style="width:100%%;max-width:600px;">%(notes)s</textarea>
  <div style="margin-top:8px;">
    <button class="btn btn-sm btn-outline" onclick="saveNotes()">Save Notes</button>
    <span id="notesStatus" style="font-size:0.8rem;color:#7a7265;margin-left:8px;"></span>
  </div>
</div>

<script>
function setStatus(s) {
  fetch('/invoices/api/%(id)s/status', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: s})
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) location.reload();
  });
}

function deleteInvoice() {
  fetch('/invoices/api/%(id)s/delete', {method: 'POST'})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.ok) window.location = '/invoices/';
    });
}

function saveNotes() {
  fetch('/invoices/api/%(id)s/notes', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({notes: document.getElementById('notesField').value})
  }).then(function(r) { return r.json(); }).then(function(d) {
    document.getElementById('notesStatus').textContent = d.ok ? 'Saved' : 'Error';
  });
}
</script>
"""
