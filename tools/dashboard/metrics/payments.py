"""Payment type, card, and fee metrics."""

import pandas as pd
from .utils import _safe_numeric, _safe_div

def compute_payment_metrics(payment_details, cash_entries=None):
    """Compute payment-level metrics from PaymentDetails and CashEntries.

    Returns dict with type/card/platform breakdowns, swiped vs keyed,
    tip summary, fees, refunds, voids, gift card usage, and cash activity.
    """
    try:
        pd_df = payment_details.copy()
        for col in ['Amount', 'Tip', 'Gratuity', 'Total', 'Swiped Card Amount',
                     'Keyed Card Amount', 'V/MC/D Fees', 'Refund Amount', 'Refund Tip Amount']:
            pd_df[col] = _safe_numeric(pd_df, col)

        # ── Type breakdown ──
        type_breakdown = []
        if 'Type' in pd_df.columns:
            total_amount = pd_df['Amount'].sum()
            type_agg = pd_df.groupby('Type').agg(
                amount=('Amount', 'sum'),
                count=('Payment Id', 'count'),
            ).reset_index()
            type_agg = type_agg.sort_values('amount', ascending=False)
            for _, row in type_agg.iterrows():
                type_breakdown.append({
                    "type": row['Type'],
                    "amount": round(row['amount'], 2),
                    "count": int(row['count']),
                    "pct": round(_safe_div(row['amount'], total_amount) * 100, 1),
                })

        # ── Card breakdown ──
        card_breakdown = []
        if 'Card Type' in pd_df.columns:
            card_agg = pd_df[pd_df['Card Type'].notna() & (pd_df['Card Type'] != '')].groupby(
                'Card Type'
            ).agg(
                amount=('Amount', 'sum'),
                count=('Payment Id', 'count'),
            ).reset_index().sort_values('amount', ascending=False)
            for _, row in card_agg.iterrows():
                card_breakdown.append({
                    "card_type": row['Card Type'],
                    "amount": round(row['amount'], 2),
                    "count": int(row['count']),
                })

        # ── Platform breakdown (by Dining Option) ──
        platform_breakdown = []
        if 'Dining Option' in pd_df.columns:
            plat_agg = pd_df.groupby('Dining Option').agg(
                amount=('Amount', 'sum'),
                count=('Payment Id', 'count'),
            ).reset_index().sort_values('amount', ascending=False)
            for _, row in plat_agg.iterrows():
                platform_breakdown.append({
                    "platform": row['Dining Option'],
                    "amount": round(row['amount'], 2),
                    "count": int(row['count']),
                })

        # ── Swiped vs keyed ──
        swiped_amount = round(pd_df['Swiped Card Amount'].sum(), 2)
        keyed_amount = round(pd_df['Keyed Card Amount'].sum(), 2)
        total_card = swiped_amount + keyed_amount
        swiped_vs_keyed = {
            "swiped_amount": swiped_amount,
            "keyed_amount": keyed_amount,
            "swiped_pct": round(_safe_div(swiped_amount, total_card) * 100, 1),
        }

        # ── Source split ──
        source_split = {}
        if 'Source' in pd_df.columns:
            for src, grp in pd_df.groupby('Source'):
                if pd.isna(src):
                    src = "Unknown"
                source_split[str(src)] = round(grp['Amount'].sum(), 2)

        # ── Tip summary ──
        total_tips = round(pd_df['Tip'].sum(), 2)
        total_payment_amount = pd_df['Amount'].sum()
        avg_tip_pct = round(_safe_div(total_tips, total_payment_amount) * 100, 1)

        tips_by_type = {}
        if 'Type' in pd_df.columns:
            for t, grp in pd_df.groupby('Type'):
                t_tips = round(grp['Tip'].sum(), 2)
                if t_tips > 0:
                    tips_by_type[str(t)] = t_tips

        tip_summary = {
            "total_tips": total_tips,
            "avg_tip_pct": avg_tip_pct,
            "tips_by_type": tips_by_type,
        }

        # ── Auto gratuity ──
        auto_gratuity_total = round(pd_df['Gratuity'].sum(), 2)

        # ── TDS fees ──
        tds_fees = round(pd_df['V/MC/D Fees'].sum(), 2)

        # ── Refund summary ──
        refunds = pd_df[pd_df['Refund Amount'] > 0]
        refund_summary = {
            "count": len(refunds),
            "total_amount": round(refunds['Refund Amount'].sum(), 2),
        }

        # ── Void summary ──
        void_summary = {"count": 0, "dates": []}
        if 'Void Date' in pd_df.columns:
            voided = pd_df[pd_df['Void Date'].notna() & (pd_df['Void Date'] != '')]
            void_dates = voided['Void Date'].unique().tolist()
            void_summary = {
                "count": len(voided),
                "dates": [str(d) for d in void_dates],
            }

        # ── Gift card usage ──
        gift_card_usage = {"count": 0, "unique_cards": 0}
        if 'Last 4 Gift Card Digits' in pd_df.columns:
            gc = pd_df[pd_df['Last 4 Gift Card Digits'].notna() & (
                pd_df['Last 4 Gift Card Digits'] != ''
            )]
            gift_card_usage = {
                "count": len(gc),
                "unique_cards": gc['Last 4 Gift Card Digits'].nunique(),
            }

        # ── Unique card count ──
        unique_card_count = 0
        if 'Last 4 Card Digits' in pd_df.columns:
            valid_cards = pd_df[
                pd_df['Last 4 Card Digits'].notna() & (pd_df['Last 4 Card Digits'] != '')
            ]
            unique_card_count = valid_cards['Last 4 Card Digits'].nunique()

        # ── Cash activity ──
        cash_activity = []
        if cash_entries is not None and len(cash_entries) > 0:
            ce = cash_entries.copy()
            ce['Amount'] = _safe_numeric(ce, 'Amount')
            for _, row in ce.iterrows():
                cash_activity.append({
                    "action": str(row.get('Action', '')).strip(),
                    "amount": round(float(row.get('Amount', 0) or 0), 2),
                    "employee": str(row.get('Employee', '')).strip(),
                    "time": str(row.get('Created Date', '')).strip(),
                })

        return {
            "type_breakdown": type_breakdown,
            "card_breakdown": card_breakdown,
            "platform_breakdown": platform_breakdown,
            "swiped_vs_keyed": swiped_vs_keyed,
            "source_split": source_split,
            "tip_summary": tip_summary,
            "auto_gratuity_total": auto_gratuity_total,
            "tds_fees": tds_fees,
            "refund_summary": refund_summary,
            "void_summary": void_summary,
            "gift_card_usage": gift_card_usage,
            "unique_card_count": unique_card_count,
            "cash_activity": cash_activity,
        }
    except Exception as e:
        return {
            "type_breakdown": [], "card_breakdown": [],
            "platform_breakdown": [], "swiped_vs_keyed": {},
            "source_split": {}, "tip_summary": {},
            "auto_gratuity_total": 0, "tds_fees": 0,
            "refund_summary": {}, "void_summary": {},
            "gift_card_usage": {}, "unique_card_count": 0,
            "cash_activity": [],
            "_error": str(e),
        }


# ═══════════════════════════════════════════════════════════════
#  11. CUSTOMER METRICS
# ═══════════════════════════════════════════════════════════════
