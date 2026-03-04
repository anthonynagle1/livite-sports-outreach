"""Menu coverage analysis — maps Toast POS sales to recipe database.

Scans cached Toast ItemSelectionDetails CSVs, consolidates name variants,
categorizes items, and cross-references with recipes.yaml to show coverage.
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from pathlib import Path

import yaml

DATA_DIR = Path(".tmp")
RECIPE_FILE = Path("data/recipes.yaml")

# ── Toast name normalization ──
# Maps known Toast POS name variants → canonical name.
# Keys are lowercase; lookup is case-insensitive.
_NAME_MAP = {
    # Wraps
    "buff chick quesadilla": "Buffalo Chicken Quesadilla",
    "roasted chicken and corn": "Roasted Chicken and Corn Wrap",
    "greek wrap": "Greek Chicken Wrap",
    "vegan chickpea tuna": "Vegan Chickpea Tuna Wrap",
    "buffalotarian-vegetarian": "Buffalotarian Wrap",
    "buffalotarian": "Buffalotarian Wrap",
    "caprese grilled cheese-vegetarian": "Caprese Panini - Vegetarian",
    "caprese panini - vegetarian": "Caprese Panini - Vegetarian",
    "the veggie garden-vegan": "The Veggie Garden Wrap - Vegan",
    "the veggie garden - vegan": "The Veggie Garden Wrap - Vegan",
    "food pantry quesadilla": "Food Pantry Quesadilla",
    "chipotle chicken wrap": "Chipotle Chicken Wrap",
    # Salads
    "buff chick salad - gf": "Buffalo Chicken Salad - GF",
    "plant based thai peanut salad -gf": "Plant-Based Thai Peanut Salad - GF",
    "plant-based thai peanut salad -gf": "Plant-Based Thai Peanut Salad - GF",
    "plant-based thai peanut salad - gf": "Plant-Based Thai Peanut Salad - GF",
    "berry berry avocado - vegetarian/gf": "Berry Berry Avocado Salad - Vegetarian/GF",
    "berry berry avocado-vegetarian/gf": "Berry Berry Avocado Salad - Vegetarian/GF",
    "berry berry avocado salad - vegetarian/gf": "Berry Berry Avocado Salad - Vegetarian/GF",
    "livite house salad - gf": "Livite House Salad - GF",
    "new! spicy peanut noodle bowl": "Spicy Peanut Noodle Bowl",
    "thai peanut salad": "Plant-Based Thai Peanut Salad - GF",
    # Soup
    "lentil soup- vegan and gluten free": "12oz Cup of Lentil Soup",
    "quart of lentil soup": "Quart of Lentil Soup",
    # Smoothies
    "pb &j": "PB &J",
    "cinnamon roll - seasonal": "Cinnamon Roll",
    "winter special - candy cane": "Candy Cane",
    "shamrock shake - march special": "Shamrock Shake",
    "winter special - gingerbread": "Gingerbread",
    "make your own smoothie w/ protein": "Make Your Own Smoothie",
    "make your own smoothie-no protein": "Make Your Own Smoothie",
    # Matcha
    "strawberry & cream iced matcha": "Strawberry Cream Matcha",
    # Juices
    "new! heatwave": "Heatwave",
    # Specials
    "special! harvest chicken salad": "Harvest Chicken Salad Wrap",
    "special! traditional chicken salad": "Traditional Chicken Salad Wrap",
    "special! raspberry limeade": "Raspberry Limeade",
}

# ── Category assignment ──
# Canonical name patterns → category.
# Checked in order; first match wins.
_CATEGORY_RULES = [
    # Wraps & Paninis
    (lambda n, g: "wrap" in n.lower() or "quesadilla" in n.lower()
     or "burrito" in n.lower() or "panini" in n.lower()
     or "grilled cheese" in n.lower(), "Wraps & Paninis"),
    # Salads & Bowls
    (lambda n, g: "salad" in n.lower() or "bowl" in n.lower(), "Salads & Bowls"),
    # Smoothies
    (lambda n, g: "smoothie" in g.lower() or g.lower().startswith("healthy smoothie")
     or g.lower().startswith("smoothie"), "Smoothies"),
    # Juices
    (lambda n, g: "juice" in g.lower() or "juice" in n.lower()
     or "shot" in n.lower(), "Juices"),
    # Matcha
    (lambda n, g: "matcha" in n.lower() or "matcha" in g.lower(), "Matcha"),
    # Soup
    (lambda n, g: "soup" in n.lower() or "soup" in g.lower(), "Soup"),
    # Tea
    (lambda n, g: "tea" in n.lower() or "tea" in g.lower(), "Tea & Coffee"),
    # Snacks
    (lambda n, g: "snack" in g.lower() or "cookie" in n.lower()
     or "oats" in n.lower() or "parfait" in n.lower()
     or "plantain" in n.lower() or "chip" in n.lower()
     or "hummus" in n.lower() or "fruit" in n.lower(), "Snacks"),
    # Ice Cream
    (lambda n, g: "ice cream" in n.lower() or "ice cream" in g.lower(), "Ice Cream"),
    # Beverages
    (lambda n, g: "lemonade" in n.lower() or "limeade" in n.lower()
     or "spindrift" in n.lower() or "water" in n.lower()
     or "pop culture" in n.lower() or "coffee" in n.lower()
     or "frappe" in n.lower() or "beverage" in g.lower()
     or "drink" in g.lower(), "Beverages"),
    # Catering
    (lambda n, g: "box" in n.lower() or "bundle" in n.lower()
     or "platter" in n.lower() or "variety" in n.lower()
     or "pack" in n.lower() or "catering" in g.lower(), "Catering Packages"),
    # Gift cards
    (lambda n, g: "gift" in n.lower() or "add value" in n.lower(), "Gift Cards"),
    # Dressings (sold individually)
    (lambda n, g: "dressing" in n.lower() or "vinaigrette" in n.lower()
     or "sauce" in n.lower() or g.lower() == "dressings"
     or "mason jar" in n.lower(), "Dressings"),
]

# Minimum total units to be considered a "real" menu item (filters one-offs)
MIN_QTY_THRESHOLD = 3


def _normalize_name(raw_name: str) -> str:
    """Map Toast POS name to canonical name."""
    key = raw_name.strip().lower()
    if key in _NAME_MAP:
        return _NAME_MAP[key]
    return raw_name.strip()


def _categorize(name: str, menu_group: str) -> str:
    """Assign a category to a menu item."""
    for rule_fn, category in _CATEGORY_RULES:
        if rule_fn(name, menu_group):
            return category
    return "Other"


def _load_recipes() -> dict:
    """Load recipes.yaml and return {lowercase toast_menu_name: recipe}."""
    if not RECIPE_FILE.exists():
        return {}
    with open(RECIPE_FILE) as f:
        data = yaml.safe_load(f) or {}
    recipes = data.get("recipes", [])
    mapping = {}
    for r in recipes:
        toast_name = r.get("toast_menu_name", "").strip()
        if toast_name:
            mapping[toast_name.lower()] = r
        # Also map by recipe name
        rname = r.get("name", "").strip()
        if rname and rname.lower() not in mapping:
            mapping[rname.lower()] = r
    return mapping


def scan_sales_data(recent_days: int = 30) -> list[dict]:
    """Scan cached Toast data and return consolidated menu item list.

    Args:
        recent_days: Number of recent days to scan. Default 30 (covers
                     full menu rotation). Use 0 for all available data.

    Returns:
        List of dicts with keys:
            name, category, total_qty, total_revenue, days_seen,
            avg_daily_qty, has_recipe, recipe_id, toast_variants
    """
    if not DATA_DIR.exists():
        return []

    dates = sorted([
        d for d in os.listdir(DATA_DIR)
        if d.isdigit() and len(d) == 8
    ])
    if recent_days > 0:
        dates = dates[-recent_days:]

    total_dates = len(dates)
    if total_dates == 0:
        return []

    # Phase 1: Scan all items and consolidate by canonical name
    raw_items = defaultdict(lambda: {
        "qty": 0, "revenue": 0, "days": set(),
        "menu_group": "", "variants": set()
    })

    for date_folder in dates:
        csv_path = DATA_DIR / date_folder / "ItemSelectionDetails.csv"
        if not csv_path.exists():
            continue
        try:
            with open(csv_path, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("Void?", "False").strip() == "True":
                        continue
                    raw_name = row.get("Menu Item", "").strip()
                    if not raw_name:
                        continue
                    canonical = _normalize_name(raw_name)
                    qty = float(row.get("Qty", "0") or "0")
                    revenue = float(row.get("Net Price", "0") or "0")
                    menu_group = row.get("Menu Group", "").strip()

                    raw_items[canonical]["qty"] += qty
                    raw_items[canonical]["revenue"] += revenue
                    raw_items[canonical]["days"].add(date_folder)
                    if menu_group:
                        raw_items[canonical]["menu_group"] = menu_group
                    if raw_name != canonical:
                        raw_items[canonical]["variants"].add(raw_name)
        except Exception:
            continue

    # Phase 2: Load recipes and match
    recipe_map = _load_recipes()

    # Phase 3: Build result list
    results = []
    for name, data in raw_items.items():
        total_qty = data["qty"]
        if total_qty < MIN_QTY_THRESHOLD:
            continue

        category = _categorize(name, data["menu_group"])

        # Skip gift cards and misc
        if category in ("Gift Cards", "Other"):
            continue

        days_seen = len(data["days"])
        avg_daily = round(total_qty / days_seen, 1) if days_seen else 0

        # Recipe match
        recipe = recipe_map.get(name.lower())
        if not recipe:
            # Try matching without common suffixes
            for variant in [name, name.replace(" - GF", ""),
                            name.replace(" - Vegetarian/GF", ""),
                            name.replace(" - Vegetarian", ""),
                            name.replace(" - Vegan", "")]:
                recipe = recipe_map.get(variant.lower())
                if recipe:
                    break

        results.append({
            "name": name,
            "category": category,
            "total_qty": int(total_qty),
            "total_revenue": round(data["revenue"], 2),
            "days_seen": days_seen,
            "total_days": total_dates,
            "avg_daily_qty": avg_daily,
            "has_recipe": recipe is not None,
            "recipe_id": recipe.get("id", "") if recipe else "",
            "toast_variants": sorted(data["variants"]),
        })

    # Sort by category then revenue descending
    results.sort(key=lambda x: (x["category"], -x["total_revenue"]))
    return results


def get_coverage_summary(items: list[dict] = None) -> dict:
    """Return high-level coverage stats.

    Returns:
        Dict with: total_items, costed_items, coverage_pct,
                   costed_revenue, total_revenue, revenue_coverage_pct,
                   by_category (list of category summaries)
    """
    if items is None:
        items = scan_sales_data()

    total = len(items)
    costed = sum(1 for i in items if i["has_recipe"])
    total_rev = sum(i["total_revenue"] for i in items)
    costed_rev = sum(i["total_revenue"] for i in items if i["has_recipe"])

    # By category
    cats = defaultdict(lambda: {"total": 0, "costed": 0,
                                "revenue": 0, "costed_revenue": 0})
    for item in items:
        c = cats[item["category"]]
        c["total"] += 1
        c["revenue"] += item["total_revenue"]
        if item["has_recipe"]:
            c["costed"] += 1
            c["costed_revenue"] += item["total_revenue"]

    by_category = []
    for cat_name in sorted(cats.keys()):
        c = cats[cat_name]
        by_category.append({
            "category": cat_name,
            "total": c["total"],
            "costed": c["costed"],
            "pct": round(100 * c["costed"] / c["total"], 1) if c["total"] else 0,
            "revenue": round(c["revenue"], 2),
            "costed_revenue": round(c["costed_revenue"], 2),
            "revenue_pct": round(100 * c["costed_revenue"] / c["revenue"], 1)
            if c["revenue"] else 0,
        })

    return {
        "total_items": total,
        "costed_items": costed,
        "coverage_pct": round(100 * costed / total, 1) if total else 0,
        "total_revenue": round(total_rev, 2),
        "costed_revenue": round(costed_rev, 2),
        "revenue_coverage_pct": round(100 * costed_rev / total_rev, 1)
        if total_rev else 0,
        "by_category": by_category,
    }
