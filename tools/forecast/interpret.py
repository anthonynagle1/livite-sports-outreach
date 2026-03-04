"""Automatic interpretation of forecast misses.

Analyzes the per-channel variance between predicted and actual revenue to
generate a human-readable explanation for why a day may have missed.

Examples:
- "⛔ Likely closed (snow day, emergency, etc.)"
- "Large unplanned catering order"
- "Unusually strong in-store traffic · Delivery underperformed"
- "Catering cancelled or no-show"
"""

from __future__ import annotations


def generate_day_interpretation(
    predicted: float | None,
    actual: float | None,
    channel_predicted: dict | None = None,
    channel_actual: dict | None = None,
) -> str | None:
    """Return a short human-readable note explaining a forecast miss.

    Returns None when the variance is within normal range (±12%) and no
    clear channel signal is detected.

    Args:
        predicted:         Total predicted revenue.
        actual:            Total actual revenue.
        channel_predicted: Dict with keys 'instore', 'delivery', 'catering'.
        channel_actual:    Dict with keys 'instore', 'delivery', 'catering'.
    """
    if actual is None or predicted is None or predicted <= 0:
        return None

    variance_pct = (actual - predicted) / predicted * 100

    # ── Closure / minimal operations ──────────────────────────────────────────
    if actual < 50:
        return "⛔ Likely closed or minimal operations"

    # ── Channel-level analysis ─────────────────────────────────────────────────
    ch_pred = channel_predicted or {}
    ch_act = channel_actual or {}

    in_pred = float(ch_pred.get("instore", 0) or 0)
    in_act = float(ch_act.get("instore", 0) or 0)
    del_pred = float(ch_pred.get("delivery", 0) or 0)
    del_act = float(ch_act.get("delivery", 0) or 0)
    cat_pred = float(ch_pred.get("catering", 0) or 0)
    cat_act = float(ch_act.get("catering", 0) or 0)

    in_var = in_act - in_pred
    del_var = del_act - del_pred
    cat_var = cat_act - cat_pred

    clues = []

    # Catering — single large orders can swing totals dramatically
    if cat_var > 250:
        clues.append("Large unplanned catering order")
    elif cat_var < -200 and cat_pred > 80:
        clues.append("Catering cancelled or smaller than expected")

    # In-store traffic
    in_var_pct = (in_var / in_pred * 100) if in_pred > 0 else 0
    if in_act < 80 and in_pred > 200:
        clues.append("In-store essentially closed")
    elif in_var_pct > 25:
        clues.append("Unusually strong in-store traffic")
    elif in_var_pct < -25:
        clues.append("Below-normal in-store traffic")

    # Delivery
    del_var_pct = (del_var / del_pred * 100) if del_pred > 0 else 0
    if del_var_pct > 35:
        clues.append("Delivery surge")
    elif del_var_pct < -30:
        clues.append("Delivery underperformed")

    if clues:
        return " · ".join(clues)

    # No specific channel identified — only flag if variance is notable
    if abs(variance_pct) < 12:
        return None
    if variance_pct > 12:
        return "Busier than typical"
    return "Slower than typical"
