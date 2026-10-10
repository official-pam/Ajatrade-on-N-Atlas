"""Turn a farmer's message into flow facts.

Cheap, predictable patterns run first (crop names, states, "2 acres",
"5 weeks", "yes"/"no"). N-ATLaS then reads the message for anything the
patterns missed. Every value is validated in code before it is stored, so a
bad model reply can only cost a repeated question, never a wrong number.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from app.agri.crops import find_crop
from app.agri.fertilizer import normalize_product
from app.agri.units import parse_dimensions, to_square_metres
from app.agri.zones import find_state
from app.services import natlas
from app.services.advisor.specs import FLOWS, FlowSpec, Slot
from app.services.diagnoses.reply import parse_model_output

logger = logging.getLogger("advisor.extract")

WORD_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "fifteen": 15, "twenty": 20, "half": 0.5, "quarter": 0.25,
}
_NUM = r"(\d+(?:\.\d+)?|" + "|".join(sorted(WORD_NUMBERS, key=len, reverse=True)) + r")"

YES = re.compile(r"\b(yes|yeah|yep|true|correct|it is|e dey|na so|sure|i do|we do|there is|there are)\b", re.I)
NO = re.compile(r"\b(no|nope|not|none|flat|never|nothing|e no dey|no be)\b", re.I)
SKIP = re.compile(r"\b(skip|i don'?t know|not sure|no idea|leave it|i no know)\b", re.I)
MENU = re.compile(r"^\s*(menu|main menu|cancel|stop|exit|start again|go back|back)\s*[.!]?\s*$", re.I)
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


def _num(token: str) -> float:
    token = token.lower()
    return float(WORD_NUMBERS[token]) if token in WORD_NUMBERS else float(token)


# ---------------------------------------------------------------------------
# Validation of one value per slot kind
# ---------------------------------------------------------------------------


def validate(slot: Slot, value: Any, today: date | None = None) -> Any:
    """Return a clean value, or None if the value is unusable."""
    if value is None or value == "" or value == {} or value == []:
        return None
    kind = slot.kind
    try:
        if kind == "crop":
            crop = find_crop(str(value))
            return crop.name if crop else str(value).strip().lower()[:40]
        if kind == "int":
            number = int(round(float(value)))
            return number if 0 <= number <= 2000 else None
        if kind == "number":
            number = float(str(value).replace(",", ""))
            return number if number > 0 else None
        if kind == "bool":
            if isinstance(value, bool):
                return value
            text = str(value).lower()
            if text in ("true", "yes", "1"):
                return True
            if text in ("false", "no", "0"):
                return False
            return None
        if kind == "area":
            return _area(value)
        if kind == "place":
            text = str(value).strip()
            return text[:80] if len(text) >= 2 else None
        if kind == "list":
            items = value if isinstance(value, list) else [value]
            return [str(i).strip().lower() for i in items if str(i).strip()][:8] or None
        if kind == "dict":
            return value if isinstance(value, dict) and value else None
        if kind == "prices":
            if not isinstance(value, dict):
                return None
            prices = {}
            for key, amount in value.items():
                product = normalize_product(key) or (key if key in ("npk15", "npk20", "urea", "ssp", "mop") else None)
                try:
                    naira = float(str(amount).replace(",", ""))
                except (TypeError, ValueError):
                    continue
                if product and 1000 <= naira <= 500_000:
                    prices[product] = naira
            return prices or None
        if kind == "month":
            if isinstance(value, str) and value.strip().lower() in MONTHS:
                return MONTHS[value.strip().lower()]
            month = int(float(value))
            return month if 1 <= month <= 12 else None
        if kind == "counts":
            if not isinstance(value, dict):
                return None
            planted = int(float(value.get("planted")))
            germinated = int(float(value.get("germinated")))
            if planted <= 0 or germinated < 0:
                return None
            return {"planted": planted, "germinated": min(germinated, planted)}
        if kind == "soil":
            return value if isinstance(value, dict) and (value.get("values") or value.get("signs")) else None
    except (TypeError, ValueError, KeyError):
        return None
    text = str(value).strip()
    return text[:120] or None


def _area(value: Any) -> dict | None:
    if isinstance(value, dict):
        if value.get("m2"):
            return {"m2": float(value["m2"]), "label": value.get("label") or f"{value['m2']} square metres"}
        if value.get("dimensions"):
            m2 = parse_dimensions(str(value["dimensions"]))
            return {"m2": m2, "label": str(value["dimensions"])} if m2 else None
        m2 = to_square_metres(value.get("value"), value.get("unit"))
        if m2:
            return {"m2": m2, "label": f"{_fmt(value.get('value'))} {value.get('unit')}"}
        return None
    return _area_from_text(str(value))


def _fmt(number: Any) -> str:
    try:
        f = float(number)
    except (TypeError, ValueError):
        return str(number)
    return str(int(f)) if f.is_integer() else str(f)


def _area_from_text(text: str) -> dict | None:
    lowered = text.lower()
    dims = parse_dimensions(lowered)
    if dims:
        return {"m2": dims, "label": text.strip()[:40]}
    match = re.search(_NUM + r"\s*(?:an?\s+|of\s+an?\s+)?(plots?|acres?|hectares?|ha|square met(?:re|er)s?|m2|sqm)\b", lowered)
    if not match:
        return None
    value = _num(match.group(1))
    unit = match.group(2)
    m2 = to_square_metres(value, unit if unit not in ("square metre", "square meters") else "square metre")
    if m2 is None and unit.startswith("square"):
        m2 = to_square_metres(value, "square metre")
    return {"m2": m2, "label": f"{_fmt(value)} {unit}"} if m2 else None


# ---------------------------------------------------------------------------
# Pattern pass
# ---------------------------------------------------------------------------


def pattern_facts(spec: FlowSpec, text: str, asked: str | None, today: date) -> dict[str, Any]:
    """Facts that simple patterns can read with confidence."""
    facts: dict[str, Any] = {}
    keys = {s.key for s in spec.slots}
    lowered = text.lower()

    if "crop" in keys:
        crop = find_crop(lowered)
        if crop:
            facts["crop"] = crop.name
    if "area" in keys:
        area = _area_from_text(lowered)
        if area:
            facts["area"] = area
    if "location" in keys and find_state(lowered):
        facts["location"] = text.strip()[:80]
    for age_key, unit_days in (("crop_age_days", None), ("days_since_planting", None), ("weeks_after_planting", 7)):
        if age_key in keys:
            match = re.search(_NUM + r"\s*(days?|weeks?|months?)\b", lowered)
            if match:
                n = _num(match.group(1))
                unit = match.group(2)
                days = n * (30 if unit.startswith("month") else 7 if unit.startswith("week") else 1)
                facts[age_key] = int(round(days / unit_days)) if unit_days else int(round(days))
    if "counts" in keys:
        out_of = re.search(r"(\d+)\s*(?:out of|of the|from)\s*(\d+)", lowered)
        planted = re.search(r"(?:planted|sowed|sow|put)\s*(\d+)", lowered)
        came = re.search(r"(\d+)\s*(?:of them\s*)?(?:came up|germinated|sprouted|grew|germinate|come out)", lowered)
        if out_of:
            facts["counts"] = {"germinated": int(out_of.group(1)), "planted": int(out_of.group(2))}
        elif planted and came:
            facts["counts"] = {"planted": int(planted.group(1)), "germinated": int(came.group(1))}
    if "planting_month" in keys:
        if re.search(r"\b(now|today|this week|tomorrow|next week|this month)\b", lowered):
            facts["planting_month"] = today.month
        else:
            for name, number in MONTHS.items():
                if re.search(rf"\b{name}\b", lowered):
                    facts["planting_month"] = number
                    break

    asked_slot = next((s for s in spec.slots if s.key == asked), None)
    if asked_slot is not None and asked_slot.kind == "bool" and asked not in facts:
        if YES.search(lowered) and not NO.search(lowered):
            facts[asked] = True
        elif NO.search(lowered) and not YES.search(lowered):
            facts[asked] = False
    if asked_slot is not None and asked_slot.kind == "number" and asked not in facts:
        match = re.search(r"(\d[\d,]*(?:\.\d+)?)", lowered)
        if match:
            facts[asked] = match.group(1)
    return facts


def is_skip(text: str) -> bool:
    return bool(SKIP.search(text or ""))


def wants_menu(text: str) -> bool:
    return bool(MENU.match(text or ""))


# ---------------------------------------------------------------------------
# N-ATLaS pass
# ---------------------------------------------------------------------------

EXTRACT_SYSTEM = (
    "You read a Nigerian farmer's message (already translated to English) and pull out "
    "facts for a farm assistant. Only include facts the farmer clearly said. Use digits for "
    "numbers. Return ONE JSON object and nothing else."
)


def _extract_prompt(spec: FlowSpec, known: dict, asked: str | None, text: str) -> str:
    fields = "\n".join(f"- {s.key}: {s.description}" for s in spec.slots)
    others = ", ".join(f for f in FLOWS if f != spec.flow)
    question = next((s.question for s in spec.slots if s.key == asked), None)
    return (
        f"Service: {spec.title}\n"
        f"Fields:\n{fields}\n"
        f"Already known: {json.dumps(known, ensure_ascii=False)}\n"
        f"Question just asked: {question or 'none'}\n"
        f'Farmer said: "{text}"\n\n'
        'Return {"facts": {only the fields the farmer stated}, "wants_menu": true or false, '
        f'"other_service": one of [{others}, crop_detection, market_price] or null}}'
    )


async def model_facts(spec: FlowSpec, known: dict, asked: str | None, text: str) -> tuple[dict, bool, str | None]:
    """N-ATLaS extraction. Returns (facts, wants_menu, other_service)."""
    try:
        raw = await natlas.chat(EXTRACT_SYSTEM, _extract_prompt(spec, known, asked, text), json_mode=True, max_tokens=300)
    except Exception as error:  # noqa: BLE001 - patterns still work without it
        logger.warning("N-ATLaS extraction failed: %s", error)
        return {}, False, None
    data = parse_model_output(raw) or {}
    facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
    other = data.get("other_service")
    other = other if isinstance(other, str) and other in (*FLOWS, "crop_detection", "market_price") and other != spec.flow else None
    return facts, bool(data.get("wants_menu") is True), other


def clean_facts(spec: FlowSpec, facts: dict, today: date) -> dict[str, Any]:
    slots = {s.key: s for s in spec.slots}
    cleaned: dict[str, Any] = {}
    for key, value in facts.items():
        slot = slots.get(key)
        if slot is None:
            continue
        valid = validate(slot, value, today)
        if valid is not None:
            cleaned[key] = valid
    return cleaned
