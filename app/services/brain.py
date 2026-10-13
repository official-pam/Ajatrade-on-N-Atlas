"""Brain: intent routing, crop-detection case flow, description diagnosis, and
conversation-memory summarizer for active, consented users.

This is the "brain" from AGENTS.md: plain Python functions with no WhatsApp
imports. The WhatsApp adapter (handle_message) calls into it.

Design notes:
- Every language-model call goes to N-ATLaS (app/services/natlas.py), behind
  a timeout + one retry + polite fallback.
- Photo diagnosis still goes through the existing disease_detection module;
  this module owns the text (description) diagnosis path and the router.
- Doses, mixing amounts and spray schedules are never sent. The vision
  build_reply already guards photo output; the description path strips any
  chemical item and caps confidence at "medium".
- send_wait_notice sends the pregenerated "please wait" voice note once per
  request at the start of photo/voice processing (TODO_new_voice_notes.md).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from tenacity import retry, stop_after_attempt, wait_exponential

from app.services import natlas

from app.schemas.memory import User
from app.storage import (
    OpenCase,
    UserFlow,
    UserStep,
    mask_phone,
    new_case_id,
    storage,
)
from app.onboarding.messages import service_menu
from app.services.diagnoses import reply as reply_mod
from app.services.diagnoses.reply import Reply, build_reply, MAX_WORDS, _build_fallback, MSG, parse_model_output
logger = logging.getLogger("brain")

# ---------------------------------------------------------------------------
# Config / provider
# ---------------------------------------------------------------------------

# Text model for intent routing, the crop-description case flow and farm
# advice: N-ATLaS.
TEXT_MODEL_NAME = natlas.MODEL_NAME


# Diagnosis caps (VOICE_NOTE.md section 3 and section 8).
MAX_QUESTIONS_PER_CASE = 2
MAX_DIAGNOSES_PER_CASE = 2
CASE_INACTIVITY_TIMEOUT_SECONDS = 3 * 60 * 60  # 3 hours

# How many follow-up questions the text model may ask before we force diagnose.
_FALLBACK_QUESTION = "Please send me a photo of the affected leaf, or tell me more about what you see on the plant."


# ---------------------------------------------------------------------------
# Public no-op hook (section 12)
# ---------------------------------------------------------------------------


async def send_wait_notice(user: User) -> None:
    """Send the pregenerated 'please wait' voice note for the user's language.

    Called once per request when a photo or voice note arrives and processing
    starts (TODO_new_voice_notes.md), before the slow steps (fetch, STT,
    model calls). The send happens in the background so it never delays the
    reply, and it never raises: a failed wait note must not stop the
    diagnosis. If the cached file is missing, the audio store logs it,
    generates it once and sends it, and falls back to text so the user is
    never left in silence.
    """
    language = (user.language or "english").strip().lower() or "english"
    try:
        from app.voice.audio_store import default_store

        sent = await default_store.send_voice_script(
            user.phone, "please_wait", language
        )
        logger.info(
            "wait_notice %s for %s, flow=%s step=%s",
            "sent" if sent else "not sent",
            mask_phone(user.phone),
            user.flow,
            user.step,
        )
    except Exception as error:
        logger.warning(
            "wait_notice failed for %s: %s", mask_phone(user.phone), error
        )


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------


def _groq_retry() -> Any:
    """Tenacity retry decorator (name kept for older call sites)."""

    return retry(
        stop=stop_after_attempt(2),  # one attempt plus one retry
        wait=wait_exponential(min=1, max=4),
        reraise=True,
    )


async def _llm_chat(system_prompt: str, user_text: str, timeout: float = 60.0) -> str:
    """One N-ATLaS JSON chat call with a timeout (the client retries once).

    Returns the model's text reply. Raises on failure so the caller can fall
    back to a polite message.
    """
    return await natlas.chat(
        system_prompt,
        user_text,
        json_mode=True,
        temperature=0.1,
        timeout=max(timeout, 60.0),
    )


# Older name, kept so nothing else breaks.
_groq_chat = _llm_chat


# ---------------------------------------------------------------------------
# Text system prompt (VOICE_NOTE.md section 8)
# ---------------------------------------------------------------------------

TEXT_SYSTEM_PROMPT = """
You are a crop health assistant for smallholder farmers in Nigeria. You talk
with a farmer who describes a crop problem. Everything you receive is already
translated into English. You return ONE JSON object and nothing else.

You are given: facts collected so far, the conversation for this problem, how
many questions you have already asked, and optionally a short summary of what
the farmer shared in the past.

RULES
1. Choose one action:
   - "ask_question": the crop or the main symptom is missing. Ask ONE short,
     simple question. Never ask more than one question at a time. If
     questions_asked is already 2 or more, do not ask.
   - "diagnose": you have enough to give a cautious best guess.
   - "refer": the description is too vague to guess from, even after
     questions. Do not invent a diagnosis.
   - "off_topic": the farmer is asking about something else. Set switch_to to
     "market_price", "connect_vendor" or "menu".
2. This is a description, not a photo. Confidence must be "low" or "medium",
   never "high".
3. Never guess when symptoms fit many causes. Lower the confidence and list
   alternatives.
4. Recommend low-cost and cultural steps only (remove infected plants, clean
   cuttings, spacing, sanitation, crop rotation). Do not name any chemical,
   dose, or spray schedule.
5. Use the past summary only for context, for example the farmer's crops and
   location. Never treat a past problem as the current diagnosis.
6. farmer_message: simple English, short sentences, under 70 words, no
   jargon, natural when read aloud. When you diagnose, always end the
   message with this exact sentence: "If you want us to confirm it well,
   you could send us a picture." Do not mention pictures or photos
   anywhere else in the message.

7. extracted: only facts the farmer actually said. Use null if unknown.
8. Output ONLY the JSON object below. No markdown, no code fences.

JSON SCHEMA
{
  "action": "ask_question | diagnose | refer | off_topic",
  "switch_to": "market_price | connect_vendor | menu | null",
  "question": "the one question, or null",
  "extracted": {
    "crop": "crop name or null",
    "part": "leaf | stem | fruit | root | whole plant | null",
    "symptoms": ["what the farmer described, one item each"]
  },
  "diagnosis": {
    "primary": {
      "name": "disease or pest name, or 'unknown'",
      "category": "fungal | bacterial | viral | pest | nutrient | abiotic | unknown",
      "confidence": "low | medium"
    },
    "alternatives": [ { "name": "other possible cause", "category": "same options" } ]
  },
  "recommended_actions": ["short cultural steps"],
  "see_extension_officer": { "needed": true or false, "reason": "one sentence" },
  "farmer_message": "short message for the farmer, or null if not diagnosing"
}
""".strip()


def _parse_text_model_output(raw: str) -> dict[str, Any] | None:
    """Parse the text model's JSON reply defensively."""
    data = build_reply(raw)
    print("data:: , in parse_text_model_output: : ", data)
    if data is None:
        return None
    action = str(data.get("action") or "").strip().lower()
    if action not in {"ask_question", "diagnose", "refer", "off_topic"}:
        return None
    return data


# ---------------------------------------------------------------------------
# Description diagnosis: dose guard + confidence cap + photo line
# (VOICE_NOTE.md section 8)
# ---------------------------------------------------------------------------

DOSE_INDICATOR_WORDS = re.compile(
    r"\b(ml|millilit|litre|liter|l|g|gram|kg|tablespoon|teaspoon|tbsp|tsp|cup|"
    r"capsule|sachet|per|every|each|days?)\b",
    re.I,
)


def _strip_chemicals_from_description(farmer_message: str | None) -> str | None:
    """Remove any chemical item from a description-based farmer_message.

    Description diagnoses must never recommend chemicals (VOICE_NOTE.md section
    8). If the model included one, drop to the cultural steps only. This is a
    lightweight guard; build_reply's DOSE_PATTERN is the authoritative check for
    photo output.
    """
    if not farmer_message:
        return None
    if DOSE_INDICATOR_WORDS.search(farmer_message):
        return None
    return farmer_message.strip()


def _build_description_reply(
    model_output: dict[str, Any],
) -> Reply:
    """Build a reply for a description-based diagnosis, applying the rules:
    - confidence capped at medium
    - no chemicals (strip if present)
    - code appends "To be more sure, please send me a photo of the leaf."
    """
    data = model_output
    primary = (data.get("diagnosis") or {}).get("primary")
    if not isinstance(primary, dict):
        return Reply("unsure", build_reply.MSG["unsure"], ["no_diagnosis"], data)

    name = str(primary.get("name") or "unknown").strip().lower()
    category = str(primary.get("category") or "unknown").strip().lower()
    confidence = str(primary.get("confidence") or "low").strip().lower()

    # Confidence cap: downgrade high -> medium.
    if confidence == "high":
        confidence = "medium"

    if name == "unknown" or category == "unknown" or confidence == "low":
        return Reply(
            "unsure",
            f"{reply_mod.MSG['unsure']} {reply_mod.MSG['closing']}",
            ["low_confidence"],
            data,
        )

    if name == "healthy" or category == "healthy":
        return Reply("healthy", reply_mod.MSG["healthy"], ["healthy"], data)

    flags: list[str] = []
    message = _strip_chemicals_from_description(data.get("farmer_message"))
    if message is None:
        flags.append("message_unusable_or_dose_found")
    elif len(message.split()) > MAX_WORDS:
        flags.append("message_too_long")
        message = None

    if message is None:
        message = _build_fallback(data, name, confidence)

    if confidence == "medium":
        message = f"{reply_mod.MSG['medium_prefix']} {message}"
        flags.append("medium_confidence")

    ext = data.get("see_extension_officer") or {}
    if ext.get("needed") and "extension officer" not in message.lower():
        message = f"{message} {reply_mod.MSG['extension']}"

    # Code-appended photo line (never from the model).
    message = f"{message} To be more sure, please send me a photo of the leaf."
    return Reply("diagnosis", message, flags, data)


# ---------------------------------------------------------------------------
# Intent classification (VOICE_NOTE.md section 4, rule 4)
# ---------------------------------------------------------------------------

INTENT_SYSTEM_PROMPT = """
You are a routing classifier for AjaTrade, a WhatsApp assistant for Nigerian
farmers and traders. You receive one message that is already translated into
English. Return ONE JSON object and nothing else.

Allowed intents:
- "crop_problem": a crop disease, pest, spots, yellowing or other sickness.
- "irrigation": how much to water, watering schedule, water wasted, leaking
  pipes, drip lines, or a sloped farm getting too much or too little water.
- "soil_health": soil test results, soil quality, acidity, soil fertility.
- "fertilizer": how much or which fertilizer, NPK, urea, when to apply.
- "water_source": where to find water, dig a well, borehole, pond.
- "weed_control": weeds, grass in the farm, Striga, herbicide for weeds.
- "variety_choice": which seed or variety to plant, hybrid seed.
- "germination": seeds that did not come up, nursery seedlings dying.
- "market_price": a market price for a crop or product.
- "find_vendor": wants to buy herbicide, fertilizer or seed from a vendor.
- "greeting": a hello or welcome with no request.
- "unclear": anything else.

Return exactly:
{"intent": "<one of the intents above>"}

Do not answer the message. Do not translate. Output only the JSON object.
""".strip()

ADVISOR_INTENTS = (
    "irrigation",
    "soil_health",
    "fertilizer",
    "water_source",
    "weed_control",
    "variety_choice",
    "germination",
)
ALL_INTENTS = {"crop_problem", "market_price", "find_vendor", "greeting", "unclear", *ADVISOR_INTENTS}

_KEYWORD_INTENTS = (
    (re.compile(r"\b(fertili[sz]er|npk|urea|manure|ssp|potash)\b", re.I), "fertilizer"),
    (re.compile(r"\b(weed|weeds|striga|witchweed|spear ?grass|herbicide)\b", re.I), "weed_control"),
    (re.compile(r"\b(germinat\w*|did not come up|didn'?t come up|sprout\w*|seedlings? (die|dying|died))\b", re.I), "germination"),
    (re.compile(r"\b(irrigat\w*|how much water|watering|drip|leak\w*|sprinkler)\b", re.I), "irrigation"),
    (re.compile(r"\b(borehole|dig a well|find water|water source|pond)\b", re.I), "water_source"),
    (re.compile(r"\b(soil test|soil ph|acidic soil|soil health|soil quality|my soil)\b", re.I), "soil_health"),
    (re.compile(r"\b(which (seed|variety)|seed variety|hybrid seed|best seed|improved seed)\b", re.I), "variety_choice"),
)


def keyword_intent(english_text: str) -> str | None:
    """Clear keyword matches skip the model call (faster, and works offline)."""
    for pattern, intent in _KEYWORD_INTENTS:
        if pattern.search(english_text or ""):
            return intent
    return None


async def classify_intent(english_text: str) -> str:
    """Return one of ALL_INTENTS (falls back to "unclear")."""
    quick = keyword_intent(english_text)
    if quick:
        return quick
    try:
        raw = await _llm_chat(INTENT_SYSTEM_PROMPT, english_text, timeout=30.0)
        data = parse_model_output(raw) or {}
        intent = str(data.get("intent") or "").strip().lower()
        if intent in ALL_INTENTS:
            return intent
    except Exception as error:
        logger.warning("Intent classification failed: %s", error)
    return "unclear"


# ---------------------------------------------------------------------------
# Welcome-back text (section 4: unclear intent / long silence -> short
# welcome-back voice note plus menu). Pregenerated script todo; text fallback
# here for now.
# ---------------------------------------------------------------------------

WELCOME_BACK_TEXT = {
    "en": "Welcome back. I am AjaTrade, your farming assistant. How can I help you today?",
    "ha": "Barka da zuwa. AjaTrade ne abin taimako na noma. Yaya za a iya taimaka maka yau?",
    "yo": "Káàbọ̀ sí ìrìnṣẹ́ yìí. AjaTrade jẹ́ olùrànlọ́wọ́ fún àgbẹ̀. Báwo ni a ó lè ràn ọ́ lọ́wọ́ lónìí?",
    "ig": "Nnọọ na-ezukọrịta gị. AjaTrade bụ enyi noma gị. Kedu ka anyị ga-esi nyere gị aka taa?",
    "pcm": "Welcome back. AjaTrade na your farming helper. How I fit help you today?",
}


def welcome_back_text(language: str) -> str:
    lang = (language or "en").strip().lower()
    return (
        WELCOME_BACK_TEXT.get(lang)
        or WELCOME_BACK_TEXT.get(lang.split("-")[0])
        or WELCOME_BACK_TEXT["en"]
    )


# ---------------------------------------------------------------------------
# Router (VOICE_NOTE.md section 4)
# ---------------------------------------------------------------------------


@dataclass
class RoutedReply:
    """What the router decided to send back to the user."""

    messages: list[dict[str, Any]]  # WhatsApp payloads to send
    english_text: str | None = None  # what was generated before translation
    close_case: bool = False
    new_flow: str | None = None  # if we switched flow mid-case
    show_menu: bool = False  # also send the tappable service menu
    menu: str | None = None  # "farm" or "trade"; None picks by the user's role


async def route_message(
    user: User,
    english_text: str,
    media_ref: str | None = None,
    *,
    location: dict | None = None,
) -> RoutedReply:
    """Route one incoming message for an active, consented user.

    This is the brain path called from handle_message for text, voice and
    location messages. Photo messages keep their existing diagnosis path in
    handle_message, except inside a farm-advice flow that takes photos.
    """
    from app.services.advisor import engine as advisor
    from app.trade import flow as trade_flow

    try:
        await storage.set_user_last_activity(user.phone)
    except Exception as error:
        logger.warning("Could not update last_activity_at for %s: %s", mask_phone(user.phone), error)

    # 0. An open farm-advice flow owns the message unless it hands it back.
    if advisor.is_advisor_flow(user.flow):
        reply = await advisor.continue_flow(user, english_text, location=location)
        if reply is not None:
            return reply

    # 0b. An open trader-ledger action (sale, restock, read-back) likewise.
    if trade_flow.is_trade_flow(user.flow):
        reply = await trade_flow.continue_flow(user, english_text)
        if reply is not None:
            return reply

    case = await storage.get_case(user.phone)

    # 1. Expiry check (section 4, rule 3): lazy, on next message.
    if case.has_case and _case_expired(case):
        logger.info("Case expired for %s; closing", mask_phone(user.phone))
        await storage.close_case(user.phone)
        case = OpenCase.empty()
        # Treat this message as having no open case.
        user_flow = None
        user_step = None
    else:
        user_flow = user.flow
        user_step = user.step

    # 2. Route by type of incoming message & flow.
    if user_flow == UserFlow.CROP_DETECTION:
        case.current_case_id=new_case_id()
        case.case_started_at=datetime.now(timezone.utc)
        case.last_activity_at=datetime.now(timezone.utc)
        return await _route_crop_detection_case(user, case, english_text, media_ref)

    if location and not english_text:
        # A location pin outside any flow: remember it and offer the menu.
        if user.consent_status == "granted":
            user.farm_profile = {**(user.farm_profile or {}), "location": location}
            await storage.save_user(user)
        text = "Thank you, I saved your farm location. What would you like help with?"
        return RoutedReply(messages=[_voice_payload(text, user.language)], english_text=text, show_menu=True)

    # Ledger messages ("I sold 3 bags of rice at 52,000 each", "today's
    # profit") go to the trader ledger before intent classification.
    if english_text and user_flow != UserFlow.CROP_DETECTION and trade_flow.looks_like_trade(english_text, user):
        return await trade_flow.start(user, english_text)

    # No open flow: classify intent.
    intent = await classify_intent(english_text)
    logger.info("Intent for %s: %s", mask_phone(user.phone), intent)

    if intent == "crop_problem":
        # Start a new crop-detection case.
        return await _start_crop_detection_case(user, english_text)

    if intent in ADVISOR_INTENTS:
        return await advisor.start_flow(user, intent, opener=english_text)

    if intent in {"market_price", "find_vendor"}:
        return _stub_service_reply(UserFlow.MARKET_PRICE if intent == "market_price" else UserFlow.CONNECT_VENDOR)

    if intent == "unclear":
        text = (
            "I can help with crop problems, watering, soil, fertilizer, finding water, "
            "weeds, choosing seed, and seed germination. Please choose from the menu, "
            "or tell me your farm problem."
        )
        if trade_flow.is_trader(user):
            text = (
                "I keep your sales book. Tell me what you sold or what stock you added, "
                "or ask for today's profit. You can also choose from the menu."
            )
        return RoutedReply(
            messages=[_voice_payload(text, user.language)],
            english_text=text,
            close_case=False,
            show_menu=True,
        )

    # greeting -> welcome-back + menu.
    return _welcome_back_and_menu(user)


def _case_expired(case: OpenCase) -> bool:
    if case.last_activity_at is None:
        return False
    delta = datetime.now(timezone.utc) - case.last_activity_at
    return delta.total_seconds() > CASE_INACTIVITY_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# Crop-detection case flow (VOICE_NOTE.md section 8)
# ---------------------------------------------------------------------------


async def _start_crop_detection_case(user: User, english_text: str) -> RoutedReply:
    case_id = new_case_id()
    case = OpenCase(
        flow=UserFlow.CROP_DETECTION,
        step=UserStep.AWAITING_CROP,
        current_case_id=case_id,
        case_started_at=datetime.now(timezone.utc),
        last_activity_at=datetime.now(timezone.utc),
        case_crop=None,
        case_part=None,
        case_symptoms=[],
        questions_asked=0,
        diagnoses_in_case=0,
    )
    # Run the text model once to extract facts and decide the first action.
    model_output = await _text_model_turn(user, case, english_text)
    extracted = model_output.get("extracted") if isinstance(model_output, dict) else None
    case = _merge_extracted(case, extracted)

    action = model_output.get("action") if isinstance(model_output, dict) else "ask_question"

    if action == "ask_question":
        case.step = UserStep.AWAITING_CROP
        await storage.set_case(user.phone, case)
        question = model_output.get("question") if isinstance(model_output, dict) else "Which crop is it?"
        return RoutedReply(
            messages=[_voice_payload(question, user.language)],
            english_text=question,
            close_case=False,
        )

    if action == "diagnose":
        return await _send_description_diagnosis(user, case, model_output)

    # refer / off_topic fallbacks.
    if action == "off_topic":
        switch_to = model_output.get("switch_to") if isinstance(model_output, dict) else None
        return _switch_flow_reply(user, switch_to)

    # refer: too vague.
    return RoutedReply(
        messages=[_voice_payload(build_reply.MSG["unsure"], user.language)],
        english_text=build_reply.MSG["unsure"],
        close_case=False,
    )


async def _route_crop_detection_case(
    user: User, case: OpenCase, english_text: str, media_ref: str | None
) -> RoutedReply:
    """Continue an open crop-detection case."""
    # Update case with the new message's extracted facts.
    model_output = await _text_model_turn(user, case, english_text)
    print("Model_output: ;", type(model_output))
    extracted = model_output.get("extracted") if isinstance(model_output, dict) else None
    print("extracted: ", extracted)
    case = _merge_extracted(case, extracted)

    action = model_output.get("action") if isinstance(model_output, dict) else "ask_question"
    print("Action: : ", action)

    # If a photo arrived while step == awaiting_photo_optional, run photo
    # diagnosis (handled in handle_message, not here). Here we only handle
    # voice/text continuations.
    if media_ref:
        # Photo while case open -> close via photo diagnosis (handled upstream).
        # Mark the case for closure and let handle_message do the photo diagnosis.
        return RoutedReply(
            messages=[],
            english_text=None,
            close_case=True,
            new_flow=None,
        )

    if action == "off_topic":
        switch_to = model_output.get("switch_to") if isinstance(model_output, dict) else None
        return _switch_flow_reply(user, switch_to)

    if action == "ask_question":
        if case.questions_asked >= MAX_QUESTIONS_PER_CASE:
            # Force diagnose.
            action = "diagnose"

    if action == "diagnose":
        return await _send_description_diagnosis(user, case, model_output)

    if action == "refer":
        return RoutedReply(
            messages=[_voice_payload(build_reply.MSG["unsure"], user.language)],
            english_text=build_reply.MSG["unsure"],
            close_case=False,
        )

    # ask_question.
    # 
    question =model_output.get("question") if isinstance(model_output, dict) else "Tell me more about the problem."
    step = _step_for_question(model_output, case)
    case.step = step
    case.questions_asked = case.questions_asked + 1
    await storage.set_case(user.phone, case)
    return RoutedReply(
        messages=[_voice_payload(question, user.language)],
        english_text=question,
        close_case=False,
    )


def _step_for_question(model_output: dict[str, Any], case: OpenCase) -> str | None:
    extracted = model_output.get("extracted") if isinstance(model_output, dict) else None
    crop = extracted.get("crop") if isinstance(extracted, dict) else None
    if case.case_crop is None and crop:
        return UserStep.AWAITING_DESCRIPTION
    return UserStep.AWAITING_DESCRIPTION


async def _send_description_diagnosis(
    user: User, case: OpenCase, model_output: dict[str, Any]
) -> RoutedReply:
    """Send a description-based diagnosis, applying caps and guards."""
    if case.diagnoses_in_case >= MAX_DIAGNOSES_PER_CASE:
        # Already told them what we can.
        msg = (
            "I have told you what I can from your description. "
            "Please send me a photo of the leaf, or show the plant to an extension officer near you."
        )
        return RoutedReply(
            messages=[_voice_payload(msg, user.language)],
            english_text=msg,
            close_case=False,
            new_flow=None,
        )

    reply = _build_description_reply(model_output)
    case.diagnoses_in_case = case.diagnoses_in_case + 1
    case.step = UserStep.AWAITING_PHOTO_OPTIONAL
    await storage.set_case(user.phone, case)

    # # Log the description diagnosis.
    # try:
    #     await storage.log_diagnosis(
    #         case_id=case.current_case_id,
    #         wa_id=user.phone,
    #         source="description",
    #         model_name=TEXT_MODEL_NAME,
    #         photo_ref=None,
    #         raw_output=json.dumps(model_output),
    #         crop=reply.parsed.get("crop", {}).get("name") if reply.parsed else None,
    #         diagnosis_name=(reply.parsed or {}).get("diagnosis", {}).get("primary", {}).get("name")
    #         if reply.parsed
    #         else None,
    #         category=(reply.parsed or {}).get("diagnosis", {}).get("primary", {}).get("category")
    #         if reply.parsed
    #         else None,
    #         confidence=(reply.parsed or {}).get("diagnosis", {}).get("primary", {}).get("confidence")
    #         if reply.parsed
    #         else None,
    #         flags=reply.flags,
    #         reply_text_en=reply.text_en,
    #     )
    # except Exception as error:
    #     logger.warning("Could not log description diagnosis for %s: %s", user.phone, error)

    return RoutedReply(
        messages=[_voice_payload(model_output["farmer_message"], user.language)],
        english_text=reply.text_en,
        close_case=False,
    )


def _merge_extracted(case: OpenCase, extracted: dict[str, Any] | None) -> OpenCase:
    if not extracted:
        return case
    new_symptoms = list(case.case_symptoms)
    for symptom in extracted.get("symptoms") or []:
        symptom = str(symptom).strip()
        if symptom and symptom not in new_symptoms:
            new_symptoms.append(symptom)
    return OpenCase(
        flow=case.flow,
        step=case.step,
        current_case_id=case.current_case_id,
        case_started_at=case.case_started_at,
        last_activity_at=case.last_activity_at,
        case_crop=extracted.get("crop") or case.case_crop,
        case_part=extracted.get("part") or case.case_part,
        case_symptoms=new_symptoms,
        questions_asked=case.questions_asked,
        diagnoses_in_case=case.diagnoses_in_case,
    )


async def _text_model_turn(user: User, case: OpenCase, english_text: str) -> dict[str, Any]:
    """One turn of the text model for the crop-detection case."""
    summary = None
    if user.phone:
        try:
            summary = await storage.get_conversation_memory(user.phone)
        except Exception as error:
            logger.warning("Could not load conversation_memory for %s: %s", user.phone, error)

    context = _build_case_context(case, summary)
    print(context)
    user_block = f"Farmer's latest message: {english_text}"
    system = TEXT_SYSTEM_PROMPT
    prompt = f"{context}\n\n{user_block}"

    try:
        raw = await _llm_chat(system, prompt, timeout=60.0)
        print("raw: : ", raw)
        # data = _parse_text_model_output(raw)
        print("data::: ")
        print("data: ", raw)
        if raw is None:
            logger.warning("Text model returned unusable output for %s", user.phone)
            return {"action": "refer"}
        return parse_model_output(raw)
    except Exception as error:
        logger.warning("Text model call failed for %s: %s", user.phone, error)
        return {"action": "refer"}


def _build_case_context(case: OpenCase, summary: str | None) -> str:
    parts = [
        "Facts collected so far:",
        f"- Crop: {case.case_crop or 'not stated'}",
        f"- Part affected: {case.case_part or 'not stated'}",
        f"- What the farmer described: {'; '.join(case.case_symptoms) or 'nothing'}",
        f"- Questions asked so far: {case.questions_asked}",
        f"- Diagnoses given so far: {case.diagnoses_in_case}",
    ]
    if summary:
        parts.append(f"Past summary (context only): {summary}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Flow switching / stubs (VOICE_NOTE.md section 9)
# ---------------------------------------------------------------------------


def _switch_flow_reply(user: User, switch_to: str | None) -> RoutedReply:
    if switch_to == "market_price":
        return _stub_service_reply(UserFlow.MARKET_PRICE)
    if switch_to == "connect_vendor":
        return _stub_service_reply(UserFlow.CONNECT_VENDOR)
    if switch_to == "menu":
        return _welcome_back_and_menu(user)
    # Unknown switch_to: fall back to welcome-back + menu.
    return _welcome_back_and_menu(user)


def _stub_service_reply(flow: str) -> RoutedReply:
    """market_price and connect_vendor stubs (VOICE_NOTE.md section 9)."""
    text = (
        "That service is coming soon. For now, you can choose another option from the menu."
    )
    return RoutedReply(
        messages=[_voice_payload(text, None)],
        english_text=text,
        close_case=False,
        new_flow=flow,
    )


def _welcome_back_and_menu(user: User) -> RoutedReply:
    """Unclear intent or long silence: welcome-back + service menu."""
    text = WELCOME_BACK_TEXT["en"]
    # The reply pipeline translates the text into the user's language and
    # then sends the tappable service menu.
    return RoutedReply(
        messages=[_voice_payload(text, user.language)],
        english_text=text,
        close_case=False,
        new_flow=None,
        show_menu=True,
    )


def _voice_payload(text_en: str, language: str | None) -> dict[str, Any]:
    """A text payload carrying the English reply. handle_message translates TTS."""
    return {
        "type": "text",
        "text": {"body": text_en},
    }


# ---------------------------------------------------------------------------
# Conversation-memory summarizer (VOICE_NOTE.md section 10)
# ---------------------------------------------------------------------------

SUMMARY_SYSTEM_PROMPT = """
You are a summarizer for AjaTrade. You receive a short rolling summary of what
a farmer has shared in the past, plus the messages and diagnosis from one
closed crop-detection case.

Return ONE JSON object and nothing else:
{"summary": "new summary, 150 words or fewer"}

Rules:
- Include only things the farmer shared: crops, location (town or LGA), past
  problems and advice given, open follow-ups.
- Do not add inferences. Do not store names, phone numbers or other
  identifiers beyond what is needed.
- Preserve still-relevant older facts and drop stale ones.
- Keep it short and factual.
""".strip()


async def summarize_and_store_memory(
    phone: str, old_summary: str | None, case_messages: list[dict[str, Any]], diagnosis_text: str
) -> None:
    """Update conversation_memory once per closed case that produced a diagnosis.

    Runs as a background task so it does not delay replies.
    """
    try:
        prompt_parts = ["Past summary:", old_summary or "none (first case)."]
        prompt_parts.append("Closed case messages (English):")
        for m in case_messages[-10:]:
            text = m.get("text") or ""
            if text:
                prompt_parts.append(f"- {text}")
        prompt_parts.append(f"Diagnosis sent: {diagnosis_text}")
        prompt = "\n".join(prompt_parts)

        raw = await _llm_chat(SUMMARY_SYSTEM_PROMPT, prompt, timeout=60.0)
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            data = json.loads(raw[start : end + 1])
            new_summary = str(data.get("summary") or "").strip()
            if new_summary:
                await storage.set_conversation_memory(phone, new_summary)
                logger.info("Updated conversation_memory for %s", phone)
    except Exception as error:
        logger.warning("Could not update conversation_memory for %s: %s", phone, error)
