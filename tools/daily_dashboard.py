"""
Livite Dashboard — Main Entry Point

Pulls Toast CSVs from Azure (with caching), computes comprehensive metrics,
fetches comparison data (WoW/MoM/SWLY), detects anomalies,
and generates a self-contained HTML dashboard.

Usage:
    python3 tools/daily_dashboard.py                     # Yesterday
    python3 tools/daily_dashboard.py 20260216             # Specific date
    python3 tools/daily_dashboard.py 20260210 20260216    # Date range
    python3 tools/daily_dashboard.py --last 7             # Last 7 days
    python3 tools/daily_dashboard.py --last 30            # Last 30 days
    python3 tools/daily_dashboard.py --this week          # Current week (Mon-today)
    python3 tools/daily_dashboard.py --this month         # Current month (1st-today)
    python3 tools/daily_dashboard.py --last-week          # Prior full Mon-Sun
    python3 tools/daily_dashboard.py --last-month         # Prior full calendar month
    python3 tools/daily_dashboard.py --force 20260216     # Bypass cache
"""

import os
import sys
import warnings
import base64
from datetime import datetime, timedelta
from calendar import monthrange

warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd

from fetch_toast_data import get_daily_data, get_toast_csv_cached, list_available_dates
import fetch_toast_data
from dashboard_metrics import compute_all_metrics, detect_anomalies, compute_analyst_insights, parse_toast_datetime
from dashboard_comparisons import (
    resolve_comparison_dates, fetch_all_comparisons, compute_all_deltas
)
from dashboard_html import build_dashboard
from dashboard_aggregation import aggregate_metrics

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')

EARLIEST_DATE = datetime(2024, 11, 7)


# ──────────────────────────────────────────────
# Date range resolution
# ──────────────────────────────────────────────

def resolve_date_range(args: list) -> tuple:
    """
    Parse CLI args into (start_date, end_date) datetimes.
    Returns (None, None) for single-day mode.
    Returns (start, end) for range mode.
    """
    if '--last' in args:
        idx = args.index('--last')
        n = int(args[idx + 1])
        end = datetime.now() - timedelta(days=1)  # yesterday
        start = end - timedelta(days=n - 1)
        return start, end

    if '--this' in args:
        idx = args.index('--this')
        period = args[idx + 1].lower()
        today = datetime.now()
        if period == 'week':
            # Monday of current week
            start = today - timedelta(days=today.weekday())
            end = today - timedelta(days=1)  # yesterday
            if end < start:
                end = start
            return start, end
        elif period == 'month':
            start = today.replace(day=1)
            end = today - timedelta(days=1)
            if end < start:
                end = start
            return start, end

    if '--last-week' in args:
        today = datetime.now()
        # Last Monday
        last_monday = today - timedelta(days=today.weekday() + 7)
        last_sunday = last_monday + timedelta(days=6)
        return last_monday, last_sunday

    if '--last-month' in args:
        today = datetime.now()
        first_of_this_month = today.replace(day=1)
        last_day_prev = first_of_this_month - timedelta(days=1)
        first_of_prev = last_day_prev.replace(day=1)
        return first_of_prev, last_day_prev

    # Two positional args = date range
    date_args = [a for a in args if a.isdigit() and len(a) == 8]
    if len(date_args) == 2:
        return (datetime.strptime(date_args[0], "%Y%m%d"),
                datetime.strptime(date_args[1], "%Y%m%d"))

    return None, None


def format_range_display(start: datetime, end: datetime, num_days: int) -> str:
    """Format a human-readable range string."""
    if start.month == end.month and start.year == end.year:
        return f"{start.strftime('%B')} {start.day}-{end.day}, {start.year} ({num_days} days)"
    elif start.year == end.year:
        return f"{start.strftime('%b')} {start.day} - {end.strftime('%b')} {end.day}, {start.year} ({num_days} days)"
    else:
        return f"{start.strftime('%b %d, %Y')} - {end.strftime('%b %d, %Y')} ({num_days} days)"


# ──────────────────────────────────────────────
# 4-week rolling average
# ──────────────────────────────────────────────

def compute_quarter_hourly_4wra(date, available_dates):
    """
    Fetch OrderDetails for the same weekday over the prior 4 weeks.
    Compute per-15-min-slot average revenue and orders.
    Returns dict keyed by "HH:QQ" with avg_revenue, avg_orders, weeks_found.
    """
    slot_totals = {}  # key: "H:QQ" → {"revenues": [], "orders": []}
    weeks_found = 0

    for w in range(1, 5):
        hist_date = date - timedelta(days=7 * w)
        if hist_date < EARLIEST_DATE:
            continue
        hist_str = hist_date.strftime("%Y%m%d")
        if hist_str not in available_dates:
            continue
        try:
            od = get_toast_csv_cached(hist_str, "OrderDetails.csv")
        except Exception:
            continue

        # Filter voided
        if 'Voided' in od.columns:
            od = od[od['Voided'] != True].copy()
        od['Amount'] = pd.to_numeric(od.get('Amount', pd.Series(dtype='float64')), errors='coerce').fillna(0)

        # Parse datetimes and extract hour/minute
        od['_opened_dt'] = od['Opened'].apply(parse_toast_datetime)
        od['_hour'] = od['_opened_dt'].apply(lambda x: x.hour if x else None)
        od['_minute'] = od['_opened_dt'].apply(lambda x: x.minute if x else None)

        weeks_found += 1

        for h in range(7, 23):
            for q in [0, 15, 30, 45]:
                mask = (od['_hour'] == h) & (od['_minute'].notna())
                mask = mask & (od['_minute'] >= q) & (od['_minute'] < q + 15)
                slot_df = od[mask]
                q_rev = round(slot_df['Amount'].sum(), 2)
                q_orders = len(slot_df)
                key = f"{h}:{q:02d}"
                if key not in slot_totals:
                    slot_totals[key] = {"revenues": [], "orders": []}
                slot_totals[key]["revenues"].append(q_rev)
                slot_totals[key]["orders"].append(q_orders)

    # Compute averages
    result = {}
    for key, vals in slot_totals.items():
        n = len(vals["revenues"])
        result[key] = {
            "avg_revenue": round(sum(vals["revenues"]) / n, 2) if n > 0 else 0,
            "avg_orders": round(sum(vals["orders"]) / n, 1) if n > 0 else 0,
            "weeks_found": n,
        }

    return result, weeks_found


# ──────────────────────────────────────────────
# Logo loading
# ──────────────────────────────────────────────

def load_logo() -> str:
    """Load and base64-encode the Livite logo."""
    logo_path = os.path.join(PROJECT_ROOT, "livite-logo-files 3", "01_wordmark",
                             "01_color", "livite-wordmark_green_rgb.png")
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        print(f"  Logo loaded ({len(b64) // 1024}KB encoded).")
        return b64
    else:
        print(f"  Warning: Logo not found at {logo_path}")
        return ""


# ──────────────────────────────────────────────
# Single-day pipeline
# ──────────────────────────────────────────────

def generate_daily_dashboard(date: datetime = None) -> str:
    """
    Full pipeline: fetch → compute → compare → detect → render → save.
    Returns path to saved HTML file.
    """
    if date is None:
        date = datetime.now() - timedelta(days=1)

    date_str = date.strftime("%Y%m%d")
    display = date.strftime("%A, %B %d, %Y")

    print(f"\n{'='*60}")
    print(f"  Livite Daily Dashboard: {display}")
    print(f"{'='*60}")

    # Step 1: Fetch today's data (all 9 CSVs)
    print(f"\n[1/5] Fetching data for {date_str}...")
    data = get_daily_data(date)

    if 'OrderDetails' not in data:
        print("FATAL: OrderDetails not found. Cannot generate dashboard.")
        return None

    # Step 2: Compute all metrics
    print("\n[2/5] Computing metrics...")
    metrics = compute_all_metrics(data, date)

    # Step 2b: Compute 4-week rolling average for 15-min slots
    print("  Computing 4-week rolling average for time breakdown...")
    try:
        _avail_for_4wra = set(list_available_dates())
    except Exception:
        _avail_for_4wra = set()

    try:
        slot_4wra, weeks_found = compute_quarter_hourly_4wra(date, _avail_for_4wra)
        if metrics.get('revenue') and slot_4wra:
            metrics['revenue']['quarter_hourly_4wra'] = slot_4wra
            metrics['revenue']['quarter_hourly_4wra_weeks'] = weeks_found
            print(f"  4WRA computed from {weeks_found} prior week(s).")
        else:
            print("  No historical data available for 4WRA.")
    except Exception as e:
        print(f"  Warning: Could not compute 4WRA: {e}")

    # Step 3: Resolve and fetch comparison data
    print("\n[3/5] Fetching comparison data...")
    comp_dates = resolve_comparison_dates(date)
    try:
        available = set(list_available_dates())
    except Exception as e:
        print(f"  Warning: Could not list available dates: {e}")
        available = set()

    comparisons = fetch_all_comparisons(comp_dates, available)

    # Compute deltas
    current_summary = {
        'revenue': metrics['revenue']['toast_total'] if metrics.get('revenue') else 0,
        'orders': metrics['revenue']['total_orders'] if metrics.get('revenue') else 0,
        'avg_check': metrics['revenue']['avg_check'] if metrics.get('revenue') else 0,
        'guests': metrics['revenue']['total_guests'] if metrics.get('revenue') else 0,
        'labor_total': metrics['labor']['total_labor'] if metrics.get('labor') else 0,
        'labor_pct': metrics['labor']['labor_pct'] if metrics.get('labor') else 0,
        'unique_customers': metrics['customers']['unique_customers'] if metrics.get('customers') else 0,
    }
    deltas = compute_all_deltas(current_summary, comparisons)
    comparisons['deltas'] = deltas

    # Step 4: Detect anomalies
    print("\n[4/5] Checking for anomalies...")
    anomalies = detect_anomalies(metrics, comparisons)
    if anomalies:
        for a in anomalies:
            icon = "!!" if a['severity'] == 'red' else "!"
            print(f"  [{icon}] {a['message']}")
    else:
        print("  No anomalies detected.")

    # Step 4b: Compute analyst insights
    print("  Computing analyst insights...")
    try:
        _4wra = metrics.get('revenue', {}).get('quarter_hourly_4wra', {})
        analyst_insights = compute_analyst_insights(metrics, _4wra)
        print(f"  {len(analyst_insights)} insight(s) generated.")
    except Exception as e:
        print(f"  Warning: Could not compute insights: {e}")
        analyst_insights = []

    # Step 5: Render HTML
    print("\n[5/5] Generating dashboard...")
    logo_b64 = load_logo()

    prev_date = date - timedelta(days=1)
    next_date = date + timedelta(days=1)
    prev_str = prev_date.strftime("%Y%m%d") if prev_date.strftime("%Y%m%d") in available else ""
    next_str = next_date.strftime("%Y%m%d") if next_date.strftime("%Y%m%d") in available else ""
    html = build_dashboard(metrics, comparisons, anomalies,
                           date_str=date_str, prev_date_str=prev_str,
                           next_date_str=next_str,
                           analyst_insights=analyst_insights,
                           logo_b64=logo_b64)

    # Save to project root
    output_filename = f"livite_daily_dashboard_{date_str}.html"
    output_path = os.path.join(PROJECT_ROOT, output_filename)
    with open(output_path, 'w') as f:
        f.write(html)

    print(f"\n  Dashboard saved: {output_path}")
    print(f"  Open in browser to view.\n")
    return output_path


# ──────────────────────────────────────────────
# Date range pipeline
# ──────────────────────────────────────────────

def generate_range_dashboard(start: datetime, end: datetime) -> str:
    """
    Multi-day pipeline: fetch each day → compute metrics → aggregate → render.
    Returns path to saved HTML file.
    """
    start_str = start.strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")
    num_days = (end - start).days + 1
    range_display = format_range_display(start, end, num_days)

    print(f"\n{'='*60}")
    print(f"  Livite Range Dashboard: {range_display}")
    print(f"{'='*60}")

    # Step 1: Fetch and compute metrics for each day
    print(f"\n[1/4] Fetching & computing {num_days} days ({start_str} to {end_str})...")
    daily_metrics = []
    current = start
    while current <= end:
        ds = current.strftime("%Y%m%d")
        print(f"\n  --- {current.strftime('%A %b %d')} ({ds}) ---")
        data = get_daily_data(current)
        if 'OrderDetails' not in data:
            print(f"    Skipped (no OrderDetails)")
            current += timedelta(days=1)
            continue

        metrics = compute_all_metrics(data, current)
        daily_metrics.append(metrics)
        rev = metrics.get('revenue', {}).get('toast_total', 0)
        orders = metrics.get('revenue', {}).get('total_orders', 0)
        print(f"    {orders} orders, ${rev:,.2f} revenue")
        current += timedelta(days=1)

    if not daily_metrics:
        print("FATAL: No data available for any day in range.")
        return None

    actual_days = len(daily_metrics)
    print(f"\n  {actual_days}/{num_days} days loaded successfully.")

    # Step 2: Aggregate across all days
    print(f"\n[2/4] Aggregating {actual_days} days...")
    agg = aggregate_metrics(daily_metrics, start_str, end_str, num_days)

    # Set display fields for the HTML renderer
    agg['date_display'] = range_display
    agg['day_of_week'] = f"{actual_days}-Day Summary"

    agg_rev = agg.get('toast_total', 0)
    agg_orders = agg.get('total_orders', 0)
    agg_labor = agg.get('total_labor', 0)
    print(f"  Totals: ${agg_rev:,.2f} revenue, {agg_orders} orders, ${agg_labor:,.2f} labor")
    if actual_days > 0:
        print(f"  Daily avg: ${agg_rev / actual_days:,.2f} revenue, {agg_orders / actual_days:.0f} orders")

    # Step 3: Detect anomalies + insights on aggregated data
    print(f"\n[3/4] Analyzing aggregated data...")
    anomalies = detect_anomalies(agg, {})
    if anomalies:
        for a in anomalies:
            icon = "!!" if a['severity'] == 'red' else "!"
            print(f"  [{icon}] {a['message']}")

    try:
        _4wra = agg.get('revenue', {}).get('quarter_hourly_4wra', {})
        analyst_insights = compute_analyst_insights(agg, _4wra)
        print(f"  {len(analyst_insights)} insight(s) generated.")
    except Exception as e:
        print(f"  Warning: Could not compute insights: {e}")
        analyst_insights = []

    # Step 4: Render HTML
    print(f"\n[4/4] Generating dashboard...")
    logo_b64 = load_logo()

    html = build_dashboard(agg, comparisons={}, anomalies=anomalies,
                           date_str=f"{start_str}_{end_str}",
                           prev_date_str="", next_date_str="",
                           analyst_insights=analyst_insights,
                           logo_b64=logo_b64)

    # Save to project root
    output_filename = f"livite_range_dashboard_{start_str}_{end_str}.html"
    output_path = os.path.join(PROJECT_ROOT, output_filename)
    with open(output_path, 'w') as f:
        f.write(html)

    print(f"\n  Dashboard saved: {output_path}")
    print(f"  Open in browser to view.\n")
    return output_path


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def generate_batch(start: datetime, end: datetime):
    """
    Generate daily dashboards for every day in a range, plus weekly summaries.
    Use this to pre-generate all dashboards so the date picker links work.
    """
    num_days = (end - start).days + 1
    print(f"\n{'='*60}")
    print(f"  Batch Generate: {start.strftime('%b %d')} - {end.strftime('%b %d, %Y')} ({num_days} days)")
    print(f"{'='*60}")

    # Generate each daily dashboard
    generated = []
    current = start
    while current <= end:
        ds = current.strftime("%Y%m%d")
        output = os.path.join(PROJECT_ROOT, f"livite_daily_dashboard_{ds}.html")
        if os.path.exists(output):
            print(f"\n  [{ds}] Already exists, skipping. (Use --force to regenerate)")
            generated.append(current)
        else:
            print(f"\n  [{ds}] Generating...")
            try:
                generate_daily_dashboard(current)
                generated.append(current)
            except Exception as e:
                print(f"    ERROR: {e}")
        current += timedelta(days=1)

    # Generate weekly range dashboards (Mon-Sun weeks within the range)
    print(f"\n{'='*60}")
    print(f"  Generating weekly summaries...")
    print(f"{'='*60}")

    # Find the first Monday on or after start
    week_start = start
    while week_start.weekday() != 0:  # 0 = Monday
        week_start += timedelta(days=1)

    while week_start + timedelta(days=6) <= end:
        week_end = week_start + timedelta(days=6)
        ws = week_start.strftime("%Y%m%d")
        we = week_end.strftime("%Y%m%d")
        output = os.path.join(PROJECT_ROOT, f"livite_range_dashboard_{ws}_{we}.html")
        if os.path.exists(output):
            print(f"\n  [Week {ws}-{we}] Already exists, skipping.")
        else:
            print(f"\n  [Week {ws}-{we}] Generating...")
            try:
                generate_range_dashboard(week_start, week_end)
            except Exception as e:
                print(f"    ERROR: {e}")
        week_start += timedelta(days=7)

    print(f"\n{'='*60}")
    print(f"  Batch complete: {len(generated)} daily + weekly dashboards generated.")
    print(f"  All dashboards saved to: {PROJECT_ROOT}")
    print(f"  Share the folder — date picker links will work for all generated dates.")
    print(f"{'='*60}\n")


USAGE = """Usage:
    python3 tools/daily_dashboard.py                     # Yesterday
    python3 tools/daily_dashboard.py 20260216             # Specific date
    python3 tools/daily_dashboard.py 20260210 20260216    # Date range
    python3 tools/daily_dashboard.py --last 7             # Last 7 days
    python3 tools/daily_dashboard.py --last 30            # Last 30 days
    python3 tools/daily_dashboard.py --this week          # Current week (Mon-today)
    python3 tools/daily_dashboard.py --this month         # Current month (1st-today)
    python3 tools/daily_dashboard.py --last-week          # Prior full Mon-Sun
    python3 tools/daily_dashboard.py --last-month         # Prior full calendar month
    python3 tools/daily_dashboard.py --batch 20260201 20260217  # Generate all daily + weekly

Flags:
    --force                                              # Bypass cache / regenerate existing"""

if __name__ == "__main__":
    args = sys.argv[1:]

    # Check for --force flag (bypass cache + regenerate existing)
    if "--force" in args:
        fetch_toast_data.FORCE_REFRESH = True
        args = [a for a in args if a != "--force"]
        print("Force refresh enabled — bypassing cache.\n")

    # Batch mode: generate all daily + weekly dashboards for a range
    if "--batch" in args:
        idx = args.index("--batch")
        remaining = [a for a in args if a != "--batch"]
        date_args = [a for a in remaining if a.isdigit() and len(a) == 8]
        if len(date_args) == 2:
            batch_start = datetime.strptime(date_args[0], "%Y%m%d")
            batch_end = datetime.strptime(date_args[1], "%Y%m%d")
            generate_batch(batch_start, batch_end)
        else:
            print("Usage: python3 tools/daily_dashboard.py --batch YYYYMMDD YYYYMMDD")
            sys.exit(1)
        sys.exit(0)

    # Try to parse as date range
    start, end = resolve_date_range(args)

    if start is not None and end is not None:
        # Range mode
        generate_range_dashboard(start, end)
    elif len(args) == 0:
        generate_daily_dashboard()
    elif len(args) == 1 and args[0].isdigit() and len(args[0]) == 8:
        dt = datetime.strptime(args[0], "%Y%m%d")
        generate_daily_dashboard(dt)
    else:
        print(USAGE)
        sys.exit(1)
