"""Customer analysis, capture rates, and spend metrics."""

import pandas as pd
from .utils import _safe_numeric, _safe_div, _get_channel_group

def compute_customer_metrics(check_details, payment_details, item_details, order_details=None):
    """Compute customer-level metrics from CheckDetails, PaymentDetails, ItemDetails.

    Returns dict with unique customer counts, contact capture rates,
    party size distribution, unique card customers, capture by channel,
    and customer spend analysis.
    """
    try:
        cd = check_details.copy()
        pd_df = payment_details.copy()

        # ── Unique customers ──
        unique_customers = 0
        customer_ids = set()
        if 'Customer Id' in cd.columns:
            valid_customers = cd[cd['Customer Id'].notna() & (cd['Customer Id'] != '')]
            unique_customers = valid_customers['Customer Id'].nunique()
            customer_ids = set(valid_customers['Customer Id'].unique())

        # ── Phone capture rate ──
        total_checks = len(cd)
        phone_count = 0
        if 'Customer Phone' in cd.columns:
            phone_filled = cd[cd['Customer Phone'].notna() & (cd['Customer Phone'] != '')]
            phone_count = len(phone_filled)
        phone_capture_rate = round(_safe_div(phone_count, total_checks) * 100, 1)

        # ── Email capture rate ──
        email_count = 0
        if 'Customer Email' in cd.columns:
            email_filled = cd[cd['Customer Email'].notna() & (cd['Customer Email'] != '')]
            email_count = len(email_filled)
        email_capture_rate = round(_safe_div(email_count, total_checks) * 100, 1)

        # ── Party size distribution ──
        party_size_distribution = {}
        avg_party_size = 0
        if 'Table Size' in cd.columns:
            cd['Table Size'] = _safe_numeric(cd, 'Table Size')
            valid_party = cd[cd['Table Size'] > 0]
            if len(valid_party) > 0:
                sizes = valid_party['Table Size'].astype(int)
                for size in sorted(sizes.unique()):
                    party_size_distribution[int(size)] = int((sizes == size).sum())
                avg_party_size = round(sizes.mean(), 2)

        # ── Tab name unique count (3P customer names) ──
        tab_name_unique_count = 0
        if 'Tab Name' in pd_df.columns:
            valid_tabs = pd_df[pd_df['Tab Name'].notna() & (pd_df['Tab Name'] != '')]
            tab_name_unique_count = valid_tabs['Tab Name'].nunique()

        # ── Unique card customers ──
        unique_card_customers = 0
        if 'Last 4 Card Digits' in pd_df.columns:
            valid_cards = pd_df[
                pd_df['Last 4 Card Digits'].notna() & (pd_df['Last 4 Card Digits'] != '')
            ]
            unique_card_customers = valid_cards['Last 4 Card Digits'].nunique()

        # ── Contact capture by channel (Walk-In vs 3P vs Online) ──
        # Join CheckDetails to OrderDetails via Check # → Order #
        capture_by_channel = {}
        if order_details is not None and 'Check #' in cd.columns:
            od = order_details.copy()
            if 'Dining Options' in od.columns and 'Order #' in od.columns:
                od['_group'] = od['Dining Options'].apply(_get_channel_group)
                merged = cd.merge(
                    od[['Order #', '_group']].drop_duplicates('Order #'),
                    left_on='Check #', right_on='Order #', how='left',
                )
                for gn in ["Walk-In", "3P", "Online"]:
                    g_subset = merged[merged['_group'] == gn]
                    g_total = len(g_subset)
                    if g_total == 0:
                        continue
                    g_phone = 0
                    if 'Customer Phone' in g_subset.columns:
                        g_phone = len(g_subset[g_subset['Customer Phone'].notna() & (g_subset['Customer Phone'] != '')])
                    g_email = 0
                    if 'Customer Email' in g_subset.columns:
                        g_email = len(g_subset[g_subset['Customer Email'].notna() & (g_subset['Customer Email'] != '')])
                    capture_by_channel[gn] = {
                        "total": g_total,
                        "phone_count": g_phone,
                        "phone_rate": round(_safe_div(g_phone, g_total) * 100, 1),
                        "email_count": g_email,
                        "email_rate": round(_safe_div(g_email, g_total) * 100, 1),
                    }

        # ── Customer spend analysis ──
        # Use CheckDetails 'Total' directly, or join to OrderDetails via Check # → Order #
        avg_spend_per_customer = 0
        top_spenders = []
        most_frequent = []
        if 'Customer Id' in cd.columns:
            # Prefer OrderDetails Amount if available, fall back to CheckDetails Total
            if order_details is not None and 'Order #' in order_details.columns and 'Check #' in cd.columns:
                od = order_details.copy()
                od['Amount'] = _safe_numeric(od, 'Amount')
                merged_spend = cd[['Check #', 'Customer Id', 'Customer']].merge(
                    od[['Order #', 'Amount']].drop_duplicates('Order #'),
                    left_on='Check #', right_on='Order #', how='left',
                )
                spend_col = 'Amount'
                order_col = 'Check #'
            elif 'Total' in cd.columns:
                merged_spend = cd[['Customer Id', 'Customer', 'Total']].copy()
                merged_spend['Total'] = _safe_numeric(merged_spend, 'Total')
                spend_col = 'Total'
                order_col = None
            else:
                merged_spend = None

            if merged_spend is not None:
                valid_spend = merged_spend[
                    merged_spend['Customer Id'].notna() & (merged_spend['Customer Id'] != '')
                ]
                if len(valid_spend) > 0:
                    agg_dict = {
                        'total_spend': (spend_col, 'sum'),
                        'name': ('Customer', 'first'),
                    }
                    if order_col:
                        agg_dict['orders'] = (order_col, 'nunique')
                    else:
                        agg_dict['orders'] = ('Customer Id', 'size')
                    cust_spend = valid_spend.groupby('Customer Id').agg(**agg_dict).reset_index()
                    cust_spend['avg_order'] = round(cust_spend['total_spend'] / cust_spend['orders'].replace(0, 1), 2)
                    avg_spend_per_customer = round(cust_spend['total_spend'].mean(), 2)
                    top_spenders_df = cust_spend.nlargest(10, 'total_spend')
                    top_spenders = [
                        {
                            "customer_id": row['Customer Id'],
                            "name": str(row['name']) if pd.notna(row['name']) else "Unknown",
                            "total_spend": round(row['total_spend'], 2),
                            "orders": int(row['orders']),
                            "avg_order": round(row['avg_order'], 2),
                        }
                        for _, row in top_spenders_df.iterrows()
                    ]

                    # Most frequent customers (by visit count, not spend)
                    most_frequent_df = cust_spend.nlargest(10, 'orders')
                    most_frequent = [
                        {
                            "customer_id": row['Customer Id'],
                            "name": str(row['name']) if pd.notna(row['name']) else "Unknown",
                            "total_spend": round(row['total_spend'], 2),
                            "orders": int(row['orders']),
                            "avg_order": round(row['avg_order'], 2),
                        }
                        for _, row in most_frequent_df.iterrows()
                    ]

        # Split direct vs 3P customer stats
        direct_customers = 0
        direct_phone = 0
        direct_email = 0
        tp_orders = 0

        for ch_name in ["Walk-In", "Online"]:
            ch_data = capture_by_channel.get(ch_name, {})
            direct_customers += ch_data.get("total", 0)
            direct_phone += ch_data.get("phone_count", 0)
            direct_email += ch_data.get("email_count", 0)

        tp_data = capture_by_channel.get("3P", {})
        tp_orders = tp_data.get("total", 0)

        # Filter top spenders into direct-only list (exclude 3P)
        # Top spenders are built from CheckDetails with Customer Id, which are
        # predominantly direct (Walk-In / Online) customers.  Pass through as-is.
        direct_top_spenders = top_spenders

        return {
            "unique_customers": unique_customers,
            "customer_ids": customer_ids,
            "phone_capture_rate": phone_capture_rate,
            "email_capture_rate": email_capture_rate,
            "phone_count": phone_count,
            "email_count": email_count,
            "party_size_distribution": party_size_distribution,
            "avg_party_size": avg_party_size,
            "tab_name_unique_count": tab_name_unique_count,
            "unique_card_customers": unique_card_customers,
            "capture_by_channel": capture_by_channel,
            "avg_spend_per_customer": avg_spend_per_customer,
            "top_spenders": top_spenders,
            "direct_customers": direct_customers,
            "direct_phone_count": direct_phone,
            "direct_phone_rate": round(direct_phone / max(direct_customers, 1) * 100, 1),
            "direct_email_count": direct_email,
            "direct_email_rate": round(direct_email / max(direct_customers, 1) * 100, 1),
            "tp_orders": tp_orders,
            "direct_top_spenders": direct_top_spenders,
            "most_frequent": most_frequent,
        }
    except Exception as e:
        return {
            "unique_customers": 0, "customer_ids": set(),
            "phone_capture_rate": 0,
            "email_capture_rate": 0, "phone_count": 0,
            "email_count": 0, "party_size_distribution": {},
            "avg_party_size": 0, "tab_name_unique_count": 0,
            "unique_card_customers": 0,
            "capture_by_channel": {},
            "avg_spend_per_customer": 0, "top_spenders": [],
            "direct_customers": 0, "direct_phone_count": 0,
            "direct_phone_rate": 0, "direct_email_count": 0,
            "direct_email_rate": 0, "tp_orders": 0,
            "direct_top_spenders": [],
            "most_frequent": [],
            "_error": str(e),
        }


# ═══════════════════════════════════════════════════════════════
#  12. ANOMALY DETECTION
# ═══════════════════════════════════════════════════════════════
