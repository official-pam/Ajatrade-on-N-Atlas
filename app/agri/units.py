"""Farm size and number helpers.

A "plot" is the common Nigerian land plot of 50 ft by 100 ft (about 465 m2).
Some places use 60 ft by 120 ft, so replies repeat the size used.
"""

from __future__ import annotations

import re

SQUARE_METRES = {
    "hectare": 10_000.0,
    "acre": 4_046.86,
    "plot": 464.5,  # 50 ft x 100 ft
    "square metre": 1.0,
    "square foot": 0.092903,
}

_UNIT_ALIASES = {
    "ha": "hectare", "hectares": "hectare", "hectare": "hectare",
    "acre": "acre", "acres": "acre",
    "plot": "plot", "plots": "plot",
    "m2": "square metre", "m²": "square metre", "sqm": "square metre",
    "square metre": "square metre", "square metres": "square metre",
    "square meter": "square metre", "square meters": "square metre",
    "ft2": "square foot", "sq ft": "square foot", "square feet": "square foot",
    "square foot": "square foot",
}


def to_square_metres(value: float | int | str | None, unit: str | None) -> float | None:
    """Convert an area to m2. Returns None when it cannot be understood."""
    try:
        number = float(str(value).replace(",", "")) if value is not None else None
    except ValueError:
        number = None
    if number is None or number <= 0:
        return None
    key = _UNIT_ALIASES.get((unit or "").strip().lower())
    if key is None:
        return None
    return number * SQUARE_METRES[key]


def parse_dimensions(text: str | None) -> float | None:
    """'20 by 30 metres' or '50x100 ft' -> m2."""
    if not text:
        return None
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:m|metres?|meters?|ft|feet)?\s*(?:by|x|×)\s*(\d+(?:\.\d+)?)\s*(m|metres?|meters?|ft|feet)?",
        text.lower(),
    )
    if not match:
        return None
    a, b = float(match.group(1)), float(match.group(2))
    unit = match.group(3) or "m"
    factor = 0.3048 if unit.startswith(("ft", "feet")) else 1.0
    return a * factor * b * factor


def describe_area(m2: float) -> str:
    if m2 >= 5_000:
        hectares = round(m2 / 10_000, 1)
        number = f"{hectares:g}"
        return f"{number} hectare" if hectares == 1 else f"{number} hectares"
    return f"{m2:,.0f} square metres"


def round_sensible(value: float) -> float:
    """Round for speaking: 3 significant figures at most."""
    if value >= 100:
        return float(round(value, -1)) if value < 1000 else float(round(value, -2))
    if value >= 10:
        return float(round(value))
    return round(value, 1)


def bags(kg: float, bag_kg: float = 50.0) -> float:
    """Kilograms to bags, rounded to the nearest half bag (minimum 0)."""
    return max(0.0, round(kg / bag_kg * 2) / 2)
