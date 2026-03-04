"""
Build self-contained HTML page for the Catering dashboard.

Uses existing Chart.js helpers and Livite theme from htmlrender package.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from htmlrender.components import (
    _reset_chart_counter, render_chartjs_bar, render_chartjs_line,
    render_chartjs_pie, render_stat, render_stat_grid, render_card,
    render_table, fmt_currency, fmt_pct, fmt_num, _safe,
    LIVITE_CHART_COLORS,
)
from htmlrender.sections import _CSS


_CATERING_COLORS = {
    "Toast": "#8cb82e",       # Livite green
    "Forkable": "#4a9cd8",    # Blue
    "EZCater": "#e86040",     # Red-orange
    "Cater2me": "#9b72c4",    # Purple
}


def build_catering_page(
    metrics: dict,
    logo_b64: str = "",
    current_start: str = "",
    current_end: str = "",
) -> str:
    """Build the complete Catering dashboard HTML page."""
    _reset_chart_counter()

    sections = []
    sections.append(_render_header(metrics, logo_b64))
    sections.append(_render_period_picker(metrics, current_start, current_end))
    sections.append(_render_kpis(metrics))
    sections.append(_render_revenue_trend(metrics))
    sections.append(_render_platform_breakdown(metrics))

    top_items_html = _render_top_items(metrics)
    if top_items_html:
        sections.append(top_items_html)

    sections.append(_render_top_customers(metrics))
    sections.append(_render_dow_analysis(metrics))
    sections.append(_render_recent_orders(metrics))
    sections.append(_render_footer())
    sections.append(_render_order_detail_modal(metrics))

    body = "\n\n".join(s for s in sections if s)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Livite Catering Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
{_CSS}
.cat-toggle {{
    display: inline-block;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 500;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface2);
    color: var(--muted);
    cursor: pointer;
    font-family: 'DM Sans', sans-serif;
    transition: all 0.15s;
}}
.cat-toggle.active {{
    background: var(--livite-green);
    color: var(--livite-cream);
    border-color: var(--livite-green);
    font-weight: 600;
}}
.grid-2 {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
}}
@media (max-width: 700px) {{
    .grid-2 {{ grid-template-columns: 1fr; }}
}}
/* ── Order detail modal ── */
.od-backdrop {{
    display:none; position:fixed; top:0; left:0; width:100%; height:100%;
    background:rgba(0,0,0,0.4); z-index:1000; justify-content:center;
    align-items:center;
}}
.od-backdrop.open {{ display:flex; }}
.od-modal {{
    background:var(--surface); border-radius:14px; width:90%;
    max-width:800px; max-height:85vh; display:flex; flex-direction:column;
    box-shadow:0 8px 32px rgba(0,0,0,0.18); border:1px solid var(--border);
}}
.od-header {{
    display:flex; justify-content:space-between; align-items:center;
    padding:16px 20px; border-bottom:1px solid var(--border);
}}
.od-title {{ font-size:14px; font-weight:600; color:var(--text); }}
.od-close {{
    width:28px; height:28px; border:none; background:var(--surface2);
    border-radius:6px; font-size:18px; color:var(--muted); cursor:pointer;
    display:flex; align-items:center; justify-content:center;
}}
.od-close:hover {{ background:var(--border); }}
.od-body {{ padding:16px 20px; overflow-y:auto; flex:1; }}
.od-body table {{ margin-top:8px; }}
.od-summary {{
    font-size:12px; color:var(--muted); margin-bottom:8px;
    font-family:'JetBrains Mono',monospace;
}}
@media(max-width:600px) {{
    .od-modal {{ width:96%; max-height:90vh; border-radius:10px; }}
    .od-header {{ padding:10px 12px; }}
    .od-body {{ padding:10px 12px; }}
    .od-body table {{ font-size:10px; }}
}}
</style>
</head>
<body>

{body}

</body>
</html>"""


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_header(metrics: dict, logo_b64: str) -> str:
    period = metrics.get("period", {})
    p_start = period.get("start", "")
    p_end = period.get("end", "")
    p_months = period.get("months", 0)
    is_filtered = period.get("is_filtered", False)

    if is_filtered:
        subtitle = (
            f'{_safe(p_start)} - {_safe(p_end)} '
            f'({p_months} month{"s" if p_months != 1 else ""})'
        )
    else:
        subtitle = (
            f'{_safe(p_start)} - {_safe(p_end)} '
            f'(all time, {p_months} months)'
        )

    logo_html = ""
    if logo_b64:
        logo_html = (
            f'<img src="data:image/png;base64,{logo_b64}" alt="Livite" '
            f'style="height:38px;margin-bottom:8px;">'
        )

    return (
        f'<div style="text-align:center;margin-bottom:4px;">'
        f'{logo_html}'
        f'<h1 style="font-size:22px;color:var(--livite-green);margin:4px 0 2px;">'
        f'Catering Dashboard</h1>'
        f'<div style="font-size:12px;color:var(--muted);">{subtitle}</div>'
        f'</div>'
        f'<div style="margin-bottom:12px;">'
        f'<a href="/" style="color:var(--green);text-decoration:none;'
        f'font-size:12px;font-weight:500;">'
        f'\u2190 Dashboard Home</a>'
        f'</div>'
    )


def _render_period_picker(metrics: dict, current_start: str, current_end: str) -> str:
    """Render period presets + custom date range picker."""
    active = "all"
    if current_start and current_end:
        from datetime import datetime as _dt
        try:
            s = _dt.strptime(current_start, "%Y%m%d")
            e = _dt.strptime(current_end, "%Y%m%d")
            today = _dt.now()
            jan1 = _dt(today.year, 1, 1)
            diff_months = (e.year - s.year) * 12 + (e.month - s.month) + 1
            if s == jan1:
                active = "ytd"
            elif diff_months == 12:
                active = "ltm"
            elif diff_months == 6:
                active = "l6m"
            else:
                active = "custom"
        except ValueError:
            active = "custom"

    def _btn(key, label):
        cls = "cat-toggle active" if key == active else "cat-toggle"
        return f'<button class="{cls}" onclick="cpPreset(\'{key}\')">{label}</button>'

    presets = (
        _btn("all", "All Time")
        + _btn("ytd", "YTD")
        + _btn("ltm", "Last 12 Months")
        + _btn("l6m", "Last 6 Months")
    )

    s_val = ""
    e_val = ""
    if current_start and len(current_start) == 8:
        s_val = current_start[:4] + "-" + current_start[4:6]
    if current_end and len(current_end) == 8:
        e_val = current_end[:4] + "-" + current_end[4:6]

    inp_style = (
        'style="font-size:12px;padding:5px 8px;border:1px solid var(--border);'
        'border-radius:6px;font-family:DM Sans,sans-serif;background:var(--surface);"'
    )
    apply_style = (
        'style="font-size:12px;padding:5px 14px;border:none;border-radius:6px;'
        'background:var(--livite-green);color:var(--livite-cream);cursor:pointer;'
        'font-family:DM Sans,sans-serif;font-weight:500;"'
    )

    return f'''<div class="card" style="margin-bottom:14px;padding:12px 16px;">
<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
<span style="font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;">Period</span>
</div>
<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px;">
{presets}
</div>
<div style="border-top:1px solid var(--border);padding-top:8px;">
<div style="display:flex;align-items:center;gap:8px;cursor:pointer;" onclick="var p=document.getElementById('cp_panel');var a=this.querySelector('.cp_arrow');if(p.style.display==='none'){{p.style.display='flex';a.textContent='\\u25BC';}}else{{p.style.display='none';a.textContent='\\u25B6';}}">
<span class="cp_arrow" style="font-size:10px;color:var(--muted);">\\u25B6</span>
<span style="font-size:11px;color:var(--muted);">Custom range</span>
</div>
<div id="cp_panel" style="display:none;flex-wrap:wrap;align-items:center;gap:8px;margin-top:8px;">
<label style="font-size:11px;color:var(--muted);">From:</label>
<input type="month" id="cp_start" value="{s_val}" {inp_style}>
<label style="font-size:11px;color:var(--muted);">To:</label>
<input type="month" id="cp_end" value="{e_val}" {inp_style}>
<button onclick="cpGoCustom()" {apply_style}>Apply</button>
</div>
</div>
</div>
<script>
function cpPreset(key) {{
  var now = new Date();
  var y = now.getFullYear(), m = now.getMonth();
  function fmt(yr, mo) {{ return String(yr) + String(mo+1).padStart(2,'0') + '01'; }}
  function fmtEnd(yr, mo) {{
    var last = new Date(yr, mo+1, 0).getDate();
    return String(yr) + String(mo+1).padStart(2,'0') + String(last).padStart(2,'0');
  }}
  var s, e;
  if (key === 'all') {{ window.location.href = '/catering'; return; }}
  else if (key === 'ytd') {{ s = fmt(y, 0); e = fmtEnd(y, m-1 < 0 ? 0 : m-1); }}
  else if (key === 'ltm') {{ var d = new Date(y, m-12, 1); s = fmt(d.getFullYear(), d.getMonth()); e = fmtEnd(y, m-1 < 0 ? 0 : m-1); }}
  else if (key === 'l6m') {{ var d = new Date(y, m-6, 1); s = fmt(d.getFullYear(), d.getMonth()); e = fmtEnd(y, m-1 < 0 ? 0 : m-1); }}
  if (s && e) window.location.href = '/catering?start=' + s + '&end=' + e;
}}
function cpGoCustom() {{
  var s = document.getElementById('cp_start').value;
  var e = document.getElementById('cp_end').value;
  if (!s || !e) {{ alert('Select both months.'); return; }}
  var sd = s.replace(/-/g, '') + '01';
  var parts = e.split('-');
  var last = new Date(parseInt(parts[0]), parseInt(parts[1]), 0).getDate();
  var ed = parts[0] + parts[1] + String(last).padStart(2,'0');
  window.location.href = '/catering?start=' + sd + '&end=' + ed;
}}
</script>'''


def _render_kpis(metrics: dict) -> str:
    kpis = metrics.get("kpis", {})

    stats = [
        render_stat(
            "Total Catering Revenue",
            fmt_currency(kpis.get("total_revenue", 0)),
            subtitle="all platforms combined",
        ),
        render_stat(
            "Toast Revenue",
            fmt_currency(kpis.get("toast_revenue", 0)),
            color="var(--green)",
        ),
        render_stat(
            "3P Revenue",
            fmt_currency(kpis.get("third_party_revenue", 0)),
            subtitle="Forkable + EZCater + Cater2me",
        ),
        render_stat(
            "Total Orders",
            fmt_num(kpis.get("total_orders", 0)),
        ),
        render_stat(
            "Avg Order Size",
            fmt_currency(kpis.get("avg_order_size", 0)),
        ),
        render_stat(
            "Catering Fees",
            fmt_currency(kpis.get("total_fees", 0)),
            subtitle="platform commissions",
        ),
    ]

    return render_stat_grid(stats)


def _render_revenue_trend(metrics: dict) -> str:
    monthly = metrics.get("monthly", {})
    months = monthly.get("months", [])
    platforms = monthly.get("platforms", {})

    if not months:
        return ""

    # Format month labels: "2025-02" -> "Feb 2025"
    labels = []
    for m in months:
        try:
            from datetime import datetime as _dt
            dt = _dt.strptime(m, "%Y-%m")
            labels.append(dt.strftime("%b %Y"))
        except ValueError:
            labels.append(m)

    # Build stacked bar datasets
    datasets = []
    for platform, values in platforms.items():
        color = _CATERING_COLORS.get(platform, "#7a7265")
        datasets.append({
            "label": platform,
            "data": values,
            "color": color,
        })

    chart = render_chartjs_bar(
        labels, datasets, height=300,
        stacked=True, dollar=True, show_legend=True,
    )

    return render_card(
        "Revenue by Platform",
        chart,
        subtitle="Monthly catering revenue -- Toast, Forkable, EZCater, Cater2me",
    )


def _render_platform_breakdown(metrics: dict) -> str:
    monthly = metrics.get("monthly", {})
    months = monthly.get("months", [])
    platforms = monthly.get("platforms", {})
    order_counts = monthly.get("order_counts", {})

    if not platforms:
        return ""

    # Aggregate totals per platform
    plat_totals = {}
    plat_orders = {}
    for plat, values in platforms.items():
        total = sum(values)
        if total > 0:
            plat_totals[plat] = round(total, 2)
    for plat, counts in order_counts.items():
        plat_orders[plat] = sum(counts)

    if not plat_totals:
        return ""

    labels = list(plat_totals.keys())
    values = list(plat_totals.values())
    colors = [_CATERING_COLORS.get(p, "#7a7265") for p in labels]

    grand_total = sum(values)
    pie = render_chartjs_pie(
        labels, values, colors=colors,
        height=260, doughnut=True,
        center_text=fmt_currency(grand_total),
    )

    # Summary table
    rows = []
    for plat in labels:
        rev = plat_totals.get(plat, 0)
        ords = plat_orders.get(plat, 0)
        pct = (rev / grand_total * 100) if grand_total else 0
        avg = rev / ords if ords else 0
        color = _CATERING_COLORS.get(plat, "#7a7265")
        dot = (
            f'<span style="display:inline-block;width:10px;height:10px;'
            f'border-radius:2px;background:{color};margin-right:6px;'
            f'vertical-align:middle;"></span>'
        )
        rows.append([
            f'{dot}{_safe(plat)}',
            fmt_currency(rev),
            fmt_num(ords),
            fmt_currency(avg),
            fmt_pct(pct, 0),
        ])

    tbl = render_table(
        ["Platform", "Revenue", "Orders", "Avg Order", "Share"],
        rows,
        right_align_cols=[1, 2, 3, 4],
    )

    content = (
        '<div class="grid-2">'
        f'<div>{pie}</div>'
        f'<div>{tbl}</div>'
        '</div>'
    )

    return render_card("Platform Breakdown", content,
                       subtitle="Revenue share by catering platform")


def _render_top_items(metrics: dict) -> str:
    items = metrics.get("top_items", [])
    if not items:
        return ""

    # Horizontal bar chart of top 15 items
    chart_items = items[:15]
    labels = [it["item"] for it in chart_items]
    values = [it["revenue"] for it in chart_items]

    chart = render_chartjs_bar(
        labels,
        [{"label": "Revenue", "data": values, "color": "#8cb82e"}],
        height=max(250, len(chart_items) * 28),
        horizontal=True, dollar=True,
    )

    # Table below
    rows = []
    for it in items[:15]:
        avg = it["revenue"] / it["qty"] if it["qty"] else 0
        rows.append([
            _safe(it["item"]),
            fmt_num(it["qty"]),
            fmt_currency(it["revenue"]),
            fmt_currency(avg),
        ])

    tbl = render_table(
        ["Item", "Qty", "Revenue", "Avg Price"],
        rows,
        right_align_cols=[1, 2, 3],
    )

    return render_card(
        "Top Catering Items",
        chart + '<div style="margin-top:14px;">' + tbl + '</div>',
        subtitle="Menu items from Toast POS catering orders",
    )


def _render_top_customers(metrics: dict) -> str:
    customers = metrics.get("customers", {})
    top_companies = customers.get("top_companies", [])
    top_custs = customers.get("top_customers", [])
    repeat_rate = customers.get("repeat_rate", 0)

    if not top_companies and not top_custs:
        return ""

    # Repeat rate stat
    rate_html = (
        f'<div style="margin-bottom:14px;">'
        f'<span style="font-size:12px;color:var(--muted);">Repeat Customer Rate: </span>'
        f'<span style="font-size:14px;font-weight:600;color:var(--green);">'
        f'{fmt_pct(repeat_rate, 0)}</span>'
        f'</div>'
    )

    # Companies table
    company_rows = []
    for c in top_companies[:10]:
        company_rows.append([
            _safe(c["company"]),
            fmt_num(c["orders"]),
            fmt_currency(c["total_spent"]),
            fmt_currency(c["avg_order"]),
        ])

    company_tbl = ""
    if company_rows:
        company_tbl = (
            '<h3 style="font-size:13px;color:var(--muted);margin:0 0 8px;">'
            'Top Companies</h3>'
            + render_table(
                ["Company", "Orders", "Total", "Avg"],
                company_rows,
                right_align_cols=[1, 2, 3],
            )
        )

    # Customers table
    cust_rows = []
    for c in top_custs[:10]:
        company_note = f' ({_safe(c["company"])})' if c.get("company") else ""
        cust_rows.append([
            f'{_safe(c["name"])}{company_note}',
            fmt_num(c["orders"]),
            fmt_currency(c["total_spent"]),
        ])

    cust_tbl = ""
    if cust_rows:
        cust_tbl = (
            '<h3 style="font-size:13px;color:var(--muted);margin:0 0 8px;">'
            'Top Customers</h3>'
            + render_table(
                ["Customer", "Orders", "Total Spent"],
                cust_rows,
                right_align_cols=[1, 2],
            )
        )

    content = rate_html + '<div class="grid-2">' + f'<div>{company_tbl}</div><div>{cust_tbl}</div>' + '</div>'

    return render_card("Customer Insights", content,
                       subtitle="Based on historical catering order data")


def _render_dow_analysis(metrics: dict) -> str:
    dow = metrics.get("dow_analysis", {})
    labels = dow.get("labels", [])
    counts = dow.get("order_counts", [])
    avg_rev = dow.get("avg_revenue", [])

    if not labels or not any(counts):
        return ""

    count_chart = render_chartjs_bar(
        labels,
        [{"label": "Orders", "data": counts, "color": "#8cb82e"}],
        height=220,
    )

    rev_chart = render_chartjs_bar(
        labels,
        [{"label": "Avg Revenue", "data": avg_rev, "color": "#4a9cd8"}],
        height=220, dollar=True,
    )

    content = (
        '<div class="grid-2">'
        f'<div><h3 style="font-size:13px;color:var(--muted);margin:0 0 8px;">'
        f'Order Count</h3>{count_chart}</div>'
        f'<div><h3 style="font-size:13px;color:var(--muted);margin:0 0 8px;">'
        f'Avg Revenue per Order</h3>{rev_chart}</div>'
        '</div>'
    )

    return render_card(
        "Day of Week Analysis",
        content,
        subtitle="Catering order patterns by day of week",
    )


def _render_recent_orders(metrics: dict) -> str:
    orders = metrics.get("recent_orders", [])
    if not orders:
        return ""

    rows = []
    for o in orders[:20]:
        # Format date: "2025-02-21" -> "Feb 21, 2025"
        date_display = o.get("date", "")
        try:
            from datetime import datetime as _dt
            dt = _dt.strptime(date_display, "%Y-%m-%d")
            date_display = dt.strftime("%b %d, %Y")
        except ValueError:
            pass

        name = _safe(o.get("name") or o.get("customer") or "")
        company = _safe(o.get("company", ""))
        platform = _safe(o.get("platform", ""))
        guests = o.get("guest_count", 0)
        guest_str = fmt_num(guests) if guests and guests > 0 else ""
        city = _safe(o.get("city", ""))

        # Platform color dot
        color = _CATERING_COLORS.get(platform, "#7a7265")
        plat_html = (
            f'<span style="display:inline-block;width:8px;height:8px;'
            f'border-radius:2px;background:{color};margin-right:4px;'
            f'vertical-align:middle;"></span>{platform}'
        )

        rows.append([
            date_display,
            name,
            company,
            plat_html,
            fmt_currency(o.get("subtotal", 0)),
            guest_str,
            city,
        ])

    tbl = render_table(
        ["Date", "Event/Customer", "Company", "Platform", "Subtotal", "Guests", "City"],
        rows,
        right_align_cols=[4, 5],
    )

    return render_card("Recent Orders", tbl,
                       subtitle="Latest catering orders across all platforms")


def _render_footer() -> str:
    return (
        '<div style="text-align:center;padding:20px 0 40px;font-size:11px;color:var(--muted);">'
        '<div>Catering data from Toast POS, Notion, and Excel Tracker</div>'
        '<div style="margin-top:6px;">'
        '<a href="/" style="color:var(--green);text-decoration:none;font-weight:500;">'
        '\u2190 Dashboard Home</a>'
        '</div>'
        '</div>'
    )


def _render_order_detail_modal(metrics: dict) -> str:
    """Render the order detail modal + chart click handlers."""
    import json as _json
    from datetime import datetime as _dt

    all_orders = metrics.get("all_orders", [])
    monthly = metrics.get("monthly", {})
    months = monthly.get("months", [])

    orders_json = _json.dumps(all_orders, ensure_ascii=False)

    # Build month label -> YYYY-MM lookup for chart click resolution
    month_labels = {}
    for m in months:
        try:
            dt = _dt.strptime(m, "%Y-%m")
            label = dt.strftime("%b %Y")
            month_labels[label] = m
        except ValueError:
            month_labels[m] = m

    month_map_json = _json.dumps(month_labels, ensure_ascii=False)

    modal_html = (
        '<div class="od-backdrop" id="odBackdrop" onclick="odClose(event)">'
        '<div class="od-modal" onclick="event.stopPropagation()">'
        '<div class="od-header">'
        '<div class="od-title" id="odTitle">Order Details</div>'
        '<button class="od-close" onclick="odHide()" title="Close">&times;</button>'
        '</div>'
        '<div class="od-body" id="odBody"></div>'
        '</div>'
        '</div>'
    )

    # Build JS as a plain string (too many braces for f-string)
    js = "<script>\n"
    js += "var _allOrders = " + orders_json + ";\n"
    js += "var _monthMap = " + month_map_json + ";\n"
    js += r"""
function odShow(title, orders) {
  document.getElementById('odTitle').textContent = title;
  var body = document.getElementById('odBody');
  if (!orders || orders.length === 0) {
    body.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:20px 0;text-align:center;">No orders found for this segment.</div>';
    document.getElementById('odBackdrop').classList.add('open');
    return;
  }
  var total = orders.reduce(function(s,o){ return s + (o.subtotal||0); }, 0);
  var html = '<div class="od-summary">' + orders.length + ' order' + (orders.length!==1?'s':'') + ' &mdash; $' + total.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}) + ' total</div>';
  html += '<table><tr><th>Date</th><th>Customer/Event</th><th>Company</th><th>Platform</th><th class="r">Subtotal</th><th class="r">Guests</th><th>City</th></tr>';
  orders.forEach(function(o) {
    var d = o.date || '';
    try { var dt = new Date(d + 'T00:00:00'); d = dt.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}); } catch(e) {}
    var name = o.has_detail ? (o.name || '--') : '<span style="color:var(--muted);font-style:italic;">No detail</span>';
    var company = o.has_detail ? (o.company || '--') : '--';
    var guests = o.guest_count > 0 ? o.guest_count : '';
    var city = o.city || '';
    html += '<tr><td>' + d + '</td><td>' + name + '</td><td>' + company + '</td><td>' + (o.platform||'') + '</td><td class="r n">$' + (o.subtotal||0).toLocaleString(undefined,{minimumFractionDigits:2}) + '</td><td class="r n">' + guests + '</td><td>' + city + '</td></tr>';
  });
  html += '</table>';
  body.innerHTML = html;
  document.getElementById('odBackdrop').classList.add('open');
}

function odHide() { document.getElementById('odBackdrop').classList.remove('open'); }
function odClose(e) { if(e.target.id==='odBackdrop') odHide(); }
document.addEventListener('keydown', function(e) { if(e.key==='Escape') odHide(); });

document.addEventListener('DOMContentLoaded', function() {
  // Revenue trend bar chart = lvc_1
  var revCanvas = document.getElementById('lvc_1');
  if (revCanvas) {
    var revChart = Chart.getChart(revCanvas);
    if (revChart) {
      revCanvas.onclick = function(evt) {
        var points = revChart.getElementsAtEventForMode(evt, 'nearest', {intersect:true}, false);
        if (points.length > 0) {
          var idx = points[0].index;
          var dsIdx = points[0].datasetIndex;
          var monthLabel = revChart.data.labels[idx];
          var platform = revChart.data.datasets[dsIdx].label;
          var monthKey = _monthMap[monthLabel] || monthLabel;
          var filtered = _allOrders.filter(function(o) {
            return o.date && o.date.substring(0,7) === monthKey && o.platform === platform;
          });
          filtered.sort(function(a,b) { return b.subtotal - a.subtotal; });
          odShow(platform + ' \u2014 ' + monthLabel, filtered);
        }
      };
      revCanvas.style.cursor = 'pointer';
    }
  }

  // Platform doughnut chart = lvc_2
  var pieCanvas = document.getElementById('lvc_2');
  if (pieCanvas) {
    var pieChart = Chart.getChart(pieCanvas);
    if (pieChart) {
      pieCanvas.onclick = function(evt) {
        var points = pieChart.getElementsAtEventForMode(evt, 'nearest', {intersect:true}, false);
        if (points.length > 0) {
          var idx = points[0].index;
          var platform = pieChart.data.labels[idx];
          var filtered = _allOrders.filter(function(o) {
            return o.platform === platform;
          });
          filtered.sort(function(a,b) { return a.date < b.date ? 1 : -1; });
          odShow(platform + ' \u2014 All Orders', filtered);
        }
      };
      pieCanvas.style.cursor = 'pointer';
    }
  }
});
"""
    js += "</script>"

    return modal_html + js
