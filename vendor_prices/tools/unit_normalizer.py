"""Normalize units across vendors for apples-to-apples price comparison.

Handles:
- Unit alias resolution (cs -> case, # -> lb, etc.)
- Pack size extraction from unit_detail strings
- Price per comparable unit computation ($/lb, $/each)
"""
from __future__ import annotations

import re

# Canonical unit aliases
UNIT_ALIASES = {
    "cs": "case",
    "ca": "case",
    "cases": "case",
    "ea": "each",
    "pc": "each",
    "pcs": "each",
    "piece": "each",
    "pieces": "each",
    "#": "lb",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "lb.": "lb",
    "gal": "gallon",
    "gallons": "gallon",
    "bg": "bag",
    "bags": "bag",
    "bx": "box",
    "boxes": "box",
    "pk": "pack",
    "packs": "pack",
    "ct": "pack",
    "dz": "dozen",
    "dzn": "dozen",
}

# Units that represent weight (can be converted to lb for comparison)
WEIGHT_UNITS = {"lb", "oz", "kg", "g"}

WEIGHT_TO_LB = {
    "lb": 1.0,
    "oz": 1.0 / 16,
    "kg": 2.20462,
    "g": 0.00220462,
}

# Units that represent volume (can be converted to gallon for comparison)
VOLUME_UNITS = {"gallon", "quart", "pint", "liter", "ml", "fl oz"}

VOLUME_TO_GAL = {
    "gallon": 1.0,
    "quart": 0.25,
    "pint": 0.125,
    "liter": 0.264172,
    "ml": 0.000264172,
    "fl oz": 0.0078125,
}


def normalize_unit(raw_unit: str) -> str:
    """Normalize a unit string to its canonical form."""
    if not raw_unit:
        return ""
    key = raw_unit.strip().lower().rstrip(".")
    return UNIT_ALIASES.get(key, key)


def parse_pack_size(unit_detail: str) -> dict:
    """Parse pack size info from a unit_detail string.

    Examples:
        "10 lb case" -> {"count": 1, "weight": 10, "weight_unit": "lb", "pack": "case"}
        "6/half gallon" -> {"count": 6, "volume": 0.5, "volume_unit": "gallon", "pack": "case"}
        "50 ct box" -> {"count": 50, "pack": "box"}
        "4x5 lb" -> {"count": 4, "weight": 5, "weight_unit": "lb", "pack": "case"}
        "12/1 lb" -> {"count": 12, "weight": 1, "weight_unit": "lb", "pack": "case"}

    Returns dict with parsed components.
    """
    if not unit_detail:
        return {}

    detail = unit_detail.strip().lower()
    result = {}

    # Pattern: "12/1 lb" or "6/10oz" or "4x5 lb"
    multi_match = re.match(
        r"(\d+)\s*[/x]\s*(\d+\.?\d*)\s*(oz|lb|lbs|#|gallon|gal|quart|pint|liter|ml|fl oz)?",
        detail,
    )
    if multi_match:
        result["count"] = int(multi_match.group(1))
        inner_qty = float(multi_match.group(2))
        inner_unit = normalize_unit(multi_match.group(3) or "")
        if inner_unit in WEIGHT_UNITS:
            result["weight"] = inner_qty * result["count"]
            result["weight_unit"] = inner_unit
        elif inner_unit in VOLUME_UNITS:
            result["volume"] = inner_qty * result["count"]
            result["volume_unit"] = inner_unit
        result["pack"] = "case"
        return result

    # Pattern: "half gallon" -> 0.5 gallon
    detail = re.sub(r"\bhalf\b", "0.5", detail)

    # Pattern: "10 lb case", "5lb bag", "20# case"
    weight_match = re.search(
        r"(\d+\.?\d*)\s*(lb|lbs|#|oz|kg|g)\b", detail
    )
    if weight_match:
        qty = float(weight_match.group(1))
        unit = normalize_unit(weight_match.group(2))
        result["weight"] = qty
        result["weight_unit"] = unit

    # Pattern: "0.5 gallon", "1 liter"
    vol_match = re.search(
        r"(\d+\.?\d*)\s*(gallon|gal|quart|pint|liter|ml|fl oz)\b", detail
    )
    if vol_match:
        qty = float(vol_match.group(1))
        unit = normalize_unit(vol_match.group(2))
        result["volume"] = qty
        result["volume_unit"] = unit

    # Pattern: "50 ct", "6 can", "12 piece", count prefix "6/"
    ct_match = re.search(r"(\d+)\s*(?:ct|can|cans|piece|pieces|pk)\b", detail)
    if ct_match:
        result["count"] = int(ct_match.group(1))

    # Detect pack type
    for pack_type in ("case", "bag", "box", "pack", "dozen", "tub", "pail", "bucket"):
        if pack_type in detail:
            result["pack"] = pack_type
            break

    return result


def compute_price_per_lb(price: float, unit: str, unit_detail: str) -> float | None:
    """Compute price per lb for weight-based items.

    Returns None if the item isn't weight-based or can't be computed.
    """
    unit = normalize_unit(unit)
    parsed = parse_pack_size(unit_detail)

    # Direct per-lb pricing
    if unit == "lb":
        return price

    # We have a weight from the pack size
    weight = parsed.get("weight")
    weight_unit = parsed.get("weight_unit", "lb")
    if weight and weight > 0:
        lbs = weight * WEIGHT_TO_LB.get(weight_unit, 1.0)
        if lbs > 0:
            return round(price / lbs, 2)

    return None


def compute_comparable_unit_price(price: float, unit: str, unit_detail: str) -> dict:
    """Compute normalized per-unit prices for comparison.

    Returns dict with:
        unit: normalized unit string
        price_per_lb: float or None
        price_per_unit: float (the base price / pack count, or just price)
        comparable_unit: the unit used for comparison ("lb", "each", "gallon")
        comparable_price: the normalized comparison price
    """
    unit = normalize_unit(unit)
    parsed = parse_pack_size(unit_detail)
    result = {
        "unit": unit,
        "price_per_lb": None,
        "price_per_unit": price,
        "comparable_unit": unit or "each",
        "comparable_price": price,
    }

    # Weight-based comparison
    price_per_lb = compute_price_per_lb(price, unit, unit_detail)
    if price_per_lb is not None:
        result["price_per_lb"] = price_per_lb
        result["comparable_unit"] = "lb"
        result["comparable_price"] = price_per_lb
        return result

    # Volume-based comparison (normalize to per-gallon)
    volume = parsed.get("volume")
    volume_unit = parsed.get("volume_unit", "gallon")
    if volume and volume > 0:
        gallons = volume * VOLUME_TO_GAL.get(volume_unit, 1.0)
        if gallons > 0:
            result["comparable_unit"] = "gallon"
            result["comparable_price"] = round(price / gallons, 2)
            return result

    # Count-based (per each/can/piece)
    count = parsed.get("count")
    if count and count > 0:
        result["price_per_unit"] = round(price / count, 2)
        result["comparable_unit"] = "each"
        result["comparable_price"] = result["price_per_unit"]
        return result

    return result


# Count-based size units (not weight/volume — just a per-unit count)
COUNT_SIZE_UNITS = {"can", "each", "piece", "ct"}


def enrich_items_with_units(items: list[dict]) -> list[dict]:
    """Add normalized unit data to extracted items.

    Adds to each item:
        unit_normalized, price_per_lb, comparable_unit, comparable_price,
        pack_qty, each_size, size_unit
    """
    for item in items:
        price = item.get("price", 0)
        unit = item.get("unit", "")
        unit_detail = item.get("unit_detail", "")

        # Use structured fields if already set (user edited in review UI),
        # otherwise parse from unit_detail text
        pack_qty = item.get("pack_qty")
        each_size = item.get("each_size")
        size_unit = item.get("size_unit")

        if pack_qty is None or each_size is None:
            parsed = parse_pack_size(unit_detail)
            if pack_qty is None:
                pack_qty = parsed.get("count", 1)
            if each_size is None and size_unit is None:
                # Derive each_size from parsed weight/volume
                if parsed.get("weight"):
                    total_weight = parsed["weight"]
                    size_unit = parsed.get("weight_unit", "lb")
                    each_size = round(total_weight / max(pack_qty, 1), 2)
                elif parsed.get("volume"):
                    total_vol = parsed["volume"]
                    size_unit = parsed.get("volume_unit", "gallon")
                    each_size = round(total_vol / max(pack_qty, 1), 2)

        if pack_qty is None:
            pack_qty = 1
        if each_size is None:
            each_size = 0
        if size_unit is None:
            size_unit = ""

        item["pack_qty"] = pack_qty
        item["each_size"] = each_size
        item["size_unit"] = size_unit

        # Handle count-based size units (can, each, piece, ct)
        if size_unit in COUNT_SIZE_UNITS and pack_qty and pack_qty > 0 and price > 0:
            item["unit_normalized"] = normalize_unit(unit)
            item["price_per_lb"] = None
            item["comparable_unit"] = size_unit
            item["comparable_price"] = round(price / pack_qty, 2)
            continue

        # Rebuild unit_detail from structured fields for comparable price calc
        if each_size and size_unit and pack_qty > 1:
            synth_detail = "%d/%s %s" % (pack_qty, str(each_size).rstrip("0").rstrip("."), size_unit)
        elif each_size and size_unit:
            synth_detail = "%s %s" % (str(each_size).rstrip("0").rstrip("."), size_unit)
        else:
            synth_detail = unit_detail

        comparison = compute_comparable_unit_price(price, unit, synth_detail)
        item["unit_normalized"] = comparison["unit"]
        item["price_per_lb"] = comparison["price_per_lb"]
        item["comparable_unit"] = comparison["comparable_unit"]
        item["comparable_price"] = comparison["comparable_price"]

    return items
