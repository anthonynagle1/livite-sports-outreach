"""Recipe data module — YAML-based recipe definitions with cost calculation.

Recipes reference ingredients from the Notion Items Master + Price Entries
databases to auto-calculate food cost per dish.
"""
from __future__ import annotations

import logging
import os
from copy import deepcopy
from datetime import date

import yaml

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RECIPES_PATH = os.path.join(_BASE_DIR, "data", "recipes.yaml")
MODIFIERS_PATH = os.path.join(_BASE_DIR, "data", "modifiers.yaml")


# ── YAML I/O ──


def load_recipes() -> list[dict]:
    """Load recipes from YAML. Returns list of recipe dicts."""
    if not os.path.exists(RECIPES_PATH):
        return []
    try:
        with open(RECIPES_PATH) as f:
            data = yaml.safe_load(f) or {}
        return data.get("recipes", [])
    except Exception as e:
        logger.error("Failed to load recipes: %s", e)
        return []


def save_recipes(recipes: list[dict]) -> None:
    """Save recipes list to YAML."""
    os.makedirs(os.path.dirname(RECIPES_PATH), exist_ok=True)
    with open(RECIPES_PATH, "w") as f:
        yaml.dump({"recipes": recipes}, f, default_flow_style=False,
                  sort_keys=False, allow_unicode=True)


def get_recipe(recipe_id: str) -> dict | None:
    """Get a single recipe by ID."""
    for r in load_recipes():
        if r.get("id") == recipe_id:
            return r
    return None


def save_recipe(recipe: dict) -> dict:
    """Create or update a single recipe. Returns the saved recipe."""
    recipes = load_recipes()
    rid = recipe.get("id", "")
    if not rid:
        # Generate simple ID from name
        rid = recipe.get("name", "recipe").lower()
        rid = "".join(c if c.isalnum() or c == "_" else "_" for c in rid)
        rid = rid.strip("_")[:50]
        # Ensure unique
        existing_ids = {r.get("id") for r in recipes}
        base = rid
        n = 1
        while rid in existing_ids:
            rid = f"{base}_{n}"
            n += 1
        recipe["id"] = rid

    # Update or append
    found = False
    for i, r in enumerate(recipes):
        if r.get("id") == rid:
            recipes[i] = recipe
            found = True
            break
    if not found:
        recipes.append(recipe)

    save_recipes(recipes)
    return recipe


def delete_recipe(recipe_id: str) -> bool:
    """Delete a recipe by ID. Returns True if found and deleted."""
    recipes = load_recipes()
    original_len = len(recipes)
    recipes = [r for r in recipes if r.get("id") != recipe_id]
    if len(recipes) < original_len:
        save_recipes(recipes)
        return True
    return False


def get_recipe_books() -> list[str]:
    """Return sorted list of unique recipe book names."""
    books = set()
    for r in load_recipes():
        b = r.get("book", "").strip()
        if b:
            books.add(b)
    return sorted(books)


# ── Modifier YAML I/O ──


def load_modifiers() -> list[dict]:
    """Load modifiers from YAML. Returns list of modifier dicts."""
    if not os.path.exists(MODIFIERS_PATH):
        return []
    try:
        with open(MODIFIERS_PATH) as f:
            data = yaml.safe_load(f) or {}
        return data.get("modifiers", [])
    except Exception as e:
        logger.error("Failed to load modifiers: %s", e)
        return []


def save_modifiers(modifiers: list[dict]) -> None:
    """Save modifiers list to YAML."""
    os.makedirs(os.path.dirname(MODIFIERS_PATH), exist_ok=True)
    with open(MODIFIERS_PATH, "w") as f:
        yaml.dump({"modifiers": modifiers}, f, default_flow_style=False,
                  sort_keys=False, allow_unicode=True)


def save_modifier(modifier: dict) -> dict:
    """Create or update a single modifier. Returns the saved modifier."""
    modifiers = load_modifiers()
    mid = modifier.get("id", "")
    if not mid:
        mid = modifier.get("name", "mod").lower()
        mid = "".join(c if c.isalnum() or c == "_" else "_" for c in mid)
        mid = mid.strip("_")[:50]
        existing_ids = {m.get("id") for m in modifiers}
        base = mid
        n = 1
        while mid in existing_ids:
            mid = "%s_%d" % (base, n)
            n += 1
        modifier["id"] = mid

    found = False
    for i, m in enumerate(modifiers):
        if m.get("id") == mid:
            modifiers[i] = modifier
            found = True
            break
    if not found:
        modifiers.append(modifier)

    save_modifiers(modifiers)
    return modifier


def delete_modifier(modifier_id: str) -> bool:
    """Delete a modifier by ID. Returns True if found and deleted."""
    modifiers = load_modifiers()
    original_len = len(modifiers)
    modifiers = [m for m in modifiers if m.get("id") != modifier_id]
    if len(modifiers) < original_len:
        save_modifiers(modifiers)
        return True
    return False


# ── Ingredient Price Lookup ──


def get_current_ingredient_prices(items_db_id: str,
                                  prices_db_id: str) -> dict:
    """Pull latest prices from Notion for all items.

    Returns dict keyed by lowercase item name:
        {name: {price, price_per_unit, unit, vendor, item_id}}
    """
    import sys
    sys.path.insert(0, os.path.join(_BASE_DIR, "vendor_prices", "tools"))
    from vendor_prices.tools import notion_sync

    items = notion_sync.get_all_items(items_db_id)
    entries = notion_sync.get_price_entries(prices_db_id)

    # Build item name lookup
    item_names = {}
    for item in items:
        item_names[item["id"]] = item.get("name", "")

    # Group entries by item, keep most recent by date
    best = {}  # item_name_lower -> entry dict
    for e in entries:
        item_id = e.get("item_relation_id", "")
        item_name = item_names.get(item_id, "")
        if not item_name:
            continue

        key = item_name.lower()
        entry_date = e.get("date", "")

        if key not in best or entry_date > best[key].get("_date", ""):
            best[key] = {
                "price": e.get("price", 0) or 0,
                "price_per_unit": e.get("price_per_unit", 0) or 0,
                "unit": e.get("unit", ""),
                "vendor": e.get("vendor", ""),
                "item_id": item_id,
                "item_name": item_name,
                "_date": entry_date,
            }

    # Clean up internal field
    for v in best.values():
        v.pop("_date", None)

    return best


def get_items_list(items_db_id: str) -> list[dict]:
    """Get simplified items list for autocomplete in recipe editor.

    Returns list of {name, category, unit, unit_size}.
    """
    import sys
    sys.path.insert(0, os.path.join(_BASE_DIR, "vendor_prices", "tools"))
    from vendor_prices.tools import notion_sync

    items = notion_sync.get_all_items(items_db_id)
    return [
        {
            "name": item.get("name", ""),
            "category": item.get("category", ""),
            "unit": item.get("unit", ""),
            "unit_size": item.get("unit_size", ""),
        }
        for item in items
        if item.get("active", True)
    ]


# ── Cost Calculation ──


def calculate_recipe_cost(recipe: dict,
                          prices: dict,
                          all_recipes: list[dict] | None = None) -> dict:
    """Calculate cost for a recipe given current ingredient prices.

    Supports sub-recipes: an ingredient with 'sub_recipe' key instead of 'item'
    will recursively resolve the sub-recipe's portion cost.

    Args:
        recipe: Recipe dict with 'ingredients' list
        prices: Dict from get_current_ingredient_prices()
        all_recipes: Full recipe list (for sub-recipe lookups). If None, loads from YAML.

    Returns:
        {
            batch_cost, portion_cost, food_cost_pct, menu_price,
            ingredients: [{name, quantity, uom, unit_cost, line_cost,
                          yield_pct, vendor, matched, is_sub_recipe}]
        }
    """
    from tools.recipes.units import convert, can_convert

    menu_price = recipe.get("menu_price", 0) or 0
    portions = recipe.get("portions", 1) or 1
    ingredients_out = []
    batch_cost = 0.0

    if all_recipes is None:
        all_recipes = load_recipes()
    recipes_by_id = {r.get("id", ""): r for r in all_recipes}

    for ing in recipe.get("ingredients", []):
        sub_recipe_id = ing.get("sub_recipe", "")
        item_name = ing.get("item", "")
        quantity = ing.get("quantity", 0) or 0
        yield_pct = ing.get("yield_pct", 1.0) or 1.0
        uom = ing.get("uom", "")

        # ── Sub-recipe handling ──
        if sub_recipe_id:
            sub = recipes_by_id.get(sub_recipe_id)
            if sub:
                sub_cost = calculate_recipe_cost(sub, prices, all_recipes)
                unit_cost = sub_cost.get("portion_cost", 0)
                line_cost = (unit_cost * quantity / yield_pct) if yield_pct > 0 else 0
                ingredients_out.append({
                    "name": sub.get("name", sub_recipe_id),
                    "quantity": quantity,
                    "uom": "portion",
                    "unit_cost": round(unit_cost, 4),
                    "line_cost": round(line_cost, 2),
                    "yield_pct": yield_pct,
                    "vendor": "",
                    "matched": True,
                    "is_sub_recipe": True,
                })
                batch_cost += line_cost
            else:
                ingredients_out.append({
                    "name": sub_recipe_id,
                    "quantity": quantity,
                    "uom": "portion",
                    "unit_cost": 0,
                    "line_cost": 0,
                    "yield_pct": yield_pct,
                    "vendor": "",
                    "matched": False,
                    "is_sub_recipe": True,
                })
            continue

        # ── Standard item ──
        key = item_name.lower()
        price_info = prices.get(key, {})
        matched = bool(price_info)

        # Use price_per_unit if available, else raw price
        unit_cost = price_info.get("price_per_unit", 0) or 0
        price_unit = price_info.get("unit", "")
        if not unit_cost:
            unit_cost = price_info.get("price", 0) or 0

        # ── Unit conversion ──
        # If recipe uom differs from price unit, try to convert
        if matched and uom and price_unit and uom.lower() != price_unit.lower():
            if can_convert(price_unit, uom):
                try:
                    # unit_cost is per price_unit, convert to per recipe_uom
                    # e.g., $2.50/lb -> $0.15625/oz
                    factor = convert(1, price_unit, uom)
                    unit_cost = unit_cost / factor
                except ValueError:
                    pass  # fall through with original unit_cost

        # Line cost = unit_cost * quantity / yield
        line_cost = (unit_cost * quantity / yield_pct) if yield_pct > 0 else 0

        ingredients_out.append({
            "name": item_name,
            "quantity": quantity,
            "uom": uom,
            "unit_cost": round(unit_cost, 4),
            "line_cost": round(line_cost, 2),
            "yield_pct": yield_pct,
            "vendor": price_info.get("vendor", ""),
            "matched": matched,
            "is_sub_recipe": False,
        })
        batch_cost += line_cost

    portion_cost = batch_cost / portions if portions > 0 else 0
    food_cost_pct = (portion_cost / menu_price * 100) if menu_price > 0 else 0

    return {
        "batch_cost": round(batch_cost, 2),
        "portion_cost": round(portion_cost, 2),
        "food_cost_pct": round(food_cost_pct, 1),
        "menu_price": menu_price,
        "portions": portions,
        "ingredients": ingredients_out,
    }


def calculate_all_recipes(items_db_id: str,
                          prices_db_id: str) -> list[dict]:
    """Load all recipes and calculate costs for each.

    Returns list of recipe dicts with cost data merged in.
    """
    recipes = load_recipes()
    if not recipes:
        return []

    prices = get_current_ingredient_prices(items_db_id, prices_db_id)

    results = []
    for recipe in recipes:
        cost_data = calculate_recipe_cost(recipe, prices)
        merged = deepcopy(recipe)
        merged["cost"] = cost_data
        results.append(merged)

    return results


def calculate_all_modifiers(items_db_id: str,
                            prices_db_id: str) -> list[dict]:
    """Load all modifiers and calculate costs for each.

    Returns list of modifier dicts with cost data merged in.
    """
    modifiers = load_modifiers()
    if not modifiers:
        return []

    prices = get_current_ingredient_prices(items_db_id, prices_db_id)

    results = []
    for mod in modifiers:
        # Reuse recipe cost calc (modifiers have same ingredient structure)
        fake_recipe = {
            "ingredients": mod.get("ingredients", []),
            "portions": 1,
            "menu_price": mod.get("menu_price", 0),
        }
        cost_data = calculate_recipe_cost(fake_recipe, prices)
        merged = deepcopy(mod)
        merged["cost"] = cost_data
        results.append(merged)

    return results
