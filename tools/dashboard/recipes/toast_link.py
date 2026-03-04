"""Toast POS to recipe linkage — compute theoretical food cost from sales data.

Joins Toast sales (ItemSelectionDetails) with recipe costs to compute
actual food cost per menu item sold, replacing the flat 35% assumption.
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_toast_sales(date_str: str) -> list[dict]:
    """Pull menu item sales from Toast for a given date.

    Args:
        date_str: Date in YYYYMMDD format

    Returns:
        List of {menu_item, menu_group, quantity, amount} dicts
    """
    import pandas as pd
    from tools.fetch_toast_data import fetch_day

    data = fetch_day(date_str)
    if not data or "ItemSelectionDetails" not in data:
        return []

    isd = data["ItemSelectionDetails"]
    if isd.empty:
        return []

    # Filter voided
    if "Voided" in isd.columns:
        isd = isd[isd["Voided"] != True]  # noqa: E712

    # Aggregate by menu item
    sales = []
    grouped = isd.groupby("Menu Item", dropna=False)
    for item_name, group in grouped:
        if not item_name or str(item_name).lower() == "nan":
            continue
        qty = len(group)
        amount = group["Gross Price"].sum() if "Gross Price" in group.columns else 0
        menu_group = ""
        if "Menu Group" in group.columns:
            mg = group["Menu Group"].mode()
            menu_group = str(mg.iloc[0]) if len(mg) > 0 else ""

        sales.append({
            "menu_item": str(item_name).strip(),
            "menu_group": menu_group,
            "quantity": qty,
            "amount": round(float(amount), 2),
        })

    return sales


def compute_theoretical_food_cost(
    date_str: str,
    recipes: list[dict],
    prices: dict,
    fallback_pct: float = 0.35,
) -> dict:
    """Compute theoretical food cost for a day by joining sales x recipe costs.

    Args:
        date_str: Date in YYYYMMDD format
        recipes: List of recipe dicts (with 'toast_menu_name' field)
        prices: Dict from get_current_ingredient_prices()
        fallback_pct: Food cost % to assume for items without recipes (default 35%)

    Returns:
        {
            total_sales, total_food_cost, food_cost_pct,
            costed_sales, costed_food_cost, costed_pct,
            uncosted_sales, uncosted_food_cost,
            coverage_pct,
            items: [{menu_item, quantity, sales, portion_cost,
                     food_cost, method, recipe_id}]
        }
    """
    from tools.recipes.data import calculate_recipe_cost

    sales = get_toast_sales(date_str)
    if not sales:
        return {"total_sales": 0, "total_food_cost": 0, "food_cost_pct": 0,
                "items": [], "coverage_pct": 0}

    # Build recipe lookup: toast_menu_name (lowercase) -> recipe
    recipe_map = {}
    for r in recipes:
        toast_name = r.get("toast_menu_name", "")
        if toast_name:
            recipe_map[toast_name.lower().strip()] = r

    items_out = []
    total_sales = 0.0
    costed_sales = 0.0
    costed_food_cost = 0.0
    uncosted_sales = 0.0
    uncosted_food_cost = 0.0

    for sale in sales:
        item_name = sale["menu_item"]
        qty = sale["quantity"]
        amount = sale["amount"]
        total_sales += amount

        # Try to match to a recipe
        recipe = recipe_map.get(item_name.lower().strip())
        if recipe:
            cost_data = calculate_recipe_cost(recipe, prices,
                                              all_recipes=recipes)
            portion_cost = cost_data.get("portion_cost", 0)
            food_cost = portion_cost * qty
            costed_sales += amount
            costed_food_cost += food_cost
            items_out.append({
                "menu_item": item_name,
                "quantity": qty,
                "sales": amount,
                "portion_cost": round(portion_cost, 2),
                "food_cost": round(food_cost, 2),
                "method": "recipe",
                "recipe_id": recipe.get("id", ""),
            })
        else:
            # Fallback: flat percentage
            food_cost = amount * fallback_pct
            uncosted_sales += amount
            uncosted_food_cost += food_cost
            items_out.append({
                "menu_item": item_name,
                "quantity": qty,
                "sales": amount,
                "portion_cost": 0,
                "food_cost": round(food_cost, 2),
                "method": "estimate",
                "recipe_id": "",
            })

    total_food_cost = costed_food_cost + uncosted_food_cost
    food_cost_pct = (total_food_cost / total_sales * 100) if total_sales > 0 else 0
    costed_pct = (costed_food_cost / costed_sales * 100) if costed_sales > 0 else 0
    coverage_pct = (costed_sales / total_sales * 100) if total_sales > 0 else 0

    # Sort by food_cost descending (highest impact first)
    items_out.sort(key=lambda x: x["food_cost"], reverse=True)

    return {
        "total_sales": round(total_sales, 2),
        "total_food_cost": round(total_food_cost, 2),
        "food_cost_pct": round(food_cost_pct, 1),
        "costed_sales": round(costed_sales, 2),
        "costed_food_cost": round(costed_food_cost, 2),
        "costed_pct": round(costed_pct, 1),
        "uncosted_sales": round(uncosted_sales, 2),
        "uncosted_food_cost": round(uncosted_food_cost, 2),
        "coverage_pct": round(coverage_pct, 1),
        "items": items_out,
    }


def get_uncovered_items(date_str: str, recipes: list[dict]) -> list[dict]:
    """Find menu items sold but not covered by recipes, ranked by sales volume.

    Useful for prioritizing which recipes to add next.
    """
    sales = get_toast_sales(date_str)
    if not sales:
        return []

    recipe_names = set()
    for r in recipes:
        toast_name = r.get("toast_menu_name", "")
        if toast_name:
            recipe_names.add(toast_name.lower().strip())

    uncovered = []
    for sale in sales:
        if sale["menu_item"].lower().strip() not in recipe_names:
            uncovered.append({
                "menu_item": sale["menu_item"],
                "menu_group": sale["menu_group"],
                "quantity": sale["quantity"],
                "sales": sale["amount"],
            })

    uncovered.sort(key=lambda x: x["sales"], reverse=True)
    return uncovered
