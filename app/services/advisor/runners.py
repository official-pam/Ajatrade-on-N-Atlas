"""Compute each farm-advice answer from the collected facts.

Each runner returns the facts N-ATLaS may phrase, a short text card with the
exact numbers, a plain fallback sentence (used when N-ATLaS is unavailable or
its wording fails the number check), and farm facts worth remembering.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.agri import fertilizer, germination, irrigation, soil, varieties, water_sources, weather, weeds
from app.agri.crops import find_crop
from app.agri.units import describe_area, round_sensible
from app.agri.zones import Place, ZONE_NAMES, find_state, in_rainy_season, place_from_state

logger = logging.getLogger("advisor.runners")

DEFAULT_AREA_M2 = 100.0  # used only to give per-100 m2 figures


@dataclass
class RunResult:
    facts: dict[str, Any]
    card: str
    fallback: str
    profile_updates: dict[str, Any] = field(default_factory=dict)
    log: dict[str, Any] = field(default_factory=dict)
    followup: dict[str, Any] | None = None  # state to keep after the answer
    safety_check_doses: bool = False


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------


def place_from_dict(value: dict | None) -> Place | None:
    if not isinstance(value, dict) or "lat" not in value:
        return None
    return Place(
        label=value.get("label") or "your farm",
        latitude=float(value["lat"]),
        longitude=float(value["lon"]),
        state=value.get("state"),
        exact=bool(value.get("exact")),
    )


def place_to_dict(place: Place) -> dict:
    return {"label": place.label, "lat": place.latitude, "lon": place.longitude, "state": place.state, "exact": place.exact}


async def resolve_place(value: Any) -> dict | None:
    """Location text or pin -> stored place dict, or None if unknown."""
    if isinstance(value, dict) and "lat" in value:
        return value
    text = str(value or "").strip()
    if not text:
        return None
    found = await weather.geocode(text)
    if found:
        return place_to_dict(found)
    state = find_state(text)
    if state:
        return place_to_dict(place_from_state(state))
    return None


# ---------------------------------------------------------------------------
# Irrigation (problems 1 and 4)
# ---------------------------------------------------------------------------


async def run_irrigation(data: dict, profile: dict, today: date) -> RunResult:
    crop = find_crop(data.get("crop"))
    place = place_from_dict(data.get("location"))
    area_m2 = float((data.get("area") or {}).get("m2") or DEFAULT_AREA_M2)
    if crop is None:
        return _unknown_crop(data.get("crop"), "watering")

    forecast = await weather.forecast(place.latitude, place.longitude) if place else None
    if forecast:
        et0, rain_today = forecast.today.et0_mm, forecast.today.rain_mm
        rain_next = sum(d.rain_mm for d in forecast.next_days)
        chance = max((d.rain_chance or 0) for d in forecast.next_days) if forecast.next_days else None
        typical = False
    else:
        zone = place.zone if place else "southern_guinea"
        season = "rainy" if in_rainy_season(zone, today) else "dry"
        et0, rain_today, rain_next, chance, typical = weather.TYPICAL_ET0[(zone, season)], 0.0, 0.0, None, True

    plan = irrigation.water_need(
        crop,
        data.get("crop_age_days") or 30,
        area_m2,
        et0,
        rain_today_mm=rain_today,
        rain_next_days_mm=rain_next,
        rain_chance_next=chance,
        et0_is_typical=typical,
        method=data.get("method"),
        soil=data.get("soil_texture") or profile.get("soil_texture"),
        sloped=bool(data.get("sloped")),
        slope_factors=profile.get("slope_factors"),
    )
    facts = plan.as_facts()
    facts["farm_size"] = describe_area(area_m2)
    if place:
        facts["place"] = place.label

    loss = None
    if data.get("loss_signs") or data.get("water_used_litres"):
        loss = irrigation.check_loss(
            data.get("loss_signs"),
            data.get("water_used_litres"),
            plan.total_litres_today * plan.interval_days if data.get("water_used_litres") else None,
        )
        facts["water_loss"] = loss.as_facts()

    lines = [f"Water plan: {crop.name}, {facts['farm_size']}, {plan.stage} (day {plan.day_of_season})"]
    for zone in facts["zones"]:
        per_plant = f", about {zone['litres_per_plant']} L per plant" if zone["litres_per_plant"] else ""
        lines.append(f"- {zone['where'].capitalize()}: {zone['litres_today']:g} litres today{per_plant}")
    lines.append(f"- Water every {plan.interval_days} day(s) on {plan.soil} soil with a {plan.method}.")
    if typical:
        lines.append("- Based on typical weather for your area, not today's forecast.")
    elif forecast:
        lines.append(f"- Today's forecast: {forecast.today.rain_mm:g} mm rain, crop uses {facts['crop_water_use_mm_today']} mm.")
    lines += [f"- {note}" for note in plan.notes[:2]]
    if loss and loss.loss_percent is not None:
        lines.append(f"- You used more water than needed: about {round(loss.loss_percent)}% may be wasted.")
    if loss:
        lines += [f"- {a}" for a in loss.advice[:2]]
    if data.get("sloped"):
        lines.append("After watering, tell me if the top or the bottom stays too wet or too dry, and I will adjust.")

    total = facts["total_litres_today"]
    fallback = (
        f"Your {crop.name} needs about {total:g} litres of water today."
        if total
        else f"Your {crop.name} does not need watering today; the rain is enough."
    )
    if data.get("sloped") and len(facts["zones"]) == 3:
        fallback += " Give the top of the slope the most water and the bottom the least."

    updates = {k: data[k] for k in ("area", "location", "sloped", "method", "soil_texture") if data.get(k) is not None}
    followup = {"stage": "slope_feedback"} if data.get("sloped") else None
    return RunResult(facts, "\n".join(lines), fallback, updates, {"crop": crop.name}, followup)


def apply_slope_feedback(profile: dict, position: str, feedback: str) -> dict:
    return irrigation.calibrate_slope_factors(profile.get("slope_factors"), position, feedback)


# ---------------------------------------------------------------------------
# Soil health (problem 2)
# ---------------------------------------------------------------------------


async def run_soil(data: dict, profile: dict, today: date) -> RunResult:
    info = data.get("soil_input") or {}
    source = str(info.get("source") or "farmer_estimate").lower()
    readings = soil.readings_from_values(info.get("values") or {}, source)
    readings += soil.estimate_from_description(info.get("signs") or {})
    texture = irrigation.normalize_soil(data.get("soil_texture")) if data.get("soil_texture") else None
    report = soil.interpret(readings, texture)
    facts = report.as_facts()

    lines = [f"Soil check: overall {report.overall}"]
    for name, rating in report.ratings.items():
        lines.append(f"- {name.replace('_', ' ')}: {rating}")
    if report.measured:
        lines.append("Measured: " + "; ".join(report.measured))
    if report.estimated:
        lines.append("Estimated (not measured): " + "; ".join(report.estimated))
    lines.append("What to do:")
    lines += [f"- {a}" for a in report.actions]

    if report.overall == "unknown":
        fallback = "I could not rate your soil from that. Please send the soil test numbers, or describe the soil colour and how the crop looks."
    else:
        fallback = f"Your soil looks {report.overall}. " + (report.actions[0] if report.actions else "")
    updates: dict[str, Any] = {}
    if report.ratings:
        updates["soil_ratings"] = report.ratings
        updates["soil_checked_on"] = today.isoformat()
    if texture:
        updates["soil_texture"] = texture
    return RunResult(facts, "\n".join(lines), fallback, updates, {"crop": data.get("crop")})


# ---------------------------------------------------------------------------
# Fertilizer (problem 3)
# ---------------------------------------------------------------------------


async def run_fertilizer(data: dict, profile: dict, today: date) -> RunResult:
    crop = find_crop(data.get("crop"))
    place = place_from_dict(data.get("location"))
    area_m2 = float((data.get("area") or {}).get("m2") or 10_000)
    if crop is None:
        return _unknown_crop(data.get("crop"), "fertilizer")
    if not fertilizer.has_recommendation(crop):
        text = (
            f"I do not have an official fertilizer rate for {crop.name} yet, and I will not guess one. "
            "Please ask your extension officer or ADP office. Adding compost or well-rotted manure is always safe."
        )
        return RunResult({"crop": crop.name, "official_rate_available": False, "message": text}, text, text, log={"crop": crop.name})

    heavy_rain = False
    if place:
        forecast = await weather.forecast(place.latitude, place.longitude)
        if forecast:
            heavy_rain = any(d.rain_mm >= 20 for d in [forecast.today, *forecast.next_days])
    plan = fertilizer.recommend(
        crop,
        place.fertilizer_region if place else "south",
        area_m2,
        soil_ratings=profile.get("soil_ratings"),
        compound=data.get("compound"),
        prices_per_bag=data.get("prices"),
        budget_naira=data.get("budget_naira"),
        slope_position=data.get("slope_position"),
        sloped=bool(data.get("sloped") or profile.get("sloped")),
        heavy_rain_soon=heavy_rain,
        previous_crop_legume=bool(data.get("previous_crop_legume")),
    )
    facts = plan.as_facts()
    facts["farm_size"] = describe_area(area_m2)
    if place:
        facts["zone"] = ZONE_NAMES[place.zone]
    if profile.get("soil_ratings"):
        facts["used_your_soil_check"] = True

    lines = [f"Fertilizer plan: {crop.name}, {facts['farm_size']}"]
    for p in facts["products"]:
        per = f", {p['grams_per_plant']:g} g per plant" if p["grams_per_plant"] else f", {p['grams_per_square_metre']:g} g per square metre"
        cost = f", about N{p['cost_naira']:,.0f}" if p["cost_naira"] else ""
        lines.append(f"- {p['product']}: {p['total_kg']:g} kg ({p['bags_of_50kg']:g} bags){per}{cost}")
    lines.append("When:")
    for i, step in enumerate(facts["when_to_apply"], start=1):
        items = ", ".join(f"{a['product']} {a['kg']:g} kg" for a in step["apply"])
        lines.append(f"{i}) {step['when'].capitalize()}: {items}")
    if facts["total_cost_naira"]:
        lines.append(f"Total cost: about N{facts['total_cost_naira']:,.0f}")
    lines += [f"- {a}" for a in plan.adjustments[:2]]
    lines += [f"! {w}" for w in plan.warnings[:2]]
    lines.append("Place fertilizer in a small hole or band beside the plant and cover it with soil.")
    lines.append(f"Source: {plan.source}. Confirm with your extension officer.")

    first = facts["products"][0]
    fallback = f"For your {crop.name}, you need about {first['bags_of_50kg']:g} bags of {first['product']}"
    if len(facts["products"]) > 1:
        second = facts["products"][1]
        fallback += f" and {second['bags_of_50kg']:g} bags of {second['product']}"
    fallback += ". I have sent the full plan with the timing as text."
    updates = {k: data[k] for k in ("area", "location") if data.get(k) is not None}
    return RunResult(facts, "\n".join(lines), fallback, updates, {"crop": crop.name})


# ---------------------------------------------------------------------------
# Water source (problem 5)
# ---------------------------------------------------------------------------


async def run_water_source(data: dict, profile: dict, today: date) -> RunResult:
    place = place_from_dict(data.get("location"))
    signs = data.get("local_signs") or {}
    area_m2 = float((data.get("area") or profile.get("area") or {}).get("m2") or 10_000)
    terrain = None
    rain = None
    if place:
        levels = await weather.elevations(water_sources.grid_points(place.latitude, place.longitude))
        if levels and len(levels) == water_sources.GRID_STEPS ** 2:
            terrain = water_sources.summarize_terrain(levels)
        rain = await weather.last_year_rainfall_mm(place.latitude, place.longitude, today)
    result = water_sources.assess(
        terrain,
        rain,
        area_m2,
        wells_nearby_with_water=signs.get("wells_nearby_with_water"),
        well_depth_m=signs.get("well_depth_m"),
        stream_or_fadama_nearby=signs.get("stream_or_fadama_nearby"),
        green_in_dry_season=signs.get("green_in_dry_season"),
        rocky_ground=signs.get("rocky_ground"),
    )
    facts = result.as_facts()
    if place:
        facts["place"] = place.label
        facts["location_is_exact"] = place.exact

    lines = [f"Water for your farm: chance of good groundwater is {result.groundwater_likelihood} (a likelihood, not a detection)"]
    lines += [f"- Because {r}" for r in result.reasons[:4]]
    if facts.get("lowest_ground"):
        lines.append(f"- Lowest ground: {facts['lowest_ground']}")
    if result.pond_catch_m3:
        lines.append(
            f"- Rain on {describe_area(area_m2)} could fill about {result.pond_catch_m3:,.0f} cubic metres of pond a year. {result.pond_example}"
        )
    lines += [f"- {a}" for a in result.actions]
    if place and not place.exact:
        lines.append("Send your location pin for a more exact check.")
    fallback = (
        f"The chance of good groundwater on your farm is {result.groundwater_likelihood}. "
        + (result.actions[0] if result.actions else "")
    )
    updates = {"location": data["location"]} if data.get("location") else {}
    return RunResult(facts, "\n".join(lines), fallback, updates)


# ---------------------------------------------------------------------------
# Weeds (problem 6)
# ---------------------------------------------------------------------------


async def run_weeds(data: dict, profile: dict, today: date) -> RunResult:
    info = data.get("weed_info") or {}
    if info.get("weeds"):  # from the photo
        weed_list = info["weeds"]
        cover = info.get("weed_cover")
        striga = bool(info.get("striga_seen"))
        crop_name = (info.get("crop") or {}).get("name") if isinstance(info.get("crop"), dict) else None
    else:
        weed_list = [{"name": info.get("name"), "type": info.get("type")}]
        cover = info.get("cover")
        striga = bool(info.get("striga_seen"))
        crop_name = None
    crop = find_crop(data.get("crop") or crop_name)
    advice = weeds.advise(
        weed_list,
        crop.name if crop else None,
        cover,
        weeks_after_planting=data.get("weeks_after_planting"),
        striga_seen=striga,
    )
    facts = advice.as_facts()
    lines = ["Weed control"]
    if advice.weeds:
        lines.append("- Weed: " + ", ".join(advice.weeds))
    if advice.timing:
        lines.append(f"- {advice.timing}")
    lines += [f"- {a}" for a in advice.actions]
    if advice.herbicide_note:
        lines.append(f"- Herbicide option: {advice.herbicide_note}. {facts['always_say']}")
    if advice.see_extension_officer:
        lines.append("- This is a serious Striga problem; please also see your extension officer.")
    fallback = advice.actions[0] if advice.actions else "Weed early, before the weeds flower and drop seed."
    return RunResult(facts, "\n".join(lines), fallback, log={"crop": crop.name if crop else None}, safety_check_doses=True)


# ---------------------------------------------------------------------------
# Variety choice (problem 7)
# ---------------------------------------------------------------------------


async def run_variety(data: dict, profile: dict, today: date) -> RunResult:
    crop = find_crop(data.get("crop"))
    place = place_from_dict(data.get("location"))
    if crop is None:
        return _unknown_crop(data.get("crop"), "seed")
    zone = place.zone if place else "southern_guinea"
    month = int(data.get("planting_month") or today.month)
    year = today.year if month >= today.month else today.year + 1
    planting = today if month == today.month else date(year, month, 1)
    stresses = data.get("stresses") or {}
    if profile.get("soil_ratings", {}).get("total_nitrogen_percent", "").startswith("low"):
        stresses.setdefault("low_soil_n", True)
    need = varieties.what_the_farm_needs(
        crop.name,
        zone,
        planting,
        drought_risk=bool(stresses.get("drought")),
        striga=bool(stresses.get("striga")),
        low_soil_n=bool(stresses.get("low_soil_n")),
        flood_prone=bool(stresses.get("flood")),
        fall_armyworm=bool(stresses.get("fall_armyworm")),
    )
    matches = varieties.match(crop.name, zone, need)
    asks = varieties.traits_to_look_for(need)
    facts = {
        "crop": crop.name,
        "zone": ZONE_NAMES[zone],
        "planting": planting.strftime("%B"),
        "season_note": need.reasons,
        "look_for": asks,
        "varieties": [{"name": m.name, "fit": m.fit, "why": m.reasons[:3]} for m in matches[:3]],
        "buy_certified_seed": True,
        "no_match_percentages_because": "not yet validated with field data",
    }
    lines = [f"Seed choice: {crop.name}, planting in {facts['planting']}, {facts['zone']}"]
    lines += [f"- {r}" for r in need.reasons]
    if asks:
        lines.append("- Ask the seed seller for: " + "; ".join(asks))
    for m in matches[:3]:
        lines.append(f"- {m.name}: {m.fit} ({'; '.join(m.reasons[:2])})")
    if not matches:
        lines.append("- I do not have a checked list of named varieties for this crop yet.")
    lines.append("Buy certified seed from a registered seed company or your ADP office.")
    good = [m.name for m in matches if m.fit == "good fit"]
    if not need.maturity_classes and crop.name in varieties.MATURITY_DAYS and crop.name != "cassava":
        fallback = need.reasons[0]
    elif good:
        fallback = f"For your farm, {good[0]} is a good fit. Ask for {', '.join(asks[:2]) or 'certified seed'}."
    else:
        fallback = f"Ask the seed seller for {', '.join(asks[:2]) or 'certified seed'}."
    updates = {"location": data["location"]} if data.get("location") else {}
    return RunResult(facts, "\n".join(lines), fallback, updates, {"crop": crop.name})


# ---------------------------------------------------------------------------
# Germination (problem 8)
# ---------------------------------------------------------------------------


async def run_germination(data: dict, profile: dict, today: date) -> RunResult:
    counts = data.get("counts") or {}
    result = germination.assess(
        counts.get("planted", 0),
        counts.get("germinated", 0),
        data.get("causes"),
        days_since_planting=data.get("days_since_planting"),
        from_photo=bool(counts.get("from_photo")),
        count_confidence=counts.get("count_confidence"),
    )
    facts = result.as_facts()
    lines = [
        f"Germination: {result.germinated} of {result.planted} came up ({round(result.percent)}%), {result.verdict}",
        *[f"- {a}" for a in result.advice],
        germination.SEED_TEST_STEPS,
    ]
    fallback = f"{round(result.percent)} percent of your seeds came up, which is {result.verdict}. " + (
        result.advice[0] if result.advice else ""
    )
    return RunResult(facts, "\n".join(lines), fallback, log={"crop": data.get("crop")})


# ---------------------------------------------------------------------------


def _unknown_crop(name: Any, topic: str) -> RunResult:
    text = (
        f"I do not have {topic} data for {name or 'that crop'} yet. "
        "I can help with maize, sorghum, millet, rice, cassava, yam, cowpea, soybean, groundnut, "
        "tomato, pepper, onion, okra, vegetables, cabbage and watermelon."
    )
    return RunResult({"message": text}, text, text)


RUNNERS = {
    "irrigation": run_irrigation,
    "soil_health": run_soil,
    "fertilizer": run_fertilizer,
    "water_source": run_water_source,
    "weed_control": run_weeds,
    "variety_choice": run_variety,
    "germination": run_germination,
}


def describe_number(value: float) -> str:
    return f"{round_sensible(value):g}"
