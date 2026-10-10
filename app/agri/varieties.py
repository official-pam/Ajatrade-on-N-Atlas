"""Problem 7: crop variety and seed-to-environment matching.

The question is not "is hybrid seed bad" but "does this variety suit this
farm's season length, soil and stresses". The matcher is rule-based and
explains every point it scores. It never shows match percentages, because
there is no validation data to back them yet.

Variety facts below are only those confirmed by the breeding institute or
CGIAR announcements cited next to each entry. Availability changes, so
replies tell the farmer to buy certified seed from a registered seed company
or the state ADP.
# REVIEW: extend this list with the NACGRAB register before scaling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.agri.zones import SEASONS, season_days_left

# Days to maturity by maturity class, by crop.
MATURITY_DAYS = {
    "maize": {"extra-early": 85, "early": 95, "medium": 115, "late": 130},
    "sorghum": {"early": 100, "medium": 120, "late": 150},
    "millet": {"early": 75, "medium": 90, "late": 120},
    "rice": {"early": 100, "medium": 120, "late": 140},
    "cowpea": {"extra-early": 60, "early": 70, "medium": 80, "late": 90},
    "soybean": {"early": 90, "medium": 110, "late": 120},
    "groundnut": {"early": 90, "medium": 110, "late": 130},
    "cassava": {"early": 270, "medium": 365, "late": 450},
}
# A crop can finish on water stored in the soil for about ten days after the
# last rain, so maturity may run slightly past the typical season end.
RESIDUAL_MOISTURE_DAYS = 10


@dataclass(frozen=True)
class Variety:
    crop: str
    name: str
    kind: str  # hybrid | open-pollinated | improved
    maturity: str
    traits: tuple[str, ...]  # drought, striga, low_n, flood, fall_armyworm, provitamin_a, aflatoxin, maruca, cmd
    zones: tuple[str, ...]  # where it was tested or recommended
    source: str


VARIETIES: list[Variety] = [
    Variety("maize", "SAMMAZ 70", "hybrid", "extra-early", ("striga", "provitamin_a"),
            ("sudan_sahel", "northern_guinea", "southern_guinea"),
            "IAR, Ahmadu Bello University release announcement"),
    Variety("maize", "SAMMAZ 71", "hybrid", "medium", ("aflatoxin",),
            ("northern_guinea", "southern_guinea"),
            "IAR, Ahmadu Bello University release announcement"),
    Variety("maize", "SAMMAZ 77", "hybrid", "medium", ("striga", "drought", "fall_armyworm"),
            ("northern_guinea", "southern_guinea", "sudan_sahel"),
            "IITA and IAR, CGIAR news, May 2025"),
    Variety("cassava", "UMUCASS 36", "improved", "early", ("provitamin_a",),
            ("forest", "southern_guinea"),
            "NRCRI Umudike / IITA yellow cassava release, 2011"),
    Variety("cassava", "TME 419", "improved", "medium", ("cmd",),
            ("forest", "southern_guinea"),
            "IITA improved variety widely distributed in Nigeria"),
    Variety("rice", "FARO 66", "improved", "medium", ("flood",),
            ("forest", "southern_guinea", "northern_guinea"),
            "NCRI / AfricaRice submergence-tolerant release, 2011"),
    Variety("rice", "FARO 67", "improved", "medium", ("flood",),
            ("forest", "southern_guinea", "northern_guinea"),
            "NCRI / AfricaRice submergence-tolerant release, 2011"),
    Variety("cowpea", "SAMPEA 20-T", "improved", "medium", ("maruca",),
            ("sudan_sahel", "northern_guinea", "southern_guinea"),
            "IAR, Ahmadu Bello University (PBR cowpea), 2019"),
]

TRAIT_WORDS = {
    "drought": "handles dry spells",
    "striga": "resists Striga (witchweed)",
    "low_n": "does well on poor soil",
    "flood": "survives flooding for some days",
    "fall_armyworm": "tolerates fall armyworm",
    "provitamin_a": "rich in vitamin A",
    "aflatoxin": "resists aflatoxin",
    "maruca": "resists the pod borer (Maruca)",
    "cmd": "resists cassava mosaic disease",
}


@dataclass
class Need:
    max_maturity_days: int | None
    maturity_classes: list[str]
    stresses: list[str]
    reasons: list[str] = field(default_factory=list)


@dataclass
class Match:
    name: str
    fit: str  # good fit | possible | poor fit
    reasons: list[str]
    source: str


def what_the_farm_needs(
    crop: str,
    zone: str,
    planting_date: date,
    *,
    drought_risk: bool = False,
    striga: bool = False,
    low_soil_n: bool = False,
    flood_prone: bool = False,
    fall_armyworm: bool = False,
) -> Need:
    reasons: list[str] = []
    stresses: list[str] = []
    classes = MATURITY_DAYS.get(crop)
    max_days = None
    allowed: list[str] = []
    if classes and crop != "cassava":
        days_left = season_days_left(zone, planting_date)
        start_month = SEASONS[zone][0][0]
        if planting_date.month < start_month:
            # Planting for the coming season: count from typical onset.
            days_left = season_days_left(zone, date(planting_date.year, start_month, 1))
        max_days = days_left + RESIDUAL_MOISTURE_DAYS
        allowed = [name for name, days in classes.items() if days <= max_days]
        if not allowed:
            reasons.append(
                f"About {days_left} days of rain are left, which is too short for a rain-fed {crop} crop. Plant only with irrigation or wait for next season."
            )
        else:
            reasons.append(f"About {days_left} days of rain are left, so choose a {allowed[-1]} or faster variety.")
    if zone == "sudan_sahel":
        drought_risk = True
    for flag, name in (
        (drought_risk, "drought"),
        (striga, "striga"),
        (low_soil_n, "low_n"),
        (flood_prone, "flood"),
        (fall_armyworm, "fall_armyworm"),
    ):
        if flag:
            stresses.append(name)
    return Need(max_days, allowed, stresses, reasons)


def match(crop: str, zone: str, need: Need) -> list[Match]:
    results: list[Match] = []
    for v in (v for v in VARIETIES if v.crop == crop):
        score = 0
        reasons: list[str] = []
        if need.maturity_classes:
            if v.maturity in need.maturity_classes:
                score += 2
                reasons.append(f"its {v.maturity} maturity fits the rain left")
            else:
                score -= 3
                reasons.append(f"it is {v.maturity} and may not finish before the rain stops")
        if zone in v.zones:
            score += 1
            reasons.append("it was tested in your zone")
        for stress in need.stresses:
            if stress in v.traits:
                score += 2
                reasons.append(TRAIT_WORDS[stress])
            else:
                score -= 1
                reasons.append(f"no claim that it handles {stress.replace('_', ' ')}")
        extra = [TRAIT_WORDS[t] for t in v.traits if t not in need.stresses]
        if extra:
            reasons.append("also " + " and ".join(extra))
        fit = "good fit" if score >= 3 else "possible" if score >= 0 else "poor fit"
        results.append(Match(v.name, fit, reasons, v.source))
    order = {"good fit": 0, "possible": 1, "poor fit": 2}
    results.sort(key=lambda m: order[m.fit])
    return results


def traits_to_look_for(need: Need) -> list[str]:
    """What to ask the seed seller for, even when no listed variety fits."""
    asks = []
    if need.maturity_classes:
        asks.append(f"{need.maturity_classes[-1]} or earlier maturity")
    asks += [TRAIT_WORDS[s] for s in need.stresses]
    return asks
