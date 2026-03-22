"""Build self-contained HTML page for the Cash Flow Forecast dashboard.

Renders a 14-day cash flow projection with:
- KPI grid (balances, revenue, expenses, net flow)
- Mixed bar+line chart (revenue bars, expense bars, balance line)
- Danger zone alerts
- Daily detail table
- Assumptions card

Uses existing Chart.js helpers and Livite theme from htmlrender package.
"""

from __future__ import annotations

import json as _json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from htmlrender.components import (
    _reset_chart_counter, render_stat, render_stat_grid,
    render_card, render_table, render_insight,
    fmt_currency, fmt_pct, fmt_num, _safe,
    LIVITE_CHART_COLORS, _next_chart_id, _js,
)
from htmlrender.sections import _CSS, _LOADING_SNIPPET


_GREEN = "#8cb82e"
_GREEN_LIGHT = "rgba(140,184,46,0.7)"
_BLUE = "#4a9cd8"
_RED = "#e86040"
_RED_LIGHT = "rgba(232,96,64,0.7)"
_ORANGE = "#e8a830"
_PURPLE = "#9b72c4"
_DARK_GREEN = "#475417"


def build_cashflow_page(
    metrics: dict,
    logo_b64: str = "",
) -> str:
    """Build the complete Cash Flow Forecast dashboard HTML page."""
    _reset_chart_counter()

    sections = []
    sections.append(_render_header(metrics, logo_b64))
    sections.append(_render_kpis(metrics))
    sections.append(_render_main_chart(metrics))
    sections.append(_render_danger_zones(metrics))
    sections.append(_render_daily_table(metrics))
    sections.append(_render_catering_upcoming(metrics))
    sections.append(_render_assumptions(metrics))
    sections.append(_render_footer())

    body = "\n\n".join(s for s in sections if s)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Livite Cash Flow Forecast</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
{_CSS}
.cf-danger {{
    background: rgba(232,96,64,0.08);
    border-left: 3px solid #e86040;
    border-radius: 0 8px 8px 0;
    padding: 10px 14px;
    margin-bottom: 8px;
    font-size: 13px;
}}
.cf-danger strong {{
    color: #e86040;
}}
.payroll-row {{
    background: rgba(232,168,48,0.06);
}}
.balance-neg {{
    color: #e86040;
    font-weight: 600;
}}
.balance-ok {{
    color: #8cb82e;
}}
.balance-warn {{
    color: #e8a830;
}}
.catering-chip {{
    display: inline-block;
    font-size: 10px;
    background: rgba(74,156,216,0.12);
    color: #4a9cd8;
    padding: 2px 8px;
    border-radius: 4px;
    margin-left: 4px;
    font-weight: 500;
}}
</style>
</head>
<body>

{body}

{_LOADING_SNIPPET}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def _render_header(metrics: dict, logo_b64: str) -> str:
    assumptions = metrics.get("assumptions", {})
    days_found = assumptions.get("hist_days_found", 0)
    forecast_ok = assumptions.get("forecast_available", False)

    logo_html = ""
    if logo_b64:
        logo_html = (
            f'<img src="data:image/png;base64,{logo_b64}" '
            f'style="height:36px;margin-right:12px;" alt="Livite">'
        )

    data_note = f"Based on {days_found} days of historical data"
    if forecast_ok:
        data_note += " + AI revenue forecast"

    sub_nav = (
        '<div style="display:flex;justify-content:center;gap:8px;margin-top:10px;flex-wrap:wrap;">'
        '<a href="/" style="font-size:12px;padding:5px 12px;border-radius:6px;'
        'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Home</a>'
        '<a href="/today" style="font-size:12px;padding:5px 12px;border-radius:6px;'
        'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Today</a>'
        '<a href="/forecast" style="font-size:12px;padding:5px 12px;border-radius:6px;'
        'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">P&amp;L Forecast</a>'
        '<a href="/cashflow" style="font-size:12px;padding:5px 12px;border-radius:6px;'
        'background:#475417;color:#fff;text-decoration:none;font-weight:600;">Cash Flow</a>'
        '<a href="/schedule" style="font-size:12px;padding:5px 12px;border-radius:6px;'
        'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Schedule</a>'
        '</div>'
    )

    num_days = len(metrics.get("daily_projection", []))

    return f"""<div class="section" style="padding-bottom:8px;">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
    {logo_html}
    <h1 style="font-size:22px;font-weight:700;margin:0;">Cash Flow Forecast</h1>
  </div>
  <div style="font-size:14px;color:var(--muted);margin-bottom:2px;">
    Next {num_days} Days
  </div>
  <div style="font-size:12px;color:var(--muted);">
    {_safe(data_note)}
  </div>
  {sub_nav}
</div>"""


# ---------------------------------------------------------------------------
# KPI Grid
# ---------------------------------------------------------------------------


def _render_kpis(metrics: dict) -> str:
    starting = metrics.get("starting_balance", 0)
    ending = metrics.get("ending_balance", 0)
    total_rev = metrics.get("total_revenue", 0)
    total_exp = metrics.get("total_expenses", 0)
    total_net = metrics.get("total_net", 0)

    # Color for ending balance
    if ending >= 10000:
        end_color = _GREEN
    elif ending >= 5000:
        end_color = _ORANGE
    else:
        end_color = _RED

    # Color for net flow
    net_color = _GREEN if total_net >= 0 else _RED

    stats = [
        render_stat("Starting Balance", fmt_currency(starting)),
        render_stat("Projected Ending", fmt_currency(ending), color=end_color),
        render_stat("Total Revenue", fmt_currency(total_rev), color=_GREEN),
        render_stat("Total Expenses", fmt_currency(total_exp), color=_RED),
        render_stat("Net Flow", fmt_currency(total_net), color=net_color),
    ]

    return render_card("Overview", render_stat_grid(stats))


# ---------------------------------------------------------------------------
# Main Mixed Chart: Revenue bars + Expense bars + Balance line
# ---------------------------------------------------------------------------


def _render_main_chart(metrics: dict) -> str:
    projection = metrics.get("daily_projection", [])
    if not projection:
        return render_card("Cash Flow Chart", "<p>No projection data available.</p>")

    labels = [d["date_display"] for d in projection]
    revenues = [round(d["revenue_projected"] + d["catering_expected"], 2) for d in projection]
    expenses = [round(-d["total_expenses"], 2) for d in projection]  # negative for below axis
    balances = [round(d["running_balance"], 2) for d in projection]

    # Highlight payroll days in expenses with a different shade
    expense_colors = []
    for d in projection:
        if d["is_payroll_day"]:
            expense_colors.append("rgba(232,168,48,0.85)")  # orange for payroll
        else:
            expense_colors.append(_RED_LIGHT)

    cid = _next_chart_id()
    threshold = metrics.get("assumptions", {}).get("balance_threshold", 5000)

    # Build custom mixed chart since we need bar + line with dual y-axes
    chart_js = f"""
    <div class="lvc" style="height:320px;position:relative;"><canvas id="{cid}"></canvas></div>
    <script>
    new Chart(document.getElementById("{cid}"), {{
        type: 'bar',
        data: {{
            labels: {_js(labels)},
            datasets: [
                {{
                    type: 'bar',
                    label: 'Revenue',
                    data: {_js(revenues)},
                    backgroundColor: '{_GREEN_LIGHT}',
                    borderRadius: 4,
                    borderSkipped: false,
                    order: 2
                }},
                {{
                    type: 'bar',
                    label: 'Expenses',
                    data: {_js(expenses)},
                    backgroundColor: {_js(expense_colors)},
                    borderRadius: 4,
                    borderSkipped: false,
                    order: 2
                }},
                {{
                    type: 'line',
                    label: 'Balance',
                    data: {_js(balances)},
                    borderColor: '{_BLUE}',
                    backgroundColor: 'rgba(74,156,216,0.08)',
                    borderWidth: 2.5,
                    pointRadius: 3,
                    pointBackgroundColor: '{_BLUE}',
                    fill: false,
                    tension: 0.3,
                    yAxisID: 'y2',
                    order: 1
                }}
            ]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{
                legend: {{
                    display: true,
                    labels: {{
                        font: {{ family: 'DM Sans', size: 11 }},
                        usePointStyle: true,
                        padding: 16
                    }}
                }},
                tooltip: {{
                    mode: 'index',
                    intersect: false,
                    callbacks: {{
                        label: function(c) {{
                            var v = c.raw;
                            var prefix = v < 0 ? '-$' : '$';
                            return c.dataset.label + ': ' + prefix + Math.abs(v).toLocaleString();
                        }}
                    }}
                }}
            }},
            scales: {{
                x: {{
                    grid: {{ display: false }},
                    ticks: {{ font: {{ family: 'DM Sans', size: 10 }}, maxRotation: 45 }}
                }},
                y: {{
                    grid: {{ color: 'rgba(0,0,0,0.05)' }},
                    ticks: {{
                        callback: function(v) {{ return (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString(); }},
                        font: {{ family: 'DM Sans', size: 11 }}
                    }}
                }},
                y2: {{
                    position: 'right',
                    grid: {{ display: false }},
                    ticks: {{
                        callback: function(v) {{ return '$' + v.toLocaleString(); }},
                        font: {{ family: 'DM Sans', size: 11 }}
                    }}
                }}
            }}
        }},
        plugins: [{{
            id: 'thresholdLine',
            afterDraw: function(chart) {{
                var scale = chart.scales.y2;
                var yPos = scale.getPixelForValue({threshold});
                var ctx = chart.ctx;
                ctx.save();
                ctx.strokeStyle = '{_RED}';
                ctx.lineWidth = 1.5;
                ctx.setLineDash([6, 4]);
                ctx.beginPath();
                ctx.moveTo(chart.chartArea.left, yPos);
                ctx.lineTo(chart.chartArea.right, yPos);
                ctx.stroke();
                ctx.font = '10px DM Sans';
                ctx.fillStyle = '{_RED}';
                ctx.fillText('Min Balance', chart.chartArea.right - 72, yPos - 5);
                ctx.restore();
            }}
        }}]
    }});
    </script>
    """

    return render_card(
        "Cash Flow Projection",
        chart_js,
        subtitle="Green = revenue in, red = expenses out, blue line = running balance"
    )


# ---------------------------------------------------------------------------
# Danger Zones
# ---------------------------------------------------------------------------


def _render_danger_zones(metrics: dict) -> str:
    dangers = metrics.get("danger_zones", [])
    if not dangers:
        return ""

    threshold = metrics.get("assumptions", {}).get("balance_threshold", 5000)

    alerts = []
    for dz in dangers:
        alerts.append(
            f'<div class="cf-danger">'
            f'<strong>{_safe(dz["date_display"])}</strong> &mdash; '
            f'Balance drops to <strong>{fmt_currency(dz["balance"])}</strong>, '
            f'which is {fmt_currency(dz["shortfall"])} below the '
            f'{fmt_currency(threshold)} threshold.'
            f'</div>'
        )

    header_text = render_insight(
        f"<strong>{len(dangers)} day{'s' if len(dangers) != 1 else ''}</strong> "
        f"where projected balance falls below {fmt_currency(threshold)}",
        severity="red",
        tag="Cash Alert",
    )

    content = header_text + "\n" + "\n".join(alerts)
    return render_card("Danger Zones", content)


# ---------------------------------------------------------------------------
# Daily Detail Table
# ---------------------------------------------------------------------------


def _render_daily_table(metrics: dict) -> str:
    projection = metrics.get("daily_projection", [])
    if not projection:
        return ""

    threshold = metrics.get("assumptions", {}).get("balance_threshold", 5000)

    rows_html = []
    for d in projection:
        balance = d["running_balance"]
        net = d["net_flow"]

        # Balance color class
        if balance < threshold:
            bal_class = "balance-neg"
        elif balance < threshold * 2:
            bal_class = "balance-warn"
        else:
            bal_class = "balance-ok"

        # Net color
        net_color = _GREEN if net >= 0 else _RED

        # Row class for payroll highlight
        row_class = ' class="payroll-row"' if d["is_payroll_day"] else ""

        # Payroll indicator
        payroll_tag = ""
        if d["is_payroll_day"]:
            payroll_tag = '<span style="font-size:9px;background:rgba(232,168,48,0.15);color:#e8a830;padding:1px 5px;border-radius:3px;margin-left:4px;font-weight:600;">PAYROLL</span>'

        # Catering indicator
        catering_tag = ""
        if d["catering_expected"] > 0:
            catering_tag = f'<span class="catering-chip">{fmt_currency(d["catering_expected"])}</span>'

        # Revenue source indicator
        source_dot = ""
        if d["revenue_source"] == "forecast":
            source_dot = '<span style="color:#4a9cd8;font-size:8px;vertical-align:super;" title="AI forecast">&#9679;</span>'

        rows_html.append(
            f'<tr{row_class}>'
            f'<td style="font-weight:500;white-space:nowrap;">{_safe(d["date_display"])}{payroll_tag}</td>'
            f'<td style="font-size:12px;color:var(--muted);">{_safe(d["day_of_week"])}</td>'
            f'<td class="r n">{fmt_currency(d["revenue_projected"])}{source_dot}</td>'
            f'<td class="r n" style="color:{_RED};">{fmt_currency(d["total_expenses"])}</td>'
            f'<td class="r n">{catering_tag if d["catering_expected"] > 0 else "&mdash;"}</td>'
            f'<td class="r n" style="color:{net_color};">{fmt_currency(net)}</td>'
            f'<td class="r n"><span class="{bal_class}">{fmt_currency(balance)}</span></td>'
            f'</tr>'
        )

    # Totals row
    total_rev = metrics.get("total_revenue", 0)
    total_exp = metrics.get("total_expenses", 0)
    total_net = metrics.get("total_net", 0)
    ending = metrics.get("ending_balance", 0)
    net_color = _GREEN if total_net >= 0 else _RED
    bal_class = "balance-neg" if ending < threshold else "balance-ok"

    rows_html.append(
        f'<tr style="border-top:2px solid var(--border);font-weight:700;">'
        f'<td colspan="2">TOTAL</td>'
        f'<td class="r n">{fmt_currency(total_rev)}</td>'
        f'<td class="r n" style="color:{_RED};">{fmt_currency(total_exp)}</td>'
        f'<td></td>'
        f'<td class="r n" style="color:{net_color};">{fmt_currency(total_net)}</td>'
        f'<td class="r n"><span class="{bal_class}">{fmt_currency(ending)}</span></td>'
        f'</tr>'
    )

    table_html = (
        '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;">'
        '<table class="data-table" style="width:100%;font-size:12px;">'
        '<thead><tr>'
        '<th style="text-align:left;">Date</th>'
        '<th style="text-align:left;">Day</th>'
        '<th style="text-align:right;">Revenue</th>'
        '<th style="text-align:right;">Expenses</th>'
        '<th style="text-align:right;">Catering</th>'
        '<th style="text-align:right;">Net</th>'
        '<th style="text-align:right;">Balance</th>'
        '</tr></thead>'
        '<tbody>' + "\n".join(rows_html) + '</tbody>'
        '</table></div>'
    )

    # Legend
    legend = (
        '<div style="font-size:11px;color:var(--muted);margin-top:8px;">'
        '<span style="color:#4a9cd8;font-size:8px;">&#9679;</span> = AI forecast &nbsp;'
        '<span style="background:rgba(232,168,48,0.15);color:#e8a830;padding:1px 5px;'
        'border-radius:3px;font-size:9px;font-weight:600;">PAYROLL</span> = payroll day &nbsp;'
        '<span style="color:#e86040;">Red balance</span> = below threshold'
        '</div>'
    )

    return render_card("Daily Breakdown", table_html + legend)


# ---------------------------------------------------------------------------
# Upcoming Catering
# ---------------------------------------------------------------------------


def _render_catering_upcoming(metrics: dict) -> str:
    orders = metrics.get("upcoming_catering", [])
    if not orders:
        return ""

    rows = []
    for o in orders:
        rows.append([
            _safe(o.get("date", "")),
            _safe(o.get("name", "Unknown")),
            _safe(o.get("platform", "")),
            fmt_currency(o.get("subtotal", 0)),
        ])

    table = render_table(
        headers=["Date", "Customer", "Platform", "Amount"],
        rows=rows,
        right_align_cols=[3],
    )

    total_catering = sum(o.get("subtotal", 0) for o in orders)
    summary = (
        f'<div style="font-size:13px;margin-bottom:10px;">'
        f'<strong>{len(orders)}</strong> upcoming orders totaling '
        f'<strong style="color:{_GREEN};">{fmt_currency(total_catering)}</strong>'
        f'</div>'
    )

    return render_card("Upcoming Catering", summary + table)


# ---------------------------------------------------------------------------
# Assumptions Card
# ---------------------------------------------------------------------------


def _render_assumptions(metrics: dict) -> str:
    a = metrics.get("assumptions", {})

    items = [
        ("Avg Daily Revenue", fmt_currency(a.get("avg_daily_revenue", 0))),
        ("Avg Weekly Labor", fmt_currency(a.get("avg_weekly_labor", 0))),
        ("Avg Weekly Vendor", fmt_currency(a.get("avg_weekly_vendor", 0))),
        ("Vendor Data", _safe(a.get("vendor_data_source", "estimated"))),
        ("Balance Threshold", fmt_currency(a.get("balance_threshold", 5000))),
        ("Biweekly Payroll", fmt_currency(a.get("biweekly_payroll", 0))),
        ("Forecast Model", "Active" if a.get("forecast_available") else "Using historical averages"),
        ("Lookback Period", f"{a.get('lookback_days', 28)} days ({a.get('hist_days_found', 0)} found)"),
    ]

    grid_items = []
    for label, value in items:
        grid_items.append(
            f'<div class="assumption-item">'
            f'<strong>{_safe(value)}</strong>'
            f'{_safe(label)}'
            f'</div>'
        )

    grid = (
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;">'
        + "\n".join(grid_items) +
        '</div>'
    )

    note = (
        '<div style="font-size:11px;color:var(--muted);margin-top:12px;line-height:1.6;">'
        'Revenue projections use the AI forecast model when available, otherwise '
        'day-of-week averages from the last 4 weeks. Labor is spread evenly across days '
        'except payroll days (biweekly Fridays) which show the full payroll hit. '
        'Vendor spend is pulled from Notion price entries when available, otherwise '
        'estimated at ~24% of revenue.'
        '</div>'
    )

    return render_card("Forecast Assumptions", grid + note)


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------


def _render_footer() -> str:
    return """<div class="section" style="text-align:center;font-size:11px;color:var(--muted);padding:16px 0 32px;">
  Livite Cash Flow Forecast &mdash; Generated from Toast POS + Notion + AI Forecast
  <br><a href="/" style="color:var(--livite-green);text-decoration:none;">Back to Home</a>
</div>"""
