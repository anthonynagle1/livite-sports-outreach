"""Build self-contained HTML page for Today's Prediction dashboard.

Renders:
1. Weather card with multiplier
2. KPI grid (predicted revenue, net income, covers, peak hour)
3. Prediction explanation (step-by-step reasoning)
4. Hourly distribution chart (7am-10pm)
5. Peak analysis
6. Daypart breakdown table
7. Scheduled catering orders
8. This-week context (actuals + forecast)
9. Full daily P&L

Uses existing Chart.js helpers and Livite theme.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from htmlrender.components import (
    _reset_chart_counter, render_chartjs_bar, render_chartjs_pie, render_chartjs_line,
    render_stat, render_stat_grid, render_card,
    render_table, fmt_currency, fmt_pct, fmt_num, _safe,
    LIVITE_CHART_COLORS, _next_chart_id, _js,
)
from htmlrender.sections import _CSS, _LOADING_SNIPPET

from .data import EXPENSE_GROUP_ORDER


_SUB_NAV = (
    '<div style="display:flex;justify-content:center;gap:8px;margin-top:12px;flex-wrap:wrap;">'
    '<a href="/" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Home</a>'
    '<a href="/today" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:#4a9cd8;color:#fff;text-decoration:none;font-weight:600;">Today</a>'
    '<a href="/week" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">This Week</a>'
    '<a href="/forecast" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">P&amp;L Forecast</a>'
    '<a href="/backtest" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Backtest</a>'
    '<a href="/schedule" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Schedule</a>'
    '</div>'
)

_GREEN = "#8cb82e"
_GREEN_LIGHT = "rgba(140,184,46,0.35)"
_BLUE = "#4a9cd8"
_BLUE_LIGHT = "rgba(74,156,216,0.35)"
_RED = "#e86040"
_DARK_GREEN = "#475417"
_ORANGE = "#e8a830"
_ORANGE_LIGHT = "rgba(232,168,48,0.35)"
_PURPLE = "#9b72c4"
_GRAY = "#999"


def build_today_page(metrics: dict, logo_b64: str = "") -> str:
    """Build the complete Today's Prediction HTML page."""
    _reset_chart_counter()

    is_past = metrics.get("is_past", False)

    sections = []
    sections.append(_render_header(metrics, logo_b64))

    # For past days: show actual vs predicted banner first
    if is_past:
        sections.append(_render_actual_vs_predicted(metrics))

    sections.append(_render_weather(metrics))
    sections.append(_render_kpis(metrics))
    sections.append(_render_channel_doughnut(metrics))
    sections.append(_render_explanation(metrics))
    sections.append(_render_hourly_chart(metrics))
    sections.append(_render_peaks(metrics))
    sections.append(_render_dayparts(metrics))
    sections.append(_render_catering(metrics))
    sections.append(_render_this_week(metrics))
    sections.append(_render_last_week_accuracy(metrics))
    sections.append(_render_discount_health(metrics))
    sections.append(_render_daily_pl(metrics))
    sections.append(_render_footer())

    body = "\n\n".join(s for s in sections if s)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Livite - Today's Prediction</title>
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


_EXTRA_CSS = """
.weather-card {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 16px 20px;
    background: var(--surface);
    border-radius: 12px;
    border: 1px solid var(--border);
    margin-bottom: 16px;
}
.weather-icon {
    font-size: 32px;
    line-height: 1;
}
.weather-main {
    flex: 1;
}
.weather-temp {
    font-size: 28px;
    font-weight: 700;
    color: var(--text);
}
.weather-conditions {
    font-size: 14px;
    color: var(--muted);
}
.weather-details {
    display: flex;
    gap: 16px;
    font-size: 12px;
    color: var(--muted);
    margin-top: 4px;
}
.weather-multiplier {
    padding: 6px 12px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
    white-space: nowrap;
}
.weather-multiplier.negative {
    background: rgba(232,96,64,0.12);
    color: #e86040;
}
.weather-multiplier.neutral {
    background: rgba(140,184,46,0.12);
    color: #8cb82e;
}
.explain-steps {
    display: flex;
    flex-direction: column;
    gap: 8px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
}
.explain-step {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 12px;
    border-radius: 6px;
    background: var(--bg);
}
.explain-step.final {
    background: rgba(140,184,46,0.12);
    font-weight: 700;
    font-size: 15px;
}
.explain-step .step-label {
    font-family: 'DM Sans', sans-serif;
    font-weight: 500;
    color: var(--text);
}
.explain-step .step-detail {
    font-size: 11px;
    color: var(--muted);
    font-family: 'DM Sans', sans-serif;
}
.explain-step .step-value {
    text-align: right;
    white-space: nowrap;
}
.explain-step .step-delta {
    font-size: 12px;
}
.explain-step .step-delta.positive { color: #8cb82e; }
.explain-step .step-delta.negative { color: #e86040; }
.explain-narrative {
    font-size: 13px;
    color: var(--muted);
    line-height: 1.6;
    margin-top: 12px;
    padding: 12px;
    background: var(--bg);
    border-radius: 8px;
}
.confidence-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}
.confidence-high { background: rgba(140,184,46,0.15); color: #8cb82e; }
.confidence-medium { background: rgba(232,168,48,0.15); color: #e8a830; }
.confidence-low { background: rgba(232,96,64,0.15); color: #e86040; }
.week-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
    gap: 6px;
    margin-bottom: 12px;
}
.week-day {
    text-align: center;
    padding: 10px 8px;
    border-radius: 8px;
    background: var(--bg);
}
.week-day.actual { border-left: 3px solid #8cb82e; }
.week-day.forecast { border-left: 3px solid #4a9cd8; opacity: 0.8; }
.week-day.today { border: 2px solid #8cb82e; background: rgba(140,184,46,0.08); }
.week-day .wd-name {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    color: var(--muted);
}
.week-day .wd-rev {
    font-size: 16px;
    font-weight: 700;
    color: var(--text);
    margin-top: 4px;
}
.week-day .wd-label {
    font-size: 9px;
    color: var(--muted);
    margin-top: 2px;
}
.pl-mini {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    font-family: 'JetBrains Mono', monospace;
}
.pl-mini th, .pl-mini td {
    padding: 6px 10px;
    text-align: right;
    border-bottom: 1px solid var(--border);
}
.pl-mini th:first-child, .pl-mini td:first-child {
    text-align: left;
    font-family: 'DM Sans', sans-serif;
    font-weight: 500;
}
.pl-mini thead th {
    font-size: 11px;
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
}
.pl-mini .subtotal-row td {
    font-weight: 600;
    border-top: 2px solid var(--border);
}
.pl-mini .section-header td {
    font-weight: 700;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--muted);
    padding-top: 12px;
    border-bottom: none;
}
.pl-mini .indent td:first-child {
    padding-left: 20px;
    font-weight: 400;
    color: var(--muted);
}
"""


# ══════════════════════════════════════════════════════════════════════════════
# Sections
# ══════════════════════════════════════════════════════════════════════════════


def _render_header(m, logo_b64):
    logo_html = ""
    if logo_b64:
        logo_html = (
            '<img src="data:image/png;base64,%s" alt="Livite" '
            'style="height:32px;margin-right:12px;vertical-align:middle;">' % logo_b64
        )

    date_str = m.get("date", "")
    dow = m.get("dow_name", "")
    is_past = m.get("is_past", False)
    is_today = m.get("is_today", True)

    if is_past:
        title = "Past Day View"
        subtitle_tag = (
            '<span style="font-size:12px;background:rgba(74,156,216,0.12);'
            'color:#4a9cd8;padding:2px 8px;border-radius:4px;margin-left:8px;">'
            'Historical</span>'
        )
    elif is_today:
        title = "Today's Prediction"
        subtitle_tag = ""
    else:
        title = "Upcoming Forecast"
        subtitle_tag = (
            '<span style="font-size:12px;background:rgba(140,184,46,0.12);'
            'color:#8cb82e;padding:2px 8px;border-radius:4px;margin-left:8px;">'
            'Forecast</span>'
        )

    day_selector = _render_day_selector(m)

    return (
        '<div class="card" style="text-align:center;padding:20px;">'
        + logo_html
        + '<h1 style="display:inline;font-size:22px;vertical-align:middle;">%s</h1>'
        % title
        + subtitle_tag
        + '<div style="font-size:14px;color:var(--muted);margin-top:8px;">%s, %s</div>'
        % (dow, date_str)
        + _SUB_NAV
        + day_selector
        + '</div>'
    )


def _render_actual_vs_predicted(m):
    """Banner shown for past-date views: actual revenue vs what the model predicted."""
    actual = m.get("actual")
    if not actual:
        return ""

    pred_total = m.get("prediction", {}).get("revenue_total", 0)
    act_total = actual.get("total", 0)
    variance = m.get("variance") or {}
    v_amount = variance.get("amount", 0)
    v_pct = variance.get("pct", 0)
    v_color = _GREEN if v_amount >= 0 else _RED

    pred_by_ch = m.get("prediction", {}).get("revenue_by_channel", {})
    act_in = actual.get("instore", 0)
    act_del = actual.get("delivery", 0)
    act_cat = actual.get("catering", 0)

    channels = [
        ("In-Store", pred_by_ch.get("instore", 0), act_in),
        ("Delivery", pred_by_ch.get("delivery", 0), act_del),
        ("Catering", pred_by_ch.get("catering", 0), act_cat),
    ]

    ch_rows = []
    for name, pred_v, act_v in channels:
        ch_v = act_v - pred_v
        ch_color = _GREEN if ch_v >= 0 else _RED
        ch_rows.append(
            '<div style="display:flex;justify-content:space-between;align-items:center;'
            'padding:6px 0;border-bottom:1px solid var(--border);font-size:13px;">'
            '<span style="color:var(--muted);">%s</span>'
            '<span>Pred: <strong>%s</strong></span>'
            '<span>Actual: <strong>%s</strong></span>'
            '<span style="color:%s;font-weight:600;">%s%s</span>'
            '</div>'
            % (name, fmt_currency(pred_v), fmt_currency(act_v),
               ch_color, "+" if ch_v >= 0 else "", fmt_currency(ch_v))
        )

    content = (
        '<div style="display:flex;gap:20px;align-items:center;'
        'padding:14px 16px;background:rgba(74,156,216,0.08);border-radius:10px;'
        'border:1px solid rgba(74,156,216,0.2);margin-bottom:4px;flex-wrap:wrap;">'
        '<div style="flex:1;min-width:140px;">'
        '<div style="font-size:11px;color:var(--muted);text-transform:uppercase;'
        'letter-spacing:0.5px;">Model Predicted</div>'
        '<div style="font-size:26px;font-weight:700;color:#4a9cd8;">%s</div>'
        '</div>'
        '<div style="font-size:28px;color:var(--muted);">→</div>'
        '<div style="flex:1;min-width:140px;">'
        '<div style="font-size:11px;color:var(--muted);text-transform:uppercase;'
        'letter-spacing:0.5px;">Actual Revenue</div>'
        '<div style="font-size:26px;font-weight:700;color:var(--text);">%s</div>'
        '</div>'
        '<div style="flex:1;min-width:120px;text-align:right;">'
        '<div style="font-size:11px;color:var(--muted);text-transform:uppercase;'
        'letter-spacing:0.5px;">Variance</div>'
        '<div style="font-size:24px;font-weight:700;color:%s;">%s%s</div>'
        '<div style="font-size:12px;color:%s;">%.1f%%</div>'
        '</div>'
        '</div>'
        '<div style="margin-top:10px;">%s</div>'
    ) % (
        fmt_currency(pred_total), fmt_currency(act_total),
        v_color, "+" if v_amount >= 0 else "", fmt_currency(v_amount),
        v_color, v_pct,
        "".join(ch_rows),
    )

    return render_card("Actual vs Predicted", content,
                       subtitle="How the model performed on this day")


def _render_weather(m):
    w = m.get("weather")
    if not w:
        return render_card("Weather", '<p style="color:var(--muted);">Weather data unavailable</p>')

    temp_high = w.get("temp_high")
    temp_low = w.get("temp_low")
    conditions = w.get("conditions", "Unknown")
    rain = w.get("rain_inches", 0)
    snow = w.get("snow_inches", 0)
    wind = w.get("wind_max_mph", 0)
    sunrise = w.get("sunrise", "")
    sunset = w.get("sunset", "")
    mult = w.get("multiplier", 1.0)
    reasons = w.get("multiplier_reasons", [])

    # Weather icon based on conditions
    code = w.get("weather_code", 0) or 0
    if code >= 71:
        icon = "&#10052;"  # snowflake
    elif code >= 51:
        icon = "&#127783;"  # rain cloud
    elif code >= 45:
        icon = "&#127787;"  # fog
    elif code >= 2:
        icon = "&#9925;"  # partly cloudy
    else:
        icon = "&#9728;"  # sun

    temp_str = ""
    if temp_high is not None and temp_low is not None:
        temp_str = "%d&deg;F" % int(temp_high)

    details = []
    if temp_low is not None:
        details.append("Low: %d&deg;F" % int(temp_low))
    if rain > 0:
        details.append("Rain: %.2f in" % rain)
    if snow > 0:
        details.append("Snow: %.1f in" % snow)
    if wind:
        details.append("Wind: %d mph" % int(wind))
    if sunrise:
        details.append("Sunrise: %s" % sunrise)
    if sunset:
        details.append("Sunset: %s" % sunset)

    # Multiplier badge
    mult_html = ""
    if mult < 1.0:
        reason_str = "; ".join(reasons) if reasons else ""
        mult_html = (
            '<div class="weather-multiplier negative">'
            'Revenue impact: %+.0f%%<br>'
            '<span style="font-size:11px;font-weight:400;">%s</span>'
            '</div>' % ((mult - 1) * 100, _safe(reason_str))
        )
    else:
        mult_html = '<div class="weather-multiplier neutral">No weather impact</div>'

    details_html = " &middot; ".join(details)

    return (
        '<div class="weather-card">'
        f'<div class="weather-icon">{icon}</div>'
        '<div class="weather-main">'
        f'<div class="weather-temp">{temp_str}</div>'
        f'<div class="weather-conditions">{_safe(conditions)}</div>'
        f'<div class="weather-details">{details_html}</div>'
        '</div>'
        f'{mult_html}'
        '</div>'
    )


def _render_kpis(m):
    pred = m.get("prediction", {})
    rev = pred.get("revenue_total", 0)
    pl = pred.get("daily_pl", {})
    net = pl.get("net_income", 0)
    staffing = m.get("staffing_hint", {})
    covers = staffing.get("expected_covers", 0)
    peaks = m.get("peaks", {})
    peak_hour = peaks.get("peak_hour", {})
    peak_label = peak_hour.get("hour", "N/A")

    explanation = pred.get("explanation", {})
    confidence = explanation.get("confidence", "medium")
    conf_class = "confidence-%s" % confidence

    stats = [
        render_stat("Predicted Revenue", fmt_currency(rev),
                    subtitle='<span class="%s">%s confidence</span>' % (conf_class, confidence)),
        render_stat("Predicted Net Income", fmt_currency(net),
                    color=_GREEN if net > 0 else _RED),
        render_stat("Expected Covers", fmt_num(covers)),
        render_stat("Peak Hour", str(peak_label),
                    subtitle=fmt_currency(peak_hour.get("expected_revenue", 0))),
    ]

    main_grid = render_stat_grid(stats)

    # Per-channel breakdown row
    by_ch = pred.get("revenue_by_channel", {})
    if by_ch:
        ch_stats = [
            render_stat("In-Store", fmt_currency(by_ch.get("instore", 0)),
                        subtitle=_channel_effect_subtitle(explanation, "instore")),
            render_stat("Delivery", fmt_currency(by_ch.get("delivery", 0)),
                        subtitle=_channel_effect_subtitle(explanation, "delivery")),
            render_stat("Catering", fmt_currency(by_ch.get("catering", 0)),
                        subtitle=_channel_effect_subtitle(explanation, "catering")),
        ]
        main_grid += render_stat_grid(ch_stats)

    return main_grid


def _channel_effect_subtitle(explanation, channel):
    """Build subtitle string showing DOW + seasonal effect for a channel."""
    ch_fx = explanation.get("channel_effects", {}).get(channel, {})
    if not ch_fx:
        return ""
    dow = ch_fx.get("dow_effect", "")
    seas = ch_fx.get("seasonal_effect", "")
    if dow == "N/A":
        return "Trailing avg"
    return "DOW %s | Seasonal %s" % (dow, seas)


def _render_explanation(m):
    pred = m.get("prediction", {})
    explanation = pred.get("explanation", {})
    steps = explanation.get("steps", [])
    narrative = explanation.get("narrative", "")

    if not steps:
        return ""

    rows_html = []
    for step in steps:
        label = _safe(step.get("label", ""))
        detail = _safe(step.get("detail", ""))
        value = step.get("value")
        delta = step.get("delta")
        delta_pct = step.get("delta_pct")

        is_final = step.get("label", "").startswith("Final")
        css_class = "explain-step final" if is_final else "explain-step"

        # Left side
        left = f'<div class="step-label">{label}</div>'
        if detail:
            left += f'<div class="step-detail">{detail}</div>'

        # Right side
        right_parts = []
        if value is not None:
            right_parts.append(fmt_currency(value))
        if delta is not None:
            sign = "positive" if delta >= 0 else "negative"
            right_parts.append(
                '<span class="step-delta %s">%s%s</span>'
                % (sign, "+" if delta >= 0 else "", fmt_currency(delta))
            )
        if delta_pct is not None and value is None and delta is None:
            sign = "positive" if delta_pct >= 0 else "negative"
            right_parts.append(
                '<span class="step-delta %s">%+.1f%%</span>' % (sign, delta_pct)
            )

        right = " ".join(right_parts) if right_parts else ""

        rows_html.append(
            f'<div class="{css_class}">'
            f'<div>{left}</div>'
            f'<div class="step-value">{right}</div>'
            '</div>'
        )

    content = '<div class="explain-steps">' + "\n".join(rows_html) + '</div>'

    # Per-channel effects summary
    ch_effects = explanation.get("channel_effects", {})
    if ch_effects:
        ch_html = '<div style="margin-top:14px;display:grid;grid-template-columns:repeat(3,1fr);gap:8px;">'
        for ch in ("instore", "delivery", "catering"):
            fx = ch_effects.get(ch, {})
            if not fx:
                continue
            ch_label = fx.get("label", ch.title())
            ch_rev = fx.get("revenue", 0)
            dow_str = fx.get("dow_effect", "N/A")
            seas_str = fx.get("seasonal_effect", "N/A")

            ch_html += (
                '<div style="padding:10px;background:var(--bg);border-radius:6px;text-align:center;">'
                '<div style="font-size:11px;color:var(--muted);font-weight:600;text-transform:uppercase;">%s</div>'
                '<div style="font-size:18px;font-weight:700;margin:4px 0;">%s</div>'
                '<div style="font-size:11px;color:var(--muted);">'
                % (_safe(ch_label), fmt_currency(ch_rev))
            )
            if dow_str != "N/A":
                ch_html += 'DOW %s &middot; Seasonal %s' % (_safe(dow_str), _safe(seas_str))
            else:
                ch_html += 'Trailing avg'
            ch_html += '</div></div>'
        ch_html += '</div>'
        content += ch_html

    if narrative:
        content += f'<div class="explain-narrative">{_safe(narrative)}</div>'

    return render_card("Prediction Breakdown", content,
                       subtitle="Step-by-step forecast reasoning")


def _render_hourly_chart(m):
    hourly = m.get("hourly_curve", [])
    if not hourly:
        return render_card("Hourly Distribution",
                           '<p style="color:var(--muted);">No hourly data available (need 4-week history)</p>')

    weeks = m.get("quarter_hourly_4wra_weeks", 0)
    labels = [h["label"] for h in hourly]
    values = [h["expected_revenue"] for h in hourly]

    chart = render_chartjs_bar(
        labels=labels,
        datasets=[{
            "label": "Expected Revenue",
            "data": values,
            "backgroundColor": _GREEN,
            "borderRadius": 4,
        }],
        height=300,
        dollar=True,
    )

    return render_card("Hourly Revenue Distribution", chart,
                       subtitle="Based on %d-week same-day rolling average" % weeks)


def _render_peaks(m):
    peaks = m.get("peaks", {})
    peak_hour = peaks.get("peak_hour", {})
    peak_15 = peaks.get("peak_15min", {})
    lunch_pct = peaks.get("lunch_pct", 0)
    dinner_pct = peaks.get("dinner_pct", 0)

    stats = [
        render_stat("Peak Hour", str(peak_hour.get("hour", "N/A")),
                    subtitle=fmt_currency(peak_hour.get("expected_revenue", 0))),
        render_stat("Peak 15-min", str(peak_15.get("slot", "N/A")),
                    subtitle=fmt_currency(peak_15.get("expected_revenue", 0))),
        render_stat("Lunch Share", fmt_pct(lunch_pct)),
        render_stat("Dinner Share", fmt_pct(dinner_pct)),
    ]

    return render_card("Peak Analysis", render_stat_grid(stats))


def _render_dayparts(m):
    dayparts = m.get("dayparts", [])
    if not dayparts:
        return ""

    headers = ["Daypart", "Hours", "Revenue", "Orders", "% of Day"]
    rows = []
    for dp in dayparts:
        rows.append([
            dp["name"],
            "%s - %s" % (dp["start"], dp["end"]),
            fmt_currency(dp["expected_revenue"]),
            fmt_num(dp["expected_orders"], decimals=0),
            fmt_pct(dp["pct_of_day"]),
        ])

    return render_card("Daypart Breakdown",
                       render_table(headers, rows, right_align_cols=[2, 3, 4]))


def _render_catering(m):
    orders = m.get("scheduled_catering", [])
    if not orders:
        return render_card("Scheduled Catering",
                           '<p style="color:var(--muted);">No catering orders scheduled for today</p>')

    total = sum(o.get("subtotal", 0) for o in orders)

    headers = ["Order", "Platform", "Subtotal"]
    rows = []
    for o in orders:
        rows.append([
            _safe(o.get("name", "")),
            _safe(o.get("platform", "")),
            fmt_currency(o.get("subtotal", 0)),
        ])

    content = render_table(headers, rows, right_align_cols=[2])
    content += (
        '<div style="text-align:right;font-weight:600;margin-top:8px;">'
        'Total: %s</div>' % fmt_currency(total)
    )

    return render_card("Scheduled Catering", content,
                       subtitle="%d confirmed order(s)" % len(orders))


def _build_week_revenue_chart(days, height=280):
    """Build a bar chart for a week's daily revenue.

    Actuals show as solid green, forecast days as lighter green.
    Today gets a distinct border. Financial-style visual distinction.
    """
    labels = []
    actual_vals = []
    forecast_vals = []

    for d in days:
        labels.append(d.get("dow_name", ""))
        rev = d.get("revenue", 0)
        is_actual = d.get("is_actual", False)
        is_today = d.get("is_today", False)

        if is_actual:
            actual_vals.append(round(rev))
            forecast_vals.append(0)
        else:
            actual_vals.append(0)
            forecast_vals.append(round(rev))

    # Average line
    total = sum(actual_vals) + sum(forecast_vals)
    avg = total / len(labels) if labels else 0

    datasets = [
        {
            "label": "Actual",
            "data": actual_vals,
            "backgroundColor": _GREEN,
            "borderRadius": 4,
        },
        {
            "label": "Forecast",
            "data": forecast_vals,
            "backgroundColor": _GREEN_LIGHT,
            "borderColor": _GREEN,
            "borderWidth": 1,
            "borderRadius": 4,
        },
    ]

    return render_chartjs_bar(
        labels=labels,
        datasets=datasets,
        height=height,
        dollar=True,
        show_legend=True,
        stacked=True,
        annotation_lines=[{"value": round(avg), "label": "Daily avg", "color": _GRAY}],
    )


def _render_channel_doughnut(m):
    """Render a channel mix doughnut chart for today's prediction."""
    pred = m.get("prediction", {})
    by_ch = pred.get("revenue_by_channel", {})
    if not by_ch:
        return ""

    ch_in = by_ch.get("instore", 0)
    ch_del = by_ch.get("delivery", 0)
    ch_cat = by_ch.get("catering", 0)
    total = ch_in + ch_del + ch_cat
    if total <= 0:
        return ""

    chart = render_chartjs_pie(
        labels=["In-Store", "Delivery", "Catering"],
        values=[round(ch_in), round(ch_del), round(ch_cat)],
        colors=[_GREEN, _BLUE, _ORANGE],
        height=200,
        doughnut=True,
        center_text=fmt_currency(total),
    )

    return render_card("Channel Mix", chart,
                       subtitle="Predicted revenue by channel")


def _render_this_week(m):
    week = m.get("this_week", {})
    days = week.get("days", [])
    if not days:
        return ""

    week_label = week.get("week_label", "")
    total_est = week.get("week_total_estimate", 0)
    total_actual = week.get("total_actual", 0)
    total_forecast = week.get("total_forecast", 0)

    # ── Bar chart: actual (solid) vs forecast (light) ──
    chart_html = _build_week_revenue_chart(days)

    day_cards = []
    for d in days:
        is_today = d.get("is_today", False)
        is_actual = d.get("is_actual", False)

        if is_today:
            css = "week-day today"
        elif is_actual:
            css = "week-day actual"
        else:
            css = "week-day forecast"

        tag = ""
        if is_today:
            tag = '<div style="font-size:9px;color:#8cb82e;font-weight:600;">TODAY</div>'
        elif not is_actual:
            tag = '<div class="wd-label">forecast</div>'

        # Per-channel mini breakdown
        ch_line = ""
        ch_in = d.get("channel_instore", 0)
        ch_del = d.get("channel_delivery", 0)
        ch_cat = d.get("channel_catering", 0)
        if ch_in > 0 or ch_del > 0:
            ch_line = (
                '<div style="font-size:9px;color:var(--muted);margin-top:3px;">'
                'In %s | Del %s'
                % (fmt_currency(ch_in), fmt_currency(ch_del))
            )
            if ch_cat > 0:
                ch_line += ' | Cat %s' % fmt_currency(ch_cat)
            ch_line += '</div>'

        day_cards.append(
            f'<div class="{css}">'
            f'<div class="wd-name">{_safe(d["dow_name"])}</div>'
            f'<div class="wd-rev">{fmt_currency(d["revenue"])}</div>'
            f'{ch_line}'
            f'{tag}'
            '</div>'
        )

    content = chart_html
    content += '<div class="week-row">' + "\n".join(day_cards) + '</div>'

    content += (
        '<div style="display:flex;justify-content:space-between;font-size:13px;'
        'padding:8px 4px;border-top:1px solid var(--border);">'
        '<span>Actual so far: <strong>%s</strong></span>'
        '<span>Remaining forecast: <strong>%s</strong></span>'
        '<span>Week estimate: <strong>%s</strong></span>'
        '</div>' % (fmt_currency(total_actual), fmt_currency(total_forecast),
                    fmt_currency(total_est))
    )

    return render_card("This Week", content, subtitle=week_label)


def _render_discount_health(m):
    pred = m.get("prediction", {})
    disc = pred.get("discount_metrics", {})
    if not disc:
        return ""

    rate_now = disc.get("delivery_discount_rate_current", 0)
    rate_3mo = disc.get("delivery_discount_rate_3mo_ago", 0)
    trend = disc.get("delivery_discount_trend", "stable")
    bogo = disc.get("bogo_annualized", 0)
    daily_disc = disc.get("daily_delivery_discount", 0)

    if rate_now < 0.5 and bogo < 1000:
        return ""  # Not significant enough to show

    trend_color = _RED if trend == "rising" else (_GREEN if trend == "falling" else _ORANGE)
    trend_label = trend.title()

    stats = [
        render_stat("Delivery Discount Rate", "%.1f%%" % rate_now,
                    subtitle="vs %.1f%% 3 months ago" % rate_3mo,
                    color=trend_color),
        render_stat("Trend", trend_label, color=trend_color),
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


def _render_daily_pl(m):
    pred = m.get("prediction", {})
    pl = pred.get("daily_pl", {})
    if not pl:
        return ""

    # P&L row definitions: (label, key, is_cost, is_subtotal, is_section_header, is_indent)
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

    pl_rows.extend([
        ("Total Operating Expenses", "total_opex", True, True, False, False),
        ("Operating Income", "operating_income", False, True, False, False),
        ("Other Income", "other_income", False, False, False, False),
        ("Net Income", "net_income", False, True, False, False),
    ])

    rev_total = pl.get("revenue_total", 0)

    rows_html = []
    for label, key, is_cost, is_subtotal, is_section_header, is_indent in pl_rows:
        if is_section_header:
            rows_html.append(
                f'<tr class="section-header"><td colspan="3">{_safe(label)}</td></tr>'
            )
            continue

        val = pl.get(key, 0)
        pct_val = (abs(val) / rev_total * 100) if rev_total and key != "revenue_total" else ""

        css_class = ""
        if is_subtotal:
            css_class = "subtotal-row"
        elif is_indent:
            css_class = "indent"

        val_str = fmt_currency(abs(val) if is_cost else val)
        pct_str = "%.1f%%" % pct_val if isinstance(pct_val, float) else ""

        # Color net income / operating income
        color_style = ""
        if key in ("net_income", "operating_income", "gross_profit"):
            color_style = ' style="color:%s"' % (_GREEN if val >= 0 else _RED)

        rows_html.append(
            f'<tr class="{css_class}">'
            f'<td>{_safe(label)}</td>'
            f'<td{color_style}>{val_str}</td>'
            f'<td style="color:var(--muted);font-size:11px;">{pct_str}</td>'
            '</tr>'
        )

    table_html = (
        '<table class="pl-mini">'
        '<thead><tr><th style="text-align:left;">Line Item</th>'
        '<th>Amount</th><th>% Rev</th></tr></thead>'
        '<tbody>' + "\n".join(rows_html) + '</tbody></table>'
    )

    return render_card("Daily P&L Projection", table_html,
                       subtitle="Based on trailing 6-month expense ratios")


def _render_day_selector(m):
    """Render scrollable week day selector strip.

    Shows last week (for accuracy review) and this week (for navigation).
    Each day pill links to /today/YYYYMMDD. The current date is highlighted.
    """
    current_date = m.get("date", "")  # "YYYY-MM-DD"
    this_week = m.get("this_week", {})
    last_week = m.get("last_week_accuracy", {})

    tw_days = this_week.get("days", [])
    lw_days = last_week.get("days", [])
    tw_label = this_week.get("week_label", "")
    lw_label = last_week.get("week_label", "")

    def _pill(d, current_date):
        date_str = d.get("date", "")
        date_slug = date_str.replace("-", "")
        dow = d.get("dow_name", "")
        is_selected = date_str == current_date
        is_actual = d.get("is_actual", False)
        is_today_flag = d.get("is_today", False)

        # Revenue: actual for past days, forecast for future
        rev = d.get("revenue") or d.get("actual") or 0
        rev_str = ("$%s" % "{:,.0f}".format(rev)) if rev else "—"

        # For last week days that have accuracy data
        variance = d.get("variance")
        if variance is not None:
            v_color = _GREEN if variance >= 0 else _RED
            v_str = ("%s%s" % ("+" if variance >= 0 else "", "${:,.0f}".format(abs(variance))))
            var_html = (
                '<div style="font-size:8px;font-weight:600;color:%s;margin-top:1px;">%s</div>'
                % (v_color, v_str)
            )
        else:
            var_html = ""

        # Badge
        if is_today_flag and not is_selected:
            badge = '<div style="font-size:8px;color:#8cb82e;font-weight:700;margin-top:2px;">TODAY</div>'
        elif is_selected:
            badge = ""
        elif not is_actual and not is_today_flag:
            badge = '<div style="font-size:8px;color:var(--muted);margin-top:2px;">fcst</div>'
        else:
            badge = ""

        # Styling
        if is_selected:
            style = ("background:#8cb82e;color:#fff;border:1.5px solid #8cb82e;"
                     "box-shadow:0 2px 8px rgba(140,184,46,0.35);")
        elif is_today_flag:
            style = "background:rgba(140,184,46,0.1);color:var(--text);border:1.5px solid #8cb82e;"
        elif is_actual:
            style = "background:var(--surface);color:var(--text);border:1px solid var(--border);"
        else:
            style = "background:var(--bg);color:var(--muted);border:1px dashed var(--border);"

        # Short date like "3/3"
        parts = date_str.split("-")
        short_date = "%d/%d" % (int(parts[1]), int(parts[2])) if len(parts) == 3 else date_str

        return (
            '<a href="/today/%s" style="text-decoration:none;flex-shrink:0;">'
            '<div style="display:flex;flex-direction:column;align-items:center;'
            'padding:7px 10px;border-radius:9px;min-width:52px;cursor:pointer;'
            'transition:all 0.15s ease;%s">'
            '<div style="font-size:11px;font-weight:700;">%s</div>'
            '<div style="font-size:9px;opacity:0.75;margin-top:1px;">%s</div>'
            '<div style="font-size:11px;font-weight:600;margin-top:3px;">%s</div>'
            '%s%s'
            '</div></a>'
        ) % (date_slug, style, dow, short_date, rev_str, var_html, badge)

    lw_pills = "".join(_pill(d, current_date) for d in lw_days) if lw_days else ""
    tw_pills = "".join(_pill(d, current_date) for d in tw_days) if tw_days else ""

    html = '<div style="margin-top:14px;text-align:left;">'

    if lw_pills:
        html += (
            '<div style="font-size:10px;color:var(--muted);font-weight:600;'
            'text-transform:uppercase;letter-spacing:0.6px;margin-bottom:5px;">'
            'Last Week &middot; %s</div>'
            '<div style="display:flex;gap:5px;overflow-x:auto;padding-bottom:4px;'
            'scrollbar-width:thin;">%s</div>'
        ) % (lw_label, lw_pills)

    if tw_pills:
        html += (
            '<div style="font-size:10px;color:var(--muted);font-weight:600;'
            'text-transform:uppercase;letter-spacing:0.6px;margin:10px 0 5px;">'
            'This Week &middot; %s</div>'
            '<div style="display:flex;gap:5px;overflow-x:auto;padding-bottom:4px;'
            'scrollbar-width:thin;">%s</div>'
        ) % (tw_label, tw_pills)

    html += '</div>'
    return html


def _render_last_week_accuracy(m):
    """Render the Last Week Prediction Accuracy card.

    Shows a bar chart (predicted vs actual) and a variance table
    for all days of last week that have actual data.
    """
    acc = m.get("last_week_accuracy", {})
    days = acc.get("days", [])
    actual_days = [d for d in days if d.get("has_actual")]
    if not actual_days:
        return ""

    week_label = acc.get("week_label", "Last Week")

    # ── Summary stats ──
    total_projected = sum(d["projected"] for d in actual_days)
    total_actual = sum(d["actual"] for d in actual_days)
    total_variance = total_actual - total_projected
    total_variance_pct = (total_variance / total_projected * 100) if total_projected > 0 else 0
    mae = sum(abs(d.get("variance") or 0) for d in actual_days) / len(actual_days)
    over_days = sum(1 for d in actual_days if (d.get("variance") or 0) > 0)
    under_days = len(actual_days) - over_days

    tv_color = _GREEN if total_variance >= 0 else _RED

    stats_html = (
        '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px;">'
        + render_stat("Total Predicted", fmt_currency(total_projected))
        + render_stat("Total Actual", fmt_currency(total_actual))
        + render_stat(
            "Net Variance",
            '<span style="color:%s;font-weight:700;">%s%s (%.1f%%)</span>'
            % (tv_color, "+" if total_variance >= 0 else "", fmt_currency(total_variance),
               total_variance_pct),
        )
        + render_stat("Avg Daily Error", fmt_currency(mae))
        + render_stat("Over/Under", '%d over · %d under' % (over_days, under_days))
        + '</div>'
    )

    # ── Bar chart ──
    labels = [d["dow_name"] for d in actual_days]
    proj_vals = [d["projected"] for d in actual_days]
    act_vals = [d["actual"] for d in actual_days]

    chart_html = render_chartjs_bar(
        labels=labels,
        datasets=[
            {"label": "Predicted", "data": proj_vals, "color": _BLUE_LIGHT,
             "borderColor": _BLUE, "borderWidth": 2},
            {"label": "Actual", "data": act_vals, "color": _GREEN_LIGHT,
             "borderColor": _GREEN, "borderWidth": 2},
        ],
        height=200,
        dollar=True,
        show_legend=True,
    )

    # ── Table ──
    rows = []
    for d in actual_days:
        v = d.get("variance") or 0
        vp = d.get("variance_pct") or 0
        v_color = _GREEN if v >= 0 else _RED
        v_cell = (
            '<span style="color:%s;font-weight:600;">%s%s (%.1f%%)</span>'
            % (v_color, "+" if v >= 0 else "", fmt_currency(v), vp)
        )
        acc_pct = 100 - abs(vp)
        acc_cell = (
            '<span style="color:%s;">%.0f%%</span>'
            % (_GREEN if acc_pct >= 90 else _ORANGE if acc_pct >= 75 else _RED, acc_pct)
        )
        note = d.get("interpretation") or ""
        note_cell = (
            '<span style="font-size:11px;color:var(--muted);font-style:italic;">%s</span>'
            % _safe(note)
            if note else ""
        )
        rows.append([
            '<a href="/today/%s" style="color:var(--primary);text-decoration:none;'
            'font-weight:500;">%s</a>' % (d["date_slug"], d["label"]),
            fmt_currency(d["projected"]),
            fmt_currency(d["actual"]),
            v_cell,
            acc_cell,
            note_cell,
        ])

    table_html = render_table(
        headers=["Day", "Predicted", "Actual", "Variance", "Accuracy", "Notes"],
        rows=rows,
    )

    content = stats_html + chart_html + table_html
    return render_card("Last Week: Prediction Accuracy", content, subtitle=week_label)


def _render_footer():
    from datetime import datetime
    now = datetime.now().strftime("%b %d, %Y %I:%M %p")
    return (
        '<div style="text-align:center;padding:16px;font-size:11px;color:var(--muted);">'
        f'Generated {now}'
        '</div>'
    )


# ══════════════════════════════════════════════════════════════════════════════
# Weekly View Page
# ══════════════════════════════════════════════════════════════════════════════

_WEEK_SUB_NAV = (
    '<div style="display:flex;justify-content:center;gap:8px;margin-top:12px;flex-wrap:wrap;">'
    '<a href="/" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Home</a>'
    '<a href="/today" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Today</a>'
    '<a href="/week" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:#8cb82e;color:#fff;text-decoration:none;font-weight:600;">This Week</a>'
    '<a href="/forecast" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">P&amp;L Forecast</a>'
    '<a href="/backtest" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Backtest</a>'
    '<a href="/schedule" style="font-size:12px;padding:5px 12px;border-radius:6px;'
    'background:var(--bg);color:var(--text);text-decoration:none;font-weight:500;">Schedule</a>'
    '</div>'
)


def build_week_page(metrics: dict, logo_b64: str = "") -> str:
    """Build the complete This Week HTML page."""
    _reset_chart_counter()

    sections = []
    sections.append(_week_header(metrics, logo_b64))
    sections.append(_week_pacing(metrics))
    sections.append(_week_revenue_chart(metrics))
    sections.append(_week_days_table(metrics, "this_week", "This Week"))
    sections.append(_week_next_summary(metrics))
    sections.append(_week_next_chart(metrics))
    sections.append(_week_days_table(metrics, "next_week", "Next Week"))
    sections.append(_week_channel_chart(metrics))
    sections.append(_week_days_table(metrics, "last_week", "Last Week"))
    sections.append(_week_pl(metrics))
    sections.append(_render_footer())

    body = "\n\n".join(s for s in sections if s)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Livite - This Week</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
{_CSS}
{_EXTRA_CSS}
{_WEEK_EXTRA_CSS}
</style>
</head>
<body>

{body}

{_LOADING_SNIPPET}
</body>
</html>"""


_WEEK_EXTRA_CSS = """
.week-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}
.week-table th, .week-table td {
    padding: 10px 8px;
    text-align: right;
    border-bottom: 1px solid var(--border);
}
.week-table th:first-child, .week-table td:first-child {
    text-align: left;
    font-weight: 600;
}
.week-table thead th {
    font-size: 11px;
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.3px;
}
.week-table .today-row {
    background: rgba(140,184,46,0.08);
    font-weight: 600;
}
.week-table .forecast-row {
    opacity: 0.7;
    font-style: italic;
}
.week-table .total-row td {
    font-weight: 700;
    border-top: 2px solid var(--text);
    padding-top: 12px;
}
.pacing-card {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 16px;
    margin-bottom: 8px;
}
@media (max-width: 600px) {
    .pacing-card { grid-template-columns: 1fr; }
    .week-table { font-size: 12px; }
    .week-table th, .week-table td { padding: 8px 4px; }
}
"""


def _week_header(m, logo_b64):
    logo_html = ""
    if logo_b64:
        logo_html = (
            '<img src="data:image/png;base64,%s" alt="Livite" '
            'style="height:32px;margin-right:12px;vertical-align:middle;">' % logo_b64
        )

    tw = m.get("this_week", {})
    week_label = tw.get("week_label", "")

    return (
        '<div class="card" style="text-align:center;padding:20px;">'
        f'{logo_html}'
        '<h1 style="display:inline;font-size:22px;vertical-align:middle;">'
        'This Week</h1>'
        '<div style="font-size:14px;color:var(--muted);margin-top:8px;">'
        '%s</div>' % _safe(week_label)
        + _WEEK_SUB_NAV
        + '</div>'
    )


def _week_pacing(m):
    comp = m.get("comparison", {})
    if not comp:
        return ""

    tw_actual = comp.get("this_week_actual_so_far", 0)
    lw_same = comp.get("last_week_thru_same_day", 0)
    lw_total = comp.get("last_week_total", 0)
    pacing = comp.get("pacing_pct", 0)
    tw_est = comp.get("this_week_estimate", 0)

    pacing_color = _GREEN if pacing >= 0 else _RED
    pacing_label = "%+.1f%%" % pacing

    # Week-over-week comparison
    wow_pct = 0.0
    if lw_total > 0:
        wow_pct = (tw_est - lw_total) / lw_total * 100
    wow_color = _GREEN if wow_pct >= 0 else _RED

    stats = [
        render_stat("Actual So Far", fmt_currency(tw_actual),
                    subtitle="vs %s last week same point" % fmt_currency(lw_same)),
        render_stat("Pacing", pacing_label,
                    subtitle="vs last week thru same day",
                    color=pacing_color),
        render_stat("Week Estimate", fmt_currency(tw_est),
                    subtitle="WoW: %+.1f%% vs %s" % (wow_pct, fmt_currency(lw_total)),
                    color=wow_color),
    ]

    return render_card("Weekly Pacing", render_stat_grid(stats),
                       subtitle="How this week compares to last week")


def _week_revenue_chart(m):
    """This Week daily revenue chart — actuals solid, forecasts light."""
    tw = m.get("this_week", {})
    days = tw.get("days", [])
    if not days:
        return ""

    chart = _build_week_revenue_chart(days)
    return render_card("This Week Revenue", chart,
                       subtitle="Solid = actual, light = forecast")


def _week_next_chart(m):
    """Next Week forecast chart — all forecast bars in lighter color."""
    nw = m.get("next_week", {})
    days = nw.get("days", [])
    if not days:
        return ""

    labels = []
    values = []
    bar_colors = []

    # Use this week daily avg as annotation reference
    tw = m.get("this_week", {})
    tw_total = tw.get("week_total", 0)
    tw_avg = tw_total / 7.0 if tw_total else 0

    for d in days:
        labels.append(d.get("dow_name", ""))
        rev = d.get("revenue", 0)
        values.append(round(rev))

        # Weather-impacted days get a different shade
        w = d.get("weather")
        wmult = w.get("multiplier", 1.0) if w else 1.0
        if wmult < 0.8:
            bar_colors.append("rgba(232,96,64,0.45)")  # red-ish for heavy weather
        elif wmult < 0.95:
            bar_colors.append("rgba(232,168,48,0.45)")  # amber for mild weather
        else:
            bar_colors.append(_BLUE_LIGHT)

    datasets = [
        {
            "label": "Forecast",
            "data": values,
            "backgroundColor": bar_colors,
            "borderColor": _BLUE,
            "borderWidth": 1,
            "borderRadius": 4,
        },
    ]

    chart = render_chartjs_bar(
        labels=labels,
        datasets=datasets,
        height=280,
        dollar=True,
        annotation_lines=[
            {"value": round(tw_avg), "label": "This wk avg", "color": _GRAY},
        ],
    )

    return render_card("Next Week Forecast", chart,
                       subtitle="Red-tinted = weather impact | Dashed line = this week avg")


def _week_channel_chart(m):
    """Two-week stacked channel chart — 14 days, actual vs forecast."""
    tw = m.get("this_week", {})
    nw = m.get("next_week", {})
    tw_days = tw.get("days", [])
    nw_days = nw.get("days", [])
    if not tw_days:
        return ""

    all_days = tw_days + nw_days
    labels = []
    in_actual = []
    in_fcst = []
    del_actual = []
    del_fcst = []
    cat_actual = []
    cat_fcst = []

    for d in all_days:
        labels.append(d.get("dow_name", ""))
        is_actual = d.get("is_actual", False)
        ch_in = d.get("channel_instore", 0)
        ch_del = d.get("channel_delivery", 0)
        ch_cat = d.get("channel_catering", 0)

        if is_actual:
            in_actual.append(round(ch_in))
            del_actual.append(round(ch_del))
            cat_actual.append(round(ch_cat))
            in_fcst.append(0)
            del_fcst.append(0)
            cat_fcst.append(0)
        else:
            in_actual.append(0)
            del_actual.append(0)
            cat_actual.append(0)
            in_fcst.append(round(ch_in))
            del_fcst.append(round(ch_del))
            cat_fcst.append(round(ch_cat))

    datasets = [
        {"label": "In-Store", "data": in_actual, "backgroundColor": _GREEN, "borderRadius": 2},
        {"label": "Delivery", "data": del_actual, "backgroundColor": _BLUE, "borderRadius": 2},
        {"label": "Catering", "data": cat_actual, "backgroundColor": _ORANGE, "borderRadius": 2},
        {"label": "In-Store (fcst)", "data": in_fcst, "backgroundColor": _GREEN_LIGHT, "borderRadius": 2},
        {"label": "Delivery (fcst)", "data": del_fcst, "backgroundColor": _BLUE_LIGHT, "borderRadius": 2},
        {"label": "Catering (fcst)", "data": cat_fcst, "backgroundColor": _ORANGE_LIGHT, "borderRadius": 2},
    ]

    chart = render_chartjs_bar(
        labels=labels,
        datasets=datasets,
        height=320,
        dollar=True,
        stacked=True,
        show_legend=True,
    )

    # Add a note about the week boundary
    tw_label = tw.get("week_label", "")
    nw_label = nw.get("week_label", "")
    note = ""
    if nw_label:
        note = (
            '<div style="font-size:11px;color:var(--muted);margin-top:6px;text-align:center;">'
            '%s (bars 1-7) | %s (bars 8-14) &mdash; '
            'Solid = actual, light = forecast'
            '</div>' % (_safe(tw_label), _safe(nw_label))
        )

    return render_card("Channel Breakdown: This + Next Week", chart + note,
                       subtitle="Stacked by channel, 14-day view")


def _week_next_summary(m):
    """Render a summary card for next week's forecast."""
    nw_comp = m.get("next_week_comparison", {})
    nw_pl = m.get("next_week_pl", {})
    nw = m.get("next_week", {})
    if not nw_comp or not nw.get("days"):
        return ""

    nw_est = nw_comp.get("next_week_estimate", 0)
    tw_est = nw_comp.get("this_week_estimate", 0)
    wow_pct = nw_comp.get("wow_pct", 0)
    nw_net = nw_comp.get("next_week_net", 0)

    wow_color = _GREEN if wow_pct >= 0 else _RED

    stats = [
        render_stat("Next Week Forecast", fmt_currency(nw_est),
                    subtitle="vs %s this week" % fmt_currency(tw_est)),
        render_stat("WoW Change", "%+.1f%%" % wow_pct,
                    subtitle="Next week vs this week",
                    color=wow_color),
        render_stat("Projected Net", fmt_currency(nw_net),
                    color=_GREEN if nw_net >= 0 else _RED),
    ]

    return render_card("Next Week Outlook", render_stat_grid(stats),
                       subtitle=nw.get("week_label", ""))


def _week_days_table(m, week_key, title):
    """Render a week's day-by-day table with per-channel breakdown.

    For this_week: shows Projected and Variance columns for actual days.
    For next_week: shows catering source indicator.
    """
    week = m.get(week_key, {})
    days = week.get("days", [])
    if not days:
        return ""

    week_label = week.get("week_label", "")
    week_total = week.get("week_total", 0)

    # Show projected/variance columns only for this_week (has actual days)
    show_projected = week_key == "this_week" and any(d.get("is_actual") for d in days)

    rows_html = []
    total_in = 0
    total_del = 0
    total_cat = 0
    total_rev = 0
    total_net = 0
    total_projected = 0
    total_variance = 0

    for d in days:
        is_today = d.get("is_today", False)
        is_actual = d.get("is_actual", False)

        css = ""
        tag = ""
        if is_today:
            css = "today-row"
            tag = ' <span style="font-size:10px;color:#8cb82e;">(today)</span>'
        elif not is_actual:
            css = "forecast-row"
            tag = ' <span style="font-size:9px;color:var(--muted);">fcst</span>'

        rev = d.get("revenue", 0)
        ch_in = d.get("channel_instore", 0)
        ch_del = d.get("channel_delivery", 0)
        ch_cat = d.get("channel_catering", 0)
        net = d.get("net_income", 0)
        net_color = _GREEN if net >= 0 else _RED

        total_in += ch_in
        total_del += ch_del
        total_cat += ch_cat
        total_rev += rev
        total_net += net

        # Catering source tag for next_week
        cat_tag = ""
        cat_src = d.get("catering_source", "")
        if cat_src == "scheduled" and not is_actual:
            cat_tag = ('<td>%s<br><span style="font-size:9px;color:%s;">booked</span></td>'
                       % (fmt_currency(ch_cat), _GREEN))
        else:
            cat_tag = '<td>%s</td>' % fmt_currency(ch_cat)

        # Weather indicator
        weather_str = ""
        w = d.get("weather")
        if w and w.get("multiplier", 1.0) < 1.0:
            weather_str = ' <span style="font-size:10px;color:%s;">%+.0f%%</span>' % (
                _RED, (w["multiplier"] - 1) * 100
            )

        # Projected / variance columns
        proj_cells = ""
        if show_projected:
            projected = d.get("projected", 0)
            variance = d.get("variance", 0)
            total_projected += projected
            total_variance += variance

            if is_actual:
                var_color = _GREEN if variance >= 0 else _RED
                proj_cells = (
                    '<td>%s</td>'
                    '<td style="color:%s;">%+.0f</td>'
                    % (fmt_currency(projected), var_color, variance)
                )
            else:
                # Forecast days — projected = revenue, no meaningful variance
                proj_cells = '<td style="color:var(--muted);">--</td><td>--</td>'

        rows_html.append(
            '<tr class="%s">'
            '<td>%s%s%s</td>'
            '<td>%s</td>'
            '<td>%s</td>'
            '%s'
            '<td>%s</td>'
            '%s'
            '<td style="color:%s;">%s</td>'
            '</tr>'
            % (css, _safe(d["dow_name"]), tag, weather_str,
               fmt_currency(ch_in), fmt_currency(ch_del),
               cat_tag, fmt_currency(rev),
               proj_cells,
               net_color, fmt_currency(net))
        )

    # Totals row
    net_color = _GREEN if total_net >= 0 else _RED
    proj_total_cells = ""
    if show_projected:
        var_color = _GREEN if total_variance >= 0 else _RED
        proj_total_cells = (
            '<td>%s</td><td style="color:%s;">%+.0f</td>'
            % (fmt_currency(total_projected), var_color, total_variance)
        )

    rows_html.append(
        '<tr class="total-row">'
        '<td>Total</td>'
        '<td>%s</td><td>%s</td><td>%s</td><td>%s</td>'
        '%s'
        '<td style="color:%s;">%s</td>'
        '</tr>'
        % (fmt_currency(total_in), fmt_currency(total_del),
           fmt_currency(total_cat), fmt_currency(total_rev),
           proj_total_cells,
           net_color, fmt_currency(total_net))
    )

    # Header row
    proj_headers = ""
    if show_projected:
        proj_headers = '<th>Projected</th><th>Var</th>'

    table = (
        '<table class="week-table">'
        '<thead><tr>'
        '<th>Day</th><th>In-Store</th><th>Delivery</th>'
        '<th>Catering</th><th>Total</th>'
        '%s'
        '<th>Net</th>'
        '</tr></thead>'
        '<tbody>' % proj_headers + "\n".join(rows_html) + '</tbody></table>'
    )

    actual_total = week.get("total_actual", 0)
    forecast_total = week.get("total_forecast", 0)

    if forecast_total > 0 and week_key == "this_week":
        note = (
            '<div style="font-size:11px;color:var(--muted);margin-top:8px;">'
            'Actual: %s | Remaining forecast: %s | Week estimate: %s'
            '</div>'
            % (fmt_currency(actual_total), fmt_currency(forecast_total),
               fmt_currency(week_total))
        )
        table += note

    return render_card(title, table, subtitle=week_label)


def _week_pl(m):
    pl = m.get("weekly_pl", {})
    if not pl:
        return ""

    rev = pl.get("revenue_total", 0)
    cogs = pl.get("cogs", 0)
    gp = pl.get("gross_profit", 0)
    labor = pl.get("labor", 0)
    third_party = pl.get("third_party_fees", 0)
    net = pl.get("net_income", 0)
    net_color = _GREEN if net >= 0 else _RED

    stats = [
        render_stat("Gross Revenue", fmt_currency(rev)),
        render_stat("COGS", fmt_currency(abs(cogs)),
                    subtitle="%.1f%%" % (abs(cogs) / rev * 100) if rev else ""),
        render_stat("Gross Profit", fmt_currency(gp),
                    color=_GREEN if gp >= 0 else _RED),
        render_stat("Net Income", fmt_currency(net), color=net_color,
                    subtitle="%.1f%% margin" % (net / rev * 100) if rev else ""),
    ]

    content = render_stat_grid(stats)

    # Expense breakdown
    expense_parts = []
    for key, label in EXPENSE_GROUP_ORDER:
        val = pl.get(key, 0)
        if val:
            pct = abs(val) / rev * 100 if rev else 0
            expense_parts.append("%s: %s (%.1f%%)" % (label, fmt_currency(abs(val)), pct))
    if expense_parts:
        content += (
            '<div style="font-size:11px;color:var(--muted);margin-top:10px;'
            'padding:10px;background:var(--bg);border-radius:8px;">'
            + " | ".join(expense_parts) + '</div>'
        )

    return render_card("Weekly P&L Estimate", content,
                       subtitle="Based on trailing 6-month expense ratios")
