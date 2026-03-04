"""Anomaly detection and analyst insight generation."""

import logging
import pandas as pd
from .utils import _safe_div

logger = logging.getLogger(__name__)

# Day-of-week revenue thresholds (multiplier vs flat threshold).
# Mon=0 ... Sun=6. Adjusts WoW drop sensitivity based on expected day traffic.
# Lower-traffic days (Mon/Tue) get a more lenient threshold for revenue drops.
_DOW_LABELS = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
_DOW_WOW_THRESHOLD = {0: -25, 1: -25, 2: -20, 3: -20, 4: -15, 5: -15, 6: -15}  # % drop to flag
_DOW_MOM_THRESHOLD = {0: -30, 1: -30, 2: -25, 3: -25, 4: -20, 5: -20, 6: -20}  # % drop to flag


def detect_anomalies(metrics, comparisons=None, date_str: str = ""):
    """Detect operational anomalies and return alerts.

    Args:
        metrics: Master metrics dict from compute_all_metrics.
        comparisons: Optional dict with 'prev_week' and/or 'prev_month' metrics
                     for WoW/MoM comparison.
        date_str: Date string (YYYYMMDD or YYYY-MM-DD) for day-of-week context.
                  Used to apply day-specific thresholds so a slow Tuesday
                  isn't flagged the same way as a slow Saturday.

    Returns list of dicts: {type, severity ('red'|'amber'), message, value, threshold}.
    """
    alerts = []

    # Determine day of week for context-aware thresholds
    dow = None
    if date_str:
        try:
            from datetime import datetime
            ds = date_str.replace("-", "")
            dt = datetime.strptime(ds, "%Y%m%d")
            dow = dt.weekday()  # 0=Mon, 6=Sun
        except Exception as e:
            logger.debug("Date parse failed for insights: %s", e)

    def _add(alert_type, severity, message, value, threshold):
        alerts.append({
            "type": alert_type,
            "severity": severity,
            "message": message,
            "value": value,
            "threshold": threshold,
        })

    try:
        # ── Labor % ──
        labor = metrics.get("labor")
        if labor:
            labor_pct = labor.get("labor_pct", 0)
            if labor_pct > 30:
                _add("labor_pct", "red",
                     f"Labor cost is {labor_pct}% of revenue (target: <30%)",
                     labor_pct, 30)

            # OT > 15% of total labor
            ot_pay = labor.get("ot_pay_total", 0)
            total_labor = labor.get("total_labor", 0)
            if total_labor > 0:
                ot_pct = round(_safe_div(ot_pay, total_labor) * 100, 1)
                if ot_pct > 15:
                    _add("overtime", "red",
                         f"Overtime is {ot_pct}% of total labor cost (target: <15%)",
                         ot_pct, 15)

            # Unknown employees
            unknown = labor.get("unknown_employees", [])
            if unknown:
                _add("unknown_employees", "amber",
                     f"{len(unknown)} employees not in master wage table: {', '.join(unknown[:5])}",
                     len(unknown), 0)

            # Auto clockouts
            auto_co = labor.get("auto_clockouts", [])
            if auto_co:
                _add("auto_clockouts", "amber",
                     f"{len(auto_co)} auto clock-out(s): {', '.join(auto_co[:5])}",
                     len(auto_co), 0)

            # Long shifts > 10h
            shift_dist = labor.get("shift_distribution", {})
            long_shifts = shift_dist.get("over_10h", 0)
            if long_shifts > 0:
                _add("long_shifts", "amber",
                     f"{long_shifts} shift(s) over 10 hours",
                     long_shifts, 0)

        # ── Revenue / Orders ──
        revenue = metrics.get("revenue")
        if revenue:
            total_discounts = revenue.get("total_discounts", 0)
            toast_total = revenue.get("toast_total", 0)
            if toast_total > 0:
                disc_rate = round(_safe_div(total_discounts, toast_total) * 100, 1)
                if disc_rate > 20:
                    _add("discount_rate", "red",
                         f"Discount rate is {disc_rate}% of revenue (target: <20%)",
                         disc_rate, 20)

        # ── Order intelligence ──
        orders = metrics.get("orders")
        if orders:
            # Uber discount rate
            uber_impact = orders.get("uber_bogo_impact", {})
            uber_disc = uber_impact.get("discount_total", 0)
            if revenue and revenue.get("toast_total", 0) > 0:
                uber_channels = revenue.get("channels", {})
                uber_rev = sum(
                    v.get("revenue", 0) for k, v in uber_channels.items()
                    if "Uber" in k
                )
                if uber_rev > 0:
                    uber_disc_pct = round(_safe_div(uber_disc, uber_rev) * 100, 1)
                    if uber_disc_pct > 25:
                        _add("uber_discount", "red",
                             f"Uber discount rate is {uber_disc_pct}% (target: <25%)",
                             uber_disc_pct, 25)

            # Single-item rate
            single_rate = orders.get("single_item_rate", 0)
            if single_rate > 50:
                _add("single_item_rate", "amber",
                     f"Single-item order rate is {single_rate}% (target: <50%)",
                     single_rate, 50)

        # ── Kitchen ──
        kitchen = metrics.get("kitchen")
        if kitchen:
            stations = kitchen.get("stations", {})
            for station_name, stats in stations.items():
                p90 = stats.get("p90", 0)
                if p90 > 15:
                    _add("kitchen_p90", "red",
                         f"Kitchen P90 at {station_name} is {p90} min (target: <15 min)",
                         p90, 15)

        # ── WoW / MoM comparison (day-of-week adjusted thresholds) ──
        if comparisons and revenue:
            current_rev = revenue.get("toast_total", 0)
            dow_label = f" ({_DOW_LABELS[dow]})" if dow is not None else ""

            # WoW: use tighter threshold on busy days, looser on slow days
            wow_threshold = _DOW_WOW_THRESHOLD.get(dow, -15) if dow is not None else -15
            prev_week_rev = comparisons.get("prev_week", {}).get("toast_total")
            if prev_week_rev and prev_week_rev > 0:
                wow_change = round((current_rev - prev_week_rev) / prev_week_rev * 100, 1)
                if wow_change < wow_threshold:
                    severity = "red" if wow_change < wow_threshold * 1.5 else "amber"
                    _add("revenue_wow_drop", severity,
                         f"Revenue dropped {abs(wow_change)}% WoW{dow_label} (threshold: {abs(wow_threshold)}% for this day)",
                         wow_change, wow_threshold)

            # MoM: same day-of-week adjusted logic
            mom_threshold = _DOW_MOM_THRESHOLD.get(dow, -20) if dow is not None else -20
            prev_month_rev = comparisons.get("prev_month", {}).get("toast_total")
            if prev_month_rev and prev_month_rev > 0:
                mom_change = round((current_rev - prev_month_rev) / prev_month_rev * 100, 1)
                if mom_change < mom_threshold:
                    _add("revenue_mom_drop", "red",
                         f"Revenue dropped {abs(mom_change)}% MoM{dow_label} (threshold: {abs(mom_threshold)}% for this day)",
                         mom_change, mom_threshold)

    except Exception as e:
        alerts.append({
            "type": "anomaly_detection_error",
            "severity": "amber",
            "message": f"Error during anomaly detection: {str(e)}",
            "value": None,
            "threshold": None,
        })

    return alerts


# ═══════════════════════════════════════════════════════════════
#  12b. ANALYST INSIGHTS
# ═══════════════════════════════════════════════════════════════

def compute_analyst_insights(metrics: dict, slot_4wra: dict = None) -> list:
    """Generate narrative-style analyst insights from the day's metrics.

    Analyzes peak times, catering events, channel shifts, rush patterns,
    dead zones, labor efficiency, item standouts, and kitchen speed against
    4-week rolling averages to produce actionable commentary.

    Args:
        metrics: Master metrics dict (same shape as compute_all_metrics output).
                 Keys include revenue, orders, baskets, modifiers, kitchen,
                 labor, payments, customers — each None or a sub-dict.
        slot_4wra: Optional dict of 4-week rolling averages keyed by "H:QQ"
                   strings (e.g. "10:00", "10:15").  Each value is a dict
                   {"avg_revenue": float, "avg_orders": float, "weeks_found": int}.

    Returns:
        List of insight dicts sorted by severity (red > amber > blue > green).
        Each dict: {category, headline, detail, severity}.
    """
    insights = []
    severity_order = {"red": 0, "amber": 1, "blue": 2, "green": 3}

    if slot_4wra is None:
        slot_4wra = {}

    # ── Convenience accessors ─────────────────────────────────
    revenue = metrics.get("revenue") or {}
    orders_intel = metrics.get("orders") or {}
    labor = metrics.get("labor") or {}
    kitchen = metrics.get("kitchen") or {}

    quarter_hourly = revenue.get("quarter_hourly", [])
    toast_total = revenue.get("toast_total", 0)
    total_orders = revenue.get("total_orders", 0)

    def _fmt_time(label):
        """Convert 'H:QQ' label like '13:30' to '1:30PM'."""
        try:
            h, m = label.split(":")
            h = int(h)
            m = int(m)
            suffix = "AM" if h < 12 else "PM"
            display_h = h if h <= 12 else h - 12
            if display_h == 0:
                display_h = 12
            return f"{display_h}:{m:02d}{suffix}"
        except Exception:
            return label

    def _pct_vs_4wra(slot_label, slot_revenue):
        """Return percentage difference vs 4WRA for a slot, or None."""
        avg = slot_4wra.get(slot_label, {}).get("avg_revenue")
        if avg is None or avg <= 0:
            return None
        return round((slot_revenue - avg) / avg * 100, 1)

    # ──────────────────────────────────────────────────────────
    #  1. PEAK TIME ANALYSIS
    # ──────────────────────────────────────────────────────────
    try:
        if quarter_hourly:
            peak_slot = max(quarter_hourly, key=lambda s: s.get("revenue", 0))
            peak_rev = peak_slot.get("revenue", 0)
            peak_label = peak_slot.get("label", "")
            peak_orders = peak_slot.get("orders", 0)

            if peak_rev > 0:
                pct_diff = _pct_vs_4wra(peak_label, peak_rev)
                if pct_diff is not None and pct_diff > 50:
                    insights.append({
                        "category": "Peak Time",
                        "headline": f"Spike at {_fmt_time(peak_label)}: {pct_diff:+.0f}% vs 4-week avg",
                        "detail": (
                            f"The {_fmt_time(peak_label)} slot generated ${peak_rev:,.2f} "
                            f"from {peak_orders} order(s), which is {pct_diff:.0f}% above the "
                            f"4-week rolling average. This may indicate a catering order or "
                            f"promotional surge."
                        ),
                        "severity": "blue",
                    })
                elif pct_diff is not None and pct_diff < -30:
                    insights.append({
                        "category": "Peak Time",
                        "headline": f"Peak slot {_fmt_time(peak_label)} underperformed vs 4-week avg",
                        "detail": (
                            f"Even the busiest 15-min window ({_fmt_time(peak_label)}, "
                            f"${peak_rev:,.2f}) came in {abs(pct_diff):.0f}% below its "
                            f"4-week rolling average — a notably slow peak."
                        ),
                        "severity": "amber",
                    })
                # Skip informational-only peak reports (no 4WRA) to reduce noise
    except Exception as e:
        logger.warning("Insight: Peak Time analysis failed: %s", e)

    # ──────────────────────────────────────────────────────────
    #  2. CATERING DETECTION
    # ──────────────────────────────────────────────────────────
    try:
        # Only flag truly large orders ($300+) as insights to avoid noise
        for slot in quarter_hourly:
            large_orders = slot.get("large_orders", [])
            if not large_orders:
                continue
            slot_label = slot.get("label", "")
            slot_rev = slot.get("revenue", 0)
            for lo in large_orders:
                amt = lo.get("amount", 0)
                if amt < 300:
                    continue  # Skip smaller orders — not notable enough for insights
                channel = lo.get("channel", "Unknown")
                pct_diff = _pct_vs_4wra(slot_label, slot_rev)
                pct_str = ""
                if pct_diff is not None and pct_diff > 0:
                    pct_str = f", boosting this slot {pct_diff:.0f}% above its 4-week average"
                insights.append({
                    "category": "Catering Detection",
                    "headline": f"${amt:,.2f} {channel} order at {_fmt_time(slot_label)}",
                    "detail": (
                        f"A ${amt:,.2f} {channel} order at {_fmt_time(slot_label)} "
                        f"was flagged as a large order (>=$300){pct_str}."
                    ),
                    "severity": "blue",
                })
    except Exception as e:
        logger.warning("Insight: Catering Detection analysis failed: %s", e)

    # ──────────────────────────────────────────────────────────
    #  3. CHANNEL SHIFT
    # ──────────────────────────────────────────────────────────
    try:
        w3o = revenue.get("walkin_3p_online", {})
        if w3o and toast_total > 0:
            walkin_pct = w3o.get("Walk-In", {}).get("pct", 0)
            tp_pct = w3o.get("3P", {}).get("pct", 0)
            online_pct = w3o.get("Online", {}).get("pct", 0)
            tp_rev = w3o.get("3P", {}).get("revenue", 0)
            walkin_rev = w3o.get("Walk-In", {}).get("revenue", 0)

            if tp_pct > 50:
                insights.append({
                    "category": "Channel Shift",
                    "headline": f"Third-party dominated today at {tp_pct:.0f}% of revenue",
                    "detail": (
                        f"3P channels drove ${tp_rev:,.2f} ({tp_pct:.1f}% of revenue). "
                        f"Walk-In was ${walkin_rev:,.2f} ({walkin_pct:.1f}%), "
                        f"Online was {online_pct:.1f}%. Heavy 3P reliance increases "
                        f"commission exposure."
                    ),
                    "severity": "amber",
                })
            elif walkin_pct < 20 and toast_total > 0:
                insights.append({
                    "category": "Channel Shift",
                    "headline": f"Walk-in traffic unusually low at {walkin_pct:.0f}%",
                    "detail": (
                        f"Walk-in revenue was only ${walkin_rev:,.2f} ({walkin_pct:.1f}% "
                        f"of total). 3P was {tp_pct:.1f}%, Online was {online_pct:.1f}%. "
                        f"This may signal reduced foot traffic or weather impact."
                    ),
                    "severity": "amber",
                })
            # Skip healthy-mix informational insights to reduce noise
    except Exception as e:
        logger.warning("Insight: Channel Shift analysis failed: %s", e)

    # ──────────────────────────────────────────────────────────
    #  4. RUSH PATTERNS
    # ──────────────────────────────────────────────────────────
    try:
        if quarter_hourly:
            # Find longest consecutive run of active slots (revenue > 0)
            active_runs = []
            current_run = []
            for slot in quarter_hourly:
                if slot.get("revenue", 0) > 0:
                    current_run.append(slot)
                else:
                    if current_run:
                        active_runs.append(current_run)
                    current_run = []
            if current_run:
                active_runs.append(current_run)

            # Skip routine rush pattern insights — only flag if unusually short/late
            if active_runs:
                longest = max(active_runs, key=len)
                run_duration_min = len(longest) * 15
                # Only flag if active window is unusually short (< 3 hours)
                if run_duration_min < 180:
                    run_start = longest[0].get("label", "")
                    run_end_slot = longest[-1]
                    run_end_h = run_end_slot.get("hour", 0)
                    run_end_q = run_end_slot.get("quarter", 0) + 15
                    if run_end_q >= 60:
                        run_end_h += 1
                        run_end_q = 0
                    run_end_label = f"{run_end_h}:{run_end_q:02d}"
                    run_revenue = sum(s.get("revenue", 0) for s in longest)
                    insights.append({
                        "category": "Rush Patterns",
                        "headline": f"Short active window: {_fmt_time(run_start)}\u2013{_fmt_time(run_end_label)} ({run_duration_min} min)",
                        "detail": (
                            f"Continuous activity was only {run_duration_min} min "
                            f"(${run_revenue:,.2f}). This is shorter than usual."
                        ),
                        "severity": "amber",
                    })
    except Exception as e:
        logger.warning("Insight: Rush Patterns analysis failed: %s", e)

    # ──────────────────────────────────────────────────────────
    #  5. DEAD ZONES
    # ──────────────────────────────────────────────────────────
    try:
        if slot_4wra and quarter_hourly:
            dead_slots = []
            for slot in quarter_hourly:
                label = slot.get("label", "")
                slot_rev = slot.get("revenue", 0)
                avg_data = slot_4wra.get(label, {})
                avg_rev = avg_data.get("avg_revenue", 0)
                if slot_rev == 0 and avg_rev > 0:
                    dead_slots.append({
                        "label": label,
                        "avg_revenue": avg_rev,
                        "avg_orders": avg_data.get("avg_orders", 0),
                    })
            if dead_slots:
                total_missed = sum(s["avg_revenue"] for s in dead_slots)
                # Report up to the top 5 by missed revenue
                dead_slots.sort(key=lambda s: s["avg_revenue"], reverse=True)
                top_dead = dead_slots[:5]
                slot_list = ", ".join(
                    f"{_fmt_time(s['label'])} (avg ${s['avg_revenue']:,.2f})"
                    for s in top_dead
                )
                extra = f" and {len(dead_slots) - 5} more" if len(dead_slots) > 5 else ""
                severity = "red" if total_missed > 100 else "amber"
                insights.append({
                    "category": "Dead Zones",
                    "headline": f"{len(dead_slots)} slot(s) with $0 revenue vs ${total_missed:,.2f} expected",
                    "detail": (
                        f"These time slots had revenue in the 4-week rolling average but "
                        f"recorded $0 today: {slot_list}{extra}. "
                        f"Total missed revenue window: ${total_missed:,.2f}."
                    ),
                    "severity": severity,
                })
    except Exception as e:
        logger.warning("Insight: Dead Zones analysis failed: %s", e)

    # ──────────────────────────────────────────────────────────
    #  6. LABOR EFFICIENCY
    # ──────────────────────────────────────────────────────────
    try:
        daypart_eff = labor.get("daypart_efficiency", [])
        if daypart_eff:
            # Filter to dayparts with actual revenue and labor cost
            valid_dp = [
                dp for dp in daypart_eff
                if dp.get("revenue", 0) > 0 and dp.get("labor_cost", 0) > 0
            ]
            if valid_dp:
                best_dp = max(valid_dp, key=lambda d: d.get("rev_per_labor_dollar", 0))
                worst_dp = min(valid_dp, key=lambda d: d.get("rev_per_labor_dollar", 0))

                best_ratio = best_dp.get("rev_per_labor_dollar", 0)
                worst_ratio = worst_dp.get("rev_per_labor_dollar", 0)

                # Skip best-ROI green insight — only flag weak dayparts
                if worst_dp["daypart"] != best_dp["daypart"]:
                    severity = "red" if worst_ratio < 2.0 else "amber" if worst_ratio < 3.0 else "green"
                    insights.append({
                        "category": "Labor Efficiency",
                        "headline": f"Weakest labor ROI: {worst_dp['daypart']} (${worst_ratio:.2f} rev per labor $)",
                        "detail": (
                            f"{worst_dp['daypart']} ({worst_dp.get('hours', '')}) generated "
                            f"${worst_dp.get('revenue', 0):,.2f} on ${worst_dp.get('labor_cost', 0):,.2f} "
                            f"labor \u2014 only ${worst_ratio:.2f} revenue per labor dollar."
                        ),
                        "severity": severity,
                    })
    except Exception as e:
        logger.warning("Insight: Labor Efficiency analysis failed: %s", e)

    # ──────────────────────────────────────────────────────────
    #  7. ITEM STANDOUTS
    # ──────────────────────────────────────────────────────────
    try:
        top_items = orders_intel.get("top_items_by_revenue", [])
        if top_items and toast_total > 0:
            top_item = top_items[0]
            item_name = top_item.get("item", "Unknown")
            item_rev = top_item.get("revenue", 0)
            item_qty = top_item.get("qty", 0)
            item_pct = round(_safe_div(item_rev, toast_total) * 100, 1)

            if item_pct > 15:
                insights.append({
                    "category": "Item Standouts",
                    "headline": f"{item_name} drove {item_pct}% of revenue (${item_rev:,.2f})",
                    "detail": (
                        f"{item_name} was today's #1 item by revenue with ${item_rev:,.2f} "
                        f"({item_qty} sold), representing {item_pct}% of total revenue. "
                        f"High single-item concentration may indicate a menu dependency."
                    ),
                    "severity": "amber",
                })
            # Skip informational #1 item report — only flag concentration risk
    except Exception as e:
        logger.warning("Insight: Item Standouts analysis failed: %s", e)

    # ──────────────────────────────────────────────────────────
    #  8. KITCHEN SPEED
    # ──────────────────────────────────────────────────────────
    try:
        stations = kitchen.get("stations", {})
        if stations:
            # Find best and worst by median fulfillment time
            station_list = [
                (name, stats) for name, stats in stations.items()
                if stats.get("total_tickets", 0) > 0
            ]
            if station_list:
                best_station = min(station_list, key=lambda s: s[1].get("median", 999))
                worst_station = max(station_list, key=lambda s: s[1].get("median", 0))

                best_name, best_stats = best_station
                best_median = best_stats.get("median", 0)
                best_p90 = best_stats.get("p90", 0)
                best_tickets = best_stats.get("total_tickets", 0)

                # Skip fastest-station green insight — only flag slow stations
                if worst_station[0] != best_station[0]:
                    worst_name, worst_stats = worst_station
                    worst_median = worst_stats.get("median", 0)
                    worst_p90 = worst_stats.get("p90", 0)
                    worst_tickets = worst_stats.get("total_tickets", 0)
                    severity = "red" if worst_p90 > 15 else "amber" if worst_p90 > 10 else "green"
                    insights.append({
                        "category": "Kitchen Speed",
                        "headline": f"Slowest station: {worst_name} (median {worst_median:.1f} min)",
                        "detail": (
                            f"{worst_name} handled {worst_tickets} ticket(s) with a median "
                            f"fulfillment of {worst_median:.1f} min (P90: {worst_p90:.1f} min)."
                        ),
                        "severity": severity,
                    })
    except Exception as e:
        logger.warning("Insight: Kitchen Speed analysis failed: %s", e)

    # ── Sort by severity: red -> amber -> blue -> green ───────
    insights.sort(key=lambda i: severity_order.get(i.get("severity", "green"), 99))
    return insights


# ═══════════════════════════════════════════════════════════════
#  13. ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════
