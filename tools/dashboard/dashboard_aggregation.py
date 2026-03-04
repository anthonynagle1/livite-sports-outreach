"""
Multi-day aggregation engine for Livite dashboards.

Takes a list of daily metrics dicts (from compute_all_metrics)
and produces a single aggregated dict with the same shape.

Aggregation rules:
    - SUM: revenue, orders, labor costs/hours, payment amounts
    - AVERAGE: kitchen timings (medians, percentiles)
    - MERGE+RERANK: top items, combos, modifiers, customers
    - DERIVE: avg_check, labor_%, rev_per_labor_hr (from aggregated values)
"""

from collections import defaultdict


def _safe_div(a, b):
    return round(a / b, 2) if b else 0


def _to_float(val, default=0):
    """Safely convert a value to float, returning default on failure."""
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _sum_field(dicts, field, default=0):
    """Sum a field across a list of dicts, ignoring None."""
    return sum(d.get(field, default) for d in dicts if d is not None)


def _avg_field(dicts, field, default=0):
    """Average a field across a list of dicts, ignoring None."""
    vals = [d.get(field, default) for d in dicts if d is not None and d.get(field) is not None]
    return round(sum(vals) / len(vals), 2) if vals else default


def _agg_food_cost(days, toast_total):
    """Sum food_cost from individual days (each may be theoretical or estimate)."""
    total = sum(d.get("food_cost", 0) or 0 for d in days)
    if total > 0:
        return round(total, 2)
    # Fallback: flat 35% if no per-day food cost was computed
    return round(toast_total * 0.35, 2)


def _agg_food_cost_method(days):
    """Determine aggregate food cost method label."""
    methods = [d.get("food_cost_method", "estimate") for d in days]
    if all(m == "theoretical" for m in methods):
        return "theoretical"
    if any(m == "theoretical" for m in methods):
        return "mixed"
    return "estimate"


def _agg_food_cost_coverage(days):
    """Average food cost coverage across days that have theoretical costing."""
    coverages = [
        d.get("food_cost_coverage", 0) or 0
        for d in days if d.get("food_cost_method") == "theoretical"
    ]
    if not coverages:
        return 0
    return round(sum(coverages) / len(coverages), 1)


# ──────────────────────────────────────────────
# Revenue aggregation
# ──────────────────────────────────────────────

def _aggregate_revenue(days):
    """Aggregate revenue dicts across multiple days."""
    revs = [d.get('revenue') for d in days if d.get('revenue')]
    if not revs:
        return None

    toast_total = _sum_field(revs, 'toast_total')
    gross_total = _sum_field(revs, 'gross_total')
    total_orders = _sum_field(revs, 'total_orders')
    total_guests = _sum_field(revs, 'total_guests')
    total_tax = _sum_field(revs, 'total_tax')
    total_tips = _sum_field(revs, 'total_tips')
    total_gratuity = _sum_field(revs, 'total_gratuity')
    total_discounts = _sum_field(revs, 'total_discounts')

    # Channels: merge by name, sum revenue/orders
    channels = defaultdict(lambda: {'revenue': 0, 'orders': 0})
    for r in revs:
        for ch_name, ch_data in r.get('channels', {}).items():
            channels[ch_name]['revenue'] += ch_data.get('revenue', 0)
            channels[ch_name]['orders'] += ch_data.get('orders', 0)
    channels = {
        k: {**v, 'avg_check': _safe_div(v['revenue'], v['orders']),
            'pct_share': _safe_div(v['revenue'], toast_total) * 100}
        for k, v in channels.items()
    }

    # Walk-in / 3P / Online split
    w3o = defaultdict(lambda: {'revenue': 0, 'orders': 0})
    for r in revs:
        for key, val in r.get('walkin_3p_online', {}).items():
            w3o[key]['revenue'] += val.get('revenue', 0)
            w3o[key]['orders'] += val.get('orders', 0)
    walkin_3p_online = {
        k: {**v, 'pct': _safe_div(v['revenue'], toast_total) * 100}
        for k, v in w3o.items()
    }

    # Hourly: merge by hour, sum revenue/orders
    hourly_map = defaultdict(lambda: {'revenue': 0, 'orders': 0})
    for r in revs:
        for h in r.get('hourly', []):
            hourly_map[h['hour']]['revenue'] += h.get('revenue', 0)
            hourly_map[h['hour']]['orders'] += h.get('orders', 0)
    hourly = sorted([
        {'hour': h, 'revenue': round(v['revenue'], 2), 'orders': v['orders'],
         'avg_check': _safe_div(v['revenue'], v['orders'])}
        for h, v in hourly_map.items()
    ], key=lambda x: x['hour'])

    peak = max(hourly, key=lambda x: x['revenue']) if hourly else {'hour': 0, 'revenue': 0, 'orders': 0}

    # Quarter-hourly: merge by label, sum revenue/orders
    qh_map = defaultdict(lambda: {'revenue': 0, 'orders': 0, 'large_orders': []})
    for r in revs:
        for q in r.get('quarter_hourly', []):
            label = q.get('label', '')
            qh_map[label]['revenue'] += q.get('revenue', 0)
            qh_map[label]['orders'] += q.get('orders', 0)
            qh_map[label]['large_orders'].extend(q.get('large_orders', []))
            if 'hour' not in qh_map[label]:
                qh_map[label]['hour'] = q.get('hour', 0)
                qh_map[label]['quarter'] = q.get('quarter', 0)
    quarter_hourly = sorted([
        {'hour': v.get('hour', 0), 'quarter': v.get('quarter', 0), 'label': k,
         'revenue': round(v['revenue'], 2), 'orders': v['orders'],
         'large_orders': v['large_orders']}
        for k, v in qh_map.items()
    ], key=lambda x: (x['hour'], x['quarter']))
    peak_q = max(quarter_hourly, key=lambda x: x['revenue']) if quarter_hourly else {'label': '', 'revenue': 0, 'orders': 0}

    # Hourly by channel: merge (values are dicts with revenue/orders sub-fields)
    hbc_ch = defaultdict(lambda: defaultdict(lambda: {'revenue': 0, 'orders': 0}))
    hbc_gr = defaultdict(lambda: defaultdict(lambda: {'revenue': 0, 'orders': 0}))
    for r in revs:
        for h in r.get('hourly_by_channel', []):
            hr = h.get('hour', 0)
            for ch, val in h.get('channels', {}).items():
                if isinstance(val, dict):
                    hbc_ch[hr][ch]['revenue'] += val.get('revenue', 0)
                    hbc_ch[hr][ch]['orders'] += val.get('orders', 0)
                else:
                    hbc_ch[hr][ch]['revenue'] += val
            for g, val in h.get('groups', {}).items():
                if isinstance(val, dict):
                    hbc_gr[hr][g]['revenue'] += val.get('revenue', 0)
                    hbc_gr[hr][g]['orders'] += val.get('orders', 0)
                else:
                    hbc_gr[hr][g]['revenue'] += val
    all_hours = set(hbc_ch.keys()) | set(hbc_gr.keys())
    hourly_by_channel = sorted([
        {'hour': h, 'channels': dict(hbc_ch[h]), 'groups': dict(hbc_gr[h])}
        for h in all_hours
    ], key=lambda x: x['hour'])

    # Service/source splits
    service_split = defaultdict(lambda: {'revenue': 0, 'orders': 0})
    for r in revs:
        for k, v in r.get('service_split', {}).items():
            service_split[k]['revenue'] += v.get('revenue', 0)
            service_split[k]['orders'] += v.get('orders', 0)

    source_split = defaultdict(lambda: {'revenue': 0, 'orders': 0})
    for r in revs:
        for k, v in r.get('source_split', {}).items():
            source_split[k]['revenue'] += v.get('revenue', 0)
            source_split[k]['orders'] += v.get('orders', 0)

    # Revenue center
    revenue_center = defaultdict(lambda: {'revenue': 0, 'orders': 0})
    for r in revs:
        for k, v in r.get('revenue_center', {}).items():
            revenue_center[k]['revenue'] += v.get('revenue', 0)
            revenue_center[k]['orders'] += v.get('orders', 0)

    # Duration by channel: average across days
    dur_map = defaultdict(list)
    for r in revs:
        for ch, val in r.get('duration_by_channel', {}).items():
            if val:
                dur_map[ch].append(val)
    duration_by_channel = {k: round(sum(v) / len(v), 1) for k, v in dur_map.items() if v}

    # Server performance: merge by server
    server_map = defaultdict(lambda: {'revenue': 0, 'orders': 0})
    for r in revs:
        for s in r.get('server_performance', []):
            name = s.get('server', '')
            server_map[name]['revenue'] += s.get('revenue', 0)
            server_map[name]['orders'] += s.get('orders', 0)
    server_performance = sorted([
        {'server': k, 'revenue': round(v['revenue'], 2), 'orders': v['orders'],
         'avg_check': _safe_div(v['revenue'], v['orders'])}
        for k, v in server_map.items()
    ], key=lambda x: x['revenue'], reverse=True)

    # 4WRA: average across days (each day already has averaged values)
    qh_4wra = {}
    qh_4wra_weeks = 0
    for r in revs:
        if r.get('quarter_hourly_4wra'):
            qh_4wra = r['quarter_hourly_4wra']
            qh_4wra_weeks = r.get('quarter_hourly_4wra_weeks', 0)
            break

    result = {
        'toast_total': round(toast_total, 2),
        'gross_total': round(gross_total, 2),
        'total_orders': total_orders,
        'avg_check': _safe_div(toast_total, total_orders),
        'total_guests': total_guests,
        'rev_per_guest': _safe_div(toast_total, total_guests),
        'channels': dict(channels),
        'walkin_3p_online': dict(walkin_3p_online),
        'service_split': dict(service_split),
        'source_split': dict(source_split),
        'hourly': hourly,
        'peak_hour': peak,
        'quarter_hourly': quarter_hourly,
        'peak_quarter': peak_q,
        'hourly_by_channel': hourly_by_channel,
        'duration_by_channel': duration_by_channel,
        'server_performance': server_performance,
        'revenue_center': dict(revenue_center),
        'total_tax': round(total_tax, 2),
        'total_tips': round(total_tips, 2),
        'total_gratuity': round(total_gratuity, 2),
        'total_discounts': round(total_discounts, 2),
    }
    if qh_4wra:
        result['quarter_hourly_4wra'] = qh_4wra
        result['quarter_hourly_4wra_weeks'] = qh_4wra_weeks

    return result


# ──────────────────────────────────────────────
# Orders aggregation
# ──────────────────────────────────────────────

def _merge_ranked_list(days, section, field, sum_fields, rank_by):
    """Merge a list of dicts from multiple days, summing numeric fields, re-ranking."""
    merged = defaultdict(lambda: {f: 0 for f in sum_fields})
    for d in days:
        sec = d.get(section)
        if not sec:
            continue
        for item in sec.get(field, []):
            key = item.get('item') or item.get('modifier') or item.get('group') or item.get('reason') or item.get('server') or ''
            for f in sum_fields:
                merged[key][f] += item.get(f, 0)
    result = []
    for key, vals in merged.items():
        entry = {'item': key, **vals}
        # Derive avg_price if we have revenue and qty
        if 'revenue' in vals and 'qty' in vals and vals['qty'] > 0:
            entry['avg_price'] = round(vals['revenue'] / vals['qty'], 2)
        result.append(entry)
    return sorted(result, key=lambda x: x.get(rank_by, 0), reverse=True)


def _aggregate_orders(days):
    """Aggregate orders dicts across multiple days."""
    ords = [d.get('orders') for d in days if d.get('orders')]
    if not ords:
        return None

    total_items_sold = _sum_field(ords, 'total_items_sold')
    total_orders = sum(d.get('revenue', {}).get('total_orders', 0)
                       for d in days if d.get('revenue'))

    top_by_qty = _merge_ranked_list(days, 'orders', 'top_items_by_qty',
                                     ['qty', 'revenue'], 'qty')
    top_by_rev = _merge_ranked_list(days, 'orders', 'top_items_by_revenue',
                                     ['revenue', 'qty'], 'revenue')

    # First/third party items
    fp = _merge_ranked_list(days, 'orders', 'first_party_items',
                            ['qty', 'revenue'], 'revenue')
    tp = _merge_ranked_list(days, 'orders', 'third_party_items',
                            ['qty', 'revenue'], 'revenue')

    # Menu group mix: merge by group
    group_map = defaultdict(lambda: {'revenue': 0, 'qty': 0})
    for o in ords:
        for g in o.get('menu_group_mix', []):
            group_map[g.get('group', '')]['revenue'] += g.get('revenue', 0)
            group_map[g.get('group', '')]['qty'] += g.get('qty', 0)
    total_rev = sum(v['revenue'] for v in group_map.values())
    menu_group_mix = sorted([
        {'group': k, 'revenue': round(v['revenue'], 2), 'qty': v['qty'],
         'pct_revenue': _safe_div(v['revenue'], total_rev) * 100}
        for k, v in group_map.items()
    ], key=lambda x: x['revenue'], reverse=True)

    # Discount by channel — metrics produces: channel, gross, discount, net, disc_rate
    disc_ch = defaultdict(lambda: {'gross': 0, 'discount': 0, 'net': 0})
    for o in ords:
        for dc in o.get('discount_by_channel', []):
            ch = dc.get('channel', '')
            disc_ch[ch]['gross'] += dc.get('gross', 0)
            disc_ch[ch]['discount'] += dc.get('discount', 0)
            disc_ch[ch]['net'] += dc.get('net', 0)
    discount_by_channel = sorted([
        {'channel': k, 'gross': round(v['gross'], 2),
         'discount': round(v['discount'], 2), 'net': round(v['net'], 2),
         'disc_rate': round(_safe_div(v['discount'], v['gross']) * 100, 1) if v['gross'] else 0}
        for k, v in disc_ch.items()
    ], key=lambda x: x['discount'], reverse=True)

    # Discount by reason — metrics produces: reason, count, total_discount
    disc_reason = defaultdict(lambda: {'total_discount': 0, 'count': 0})
    for o in ords:
        for dr in o.get('discount_by_reason', []):
            reason = dr.get('reason', '')
            disc_reason[reason]['total_discount'] += dr.get('total_discount', 0)
            disc_reason[reason]['count'] += dr.get('count', 0)
    discount_by_reason = sorted([
        {'reason': k, 'total_discount': round(v['total_discount'], 2),
         'count': v['count']}
        for k, v in disc_reason.items()
    ], key=lambda x: x['total_discount'], reverse=True)

    # Uber BOGO impact: merge dicts, summing numeric fields
    uber_bogo = defaultdict(float)
    for o in ords:
        ub = o.get('uber_bogo_impact', {})
        if isinstance(ub, dict):
            for k, v in ub.items():
                if isinstance(v, (int, float)):
                    uber_bogo[k] += v
    uber_bogo_impact = dict(uber_bogo) if uber_bogo else {}

    # Void analysis
    void_items = sum(o.get('void_analysis', {}).get('total_voided_items', 0) for o in ords)
    void_rev = sum(o.get('void_analysis', {}).get('total_voided_revenue', 0) for o in ords)

    return {
        'total_items_sold': total_items_sold,
        'avg_items_per_order': _safe_div(total_items_sold, total_orders),
        'top_items_by_qty': top_by_qty[:20],
        'top_items_by_revenue': top_by_rev[:20],
        'first_party_items': fp[:20],
        'third_party_items': tp[:20],
        'menu_group_mix': menu_group_mix,
        'discount_by_channel': discount_by_channel,
        'discount_by_reason': discount_by_reason,
        'uber_bogo_impact': uber_bogo_impact,
        'void_analysis': {
            'total_voided_items': void_items,
            'total_voided_revenue': round(void_rev, 2),
            'pct_of_total': 0,
        },
        'avg_selling_price': 0,
        'single_item_rate': _avg_field(ords, 'single_item_rate'),
        'single_item_by_channel': {},
        'items_by_menu_breakdown': [],
        'menu_mix': [],
        'category_mix': [],
        'catering': {'revenue': 0, 'qty': 0, 'orders': 0, 'top_items': [], 'pct_of_total': 0},
    }


# ──────────────────────────────────────────────
# Baskets aggregation
# ──────────────────────────────────────────────

def _aggregate_baskets(days):
    """Aggregate basket/combo data across multiple days."""
    basks = [d.get('baskets') for d in days if d.get('baskets')]
    if not basks:
        return None

    # Combos: merge pair frequencies
    combo_map = defaultdict(int)
    combo_nb_map = defaultdict(int)
    for b in basks:
        for c in b.get('top_combos', []):
            key = (c.get('item1', ''), c.get('item2', ''))
            combo_map[key] += c.get('frequency', 0)
        for c in b.get('top_combos_no_bogo', []):
            key = (c.get('item1', ''), c.get('item2', ''))
            combo_nb_map[key] += c.get('frequency', 0)

    top_combos = sorted([
        {'item1': k[0], 'item2': k[1], 'frequency': v}
        for k, v in combo_map.items()
    ], key=lambda x: x['frequency'], reverse=True)[:15]

    top_combos_no_bogo = sorted([
        {'item1': k[0], 'item2': k[1], 'frequency': v}
        for k, v in combo_nb_map.items()
    ], key=lambda x: x['frequency'], reverse=True)[:15]

    # Attach rates: average across days
    wrap_attach = defaultdict(list)
    for b in basks:
        for k, v in b.get('wrap_attach', {}).items():
            if isinstance(v, (int, float)):
                wrap_attach[k].append(v)
    wrap_attach = {k: round(sum(v) / len(v), 1) for k, v in wrap_attach.items() if v}

    # Basket size
    basket_vals = []
    basket_by_ch = defaultdict(list)
    for b in basks:
        bs = b.get('avg_basket_size', 0)
        if isinstance(bs, dict):
            if bs.get('overall'):
                basket_vals.append(bs['overall'])
            for ch, val in bs.get('by_channel', {}).items():
                if isinstance(val, (int, float)):
                    basket_by_ch[ch].append(val)
        elif isinstance(bs, (int, float)):
            basket_vals.append(bs)

    avg_basket = round(sum(basket_vals) / len(basket_vals), 2) if basket_vals else 0
    basket_by_channel = {k: round(sum(v) / len(v), 2) for k, v in basket_by_ch.items() if v}

    # Basket revenue
    rev_vals = []
    rev_by_ch = defaultdict(list)
    for b in basks:
        br = b.get('avg_basket_revenue', 0)
        if isinstance(br, dict):
            if br.get('overall'):
                rev_vals.append(br['overall'])
            for ch, val in br.get('by_channel', {}).items():
                if isinstance(val, (int, float)):
                    rev_by_ch[ch].append(val)
        elif isinstance(br, (int, float)):
            rev_vals.append(br)

    avg_rev = round(sum(rev_vals) / len(rev_vals), 2) if rev_vals else 0
    rev_by_channel = {k: round(sum(v) / len(v), 2) for k, v in rev_by_ch.items() if v}

    return {
        'top_combos': top_combos,
        'top_combos_no_bogo': top_combos_no_bogo,
        'uber_bogo_orders_excluded': sum(b.get('uber_bogo_orders_excluded', 0) for b in basks),
        'wrap_attach': wrap_attach,
        'smoothie_attach': {},
        'salad_attach': {},
        'snack_attach_rate': {},
        'avg_basket_size': {'overall': avg_basket, 'by_channel': basket_by_channel},
        'avg_basket_revenue': {'overall': avg_rev, 'by_channel': rev_by_channel},
        'uber_vs_walkin': {},
    }


# ──────────────────────────────────────────────
# Modifiers aggregation
# ──────────────────────────────────────────────

def _aggregate_modifiers(days):
    """Aggregate modifier data across multiple days."""
    mods = [d.get('modifiers') for d in days if d.get('modifiers')]
    if not mods:
        return None

    total_modifier_revenue = _sum_field(mods, 'total_modifier_revenue')
    total_modifiers = _sum_field(mods, 'total_modifiers')
    total_dressing_portions = _sum_field(mods, 'total_dressing_portions')

    # Top paid modifiers: merge
    paid_map = defaultdict(lambda: {'qty': 0, 'revenue': 0})
    for m in mods:
        for p in m.get('top_paid_modifiers', []):
            name = p.get('modifier', '')
            paid_map[name]['qty'] += p.get('qty', 0)
            paid_map[name]['revenue'] += p.get('revenue', 0)
    top_paid = sorted([
        {'modifier': k, 'qty': v['qty'], 'revenue': round(v['revenue'], 2)}
        for k, v in paid_map.items()
    ], key=lambda x: x['revenue'], reverse=True)[:15]

    # Dressing prep: merge
    dress_map = defaultdict(int)
    for m in mods:
        for dp in m.get('dressing_prep', []):
            dress_map[dp.get('dressing', '')] += dp.get('portions', 0)
    dressing_prep = sorted([
        {'dressing': k, 'portions': v} for k, v in dress_map.items()
    ], key=lambda x: x['portions'], reverse=True)

    # Item modifier pairs: merge by item, sum counts
    imp_map = {}
    for m in mods:
        for imp in m.get('item_modifier_pairs', []):
            item = imp.get('item', '')
            if item not in imp_map:
                imp_map[item] = {
                    'item': item,
                    'item_ordered': 0,
                    'menu_group': imp.get('menu_group', 'Other'),
                    'bases': defaultdict(lambda: {'qty': 0, 'revenue': 0}),
                    'addons': defaultdict(lambda: {'qty': 0, 'revenue': 0}),
                    'removals': defaultdict(lambda: {'qty': 0, 'revenue': 0}),
                }
            imp_map[item]['item_ordered'] += imp.get('item_ordered', 0)
            for cat in ('bases', 'addons', 'removals'):
                for mod in imp.get(cat, []):
                    name = mod.get('modifier', '')
                    imp_map[item][cat][name]['qty'] += mod.get('qty', 0)
                    imp_map[item][cat][name]['revenue'] += mod.get('revenue', 0)

    item_modifier_pairs = []
    for item, data in sorted(imp_map.items(), key=lambda x: x[1]['item_ordered'], reverse=True):
        total_ordered = data['item_ordered']
        entry = {
            'item': item, 'item_ordered': total_ordered,
            'menu_group': data['menu_group'],
        }
        for cat in ('bases', 'addons', 'removals'):
            cat_list = []
            for name, vals in sorted(data[cat].items(), key=lambda x: x[1]['qty'], reverse=True):
                cat_list.append({
                    'modifier': name, 'qty': vals['qty'],
                    'revenue': round(vals['revenue'], 2),
                    'pct_of_orders': round(vals['qty'] / total_ordered * 100, 1) if total_ordered > 0 else 0,
                })
            entry[cat] = cat_list
        item_modifier_pairs.append(entry)

    # Best dressings: merge
    dress_best = defaultdict(lambda: {'qty': 0, 'revenue': 0})
    for m in mods:
        for d in m.get('best_dressings', []):
            name = d.get('modifier', d.get('dressing', ''))
            dress_best[name]['qty'] += d.get('qty', 0)
            dress_best[name]['revenue'] += d.get('revenue', 0)
    best_dressings = sorted([
        {'modifier': k, 'qty': v['qty'], 'revenue': round(v['revenue'], 2)}
        for k, v in dress_best.items()
    ], key=lambda x: x['qty'], reverse=True)

    # Removal summary: merge
    rem_map = defaultdict(lambda: {'count': 0})
    for m in mods:
        rs = m.get('removal_summary', {})
        if isinstance(rs, dict):
            for k, v in rs.items():
                if isinstance(v, dict):
                    rem_map[k]['count'] += v.get('count', 0)
                elif isinstance(v, (int, float)):
                    rem_map[k]['count'] += v

    # Free modification breakdown: merge
    free_map = {}
    for m in mods:
        for fb in m.get('free_modification_breakdown', []):
            og = fb.get('option_group', fb.get('group', ''))
            if og not in free_map:
                free_map[og] = {'total': 0, 'choices': defaultdict(int)}
            free_map[og]['total'] += fb.get('total', 0)
            for ch in fb.get('choices', []):
                free_map[og]['choices'][ch.get('modifier', '')] += ch.get('qty', 0)
    free_modification_breakdown = []
    for og, data in free_map.items():
        choices = sorted([
            {'modifier': k, 'qty': v, 'pct': round(v / data['total'] * 100, 1) if data['total'] > 0 else 0}
            for k, v in data['choices'].items()
        ], key=lambda x: x['qty'], reverse=True)
        free_modification_breakdown.append({
            'option_group': og, 'total': data['total'], 'choices': choices,
        })

    # Wrap type frequency: merge
    wrap_freq = defaultdict(int)
    for m in mods:
        wf = m.get('wrap_type_freq', {})
        if isinstance(wf, dict):
            for k, v in wf.items():
                wrap_freq[k] += v if isinstance(v, (int, float)) else 0

    return {
        'total_modifier_revenue': round(total_modifier_revenue, 2),
        'total_modifiers': total_modifiers,
        'top_paid_modifiers': top_paid,
        'bottom_paid_modifiers': [],
        'option_group_analysis': [],
        'most_modified_items': [],
        'item_modifier_pairs': item_modifier_pairs,
        'free_modification_breakdown': free_modification_breakdown,
        'modifier_categories': {},
        'wrap_type_freq': dict(wrap_freq),
        'base_selections': {},
        'removal_analysis': {},
        'removal_summary': dict(rem_map),
        'best_dressings': best_dressings,
        'item_mod_matrix': {},
        'alt_milk': [],
        'modifier_attach_rate': 0,
        'free_vs_paid': {},
        'dressing_prep': dressing_prep,
        'total_dressing_portions': total_dressing_portions,
    }


# ──────────────────────────────────────────────
# Kitchen aggregation
# ──────────────────────────────────────────────

def _aggregate_kitchen(days):
    """Aggregate kitchen timing data across multiple days."""
    kits = [d.get('kitchen') for d in days if d.get('kitchen')]
    if not kits:
        return None

    # Stations: average the percentiles across days
    station_map = defaultdict(lambda: defaultdict(list))
    for k in kits:
        for st_name, st_data in k.get('stations', {}).items():
            for field in ('median', 'mean', 'p75', 'p90', 'p95',
                          'under_5min_pct', 'under_10min_pct'):
                if st_data.get(field) is not None:
                    station_map[st_name][field].append(st_data[field])
            station_map[st_name]['total_tickets'].append(st_data.get('total_tickets', 0))
            station_map[st_name]['over_15min_count'].append(st_data.get('over_15min_count', 0))

    stations = {}
    for st, fields in station_map.items():
        stations[st] = {}
        for f in ('median', 'mean', 'p75', 'p90', 'p95',
                  'under_5min_pct', 'under_10min_pct'):
            vals = fields.get(f, [])
            stations[st][f] = round(sum(vals) / len(vals), 1) if vals else 0
        stations[st]['total_tickets'] = sum(fields.get('total_tickets', []))
        stations[st]['over_15min_count'] = sum(fields.get('over_15min_count', []))

    # Hourly speed: merge by station+hour, average medians weighted by tickets
    hourly_speed = defaultdict(lambda: defaultdict(lambda: {'median_sum': 0, 'tickets': 0}))
    for k in kits:
        for st, hours in k.get('hourly_speed', {}).items():
            for h in hours:
                hr = h.get('hour', 0)
                tickets = h.get('tickets', 0)
                hourly_speed[st][hr]['median_sum'] += h.get('median', 0) * tickets
                hourly_speed[st][hr]['tickets'] += tickets
    hourly_speed_out = {}
    for st, hours in hourly_speed.items():
        hourly_speed_out[st] = sorted([
            {'hour': h, 'tickets': v['tickets'],
             'median': round(v['median_sum'] / v['tickets'], 1) if v['tickets'] > 0 else 0}
            for h, v in hours.items()
        ], key=lambda x: x['hour'])

    # Fulfilled by: merge
    fb_map = defaultdict(int)
    for k in kits:
        for f in k.get('fulfilled_by', []):
            fb_map[f.get('name', '')] += f.get('tickets', 0)
    fulfilled_by = sorted([
        {'name': k, 'tickets': v} for k, v in fb_map.items()
    ], key=lambda x: x['tickets'], reverse=True)

    # Kitchen speed by day of week (range mode only)
    _DOW_ORDER = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3,
                  'Friday': 4, 'Saturday': 5, 'Sunday': 6}
    dow_map = defaultdict(lambda: {'median_sum': 0, 'tickets': 0, 'over_15min': 0, 'days': 0})
    for d in days:
        dow = d.get('day_of_week', '')
        kit = d.get('kitchen')
        if not dow or not kit:
            continue
        # Compute weighted overall median across all stations for this day
        day_tickets = 0
        day_median_sum = 0
        day_over15 = 0
        for st_data in kit.get('stations', {}).values():
            t = st_data.get('total_tickets', 0)
            m = st_data.get('median', 0)
            day_tickets += t
            day_median_sum += m * t
            day_over15 += st_data.get('over_15min_count', 0)
        if day_tickets > 0:
            dow_map[dow]['median_sum'] += day_median_sum
            dow_map[dow]['tickets'] += day_tickets
            dow_map[dow]['over_15min'] += day_over15
            dow_map[dow]['days'] += 1
    kitchen_by_dow = []
    for dow_name, data in sorted(dow_map.items(), key=lambda x: _DOW_ORDER.get(x[0], 9)):
        t = data['tickets']
        kitchen_by_dow.append({
            'dow': dow_name,
            'median': round(data['median_sum'] / t, 1) if t > 0 else 0,
            'tickets': t,
            'avg_tickets': round(t / max(data['days'], 1)),
            'over_15min': data['over_15min'],
        })

    return {
        'stations': stations,
        'distribution': {},
        'hourly_speed': hourly_speed_out,
        'walkin_vs_online': {},
        'peak_concurrent': {},
        'fulfilled_by': fulfilled_by,
        'outlier_count': sum(k.get('outlier_count', 0) for k in kits),
        'kitchen_by_dow': kitchen_by_dow,
    }


# ──────────────────────────────────────────────
# Labor aggregation
# ──────────────────────────────────────────────

def _aggregate_labor(days):
    """Aggregate labor data across multiple days."""
    labs = [d.get('labor') for d in days if d.get('labor')]
    if not labs:
        return None

    total_labor = _sum_field(labs, 'total_labor')
    total_hours = _sum_field(labs, 'total_hours')
    toast_total = sum(d.get('revenue', {}).get('toast_total', 0) for d in days if d.get('revenue'))

    # Employee roster: merge by employee name, sum hours/pay
    emp_map = defaultdict(lambda: {
        'total_hours': 0, 'regular_hours': 0, 'overtime_hours': 0,
        'payable_hours': 0, 'total_pay': 0, 'cash_tips': 0, 'non_cash_tips': 0,
        'role': '', 'toast_wage': 0, 'effective_wage': 0,
        'wage_source': '', 'employee_type': '', 'auto_clockout': False,
        'shifts': 0,
    })
    for l in labs:
        for e in l.get('employee_roster', []):
            name = e.get('employee', '')
            emp_map[name]['total_hours'] += e.get('total_hours', 0)
            emp_map[name]['regular_hours'] += e.get('regular_hours', 0)
            emp_map[name]['overtime_hours'] += e.get('overtime_hours', 0)
            emp_map[name]['payable_hours'] += e.get('payable_hours', 0)
            emp_map[name]['total_pay'] += e.get('total_pay', 0)
            emp_map[name]['cash_tips'] += e.get('cash_tips', 0)
            emp_map[name]['non_cash_tips'] += e.get('non_cash_tips', 0)
            emp_map[name]['role'] = e.get('role', emp_map[name]['role'])
            emp_map[name]['toast_wage'] = e.get('toast_wage', 0)
            emp_map[name]['wage_source'] = e.get('wage_source', '')
            emp_map[name]['employee_type'] = e.get('employee_type', '')
            emp_map[name]['shifts'] += 1
            if e.get('auto_clockout'):
                emp_map[name]['auto_clockout'] = True

    employee_roster = sorted([
        {'employee': k, **v,
         'effective_wage': _safe_div(v['total_pay'], v['payable_hours'])}
        for k, v in emp_map.items()
    ], key=lambda x: x['total_pay'], reverse=True)

    # OT detail: merge
    ot_map = defaultdict(lambda: {'ot_hrs': 0, 'ot_pay': 0, 'shift_hours': 0,
                                   'reg_hrs': 0, 'role': '', 'wage': 0})
    for l in labs:
        for o in l.get('ot_detail', []):
            name = o.get('employee', '')
            ot_map[name]['ot_hrs'] += o.get('ot_hrs', 0)
            ot_map[name]['ot_pay'] += o.get('ot_pay', 0)
            ot_map[name]['shift_hours'] += o.get('shift_hours', 0)
            ot_map[name]['reg_hrs'] += o.get('reg_hrs', 0)
            ot_map[name]['role'] = o.get('role', '')
            ot_map[name]['wage'] = o.get('wage', 0)
    ot_detail = sorted([
        {'employee': k, **v, 'ot_pct_of_pay': 0}
        for k, v in ot_map.items() if v['ot_hrs'] > 0
    ], key=lambda x: x['ot_pay'], reverse=True)

    # Staffing by half-hour: average across days
    staff_map = defaultdict(lambda: {'staff_count': 0, 'order_count': 0, 'n': 0})
    for l in labs:
        for s in l.get('staffing_by_halfhour', []):
            t = s.get('time', '')
            staff_map[t]['staff_count'] += s.get('staff_count', 0)
            staff_map[t]['order_count'] += s.get('order_count', 0)
            staff_map[t]['n'] += 1
    staffing_by_halfhour = sorted([
        {'time': k, 'staff_count': round(v['staff_count'] / v['n'], 1),
         'order_count': round(v['order_count'] / v['n'], 1),
         'orders_per_staff': _safe_div(v['order_count'] / v['n'],
                                        v['staff_count'] / v['n'])}
        for k, v in staff_map.items() if v['n'] > 0
    ], key=lambda x: x['time'])

    # Daypart efficiency: sum across days
    dp_map = defaultdict(lambda: {'hours': 0, 'orders': 0, 'revenue': 0, 'labor_cost': 0, 'name': ''})
    for l in labs:
        for dp in l.get('daypart_efficiency', []):
            key = dp.get('daypart', dp.get('name', ''))
            dp_map[key]['hours'] += _to_float(dp.get('hours', 0))
            dp_map[key]['orders'] += _to_float(dp.get('orders', 0))
            dp_map[key]['revenue'] += _to_float(dp.get('revenue', 0))
            dp_map[key]['labor_cost'] += _to_float(dp.get('labor_cost', 0))
            dp_map[key]['name'] = dp.get('name', key)
    daypart_efficiency = [
        {'daypart': k, 'name': v['name'], 'hours': round(v['hours'], 1),
         'orders': v['orders'], 'revenue': round(v['revenue'], 2),
         'labor_cost': round(v['labor_cost'], 2),
         'rev_per_labor_dollar': _safe_div(v['revenue'], v['labor_cost'])}
        for k, v in dp_map.items()
    ]

    # Role breakdown: merge
    role_map = defaultdict(lambda: {'headcount': 0, 'hours': 0, 'cost': 0})
    for l in labs:
        for r in l.get('role_breakdown', []):
            role = r.get('role', '')
            role_map[role]['hours'] += r.get('hours', 0)
            role_map[role]['cost'] += r.get('cost', 0)
            role_map[role]['headcount'] = max(role_map[role]['headcount'], r.get('headcount', 0))
    role_breakdown = sorted([
        {'role': k, 'headcount': v['headcount'], 'hours': round(v['hours'], 1),
         'cost': round(v['cost'], 2),
         'cost_pct': _safe_div(v['cost'], total_labor) * 100}
        for k, v in role_map.items()
    ], key=lambda x: x['cost'], reverse=True)

    return {
        'total_labor': round(total_labor, 2),
        'total_hours': round(total_hours, 2),
        'labor_pct': _safe_div(total_labor, toast_total) * 100 if toast_total > 0 else 0,
        'rev_per_labor_hr': _safe_div(toast_total, total_hours),
        'orders_per_labor_hr': 0,
        'ftes': round(total_hours / 8, 1) if total_hours > 0 else 0,
        'blended_rate': _safe_div(total_labor, total_hours),
        'hourly_records': [],
        'ot_detail': ot_detail,
        'staffing_by_halfhour': staffing_by_halfhour,
        'daypart_efficiency': daypart_efficiency,
        'role_breakdown': role_breakdown,
        'shift_distribution': {},
        'avg_shift_length': 0,
        'median_shift_length': 0,
        'break_violations': [],
        'auto_clockouts': [],
        'tip_distribution': [],
        'employee_roster': employee_roster,
    }


# ──────────────────────────────────────────────
# Payments aggregation
# ──────────────────────────────────────────────

def _aggregate_payments(days):
    """Aggregate payment data across multiple days."""
    pays = [d.get('payments') for d in days if d.get('payments')]
    if not pays:
        return None

    # Type breakdown: merge by type
    type_map = defaultdict(lambda: {'amount': 0, 'count': 0})
    for p in pays:
        for t in p.get('type_breakdown', []):
            type_map[t.get('type', '')]['amount'] += t.get('amount', 0)
            type_map[t.get('type', '')]['count'] += t.get('count', 0)
    total_amount = sum(v['amount'] for v in type_map.values())
    type_breakdown = sorted([
        {'type': k, 'amount': round(v['amount'], 2), 'count': v['count'],
         'pct': _safe_div(v['amount'], total_amount) * 100}
        for k, v in type_map.items()
    ], key=lambda x: x['amount'], reverse=True)

    # Card breakdown: merge
    card_map = defaultdict(lambda: {'amount': 0, 'count': 0})
    for p in pays:
        for c in p.get('card_breakdown', []):
            card_map[c.get('card_type', '')]['amount'] += c.get('amount', 0)
            card_map[c.get('card_type', '')]['count'] += c.get('count', 0)
    card_breakdown = sorted([
        {'card_type': k, 'amount': round(v['amount'], 2), 'count': v['count']}
        for k, v in card_map.items()
    ], key=lambda x: x['amount'], reverse=True)

    # Platform breakdown: merge
    plat_map = defaultdict(lambda: {'amount': 0, 'count': 0})
    for p in pays:
        for pl in p.get('platform_breakdown', []):
            plat_map[pl.get('platform', '')]['amount'] += pl.get('amount', 0)
            plat_map[pl.get('platform', '')]['count'] += pl.get('count', 0)
    platform_breakdown = sorted([
        {'platform': k, 'amount': round(v['amount'], 2), 'count': v['count']}
        for k, v in plat_map.items()
    ], key=lambda x: x['amount'], reverse=True)

    tds_fees = _sum_field(pays, 'tds_fees')

    return {
        'type_breakdown': type_breakdown,
        'card_breakdown': card_breakdown,
        'platform_breakdown': platform_breakdown,
        'swiped_vs_keyed': {},
        'source_split': {},
        'tip_summary': {},
        'auto_gratuity_total': _sum_field(pays, 'auto_gratuity_total'),
        'tds_fees': round(tds_fees, 2),
        'refund_summary': {},
        'void_summary': {},
        'gift_card_usage': {},
        'unique_card_count': 0,
        'cash_activity': [],
    }


# ──────────────────────────────────────────────
# Customers aggregation
# ──────────────────────────────────────────────

def _aggregate_customers(days):
    """Aggregate customer data across multiple days."""
    custs = [d.get('customers') for d in days if d.get('customers')]
    if not custs:
        return None

    # Deduplicate customer IDs across days
    all_ids = set()
    for c in custs:
        ids = c.get('customer_ids', [])
        if isinstance(ids, (set, list)):
            all_ids.update(ids)

    # Top spenders: merge by customer_id
    spender_map = defaultdict(lambda: {'total_spend': 0, 'orders': 0, 'name': ''})
    for c in custs:
        for s in c.get('top_spenders', []):
            cid = s.get('customer_id', '')
            spender_map[cid]['total_spend'] += s.get('total_spend', 0)
            spender_map[cid]['orders'] += s.get('orders', 0)
            spender_map[cid]['name'] = s.get('name', '')
    top_spenders = sorted([
        {'customer_id': k, 'name': v['name'], 'total_spend': round(v['total_spend'], 2),
         'orders': v['orders'], 'avg_order': _safe_div(v['total_spend'], v['orders'])}
        for k, v in spender_map.items()
    ], key=lambda x: x['total_spend'], reverse=True)[:20]

    # Direct top spenders
    direct_map = defaultdict(lambda: {'total_spend': 0, 'orders': 0, 'name': ''})
    for c in custs:
        for s in c.get('direct_top_spenders', []):
            cid = s.get('customer_id', '')
            direct_map[cid]['total_spend'] += s.get('total_spend', 0)
            direct_map[cid]['orders'] += s.get('orders', 0)
            direct_map[cid]['name'] = s.get('name', '')
    direct_top_spenders = sorted([
        {'customer_id': k, 'name': v['name'], 'total_spend': round(v['total_spend'], 2),
         'orders': v['orders'], 'avg_order': _safe_div(v['total_spend'], v['orders'])}
        for k, v in direct_map.items()
    ], key=lambda x: x['total_spend'], reverse=True)[:20]

    # Most frequent customers (by visit count, sorted by orders desc)
    freq_map = defaultdict(lambda: {'total_spend': 0, 'orders': 0, 'name': ''})
    for c in custs:
        for s in c.get('most_frequent', []):
            cid = s.get('customer_id', '')
            freq_map[cid]['total_spend'] += s.get('total_spend', 0)
            freq_map[cid]['orders'] += s.get('orders', 0)
            freq_map[cid]['name'] = s.get('name', '')
    # Also merge from top_spenders / direct_top_spenders for completeness
    for cid, v in spender_map.items():
        if cid not in freq_map:
            freq_map[cid] = dict(v)
        else:
            # Already merged, just ensure we have latest totals
            if freq_map[cid]['orders'] < v['orders']:
                freq_map[cid] = dict(v)
    most_frequent = sorted([
        {'customer_id': k, 'name': v['name'], 'total_spend': round(v['total_spend'], 2),
         'orders': v['orders'], 'avg_order': round(_safe_div(v['total_spend'], v['orders']), 2)}
        for k, v in freq_map.items() if v['orders'] > 1
    ], key=lambda x: x['orders'], reverse=True)[:15]

    phone_count = _sum_field(custs, 'phone_count')
    email_count = _sum_field(custs, 'email_count')
    direct_phone = _sum_field(custs, 'direct_phone_count')
    direct_email = _sum_field(custs, 'direct_email_count')
    direct_customers = _sum_field(custs, 'direct_customers')
    tp_orders = _sum_field(custs, 'tp_orders')

    total_checks = sum(c.get('phone_count', 0) + c.get('email_count', 0)
                       for c in custs)  # approximate

    return {
        'unique_customers': len(all_ids),
        'customer_ids': all_ids,
        'phone_capture_rate': _safe_div(phone_count, total_checks) * 100 if total_checks > 0 else 0,
        'email_capture_rate': _safe_div(email_count, total_checks) * 100 if total_checks > 0 else 0,
        'phone_count': phone_count,
        'email_count': email_count,
        'party_size_distribution': {},
        'avg_party_size': _avg_field(custs, 'avg_party_size'),
        'tab_name_unique_count': sum(c.get('tab_name_unique_count', 0) for c in custs),
        'unique_card_customers': 0,
        'capture_by_channel': {},
        'avg_spend_per_customer': 0,
        'top_spenders': top_spenders,
        'direct_customers': direct_customers,
        'direct_phone_count': direct_phone,
        'direct_phone_rate': _safe_div(direct_phone, direct_customers) * 100 if direct_customers > 0 else 0,
        'direct_email_count': direct_email,
        'direct_email_rate': _safe_div(direct_email, direct_customers) * 100 if direct_customers > 0 else 0,
        'tp_orders': tp_orders,
        'direct_top_spenders': direct_top_spenders,
        'most_frequent': most_frequent,
    }


# ──────────────────────────────────────────────
# Staffing demand overlay
# ──────────────────────────────────────────────

def _aggregate_staffing_overlay(days):
    """Average the staffing demand overlay across days."""
    overlays = [d.get('staffing_demand_overlay', []) for d in days
                if d.get('staffing_demand_overlay')]
    if not overlays:
        return []

    label_map = defaultdict(lambda: {'staff_count': 0, 'revenue': 0, 'orders': 0, 'n': 0, 'hour': None})
    for ov in overlays:
        for slot in ov:
            label = slot.get('label', '')
            label_map[label]['staff_count'] += slot.get('staff_count', 0)
            label_map[label]['revenue'] += slot.get('revenue', 0)
            label_map[label]['orders'] += slot.get('orders', 0)
            label_map[label]['n'] += 1
            if label_map[label]['hour'] is None:
                label_map[label]['hour'] = slot.get('hour')

    return sorted([
        {'hour': v['hour'], 'label': k,
         'staff_count': round(v['staff_count'] / v['n'], 1),
         'revenue': round(v['revenue'] / v['n'], 2),
         'orders': round(v['orders'] / v['n'], 1),
         'orders_per_staff': _safe_div(v['orders'] / v['n'],
                                        v['staff_count'] / v['n']),
         'rev_per_staff': _safe_div(v['revenue'] / v['n'],
                                     v['staff_count'] / v['n'])}
        for k, v in label_map.items() if v['n'] > 0
    ], key=lambda x: x.get('label', ''))


# ──────────────────────────────────────────────
# Main aggregation function
# ──────────────────────────────────────────────

FOOD_COST_PCT = 0.30


def _fmt_daily_label(d):
    """Format a daily metrics dict into a short label like 'Mon 2/12'."""
    ds = d.get('date_str', '')
    dow = d.get('day_of_week', '')
    if ds and len(ds) == 8:
        m = int(ds[4:6])
        day = int(ds[6:8])
        short_dow = dow[:3] if dow else ''
        return f"{short_dow} {m}/{day}".strip()
    return ds


def _safe_num(v, default=0):
    """Return v if it's a finite number, else default. Handles NaN/None."""
    if v is None:
        return default
    try:
        f = float(v)
        if f != f:  # NaN check
            return default
        return f
    except (TypeError, ValueError):
        return default


def _weighted_kitchen_median(kitchen):
    """Compute overall weighted median from a day's kitchen station data."""
    if not kitchen or not kitchen.get('stations'):
        return None
    total_tickets = 0
    median_sum = 0
    for st_data in kitchen['stations'].values():
        t = st_data.get('total_tickets', 0)
        m = st_data.get('median', 0)
        total_tickets += t
        median_sum += m * t
    if total_tickets > 0:
        return round(median_sum / total_tickets, 1)
    return None


def _classify_weather_type(weather):
    """Classify weather into workbench categories.

    Priority: if both rain and snow, classify by dominant precip type
    (using water-equivalent: 1in snow ~ 0.1in rain).
    """
    if not weather:
        return "Unknown"
    snow = weather.get("snow_inches", 0) or 0
    rain = weather.get("rain_inches", 0) or 0
    wind = weather.get("wind_max_mph", 0) or 0
    conditions = weather.get("conditions", "")
    # Compare water-equivalent: 1in snow ~ 0.1in rain
    # When both present, classify by dominant precip type
    snow_we = snow * 0.1
    if snow > 0 and rain > 0:
        return "Snow" if snow_we >= rain else "Rain"
    if snow > 0:
        return "Snow"
    if rain >= 0.05:
        return "Rain"
    if wind > 25:
        return "Windy"
    if conditions in ("Clear", "Mostly Clear"):
        return "Clear"
    return "Cloudy"


def _classify_temp_band(weather):
    """Classify temperature into bands based on high temp."""
    if not weather:
        return "Unknown"
    temp = weather.get("temp_high")
    if temp is None:
        return "Unknown"
    if temp < 40:
        return "Cold"
    elif temp < 60:
        return "Cool"
    elif temp < 80:
        return "Warm"
    return "Hot"


_WX_MG_MAP = {
    "panini wraps": "Wraps & Paninis",
    "panini wraps  + side of plantain chips": "Wraps & Paninis",
    "wrap boxes": "Wraps & Paninis",
    "salads & bowls": "Salads & Bowls", "salads + bowls": "Salads & Bowls",
    "healthy smoothies (18 oz)": "Smoothies", "smoothies": "Smoothies",
    "fresh pressed juice": "Juice", "freshly pressed juice": "Juice",
    "snacks": "Snacks", "all snacks": "Snacks",
    "soup": "Soup", "soups": "Soup",
    "house made beverages": "Drinks", "house beverages": "Drinks",
    "grab & go drinks": "Drinks", "drinks": "Drinks", "hot teas": "Drinks",
    "iced matcha": "Matcha", "for the group": "Catering",
}
_WX_WALKIN = {"To Go"}
_WX_3P = {"Uber Eats - Delivery", "Uber Eats - Takeout",
           "DoorDash - Delivery", "DoorDash - Takeout",
           "Grubhub - Delivery", "Grubhub - Takeout"}
_WX_ONLINE = {"Online Ordering - Takeout", "Online Ordering - Delivery"}


def _build_channel_x_category(date_str):
    """Compute channel x category cross-tab from ItemSelectionDetails."""
    import os, pandas as pd
    cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              '..', '.tmp', date_str, 'ItemSelectionDetails.csv')
    if not os.path.exists(cache_path):
        return {}
    try:
        items = pd.read_csv(cache_path)
        if 'Void?' in items.columns:
            items = items[items['Void?'] != True]
        # Channel group
        def _ch(opt):
            if pd.isna(opt):
                return "Other"
            s = str(opt).strip()
            if s in _WX_WALKIN:
                return "Walk-In"
            if s in _WX_3P:
                return "3P"
            if s in _WX_ONLINE:
                return "Online"
            return "Other"
        items['_ch'] = items['Dining Option'].apply(_ch) if 'Dining Option' in items.columns else 'Other'
        # Menu group
        if 'Menu Group' in items.columns:
            items['_mg'] = items['Menu Group'].astype(str).str.strip().str.lower().map(
                _WX_MG_MAP
            ).fillna(items['Menu Group'].astype(str).str.strip())
        else:
            return {}
        # Cross-tab
        ct = items.groupby(['_ch', '_mg']).agg(
            revenue=('Net Price', 'sum'), qty=('Qty', 'sum')
        ).reset_index()
        result = {}
        for _, row in ct.iterrows():
            ch = row['_ch']
            mg = row['_mg']
            if ch not in result:
                result[ch] = {}
            result[ch][mg] = {
                'revenue': round(row['revenue'], 2),
                'qty': int(row['qty']),
            }
        return result
    except Exception:
        return {}


def _build_weather_workbench_days(days):
    """Build enriched per-day array for weather analysis workbench."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fetch_weather_data import get_daily_weather

    result = []
    for d in days:
        date_str = d.get('date_str', '')
        if not date_str:
            continue
        weather = get_daily_weather(date_str)
        if not weather:
            continue

        rev = d.get('revenue') or {}
        orders_section = d.get('orders') or {}

        # Channel breakdown (grouped: Walk-In, Online, 3P)
        w3o = rev.get('walkin_3p_online', {})
        channels = {}
        for gname, gdata in w3o.items():
            if isinstance(gdata, dict):
                channels[gname] = {
                    'revenue': round(gdata.get('revenue', 0), 2),
                    'orders': gdata.get('orders', 0),
                }

        # Menu group mix
        menu_groups = {}
        for mg in orders_section.get('menu_group_mix', []):
            gname = mg.get('group', '')
            if gname and gname != 'nan' and str(gname).lower() != 'nan':
                menu_groups[gname] = {
                    'revenue': round(mg.get('revenue', 0), 2),
                    'qty': mg.get('qty', 0),
                }

        # Day label
        dow = d.get('day_of_week', '')
        short_dow = dow[:3] if dow else ''
        if len(date_str) == 8:
            m = int(date_str[4:6])
            day_num = int(date_str[6:8])
            label = f"{short_dow} {m}/{day_num}"
        else:
            label = date_str

        # Channel x Category cross-tab for combined filtering
        cx = _build_channel_x_category(date_str)

        result.append({
            'date': date_str,
            'label': label,
            'dow': dow,
            'weather_type': _classify_weather_type(weather),
            'temp_band': _classify_temp_band(weather),
            'temp_high': weather.get('temp_high'),
            'temp_low': weather.get('temp_low'),
            'conditions': weather.get('conditions', ''),
            'rain_inches': round(weather.get('rain_inches', 0) or 0, 2),
            'snow_inches': round(weather.get('snow_inches', 0) or 0, 2),
            'wind_max_mph': round(weather.get('wind_max_mph', 0) or 0, 1),
            'bad_weather': weather.get('bad_weather', False),
            'revenue': round(rev.get('toast_total', 0), 2),
            'orders': rev.get('total_orders', 0),
            'avg_check': round(rev.get('avg_check', 0), 2),
            'channels': channels,
            'menu_groups': menu_groups,
            'cx': cx,
        })
    return result


_CHANNEL_GROUPS = {
    'Walk-In': ['Walk-In', 'To Go', 'Phone'],
    'Online': ['Online', 'Online Ordering - Delivery', 'Online Ordering - Takeout',
               'Online Ordering - Takeout, Online Ordering - Takeout'],
    'Uber': ['Uber Delivery', 'Uber Takeout'],
    'DoorDash': ['DD Delivery', 'DD Takeout'],
    'GrubHub': ['GH Delivery', 'GH Takeout'],
    'Catering': ['Catering Delivery', 'Catering Takeout', 'Catering'],
}


def _group_channel(name):
    """Map a raw channel name to a consolidated group."""
    for group, members in _CHANNEL_GROUPS.items():
        if name in members:
            return group
    return 'Other'


def _build_channel_series(days):
    """Build per-channel-group daily revenue series for stacked chart."""
    n = len(days)
    grouped = {}  # group_name -> [daily values]
    for i, d in enumerate(days):
        channels = d.get('revenue', {}).get('channels', {})
        if not channels:
            continue
        for ch_name, val in channels.items():
            if isinstance(val, dict):
                val = val.get('revenue', val.get('total', 0))
            val = _safe_num(val)
            group = _group_channel(ch_name)
            if group not in grouped:
                grouped[group] = [0.0] * n
            grouped[group][i] += val
    # Round values
    for g in grouped:
        grouped[g] = [round(v, 2) for v in grouped[g]]
    return grouped


def aggregate_metrics(daily_metrics_list: list, start_date_str: str,
                      end_date_str: str, num_days: int) -> dict:
    """
    Aggregate a list of daily metrics dicts into a single combined dict.
    The result has the same shape as compute_all_metrics() output.

    Args:
        daily_metrics_list: list of dicts from compute_all_metrics()
        start_date_str: YYYYMMDD of first day
        end_date_str: YYYYMMDD of last day
        num_days: number of days in range
    """
    days = [d for d in daily_metrics_list if d is not None]
    if not days:
        return {}

    revenue = _aggregate_revenue(days)
    orders = _aggregate_orders(days)
    labor = _aggregate_labor(days)

    toast_total = revenue.get('toast_total', 0) if revenue else 0
    total_orders = revenue.get('total_orders', 0) if revenue else 0
    total_labor = labor.get('total_labor', 0) if labor else 0
    total_hours = labor.get('total_hours', 0) if labor else 0
    _agg_fc = _agg_food_cost(days, toast_total)

    result = {
        # Range metadata
        'date_str': f"{start_date_str}-{end_date_str}",
        'date_display': f"{start_date_str} to {end_date_str}",
        'day_of_week': f"{num_days}-day range",
        'is_range': True,
        'range_days': num_days,
        'range_start': start_date_str,
        'range_end': end_date_str,
        'daily_dates': [d.get('date_str', '') for d in days],

        # Sections
        'revenue': revenue,
        'orders': orders,
        'baskets': _aggregate_baskets(days),
        'modifiers': _aggregate_modifiers(days),
        'kitchen': _aggregate_kitchen(days),
        'labor': labor,
        'payments': _aggregate_payments(days),
        'customers': _aggregate_customers(days),
        'staffing_demand_overlay': _aggregate_staffing_overlay(days),

        # Top-level convenience fields
        'toast_total': toast_total,
        'total_orders': total_orders,
        'total_labor': total_labor,
        'total_hours': total_hours,
        'food_cost': _agg_fc,
        'food_cost_pct': round(_agg_fc / toast_total * 100, 1) if toast_total > 0 else 0,
        'food_cost_method': _agg_food_cost_method(days),
        'food_cost_coverage': _agg_food_cost_coverage(days),
        'prime_cost': round(_agg_fc + total_labor, 2),
        'prime_cost_pct': round((_agg_fc + total_labor) / toast_total * 100, 1) if toast_total > 0 else 0,
        'tds_fees': _sum_field(
            [d.get('payments') for d in days if d.get('payments')], 'tds_fees'),

        # Daily breakdown for summary table
        'daily_summary': [
            {
                'date_str': d.get('date_str', ''),
                'day_of_week': d.get('day_of_week', ''),
                'revenue': _safe_num(d.get('revenue', {}).get('toast_total')),
                'orders': _safe_num(d.get('revenue', {}).get('total_orders')),
                'avg_check': _safe_num(d.get('revenue', {}).get('avg_check')),
                'labor': _safe_num(d.get('labor', {}).get('total_labor')) if d.get('labor') else None,
                'labor_pct': _safe_num(d.get('labor', {}).get('labor_pct')) if d.get('labor') else None,
                'kitchen_median': _safe_num(_weighted_kitchen_median(d.get('kitchen'))),
            }
            for d in days
        ],

        # Chart-ready daily series for trend charts (range mode only)
        # All values run through _safe_num to convert NaN/None → 0
        'daily_dates': [_fmt_daily_label(d) for d in days],
        'daily_revenue_series': [
            _safe_num(d.get('revenue', {}).get('toast_total')) for d in days
        ],
        'daily_labor_series': [
            _safe_num(d.get('labor', {}).get('total_labor')) for d in days
        ],
        'daily_labor_pct_series': [
            _safe_num(d.get('labor', {}).get('labor_pct')) for d in days
        ],
        'daily_orders_series': [
            _safe_num(d.get('revenue', {}).get('total_orders')) for d in days
        ],
        'daily_avg_check_series': [
            _safe_num(d.get('revenue', {}).get('avg_check')) for d in days
        ],
        'daily_channel_series': _build_channel_series(days),
    }

    # Weather analysis workbench (range mode only)
    try:
        result['weather_workbench_days'] = _build_weather_workbench_days(days)
    except Exception:
        result['weather_workbench_days'] = []

    return result
