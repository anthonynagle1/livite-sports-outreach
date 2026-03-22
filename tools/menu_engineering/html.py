"""Build self-contained HTML page for the Menu Engineering Matrix.

Renders:
1. Header with logo, title, period selector
2. KPI stat grid (total items, quadrant counts, medians)
3. Scatter chart — the key visualization (x=qty, y=avg price, colored by quadrant)
4. Quadrant breakdown cards (top 10 per quadrant)
5. Actionable recommendations
6. Full item table
7. Footer

Uses existing Chart.js helpers and Livite theme.
"""

from __future__ import annotations

import json as _json

from htmlrender.components import (
    _reset_chart_counter, _next_chart_id,
    render_stat, render_stat_grid, render_card,
    render_table, render_insight,
    fmt_currency, fmt_num, _safe,
)
from htmlrender.sections import _CSS, _LOADING_SNIPPET


# ---------------------------------------------------------------------------
# Quadrant colors (consistent with the spec)
# ---------------------------------------------------------------------------

_Q_COLORS = {
    'star': '#15803d',       # green
    'plowhorse': '#1a6eb5',  # blue
    'puzzle': '#ca8a04',     # amber
    'dog': '#dc2626',        # red
}

_Q_BG_LIGHT = {
    'star': 'rgba(21,128,61,0.08)',
    'plowhorse': 'rgba(26,110,181,0.08)',
    'puzzle': 'rgba(202,138,4,0.08)',
    'dog': 'rgba(220,38,38,0.08)',
}

_Q_EMOJI = {
    'star': '\u2b50',        # star
    'plowhorse': '\U0001f434',  # horse
    'puzzle': '\U0001f9e9',  # puzzle
    'dog': '\U0001f436',     # dog
}

_Q_LABELS = {
    'star': 'Stars',
    'plowhorse': 'Plowhorses',
    'puzzle': 'Puzzles',
    'dog': 'Dogs',
}


# ---------------------------------------------------------------------------
# Sub-nav (matches existing dashboard pattern, highlights this page)
# ---------------------------------------------------------------------------

_SUB_NAV = (
    '<div style="display:flex;justify-content:center;gap:8px;margin-top:12px;flex-wrap:wrap;">'
    '<a href="/" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Home</a>'
    '<a href="/today" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Today</a>'
    '<a href="/week" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">This Week</a>'
    '<a href="/menu-engineering" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:#8cb82e;color:#fff;text-decoration:none;font-weight:600;">Menu Matrix</a>'
    '<a href="/schedule" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Schedule</a>'
    '<a href="/forecast" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">P&amp;L Forecast</a>'
    '</div>'
)


# ---------------------------------------------------------------------------
# Extra CSS specific to this page
# ---------------------------------------------------------------------------

_EXTRA_CSS = """
.period-selector { display:flex; justify-content:center; gap:0; margin-top:14px; }
.period-selector a {
    font-size:12px; padding:6px 16px; border:1px solid var(--border);
    background:var(--surface2); color:var(--text); text-decoration:none;
    font-weight:500; transition: background 0.15s, color 0.15s;
}
.period-selector a:first-child { border-radius:6px 0 0 6px; }
.period-selector a:last-child { border-radius:0 6px 6px 0; border-left:none; }
.period-selector a:not(:first-child):not(:last-child) { border-left:none; }
.period-selector a.active {
    background:var(--livite-green); color:var(--livite-cream); font-weight:600;
}
.period-selector a:hover:not(.active) {
    background:var(--accent-dim);
}
.q-card-header {
    display:flex; align-items:center; gap:8px; margin-bottom:10px;
}
.q-card-header .q-emoji { font-size:20px; }
.q-card-header .q-title { font-size:13px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; }
.q-card-header .q-count {
    font-size:11px; font-weight:600; padding:2px 8px; border-radius:4px;
    margin-left:auto;
}
.q-item-row {
    display:flex; justify-content:space-between; align-items:center;
    padding:5px 0; border-bottom:1px solid var(--border); font-size:12px;
}
.q-item-row:last-child { border-bottom:none; }
.q-item-name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.q-item-stats { display:flex; gap:12px; font-family:'JetBrains Mono',monospace; font-size:11px; color:var(--text-secondary); flex-shrink:0; }
"""


# ---------------------------------------------------------------------------
# Section Renderers
# ---------------------------------------------------------------------------

def _render_header(metrics: dict, logo_b64: str) -> str:
    """Page header with logo, title, period selector, and sub-nav."""
    period_label = metrics.get('period_label', 'Last 30 Days')
    period_days = metrics.get('period_days', 30)
    total = metrics.get('total_items_analyzed', 0)

    logo_html = ""
    if logo_b64:
        logo_html = (
            f'<img src="data:image/png;base64,{logo_b64}" alt="Livite" '
            f'style="height:44px;max-width:80%;margin-bottom:8px;display:block;'
            f'margin-left:auto;margin-right:auto;">'
        )

    # Period selector buttons
    periods = [(7, '7d'), (30, '30d'), (90, '90d')]
    period_btns = []
    for d, label in periods:
        active = ' active' if d == period_days else ''
        period_btns.append(
            f'<a href="/menu-engineering?days={d}" class="{active}">{label}</a>'
        )
    period_selector = '<div class="period-selector">' + ''.join(period_btns) + '</div>'

    return (
        f'<div style="text-align:center;margin-bottom:20px;">'
        f'{logo_html}'
        f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:2px;color:var(--muted);margin-bottom:6px;">Menu Engineering</div>'
        f'<div style="font-size:26px;font-weight:700;color:var(--livite-green);">Menu Matrix</div>'
        f'<div style="font-size:14px;color:var(--muted);margin-top:2px;">{_safe(period_label)} &middot; {fmt_num(total)} items analyzed</div>'
        f'{period_selector}'
        f'{_SUB_NAV}'
        f'</div>'
    )


def _render_kpi_grid(metrics: dict) -> str:
    """KPI stat grid: total items, quadrant counts, medians."""
    counts = metrics.get('quadrant_counts', {})
    total = metrics.get('total_items_analyzed', 0)
    median_qty = metrics.get('median_qty', 0)
    median_price = metrics.get('median_avg_price', 0)

    stats = [
        render_stat('Total Items', fmt_num(total)),
        render_stat('Stars', fmt_num(counts.get('star', 0)),
                    color=_Q_COLORS['star']),
        render_stat('Plowhorses', fmt_num(counts.get('plowhorse', 0)),
                    color=_Q_COLORS['plowhorse']),
        render_stat('Puzzles', fmt_num(counts.get('puzzle', 0)),
                    color=_Q_COLORS['puzzle']),
        render_stat('Dogs', fmt_num(counts.get('dog', 0)),
                    color=_Q_COLORS['dog']),
        render_stat('Median Qty', fmt_num(median_qty)),
        render_stat('Median Avg Price', fmt_currency(median_price)),
    ]
    return render_stat_grid(stats)


def _render_scatter_chart(metrics: dict) -> str:
    """Scatter chart — the core menu engineering visualization.

    X axis = qty_sold, Y axis = avg_price.
    Each point colored by quadrant. Quadrant dividing lines at medians.
    """
    items = metrics.get('items', [])
    median_qty = metrics.get('median_qty', 0)
    median_price = metrics.get('median_avg_price', 0)

    if not items:
        return render_card('Menu Engineering Matrix', '<div style="text-align:center;color:var(--muted);padding:40px;">No item data available for this period.</div>')

    # Build datasets per quadrant
    datasets = {}
    for q in ('star', 'plowhorse', 'puzzle', 'dog'):
        datasets[q] = []
    for item in items:
        q = item.get('quadrant', 'dog')
        datasets[q].append({
            'x': item['qty_sold'],
            'y': item['avg_price'],
            'name': item['name'],
        })

    cid = _next_chart_id()

    # Serialize data for JS
    ds_js_parts = []
    for q in ('star', 'plowhorse', 'puzzle', 'dog'):
        points = datasets[q]
        data_arr = _json.dumps([{'x': p['x'], 'y': p['y']} for p in points])
        names_arr = _json.dumps([p['name'] for p in points])
        ds_js_parts.append(
            f"{{label:'{_Q_LABELS[q]}',data:{data_arr},backgroundColor:'{_Q_COLORS[q]}',"
            f"pointRadius:5,pointHoverRadius:8,_names:{names_arr}}}"
        )
    ds_js = ','.join(ds_js_parts)

    chart_script = f"""
<div class="card">
<h2>Menu Engineering Matrix</h2>
<div style="font-size:11px;color:var(--muted);margin-top:-10px;margin-bottom:12px;">
Each dot is a menu item. X = units sold, Y = average price. Dashed lines = medians.
</div>
<div style="position:relative;height:420px;">
<canvas id="{cid}"></canvas>
</div>
<!-- Quadrant legend -->
<div style="display:flex;justify-content:center;gap:16px;margin-top:10px;flex-wrap:wrap;">
<span style="font-size:11px;display:flex;align-items:center;gap:4px;">
  <span style="width:10px;height:10px;border-radius:50%;background:{_Q_COLORS['star']};display:inline-block;"></span> Stars
</span>
<span style="font-size:11px;display:flex;align-items:center;gap:4px;">
  <span style="width:10px;height:10px;border-radius:50%;background:{_Q_COLORS['plowhorse']};display:inline-block;"></span> Plowhorses
</span>
<span style="font-size:11px;display:flex;align-items:center;gap:4px;">
  <span style="width:10px;height:10px;border-radius:50%;background:{_Q_COLORS['puzzle']};display:inline-block;"></span> Puzzles
</span>
<span style="font-size:11px;display:flex;align-items:center;gap:4px;">
  <span style="width:10px;height:10px;border-radius:50%;background:{_Q_COLORS['dog']};display:inline-block;"></span> Dogs
</span>
</div>
</div>
<script>
(function(){{
  var ctx = document.getElementById('{cid}').getContext('2d');
  var MEDIAN_QTY = {median_qty};
  var MEDIAN_PRICE = {median_price};
  new Chart(ctx, {{
    type: 'scatter',
    data: {{
      datasets: [{ds_js}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      scales: {{
        x: {{
          title: {{ display: true, text: 'Units Sold', font: {{ size: 12 }} }},
          grid: {{ color: 'rgba(0,0,0,0.05)' }},
          ticks: {{ font: {{ size: 11 }} }}
        }},
        y: {{
          title: {{ display: true, text: 'Avg Price ($)', font: {{ size: 12 }} }},
          grid: {{ color: 'rgba(0,0,0,0.05)' }},
          ticks: {{
            font: {{ size: 11 }},
            callback: function(v) {{ return '$' + v.toFixed(0); }}
          }}
        }}
      }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            label: function(ctx) {{
              var ds = ctx.dataset;
              var idx = ctx.dataIndex;
              var name = ds._names ? ds._names[idx] : '';
              return name + ': ' + ctx.parsed.x + ' sold, $' + ctx.parsed.y.toFixed(2);
            }}
          }}
        }}
      }}
    }},
    plugins: [{{
      afterDraw: function(chart) {{
        var ctx = chart.ctx;
        var xAxis = chart.scales.x;
        var yAxis = chart.scales.y;
        ctx.save();
        ctx.strokeStyle = 'rgba(0,0,0,0.2)';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([6, 4]);
        // Vertical line at median qty
        var xPx = xAxis.getPixelForValue(MEDIAN_QTY);
        if (xPx >= xAxis.left && xPx <= xAxis.right) {{
          ctx.beginPath();
          ctx.moveTo(xPx, yAxis.top);
          ctx.lineTo(xPx, yAxis.bottom);
          ctx.stroke();
        }}
        // Horizontal line at median price
        var yPx = yAxis.getPixelForValue(MEDIAN_PRICE);
        if (yPx >= yAxis.top && yPx <= yAxis.bottom) {{
          ctx.beginPath();
          ctx.moveTo(xAxis.left, yPx);
          ctx.lineTo(xAxis.right, yPx);
          ctx.stroke();
        }}
        ctx.setLineDash([]);
        // Quadrant labels
        ctx.font = '10px DM Sans, sans-serif';
        ctx.fillStyle = 'rgba(0,0,0,0.25)';
        ctx.textAlign = 'center';
        // Top-left: Puzzle
        var tlx = (xAxis.left + xPx) / 2;
        var tly = (yAxis.top + yPx) / 2;
        ctx.fillText('PUZZLE', tlx, tly);
        // Top-right: Star
        var trx = (xPx + xAxis.right) / 2;
        ctx.fillText('STAR', trx, tly);
        // Bottom-left: Dog
        var bly = (yPx + yAxis.bottom) / 2;
        ctx.fillText('DOG', tlx, bly);
        // Bottom-right: Plowhorse
        ctx.fillText('PLOWHORSE', trx, bly);
        ctx.restore();
      }}
    }}]
  }});
}})();
</script>
"""
    return chart_script


def _render_quadrant_cards(metrics: dict) -> str:
    """Four cards showing top 10 items per quadrant."""
    quadrant_items = metrics.get('quadrant_items', {})
    counts = metrics.get('quadrant_counts', {})

    cards = []
    for q in ('star', 'plowhorse', 'puzzle', 'dog'):
        items = quadrant_items.get(q, [])
        count = counts.get(q, 0)
        color = _Q_COLORS[q]
        bg = _Q_BG_LIGHT[q]
        emoji = _Q_EMOJI[q]
        label = _Q_LABELS[q]

        header = (
            f'<div class="q-card-header">'
            f'<span class="q-emoji">{emoji}</span>'
            f'<span class="q-title" style="color:{color};">{label}</span>'
            f'<span class="q-count" style="background:{bg};color:{color};">{count} items</span>'
            f'</div>'
        )

        if not items:
            body = '<div style="font-size:12px;color:var(--muted);text-align:center;padding:12px 0;">No items in this quadrant.</div>'
        else:
            rows = []
            for item in items:
                rows.append(
                    f'<div class="q-item-row">'
                    f'<span class="q-item-name">{_safe(item["name"])}</span>'
                    f'<span class="q-item-stats">'
                    f'<span>{fmt_num(item["qty_sold"])} sold</span>'
                    f'<span>{fmt_currency(item["avg_price"])}</span>'
                    f'</span>'
                    f'</div>'
                )
            body = ''.join(rows)

        cards.append(
            f'<div class="card" style="border-top:3px solid {color};">'
            f'{header}{body}'
            f'</div>'
        )

    # Arrange in 2x2 grid
    return (
        f'<div class="grid-2">'
        f'{cards[0]}{cards[1]}'
        f'</div>'
        f'<div class="grid-2">'
        f'{cards[2]}{cards[3]}'
        f'</div>'
    )


def _render_recommendations(metrics: dict) -> str:
    """Actionable recommendations per quadrant."""
    recs = metrics.get('recommendations', [])
    if not recs:
        return ''

    parts = []
    for rec in recs:
        q = rec.get('quadrant', 'dog')
        item_name = rec.get('item', '')
        action = rec.get('action', '')
        color = _Q_COLORS.get(q, 'var(--amber)')
        label = _Q_LABELS.get(q, q.title())

        # Map quadrant to insight severity
        severity_map = {
            'plowhorse': 'blue',
            'puzzle': 'amber',
            'dog': 'red',
        }
        severity = severity_map.get(q, 'amber')

        parts.append(render_insight(
            f'<strong>{_safe(item_name)}</strong> ({label}): {_safe(action)}',
            severity=severity,
            tag=f'{label} ACTION',
        ))

    return render_card('Recommendations', '\n'.join(parts))


def _render_full_table(metrics: dict) -> str:
    """Full item table with all items."""
    items = metrics.get('items', [])
    if not items:
        return ''

    headers = ['Item', 'Group', 'Qty', 'Revenue', 'Avg Price', 'Quadrant']
    rows = []
    for item in items:
        q = item.get('quadrant', 'dog')
        color = _Q_COLORS.get(q, 'var(--text)')
        q_label = _Q_LABELS.get(q, q.title())
        badge = (
            f'<span style="display:inline-block;padding:1px 7px;border-radius:4px;'
            f'font-size:10px;font-weight:600;background:{_Q_BG_LIGHT.get(q, "transparent")};'
            f'color:{color};">{q_label}</span>'
        )
        rows.append([
            _safe(item['name']),
            _safe(item.get('menu_group', '')),
            fmt_num(item['qty_sold']),
            fmt_currency(item['revenue']),
            fmt_currency(item['avg_price']),
            badge,
        ])

    table_html = render_table(headers, rows, right_align_cols=[2, 3, 4])

    return render_card('All Items', table_html, subtitle=f'{len(items)} items sorted by revenue')


def _render_footer() -> str:
    """Page footer."""
    return (
        '<div style="text-align:center;color:var(--muted);font-size:11px;'
        'padding:20px 0 10px;border-top:1px solid var(--border);margin-top:20px;">'
        'Menu Engineering Matrix &middot; Livite Dashboard &middot; '
        'Data from Toast POS'
        '</div>'
    )


def _render_no_data() -> str:
    """Shown when no data is available at all."""
    return (
        '<div class="card" style="text-align:center;padding:60px 20px;">'
        '<div style="font-size:48px;margin-bottom:12px;">\U0001f4ca</div>'
        '<div style="font-size:16px;font-weight:600;color:var(--text);margin-bottom:8px;">'
        'No Menu Data Available</div>'
        '<div style="font-size:13px;color:var(--muted);max-width:400px;margin:0 auto;">'
        'No ItemSelectionDetails CSVs found for this period. '
        'Make sure Toast POS data has been fetched and cached in <code>.tmp/</code>.'
        '</div>'
        '</div>'
    )


# ---------------------------------------------------------------------------
# Main page builder
# ---------------------------------------------------------------------------

def build_menu_engineering_page(metrics: dict, logo_b64: str = "") -> str:
    """Build the complete Menu Engineering Matrix HTML page.

    Args:
        metrics: Output from compute_menu_engineering().
        logo_b64: Base64-encoded logo PNG (optional).

    Returns:
        Self-contained HTML string.
    """
    _reset_chart_counter()

    sections = []
    sections.append(_render_header(metrics, logo_b64))

    if metrics.get('total_items_analyzed', 0) == 0:
        sections.append(_render_no_data())
    else:
        sections.append(_render_kpi_grid(metrics))
        sections.append(_render_scatter_chart(metrics))
        sections.append(_render_quadrant_cards(metrics))
        sections.append(_render_recommendations(metrics))
        sections.append(_render_full_table(metrics))

    sections.append(_render_footer())

    body = "\n\n".join(s for s in sections if s)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Livite - Menu Engineering Matrix</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
{_CSS}
{_EXTRA_CSS}
</style>
</head>
<body>

{body}

{_LOADING_SNIPPET}
</body>
</html>"""
