"""
Build self-contained HTML page for the P&L Forecast dashboard.

Renders real line-by-line P&L structure matching accounting:
Income -> COGS -> Gross Profit -> 8 OpEx groups -> Operating Income -> Net Income

Includes next-week daily projections and last-week actual-vs-predicted review.

Uses existing Chart.js helpers and Livite theme from htmlrender package.
"""

from __future__ import annotations

import json as _json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from htmlrender.components import (
    _reset_chart_counter, render_chartjs_bar, render_chartjs_line,
    render_stat, render_stat_grid, render_card,
    render_table, fmt_currency, fmt_pct, fmt_num, _safe,
    LIVITE_CHART_COLORS, _next_chart_id, _js,
)
from htmlrender.sections import _CSS

from .data import EXPENSE_GROUP_ORDER


_GREEN = "#8cb82e"
_GREEN_LIGHT = "rgba(140,184,46,0.35)"
_BLUE = "#4a9cd8"
_PURPLE = "#9b72c4"
_RED = "#e86040"
_DARK_GREEN = "#475417"
_ORANGE = "#e8a830"


def build_forecast_page(
    metrics: dict,
    logo_b64: str = "",
) -> str:
    """Build the complete P&L Forecast dashboard HTML page."""
    _reset_chart_counter()

    sections = []
    sections.append(_render_header(metrics, logo_b64))
    sections.append(_render_kpis(metrics))
    sections.append(_render_next_week(metrics))
    sections.append(_render_week_review(metrics))
    sections.append(_render_backtest(metrics))
    sections.append(_render_annual_2026(metrics))
    sections.append(_render_weekly_forecast(metrics))
    sections.append(_render_revenue_forecast(metrics))
    sections.append(_render_channel_breakdown(metrics))
    sections.append(_render_discount_health(metrics))
    sections.append(_render_pl_table(metrics))
    sections.append(_render_dow_pattern(metrics))
    sections.append(_render_seasonal_pattern(metrics))
    sections.append(_render_assumptions(metrics))
    sections.append(_render_footer())

    body = "\n\n".join(s for s in sections if s)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Livite P&amp;L Forecast</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
{_CSS}
.grid-2 {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
}}
@media (max-width: 700px) {{
    .grid-2 {{ grid-template-columns: 1fr; }}
}}
.pl-table-wrap {{
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
}}
.pl-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    font-family: 'JetBrains Mono', monospace;
    white-space: nowrap;
}}
.pl-table th, .pl-table td {{
    padding: 6px 10px;
    text-align: right;
    border-bottom: 1px solid var(--border);
}}
.pl-table th:first-child, .pl-table td:first-child {{
    text-align: left;
    font-family: 'DM Sans', sans-serif;
    font-weight: 500;
    position: sticky;
    left: 0;
    background: var(--surface);
    z-index: 1;
}}
.pl-table thead th {{
    font-size: 11px;
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.3px;
}}
.pl-table .forecast-col {{
    background: rgba(140,184,46,0.06);
}}
.pl-table .subtotal-row td {{
    font-weight: 600;
    border-top: 2px solid var(--border);
}}
.pl-table .subtotal-row td:first-child {{
    font-weight: 700;
}}
.pl-table .section-header td {{
    font-weight: 700;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--muted);
    padding-top: 12px;
    border-bottom: none;
}}
.pl-table .indent td:first-child {{
    padding-left: 20px;
    font-weight: 400;
    color: var(--muted);
}}
.assumption-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px;
}}
.assumption-item {{
    font-size: 12px;
    color: var(--muted);
}}
.assumption-item strong {{
    display: block;
    font-size: 14px;
    color: var(--text);
    font-weight: 600;
    margin-bottom: 2px;
}}
.next-week-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 8px;
    margin-bottom: 16px;
}}
.day-card {{
    background: var(--bg);
    border-radius: 8px;
    padding: 12px;
    text-align: center;
}}
.day-card .day-name {{
    font-size: 11px;
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
.day-card .day-date {{
    font-size: 10px;
    color: var(--muted);
    margin-bottom: 8px;
}}
.day-card .day-rev {{
    font-size: 18px;
    font-weight: 700;
    color: var(--text);
}}
.day-card .day-profit {{
    font-size: 12px;
    margin-top: 4px;
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
    assumptions = metrics.get("assumptions", {})
    data_start = assumptions.get("data_start", "")
    data_end = assumptions.get("data_end", "")
    data_days = assumptions.get("data_days", 0)
    has_acct = assumptions.get("has_accounting", False)
    acct_months = assumptions.get("accounting_months", 0)

    logo_html = ""
    if logo_b64:
        logo_html = (
            f'<img src="data:image/png;base64,{logo_b64}" '
            f'style="height:36px;margin-right:12px;" alt="Livite">'
        )

    acct_note = ""
    if has_acct:
        acct_note = f" | {acct_months} months of accounting P&amp;L data"

    sub_nav = (
        '<div style="display:flex;justify-content:center;gap:8px;margin-top:10px;flex-wrap:wrap;">'
        '<a href="/" style="font-size:12px;padding:5px 12px;border-radius:6px;'
        'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Home</a>'
        '<a href="/today" style="font-size:12px;padding:5px 12px;border-radius:6px;'
        'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Today</a>'
        '<a href="/week" style="font-size:12px;padding:5px 12px;border-radius:6px;'
        'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">This Week</a>'
        '<a href="/forecast" style="font-size:12px;padding:5px 12px;border-radius:6px;'
        'background:#475417;color:#fff;text-decoration:none;font-weight:600;">P&amp;L Forecast</a>'
        '<a href="/schedule" style="font-size:12px;padding:5px 12px;border-radius:6px;'
        'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Schedule</a>'
        '</div>'
    )

    return f"""<div class="section" style="padding-bottom:8px;">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
    {logo_html}
    <h1 style="font-size:22px;font-weight:700;margin:0;">P&amp;L Forecast</h1>
  </div>
  <div style="font-size:12px;color:var(--muted);">
    Based on {data_days} days of sales data ({_safe(data_start)} to {_safe(data_end)}){acct_note}
  </div>
  {sub_nav}
</div>"""


def _render_kpis(metrics: dict) -> str:
    kpis = metrics.get("kpis", {})
    expense_ratios = metrics.get("expense_ratios", {})

    annual_rev = kpis.get("annual_2026_revenue", 0)
    annual_net = kpis.get("annual_2026_net_income", 0)
    run_rate = kpis.get("run_rate", 0)
    growth_pct = kpis.get("monthly_growth_pct", 0)
    gross_margin = kpis.get("gross_margin_pct", 0)
    operating_margin = kpis.get("operating_margin_pct", 0)

    growth_color = _GREEN if growth_pct >= 0 else _RED
    growth_str = "%+.1f%%/mo" % growth_pct

    stats = [
        render_stat(
            "2026 Projected Revenue",
            fmt_currency(annual_rev),
            "Full year (actual + forecast)",
        ),
        render_stat(
            "2026 Projected Net Income",
            fmt_currency(annual_net),
            "After all operating expenses",
        ),
        render_stat(
            "Monthly Run Rate",
            fmt_currency(run_rate),
            "Trailing 3-month avg",
        ),
        render_stat(
            "Revenue Trend",
            growth_str,
            "Monthly growth rate",
            color=growth_color,
        ),
        render_stat(
            "Gross Margin",
            fmt_pct(gross_margin, 1),
            "Revenue less COGS",
        ),
        render_stat(
            "Operating Margin",
            fmt_pct(operating_margin, 1),
            "After all operating expenses",
        ),
    ]
    return render_stat_grid(stats)


# ---------------------------------------------------------------------------
# Next-Week Daily Projection
# ---------------------------------------------------------------------------


def _render_next_week(metrics: dict) -> str:
    """Day-by-day P&L projection for the upcoming week."""
    nw = metrics.get("next_week", {})
    days = nw.get("days", [])
    totals = nw.get("totals", {})
    week_label = nw.get("week_label", "")

    if not days:
        return ""

    # Day cards with revenue + net income + weather
    cards_html = '<div class="next-week-grid">'
    for d in days:
        rev = d.get("revenue_total", 0)
        net = d.get("net_income", 0)
        net_color = _GREEN if net >= 0 else _RED

        # Weather badge
        weather_badge = ""
        w = d.get("weather")
        if w and w.get("conditions"):
            temp = w.get("temp_high")
            temp_str = "%d&deg;" % int(temp) if temp is not None else ""
            cond = _safe(w["conditions"])
            mult = w.get("multiplier", 1.0)
            badge_color = _RED if mult < 0.95 else "var(--muted)"
            weather_badge = (
                '<div style="font-size:10px;color:%s;margin-top:2px;">%s %s</div>'
                % (badge_color, temp_str, cond)
            )
            if mult < 1.0:
                weather_badge += (
                    '<div style="font-size:9px;color:%s;">%+.0f%%</div>'
                    % (_RED, (mult - 1) * 100)
                )

        # Per-channel line
        ch_in = d.get("channel_instore", 0)
        ch_del = d.get("channel_delivery", 0)
        ch_cat = d.get("channel_catering", 0)
        channel_line = ""
        if ch_in > 0 or ch_del > 0:
            channel_line = (
                '<div style="font-size:9px;color:var(--muted);margin-top:2px;">'
                'In %s | Del %s' % (fmt_currency(ch_in), fmt_currency(ch_del))
            )
            if ch_cat > 0:
                channel_line += ' | Cat %s' % fmt_currency(ch_cat)
            channel_line += '</div>'

        cards_html += f"""<div class="day-card">
  <div class="day-name">{_safe(d.get("dow_name", ""))}</div>
  <div class="day-date">{_safe(d.get("date", "")[-5:])}</div>
  <div class="day-rev">{fmt_currency(rev)}</div>
  {channel_line}
  <div class="day-profit" style="color:{net_color};">{fmt_currency(net)}</div>
  {weather_badge}
</div>"""
    cards_html += "</div>"

    # Explanation details (collapsible)
    explain_html = _render_next_week_explanations(days)

    # Weekly total summary
    total_rev = totals.get("revenue_total", 0)
    total_net = totals.get("net_income", 0)
    total_gp = totals.get("gross_profit", 0)
    total_cogs = totals.get("cogs", 0)
    total_labor = totals.get("labor", 0)
    total_3p = totals.get("third_party_fees", 0)

    summary_html = f"""<div style="display:flex;flex-wrap:wrap;gap:24px;font-size:13px;margin-top:8px;">
  <div><strong>Week Total:</strong> {fmt_currency(total_rev)}</div>
  <div><strong>COGS:</strong> ({fmt_currency(abs(total_cogs))})</div>
  <div><strong>Gross Profit:</strong> {fmt_currency(total_gp)}</div>
  <div><strong>Labor:</strong> ({fmt_currency(abs(total_labor))})</div>
  <div><strong>3P Fees:</strong> ({fmt_currency(abs(total_3p))})</div>
  <div><strong>Net Income:</strong> <span style="color:{_GREEN if total_net >= 0 else _RED};">{fmt_currency(total_net)}</span></div>
</div>"""

    # Detailed P&L table for the week
    table_html = _render_weekly_pl_detail(days, totals)

    return render_card(
        "Next Week Projection: %s" % _safe(week_label),
        cards_html + explain_html + summary_html + table_html,
        subtitle="Daily revenue and P&L forecast for the coming week",
    )


def _render_next_week_explanations(days: list) -> str:
    """Render collapsible explanation for each day's prediction."""
    has_any = any(d.get("explanation") for d in days)
    if not has_any:
        return ""

    rows = []
    for d in days:
        expl = d.get("explanation", {})
        if not expl:
            continue
        dow = _safe(expl.get("dow_name", d.get("dow_name", "")))
        narrative = _safe(expl.get("narrative", ""))
        dow_pct = expl.get("dow_effect_pct", 0)
        seas_pct = expl.get("seasonal_effect_pct", 0)
        trend_val = expl.get("trend_value", 0)

        dow_color = _GREEN if dow_pct >= 0 else _RED
        seas_color = _GREEN if seas_pct >= 0 else _RED

        # Weather effect (if present)
        weather_html = ""
        weather_pct = expl.get("weather_effect_pct")
        if weather_pct and weather_pct != 0:
            weather_color = _RED if weather_pct < 0 else _GREEN
            weather_reason = _safe(expl.get("weather_reason", ""))
            weather_html = (
                '<span style="color:%s;">Weather %+.0f%%</span>'
                % (weather_color, weather_pct)
            )
            if weather_reason:
                weather_html += (
                    '<span style="font-size:10px;color:var(--muted);">(%s)</span>'
                    % weather_reason
                )

        # Per-channel mini breakdown
        ch_html = ""
        ch_in = expl.get("channel_instore", 0)
        ch_del = expl.get("channel_delivery", 0)
        if ch_in > 0 or ch_del > 0:
            ch_html = (
                '<span style="font-size:10px;color:var(--muted);">'
                '[In: $%s | Del: $%s]</span>'
                % ("{:,.0f}".format(ch_in), "{:,.0f}".format(ch_del))
            )

        rows.append(
            '<div style="display:flex;gap:12px;align-items:baseline;'
            'padding:4px 0;font-size:12px;">'
            '<strong style="min-width:80px;">%s</strong>'
            '<span>Trend $%s</span>'
            '<span style="color:%s;">DOW %+.1f%%</span>'
            '<span style="color:%s;">Seasonal %+.1f%%</span>'
            '%s %s'
            '</div>'
            % (dow, "{:,.0f}".format(trend_val),
               dow_color, dow_pct, seas_color, seas_pct,
               weather_html, ch_html)
        )

    content = "\n".join(rows)
    return (
        '<details style="margin-top:12px;">'
        '<summary style="cursor:pointer;font-size:12px;color:var(--muted);'
        'font-weight:600;">Why these numbers? (click to expand)</summary>'
        '<div style="padding:8px 0;">' + content + '</div>'
        '</details>'
    )


def _render_weekly_pl_detail(days: list, totals: dict) -> str:
    """Render a detailed daily P&L table for one week."""
    if not days:
        return ""

    header_cells = ['<th></th>']
    for d in days:
        header_cells.append(f'<th>{_safe(d.get("dow_name", ""))}</th>')
    header_cells.append('<th style="border-left:2px solid var(--border);">Total</th>')

    # P&L rows definition: (label, key, is_cost, is_subtotal, is_section_header, is_indent)
    pl_rows = [
        ("REVENUE", None, False, False, True, False),
        ("In-Store", "revenue_instore", False, False, False, True),
        ("Delivery", "revenue_delivery", False, False, False, True),
        ("Catering", "revenue_catering", False, False, False, True),
        ("Total Revenue", "revenue_total", False, True, False, False),
        ("Cost of Goods Sold", "cogs", True, False, False, False),
        ("Gross Profit", "gross_profit", False, True, False, False),
        ("OPERATING EXPENSES", None, False, False, True, False),
    ]
    for key, label in EXPENSE_GROUP_ORDER:
        pl_rows.append((label, key, True, False, False, True))
    pl_rows += [
        ("Total Operating Expenses", "total_opex", True, True, False, False),
        ("Operating Income", "operating_income", False, True, False, False),
        ("Net Income", "net_income", False, True, False, False),
    ]

    body_rows = []
    for label, key, is_cost, is_subtotal, is_section, is_indent in pl_rows:
        if is_section:
            body_rows.append(
                '<tr class="section-header"><td colspan="%d">%s</td></tr>'
                % (len(days) + 2, _safe(label))
            )
            continue

        row_cls = ""
        if is_subtotal:
            row_cls = ' class="subtotal-row"'
        elif is_indent:
            row_cls = ' class="indent"'

        cells = [f'<td>{_safe(label)}</td>']
        for d in days:
            val = d.get(key, 0)
            if is_cost and not is_subtotal:
                formatted = "(%s)" % fmt_currency(abs(val))
            else:
                formatted = fmt_currency(val)
            cells.append(f'<td>{formatted}</td>')

        # Total column
        total_val = totals.get(key, 0)
        if is_cost and not is_subtotal:
            fmt_total = "(%s)" % fmt_currency(abs(total_val))
        else:
            fmt_total = fmt_currency(total_val)
        cells.append(
            '<td style="border-left:2px solid var(--border);font-weight:600;">'
            '%s</td>' % fmt_total
        )
        body_rows.append(f'<tr{row_cls}>{"".join(cells)}</tr>')

    return f"""<div class="pl-table-wrap" style="margin-top:16px;">
<table class="pl-table">
<thead><tr>{"".join(header_cells)}</tr></thead>
<tbody>{"".join(body_rows)}</tbody>
</table>
</div>"""


# ---------------------------------------------------------------------------
# Last-Week Review
# ---------------------------------------------------------------------------


def _render_week_review(metrics: dict) -> str:
    """Show last week's actual vs predicted comparison."""
    wr = metrics.get("week_review", {})
    days = wr.get("days", [])
    summary = wr.get("summary", {})
    week_label = wr.get("week_label", "")

    if not days:
        return ""

    # Only show if we have actual data
    days_with_data = summary.get("days_with_data", 0)
    if days_with_data == 0:
        return ""

    # Chart: actual vs predicted bars
    labels = [d["dow_name"] for d in days if d.get("actual") is not None]
    actual_data = [d["actual"] for d in days if d.get("actual") is not None]
    pred_data = [d["predicted"] for d in days if d.get("actual") is not None]

    datasets = [
        {"label": "Actual", "data": actual_data, "color": _GREEN},
        {"label": "Predicted", "data": pred_data, "color": _BLUE},
    ]
    chart = render_chartjs_bar(labels, datasets, height=220, dollar=True, show_legend=True)

    # Daily comparison table
    headers = ["Day", "Actual", "Predicted", "Diff", "Error"]
    rows = []
    for d in days:
        if d.get("actual") is None:
            continue
        err = d.get("error_pct", 0)
        err_color = _GREEN if abs(err) < 5 else _ORANGE if abs(err) < 10 else _RED
        rows.append([
            _safe(d["dow_name"]),
            fmt_currency(d["actual"]),
            fmt_currency(d["predicted"]),
            fmt_currency(d["diff"]),
            '<span style="color:%s">%+.1f%%</span>' % (err_color, err),
        ])

    tbl = render_table(headers, rows)

    # Summary line
    total_err = summary.get("total_error_pct")
    err_color = _GREEN if total_err is not None and abs(total_err) < 5 else _RED
    err_str = "%+.1f%%" % total_err if total_err is not None else "N/A"
    summary_html = (
        f'<div style="font-size:12px;color:var(--muted);margin-top:8px;">'
        f'Week total: Actual {fmt_currency(summary.get("total_actual", 0))} '
        f'vs Predicted {fmt_currency(summary.get("total_predicted", 0))} '
        f'(<strong style="color:{err_color}">{err_str}</strong> error)'
        f'</div>'
    )

    return render_card(
        "Last Week Review: %s" % _safe(week_label),
        chart + tbl + summary_html,
        subtitle="Actual vs model prediction for prior week",
    )


# ---------------------------------------------------------------------------
# Revenue Forecast Chart
# ---------------------------------------------------------------------------


def _render_revenue_forecast(metrics: dict) -> str:
    """Main chart: actual + forecast monthly revenue bars with trend line."""
    actuals = metrics.get("actuals", [])
    forecast = metrics.get("forecast", [])

    if not actuals:
        return ""

    display_actuals = [m for m in actuals if m["days"] >= 15]
    all_months = display_actuals + forecast

    labels = [m["label"] for m in all_months]

    bar_colors = []
    for m in all_months:
        if m.get("is_forecast"):
            bar_colors.append(_GREEN_LIGHT)
        else:
            bar_colors.append(_GREEN)

    revenue_data = [m["revenue_total"] for m in all_months]

    # Trend line
    trend_data = []
    if len(all_months) >= 2:
        first = all_months[0]["revenue_total"]
        last = all_months[-1]["revenue_total"]
        n = len(all_months)
        for i in range(n):
            trend_data.append(round(first + (last - first) * i / (n - 1), 0))

    datasets = [
        {
            "label": "Revenue",
            "data": revenue_data,
            "colors": bar_colors,
        },
    ]

    if trend_data:
        datasets.append({
            "label": "Trend",
            "data": trend_data,
            "type": "line",
            "color": _DARK_GREEN,
            "borderColor": _DARK_GREEN,
            "borderWidth": 2,
            "pointRadius": 0,
            "borderDash": [6, 3],
        })

    chart = render_chartjs_bar(
        labels, datasets, height=400, dollar=True, show_legend=True,
    )

    note = (
        '<div style="font-size:11px;color:var(--muted);margin-top:6px;">'
        'Solid bars = actual &nbsp;|&nbsp; '
        'Lighter bars = forecast'
        '</div>'
    )

    return render_card("Revenue Forecast", chart + note,
                       subtitle="Monthly actual + projected revenue")


# ---------------------------------------------------------------------------
# Channel Breakdown
# ---------------------------------------------------------------------------


def _render_channel_breakdown(metrics: dict) -> str:
    """Stacked bar chart showing In-Store / Delivery / Catering split."""
    actuals = metrics.get("actuals", [])
    forecast = metrics.get("forecast", [])

    display_actuals = [m for m in actuals if m["days"] >= 15]
    all_months = display_actuals + forecast

    if not all_months:
        return ""

    labels = [m["label"] for m in all_months]

    datasets = [
        {
            "label": "In-Store",
            "data": [m.get("revenue_instore", 0) for m in all_months],
            "color": _GREEN,
        },
        {
            "label": "Delivery",
            "data": [m.get("revenue_delivery", 0) for m in all_months],
            "color": _BLUE,
        },
        {
            "label": "Catering",
            "data": [m.get("revenue_catering", 0) for m in all_months],
            "color": _PURPLE,
        },
    ]

    chart = render_chartjs_bar(
        labels, datasets, height=300, dollar=True,
        stacked=True, show_legend=True,
    )

    # Channel trend summary
    ch_trends = metrics.get("channel_trends", {})
    trend_note = ""
    if ch_trends:
        parts = []
        for ch, label in [("instore", "In-Store"), ("delivery", "Delivery")]:
            t = ch_trends.get(ch, {})
            slope = t.get("slope", 0)
            if slope != 0:
                direction = "growing" if slope > 0 else "declining"
                daily_delta = abs(slope)
                monthly_delta = daily_delta * 30
                parts.append(
                    "%s is %s at ~$%.0f/month" % (label, direction, monthly_delta)
                )
        if parts:
            trend_note = (
                '<div style="font-size:12px;color:var(--muted);margin-top:8px;">'
                + ". ".join(parts) + ". "
                "Channels are forecast independently using separate DOW, seasonal, "
                "and trend models."
                '</div>'
            )

    return render_card("Revenue by Channel", chart + trend_note,
                       subtitle="In-Store / Delivery / Catering breakdown")


# ---------------------------------------------------------------------------
# Discount Health
# ---------------------------------------------------------------------------


def _render_discount_health(metrics: dict) -> str:
    """Render delivery discount health card."""
    disc = metrics.get("discount_metrics", {})
    if not disc:
        return ""

    rate_now = disc.get("delivery_discount_rate_current", 0)
    rate_3mo = disc.get("delivery_discount_rate_3mo_ago", 0)
    trend = disc.get("delivery_discount_trend", "stable")
    bogo = disc.get("bogo_annualized", 0)
    daily_disc = disc.get("daily_delivery_discount", 0)

    if rate_now < 0.5 and bogo < 1000:
        return ""

    trend_color = _RED if trend == "rising" else (_GREEN if trend == "falling" else _ORANGE)

    stats = [
        render_stat("Delivery Discount Rate", "%.1f%%" % rate_now,
                    subtitle="vs %.1f%% 3 months ago" % rate_3mo,
                    color=trend_color),
        render_stat("Trend", trend.title(), color=trend_color),
        render_stat("Daily BOGO Cost", fmt_currency(daily_disc)),
        render_stat("Annualized", fmt_currency(bogo),
                    subtitle="Projected delivery discounts/yr"),
    ]

    content = render_stat_grid(stats)

    if trend == "rising" and rate_now > 3:
        content += (
            '<div style="margin-top:10px;padding:10px;background:rgba(232,96,64,0.08);'
            'border-radius:8px;font-size:12px;color:%s;">'
            'Delivery discounts have risen significantly. '
            'Uber BOGO and similar promotions are inflating delivery revenue. '
            'The forecast model accounts for this by forecasting each channel independently.'
            '</div>' % _RED
        )

    return render_card("Discount Health", content,
                       subtitle="Delivery platform promotional discounts")


# ---------------------------------------------------------------------------
# Monthly P&L Table (full line-by-line)
# ---------------------------------------------------------------------------


def _render_pl_table(metrics: dict) -> str:
    """Monthly P&L table with real line items, actual and forecast columns."""
    actuals = metrics.get("actuals", [])
    forecast = metrics.get("forecast", [])

    display_actuals = [m for m in actuals if m["days"] >= 15]
    all_months = display_actuals + forecast

    if not all_months:
        return ""

    header_cells = ['<th></th>']
    for m in all_months:
        cls = ' class="forecast-col"' if m.get("is_forecast") else ""
        header_cells.append(f'<th{cls}>{_safe(m["label"])}</th>')

    body_rows = _build_pl_body_rows(all_months)

    table_html = f"""<div class="pl-table-wrap">
<table class="pl-table">
<thead><tr>{"".join(header_cells)}</tr></thead>
<tbody>{"".join(body_rows)}</tbody>
</table>
</div>"""

    return render_card("Monthly P&amp;L", table_html,
                       subtitle="Actual vs. projected profit and loss (line-by-line)")


def _build_pl_body_rows(all_months: list) -> list:
    """Build the P&L table body rows with full line items."""
    # Row definitions: (label, key, is_cost, is_subtotal, is_section_header, is_indent)
    pl_rows = [
        ("REVENUE", None, False, False, True, False),
        ("In-Store", "revenue_instore", False, False, False, True),
        ("Delivery", "revenue_delivery", False, False, False, True),
        ("Catering", "revenue_catering", False, False, False, True),
        ("Total Revenue", "revenue_total", False, True, False, False),
        ("Cost of Goods Sold", "cogs", True, False, False, False),
        ("Gross Profit", "gross_profit", False, True, False, False),
        ("OPERATING EXPENSES", None, False, False, True, False),
    ]
    for key, label in EXPENSE_GROUP_ORDER:
        pl_rows.append((label, key, True, False, False, True))
    pl_rows += [
        ("Total Operating Expenses", "total_opex", True, True, False, False),
        ("Operating Income", "operating_income", False, True, False, False),
        ("Other Income", "other_income", False, False, False, False),
        ("Net Income", "net_income", False, True, False, False),
    ]

    body_rows = []
    for label, key, is_cost, is_subtotal, is_section, is_indent in pl_rows:
        if is_section:
            body_rows.append(
                '<tr class="section-header"><td colspan="%d">%s</td></tr>'
                % (len(all_months) + 1, _safe(label))
            )
            continue

        row_cls = ""
        if is_subtotal:
            row_cls = ' class="subtotal-row"'
        elif is_indent:
            row_cls = ' class="indent"'

        cells = [f'<td>{_safe(label)}</td>']
        for m in all_months:
            val = m.get(key, 0)
            cls = ' class="forecast-col"' if m.get("is_forecast") else ""
            if is_cost and not is_subtotal:
                formatted = "(%s)" % fmt_currency(abs(val))
            else:
                formatted = fmt_currency(val)
            cells.append(f'<td{cls}>{formatted}</td>')

        body_rows.append(f'<tr{row_cls}>{"".join(cells)}</tr>')

    return body_rows


# ---------------------------------------------------------------------------
# 2026 Annual Projection
# ---------------------------------------------------------------------------


def _render_annual_2026(metrics: dict) -> str:
    """Full 2026 P&L projection table (actual + forecast by month)."""
    annual = metrics.get("annual_2026", {})
    months_2026 = annual.get("months", [])
    annual_total = annual.get("annual_total", {})

    if not months_2026:
        return ""

    # Build header
    header_cells = ['<th></th>']
    for m in months_2026:
        cls = ' class="forecast-col"' if m.get("is_forecast") else ""
        header_cells.append(f'<th{cls}>{_safe(m["label"])}</th>')
    header_cells.append('<th style="border-left:2px solid var(--border);">2026 Total</th>')

    # Build body using same P&L structure
    pl_rows = [
        ("REVENUE", None, False, False, True, False),
        ("In-Store", "revenue_instore", False, False, False, True),
        ("Delivery", "revenue_delivery", False, False, False, True),
        ("Catering", "revenue_catering", False, False, False, True),
        ("Total Revenue", "revenue_total", False, True, False, False),
        ("Cost of Goods Sold", "cogs", True, False, False, False),
        ("Gross Profit", "gross_profit", False, True, False, False),
        ("OPERATING EXPENSES", None, False, False, True, False),
    ]
    for key, label in EXPENSE_GROUP_ORDER:
        pl_rows.append((label, key, True, False, False, True))
    pl_rows += [
        ("Total Operating Expenses", "total_opex", True, True, False, False),
        ("Operating Income", "operating_income", False, True, False, False),
        ("Net Income", "net_income", False, True, False, False),
    ]

    body_rows = []
    for label, key, is_cost, is_subtotal, is_section, is_indent in pl_rows:
        if is_section:
            body_rows.append(
                '<tr class="section-header"><td colspan="%d">%s</td></tr>'
                % (len(months_2026) + 2, _safe(label))
            )
            continue

        row_cls = ""
        if is_subtotal:
            row_cls = ' class="subtotal-row"'
        elif is_indent:
            row_cls = ' class="indent"'

        cells = [f'<td>{_safe(label)}</td>']
        for m in months_2026:
            val = m.get(key, 0)
            cls = ' class="forecast-col"' if m.get("is_forecast") else ""
            if is_cost and not is_subtotal:
                formatted = "(%s)" % fmt_currency(abs(val))
            else:
                formatted = fmt_currency(val)
            cells.append(f'<td{cls}>{formatted}</td>')

        # Annual total
        total_val = annual_total.get(key, 0)
        if is_cost and not is_subtotal:
            fmt_total = "(%s)" % fmt_currency(abs(total_val))
        else:
            fmt_total = fmt_currency(total_val)
        cells.append(
            '<td style="border-left:2px solid var(--border);font-weight:700;">'
            '%s</td>' % fmt_total
        )
        body_rows.append(f'<tr{row_cls}>{"".join(cells)}</tr>')

    actual_count = annual_total.get("months_actual", 0)
    forecast_count = annual_total.get("months_forecast", 0)

    table_html = f"""<div class="pl-table-wrap">
<table class="pl-table">
<thead><tr>{"".join(header_cells)}</tr></thead>
<tbody>{"".join(body_rows)}</tbody>
</table>
</div>
<div style="font-size:11px;color:var(--muted);margin-top:6px;">
{actual_count} months actual data, {forecast_count} months forecasted.
Solid columns = actual | Highlighted = forecast
</div>"""

    return render_card(
        "2026 Annual P&amp;L Projection",
        table_html,
        subtitle="Full year by month (actual where available, forecast for remaining)",
    )


# ---------------------------------------------------------------------------
# Weekly Forecast Table
# ---------------------------------------------------------------------------


def _render_weekly_forecast(metrics: dict) -> str:
    """Weekly revenue forecast chart + P&L table for 2026."""
    weeks = metrics.get("forecast_weeks", [])
    if not weeks:
        return ""

    # Chart: weekly revenue bars
    labels = [w["label"] for w in weeks]
    revenue_data = [w["revenue_total"] for w in weeks]

    datasets = [{
        "label": "Weekly Revenue",
        "data": revenue_data,
        "color": _GREEN,
    }]

    chart = render_chartjs_bar(labels, datasets, height=300, dollar=True)

    # Table with full P&L lines
    display_weeks = weeks[:52]
    headers = ["Week", "Revenue", "COGS", "Gross Profit", "Labor", "3P Fees", "Net Income"]
    rows = []
    for w in display_weeks:
        rows.append([
            _safe(w["label"]),
            fmt_currency(w["revenue_total"]),
            "(%s)" % fmt_currency(abs(w.get("cogs", 0))),
            fmt_currency(w.get("gross_profit", 0)),
            "(%s)" % fmt_currency(abs(w.get("labor", 0))),
            "(%s)" % fmt_currency(abs(w.get("third_party_fees", 0))),
            fmt_currency(w.get("net_income", 0)),
        ])

    tbl = render_table(headers, rows)

    return render_card(
        "2026 Weekly Forecast",
        chart + tbl,
        subtitle="Projected weekly revenue and P&L through Dec 2026",
    )


# ---------------------------------------------------------------------------
# DOW Pattern
# ---------------------------------------------------------------------------


def _render_dow_pattern(metrics: dict) -> str:
    """Grouped bar chart of DOW indices — per-channel when available."""
    dow = metrics.get("dow_indices", {})
    if not dow:
        return ""

    ch_dow = metrics.get("channel_dow_indices", {})

    labels = list(dow.keys())

    if ch_dow and "instore" in ch_dow and "delivery" in ch_dow:
        # Per-channel grouped bars (keys are string labels: "Mon", "Tue", etc.)
        in_vals = [ch_dow["instore"].get(labels[i], 1.0) for i in range(7)]
        del_vals = [ch_dow["delivery"].get(labels[i], 1.0) for i in range(7)]

        datasets = [
            {"label": "In-Store", "data": in_vals, "backgroundColor": _GREEN},
            {"label": "Delivery", "data": del_vals, "backgroundColor": _BLUE},
        ]

        chart = render_chartjs_bar(
            labels, datasets, height=240,
            annotation_lines=[{"value": 1.0, "color": _DARK_GREEN, "label": "Average"}],
        )

        # Per-channel insights
        in_best = max(range(7), key=lambda i: in_vals[i])
        del_best = max(range(7), key=lambda i: del_vals[i])
        note = (
            '<div style="font-size:12px;color:var(--muted);margin-top:8px;">'
            'In-Store peaks %s (+%.0f%%). '
            'Delivery peaks %s (+%.0f%%). '
            'Patterns are inversely correlated: delivery peaks weeknights, '
            'in-store peaks weekends.'
            '</div>'
            % (labels[in_best], (in_vals[in_best] - 1) * 100,
               labels[del_best], (del_vals[del_best] - 1) * 100)
        )
    else:
        # Fallback: single series
        values = list(dow.values())
        colors = [_GREEN if v >= 1.0 else "#c4c0b4" for v in values]
        datasets = [{"label": "DOW Index", "data": values, "colors": colors}]

        chart = render_chartjs_bar(
            labels, datasets, height=220, horizontal=True,
            annotation_lines=[{"value": 1.0, "color": _DARK_GREEN, "label": "Average"}],
        )

        best_day = max(dow, key=dow.get)
        worst_day = min(dow, key=dow.get)
        best_pct = (dow[best_day] - 1) * 100
        worst_pct = (1 - dow[worst_day]) * 100
        note = (
            '<div style="font-size:12px;color:var(--muted);margin-top:8px;">'
            '%s generates %.0f%% more revenue than average. '
            '%s generates %.0f%% less.'
            '</div>' % (best_day, best_pct, worst_day, worst_pct)
        )

    return render_card("Day-of-Week Pattern", chart + note,
                       subtitle="Revenue index by weekday (1.0 = average)")


# ---------------------------------------------------------------------------
# Seasonal Pattern
# ---------------------------------------------------------------------------


def _render_seasonal_pattern(metrics: dict) -> str:
    """Bar chart of monthly seasonal indices — per-channel when available."""
    seasonal = metrics.get("seasonal_indices", {})
    if not seasonal:
        return ""

    ch_seasonal = metrics.get("channel_seasonal_indices", {})
    labels = list(seasonal.keys())

    if ch_seasonal and "instore" in ch_seasonal and "delivery" in ch_seasonal:
        # Per-channel grouped bars (keys are string labels: "Jan", "Feb", etc.)
        in_vals = [ch_seasonal["instore"].get(labels[i], 1.0) for i in range(12)]
        del_vals = [ch_seasonal["delivery"].get(labels[i], 1.0) for i in range(12)]

        datasets = [
            {"label": "In-Store", "data": in_vals, "backgroundColor": _GREEN},
            {"label": "Delivery", "data": del_vals, "backgroundColor": _BLUE},
        ]

        chart = render_chartjs_bar(
            labels, datasets, height=240,
            annotation_lines=[{"value": 1.0, "color": _DARK_GREEN, "label": "Average"}],
        )

        # Find peak/trough for each channel
        in_peak_m = max(range(12), key=lambda i: in_vals[i])
        del_trough_m = min(range(12), key=lambda i: del_vals[i])
        note = (
            '<div style="font-size:12px;color:var(--muted);margin-top:8px;">'
            'In-Store peaks in %s (+%.0f%%), drops in winter. '
            'Delivery is more stable year-round, with slight summer dip.'
            '</div>' % (labels[in_peak_m], (in_vals[in_peak_m] - 1) * 100)
        )
    else:
        values = list(seasonal.values())
        colors = [_GREEN if v >= 1.0 else "#c4c0b4" for v in values]
        datasets = [{"label": "Seasonal Index", "data": values, "colors": colors}]

        chart = render_chartjs_bar(
            labels, datasets, height=220,
            annotation_lines=[{"value": 1.0, "color": _DARK_GREEN, "label": "Average"}],
        )

        peak = max(seasonal, key=seasonal.get)
        trough = min(seasonal, key=seasonal.get)
        peak_pct = (seasonal[peak] - 1) * 100
        trough_pct = (1 - seasonal[trough]) * 100
        note = (
            '<div style="font-size:12px;color:var(--muted);margin-top:8px;">'
            'Peak: %s (+%.0f%% vs avg). '
            'Trough: %s (-%.0f%% vs avg).'
            '</div>' % (peak, peak_pct, trough, trough_pct)
        )

    return render_card("Monthly Seasonality", chart + note,
                       subtitle="Seasonal revenue index (1.0 = average)")


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------


def _render_backtest(metrics: dict) -> str:
    """Show backtest: model predicted vs actual for Jan/Feb 2026."""
    bt = metrics.get("backtest", {})
    weeks = bt.get("weeks", [])
    bt_months = bt.get("months", [])
    summary = bt.get("summary", {})

    if not weeks:
        return ""

    # Monthly comparison table
    headers = ["Month", "Actual", "Predicted", "Diff", "Error"]
    rows = []
    for m in bt_months:
        diff_color = _GREEN if abs(m["error_pct"]) < 5 else _RED
        rows.append([
            _safe(m["label"]),
            fmt_currency(m["actual"]),
            fmt_currency(m["predicted"]),
            fmt_currency(m["diff"]),
            '<span style="color:%s">%+.1f%%</span>' % (diff_color, m["error_pct"]),
        ])

    tbl = render_table(headers, rows)

    # Weekly comparison chart
    labels = [w["label"] for w in weeks]
    datasets = [
        {"label": "Actual", "data": [w["actual"] for w in weeks], "color": _GREEN},
        {"label": "Predicted", "data": [w["predicted"] for w in weeks], "color": _BLUE},
    ]
    chart = render_chartjs_bar(labels, datasets, height=250, dollar=True, show_legend=True)

    total_err = summary.get("total_error_pct", 0)
    mape = summary.get("mape_weekly", 0)
    train_days = summary.get("training_days", 0)

    err_color = _GREEN if abs(total_err) < 5 else _RED
    summary_html = (
        f'<div style="font-size:12px;color:var(--muted);margin-top:8px;">'
        f'Model trained on {train_days} days of data (pre-Jan 2026). '
        f'Overall error: <strong style="color:{err_color}">{total_err:+.1f}%</strong>. '
        f'Weekly MAPE: {mape:.1f}%.'
        f'</div>'
    )

    content = chart + tbl + summary_html
    return render_card(
        "Model Backtest: Predicted vs Actual",
        content,
        subtitle="Trained on pre-2026 data, tested against actual Jan-Feb 2026",
    )


# ---------------------------------------------------------------------------
# Assumptions
# ---------------------------------------------------------------------------


def _render_assumptions(metrics: dict) -> str:
    """Card showing all forecast assumptions with real expense ratios."""
    assumptions = metrics.get("assumptions", {})
    channel_mix = metrics.get("channel_mix", {})
    trend = metrics.get("trend", {})
    expense_ratios = metrics.get("expense_ratios", {})

    days = assumptions.get("data_days", 0)
    has_acct = assumptions.get("has_accounting", False)
    acct_months = assumptions.get("accounting_months", 0)
    r_sq = trend.get("r_squared", 0)
    slope = trend.get("slope", 0)

    # Channel mix string
    mix_parts = []
    for ch in ("instore", "delivery", "catering", "other"):
        pct = channel_mix.get(ch, 0)
        if pct > 0.005:
            name = ch.replace("instore", "In-Store").replace("delivery", "Delivery").replace("catering", "Catering").replace("other", "Other")
            mix_parts.append(f"{name} {pct*100:.0f}%")
    mix_str = " / ".join(mix_parts)

    trend_dir = "up" if slope > 0 else "down" if slope < 0 else "flat"
    trend_str = f"${abs(slope):.0f}/day ({trend_dir})"

    # Expense ratios
    cogs_pct = expense_ratios.get("cogs", 0) * 100
    labor_pct = expense_ratios.get("labor", 0) * 100
    tp_pct = expense_ratios.get("third_party_fees", 0) * 100
    rent_pct = expense_ratios.get("rent_occupancy", 0) * 100

    source_note = "from real accounting P&L" if has_acct else "estimated from industry benchmarks"

    items = f"""<div class="assumption-grid">
  <div class="assumption-item"><strong>{cogs_pct:.1f}%</strong>COGS (% of revenue)</div>
  <div class="assumption-item"><strong>{labor_pct:.1f}%</strong>Labor (% of revenue)</div>
  <div class="assumption-item"><strong>{tp_pct:.1f}%</strong>Third Party Fees (% of revenue)</div>
  <div class="assumption-item"><strong>{rent_pct:.1f}%</strong>Rent & Occupancy (% of revenue)</div>
  <div class="assumption-item"><strong>{mix_str}</strong>Channel Mix (trailing 3 months)</div>
  <div class="assumption-item"><strong>{trend_str}</strong>Daily Trend (R&sup2; = {r_sq:.3f})</div>
  <div class="assumption-item"><strong>{days} days</strong>Historical data used</div>
  <div class="assumption-item"><strong>{acct_months} months</strong>Accounting P&L data</div>
</div>
<div style="font-size:11px;color:var(--muted);margin-top:12px;">
  Expense ratios {source_note} (trailing 6-month average).
  Revenue forecast: Day-of-week indexing x monthly seasonal adjustment x linear trend.
  Channel splits: trailing 3-month proportions. Costs: fixed percentages of forecasted revenue.
</div>"""

    return render_card("Assumptions &amp; Methodology", items)


def _render_footer() -> str:
    return """<div class="section" style="text-align:center;font-size:11px;color:var(--muted);padding:16px 0 32px;">
  Livite P&amp;L Forecast &mdash; Generated from Toast POS + Accounting P&amp;L data
  <br><a href="/" style="color:var(--livite-green);text-decoration:none;">Back to Home</a>
</div>"""
