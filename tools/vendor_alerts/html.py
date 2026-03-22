"""Build self-contained HTML page for the Vendor Price Alerts dashboard.

Uses existing Chart.js helpers and Livite theme from htmlrender package.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from htmlrender.components import (
    _reset_chart_counter,
    render_chartjs_bar,
    render_chartjs_line,
    render_chartjs_pie,
    render_stat,
    render_stat_grid,
    render_card,
    render_table,
    render_insight,
    fmt_currency,
    fmt_pct,
    fmt_num,
    _safe,
    LIVITE_CHART_COLORS,
)
from htmlrender.sections import _CSS, _LOADING_SNIPPET


def build_vendor_alerts_page(metrics: dict, logo_b64: str = "") -> str:
    """Build the complete Vendor Price Alerts HTML page.

    Args:
        metrics: Output of compute_vendor_alerts().
        logo_b64: Optional base64-encoded logo PNG.
    """
    _reset_chart_counter()

    sections: list[str] = []

    # ── 1. Header ──
    sections.append(_render_header(metrics, logo_b64))

    # ── 2. Summary KPIs ──
    sections.append(_render_summary_kpis(metrics))

    # ── 3. Critical alerts (insights) ──
    sections.append(_render_critical_insights(metrics))

    # ── 4. Alert Table ──
    sections.append(_render_alert_table(metrics))

    # ── 5. Vendor Spend Trend ──
    sections.append(_render_vendor_spend_trend(metrics))

    # ── 6. Category Breakdown ──
    sections.append(_render_category_pie(metrics))

    # ── 7. Multi-Vendor Comparison ──
    sections.append(_render_multi_vendor(metrics))

    # ── 8. Weekly Spend Total ──
    sections.append(_render_weekly_spend(metrics))

    # ── 9. Footer ──
    sections.append(_render_footer())

    body = "\n\n".join(s for s in sections if s)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Livite Vendor Price Alerts</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
{_CSS}
/* Alert table severity row tints */
tr.alert-red {{ background: rgba(220,38,38,0.06); }}
tr.alert-amber {{ background: rgba(202,138,4,0.06); }}
tr.alert-red:hover, tr.alert-amber:hover {{ filter: brightness(0.97); }}
/* Sub-nav links */
.subnav {{ display:flex; gap:10px; flex-wrap:wrap; justify-content:center; margin-bottom:20px; }}
.subnav a {{
    display:inline-block; padding:6px 14px; font-size:12px; font-weight:500;
    border:1px solid var(--border); border-radius:6px; background:var(--surface2);
    color:var(--muted); text-decoration:none; font-family:'DM Sans',sans-serif;
}}
.subnav a:hover {{ background:var(--livite-green); color:var(--livite-cream); }}
/* Severity badges */
.sev-badge {{
    display:inline-block; padding:2px 8px; border-radius:4px;
    font-size:10px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px;
}}
.sev-red {{ background:rgba(220,38,38,0.12); color:var(--red); }}
.sev-amber {{ background:rgba(202,138,4,0.12); color:var(--amber); }}
/* Change value styling */
.change-up {{ color:var(--red); font-weight:600; }}
</style>
</head>
<body>

{body}

{_LOADING_SNIPPET}
</body>
</html>"""


# ======================================================================
# Section renderers
# ======================================================================

def _render_header(metrics: dict, logo_b64: str) -> str:
    summary = metrics.get("summary", {})
    total_alerts = summary.get("total_alerts", 0)
    red = summary.get("red_count", 0)
    amber = summary.get("amber_count", 0)

    logo_html = ""
    if logo_b64:
        logo_html = (
            f'<img src="data:image/png;base64,{logo_b64}" alt="Livite" '
            f'style="height:44px;max-width:80%;margin-bottom:8px;display:block;'
            f'margin-left:auto;margin-right:auto;">'
        )

    # Subtitle with badge counts
    badges = []
    if red:
        badges.append(f'<span class="sev-badge sev-red">{red} Critical</span>')
    if amber:
        badges.append(f'<span class="sev-badge sev-amber">{amber} Warning</span>')
    badge_html = " ".join(badges)

    subtitle = f"{total_alerts} active alert{'s' if total_alerts != 1 else ''}"
    if badge_html:
        subtitle += f" &mdash; {badge_html}"

    # Sub-navigation anchors
    nav = (
        '<div class="subnav">'
        '<a href="#alerts">Alerts</a>'
        '<a href="#spend-trend">Spend Trend</a>'
        '<a href="#categories">Categories</a>'
        '<a href="#multi-vendor">Multi-Vendor</a>'
        '<a href="#weekly-spend">Weekly Spend</a>'
        '<a href="/prices/">Back to Prices</a>'
        '</div>'
    )

    return (
        f'<div style="text-align:center;margin-bottom:12px;">'
        f'{logo_html}'
        f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:2px;'
        f'color:var(--muted);margin-bottom:6px;">Vendor Intelligence</div>'
        f'<h1>Price Trend <span>Alerts</span></h1>'
        f'<div class="subtitle">{subtitle}</div>'
        f'</div>'
        f'{nav}'
    )


def _render_summary_kpis(metrics: dict) -> str:
    summary = metrics.get("summary", {})
    alerts = metrics.get("alerts", [])

    total_alerts = summary.get("total_alerts", 0)
    red_count = summary.get("red_count", 0)
    amber_count = summary.get("amber_count", 0)
    grand_total = summary.get("grand_total", 0)
    items_tracked = summary.get("items_tracked", 0)

    # Largest increase %
    largest_pct = 0.0
    if alerts:
        largest_pct = max(a.get("pct_change", 0) for a in alerts)

    alert_subtitle = ""
    if total_alerts:
        parts = []
        if red_count:
            parts.append(f"{red_count} critical")
        if amber_count:
            parts.append(f"{amber_count} warning")
        alert_subtitle = " / ".join(parts)

    stats = [
        render_stat(
            "Total Alerts",
            str(total_alerts),
            subtitle=alert_subtitle,
            color="var(--red)" if red_count else ("var(--amber)" if amber_count else None),
        ),
        render_stat(
            "Total Spend (12w)",
            fmt_currency(grand_total),
        ),
        render_stat(
            "Largest Increase",
            fmt_pct(largest_pct) if largest_pct else "N/A",
            color="var(--red)" if largest_pct >= 20 else ("var(--amber)" if largest_pct >= 10 else None),
        ),
        render_stat(
            "Items Tracked",
            fmt_num(items_tracked),
        ),
    ]

    return render_stat_grid(stats)


def _render_critical_insights(metrics: dict) -> str:
    """Show render_insight callouts for the most severe alerts."""
    alerts = metrics.get("alerts", [])
    red_alerts = [a for a in alerts if a["severity"] == "red"]

    if not red_alerts:
        return ""

    parts = []
    # Show up to 3 critical insights
    for alert in red_alerts[:3]:
        parts.append(render_insight(
            f'<strong>{_safe(alert["item_name"])}</strong> from '
            f'<strong>{_safe(alert["vendor"])}</strong> increased '
            f'<span class="hl">+{alert["pct_change"]:.1f}%</span> '
            f'({fmt_currency(alert["prev_price"])} &rarr; {fmt_currency(alert["current_price"])}) '
            f'over the past month.',
            severity="red",
            tag="Critical Price Alert",
        ))

    return "\n".join(parts)


def _render_alert_table(metrics: dict) -> str:
    """Render the main alerts table with severity-tinted rows."""
    alerts = metrics.get("alerts", [])

    if not alerts:
        return render_card(
            "Price Alerts",
            '<div style="text-align:center;padding:30px 0;color:var(--muted);">'
            'No price alerts detected. All items are within normal ranges.</div>',
        )

    headers = ["Item", "Vendor", "Was", "Now", "Change", "Timeframe"]
    rows_html = []

    for alert in alerts:
        sev = alert.get("severity", "amber")
        sev_badge = (
            f'<span class="sev-badge sev-{sev}">'
            f'{"Critical" if sev == "red" else "Warning"}</span>'
        )
        timeframe_label = "MoM" if alert.get("timeframe") == "mom" else "WoW"
        change_str = f'+{alert["pct_change"]:.1f}%'

        rows_html.append(
            f'<tr class="alert-{sev}">'
            f'<td><strong>{_safe(alert["item_name"])}</strong></td>'
            f'<td>{_safe(alert["vendor"])}</td>'
            f'<td class="r n">{fmt_currency(alert["prev_price"])}</td>'
            f'<td class="r n">{fmt_currency(alert["current_price"])}</td>'
            f'<td class="r"><span class="change-up">{change_str}</span></td>'
            f'<td>{sev_badge} {timeframe_label}</td>'
            f'</tr>'
        )

    r_cls = ' class="r"'
    thead = (
        '<tr>'
        + "".join(f'<th{r_cls if i in (2,3,4) else ""}>{h}</th>'
                  for i, h in enumerate(headers))
        + '</tr>'
    )
    tbody = "\n".join(rows_html)

    table_html = f'<table>\n{thead}\n{tbody}\n</table>'

    return (
        f'<div id="alerts">'
        + render_card(
            "Price Alerts",
            table_html,
            subtitle=f"{len(alerts)} item{'s' if len(alerts) != 1 else ''} with significant price increases",
        )
        + '</div>'
    )


def _render_vendor_spend_trend(metrics: dict) -> str:
    """Line chart: spend per vendor (top 5) over 12 weeks."""
    vendor_data = metrics.get("vendor_spend_trend", {})

    if not vendor_data:
        return render_card(
            "Vendor Spend Trend",
            '<div style="text-align:center;padding:30px 0;color:var(--muted);">'
            'No spend data available.</div>',
        )

    # Rank vendors by total spend, take top 5
    vendor_totals = {
        v: sum(w["total"] for w in weeks)
        for v, weeks in vendor_data.items()
    }
    top_vendors = sorted(vendor_totals, key=lambda v: -vendor_totals[v])[:5]

    if not top_vendors:
        return ""

    # Build labels from the first vendor's week list
    first_vendor = top_vendors[0]
    labels = [w["week"] for w in vendor_data[first_vendor]]

    # Shorten week labels (e.g. "2026-W08" → "W08")
    short_labels = [lbl.split("-")[-1] if "-" in lbl else lbl for lbl in labels]

    datasets = []
    for i, vendor in enumerate(top_vendors):
        weeks = vendor_data.get(vendor, [])
        # Align data to labels
        week_map = {w["week"]: w["total"] for w in weeks}
        data = [week_map.get(lbl, 0) for lbl in labels]
        datasets.append({
            "label": vendor,
            "data": data,
            "color": LIVITE_CHART_COLORS[i % len(LIVITE_CHART_COLORS)],
            "fill": False,
        })

    chart = render_chartjs_line(short_labels, datasets, height=280, dollar=True)

    return (
        f'<div id="spend-trend">'
        + render_card("Vendor Spend Trend (12 weeks)", chart,
                      subtitle="Top 5 vendors by total spend")
        + '</div>'
    )


def _render_category_pie(metrics: dict) -> str:
    """Doughnut chart: spend by category."""
    cat_spend = metrics.get("category_spend", {})

    if not cat_spend:
        return render_card(
            "Category Breakdown",
            '<div style="text-align:center;padding:30px 0;color:var(--muted);">'
            'No category data available.</div>',
        )

    # Sort by spend descending
    sorted_cats = sorted(cat_spend.items(), key=lambda x: -x[1])
    labels = [c[0] for c in sorted_cats]
    values = [round(c[1], 2) for c in sorted_cats]
    total = sum(values)

    chart = render_chartjs_pie(
        labels, values, height=280, doughnut=True,
        center_text=fmt_currency(total),
    )

    return (
        f'<div id="categories">'
        + render_card("Spend by Category", chart,
                      subtitle="Total spend across all categories (12 weeks)")
        + '</div>'
    )


def _render_multi_vendor(metrics: dict) -> str:
    """Table: items available from multiple vendors with price comparison."""
    multi = metrics.get("multi_vendor_items", [])

    if not multi:
        return render_card(
            "Multi-Vendor Comparison",
            '<div style="text-align:center;padding:30px 0;color:var(--muted);">'
            'No items found from multiple vendors.</div>',
        )

    headers = ["Item", "Vendors & Prices", "Cheapest", "Spread", "Savings"]
    rows: list[list[str]] = []

    for item in multi[:20]:  # Limit to 20 items
        # Build vendor price list
        vendor_lines = []
        for v in item.get("vendors", []):
            is_cheapest = v["vendor"] == item.get("cheapest_vendor", "")
            marker = ' <span style="color:var(--green);font-weight:600;">&#x2713;</span>' if is_cheapest else ""
            vendor_lines.append(
                f'{_safe(v["vendor"])}: '
                f'<span class="n">{fmt_currency(v["price"])}</span>'
                f'{" / " + _safe(v["unit"]) if v.get("unit") else ""}'
                f'{marker}'
            )
        vendors_html = "<br>".join(vendor_lines)

        rows.append([
            f'<strong>{_safe(item["item_name"])}</strong>',
            vendors_html,
            _safe(item.get("cheapest_vendor", "")),
            fmt_currency(item.get("price_spread", 0)),
            f'<span style="color:var(--green);font-weight:600;">'
            f'{fmt_currency(item.get("savings_potential", 0))}</span>',
        ])

    # Build table manually for HTML content in cells
    r_cls = ' class="r"'
    th_row = '<tr>' + ''.join(
        f'<th{r_cls if i in (3, 4) else ""}>{h}</th>'
        for i, h in enumerate(headers)
    ) + '</tr>'

    body_rows = []
    for row in rows:
        tds = []
        for i, cell in enumerate(row):
            cls = ' class="r n"' if i in (3, 4) else ""
            tds.append(f'<td{cls}>{cell}</td>')
        body_rows.append('<tr>' + ''.join(tds) + '</tr>')

    table = f'<table>\n{th_row}\n' + '\n'.join(body_rows) + '\n</table>'

    total_savings = sum(it.get("savings_potential", 0) for it in multi)
    subtitle = (
        f'{len(multi)} item{"s" if len(multi) != 1 else ""} from multiple vendors'
        f' &mdash; potential savings: <span class="hl">{fmt_currency(total_savings)}</span>'
    )

    return (
        f'<div id="multi-vendor">'
        + render_card("Multi-Vendor Comparison", table, subtitle=subtitle)
        + '</div>'
    )


def _render_weekly_spend(metrics: dict) -> str:
    """Bar chart: total spend per week."""
    weekly = metrics.get("weekly_totals", [])

    if not weekly:
        return render_card(
            "Weekly Spend",
            '<div style="text-align:center;padding:30px 0;color:var(--muted);">'
            'No weekly spend data available.</div>',
        )

    labels = [w["week"] for w in weekly]
    short_labels = [lbl.split("-")[-1] if "-" in lbl else lbl for lbl in labels]
    values = [w["total"] for w in weekly]

    # Compute average for reference line
    avg_spend = sum(values) / len(values) if values else 0

    chart = render_chartjs_bar(
        short_labels,
        [{"label": "Total Spend", "data": values, "color": LIVITE_CHART_COLORS[0]}],
        height=250,
        dollar=True,
        annotation_lines=[{
            "value": round(avg_spend, 2),
            "color": "#999",
            "label": f"Avg: {fmt_currency(avg_spend)}",
        }],
    )

    return (
        f'<div id="weekly-spend">'
        + render_card("Weekly Spend Total", chart,
                      subtitle="Total purchases per week with 12-week average")
        + '</div>'
    )


def _render_footer() -> str:
    return (
        '<div style="text-align:center;padding:28px 0 12px;font-size:11px;color:var(--muted);">'
        'Livite Vendor Price Alerts &middot; Data sourced from Notion &middot; '
        'Prices analysed from purchase records'
        '</div>'
    )
