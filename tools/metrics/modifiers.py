"""Modifier analysis: add-ons, removals, customizations."""

import pandas as pd
import numpy as np
from .utils import (
    _filter_voided_modifiers, _safe_numeric, _safe_div,
    _get_channel, ALT_MILK_KEYWORDS, ALT_MILK_RECOMMENDED_PRICE,
)

def compute_modifier_analysis(modifier_details, item_selection_details=None):
    """Compute modifier-level analysis: paid modifiers, attach rates, removal rates.

    Returns dict with top modifiers, option group breakdown,
    and free vs paid split.
    """
    try:
        mods = _filter_voided_modifiers(modifier_details.copy())
        mods['Net Price'] = _safe_numeric(mods, 'Net Price')
        mods['Gross Price'] = _safe_numeric(mods, 'Gross Price')
        mods['Qty'] = _safe_numeric(mods, 'Qty')

        # ── Normalize modifier names (merge duplicates from case/whitespace) ──
        if 'Modifier' in mods.columns:
            mods['Modifier'] = mods['Modifier'].astype(str).str.strip()
            mod_lower = mods['Modifier'].str.lower()
            canonical = mods.groupby(mod_lower)['Modifier'].agg(
                lambda x: x.mode().iloc[0] if len(x.mode()) > 0 else x.iloc[0]
            )
            mods['Modifier'] = mod_lower.map(canonical)
        if 'Parent Menu Selection' in mods.columns:
            mods['Parent Menu Selection'] = mods['Parent Menu Selection'].astype(str).str.strip()

        total_modifiers = int(mods['Qty'].sum())
        total_modifier_revenue = round(mods['Net Price'].sum(), 2)

        # ── Top paid modifiers ──
        paid_mods = mods[mods['Net Price'] > 0]
        paid_agg = paid_mods.groupby('Modifier').agg(
            qty=('Qty', 'sum'),
            revenue=('Net Price', 'sum'),
        ).reset_index().sort_values('revenue', ascending=False).head(15)
        top_paid_modifiers = [
            {
                "modifier": row['Modifier'],
                "qty": int(row['qty']),
                "revenue": round(row['revenue'], 2),
            }
            for _, row in paid_agg.iterrows()
        ]

        # ── Option group analysis ──
        option_group_analysis = []
        if 'Option Group Name' in mods.columns:
            og_agg = mods.groupby('Option Group Name').agg(
                count=('Qty', 'sum'),
                revenue=('Net Price', 'sum'),
            ).reset_index().sort_values('revenue', ascending=False)
            for _, row in og_agg.iterrows():
                option_group_analysis.append({
                    "group": row['Option Group Name'],
                    "count": int(row['count']),
                    "revenue": round(row['revenue'], 2),
                })

        # ── Most modified items ──
        most_modified_items = []
        if 'Parent Menu Selection' in mods.columns:
            mod_count = mods.groupby('Parent Menu Selection')['Qty'].sum().reset_index()
            mod_count.columns = ['item', 'modifier_count']
            mod_count = mod_count.sort_values('modifier_count', ascending=False).head(15)
            most_modified_items = [
                {"item": row['item'], "modifier_count": int(row['modifier_count'])}
                for _, row in mod_count.iterrows()
            ]

        # ── Alt milk analysis ──
        alt_milk = []
        for kw in ALT_MILK_KEYWORDS:
            milk_mods = mods[
                mods['Modifier'].astype(str).str.contains(kw, case=False, na=False)
            ]
            if len(milk_mods) > 0:
                qty = int(milk_mods['Qty'].sum())
                current_charge = round(milk_mods['Net Price'].sum(), 2)
                avg_current = round(_safe_div(current_charge, qty), 2)
                recommended_rev = round(qty * ALT_MILK_RECOMMENDED_PRICE, 2)
                revenue_gap = round(recommended_rev - current_charge, 2)
                alt_milk.append({
                    "milk_type": kw.capitalize(),
                    "qty": qty,
                    "current_charge": current_charge,
                    "recommended_price": ALT_MILK_RECOMMENDED_PRICE,
                    "revenue_gap": revenue_gap,
                })

        # ── Modifier attach rate ──
        # Overall: orders with at least one modifier / total orders
        unique_orders_with_mods = mods['Order Id'].nunique()
        # We need total orders from item details — approximate from Order Id in mods
        # (this is a reasonable approximation; the caller can also cross-reference)
        all_order_ids_in_mods = mods['Order Id'].nunique()
        modifier_attach_rate_overall = 0

        # By channel
        modifier_attach_by_channel = {}
        if 'Dining Option' in mods.columns:
            for ch_val in mods['Dining Option'].dropna().unique():
                ch_mods = mods[mods['Dining Option'] == ch_val]
                ch_orders_with_mods = ch_mods['Order Id'].nunique()
                channel_name = _get_channel(ch_val)
                modifier_attach_by_channel[channel_name] = ch_orders_with_mods

        modifier_attach_rate = {
            "overall": modifier_attach_rate_overall,
            "orders_with_modifiers": unique_orders_with_mods,
            "by_channel": modifier_attach_by_channel,
        }

        # ── Free vs paid ──
        free_mods = mods[mods['Net Price'] == 0]
        paid_mods_all = mods[mods['Net Price'] > 0]
        free_count = int(free_mods['Qty'].sum())
        paid_count = int(paid_mods_all['Qty'].sum())
        paid_revenue = round(paid_mods_all['Net Price'].sum(), 2)
        free_vs_paid = {
            "free_count": free_count,
            "paid_count": paid_count,
            "paid_revenue": paid_revenue,
        }

        # ── Build per-item order counts (needed by multiple sections below) ──
        item_order_counts = {}
        if item_selection_details is not None and len(item_selection_details) > 0:
            _isd_counts = item_selection_details.copy()
            if 'Voided' in _isd_counts.columns:
                _isd_counts = _isd_counts[_isd_counts['Voided'] != True]
            if 'Menu Item' in _isd_counts.columns:
                _isd_counts['Qty'] = pd.to_numeric(_isd_counts.get('Qty', pd.Series(dtype='float64')), errors='coerce').fillna(1)
                item_order_counts = _isd_counts.groupby('Menu Item')['Qty'].sum().to_dict()

        # ── Per-item modifier breakdown (ALL items × ALL modifiers, split removals vs add-ons) ──
        # Build item → menu_group lookup from ItemSelectionDetails
        _MENU_GROUP_NORMALIZE = {
            "panini wraps": "Wraps",
            "panini wraps  + side of plantain chips": "Wraps",
            "salads + bowls": "Salads & Bowls",
            "salads & bowls": "Salads & Bowls",
            "smoothies": "Smoothies",
            "healthy smoothies (18 oz)": "Smoothies",
            "fresh pressed juice": "Juices",
            "freshly pressed juice": "Juices",
            "all snacks": "Snacks",
            "snacks": "Snacks",
            "house beverages": "Beverages",
            "iced matcha": "Beverages",
            "grab & go drinks": "Beverages",
            "drinks": "Beverages",
            "hot teas": "Beverages",
            "soup": "Soup",
            "soups": "Soup",
        }
        item_menu_group = {}
        if item_selection_details is not None and 'Menu Group' in item_selection_details.columns:
            isd = item_selection_details.copy()
            if 'Voided' in isd.columns:
                isd = isd[isd['Voided'] != True]
            for _, row in isd[['Menu Item', 'Menu Group']].drop_duplicates().iterrows():
                raw_group = str(row.get('Menu Group', '')).strip()
                normalized = _MENU_GROUP_NORMALIZE.get(raw_group.lower(), raw_group)
                item_menu_group[str(row['Menu Item']).strip()] = normalized

        # Modifier-level skip patterns (substring match, case-insensitive)
        _MODIFIER_SKIP_PATTERNS = [
            "allergy", "allergies", "please label", "label for",
            "on the side please", "on side please",
        ]

        def _should_skip_modifier(mod_name):
            """Return True if this modifier is a note/instruction, not a real customization."""
            if not isinstance(mod_name, str):
                return False
            low = mod_name.lower().strip()
            return any(pat in low for pat in _MODIFIER_SKIP_PATTERNS)

        # Classify option groups: base_selection, addon, removal, or skip
        _OG_CATEGORY = {
            # Base selections (built-in choices)
            "wraps": "base", "grain option": "base", "lettuce choice": "base",
            "milk selection": "base", "size": "base", "substitute smoothie base": "base",
            "chip flavor": "base", "flavor": "base", "strength": "base",
            "spindrift": "base", "dirty potato chips": "base",
            "make your own juice": "base", "smoothie add-ons make own": "base",
            # Add-ons
            "salad add ons": "addon", "salad add-ons": "addon",
            "wrap add-ons": "addon", "smoothie add-ons": "addon",
            "juice add ons!": "addon", "extra sauce/dressing": "addon",
            "smoothie protein": "addon", "soup add ons": "addon",
            "todays flavor(s)": "addon",
            # Removals
            "remove": "removal", "remove avocado": "removal",
            "remove blue cheese": "removal", "remove chicken": "removal",
            "remove tofu": "removal",
            # Skip (not relevant modifications)
            "allergies": "skip",
            "allergies (we cannot accomodate peanut and tree nut allergies for smoothies)": "skip",
            "dressing on the side": "skip", "spoon": "skip",
            "unsweetened": "skip", "make it on a wrap": "skip",
            "house made flavorings": "skip",
            "backup printer": "skip", "no print": "skip",
        }

        item_modifier_pairs = []
        if 'Parent Menu Selection' in mods.columns:
            all_parents = mods.groupby('Parent Menu Selection')['Qty'].sum().sort_values(ascending=False).index
            for parent_item in all_parents:
                item_name = str(parent_item).strip()
                if not item_name or item_name == 'nan':
                    continue
                item_mods = mods[mods['Parent Menu Selection'] == parent_item]
                item_ordered = int(item_order_counts.get(item_name, 0))
                menu_group = item_menu_group.get(item_name, "Other")

                # Classify each modifier row
                og_lower = item_mods['Option Group Name'].astype(str).str.lower().str.strip()
                cat_series = og_lower.map(lambda x: _OG_CATEGORY.get(x, "addon"))
                base_mods = item_mods[cat_series == "base"]
                addon_mods = item_mods[cat_series == "addon"]
                removal_mods = item_mods[cat_series == "removal"]
                # Skip rows where cat == "skip"

                def _build_mod_list(subset):
                    if len(subset) == 0:
                        return []
                    agg = subset.groupby('Modifier').agg(
                        qty=('Qty', 'sum'), revenue=('Net Price', 'sum'),
                    ).reset_index().sort_values('qty', ascending=False)
                    result = []
                    for _, row in agg.iterrows():
                        if _should_skip_modifier(row['Modifier']):
                            continue
                        entry = {"modifier": row['Modifier'], "qty": int(row['qty']), "revenue": round(row['revenue'], 2)}
                        if item_ordered > 0:
                            entry["pct_of_orders"] = round(_safe_div(int(row['qty']), item_ordered) * 100, 1)
                        result.append(entry)
                    return result

                bases = _build_mod_list(base_mods)
                addons = _build_mod_list(addon_mods)
                removals = _build_mod_list(removal_mods)

                if not bases and not addons and not removals:
                    continue

                item_modifier_pairs.append({
                    "item": item_name,
                    "item_ordered": item_ordered,
                    "menu_group": menu_group,
                    "bases": bases,
                    "addons": addons,
                    "removals": removals,
                })

        # ── Free modification breakdown by option group ──
        free_modification_breakdown = []
        if len(free_mods) > 0 and 'Option Group Name' in free_mods.columns:
            for og, grp in free_mods.groupby('Option Group Name'):
                if pd.isna(og) or str(og).strip() == '':
                    continue
                mod_counts = grp.groupby('Modifier')['Qty'].sum().sort_values(ascending=False)
                total_in_group = int(mod_counts.sum())
                choices = []
                for m, q in mod_counts.items():
                    choices.append({
                        "modifier": str(m),
                        "qty": int(q),
                        "pct": round(_safe_div(int(q), total_in_group) * 100, 1),
                    })
                free_modification_breakdown.append({
                    "option_group": str(og),
                    "total": total_in_group,
                    "choices": choices,
                })
            free_modification_breakdown.sort(key=lambda x: x['total'], reverse=True)

        # ── Bottom paid modifiers (lowest revenue — candidates for removal/repricing) ──
        bottom_paid_modifiers = []
        if len(paid_mods_all) > 0:
            bottom_agg = paid_mods_all.groupby('Modifier').agg(
                qty=('Qty', 'sum'),
                revenue=('Net Price', 'sum'),
            ).reset_index().sort_values('revenue', ascending=True).head(10)
            bottom_paid_modifiers = [
                {"modifier": row['Modifier'], "qty": int(row['qty']), "revenue": round(row['revenue'], 2)}
                for _, row in bottom_agg.iterrows()
            ]

        # ── Categorized modifiers by Option Group ──
        # Normalize option group names to merge duplicates
        _OG_NORMALIZE = {
            'salad add ons': 'Salad Add-Ons',
            'salad add-ons': 'Salad Add-Ons',
            'smoothie add-ons': 'Smoothie Add-Ons',
            'smoothie add-ons make own': 'Smoothie Add-Ons',
            'soup add ons': 'Soup Add-Ons',
            'soup add-ons': 'Soup Add-Ons',
            'wrap add-ons': 'Wrap Add-Ons',
            'wrap add ons': 'Wrap Add-Ons',
            'juice add ons!': 'Juice Add-Ons',
            'juice add-ons': 'Juice Add-Ons',
        }
        if 'Option Group Name' in mods.columns:
            mods['Option Group Name'] = mods['Option Group Name'].apply(
                lambda x: _OG_NORMALIZE.get(str(x).strip().lower(), str(x).strip()) if pd.notna(x) else x
            )

        modifier_categories = []
        if 'Option Group Name' in mods.columns:
            for og, grp in mods.groupby('Option Group Name'):
                if pd.isna(og) or str(og).strip() == '':
                    continue
                # Skip non-relevant option groups
                og_low = str(og).strip().lower()
                if _OG_CATEGORY.get(og_low) == 'skip':
                    continue
                og_paid = grp[grp['Net Price'] > 0]
                og_free = grp[grp['Net Price'] == 0]
                paid_list = []
                if len(og_paid) > 0:
                    pa = og_paid.groupby('Modifier').agg(
                        qty=('Qty', 'sum'), revenue=('Net Price', 'sum')
                    ).reset_index().sort_values('revenue', ascending=False)
                    paid_list = [
                        {"modifier": r['Modifier'], "qty": int(r['qty']),
                         "revenue": round(r['revenue'], 2)}
                        for _, r in pa.iterrows()
                    ]
                free_list = []
                if len(og_free) > 0:
                    fa = og_free.groupby('Modifier')['Qty'].sum().reset_index().sort_values('Qty', ascending=False)
                    free_list = [
                        {"modifier": r['Modifier'], "qty": int(r['Qty'])}
                        for _, r in fa.iterrows()
                    ]
                modifier_categories.append({
                    "category": str(og),
                    "paid_modifiers": paid_list,
                    "free_modifiers": free_list,
                    "total_revenue": round(grp['Net Price'].sum(), 2),
                    "total_qty": int(grp['Qty'].sum()),
                })
            modifier_categories.sort(key=lambda x: x['total_revenue'], reverse=True)

        # ── Base selections (wrap types + smoothie bases + milk selections) ──
        base_selections = {}  # category → [{type, qty, pct}]
        if 'Option Group Name' in mods.columns:
            # Wrap type (White / Wheat / GF only — filter out add-ons)
            _bread_types = {'white', 'wheat', 'gluten free', 'white wraps', 'wheat wraps'}
            _bread_normalize = {'white wraps': 'White', 'wheat wraps': 'Wheat'}
            wraps_og = mods[mods['Option Group Name'].astype(str).str.lower().str.contains('wraps|select wrap', na=False)]
            if len(wraps_og) > 0:
                bread_only = wraps_og[wraps_og['Modifier'].astype(str).str.strip().str.lower().isin(_bread_types)].copy()
                if len(bread_only) > 0:
                    # Normalize "White Wraps" → "White", "Wheat Wraps" → "Wheat"
                    bread_only['_bt_norm'] = bread_only['Modifier'].astype(str).str.strip().str.lower().map(
                        lambda x: _bread_normalize.get(x, x.title())
                    )
                    bt_agg = bread_only.groupby('_bt_norm')['Qty'].sum().sort_values(ascending=False)
                    bt_total = int(bt_agg.sum())
                    base_selections['Wrap Type'] = [
                        {"type": str(m), "qty": int(q), "pct": round(_safe_div(int(q), bt_total) * 100, 1)}
                        for m, q in bt_agg.items()
                    ]

            # Smoothie bases (include default Oat Milk for orders with no explicit selection)
            smoothie_base = mods[mods['Option Group Name'].astype(str).str.lower().str.contains('substitute smoothie base', na=False)]
            # Count total smoothie orders from item data to find defaults
            default_oat_count = 0
            if item_selection_details is not None and 'Menu Group' in item_selection_details.columns:
                isd = item_selection_details.copy()
                if 'Voided' in isd.columns:
                    isd = isd[isd['Voided'] != True]
                smoothie_items = isd[isd['Menu Group'].astype(str).str.lower().str.contains('smoothie', na=False)]
                if len(smoothie_items) > 0:
                    total_smoothie_orders = len(smoothie_items)
                    explicit_base_order_ids = set(smoothie_base['Order Id'].unique()) if len(smoothie_base) > 0 else set()
                    smoothie_order_ids = set(smoothie_items['Order Id'].unique())
                    default_orders = smoothie_order_ids - explicit_base_order_ids
                    default_oat_count = len(default_orders)

            if len(smoothie_base) > 0 or default_oat_count > 0:
                sb_dict = {}
                if len(smoothie_base) > 0:
                    for m, q in smoothie_base.groupby('Modifier')['Qty'].sum().items():
                        sb_dict[str(m)] = int(q)
                if default_oat_count > 0:
                    sb_dict['Oat Milk (default)'] = sb_dict.get('Oat Milk (default)', 0) + default_oat_count
                    # Merge explicit Oat Milk selections into the default count
                    if 'Oat Milk' in sb_dict:
                        sb_dict['Oat Milk (default)'] += sb_dict.pop('Oat Milk')
                sb_total = sum(sb_dict.values())
                sb_sorted = sorted(sb_dict.items(), key=lambda x: x[1], reverse=True)
                base_selections['Smoothie Base'] = [
                    {"type": k, "qty": v, "pct": round(_safe_div(v, sb_total) * 100, 1)}
                    for k, v in sb_sorted
                ]

            # Milk selection (matcha, coffee, etc.)
            milk_sel = mods[mods['Option Group Name'].astype(str).str.lower().str.contains('milk selection|splash of milk', na=False)]
            if len(milk_sel) > 0:
                ms_agg = milk_sel.groupby('Modifier')['Qty'].sum().sort_values(ascending=False)
                ms_total = int(ms_agg.sum())
                base_selections['Milk Selection'] = [
                    {"type": str(m), "qty": int(q), "pct": round(_safe_div(int(q), ms_total) * 100, 1)}
                    for m, q in ms_agg.items()
                ]

            # Grain option (merge "Farro" with "Organic Farro")
            grain_sel = mods[mods['Option Group Name'].astype(str).str.lower().str.contains('grain option', na=False)].copy()
            if len(grain_sel) > 0:
                _grain_norm = {'farro': 'Farro', 'organic farro': 'Farro'}
                grain_sel['_grain'] = grain_sel['Modifier'].astype(str).str.strip().str.lower().map(
                    lambda x: _grain_norm.get(x, x.title())
                )
                gr_agg = grain_sel.groupby('_grain')['Qty'].sum().sort_values(ascending=False)
                gr_total = int(gr_agg.sum())
                base_selections['Grain Option'] = [
                    {"type": str(m), "qty": int(q), "pct": round(_safe_div(int(q), gr_total) * 100, 1)}
                    for m, q in gr_agg.items()
                ]

            # Lettuce choice
            lettuce_sel = mods[mods['Option Group Name'].astype(str).str.lower().str.contains('lettuce choice', na=False)]
            if len(lettuce_sel) > 0:
                lc_agg = lettuce_sel.groupby('Modifier')['Qty'].sum().sort_values(ascending=False)
                lc_total = int(lc_agg.sum())
                base_selections['Lettuce Choice'] = [
                    {"type": str(m), "qty": int(q), "pct": round(_safe_div(int(q), lc_total) * 100, 1)}
                    for m, q in lc_agg.items()
                ]

        # Keep wrap_type_freq for backwards compat
        wrap_type_freq = base_selections.get('Wrap Type', [])

        # ── Removal analysis (what items have things removed + what gets removed) ──
        # Normalisation map: merge similar removal modifiers into canonical names
        _REMOVAL_MERGE = {
            "no red onion": "No Onion", "no red onions": "No Onion",
            "no onion": "No Onion", "no onions": "No Onion",
            "no tomato": "No Tomato", "no tomatoes": "No Tomato", "no cherry tomatoes": "No Tomato",
            "no feta cheese": "No Feta", "no feta": "No Feta",
            "no cheese": "No Cheese",
            "no crouton": "No Croutons", "no croutons": "No Croutons",
            "no pickle": "No Pickles", "no pickles": "No Pickles",
            "no pepper": "No Peppers", "no peppers": "No Peppers", "no bell pepper": "No Peppers",
            "no cucumber": "No Cucumber", "no cucumbers": "No Cucumber",
            "no lettuce": "No Lettuce",
            "no bacon": "No Bacon",
            "no avocado": "No Avocado",
        }

        # item_order_counts already computed above

        removal_analysis = {}
        if 'Option Group Name' in mods.columns and 'Parent Menu Selection' in mods.columns:
            remove_mods = mods[mods['Option Group Name'].astype(str).str.lower().str.contains('remove', na=False)]
            if len(remove_mods) > 0:
                # By parent item: what gets removed from each item
                for item, grp in remove_mods.groupby('Parent Menu Selection'):
                    item_name = str(item).strip()
                    if not item_name or item_name == 'nan':
                        continue
                    grp = grp.copy()
                    grp['_norm_mod'] = grp['Modifier'].str.strip().str.lower().map(
                        lambda x: _REMOVAL_MERGE.get(x, x.title())
                    )
                    removals = grp.groupby('_norm_mod')['Qty'].sum().sort_values(ascending=False)
                    total_for_item = int(removals.sum())
                    # How many times was this item ordered total?
                    item_total_ordered = int(item_order_counts.get(item_name, 0))
                    removal_list = []
                    for m, q in removals.items():
                        qty = int(q)
                        entry = {
                            "removed": str(m),
                            "qty": qty,
                            "pct_of_removals": round(_safe_div(qty, total_for_item) * 100, 1),
                        }
                        if item_total_ordered > 0:
                            entry["pct_of_orders"] = round(_safe_div(qty, item_total_ordered) * 100, 1)
                            entry["item_total_ordered"] = item_total_ordered
                        removal_list.append(entry)
                    removal_analysis[item_name] = removal_list

        # Build removal summary: for each removed ingredient, compute total removals
        # and total applicable orders (orders of items that offer that removal option)
        removal_summary = []
        if removal_analysis:
            ingredient_totals = {}  # ingredient → {qty, applicable_orders}
            for item_name, removals in removal_analysis.items():
                item_ordered = int(item_order_counts.get(item_name, 0))
                for r in removals:
                    ing = r.get("removed", "")
                    if ing not in ingredient_totals:
                        ingredient_totals[ing] = {"qty": 0, "applicable_orders": 0}
                    ingredient_totals[ing]["qty"] += r.get("qty", 0)
                    ingredient_totals[ing]["applicable_orders"] += item_ordered
            for ing, data in sorted(ingredient_totals.items(), key=lambda x: x[1]["qty"], reverse=True)[:10]:
                pct = round(_safe_div(data["qty"], data["applicable_orders"]) * 100, 1) if data["applicable_orders"] > 0 else 0
                removal_summary.append({
                    "ingredient": ing,
                    "removed_qty": data["qty"],
                    "applicable_orders": data["applicable_orders"],
                    "removal_rate": pct,
                })

        # ── Best-selling dressings ──
        best_dressings = []
        dressing_keywords = ['dressing', 'sauce', 'vinaigrette']
        if 'Option Group Name' in mods.columns:
            dressing_groups = mods[
                mods['Option Group Name'].astype(str).str.lower().apply(
                    lambda x: any(kw in x for kw in dressing_keywords)
                )
            ]
            if len(dressing_groups) > 0:
                dr_agg = dressing_groups.groupby('Modifier').agg(
                    qty=('Qty', 'sum'), revenue=('Net Price', 'sum')
                ).reset_index().sort_values('qty', ascending=False).head(15)
                best_dressings = [
                    {"dressing": r['Modifier'], "qty": int(r['qty']),
                     "revenue": round(r['revenue'], 2)}
                    for _, r in dr_agg.iterrows()
                ]

        # ── Item × Modifier matrix (top 10 items × top 10 paid mods) ──
        item_mod_matrix = {}
        if 'Parent Menu Selection' in mods.columns and len(paid_mods_all) > 0:
            top_items_for_matrix = paid_mods_all.groupby(
                'Parent Menu Selection')['Qty'].sum().nlargest(10).index.tolist()
            top_mods_for_matrix = paid_mods_all.groupby(
                'Modifier')['Qty'].sum().nlargest(10).index.tolist()
            matrix_rows = []
            for item in top_items_for_matrix:
                row_data = {"item": str(item), "mods": {}}
                item_df = paid_mods_all[paid_mods_all['Parent Menu Selection'] == item]
                for mod in top_mods_for_matrix:
                    cnt = int(item_df[item_df['Modifier'] == mod]['Qty'].sum())
                    row_data["mods"][str(mod)] = cnt
                matrix_rows.append(row_data)
            item_mod_matrix = {
                "items": [str(i) for i in top_items_for_matrix],
                "modifiers": [str(m) for m in top_mods_for_matrix],
                "rows": matrix_rows,
            }

        # ── Dressing Prep Calculator ──
        # Only salads & bowls need portioned dressings; wraps use squeeze bottles.
        # Extra dressing add-ons from any item DO count (they need portioning).
        _ITEM_DEFAULT_DRESSING = {
            # Salads
            "apple walnut salad - vegetarian/gf": "Balsamic Vinaigrette",
            "berry berry avocado salad - vegetarian/gf": "Balsamic Vinaigrette",
            "buffalo chicken salad - gf": "Blue Cheese Dressing",
            "chipotle chicken salad": "Chipotle Vinaigrette",
            "livite house salad - gf": "Herb Vinaigrette",
            "plant-based thai peanut salad - gf": "Thai Peanut Dressing",
            # Bowls
            "the livite bowl": "Herb Vinaigrette",
            "southwest vegan bowl": "Herb Vinaigrette",
            "spicy peanut noodle bowl": "Thai Peanut Dressing",
        }
        # Map "Side XYZ" modifiers to canonical dressing names
        _EXTRA_DRESSING_MAP = {
            "side herb vinaigrette": "Herb Vinaigrette",
            "side balsamic vinaigrette": "Balsamic Vinaigrette",
            "side caesar dressing": "Caesar Dressing",
            "side thai peanut dressing": "Thai Peanut Dressing",
            "side blue cheese": "Blue Cheese Dressing",
            "side ranch": "Ranch",
            "side buffalo sauce": "Buffalo Sauce",
            "side chipotle mayo": "Chipotle Mayo",
            "side avocado cilantro sauce": "Avocado Cilantro Sauce",
            "side dijon mustard": "Dijon Mustard",
            "side jalapeno cilantro dressing": "Jalapeno Cilantro Dressing",
            "side pesto": "Pesto",
            "extra dressing": None,  # resolved by parent item below
        }
        dressing_prep = {}  # dressing_name → count

        # 1) Default dressings from salad/bowl orders
        for item_name_lc, dressing in _ITEM_DEFAULT_DRESSING.items():
            qty = int(item_order_counts.get(
                next((k for k in item_order_counts if k.lower() == item_name_lc), ""), 0))
            if qty > 0:
                dressing_prep[dressing] = dressing_prep.get(dressing, 0) + qty

        # 2) Subtract "remove dressing" / "no dressing" from salads/bowls
        if 'Option Group Name' in mods.columns and 'Parent Menu Selection' in mods.columns:
            remove_dress_mask = (
                mods['Option Group Name'].astype(str).str.lower().str.contains('remove', na=False)
                & mods['Modifier'].astype(str).str.lower().str.contains('dressing', na=False)
            )
            for _, row in mods[remove_dress_mask].iterrows():
                parent = str(row.get('Parent Menu Selection', '')).strip().lower()
                dressing = _ITEM_DEFAULT_DRESSING.get(parent)
                if dressing:
                    q = int(row.get('Qty', 1)) if pd.notna(row.get('Qty')) else 1
                    dressing_prep[dressing] = dressing_prep.get(dressing, 0) - q

            # Also subtract "Dressing on the Side" removals that explicitly remove dressing
            # Note: "Dressing on the Side" alone does NOT reduce count (same dressing, just cupped)

        # 3) Add extra dressing add-ons (from any item — these are portioned)
        if 'Option Group Name' in mods.columns:
            extra_mask = mods['Option Group Name'].astype(str).str.lower().str.contains(
                'extra sauce|extra dressing', na=False)
            for _, row in mods[extra_mask].iterrows():
                mod_name = str(row.get('Modifier', '')).strip().lower()
                q = int(row.get('Qty', 1)) if pd.notna(row.get('Qty')) else 1
                canonical = _EXTRA_DRESSING_MAP.get(mod_name)
                if canonical is None and mod_name == "extra dressing":
                    # Resolve from parent item's default dressing
                    parent = str(row.get('Parent Menu Selection', '')).strip().lower()
                    canonical = _ITEM_DEFAULT_DRESSING.get(parent, "Extra Dressing (unknown)")
                elif canonical is None:
                    canonical = str(row.get('Modifier', '')).strip()
                dressing_prep[canonical] = dressing_prep.get(canonical, 0) + q

        # 4) Handle "Remove Blue Cheese, Ranch Instead" type substitutions
        if 'Option Group Name' in mods.columns:
            sub_mask = mods['Modifier'].astype(str).str.lower().str.contains(
                'ranch instead|caesar instead|vinaigrette instead', na=False)
            for _, row in mods[sub_mask].iterrows():
                parent = str(row.get('Parent Menu Selection', '')).strip().lower()
                q = int(row.get('Qty', 1)) if pd.notna(row.get('Qty')) else 1
                orig_dressing = _ITEM_DEFAULT_DRESSING.get(parent)
                if orig_dressing:
                    dressing_prep[orig_dressing] = dressing_prep.get(orig_dressing, 0) - q
                mod_lower = str(row.get('Modifier', '')).lower()
                if 'ranch' in mod_lower:
                    dressing_prep["Ranch"] = dressing_prep.get("Ranch", 0) + q
                elif 'caesar' in mod_lower:
                    dressing_prep["Caesar Dressing"] = dressing_prep.get("Caesar Dressing", 0) + q

        # Build sorted result (remove zero/negative entries)
        dressing_prep_list = sorted(
            [{"dressing": k, "portions": max(v, 0)} for k, v in dressing_prep.items() if v > 0],
            key=lambda x: x["portions"], reverse=True
        )
        total_dressing_portions = sum(d["portions"] for d in dressing_prep_list)

        return {
            "total_modifier_revenue": total_modifier_revenue,
            "total_modifiers": total_modifiers,
            "top_paid_modifiers": top_paid_modifiers,
            "bottom_paid_modifiers": bottom_paid_modifiers,
            "option_group_analysis": option_group_analysis,
            "most_modified_items": most_modified_items,
            "item_modifier_pairs": item_modifier_pairs,
            "free_modification_breakdown": free_modification_breakdown,
            "modifier_categories": modifier_categories,
            "wrap_type_freq": wrap_type_freq,
            "base_selections": base_selections,
            "removal_analysis": removal_analysis,
            "removal_summary": removal_summary,
            "best_dressings": best_dressings,
            "item_mod_matrix": item_mod_matrix,
            "alt_milk": alt_milk,
            "modifier_attach_rate": modifier_attach_rate,
            "free_vs_paid": free_vs_paid,
            "dressing_prep": dressing_prep_list,
            "total_dressing_portions": total_dressing_portions,
        }
    except Exception as e:
        return {
            "total_modifier_revenue": 0, "total_modifiers": 0,
            "top_paid_modifiers": [], "bottom_paid_modifiers": [],
            "option_group_analysis": [],
            "most_modified_items": [], "item_modifier_pairs": [],
            "free_modification_breakdown": [],
            "alt_milk": [],
            "modifier_attach_rate": 0, "free_vs_paid": {},
            "dressing_prep": [], "total_dressing_portions": 0,
            "_error": str(e),
        }


# ═══════════════════════════════════════════════════════════════
#  8. KITCHEN METRICS
# ═══════════════════════════════════════════════════════════════
