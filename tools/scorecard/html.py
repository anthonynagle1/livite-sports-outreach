"""
Build self-contained HTML page for the Weekly Scorecard.

Uses existing Chart.js helpers and Livite theme from htmlrender package.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from htmlrender.components import (
    _reset_chart_counter, render_chartjs_line,
    render_stat, render_stat_grid, render_card,
    render_table, render_insight, fmt_currency, fmt_pct, fmt_num, _safe,
    LIVITE_CHART_COLORS,
)
from htmlrender.sections import _CSS, _LOADING_SNIPPET


# ---------------------------------------------------------------------------
# Delta formatting
# ---------------------------------------------------------------------------

def _delta_html(diff, pct, invert=False):
    """Format a WoW/YoY delta as a colored arrow + percentage.

    Args:
        diff: Absolute difference (current - prior).
        pct: Percent change.
        invert: True for metrics where lower is better (labor%, prime cost%).
    """
    if pct is None:
        return ""
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        return ""
    is_positive = pct > 0
    is_good = not is_positive if invert else is_positive
    color = "var(--green)" if is_good else "var(--red)"
    arrow = "\u25B2" if pct > 0 else "\u25BC"
    return (
        f'<span style="color:{color};font-size:11px;'
        f"font-family:'JetBrains Mono',monospace;\">"
        f'{arrow} {abs(pct):.1f}%</span>'
    )


def _safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Nav links
# ---------------------------------------------------------------------------

_NAV_LINKS = [
    ("Home", "/"),
    ("Scorecard", "/scorecard"),
    ("Monthly", "/monthly"),
    ("Pipeline", "/pipeline"),
    ("Menu", "/menu"),
    ("Vendor Alerts", "/prices/alerts"),
    ("Cash Flow", "/cashflow"),
]


def _render_nav(active: str = "Scorecard") -> str:
    """Render the sub-navigation bar."""
    links = []
    for label, href in _NAV_LINKS:
        if label == active:
            links.append(
                f'<a href="{href}" style="padding:6px 14px;font-size:12px;'
                f'font-weight:600;background:var(--livite-green);color:var(--livite-cream);'
                f'border-radius:6px;text-decoration:none;">{_safe(label)}</a>'
            )
        else:
            links.append(
                f'<a href="{href}" style="padding:6px 14px;font-size:12px;'
                f'font-weight:500;color:var(--muted);text-decoration:none;'
                f'border-radius:6px;border:1px solid var(--border);'
                f'background:var(--surface2);">{_safe(label)}</a>'
            )
    return (
        '<div style="display:flex;flex-wrap:wrap;gap:6px;justify-content:center;'
        'margin-bottom:20px;">'
        + "".join(links)
        + "</div>"
    )


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

def _render_header(metrics: dict, logo_b64: str = "") -> str:
    week_label = metrics.get("week_label", "")

    logo_html = ""
    if logo_b64:
        logo_html = (
            f'<img src="data:image/png;base64,{logo_b64}" alt="Livite" '
            f'style="height:44px;max-width:80%;margin-bottom:8px;'
            f'display:block;margin-left:auto;margin-right:auto;">'
        )

    return (
        f'<div style="text-align:center;margin-bottom:8px;">'
        f'{logo_html}'
        f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:2px;'
        f'color:var(--muted);margin-bottom:6px;">Weekly Scorecard</div>'
        f'<div style="font-size:26px;font-weight:700;color:var(--livite-green);">'
        f'{_safe(week_label)}</div>'
        f'</div>'
        + _render_nav("Scorecard")
    )


# ---------------------------------------------------------------------------
# KPI Grid
# ---------------------------------------------------------------------------

def _render_kpis(metrics: dict) -> str:
    """Render the KPI stat grid with WoW deltas."""
    cur = metrics.get("current", {})
    deltas = metrics.get("deltas", {})

    def _wow_delta(key, invert=False):
        d = deltas.get(key, {}).get("wow", (None, None))
        diff, pct = d if isinstance(d, tuple) and len(d) == 2 else (None, None)
        return _delta_html(diff, pct, invert=invert)

    stats = [
        render_stat("Revenue", fmt_currency(cur.get("revenue", 0)),
                     delta_html=_wow_delta("revenue")),
        render_stat("Labor", fmt_currency(cur.get("labor", 0)),
                     delta_html=_wow_delta("labor", invert=True)),
        render_stat("Labor %", fmt_pct(cur.get("labor_pct", 0)),
                     delta_html=_wow_delta("labor_pct", invert=True)),
        render_stat("Prime Cost %", fmt_pct(cur.get("prime_cost_pct", 0)),
                     delta_html=_wow_delta("prime_cost_pct", invert=True)),
        render_stat("Avg Check", fmt_currency(cur.get("avg_check", 0)),
                     delta_html=_wow_delta("avg_check")),
        render_stat("Orders", fmt_num(cur.get("orders", 0)),
                     delta_html=_wow_delta("orders")),
        render_stat("Catering Rev", fmt_currency(cur.get("catering_revenue", 0)),
                     delta_html=_wow_delta("catering_revenue")),
        render_stat("3P Fees", fmt_currency(cur.get("third_party_fees", 0)),
                     delta_html=_wow_delta("third_party_fees", invert=True)),
        render_stat("3P Revenue", fmt_currency(cur.get("third_party_revenue", 0)),
                     delta_html=_wow_delta("third_party_revenue")),
        render_stat("Customers", fmt_num(cur.get("customer_count", 0)),
                     delta_html=_wow_delta("customer_count")),
    ]
    return render_card("Key Performance Indicators", render_stat_grid(stats),
                       subtitle="vs. prior week")


# ---------------------------------------------------------------------------
# Sparklines
# ---------------------------------------------------------------------------

def _render_sparklines(metrics: dict) -> str:
    """Render 4 mini line charts in a 2x2 grid."""
    sparklines = metrics.get("sparklines", {})
    labels = sparklines.get("labels", [])

    if not labels:
        return ""

    charts = []

    # Revenue trend
    rev_data = sparklines.get("revenue", [])
    if rev_data:
        chart = render_chartjs_line(
            labels, [{"label": "Revenue", "data": rev_data,
                       "color": LIVITE_CHART_COLORS[0], "fill": True}],
            height=120, dollar=True,
        )
        charts.append(render_card("Revenue Trend", chart, subtitle="12 weeks"))

    # Labor % trend
    labor_data = sparklines.get("labor_pct", [])
    if labor_data:
        chart = render_chartjs_line(
            labels, [{"label": "Labor %", "data": labor_data,
                       "color": LIVITE_CHART_COLORS[1]}],
            height=120,
            annotation_lines=[{"value": 30, "color": "var(--amber)", "label": "30%"},
                              {"value": 35, "color": "var(--red)", "label": "35%"}],
        )
        charts.append(render_card("Labor % Trend", chart, subtitle="12 weeks"))

    # Orders trend
    orders_data = sparklines.get("orders", [])
    if orders_data:
        chart = render_chartjs_line(
            labels, [{"label": "Orders", "data": orders_data,
                       "color": LIVITE_CHART_COLORS[3]}],
            height=120,
        )
        charts.append(render_card("Orders Trend", chart, subtitle="12 weeks"))

    # Avg Check trend
    check_data = sparklines.get("avg_check", [])
    if check_data:
        chart = render_chartjs_line(
            labels, [{"label": "Avg Check", "data": check_data,
                       "color": LIVITE_CHART_COLORS[4], "fill": True}],
            height=120, dollar=True,
        )
        charts.append(render_card("Avg Check Trend", chart, subtitle="12 weeks"))

    if not charts:
        return ""

    # Pair charts into a 2-column grid
    rows = []
    for i in range(0, len(charts), 2):
        pair = charts[i:i + 2]
        if len(pair) == 2:
            rows.append(f'<div class="grid-2">{pair[0]}{pair[1]}</div>')
        else:
            rows.append(pair[0])

    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Daily Breakdown Table
# ---------------------------------------------------------------------------

def _render_daily_breakdown(metrics: dict) -> str:
    """Render the daily breakdown as a table."""
    breakdown = metrics.get("daily_breakdown", [])
    if not breakdown:
        return ""

    headers = ["Day", "Revenue", "Orders", "Avg Check", "Labor %"]
    rows = []
    for d in breakdown:
        date_str = d.get("date_str", "")
        day_label = d.get("day", "")
        # Format date as MM/DD
        if len(date_str) == 8:
            formatted = f"{date_str[4:6]}/{date_str[6:8]}"
        else:
            formatted = date_str
        display = f"{day_label} {formatted}" if day_label else formatted

        rows.append([
            f'<span style="font-weight:600;">{_safe(display)}</span>',
            fmt_currency(d.get("revenue", 0)),
            fmt_num(d.get("orders", 0)),
            fmt_currency(d.get("avg_check", 0)),
            fmt_pct(d.get("labor_pct", 0)),
        ])

    # Totals row
    if breakdown:
        total_rev = sum(_safe_float(d.get("revenue")) for d in breakdown)
        total_orders = sum(int(_safe_float(d.get("orders"))) for d in breakdown)
        avg_check = round(total_rev / total_orders, 2) if total_orders else 0
        rows.append([
            '<span style="font-weight:700;">TOTAL</span>',
            f'<span style="font-weight:700;">{fmt_currency(total_rev)}</span>',
            f'<span style="font-weight:700;">{fmt_num(total_orders)}</span>',
            f'<span style="font-weight:700;">{fmt_currency(avg_check)}</span>',
            "",
        ])

    table = render_table(headers, rows, right_align_cols=[1, 2, 3, 4])
    return render_card("Daily Breakdown", table)


# ---------------------------------------------------------------------------
# Anomalies
# ---------------------------------------------------------------------------

def _render_anomalies(metrics: dict) -> str:
    """Render top anomalies as insight cards."""
    anomalies = metrics.get("top_anomalies", [])
    if not anomalies:
        return ""

    parts = []
    for a in anomalies:
        severity = a.get("severity", "amber")
        text = a.get("text", "")
        parts.append(render_insight(text, severity=severity, tag="Alert"))

    return render_card("Alerts & Anomalies", "\n".join(parts))


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

def _render_footer() -> str:
    return (
        '<div style="text-align:center;color:var(--muted);font-size:11px;'
        'padding:24px 0 12px;">'
        '<div>Livite Weekly Scorecard &mdash; powered by Toast POS data</div>'
        '<div style="margin-top:6px;">'
        '<a href="/" style="color:var(--green);text-decoration:none;font-weight:500;">'
        '\u2190 Dashboard Home</a>'
        '</div>'
        '</div>'
    )


# ---------------------------------------------------------------------------
# Main page builder
# ---------------------------------------------------------------------------

def build_scorecard_page(metrics: dict, logo_b64: str = "") -> str:
    """Build the complete Weekly Scorecard HTML page.

    Args:
        metrics: Dict from compute_weekly_scorecard().
        logo_b64: Optional base64-encoded logo PNG.

    Returns:
        Self-contained HTML string.
    """
    _reset_chart_counter()

    sections = []
    sections.append(_render_header(metrics, logo_b64))
    sections.append(_render_kpis(metrics))
    sections.append(_render_sparklines(metrics))
    sections.append(_render_daily_breakdown(metrics))
    sections.append(_render_anomalies(metrics))
    sections.append(_render_footer())

    body = "\n\n".join(s for s in sections if s)

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Livite Weekly Scorecard</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
{_CSS}
</style>
</head>
<body>
{body}
{_LOADING_SNIPPET}
</body>
</html>"""
