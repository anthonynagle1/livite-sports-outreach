"""
Build self-contained HTML page for the Financials dashboard.

Uses existing Chart.js helpers and Livite theme from htmlrender package.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from htmlrender.components import (
    _reset_chart_counter, render_chartjs_bar, render_chartjs_line,
    render_chartjs_pie, render_stat, render_stat_grid, render_card,
    fmt_currency, fmt_pct, fmt_num, fmt_delta, color_for_delta,
    LIVITE_CHART_COLORS, _safe,
)
from htmlrender.sections import _CSS

# Extended color palette for 10 expense groups
_EXPENSE_COLORS = [
    '#8cb82e',  # Labor - Livite lime
    '#e86040',  # Cost of Goods - Red
    '#4a9cd8',  # Third Party - Blue
    '#9b72c4',  # Rent - Purple
    '#e8a830',  # Professional - Amber
    '#2db88a',  # Marketing - Teal
    '#475417',  # Technology - Dark green
    '#c44a8a',  # Capital - Pink
    '#7a7265',  # Other - Muted brown
]


def build_financials_page(
    metrics: dict,
    logo_b64: str = "",
    current_start: str = "",
    current_end: str = "",
) -> str:
    """Build the complete Financials HTML page.

    Args:
        current_start: YYYYMMDD query param (for highlighting active preset)
        current_end: YYYYMMDD query param
    """
    _reset_chart_counter()

    sections = []

    # ── Header ──
    sections.append(_render_header(metrics, logo_b64))

    # ── Period Picker ──
    sections.append(_render_period_picker(metrics, current_start, current_end))

    # ── KPI Summary ──
    sections.append(_render_kpis(metrics))

    # ── Revenue & Profit Trend ──
    sections.append(_render_revenue_trend(metrics))

    # ── Margin Trends ──
    sections.append(_render_margins(metrics))

    # ── Annual Comparison ──
    sections.append(_render_annual(metrics))

    # ── Catering Platform Revenue (from Notion) ──
    catering = metrics.get("catering")
    if catering:
        sections.append(_render_catering(catering))

    # ── Expense Breakdown ──
    sections.append(_render_expense_breakdown(metrics))

    # ── Expense Trends ──
    sections.append(_render_expense_trends(metrics))

    # ── Balance Sheet ──
    sections.append(_render_balance_sheet(metrics))

    # ── Seasonality ──
    sections.append(_render_seasonality(metrics))

    # ── Consultant Analysis ──
    sections.append(_render_analysis(metrics))

    # ── Footer ──
    sections.append(_render_footer())

    body = "\n\n".join(s for s in sections if s)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Livite Financial Overview</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
{_CSS}
.fin-toggle {{
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
.fin-toggle.active {{
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
        subtitle = f'{_safe(p_start)} - {_safe(p_end)} ({p_months} month{"s" if p_months != 1 else ""})'
    else:
        subtitle = f'{_safe(p_start)} - {_safe(p_end)} (all time, {p_months} months)'

    logo_html = ""
    if logo_b64:
        logo_html = f'<img src="data:image/png;base64,{logo_b64}" alt="Livite" style="height:38px;margin-bottom:8px;">'

    return (
        f'<div style="text-align:center;margin-bottom:4px;">'
        f'{logo_html}'
        f'<h1 style="font-size:22px;color:var(--livite-green);margin:4px 0 2px;">Financial Overview</h1>'
        f'<div style="font-size:12px;color:var(--muted);">{subtitle}</div>'
        f'</div>'
        f'<div style="margin-bottom:12px;">'
        f'<a href="/" style="color:var(--green);text-decoration:none;font-size:12px;font-weight:500;">'
        f'\u2190 Dashboard Home</a>'
        f'</div>'
    )


def _render_period_picker(metrics: dict, current_start: str, current_end: str) -> str:
    """Render period selection presets + custom date range picker."""
    period = metrics.get("period", {})
    is_filtered = period.get("is_filtered", False)

    # Determine which preset is active (approximate match)
    active = "all"
    if current_start and current_end:
        from datetime import datetime as _dt, timedelta
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
            elif diff_months == 24:
                active = "l24"
            elif diff_months >= 58 and diff_months <= 62:
                active = "l5y"
            else:
                active = "custom"
        except ValueError:
            active = "custom"

    def _btn(key, label):
        cls = "fin-toggle active" if key == active else "fin-toggle"
        return f'<button class="{cls}" onclick="fpPreset(\'{key}\')">{label}</button>'

    presets = (
        _btn("all", "All Time")
        + _btn("ytd", "YTD")
        + _btn("ltm", "Last 12 Months")
        + _btn("l24", "Last 24 Months")
        + _btn("l5y", "Last 5 Years")
    )

    # Custom date picker (collapsible)
    s_val = ""
    e_val = ""
    if current_start and len(current_start) == 8:
        s_val = current_start[:4] + "-" + current_start[4:6] + "-" + current_start[6:]
    if current_end and len(current_end) == 8:
        e_val = current_end[:4] + "-" + current_end[4:6] + "-" + current_end[6:]

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
<div style="display:flex;align-items:center;gap:8px;cursor:pointer;" onclick="var p=document.getElementById('fp_panel');var a=this.querySelector('.fp_arrow');if(p.style.display==='none'){{p.style.display='flex';a.textContent='\\u25BC';}}else{{p.style.display='none';a.textContent='\\u25B6';}}">
<span class="fp_arrow" style="font-size:10px;color:var(--muted);">\\u25B6</span>
<span style="font-size:11px;color:var(--muted);">Custom range</span>
</div>
<div id="fp_panel" style="display:none;flex-wrap:wrap;align-items:center;gap:8px;margin-top:8px;">
<label style="font-size:11px;color:var(--muted);">From:</label>
<input type="month" id="fp_start" value="{s_val[:7]}" {inp_style}>
<label style="font-size:11px;color:var(--muted);">To:</label>
<input type="month" id="fp_end" value="{e_val[:7]}" {inp_style}>
<button onclick="fpGoCustom()" {apply_style}>Apply</button>
</div>
</div>
</div>
<script>
function fpPreset(key) {{
  var now = new Date();
  var y = now.getFullYear(), m = now.getMonth();
  function fmt(yr, mo) {{ return String(yr) + String(mo+1).padStart(2,'0') + '01'; }}
  function fmtEnd(yr, mo) {{
    var last = new Date(yr, mo+1, 0).getDate();
    return String(yr) + String(mo+1).padStart(2,'0') + String(last).padStart(2,'0');
  }}
  var s, e;
  if (key === 'all') {{ window.location.href = '/financials'; return; }}
  else if (key === 'ytd') {{ s = fmt(y, 0); e = fmtEnd(y, m-1 < 0 ? 0 : m-1); }}
  else if (key === 'ltm') {{ var d = new Date(y, m-12, 1); s = fmt(d.getFullYear(), d.getMonth()); e = fmtEnd(y, m-1 < 0 ? 0 : m-1); }}
  else if (key === 'l24') {{ var d = new Date(y, m-24, 1); s = fmt(d.getFullYear(), d.getMonth()); e = fmtEnd(y, m-1 < 0 ? 0 : m-1); }}
  else if (key === 'l5y') {{ var d = new Date(y-5, m, 1); s = fmt(d.getFullYear(), d.getMonth()); e = fmtEnd(y, m-1 < 0 ? 0 : m-1); }}
  if (s && e) window.location.href = '/financials?start=' + s + '&end=' + e;
}}
function fpGoCustom() {{
  var s = document.getElementById('fp_start').value;
  var e = document.getElementById('fp_end').value;
  if (!s || !e) {{ alert('Select both months.'); return; }}
  var sd = s.replace(/-/g, '') + '01';
  var parts = e.split('-');
  var last = new Date(parseInt(parts[0]), parseInt(parts[1]), 0).getDate();
  var ed = parts[0] + parts[1] + String(last).padStart(2,'0');
  window.location.href = '/financials?start=' + sd + '&end=' + ed;
}}
</script>'''


def _render_kpis(metrics: dict) -> str:
    kpis = metrics.get("kpis", {})
    period = metrics.get("period", {})
    is_filtered = period.get("is_filtered", False)
    p_months = period.get("months", 0)

    rev = kpis.get("monthly_revenue", 0)
    net = kpis.get("monthly_net_income", 0)
    gm = kpis.get("gross_margin")
    om = kpis.get("operating_margin")
    cash = kpis.get("total_cash", 0)
    yoy = kpis.get("yoy_revenue_growth")
    mom = kpis.get("mom_revenue_growth")

    mom_delta = fmt_delta(None, mom, "up") if mom is not None else ""
    yoy_delta = fmt_delta(None, yoy, "up") if yoy is not None else ""

    # For multi-month views, show period totals + averages
    if is_filtered and p_months > 1:
        total_rev = period.get("total_revenue", 0)
        total_net = period.get("total_net_income", 0)
        avg_rev = period.get("avg_monthly_revenue", 0)
        avg_net = period.get("avg_monthly_net_income", 0)

        stats = [
            render_stat("Period Revenue", fmt_currency(total_rev),
                        subtitle=f'{p_months} months'),
            render_stat("Avg Monthly Revenue", fmt_currency(avg_rev),
                        delta_html=mom_delta),
            render_stat("Period Net Income", fmt_currency(total_net)),
            render_stat("Avg Monthly Net Income", fmt_currency(avg_net)),
            render_stat("Gross Margin", fmt_pct(gm),
                        subtitle="latest month",
                        color="var(--green)" if gm and gm > 50 else None),
            render_stat("Operating Margin", fmt_pct(om),
                        subtitle="latest month",
                        color="var(--green)" if om and om > 10 else None),
        ]
    else:
        stats = [
            render_stat("Monthly Revenue", fmt_currency(rev),
                        subtitle=kpis.get("latest_month", ""), delta_html=mom_delta),
            render_stat("Net Income", fmt_currency(net)),
            render_stat("Gross Margin", fmt_pct(gm),
                        color="var(--green)" if gm and gm > 50 else None),
            render_stat("Operating Margin", fmt_pct(om),
                        color="var(--green)" if om and om > 10 else None),
            render_stat("Cash Position", fmt_currency(cash)),
            render_stat("YoY Revenue Growth", fmt_pct(yoy) if yoy is not None else "N/A",
                        delta_html=yoy_delta),
        ]

    return render_stat_grid(stats)


def _render_revenue_trend(metrics: dict) -> str:
    rt = metrics.get("revenue_trend", {})
    labels = rt.get("labels", [])
    sales = rt.get("sales", [])
    net_income = rt.get("net_income", [])
    rolling = rt.get("rolling_12", [])

    # Show every 6th label to avoid crowding
    sparse_labels = []
    for i, lbl in enumerate(labels):
        if i % 6 == 0:
            sparse_labels.append(lbl)
        else:
            sparse_labels.append("")

    datasets = [
        {
            "label": "Monthly Sales",
            "data": sales,
            "color": "#8cb82e",
            "borderWidth": 2,
            "pointRadius": 1,
        },
        {
            "label": "Net Income",
            "data": net_income,
            "type": "bar",
            "color": "#4a9cd880",
            "colors": ["#4a7c1f" if v >= 0 else "#d9342b" for v in net_income],
            "order": 2,
        },
    ]

    # Add rolling average (skip None values, use dashed line)
    if any(v is not None for v in rolling):
        datasets.append({
            "label": "12-Month Avg",
            "data": rolling,
            "color": "#475417",
            "borderWidth": 2,
            "pointRadius": 0,
            "borderDash": [6, 4],
            "spanGaps": True,
        })

    chart = render_chartjs_line(sparse_labels, datasets, height=350, dollar=True)

    return render_card("Revenue & Profit Trend",
                       '<div style="font-size:11px;color:var(--muted);margin-bottom:8px;">'
                       'Monthly sales (line), net income (bars), and 12-month rolling average (dashed)</div>'
                       + chart,
                       subtitle="111 months of financial data")


def _render_margins(metrics: dict) -> str:
    mg = metrics.get("margins", {})
    labels = mg.get("labels", [])
    gm = mg.get("gross_margin_pct", [])
    om = mg.get("operating_margin_pct", [])
    nm = mg.get("net_margin_pct", [])

    sparse_labels = [lbl if i % 6 == 0 else "" for i, lbl in enumerate(labels)]

    datasets = [
        {"label": "Gross Margin", "data": gm, "color": "#8cb82e", "pointRadius": 1},
        {"label": "Operating Margin", "data": om, "color": "#4a9cd8", "pointRadius": 1},
        {"label": "Net Margin", "data": nm, "color": "#9b72c4", "pointRadius": 1},
    ]

    chart = render_chartjs_line(sparse_labels, datasets, height=280,
                                annotation_lines=[{"value": 0, "color": "#ccc", "label": ""}])

    return render_card("Margin Trends", chart,
                       subtitle="Gross, Operating, and Net margin as % of total income")


def _render_annual(metrics: dict) -> str:
    annual = metrics.get("annual", {})
    years = annual.get("years", [])
    revenue = annual.get("revenue", [])
    net_income = annual.get("net_income", [])

    rev_chart = render_chartjs_bar(
        years,
        [{"label": "Revenue", "data": revenue, "color": "#8cb82e"}],
        height=280, dollar=True
    )

    # Color bars green for positive, red for negative
    ni_colors = ["#4a7c1f" if v >= 0 else "#d9342b" for v in net_income]
    ni_chart = render_chartjs_bar(
        years,
        [{"label": "Net Income", "data": net_income, "colors": ni_colors}],
        height=280, dollar=True
    )

    content = (
        '<div class="grid-2">'
        f'<div><h3 style="font-size:13px;color:var(--muted);margin:0 0 8px;">Annual Revenue</h3>{rev_chart}</div>'
        f'<div><h3 style="font-size:13px;color:var(--muted);margin:0 0 8px;">Annual Net Income</h3>{ni_chart}</div>'
        '</div>'
    )

    return render_card("Year-over-Year Comparison", content,
                       subtitle="Full calendar year totals (2026 is partial — Jan only)")


# ── Catering Platform Revenue ──

_CATERING_COLORS = {
    "Forkable": "#4a9cd8",        # Blue
    "EZCater": "#e86040",         # Red-orange
    "Cater2me": "#9b72c4",        # Purple
    "Toast Catering": "#8cb82e",  # Livite green
}


def _render_catering(catering: dict) -> str:
    """Render catering revenue by platform (Excel tracker + Toast POS)."""
    months = catering.get("months", [])
    platforms = catering.get("platforms", {})
    totals = catering.get("totals", [])
    order_counts = catering.get("order_counts", {})

    if not months or not platforms:
        return ""

    # Format month labels: "2026-01" → "Jan 2026"
    labels = []
    for m in months:
        try:
            from datetime import datetime as _dt
            dt = _dt.strptime(m, "%Y-%m")
            labels.append(dt.strftime("%b %Y"))
        except ValueError:
            labels.append(m)

    # Build datasets for stacked bar chart
    datasets = []
    for platform, values in platforms.items():
        color = _CATERING_COLORS.get(platform, "#7a7265")
        datasets.append({
            "label": platform,
            "data": values,
            "color": color,
        })

    chart_html = render_chartjs_bar(
        labels, datasets, height=280,
        stacked=True, dollar=True, show_legend=True,
        y_label="Revenue",
    )

    # Summary stats
    total_all = sum(totals)
    total_orders = sum(
        sum(cnts) for cnts in order_counts.values()
    )
    avg_order = total_all / total_orders if total_orders else 0

    # Platform breakdown for latest month
    latest_month = labels[-1] if labels else ""
    latest_total = totals[-1] if totals else 0
    breakdown_parts = []
    for platform in platforms:
        val = platforms[platform][-1] if platforms[platform] else 0
        cnt = order_counts.get(platform, [0])[-1] if order_counts.get(platform) else 0
        if val > 0:
            breakdown_parts.append(
                f'<span style="display:inline-block;margin-right:16px;">'
                f'<span style="display:inline-block;width:10px;height:10px;'
                f'border-radius:2px;background:{_CATERING_COLORS.get(platform, "#7a7265")};'
                f'margin-right:4px;vertical-align:middle;"></span>'
                f'{_safe(platform)}: {fmt_currency(val)} ({cnt} orders)</span>'
            )

    breakdown_html = "".join(breakdown_parts)

    stats_html = (
        f'<div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:12px;">'
        f'<div><span style="font-size:11px;color:var(--muted);">Total Catering Revenue</span>'
        f'<div style="font-size:16px;font-weight:600;color:var(--text);">{fmt_currency(total_all)}</div></div>'
        f'<div><span style="font-size:11px;color:var(--muted);">Total Orders</span>'
        f'<div style="font-size:16px;font-weight:600;color:var(--text);">{fmt_num(total_orders)}</div></div>'
        f'<div><span style="font-size:11px;color:var(--muted);">Avg Order Size</span>'
        f'<div style="font-size:16px;font-weight:600;color:var(--text);">{fmt_currency(avg_order)}</div></div>'
        f'</div>'
        f'<div style="font-size:12px;margin-bottom:14px;">'
        f'<span style="color:var(--muted);">{_safe(latest_month)}:</span> {breakdown_html}</div>'
    )

    content = stats_html + chart_html

    return render_card(
        "Catering Platform Revenue",
        content,
        subtitle="Catering revenue by platform -- Forkable, EZCater, Cater2me, and Toast Catering"
    )


def _render_expense_breakdown(metrics: dict) -> str:
    eg = metrics.get("expense_groups", {})
    pie_data = eg.get("pie", {})
    pie_pct_data = eg.get("pie_pct", {})
    latest_rev = eg.get("latest_12_revenue", 0)

    if not pie_data:
        return ""

    labels = list(pie_data.keys())
    values = list(pie_data.values())
    colors = _EXPENSE_COLORS[:len(labels)]

    total = sum(values)
    center = fmt_currency(total)

    # Dollar view
    pie_chart = render_chartjs_pie(labels, values, colors=colors,
                                   height=300, doughnut=True, center_text=center)
    bar_chart = render_chartjs_bar(
        labels, [{"label": "Expenses", "data": values, "colors": colors}],
        height=300, horizontal=True, dollar=True
    )

    # Percent of Sales view
    pct_labels = list(pie_pct_data.keys()) if pie_pct_data else labels
    pct_values = list(pie_pct_data.values()) if pie_pct_data else [0] * len(labels)
    pct_colors = _EXPENSE_COLORS[:len(pct_labels)]

    pct_total = fmt_pct(sum(pct_values)) if pct_values else "0%"
    pie_chart_pct = render_chartjs_pie(pct_labels, pct_values, colors=pct_colors,
                                       height=300, doughnut=True, center_text=pct_total)
    bar_chart_pct = render_chartjs_bar(
        pct_labels, [{"label": "% of Sales", "data": pct_values, "colors": pct_colors}],
        height=300, horizontal=True, pct=True
    )

    toggle = (
        '<div style="display:flex;gap:6px;margin-bottom:12px;">'
        '<button class="fin-toggle active" onclick="toggleExpBreak(\'dollar\')" id="eb-dollar">Dollar</button>'
        '<button class="fin-toggle" onclick="toggleExpBreak(\'pct\')" id="eb-pct">% of Sales</button>'
        f'<span style="font-size:10px;color:var(--muted);align-self:center;margin-left:8px;">Revenue: {fmt_currency(latest_rev)}</span>'
        '</div>'
    )

    content = (
        f'{toggle}'
        f'<div id="eb-view-dollar"><div class="grid-2">'
        f'<div>{pie_chart}</div>'
        f'<div>{bar_chart}</div>'
        f'</div></div>'
        f'<div id="eb-view-pct" style="display:none;"><div class="grid-2">'
        f'<div>{pie_chart_pct}</div>'
        f'<div>{bar_chart_pct}</div>'
        f'</div></div>'
        '<script>'
        'function toggleExpBreak(mode){'
        'document.getElementById("eb-view-dollar").style.display=mode==="dollar"?"block":"none";'
        'document.getElementById("eb-view-pct").style.display=mode==="pct"?"block":"none";'
        'document.getElementById("eb-dollar").className="fin-toggle"+(mode==="dollar"?" active":"");'
        'document.getElementById("eb-pct").className="fin-toggle"+(mode==="pct"?" active":"");'
        '}</script>'
    )

    return render_card("Expense Breakdown", content,
                       subtitle="Last 12 months — COGS + Operating Expenses grouped by category")


def _render_expense_trends(metrics: dict) -> str:
    eg = metrics.get("expense_groups", {})
    q_labels = eg.get("quarter_labels", [])
    q_data = eg.get("quarterly", {})
    q_data_pct = eg.get("quarterly_pct", {})

    if not q_labels:
        return ""

    # Order groups by total descending for consistent legend
    group_totals = {grp: sum(vals) for grp, vals in q_data.items()}
    sorted_groups = sorted(group_totals.keys(), key=lambda g: group_totals[g], reverse=True)
    sorted_groups = [g for g in sorted_groups if group_totals[g] > 0]

    # Show every other quarter label
    sparse_labels = [lbl if i % 2 == 0 else "" for i, lbl in enumerate(q_labels)]

    # Dollar chart
    ds_dollar = []
    for i, grp in enumerate(sorted_groups):
        ds_dollar.append({
            "label": grp,
            "data": q_data[grp],
            "color": _EXPENSE_COLORS[i % len(_EXPENSE_COLORS)],
        })

    chart_dollar = render_chartjs_bar(
        sparse_labels, ds_dollar,
        height=350, stacked=True, dollar=True, show_legend=True
    )

    # Percent of Sales chart
    ds_pct = []
    for i, grp in enumerate(sorted_groups):
        ds_pct.append({
            "label": grp,
            "data": q_data_pct.get(grp, []),
            "color": _EXPENSE_COLORS[i % len(_EXPENSE_COLORS)],
        })

    chart_pct = render_chartjs_bar(
        sparse_labels, ds_pct,
        height=350, stacked=True, pct=True, show_legend=True
    )

    toggle = (
        '<div style="display:flex;gap:6px;margin-bottom:12px;">'
        '<button class="fin-toggle active" onclick="toggleExpTrend(\'dollar\')" id="et-dollar">Dollar</button>'
        '<button class="fin-toggle" onclick="toggleExpTrend(\'pct\')" id="et-pct">% of Sales</button>'
        '</div>'
    )

    content = (
        f'{toggle}'
        f'<div id="et-view-dollar">{chart_dollar}</div>'
        f'<div id="et-view-pct" style="display:none;">{chart_pct}</div>'
        '<script>'
        'function toggleExpTrend(mode){'
        'document.getElementById("et-view-dollar").style.display=mode==="dollar"?"block":"none";'
        'document.getElementById("et-view-pct").style.display=mode==="pct"?"block":"none";'
        'document.getElementById("et-dollar").className="fin-toggle"+(mode==="dollar"?" active":"");'
        'document.getElementById("et-pct").className="fin-toggle"+(mode==="pct"?" active":"");'
        '}</script>'
    )

    return render_card("Expense Trends by Quarter", content,
                       subtitle="Stacked quarterly expenses showing category composition over time")


def _render_balance_sheet(metrics: dict) -> str:
    bs = metrics.get("balance_sheet", {})
    labels = bs.get("labels", [])
    cash = bs.get("total_cash", [])
    equity = bs.get("total_equity", [])
    debt = bs.get("debt", [])

    if not labels:
        return ""

    sparse_labels = [lbl if i % 6 == 0 else "" for i, lbl in enumerate(labels)]

    datasets = [
        {"label": "Cash", "data": cash, "color": "#8cb82e", "fill": True, "pointRadius": 1},
        {"label": "Total Equity", "data": equity, "color": "#4a9cd8", "pointRadius": 1},
        {"label": "Long-Term Debt", "data": debt, "color": "#e86040", "pointRadius": 1},
    ]

    chart = render_chartjs_line(sparse_labels, datasets, height=300, dollar=True)

    return render_card("Balance Sheet Trends", chart,
                       subtitle="Cash position, total equity, and long-term debt over time")


def _render_seasonality(metrics: dict) -> str:
    s = metrics.get("seasonality", {})
    labels = s.get("labels", [])
    values = s.get("values", [])

    if not labels:
        return ""

    chart = render_chartjs_bar(
        labels,
        [{"label": "Avg Monthly Revenue", "data": values, "color": "#8cb82e"}],
        height=250, dollar=True
    )

    return render_card("Seasonality", chart,
                       subtitle="Average monthly revenue across all years — identify peak and slow months")


def _render_analysis(metrics: dict) -> str:
    analysis = metrics.get("analysis", {})
    analysis_sections = analysis.get("sections", [])

    if not analysis_sections:
        return ""

    # Color accents for section headers
    section_colors = [
        "#8cb82e",  # green
        "#4a9cd8",  # blue
        "#9b72c4",  # purple
        "#e8a830",  # amber
        "#2db88a",  # teal
        "#e86040",  # red
    ]

    parts = []
    for idx, section in enumerate(analysis_sections):
        color = section_colors[idx % len(section_colors)]
        title = _safe(section.get("title", ""))
        items = section.get("items", [])

        items_html = ""
        for item in items:
            items_html += (
                f'<li style="margin-bottom:6px;line-height:1.5;font-size:13px;'
                f'color:var(--text);">{_safe(item)}</li>'
            )

        parts.append(
            f'<div style="margin-bottom:16px;">'
            f'<h3 style="font-size:14px;font-weight:600;margin:0 0 8px;'
            f'color:{color};border-left:3px solid {color};padding-left:10px;">'
            f'{title}</h3>'
            f'<ul style="margin:0;padding-left:24px;list-style-type:disc;">'
            f'{items_html}</ul>'
            f'</div>'
        )

    content = "".join(parts)

    return render_card(
        "Financial Analysis",
        '<div style="font-size:11px;color:var(--muted);margin-bottom:12px;font-style:italic;">'
        'Data-driven analysis computed from 9+ years of P&L and Balance Sheet records. '
        'Industry benchmarks sourced from NRA and fast-casual restaurant averages.</div>'
        + content,
        subtitle="Independent assessment of financial health and performance"
    )


def _render_footer() -> str:
    return (
        '<div style="text-align:center;padding:20px 0 40px;font-size:11px;color:var(--muted);">'
        '<div>Financial data from accounting records (Nov 2016 - Jan 2026)</div>'
        '<div style="margin-top:6px;">'
        '<a href="/" style="color:var(--green);text-decoration:none;font-weight:500;">'
        '\u2190 Dashboard Home</a>'
        '</div>'
        '</div>'
    )
