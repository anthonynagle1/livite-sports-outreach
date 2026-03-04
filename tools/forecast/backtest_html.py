"""HTML renderer for the Livite Backtest page.

Builds a self-contained page that:
1. Shows a date range picker form
2. Renders per-day predicted vs actual results
3. Shows aggregate accuracy statistics
4. Links each day to /today/YYYYMMDD for the full prediction view

All forecasts use strictly as-of data — no look-ahead. This is noted
prominently so the results are not confused with retrofitted predictions.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from htmlrender.components import (
    _reset_chart_counter, render_chartjs_bar, render_chartjs_line,
    render_stat, render_stat_grid, render_card,
    render_table, fmt_currency, fmt_pct, fmt_num, _safe,
    LIVITE_CHART_COLORS, _next_chart_id,
)
from htmlrender.sections import _CSS, _LOADING_SNIPPET

_GREEN = "#8cb82e"
_GREEN_LIGHT = "rgba(140,184,46,0.35)"
_BLUE = "#4a9cd8"
_BLUE_LIGHT = "rgba(74,156,216,0.35)"
_RED = "#e86040"
_RED_LIGHT = "rgba(232,96,64,0.35)"
_ORANGE = "#e8a830"
_GRAY = "#999"

_SUB_NAV = (
    '<div style="display:flex;justify-content:center;gap:8px;margin-top:12px;flex-wrap:wrap;">'
    '<a href="/" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Home</a>'
    '<a href="/today" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Today</a>'
    '<a href="/week" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">This Week</a>'
    '<a href="/forecast" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">P&amp;L Forecast</a>'
    '<a href="/backtest" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:#4a9cd8;color:#fff;text-decoration:none;font-weight:600;">Backtest</a>'
    '</div>'
)


def build_backtest_page(result: dict, logo_b64: str = "",
                        form_start: str = "", form_end: str = "") -> str:
    """Build the complete Backtest HTML page."""
    _reset_chart_counter()

    sections = []
    sections.append(_render_header(result, logo_b64, form_start, form_end))

    if result.get("days"):
        sections.append(_render_honesty_note())
        sections.append(_render_summary(result))
        sections.append(_render_charts(result))
        sections.append(_render_table(result))
    elif form_start and form_end:
        sections.append(
            render_card("No Data", '<p style="color:var(--muted);">No data found for this range. '
                        'Try a different date range.</p>')
        )

    sections.append(_render_footer())
    body = "\n\n".join(s for s in sections if s)

    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Livite - Backtest</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
%s
%s
</style>
</head>
<body>

%s

%s
</body>
</html>""" % (_CSS, _EXTRA_CSS, body, _LOADING_SNIPPET)


def _render_header(result, logo_b64, form_start, form_end):
    logo_html = ""
    if logo_b64:
        logo_html = (
            '<img src="data:image/png;base64,%s" alt="Livite" '
            'style="height:32px;margin-right:12px;vertical-align:middle;">' % logo_b64
        )

    range_label = result.get("range_label", "")
    subtitle = ('&nbsp;&middot;&nbsp;<span style="color:var(--muted);">%s</span>' % range_label
                if range_label else "")

    # Date inputs — default to last 30 days if no values provided
    from datetime import datetime, timedelta
    today = datetime.now()
    default_end = today.strftime("%Y-%m-%d")
    default_start = (today - timedelta(days=29)).strftime("%Y-%m-%d")

    start_val = _iso_from_slug(form_start) or default_start
    end_val = _iso_from_slug(form_end) or default_end

    form_html = (
        '<form method="GET" action="/backtest" '
        'style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;'
        'justify-content:center;margin-top:14px;">'
        '<label style="font-size:13px;color:var(--muted);">From</label>'
        '<input type="date" name="start" value="%s" '
        'style="padding:6px 10px;border:1px solid var(--border);border-radius:6px;'
        'background:var(--bg);color:var(--text);font-size:13px;">'
        '<label style="font-size:13px;color:var(--muted);">To</label>'
        '<input type="date" name="end" value="%s" '
        'style="padding:6px 10px;border:1px solid var(--border);border-radius:6px;'
        'background:var(--bg);color:var(--text);font-size:13px;">'
        '<button type="submit" '
        'style="padding:6px 18px;background:#4a9cd8;color:#fff;border:none;'
        'border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;">Run</button>'
        '</form>'
        '<div style="display:flex;gap:8px;justify-content:center;margin-top:8px;flex-wrap:wrap;">'
        + _preset_link("Last 7 days", today - timedelta(days=6), today)
        + _preset_link("Last 30 days", today - timedelta(days=29), today)
        + _preset_link("Last 60 days", today - timedelta(days=59), today)
        + _preset_link("Last 90 days", today - timedelta(days=89), today)
        + '</div>'
    ) % (start_val, end_val)

    return (
        '<div class="card" style="text-align:center;padding:20px;">'
        + logo_html
        + '<h1 style="display:inline;font-size:22px;vertical-align:middle;">Forecast Backtest</h1>'
        + subtitle
        + _SUB_NAV
        + form_html
        + '</div>'
    )


def _preset_link(label, start, end):
    from datetime import datetime
    s = start.strftime("%Y-%m-%d")
    e = end.strftime("%Y-%m-%d")
    return (
        '<a href="/backtest?start=%s&end=%s" '
        'style="font-size:11px;color:#4a9cd8;text-decoration:none;'
        'padding:3px 8px;border:1px solid rgba(74,156,216,0.3);border-radius:4px;">%s</a>'
    ) % (s, e, label)


def _render_honesty_note():
    return (
        '<div style="background:rgba(74,156,216,0.08);border:1px solid rgba(74,156,216,0.2);'
        'border-radius:8px;padding:10px 14px;font-size:12px;color:var(--text);'
        'display:flex;align-items:center;gap:8px;margin-bottom:0;">'
        '<span style="font-size:16px;">&#128274;</span>'
        '<span><strong>Honest backtesting:</strong> Each day\'s prediction was built using '
        'only data available <em>before</em> that day. No future data was used to train the '
        'model. This is what the model would have actually predicted at the time.</span>'
        '</div>'
    )


def _render_summary(result):
    summary = result.get("summary", {})
    if not summary:
        return ""

    tv = summary.get("total_variance", 0)
    tvp = summary.get("total_variance_pct", 0)
    tv_color = _GREEN if tv >= 0 else _RED

    bias = summary.get("bias", 0)
    bias_label = "Avg daily over" if bias > 0 else "Avg daily under"
    bias_color = _GREEN if bias > 0 else _RED

    w15 = summary.get("within_15pct", 0)
    w15_color = _GREEN if w15 >= 80 else _ORANGE if w15 >= 60 else _RED

    over = summary.get("over_days", 0)
    under = summary.get("under_days", 0)

    stats = [
        render_stat("Days Evaluated", str(summary.get("days_evaluated", 0))),
        render_stat("Total Predicted", fmt_currency(summary.get("total_predicted", 0))),
        render_stat("Total Actual", fmt_currency(summary.get("total_actual", 0))),
        render_stat(
            "Total Variance",
            '<span style="color:%s;font-weight:700;">%s%s (%.1f%%)</span>'
            % (tv_color, "+" if tv >= 0 else "", fmt_currency(tv), tvp),
        ),
        render_stat("MAE (daily)", fmt_currency(summary.get("mae", 0))),
        render_stat("RMSE (daily)", fmt_currency(summary.get("rmse", 0))),
        render_stat(
            bias_label,
            '<span style="color:%s;font-weight:600;">%s%s/day</span>'
            % (bias_color, "+" if bias > 0 else "", fmt_currency(abs(bias))),
        ),
        render_stat(
            "Within ±15%",
            '<span style="color:%s;font-weight:700;">%.0f%%</span>' % (w15_color, w15),
            subtitle="of days",
        ),
        render_stat(
            "Within ±10%",
            '<span style="color:%s;font-weight:700;">%.0f%%</span>'
            % (_GREEN if summary.get("within_10pct", 0) >= 70 else _ORANGE,
               summary.get("within_10pct", 0)),
            subtitle="of days",
        ),
        render_stat("Over / Under", "%d over · %d under" % (over, under)),
    ]

    content = '<div style="display:flex;gap:12px;flex-wrap:wrap;">%s</div>' % "".join(stats)
    return render_card("Accuracy Summary", content,
                       subtitle=result.get("range_label", ""))


def _render_charts(result):
    days = [d for d in result.get("days", []) if not d.get("skipped")]
    if not days:
        return ""

    valid = [d for d in days if d.get("predicted") and d.get("actual")]
    if not valid:
        return ""

    labels = [d["label"] for d in valid]
    pred_vals = [d["predicted"] for d in valid]
    act_vals = [d["actual"] for d in valid]
    variance_vals = [d.get("variance", 0) or 0 for d in valid]
    acc_vals = [max(0, 100 - abs(d.get("variance_pct", 0) or 0)) for d in valid]

    # ── Chart 1: Predicted vs Actual grouped bars ──
    bar_html = render_chartjs_bar(
        labels=labels,
        datasets=[
            {"label": "Predicted", "data": pred_vals, "color": _BLUE_LIGHT,
             "borderColor": _BLUE, "borderWidth": 1.5},
            {"label": "Actual", "data": act_vals, "color": _GREEN_LIGHT,
             "borderColor": _GREEN, "borderWidth": 1.5},
        ],
        height=220,
        dollar=True,
        show_legend=True,
    )

    # ── Chart 2: Daily variance (delta) ──
    var_colors = [_GREEN_LIGHT if v >= 0 else _RED_LIGHT for v in variance_vals]
    var_borders = [_GREEN if v >= 0 else _RED for v in variance_vals]
    var_chart_html = render_chartjs_bar(
        labels=labels,
        datasets=[
            {"label": "Variance (Actual − Predicted)", "data": variance_vals,
             "color": var_colors, "borderColor": var_borders, "borderWidth": 1.5},
        ],
        height=160,
        dollar=True,
        show_legend=False,
    )

    # ── Chart 3: Day-by-day accuracy % ──
    acc_colors = [
        (_GREEN_LIGHT if v >= 90 else "rgba(232,168,48,0.3)" if v >= 75 else _RED_LIGHT)
        for v in acc_vals
    ]
    acc_borders = [
        (_GREEN if v >= 90 else _ORANGE if v >= 75 else _RED)
        for v in acc_vals
    ]
    acc_chart_html = render_chartjs_bar(
        labels=labels,
        datasets=[
            {"label": "Accuracy %", "data": acc_vals,
             "color": acc_colors, "borderColor": acc_borders, "borderWidth": 1.5},
        ],
        height=160,
        pct=True,
        show_legend=False,
    )

    charts_content = (
        '<div style="margin-bottom:16px;">'
        '<div style="font-size:12px;color:var(--muted);font-weight:600;'
        'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">'
        'Predicted vs Actual Revenue</div>'
        + bar_html
        + '</div>'
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">'
        '<div>'
        '<div style="font-size:12px;color:var(--muted);font-weight:600;'
        'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">'
        'Daily Variance (Actual − Predicted)</div>'
        + var_chart_html
        + '</div>'
        '<div>'
        '<div style="font-size:12px;color:var(--muted);font-weight:600;'
        'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">'
        'Day-by-Day Accuracy</div>'
        + acc_chart_html
        + '</div>'
        '</div>'
    )

    return render_card("Charts", charts_content)


def _render_table(result):
    days = result.get("days", [])
    if not days:
        return ""

    rows = []
    for d in days:
        if d.get("skipped"):
            rows.append([
                d["label"],
                d["dow_name"],
                "—",
                _safe(str(d.get("actual", "—") or "—")),
                "—",
                "—",
                '<span style="color:var(--muted);font-size:11px;">%s</span>'
                % _safe(d.get("skip_reason", "skipped")),
            ])
            continue

        pred = d.get("predicted")
        act = d.get("actual")
        v = d.get("variance")
        vp = d.get("variance_pct")
        acc = 100 - abs(vp or 0)

        v_color = _GREEN if (v or 0) >= 0 else _RED
        acc_color = _GREEN if acc >= 90 else _ORANGE if acc >= 75 else _RED

        v_cell = (
            '<span style="color:%s;font-weight:600;">%s%s (%.1f%%)</span>'
            % (v_color, "+" if (v or 0) >= 0 else "", fmt_currency(v or 0), vp or 0)
            if v is not None else "—"
        )
        acc_cell = (
            '<span style="color:%s;font-weight:700;">%.0f%%</span>' % (acc_color, acc)
            if v is not None else "—"
        )

        day_link = (
            '<a href="/today/%s" style="color:var(--primary);text-decoration:none;'
            'font-weight:500;">%s</a>' % (d["date_slug"], d["label"])
        )

        note = d.get("interpretation") or ""
        note_cell = (
            '<span style="font-size:11px;color:var(--muted);font-style:italic;">%s</span>'
            % _safe(note)
            if note else ""
        )

        rows.append([
            day_link,
            d["dow_name"],
            fmt_currency(pred) if pred else "—",
            fmt_currency(act) if act else "—",
            v_cell,
            acc_cell,
            note_cell,
        ])

    table_html = render_table(
        headers=["Date", "Day", "Predicted", "Actual", "Variance", "Accuracy", "Notes"],
        rows=rows,
    )
    return render_card("Day-by-Day Results", table_html,
                       subtitle="Click any date to view full prediction details")


def _render_footer():
    from datetime import datetime
    now = datetime.now().strftime("%b %d, %Y %I:%M %p")
    return (
        '<div style="text-align:center;padding:16px;font-size:11px;color:var(--muted);">'
        'Generated %s &middot; Predictions use strictly as-of training data'
        '</div>' % now
    )


def _iso_from_slug(s: str) -> str:
    """Convert YYYYMMDD or YYYY-MM-DD to YYYY-MM-DD, or return '' on failure."""
    if not s:
        return ""
    s = s.strip()
    if len(s) == 8 and s.isdigit():
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:])
    if len(s) == 10 and s[4] == "-":
        return s
    return ""


_EXTRA_CSS = """
.backtest-note {
    background: rgba(74,156,216,0.08);
    border: 1px solid rgba(74,156,216,0.2);
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 12px;
}
"""
