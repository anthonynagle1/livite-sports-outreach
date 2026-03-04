"""
HTML Dashboard Template Engine for Livite Daily Analysis — V2.

Generates a self-contained, dark-themed HTML dashboard from computed metrics dicts.
All CSS is inlined; the only external dependency is Google Fonts CDN.

V2 enhancements:
  - 15-minute interval heatmap for hourly trends
  - Stacked horizontal bars for channel/category proportions
  - Insight callouts with actionable text
  - Side-by-side Uber vs Walk-In comparisons
  - Catering breakout card
  - Per-item modifier breakdown and free modification patterns
  - Customer capture by channel and spend analysis

Usage:
    from dashboard_html import build_dashboard
    html = build_dashboard(metrics, comparisons, anomalies)
    with open("dashboard.html", "w") as f:
        f.write(html)
"""

from __future__ import annotations


from __future__ import annotations

import html as _html
import json as _json
from typing import Any
from urllib.parse import quote as _url_quote

_DASH = "\u2014"  # em dash — used as default for missing values

# ---------------------------------------------------------------------------
# Chart.js Helpers
# ---------------------------------------------------------------------------

_CHART_COUNTER = 0  # auto-incrementing canvas ID

LIVITE_CHART_COLORS = [
    '#8cb82e', '#4a9cd8', '#9b72c4', '#2db88a',
    '#e8a830', '#e86040', '#475417', '#c44a8a',
]

def _next_chart_id() -> str:
    global _CHART_COUNTER
    _CHART_COUNTER += 1
    return f"lvc_{_CHART_COUNTER}"


def _reset_chart_counter():
    global _CHART_COUNTER
    _CHART_COUNTER = 0


def _js(obj) -> str:
    """Serialize to JS-safe JSON."""
    return _json.dumps(obj, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Chart Config Builders (shared between web canvas and QuickChart email)
# ---------------------------------------------------------------------------

def build_bar_config(labels: list, datasets: list, horizontal: bool = False,
                     stacked: bool = False, dollar: bool = False,
                     pct: bool = False, show_legend: bool = False,
                     bar_thickness: int = 0) -> dict:
    """Build a Chart.js bar chart config dict.

    Returns a JSON-serializable dict usable with both Chart.js v4 (web)
    and QuickChart.io v4 (email images). Web renderers add JS-only
    features (callbacks, annotation plugins) on top.
    """
    ds_list = []
    for i, ds in enumerate(datasets):
        color = ds.get("color", LIVITE_CHART_COLORS[i % len(LIVITE_CHART_COLORS)])
        colors = ds.get("colors", None)
        entry = {
            "label": ds.get("label", ""),
            "data": ds.get("data", []),
            "backgroundColor": colors if colors else color,
            "borderRadius": 4,
            "borderSkipped": False,
        }
        if bar_thickness > 0:
            entry["barThickness"] = bar_thickness
        if ds.get("type"):
            entry["type"] = ds["type"]
            entry["borderColor"] = ds.get("borderColor", color)
            entry["borderWidth"] = ds.get("borderWidth", 2)
            entry["pointRadius"] = ds.get("pointRadius", 3)
            entry["pointBackgroundColor"] = ds.get("pointBgColor", color)
            entry["fill"] = False
            entry.pop("borderRadius", None)
            entry.pop("borderSkipped", None)
            entry.pop("backgroundColor", None)
        ds_list.append(entry)

    return {
        "type": "bar",
        "data": {"labels": labels, "datasets": ds_list},
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "indexAxis": "y" if horizontal else "x",
            "plugins": {
                "legend": {"display": show_legend},
                "tooltip": {"enabled": True},
            },
            "scales": {
                "x": {
                    "stacked": stacked,
                    "grid": {"display": False} if horizontal else {"color": "rgba(0,0,0,0.05)"},
                    "ticks": {"font": {"size": 11}},
                },
                "y": {
                    "stacked": stacked,
                    "grid": {"color": "rgba(0,0,0,0.05)"},
                    "ticks": {"font": {"size": 11}},
                },
            },
        },
    }


def build_pie_config(labels: list, values: list, colors: list | None = None,
                     doughnut: bool = False) -> dict:
    """Build a Chart.js pie/doughnut config dict."""
    if colors is None:
        colors = LIVITE_CHART_COLORS[:len(labels)]
    chart_type = "doughnut" if doughnut else "pie"
    config = {
        "type": chart_type,
        "data": {
            "labels": labels,
            "datasets": [{
                "data": values,
                "backgroundColor": colors,
                "borderWidth": 2,
                "borderColor": "#ffffff",
            }],
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {
                "legend": {
                    "position": "bottom",
                    "labels": {"font": {"size": 11}, "padding": 12, "usePointStyle": True},
                },
            },
        },
    }
    if doughnut:
        config["options"]["cutout"] = "55%"
    return config


def build_line_config(labels: list, datasets: list, dollar: bool = False,
                      y2: bool = False) -> dict:
    """Build a Chart.js line chart config dict."""
    ds_list = []
    for i, ds in enumerate(datasets):
        color = ds.get("color", LIVITE_CHART_COLORS[i % len(LIVITE_CHART_COLORS)])
        entry = {
            "label": ds.get("label", ""),
            "data": ds.get("data", []),
            "borderColor": color,
            "backgroundColor": color + "33" if ds.get("fill") else "transparent",
            "borderWidth": ds.get("borderWidth", 2),
            "pointRadius": ds.get("pointRadius", 3),
            "pointBackgroundColor": color,
            "fill": ds.get("fill", False),
            "tension": 0.3,
        }
        if ds.get("borderDash"):
            entry["borderDash"] = ds["borderDash"]
        if ds.get("spanGaps") is not None:
            entry["spanGaps"] = ds["spanGaps"]
        if ds.get("type") == "bar":
            entry["type"] = "bar"
            entry["backgroundColor"] = ds.get("colors", color + "99")
            entry["borderRadius"] = 3
            entry.pop("pointRadius", None)
            entry.pop("pointBackgroundColor", None)
            entry.pop("tension", None)
            entry.pop("fill", None)
        if ds.get("yAxisID"):
            entry["yAxisID"] = ds["yAxisID"]
        if ds.get("order") is not None:
            entry["order"] = ds["order"]
        ds_list.append(entry)

    scales = {
        "x": {
            "grid": {"display": False},
            "ticks": {"font": {"size": 10}, "maxRotation": 0},
        },
        "y": {
            "grid": {"color": "rgba(0,0,0,0.05)"},
            "ticks": {"font": {"size": 11}},
        },
    }
    if y2:
        scales["y2"] = {
            "position": "right",
            "grid": {"display": False},
            "ticks": {"font": {"size": 11}},
        }

    return {
        "type": "line",
        "data": {"labels": labels, "datasets": ds_list},
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {
                "legend": {
                    "display": len(ds_list) > 1,
                    "labels": {"font": {"size": 11}, "usePointStyle": True},
                },
                "tooltip": {"mode": "index", "intersect": False},
            },
            "scales": scales,
        },
    }


# ---------------------------------------------------------------------------
# QuickChart.io — Render chart configs as PNG images for email
# ---------------------------------------------------------------------------

def quickchart_url(config: dict, width: int = 520, height: int = 220) -> str:
    """Build a QuickChart.io GET URL for a Chart.js config dict.

    Returns a URL that renders a PNG chart image on-demand (no API key).
    Uses Chart.js v4 to match the web dashboard.
    """
    config_json = _json.dumps(config, separators=(',', ':'))
    encoded = _url_quote(config_json, safe='')
    return "https://quickchart.io/chart?w=%d&h=%d&bkg=white&f=png&v=4&c=%s" % (
        width, height, encoded)


def quickchart_img(config: dict, width: int = 520, height: int = 220,
                   alt: str = "Chart") -> str:
    """Build an <img> tag for a QuickChart.io chart image."""
    url = quickchart_url(config, width, height)
    return (
        '<img src="%s" alt="%s" '
        'style="width:100%%;max-width:%dpx;height:auto;display:block;'
        'margin:0 auto;border-radius:6px;" />'
    ) % (url, alt, width)


# ---------------------------------------------------------------------------
# Chart.js HTML Renderers (web dashboard — canvas + script)
# ---------------------------------------------------------------------------

def render_chartjs_bar(labels: list, datasets: list, height: int = 250,
                       horizontal: bool = False, stacked: bool = False,
                       dollar: bool = False, pct: bool = False,
                       show_legend: bool = False, x_label: str = "",
                       y_label: str = "", bar_thickness: int = 0,
                       annotation_lines: list | None = None) -> str:
    """Render a Chart.js bar chart.

    datasets: list of {label, data: list[float], color: str, ...}
    annotation_lines: list of {value, color, label} for horizontal reference lines
    """
    cid = _next_chart_id()
    config = build_bar_config(labels, datasets, horizontal=horizontal,
                              stacked=stacked, dollar=dollar, pct=pct,
                              show_legend=show_legend, bar_thickness=bar_thickness)
    ds_js = config["data"]["datasets"]

    tick_cb = ""
    if dollar:
        tick_cb = "callback:function(v){return '$'+v.toLocaleString();},"
    elif pct:
        tick_cb = "callback:function(v){return v+'%';},"

    # Apply tick_cb only to the VALUE axis, not the label/category axis
    if horizontal:
        x_tick = tick_cb   # x is value axis for horizontal bars
        y_tick = ""        # y is label axis — no formatting
    else:
        x_tick = ""        # x is label axis — no formatting
        y_tick = tick_cb   # y is value axis for vertical bars

    idx_axis = "'y'" if horizontal else "'x'"
    x_grid = "display:false" if horizontal else "color:'rgba(0,0,0,0.05)'"
    y_grid = "color:'rgba(0,0,0,0.05)'" if horizontal else "color:'rgba(0,0,0,0.05)'"

    stacked_js = "true" if stacked else "false"
    legend_js = "true" if show_legend else "false"

    # Annotation plugin (simple reference lines via afterDraw)
    annotation_js = ""
    if annotation_lines:
        draws = []
        for al in annotation_lines:
            draws.append(
                f"{{value:{al['value']},color:'{al.get('color','#999')}',label:'{al.get('label','')}'}},"
            )
        annotation_js = f"""
        {{id:'refLines',afterDraw:function(chart){{
            var lines=[{' '.join(draws)}];
            lines.forEach(function(l){{
                var scale=chart.scales['{("x" if horizontal else "y")}'];
                var pos=scale.getPixelForValue(l.value);
                var ctx=chart.ctx;ctx.save();
                ctx.strokeStyle=l.color;ctx.lineWidth=1.5;ctx.setLineDash([4,3]);
                ctx.beginPath();
                if({'true' if horizontal else 'false'}){{ctx.moveTo(pos,chart.chartArea.top);ctx.lineTo(pos,chart.chartArea.bottom);}}
                else{{ctx.moveTo(chart.chartArea.left,pos);ctx.lineTo(chart.chartArea.right,pos);}}
                ctx.stroke();ctx.restore();
            }});
        }}}},"""

    tooltip_cb = ""
    if dollar:
        tooltip_cb = "callbacks:{label:function(c){return c.dataset.label+': $'+c.parsed['" + ("x" if horizontal else "y") + "'].toLocaleString();}},"
    elif pct:
        tooltip_cb = "callbacks:{label:function(c){return c.dataset.label+': '+c.parsed['" + ("x" if horizontal else "y") + "']+'%';}},"

    return (
        f'<div class="lvc" style="height:{height}px;position:relative;"><canvas id="{cid}"></canvas></div>'
        f'<script>'
        f'new Chart(document.getElementById("{cid}"),{{'
        f'type:"bar",'
        f'data:{{labels:{_js(labels)},datasets:{_js(ds_js)}}},'
        f'options:{{responsive:true,maintainAspectRatio:false,'
        f'indexAxis:{idx_axis},'
        f'plugins:{{legend:{{display:{legend_js}}},tooltip:{{enabled:true,{tooltip_cb}}}}},'
        f'scales:{{x:{{stacked:{stacked_js},grid:{{{x_grid}}},ticks:{{{x_tick}font:{{family:"DM Sans",size:11}}}}}},'
        f'y:{{stacked:{stacked_js},grid:{{{y_grid}}},ticks:{{{y_tick}font:{{family:"DM Sans",size:11}}}}}}}}}},'
        f'plugins:[{annotation_js}]'
        f'}});</script>'
    )


def render_chartjs_pie(labels: list, values: list, colors: list | None = None,
                       height: int = 220, doughnut: bool = False,
                       center_text: str = "") -> str:
    """Render a Chart.js pie or doughnut chart."""
    cid = _next_chart_id()
    config = build_pie_config(labels, values, colors=colors, doughnut=doughnut)
    colors = config["data"]["datasets"][0]["backgroundColor"]

    chart_type = config["type"]
    cutout = "cutout:'55%'," if doughnut else ""

    center_plugin = ""
    if center_text and doughnut:
        safe_text = _safe(center_text)
        center_plugin = f"""
        {{id:'centerText',afterDraw:function(chart){{
            var ctx=chart.ctx;var w=chart.chartArea;
            ctx.save();ctx.font='600 18px DM Sans';ctx.fillStyle='#2d2a24';
            ctx.textAlign='center';ctx.textBaseline='middle';
            ctx.fillText('{safe_text}',(w.left+w.right)/2,(w.top+w.bottom)/2);
            ctx.restore();
        }}}},"""

    return (
        f'<div class="lvc lvc-pie" style="height:{height}px;position:relative;max-width:min({height + 60}px,100%);margin:0 auto;">'
        f'<canvas id="{cid}"></canvas></div>'
        f'<script>'
        f'new Chart(document.getElementById("{cid}"),{{'
        f'type:"{chart_type}",'
        f'data:{{labels:{_js(labels)},datasets:[{{data:{_js(values)},backgroundColor:{_js(colors)},borderWidth:2,borderColor:"#ffffff"}}]}},'
        f'options:{{responsive:true,maintainAspectRatio:false,{cutout}'
        f'plugins:{{legend:{{position:"bottom",labels:{{font:{{family:"DM Sans",size:11}},padding:12,usePointStyle:true}}}},'
        f'tooltip:{{callbacks:{{label:function(c){{var t=c.dataset.data.reduce(function(a,b){{return a+b}},0);'
        f'var p=(c.raw/t*100).toFixed(1);return c.label+": $"+c.raw.toLocaleString()+" ("+p+"%)";}}}}}}'
        f'}}}},'
        f'plugins:[{center_plugin}]'
        f'}});</script>'
    )


def render_chartjs_line(labels: list, datasets: list, height: int = 250,
                        dollar: bool = False, y2: bool = False,
                        annotation_lines: list | None = None) -> str:
    """Render a Chart.js line chart.

    datasets: list of {label, data, color, fill?, yAxisID?, type?}
    """
    cid = _next_chart_id()
    config = build_line_config(labels, datasets, dollar=dollar, y2=y2)
    ds_js = config["data"]["datasets"]

    tick_cb = "callback:function(v){return '$'+v.toLocaleString();}," if dollar else ""

    y2_scale = ""
    if y2:
        y2_scale = ",y2:{position:'right',grid:{display:false},ticks:{font:{family:'DM Sans',size:11}}}"

    # Reference lines
    annotation_js = ""
    if annotation_lines:
        draws = []
        for al in annotation_lines:
            draws.append(f"{{value:{al['value']},color:'{al.get('color','#999')}',label:'{al.get('label','')}'}}," )
        annotation_js = f"""
        {{id:'refLines',afterDraw:function(chart){{
            var lines=[{' '.join(draws)}];
            lines.forEach(function(l){{
                var scale=chart.scales.y;var pos=scale.getPixelForValue(l.value);
                var ctx=chart.ctx;ctx.save();ctx.strokeStyle=l.color;ctx.lineWidth=1.5;ctx.setLineDash([4,3]);
                ctx.beginPath();ctx.moveTo(chart.chartArea.left,pos);ctx.lineTo(chart.chartArea.right,pos);
                ctx.stroke();
                if(l.label){{ctx.font='10px DM Sans';ctx.fillStyle=l.color;ctx.fillText(l.label,chart.chartArea.right-60,pos-4);}}
                ctx.restore();
            }});
        }}}},"""

    y_scale = f'y:{{grid:{{color:"rgba(0,0,0,0.05)"}},ticks:{{{tick_cb}font:{{family:"DM Sans",size:11}}}}}}'
    scales_inner = f'x:{{grid:{{display:false}},ticks:{{font:{{family:"DM Sans",size:10}},maxRotation:0}}}},{y_scale}{y2_scale}'
    return (
        f'<div class="lvc" style="height:{height}px;position:relative;"><canvas id="{cid}"></canvas></div>'
        f'<script>'
        f'new Chart(document.getElementById("{cid}"),{{'
        f'type:"line",'
        f'data:{{labels:{_js(labels)},datasets:{_js(ds_js)}}},'
        f'options:{{responsive:true,maintainAspectRatio:false,'
        f'plugins:{{legend:{{display:{_js(len(ds_js) > 1)},labels:{{font:{{family:"DM Sans",size:11}},usePointStyle:true}}}},'
        f'tooltip:{{mode:"index",intersect:false}}}},'
        f'scales:{{{scales_inner}}}}}'
        f',plugins:[{annotation_js}]'
        f'}});</script>'
    )

# ---------------------------------------------------------------------------
# Primitive Renderers
# ---------------------------------------------------------------------------

def _safe(val: Any) -> str:
    if val is None:
        return ""
    return _html.escape(str(val))


def fmt_currency(val: Any) -> str:
    if val is None:
        return "$0"
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "$0"
    if v < 0:
        return f"-${abs(v):,.2f}"
    return f"${v:,.2f}"


def fmt_pct(val: Any, decimals: int = 1) -> str:
    if val is None:
        return "N/A"
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "N/A"
    return f"{v:,.{decimals}f}%"


def fmt_num(val: Any, decimals: int = 0) -> str:
    if val is None:
        return "N/A"
    try:
        v = float(val)
    except (TypeError, ValueError):
        return "N/A"
    if decimals == 0:
        return f"{int(round(v)):,}"
    return f"{v:,.{decimals}f}"


def color_for_delta(pct: Any, invert: bool = False) -> str:
    if pct is None:
        return "var(--muted)"
    try:
        v = float(pct)
    except (TypeError, ValueError):
        return "var(--muted)"
    if v == 0:
        return "var(--muted)"
    positive_is_good = not invert
    if (v > 0) == positive_is_good:
        return "var(--green)"
    return "var(--red)"


def fmt_delta(diff: Any, pct: Any, direction: str = "up") -> str:
    if pct is None and diff is None:
        return '<span style="color:var(--muted);">\u2014</span>'
    invert = direction == "down"
    color = color_for_delta(pct, invert=invert)
    parts = []
    if pct is not None:
        try:
            p = float(pct)
            arrow = "\u25B2" if p > 0 else ("\u25BC" if p < 0 else "")
            sign = "+" if p > 0 else ""
            parts.append(f"{arrow}{sign}{p:.1f}%")
        except (TypeError, ValueError):
            pass
    if diff is not None and not parts:
        try:
            d = float(diff)
            arrow = "\u25B2" if d > 0 else ("\u25BC" if d < 0 else "")
            sign = "+" if d > 0 else ""
            parts.append(f"{arrow}{sign}{d:,.0f}")
        except (TypeError, ValueError):
            pass
    text = " ".join(parts) if parts else "\u2014"
    return f'<span style="color:{color};font-size:11px;font-family:\'JetBrains Mono\',monospace;">{text}</span>'


# ---------------------------------------------------------------------------
# Component Renderers
# ---------------------------------------------------------------------------

def render_stat(label: str, value: str, subtitle: str = "", color: str | None = None,
                delta_html: str = "", period_deltas: dict | None = None,
                avg_value: str = "") -> str:
    import html as _h
    style = f' style="color:{color};"' if color else ""
    sub = f'<div class="sub">{subtitle}</div>' if subtitle else ""
    # If period_deltas supplied, embed all periods as data attrs for JS toggle
    if period_deltas:
        attrs = ""
        for p, html_str in period_deltas.items():
            attrs += f' data-{p}-html="{_h.escape(html_str)}"'
        # Show wow by default
        default_html = period_deltas.get("wow", "")
        delta = f'<div class="sub delta-toggle"{attrs}>{default_html}</div>'
    elif delta_html:
        delta = f'<div class="sub">{delta_html}</div>'
    else:
        delta = ""
    # Range mode: store both total and avg values for JS toggle
    avg_attr = f' data-avg="{_h.escape(avg_value)}" data-total="{_h.escape(value)}"' if avg_value else ""
    return (
        f'<div class="stat">'
        f'<div class="l">{_safe(label)}</div>'
        f'<div class="v range-toggle"{avg_attr}{style}>{value}</div>'
        f'{sub}{delta}'
        f'</div>'
    )


def render_stat_grid(stats_list: list[str]) -> str:
    inner = "\n".join(stats_list)
    return f'<div class="stat-grid">\n{inner}\n</div>'


def render_insight(text: str, severity: str = "amber", tag: str = "") -> str:
    sev_class = f" {severity}" if severity != "amber" else ""
    tag_html = f'<div class="tag">{_safe(tag)}</div>' if tag else ""
    return (
        f'<div class="insight{sev_class}">'
        f'{tag_html}'
        f'{text}'
        f'</div>'
    )


_LIGHT_BAR_FILLS = {
    "var(--bar-amber)", "var(--bar-cyan)", "var(--livite-lime)",
    "#e8a830", "#8cb82e", "#2db88a",
}

def _bar_text_color(bg_color: str) -> str:
    """Return dark text for light bar fills, white for dark ones."""
    if bg_color in _LIGHT_BAR_FILLS:
        return "#2d2a24"
    return "#fff"


def render_bar_h(label: str, value: float | None, max_value: float | None,
                 color: str = "var(--cyan)", bar_text: str = "") -> str:
    if value is None or max_value is None or max_value == 0:
        pct_w = 0
    else:
        pct_w = min(100, max(0, (float(value) / float(max_value)) * 100))
    txt_color = _bar_text_color(color) if bar_text else ""
    text_span = f'<span class="bar-text" style="color:{txt_color};">{bar_text}</span>' if bar_text else ""
    return (
        f'<div class="bar-h">'
        f'<span class="label">{_safe(label)}</span>'
        f'<div class="bar-track">'
        f'<div class="bar-fill" style="width:{pct_w:.1f}%;background:{color};opacity:0.7;">{text_span}</div>'
        f'</div>'
        f'</div>'
    )

def render_stacked_bar(segments: list[dict], height: int = 28) -> str:
    """Render a stacked horizontal bar.
    Each segment: {label, value, color, pct}
    """
    total = sum(s.get("value", 0) for s in segments)
    if total == 0:
        return '<div class="stacked-bar"><div class="bar-track" style="height:28px;"></div></div>'
    parts = []
    for s in segments:
        val = s.get("value", 0)
        pct = (val / total * 100) if total > 0 else 0
        color = s.get("color", "var(--cyan)")
        label = s.get("label", "")
        txt_color = _bar_text_color(color)
        text = f' title="{_safe(label)}: {fmt_currency(val)} ({pct:.0f}%)"'
        if pct >= 12:
            inner = f'<span style="position:absolute;left:6px;top:50%;transform:translateY(-50%);font-size:10px;color:{txt_color};white-space:nowrap;">{_safe(label)} {pct:.0f}%</span>'
        else:
            inner = ""
        parts.append(
            f'<div style="width:{pct:.1f}%;background:{color};height:{height}px;position:relative;opacity:0.8;"{text}>{inner}</div>'
        )
    bar = "".join(parts)
    return f'<div class="stacked-bar" style="display:flex;border-radius:4px;overflow:hidden;margin:6px 0;">{bar}</div>'


def render_vertical_bars(items: list, height: int = 120, bar_width: int = 0,
                         show_values: bool = True, value_prefix: str = "",
                         value_suffix: str = "") -> str:
    """Render a vertical bar chart.

    Args:
        items: list of {"label": str, "value": float, "color": str}
               Optional keys: "bar_text" (override label above bar)
        height: chart height in pixels
        bar_width: bar width in px (0 = auto/flex)
        show_values: show value labels above bars
        value_prefix: e.g. "$" for currency
        value_suffix: e.g. "%" for percentages
    """
    if not items:
        return ""
    max_val = max((it.get("value", 0) for it in items), default=1)
    if max_val <= 0:
        max_val = 1

    n = len(items)
    bw = bar_width if bar_width > 0 else max(18, min(40, 300 // max(n, 1)))
    gap = max(2, min(6, 60 // max(n, 1)))

    bars_html = []
    for it in items:
        val = it.get("value", 0)
        color = it.get("color", "var(--bar-green)")
        label = it.get("label", "")
        bar_text = it.get("bar_text", "")
        pct_h = max(2, val / max_val * 100) if val > 0 else 0
        bar_h = max(2, int(pct_h / 100 * height))

        val_label = ""
        if show_values and val > 0:
            if bar_text:
                val_label = bar_text
            elif value_prefix == "$":
                if val >= 1000:
                    val_label = f"${val:,.0f}"
                else:
                    val_label = f"${val:.0f}" if val == int(val) else f"${val:,.2f}"
            elif value_suffix:
                val_label = f"{val:.0f}{value_suffix}"
            else:
                val_label = f"{val:,.0f}" if val == int(val) else f"{val:.1f}"

        bars_html.append(
            f'<div style="display:flex;flex-direction:column;align-items:center;width:{bw}px;">'
            f'<div style="font-size:9px;color:var(--muted);margin-bottom:2px;white-space:nowrap;">{_safe(val_label)}</div>'
            f'<div style="width:100%;height:{height}px;display:flex;align-items:flex-end;">'
            f'<div style="width:100%;height:{bar_h}px;background:{color};border-radius:3px 3px 0 0;opacity:0.8;" '
            f'title="{_safe(label)}: {val_label}"></div>'
            f'</div>'
            f'<div style="font-size:8px;color:var(--muted);margin-top:3px;text-align:center;'
            f'max-width:{max(bw + 10, 60)}px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"'
            f' title="{_safe(label)}">{_safe(label[:18])}</div>'
            f'</div>'
        )

    return (
        f'<div style="display:flex;align-items:flex-end;gap:{gap}px;padding:4px 0;overflow-x:auto;">'
        + "".join(bars_html) +
        '</div>'
    )


def render_heatmap_cell(value: float, max_value: float, label: str = "", sublabel: str = "") -> str:
    """Render a heatmap cell colored by intensity (low=cool, high=warm)."""
    if max_value == 0:
        intensity = 0
    else:
        intensity = min(1.0, value / max_value)
    # Color gradient: dark → blue → cyan → green → yellow
    if intensity < 0.25:
        bg = f"rgba(96,165,250,{0.1 + intensity * 0.4})"
    elif intensity < 0.5:
        bg = f"rgba(34,211,238,{0.2 + intensity * 0.5})"
    elif intensity < 0.75:
        bg = f"rgba(52,211,153,{0.3 + intensity * 0.5})"
    else:
        bg = f"rgba(251,191,36,{0.4 + intensity * 0.5})"
    label_html = f'<div style="font-size:9px;color:var(--muted);">{_safe(label)}</div>' if label else ""
    sub_html = f'<div style="font-size:9px;color:var(--muted);opacity:0.8;">{_safe(sublabel)}</div>' if sublabel else ""
    return (
        f'<div class="heatmap-cell" style="background:{bg};padding:4px 3px;text-align:center;border-radius:3px;min-width:20px;">'
        f'{label_html}'
        f'<div style="font-size:11px;font-weight:600;font-family:\'JetBrains Mono\',monospace;">{fmt_currency(value)}</div>'
        f'{sub_html}'
        f'</div>'
    )


def render_table(headers: list[str], rows: list[list[str]],
                 right_align_cols: list[int] | None = None) -> str:
    if right_align_cols is None:
        right_align_cols = []
    th_parts = []
    for i, h in enumerate(headers):
        cls = ' class="r"' if i in right_align_cols else ""
        th_parts.append(f"<th{cls}>{_safe(h)}</th>")
    thead = "<tr>" + "".join(th_parts) + "</tr>"
    body_rows = []
    for row in rows:
        tds = []
        for i, cell in enumerate(row):
            cls = ' class="r n"' if i in right_align_cols else ""
            tds.append(f"<td{cls}>{cell}</td>")
        body_rows.append("<tr>" + "".join(tds) + "</tr>")
    return f'<table>\n{thead}\n' + "\n".join(body_rows) + "\n</table>"


def render_badge(text: str, color_class: str = "b") -> str:
    return f'<span class="badge {color_class}">{_safe(text)}</span>'


def render_divider() -> str:
    return '<hr class="divider">'


def render_card(title: str, content: str, subtitle: str = "") -> str:
    sub = f'<div style="font-size:11px;color:var(--muted);margin-top:-10px;margin-bottom:12px;">{subtitle}</div>' if subtitle else ""
    return (
        f'<div class="card">\n'
        f'<h2>{title}</h2>\n'
        f'{sub}'
        f'{content}\n'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Helper: safe dict access
# ---------------------------------------------------------------------------

def _g(d, *keys, default=None):
    if d is None:
        return default
    cur = d
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k, default)
        else:
            return default
    return cur if cur is not None else default


def _gl(d, key, default=None):
    if d is None:
        return default if default is not None else []
    if isinstance(d, dict):
        val = d.get(key)
    else:
        return default if default is not None else []
    if val is None:
        return default if default is not None else []
    return val


def _delta_html(deltas: dict | None, metric_key: str, period: str = "wow", direction: str = "up") -> str:
    """Build delta HTML from the deltas dict structure.
    deltas[metric_key][period] = (diff, pct, dir_str)
    """
    if deltas is None:
        return ""
    metric_deltas = _g(deltas, metric_key)
    if metric_deltas is None:
        return ""
    tup = _g(metric_deltas, period)
    if tup is None or not isinstance(tup, tuple) or len(tup) < 3:
        return ""
    diff, pct, _ = tup
    return fmt_delta(diff, pct, direction)


# ---------------------------------------------------------------------------
# Section 0: Header
# ---------------------------------------------------------------------------
