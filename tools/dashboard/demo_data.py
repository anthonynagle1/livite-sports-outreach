"""Demo data generator — scales real cached data for demo dashboards.

Loads real Toast CSV data from a cached date, scales dollar amounts and
quantities down to simulate a smaller location. Employee names are kept
intact so MASTER_WAGES lookup works correctly. Customer PII is cleared.
The output has the same schema as get_daily_data() so it feeds directly
into the normal metrics + HTML pipeline.
"""
from __future__ import annotations

import os
import logging

import pandas as pd

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_DIR = os.path.join(_BASE_DIR, ".tmp")

# Revenue scale factor — 0.35 takes ~$11,750 → ~$4,100
REVENUE_SCALE = 0.35


def generate_demo_data(source_date: str = "20260217") -> dict:
    """Load real cached CSVs and scale down for demo.

    Returns dict of {filename_without_ext: DataFrame} — same format as
    fetch_toast_data.get_daily_data().
    """
    cache_dir = os.path.join(_CACHE_DIR, source_date)
    if not os.path.isdir(cache_dir):
        raise FileNotFoundError(
            f"No cached data for {source_date}. Run a real dashboard first "
            f"to populate .tmp/{source_date}/"
        )

    csvs = {}
    for fname in os.listdir(cache_dir):
        if fname.endswith(".csv"):
            key = fname.replace(".csv", "")
            csvs[key] = pd.read_csv(os.path.join(cache_dir, fname))

    logger.info("Loaded %d CSVs from %s for demo", len(csvs), source_date)

    scale = REVENUE_SCALE

    # ── OrderDetails ──
    if "OrderDetails" in csvs:
        od = csvs["OrderDetails"].copy()
        for col in ("Amount", "Tax", "Tip", "Total", "Discount Amount"):
            if col in od.columns:
                od[col] = (od[col] * scale).round(2)
        if "# of Guests" in od.columns:
            od["# of Guests"] = (od["# of Guests"] * scale).clip(lower=1).round().astype(int)
        csvs["OrderDetails"] = od

    # ── ItemSelectionDetails ──
    if "ItemSelectionDetails" in csvs:
        isd = csvs["ItemSelectionDetails"].copy()
        for col in ("Gross Price", "Net Price"):
            if col in isd.columns:
                isd[col] = (isd[col] * scale).round(2)
        csvs["ItemSelectionDetails"] = isd

    # ── ModifiersSelectionDetails ──
    if "ModifiersSelectionDetails" in csvs:
        msd = csvs["ModifiersSelectionDetails"].copy()
        for col in ("Gross Price", "Net Price"):
            if col in msd.columns:
                msd[col] = (msd[col] * scale).round(2)
        csvs["ModifiersSelectionDetails"] = msd

    # ── PaymentDetails ──
    if "PaymentDetails" in csvs:
        pd_ = csvs["PaymentDetails"].copy()
        for col in ("Amount", "Tip", "Total", "Swiped Card Amount",
                     "Keyed Card Amount", "Amount Tendered", "Refund Amount",
                     "Refund Tip Amount", "V/MC/D Fees"):
            if col in pd_.columns:
                pd_[col] = (pd_[col] * scale).round(2)
        csvs["PaymentDetails"] = pd_

    # ── TimeEntries ──
    # Keep employee names intact (labor computation uses MASTER_WAGES lookup).
    # Scale hours and pay down slightly for smaller-location feel.
    if "TimeEntries" in csvs:
        te = csvs["TimeEntries"].copy()
        for col in ("Total Hours", "Payable Hours", "Regular Hours", "Overtime Hours"):
            if col in te.columns:
                te[col] = (te[col] * 0.7).round(2)
        for col in ("Regular Pay", "Overtime Pay", "Total Pay"):
            if col in te.columns:
                te[col] = (te[col] * 0.7).round(2)
        if "Total Tips" in te.columns:
            te["Total Tips"] = (te["Total Tips"] * scale).round(2)
        if "Total Gratuity" in te.columns:
            te["Total Gratuity"] = (te["Total Gratuity"] * scale).round(2)
        csvs["TimeEntries"] = te

    # ── CheckDetails ──
    if "CheckDetails" in csvs:
        cd = csvs["CheckDetails"].copy()
        for col in ("Tax", "Amount", "Total"):
            if col in cd.columns:
                cd[col] = (cd[col] * scale).round(2)
        # Clear customer PII
        for col in ("Customer", "Customer Phone", "Customer Email", "Customer Id"):
            if col in cd.columns:
                cd[col] = ""
        csvs["CheckDetails"] = cd

    # ── AllItemsReport ──
    if "AllItemsReport" in csvs:
        air = csvs["AllItemsReport"].copy()
        for col in air.columns:
            if col.startswith("$") or "price" in col.lower() or "sales" in col.lower():
                if air[col].dtype in ("float64", "int64"):
                    air[col] = (air[col] * scale).round(2)
        # Scale quantities
        for col in air.columns:
            if "qty" in col.lower():
                if air[col].dtype in ("float64", "int64"):
                    air[col] = (air[col] * scale).round().astype(int).clip(lower=0)
        csvs["AllItemsReport"] = air

    # ── KitchenTimings — keep unchanged (timing patterns are fine) ──

    # ── CashEntries ──
    if "CashEntries" in csvs:
        ce = csvs["CashEntries"].copy()
        if "Amount" in ce.columns:
            ce["Amount"] = (ce["Amount"] * scale).round(2)
        csvs["CashEntries"] = ce

    logger.info("Demo data ready: revenue scaled to %.0f%%", scale * 100)
    return csvs
