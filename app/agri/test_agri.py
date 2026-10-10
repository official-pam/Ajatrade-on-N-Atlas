"""Tests for the farm-advice calculators (no network)."""

from __future__ import annotations

from datetime import date

import pytest

from app.agri import fertilizer, germination, irrigation, soil, varieties, water_sources, weeds
from app.agri.crops import find_crop, stands_per_hectare
from app.agri.units import bags, parse_dimensions, to_square_metres
from app.agri.zones import find_state, place_from_state, season_days_left, zone_for_latitude


# ---------------------------------------------------------------- crops / units


@pytest.mark.parametrize(
    "text,expected",
    [("maize", "maize"), ("Corn", "maize"), ("my guinea corn", "sorghum"), ("agbado", "maize"),
     ("tomatoes and pepper", "tomato"), ("sweet potato", None), ("ata rodo", "pepper")],
)
def test_find_crop(text, expected):
    crop = find_crop(text)
    assert (crop.name if crop else None) == expected


def test_kc_curve_follows_fao56_shape():
    maize = find_crop("maize")
    assert maize.kc_on_day(1) == pytest.approx(0.3)
    assert maize.kc_on_day(20 + 35 + 10) == pytest.approx(1.2)
    assert 0.3 < maize.kc_on_day(37) < 1.2
    assert maize.kc_on_day(500) == pytest.approx(0.6)


def test_units():
    assert to_square_metres(1, "acre") == pytest.approx(4046.86)
    assert to_square_metres(2, "plots") == pytest.approx(929.0)
    assert to_square_metres("1,5", "ha") is None or to_square_metres("1.5", "ha") == 15000
    assert to_square_metres(1, "bananas") is None
    assert parse_dimensions("20 by 30 metres") == pytest.approx(600)
    assert parse_dimensions("50x100 ft") == pytest.approx(464.5, rel=1e-3)
    assert bags(130) == 2.5
    assert stands_per_hectare(find_crop("maize")) == 53333


def test_zones_and_states():
    assert find_state("Kano State") == "kano"
    assert find_state("I farm near Zaria") == "kaduna"
    assert find_state("abuja") == "fct"
    assert find_state("somewhere") is None
    assert zone_for_latitude(12.5) == "sudan_sahel"
    assert zone_for_latitude(7.4) == "forest"
    assert place_from_state("kano").fertilizer_region == "north"
    assert place_from_state("oyo").fertilizer_region == "south"
    assert season_days_left("sudan_sahel", date(2026, 9, 1)) == 29
    assert season_days_left("sudan_sahel", date(2026, 11, 1)) == 0


# ---------------------------------------------------------------- irrigation


def test_water_need_matches_hand_calculation():
    maize = find_crop("maize")
    plan = irrigation.water_need(maize, day_of_season=70, area_m2=100, et0_mm=5.0, method="drip")
    # mid-season Kc 1.2 x 5 mm = 6 mm = 6 L/m2; /0.9 drip = 666.7 L for 100 m2
    assert plan.etc_mm == pytest.approx(6.0)
    assert plan.total_litres_today == pytest.approx(666.67, rel=1e-3)
    per_plant = plan.zones[0].litres_per_stand
    assert per_plant == pytest.approx(6.0 / 0.9 * 0.1875, rel=1e-3)


def test_rain_reduces_need_and_slope_zones_differ():
    maize = find_crop("maize")
    dry = irrigation.water_need(maize, 70, 300, 5.0)
    wet = irrigation.water_need(maize, 70, 300, 5.0, rain_today_mm=10)
    assert wet.total_litres_today < dry.total_litres_today
    covered = irrigation.water_need(maize, 70, 300, 5.0, rain_today_mm=20)
    assert covered.total_litres_today == 0
    sloped = irrigation.water_need(maize, 70, 300, 5.0, sloped=True)
    litres = {z.position: z.litres_today for z in sloped.zones}
    assert litres["uphill"] > litres["middle"] > litres["downhill"]
    soon = irrigation.water_need(maize, 70, 300, 5.0, rain_next_days_mm=30, rain_chance_next=80)
    assert soon.rain_soon and soon.total_litres_today == pytest.approx(dry.total_litres_today / 2)


def test_soil_changes_interval():
    maize = find_crop("maize")
    sandy = irrigation.water_need(maize, 70, 100, 5.0, soil="sandy soil")
    clay = irrigation.water_need(maize, 70, 100, 5.0, soil="clay")
    assert sandy.interval_days < clay.interval_days


def test_loss_check_and_calibration():
    check = irrigation.check_loss(["wet_patch", "dry_line_end"], water_used_litres=2000, water_needed_litres=1200)
    assert check.loss_percent == pytest.approx(40)
    assert check.likely_causes == ["leak", "pressure loss"]
    factors = irrigation.calibrate_slope_factors(None, "downhill", "too_wet")
    assert factors["downhill"] == pytest.approx(0.585)
    for _ in range(20):
        factors = irrigation.calibrate_slope_factors(factors, "downhill", "too_wet")
    assert factors["downhill"] == pytest.approx(0.4)


# ---------------------------------------------------------------- soil


def test_soil_ratings_and_measured_vs_estimated():
    readings = soil.readings_from_values(
        {"ph": 5.1, "available_p_mg_per_kg": 25, "exchangeable_k_cmol_per_kg": 0.1, "organic_matter_percent": 1.2},
        "lab",
    )
    report = soil.interpret(readings)
    assert report.ratings["ph"] == "strongly acidic"
    assert report.ratings["available_p_mg_per_kg"] == "high"
    assert report.ratings["exchangeable_k_cmol_per_kg"] == "low"
    assert report.ratings["organic_carbon_percent"] == "low"  # 1.2 OM = 0.7 OC
    assert report.measured and not report.estimated
    assert not report.needs_lab_test
    assert any("lime" in a for a in report.actions)


def test_npk_sensor_values_are_estimates():
    readings = soil.readings_from_values({"nitrogen": "low", "phosphorus": "medium"}, "npk_sensor")
    report = soil.interpret(readings)
    assert report.ratings["nitrogen"] == "low (estimate)"
    assert report.needs_lab_test


# ---------------------------------------------------------------- fertilizer


def test_maize_north_plan_supplies_target_nutrients():
    plan = fertilizer.recommend(find_crop("maize"), "north", 10_000)
    kg = {p.product: p.kg_total for p in plan.products}
    n = kg["npk15"] * 0.15 + kg["urea"] * 0.46
    assert n == pytest.approx(120, rel=0.01)
    assert kg["npk15"] * 0.15 == pytest.approx(60, rel=0.01)
    # NPK goes in the first application, urea in the second
    assert plan.schedule[0]["apply"][0]["product"] == "NPK 15-15-15"
    assert plan.schedule[1]["apply"][0]["product"] == "Urea"


def test_fertilizer_scales_with_area_and_prices():
    plan = fertilizer.recommend(
        find_crop("maize"), "south", 4046.86, prices_per_bag={"npk15": 40000, "urea": 38000}, budget_naira=100000
    )
    facts = plan.as_facts()
    assert facts["farm_size_m2"] == 4047
    assert plan.total_cost_naira and plan.total_cost_naira > 100000
    assert 0 < plan.affordable_share < 1
    assert any("budget" in w for w in plan.warnings)


def test_soil_and_slope_adjust_fertilizer():
    base = fertilizer.recommend(find_crop("maize"), "north", 10_000)
    high_p = fertilizer.recommend(find_crop("maize"), "north", 10_000, soil_ratings={"available_p_mg_per_kg": "high"})
    assert high_p.targets_kg_per_ha[1] == base.targets_kg_per_ha[1] / 2
    downhill = fertilizer.recommend(find_crop("maize"), "north", 10_000, sloped=True, slope_position="downhill")
    assert downhill.targets_kg_per_ha[0] < base.targets_kg_per_ha[0]
    assert len(downhill.schedule) == 3
    acid = fertilizer.recommend(find_crop("maize"), "north", 10_000, soil_ratings={"ph": "strongly acidic"})
    assert any("Lime" in w for w in acid.warnings)


def test_no_official_rate_means_no_numbers():
    assert fertilizer.recommend(find_crop("tomato"), "south", 1000) is None


def test_groundnut_gets_no_nitrogen():
    plan = fertilizer.recommend(find_crop("groundnut"), "north", 10_000)
    assert {p.product for p in plan.products} == {"ssp", "mop"}


# ---------------------------------------------------------------- varieties


def test_late_planting_in_far_north_needs_extra_early_maize():
    need = varieties.what_the_farm_needs("maize", "sudan_sahel", date(2026, 7, 15))
    assert need.maturity_classes == ["extra-early"]
    assert "drought" in need.stresses
    matches = varieties.match("maize", "sudan_sahel", need)
    assert matches[0].name == "SAMMAZ 70"
    assert matches[0].fit in ("good fit", "possible")
    assert all(m.fit == "poor fit" for m in matches if m.name == "SAMMAZ 71")


def test_striga_pushes_striga_resistant_maize():
    need = varieties.what_the_farm_needs("maize", "northern_guinea", date(2026, 5, 20), striga=True)
    best = varieties.match("maize", "northern_guinea", need)[0]
    assert best.name in ("SAMMAZ 77", "SAMMAZ 70")
    assert any("Striga" in r for r in best.reasons)


def test_season_over_says_so():
    need = varieties.what_the_farm_needs("maize", "sudan_sahel", date(2026, 9, 25))
    assert need.maturity_classes == []
    assert "too short" in need.reasons[0]


# ---------------------------------------------------------------- germination


@pytest.mark.parametrize("planted,up,verdict", [(100, 92, "good"), (100, 65, "fair"), (100, 30, "poor")])
def test_germination_verdicts(planted, up, verdict):
    result = germination.assess(planted, up)
    assert result.verdict == verdict
    assert round(result.percent) == up


def test_germination_clamps_and_causes():
    result = germination.assess(50, 80, ["planted_too_deep"])
    assert result.germinated == 50
    weak = germination.assess(100, 40, ["planted_too_deep"], days_since_planting=10)
    assert any("too deep" in a for a in weak.advice)


# ---------------------------------------------------------------- water sources


def _bowl_grid(low_row: int, low_col: int) -> list[float]:
    grid = []
    for r in range(5):
        for c in range(5):
            grid.append(300 + 4 * abs(r - low_row) + 4 * abs(c - low_col))
    return grid


def test_terrain_finds_lowest_ground_direction():
    summary = water_sources.summarize_terrain(_bowl_grid(4, 2))  # lowest due south
    assert summary.lowest_direction == "south"
    assert summary.lowest_distance_m == 300
    assert summary.position in ("mid slope", "lower slope")
    valley = water_sources.summarize_terrain(_bowl_grid(2, 2))
    assert valley.position == "valley bottom"
    flat = water_sources.summarize_terrain([250.0] * 25)
    assert flat.position == "flat"


def test_water_likelihood_and_pond():
    valley = water_sources.summarize_terrain(_bowl_grid(2, 2))
    result = water_sources.assess(valley, 1300, 10_000, wells_nearby_with_water=True, stream_or_fadama_nearby=True)
    assert result.groundwater_likelihood == "high"
    assert result.pond_catch_m3 == pytest.approx(3900)
    assert any("VES" in a for a in result.actions)
    hill = water_sources.summarize_terrain([300 - 4 * abs(r - 2) - 4 * abs(c - 2) for r in range(5) for c in range(5)])
    dry = water_sources.assess(hill, 500, 10_000, wells_nearby_with_water=False, rocky_ground=True)
    assert dry.groundwater_likelihood == "low"


def test_grid_points_centre_is_farm():
    points = water_sources.grid_points(9.0, 7.0)
    assert len(points) == 25 and points[12] == (9.0, 7.0)
    assert points[0][0] > 9.0  # first row is north


# ---------------------------------------------------------------- weeds


def test_striga_advice_has_no_herbicide_and_timing():
    advice = weeds.advise([{"type": "parasitic", "likely_species": "Striga hermonthica"}], "maize", "heavy",
                          weeks_after_planting=5)
    assert "striga" in advice.weeds
    assert advice.herbicide_note is None
    assert advice.see_extension_officer
    assert "week 3" in advice.timing


def test_spear_grass_herbicide_named_without_dose():
    advice = weeds.advise([{"type": "grass", "likely_species": "Imperata cylindrica"}], None, "moderate")
    assert "glyphosate" in advice.herbicide_note
    assert not any(ch.isdigit() for ch in advice.herbicide_note)
