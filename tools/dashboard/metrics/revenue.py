"""Revenue and channel metrics computation."""

import logging
import pandas as pd

logger = logging.getLogger(__name__)
from .utils import (
    _filter_voided_orders, _safe_numeric, _safe_div,
    _get_channel, _get_channel_group, CHANNEL_MAP,
    parse_toast_datetime, parse_duration
)

def compute_revenue_metrics(order_details):
    """Compute comprehensive revenue metrics from OrderDetails.

    Returns dict with toast_total, gross_total, channel breakdowns,
    hourly curves, server performance, and more.
    """
    try:
        od = _filter_voided_orders(order_details.copy())
        for col in ['Amount', 'Tax', 'Tip', 'Gratuity', 'Discount Amount']:
            od[col] = _safe_numeric(od, col)
        od['# of Guests'] = _safe_numeric(od, '# of Guests')

        toast_total = round(od['Amount'].sum(), 2)
        gross_total = round((od['Amount'] + od['Tax'] + od['Tip'] + od['Gratuity']).sum(), 2)
        total_orders = len(od)
        avg_check = round(_safe_div(toast_total, total_orders), 2)
        total_guests = int(od['# of Guests'].sum())
        rev_per_guest = round(_safe_div(toast_total, total_guests), 2) if total_guests > 0 else 0
        total_tax = round(od['Tax'].sum(), 2)
        total_tips = round(od['Tip'].sum(), 2)
        total_gratuity = round(od['Gratuity'].sum(), 2)
        total_discounts = round(od['Discount Amount'].sum(), 2)

        # ── Channel breakdown ──
        od['_channel'] = od['Dining Options'].apply(_get_channel)
        channels = {}
        if total_orders > 0:
            for ch, grp in od.groupby('_channel'):
                ch_revenue = round(grp['Amount'].sum(), 2)
                ch_orders = len(grp)
                channels[ch] = {
                    "revenue": ch_revenue,
                    "orders": ch_orders,
                    "avg_check": round(_safe_div(ch_revenue, ch_orders), 2),
                    "pct_share": round(_safe_div(ch_revenue, toast_total) * 100, 1),
                }

        # ── Walk-in / 3P / Online ──
        od['_group'] = od['Dining Options'].apply(_get_channel_group)
        walkin_3p_online = {}
        for grp_name in ["Walk-In", "3P", "Online"]:
            subset = od[od['_group'] == grp_name]
            grp_revenue = round(subset['Amount'].sum(), 2)
            grp_orders = len(subset)
            walkin_3p_online[grp_name] = {
                "revenue": grp_revenue,
                "orders": grp_orders,
                "pct": round(_safe_div(grp_revenue, toast_total) * 100, 1),
            }

        # ── Service split ──
        service_split = {}
        if 'Service' in od.columns:
            for svc, grp in od.groupby('Service'):
                if pd.isna(svc):
                    svc = "Unknown"
                service_split[str(svc)] = {
                    "revenue": round(grp['Amount'].sum(), 2),
                    "orders": len(grp),
                }

        # ── Source split ──
        source_split = {}
        if 'Order Source' in od.columns:
            for src, grp in od.groupby('Order Source'):
                if pd.isna(src):
                    src = "Unknown"
                source_split[str(src)] = {
                    "revenue": round(grp['Amount'].sum(), 2),
                    "orders": len(grp),
                }

        # ── Hourly curve (7am-10pm) ──
        od['_opened_dt'] = od['Opened'].apply(parse_toast_datetime)
        od['_hour'] = od['_opened_dt'].apply(lambda x: x.hour if x else None)
        hourly = []
        peak_hour = {"hour": None, "revenue": 0, "orders": 0}
        for h in range(7, 23):
            hour_df = od[od['_hour'] == h]
            h_rev = round(hour_df['Amount'].sum(), 2)
            h_orders = len(hour_df)
            h_avg = round(_safe_div(h_rev, h_orders), 2)
            hourly.append({
                "hour": h,
                "revenue": h_rev,
                "orders": h_orders,
                "avg_check": h_avg,
            })
            if h_rev > peak_hour["revenue"]:
                peak_hour = {"hour": h, "revenue": h_rev, "orders": h_orders}

        # ── 15-minute interval curve ──
        od['_minute'] = od['_opened_dt'].apply(lambda x: x.minute if x else None)
        quarter_hourly = []
        peak_quarter = {"label": None, "revenue": 0, "orders": 0}
        # Threshold for flagging a single order as "large" (likely catering)
        large_order_threshold = 250
        for h in range(7, 23):
            for q in [0, 15, 30, 45]:
                mask = (od['_hour'] == h) & (od['_minute'].notna())
                mask = mask & (od['_minute'] >= q) & (od['_minute'] < q + 15)
                slot_df = od[mask]
                q_rev = round(slot_df['Amount'].sum(), 2)
                q_orders = len(slot_df)
                label = f"{h}:{q:02d}"
                # Detect large orders in this slot
                large_orders = []
                if len(slot_df) > 0:
                    for _, row in slot_df.iterrows():
                        amt = row['Amount']
                        if amt >= large_order_threshold:
                            large_orders.append({
                                "amount": round(amt, 2),
                                "channel": str(row.get('_channel', '')),
                                "order_id": str(row.get('Order #', '')),
                            })
                entry = {
                    "hour": h, "quarter": q,
                    "label": label,
                    "revenue": q_rev,
                    "orders": q_orders,
                }
                if large_orders:
                    large_orders.sort(key=lambda x: x['amount'], reverse=True)
                    entry["large_orders"] = large_orders
                quarter_hourly.append(entry)
                if q_rev > peak_quarter["revenue"]:
                    peak_quarter = {"label": label, "revenue": q_rev, "orders": q_orders}

        # ── Hourly by channel ──
        hourly_by_channel = []
        for h in range(7, 23):
            hour_df = od[od['_hour'] == h]
            ch_data = {}
            for ch, grp in hour_df.groupby('_channel'):
                ch_data[ch] = {
                    "revenue": round(grp['Amount'].sum(), 2),
                    "orders": len(grp),
                }
            # Also add channel group breakdown
            grp_data = {}
            for gn in ["Walk-In", "3P", "Online"]:
                g_subset = hour_df[hour_df['_group'] == gn]
                grp_data[gn] = {
                    "revenue": round(g_subset['Amount'].sum(), 2),
                    "orders": len(g_subset),
                }
            hourly_by_channel.append({
                "hour": h,
                "channels": ch_data,
                "groups": grp_data,
            })

        # ── Duration by channel ──
        od['_duration_min'] = od['Duration (Opened to Paid)'].apply(parse_duration)
        duration_by_channel = {}
        valid_dur = od[od['_duration_min'].notna()]
        if len(valid_dur) > 0:
            for ch, grp in valid_dur.groupby('_channel'):
                duration_by_channel[ch] = round(grp['_duration_min'].mean(), 2)

        # ── Server performance ──
        server_performance = []
        if 'Server' in od.columns:
            for svr, grp in od.groupby('Server'):
                if pd.isna(svr) or str(svr).strip() == '':
                    svr = "Unknown"
                svr_rev = round(grp['Amount'].sum(), 2)
                svr_orders = len(grp)
                server_performance.append({
                    "server": str(svr).strip(),
                    "revenue": svr_rev,
                    "orders": svr_orders,
                    "avg_check": round(_safe_div(svr_rev, svr_orders), 2),
                })
            server_performance.sort(key=lambda x: x["revenue"], reverse=True)

        # ── Revenue center ──
        revenue_center = {}
        if 'Revenue Center' in od.columns:
            for rc, grp in od.groupby('Revenue Center'):
                if pd.isna(rc):
                    rc = "Unknown"
                revenue_center[str(rc)] = {
                    "revenue": round(grp['Amount'].sum(), 2),
                    "orders": len(grp),
                }

        return {
            "toast_total": toast_total,
            "gross_total": gross_total,
            "total_orders": total_orders,
            "avg_check": avg_check,
            "total_guests": total_guests,
            "rev_per_guest": rev_per_guest,
            "channels": channels,
            "walkin_3p_online": walkin_3p_online,
            "service_split": service_split,
            "source_split": source_split,
            "hourly": hourly,
            "peak_hour": peak_hour,
            "quarter_hourly": quarter_hourly,
            "peak_quarter": peak_quarter,
            "hourly_by_channel": hourly_by_channel,
            "duration_by_channel": duration_by_channel,
            "server_performance": server_performance,
            "revenue_center": revenue_center,
            "total_tax": total_tax,
            "total_tips": total_tips,
            "total_gratuity": total_gratuity,
            "total_discounts": total_discounts,
        }
    except Exception as e:
        logger.warning("compute_revenue_metrics failed: %s", e)
        return {
            "toast_total": 0, "gross_total": 0, "total_orders": 0,
            "avg_check": 0, "total_guests": 0, "rev_per_guest": 0,
            "channels": {}, "walkin_3p_online": {},
            "service_split": {}, "source_split": {},
            "hourly": [], "peak_hour": {},
            "quarter_hourly": [], "peak_quarter": {},
            "hourly_by_channel": [],
            "duration_by_channel": {}, "server_performance": [],
            "revenue_center": {},
            "total_tax": 0, "total_tips": 0, "total_gratuity": 0,
            "total_discounts": 0,
            "_error": str(e),
        }


# ═══════════════════════════════════════════════════════════════
#  5. ORDER INTELLIGENCE
# ═══════════════════════════════════════════════════════════════
