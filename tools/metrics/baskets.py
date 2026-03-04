"""Basket analysis and cross-sell metrics."""

import pandas as pd
from itertools import combinations
from .utils import (
    _filter_voided_items, _safe_numeric, _safe_div,
    _classify_menu_group, _get_channel
)

def compute_basket_analysis(item_details):
    """Compute basket-level analysis: combos, cross-sell, attach rates.

    Groups items by Order Id and analyzes item pairs, category attach
    rates, basket sizes, and channel-specific top items.
    """
    try:
        items = _filter_voided_items(item_details.copy())
        items['Qty'] = _safe_numeric(items, 'Qty')
        items['Net Price'] = _safe_numeric(items, 'Net Price')

        # ── Identify Uber BOGO orders (Uber channel + any item with Discount > 0) ──
        items['Discount'] = _safe_numeric(items, 'Discount')
        uber_bogo_order_ids = set()
        if 'Dining Option' in items.columns:
            uber_items = items[items['Dining Option'].astype(str).str.contains('Uber', case=False, na=False)]
            uber_disc = uber_items[uber_items['Discount'] > 0]
            uber_bogo_order_ids = set(uber_disc['Order Id'].unique())

        def _count_combos(df):
            counter = {}
            grouped = df.groupby('Order Id')['Menu Item'].apply(list)
            for _, item_list in grouped.items():
                unique = sorted(set(str(i).strip() for i in item_list if pd.notna(i)))
                if len(unique) >= 2:
                    for pair in combinations(unique, 2):
                        counter[pair] = counter.get(pair, 0) + 1
            sorted_c = sorted(counter.items(), key=lambda x: x[1], reverse=True)[:10]
            return [{"item1": p[0], "item2": p[1], "frequency": c} for p, c in sorted_c]

        # ── Top combos — ALL orders ──
        top_combos = _count_combos(items)

        # ── Top combos — excluding Uber BOGO ──
        if uber_bogo_order_ids:
            items_no_bogo = items[~items['Order Id'].isin(uber_bogo_order_ids)]
            top_combos_no_bogo = _count_combos(items_no_bogo)
        else:
            top_combos_no_bogo = top_combos

        # ── Cross-sell / attach rates ──
        # Classify each item's menu group into a category
        items['_category'] = items['Menu Group'].apply(_classify_menu_group)

        # Build order-level category presence
        order_categories = items.groupby('Order Id')['_category'].apply(
            lambda x: set(c for c in x if c is not None)
        )

        def _calc_attach(anchor_cat, target_cats, order_cats):
            """Calculate what % of orders containing anchor_cat also have a target_cat."""
            anchor_orders = [oc for oc in order_cats if anchor_cat in oc]
            if not anchor_orders:
                return {}
            result = {}
            for tc in target_cats:
                both = sum(1 for oc in anchor_orders if tc in oc)
                result[tc] = round(_safe_div(both, len(anchor_orders)) * 100, 1)
            return result

        all_cats = ["Smoothie", "Snack", "Salad", "Juice", "Soup"]
        wrap_attach = _calc_attach("Wrap", all_cats, order_categories.values)
        smoothie_attach = _calc_attach(
            "Smoothie", ["Wrap", "Snack", "Salad", "Juice", "Soup"], order_categories.values
        )
        salad_attach = _calc_attach(
            "Salad", ["Wrap", "Smoothie", "Snack", "Juice", "Soup"], order_categories.values
        )

        # ── Snack attach rate ──
        total_orders_count = len(order_categories)
        snack_orders = sum(1 for oc in order_categories.values if "Snack" in oc)
        snack_attach_rate_overall = round(_safe_div(snack_orders, total_orders_count) * 100, 1)

        snack_attach_by_channel = {}
        if 'Dining Option' in items.columns:
            order_channel = items.drop_duplicates('Order Id').set_index('Order Id')['Dining Option']
            for ch_val in items['Dining Option'].dropna().unique():
                ch_orders = order_channel[order_channel == ch_val].index
                ch_cats = order_categories.reindex(ch_orders).dropna()
                ch_total = len(ch_cats)
                ch_snack = sum(1 for oc in ch_cats.values if "Snack" in oc)
                channel_name = _get_channel(ch_val)
                snack_attach_by_channel[channel_name] = round(
                    _safe_div(ch_snack, ch_total) * 100, 1
                )

        snack_attach_rate = {
            "overall": snack_attach_rate_overall,
            "by_channel": snack_attach_by_channel,
        }

        # ── Average basket size (units) ──
        basket_sizes = items.groupby('Order Id')['Qty'].sum()
        avg_basket_overall = round(basket_sizes.mean(), 2) if len(basket_sizes) > 0 else 0

        avg_basket_by_channel = {}
        if 'Dining Option' in items.columns:
            order_channel = items.drop_duplicates('Order Id').set_index('Order Id')['Dining Option']
            for ch_val in items['Dining Option'].dropna().unique():
                ch_orders = order_channel[order_channel == ch_val].index
                ch_baskets = basket_sizes.reindex(ch_orders).dropna()
                channel_name = _get_channel(ch_val)
                avg_basket_by_channel[channel_name] = round(
                    ch_baskets.mean(), 2
                ) if len(ch_baskets) > 0 else 0

        avg_basket_size = {
            "overall": avg_basket_overall,
            "by_channel": avg_basket_by_channel,
        }

        # ── Average basket revenue ($) ──
        basket_revenue = items.groupby('Order Id')['Net Price'].sum()
        avg_rev_overall = round(basket_revenue.mean(), 2) if len(basket_revenue) > 0 else 0

        avg_rev_by_channel = {}
        if 'Dining Option' in items.columns:
            for ch_val in items['Dining Option'].dropna().unique():
                ch_orders = order_channel[order_channel == ch_val].index
                ch_rev = basket_revenue.reindex(ch_orders).dropna()
                channel_name = _get_channel(ch_val)
                avg_rev_by_channel[channel_name] = round(
                    ch_rev.mean(), 2
                ) if len(ch_rev) > 0 else 0

        avg_basket_revenue = {
            "overall": avg_rev_overall,
            "by_channel": avg_rev_by_channel,
        }

        # ── Uber vs walk-in top items ──
        uber_vs_walkin = {"uber_top_items": [], "walkin_top_items": []}
        if 'Dining Option' in items.columns:
            uber_items = items[
                items['Dining Option'].astype(str).str.contains('Uber', case=False, na=False)
            ]
            walkin_items = items[items['Dining Option'] == 'To Go']

            if len(uber_items) > 0:
                uber_top = uber_items.groupby('Menu Item')['Qty'].sum().nlargest(10)
                uber_vs_walkin["uber_top_items"] = [
                    {"item": item, "qty": int(qty)} for item, qty in uber_top.items()
                ]
            if len(walkin_items) > 0:
                walkin_top = walkin_items.groupby('Menu Item')['Qty'].sum().nlargest(10)
                uber_vs_walkin["walkin_top_items"] = [
                    {"item": item, "qty": int(qty)} for item, qty in walkin_top.items()
                ]

        return {
            "top_combos": top_combos,
            "top_combos_no_bogo": top_combos_no_bogo,
            "uber_bogo_orders_excluded": len(uber_bogo_order_ids),
            "wrap_attach": wrap_attach,
            "smoothie_attach": smoothie_attach,
            "salad_attach": salad_attach,
            "snack_attach_rate": snack_attach_rate,
            "avg_basket_size": avg_basket_size,
            "avg_basket_revenue": avg_basket_revenue,
            "uber_vs_walkin": uber_vs_walkin,
        }
    except Exception as e:
        return {
            "top_combos": [], "top_combos_no_bogo": [],
            "uber_bogo_orders_excluded": 0,
            "wrap_attach": {}, "smoothie_attach": {},
            "salad_attach": {}, "snack_attach_rate": 0,
            "avg_basket_size": 0, "avg_basket_revenue": 0,
            "uber_vs_walkin": {},
            "_error": str(e),
        }


# ═══════════════════════════════════════════════════════════════
#  7. MODIFIER ANALYSIS
# ═══════════════════════════════════════════════════════════════
