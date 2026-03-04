"""Main orchestrator: compute_all_metrics entry point."""

from datetime import datetime
import pandas as pd

from .utils import _safe_div, _safe_numeric, FOOD_COST_PCT, calc_revenue, calc_fees
from .revenue import compute_revenue_metrics
from .orders import compute_order_intelligence
from .baskets import compute_basket_analysis
from .modifiers import compute_modifier_analysis
from .kitchen import compute_kitchen_metrics
from .labor import compute_labor_metrics
from .payments import compute_payment_metrics
from .customers import compute_customer_metrics
from .insights import detect_anomalies
from .weather import compute_weather_metrics, get_events_for_date

def compute_all_metrics(data, date, skip_weather=False):
    """Orchestrate all metric computations from a get_daily_data() result.

    Args:
        data: Dict of {csv_name_without_ext: DataFrame} from get_daily_data().
        date: datetime object for the analysis date.
        skip_weather: If True, skip weather API calls (used for range dashboards
                      where weather is not aggregated and API calls are expensive).

    Returns master dict with all metric sections, handling missing CSVs
    gracefully by returning None for unavailable sections.
    """
    date_str = date.strftime("%Y%m%d")
    date_display = date.strftime("%m/%d/%Y")
    day_of_week = date.strftime("%A")

    result = {
        "date_str": date_str,
        "date_display": date_display,
        "day_of_week": day_of_week,
    }

    # ── Revenue ──
    if 'OrderDetails' in data:
        try:
            result["revenue"] = compute_revenue_metrics(data['OrderDetails'])
        except Exception as e:
            result["revenue"] = None
            result["_revenue_error"] = str(e)
    else:
        result["revenue"] = None

    # Derive totals for downstream use
    revenue_total = 0
    total_orders = 0
    if result["revenue"]:
        revenue_total = result["revenue"].get("toast_total", 0)
        total_orders = result["revenue"].get("total_orders", 0)

    # ── Order intelligence ──
    if 'OrderDetails' in data and 'ItemSelectionDetails' in data:
        try:
            result["orders"] = compute_order_intelligence(
                data['OrderDetails'],
                data['ItemSelectionDetails'],
                data.get('AllItemsReport'),
                data.get('CheckDetails'),
            )
        except Exception as e:
            result["orders"] = None
            result["_orders_error"] = str(e)
    else:
        result["orders"] = None

    # ── Basket analysis ──
    if 'ItemSelectionDetails' in data:
        try:
            result["baskets"] = compute_basket_analysis(data['ItemSelectionDetails'])
        except Exception as e:
            result["baskets"] = None
            result["_baskets_error"] = str(e)
    else:
        result["baskets"] = None

    # ── Modifier analysis ──
    if 'ModifiersSelectionDetails' in data:
        try:
            result["modifiers"] = compute_modifier_analysis(
                data['ModifiersSelectionDetails'],
                item_selection_details=data.get('ItemSelectionDetails')
            )
        except Exception as e:
            result["modifiers"] = None
            result["_modifiers_error"] = str(e)
    else:
        result["modifiers"] = None

    # ── Kitchen metrics ──
    if 'KitchenTimings' in data and 'OrderDetails' in data:
        try:
            result["kitchen"] = compute_kitchen_metrics(
                data['KitchenTimings'],
                data['OrderDetails'],
            )
        except Exception as e:
            result["kitchen"] = None
            result["_kitchen_error"] = str(e)
    else:
        result["kitchen"] = None

    # ── Labor metrics ──
    if 'TimeEntries' in data and 'OrderDetails' in data:
        try:
            result["labor"] = compute_labor_metrics(
                data['TimeEntries'],
                data['OrderDetails'],
                revenue_total,
            )
        except Exception as e:
            result["labor"] = None
            result["_labor_error"] = str(e)
    else:
        result["labor"] = None

    # ── Payment metrics ──
    if 'PaymentDetails' in data:
        try:
            result["payments"] = compute_payment_metrics(
                data['PaymentDetails'],
                data.get('CashEntries'),
            )
        except Exception as e:
            result["payments"] = None
            result["_payments_error"] = str(e)
    else:
        result["payments"] = None

    # ── Customer metrics ──
    if 'CheckDetails' in data and 'PaymentDetails' in data:
        try:
            result["customers"] = compute_customer_metrics(
                data['CheckDetails'],
                data['PaymentDetails'],
                data.get('ItemSelectionDetails'),
                data.get('OrderDetails'),
            )
        except Exception as e:
            result["customers"] = None
            result["_customers_error"] = str(e)
    else:
        result["customers"] = None

    # ── Weather metrics ──
    if skip_weather:
        result["weather"] = None
    else:
        try:
            from fetch_weather_data import get_daily_weather, get_weather_range
            weather_data = get_daily_weather(date)
            if weather_data:
                # Get historical weather + revenue for correlations (last 30 days)
                hist_start = date - pd.Timedelta(days=30)
                hist_weather = get_weather_range(hist_start, date - pd.Timedelta(days=1))

                # Build historical revenue from cached metrics
                hist_revenue = []
                try:
                    from metrics_cache import get_cached_metrics
                    for i in range(1, 31):
                        hd = (date - pd.Timedelta(days=i)).strftime("%Y%m%d")
                        cached = get_cached_metrics(hd)
                        if cached and cached.get('revenue'):
                            hist_revenue.append({
                                'date': hd,
                                'revenue': cached['revenue'].get('toast_total', 0)
                            })
                except Exception:
                    pass

                result["weather"] = compute_weather_metrics(
                    weather_data, date_str, hist_weather, hist_revenue
                )
            else:
                result["weather"] = None
        except Exception as e:
            result["weather"] = None
            result["_weather_error"] = str(e)

    # ── Staffing-demand overlay ──
    # Merges labor staffing_by_halfhour with revenue hourly data
    staffing_demand_overlay = []
    if result.get("labor") and result.get("revenue"):
        hourly_rev = result["revenue"].get("hourly", [])
        staffing = result["labor"].get("staffing_by_halfhour", [])
        hourly_rev_map = {h["hour"]: h for h in hourly_rev}
        for entry in staffing:
            label = entry.get("time", "")
            staff_count = entry.get("staff_count", 0)
            order_count = entry.get("order_count", 0)
            # Parse hour from time string (e.g. "07:00 AM" → 7)
            h = None
            try:
                from datetime import datetime as _dt
                h = _dt.strptime(label.strip(), "%I:%M %p").hour
            except Exception:
                h = None
            hr = hourly_rev_map.get(h, {}) if h is not None else {}
            h_rev = hr.get("revenue", 0)
            staffing_demand_overlay.append({
                "hour": h,
                "label": label,
                "staff_count": staff_count,
                "revenue": h_rev,
                "orders": order_count,
                "orders_per_staff": round(_safe_div(order_count, staff_count), 1),
                "rev_per_staff": round(_safe_div(h_rev, staff_count), 2),
            })
    result["staffing_demand_overlay"] = staffing_demand_overlay

    # ── Anomaly detection ──
    try:
        result["anomalies"] = detect_anomalies(result)
    except Exception as e:
        result["anomalies"] = []
        result["_anomalies_error"] = str(e)

    # ── Raw totals for comparison ──
    result["toast_total"] = revenue_total
    result["total_orders"] = total_orders
    result["total_labor"] = result["labor"]["total_labor"] if result.get("labor") else 0
    result["total_hours"] = result["labor"]["total_hours"] if result.get("labor") else 0
    result["tds_fees"] = (
        result["payments"]["tds_fees"] if result.get("payments") else 0
    )

    # ── Food Cost: theoretical (recipe-based) with flat 35% fallback ──
    food_cost_data = _compute_food_cost(date_str, revenue_total)
    result["food_cost"] = food_cost_data["food_cost"]
    result["food_cost_pct"] = food_cost_data["food_cost_pct"]
    result["food_cost_method"] = food_cost_data["method"]
    result["food_cost_coverage"] = food_cost_data.get("coverage_pct", 0)

    # Prime cost = food + labor (target: under 60%)
    total_labor = result["total_labor"]
    prime_cost = result["food_cost"] + total_labor
    prime_cost_pct = round(
        prime_cost / revenue_total * 100, 1) if revenue_total > 0 else 0
    result["prime_cost"] = round(prime_cost, 2)
    result["prime_cost_pct"] = prime_cost_pct

    return result


def _compute_food_cost(date_str: str, revenue_total: float) -> dict:
    """Compute food cost — try recipe-based theoretical, fall back to flat 35%.

    Returns dict with food_cost, food_cost_pct, method, and coverage_pct.
    """
    import os

    items_db = os.getenv("NOTION_ITEMS_DB_ID", "")
    prices_db = os.getenv("NOTION_PRICES_DB_ID", "")

    if items_db and prices_db:
        try:
            from tools.recipes.data import load_recipes, get_current_ingredient_prices
            from tools.recipes.toast_link import compute_theoretical_food_cost

            recipes = load_recipes()
            if recipes:
                prices = get_current_ingredient_prices(items_db, prices_db)
                tc = compute_theoretical_food_cost(date_str, recipes, prices)
                if tc.get("total_sales", 0) > 0:
                    return {
                        "food_cost": tc["total_food_cost"],
                        "food_cost_pct": tc["food_cost_pct"],
                        "method": "theoretical",
                        "coverage_pct": tc.get("coverage_pct", 0),
                        "costed_pct": tc.get("costed_pct", 0),
                    }
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "Theoretical food cost failed, using 35%%: %s", e)

    # Fallback: flat 35%
    food_cost = round(revenue_total * FOOD_COST_PCT, 2)
    food_cost_pct = round(FOOD_COST_PCT * 100, 1)
    return {
        "food_cost": food_cost,
        "food_cost_pct": food_cost_pct,
        "method": "estimate",
        "coverage_pct": 0,
    }
