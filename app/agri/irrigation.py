"""Problems 1 and 4: irrigation water need, water loss, and slope.

Water need follows FAO-56: crop water use ETc = Kc x ET0 (mm per day, and
1 mm over 1 m2 is 1 litre). Rain that falls today is credited with a simple
effective-rain rule, and the result is divided by the efficiency of the
watering method to get the litres to apply.

Slope: water and dissolved fertilizer run downhill, so the bottom of a
sloped field stays wetter than the top. Each slope position gets a factor
(start: top 100 %, middle 85 %, bottom 65 %, from the AjaTrade precision
agriculture plan). These starting numbers are not universal, so they are
calibrated per farm from the farmer's feedback ("bottom is too wet").
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agri.crops import Crop
from app.agri.units import round_sensible

METHOD_EFFICIENCY = {
    "drip": 0.90,
    "sprinkler": 0.75,
    "hose": 0.70,
    "watering can": 0.70,
    "bucket": 0.70,
    "furrow": 0.60,
    "basin": 0.60,
    "flood": 0.55,
}
DEFAULT_METHOD = "watering can"

# Plant-available water per metre of soil (mm/m), typical textbook values.
AVAILABLE_WATER_MM_PER_M = {"sandy": 70.0, "loamy": 140.0, "clay": 180.0}
DEFAULT_SOIL = "loamy"
ALLOWED_DEPLETION = 0.5  # water again when half the available water is used

# Maximum rooting depth (m) for scheduling; young crops use 0.25 m.
ROOT_DEPTH_M = {
    "maize": 1.0, "sorghum": 1.0, "millet": 1.0, "cassava": 0.7, "yam": 0.6,
    "cowpea": 0.6, "soybean": 0.6, "groundnut": 0.5, "tomato": 0.7,
    "pepper": 0.5, "onion": 0.3, "okra": 0.5, "vegetables": 0.3,
    "cabbage": 0.5, "watermelon": 0.8,
}

SLOPE_POSITIONS = ("uphill", "middle", "downhill")
DEFAULT_SLOPE_FACTORS = {"uphill": 1.0, "middle": 0.85, "downhill": 0.65}
FACTOR_LIMITS = (0.4, 1.3)


def effective_rain_mm(rain_mm: float) -> float:
    """Light showers mostly evaporate; count 80 % of rain above 3 mm."""
    return max(0.0, rain_mm - 3.0) * 0.8


def normalize_method(method: str | None) -> str:
    value = (method or "").strip().lower()
    for key in METHOD_EFFICIENCY:
        if key in value:
            return key
    if "pipe" in value or "drip" in value or "tape" in value:
        return "drip"
    if "can" in value:
        return "watering can"
    return DEFAULT_METHOD


def normalize_soil(texture: str | None) -> str:
    value = (texture or "").strip().lower()
    if "sand" in value:
        return "sandy"
    if "clay" in value or "heavy" in value or "sticky" in value:
        return "clay"
    return DEFAULT_SOIL


@dataclass
class ZoneResult:
    position: str  # uphill | middle | downhill | whole farm
    area_m2: float
    factor: float
    litres_today: float
    litres_per_stand: float | None


@dataclass
class IrrigationPlan:
    crop: str
    day_of_season: int
    stage: str
    kc: float
    et0_mm: float
    et0_is_typical: bool
    etc_mm: float
    rain_today_mm: float
    effective_rain_mm: float
    net_need_mm: float
    method: str
    efficiency: float
    soil: str
    interval_days: int
    zones: list[ZoneResult]
    total_litres_today: float
    rain_soon: bool
    notes: list[str] = field(default_factory=list)

    def as_facts(self) -> dict:
        """Numbers the reply must use, in a form N-ATLaS can phrase."""
        return {
            "crop": self.crop,
            "crop_stage": self.stage,
            "day_of_season": self.day_of_season,
            "crop_water_use_mm_today": round(self.etc_mm, 1),
            "weather_source": "typical for your area (no live forecast)" if self.et0_is_typical else "today's forecast",
            "rain_today_mm": round(self.rain_today_mm, 1),
            "watering_method": self.method,
            "soil": self.soil,
            "water_every_days": self.interval_days,
            "zones": [
                {
                    "where": z.position,
                    "litres_today": round_sensible(z.litres_today),
                    "litres_per_plant": round_sensible(z.litres_per_stand) if z.litres_per_stand else None,
                }
                for z in self.zones
            ],
            "total_litres_today": round_sensible(self.total_litres_today),
            "rain_expected_soon": self.rain_soon,
            "notes": self.notes,
        }


def water_need(
    crop: Crop,
    day_of_season: int,
    area_m2: float,
    et0_mm: float,
    *,
    rain_today_mm: float = 0.0,
    rain_next_days_mm: float = 0.0,
    rain_chance_next: int | None = None,
    et0_is_typical: bool = False,
    method: str | None = None,
    soil: str | None = None,
    sloped: bool = False,
    zone_areas: dict[str, float] | None = None,
    slope_factors: dict[str, float] | None = None,
) -> IrrigationPlan:
    """Litres to apply today, per slope zone when the field is sloped."""
    day = max(1, int(day_of_season))
    kc = crop.kc_on_day(day)
    etc = kc * et0_mm
    eff_rain = effective_rain_mm(rain_today_mm)
    net_mm = max(0.0, etc - eff_rain)
    method_key = normalize_method(method)
    efficiency = METHOD_EFFICIENCY[method_key]
    soil_key = normalize_soil(soil)
    notes: list[str] = []

    root = 0.25 if crop.stage_on_day(day) == "early growth" else ROOT_DEPTH_M.get(crop.name, 0.6)
    readily_available = AVAILABLE_WATER_MM_PER_M[soil_key] * root * ALLOWED_DEPLETION
    interval = int(max(1, min(7, readily_available // max(etc, 0.5))))

    factors = dict(DEFAULT_SLOPE_FACTORS)
    factors.update(slope_factors or {})
    if sloped:
        areas = zone_areas or {pos: area_m2 / 3 for pos in SLOPE_POSITIONS}
    else:
        areas = {"whole farm": area_m2}

    rain_soon = effective_rain_mm(rain_next_days_mm) >= etc and (rain_chance_next or 0) >= 60
    today_share = 0.5 if rain_soon else 1.0

    zones: list[ZoneResult] = []
    for position, zone_area in areas.items():
        factor = factors.get(position, 1.0) if sloped else 1.0
        litres = net_mm * factor * zone_area / efficiency * today_share
        per_stand = (
            litres / zone_area * crop.area_per_stand_m2
            if crop.area_per_stand_m2 and zone_area > 0
            else None
        )
        zones.append(ZoneResult(position, zone_area, factor, litres, per_stand))

    if crop.name == "rice":
        notes.append("Lowland rice is usually kept under 2 to 5 cm of standing water; these litres are for upland rice.")
    if net_mm == 0:
        notes.append("Today's rain covers the crop's need. No watering needed today.")
    elif rain_soon:
        notes.append("Good rain is likely in the next two days, so give only half today and check the soil.")
    if soil_key == "sandy":
        notes.append("Sandy soil drains fast: split the water into a morning and an evening watering.")
    if method_key in ("furrow", "basin", "flood") and sloped:
        notes.append("On a slope, furrows should follow the contour so water does not rush to the bottom.")
    if crop.kc_estimated:
        notes.append(f"The water figure for {crop.name} is estimated from similar crops.")

    return IrrigationPlan(
        crop=crop.name,
        day_of_season=day,
        stage=crop.stage_on_day(day),
        kc=round(kc, 2),
        et0_mm=round(et0_mm, 2),
        et0_is_typical=et0_is_typical,
        etc_mm=etc,
        rain_today_mm=rain_today_mm,
        effective_rain_mm=eff_rain,
        net_need_mm=net_mm,
        method=method_key,
        efficiency=efficiency,
        soil=soil_key,
        interval_days=interval,
        zones=zones,
        total_litres_today=sum(z.litres_today for z in zones),
        rain_soon=rain_soon,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Water loss (problem 1)
# ---------------------------------------------------------------------------

LOSS_SIGNS = {
    "wet_patch": "leak",
    "puddle": "leak",
    "dry_line_end": "pressure loss",
    "weak_flow_far": "pressure loss",
    "one_zone_dry": "blocked line",
    "blocked_emitters": "blocked line",
    "pump_runs_long": "leak or burst",
    "water_runs_off": "runoff",
    "bottom_too_wet": "runoff",
}

CAUSE_ADVICE = {
    "leak": "Walk the pipe while the pump runs and look for wet spots or spraying joints. Tighten or tape the joint, or replace the broken piece.",
    "pressure loss": "The far end gets too little pressure. Make lines shorter, water one section at a time, or raise the tank higher.",
    "blocked line": "Flush the line by opening the end cap for a minute. Clean or replace blocked drip holes, and put a filter after the tank.",
    "leak or burst": "If the pump runs longer than before for the same area, there is a big leak or a burst. Check the main pipe first.",
    "runoff": "Water is running off instead of soaking in. Water slowly in two short turns, add mulch, and make small ridges across the slope.",
}


@dataclass
class LossCheck:
    water_used_litres: float | None
    water_needed_litres: float | None
    loss_percent: float | None
    likely_causes: list[str]
    advice: list[str]

    def as_facts(self) -> dict:
        return {
            "water_used_litres": round_sensible(self.water_used_litres) if self.water_used_litres else None,
            "water_needed_litres": round_sensible(self.water_needed_litres) if self.water_needed_litres else None,
            "estimated_waste_percent": round(self.loss_percent) if self.loss_percent is not None else None,
            "likely_causes": self.likely_causes,
            "what_to_do": self.advice,
        }


def check_loss(
    signs: list[str] | None,
    water_used_litres: float | None = None,
    water_needed_litres: float | None = None,
) -> LossCheck:
    """Compare water used with water needed, and read the signs of loss."""
    loss = None
    if water_used_litres and water_needed_litres and water_used_litres > 0:
        loss = max(0.0, (water_used_litres - water_needed_litres) / water_used_litres * 100)
    causes: list[str] = []
    for sign in signs or []:
        cause = LOSS_SIGNS.get(str(sign).strip().lower())
        if cause and cause not in causes:
            causes.append(cause)
    if loss is not None and loss >= 25 and not causes:
        causes.append("runoff")
    return LossCheck(
        water_used_litres=water_used_litres,
        water_needed_litres=water_needed_litres,
        loss_percent=loss,
        likely_causes=causes,
        advice=[CAUSE_ADVICE[c] for c in causes],
    )


def calibrate_slope_factors(
    factors: dict[str, float] | None, position: str, feedback: str
) -> dict[str, float]:
    """Adjust one zone's factor from the farmer's report (10 % per report)."""
    updated = dict(DEFAULT_SLOPE_FACTORS)
    updated.update(factors or {})
    if position not in SLOPE_POSITIONS:
        return updated
    change = {"too_wet": 0.9, "too_dry": 1.1}.get(feedback)
    if change:
        low, high = FACTOR_LIMITS
        updated[position] = round(min(high, max(low, updated[position] * change)), 3)
    return updated
