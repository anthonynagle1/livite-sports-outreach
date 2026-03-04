"""Kitchen speed and fulfillment time metrics."""

import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

import pandas as pd
import numpy as np
from statistics import median
from .utils import (
    parse_fulfillment_time, parse_toast_datetime,
    _filter_voided_orders, _safe_div, _get_channel_group
)

def compute_kitchen_metrics(kitchen_timings, order_details):
    """Compute kitchen performance metrics from KitchenTimings.

    Returns dict with station stats, time distributions, hourly speed,
    walk-in vs online comparison, peak concurrent tickets, and
    fulfilled-by breakdown.
    """
    try:
        kt = kitchen_timings.copy()
        kt['_fulfillment_min'] = kt['Fulfillment Time'].apply(parse_fulfillment_time)

        # Filter out rows with no fulfillment time + outliers
        kt_all = kt[kt['_fulfillment_min'].notna()]
        outlier_low = (kt_all['_fulfillment_min'] < 0.5)  # < 30 sec = instant close-outs
        outlier_high = (kt_all['_fulfillment_min'] > 60)   # > 60 min = forgotten tickets
        outlier_count = int(outlier_low.sum() + outlier_high.sum())
        kt_valid = kt_all[~outlier_low & ~outlier_high].copy()

        # Parse timestamps for concurrent ticket analysis
        kt['_fired_dt'] = kt['Fired Date'].apply(parse_toast_datetime)
        kt['_fulfilled_dt'] = kt['Fulfilled Date'].apply(parse_toast_datetime)

        # ── Station-level stats ──
        stations = {}
        if 'Station' in kt_valid.columns and len(kt_valid) > 0:
            for station, grp in kt_valid.groupby('Station'):
                if pd.isna(station):
                    station = "Unknown"
                times = grp['_fulfillment_min'].values
                station_name = str(station)
                total_tickets = len(times)
                stations[station_name] = {
                    "median": round(float(np.median(times)), 2),
                    "mean": round(float(np.mean(times)), 2),
                    "p75": round(float(np.percentile(times, 75)), 2),
                    "p90": round(float(np.percentile(times, 90)), 2),
                    "p95": round(float(np.percentile(times, 95)), 2),
                    "total_tickets": total_tickets,
                    "under_5min_pct": round(
                        _safe_div(int((times < 5).sum()), total_tickets) * 100, 1
                    ),
                    "under_10min_pct": round(
                        _safe_div(int((times < 10).sum()), total_tickets) * 100, 1
                    ),
                    "over_15min_count": int((times > 15).sum()),
                }

        # ── Time distribution buckets ──
        buckets = [
            ("bucket_0_2", 0, 2),
            ("bucket_2_5", 2, 5),
            ("bucket_5_8", 5, 8),
            ("bucket_8_10", 8, 10),
            ("bucket_10_15", 10, 15),
            ("bucket_15_20", 15, 20),
            ("bucket_20_plus", 20, 999),
        ]
        distribution = {}
        if 'Station' in kt_valid.columns and len(kt_valid) > 0:
            for station, grp in kt_valid.groupby('Station'):
                station_name = str(station) if not pd.isna(station) else "Unknown"
                times = grp['_fulfillment_min'].values
                dist = {}
                for bucket_name, low, high in buckets:
                    dist[bucket_name] = int(((times >= low) & (times < high)).sum())
                distribution[station_name] = dist

        # ── Hourly speed by station ──
        hourly_speed = {}
        kt_valid['_fired_dt'] = kt_valid['Fired Date'].apply(parse_toast_datetime)
        kt_valid_with_hour = kt_valid[kt_valid['_fired_dt'].notna()].copy()
        kt_valid_with_hour['_hour'] = kt_valid_with_hour['_fired_dt'].apply(lambda x: x.hour)

        if 'Station' in kt_valid_with_hour.columns and len(kt_valid_with_hour) > 0:
            for station, grp in kt_valid_with_hour.groupby('Station'):
                station_name = str(station) if not pd.isna(station) else "Unknown"
                hourly_data = []
                for h, h_grp in grp.groupby('_hour'):
                    times = h_grp['_fulfillment_min'].values
                    hourly_data.append({
                        "hour": int(h),
                        "median": round(float(np.median(times)), 2),
                        "tickets": len(times),
                    })
                hourly_data.sort(key=lambda x: x["hour"])
                hourly_speed[station_name] = hourly_data

        # ── Walk-in vs online by station ──
        walkin_vs_online = {}
        od = _filter_voided_orders(order_details.copy())
        if 'Order #' in od.columns and 'Check #' in kt_valid.columns:
            # Join kitchen timings with order details to get dining option
            od_channel = od[['Order #', 'Dining Options']].drop_duplicates('Order #')
            kt_merged = kt_valid.merge(
                od_channel,
                left_on='Check #',
                right_on='Order #',
                how='left',
            )
            kt_merged['_group'] = kt_merged['Dining Options'].apply(_get_channel_group)

            if 'Station' in kt_merged.columns:
                for station, grp in kt_merged.groupby('Station'):
                    station_name = str(station) if not pd.isna(station) else "Unknown"
                    walkin = grp[grp['_group'] == 'Walk-In']['_fulfillment_min']
                    online = grp[grp['_group'].isin(['3P', 'Online'])]['_fulfillment_min']
                    walkin_vs_online[station_name] = {
                        "walkin_median": round(float(walkin.median()), 2) if len(walkin) > 0 else None,
                        "online_median": round(float(online.median()), 2) if len(online) > 0 else None,
                    }

        # ── Peak concurrent tickets ──
        peak_concurrent = {"count": 0, "time_str": ""}
        kt_with_times = kt[kt['_fired_dt'].notna() & kt['_fulfilled_dt'].notna()].copy()
        if len(kt_with_times) > 0:
            # Sample each minute of the day and count overlapping tickets
            min_time = kt_with_times['_fired_dt'].min()
            max_time = kt_with_times['_fulfilled_dt'].max()
            if min_time and max_time:
                max_count = 0
                max_time_str = ""
                current_time = min_time.replace(second=0)
                end_scan = max_time.replace(second=0) + timedelta(minutes=1)
                # Limit scan to avoid runaway loops (max 16 hours of minutes = 960)
                max_iterations = 960
                iteration = 0
                while current_time <= end_scan and iteration < max_iterations:
                    concurrent = len(kt_with_times[
                        (kt_with_times['_fired_dt'] <= current_time) &
                        (kt_with_times['_fulfilled_dt'] >= current_time)
                    ])
                    if concurrent > max_count:
                        max_count = concurrent
                        max_time_str = current_time.strftime("%I:%M %p")
                    current_time += timedelta(minutes=1)
                    iteration += 1
                peak_concurrent = {"count": max_count, "time_str": max_time_str}

        # ── Fulfilled by ──
        fulfilled_by = []
        if 'Fulfilled By' in kt.columns:
            fb_agg = kt[kt['Fulfilled By'].notna()].groupby('Fulfilled By').size().reset_index(
                name='tickets'
            )
            fb_agg = fb_agg.sort_values('tickets', ascending=False)
            fulfilled_by = [
                {"name": row['Fulfilled By'], "tickets": int(row['tickets'])}
                for _, row in fb_agg.iterrows()
            ]

        return {
            "stations": stations,
            "distribution": distribution,
            "hourly_speed": hourly_speed,
            "walkin_vs_online": walkin_vs_online,
            "peak_concurrent": peak_concurrent,
            "fulfilled_by": fulfilled_by,
            "outlier_count": outlier_count,
        }
    except Exception as e:
        logger.warning("compute_kitchen_metrics failed: %s", e)
        return {
            "stations": {}, "distribution": {},
            "hourly_speed": {}, "walkin_vs_online": {},
            "peak_concurrent": {"count": 0, "time_str": ""},
            "fulfilled_by": [],
            "_error": str(e),
        }


# ═══════════════════════════════════════════════════════════════
#  9. LABOR METRICS
# ═══════════════════════════════════════════════════════════════
