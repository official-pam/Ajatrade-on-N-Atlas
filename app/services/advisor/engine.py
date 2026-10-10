"""Conversation engine for the farm-advice flows.

One flow at a time per user. The engine asks for one missing fact per turn,
accepts answers by voice, text, photo or location pin, runs the calculator
when it has enough, and replies with a short spoken summary (N-ATLaS) plus a
text card with the exact numbers.

State lives on the user row: ``flow`` is the flow id and ``flow_data`` holds
the answers so far. Farm-level facts are copied to ``farm_profile`` so the
farmer is not asked again next time.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

from app.schemas.memory import User
from app.services import natlas, vision
from app.services.advisor import compose, extract, runners
from app.services.advisor.specs import FLOWS, PROFILE_KEYS, FlowSpec
from app.services.brain import RoutedReply
from app.storage import mask_phone, storage

logger = logging.getLogger("advisor")

MAX_ASKS_PER_SLOT = 2
CHANGE = re.compile(r"^\s*(change|change it|that is wrong|wrong|it is wrong|no,? change)\s*[.!]?\s*$", re.I)
MENU_HINT = "Say menu to see other services."

# Used when a required fact is still missing after two questions.
DEFAULTS: dict[str, tuple[Any, str]] = {
    "area": ({"m2": 100.0, "label": "100 square metres (example size)"}, "I used an example size of 100 square metres; multiply for your farm."),
    "crop_age_days": (35, "I assumed the crop is about five weeks old."),
    "planting_month": (None, "I assumed you will plant this month."),
    "location": (None, "Without your location I used typical weather for the middle belt."),
}


def is_advisor_flow(flow: str | None) -> bool:
    return flow in FLOWS


def _text(body: str) -> dict[str, Any]:
    return {"type": "text", "text": {"body": body}}


def _card(body: str) -> dict[str, Any]:
    """Text-only message (no voice note): the exact numbers."""
    return {"type": "text", "text": {"body": body}, "voice": False}


def _today() -> date:
    return datetime.now(timezone.utc).date()


async def _save(user: User) -> None:
    try:
        await storage.save_user(user)
    except Exception as error:  # noqa: BLE001 - a failed save only costs a repeat question
        logger.warning("Could not save flow state for %s: %s", mask_phone(user.phone), error)


def _end_flow(user: User) -> None:
    user.flow = None
    user.step = None
    user.flow_data = {}


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def start_flow(user: User, flow: str, opener: str | None = None) -> RoutedReply:
    """Start a flow (menu tap or detected intent), using any facts in ``opener``."""
    spec = FLOWS[flow]
    profile = user.farm_profile or {}
    slots: dict[str, Any] = {}
    reused: list[str] = []
    for key in PROFILE_KEYS:
        if any(s.key == key for s in spec.slots) and profile.get(key) is not None:
            slots[key] = profile[key]
            reused.append(key)
    user.flow = flow
    user.flow_data = {"slots": slots, "asked": {}, "last": None, "notes": [], "started": datetime.now(timezone.utc).isoformat()}
    if opener:
        await _absorb_text(user, spec, opener)
    intro = spec.intro
    if reused:
        intro += " " + _reuse_note(slots, reused)
    return await _next_step(user, spec, prefix=intro)


async def continue_flow(
    user: User,
    english_text: str,
    *,
    location: dict | None = None,
    photo: tuple[bytes, str] | None = None,
) -> RoutedReply | None:
    """Continue the user's open flow. Returns None when the message is not
    part of the flow (the caller then routes it normally)."""
    flow = user.flow
    if not is_advisor_flow(flow):
        return None
    spec = FLOWS[flow]
    state = user.flow_data or {}
    text = (english_text or "").strip()

    if state.get("stage") == "slope_feedback":
        return await _slope_feedback(user, text)

    if extract.wants_menu(text):
        _end_flow(user)
        await _save(user)
        return RoutedReply(messages=[_text("Okay.")], english_text="Okay.", new_flow=None, show_menu=True)

    if not state.get("slots") and not state.get("asked"):
        user.flow_data = {"slots": {}, "asked": {}, "last": None, "notes": []}

    if location:
        user.flow_data["slots"]["location"] = location

    if CHANGE.match(text):
        # "Say change if that is wrong": forget the reused farm details.
        for key in PROFILE_KEYS:
            user.flow_data["slots"].pop(key, None)
            user.flow_data.get("asked", {}).pop(key, None)
        return await _next_step(user, spec)

    if photo is not None:
        reply = await _absorb_photo(user, spec, photo, text)
        if reply is not None:
            return reply
    elif text:
        switch = await _absorb_text(user, spec, text)
        if switch == "__menu__":
            _end_flow(user)
            await _save(user)
            return RoutedReply(messages=[_text("Okay.")], english_text="Okay.", show_menu=True)
        if switch:
            if switch in FLOWS:
                return await start_flow(user, switch, opener=text)
            # crop_detection / market_price: hand back to the router.
            _end_flow(user)
            await _save(user)
            return None
    return await _next_step(user, spec)


# ---------------------------------------------------------------------------
# Absorbing answers
# ---------------------------------------------------------------------------


async def _absorb_text(user: User, spec: FlowSpec, text: str) -> str | None:
    state = user.flow_data
    slots: dict[str, Any] = state.setdefault("slots", {})
    asked = state.get("last")
    today = _today()

    found = extract.clean_facts(spec, extract.pattern_facts(spec, text, asked, today), today)
    need_model = (asked and asked not in found) or len(text.split()) > 10 or not found
    other = None
    if need_model:
        known = {k: v for k, v in slots.items() if k != "location"}
        model, menu, other = await extract.model_facts(spec, known, asked, text)
        if menu:
            return "__menu__"
        for key, value in extract.clean_facts(spec, model, today).items():
            found.setdefault(key, value)
    if other and not found:
        return other

    # A short answer to "Where is the farm?" is the place name itself.
    if asked == "location" and "location" not in found and len(text.split()) <= 6 and not extract.is_skip(text):
        found["location"] = text

    if "location" in found and not isinstance(found["location"], dict):
        place = await runners.resolve_place(found["location"])
        if place:
            found["location"] = place
        else:
            found.pop("location")
            state.setdefault("notes", []).append("place_not_found")

    if asked and asked not in found and extract.is_skip(text):
        state.setdefault("skipped", []).append(asked)
    slots.update(found)
    return None


async def _absorb_photo(user: User, spec: FlowSpec, photo: tuple[bytes, str], caption: str) -> RoutedReply | None:
    if not spec.photo_task:
        return None
    image_bytes, mime = photo
    observation = await vision.perceive(image_bytes, mime, spec.photo_task, caption or None)
    if not vision.photo_is_usable(observation):
        advice = (observation.get("photo_quality") or {}).get("retake_advice") or (
            "I could not see that photo well. Please send a clear, close photo in daylight."
        )
        return RoutedReply(messages=[_text(advice)], english_text=advice)
    slots = user.flow_data.setdefault("slots", {})
    if spec.photo_task == "soil_report":
        values = observation.get("values") or {}
        lab_fields = ("organic_carbon_percent", "total_nitrogen_percent", "available_p_mg_per_kg", "exchangeable_k_cmol_per_kg")
        source = "lab" if any(values.get(f) is not None for f in lab_fields) else "meter"
        if not any(v is not None for v in values.values()):
            msg = "I could not read any numbers on that photo. Please send a closer photo of the results, or say the numbers."
            return RoutedReply(messages=[_text(msg)], english_text=msg)
        slots["soil_input"] = {"values": values, "source": source}
        if values.get("texture"):
            slots["soil_texture"] = values["texture"]
    elif spec.photo_task == "weeds":
        slots["weed_info"] = observation
        crop = (observation.get("crop") or {}).get("name")
        if crop and "crop" not in slots:
            slots["crop"] = crop
    elif spec.photo_task == "germination":
        germinated = observation.get("germinated")
        missing = observation.get("not_germinated")
        positions = observation.get("positions_visible")
        planted = positions or ((germinated or 0) + (missing or 0))
        if not planted or germinated is None:
            msg = "I could not count the seeds in that photo. Please tell me how many you planted and how many came up."
            return RoutedReply(messages=[_text(msg)], english_text=msg)
        slots["counts"] = {
            "planted": int(planted),
            "germinated": int(min(germinated, planted)),
            "from_photo": True,
            "count_confidence": observation.get("count_confidence"),
        }
        causes = []
        for problem in observation.get("visible_problems") or []:
            p = str(problem).lower()
            if "damp" in p or "collapse" in p:
                causes.append("damping_off")
            elif "rot" in p or "mould" in p or "mold" in p:
                causes.append("waterlogged")
            elif "insect" in p or "eaten" in p or "termite" in p:
                causes.append("pests")
            elif "dry" in p:
                causes.append("dry_soil")
        if causes:
            slots["causes"] = list(dict.fromkeys(causes))
    if caption:
        await _absorb_text(user, spec, caption)
    return None


# ---------------------------------------------------------------------------
# Asking, defaults, and running
# ---------------------------------------------------------------------------


async def _next_step(user: User, spec: FlowSpec, prefix: str | None = None) -> RoutedReply:
    state = user.flow_data
    slots = state.setdefault("slots", {})
    asked: dict[str, int] = state.setdefault("asked", {})
    notes: list[str] = state.setdefault("notes", [])
    skipped = set(state.get("skipped", []))

    for slot in spec.slots:
        if not slot.required or slot.key in slots:
            continue
        if asked.get(slot.key, 0) < MAX_ASKS_PER_SLOT and slot.key not in skipped:
            return await _ask(user, slot.key, slot.question, prefix, notes)
        if slot.key in DEFAULTS:
            value, note = DEFAULTS[slot.key]
            if value is not None:
                slots[slot.key] = value
            else:
                slots[slot.key] = None
            notes.append(note)
            continue
        # A required fact we cannot do without.
        _end_flow(user)
        await _save(user)
        msg = "I need that information to help with this. Let us try again later. " + MENU_HINT
        return RoutedReply(messages=[_text(msg)], english_text=msg, show_menu=True)

    for slot in spec.slots:
        if slot.required or not slot.ask or slot.key in slots or slot.key in skipped:
            continue
        if asked.get(slot.key, 0) == 0:
            return await _ask(user, slot.key, slot.question, prefix, notes)

    return await _run(user, spec, prefix)


async def _ask(user: User, key: str, question: str, prefix: str | None, notes: list[str]) -> RoutedReply:
    state = user.flow_data
    state["asked"][key] = state["asked"].get(key, 0) + 1
    state["last"] = key
    parts = [prefix] if prefix else []
    if "place_not_found" in notes and key == "location":
        parts.append("I could not find that place.")
        notes.remove("place_not_found")
    elif state["asked"][key] > 1:
        parts.append("Sorry, I did not get that.")
    parts.append(question)
    body = " ".join(p for p in parts if p)
    await _save(user)
    return RoutedReply(messages=[_text(body)], english_text=body)


async def _run(user: User, spec: FlowSpec, prefix: str | None) -> RoutedReply:
    state = user.flow_data
    slots = dict(state.get("slots") or {})
    profile = dict(user.farm_profile or {})
    today = _today()
    try:
        result = await runners.RUNNERS[spec.flow](slots, profile, today)
    except Exception as error:  # noqa: BLE001 - never leave the farmer in silence
        logger.exception("Runner %s failed for %s: %s", spec.flow, mask_phone(user.phone), error)
        _end_flow(user)
        await _save(user)
        msg = "Sorry, something went wrong while working this out. Please try again in a few minutes. " + MENU_HINT
        return RoutedReply(messages=[_text(msg)], english_text=msg)

    for note in state.get("notes") or []:
        if note != "place_not_found":
            result.facts.setdefault("assumptions", []).append(note)
    summary, from_natlas = await compose.spoken_summary(
        spec.title, result.facts, result.fallback, check_doses=result.safety_check_doses
    )
    spoken = f"{prefix} {summary}" if prefix else summary
    card = result.card
    if result.facts.get("assumptions"):
        card += "\nNote: " + " ".join(result.facts["assumptions"])
    card += "\n" + MENU_HINT

    if user.consent_status == "granted":
        profile.update(result.profile_updates)
        user.farm_profile = profile
        await _log(user, spec.flow, result, summary, from_natlas)

    if result.followup:
        user.flow_data = {**result.followup, "flow": spec.flow}
    else:
        _end_flow(user)
    await _save(user)
    return RoutedReply(messages=[_text(spoken), _card(card)], english_text=spoken)


async def _log(user: User, flow: str, result: runners.RunResult, summary: str, from_natlas: bool) -> None:
    """Keep each answer as labelled data (consented users only)."""
    try:
        await storage.log_diagnosis(
            case_id=None,
            wa_id=user.phone,
            source=f"advisor:{flow}",
            model_name=natlas.MODEL_NAME,
            photo_ref=None,
            raw_output=json.dumps(result.facts, ensure_ascii=False, default=str)[:20000],
            crop=(result.log or {}).get("crop"),
            diagnosis_name=flow,
            category="farm_advice",
            confidence=None,
            flags=["natlas_summary" if from_natlas else "fallback_summary"],
            reply_text_en=summary,
        )
    except Exception as error:  # noqa: BLE001
        logger.warning("Could not log %s answer for %s: %s", flow, mask_phone(user.phone), error)


def _reuse_note(slots: dict, keys: list[str]) -> str:
    bits = []
    if "area" in keys and isinstance(slots.get("area"), dict):
        bits.append(str(slots["area"].get("label")))
    if "location" in keys and isinstance(slots.get("location"), dict):
        bits.append(f"in {slots['location'].get('label')}")
    if not bits:
        return ""
    return f"I am using your farm details from before: {' '.join(bits)}. Say change if that is wrong."


# ---------------------------------------------------------------------------
# Slope calibration (problem 4: the farm teaches the system)
# ---------------------------------------------------------------------------

_POSITION = (
    (re.compile(r"\b(top|upper|uphill|up hill|high|higher|upland)\b", re.I), "uphill"),
    (re.compile(r"\b(bottom|lower|downhill|down hill|low|valley|base)\b", re.I), "downhill"),
    (re.compile(r"\b(middle|centre|center)\b", re.I), "middle"),
)
_WET = re.compile(r"\b(wet|soggy|flood\w*|water ?logged|too much water|muddy|swamp\w*)\b", re.I)
_DRY = re.compile(r"\b(dry|drying|wilt\w*|thirsty|not enough water|cracked)\b", re.I)


async def _slope_feedback(user: User, text: str) -> RoutedReply | None:
    position = next((name for pattern, name in _POSITION if pattern.search(text)), None)
    feedback = "too_wet" if _WET.search(text) else "too_dry" if _DRY.search(text) else None
    if not position or not feedback:
        _end_flow(user)
        await _save(user)
        return None
    profile = dict(user.farm_profile or {})
    profile["slope_factors"] = runners.apply_slope_feedback(profile, position, feedback)
    user.farm_profile = profile
    _end_flow(user)
    await _save(user)
    change = "less" if feedback == "too_wet" else "more"
    where = {"uphill": "top", "downhill": "bottom", "middle": "middle"}[position]
    msg = f"Thank you. Next time I will give the {where} of your farm a little {change} water. " + MENU_HINT
    return RoutedReply(messages=[_text(msg)], english_text=msg)
