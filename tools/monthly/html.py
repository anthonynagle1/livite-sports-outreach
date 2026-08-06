"""Build self-contained HTML page for the Monthly P&L Report.

Uses the shared Chart.js helpers and Livite theme from the htmlrender package.
All CSS is inlined; external deps are Google Fonts CDN and Chart.js v4 CDN.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from htmlrender.components import (
    _reset_chart_counter, render_chartjs_bar, render_chartjs_line,
    render_chartjs_pie, render_stat, render_stat_grid, render_card,
    render_table, render_insight,
    fmt_currency, fmt_pct, fmt_num, fmt_delta, _safe,
    LIVITE_CHART_COLORS,
)
from htmlrender.sections import _CSS, _LOADING_SNIPPET


# ---------------------------------------------------------------------------
# Delta helper (matches scorecard convention)
# ---------------------------------------------------------------------------

def _delta_html(deltas: dict, metric: str, period: str = "mom",
                direction: str = "up") -> str:
    """Render a MoM or YoY delta as a coloured arrow + percentage span.

    deltas structure: {metric: {'mom': (diff, pct), 'yoy': (diff, pct)}}
    """
    if not deltas:
        return ""
    entry = deltas.get(metric)
    if not entry:
        return ""
    tup = entry.get(period)
    if not tup or not isinstance(tup, tuple) or len(tup) < 2:
        return ""
    diff, pct = tup
    return fmt_delta(diff, pct, direction)


def _fmt_date_display(date_str: str) -> str:
    """Format YYYYMMDD -> MM/DD."""
    if len(date_str) == 8:
        return f"{date_str[4:6]}/{date_str[6:8]}"
    return date_str


# ---------------------------------------------------------------------------
# Section: Header
# ---------------------------------------------------------------------------

def _render_header(metrics: dict, logo_b64: str) -> str:
    month_label = metrics.get("month_label", "")
    month_str = metrics.get("month_str", "")

    logo_html = ""
    if logo_b64:
        logo_html = (
            f'<img src="data:image/png;base64,{logo_b64}" alt="Livite" '
            f'style="height:38px;margin-bottom:8px;display:block;'
            f'margin-left:auto;margin-right:auto;">'
        )

    # Month navigation
    year, month = map(int, month_str.split("-"))
    if month == 1:
        prev_ms = f"{year - 1:04d}-12"
    else:
        prev_ms = f"{year:04d}-{month - 1:02d}"
    if month == 12:
        next_ms = f"{year + 1:04d}-01"
    else:
        next_ms = f"{year:04d}-{month + 1:02d}"

    nav = (
        f'<div style="display:flex;align-items:center;justify-content:center;'
        f'gap:18px;margin-top:8px;margin-bottom:6px;">'
        f'<a href="/monthly?month={prev_ms}" '
        f'style="color:var(--livite-green);text-decoration:none;font-size:16px;'
        f'font-weight:600;padding:4px 10px;border-radius:6px;'
        f'border:1px solid var(--border);">&larr;</a>'
        f'<span style="font-size:22px;font-weight:700;color:var(--livite-green);">'
        f'{_safe(month_label)}</span>'
        f'<a href="/monthly?month={next_ms}" '
        f'style="color:var(--livite-green);text-decoration:none;font-size:16px;'
        f'font-weight:600;padding:4px 10px;border-radius:6px;'
        f'border:1px solid var(--border);">&rarr;</a>'
        f'</div>'
    )

    sub_nav = (
        f'<div style="margin-bottom:14px;display:flex;gap:14px;'
        f'justify-content:center;flex-wrap:wrap;">'
        f'<a href="/" style="color:var(--green);text-decoration:none;'
        f'font-size:12px;font-weight:500;">&larr; Dashboard Home</a>'
        f'<a href="/scorecard" style="color:var(--muted);text-decoration:none;'
        f'font-size:12px;font-weight:500;">Weekly Scorecard</a>'
        f'<a href="/financials" style="color:var(--muted);text-decoration:none;'
        f'font-size:12px;font-weight:500;">Financials</a>'
        f'</div>'
    )

    return (
        f'<div style="text-align:center;margin-bottom:4px;">'
        f'{logo_html}'
        f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:2px;'
        f'color:var(--muted);margin-bottom:2px;">Monthly P&amp;L Report</div>'
        f'{nav}'
        f'</div>'
        f'{sub_nav}'
    )


# ---------------------------------------------------------------------------
# Section: KPI Grid
# ---------------------------------------------------------------------------

def _render_kpis(metrics: dict) -> str:
    current = metrics.get("current")
    if not current:
        return render_card("Key Metrics", render_insight("No data available for this month.", "amber"))

    deltas = metrics.get("deltas", {})

    stats = [
        render_stat("Revenue", fmt_currency(current["revenue"]),
                     delta_html=_delta_html(deltas, "revenue", "mom")),
        render_stat("Labor", fmt_currency(current["labor"]),
                     delta_html=_delta_html(deltas, "labor", "mom", "down")),
        render_stat("Labor %", fmt_pct(current["labor_pct"]),
                     delta_html=_delta_html(deltas, "labor_pct", "mom", "down")),
        render_stat("Prime Cost %", fmt_pct(current["prime_cost_pct"]),
                     delta_html=_delta_html(deltas, "prime_cost_pct", "mom", "down")),
        render_stat("Avg Check", fmt_currency(current["avg_check"]),
                     delta_html=_delta_html(deltas, "avg_check", "mom")),
        render_stat("Orders", fmt_num(current["orders"]),
                     delta_html=_delta_html(deltas, "orders", "mom")),
        render_stat("Avg Daily Rev", fmt_currency(current["avg_daily_revenue"]),
                     delta_html=_delta_html(deltas, "avg_daily_revenue", "mom")),
        render_stat("Customers", fmt_num(current["customer_count"]),
                     delta_html=_delta_html(deltas, "customer_count", "mom")),
        render_stat("Catering Rev", fmt_currency(current["catering_revenue"]),
                     delta_html=_delta_html(deltas, "catering_revenue", "mom")),
        render_stat("Catering Growth", fmt_pct(current["catering_growth_pct"]),
                     color="var(--green)" if current["catering_growth_pct"] > 0
                     else ("var(--red)" if current["catering_growth_pct"] < 0
                           else None)),
        render_stat("3P Fees", fmt_currency(current["third_party_fees"]),
                     delta_html=_delta_html(deltas, "third_party_fees", "mom", "down")),
        render_stat("3P Fee Rate", fmt_pct(current["third_party_pct"]),
                     delta_html=_delta_html(deltas, "third_party_pct", "mom", "down")),
    ]

    return render_card("Key Performance Indicators",
                       render_stat_grid(stats),
                       subtitle="Month-over-Month deltas shown")


# ---------------------------------------------------------------------------
# Section: Revenue by Channel
# ---------------------------------------------------------------------------

def _render_channel_charts(metrics: dict) -> str:
    current = metrics.get("current")
    if not current:
        return ""

    channels = current.get("channel_breakdown", {})
    if not channels:
        return ""

    # Sort channels by revenue descending
    sorted_ch = sorted(channels.items(), key=lambda x: x[1], reverse=True)
    labels = [ch[0] for ch in sorted_ch]
    values = [round(ch[1], 2) for ch in sorted_ch]

    # Assign colors
    colors = LIVITE_CHART_COLORS[:len(labels)]

    bar_chart = render_chartjs_bar(
        labels, [{"label": "Revenue", "data": values, "colors": colors}],
        height=280, stacked=True, dollar=True, show_legend=False,
    )

    pie_chart = render_chartjs_pie(
        labels, values, colors=colors, height=280, doughnut=True,
        center_text=fmt_currency(current["revenue"]),
    )

    return render_card(
        "Revenue by Channel",
        f'<div class="grid-2">'
        f'<div>{bar_chart}</div>'
        f'<div>{pie_chart}</div>'
        f'</div>',
    )


# ---------------------------------------------------------------------------
# Section: 6-Month Trend
# ---------------------------------------------------------------------------

def _render_trend(metrics: dict) -> str:
    trend = metrics.get("monthly_trend", [])
    if not trend:
        return ""

    labels = [t.get("month_label", "")[:3] + " " + t.get("month_str", "")[-2:]
              for t in trend]
    rev_data = [t.get("revenue", 0) for t in trend]
    labor_pct_data = [t.get("labor_pct", 0) for t in trend]

    chart = render_chartjs_line(
        labels,
        [
            {
                "label": "Revenue",
                "data": rev_data,
                "color": "#8cb82e",
                "fill": True,
                "yAxisID": "y",
                "order": 1,
            },
            {
                "label": "Labor %",
                "data": labor_pct_data,
                "color": "#e86040",
                "borderDash": [4, 3],
                "yAxisID": "y2",
                "order": 0,
            },
        ],
        height=280, dollar=True, y2=True,
    )

    return render_card("6-Month Revenue & Labor % Trend", chart)


# ---------------------------------------------------------------------------
# Section: Top & Worst Days
# ---------------------------------------------------------------------------

def _render_top_worst(metrics: dict) -> str:
    top_days = metrics.get("top_days", [])
    worst_days = metrics.get("worst_days", [])

    if not top_days and not worst_days:
        return ""

    def _day_rows(days: list) -> list:
        rows = []
        for i, d in enumerate(days, 1):
            ds = d.get("date_str", "")
            day = d.get("day", "")[:3]
            rev = d.get("revenue", 0)
            rows.append([
                str(i),
                f"{day} {_fmt_date_display(ds)}",
                fmt_currency(rev),
            ])
        return rows

    headers = ["#", "Day", "Revenue"]

    top_table = render_table(headers, _day_rows(top_days),
                              right_align_cols=[2])
    worst_table = render_table(headers, _day_rows(worst_days),
                                right_align_cols=[2])

    top_card = render_card(
        "Top 5 Days",
        top_table,
        subtitle="Highest revenue days",
    )
    worst_card = render_card(
        "Bottom 5 Days",
        worst_table,
        subtitle="Lowest revenue days",
    )

    return f'<div class="grid-2">{top_card}{worst_card}</div>'


# ---------------------------------------------------------------------------
# Section: Daily Breakdown
# ---------------------------------------------------------------------------

def _render_daily_breakdown(metrics: dict) -> str:
    days = metrics.get("daily_breakdown", [])
    if not days:
        return ""

    headers = ["Day", "Date", "Revenue", "Orders", "Avg Check", "Labor %"]
    rows = []
    for d in days:
        ds = d.get("date_str", "")
        day_name = d.get("day", "")[:3]
        rows.append([
            day_name,
            _fmt_date_display(ds),
            fmt_currency(d.get("revenue", 0)),
            fmt_num(d.get("orders", 0)),
            fmt_currency(d.get("avg_check", 0)),
            fmt_pct(d.get("labor_pct", 0)),
        ])

    # Totals row
    total_rev = sum(d.get("revenue", 0) for d in days)
    total_orders = sum(d.get("orders", 0) for d in days)
    total_check = round(total_rev / total_orders, 2) if total_orders else 0
    rows.append([
        "<strong>TOTAL</strong>", "",
        f"<strong>{fmt_currency(total_rev)}</strong>",
        f"<strong>{fmt_num(total_orders)}</strong>",
        f"<strong>{fmt_currency(total_check)}</strong>",
        "",
    ])

    table = render_table(headers, rows, right_align_cols=[2, 3, 4, 5])
    return render_card("Daily Breakdown", f'<div style="overflow-x:auto;">{table}</div>')


# ---------------------------------------------------------------------------
# Section: Footer
# ---------------------------------------------------------------------------

def _render_footer() -> str:
    return (
        '<div style="text-align:center;margin-top:24px;padding:18px 0;'
        'font-size:11px;color:var(--muted);border-top:1px solid var(--border);">'
        'Livite Monthly P&amp;L Report &middot; Data sourced from Toast POS'
        '</div>'
    )


# ---------------------------------------------------------------------------
# Main Builder
# ---------------------------------------------------------------------------

def build_monthly_page(metrics: dict, logo_b64: str = "") -> str:
    """Build the complete Monthly P&L Report HTML page.

    Args:
        metrics: Output of compute_monthly_report().
        logo_b64: Base64-encoded logo PNG (optional).

    Returns:
        Complete self-contained HTML string.
    """
    _reset_chart_counter()

    sections = []
    sections.append(_render_header(metrics, logo_b64))
    sections.append(_render_kpis(metrics))
    sections.append(_render_channel_charts(metrics))
    sections.append(_render_trend(metrics))
    sections.append(_render_top_worst(metrics))
    sections.append(_render_daily_breakdown(metrics))
    sections.append(_render_footer())

    body = "\n\n".join(s for s in sections if s)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Livite Monthly P&amp;L Report — {_safe(metrics.get('month_label', ''))}</title>
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
</style>
</head>
<body>

{body}

{_LOADING_SNIPPET}
</body>
</html>"""
