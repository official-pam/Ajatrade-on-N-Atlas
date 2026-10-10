"""Problem 2: soil health interpretation.

Turns soil readings into plain ratings and actions. Every value carries its
source, because a laboratory number, a cheap meter reading and a farmer's
guess are not equally trustworthy (cheap online "NPK sensors" in particular
estimate nutrients rather than measure them).

Rating limits are the ones Nigerian soil laboratories commonly report against
(Esu 1991, Fundamentals of Pedology; FPDD 1989), and FAO's salinity classes.
# REVIEW: confirm the limits with the soil science lecturer / partner lab.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# source of a reading, most to least reliable
SOURCES = ("lab", "meter", "npk_sensor", "farmer_estimate")
MEASURED_SOURCES = {"lab", "meter"}

# (upper limit for "low", upper limit for "medium"); above that is "high".
RATING_LIMITS = {
    "organic_carbon_percent": (1.0, 1.5),
    "total_nitrogen_percent": (0.10, 0.20),
    "available_p_mg_per_kg": (10.0, 20.0),
    "exchangeable_k_cmol_per_kg": (0.15, 0.30),
}

FIELD_CAPACITY_PERCENT = {"sandy": 15.0, "loamy": 28.0, "clay": 40.0}


@dataclass
class Reading:
    name: str
    value: float | str
    source: str = "farmer_estimate"

    @property
    def measured(self) -> bool:
        return self.source in MEASURED_SOURCES


@dataclass
class SoilReport:
    ratings: dict[str, str] = field(default_factory=dict)
    measured: list[str] = field(default_factory=list)
    estimated: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    overall: str = "unknown"
    needs_lab_test: bool = False

    def as_facts(self) -> dict:
        return {
            "overall_condition": self.overall,
            "ratings": self.ratings,
            "measured_values": self.measured,
            "estimated_values": self.estimated,
            "problems": self.problems,
            "actions": self.actions,
            "recommend_lab_test": self.needs_lab_test,
        }


def rate(name: str, value: float) -> str:
    low, medium = RATING_LIMITS[name]
    if value < low:
        return "low"
    if value <= medium:
        return "medium"
    return "high"


def rate_ph(ph: float) -> str:
    if ph < 4.5:
        return "extremely acidic"
    if ph < 5.5:
        return "strongly acidic"
    if ph < 6.6:
        return "slightly acidic (good for most crops)"
    if ph <= 7.3:
        return "neutral (good)"
    if ph <= 8.4:
        return "alkaline"
    return "strongly alkaline"


def rate_ec(ec_ds_per_m: float) -> str:
    if ec_ds_per_m < 2:
        return "not salty"
    if ec_ds_per_m < 4:
        return "slightly salty"
    if ec_ds_per_m < 8:
        return "salty"
    return "very salty"


def _label(reading: Reading) -> str:
    where = {
        "lab": "lab test",
        "meter": "soil meter",
        "npk_sensor": "NPK sensor, an estimate",
        "farmer_estimate": "your description, an estimate",
    }.get(reading.source, reading.source)
    return f"{reading.name.replace('_', ' ')} {reading.value} ({where})"


def interpret(readings: list[Reading], texture: str | None = None) -> SoilReport:
    report = SoilReport()
    by_name = {r.name: r for r in readings}
    for r in readings:
        (report.measured if r.measured else report.estimated).append(_label(r))

    ph = by_name.get("ph")
    if ph is not None and isinstance(ph.value, (int, float)):
        rating = rate_ph(float(ph.value))
        report.ratings["ph"] = rating
        if float(ph.value) < 5.5:
            report.problems.append("The soil is too acidic, so plants cannot use fertilizer well.")
            report.actions.append(
                "Apply agricultural lime or wood ash before planting, mix it into the soil, and test again next season."
            )
        elif float(ph.value) > 7.8:
            report.problems.append("The soil is alkaline, so some nutrients are locked up.")
            report.actions.append("Add plenty of compost or animal manure; avoid wood ash and lime.")

    ec = by_name.get("ec_ds_per_m")
    if ec is not None and isinstance(ec.value, (int, float)):
        rating = rate_ec(float(ec.value))
        report.ratings["salt"] = rating
        if float(ec.value) >= 2:
            report.problems.append("There is too much salt in the soil for many crops.")
            report.actions.append("Water deeply to wash salt down, improve drainage, and grow salt-tolerant crops for now.")

    for name in RATING_LIMITS:
        r = by_name.get(name)
        if r is None or not isinstance(r.value, (int, float)):
            continue
        rating = rate(name, float(r.value))
        report.ratings[name] = rating if r.measured else f"{rating} (estimate)"
        if rating == "low":
            report.problems.append(_LOW_PROBLEM[name])
            report.actions.append(_LOW_ACTION[name])

    # Qualitative estimates from what the farmer described.
    for name in ("nitrogen", "phosphorus", "potassium", "organic_matter"):
        r = by_name.get(name)
        if r is not None and isinstance(r.value, str):
            report.ratings[name] = f"{r.value} (estimate)"
            if r.value == "low":
                report.problems.append(_LOW_PROBLEM_QUAL[name])
                report.actions.append(_LOW_ACTION_QUAL[name])

    moisture = by_name.get("moisture_percent")
    if moisture is not None and isinstance(moisture.value, (int, float)):
        fc = FIELD_CAPACITY_PERCENT.get(texture or "", FIELD_CAPACITY_PERCENT["loamy"])
        share = float(moisture.value) / fc
        state = "dry" if share < 0.5 else "moist" if share <= 1.1 else "waterlogged"
        report.ratings["moisture"] = state
        if state == "dry":
            report.actions.append("The soil is dry; water before applying fertilizer.")
        if state == "waterlogged":
            report.problems.append("The soil is waterlogged; roots can rot.")
            report.actions.append("Open drainage channels and plant on ridges or mounds.")

    bad = len(report.problems)
    report.overall = "good" if bad == 0 and report.ratings else "fair" if bad <= 2 else "poor"
    if not report.ratings:
        report.overall = "unknown"
    report.needs_lab_test = not any(r.measured for r in readings) or any(
        r.source == "npk_sensor" for r in readings
    )
    if report.needs_lab_test:
        report.actions.append(
            "For exact numbers, take a soil sample to a soil lab (ADP office, a university or a research institute)."
        )
    # Keep advice short for voice: no duplicates, at most five actions.
    report.actions = list(dict.fromkeys(report.actions))[:5]
    return report


_LOW_PROBLEM = {
    "organic_carbon_percent": "Organic matter is low, so the soil holds little water and food.",
    "total_nitrogen_percent": "Nitrogen is low; leaves may turn pale or yellow.",
    "available_p_mg_per_kg": "Phosphorus is low; roots and early growth will be weak.",
    "exchangeable_k_cmol_per_kg": "Potassium is low; plants may lodge and yield less.",
}
_LOW_ACTION = {
    "organic_carbon_percent": "Add compost, animal manure or crop residues every season.",
    "total_nitrogen_percent": "Use the fertilizer plan for your crop, and rotate with beans, cowpea or soybean.",
    "available_p_mg_per_kg": "Include a phosphorus fertilizer such as SSP or NPK at planting.",
    "exchangeable_k_cmol_per_kg": "Include potassium (MOP or NPK) and return crop residues to the soil.",
}
_LOW_PROBLEM_QUAL = {
    "nitrogen": "Nitrogen looks low from what you described.",
    "phosphorus": "Phosphorus may be low from what you described.",
    "potassium": "Potassium may be low from what you described.",
    "organic_matter": "Organic matter looks low from what you described.",
}
_LOW_ACTION_QUAL = {
    "nitrogen": _LOW_ACTION["total_nitrogen_percent"],
    "phosphorus": _LOW_ACTION["available_p_mg_per_kg"],
    "potassium": _LOW_ACTION["exchangeable_k_cmol_per_kg"],
    "organic_matter": _LOW_ACTION["organic_carbon_percent"],
}


def readings_from_values(values: dict, source: str) -> list[Reading]:
    """Build readings from extracted numbers (lab report photo or typed)."""
    readings: list[Reading] = []
    if source not in SOURCES:
        source = "farmer_estimate"
    mapping = {
        "ph": "ph",
        "ec_ds_per_m": "ec_ds_per_m",
        "organic_carbon_percent": "organic_carbon_percent",
        "total_nitrogen_percent": "total_nitrogen_percent",
        "available_p_mg_per_kg": "available_p_mg_per_kg",
        "exchangeable_k_cmol_per_kg": "exchangeable_k_cmol_per_kg",
        "moisture_percent": "moisture_percent",
    }
    for key, name in mapping.items():
        value = values.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            readings.append(Reading(name, float(value), source))
    om = values.get("organic_matter_percent")
    if "organic_carbon_percent" not in values or values.get("organic_carbon_percent") is None:
        if isinstance(om, (int, float)) and not isinstance(om, bool):
            # Organic matter is about 1.724 x organic carbon (van Bemmelen).
            readings.append(Reading("organic_carbon_percent", round(float(om) / 1.724, 2), source))
    for key in ("nitrogen", "phosphorus", "potassium", "organic_matter"):
        level = str(values.get(key) or "").strip().lower()
        if level in ("low", "medium", "high"):
            readings.append(Reading(key, level, "npk_sensor" if source == "npk_sensor" else "farmer_estimate"))
    return readings


def estimate_from_description(signs: dict) -> list[Reading]:
    """Rough estimates from farmer observations. Always labelled estimates."""
    readings: list[Reading] = []
    if signs.get("yellow_lower_leaves") or signs.get("pale_crop"):
        readings.append(Reading("nitrogen", "low"))
    if signs.get("purple_leaves") or signs.get("stunted_roots"):
        readings.append(Reading("phosphorus", "low"))
    if signs.get("leaf_edges_burnt"):
        readings.append(Reading("potassium", "low"))
    colour = str(signs.get("soil_colour") or "").lower()
    if colour in ("light", "pale", "grey", "white", "sandy"):
        readings.append(Reading("organic_matter", "low"))
    elif colour in ("dark", "black", "dark brown"):
        readings.append(Reading("organic_matter", "medium"))
    return readings
