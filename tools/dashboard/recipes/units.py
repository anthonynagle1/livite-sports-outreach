"""Unit conversion engine for recipe costing.

Handles weight, volume, and count conversions used in recipe ingredients.
All conversions within the same measure class (weight-to-weight, volume-to-volume)
are universal. Cross-class conversions (weight-to-volume) require item-specific
density factors and are not supported here.

Usage:
    from tools.recipes.units import convert, can_convert

    oz_value = convert(2, "lb", "oz")       # -> 32.0
    cups = convert(16, "fl oz", "cup")      # -> 2.0
    ok = can_convert("lb", "oz")            # -> True
    ok = can_convert("lb", "fl oz")         # -> False (cross-class)
"""
from __future__ import annotations

# ── Base Units ──
# Everything converts through a base unit per measure class:
#   Weight -> oz
#   Volume -> fl_oz
#   Count  -> each

# Weight conversions to oz
_WEIGHT_TO_OZ = {
    "oz": 1.0,
    "lb": 16.0,
    "lbs": 16.0,
    "g": 0.035274,
    "gram": 0.035274,
    "grams": 0.035274,
    "kg": 35.274,
    "kilogram": 35.274,
}

# Volume conversions to fl oz
_VOLUME_TO_FLOZ = {
    "fl oz": 1.0,
    "fl_oz": 1.0,
    "floz": 1.0,
    "tsp": 0.166667,
    "teaspoon": 0.166667,
    "tbsp": 0.5,
    "tablespoon": 0.5,
    "cup": 8.0,
    "cups": 8.0,
    "pint": 16.0,
    "pt": 16.0,
    "quart": 32.0,
    "qt": 32.0,
    "gallon": 128.0,
    "gal": 128.0,
    "liter": 33.814,
    "l": 33.814,
    "ml": 0.033814,
    "milliliter": 0.033814,
}

# Count conversions to each
_COUNT_TO_EACH = {
    "each": 1.0,
    "ea": 1.0,
    "piece": 1.0,
    "pc": 1.0,
    "dozen": 12.0,
    "dz": 12.0,
    "bunch": 1.0,
    "head": 1.0,
    "slice": 1.0,
}


def _normalize_unit(unit: str) -> str:
    """Normalize unit string for lookup."""
    return unit.lower().strip().rstrip("s") if unit else ""


def _get_class_and_factor(unit: str) -> tuple[str, float] | None:
    """Get measure class and conversion factor to base unit.

    Returns (class_name, factor_to_base) or None if not recognized.
    """
    u = _normalize_unit(unit)

    # Check weight
    for key, factor in _WEIGHT_TO_OZ.items():
        if u == key or u == key.rstrip("s"):
            return ("weight", factor)

    # Check volume
    for key, factor in _VOLUME_TO_FLOZ.items():
        if u == key or u == key.rstrip("s"):
            return ("volume", factor)

    # Check count
    for key, factor in _COUNT_TO_EACH.items():
        if u == key or u == key.rstrip("s"):
            return ("count", factor)

    return None


def can_convert(from_unit: str, to_unit: str) -> bool:
    """Check if conversion between two units is possible."""
    a = _get_class_and_factor(from_unit)
    b = _get_class_and_factor(to_unit)
    if a is None or b is None:
        return False
    return a[0] == b[0]


def convert(qty: float, from_unit: str, to_unit: str) -> float:
    """Convert a quantity from one unit to another.

    Only works within the same measure class (weight, volume, count).
    Raises ValueError if units are incompatible or unrecognized.

    Examples:
        convert(2, "lb", "oz")      -> 32.0
        convert(1, "gallon", "cup") -> 16.0
        convert(6, "oz", "lb")      -> 0.375
    """
    if not qty:
        return 0.0

    from_info = _get_class_and_factor(from_unit)
    to_info = _get_class_and_factor(to_unit)

    if from_info is None:
        raise ValueError("Unrecognized unit: %s" % from_unit)
    if to_info is None:
        raise ValueError("Unrecognized unit: %s" % to_unit)

    from_class, from_factor = from_info
    to_class, to_factor = to_info

    if from_class != to_class:
        raise ValueError(
            "Cannot convert between %s (%s) and %s (%s)" % (
                from_unit, from_class, to_unit, to_class))

    # Convert: from_unit -> base -> to_unit
    base_qty = qty * from_factor
    return base_qty / to_factor


def get_cost_per_recipe_unit(
    purchase_price: float,
    purchase_unit: str,
    pack_qty: float,
    each_size: float,
    size_unit: str,
    recipe_unit: str,
) -> float:
    """Calculate cost per recipe unit from vendor pricing.

    Works through the chain: Purchase Price -> Pack -> Each -> Recipe Unit

    Args:
        purchase_price: Price per purchase unit (e.g., $42.50 per case)
        purchase_unit: What you buy (e.g., "case")
        pack_qty: Number of eaches in the purchase unit (e.g., 6 bags)
        each_size: Size of each unit (e.g., 10 lb per bag)
        size_unit: Unit of each_size (e.g., "lb")
        recipe_unit: Unit used in recipe (e.g., "oz")

    Returns:
        Cost per recipe unit (e.g., $0.044 per oz)
    """
    if not purchase_price or not pack_qty or not each_size:
        return 0.0

    # Total quantity in size_unit
    total_in_size_unit = pack_qty * each_size

    # Cost per size_unit
    cost_per_size_unit = purchase_price / total_in_size_unit

    # Convert to recipe_unit if different
    if can_convert(size_unit, recipe_unit):
        # How many recipe_units in 1 size_unit?
        recipe_units_per_size = convert(1, size_unit, recipe_unit)
        return cost_per_size_unit / recipe_units_per_size
    elif _normalize_unit(size_unit) == _normalize_unit(recipe_unit):
        return cost_per_size_unit
    else:
        # Can't convert — return cost per size_unit as best guess
        return cost_per_size_unit
