"""Dashboard section renderers: header, revenue, orders, kitchen, labor, etc."""

from __future__ import annotations


from .components import (
    _next_chart_id, _js, _safe, _g, _gl, _delta_html, _DASH,
    render_chartjs_bar, render_chartjs_pie, render_chartjs_line,
    fmt_currency, fmt_pct, fmt_num, color_for_delta, fmt_delta,
    render_stat, render_stat_grid, render_insight, render_bar_h,
    render_stacked_bar, render_vertical_bars, render_heatmap_cell,
    render_table, render_badge, render_divider, render_card,
    _bar_text_color, LIVITE_CHART_COLORS,
)

def render_header(metrics: dict, logo_b64: str = "") -> str:
    date_display = _g(metrics, "date_display", default="")
    day_name = _g(metrics, "day_of_week", default="")
    rev = _g(metrics, "revenue")
    total_orders = _g(rev, "total_orders", default=0) if rev else 0
    net_revenue = _g(rev, "toast_total", default=0) if rev else 0
    is_range = metrics.get("is_range", False)
    range_days = metrics.get("range_days", 1)

    # Determine day type
    catering = _g(metrics, "orders", "catering", default={})
    catering_pct = _g(catering, "pct_of_total", default=0) if isinstance(catering, dict) else 0
    day_badge = ""
    if catering_pct and float(catering_pct) > 10:
        day_badge = f' {render_badge("Catering Day", "a")}'

    logo_html = ""
    if logo_b64:
        logo_html = f'<img src="data:image/png;base64,{logo_b64}" alt="Livite" style="height:44px;max-width:80%;margin-bottom:8px;display:block;margin-left:auto;margin-right:auto;">'

    subtitle_parts = [f"{fmt_num(total_orders)} orders", fmt_currency(net_revenue)]
    if day_badge:
        subtitle_parts.append(day_badge)
    subtitle = " &middot; ".join(subtitle_parts)

    dashboard_type = "Range Dashboard" if is_range else "Daily Dashboard"

    # Daily average row for range mode
    avg_row = ""
    if is_range and range_days > 0:
        avg_rev = net_revenue / range_days
        avg_orders = total_orders / range_days
        avg_row = (
            f'<div style="font-size:12px;color:var(--muted);margin-top:4px;">'
            f'Daily avg: {fmt_currency(avg_rev)} &middot; {avg_orders:.0f} orders/day'
            f'</div>'
        )

    header = (
        f'<div style="text-align:center;margin-bottom:20px;">'
        f'{logo_html}'
        f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:2px;color:var(--muted);margin-bottom:6px;">{dashboard_type}</div>'
        f'<div style="font-size:26px;font-weight:700;color:var(--livite-green);">{_safe(day_name)}</div>'
        f'<div style="font-size:14px;color:var(--muted);margin-top:2px;">{_safe(date_display)}</div>'
        f'<div style="font-size:13px;color:var(--muted);margin-top:4px;">{subtitle}</div>'
        f'{avg_row}'
        f'</div>'
    )

    # Daily breakdown table for range mode
    daily_summary = metrics.get("daily_summary", [])
    if is_range and daily_summary:
        rows = []
        for ds in daily_summary:
            day_label = ds.get("day_of_week", "")[:3]
            d_str = ds.get("date_str", "")
            # Format date as MM/DD
            if len(d_str) == 8:
                d_display = f"{d_str[4:6]}/{d_str[6:8]}"
            else:
                d_display = d_str
            d_rev = ds.get("revenue", 0)
            d_orders = ds.get("orders", 0)
            d_check = ds.get("avg_check", 0)
            d_labor_pct = ds.get("labor_pct", 0)
            rows.append(
                f'<tr>'
                f'<td style="font-weight:600;">{_safe(day_label)} {d_display}</td>'
                f'<td class="mono" style="text-align:right;">{fmt_currency(d_rev)}</td>'
                f'<td class="mono" style="text-align:right;">{fmt_num(d_orders)}</td>'
                f'<td class="mono" style="text-align:right;">{fmt_currency(d_check)}</td>'
                f'<td class="mono" style="text-align:right;">{fmt_pct(d_labor_pct)}</td>'
                f'</tr>'
            )
        # Totals row
        tot_rev = sum(ds.get("revenue", 0) for ds in daily_summary)
        tot_orders = sum(ds.get("orders", 0) for ds in daily_summary)
        tot_check = tot_rev / tot_orders if tot_orders > 0 else 0
        rows.append(
            f'<tr style="border-top:2px solid var(--border);font-weight:700;">'
            f'<td>TOTAL</td>'
            f'<td class="mono" style="text-align:right;">{fmt_currency(tot_rev)}</td>'
            f'<td class="mono" style="text-align:right;">{fmt_num(tot_orders)}</td>'
            f'<td class="mono" style="text-align:right;">{fmt_currency(tot_check)}</td>'
            f'<td></td>'
            f'</tr>'
        )
        table_rows = "\n".join(rows)
        header += (
            f'<div class="card" style="margin-top:16px;">'
            f'<div style="font-size:11px;color:var(--muted);margin-bottom:6px;">DAILY BREAKDOWN</div>'
            f'<div style="overflow-x:auto;">'
            f'<table class="data-table" style="width:100%;font-size:12px;">'
            f'<thead><tr>'
            f'<th style="text-align:left;">Day</th>'
            f'<th style="text-align:right;">Revenue</th>'
            f'<th style="text-align:right;">Orders</th>'
            f'<th style="text-align:right;">Avg Check</th>'
            f'<th style="text-align:right;">Labor %</th>'
            f'</tr></thead>'
            f'<tbody>{table_rows}</tbody>'
            f'</table></div></div>'
        )

    return header


# ---------------------------------------------------------------------------
# Section 0b: Comparison Bar
# ---------------------------------------------------------------------------

def render_comparison_bar(comparisons: dict | None) -> str:
    if not comparisons:
        return ""
    deltas = _g(comparisons, "deltas")
    if not deltas:
        return ""

    # Toggle buttons
    toggle_html = (
        '<div style="margin-bottom:4px;display:flex;gap:0;">'
        '<button class="period-btn active" data-period="wow" onclick="setPeriod(\'wow\')" '
        'style="padding:5px 14px;border:1px solid var(--border);border-radius:4px 0 0 4px;background:var(--livite-green);color:var(--livite-cream);font-size:11px;cursor:pointer;font-weight:600;">WoW</button>'
        '<button class="period-btn toggle-pulse" data-period="yoy" onclick="setPeriod(\'yoy\')" '
        'style="padding:5px 14px;border:1px solid var(--border);border-left:none;background:var(--surface2);color:var(--muted);font-size:11px;cursor:pointer;">YoY</button>'
        '<button class="period-btn toggle-pulse" data-period="swly" onclick="setPeriod(\'swly\')" '
        'style="padding:5px 14px;border:1px solid var(--border);border-left:none;border-radius:0 4px 4px 0;background:var(--surface2);color:var(--muted);font-size:11px;cursor:pointer;">SWLY</button>'
        '</div>'
        '<div class="toggle-hint">Click to compare periods</div>'
    )

    # Summary for all periods
    parts = []
    for period, label in [("wow", "WoW"), ("yoy", "YoY"), ("swly", "SWLY")]:
        rev_delta = _delta_html(deltas, "revenue", period)
        order_delta = _delta_html(deltas, "orders", period)
        guest_delta = _delta_html(deltas, "guests", period)
        display = "block" if period == "wow" else "none"
        parts.append(
            f'<div class="period-detail" data-period-detail="{period}" style="display:{display};font-size:12px;">'
            f'<span style="font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-right:6px;">{label}</span>'
            f'Rev {rev_delta} &nbsp; Orders {order_delta} &nbsp; Guests {guest_delta}'
            f'</div>'
        )
    if not parts:
        return ""
    return f'{toggle_html}<div style="margin-bottom:16px;">{"".join(parts)}</div>'


# ---------------------------------------------------------------------------
# Section 0c: Anomaly Alerts
# ---------------------------------------------------------------------------

def render_anomalies(anomalies: list | None) -> str:
    if not anomalies:
        return ""
    parts = []
    for a in anomalies:
        sev = a.get("severity", "amber")
        msg = a.get("message", "")
        a_type = a.get("type", "")
        parts.append(render_insight(msg, severity=sev if sev == "red" else "amber", tag=a_type.replace("_", " ").upper()))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Section 1: Executive Summary
# ---------------------------------------------------------------------------

def render_executive_summary(metrics: dict, comparisons: dict | None) -> str:
    rev = _g(metrics, "revenue")
    labor = _g(metrics, "labor")
    deltas = _g(comparisons, "deltas") if comparisons else None

    if not rev:
        return ""

    is_range = metrics.get("is_range", False)
    range_days = max(metrics.get("range_days", 1), 1)

    def _multi_delta(metric_key, direction="up"):
        """Build period_deltas dict for all comparison periods."""
        if not deltas:
            return None
        pd = {}
        for p in ("wow", "yoy", "swly"):
            pd[p] = _delta_html(deltas, metric_key, p, direction)
        if any(pd.values()):
            return pd
        return None

    def _avg(val, is_currency=True):
        """Compute daily avg string for range mode."""
        if not is_range or val is None:
            return ""
        try:
            avg = float(val) / range_days
        except (TypeError, ValueError):
            return ""
        return fmt_currency(avg) if is_currency else fmt_num(avg)

    net = _g(rev, "toast_total")
    gross = _g(rev, "gross_total")
    orders = _g(rev, "total_orders")
    guests = _g(rev, "total_guests")
    rpg = _g(rev, "rev_per_guest")
    lab_total = _g(labor, "total_labor")
    lab_pct = _g(labor, "labor_pct")
    rplh = _g(labor, "rev_per_labor_hr")
    disc = round(_g(rev, "total_discounts", default=0) / max(_g(rev, "toast_total", default=1), 1) * 100, 1)

    # Food cost + prime cost
    fc = metrics.get("food_cost", 0) or 0
    fc_pct = metrics.get("food_cost_pct", 0) or 0
    fc_method = metrics.get("food_cost_method", "estimate")
    fc_coverage = metrics.get("food_cost_coverage", 0) or 0
    pc = metrics.get("prime_cost", 0) or 0
    pc_pct = metrics.get("prime_cost_pct", 0) or 0

    if fc_method == "theoretical":
        fc_sub = "%d%% recipe-costed" % int(fc_coverage)
    else:
        fc_sub = "est. 35%%"

    stats = [
        render_stat("Net Revenue", fmt_currency(net),
                     period_deltas=_multi_delta("revenue"),
                     avg_value=_avg(net)),
        render_stat("Gross Revenue", fmt_currency(gross),
                     avg_value=_avg(gross)),
        render_stat("Total Orders", fmt_num(orders),
                     period_deltas=_multi_delta("orders"),
                     avg_value=_avg(orders, False)),
        render_stat("Avg Check", fmt_currency(_g(rev, "avg_check")),
                     period_deltas=_multi_delta("avg_check")),
        render_stat("Total Guests", fmt_num(guests),
                     period_deltas=_multi_delta("guests"),
                     avg_value=_avg(guests, False)),
        render_stat("Rev / Guest", fmt_currency(rpg)),
        render_stat("Labor $", fmt_currency(lab_total),
                     period_deltas=_multi_delta("labor_total", "down"),
                     avg_value=_avg(lab_total)),
        render_stat("Labor %", fmt_pct(lab_pct),
                     color="var(--red)" if (lab_pct or 0) > 30 else None,
                     period_deltas=_multi_delta("labor_pct", "down")),
        render_stat("Food Cost", fmt_currency(fc),
                     subtitle=fc_sub,
                     avg_value=_avg(fc)),
        render_stat("Food %", fmt_pct(fc_pct),
                     color="var(--red)" if fc_pct > 35 else None),
        render_stat("Prime Cost", fmt_currency(pc),
                     subtitle="Food + Labor",
                     avg_value=_avg(pc)),
        render_stat("Prime %", fmt_pct(pc_pct),
                     color="var(--red)" if pc_pct > 60 else None),
        render_stat("Rev / Labor Hr", fmt_currency(rplh)),
        render_stat("Discount Rate", fmt_pct(disc)),
    ]

    # Range mode: add Total / Daily Avg toggle button
    toggle = ""
    if is_range:
        toggle = (
            '<div style="display:flex;justify-content:flex-end;margin-bottom:8px;">'
            '<div style="display:inline-flex;border:1px solid var(--border);border-radius:6px;overflow:hidden;font-size:11px;">'
            '<button id="btnTotal" onclick="setRangeMode(\'total\')" '
            'style="padding:5px 12px;border:none;background:var(--livite-green);color:#fff;cursor:pointer;font-family:inherit;font-weight:600;">Total</button>'
            '<button id="btnAvg" class="toggle-pulse" onclick="setRangeMode(\'avg\')" '
            'style="padding:5px 12px;border:none;background:var(--surface2);color:var(--muted);cursor:pointer;font-family:inherit;">Daily Avg</button>'
            '</div></div>'
            '<div class="toggle-hint" style="text-align:right;">Click to switch views</div>'
            '<script>'
            'function setRangeMode(mode){'
            'document.querySelectorAll(".range-toggle").forEach(function(el){'
            'var t=el.getAttribute("data-total"),a=el.getAttribute("data-avg");'
            'if(t&&a){el.textContent=mode==="avg"?a:t;}'
            '});'
            'var bt=document.getElementById("btnTotal"),ba=document.getElementById("btnAvg");'
            'if(mode==="avg"){'
            'bt.style.background="var(--surface2)";bt.style.color="var(--muted)";'
            'ba.style.background="var(--livite-green)";ba.style.color="#fff";'
            '}else{'
            'bt.style.background="var(--livite-green)";bt.style.color="#fff";'
            'ba.style.background="var(--surface2)";ba.style.color="var(--muted)";'
            '}}'
            '</script>'
        )

    return render_card("Executive Summary", toggle + render_stat_grid(stats))


# ---------------------------------------------------------------------------
# Section 2: Revenue & Hourly Trends
# ---------------------------------------------------------------------------

def render_revenue_channels(metrics: dict, comparisons: dict | None) -> str:
    rev = _g(metrics, "revenue")
    if not rev:
        return ""

    parts = []

    # ── Walk-In / 3P / Online PIE CHART ──
    w3o = _g(rev, "walkin_3p_online", default={})
    if w3o:
        pie_labels = []
        pie_values = []
        pie_colors = []
        color_map = {"Walk-In": "#4a7c1f", "3P": "#9b72c4", "Online": "#4a9cd8"}
        for group_name in ["Walk-In", "3P", "Online"]:
            gd = w3o.get(group_name, {})
            r = gd.get("revenue", 0)
            if r > 0:
                pie_labels.append(group_name)
                pie_values.append(round(r, 2))
                pie_colors.append(color_map.get(group_name, "#999"))
        if pie_values:
            parts.append('<div style="margin-bottom:16px;">')
            parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">REVENUE BY SOURCE</div>')
            parts.append(render_chartjs_pie(pie_labels, pie_values, pie_colors, height=220, doughnut=True,
                                             center_text=fmt_currency(_g(rev, "toast_total", default=0))))
            # Summary badges below pie
            badge_parts = []
            for group_name in ["Walk-In", "3P", "Online"]:
                gd = w3o.get(group_name, {})
                r = gd.get("revenue", 0)
                o = gd.get("orders", 0)
                p = gd.get("pct", 0)
                badge_parts.append(
                    f'<span style="font-size:11px;margin-right:16px;">'
                    f'<span style="color:{color_map.get(group_name, "#999")};">\u25CF</span> '
                    f'{group_name}: {fmt_currency(r)} ({fmt_pct(p)}) &middot; {fmt_num(o)} orders'
                    f'</span>'
                )
            parts.append(f'<div style="margin:8px 0 12px;text-align:center;">{"".join(badge_parts)}</div>')
            parts.append('</div>')

    # ── Channel detail bars (Chart.js horizontal) ──
    channels = _g(rev, "channels", default={})
    if channels:
        sorted_channels = sorted(channels.items(), key=lambda x: x[1].get("revenue", 0), reverse=True)
        ch_labels = [ch_name for ch_name, _ in sorted_channels]
        ch_values = [round(ch_data.get("revenue", 0), 2) for _, ch_data in sorted_channels]
        if ch_values:
            parts.append('<div style="margin-bottom:16px;">')
            parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">CHANNEL BREAKDOWN</div>')
            ch_height = max(150, len(ch_labels) * 32 + 40)
            parts.append(render_chartjs_bar(ch_labels, [{"label": "Revenue", "data": ch_values, "color": "#2db88a"}],
                                             height=ch_height, horizontal=True, dollar=True))
            parts.append('</div>')

    # ── 15-Minute Revenue — Highlights + Collapsible Detail ──
    quarter_hourly = _gl(rev, "quarter_hourly", default=[])
    peak_quarter = _g(rev, "peak_quarter", default={})
    slot_4wra = _g(rev, "quarter_hourly_4wra", default={})
    wra_weeks = _g(rev, "quarter_hourly_4wra_weeks", default=0)
    if quarter_hourly:
        quarter_hourly_sorted = sorted(quarter_hourly, key=lambda q: (q.get("hour", 0), q.get("quarter", 0)))
        active_slots = [q for q in quarter_hourly_sorted if q.get("revenue", 0) > 0 or q.get("orders", 0) > 0]
        if active_slots:
            total_rev = _g(rev, "toast_total", default=1)

            # ── Highlight callouts ──
            # Peak slot
            if peak_quarter and peak_quarter.get("label"):
                pk_rev = peak_quarter.get("revenue", 0)
                pk_orders = peak_quarter.get("orders", 0)
                pk_pct = round(pk_rev / max(total_rev, 1) * 100, 1)
                pk_key = peak_quarter.get("label", "")
                pk_h = int(pk_key.split(":")[0]) if ":" in pk_key else 0
                pk_ap = "AM" if pk_h < 12 else "PM"
                pk_dh = pk_h if pk_h <= 12 else pk_h - 12
                if pk_dh == 0:
                    pk_dh = 12
                pk_label = f"{pk_dh}:{pk_key.split(':')[1]}{pk_ap}" if ":" in pk_key else pk_key
                pk_vs = ""
                pk_wra = slot_4wra.get(pk_key, {}).get("avg_revenue", 0)
                if pk_wra > 0:
                    pk_delta = round((pk_rev - pk_wra) / pk_wra * 100, 0)
                    direction = "above" if pk_delta > 0 else "below"
                    pk_vs = f" &mdash; {abs(pk_delta):.0f}% {direction} 4wk avg"
                parts.append(
                    f'<div class="callout win">'
                    f'<div class="body">'
                    f'<div class="headline">Peak 15 min: {pk_label}</div>'
                    f'<div class="detail">{fmt_currency(pk_rev)} revenue, {pk_orders} orders ({pk_pct}% of daily total){pk_vs}</div>'
                    f'</div></div>'
                )

            # Large orders (catering flags)
            all_large = []
            for q in active_slots:
                for lo in q.get("large_orders", []):
                    h = q.get("hour", 0)
                    qtr = q.get("quarter", 0)
                    am_pm = "AM" if h < 12 else "PM"
                    disp_h = h if h <= 12 else h - 12
                    if disp_h == 0:
                        disp_h = 12
                    t_label = f"{disp_h}:{qtr:02d}{am_pm}"
                    all_large.append({"time": t_label, "amount": lo.get("amount", 0), "channel": lo.get("channel", "")})
            if all_large:
                all_large.sort(key=lambda x: x["amount"], reverse=True)
                lo_lines = []
                for lo in all_large[:5]:
                    lo_lines.append(f"{fmt_currency(lo['amount'])} at {lo['time']} ({lo['channel']})")
                parts.append(
                    f'<div class="callout warn">'
                    f'<div class="body">'
                    f'<div class="headline">{len(all_large)} Large Order{"s" if len(all_large) > 1 else ""} Detected</div>'
                    f'<div class="detail">{"  &bull;  ".join(lo_lines)}</div>'
                    f'</div></div>'
                )

            # Dead zones (slots with 4WRA revenue but $0 today)
            if wra_weeks > 0:
                dead_zones = []
                for q in quarter_hourly_sorted:
                    slot_key = q.get("label", "")
                    wra = slot_4wra.get(slot_key, {})
                    if wra.get("avg_revenue", 0) > 20 and q.get("revenue", 0) == 0:
                        h = q.get("hour", 0)
                        qtr = q.get("quarter", 0)
                        am_pm = "AM" if h < 12 else "PM"
                        disp_h = h if h <= 12 else h - 12
                        if disp_h == 0:
                            disp_h = 12
                        dead_zones.append(f"{disp_h}:{qtr:02d}{am_pm} (avg {fmt_currency(wra['avg_revenue'])})")
                if dead_zones:
                    parts.append(
                        f'<div class="callout info">'
                        f'<div class="body">'
                        f'<div class="headline">{len(dead_zones)} Missed Revenue Window{"s" if len(dead_zones) > 1 else ""}</div>'
                        f'<div class="detail">Slots usually active but $0 today: {", ".join(dead_zones[:6])}</div>'
                        f'</div></div>'
                    )

            # Rush window detection — slots above average rev qualify
            total_rev = sum(q.get("revenue", 0) for q in active_slots)
            slots_with_rev = sum(1 for q in active_slots if q.get("revenue", 0) > 0)
            rush_threshold = (total_rev / max(slots_with_rev, 1)) * 0.8  # 80% of avg
            rush_best = {"start": None, "end": None, "rev": 0, "orders": 0, "slots": 0}
            current_run = {"start": None, "rev": 0, "orders": 0, "slots": 0}
            for q in active_slots:
                q_rev = q.get("revenue", 0)
                if q_rev >= rush_threshold:
                    if current_run["start"] is None:
                        current_run["start"] = q
                    current_run["rev"] += q_rev
                    current_run["orders"] += q.get("orders", 0)
                    current_run["slots"] += 1
                    current_run["end"] = q
                else:
                    if current_run["rev"] > rush_best["rev"]:
                        rush_best = dict(current_run)
                    current_run = {"start": None, "rev": 0, "orders": 0, "slots": 0, "end": None}
            if current_run["rev"] > rush_best["rev"]:
                rush_best = dict(current_run)
            if rush_best["start"] and rush_best["slots"] >= 4:
                def _fmt_slot(slot):
                    sh = slot.get("hour", 0)
                    sq = slot.get("quarter", 0)
                    sap = "AM" if sh < 12 else "PM"
                    sdh = sh if sh <= 12 else sh - 12
                    if sdh == 0:
                        sdh = 12
                    return f"{sdh}:{sq:02d}{sap}"
                rush_dur = rush_best["slots"] * 15
                parts.append(
                    f'<div class="callout win">'
                    f'<div class="body">'
                    f'<div class="headline">Main Rush: {_fmt_slot(rush_best["start"])} \u2013 {_fmt_slot(rush_best["end"])}</div>'
                    f'<div class="detail">{rush_dur} minutes, {fmt_currency(rush_best["rev"])} revenue, {rush_best["orders"]} orders</div>'
                    f'</div></div>'
                )

            # ── Chart.js bar+line combo: today's revenue (bars) + 4WRA (line) ──
            slot_labels = []
            today_data = []
            wra_data = []
            for q in active_slots:
                h = q.get("hour", 0)
                qtr = q.get("quarter", 0)
                am_pm = "a" if h < 12 else "p"
                disp_h = h if h <= 12 else h - 12
                if disp_h == 0:
                    disp_h = 12
                slot_labels.append(f"{disp_h}:{qtr:02d}{am_pm}" if qtr == 0 else f":{qtr:02d}")
                today_data.append(round(q.get("revenue", 0), 2))
                slot_key = q.get("label", "")
                wra_data.append(round(slot_4wra.get(slot_key, {}).get("avg_revenue", 0), 2))

            # In range mode, show daily averages instead of totals
            _is_range = metrics.get("is_range", False)
            _rdays = max(metrics.get("range_days", 1), 1)
            if _is_range:
                today_data = [round(v / _rdays, 2) for v in today_data]
                bar_label = "Daily Avg"
            else:
                bar_label = "Today"

            chart_ds = [
                {"label": bar_label, "data": today_data, "color": "#8cb82e"},
            ]
            if any(v > 0 for v in wra_data) and not _is_range:
                chart_ds.append({"label": "4-Wk Avg", "data": wra_data, "color": "#475417",
                                  "type": "line", "borderWidth": 2, "pointRadius": 0})
            parts.append(render_chartjs_bar(slot_labels, chart_ds, height=280, dollar=True, show_legend=True))

    # ── Hourly Revenue Bars (Chart.js) ──
    hourly = _gl(rev, "hourly", default=[])
    if hourly:
        active_hourly = [e for e in hourly if e.get("revenue", 0) > 0]
        if active_hourly:
            _is_range = metrics.get("is_range", False)
            _rdays = max(metrics.get("range_days", 1), 1)
            hr_labels = []
            hr_values = []
            for entry in active_hourly:
                h = entry.get("hour", 0)
                am_pm = "a" if h < 12 else "p"
                disp_h = h if h <= 12 else h - 12
                if disp_h == 0:
                    disp_h = 12
                hr_labels.append(f"{disp_h}{am_pm}")
                val = round(entry.get("revenue", 0), 2)
                hr_values.append(round(val / _rdays, 2) if _is_range else val)
            hr_title = "HOURLY REVENUE (DAILY AVG)" if _is_range else "HOURLY REVENUE"
            parts.append('<div style="margin-bottom:16px;">')
            parts.append(f'<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">{hr_title}</div>')
            parts.append(render_chartjs_bar(hr_labels, [{"label": "Revenue", "data": hr_values, "color": "#2db88a"}],
                                             height=180, dollar=True))
            parts.append('</div>')

    # ── Hourly Channel Mix — Chart.js Stacked Bar ──
    hourly_by_channel = _gl(rev, "hourly_by_channel", default=[])
    if hourly_by_channel:
        ch_colors_map = {"Walk-In": "#4a7c1f", "3P": "#9b72c4", "Online": "#4a9cd8"}
        ch_names = ["Walk-In", "3P", "Online"]
        active_hours = [e for e in hourly_by_channel
                        if sum(e.get("groups", {}).get(gn, {}).get("revenue", 0) for gn in ch_names) > 0]
        if active_hours:
            hcm_labels = []
            for entry in active_hours:
                h = entry.get("hour", 0)
                am_pm = "a" if h < 12 else "p"
                disp_h = h if h <= 12 else h - 12
                if disp_h == 0:
                    disp_h = 12
                hcm_labels.append(f"{disp_h}{am_pm}")
            hcm_datasets = []
            for gn in ch_names:
                data = [round(e.get("groups", {}).get(gn, {}).get("revenue", 0), 2) for e in active_hours]
                if any(v > 0 for v in data):
                    hcm_datasets.append({"label": gn, "data": data, "color": ch_colors_map[gn]})
            if hcm_datasets:
                parts.append('<div style="margin-bottom:16px;">')
                parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">HOURLY CHANNEL MIX (REVENUE)</div>')
                parts.append(render_chartjs_bar(hcm_labels, hcm_datasets, height=200,
                                                 stacked=True, dollar=True, show_legend=True))
                parts.append('</div>')

    content = "\n".join(parts)
    return render_card("Revenue & Hourly Trends", content,
                       subtitle="Channel mix, 15-min heatmap, and hourly trends")


# ---------------------------------------------------------------------------
# Section 3: Menu Intelligence (was Order Intelligence)
# ---------------------------------------------------------------------------

def render_order_intelligence(metrics: dict) -> str:
    orders = _g(metrics, "orders")
    if not orders:
        return ""

    parts = []

    # ── Top Items by Revenue with menu breakdown ──
    top_items = _gl(orders, "top_items_by_revenue", default=[])
    items_menu = _gl(orders, "items_by_menu_breakdown", default=[])
    menu_map = {entry["item"]: entry.get("menu_split", {}) for entry in items_menu}

    if top_items:
        max_rev = max((it.get("revenue", 0) for it in top_items), default=1)
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">TOP ITEMS (COMBINED ACROSS ALL MENUS)</div>')
        for it in top_items[:15]:
            item_name = it.get("item", "")
            it_rev = it.get("revenue", 0)
            it_qty = it.get("qty", 0)
            bar_text = f"{fmt_currency(it_rev)} / {it_qty} sold"

            # Menu breakdown badges
            ms = menu_map.get(item_name, {})
            menu_badges = ""
            if len(ms) > 1:
                total_qty = sum(v.get("qty", 0) for v in ms.values())
                badge_parts = []
                for menu_name, menu_data in sorted(ms.items(), key=lambda x: x[1].get("qty", 0), reverse=True):
                    m_qty = menu_data.get("qty", 0)
                    m_pct = round(m_qty / max(total_qty, 1) * 100, 0)
                    short_name = menu_name.replace("3rd Party Ordering", "3P").replace("Livite Menu", "Livite").replace("Catering Menu", "Catering")
                    badge_parts.append(f'{short_name} {m_pct:.0f}%')
                menu_badges = f' <span style="font-size:9px;color:var(--muted);">({" | ".join(badge_parts)})</span>'

            parts.append(
                f'<div class="bar-h">'
                f'<span class="label" style="width:140px;" title="{_safe(item_name)}">{_safe(item_name[:20])}</span>'
                f'<div class="bar-track">'
                f'<div class="bar-fill" style="width:{min(100, it_rev / max(max_rev, 1) * 100):.1f}%;background:var(--cyan);opacity:0.7;">'
                f'<span class="bar-text">{bar_text}</span>'
                f'</div></div>'
                f'<span style="font-size:9px;color:var(--muted);width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{menu_badges}</span>'
                f'</div>'
            )
        parts.append('</div>')

    # ── Top Items: Revenue vs Units Toggle ──
    top_by_qty = _gl(orders, "top_items_by_qty", default=[])
    if top_items or top_by_qty:
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">')
        parts.append('<span style="font-size:11px;color:var(--muted);">TOP ITEMS</span>')
        parts.append(
            '<div style="display:inline-flex;border:1px solid var(--border);border-radius:14px;overflow:hidden;font-size:10px;">'
            '<button onclick="document.getElementById(\'items-rev\').style.display=\'block\';'
            'document.getElementById(\'items-qty\').style.display=\'none\';'
            'this.style.background=\'var(--livite-lime)\';this.style.color=\'#2d2a24\';this.style.fontWeight=\'600\';'
            'this.nextElementSibling.style.background=\'transparent\';this.nextElementSibling.style.color=\'var(--muted)\';this.nextElementSibling.style.fontWeight=\'normal\';" '
            'style="padding:3px 10px;border:none;cursor:pointer;background:var(--livite-lime);color:#2d2a24;font-weight:600;">'
            'By Revenue</button>'
            '<button class="toggle-pulse" onclick="document.getElementById(\'items-qty\').style.display=\'block\';'
            'document.getElementById(\'items-rev\').style.display=\'none\';'
            'this.style.background=\'var(--livite-lime)\';this.style.color=\'#2d2a24\';this.style.fontWeight=\'600\';'
            'this.previousElementSibling.style.background=\'transparent\';this.previousElementSibling.style.color=\'var(--muted)\';this.previousElementSibling.style.fontWeight=\'normal\';" '
            'style="padding:3px 10px;border:none;cursor:pointer;background:transparent;color:var(--muted);">'
            'By Units</button></div>'
        )
        parts.append('<div class="toggle-hint">Click to switch views</div>')
        parts.append('</div>')

        # Revenue panel (Chart.js horizontal bar)
        parts.append('<div id="items-rev">')
        if top_items:
            rev_labels = [it.get("item", "") for it in top_items[:15]]
            rev_values = [round(it.get("revenue", 0), 2) for it in top_items[:15]]
            parts.append(render_chartjs_bar(rev_labels, [{"label": "Revenue", "data": rev_values, "color": "#8cb82e"}],
                                             height=max(250, len(rev_labels) * 28 + 40), horizontal=True, dollar=True))
        parts.append('</div>')

        # Units panel (Chart.js horizontal bar, hidden by default)
        parts.append('<div id="items-qty" style="display:none;">')
        if top_by_qty:
            qty_labels = [it.get("item", "") for it in top_by_qty[:15]]
            qty_values = [it.get("qty", 0) for it in top_by_qty[:15]]
            parts.append(render_chartjs_bar(qty_labels, [{"label": "Units Sold", "data": qty_values, "color": "#2db88a"}],
                                             height=max(250, len(qty_labels) * 28 + 40), horizontal=True))
        parts.append('</div>')
        parts.append('</div>')

    # ── First Party vs Third Party Toggle ──
    fp_items = _gl(orders, "first_party_items", default=[])
    tp_items = _gl(orders, "third_party_items", default=[])
    if fp_items or tp_items:
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">')
        parts.append('<span style="font-size:11px;color:var(--muted);">TOP ITEMS BY CHANNEL</span>')
        parts.append(
            '<div style="display:inline-flex;border:1px solid var(--border);border-radius:14px;overflow:hidden;font-size:10px;">'
            '<button class="item-toggle-btn active" data-target="fp-items" onclick="toggleItemView(\'fp-items\')" '
            'style="padding:3px 10px;border:none;cursor:pointer;background:var(--livite-lime);color:#2d2a24;font-weight:600;">First Party</button>'
            '<button class="item-toggle-btn toggle-pulse" data-target="tp-items" onclick="toggleItemView(\'tp-items\')" '
            'style="padding:3px 10px;border:none;cursor:pointer;background:transparent;color:var(--muted);">Third Party</button></div>'
        )
        parts.append('<div class="toggle-hint">Click to switch channels</div>')
        parts.append('</div>')
        # First Party panel (Chart.js horizontal)
        parts.append('<div id="fp-items" class="item-panel">')
        if fp_items:
            fp_labels = [it.get("item", "") for it in fp_items[:12]]
            fp_values = [round(it.get("revenue", 0), 2) for it in fp_items[:12]]
            parts.append(render_chartjs_bar(fp_labels, [{"label": "Revenue", "data": fp_values, "color": "#4a7c1f"}],
                                             height=max(200, len(fp_labels) * 28 + 40), horizontal=True, dollar=True))
        else:
            parts.append('<div style="font-size:11px;color:var(--muted);">No first party orders</div>')
        parts.append('</div>')
        # Third Party panel (Chart.js horizontal)
        parts.append('<div id="tp-items" class="item-panel" style="display:none;">')
        if tp_items:
            tp_labels = [it.get("item", "") for it in tp_items[:12]]
            tp_values = [round(it.get("revenue", 0), 2) for it in tp_items[:12]]
            parts.append(render_chartjs_bar(tp_labels, [{"label": "Revenue", "data": tp_values, "color": "#9b72c4"}],
                                             height=max(200, len(tp_labels) * 28 + 40), horizontal=True, dollar=True))
        else:
            parts.append('<div style="font-size:11px;color:var(--muted);">No third party orders</div>')
        parts.append('</div>')
        parts.append('</div>')

    # ── Menu Group Mix (Chart.js pie chart) ──
    menu_group_mix = _gl(orders, "menu_group_mix", default=[])
    if menu_group_mix:
        mg_labels = [g.get("group", "") for g in menu_group_mix[:10]]
        mg_values = [round(g.get("revenue", 0), 2) for g in menu_group_mix[:10]]
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">MENU GROUP MIX</div>')
        mg_total = sum(mg_values)
        mg_center = f"${mg_total:,.0f}" if mg_total else ""
        parts.append(render_chartjs_pie(mg_labels, mg_values, height=260, doughnut=True, center_text=mg_center))
        parts.append('</div>')

    # ── Catering Breakout ──
    catering = _g(orders, "catering", default={})
    if isinstance(catering, dict) and catering.get("revenue", 0) > 0:
        cat_rev = catering.get("revenue", 0)
        cat_qty = catering.get("qty", 0)
        cat_orders = catering.get("orders", 0)
        cat_pct = catering.get("pct_of_total", 0)
        cat_top = catering.get("top_items", [])

        parts.append('<div style="background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:16px;">')
        parts.append(f'<div style="font-size:11px;color:var(--muted);margin-bottom:8px;">CATERING BREAKOUT {render_badge(fmt_pct(cat_pct) + " of total", "a")}</div>')
        cat_stats = render_stat_grid([
            render_stat("Revenue", fmt_currency(cat_rev), color="var(--amber)"),
            render_stat("Items", fmt_num(cat_qty)),
            render_stat("Orders", fmt_num(cat_orders)),
        ])
        parts.append(cat_stats)
        if cat_top:
            parts.append('<div style="font-size:10px;color:var(--muted);margin-top:8px;">Top Catering Items:</div>')
            for ct in cat_top[:5]:
                parts.append(f'<div style="font-size:11px;margin:2px 0;">{_safe(ct.get("item", ""))} &mdash; {fmt_currency(ct.get("revenue", 0))} ({ct.get("qty", 0)} sold)</div>')
        parts.append('</div>')

    # ── Uber vs Walk-In Side-by-Side ──
    baskets = _g(metrics, "baskets")
    uber_walkin = _g(baskets, "uber_vs_walkin", default={}) if baskets else {}
    uber_top = uber_walkin.get("uber_top_items", [])
    walkin_top = uber_walkin.get("walkin_top_items", [])
    if uber_top and walkin_top:
        parts.append('<div class="grid-2" style="margin-bottom:16px;">')
        # Uber side
        parts.append('<div style="background:var(--surface2);border-radius:8px;padding:12px;">')
        parts.append(f'<div style="font-size:11px;color:var(--purple);margin-bottom:6px;">UBER EATS TOP ITEMS</div>')
        for it in uber_top[:8]:
            parts.append(f'<div style="font-size:11px;display:flex;justify-content:space-between;padding:2px 0;">'
                         f'<span>{_safe(it.get("item", ""))}</span>'
                         f'<span class="mono" style="font-size:10px;color:var(--muted);">{it.get("qty", 0)}</span>'
                         f'</div>')
        parts.append('</div>')
        # Walk-In side
        parts.append('<div style="background:var(--surface2);border-radius:8px;padding:12px;">')
        parts.append(f'<div style="font-size:11px;color:var(--green);margin-bottom:6px;">WALK-IN TOP ITEMS</div>')
        for it in walkin_top[:8]:
            parts.append(f'<div style="font-size:11px;display:flex;justify-content:space-between;padding:2px 0;">'
                         f'<span>{_safe(it.get("item", ""))}</span>'
                         f'<span class="mono" style="font-size:10px;color:var(--muted);">{it.get("qty", 0)}</span>'
                         f'</div>')
        parts.append('</div>')
        parts.append('</div>')

    # ── Discount Analysis ──
    disc_by_reason = _gl(orders, "discount_by_reason", default=[])
    disc_by_channel = _gl(orders, "discount_by_channel", default=[])
    total_disc = sum(d.get("total_discount", 0) for d in disc_by_reason)
    uber_bogo = _g(orders, "uber_bogo_impact", default={})
    if total_disc > 0 or (uber_bogo and uber_bogo.get("discount_total", 0) > 0):
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">DISCOUNT ANALYSIS</div>')

        # Explanation callout
        parts.append(render_insight(
            '"Gross" is menu price before discounts. "Discount" is the total reduced '
            '(Uber BOGO, employee meals, manager comps, loyalty). "Net" is what Livite receives. '
            'A discount rate above 10% warrants investigation.',
            severity="blue", tag="HOW TO READ THIS"
        ))

        # Discount by channel table (Phase 0I)
        if disc_by_channel:
            dc_rows = []
            for dc in disc_by_channel:
                dc_rows.append([
                    _safe(dc.get("channel", "Unknown")),
                    fmt_currency(dc.get("gross", 0)),
                    fmt_currency(dc.get("discount", 0)),
                    fmt_currency(dc.get("net", 0)),
                    fmt_pct(dc.get("disc_rate", 0)),
                ])
            parts.append(render_table(
                ["Platform", "Gross", "Discount", "Net", "Rate"],
                dc_rows, [1, 2, 3, 4]
            ))

        # Discount by reason (Chart.js horizontal bar)
        if disc_by_reason:
            dr_labels = [d.get("reason", "Unknown") for d in disc_by_reason[:8]]
            dr_values = [round(d.get("total_discount", 0), 2) for d in disc_by_reason[:8]]
            parts.append('<div style="margin-top:12px;">')
            parts.append('<div style="font-size:10px;color:var(--muted);margin-bottom:4px;">BY REASON</div>')
            parts.append(render_chartjs_bar(dr_labels, [{"label": "Discount", "data": dr_values, "color": "#e86040"}],
                                             height=max(150, len(dr_labels) * 30 + 40), horizontal=True, dollar=True))
            parts.append('</div>')

        if uber_bogo and uber_bogo.get("discount_total", 0) > 0:
            ub_disc = uber_bogo.get("discount_total", 0)
            ub_annual = uber_bogo.get("annualized", 0)
            parts.append(render_insight(
                f'Uber BOGO discounts today: <span class="hl">{fmt_currency(ub_disc)}</span> '
                f'&mdash; Annualized: <span class="hl">{fmt_currency(ub_annual)}</span>',
                severity="amber", tag="UBER BOGO"
            ))
        parts.append('</div>')

    # ── Void Analysis ──
    voids = _g(orders, "void_analysis", default={})
    if isinstance(voids, dict) and voids.get("void_qty", 0) > 0:
        parts.append(render_insight(
            f'Voids: {voids.get("void_qty", 0)} items voided ({fmt_currency(voids.get("void_amount", 0))})',
            severity="amber", tag="VOIDS"
        ))

    # ── Single Item Rate ──
    single_rate = _g(orders, "single_item_rate", default=0)
    if single_rate:
        single_color = "red" if float(single_rate) > 50 else "amber"
        parts.append(render_insight(
            f'Single-item order rate: <span class="hl">{fmt_pct(single_rate)}</span> '
            f'({fmt_num(_g(orders, "total_items_sold"))} total items, '
            f'{fmt_num(_g(orders, "avg_items_per_order"), 1)} avg/order)',
            severity=single_color, tag="BASKET SIZE"
        ))

    content = "\n".join(parts)
    return render_card("Menu Intelligence", content,
                       subtitle="Items, menu groups, catering, and discounts")


# ---------------------------------------------------------------------------
# Section 4: Baskets & Cross-Sell
# ---------------------------------------------------------------------------

def render_baskets_crosssell(metrics: dict) -> str:
    baskets = _g(metrics, "baskets")
    if not baskets:
        return ""

    parts = []

    # ── Natural Combos with BOGO toggle ──
    combos = _gl(baskets, "top_combos", default=[])
    combos_no_bogo = _gl(baskets, "top_combos_no_bogo", default=[])
    bogo_excluded = _g(baskets, "uber_bogo_orders_excluded", default=0)

    if combos:
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">')
        parts.append('<span style="font-size:11px;color:var(--muted);">TOP NATURAL COMBOS</span>')
        if bogo_excluded and combos_no_bogo:
            parts.append(
                '<div style="display:inline-flex;border:1px solid var(--border);border-radius:14px;overflow:hidden;font-size:10px;">'
                '<button onclick="document.getElementById(\'combos-all\').style.display=\'block\';'
                'document.getElementById(\'combos-nobogo\').style.display=\'none\';'
                'this.style.background=\'var(--livite-lime)\';this.style.color=\'#2d2a24\';'
                'this.nextElementSibling.style.background=\'transparent\';this.nextElementSibling.style.color=\'var(--muted)\';" '
                'style="padding:3px 10px;border:none;cursor:pointer;background:var(--livite-lime);color:#2d2a24;font-weight:600;">'
                'All Orders</button>'
                '<button onclick="document.getElementById(\'combos-nobogo\').style.display=\'block\';'
                'document.getElementById(\'combos-all\').style.display=\'none\';'
                'this.style.background=\'var(--livite-lime)\';this.style.color=\'#2d2a24\';'
                'this.previousElementSibling.style.background=\'transparent\';this.previousElementSibling.style.color=\'var(--muted)\';" '
                f'style="padding:3px 10px;border:none;cursor:pointer;background:transparent;color:var(--muted);">'
                f'No BOGO ({bogo_excluded} orders)</button>'
                '</div>'
            )
        parts.append('</div>')

        # All orders combos (Chart.js horizontal bar — full labels)
        parts.append('<div id="combos-all">')
        combo_labels = [f'{c.get("item1", "")} + {c.get("item2", "")}' for c in combos[:8]]
        combo_values = [c.get("frequency", 0) for c in combos[:8]]
        parts.append(render_chartjs_bar(combo_labels,
                                         [{"label": "Frequency", "data": combo_values, "color": "#9b72c4"}],
                                         height=max(200, len(combo_labels) * 32 + 40), horizontal=True))
        parts.append('</div>')

        # No-BOGO combos (hidden by default)
        if bogo_excluded and combos_no_bogo:
            parts.append('<div id="combos-nobogo" style="display:none;">')
            cnb_labels = [f'{c.get("item1", "")} + {c.get("item2", "")}' for c in combos_no_bogo[:8]]
            cnb_values = [c.get("frequency", 0) for c in combos_no_bogo[:8]]
            parts.append(render_chartjs_bar(cnb_labels,
                                             [{"label": "Frequency", "data": cnb_values, "color": "#9b72c4"}],
                                             height=max(200, len(cnb_labels) * 32 + 40), horizontal=True))
            parts.append('</div>')
        parts.append('</div>')

    # ── Attach Rates (visual bars) ──
    wrap_attach = _g(baskets, "wrap_attach", default={})
    smoothie_attach = _g(baskets, "smoothie_attach", default={})
    salad_attach = _g(baskets, "salad_attach", default={})

    def _render_attach(anchor: str, attach_data: dict, color: str) -> str:
        if not attach_data:
            return ""
        sorted_items = sorted(attach_data.items(), key=lambda x: x[1], reverse=True)
        att_labels = [cat for cat, _ in sorted_items]
        att_values = [round(pct, 1) for _, pct in sorted_items]
        html_parts = [f'<div style="margin-bottom:12px;">']
        html_parts.append(f'<div style="font-size:10px;color:var(--muted);margin-bottom:3px;">{anchor} ORDERS ALSO INCLUDE:</div>')
        html_parts.append(render_chartjs_bar(att_labels,
                                              [{"label": "Attach %", "data": att_values, "color": color}],
                                              height=max(120, len(att_labels) * 28 + 40), horizontal=True, pct=True))
        html_parts.append('</div>')
        return "\n".join(html_parts)

    attach_html = ""
    attach_html += _render_attach("WRAP", wrap_attach, "var(--cyan)")
    attach_html += _render_attach("SMOOTHIE", smoothie_attach, "var(--pink)")
    attach_html += _render_attach("SALAD", salad_attach, "var(--green)")
    if attach_html:
        parts.append(attach_html)

    # ── Basket Size by Channel with Units/Revenue toggle ──
    basket_size = _g(baskets, "avg_basket_size", default={})
    basket_rev = _g(baskets, "avg_basket_revenue", default={})
    if isinstance(basket_size, dict):
        overall = basket_size.get("overall", 0)
        by_ch = basket_size.get("by_channel", {})
        rev_overall = basket_rev.get("overall", 0) if isinstance(basket_rev, dict) else 0
        rev_by_ch = basket_rev.get("by_channel", {}) if isinstance(basket_rev, dict) else {}
        if by_ch:
            sorted_bs = sorted(by_ch.items(), key=lambda x: x[1], reverse=True)
            bs_labels = [ch for ch, _ in sorted_bs]
            bs_values = [round(bs, 1) for _, bs in sorted_bs]
            # Revenue data sorted to match same channel order
            rv_values = [round(rev_by_ch.get(ch, 0), 2) for ch in bs_labels]

            parts.append('<div style="margin-bottom:16px;">')
            parts.append('<div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">')
            parts.append(f'<span id="bs-title" style="font-size:11px;color:var(--muted);">AVG BASKET SIZE BY CHANNEL (Overall: {fmt_num(overall, 1)})</span>')
            parts.append(
                '<div style="display:inline-flex;border:1px solid var(--border);border-radius:14px;overflow:hidden;font-size:10px;">'
                '<button id="bs-btn-units" onclick="bsToggle(\'units\')" '
                'style="padding:3px 10px;border:none;cursor:pointer;background:var(--livite-lime);color:#2d2a24;font-weight:600;">'
                'Units</button>'
                '<button id="bs-btn-rev" onclick="bsToggle(\'revenue\')" '
                'style="padding:3px 10px;border:none;cursor:pointer;background:transparent;color:var(--muted);">'
                'Revenue</button>'
                '</div>'
            )
            parts.append('</div>')

            # Units chart (visible by default)
            parts.append('<div id="bs-chart-units">')
            parts.append(render_chartjs_bar(bs_labels,
                                             [{"label": "Avg Items", "data": bs_values, "color": "#4a9cd8"}],
                                             height=max(120, len(bs_labels) * 30 + 40), horizontal=True))
            parts.append('</div>')

            # Revenue chart (hidden by default)
            parts.append('<div id="bs-chart-revenue" style="display:none;">')
            parts.append(render_chartjs_bar(bs_labels,
                                             [{"label": "Avg Revenue", "data": rv_values, "color": "#9b72c4"}],
                                             height=max(120, len(bs_labels) * 30 + 40), horizontal=True, dollar=True))
            parts.append('</div>')

            # Toggle JS
            overall_units = fmt_num(overall, 1)
            overall_rev = fmt_num(rev_overall, 2)
            parts.append('<script>')
            parts.append('function bsToggle(mode){')
            parts.append('  var u=document.getElementById("bs-chart-units"),r=document.getElementById("bs-chart-revenue");')
            parts.append('  var bu=document.getElementById("bs-btn-units"),br=document.getElementById("bs-btn-rev");')
            parts.append('  var t=document.getElementById("bs-title");')
            parts.append('  if(mode==="revenue"){')
            parts.append('    u.style.display="none";r.style.display="block";')
            parts.append('    bu.style.background="transparent";bu.style.color="var(--muted)";bu.style.fontWeight="normal";')
            parts.append('    br.style.background="var(--livite-lime)";br.style.color="#2d2a24";br.style.fontWeight="600";')
            parts.append(f'    t.textContent="AVG BASKET REVENUE BY CHANNEL (Overall: ${overall_rev})";')
            parts.append('  }else{')
            parts.append('    r.style.display="none";u.style.display="block";')
            parts.append('    br.style.background="transparent";br.style.color="var(--muted)";br.style.fontWeight="normal";')
            parts.append('    bu.style.background="var(--livite-lime)";bu.style.color="#2d2a24";bu.style.fontWeight="600";')
            parts.append(f'    t.textContent="AVG BASKET SIZE BY CHANNEL (Overall: {overall_units})";')
            parts.append('  }')
            parts.append('}')
            parts.append('</script>')
            parts.append('</div>')

    content = "\n".join(parts)
    return render_card("Baskets & Cross-Sell", content,
                       subtitle="Natural combos, attach rates, and basket size")


# ---------------------------------------------------------------------------
# Section 5: Add-Ons & Modifications
# ---------------------------------------------------------------------------

def render_modifiers(metrics: dict) -> str:
    mods = _g(metrics, "modifiers")
    if not mods:
        return ""

    parts = []

    # ── Summary Stats ──
    stats = [
        render_stat("Modifier Revenue", fmt_currency(_g(mods, "total_modifier_revenue")), color="var(--cyan)"),
        render_stat("Total Modifiers", fmt_num(_g(mods, "total_modifiers"))),
        render_stat("Paid", fmt_num(_g(mods, "free_vs_paid", "paid_count")), subtitle=fmt_currency(_g(mods, "free_vs_paid", "paid_revenue"))),
        render_stat("Free", fmt_num(_g(mods, "free_vs_paid", "free_count"))),
    ]
    parts.append(render_stat_grid(stats))

    # ── Best Add-Ons by Category ──
    categories = _gl(mods, "modifier_categories", default=[])
    top_paid = _gl(mods, "top_paid_modifiers", default=[])
    if categories:
        # Show per-category paid add-on charts
        parts.append('<div style="margin-bottom:16px;">')
        parts.append(f'<div style="font-size:11px;color:var(--green);margin-bottom:8px;">\u25B2 BEST ADD-ONS BY CATEGORY</div>')
        cat_palette = ["#8cb82e", "#4a9cd8", "#e8a830", "#9b72c4", "#e86040", "#475417", "#c47d0a", "#4a7c1f"]
        shown = 0
        for i, cat in enumerate(categories):
            paid = cat.get("paid_modifiers", [])
            if not paid:
                continue
            cat_name = cat.get("category", "Unknown")
            cat_rev = cat.get("total_revenue", 0)
            c_labels = [p.get("modifier", "") for p in paid[:8]]
            c_values = [round(p.get("revenue", 0), 2) for p in paid[:8]]
            color = cat_palette[i % len(cat_palette)]
            parts.append(f'<div style="margin-bottom:12px;">')
            parts.append(f'<div style="font-size:10px;color:var(--muted);margin-bottom:2px;">'
                         f'{_safe(cat_name)} ({fmt_currency(cat_rev)} total)</div>')
            parts.append(render_chartjs_bar(
                c_labels, [{"label": "Revenue", "data": c_values, "color": color}],
                height=max(120, len(c_labels) * 26 + 30), horizontal=True, dollar=True))
            parts.append('</div>')
            shown += 1
            if shown >= 6:
                break
        parts.append('</div>')
    elif top_paid:
        # Fallback: flat list if no category data
        tp_labels = [m.get("modifier", "") for m in top_paid[:10]]
        tp_values = [round(m.get("revenue", 0), 2) for m in top_paid[:10]]
        parts.append('<div style="margin-bottom:16px;">')
        parts.append(f'<div style="font-size:11px;color:var(--green);margin-bottom:4px;">\u25B2 BEST ADD-ONS (BY REVENUE)</div>')
        parts.append(render_chartjs_bar(tp_labels, [{"label": "Revenue", "data": tp_values, "color": "#8cb82e"}],
                                         height=max(200, len(tp_labels) * 28 + 40), horizontal=True, dollar=True))
        parts.append('</div>')

    # ── Worst Add-Ons (Bottom Paid Modifiers) ──
    bottom_paid = _gl(mods, "bottom_paid_modifiers", default=[])
    if bottom_paid:
        parts.append('<div style="margin-bottom:16px;">')
        parts.append(f'<div style="font-size:11px;color:var(--red);margin-bottom:4px;">\u25BC LOWEST-PERFORMING PAID ADD-ONS</div>')
        for m in bottom_paid[:8]:
            mod_name = m.get("modifier", "")
            m_rev = m.get("revenue", 0)
            m_qty = m.get("qty", 0)
            parts.append(f'<div style="font-size:11px;display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid rgba(30,41,59,0.4);">'
                         f'<span>{_safe(mod_name)}</span>'
                         f'<span class="mono" style="font-size:10px;color:var(--muted);">{fmt_currency(m_rev)} ({m_qty}x)</span>'
                         f'</div>')
        parts.append(render_insight(
            'Consider removing or repricing low-performing paid modifiers to simplify the menu.',
            severity="blue", tag="SUGGESTION"
        ))
        parts.append('</div>')

    # ── Per-Item Modifier Breakdown ──
    item_pairs = _gl(mods, "item_modifier_pairs", default=[])
    if item_pairs:
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">MOST COMMON MODIFIERS BY ITEM</div>')
        for pair in item_pairs[:8]:
            item_name = pair.get("item", "")
            item_mods = pair.get("modifiers", [])
            if not item_mods:
                continue
            mod_tags = []
            for im in item_mods[:4]:
                im_name = im.get("modifier", "")
                im_qty = im.get("qty", 0)
                im_rev = im.get("revenue", 0)
                color_cls = "g" if im_rev > 0 else "b"
                mod_tags.append(f'{render_badge(f"{im_name} ({im_qty}x)", color_cls)}')
            parts.append(
                f'<div style="margin:6px 0;">'
                f'<div style="font-size:12px;font-weight:500;margin-bottom:3px;">{_safe(item_name[:35])}</div>'
                f'<div style="display:flex;flex-wrap:wrap;gap:4px;">{"".join(mod_tags)}</div>'
                f'</div>'
            )
        parts.append('</div>')

    # ── Free Modification Patterns ──
    free_breakdown = _gl(mods, "free_modification_breakdown", default=[])
    if free_breakdown:
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">FREE MODIFICATION PATTERNS</div>')
        parts.append('<div style="font-size:9px;color:var(--muted);margin-bottom:8px;">Percentages show share of selections within each option group</div>')
        for fb in free_breakdown[:6]:
            og = fb.get("option_group", "")
            choices = fb.get("choices", [])
            total = fb.get("total", 0)
            if not choices or total == 0:
                continue
            parts.append(f'<div style="margin:8px 0;">')
            parts.append(f'<div style="font-size:11px;font-weight:500;margin-bottom:3px;">{_safe(og)} ({total} selections)</div>')
            # Stacked bar for choices
            segments = []
            palette = ["var(--cyan)", "var(--blue)", "var(--purple)", "var(--green)", "var(--pink)", "var(--amber)"]
            for i, ch in enumerate(choices[:6]):
                segments.append({
                    "label": ch.get("modifier", ""),
                    "value": ch.get("qty", 0),
                    "color": palette[i % len(palette)],
                })
            parts.append(render_stacked_bar(segments, height=22))
            # Legend
            legend_parts = []
            for i, ch in enumerate(choices[:4]):
                c = palette[i % len(palette)]
                legend_parts.append(
                    f'<span style="font-size:10px;margin-right:10px;">'
                    f'<span style="color:{c};">\u25CF</span> '
                    f'{_safe(ch.get("modifier", "")[:20])} ({fmt_pct(ch.get("pct", 0))})'
                    f'</span>'
                )
            parts.append(f'<div>{"".join(legend_parts)}</div>')
            parts.append('</div>')
        parts.append('</div>')

    # ── Base Selections (Wrap Type, Smoothie Base, Milk, Grain, Lettuce) ──
    base_selections = _g(mods, "base_selections", default={})
    if base_selections and isinstance(base_selections, dict):
        palette = ["var(--bar-green)", "var(--bar-blue)", "var(--bar-purple)", "var(--bar-amber)", "var(--bar-cyan)", "var(--bar-red)"]
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:8px;">BASE &amp; INGREDIENT SELECTIONS</div>')
        for cat_name, cat_items in base_selections.items():
            if not cat_items:
                continue
            parts.append(f'<div style="margin-bottom:10px;">')
            parts.append(f'<div style="font-size:10px;font-weight:600;margin-bottom:4px;">{_safe(cat_name)}</div>')
            segments = [
                {"label": w.get("type", ""), "value": w.get("qty", 0),
                 "color": palette[i % len(palette)]}
                for i, w in enumerate(cat_items[:6])
            ]
            parts.append(render_stacked_bar(segments, height=24))
            legend = []
            for i, w in enumerate(cat_items[:6]):
                c = palette[i % len(palette)]
                legend.append(
                    f'<span style="font-size:10px;margin-right:10px;">'
                    f'<span style="color:{c};">\u25CF</span> '
                    f'{_safe(w.get("type", "")[:25])} {w.get("qty",0)}x ({fmt_pct(w.get("pct", 0))})'
                    f'</span>'
                )
            parts.append(f'<div>{"".join(legend)}</div>')
            parts.append('</div>')
        parts.append('</div>')

    # ── Removal Analysis (what gets removed, by item — with % of orders + freq) ──
    removal_analysis = _g(mods, "removal_analysis", default={})
    if removal_analysis and isinstance(removal_analysis, dict):
        rm_id = "removal_detail"
        total_removals = sum(sum(r.get("qty", 0) for r in items) for items in removal_analysis.values())
        items_with_removals = sorted(removal_analysis.items(), key=lambda x: sum(r['qty'] for r in x[1]), reverse=True)

        parts.append('<div style="margin-bottom:16px;">')
        parts.append(f'<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">INGREDIENT REMOVALS ({total_removals} total)</div>')

        # Summary — removal rate as % of applicable orders (Chart.js horizontal)
        removal_summary = _gl(mods, "removal_summary", default=[])
        if removal_summary:
            parts.append('<div style="font-size:9px;color:var(--muted);margin-bottom:4px;">Rate = % of orders for items that offer this removal</div>')
            rs_labels = [f'{rs.get("ingredient", "")} ({rs.get("removed_qty", 0)}x)' for rs in removal_summary[:8]]
            rs_values = [rs.get("removal_rate", 0) for rs in removal_summary[:8]]
            parts.append(render_chartjs_bar(rs_labels,
                                             [{"label": "Removal Rate %", "data": rs_values, "color": "#e86040"}],
                                             height=max(180, len(rs_labels) * 30 + 40), horizontal=True, pct=True))
        else:
            # Fallback to simple qty bars
            all_removals = {}
            for item_name, removals in removal_analysis.items():
                for r in removals:
                    name = r.get("removed", "")
                    all_removals[name] = all_removals.get(name, 0) + r.get("qty", 0)
            top_removed = sorted(all_removals.items(), key=lambda x: x[1], reverse=True)[:8]
            if top_removed:
                rm_labels = [name for name, _ in top_removed]
                rm_values = [qty for _, qty in top_removed]
                parts.append(render_chartjs_bar(rm_labels,
                                                 [{"label": "Count", "data": rm_values, "color": "#e86040"}],
                                                 height=max(180, len(rm_labels) * 30 + 40), horizontal=True))

        # Per-item breakdown with % of that item's orders — ALL items
        parts.append(
            f'<div class="appendix-toggle" onclick="var c=document.getElementById(\'{rm_id}\');'
            f'c.classList.toggle(\'open\');this.querySelector(\'span\').textContent='
            f'c.classList.contains(\'open\')?\'\\u25BC\':\'\\u25B6\';">'
            f'<span>\u25B6</span> Show removals by item (% = share of that item\'s orders)'
            f'</div>'
            f'<div id="{rm_id}" class="appendix-content">'
        )
        for item_name, removals in items_with_removals:
            item_total = removals[0].get("item_total_ordered", 0) if removals else 0
            total_label = f" ({item_total} ordered)" if item_total > 0 else ""
            parts.append(f'<div style="margin:10px 0;">')
            parts.append(f'<div style="font-size:11px;font-weight:600;margin-bottom:4px;">{_safe(item_name)}{total_label}</div>')
            # Chart.js horizontal bar for this item's removals
            rm_labels = [r.get("removed", "") for r in removals]
            rm_pcts = [r.get("pct_of_orders", 0) for r in removals]
            if any(p > 0 for p in rm_pcts):
                parts.append(render_chartjs_bar(
                    rm_labels,
                    [{"label": "% of orders", "data": rm_pcts, "color": "#e86040"}],
                    height=max(100, len(rm_labels) * 28 + 40), horizontal=True, pct=True))
            else:
                for r in removals:
                    parts.append(
                        f'<div style="font-size:10px;color:var(--muted);padding-left:12px;">'
                        f'\u2716 {_safe(r.get("removed", ""))} ({r.get("qty", 0)}x)'
                        f'</div>'
                    )
            parts.append('</div>')
        parts.append('</div>')  # close appendix
        parts.append('</div>')

    # ── Item Modification Profiles (grouped by category, colored mini-bars with %) ──
    item_mod_pairs = _gl(mods, "item_modifier_pairs", default=[])
    if item_mod_pairs:
        # Group items by menu category
        categories = {}
        for imp in item_mod_pairs:
            cat = imp.get("menu_group", "Other")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(imp)

        cat_order = ["Wraps", "Salads & Bowls", "Smoothies", "Juices", "Beverages", "Snacks", "Soup", "Other"]
        sorted_cats = [c for c in cat_order if c in categories]
        sorted_cats += [c for c in categories if c not in sorted_cats]

        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">ITEM MODIFICATION PROFILES BY CATEGORY</div>')
        parts.append('<div style="font-size:9px;color:var(--muted);margin-bottom:8px;">'
                     'Base selections, add-ons, and removals per item. '
                     'Bars scaled to 100% = every order of that item.</div>')

        def _render_mod_bars(mod_list, color, max_items=12):
            """Render colored mini-bars with % on a consistent 0-100% scale."""
            html_parts = []
            for m in mod_list[:max_items]:
                name = m.get("modifier", "")
                qty = m.get("qty", 0)
                pct = m.get("pct_of_orders", 0)
                bar_w = max(pct, 1)
                html_parts.append(
                    f'<div style="display:flex;align-items:center;gap:6px;margin:2px 0;">'
                    f'<span style="font-size:10px;width:130px;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{_safe(name)}</span>'
                    f'<div style="flex:1;background:var(--surface2);border-radius:3px;height:14px;position:relative;">'
                    f'<div style="width:{min(bar_w, 100)}%;background:{color};height:100%;border-radius:3px;opacity:0.75;"></div>'
                    f'</div>'
                    f'<span class="mono" style="font-size:10px;width:70px;text-align:right;flex-shrink:0;">'
                    f'{qty}x {fmt_pct(pct)}</span>'
                    f'</div>'
                )
            return "\n".join(html_parts)

        for cat in sorted_cats:
            cat_items = categories[cat]
            cat_id = f"mod_cat_{cat.lower().replace(' ', '_').replace('&', 'and')}"
            cat_item_count = len(cat_items)
            total_cat_orders = sum(i.get("item_ordered", 0) for i in cat_items)

            parts.append(
                f'<div class="appendix-toggle" style="margin-top:8px;font-size:12px;font-weight:600;" '
                f'onclick="var c=document.getElementById(\'{cat_id}\');'
                f'c.classList.toggle(\'open\');this.querySelector(\'span\').textContent='
                f'c.classList.contains(\'open\')?\'\\u25BC\':\'\\u25B6\';">'
                f'<span>\u25B6</span> {_safe(cat)} ({cat_item_count} items, {total_cat_orders} orders)'
                f'</div>'
                f'<div id="{cat_id}" class="appendix-content">'
            )

            for imp in cat_items:
                item_name = imp.get("item", "")
                item_ordered = imp.get("item_ordered", 0)
                bases = imp.get("bases", [])
                addons = imp.get("addons", [])
                removals = imp.get("removals", [])
                if not bases and not addons and not removals:
                    continue
                ordered_label = f" ({item_ordered} ordered)" if item_ordered > 0 else ""
                parts.append(f'<div style="margin:10px 0 10px 8px;border-left:3px solid var(--surface2);padding-left:10px;">')
                parts.append(f'<div style="font-size:11px;font-weight:600;margin-bottom:4px;">{_safe(item_name)}{ordered_label}</div>')

                if bases:
                    parts.append('<div style="font-size:9px;color:var(--green);font-weight:600;margin:4px 0 2px;">Base Selections</div>')
                    parts.append(_render_mod_bars(bases, "var(--bar-green)"))

                if addons:
                    parts.append('<div style="font-size:9px;color:var(--blue);font-weight:600;margin:4px 0 2px;">Add-ons</div>')
                    parts.append(_render_mod_bars(addons, "var(--bar-blue)"))

                if removals:
                    parts.append('<div style="font-size:9px;color:var(--red);font-weight:600;margin:4px 0 2px;">Removals</div>')
                    parts.append(_render_mod_bars(removals, "var(--bar-red)"))

                parts.append('</div>')

            parts.append('</div>')  # close category appendix

        parts.append('</div>')

    # ── Best-Selling Dressings (Chart.js horizontal) ──
    dressings = _gl(mods, "best_dressings", default=[])
    if dressings:
        dr_labels = [d.get("dressing", d.get("modifier", "")) for d in dressings[:10]]
        dr_values = [d.get("qty", 0) for d in dressings[:10]]
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">BEST-SELLING DRESSINGS & SAUCES</div>')
        parts.append(render_chartjs_bar(dr_labels,
                                         [{"label": "Qty Sold", "data": dr_values, "color": "#4a9cd8"}],
                                         height=max(200, len(dr_labels) * 28 + 40), horizontal=True))
        parts.append('</div>')

    # ── Dressing Prep Calculator ──
    dressing_prep = _gl(mods, "dressing_prep", default=[])
    total_portions = _g(mods, "total_dressing_portions", default=0)
    is_range = metrics.get("is_range", False)
    range_days = max(metrics.get("range_days", 1), 1)
    if dressing_prep:
        # In range mode, show daily average instead of totals
        if is_range:
            dp_display = []
            for d in dressing_prep:
                avg_p = round(d.get("portions", 0) / range_days)
                dp_display.append({"dressing": d.get("dressing", ""), "portions": avg_p})
            avg_total = round(total_portions / range_days)
            label_mode = "Daily Avg"
            header_note = f"Daily average over {range_days} days ({total_portions} total)"
        else:
            dp_display = dressing_prep
            avg_total = total_portions
            label_mode = "Total"
            header_note = f"{total_portions} total portions"

        parts.append('<div style="margin-bottom:16px;">')
        parts.append(f'<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">DRESSING PREP CALCULATOR ({header_note})</div>')
        parts.append('<div style="font-size:9px;color:var(--muted);margin-bottom:8px;">'
                     'Counts portioned dressings for salads &amp; bowls (default + extras - removals). '
                     'Wrap sauces from squeeze bottles are not included.</div>')
        dp_labels = [d.get("dressing", "") for d in dp_display]
        dp_values = [d.get("portions", 0) for d in dp_display]
        parts.append(render_chartjs_bar(dp_labels,
                                         [{"label": "Portions", "data": dp_values, "color": "#8cb82e"}],
                                         height=max(160, len(dp_labels) * 32 + 40), horizontal=True))
        # Also show as a table for easy reading
        parts.append('<table style="width:100%;font-size:10px;margin-top:6px;border-collapse:collapse;">')
        parts.append('<tr style="border-bottom:1px solid var(--surface2);">'
                     '<th style="text-align:left;padding:3px 6px;">Dressing</th>'
                     f'<th style="text-align:right;padding:3px 6px;">{"Daily Avg" if is_range else "Portions"}</th></tr>')
        for d in dp_display:
            parts.append(f'<tr style="border-bottom:1px solid var(--surface2);">'
                         f'<td style="padding:3px 6px;">{_safe(d.get("dressing", ""))}</td>'
                         f'<td class="mono" style="text-align:right;padding:3px 6px;">{d.get("portions", 0)}</td></tr>')
        parts.append(f'<tr style="font-weight:600;border-top:2px solid var(--muted);">'
                     f'<td style="padding:3px 6px;">{label_mode}</td>'
                     f'<td class="mono" style="text-align:right;padding:3px 6px;">{avg_total}</td></tr>')
        parts.append('</table>')
        parts.append('</div>')

    # ── Categorized Modifiers by Option Group ──
    categories = _gl(mods, "modifier_categories", default=[])
    if categories:
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">MODIFIERS BY CATEGORY</div>')
        for cat in categories[:12]:
            cat_name = cat.get("category", "")
            cat_rev = cat.get("total_revenue", 0)
            cat_qty = cat.get("total_qty", 0)
            paid = cat.get("paid_modifiers", [])
            free = cat.get("free_modifiers", [])
            rev_str = f" \u2014 {fmt_currency(cat_rev)}" if cat_rev > 0 else ""
            parts.append(
                f'<div style="margin:8px 0;padding:8px;background:var(--surface2);border-radius:6px;">'
                f'<div style="font-size:11px;font-weight:600;margin-bottom:4px;">'
                f'{_safe(cat_name)} ({cat_qty}x){rev_str}</div>'
            )
            if paid:
                items_str = ", ".join(
                    f'{p.get("modifier", "")} ({p.get("qty", 0)}x, {fmt_currency(p.get("revenue", 0))})'
                    for p in paid[:5]
                )
                parts.append(f'<div style="font-size:10px;color:var(--green);margin-bottom:2px;">Paid: {_safe(items_str)}</div>')
            if free:
                items_str = ", ".join(
                    f'{f.get("modifier", "")} ({f.get("qty", 0)}x)'
                    for f in free[:5]
                )
                parts.append(f'<div style="font-size:10px;color:var(--muted);">Free: {_safe(items_str)}</div>')
            parts.append('</div>')
        parts.append('</div>')

    # ── Item × Add-On Matrix ──
    matrix = _g(mods, "item_mod_matrix", default={})
    matrix_rows = _gl(matrix, "rows", default=[]) if isinstance(matrix, dict) else []
    matrix_mods = _gl(matrix, "modifiers", default=[]) if isinstance(matrix, dict) else []
    if matrix_rows and matrix_mods:
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">ITEM \u00D7 ADD-ON MATRIX</div>')
        parts.append('<div style="overflow-x:auto;">')
        # Table header
        hdr_cells = ['<th style="text-align:left;padding:4px 6px;font-size:9px;">Item</th>']
        for mod_name in matrix_mods:
            short_name = str(mod_name)[:10]
            hdr_cells.append(f'<th style="text-align:center;padding:4px 3px;font-size:9px;writing-mode:vertical-lr;max-width:30px;">{_safe(short_name)}</th>')
        parts.append(f'<table style="border-collapse:collapse;font-size:11px;"><thead><tr>{"".join(hdr_cells)}</tr></thead><tbody>')
        # Find max for heatmap coloring
        all_vals = [row.get("mods", {}).get(str(m), 0) for row in matrix_rows for m in matrix_mods]
        mx_val = max(all_vals) if all_vals else 1
        for row in matrix_rows:
            item_name = str(row.get("item", ""))[:20]
            cells = [f'<td style="padding:4px 6px;font-size:10px;white-space:nowrap;">{_safe(item_name)}</td>']
            row_mods = row.get("mods", {})
            for mod_name in matrix_mods:
                cnt = row_mods.get(str(mod_name), 0)
                if cnt == 0:
                    cells.append('<td style="text-align:center;padding:2px;color:var(--muted);font-size:9px;">\u2014</td>')
                else:
                    intensity = min(1.0, cnt / max(mx_val, 1))
                    bg = f"rgba(181,227,89,{0.1 + intensity * 0.5})"
                    cells.append(f'<td style="text-align:center;padding:2px;background:{bg};font-size:10px;font-weight:600;">{cnt}</td>')
            parts.append(f'<tr>{"".join(cells)}</tr>')
        parts.append('</tbody></table>')
        parts.append('</div>')
        parts.append(render_insight(
            'Matrix shows how often each paid add-on is ordered with each menu item. '
            'High-count cells indicate natural upsell pairings.',
            severity="blue", tag="INTERPRETATION"
        ))
        parts.append('</div>')

    # ── Bad Add-On Removal Recommendations ──
    if bottom_paid:
        recs = [m for m in bottom_paid if m.get("revenue", 0) < 5 and m.get("qty", 0) < 5]
        if recs:
            rec_lines = []
            for m in recs[:5]:
                rec_lines.append(f'<strong>{_safe(m.get("modifier", ""))}</strong> '
                                 f'({fmt_currency(m.get("revenue", 0))}, {m.get("qty", 0)}x)')
            parts.append(render_insight(
                f'Consider removing: {" | ".join(rec_lines)}. '
                f'These add-ons generate minimal revenue and may add menu complexity.',
                severity="red", tag="REMOVAL CANDIDATES"
            ))

    content = "\n".join(parts)
    return render_card("Add-Ons & Modifications", content,
                       subtitle="Categorized modifiers, wrap types, dressings, item matrix, and recommendations")


# ---------------------------------------------------------------------------
# Section 6: Kitchen Speed
# ---------------------------------------------------------------------------

def render_kitchen_speed(metrics: dict) -> str:
    kitchen = _g(metrics, "kitchen")
    if not kitchen:
        return ""

    parts = []

    # ── Outlier filter info ──
    outlier_count = _g(kitchen, "outlier_count", default=0)
    if outlier_count and outlier_count > 0:
        parts.append(render_insight(
            f'Filtered {outlier_count} outlier ticket(s) (<30 sec instant close-outs or >60 min forgotten fires).',
            severity="amber", tag="DATA QUALITY"
        ))

    # ── Station Stat Grids ──
    stations = _g(kitchen, "stations", default={})
    for station_name, stats in stations.items():
        p50 = stats.get("median", 0)
        p75 = stats.get("p75", 0)
        p90 = stats.get("p90", 0)
        p95 = stats.get("p95", 0)
        mean_val = stats.get("mean", 0)
        count = stats.get("total_tickets", 0)

        def _speed_color(val):
            if val <= 5:
                return "var(--green)"
            elif val <= 10:
                return "var(--amber)"
            else:
                return "var(--red)"

        station_stats = [
            render_stat("Tickets", fmt_num(count)),
            render_stat("Median", f"{p50:.1f}m", color=_speed_color(p50)),
            render_stat("P75", f"{p75:.1f}m", color=_speed_color(p75)),
            render_stat("P90", f"{p90:.1f}m", color=_speed_color(p90)),
            render_stat("P95", f"{p95:.1f}m", color=_speed_color(p95)),
            render_stat("Mean", f"{mean_val:.1f}m"),
        ]
        parts.append(f'<div style="margin-bottom:12px;">')
        parts.append(f'<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">{_safe(station_name.upper())}</div>')
        parts.append(render_stat_grid(station_stats))
        parts.append('</div>')

    # ── Distribution Histogram ──
    distribution = _g(kitchen, "distribution", default={})
    bucket_labels = ["0-2m", "2-5m", "5-8m", "8-10m", "10-15m", "15-20m", "20m+"]
    bucket_keys = ["bucket_0_2", "bucket_2_5", "bucket_5_8", "bucket_8_10", "bucket_10_15", "bucket_15_20", "bucket_20_plus"]
    bucket_colors = ["var(--green)", "var(--cyan)", "var(--blue)", "var(--amber)", "var(--amber)", "var(--red)", "var(--red)"]

    for station_name, buckets in distribution.items():
        if not isinstance(buckets, dict):
            continue
        values = [buckets.get(k, 0) for k in bucket_keys]
        total = sum(values)
        if total == 0:
            continue
        segments = []
        for i, (lbl, val) in enumerate(zip(bucket_labels, values)):
            segments.append({"label": lbl, "value": val, "color": bucket_colors[i]})
        parts.append(f'<div style="margin-bottom:12px;">')
        parts.append(f'<div style="font-size:10px;color:var(--muted);margin-bottom:3px;">{_safe(station_name)} DISTRIBUTION ({total} tickets)</div>')
        parts.append(render_stacked_bar(segments, height=24))
        # Percentage labels
        pct_parts = []
        for i, (lbl, val) in enumerate(zip(bucket_labels, values)):
            pct = round(val / max(total, 1) * 100, 0)
            pct_parts.append(f'<span style="font-size:9px;color:{bucket_colors[i]};">{lbl}: {pct:.0f}%</span>')
        parts.append(f'<div style="display:flex;gap:8px;flex-wrap:wrap;">{"  ".join(pct_parts)}</div>')
        parts.append('</div>')

    # ── Speed Over Time (Chart.js line — Phase 0J) ──
    hourly_speed = _g(kitchen, "hourly_speed", default={})
    if hourly_speed and isinstance(hourly_speed, dict):
        # Build line datasets per station
        speed_datasets = []
        all_hours = set()
        for station_name, entries in hourly_speed.items():
            if isinstance(entries, list):
                for e in entries:
                    all_hours.add(e.get("hour", 0))
        if all_hours:
            sorted_hours = sorted(all_hours)
            speed_labels = []
            for h in sorted_hours:
                am_pm = "a" if h < 12 else "p"
                disp_h = h if h <= 12 else h - 12
                if disp_h == 0:
                    disp_h = 12
                speed_labels.append(f"{disp_h}{am_pm}")
            color_idx = 0
            for station_name, entries in hourly_speed.items():
                if not isinstance(entries, list) or not entries:
                    continue
                hour_map = {e.get("hour", 0): e for e in entries}
                line_data = [round(hour_map.get(h, {}).get("median", 0), 1) for h in sorted_hours]
                if any(v > 0 for v in line_data):
                    speed_datasets.append({
                        "label": station_name, "data": line_data,
                        "color": LIVITE_CHART_COLORS[color_idx % len(LIVITE_CHART_COLORS)],
                        "borderWidth": 2, "pointRadius": 4, "fill": False,
                    })
                    color_idx += 1
            if speed_datasets:
                parts.append('<div style="margin-bottom:16px;">')
                parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">KITCHEN SPEED OVER TIME (Median Minutes)</div>')
                parts.append(render_chartjs_line(speed_labels, speed_datasets, height=250,
                                                  annotation_lines=[
                                                      {"value": 5, "color": "#4a7c1f", "label": "5 min target"},
                                                      {"value": 10, "color": "#d9342b", "label": "10 min warning"},
                                                  ]))
                parts.append('</div>')

    # ── Kitchen Speed by Day of Week (range mode only) ──
    kitchen_by_dow = _gl(kitchen, "kitchen_by_dow", default=[])
    if kitchen_by_dow:
        dow_labels = [d.get('dow', '')[:3] for d in kitchen_by_dow]
        dow_medians = [d.get('median', 0) for d in kitchen_by_dow]
        dow_tickets = [d.get('avg_tickets', 0) for d in kitchen_by_dow]
        # Color each bar by speed
        dow_colors = []
        for m in dow_medians:
            if m <= 5:
                dow_colors.append('#4a7c1f')
            elif m <= 10:
                dow_colors.append('#f5a623')
            else:
                dow_colors.append('#d9342b')
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">TICKET SPEED BY DAY OF WEEK (Median Minutes)</div>')
        parts.append(render_chartjs_line(
            dow_labels,
            [
                {"label": "Median (min)", "data": dow_medians, "type": "bar",
                 "color": "#8cb82e", "colors": dow_colors},
                {"label": "Avg Tickets/Day", "data": dow_tickets,
                 "color": "#7a7a6f", "borderWidth": 2, "pointRadius": 4,
                 "yAxisID": "y2"},
            ],
            height=220, y2=True,
            annotation_lines=[
                {"value": 5, "color": "#4a7c1f", "label": "5 min"},
                {"value": 10, "color": "#d9342b", "label": "10 min"},
            ]))
        parts.append('</div>')

    # ── Speed Heatmap: Station × Hour ──
    hourly_speed_hm = _g(kitchen, "hourly_speed", default={})
    if hourly_speed_hm and isinstance(hourly_speed_hm, dict):
        # Collect all hours across stations
        all_hrs = set()
        for entries in hourly_speed_hm.values():
            if isinstance(entries, list):
                for e in entries:
                    all_hrs.add(e.get("hour", 0))
        if all_hrs:
            sorted_hrs = sorted(all_hrs)
            parts.append('<div style="margin-bottom:16px;">')
            parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">SPEED HEATMAP (Median Minutes by Station &amp; Hour)</div>')
            parts.append('<div style="overflow-x:auto;">')
            parts.append('<table style="width:100%;border-collapse:collapse;font-size:10px;">')
            # Header row
            parts.append('<tr><th style="text-align:left;padding:3px 4px;color:var(--muted);font-weight:600;">Station</th>')
            for h in sorted_hrs:
                ampm = "a" if h < 12 else "p"
                dh = h if h <= 12 else h - 12
                if dh == 0:
                    dh = 12
                parts.append(f'<th style="padding:3px 2px;color:var(--muted);font-weight:500;text-align:center;">{dh}{ampm}</th>')
            parts.append('</tr>')
            # Data rows
            for st_name, entries in hourly_speed_hm.items():
                if not isinstance(entries, list):
                    continue
                hour_map = {e.get("hour", 0): e for e in entries}
                parts.append(f'<tr><td style="padding:3px 4px;font-weight:600;white-space:nowrap;color:var(--text);">{_safe(st_name)}</td>')
                for h in sorted_hrs:
                    e = hour_map.get(h)
                    if e and e.get("tickets", 0) > 0:
                        med = e.get("median", 0)
                        if med <= 5:
                            bg = "#e8f5e2"
                            clr = "#2d6a10"
                        elif med <= 8:
                            bg = "#fff8e1"
                            clr = "#8a6d00"
                        elif med <= 10:
                            bg = "#fff3e0"
                            clr = "#bf6000"
                        else:
                            bg = "#fde8e8"
                            clr = "#c0392b"
                        parts.append(f'<td style="text-align:center;padding:2px;background:{bg};color:{clr};font-weight:600;border-radius:3px;">{med:.0f}</td>')
                    else:
                        parts.append('<td style="text-align:center;padding:2px;color:#ddd;">-</td>')
                parts.append('</tr>')
            parts.append('</table>')
            parts.append('</div>')  # overflow wrapper
            parts.append('</div>')

    # ── Walk-In vs Online Speed ──
    walkin_vs = _g(kitchen, "walkin_vs_online", default={})
    if walkin_vs:
        wi_median = _g(walkin_vs, "walkin", "median", default=0)
        ol_median = _g(walkin_vs, "online", "median", default=0)
        if wi_median or ol_median:
            parts.append(render_insight(
                f'Walk-In median: <span class="hl">{wi_median:.1f} min</span> vs '
                f'Online/3P median: <span class="hl">{ol_median:.1f} min</span>',
                severity="blue", tag="SPEED COMPARISON"
            ))

    # ── Peak Concurrent ──
    peak_concurrent = _g(kitchen, "peak_concurrent", default={})
    if isinstance(peak_concurrent, dict) and peak_concurrent.get("count", 0) > 0:
        parts.append(render_insight(
            f'Peak concurrent tickets: <span class="hl">{peak_concurrent.get("count", 0)}</span> '
            f'at {_safe(peak_concurrent.get("time", ""))}',
            severity="blue", tag="PEAK LOAD"
        ))

    content = "\n".join(parts)
    return render_card("Kitchen Speed", content,
                       subtitle="Station performance, distribution, and speed comparisons")


# ---------------------------------------------------------------------------
# Section 7: Labor & Staffing
# ---------------------------------------------------------------------------

def render_labor_staffing(metrics: dict, comparisons: dict | None) -> str:
    labor = _g(metrics, "labor")
    if not labor:
        return ""

    parts = []

    # ── Summary Stats ──
    stats = [
        render_stat("Total Labor", fmt_currency(_g(labor, "total_labor"))),
        render_stat("Labor %", fmt_pct(_g(labor, "labor_pct")),
                     color="var(--red)" if (_g(labor, "labor_pct") or 0) > 30 else None),
        render_stat("Rev / Labor Hr", fmt_currency(_g(labor, "rev_per_labor_hr"))),
        render_stat("Orders / Hr", fmt_num(_g(labor, "orders_per_labor_hr"), 1)),
        render_stat("Total Hours", fmt_num(_g(labor, "total_hours"), 1)),
        render_stat("Employees", fmt_num(_g(labor, "total_employees"))),
    ]
    parts.append(render_stat_grid(stats))

    # ── OT Detail ──
    ot_detail = _gl(labor, "ot_detail", default=[])
    ot_total = _g(labor, "ot_pay_total", default=0)
    if ot_detail:
        parts.append('<div style="margin-bottom:16px;">')
        parts.append(f'<div style="font-size:11px;color:var(--red);margin-bottom:4px;">OVERTIME DETAIL (Total: {fmt_currency(ot_total)})</div>')
        rows = []
        for ot in ot_detail:
            _ot_name = ot.get("employee", "")
            if not _ot_name or str(_ot_name).lower() in ("nan", "na", "none", ""):
                _ot_name = "Unknown"
            rows.append([
                _safe(_ot_name),
                fmt_num(ot.get("ot_hrs", 0), 1),
                fmt_currency(ot.get("ot_pay", 0)),
                fmt_pct(ot.get("ot_pct_of_pay", 0)),
            ])
        if rows:
            parts.append(render_table(["Employee", "OT Hrs", "OT Pay", "OT % Pay"], rows, [1, 2, 3]))
        parts.append('</div>')

    # ── Staffing-Demand Overlay (Chart.js dual-axis) ──
    overlay = _gl(metrics, "staffing_demand_overlay", default=[])
    if overlay:
        ov_labels = []
        ov_staff = []
        ov_orders = []
        for entry in overlay:
            staff = entry.get("staff_count", 0)
            orders = entry.get("orders", 0)
            if staff == 0 and orders == 0:
                continue
            # Shorten labels for mobile: "10:00 AM" -> "10A", "10:30 AM" -> "10:30"
            raw_label = entry.get("label", "")
            try:
                parts_lbl = raw_label.strip().split()
                time_part = parts_lbl[0] if parts_lbl else raw_label
                ampm = parts_lbl[1][0] if len(parts_lbl) > 1 else ""
                if time_part.endswith(":00"):
                    short = time_part.replace(":00", "") + ampm
                else:
                    short = time_part
                ov_labels.append(short)
            except Exception:
                ov_labels.append(raw_label)
            ov_staff.append(staff)
            ov_orders.append(orders)
        if ov_labels:
            parts.append('<div style="margin-bottom:16px;">')
            parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">STAFFING vs DEMAND BY HALF-HOUR</div>')
            parts.append(render_chartjs_line(
                ov_labels,
                [
                    {"label": "Staff on Floor", "data": ov_staff, "color": "#4a9cd8",
                     "fill": True, "type": "bar", "yAxisID": "y", "order": 2},
                    {"label": "Orders", "data": ov_orders, "color": "#475417",
                     "borderWidth": 2, "pointRadius": 3, "yAxisID": "y2", "order": 1},
                ],
                height=260, y2=True,
            ))
            parts.append('</div>')

    # ── Daypart Efficiency (Chart.js bar + detail table — Phase 0K bigger) ──
    dayparts = _gl(labor, "daypart_efficiency", default=[])
    if dayparts:
        dp_labels = [dp.get("daypart", "") for dp in dayparts]
        dp_values = [round(dp.get("rev_per_labor_dollar", 0), 2) for dp in dayparts]
        # Color by efficiency: green >$4, amber $3-4, red <$3
        dp_colors = []
        for v in dp_values:
            if v >= 4:
                dp_colors.append("#4a7c1f")
            elif v >= 3:
                dp_colors.append("#e8a830")
            else:
                dp_colors.append("#e86040")
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">DAYPART EFFICIENCY (Rev per $1 Labor)</div>')
        parts.append(render_chartjs_bar(dp_labels,
                                         [{"label": "Rev/$1 Labor", "data": dp_values, "colors": dp_colors}],
                                         height=250, dollar=True,
                                         annotation_lines=[{"value": 3, "color": "#c47d0a", "label": "$3 target"}]))
        # Detailed breakdown table
        dp_rows = []
        for dp in dayparts:
            dp_rows.append([
                _safe(dp.get("daypart", "")),
                dp.get("hours", ""),
                fmt_currency(dp.get("revenue", 0)),
                fmt_currency(dp.get("labor_cost", 0)),
                f'${dp.get("rev_per_labor_dollar", 0):.2f}',
            ])
        parts.append(render_table(["Daypart", "Hours", "Revenue", "Labor", "Rev/$1"], dp_rows, [2, 3, 4]))
        parts.append('</div>')

    # ── Role Breakdown ──
    roles = _gl(labor, "role_breakdown", default=[])
    if roles:
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">LABOR BY ROLE</div>')
        rows = []
        for r in roles:
            rows.append([
                _safe(r.get("role", _DASH)),
                fmt_num(r.get("headcount", 0)),
                fmt_num(r.get("hours", 0), 1),
                fmt_currency(r.get("cost", 0)),
                fmt_pct(r.get("pct_of_total", r.get("cost_pct", r.get("pct_total", 0)))),
            ])
        parts.append(render_table(["Role", "Staff", "Hours", "Cost", "% Total"], rows, [1, 2, 3, 4]))
        parts.append('</div>')


    # ── Employee Roster ──
    roster = _gl(labor, "employee_roster", default=[])
    if roster:
        parts.append('<div style="margin-bottom:16px;">')
        parts.append(f'<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">EMPLOYEE ROSTER ({len(roster)} employees)</div>')
        rows = []
        for emp in roster:
            _emp_name = emp.get("employee", "")
            if not _emp_name or str(_emp_name).lower() in ("nan", "na", "none", ""):
                _emp_name = "Unknown"
            rows.append([
                _safe(_emp_name),
                _safe(emp.get("job_title", _DASH)),
                _safe(emp.get("in_date", _DASH)),
                _safe(emp.get("out_date", _DASH)),
                fmt_num(emp.get("total_hours", 0), 1),
                fmt_currency(emp.get("effective_wage", 0)),
                fmt_currency(emp.get("total_pay", 0)),
            ])
        parts.append(render_table(
            ["Employee", "Role", "In", "Out", "Hrs", "Wage", "Pay"],
            rows, [4, 5, 6]
        ))
        parts.append('</div>')

    content = "\n".join(parts)
    return render_card("Labor & Staffing", content,
                       subtitle="Overtime, staffing demand, daypart efficiency, and roster")


# ---------------------------------------------------------------------------
# Section 8: Payments
# ---------------------------------------------------------------------------

def render_payments(metrics: dict) -> str:
    pay = _g(metrics, "payments")
    if not pay:
        return ""

    parts = []

    # ── Payment Type Breakdown (Chart.js doughnut — Phase 0K bigger) ──
    type_bd = _gl(pay, "type_breakdown", default=[])
    if type_bd:
        pt_color_map = {"Credit Card": "#2db88a", "Cash": "#8cb82e", "Other": "#9b72c4"}
        pt_labels = [t.get("type", "Other") for t in type_bd]
        pt_values = [round(t.get("amount", 0), 2) for t in type_bd]
        pt_colors = [pt_color_map.get(t.get("type", "Other"), "#999") for t in type_bd]
        total_pay = sum(pt_values)
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">PAYMENT TYPE</div>')
        parts.append(render_chartjs_pie(pt_labels, pt_values, pt_colors, height=220, doughnut=True,
                                         center_text=fmt_currency(total_pay)))
        parts.append('</div>')

    # ── Card Type Breakdown (Chart.js doughnut) ──
    card_bd = _gl(pay, "card_breakdown", default=[])
    if card_bd:
        card_color_map = {"Visa": "#1a6eb5", "Mastercard": "#d9342b", "Amex": "#2db88a", "Discover": "#e8a830"}
        cd_labels = [c.get("card_type", "Other") for c in card_bd]
        cd_values = [round(c.get("amount", 0), 2) for c in card_bd]
        cd_colors = [card_color_map.get(c.get("card_type", "Other"), "#999") for c in card_bd]
        if cd_values:
            parts.append('<div style="margin-bottom:16px;">')
            parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">CARD TYPE MIX</div>')
            parts.append(render_chartjs_pie(cd_labels, cd_values, cd_colors, height=200, doughnut=True))
            parts.append('</div>')

    # ── 3P Platform Breakdown (Chart.js horizontal — Phase 0K bigger) ──
    platform_bd = _gl(pay, "platform_breakdown", default=[])
    if platform_bd:
        plat_labels = [p.get("platform", "") for p in platform_bd]
        plat_values = [round(p.get("amount", 0), 2) for p in platform_bd]
        parts.append('<div style="margin-bottom:16px;">')
        parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:4px;">3P PLATFORM BREAKDOWN</div>')
        parts.append(render_chartjs_bar(plat_labels,
                                         [{"label": "Revenue", "data": plat_values, "color": "#9b72c4"}],
                                         height=max(150, len(plat_labels) * 35 + 40), horizontal=True, dollar=True))
        parts.append('</div>')

    # ── Swiped vs Keyed ──
    svk = _g(pay, "swiped_vs_keyed", default={})
    if isinstance(svk, dict) and (svk.get("swiped", 0) > 0 or svk.get("keyed", 0) > 0):
        swiped = svk.get("swiped", 0)
        keyed = svk.get("keyed", 0)
        total = swiped + keyed
        keyed_pct = round(keyed / max(total, 1) * 100, 1)
        severity = "amber" if keyed_pct > 20 else "blue"
        parts.append(render_insight(
            f'Swiped: {fmt_currency(swiped)} vs Keyed: {fmt_currency(keyed)} ({fmt_pct(keyed_pct)} keyed)',
            severity=severity, tag="CARD ENTRY"
        ))

    # ── Tip Summary ──
    tips = _g(pay, "tip_summary", default={})
    if isinstance(tips, dict) and tips.get("total_tips", 0) > 0:
        parts.append('<div style="margin-bottom:16px;">')
        tip_stats = [
            render_stat("Total Tips", fmt_currency(tips.get("total_tips", 0)), color="var(--green)"),
            render_stat("Avg Tip %", fmt_pct(tips.get("avg_tip_pct", 0))),
            render_stat("Tip Count", fmt_num(tips.get("tip_count", 0))),
        ]
        parts.append(render_stat_grid(tip_stats))
        parts.append('</div>')

    content = "\n".join(parts)
    return render_card("Payments & Cash", content,
                       subtitle="Payment types, card mix, 3P platforms, tips")


# ---------------------------------------------------------------------------
# Section 9: Customer Intelligence
# ---------------------------------------------------------------------------

def render_customers(metrics: dict) -> str:
    cust = _g(metrics, "customers")
    if not cust:
        return ""

    parts = []

    # ── Direct vs 3P Split (doughnut) ──
    direct_count = _g(cust, "direct_customers", default=0)
    tp_orders = _g(cust, "tp_orders", default=0)
    total_unique = _g(cust, "unique_customers", default=0)
    if direct_count or tp_orders:
        parts.append(render_chartjs_pie(
            ["Direct (Walk-In + Online)", "Third-Party"],
            [direct_count, tp_orders],
            colors=["#4a7c1f", "#9b72c4"],
            height=200, doughnut=True,
            center_text=f"{total_unique} total",
        ))

    # ── DIRECT CUSTOMERS ──
    parts.append('<div style="margin-top:16px;margin-bottom:16px;">')
    parts.append('<div style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:8px;">Direct Customers (Walk-In + Online)</div>')
    parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:10px;">'
                 'These are customers you can market to directly.</div>')

    direct_stats = [
        render_stat("Direct Customers", fmt_num(direct_count), color="var(--green)"),
        render_stat("Phone Capture", fmt_pct(_g(cust, "direct_phone_rate")),
                     subtitle=f"{_g(cust, 'direct_phone_count', default=0)} of {direct_count}"),
        render_stat("Email Capture", fmt_pct(_g(cust, "direct_email_rate")),
                     subtitle=f"{_g(cust, 'direct_email_count', default=0)} emails"),
        render_stat("Avg Party Size", fmt_num(_g(cust, "avg_party_size"), 1)),
    ]
    parts.append(render_stat_grid(direct_stats))

    # Contact Capture by Channel (Chart.js horizontal bar)
    capture_ch = _g(cust, "capture_by_channel", default={})
    if capture_ch:
        cap_labels = []
        cap_values = []
        cap_colors = []
        color_map = {"Walk-In": "#4a7c1f", "Online": "#4a9cd8"}
        for ch_name in ["Walk-In", "Online"]:
            ch_data = capture_ch.get(ch_name)
            if not ch_data:
                continue
            ph_rate = ch_data.get("phone_rate", 0)
            em_rate = ch_data.get("email_rate", 0)
            cap_labels.extend([f"{ch_name} Phone", f"{ch_name} Email"])
            cap_values.extend([round(ph_rate, 1), round(em_rate, 1)])
            c = color_map.get(ch_name, "#4a9cd8")
            cap_colors.extend([c, c])
        if cap_labels:
            parts.append('<div style="font-size:11px;color:var(--muted);margin:8px 0 4px;">CONTACT CAPTURE RATES</div>')
            parts.append(render_chartjs_bar(
                cap_labels,
                [{"label": "Capture %", "data": cap_values, "backgroundColor": cap_colors}],
                height=160, horizontal=True, pct=True,
            ))

    # Direct Top Spenders
    direct_top = _gl(cust, "direct_top_spenders", default=[])
    if direct_top:
        parts.append('<div style="font-size:11px;color:var(--muted);margin:12px 0 4px;">TOP DIRECT SPENDERS (by total spend)</div>')
        rows = []
        for s in direct_top[:8]:
            name = s.get("name", "Unknown")
            if name == "nan" or not name:
                name = "Unknown"
            rows.append([
                _safe(name[:25]),
                fmt_currency(s.get("total_spend", 0)),
                fmt_num(s.get("orders", 0)),
                fmt_currency(s.get("avg_order", 0)),
            ])
        parts.append(render_table(["Customer", "Spent", "Orders", "Avg Order"], rows, [1, 2, 3]))

    # Most Frequent Customers (by visit count — excludes one-time visitors)
    most_freq = _gl(cust, "most_frequent", default=[])
    if most_freq:
        parts.append('<div style="font-size:11px;color:var(--green);margin:16px 0 4px;">MOST FREQUENT VISITORS (2+ orders)</div>')
        freq_rows = []
        for s in most_freq[:10]:
            name = s.get("name", "Unknown")
            if name == "nan" or not name:
                name = "Unknown"
            freq_rows.append([
                _safe(name[:25]),
                fmt_num(s.get("orders", 0)),
                fmt_currency(s.get("total_spend", 0)),
                fmt_currency(s.get("avg_order", 0)),
            ])
        parts.append(render_table(["Customer", "Visits", "Total Spent", "Avg Order"], freq_rows, [1, 2, 3]))
    parts.append('</div>')

    # ── THIRD-PARTY CUSTOMERS ──
    parts.append('<div style="margin-bottom:16px;">')
    parts.append('<div style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:8px;">Third-Party Customers</div>')
    parts.append('<div style="font-size:11px;color:var(--muted);margin-bottom:10px;">'
                 'Customer data is owned by the platform — contact info not available for direct marketing.</div>')

    tp_stats = [
        render_stat("3P Orders", fmt_num(tp_orders), color="var(--purple)"),
        render_stat("Unique Cards", fmt_num(_g(cust, "unique_card_customers"))),
    ]
    parts.append(render_stat_grid(tp_stats))

    # All-customer spend analysis
    avg_spend = _g(cust, "avg_spend_per_customer", default=0)
    if avg_spend:
        parts.append(render_insight(
            f'Average spend per customer (all channels): <span class="hl">{fmt_currency(avg_spend)}</span>',
            severity="blue", tag="AVG SPEND"
        ))
    parts.append('</div>')

    content = "\n".join(parts)
    return render_card("Customer Intelligence", content,
                       subtitle="Direct vs third-party customer analysis")


# ---------------------------------------------------------------------------
# Weather & Seasonality
# ---------------------------------------------------------------------------

_WEATHER_ICONS = {
    "Clear": "&#9728;&#65039;",  # sun
    "Mostly Clear": "&#127780;&#65039;",  # sun behind cloud
    "Partly Cloudy": "&#9925;",  # sun behind cloud
    "Overcast": "&#9729;&#65039;",  # cloud
    "Fog": "&#127787;&#65039;",  # fog
    "Freezing Fog": "&#127787;&#65039;",
    "Light Drizzle": "&#127782;&#65039;",  # rain
    "Drizzle": "&#127782;&#65039;",
    "Heavy Drizzle": "&#127782;&#65039;",
    "Light Rain": "&#127783;&#65039;",
    "Rain": "&#127783;&#65039;",
    "Heavy Rain": "&#127783;&#65039;",
    "Light Snow": "&#127784;&#65039;",  # snowflake
    "Snow": "&#127784;&#65039;",
    "Heavy Snow": "&#127784;&#65039;",
    "Thunderstorm": "&#9889;",  # lightning
}


def render_weather_seasonality(metrics: dict) -> str:
    """Render weather conditions, correlations, and seasonality analysis."""
    w = metrics.get("weather")
    if not w:
        return ""

    parts = []

    # ── Weather card row ──
    icon = _WEATHER_ICONS.get(w.get("conditions", ""), "&#127777;&#65039;")
    temp_h = w.get("temp_high")
    temp_l = w.get("temp_low")
    conditions = _safe(w.get("conditions", ""))
    sunset = _safe(w.get("sunset", ""))
    sunrise = _safe(w.get("sunrise", ""))
    precip = w.get("precipitation_inches", 0)
    snow = w.get("snow_inches", 0)
    wind = w.get("wind_max_mph")
    day_len = w.get("day_length_hours")

    # Weather summary row
    parts.append('<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;margin-bottom:16px;">')

    # Temp card
    temp_str = f"{temp_h:.0f}&deg;/{temp_l:.0f}&deg;F" if temp_h is not None and temp_l is not None else "N/A"
    parts.append(f'''<div style="text-align:center;padding:12px;background:var(--surface2);border-radius:8px;">
        <div style="font-size:24px;">{icon}</div>
        <div style="font-size:16px;font-weight:600;color:var(--text);">{temp_str}</div>
        <div style="font-size:11px;color:var(--muted);">{conditions}</div>
    </div>''')

    # Precipitation
    if snow > 0:
        precip_str = f'{snow:.1f}" snow'
    elif precip > 0:
        precip_str = f'{precip:.2f}" rain'
    else:
        precip_str = "None"
    parts.append(f'''<div style="text-align:center;padding:12px;background:var(--surface2);border-radius:8px;">
        <div style="font-size:24px;">&#127782;&#65039;</div>
        <div style="font-size:16px;font-weight:600;color:var(--text);">{precip_str}</div>
        <div style="font-size:11px;color:var(--muted);">Precipitation</div>
    </div>''')

    # Wind
    wind_str = f"{wind:.0f} mph" if wind else "N/A"
    parts.append(f'''<div style="text-align:center;padding:12px;background:var(--surface2);border-radius:8px;">
        <div style="font-size:24px;">&#127788;&#65039;</div>
        <div style="font-size:16px;font-weight:600;color:var(--text);">{wind_str}</div>
        <div style="font-size:11px;color:var(--muted);">Max Wind</div>
    </div>''')

    # Sunset
    parts.append(f'''<div style="text-align:center;padding:12px;background:var(--surface2);border-radius:8px;">
        <div style="font-size:24px;">&#127751;</div>
        <div style="font-size:16px;font-weight:600;color:var(--text);">{sunset or "N/A"}</div>
        <div style="font-size:11px;color:var(--muted);">Sunset{f" ({day_len}h daylight)" if day_len else ""}</div>
    </div>''')

    parts.append('</div>')

    # ── Events ──
    events = w.get("events", [])
    if events:
        for ev in events:
            parts.append(render_insight(
                f"&#127881; {_safe(ev.get('name', 'Event'))}",
                severity="green", tag=ev.get("type", "event").upper()
            ))

    # ── Bad Weather Impact ──
    bad_avg = w.get("bad_weather_avg_revenue")
    good_avg = w.get("good_weather_avg_revenue")
    impact = w.get("weather_impact_pct")
    if bad_avg and good_avg and impact is not None:
        color = "red" if impact < -5 else ("green" if impact > 5 else "muted")
        parts.append(f'''<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin:12px 0;">
            <div style="text-align:center;padding:10px;background:var(--surface2);border-radius:8px;">
                <div style="font-size:11px;color:var(--muted);">Clear Days Avg</div>
                <div style="font-size:16px;font-weight:600;color:var(--green);">{fmt_currency(good_avg)}</div>
                <div style="font-size:10px;color:var(--muted);">{w.get("good_weather_days", 0)} days</div>
            </div>
            <div style="text-align:center;padding:10px;background:var(--surface2);border-radius:8px;">
                <div style="font-size:11px;color:var(--muted);">Bad Weather Avg</div>
                <div style="font-size:16px;font-weight:600;color:var(--red);">{fmt_currency(bad_avg)}</div>
                <div style="font-size:10px;color:var(--muted);">{w.get("bad_weather_days", 0)} days</div>
            </div>
            <div style="text-align:center;padding:10px;background:var(--surface2);border-radius:8px;">
                <div style="font-size:11px;color:var(--muted);">Weather Impact</div>
                <div style="font-size:16px;font-weight:600;color:var(--{color});">{impact:+.1f}%</div>
                <div style="font-size:10px;color:var(--muted);">bad vs clear</div>
            </div>
        </div>''')

    # ── Revenue vs Temperature scatter chart ──
    scatter = w.get("temp_revenue_scatter", [])
    if len(scatter) >= 5:
        temps = [p["temp"] for p in scatter]
        revs = [p["revenue"] for p in scatter]
        parts.append(render_chartjs_bar(
            [f'{t:.0f}F' for t in temps],
            [{"label": "Revenue", "data": revs, "color": "#4a9cd8"}],
            height=180, dollar=True,
        ))
        parts.append('<div style="text-align:center;font-size:11px;color:var(--muted);margin-top:-8px;margin-bottom:12px;">Revenue by Temperature (last 30 days)</div>')

    # ── Day-of-Week Seasonality chart ──
    dow_avgs = w.get("dow_avg_revenue", {})
    if dow_avgs:
        dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        dow_labels = [d[:3] for d in dow_order if d in dow_avgs]
        dow_values = [dow_avgs[d] for d in dow_order if d in dow_avgs]
        if dow_labels:
            # Color bars: highlight best day green, worst day red
            max_rev = max(dow_values) if dow_values else 0
            min_rev = min(dow_values) if dow_values else 0
            colors = []
            for v in dow_values:
                if v == max_rev:
                    colors.append("#4a7c1f")
                elif v == min_rev:
                    colors.append("#e86040")
                else:
                    colors.append("#8cb82e")

            parts.append(render_chartjs_bar(
                dow_labels,
                [{"label": "Avg Revenue", "data": dow_values, "backgroundColor": colors}],
                height=160, dollar=True,
            ))
            parts.append('<div style="text-align:center;font-size:11px;color:var(--muted);margin-top:-8px;margin-bottom:12px;">Average Revenue by Day of Week (last 30 days)</div>')

    # ── Daily Revenue Trend ──
    trend = w.get("daily_revenue_trend", [])
    if len(trend) >= 7:
        trend_labels = [t["date"][4:6] + "/" + t["date"][6:] for t in trend]
        trend_values = [t["revenue"] for t in trend]
        parts.append(render_chartjs_line(
            trend_labels,
            [{"label": "Daily Revenue", "data": trend_values, "color": "#2db88a"}],
            height=160,
        ))
        parts.append('<div style="text-align:center;font-size:11px;color:var(--muted);margin-top:-8px;margin-bottom:12px;">Revenue Trend (last 30 days)</div>')

    if not parts:
        return ""

    content = "\n".join(parts)
    return render_card("Weather & Seasonality", content,
                       subtitle="Weather conditions, impact analysis & trends")


# ---------------------------------------------------------------------------
# Weather Analysis Workbench (Range Mode)
# ---------------------------------------------------------------------------

def _wx_filter_row(label, filter_key, options):
    """Build a row of filter buttons for the weather workbench."""
    buttons = []
    for i, (val, text) in enumerate(options):
        if i == 0:
            active_style = 'background:var(--livite-green);color:#fff;font-weight:600;'
            active_cls = ' wx-active'
        else:
            active_style = 'background:var(--surface2);color:var(--muted);'
            active_cls = ''
        buttons.append(
            f'<button class="wx-btn{active_cls}" data-filter="{filter_key}" '
            f'data-value="{val}" onclick="wxFilter(\'{filter_key}\',\'{val}\',this)" '
            f'style="padding:4px 11px;border:1px solid var(--border);border-radius:6px;'
            f'font-size:10px;cursor:pointer;font-family:inherit;{active_style}">'
            f'{text}</button>'
        )
    return (
        '<div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;flex-wrap:wrap;">'
        f'<span style="font-size:9px;text-transform:uppercase;letter-spacing:1px;'
        f'color:var(--muted);min-width:65px;font-weight:600;">{label}</span>'
        + ''.join(buttons) +
        '</div>'
    )


def render_weather_analysis(metrics: dict) -> str:
    """Render interactive weather analysis workbench for range dashboards."""
    if not metrics.get('is_range'):
        return ""
    wx_days = metrics.get('weather_workbench_days', [])
    if not wx_days or len(wx_days) < 3:
        return ""

    wx_json = _js(wx_days)

    # Filter rows
    filter_html = ''
    filter_html += _wx_filter_row("Weather", "weather_type", [
        ("All", "All"), ("Clear", "Clear"), ("Cloudy", "Cloudy"),
        ("Rain", "Rain"), ("Snow", "Snow"), ("Windy", "Windy"),
    ])
    filter_html += _wx_filter_row("Temp", "temp_band", [
        ("All", "All"), ("Cold", "Cold (&lt;40)"),
        ("Cool", "Cool (40-60)"), ("Warm", "Warm (60-80)"),
        ("Hot", "Hot (&gt;80)"),
    ])
    filter_html += _wx_filter_row("Category", "menu_category", [
        ("All", "All"), ("Smoothies", "Smoothies"),
        ("Wraps & Paninis", "Wraps"), ("Salads & Bowls", "Salads/Bowls"),
        ("Juice", "Juice"), ("Soup", "Soup"),
    ])
    filter_html += _wx_filter_row("Channel", "channel", [
        ("All", "All"), ("Walk-In", "Walk-In"),
        ("Online", "Online"), ("3P", "Delivery"),
    ])

    # Summary cards (compact)
    summary_html = (
        '<div id="wx-summary" style="display:grid;grid-template-columns:repeat(4,1fr);'
        'gap:8px;margin:10px 0;">'
        '<div style="text-align:center;padding:8px 6px;background:var(--surface2);border-radius:6px;">'
        '<div style="font-size:8px;color:var(--muted);text-transform:uppercase;">Days</div>'
        '<div id="wx-days-count" style="font-size:18px;font-weight:700;color:var(--text);">--</div>'
        '<div id="wx-days-pct" style="font-size:8px;color:var(--muted);">of -- total</div>'
        '</div>'
        '<div style="text-align:center;padding:8px 6px;background:var(--surface2);border-radius:6px;">'
        '<div style="font-size:8px;color:var(--muted);text-transform:uppercase;">Avg Revenue</div>'
        '<div id="wx-avg-rev" style="font-size:18px;font-weight:700;color:#4a7c1f;">--</div>'
        '<div id="wx-rev-delta" style="font-size:8px;color:var(--muted);">vs all days</div>'
        '</div>'
        '<div style="text-align:center;padding:8px 6px;background:var(--surface2);border-radius:6px;">'
        '<div style="font-size:8px;color:var(--muted);text-transform:uppercase;">Avg Orders</div>'
        '<div id="wx-avg-orders" style="font-size:18px;font-weight:700;color:#3498db;">--</div>'
        '<div id="wx-orders-delta" style="font-size:8px;color:var(--muted);">vs all days</div>'
        '</div>'
        '<div style="text-align:center;padding:8px 6px;background:var(--surface2);border-radius:6px;">'
        '<div style="font-size:8px;color:var(--muted);text-transform:uppercase;">Avg Check</div>'
        '<div id="wx-avg-check" style="font-size:18px;font-weight:700;color:#9b59b6;">--</div>'
        '<div id="wx-check-delta" style="font-size:8px;color:var(--muted);">vs all days</div>'
        '</div>'
        '</div>'
    )

    # Timeline chart (compact)
    timeline_html = (
        '<div style="background:var(--surface2);border-radius:6px;padding:10px;margin:10px 0;">'
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">'
        '<div id="wx-timeline-title" style="font-size:9px;color:var(--muted);'
        'text-transform:uppercase;font-weight:600;">Daily Revenue Timeline</div>'
        '<div id="wx-timeline-legend" style="font-size:8px;color:var(--muted);"></div>'
        '</div>'
        '<div style="height:150px;position:relative;"><canvas id="wx-timeline-chart"></canvas></div>'
        '</div>'
    )

    # Auto-insights container (compact)
    insights_html = (
        '<div id="wx-insights" style="background:var(--surface2);border-radius:6px;'
        'padding:10px;margin-bottom:10px;">'
        '<div style="font-size:9px;color:var(--muted);text-transform:uppercase;'
        'font-weight:600;margin-bottom:6px;">Weather Insights</div>'
        '<div id="wx-insights-body" style="font-size:11px;color:var(--text);line-height:1.6;">'
        '</div></div>'
    )

    compare_html = ''  # removed

    # Chart containers — pie charts for channel and category mix
    chart_html = (
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:10px 0;">'
        '<div style="background:var(--surface2);border-radius:6px;padding:10px;">'
        '<div id="wx-pie-channel-title" style="font-size:9px;color:var(--muted);'
        'text-transform:uppercase;margin-bottom:4px;font-weight:600;">Channel Mix</div>'
        '<div style="height:180px;position:relative;"><canvas id="wx-pie-channel"></canvas></div>'
        '</div>'
        '<div style="background:var(--surface2);border-radius:6px;padding:10px;">'
        '<div id="wx-pie-category-title" style="font-size:9px;color:var(--muted);'
        'text-transform:uppercase;margin-bottom:4px;font-weight:600;">Category Mix</div>'
        '<div style="height:180px;position:relative;"><canvas id="wx-pie-category"></canvas></div>'
        '</div>'
        '</div>'
    )

    # Table container (compact)
    table_html = (
        '<div id="wx-table-wrap" style="margin-top:10px;overflow-x:auto;">'
        '<table id="wx-table" style="width:100%;font-size:10px;border-collapse:collapse;">'
        '<thead><tr>'
        '<th style="cursor:pointer;padding:6px 4px;text-align:left;border-bottom:2px solid var(--border);" '
        'onclick="wxSort(\'date\')">Date</th>'
        '<th style="cursor:pointer;padding:6px 4px;text-align:left;border-bottom:2px solid var(--border);" '
        'onclick="wxSort(\'conditions\')">Weather</th>'
        '<th style="cursor:pointer;padding:6px 4px;text-align:right;border-bottom:2px solid var(--border);" '
        'onclick="wxSort(\'temp_high\')">High</th>'
        '<th style="cursor:pointer;padding:6px 4px;text-align:right;border-bottom:2px solid var(--border);" '
        'onclick="wxSort(\'revenue\')">Revenue</th>'
        '<th style="cursor:pointer;padding:6px 4px;text-align:right;border-bottom:2px solid var(--border);" '
        'onclick="wxSort(\'orders\')">Orders</th>'
        '<th style="cursor:pointer;padding:6px 4px;text-align:right;border-bottom:2px solid var(--border);" '
        'onclick="wxSort(\'avg_check\')">Avg Check</th>'
        '</tr></thead>'
        '<tbody id="wx-table-body"></tbody>'
        '</table></div>'
    )

    # Hint text
    hint = '<div class="toggle-hint" style="margin-bottom:6px;">Click filters to slice data by weather, temperature, category, or channel</div>'

    # Build the JS block using string concatenation to avoid f-string brace issues
    js_block = '<script>\nvar wxDays=' + wx_json + ';\n' + r"""
var wxFilters={weather_type:'All',temp_band:'All',menu_category:'All',channel:'All'};
var wxSortKey='date',wxSortAsc=true;
var wxPieCh=null,wxPieCat=null,wxTChart=null;

function wxFilter(key,val,btn){
  wxFilters[key]=val;
  var sibs=btn.parentElement.querySelectorAll('.wx-btn');
  sibs.forEach(function(b){
    if(b===btn){b.style.background='var(--livite-green)';b.style.color='#fff';b.style.fontWeight='600';b.classList.add('wx-active');}
    else{b.style.background='var(--surface2)';b.style.color='var(--muted)';b.style.fontWeight='normal';b.classList.remove('wx-active');}
  });
  wxUpdate();
}
function wxGetFiltered(){
  return wxDays.filter(function(d){
    if(wxFilters.weather_type!=='All'&&d.weather_type!==wxFilters.weather_type)return false;
    if(wxFilters.temp_band!=='All'&&d.temp_band!==wxFilters.temp_band)return false;
    if(wxFilters.channel!=='All'){var ch=d.channels[wxFilters.channel];if(!ch||ch.orders===0)return false;}
    if(wxFilters.menu_category!=='All'){var mg=d.menu_groups[wxFilters.menu_category];if(!mg||mg.qty===0)return false;}
    return true;
  });
}
function wxMetric(d,m){
  var ch=wxFilters.channel,cat=wxFilters.menu_category;
  // Both channel + category active: use cross-tab
  if(ch!=='All'&&cat!=='All'){
    var cx=d.cx&&d.cx[ch]&&d.cx[ch][cat];if(!cx)return 0;
    if(m==='revenue')return cx.revenue;if(m==='orders')return cx.qty;
    if(m==='avg_check')return cx.qty>0?cx.revenue/cx.qty:0;return 0;
  }
  if(ch!=='All'){var c=d.channels[ch];if(!c)return 0;if(m==='revenue')return c.revenue;if(m==='orders')return c.orders;if(m==='avg_check')return c.orders>0?c.revenue/c.orders:0;}
  if(cat!=='All'){var mg=d.menu_groups[cat];if(!mg)return 0;if(m==='revenue')return mg.revenue;if(m==='orders')return mg.qty;if(m==='avg_check')return mg.qty>0?mg.revenue/mg.qty:0;}
  return d[m]||0;
}
function wxFmt(v){return '$'+Math.round(v).toLocaleString();}
function wxDelta(pct,suffix){
  var c=pct>0?'#4a7c1f':(pct<0?'#d9342b':'var(--muted)');
  var a=pct>0?'&#9650; +':(pct<0?'&#9660; ':'');
  return '<span style="color:'+c+';font-weight:600;">'+a+pct.toFixed(1)+'%</span> '+suffix;
}
function wxAvg(arr,key){var s=0;arr.forEach(function(d){s+=wxMetric(d,key);});return arr.length>0?s/arr.length:0;}

function wxUpdate(){
  var f=wxGetFiltered(),all=wxDays,n=f.length,t=all.length;
  var fRev=wxAvg(f,'revenue'),fOrd=wxAvg(f,'orders'),fChk=n>0?(f.reduce(function(s,d){return s+wxMetric(d,'revenue');},0)/(f.reduce(function(s,d){return s+wxMetric(d,'orders');},0)||1)):0;
  var aRev=wxAvg(all,'revenue'),aOrd=wxAvg(all,'orders'),aChk=t>0?(all.reduce(function(s,d){return s+wxMetric(d,'revenue');},0)/(all.reduce(function(s,d){return s+wxMetric(d,'orders');},0)||1)):0;

  document.getElementById('wx-days-count').textContent=n;
  document.getElementById('wx-days-pct').textContent='of '+t+' total';
  document.getElementById('wx-avg-rev').textContent=wxFmt(fRev);
  document.getElementById('wx-avg-orders').textContent=Math.round(fOrd);
  document.getElementById('wx-avg-check').textContent='$'+fChk.toFixed(2);

  var rd=aRev>0?((fRev-aRev)/aRev*100):0;
  var od=aOrd>0?((fOrd-aOrd)/aOrd*100):0;
  var cd=aChk>0?((fChk-aChk)/aChk*100):0;
  document.getElementById('wx-rev-delta').innerHTML=n<t?wxDelta(rd,'vs all'):'all days';
  document.getElementById('wx-orders-delta').innerHTML=n<t?wxDelta(od,'vs all'):'all days';
  document.getElementById('wx-check-delta').innerHTML=n<t?wxDelta(cd,'vs all'):'all days';

  wxUpdateTimeline(f,all);
  wxUpdateInsights(f,all,n,t,fRev,aRev,fOrd,aOrd,fChk,aChk);
  wxUpdatePieChannel(f);
  wxUpdatePieCategory(f);
  wxUpdateTable(f);
}

function wxIntensityColor(d){
  var wt=wxFilters.weather_type,tb=wxFilters.temp_band;
  if(wt==='Rain'){var r=d.rain_inches||0;var a=Math.min(1,0.25+r*0.75);return 'rgba(74,156,216,'+a+')';}
  if(wt==='Snow'){var s=d.snow_inches||0;var a=Math.min(1,0.25+s*0.15);return 'rgba(155,114,196,'+a+')';}
  if(wt==='Clear')return 'rgba(232,168,48,0.8)';
  if(wt==='Cloudy')return 'rgba(122,114,101,0.6)';
  if(wt==='Windy'){var w=d.wind_max_mph||0;var a=Math.min(1,0.3+w*0.02);return 'rgba(45,184,138,'+a+')';}
  if(tb==='Cold')return 'rgba(74,156,216,0.7)';
  if(tb==='Cool')return 'rgba(45,184,138,0.7)';
  if(tb==='Warm')return 'rgba(232,168,48,0.7)';
  if(tb==='Hot')return 'rgba(232,96,64,0.7)';
  // Default: color by weather type
  var cm={Clear:'rgba(232,168,48,0.7)',Cloudy:'rgba(122,114,101,0.5)',Rain:'rgba(74,156,216,0.7)',Snow:'rgba(155,114,196,0.7)',Windy:'rgba(45,184,138,0.7)'};
  return cm[d.weather_type]||'rgba(140,184,46,0.7)';
}

function wxUpdateTimeline(filtered,all){
  var filteredSet={};filtered.forEach(function(d){filteredSet[d.date]=true;});
  var isFiltered=filtered.length<all.length;
  var labels=[],data=[],bgColors=[],borderColors=[],borderWidths=[];
  var legendParts=[];

  all.forEach(function(d){
    labels.push(d.label);
    data.push(wxMetric(d,'revenue'));
    var matched=filteredSet[d.date];
    if(!isFiltered){
      bgColors.push(wxIntensityColor(d));
      borderColors.push('transparent');
      borderWidths.push(0);
    }else if(matched){
      bgColors.push(wxIntensityColor(d));
      borderColors.push('rgba(0,0,0,0.15)');
      borderWidths.push(1);
    }else{
      bgColors.push('rgba(224,213,191,0.35)');
      borderColors.push('transparent');
      borderWidths.push(0);
    }
  });

  // Legend text
  var leg=document.getElementById('wx-timeline-legend');
  if(isFiltered){
    var wt=wxFilters.weather_type,tb=wxFilters.temp_band;
    if(wt!=='All')leg.innerHTML='<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:'+wxIntensityColor(filtered[0]||all[0])+';margin-right:3px;"></span>'+wt+' days highlighted';
    else if(tb!=='All')leg.innerHTML='<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:'+wxIntensityColor(filtered[0]||all[0])+';margin-right:3px;"></span>'+tb+' days highlighted';
    else leg.innerHTML=filtered.length+' days highlighted';
  }else{
    leg.innerHTML='colored by weather type';
  }

  // Title
  var title='Daily Revenue Timeline';
  if(wxFilters.menu_category!=='All')title=wxFilters.menu_category+' Revenue Timeline';
  if(wxFilters.channel!=='All')title=wxFilters.channel+' Revenue Timeline';
  document.getElementById('wx-timeline-title').textContent=title;

  if(wxTChart)wxTChart.destroy();
  wxTChart=new Chart(document.getElementById('wx-timeline-chart'),{
    type:'bar',
    data:{labels:labels,datasets:[{data:data,backgroundColor:bgColors,borderColor:borderColors,borderWidth:borderWidths,borderRadius:3,borderSkipped:false}]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false},tooltip:{callbacks:{
        title:function(items){var i=items[0].dataIndex;var d=wxDays[i];return d.label+' - '+d.conditions+(d.temp_high!==null?' ('+Math.round(d.temp_high)+'F)':'');},
        label:function(c){return '$'+c.raw.toLocaleString();}
      }}},
      scales:{y:{ticks:{callback:function(v){return '$'+v.toLocaleString();},font:{family:'DM Sans',size:9}},grid:{color:'rgba(0,0,0,0.04)'}},
              x:{ticks:{font:{family:'DM Sans',size:8},maxRotation:45},grid:{display:false}}}
    }
  });
}

function wxUpdateInsights(f,all,n,t,fRev,aRev,fOrd,aOrd,fChk,aChk){
  var ins=[];
  var anyFilter=wxFilters.weather_type!=='All'||wxFilters.temp_band!=='All'||wxFilters.menu_category!=='All'||wxFilters.channel!=='All';
  var cat=wxFilters.menu_category,chan=wxFilters.channel,wt=wxFilters.weather_type,tb=wxFilters.temp_band;
  var catLabel=cat!=='All'?cat:'total';
  var chanLabel=chan!=='All'?chan:'all channels';

  // Median helper
  function wxMedian(arr){arr.sort(function(a,b){return a-b;});var m=Math.floor(arr.length/2);return arr.length%2?arr[m]:(arr[m-1]+arr[m])/2;}
  // Overall median for comparison
  var allRevs=[];all.forEach(function(d){allRevs.push(wxMetric(d,'revenue'));});
  var medAll=allRevs.length>0?wxMedian(allRevs.slice()):0;

  if(!anyFilter){
    // Default insights: compare weather types using median (resists outliers)
    var types=['Rain','Snow','Clear','Cloudy'];
    types.forEach(function(tp){
      var sub=all.filter(function(d){return d.weather_type===tp;});
      if(sub.length<2)return;
      var revs=[];sub.forEach(function(d){revs.push(wxMetric(d,'revenue'));});
      var med=wxMedian(revs.slice());
      var avg=revs.reduce(function(a,b){return a+b;},0)/revs.length;
      var pct=medAll>0?((med-medAll)/medAll*100):0;
      var dir=pct>0?'higher':'lower';
      var clr=pct>0?'#4a7c1f':'#d9342b';
      var note='';
      // Flag if avg and median diverge significantly (outlier influence)
      if(sub.length>=3&&Math.abs(avg-med)/Math.max(med,1)*100>15){
        note=' <span style="color:var(--muted);font-size:10px;">(avg: '+wxFmt(avg)+' &mdash; outlier days pulling '+(avg>med?'up':'down')+')</span>';
      }
      ins.push('<b>'+tp+' days</b> ('+sub.length+'): median revenue <span style="color:'+clr+';font-weight:600;">'+pct.toFixed(1)+'% '+dir+'</span> vs overall'+note);
    });
    // Channel comparison on bad weather days
    var bad=all.filter(function(d){return d.bad_weather;});
    var good=all.filter(function(d){return !d.bad_weather;});
    if(bad.length>=2&&good.length>=2){
      var badWI=0,goodWI=0,bad3P=0,good3P=0;
      bad.forEach(function(d){var w=d.channels['Walk-In'];var t=d.channels['3P'];if(w)badWI+=w.orders;if(t)bad3P+=t.orders;});
      good.forEach(function(d){var w=d.channels['Walk-In'];var t=d.channels['3P'];if(w)goodWI+=w.orders;if(t)good3P+=t.orders;});
      var avgBadWI=badWI/bad.length,avgGoodWI=goodWI/good.length;
      var avgBad3P=bad3P/bad.length,avgGood3P=good3P/good.length;
      var wiPct=avgGoodWI>0?((avgBadWI-avgGoodWI)/avgGoodWI*100):0;
      var tpPct=avgGood3P>0?((avgBad3P-avgGood3P)/avgGood3P*100):0;
      ins.push('<b>Walk-in orders</b> on bad weather days: <span style="color:'+(wiPct<0?'#d9342b':'#4a7c1f')+';font-weight:600;">'+wiPct.toFixed(0)+'%</span> vs good weather');
      ins.push('<b>Delivery orders</b> on bad weather days: <span style="color:'+(tpPct>0?'#4a7c1f':'#d9342b')+';font-weight:600;">'+(tpPct>0?'+':'')+tpPct.toFixed(0)+'%</span> vs good weather');
    }
    // Worst weather day with context
    var worst=null;all.forEach(function(d){var r=d.rain_inches+d.snow_inches;if(!worst||r>worst.precip)worst={d:d,precip:r};});
    if(worst&&worst.precip>0){
      var wRev=worst.d.revenue;var wPct=medAll>0?((wRev-medAll)/medAll*100):0;
      ins.push('<b>Worst weather day</b>: '+worst.d.label+' ('+worst.d.conditions+', '+worst.precip.toFixed(1)+'in) &mdash; '+wxFmt(wRev)+' (<span style="color:'+(wPct<0?'#d9342b':'#4a7c1f')+';">'+wPct.toFixed(0)+'% vs median</span>)');
    }
  }else{
    // Filtered insights
    if(n===0){ins.push('No days match the current filters.');document.getElementById('wx-insights-body').innerHTML=ins.join('<br>');return;}
    var revPct=aRev>0?((fRev-aRev)/aRev*100):0;
    var ordPct=aOrd>0?((fOrd-aOrd)/aOrd*100):0;
    // Revenue insight
    var revDir=revPct>=0?'higher':'lower';
    var revClr=revPct>=0?'#4a7c1f':'#d9342b';
    ins.push('<b>'+catLabel+' revenue</b> ('+chanLabel+') is <span style="color:'+revClr+';font-weight:600;">'+Math.abs(revPct).toFixed(1)+'% '+revDir+'</span> on selected days vs all days');

    // Best and worst filtered day
    if(f.length>=2){
      var best=f[0],worst=f[0];
      f.forEach(function(d){var r=wxMetric(d,'revenue');if(r>wxMetric(best,'revenue'))best=d;if(r<wxMetric(worst,'revenue'))worst=d;});
      ins.push('<b>Best day</b>: '+best.label+' ('+best.conditions+') &mdash; '+wxFmt(wxMetric(best,'revenue')));
      ins.push('<b>Worst day</b>: '+worst.label+' ('+worst.conditions+') &mdash; '+wxFmt(wxMetric(worst,'revenue')));
    }

    // Precipitation severity insight
    if(wt==='Rain'&&f.length>=3){
      var light=f.filter(function(d){return d.rain_inches<=0.25;});
      var heavy=f.filter(function(d){return d.rain_inches>0.25;});
      if(light.length>0&&heavy.length>0){
        var lAvg=0,hAvg=0;light.forEach(function(d){lAvg+=wxMetric(d,'revenue');});hAvg=0;heavy.forEach(function(d){hAvg+=wxMetric(d,'revenue');});
        lAvg/=light.length;hAvg/=heavy.length;
        var diff=lAvg>0?((hAvg-lAvg)/lAvg*100):0;
        ins.push('<b>Light rain</b> (&le;0.25in, '+light.length+'d): '+wxFmt(lAvg)+' avg &mdash; <b>Heavy rain</b> (&gt;0.25in, '+heavy.length+'d): '+wxFmt(hAvg)+' avg (<span style="color:'+(diff<0?'#d9342b':'#4a7c1f')+';">'+diff.toFixed(0)+'%</span>)');
      }
    }
    if(wt==='Snow'&&f.length>=3){
      var light=f.filter(function(d){return d.snow_inches<=2;});
      var heavy=f.filter(function(d){return d.snow_inches>2;});
      if(light.length>0&&heavy.length>0){
        var lAvg=0,hAvg=0;light.forEach(function(d){lAvg+=wxMetric(d,'revenue');});heavy.forEach(function(d){hAvg+=wxMetric(d,'revenue');});
        lAvg/=light.length;hAvg/=heavy.length;
        var diff=lAvg>0?((hAvg-lAvg)/lAvg*100):0;
        ins.push('<b>Light snow</b> (&le;2in, '+light.length+'d): '+wxFmt(lAvg)+' avg &mdash; <b>Heavy snow</b> (&gt;2in, '+heavy.length+'d): '+wxFmt(hAvg)+' avg (<span style="color:'+(diff<0?'#d9342b':'#4a7c1f')+';">'+diff.toFixed(0)+'%</span>)');
      }
    }

    // Channel shift insight
    if(chan==='All'&&f.length>=2){
      var fWI=0,fTP=0,aWI=0,aTP=0;
      f.forEach(function(d){var w=d.channels['Walk-In'];var t=d.channels['3P'];if(w)fWI+=w.orders;if(t)fTP+=t.orders;});
      all.forEach(function(d){var w=d.channels['Walk-In'];var t=d.channels['3P'];if(w)aWI+=w.orders;if(t)aTP+=t.orders;});
      var fWIPct=fWI+fTP>0?(fWI/(fWI+fTP)*100):0;
      var aWIPct=aWI+aTP>0?(aWI/(aWI+aTP)*100):0;
      var shift=fWIPct-aWIPct;
      if(Math.abs(shift)>2){
        ins.push('<b>Channel shift</b>: Walk-in is '+fWIPct.toFixed(0)+'% of orders on selected days vs '+aWIPct.toFixed(0)+'% overall (<span style="color:'+(shift>0?'#4a7c1f':'#d9342b')+';">'+(shift>0?'+':'')+shift.toFixed(0)+'pt</span>)');
      }
    }
  }
  document.getElementById('wx-insights-body').innerHTML=ins.length>0?ins.join('<br>'):'<span style="color:var(--muted);">Select filters to see insights</span>';
}

function wxUpdatePieChannel(filtered){
  var totals={'Walk-In':0,Online:0,'3P':0};
  filtered.forEach(function(d){
    Object.keys(d.channels).forEach(function(ch){
      totals[ch]=(totals[ch]||0)+d.channels[ch].revenue;
    });
  });
  var labels=[],data=[],colors=[];
  var cm={'Walk-In':'#8cb82e',Online:'#4a9cd8','3P':'#e86040'};
  Object.keys(totals).forEach(function(ch){
    if(totals[ch]>0){labels.push(ch);data.push(Math.round(totals[ch]));colors.push(cm[ch]||'#7a7265');}
  });
  var total=data.reduce(function(a,b){return a+b;},0);
  var title='Channel Mix';
  if(wxFilters.weather_type!=='All')title='Channel Mix ('+wxFilters.weather_type+')';
  else if(wxFilters.temp_band!=='All')title='Channel Mix ('+wxFilters.temp_band+')';
  document.getElementById('wx-pie-channel-title').textContent=title;

  if(wxPieCh)wxPieCh.destroy();
  wxPieCh=new Chart(document.getElementById('wx-pie-channel'),{
    type:'doughnut',
    data:{labels:labels,datasets:[{data:data,backgroundColor:colors,borderWidth:0}]},
    options:{responsive:true,maintainAspectRatio:false,cutout:'55%',
      plugins:{legend:{display:true,position:'bottom',labels:{font:{family:'DM Sans',size:9},padding:8,usePointStyle:true,pointStyleWidth:8}},
        tooltip:{callbacks:{label:function(c){var pct=total>0?(c.raw/total*100).toFixed(1)+'%':'';return c.label+': $'+c.raw.toLocaleString()+' ('+pct+')';}}}}}
  });
}

function wxUpdatePieCategory(filtered){
  var cats=['Smoothies','Wraps & Paninis','Salads & Bowls','Juice','Soup'];
  var cm={Smoothies:'#8cb82e','Wraps & Paninis':'#4a9cd8','Salads & Bowls':'#2db88a',Juice:'#e8a830',Soup:'#9b72c4'};
  var totals={};
  cats.forEach(function(c){totals[c]=0;});
  var otherRev=0;
  filtered.forEach(function(d){
    Object.keys(d.menu_groups).forEach(function(g){
      if(totals[g]!==undefined)totals[g]+=d.menu_groups[g].revenue;
      else otherRev+=d.menu_groups[g].revenue;
    });
  });
  var labels=[],data=[],colors=[];
  cats.forEach(function(c){
    if(totals[c]>0){labels.push(c);data.push(Math.round(totals[c]));colors.push(cm[c]||'#7a7265');}
  });
  if(otherRev>0){labels.push('Other');data.push(Math.round(otherRev));colors.push('#7a7265');}
  var total=data.reduce(function(a,b){return a+b;},0);
  var title='Category Mix';
  if(wxFilters.weather_type!=='All')title='Category Mix ('+wxFilters.weather_type+')';
  else if(wxFilters.temp_band!=='All')title='Category Mix ('+wxFilters.temp_band+')';
  document.getElementById('wx-pie-category-title').textContent=title;

  if(wxPieCat)wxPieCat.destroy();
  wxPieCat=new Chart(document.getElementById('wx-pie-category'),{
    type:'doughnut',
    data:{labels:labels,datasets:[{data:data,backgroundColor:colors,borderWidth:0}]},
    options:{responsive:true,maintainAspectRatio:false,cutout:'55%',
      plugins:{legend:{display:true,position:'bottom',labels:{font:{family:'DM Sans',size:9},padding:8,usePointStyle:true,pointStyleWidth:8}},
        tooltip:{callbacks:{label:function(c){var pct=total>0?(c.raw/total*100).toFixed(1)+'%':'';return c.label+': $'+c.raw.toLocaleString()+' ('+pct+')';}}}}}
  });
}

function wxUpdateTable(filtered){
  filtered.sort(function(a,b){
    var av=wxSortKey==='revenue'||wxSortKey==='orders'||wxSortKey==='avg_check'?wxMetric(a,wxSortKey):a[wxSortKey];
    var bv=wxSortKey==='revenue'||wxSortKey==='orders'||wxSortKey==='avg_check'?wxMetric(b,wxSortKey):b[wxSortKey];
    if(typeof av==='string')return wxSortAsc?av.localeCompare(bv):bv.localeCompare(av);
    return wxSortAsc?(av||0)-(bv||0):(bv||0)-(av||0);
  });
  var h='';
  filtered.forEach(function(d){
    var rv=wxMetric(d,'revenue'),od=wxMetric(d,'orders'),ck=od>0?rv/od:0;
    h+='<tr style="border-bottom:1px solid var(--border);">'+
      '<td style="padding:4px 3px;font-weight:600;font-size:10px;">'+d.label+'</td>'+
      '<td style="padding:4px 3px;font-size:10px;">'+d.conditions+(d.snow_inches>0?' ('+d.snow_inches.toFixed(1)+'in)':'')+(d.rain_inches>0?' ('+d.rain_inches.toFixed(1)+'in)':'')+'</td>'+
      '<td style="padding:4px 3px;text-align:right;font-size:10px;">'+(d.temp_high!==null?Math.round(d.temp_high)+'&deg;F':'--')+'</td>'+
      '<td style="padding:4px 3px;text-align:right;font-weight:600;font-size:10px;">'+wxFmt(rv)+'</td>'+
      '<td style="padding:4px 3px;text-align:right;font-size:10px;">'+od+'</td>'+
      '<td style="padding:4px 3px;text-align:right;font-size:10px;">$'+ck.toFixed(2)+'</td>'+
      '</tr>';
  });
  document.getElementById('wx-table-body').innerHTML=h;
}

function wxSort(key){
  if(wxSortKey===key){wxSortAsc=!wxSortAsc;}else{wxSortKey=key;wxSortAsc=key==='date';}
  wxUpdate();
}

wxUpdate();
""" + '</script>'

    content = filter_html + hint + summary_html + timeline_html + insights_html + compare_html + chart_html + table_html + js_block
    return render_card("Weather Analysis", content,
                       subtitle="Filter by weather, temperature, menu category & channel")


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

def render_footer(metrics: dict) -> str:
    footer_parts = []
    date_display = _g(metrics, "date_display", default="")
    if date_display:
        footer_parts.append(date_display)
    footer_parts.append("Livite Washington Square")
    inner = " &middot; ".join(footer_parts)
    return (
        f'<div style="text-align:center;padding:20px;color:var(--muted);font-size:11px;">'
        f'{inner}'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# CSS Design System
# ---------------------------------------------------------------------------

_CSS = """\
:root {
  /* Livite Brand — warm cream theme */
  --bg: #F5EDDC; --surface: #FFFEF9; --surface2: #f0e8d6;
  --border: rgba(0,0,0,0.08); --border-strong: rgba(0,0,0,0.12);
  --text: #292524; --muted: #a8a29e; --text-secondary: #57534e;
  /* Livite Brand Colors */
  --livite-green: #475417; --livite-lime: #8cb82e; --livite-cream: #F5EDDC;
  --accent-dim: rgba(71,84,23,0.08); --accent-border: rgba(71,84,23,0.15);
  /* Semantic colors */
  --green: #15803d; --red: #dc2626; --amber: #ca8a04; --blue: #1a6eb5;
  --purple: #7c4daa; --cyan: #0f8a6a; --pink: #c44a8a;
  --success: #15803d; --error: #dc2626; --warning: #ca8a04;
  /* Bar fill colors (softer for light bg) */
  --bar-green: #8cb82e; --bar-blue: #4a9cd8; --bar-purple: #9b72c4;
  --bar-cyan: #2db88a; --bar-amber: #e8a830; --bar-red: #e86040;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:'DM Sans',sans-serif; background:var(--bg); color:var(--text); padding:28px; max-width:1100px; margin:0 auto; line-height:1.6; }
.mono { font-family:'JetBrains Mono',monospace; }
h1 { font-size:24px; font-weight:700; margin-bottom:4px; color:var(--livite-green); }
h1 span { color:var(--livite-lime); }
.subtitle { color:var(--muted); font-size:13px; margin-bottom:28px; }
.card { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:22px; margin-bottom:16px; box-shadow:0 1px 3px rgba(0,0,0,0.06); }
.card h2 { font-size:12px; text-transform:uppercase; letter-spacing:1px; color:var(--muted); margin-bottom:14px; font-weight:600; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { text-align:left; color:var(--muted); font-weight:500; font-size:11px; text-transform:uppercase; letter-spacing:0.5px; padding:7px 10px; border-bottom:1px solid var(--border); }
td { padding:7px 10px; border-bottom:1px solid var(--border); }
.r { text-align:right; }
.n { font-family:'JetBrains Mono',monospace; font-size:12px; }
.grid-2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:14px; }
@media(max-width:800px){ .grid-2{grid-template-columns:1fr;} }
.grid-3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:14px; margin-bottom:14px; }
@media(max-width:800px){ .grid-3{grid-template-columns:1fr;} }
.stat-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; margin-bottom:16px; }
.stat { text-align:center; padding:14px 10px; background:var(--surface2); border-radius:10px; border:1px solid var(--border); }
.stat .l { font-size:10px; text-transform:uppercase; letter-spacing:0.7px; color:var(--muted); margin-bottom:4px; }
.stat .v { font-size:20px; font-weight:700; color:var(--text); white-space:nowrap; }
.stat .sub { font-size:10px; color:var(--muted); margin-top:2px; }
.insight { background:var(--surface2); border-left:3px solid var(--amber); border-radius:0 8px 8px 0; padding:14px 18px; margin-bottom:10px; font-size:13px; }
.insight .tag { font-size:10px; text-transform:uppercase; letter-spacing:1px; font-weight:600; margin-bottom:4px; }
.insight.red { border-left-color:var(--red); } .insight.red .tag { color:var(--red); }
.insight.green { border-left-color:var(--green); } .insight.green .tag { color:var(--green); }
.insight.blue { border-left-color:var(--blue); } .insight.blue .tag { color:var(--blue); }
.insight.amber .tag { color:var(--amber); }
.insight.purple { border-left-color:var(--purple); } .insight.purple .tag { color:var(--purple); }
.hl { color:var(--cyan); font-weight:600; font-family:'JetBrains Mono',monospace; }
.divider { border:none; border-top:1px solid var(--border); margin:28px 0; }
.bar-h { display:flex; align-items:center; gap:6px; margin:4px 0; font-size:12px; }
.bar-h .label { width:100px; flex-shrink:0; text-align:right; font-family:'JetBrains Mono',monospace; font-size:11px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text); }
.bar-track { flex:1; height:24px; background:var(--surface2); border-radius:6px; overflow:hidden; position:relative; }
.bar-fill { height:100%; border-radius:6px; position:relative; }
.bar-text { position:absolute; right:6px; top:50%; transform:translateY(-50%); font-size:10px; font-family:'JetBrains Mono',monospace; color:#2d2a24; white-space:nowrap; }
.bar-h .vals { width:140px; flex-shrink:0; font-family:'JetBrains Mono',monospace; font-size:11px; display:flex; gap:8px; }
.badge { display:inline-block; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:500; }
.badge.g { background:rgba(74,124,31,0.10); color:var(--green); }
.badge.r { background:rgba(217,52,43,0.10); color:var(--red); }
.badge.a { background:rgba(196,125,10,0.10); color:var(--amber); }
.badge.b { background:rgba(26,110,181,0.10); color:var(--blue); }
.badge.p { background:rgba(124,77,170,0.10); color:var(--purple); }
.heatmap-cell { transition:opacity 0.2s; cursor:default; }
.heatmap-cell:hover { opacity:0.85; }
/* Callout cards for fun insights */
.callout { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:16px 20px; margin-bottom:12px; display:flex; gap:12px; align-items:flex-start; }
.callout .emoji { font-size:24px; flex-shrink:0; line-height:1; }
.callout .body { flex:1; }
.callout .headline { font-weight:600; font-size:14px; margin-bottom:2px; }
.callout .detail { font-size:12px; color:var(--muted); }
.callout.hot { border-left:3px solid var(--red); }
.callout.win { border-left:3px solid var(--green); }
.callout.info { border-left:3px solid var(--blue); }
.callout.warn { border-left:3px solid var(--amber); }
/* Appendix toggle */
.appendix-toggle { cursor:pointer; display:flex; align-items:center; gap:6px; padding:10px 0; font-weight:600; color:var(--muted); font-size:13px; }
.appendix-content { display:none; }
.appendix-content.open { display:block; }
/* ── Chart container class ── */
.lvc { overflow:hidden; }
/* ── Mobile responsive (phones <600px) ── */
@media(max-width:600px){
  body { padding:10px 6px !important; font-size:13px; max-width:100% !important; }
  h1 { font-size:18px !important; }
  .subtitle { font-size:11px; margin-bottom:12px; }
  .card { padding:12px 8px; margin-bottom:8px; border-radius:8px; overflow-x:auto; -webkit-overflow-scrolling:touch; }
  .card h2 { font-size:11px; margin-bottom:8px; }
  .stat-grid { grid-template-columns:repeat(2,1fr) !important; gap:6px; }
  .stat { padding:8px 6px; border-radius:6px; min-width:0; }
  .stat .v { font-size:15px !important; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .stat .l { font-size:8px; }
  .stat .sub { font-size:8px; }
  table { font-size:10px; display:block; overflow-x:auto; -webkit-overflow-scrolling:touch; min-width:0; }
  th, td { padding:4px 5px; white-space:nowrap; }
  td.n { font-size:10px; }
  .bar-h .label { width:60px; font-size:9px; }
  .bar-h .vals { width:80px; font-size:9px; gap:4px; }
  .bar-track { height:16px; }
  .bar-text { font-size:8px !important; }
  .callout { padding:8px 10px; gap:6px; }
  .callout .headline { font-size:11px; }
  .callout .detail { font-size:10px; }
  .insight { padding:8px 10px; font-size:11px; }
  .appendix-toggle { font-size:11px; padding:12px 0; min-height:44px; }
  .divider { margin:12px 0; }
  /* Chart containers — JS handles height scaling; ensure no overflow */
  .lvc { overflow:hidden; }
  .lvc-pie { max-width:100% !important; }
  /* Comparison toggle buttons */
  .period-btn { padding:6px 10px !important; font-size:10px !important; }
  .item-toggle-btn { padding:6px 10px !important; font-size:10px !important; }
  /* Daily trend charts: full width on mobile */
  .lvc canvas { max-width:100%; }
}
/* ── Tablet (600-800px) ── */
@media(min-width:601px) and (max-width:800px){
  body { padding:16px 12px; }
  .stat-grid { grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); }
}
"""


# ---------------------------------------------------------------------------
# Loading Overlay
# Embed _LOADING_SNIPPET just before </body> in every page template.
# Shows a thin progress bar + "Loading…" chip whenever a link or form
# is clicked, so users know the server is working.
# ---------------------------------------------------------------------------

_LOADING_SNIPPET = """\
<div id="lv-bar" style="position:fixed;top:0;left:0;height:3px;width:0;
  background:linear-gradient(90deg,#4a9cd8,#8cb82e);z-index:9999;opacity:0;
  box-shadow:0 0 6px rgba(74,156,216,0.45);pointer-events:none;"></div>
<div id="lv-tip" style="display:none;position:fixed;bottom:20px;right:20px;
  background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:9px 16px;font-size:13px;color:var(--muted);gap:8px;align-items:center;
  box-shadow:0 4px 16px rgba(0,0,0,0.10);z-index:9998;">
  <span id="lv-spinner" style="display:inline-block;width:13px;height:13px;
    border:2px solid var(--border);border-top-color:#4a9cd8;border-radius:50%;
    animation:lv-spin 0.65s linear infinite;vertical-align:middle;margin-right:6px;"></span>Loading\u2026
</div>
<style>@keyframes lv-spin{to{transform:rotate(360deg)}}</style>
<script>
(function(){
  var bar=document.getElementById('lv-bar');
  var tip=document.getElementById('lv-tip');
  var tid;
  function show(){
    clearTimeout(tid);
    bar.style.transition='none';
    bar.style.width='0';
    bar.style.opacity='1';
    tip.style.display='flex';
    requestAnimationFrame(function(){
      bar.style.transition='width 0.35s ease';
      bar.style.width='38%';
      setTimeout(function(){
        bar.style.transition='width 5s ease';
        bar.style.width='78%';
      },350);
    });
    tid=setTimeout(hide,30000);
  }
  function hide(){
    clearTimeout(tid);
    bar.style.transition='width 0.15s ease';
    bar.style.width='100%';
    setTimeout(function(){
      bar.style.opacity='0';
      tip.style.display='none';
      bar.style.width='0';
    },150);
  }
  document.addEventListener('click',function(e){
    var a=e.target.closest('a');
    if(!a)return;
    var h=a.getAttribute('href')||'';
    if(!h||h[0]==='#'||/^(mailto|tel|javascript):/i.test(h)||a.target==='_blank')return;
    if(h.indexOf('//')!==-1&&h.indexOf(location.host)===-1)return;
    show();
  });
  document.addEventListener('submit',function(){show();});
  window.addEventListener('pageshow',function(e){if(e.persisted)hide();});
})();
</script>"""


# ---------------------------------------------------------------------------
# Main Assembly
# ---------------------------------------------------------------------------

def render_analyst_insights(insights: list) -> str:
    """Render analyst insights as severity-colored callout cards (no emojis)."""
    if not insights:
        return ""
    parts = []
    severity_class = {"red": "hot", "amber": "warn", "green": "win", "blue": "info"}
    for ins in insights:
        cls = severity_class.get(ins.get("severity", "blue"), "info")
        headline = ins.get("headline", "")
        detail = ins.get("detail", "")
        category = ins.get("category", "")
        parts.append(
            f'<div class="callout {cls}">'
            f'<div class="body">'
            f'<div style="font-size:9px;text-transform:uppercase;letter-spacing:0.5px;color:var(--muted);margin-bottom:1px;">{_safe(category)}</div>'
            f'<div class="headline">{_safe(headline)}</div>'
            f'<div class="detail">{_safe(detail)}</div>'
            f'</div></div>'
        )
    content = "\n".join(parts)
    return render_card("Today's Insights", content, subtitle="Auto-detected trends and highlights")


def _render_date_picker() -> str:
    """Render a date picker with always-visible preset buttons and collapsible custom picker."""
    inp = 'style="padding:5px 8px;font-size:12px;border:1px solid var(--border);border-radius:6px;background:var(--surface2);color:var(--text);font-family:inherit;"'
    btn = 'style="padding:6px 14px;font-size:11px;border:1px solid var(--border);border-radius:6px;background:var(--surface2);cursor:pointer;color:var(--text);"'
    preset_btn = 'style="padding:7px 14px;font-size:12px;font-weight:500;border:1px solid var(--border);border-radius:8px;background:var(--surface2);cursor:pointer;color:var(--livite-green);font-family:inherit;transition:background 0.15s,border-color 0.15s;"'
    return f'''<div class="card" style="margin-bottom:16px;padding:14px 16px;">
<div style="font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:10px;">Quick View</div>
<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;">
<button onclick="dpPreset('thisweek')" {preset_btn} onmouseover="this.style.background='var(--livite-green)';this.style.color='#fff';" onmouseout="this.style.background='var(--surface2)';this.style.color='var(--livite-green)';">This Week</button>
<button onclick="dpPreset('lastweek')" {preset_btn} onmouseover="this.style.background='var(--livite-green)';this.style.color='#fff';" onmouseout="this.style.background='var(--surface2)';this.style.color='var(--livite-green)';">Last Week</button>
<button onclick="dpPreset('last7')" {preset_btn} onmouseover="this.style.background='var(--livite-green)';this.style.color='#fff';" onmouseout="this.style.background='var(--surface2)';this.style.color='var(--livite-green)';">Last 7 Days</button>
<button onclick="dpPreset('last14')" {preset_btn} onmouseover="this.style.background='var(--livite-green)';this.style.color='#fff';" onmouseout="this.style.background='var(--surface2)';this.style.color='var(--livite-green)';">Last 14 Days</button>
<button onclick="dpPreset('last30')" {preset_btn} onmouseover="this.style.background='var(--livite-green)';this.style.color='#fff';" onmouseout="this.style.background='var(--surface2)';this.style.color='var(--livite-green)';">Last 30 Days</button>
<button onclick="dpPreset('thismonth')" {preset_btn} onmouseover="this.style.background='var(--livite-green)';this.style.color='#fff';" onmouseout="this.style.background='var(--surface2)';this.style.color='var(--livite-green)';">This Month</button>
<button onclick="dpPreset('lastmonth')" {preset_btn} onmouseover="this.style.background='var(--livite-green)';this.style.color='#fff';" onmouseout="this.style.background='var(--surface2)';this.style.color='var(--livite-green)';">Last Month</button>
<button onclick="dpPreset('ytd')" {preset_btn} onmouseover="this.style.background='var(--livite-green)';this.style.color='#fff';" onmouseout="this.style.background='var(--surface2)';this.style.color='var(--livite-green)';">Year to Date</button>
</div>
<div style="border-top:1px solid var(--border);padding-top:10px;">
<div style="display:flex;align-items:center;gap:8px;cursor:pointer;" onclick="var p=document.getElementById('date_picker_panel');p.style.display=p.style.display==='none'?'block':'none';this.querySelector('.arrow').textContent=p.style.display==='none'?'\u25b6':'\u25bc';">
<span class="arrow" style="font-size:10px;">\u25b6</span>
<span style="font-size:11px;color:var(--muted);">Custom date or range</span>
</div>
<div id="date_picker_panel" style="display:none;margin-top:10px;">
<div style="display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:12px;">
<label style="font-size:11px;color:var(--muted);font-weight:600;">Single Day:</label>
<input type="date" id="dp_single" min="2024-11-07" {inp}>
<button onclick="dpGoSingle()" {btn}>Go</button>
</div>
<div style="display:flex;flex-wrap:wrap;align-items:center;gap:10px;">
<label style="font-size:11px;color:var(--muted);font-weight:600;">Range:</label>
<input type="date" id="dp_start" min="2024-11-07" {inp}>
<span style="font-size:11px;color:var(--muted);">to</span>
<input type="date" id="dp_end" min="2024-11-07" {inp}>
<button onclick="dpGoRange()" {btn}>Go</button>
</div>
</div>
</div>
</div>
<script>
function dpGoSingle(){{
var v=document.getElementById('dp_single').value;
if(!v){{alert('Please select a date.');return;}}
var ds=v.replace(/-/g,'');
window.location.href='/daily/'+ds;
}}
function dpGoRange(){{
var s=document.getElementById('dp_start').value,e=document.getElementById('dp_end').value;
if(!s||!e){{alert('Please select both start and end dates.');return;}}
if(s>e){{alert('Start date must be before end date.');return;}}
var sd=s.replace(/-/g,''),ed=e.replace(/-/g,'');
if(sd===ed){{window.location.href='/daily/'+sd;return;}}
window.location.href='/range/'+sd+'/'+ed;
}}
function dpPreset(key){{
var now=new Date();
var y=now.getFullYear(),m=now.getMonth(),d=now.getDate();
var dow=now.getDay();  // 0=Sun
var s,e;
function pad(n){{return n<10?'0'+n:String(n);}}
function fmt(dt){{return String(dt.getFullYear())+pad(dt.getMonth()+1)+pad(dt.getDate());}}
if(key==='thisweek'){{
  var mon=new Date(y,m,d-(dow===0?6:dow-1));
  s=fmt(mon);e=fmt(new Date(y,m,d-1));
  if(s>e){{window.location.href='/daily/'+s;return;}}
}}else if(key==='lastweek'){{
  var lastMon=new Date(y,m,d-(dow===0?6:dow-1)-7);
  var lastSun=new Date(lastMon.getFullYear(),lastMon.getMonth(),lastMon.getDate()+6);
  s=fmt(lastMon);e=fmt(lastSun);
}}else if(key==='last7'){{
  s=fmt(new Date(y,m,d-7));e=fmt(new Date(y,m,d-1));
}}else if(key==='last14'){{
  s=fmt(new Date(y,m,d-14));e=fmt(new Date(y,m,d-1));
}}else if(key==='last30'){{
  s=fmt(new Date(y,m,d-30));e=fmt(new Date(y,m,d-1));
}}else if(key==='thismonth'){{
  var first=new Date(y,m,1);
  s=fmt(first);e=fmt(new Date(y,m,d-1));
  if(d===1){{window.location.href='/daily/'+s;return;}}
}}else if(key==='lastmonth'){{
  var first=new Date(y,m-1,1);
  var last=new Date(y,m,0);
  s=fmt(first);e=fmt(last);
}}else if(key==='ytd'){{
  s=fmt(new Date(y,0,1));e=fmt(new Date(y,m,d-1));
}}
if(s&&e)window.location.href='/range/'+s+'/'+e;
}}
</script>'''


# ---------------------------------------------------------------------------
# Daily Trend Charts (range dashboards only)
# ---------------------------------------------------------------------------

def render_daily_trends(metrics: dict) -> str:
    """Render daily trend charts for range dashboards.

    Only renders when daily_dates exists (range mode).
    """
    dates = metrics.get('daily_dates')
    if not dates or len(dates) < 2:
        return ""

    revenue = metrics.get('daily_revenue_series', [])
    labor = metrics.get('daily_labor_series', [])
    labor_pct = metrics.get('daily_labor_pct_series', [])
    orders = metrics.get('daily_orders_series', [])
    avg_check = metrics.get('daily_avg_check_series', [])
    channel_series = metrics.get('daily_channel_series', {})

    parts = []

    # 1. Revenue by Day — bar chart
    rev_chart = render_chartjs_bar(
        dates,
        [{"label": "Revenue", "data": revenue, "color": "#8cb82e"}],
        height=260, dollar=True,
    )
    parts.append(render_card("Revenue by Day", rev_chart))

    # 2. Revenue vs Labor Cost — dual axis (revenue bars left, labor bars right)
    rev_labor_chart = render_chartjs_line(
        dates,
        [
            {"label": "Revenue", "data": revenue, "color": "#8cb82e",
             "type": "bar", "order": 2},
            {"label": "Labor Cost", "data": labor, "color": "#e86040",
             "type": "bar", "yAxisID": "y2", "order": 1},
        ],
        height=280, dollar=True, y2=True,
    )
    parts.append(render_card("Revenue vs Labor Cost", rev_labor_chart,
                             subtitle="Left axis: Revenue | Right axis: Labor"))

    # 3. Labor % by Day — bar chart with 35% target line
    labor_pct_chart = render_chartjs_bar(
        dates,
        [{"label": "Labor %", "data": labor_pct, "color": "#e86040"}],
        height=240, pct=True,
        annotation_lines=[{"value": 35, "color": "#999", "label": "35% target"}],
    )
    parts.append(render_card("Labor % by Day", labor_pct_chart))

    # 4. Orders + Avg Check — bar chart for orders
    orders_chart = render_chartjs_bar(
        dates,
        [{"label": "Orders", "data": orders, "color": "#4a9cd8"}],
        height=240,
    )
    parts.append(render_card("Orders by Day", orders_chart))

    # 5. Avg Check by Day — bar chart (line was invisible)
    avg_check_chart = render_chartjs_bar(
        dates,
        [{"label": "Avg Check", "data": avg_check, "color": "#9b72c4"}],
        height=240, dollar=True,
    )
    parts.append(render_card("Average Check by Day", avg_check_chart))

    # 6. Revenue by Channel by Day — stacked bar (grouped channels)
    if channel_series:
        # Sort by total revenue descending
        ch_totals = {k: sum(v) for k, v in channel_series.items()}
        sorted_channels = sorted(ch_totals.keys(), key=lambda k: -ch_totals[k])
        ch_datasets = []
        for i, ch_name in enumerate(sorted_channels):
            ch_datasets.append({
                "label": ch_name,
                "data": channel_series[ch_name],
                "color": LIVITE_CHART_COLORS[i % len(LIVITE_CHART_COLORS)],
            })
        channel_chart = render_chartjs_bar(
            dates, ch_datasets,
            height=300, dollar=True, stacked=True, show_legend=True,
        )
        parts.append(render_card("Revenue by Channel by Day", channel_chart))

    # 7. Day-of-Week Analysis — avg revenue, orders, labor % by day of week
    dow_html = _render_dow_analysis(metrics)
    if dow_html:
        parts.append(dow_html)

    return (
        '<div style="margin-top:24px;">'
        '<h2 style="font-size:20px;font-weight:700;color:var(--text);margin-bottom:16px;">'
        'Daily Trends</h2>'
        + "\n".join(parts) +
        '</div>'
    )


def _render_dow_analysis(metrics: dict) -> str:
    """Render day-of-week average analysis from daily_summary."""
    summary = metrics.get('daily_summary', [])
    if not summary or len(summary) < 7:
        return ""

    # Aggregate by day of week
    dow_order = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    dow_data = {d: {'revenue': [], 'orders': [], 'labor_pct': [], 'avg_check': []}
                for d in dow_order}

    for day in summary:
        dow = day.get('day_of_week', '')[:3]
        if dow in dow_data:
            dow_data[dow]['revenue'].append(day.get('revenue', 0))
            dow_data[dow]['orders'].append(day.get('orders', 0))
            dow_data[dow]['labor_pct'].append(day.get('labor_pct', 0))
            dow_data[dow]['avg_check'].append(day.get('avg_check', 0))

    # Only show days that have data
    labels = []
    avg_rev = []
    avg_orders = []
    avg_labor = []
    avg_chk = []
    for d in dow_order:
        vals = dow_data[d]
        if vals['revenue']:
            labels.append(d)
            avg_rev.append(round(sum(vals['revenue']) / len(vals['revenue']), 2))
            avg_orders.append(round(sum(vals['orders']) / len(vals['orders'])))
            non_zero_labor = [v for v in vals['labor_pct'] if v > 0]
            avg_labor.append(round(sum(non_zero_labor) / len(non_zero_labor), 1) if non_zero_labor else 0)
            avg_chk.append(round(sum(vals['avg_check']) / len(vals['avg_check']), 2))

    if not labels:
        return ""

    parts = []

    # Revenue by day of week
    rev_dow = render_chartjs_bar(
        labels,
        [{"label": "Avg Revenue", "data": avg_rev, "color": "#8cb82e"}],
        height=220, dollar=True,
    )
    parts.append(rev_dow)

    # Orders by day of week
    orders_dow = render_chartjs_bar(
        labels,
        [{"label": "Avg Orders", "data": avg_orders, "color": "#4a9cd8"}],
        height=200,
    )
    parts.append(orders_dow)

    # Summary table
    parts.append('<table style="width:100%;font-size:11px;margin-top:10px;">')
    parts.append('<tr><th>Day</th><th class="r">Avg Revenue</th><th class="r">Avg Orders</th>'
                 '<th class="r">Avg Check</th><th class="r">Avg Labor %</th></tr>')
    best_rev = max(avg_rev) if avg_rev else 0
    for i, d in enumerate(labels):
        style = ' style="background:rgba(140,184,46,0.1);"' if avg_rev[i] == best_rev else ""
        lp_color = "#e86040" if avg_labor[i] > 35 else "#475417"
        parts.append(
            f'<tr{style}><td style="font-weight:600;">{d}</td>'
            f'<td class="r n">{fmt_currency(avg_rev[i])}</td>'
            f'<td class="r n">{fmt_num(avg_orders[i])}</td>'
            f'<td class="r n">{fmt_currency(avg_chk[i])}</td>'
            f'<td class="r n" style="color:{lp_color};">{fmt_pct(avg_labor[i])}</td></tr>'
        )
    parts.append('</table>')

    return render_card("Day-of-Week Analysis", "\n".join(parts),
                       subtitle="Averages by day of week across the selected range")


# ---------------------------------------------------------------------------
# Daily Breakdown Table (range dashboards only)
# ---------------------------------------------------------------------------

def render_daily_table(metrics: dict) -> str:
    """Render a day-by-day breakdown table for range dashboards."""
    summary = metrics.get('daily_summary', [])
    if not summary or len(summary) < 2:
        return ""

    # Find best/worst for highlighting
    revenues = [d.get('revenue', 0) for d in summary]
    best_rev = max(revenues) if revenues else 0
    worst_rev = min(revenues) if revenues else 0

    headers = ["Date", "Day", "Revenue", "Orders", "Avg Check", "Labor $", "Labor %"]
    rows = []
    for d in summary:
        rev = d.get('revenue', 0)
        lab = d.get('labor')
        lab_pct = d.get('labor_pct')

        # Highlight best/worst
        if rev == best_rev and best_rev != worst_rev:
            style = "background:rgba(140,184,46,0.1);"
        elif rev == worst_rev and best_rev != worst_rev:
            style = "background:rgba(232,96,64,0.08);"
        else:
            style = ""

        # Format date label
        ds = d.get('date_str', '')
        if ds and len(ds) == 8:
            date_label = f"{int(ds[4:6])}/{int(ds[6:8])}"
        else:
            date_label = ds

        dow = d.get('day_of_week', '')[:3]

        # Labor formatting — show dash for missing data
        lab_str = fmt_currency(lab) if lab is not None else '<span style="color:var(--muted);">&mdash;</span>'
        if lab_pct is not None:
            lp_color = "#e86040" if lab_pct > 35 else "#475417"
            lp_str = fmt_pct(lab_pct)
        else:
            lp_color = "var(--muted)"
            lp_str = "&mdash;"

        rows.append(
            f'<tr style="{style}">'
            f'<td style="font-family:\'JetBrains Mono\',monospace;font-size:12px;">{date_label}</td>'
            f'<td>{dow}</td>'
            f'<td class="r n">{fmt_currency(rev)}</td>'
            f'<td class="r n">{fmt_num(d.get("orders", 0))}</td>'
            f'<td class="r n">{fmt_currency(d.get("avg_check", 0))}</td>'
            f'<td class="r n">{lab_str}</td>'
            f'<td class="r n" style="color:{lp_color};font-weight:600;">{lp_str}</td>'
            f'</tr>'
        )

    # Totals row
    total_rev = sum(d.get('revenue', 0) for d in summary)
    total_orders = sum(d.get('orders', 0) for d in summary)
    total_labor = sum(d.get('labor', 0) or 0 for d in summary)
    avg_check_total = total_rev / total_orders if total_orders else 0
    labor_pct_total = (total_labor / total_rev * 100) if total_rev else 0
    lp_total_color = "#e86040" if labor_pct_total > 35 else "#475417"

    total_row = (
        f'<tr style="font-weight:700;border-top:2px solid var(--border);">'
        f'<td colspan="2">Total</td>'
        f'<td class="r n">{fmt_currency(total_rev)}</td>'
        f'<td class="r n">{fmt_num(total_orders)}</td>'
        f'<td class="r n">{fmt_currency(avg_check_total)}</td>'
        f'<td class="r n">{fmt_currency(total_labor)}</td>'
        f'<td class="r n" style="color:{lp_total_color};font-weight:700;">{fmt_pct(labor_pct_total)}</td>'
        f'</tr>'
    )

    # Daily avg row
    n = len(summary)
    avg_rev = total_rev / n if n else 0
    avg_orders = total_orders / n if n else 0
    avg_labor = total_labor / n if n else 0

    avg_row = (
        f'<tr style="color:var(--muted);font-style:italic;border-top:1px solid var(--border);">'
        f'<td colspan="2">Daily Avg</td>'
        f'<td class="r n">{fmt_currency(avg_rev)}</td>'
        f'<td class="r n">{fmt_num(avg_orders)}</td>'
        f'<td class="r n">{fmt_currency(avg_check_total)}</td>'
        f'<td class="r n">{fmt_currency(avg_labor)}</td>'
        f'<td class="r n" style="color:{lp_total_color};">{fmt_pct(labor_pct_total)}</td>'
        f'</tr>'
    )

    thead = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    table_html = (
        f'<table>\n{thead}\n'
        + "\n".join(rows)
        + f"\n{total_row}\n{avg_row}\n</table>"
    )

    return render_card("Daily Breakdown", table_html, subtitle="One row per day in the selected range")


