"""
Daily email report — sends yesterday's dashboard + today's outlook via Gmail API.

Uses the shared GmailClient (tools/gmail_service/gmail_client.py) with existing
Google OAuth credentials from .env.

Charts rendered as PNG images via QuickChart.io (no API key needed, free tier).

Sections:
  1. Yesterday Review: profit, KPIs, hourly flow chart, channel doughnut,
     daypart bars, comparisons, channels, top items, highlights
  2. Looking Ahead: today's outlook, this week so far, upcoming catering, events

Usage:
    from send_daily_email import send_daily_report
    result = send_daily_report()           # Yesterday
    result = send_daily_report("20260302") # Specific date
"""
from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta

import yaml

from htmlrender.components import (
    build_bar_config, build_pie_config, build_line_config,
    quickchart_img, LIVITE_CHART_COLORS,
)

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
EVENTS_PATH = os.path.join(os.path.dirname(__file__), '..', 'docs', 'events.yaml')
FOOD_COST_PCT = 0.35


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════


def _load_email_config():
    """Load daily_email section from config.yaml."""
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get('daily_email', {})
    except Exception as e:
        logger.error("Failed to load email config: %s", e)
        return {}


def _load_fixed_cost(date):
    """Load the fixed cost for a given date from config.yaml schedule."""
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        fc = cfg.get('fixed_costs', {})
        schedule = fc.get('schedule', [])
        default = fc.get('default', 850)
        date_str = date.strftime("%Y%m%d")
        for entry in sorted(schedule, key=lambda e: e['date'], reverse=True):
            if date_str >= entry['date']:
                return entry['amount']
        return default
    except Exception:
        return 850


def _load_upcoming_events(from_date, days=7):
    """Load events from docs/events.yaml in the next N days."""
    try:
        with open(EVENTS_PATH) as f:
            data = yaml.safe_load(f) or {}
        holidays = data.get('holidays', [])
        end_dt = datetime.strptime(from_date, "%Y-%m-%d") + timedelta(days=days)
        end_date = end_dt.strftime("%Y-%m-%d")
        return [
            e for e in holidays
            if e.get('date') and from_date <= e['date'] <= end_date
        ]
    except Exception as e:
        logger.warning("Failed to load events: %s", e)
        return []


def _fmt(val, prefix="$", decimals=0):
    """Format a number for display."""
    if val is None:
        return "--"
    if decimals == 0:
        formatted = "{:,.0f}".format(abs(val))
    else:
        fmt_str = "{:,.%df}" % decimals
        formatted = fmt_str.format(abs(val))
    sign = "-" if val < 0 else ""
    if prefix:
        return "%s%s%s" % (sign, prefix, formatted)
    return "%s%s" % (sign, formatted)


def _pct(val):
    """Format as percentage."""
    if val is None:
        return "--"
    if val >= 0:
        return "+%.1f%%" % val
    return "%.1f%%" % val


def _delta_html(diff, pct, direction):
    """Render a comparison delta as colored text."""
    if direction == "up":
        color = "#16a34a"
        arrow = "&#9650;"
    elif direction == "down":
        color = "#dc2626"
        arrow = "&#9660;"
    else:
        color = "#7a7a6f"
        arrow = "&#8212;"
    return '<span style="color:%s;font-weight:600;">%s %s</span>' % (
        color, arrow, _pct(pct))


# ══════════════════════════════════════════════════════════════════════════════
# HTML Builder
# ══════════════════════════════════════════════════════════════════════════════


def _build_email_html(metrics, comparisons, date,
                      today_data=None, catering_orders=None, events=None):
    """Build enhanced mobile-first HTML email report with charts.

    Args:
        metrics: Yesterday's compute_all_metrics() output
        comparisons: WoW/SWLY/YoY deltas
        date: Yesterday's datetime
        today_data: generate_today_prediction() output (optional)
        catering_orders: Upcoming catering list (optional)
        events: Upcoming events list (optional)
    """
    rev = metrics.get('revenue', {})
    lab = metrics.get('labor', {})
    ords = metrics.get('orders', {})
    cust = metrics.get('customers', {})
    pays = metrics.get('payments', {})
    kit = metrics.get('kitchen', {}) or {}
    deltas = comparisons.get('deltas', {}) if comparisons else {}

    # Yesterday's key numbers
    revenue = rev.get('toast_total', 0)
    orders_count = rev.get('total_orders', 0)
    avg_check = rev.get('avg_check', 0)
    labor_total = lab.get('total_labor', 0)
    labor_pct = lab.get('labor_pct', 0)
    labor_hours = lab.get('total_hours', 0)
    unique_cust = cust.get('unique_customers', 0)

    # Profit estimate
    food_cost = revenue * FOOD_COST_PCT
    fixed_cost = _load_fixed_cost(date)
    est_profit = revenue - labor_total - food_cost - fixed_cost

    day_label = date.strftime("%A, %B %-d, %Y")
    short_label = date.strftime("%a %b %-d")
    profit_color = "#16a34a" if est_profit > 0 else "#dc2626"

    parts = []

    # ── Head + styles ──
    parts.append(
        '<!DOCTYPE html>'
        '<html><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<style>'
        'body{margin:0;padding:0;background:#F5EDDC;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}"
        '.wrap{max-width:560px;margin:0 auto;padding:12px;}'
        '.card{background:#ffffff;border-radius:10px;padding:16px 18px;margin-bottom:10px;}'
        '.stitle{font-size:13px;font-weight:700;color:#475417;'
        'text-transform:uppercase;letter-spacing:0.5px;margin:0 0 8px;}'
        'table{width:100%;border-collapse:collapse;font-size:13px;}'
        'th{text-align:left;padding:6px 8px;color:#7a7a6f;font-weight:600;'
        'font-size:11px;text-transform:uppercase;letter-spacing:0.3px;'
        'border-bottom:2px solid #e2d9c8;}'
        'td{border-bottom:1px solid #f0ebe0;}'
        'tr:last-child td{border-bottom:none;}'
        '</style></head>'
        '<body><div class="wrap">'
    )

    # ── Header ──
    parts.append(
        '<div style="text-align:center;padding:16px 0 8px;">'
        '<p style="font-size:18px;font-weight:700;color:#475417;margin:0;">'
        'Livite Daily Report</p>'
        '<p style="font-size:13px;color:#7a7a6f;margin:4px 0 0;">%s</p>'
        '</div>' % day_label
    )

    # ── Profit banner ──
    parts.append(
        '<div style="background:%s;border-radius:8px;padding:14px 18px;'
        'text-align:center;margin-bottom:10px;">'
        '<p style="font-size:26px;font-weight:700;color:#fff;margin:0;">%s</p>'
        '<p style="font-size:11px;color:rgba(255,255,255,0.8);margin:2px 0 0;'
        'text-transform:uppercase;letter-spacing:0.3px;">Est. Daily Profit</p>'
        '</div>' % (profit_color, _fmt(est_profit))
    )

    # ── KPI grid 2x2 (table layout for email compatibility) ──
    _kpi_cell = (
        '<td style="width:50%%;background:#faf6ee;border-radius:8px;'
        'padding:10px 12px;text-align:center;border:none;">'
        '<p style="font-size:22px;font-weight:700;color:#475417;margin:0;">%s</p>'
        '<p style="font-size:11px;color:#7a7a6f;margin:2px 0 0;'
        'text-transform:uppercase;letter-spacing:0.3px;">%s</p></td>'
    )
    labor_label = "Labor (%.1f%%)" % labor_pct
    parts.append(
        '<div class="card">'
        '<table cellpadding="0" cellspacing="0" '
        'style="width:100%%;border-collapse:separate;border-spacing:4px;">'
        '<tr>' + _kpi_cell % (_fmt(revenue), "Revenue")
        + _kpi_cell % (str(orders_count), "Orders") + '</tr>'
        '<tr>' + _kpi_cell % (_fmt(avg_check, decimals=2), "Avg Check")
        + _kpi_cell % (_fmt(labor_total), labor_label) + '</tr>'
        '</table></div>'
    )

    # ── Hourly Revenue Flow chart (same config builder as web dashboard) ──
    hourly_data = rev.get('hourly', [])
    if hourly_data:
        hr_labels = []
        hr_values = []
        for h in hourly_data:
            hour = h.get('hour', 0)
            if hour == 0:
                lbl = "12A"
            elif hour < 12:
                lbl = "%dA" % hour
            elif hour == 12:
                lbl = "12P"
            else:
                lbl = "%dP" % (hour - 12)
            hr_labels.append(lbl)
            hr_values.append(round(h.get('revenue', 0)))

        flow_config = build_line_config(
            hr_labels,
            [{"label": "Revenue", "data": hr_values, "color": "#475417",
              "fill": True, "pointRadius": 0}],
        )

        peak_q = rev.get('peak_quarter', {})
        peak_h = rev.get('peak_hour', {})
        peak_text = ""
        if peak_q and peak_q.get('label'):
            peak_text = (
                '<p style="font-size:12px;color:#7a7a6f;margin:8px 0 0;">'
                'Peak 15 min: %s (%s, %d orders)</p>'
            ) % (peak_q['label'], _fmt(peak_q.get('revenue', 0)),
                 peak_q.get('orders', 0))
        elif peak_h and peak_h.get('hour') is not None:
            hour = peak_h['hour']
            if hour < 12:
                h_label = "%d AM" % hour
            elif hour == 12:
                h_label = "12 PM"
            else:
                h_label = "%d PM" % (hour - 12)
            peak_text = (
                '<p style="font-size:12px;color:#7a7a6f;margin:8px 0 0;">'
                'Peak hour: %s (%s, %d orders)</p>'
            ) % (h_label, _fmt(peak_h.get('revenue', 0)),
                 peak_h.get('orders', 0))

        parts.append(
            '<div class="card">'
            '<p class="stitle">Revenue Flow</p>'
            + quickchart_img(flow_config, 520, 200, "Hourly Revenue")
            + peak_text
            + '</div>'
        )

    # ── Channel Mix doughnut (same config builder as web dashboard) ──
    w3o = rev.get('walkin_3p_online', {})
    if w3o:
        ch_labels = []
        ch_values = []
        ch_colors = ['#475417', '#8FBC8F', '#a8c06a']
        for key in ['Walk-In', '3P', 'Online']:
            entry = w3o.get(key, {})
            ch_rev = entry.get('revenue', 0)
            if ch_rev > 0:
                ch_labels.append(key)
                ch_values.append(round(ch_rev))
        if ch_labels:
            donut_config = build_pie_config(
                ch_labels, ch_values,
                colors=ch_colors[:len(ch_labels)], doughnut=True,
            )
            parts.append(
                '<div class="card">'
                '<p class="stitle">Channel Mix</p>'
                + quickchart_img(donut_config, 520, 220, "Channel Mix")
                + '</div>'
            )

    # ── Daypart Breakdown bar chart (same config builder as web dashboard) ──
    daypart_data = lab.get('daypart_efficiency', [])
    if daypart_data:
        dp_labels = [dp.get('name', dp.get('daypart', '')) for dp in daypart_data]
        dp_values = [round(dp.get('revenue', 0)) for dp in daypart_data]
        daypart_config = build_bar_config(
            dp_labels,
            [{"label": "Revenue", "data": dp_values,
              "colors": LIVITE_CHART_COLORS[:len(dp_labels)]}],
            horizontal=True,
        )
        parts.append(
            '<div class="card">'
            '<p class="stitle">Revenue by Daypart</p>'
            + quickchart_img(daypart_config, 520, 200, "Daypart Revenue")
            + '</div>'
        )

    # ── Menu Group pie chart (same config builder as web dashboard) ──
    menu_groups = ords.get('menu_group_mix', [])
    if menu_groups:
        sorted_groups = sorted(menu_groups,
                               key=lambda x: x.get('revenue', 0), reverse=True)
        mg_labels = []
        mg_values = []
        other_rev = 0
        for i, grp in enumerate(sorted_groups):
            grp_rev = grp.get('revenue', 0)
            if grp_rev < 1:
                continue
            if i < 6:
                mg_labels.append(grp.get('group', ''))
                mg_values.append(round(grp_rev))
            else:
                other_rev += grp_rev
        if other_rev > 0:
            mg_labels.append("Other")
            mg_values.append(round(other_rev))
        if mg_labels:
            menu_config = build_pie_config(mg_labels, mg_values)
            parts.append(
                '<div class="card">'
                '<p class="stitle">Menu Category Mix</p>'
                + quickchart_img(menu_config, 520, 220, "Menu Groups")
                + '</div>'
            )

    # ── Comparisons ──
    def _comp_row(label, key):
        d = deltas.get(key, {})
        cells = ""
        for period in ['wow', 'swly', 'yoy']:
            delta = d.get(period)
            if delta and len(delta) >= 3:
                cells += (
                    '<td style="padding:6px 8px;text-align:center;">%s</td>'
                    % _delta_html(*delta)
                )
            else:
                cells += (
                    '<td style="padding:6px 8px;text-align:center;'
                    'color:#ccc;">--</td>'
                )
        return (
            '<tr><td style="padding:6px 8px;font-weight:500;">%s</td>'
            '%s</tr>' % (label, cells)
        )

    parts.append(
        '<div class="card">'
        '<p class="stitle">Week / Year Comparisons</p>'
        '<table>'
        '<tr><th></th>'
        '<th style="text-align:center;">WoW</th>'
        '<th style="text-align:center;">SWLY</th>'
        '<th style="text-align:center;">YoY</th></tr>'
        + _comp_row("Revenue", "revenue")
        + _comp_row("Orders", "orders")
        + _comp_row("Avg Check", "avg_check")
        + _comp_row("Labor", "labor_total")
        + _comp_row("Guests", "guests")
        + '</table></div>'
    )

    # ── Channel breakdown with inline CSS bars ──
    channels = rev.get('channels', {})
    sorted_channels = sorted(
        channels.items(), key=lambda x: x[1].get('revenue', 0), reverse=True
    )
    max_ch_rev = max(
        (ch.get('revenue', 0) for _, ch in sorted_channels), default=1
    )
    if max_ch_rev < 1:
        max_ch_rev = 1

    ch_rows = ""
    for ch_name, ch_data in sorted_channels:
        ch_rev = ch_data.get('revenue', 0)
        if ch_rev < 1:
            continue
        bar_w = int(ch_rev / max_ch_rev * 100)
        ch_rows += (
            '<tr>'
            '<td style="padding:5px 8px;width:30%%;border:none;">%s</td>'
            '<td style="padding:5px 8px;width:40%%;border:none;">'
            '<div style="background:#e2d9c8;border-radius:3px;height:14px;">'
            '<div style="background:#475417;border-radius:3px;height:14px;'
            'width:%d%%;"></div></div></td>'
            '<td style="padding:5px 8px;text-align:right;font-weight:600;'
            'white-space:nowrap;border:none;">%s</td></tr>'
        ) % (ch_name, bar_w, _fmt(ch_rev))

    parts.append(
        '<div class="card">'
        '<p class="stitle">Channel Detail</p>'
        '<table>%s</table></div>' % ch_rows
    )

    # ── Top 5 items ──
    top_items = ords.get('top_items_by_qty', [])[:5]
    item_rows = ""
    for i, item in enumerate(top_items):
        idx = i + 1
        name = item.get('item', '')
        qty = item.get('qty', 0)
        irev = item.get('revenue', 0)
        item_rows += (
            '<tr>'
            '<td style="padding:5px 8px;">%d. %s</td>'
            '<td style="padding:5px 8px;text-align:center;font-weight:600;">'
            '%s</td>'
            '<td style="padding:5px 8px;text-align:right;">%s</td></tr>'
        ) % (idx, name, qty, _fmt(irev))

    parts.append(
        '<div class="card">'
        '<p class="stitle">Top 5 Items</p>'
        '<table>'
        '<tr><th>Item</th><th style="text-align:center;">Qty</th>'
        '<th style="text-align:right;">Revenue</th></tr>'
        '%s</table></div>' % item_rows
    )

    # ── Yesterday's Highlights (catering, large orders, discounts, tips) ──
    highlights = []

    # Catering from yesterday
    cat_data = ords.get('catering', {})
    cat_rev = cat_data.get('revenue', 0)
    if cat_rev > 0:
        cat_orders = cat_data.get('orders', 0)
        cat_top = cat_data.get('top_items', [])
        top_item_str = ""
        if cat_top:
            top_item_str = " (%s)" % cat_top[0].get('item', '')
        highlights.append(
            '<tr><td style="padding:4px 8px;border:none;">Catering</td>'
            '<td style="padding:4px 8px;text-align:right;border:none;'
            'font-weight:600;">%s</td>'
            '<td style="padding:4px 8px;text-align:right;border:none;'
            'color:#7a7a6f;">%d orders%s</td></tr>'
            % (_fmt(cat_rev), cat_orders, top_item_str)
        )

    # Uber BOGO impact
    bogo = ords.get('uber_bogo_impact', {})
    bogo_total = bogo.get('discount_total', 0)
    if bogo_total > 5:
        bogo_annual = bogo.get('annualized', 0)
        highlights.append(
            '<tr><td style="padding:4px 8px;border:none;">BOGO Discounts</td>'
            '<td style="padding:4px 8px;text-align:right;border:none;'
            'font-weight:600;color:#dc2626;">-%s</td>'
            '<td style="padding:4px 8px;text-align:right;border:none;'
            'color:#7a7a6f;">%s/yr annualized</td></tr>'
            % (_fmt(bogo_total), _fmt(bogo_annual))
        )

    # Total discounts
    total_discounts = rev.get('total_discounts', 0)
    if total_discounts > 5 and total_discounts != bogo_total:
        highlights.append(
            '<tr><td style="padding:4px 8px;border:none;">Total Discounts</td>'
            '<td style="padding:4px 8px;text-align:right;border:none;'
            'font-weight:600;color:#dc2626;">-%s</td>'
            '<td style="padding:4px 8px;border:none;"></td></tr>'
            % _fmt(total_discounts)
        )

    # Tips
    tip_summary = pays.get('tip_summary', {})
    total_tips = tip_summary.get('total_tips', 0)
    if total_tips > 0:
        avg_tip_pct = tip_summary.get('avg_tip_pct', 0)
        tip_pct_str = "%.1f%% avg" % avg_tip_pct if avg_tip_pct else ""
        highlights.append(
            '<tr><td style="padding:4px 8px;border:none;">Tips</td>'
            '<td style="padding:4px 8px;text-align:right;border:none;'
            'font-weight:600;">%s</td>'
            '<td style="padding:4px 8px;text-align:right;border:none;'
            'color:#7a7a6f;">%s</td></tr>'
            % (_fmt(total_tips), tip_pct_str)
        )

    # Voids
    void_data = ords.get('void_analysis', {})
    void_amt = void_data.get('void_amount', 0)
    if void_amt > 0:
        void_qty = void_data.get('void_qty', 0)
        highlights.append(
            '<tr><td style="padding:4px 8px;border:none;">Voids</td>'
            '<td style="padding:4px 8px;text-align:right;border:none;'
            'font-weight:600;color:#dc2626;">-%s</td>'
            '<td style="padding:4px 8px;text-align:right;border:none;'
            'color:#7a7a6f;">%d items</td></tr>'
            % (_fmt(void_amt), void_qty)
        )

    if highlights:
        parts.append(
            '<div class="card">'
            '<p class="stitle">Highlights</p>'
            '<table style="font-size:13px;">%s</table></div>'
            % "".join(highlights)
        )

    # ── Kitchen Ticket Speed by Hour ──
    hourly_speed = kit.get('hourly_speed', {})
    # Merge all stations into a single hourly view
    if hourly_speed:
        merged_hours = {}
        for station_data in hourly_speed.values():
            if not isinstance(station_data, list):
                continue
            for slot in station_data:
                h = slot.get('hour', 0)
                if h not in merged_hours:
                    merged_hours[h] = {'times': [], 'tickets': 0}
                merged_hours[h]['times'].append(slot.get('median', 0))
                merged_hours[h]['tickets'] += slot.get('tickets', 0)
        if merged_hours:
            sorted_hours = sorted(merged_hours.keys())
            kt_labels = []
            kt_values = []
            kt_colors = []
            for h in sorted_hours:
                if h == 0:
                    lbl = "12A"
                elif h < 12:
                    lbl = "%dA" % h
                elif h == 12:
                    lbl = "12P"
                else:
                    lbl = "%dP" % (h - 12)
                kt_labels.append(lbl)
                avg_med = sum(merged_hours[h]['times']) / len(merged_hours[h]['times'])
                kt_values.append(round(avg_med, 1))
                if avg_med <= 5:
                    kt_colors.append('#16a34a')
                elif avg_med <= 10:
                    kt_colors.append('#e8a830')
                else:
                    kt_colors.append('#dc2626')

            speed_config = build_bar_config(
                kt_labels,
                [{"label": "Median (min)", "data": kt_values, "colors": kt_colors}],
            )
            # Overall median from stations
            all_stations = kit.get('stations', {})
            overall_median = 0
            total_tickets = 0
            for st_data in all_stations.values():
                if isinstance(st_data, dict):
                    overall_median = st_data.get('median', 0)
                    total_tickets = st_data.get('total_tickets', 0)
            speed_note = ""
            if overall_median > 0:
                med_color = '#16a34a' if overall_median <= 5 else (
                    '#e8a830' if overall_median <= 10 else '#dc2626')
                speed_note = (
                    '<p style="font-size:12px;color:#7a7a6f;margin:8px 0 0;">'
                    'Overall median: <span style="color:%s;font-weight:600;">'
                    '%.1f min</span> (%d tickets)</p>'
                ) % (med_color, overall_median, total_tickets)

            parts.append(
                '<div class="card">'
                '<p class="stitle">Kitchen Ticket Speed</p>'
                + quickchart_img(speed_config, 520, 200, "Ticket Speed")
                + speed_note
                + '</div>'
            )

    # ── Clock-Out Alerts ──
    auto_clockouts = lab.get('auto_clockouts', [])
    shift_dist = lab.get('shift_distribution', {})
    over_10h = shift_dist.get('over_10h', 0)
    alert_rows = []

    if auto_clockouts:
        names = ", ".join(auto_clockouts[:5])
        if len(auto_clockouts) > 5:
            names += " +%d more" % (len(auto_clockouts) - 5)
        alert_rows.append(
            '<tr>'
            '<td style="padding:5px 8px;border:none;color:#dc2626;'
            'font-weight:600;">Auto Clock-Out</td>'
            '<td style="padding:5px 8px;border:none;">%s</td></tr>'
            % names
        )

    if over_10h > 0:
        # Find who had 10h+ shifts from employee_roster
        long_names = []
        for emp in lab.get('employee_roster', []):
            if emp.get('total_hours', 0) > 10:
                name_parts = emp.get('employee', '').split(', ')
                if len(name_parts) >= 2:
                    long_names.append(
                        "%s (%.1fh)" % (name_parts[1].title(), emp['total_hours']))
                else:
                    long_names.append(
                        "%s (%.1fh)" % (emp['employee'].title(), emp['total_hours']))
        detail = ", ".join(long_names) if long_names else "%d shifts" % over_10h
        alert_rows.append(
            '<tr>'
            '<td style="padding:5px 8px;border:none;color:#e8a830;'
            'font-weight:600;">10h+ Shifts</td>'
            '<td style="padding:5px 8px;border:none;">%s</td></tr>'
            % detail
        )

    if alert_rows:
        parts.append(
            '<div class="card" style="border-left:3px solid #dc2626;">'
            '<p class="stitle">Clock-Out Alerts</p>'
            '<table style="font-size:13px;">%s</table></div>'
            % "".join(alert_rows)
        )

    # ══════════════════════════════════════════════════════════════════════
    # LOOKING AHEAD section
    # ══════════════════════════════════════════════════════════════════════

    has_ahead = (today_data or catering_orders or events)
    if has_ahead:
        parts.append(
            '<div style="text-align:center;padding:8px 0;font-size:11px;'
            'color:#a0a090;text-transform:uppercase;letter-spacing:1px;">'
            '&#8212;&#8212; Looking Ahead &#8212;&#8212;</div>'
        )

    # ── Today's Outlook ──
    if today_data:
        pred = today_data.get('prediction', {})
        pred_rev = pred.get('revenue_total', 0)
        weather = today_data.get('weather')
        dow_name = today_data.get('dow_name', '')
        by_ch = pred.get('revenue_by_channel', {})
        ch_in = by_ch.get('instore', 0)
        ch_del = by_ch.get('delivery', 0)
        ch_cat = by_ch.get('catering', 0)

        # Weather row
        weather_row = ""
        if weather:
            cond = weather.get('conditions', '')
            temp_h = weather.get('temp_high')
            temp_l = weather.get('temp_low')
            wmult = weather.get('multiplier', 1.0)
            temp_str = ""
            if temp_h is not None and temp_l is not None:
                temp_str = "%d-%dF" % (int(temp_l), int(temp_h))
            elif temp_h is not None:
                temp_str = "%dF" % int(temp_h)
            wmult_pct = (wmult - 1.0) * 100
            weather_impact = ""
            if abs(wmult_pct) > 0.5:
                w_color = "#16a34a" if wmult_pct >= 0 else "#dc2626"
                weather_impact = (
                    ' <span style="color:%s;font-weight:600;">(%+.0f%%)</span>'
                    % (w_color, wmult_pct)
                )
            weather_row = (
                '<tr><td style="padding:4px 8px;color:#7a7a6f;border:none;">'
                'Weather</td>'
                '<td style="padding:4px 8px;text-align:right;border:none;">'
                '%s %s%s</td></tr>'
            ) % (cond, temp_str, weather_impact)

        # P&L estimate for today
        daily_pl = pred.get('daily_pl', {})
        est_net = daily_pl.get('net_income', 0)
        net_color = "#16a34a" if est_net > 0 else "#dc2626"

        parts.append(
            '<div class="card">'
            '<p class="stitle">Today\'s Outlook &mdash; %s</p>'
            '<table style="font-size:13px;">'
            '<tr><td style="padding:4px 8px;color:#7a7a6f;border:none;">'
            'Projected Revenue</td>'
            '<td style="padding:4px 8px;text-align:right;font-weight:700;'
            'font-size:18px;color:#475417;border:none;">%s</td></tr>'
            '%s'
            '<tr><td style="padding:4px 8px;color:#7a7a6f;border:none;">'
            'In-Store</td>'
            '<td style="padding:4px 8px;text-align:right;border:none;">'
            '%s</td></tr>'
            '<tr><td style="padding:4px 8px;color:#7a7a6f;border:none;">'
            'Delivery</td>'
            '<td style="padding:4px 8px;text-align:right;border:none;">'
            '%s</td></tr>'
            '<tr><td style="padding:4px 8px;color:#7a7a6f;border:none;">'
            'Catering</td>'
            '<td style="padding:4px 8px;text-align:right;border:none;">'
            '%s</td></tr>'
            '<tr><td style="padding:4px 8px;color:#7a7a6f;border:none;">'
            'Est. Net Income</td>'
            '<td style="padding:4px 8px;text-align:right;font-weight:600;'
            'color:%s;border:none;">%s</td></tr>'
            '</table></div>'
            % (dow_name, _fmt(pred_rev), weather_row,
               _fmt(ch_in), _fmt(ch_del), _fmt(ch_cat),
               net_color, _fmt(est_net))
        )

    # ── This Week So Far ──
    if today_data and today_data.get('this_week'):
        tw = today_data['this_week']
        week_days = tw.get('days', [])
        if week_days:
            max_day_rev = max(
                (d.get('revenue', 0) for d in week_days), default=1
            )
            if max_day_rev < 1:
                max_day_rev = 1

            day_rows = ""
            for d in week_days:
                d_rev = d.get('revenue', 0)
                bar_w = int(d_rev / max_day_rev * 100)
                is_actual = d.get('is_actual', False)
                is_today_flag = d.get('is_today', False)
                bar_color = "#475417" if is_actual else "#a8c06a"
                lbl_style = "font-weight:700;" if is_today_flag else ""

                tag = ""
                if is_today_flag:
                    tag = (' <span style="color:#7a7a6f;font-size:10px;">'
                           '(today)</span>')
                elif not is_actual:
                    tag = (' <span style="color:#7a7a6f;font-size:10px;">'
                           '(est)</span>')

                day_rows += (
                    '<tr>'
                    '<td style="padding:4px 8px;%sborder:none;">%s%s</td>'
                    '<td style="padding:4px 8px;width:40%%;border:none;">'
                    '<div style="background:#e2d9c8;border-radius:3px;'
                    'height:14px;">'
                    '<div style="background:%s;border-radius:3px;height:14px;'
                    'width:%d%%;"></div></div></td>'
                    '<td style="padding:4px 8px;text-align:right;'
                    'font-weight:600;white-space:nowrap;border:none;">'
                    '%s</td></tr>'
                ) % (lbl_style, d.get('dow_name', ''), tag,
                     bar_color, bar_w, _fmt(d_rev))

            wk_total = tw.get('week_total_estimate', 0)

            parts.append(
                '<div class="card">'
                '<p class="stitle">This Week So Far</p>'
                '<p style="font-size:12px;color:#7a7a6f;margin:0 0 8px;">'
                '%s</p>'
                '<table>%s'
                '<tr><td colspan="2" style="padding:6px 8px;font-weight:700;'
                'border-top:2px solid #e2d9c8;border-bottom:none;">'
                'Week Total (Est.)</td>'
                '<td style="padding:6px 8px;text-align:right;font-weight:700;'
                'border-top:2px solid #e2d9c8;border-bottom:none;">'
                '%s</td></tr></table></div>'
                % (tw.get('week_label', ''), day_rows, _fmt(wk_total))
            )

    # ── Upcoming Catering ──
    if catering_orders:
        cat_rows = ""
        for order in catering_orders[:5]:
            o_name = order.get('name', 'Order')
            o_plat = order.get('platform', '')
            o_sub = order.get('subtotal', 0)
            o_date = order.get('date', '')
            try:
                o_dt = datetime.strptime(o_date, "%Y-%m-%d")
                o_label = o_dt.strftime("%a %-m/%-d")
            except ValueError:
                o_label = o_date
            display_name = o_name[:25]
            if len(o_name) > 25:
                display_name += "..."
            cat_rows += (
                '<tr>'
                '<td style="padding:4px 8px;">%s</td>'
                '<td style="padding:4px 8px;color:#7a7a6f;">%s</td>'
                '<td style="padding:4px 8px;text-align:right;font-weight:600;">'
                '%s</td></tr>'
            ) % (o_label, "%s / %s" % (o_plat, display_name), _fmt(o_sub))

        cat_total = sum(o.get('subtotal', 0) for o in catering_orders[:5])
        parts.append(
            '<div class="card">'
            '<p class="stitle">Upcoming Catering</p>'
            '<table style="font-size:13px;">'
            '<tr><th>Date</th><th>Order</th>'
            '<th style="text-align:right;">Total</th></tr>'
            '%s</table>'
            '<p style="font-size:12px;color:#7a7a6f;margin:8px 0 0;'
            'text-align:right;">Pipeline: %s</p></div>'
            % (cat_rows, _fmt(cat_total))
        )

    # ── Upcoming Events ──
    if events:
        ev_tags = ""
        for ev in events[:4]:
            ev_name = ev.get('name', '')
            ev_date = ev.get('date', '')
            try:
                ev_dt = datetime.strptime(ev_date, "%Y-%m-%d")
                ev_label = ev_dt.strftime("%a %-m/%-d")
            except ValueError:
                ev_label = ev_date
            ev_tags += (
                '<span style="display:inline-block;background:#faf6ee;'
                'border-radius:6px;padding:4px 10px;margin:2px 4px 2px 0;'
                'font-size:12px;">%s &mdash; %s</span> '
            ) % (ev_label, ev_name)
        parts.append(
            '<div class="card">'
            '<p class="stitle">Upcoming Events</p>'
            '<div>%s</div></div>' % ev_tags
        )

    # ── Cost summary footer ──
    parts.append(
        '<div class="card" style="font-size:12px;color:#7a7a6f;">'
        '<table style="font-size:12px;">'
        '<tr><td style="padding:3px 8px;border:none;">Food Cost (35%%)</td>'
        '<td style="padding:3px 8px;text-align:right;border:none;">'
        '%s</td></tr>'
        '<tr><td style="padding:3px 8px;border:none;">Fixed Costs</td>'
        '<td style="padding:3px 8px;text-align:right;border:none;">'
        '%s</td></tr>'
        '<tr><td style="padding:3px 8px;border:none;">Labor Hours</td>'
        '<td style="padding:3px 8px;text-align:right;border:none;">'
        '%.1f hrs</td></tr>'
        '<tr><td style="padding:3px 8px;border:none;">Unique Customers</td>'
        '<td style="padding:3px 8px;text-align:right;border:none;">'
        '%s</td></tr>'
        '</table></div>'
        % (_fmt(food_cost), _fmt(fixed_cost), labor_hours, unique_cust)
    )

    # ── Footer ──
    parts.append(
        '<div style="text-align:center;padding:12px 0;font-size:11px;'
        'color:#a0a090;">Livite Dashboard &mdash; %s</div>'
        '</div></body></html>' % short_label
    )

    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# Main entry point
# ══════════════════════════════════════════════════════════════════════════════


def send_daily_report(date_str=None):
    """Generate yesterday's dashboard and email it.

    Args:
        date_str: Optional YYYYMMDD string. Defaults to yesterday (ET).

    Returns:
        Dict with 'ok' (bool) and 'error' or 'recipients'.
    """
    if date_str is None:
        from pytz import timezone as _tz
        et = _tz('US/Eastern')
        yesterday = datetime.now(et) - timedelta(days=1)
        date_str = yesterday.strftime("%Y%m%d")

    email_cfg = _load_email_config()
    if not email_cfg.get('enabled', False):
        return {"ok": False, "error": "Daily email is disabled in config.yaml"}

    recipients = email_cfg.get('recipients', [])
    if not recipients:
        return {"ok": False, "error": "No recipients in config.yaml"}

    try:
        date = datetime.strptime(date_str, "%Y%m%d")
    except ValueError:
        return {"ok": False, "error": "Invalid date: %s" % date_str}

    # ── 1. Yesterday's metrics ──
    try:
        from fetch_toast_data import get_daily_data
        from dashboard_metrics import compute_all_metrics
        from dashboard_comparisons import (
            resolve_comparison_dates, fetch_all_comparisons, compute_all_deltas
        )
        from metrics_cache import get_cached_metrics, cache_metrics, is_today

        use_cache = not is_today(date_str)
        metrics = get_cached_metrics(date_str) if use_cache else None
        if metrics is None:
            data = get_daily_data(date, quiet=True)
            if 'OrderDetails' not in data:
                return {"ok": False,
                        "error": "No data available for %s" % date_str}
            metrics = compute_all_metrics(data, date)
            if use_cache:
                cache_metrics(date_str, metrics)

        # Comparisons (WoW, SWLY, YoY)
        comparisons = {}
        try:
            from fetch_toast_data import list_available_dates
            available = set(list_available_dates())
            comp_dates = resolve_comparison_dates(date)
            comparisons = fetch_all_comparisons(comp_dates, available)
            current_summary = {
                'revenue': metrics.get('revenue', {}).get('toast_total', 0),
                'orders': metrics.get('revenue', {}).get('total_orders', 0),
                'avg_check': metrics.get('revenue', {}).get('avg_check', 0),
                'guests': metrics.get('revenue', {}).get('total_guests', 0),
                'labor_total': metrics.get('labor', {}).get('total_labor', 0),
                'labor_pct': metrics.get('labor', {}).get('labor_pct', 0),
                'unique_customers': metrics.get('customers', {}).get(
                    'unique_customers', 0),
            }
            all_deltas = compute_all_deltas(current_summary, comparisons)
            comparisons['deltas'] = all_deltas
        except Exception as e:
            logger.warning("Comparisons failed: %s", e)

    except Exception as e:
        return {"ok": False,
                "error": "Metrics computation failed: %s" % str(e)}

    # ── 2. Today's outlook (forecast + this week) ──
    today_data = None
    try:
        from forecast.today_data import generate_today_prediction
        today_data = generate_today_prediction()
        logger.info("Today's prediction loaded: $%s",
                     "{:,.0f}".format(
                         today_data.get('prediction', {}).get(
                             'revenue_total', 0)))
    except Exception as e:
        logger.warning("Today's prediction failed (non-critical): %s", e)

    # ── 3. Upcoming catering orders (next 7 days) ──
    catering_orders = None
    try:
        today_iso = datetime.now().strftime("%Y-%m-%d")
        from catering.notion import fetch_upcoming_orders
        catering_orders = fetch_upcoming_orders(from_date=today_iso)
        if catering_orders:
            logger.info("Found %d upcoming catering orders",
                        len(catering_orders))
    except Exception as e:
        logger.warning("Catering fetch failed (non-critical): %s", e)

    # ── 4. Upcoming events (next 7 days) ──
    today_iso = datetime.now().strftime("%Y-%m-%d")
    events = _load_upcoming_events(today_iso, days=7)

    # ── Build email HTML ──
    html = _build_email_html(
        metrics, comparisons, date,
        today_data=today_data,
        catering_orders=catering_orders,
        events=events,
    )

    day_label = date.strftime("%a %b %-d, %Y")
    subject = "Livite Daily Report -- %s" % day_label
    rev_total = metrics.get('revenue', {}).get('toast_total', 0)
    plain = "Livite Daily Report for %s. Revenue: $%s" % (
        day_label, "{:,.0f}".format(rev_total))

    try:
        from gmail_service.gmail_client import GmailClient
        client = GmailClient()
        return client.send_html(recipients, subject, html, plain)
    except Exception as e:
        return {"ok": False, "error": "Email send failed: %s" % str(e)}
