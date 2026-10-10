"""Problem 5: where to find or collect water for the farm.

This is decision support, not detection. A phone cannot see underground
water, so the module combines what can be known cheaply:

- terrain around the farm (Open-Meteo 90 m elevation model): low ground and
  valley bottoms collect runoff and usually have shallower groundwater;
- the last year's rainfall: how much water a pond could catch;
- what the farmer sees: wells nearby, a stream or fadama, plants that stay
  green in the dry season, rocky ground.

It returns a likelihood (low / medium / high) and always tells the farmer to
get a resistivity (VES) survey by a hydrogeologist before paying for a
borehole.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

GRID_STEPS = 5  # 5 x 5 points
GRID_SPACING_M = 150.0
RUNOFF_COEFFICIENT = 0.3  # cultivated land; FAO water-harvesting range 0.1 to 0.5
DIRECTIONS = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")


def grid_points(lat: float, lon: float) -> list[tuple[float, float]]:
    """Points on a square grid centred on the farm (row-major, north first)."""
    dlat = GRID_SPACING_M / 111_320.0
    dlon = GRID_SPACING_M / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
    half = GRID_STEPS // 2
    return [
        (lat + (half - r) * dlat, lon + (c - half) * dlon)
        for r in range(GRID_STEPS)
        for c in range(GRID_STEPS)
    ]


def _direction(dy: float, dx: float) -> str:
    angle = (math.degrees(math.atan2(dx, dy)) + 360) % 360  # 0 = north
    return DIRECTIONS[int((angle + 22.5) // 45) % 8]


@dataclass
class TerrainSummary:
    farm_elevation_m: float
    lowest_m: float
    highest_m: float
    position: str  # valley bottom | lower slope | mid slope | upper slope | hilltop | flat
    lowest_direction: str | None
    lowest_distance_m: float | None
    slope_percent: float


def summarize_terrain(elevations: list[float]) -> TerrainSummary:
    if len(elevations) != GRID_STEPS * GRID_STEPS:
        raise ValueError("expected a full elevation grid")
    centre_index = (GRID_STEPS * GRID_STEPS) // 2
    farm = elevations[centre_index]
    low, high = min(elevations), max(elevations)
    relief = high - low
    half = GRID_STEPS // 2
    low_index = elevations.index(low)
    r, c = divmod(low_index, GRID_STEPS)
    dy, dx = (half - r), (c - half)
    distance = math.hypot(dy, dx) * GRID_SPACING_M
    # Steepest drop from the farm point to its 8 neighbours.
    drops = []
    for rr in range(half - 1, half + 2):
        for cc in range(half - 1, half + 2):
            if (rr, cc) == (half, half):
                continue
            run = math.hypot(rr - half, cc - half) * GRID_SPACING_M
            drops.append(abs(farm - elevations[rr * GRID_STEPS + cc]) / run * 100)
    slope = max(drops) if drops else 0.0
    if relief < 3:
        position = "flat"
    else:
        share = (farm - low) / relief
        position = (
            "valley bottom" if share < 0.15
            else "lower slope" if share < 0.4
            else "mid slope" if share < 0.65
            else "upper slope" if share < 0.9
            else "hilltop"
        )
    return TerrainSummary(
        farm_elevation_m=round(farm, 1),
        lowest_m=round(low, 1),
        highest_m=round(high, 1),
        position=position,
        lowest_direction=_direction(dy, dx) if distance > 0 else None,
        lowest_distance_m=round(distance) if distance > 0 else None,
        slope_percent=round(slope, 1),
    )


@dataclass
class WaterAssessment:
    groundwater_likelihood: str
    reasons: list[str]
    pond_catch_m3: float | None
    pond_example: str | None
    actions: list[str] = field(default_factory=list)
    terrain: TerrainSummary | None = None
    annual_rain_mm: float | None = None

    def as_facts(self) -> dict:
        t = self.terrain
        return {
            "groundwater_likelihood": self.groundwater_likelihood,
            "why": self.reasons,
            "farm_position": t.position if t else None,
            "lowest_ground": (
                f"about {t.lowest_distance_m} m to the {t.lowest_direction}, {round(t.farm_elevation_m - t.lowest_m)} m lower"
                if t and t.lowest_direction
                else None
            ),
            "slope_percent": t.slope_percent if t else None,
            "rain_last_year_mm": self.annual_rain_mm,
            "rainwater_a_pond_could_catch_m3": self.pond_catch_m3,
            "pond_example": self.pond_example,
            "actions": self.actions,
            "this_is_a_likelihood_not_a_detection": True,
        }


def assess(
    terrain: TerrainSummary | None,
    annual_rain_mm: float | None,
    catchment_m2: float,
    *,
    wells_nearby_with_water: bool | None = None,
    well_depth_m: float | None = None,
    stream_or_fadama_nearby: bool | None = None,
    green_in_dry_season: bool | None = None,
    rocky_ground: bool | None = None,
) -> WaterAssessment:
    score = 0
    reasons: list[str] = []
    if terrain:
        if terrain.position in ("valley bottom", "lower slope"):
            score += 2
            reasons.append("the farm is on low ground where water collects")
        elif terrain.position in ("upper slope", "hilltop"):
            score -= 1
            reasons.append("the farm is on high ground, so water drains away")
        elif terrain.position == "flat":
            reasons.append("the land is flat")
    if annual_rain_mm is not None:
        if annual_rain_mm >= 1200:
            score += 1
            reasons.append(f"rain is plentiful (about {int(round(annual_rain_mm, -1))} mm last year)")
        elif annual_rain_mm < 600:
            score -= 1
            reasons.append(f"rain is low (about {int(round(annual_rain_mm, -1))} mm last year)")
    if wells_nearby_with_water:
        score += 2
        depth = f" at about {int(well_depth_m)} m" if well_depth_m else ""
        reasons.append(f"nearby wells have water{depth}")
    elif wells_nearby_with_water is False:
        score -= 1
        reasons.append("nearby wells dry up")
    if stream_or_fadama_nearby:
        score += 2
        reasons.append("there is a stream or fadama nearby")
    if green_in_dry_season:
        score += 1
        reasons.append("some plants stay green in the dry season, a sign of shallow water")
    if rocky_ground:
        score -= 1
        reasons.append("rocky ground makes drilling harder and water less certain")

    likelihood = "high" if score >= 4 else "medium" if score >= 1 else "low"

    pond = None
    example = None
    if annual_rain_mm:
        pond = round(catchment_m2 * annual_rain_mm / 1000 * RUNOFF_COEFFICIENT, -1)
        side = 10
        depth = 2
        example = (
            f"A pond {side} m by {side} m and {depth} m deep holds {side * side * depth} cubic metres "
            f"({side * side * depth * 1000:,} litres)."
        )

    actions = []
    if terrain and terrain.lowest_direction and terrain.position not in ("valley bottom", "flat"):
        actions.append(
            f"The lowest nearby ground is to the {terrain.lowest_direction}. That is the best place for a pond or a hand-dug well."
        )
    elif terrain and terrain.position == "valley bottom":
        actions.append("Your farm is in the low part already; a hand-dug well or pond on the lowest corner is the cheapest start.")
    actions.append("Line a pond with clay or plastic and cover it from the sun to reduce loss.")
    actions.append(
        "Before paying for a borehole, ask a hydrogeologist for a resistivity (VES) survey; it shows where and how deep to drill."
    )
    return WaterAssessment(
        groundwater_likelihood=likelihood,
        reasons=reasons,
        pond_catch_m3=pond,
        pond_example=example,
        actions=actions,
        terrain=terrain,
        annual_rain_mm=annual_rain_mm,
    )
