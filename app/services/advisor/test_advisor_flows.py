"""End-to-end conversations through the farm-advice engine.

N-ATLaS, Open-Meteo, the vision model and the database are replaced by
fakes, so these run offline. They check that each flow asks for what it
needs, accepts voice/text/photo/location answers, computes the right
numbers, and that bad model output never reaches the farmer.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from app.agri import weather
from app.schemas.memory import User
from app.services import natlas, vision
from app.services.advisor import compose, engine, extract


class FakeStorage:
    def __init__(self) -> None:
        self.saved: list[dict] = []
        self.logs: list[dict] = []

    async def save_user(self, user: User) -> None:
        self.saved.append(json.loads(json.dumps(user.flow_data, default=str)))

    async def log_diagnosis(self, **kwargs) -> None:
        self.logs.append(kwargs)


class FakeNatlas:
    """Scripted N-ATLaS: extraction replies are queued per test."""

    def __init__(self) -> None:
        self.extractions: list[dict] = []
        self.summary = "Water your maize well today."
        self.calls: list[str] = []

    async def chat(self, system_prompt: str, user_text: str, **kwargs) -> str:
        if system_prompt == extract.EXTRACT_SYSTEM:
            self.calls.append("extract")
            facts = self.extractions.pop(0) if self.extractions else {}
            return json.dumps({"facts": facts, "wants_menu": False, "other_service": None})
        if system_prompt == compose.SYSTEM:
            self.calls.append("compose")
            return self.summary
        self.calls.append("other")
        return "{}"


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch):
    store = FakeStorage()
    fake = FakeNatlas()
    monkeypatch.setattr(engine, "storage", store)
    monkeypatch.setattr(natlas, "chat", fake.chat)
    monkeypatch.setattr(engine, "_today", lambda: date(2026, 7, 15))

    async def no_geocode(name):
        return None

    async def forecast(lat, lon):
        today = weather.DayWeather(date(2026, 7, 15), et0_mm=5.0, rain_mm=0.0, rain_chance=10, tmax_c=33)
        tomorrow = weather.DayWeather(date(2026, 7, 16), et0_mm=5.0, rain_mm=0.0, rain_chance=10, tmax_c=33)
        return weather.Forecast(today=today, next_days=[tomorrow], past_week_rain_mm=4.0)

    async def elevations(points):
        return [300 + 4 * abs(i // 5 - 4) + 4 * abs(i % 5 - 2) for i in range(len(points))]

    async def rainfall(lat, lon, today=None):
        return 1100.0

    monkeypatch.setattr(weather, "geocode", no_geocode)
    monkeypatch.setattr(weather, "forecast", forecast)
    monkeypatch.setattr(weather, "elevations", elevations)
    monkeypatch.setattr(weather, "last_year_rainfall_mm", rainfall)
    return store, fake


def _user() -> User:
    return User(phone="2348000000000", state="active", language="english", consent_status="granted")


def _body(reply, i=0) -> str:
    return reply.messages[i]["text"]["body"]


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- irrigation


def test_irrigation_full_conversation(env):
    store, fake = env
    user = _user()
    reply = run(engine.start_flow(user, "irrigation"))
    assert "Which crop" in _body(reply)
    assert user.flow == "irrigation"

    reply = run(engine.continue_flow(user, "maize"))
    assert "weeks ago" in _body(reply)
    reply = run(engine.continue_flow(user, "about 10 weeks"))
    assert "How big" in _body(reply)
    reply = run(engine.continue_flow(user, "2 plots"))
    assert "Where is the farm" in _body(reply)
    reply = run(engine.continue_flow(user, "Kano"))
    assert "How do you water" in _body(reply)
    reply = run(engine.continue_flow(user, "with a hose"))
    assert "slope" in _body(reply)
    reply = run(engine.continue_flow(user, "yes it is on a hill side"))

    # Final answer: spoken summary + text card, flow waits for slope feedback.
    assert len(reply.messages) == 2
    assert reply.messages[1]["voice"] is False
    card = _body(reply, 1)
    # day 70 maize: Kc 1.2 x 5 mm = 6 mm; 929 m2 in thirds; hose 70 %
    # -> 309.7 m2 x 6 / 0.7 = 2654 L, spoken rounded to 2700; 1.6 L per plant
    assert "Uphill: 2700 litres today, about 1.6 L per plant" in card
    assert "Downhill" in card and "Water every" in card
    assert user.flow_data.get("stage") == "slope_feedback"
    assert store.logs and store.logs[0]["source"] == "advisor:irrigation"
    assert user.farm_profile["location"]["state"] == "kano"

    # Feedback calibrates the downhill zone for next time.
    reply = run(engine.continue_flow(user, "the bottom part is too wet"))
    assert "less water" in _body(reply)
    assert user.farm_profile["slope_factors"]["downhill"] == pytest.approx(0.585)
    assert user.flow is None


def test_irrigation_one_message_with_everything_needs_no_questions(env):
    store, fake = env
    user = _user()
    user.farm_profile = {"method": "drip", "sloped": False}
    reply = run(engine.start_flow(user, "irrigation", opener="my maize planted 3 weeks ago on 1 acre in Kaduna"))
    assert len(reply.messages) == 2, _body(reply)
    assert "Whole farm" in _body(reply, 1)
    assert user.flow is None


def test_place_not_found_is_asked_again(env):
    user = _user()
    run(engine.start_flow(user, "irrigation", opener="maize 5 weeks 1 acre"))
    reply = run(engine.continue_flow(user, "Abcxyz village"))
    assert "could not find that place" in _body(reply)


def test_location_pin_answers_the_location_question(env):
    user = _user()
    run(engine.start_flow(user, "variety_choice", opener="maize"))
    pin = {"label": "My farm", "lat": 12.0, "lon": 8.5, "state": "kano", "exact": True}
    reply = run(engine.continue_flow(user, "", location=pin))
    assert "When will you plant" in _body(reply)


def test_menu_word_exits_flow(env):
    user = _user()
    run(engine.start_flow(user, "fertilizer"))
    reply = run(engine.continue_flow(user, "menu"))
    assert reply.show_menu and user.flow is None


# ---------------------------------------------------------------- fertilizer


def test_fertilizer_uses_soil_check_and_prices(env):
    store, fake = env
    user = _user()
    user.farm_profile = {"soil_ratings": {"available_p_mg_per_kg": "high", "ph": "slightly acidic (good for most crops)"}}
    fake.extractions = [{"prices": {"NPK": 42000, "urea": "38,000"}}]
    run(engine.start_flow(user, "fertilizer", opener="fertilizer for 1 hectare of maize in Kano"))
    reply = run(engine.continue_flow(user, "NPK is 42,000 naira and urea 38,000"))
    card = _body(reply, 1)
    assert "NPK 15-15-15" in card and "Urea" in card
    assert "Total cost" in card
    assert "phosphate is halved" in card
    assert "Source: FMARD" in card


def test_fertilizer_skip_prices_still_answers(env):
    user = _user()
    run(engine.start_flow(user, "fertilizer", opener="maize 1 acre in Oyo"))
    reply = run(engine.continue_flow(user, "skip"))
    assert len(reply.messages) == 2
    assert "Total cost" not in _body(reply, 1)


def test_fertilizer_refuses_to_guess_for_unlisted_crop(env):
    user = _user()
    run(engine.start_flow(user, "fertilizer", opener="tomato on 2 plots in Lagos"))
    reply = run(engine.continue_flow(user, "skip"))
    assert "will not guess" in _body(reply, 1)


# ---------------------------------------------------------------- soil


def test_soil_photo_of_lab_report(env, monkeypatch):
    user = _user()

    async def perceive(image_bytes, mime, task, farmer_text=None):
        assert task == "soil_report"
        return {"image_status": "ok", "photo_quality": {"usable": True},
                "values": {"ph": 5.0, "available_p_mg_per_kg": 6, "exchangeable_k_cmol_per_kg": 0.4}}

    monkeypatch.setattr(vision, "perceive", perceive)
    reply = run(engine.start_flow(user, "soil_health"))
    assert "soil test numbers" in _body(reply)
    reply = run(engine.continue_flow(user, "", photo=(b"jpg", "image/jpeg")))
    card = _body(reply, 1)
    assert "strongly acidic" in card and "lab test" in card
    assert user.farm_profile["soil_ratings"]["available_p_mg_per_kg"] == "low"


def test_soil_bad_photo_asks_for_retake(env, monkeypatch):
    user = _user()

    async def perceive(*args, **kwargs):
        return vision.unreadable()

    monkeypatch.setattr(vision, "perceive", perceive)
    run(engine.start_flow(user, "soil_health"))
    reply = run(engine.continue_flow(user, "", photo=(b"jpg", "image/jpeg")))
    assert "clear" in _body(reply) and user.flow == "soil_health"


def test_soil_description_is_an_estimate(env):
    store, fake = env
    user = _user()
    fake.extractions = [{"soil_input": {"signs": {"soil_colour": "light", "yellow_lower_leaves": True}}}]
    run(engine.start_flow(user, "soil_health"))
    reply = run(engine.continue_flow(user, "my soil is light coloured and the lower leaves are yellow"))
    card = _body(reply, 1)
    assert "Estimated" in card and "soil lab" in card


# ---------------------------------------------------------------- weeds / germination / water / variety


def test_weed_photo_striga(env, monkeypatch):
    user = _user()

    async def perceive(image_bytes, mime, task, farmer_text=None):
        return {"image_status": "ok", "photo_quality": {"usable": True}, "crop": {"name": "maize"},
                "weeds": [{"type": "parasitic", "likely_species": "Striga hermonthica"}],
                "weed_cover": "heavy", "striga_seen": True}

    monkeypatch.setattr(vision, "perceive", perceive)
    run(engine.start_flow(user, "weed_control"))
    reply = run(engine.continue_flow(user, "", photo=(b"jpg", "image/jpeg")))
    card = _body(reply, 1)
    assert "Striga" in card and "extension officer" in card
    assert "Herbicide option" not in card


def test_germination_from_numbers(env):
    user = _user()
    run(engine.start_flow(user, "germination"))
    reply = run(engine.continue_flow(user, "I planted 100 seeds and 62 came up"))
    assert "How many days" in _body(reply)
    reply = run(engine.continue_flow(user, "8 days"))
    card = _body(reply, 1)
    assert "62 of 100 came up (62%), fair" in card


def test_germination_photo_counts(env, monkeypatch):
    user = _user()

    async def perceive(*args, **kwargs):
        return {"image_status": "ok", "photo_quality": {"usable": True}, "positions_visible": 40,
                "germinated": 38, "not_germinated": 2, "count_confidence": "medium", "visible_problems": []}

    monkeypatch.setattr(vision, "perceive", perceive)
    run(engine.start_flow(user, "germination"))
    run(engine.continue_flow(user, "", photo=(b"jpg", "image/jpeg")))
    reply = run(engine.continue_flow(user, "skip"))
    assert "38 of 40 came up (95%), good" in _body(reply, 1)


def test_water_source_with_pin(env):
    store, fake = env
    user = _user()
    fake.extractions = [{"local_signs": {"wells_nearby_with_water": True}}]
    run(engine.start_flow(user, "water_source"))
    pin = {"label": "Farm", "lat": 9.6, "lon": 6.5, "state": "niger", "exact": True}
    reply = run(engine.continue_flow(user, "", location=pin))
    assert "wells" in _body(reply)
    reply = run(engine.continue_flow(user, "yes the wells near us have water all year"))
    card = _body(reply, 1)
    assert "likelihood, not a detection" in card
    assert "to the south" in card
    assert "VES" in card


def test_variety_late_planting_far_north(env):
    store, fake = env
    user = _user()
    fake.extractions = [{"stresses": {"striga": True}}]
    run(engine.start_flow(user, "variety_choice", opener="which maize seed should I plant now in Kano"))
    reply = run(engine.continue_flow(user, "striga is a problem"))
    card = _body(reply, 1)
    assert "SAMMAZ 70: good fit" in card
    assert "%" not in card  # no made-up match percentages


# ---------------------------------------------------------------- safety of the spoken summary


def test_summary_with_invented_number_is_replaced(env):
    store, fake = env
    fake.summary = "Apply 999 bags of urea today."
    user = _user()
    reply = run(engine.start_flow(user, "fertilizer", opener="maize 1 hectare in Kano"))
    reply = run(engine.continue_flow(user, "skip"))
    spoken = _body(reply, 0)
    assert "999" not in spoken
    assert "bags of NPK 15-15-15" in spoken
    assert store.logs[-1]["flags"] == ["fallback_summary"]


def test_summary_with_known_numbers_is_kept(env):
    store, fake = env
    fake.summary = "For your maize, buy 8 bags of NPK and 2.5 bags of urea."
    user = _user()
    run(engine.start_flow(user, "fertilizer", opener="maize 1 hectare in Kano"))
    reply = run(engine.continue_flow(user, "skip"))
    assert _body(reply, 0) == fake.summary
    assert store.logs[-1]["flags"] == ["natlas_summary"]


def test_weed_summary_with_dose_is_replaced():
    facts = {"weeds": ["spear grass"]}
    assert compose.check_summary("Spray 200 ml per knapsack.", facts, check_doses=True) is not None
    assert compose.check_summary("Dig out the white roots before planting.", facts, check_doses=True) is None


def test_natlas_down_still_answers(env, monkeypatch):
    async def broken(*args, **kwargs):
        raise natlas.NatlasError("down")

    monkeypatch.setattr(natlas, "chat", broken)
    user = _user()
    run(engine.start_flow(user, "germination"))
    reply = run(engine.continue_flow(user, "planted 50 and 20 germinated"))
    reply = run(engine.continue_flow(user, "skip"))
    assert "40 percent" in _body(reply, 0)
    assert "poor" in _body(reply, 1)


def test_change_forgets_reused_farm_details(env):
    user = _user()
    user.farm_profile = {"area": {"m2": 4046.86, "label": "1 acre"},
                         "location": {"label": "Kano", "lat": 12.0, "lon": 8.5, "state": "kano", "exact": False}}
    reply = run(engine.start_flow(user, "fertilizer", opener="maize"))
    assert "from before: 1 acre in Kano" in _body(reply)
    reply = run(engine.continue_flow(user, "change"))
    assert "How big is the farm" in _body(reply)
