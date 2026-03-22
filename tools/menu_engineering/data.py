"""Menu Engineering data computation.

Reads ItemSelectionDetails CSVs from .tmp/<YYYYMMDD>/ cache, aggregates
per menu item, and classifies into quadrants (Star / Plowhorse / Puzzle / Dog)
using median splits on quantity sold and average price.
"""

from __future__ import annotations

import logging
import os
import statistics
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Project root is two levels up from this file (tools/menu_engineering/ -> tools/ -> project)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CACHE_DIR = os.path.join(_PROJECT_ROOT, '.tmp')


# ---------------------------------------------------------------------------
# Quadrant classification
# ---------------------------------------------------------------------------

_QUADRANT_LABELS = {
    (True, True): 'star',
    (True, False): 'plowhorse',
    (False, True): 'puzzle',
    (False, False): 'dog',
}

_QUADRANT_DESCRIPTIONS = {
    'star': 'High popularity, high profitability',
    'plowhorse': 'High popularity, low profitability',
    'puzzle': 'Low popularity, high profitability',
    'dog': 'Low popularity, low profitability',
}

_QUADRANT_ACTIONS = {
    'star': 'Maintain prominence on the menu. Feature in marketing and keep consistency.',
    'plowhorse': 'Consider modest price increases, reduce portion cost, or bundle with higher-margin sides.',
    'puzzle': 'Increase visibility — move on menu, add to specials, promote via staff upselling.',
    'dog': 'Evaluate for removal, rebrand, reposition, or rework the recipe to cut cost.',
}


# ---------------------------------------------------------------------------
# CSV collection
# ---------------------------------------------------------------------------

def _collect_item_csvs(start_date: datetime, end_date: datetime) -> pd.DataFrame | None:
    """Read and concatenate ItemSelectionDetails CSVs for the date range.

    Returns a single DataFrame or None if no data is available.
    """
    frames: list[pd.DataFrame] = []
    current = start_date
    while current <= end_date:
        date_str = current.strftime('%Y%m%d')
        csv_path = os.path.join(_CACHE_DIR, date_str, 'ItemSelectionDetails.csv')
        if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
            try:
                df = pd.read_csv(csv_path)
                df['_date'] = date_str
                frames.append(df)
            except Exception:
                logger.debug("Skipping unreadable CSV for %s", date_str)
        current += timedelta(days=1)

    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Fallback: use cached metrics top-15 items
# ---------------------------------------------------------------------------

def _fallback_from_cache(days: int) -> dict:
    """Build a minimal result from the metrics cache top-15 items.

    This is a best-effort fallback when no raw CSVs are available.
    """
    try:
        from metrics_cache import get_cached_metrics, batch_connection
    except ImportError:
        logger.warning("metrics_cache not available for fallback")
        return _empty_result(days)

    end = datetime.now() - timedelta(days=1)
    start = end - timedelta(days=days - 1)

    # Collect top items from cached daily metrics
    item_map: dict[str, dict[str, Any]] = {}
    try:
        with batch_connection():
            current = start
            while current <= end:
                ds = current.strftime('%Y%m%d')
                cached = get_cached_metrics(ds)
                if cached:
                    orders = cached.get('orders', {})
                    for entry in (orders.get('top_items_by_qty') or []):
                        name = entry.get('item', '')
                        if not name:
                            continue
                        if name not in item_map:
                            item_map[name] = {'qty': 0, 'revenue': 0.0, 'menu_group': ''}
                        item_map[name]['qty'] += entry.get('qty', 0)
                        item_map[name]['revenue'] += entry.get('revenue', 0.0)
                current += timedelta(days=1)
    except Exception:
        logger.warning("Failed to read metrics cache for fallback", exc_info=True)
        return _empty_result(days)

    if not item_map:
        return _empty_result(days)

    items = []
    for name, agg in item_map.items():
        qty = agg['qty']
        rev = agg['revenue']
        avg_p = round(rev / qty, 2) if qty > 0 else 0.0
        items.append({
            'name': name,
            'menu_group': agg.get('menu_group', ''),
            'qty_sold': qty,
            'revenue': round(rev, 2),
            'avg_price': avg_p,
            'quadrant': '',  # classified below
        })

    return _classify_and_build(items, days)


# ---------------------------------------------------------------------------
# Empty result
# ---------------------------------------------------------------------------

def _empty_result(days: int) -> dict:
    """Return a valid but empty result dict."""
    period_label = f'Last {days} Days' if days != 1 else 'Yesterday'
    return {
        'period_label': period_label,
        'period_days': days,
        'total_items_analyzed': 0,
        'items': [],
        'quadrant_counts': {'star': 0, 'plowhorse': 0, 'puzzle': 0, 'dog': 0},
        'quadrant_items': {'star': [], 'plowhorse': [], 'puzzle': [], 'dog': []},
        'median_qty': 0,
        'median_avg_price': 0,
        'menu_groups': [],
        'recommendations': [],
    }


# ---------------------------------------------------------------------------
# Classification + result builder
# ---------------------------------------------------------------------------

def _classify_and_build(items: list[dict], days: int) -> dict:
    """Classify items into quadrants and build the full result dict."""
    period_label = f'Last {days} Days' if days != 1 else 'Yesterday'

    if len(items) == 0:
        return _empty_result(days)

    qtys = [it['qty_sold'] for it in items if it['qty_sold'] > 0]
    prices = [it['avg_price'] for it in items if it['avg_price'] > 0]

    if not qtys or not prices:
        return _empty_result(days)

    # Use median as the dividing line — standard menu engineering approach
    median_qty = statistics.median(qtys) if len(qtys) > 1 else qtys[0]
    median_avg_price = statistics.median(prices) if len(prices) > 1 else prices[0]

    # Classify each item
    for item in items:
        high_pop = item['qty_sold'] >= median_qty
        high_price = item['avg_price'] >= median_avg_price
        item['quadrant'] = _QUADRANT_LABELS[(high_pop, high_price)]

    # Sort items by revenue descending for display
    items.sort(key=lambda x: x['revenue'], reverse=True)

    # Count per quadrant
    quadrant_counts = {'star': 0, 'plowhorse': 0, 'puzzle': 0, 'dog': 0}
    quadrant_items: dict[str, list] = {'star': [], 'plowhorse': [], 'puzzle': [], 'dog': []}
    for item in items:
        q = item['quadrant']
        quadrant_counts[q] += 1
        if len(quadrant_items[q]) < 10:
            quadrant_items[q].append(item)

    # Generate recommendations — top 3 items per non-star quadrant
    recommendations = []
    for q in ('plowhorse', 'puzzle', 'dog'):
        for item in quadrant_items[q][:3]:
            recommendations.append({
                'item': item['name'],
                'quadrant': q,
                'action': _QUADRANT_ACTIONS[q],
            })

    # Unique menu groups
    menu_groups = sorted(set(it['menu_group'] for it in items if it.get('menu_group')))

    return {
        'period_label': period_label,
        'period_days': days,
        'total_items_analyzed': len(items),
        'items': items,
        'quadrant_counts': quadrant_counts,
        'quadrant_items': quadrant_items,
        'median_qty': round(median_qty, 1),
        'median_avg_price': round(median_avg_price, 2),
        'menu_groups': menu_groups,
        'recommendations': recommendations,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_menu_engineering(days: int = 30) -> dict:
    """Compute menu engineering matrix from Toast POS data.

    Args:
        days: Number of days to analyze (default 30).

    Returns:
        Dict with items classified into quadrants, counts, recommendations, etc.
    """
    end = datetime.now() - timedelta(days=1)  # yesterday
    start = end - timedelta(days=days - 1)

    # Try reading raw CSVs first (all items, not just top-15)
    combined = _collect_item_csvs(start, end)

    if combined is None or combined.empty:
        logger.info("No CSV data for menu engineering; falling back to cached metrics")
        return _fallback_from_cache(days)

    # Ensure numeric columns
    for col in ('Qty', 'Net Price', 'Gross Price'):
        if col in combined.columns:
            combined[col] = pd.to_numeric(combined[col], errors='coerce').fillna(0)

    # Filter: keep only positive qty rows (exclude voids/refunds)
    combined = combined[combined['Qty'] > 0].copy()

    if combined.empty:
        return _fallback_from_cache(days)

    # Aggregate per menu item
    agg = combined.groupby('Menu Item', dropna=False).agg(
        qty_sold=('Qty', 'sum'),
        revenue=('Net Price', 'sum'),
    ).reset_index()

    # Get menu group (first occurrence per item)
    if 'Menu Group' in combined.columns:
        group_map = (
            combined.dropna(subset=['Menu Group'])
            .drop_duplicates(subset=['Menu Item'])
            .set_index('Menu Item')['Menu Group']
            .to_dict()
        )
    else:
        group_map = {}

    agg['avg_price'] = (agg['revenue'] / agg['qty_sold'].replace(0, float('nan'))).round(2).fillna(0)
    agg['revenue'] = agg['revenue'].round(2)
    agg['qty_sold'] = agg['qty_sold'].astype(int)

    items = []
    for _, row in agg.iterrows():
        name = str(row['Menu Item']) if pd.notna(row['Menu Item']) else 'Unknown'
        items.append({
            'name': name,
            'menu_group': group_map.get(row['Menu Item'], ''),
            'qty_sold': int(row['qty_sold']),
            'revenue': float(row['revenue']),
            'avg_price': float(row['avg_price']),
            'quadrant': '',  # classified in _classify_and_build
        })

    # Filter out items with zero qty or zero revenue (noise)
    items = [it for it in items if it['qty_sold'] > 0 and it['revenue'] > 0]

    return _classify_and_build(items, days)
