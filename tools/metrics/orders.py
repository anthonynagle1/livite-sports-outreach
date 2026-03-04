"""Order intelligence and menu item analysis."""

import pandas as pd
import numpy as np
from .utils import (
    _filter_voided_items, _filter_voided_orders, _safe_numeric, _safe_div,
    _get_channel, _get_channel_group, CHANNEL_MAP
)

def compute_order_intelligence(order_details, item_details, all_items_report, check_details):
    """Compute order-level intelligence: top items, menu mix, voids, discounts.

    Returns dict with item rankings, category mix, single-item rates,
    discount analysis, void analysis, and more.
    """
    try:
        items = _filter_voided_items(item_details.copy())
        for col in ['Net Price', 'Gross Price', 'Discount', 'Qty']:
            items[col] = _safe_numeric(items, col)

        od = _filter_voided_orders(order_details.copy())
        od['Amount'] = _safe_numeric(od, 'Amount')

        total_items_sold = int(items['Qty'].sum())
        total_orders = len(od)
        avg_items_per_order = round(_safe_div(total_items_sold, total_orders), 2)

        # ── Top items by qty ──
        item_agg = items.groupby('Menu Item').agg(
            qty=('Qty', 'sum'),
            revenue=('Net Price', 'sum'),
            orders=('Order Id', 'nunique'),
        ).reset_index()
        item_agg['avg_price'] = round(item_agg['revenue'] / item_agg['qty'].replace(0, np.nan), 2)
        item_agg['avg_price'] = item_agg['avg_price'].fillna(0)

        top_by_qty = item_agg.nlargest(15, 'qty')
        top_items_by_qty = [
            {
                "item": row['Menu Item'],
                "qty": int(row['qty']),
                "revenue": round(row['revenue'], 2),
                "avg_price": round(row['avg_price'], 2),
                "orders": int(row['orders']),
            }
            for _, row in top_by_qty.iterrows()
        ]

        # ── Top items by revenue ──
        top_by_rev = item_agg.nlargest(15, 'revenue')
        top_items_by_revenue = [
            {
                "item": row['Menu Item'],
                "revenue": round(row['revenue'], 2),
                "qty": int(row['qty']),
                "avg_price": round(row['avg_price'], 2),
            }
            for _, row in top_by_rev.iterrows()
        ]

        # ── Normalize Menu Group names ──
        # Toast uses different Menu Group names for Walk-In vs 3P menus.
        # This map merges them into canonical display names.
        _MENU_GROUP_MAP = {
            "panini wraps": "Wraps & Paninis",
            "panini wraps  + side of plantain chips": "Wraps & Paninis",
            "wrap boxes": "Wraps & Paninis",
            "salads & bowls": "Salads & Bowls",
            "salads + bowls": "Salads & Bowls",
            "healthy smoothies (18 oz)": "Smoothies",
            "smoothies": "Smoothies",
            "fresh pressed juice": "Juice",
            "freshly pressed juice": "Juice",
            "snacks": "Snacks",
            "all snacks": "Snacks",
            "soup": "Soup",
            "soups": "Soup",
            "house made beverages": "Drinks",
            "house beverages": "Drinks",
            "grab & go drinks": "Drinks",
            "drinks": "Drinks",
            "hot teas": "Drinks",
            "iced matcha": "Matcha",
            "for the group": "Catering",
        }
        if 'Menu Group' in items.columns:
            items['_menu_group_norm'] = items['Menu Group'].astype(str).str.strip().str.lower().map(
                _MENU_GROUP_MAP
            ).fillna(items['Menu Group'].astype(str).str.strip())

        # ── Menu group mix ──
        total_revenue = items['Net Price'].sum()
        menu_group_mix = []
        group_col = '_menu_group_norm' if '_menu_group_norm' in items.columns else 'Menu Group'
        if group_col in items.columns:
            group_agg = items.groupby(group_col).agg(
                revenue=('Net Price', 'sum'),
                qty=('Qty', 'sum'),
            ).reset_index()
            group_agg = group_agg.sort_values('revenue', ascending=False)
            for _, row in group_agg.iterrows():
                menu_group_mix.append({
                    "group": row[group_col],
                    "revenue": round(row['revenue'], 2),
                    "qty": int(row['qty']),
                    "pct_revenue": round(_safe_div(row['revenue'], total_revenue) * 100, 1),
                })

        # ── Menu mix ──
        menu_mix = []
        if 'Menu' in items.columns:
            m_agg = items.groupby('Menu').agg(
                revenue=('Net Price', 'sum'),
                qty=('Qty', 'sum'),
            ).reset_index()
            m_agg = m_agg.sort_values('revenue', ascending=False)
            for _, row in m_agg.iterrows():
                menu_mix.append({
                    "menu": row['Menu'],
                    "revenue": round(row['revenue'], 2),
                    "qty": int(row['qty']),
                })

        # ── Sales category mix ──
        category_mix = []
        if 'Sales Category' in items.columns:
            cat_agg = items.groupby('Sales Category').agg(
                revenue=('Net Price', 'sum'),
                qty=('Qty', 'sum'),
            ).reset_index()
            cat_agg = cat_agg.sort_values('revenue', ascending=False)
            for _, row in cat_agg.iterrows():
                category_mix.append({
                    "category": row['Sales Category'],
                    "revenue": round(row['revenue'], 2),
                    "qty": int(row['qty']),
                    "pct": round(_safe_div(row['revenue'], total_revenue) * 100, 1),
                })

        # ── Single-item rate ──
        items_per_order = items.groupby('Order Id')['Qty'].sum()
        single_item_orders = int((items_per_order == 1).sum())
        single_item_rate = round(_safe_div(single_item_orders, total_orders) * 100, 1)

        # Single-item rate by channel
        single_item_by_channel = {}
        if 'Dining Option' in items.columns:
            for ch, grp in items.groupby('Dining Option'):
                ch_items_per_order = grp.groupby('Order Id')['Qty'].sum()
                ch_total = len(ch_items_per_order)
                ch_single = int((ch_items_per_order == 1).sum())
                channel_name = _get_channel(ch)
                single_item_by_channel[channel_name] = round(
                    _safe_div(ch_single, ch_total) * 100, 1
                )

        # ── Discount analysis by channel ──
        # Use Gross - Net as effective discount (captures BOGO, employee meals,
        # loyalty, manager comps — not just the Discount column)
        discount_by_channel = []
        if 'Dining Option' in items.columns:
            disc_agg = items.groupby('Dining Option').agg(
                gross=('Gross Price', 'sum'),
                net=('Net Price', 'sum'),
            ).reset_index()
            for _, row in disc_agg.iterrows():
                channel_name = _get_channel(row['Dining Option'])
                effective_disc = max(round(row['gross'] - row['net'], 2), 0)
                disc_rate = round(_safe_div(effective_disc, row['gross']) * 100, 1)
                discount_by_channel.append({
                    "channel": channel_name,
                    "gross": round(row['gross'], 2),
                    "discount": effective_disc,
                    "net": round(row['net'], 2),
                    "disc_rate": disc_rate,
                })
            discount_by_channel.sort(key=lambda x: x['discount'], reverse=True)

        # ── Discount by reason (from CheckDetails) ──
        discount_by_reason = []
        if check_details is not None and 'Reason of Discount' in check_details.columns:
            cd = check_details.copy()
            cd['Discount'] = _safe_numeric(cd, 'Discount')
            cd_disc = cd[cd['Discount'] > 0]
            if len(cd_disc) > 0:
                # Normalize discount reasons: consolidate repeated/compound entries
                # e.g. "Uber Eats BOGO, Uber Eats BOGO" → "Uber Eats BOGO"
                def _normalize_discount_reason(reason):
                    if not isinstance(reason, str) or not reason.strip():
                        return reason
                    # Split on comma, deduplicate, rejoin
                    parts = [p.strip() for p in reason.split(',') if p.strip()]
                    seen = []
                    for p in parts:
                        if p not in seen:
                            seen.append(p)
                    return ', '.join(seen) if seen else reason

                cd_disc = cd_disc.copy()
                cd_disc['Reason of Discount'] = cd_disc['Reason of Discount'].apply(_normalize_discount_reason)

                reason_agg = cd_disc.groupby('Reason of Discount').agg(
                    count=('Discount', 'size'),
                    total_discount=('Discount', 'sum'),
                ).reset_index()
                reason_agg = reason_agg.sort_values('total_discount', ascending=False)
                for _, row in reason_agg.iterrows():
                    discount_by_reason.append({
                        "reason": row['Reason of Discount'],
                        "count": int(row['count']),
                        "total_discount": round(row['total_discount'], 2),
                    })

        # ── Uber BOGO impact ──
        uber_bogo_impact = {"discount_total": 0, "annualized": 0}
        uber_items = items[
            items['Dining Option'].astype(str).str.contains('Uber', case=False, na=False)
        ]
        if len(uber_items) > 0:
            uber_disc = round(uber_items['Discount'].sum(), 2)
            uber_bogo_impact = {
                "discount_total": uber_disc,
                "annualized": round(uber_disc * 365, 2),
            }

        # ── Void analysis (from unfiltered items to see voids) ──
        void_analysis = {"void_qty": 0, "void_amount": 0, "void_items": []}
        raw_items = item_details.copy()
        if 'Void?' in raw_items.columns:
            raw_items['_is_void'] = raw_items['Void?'].astype(str).str.strip().str.lower().isin(
                ['true', '1', 'yes']
            )
            voids = raw_items[raw_items['_is_void']]
            if len(voids) > 0:
                voids_calc = voids.copy()
                voids_calc['Qty'] = _safe_numeric(voids_calc, 'Qty')
                voids_calc['Net Price'] = _safe_numeric(voids_calc, 'Net Price')
                void_qty = int(voids_calc['Qty'].sum())
                void_amount = round(voids_calc['Net Price'].sum(), 2)
                void_item_agg = voids_calc.groupby('Menu Item').agg(
                    qty=('Qty', 'sum'),
                    amount=('Net Price', 'sum'),
                ).reset_index().sort_values('qty', ascending=False).head(10)
                void_items = [
                    {"item": row['Menu Item'], "qty": int(row['qty']), "amount": round(row['amount'], 2)}
                    for _, row in void_item_agg.iterrows()
                ]
                void_analysis = {
                    "void_qty": void_qty,
                    "void_amount": void_amount,
                    "void_items": void_items,
                }

        # ── Average selling price ──
        avg_selling_price = round(
            _safe_div(items['Net Price'].sum(), items['Qty'].sum()), 2
        )

        # ── Normalize Menu Group names ──
        if 'Menu Group' in items.columns:
            items['Menu Group'] = items['Menu Group'].astype(str).str.strip()

        # ── First Party vs Third Party top items ──
        first_party_items = []
        third_party_items = []
        if 'Dining Option' in items.columns:
            items['_party'] = items['Dining Option'].apply(
                lambda x: 'first' if _get_channel_group(x) in ('Walk-In', 'Online') else 'third'
            )
            for party, label_list in [('first', first_party_items), ('third', third_party_items)]:
                party_df = items[items['_party'] == party]
                if len(party_df) > 0:
                    pa = party_df.groupby('Menu Item').agg(
                        qty=('Qty', 'sum'), revenue=('Net Price', 'sum'),
                    ).reset_index().sort_values('qty', ascending=False).head(15)
                    for _, row in pa.iterrows():
                        label_list.append({
                            "item": row['Menu Item'],
                            "qty": int(row['qty']),
                            "revenue": round(row['revenue'], 2),
                        })

        # ── Per-item menu breakdown (Livite vs 3P vs Catering) ──
        items_by_menu_breakdown = []
        top_item_names = [r['item'] for r in top_items_by_qty[:15]]
        if 'Menu' in items.columns:
            for item_name in top_item_names:
                item_rows = items[items['Menu Item'] == item_name]
                menu_split = {}
                for menu_val, grp in item_rows.groupby('Menu'):
                    menu_split[str(menu_val)] = {
                        "qty": int(grp['Qty'].sum()),
                        "revenue": round(grp['Net Price'].sum(), 2),
                    }
                items_by_menu_breakdown.append({
                    "item": item_name,
                    "menu_split": menu_split,
                })

        # ── Catering breakout ──
        catering = {"revenue": 0, "qty": 0, "orders": 0, "top_items": [], "pct_of_total": 0}
        if 'Menu' in items.columns:
            catering_items = items[
                items['Menu'].astype(str).str.contains('Catering', case=False, na=False)
            ]
            if len(catering_items) > 0:
                catering_rev = round(catering_items['Net Price'].sum(), 2)
                catering_qty = int(catering_items['Qty'].sum())
                catering_orders = int(catering_items['Order Id'].nunique())
                catering_top = catering_items.groupby('Menu Item').agg(
                    qty=('Qty', 'sum'),
                    revenue=('Net Price', 'sum'),
                ).reset_index().sort_values('revenue', ascending=False).head(10)
                catering_top_items = [
                    {"item": row['Menu Item'], "qty": int(row['qty']), "revenue": round(row['revenue'], 2)}
                    for _, row in catering_top.iterrows()
                ]
                catering = {
                    "revenue": catering_rev,
                    "qty": catering_qty,
                    "orders": catering_orders,
                    "top_items": catering_top_items,
                    "pct_of_total": round(_safe_div(catering_rev, total_revenue) * 100, 1),
                }

        return {
            "total_items_sold": total_items_sold,
            "avg_items_per_order": avg_items_per_order,
            "top_items_by_qty": top_items_by_qty,
            "top_items_by_revenue": top_items_by_revenue,
            "items_by_menu_breakdown": items_by_menu_breakdown,
            "first_party_items": first_party_items,
            "third_party_items": third_party_items,
            "menu_group_mix": menu_group_mix,
            "menu_mix": menu_mix,
            "category_mix": category_mix,
            "catering": catering,
            "single_item_rate": single_item_rate,
            "single_item_by_channel": single_item_by_channel,
            "discount_by_channel": discount_by_channel,
            "discount_by_reason": discount_by_reason,
            "uber_bogo_impact": uber_bogo_impact,
            "void_analysis": void_analysis,
            "avg_selling_price": avg_selling_price,
        }
    except Exception as e:
        return {
            "total_items_sold": 0, "avg_items_per_order": 0,
            "top_items_by_qty": [], "top_items_by_revenue": [],
            "items_by_menu_breakdown": [],
            "first_party_items": [], "third_party_items": [],
            "menu_group_mix": [], "menu_mix": [], "category_mix": [],
            "catering": {},
            "single_item_rate": 0, "single_item_by_channel": {},
            "discount_by_channel": [], "discount_by_reason": [],
            "uber_bogo_impact": {}, "void_analysis": {},
            "avg_selling_price": 0,
            "_error": str(e),
        }


# ═══════════════════════════════════════════════════════════════
#  6. BASKET ANALYSIS
# ═══════════════════════════════════════════════════════════════
