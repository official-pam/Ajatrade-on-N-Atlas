"""Handling for individual WhatsApp messages (active, onboarded users).

Incoming pipeline (voice): N-ATLAS ASR (by the user's language) -> N-ATLaS
translation to English.
Incoming pipeline (text): N-ATLaS translation to English when the user's
language is not English.
Incoming pipeline (photo): crop diagnosis (vision observes, N-ATLaS
diagnoses), or the open farm-advice flow when it takes photos (weeds, seed
germination, soil-test report).
Incoming pipeline (location pin, menu tap): the brain router / farm-advice
flows.

Replies are written in English, translated into the user's language by
N-ATLaS and voiced; number-heavy answers are also sent as a text card.
"""

import asyncio
import json
import logging
import os
import re
from typing import Any

from app.schemas.webhook import Message
from app.services.speech_to_text import transcribe_voice_note
from app.services.translation import translate_to_english, translate_for_speech
from app.services.whatsapp.get_media import fetch_voice_note, fetch_whatsapp_image
from app.services.chat_history import record_message
from app.services.diagnoses.disease_detection import diagnose
from app.services.diagnoses.reply import build_reply
from app.services.languages import normalize_language
from app.services.text_to_speech import text_to_speech
from app.services.whatsapp.send_media import send_document, send_text, send_voice_note
from app.storage import StorageUnavailableError, mask_phone, storage
from app.services.brain import (
    RoutedReply,
    _welcome_back_and_menu,
    _stub_service_reply,
    route_message,
    send_wait_notice,
)
from app.agri.zones import find_state

ADVISOR_MENU_IDS = (
    "irrigation",
    "soil_health",
    "fertilizer",
    "water_source",
    "weed_control",
    "variety_choice",
    "germination",
)

logger = logging.getLogger("webhook")


def _is_consented(user) -> bool:
    """Return True when the user has granted consent for chat history."""
    return user is not None and getattr(user, "consent_status", None) == "granted"


async def _save_inbound(
    phone: str,
    msg_type: str,
    text: str | None,
    wa_message_id: str | None = None,
    media_ref: str | None = None,
    case_id: Any = None,
    user=None,
) -> None:
    """Save an inbound message when the user has consented, with dedupe."""
    if not _is_consented(user):
        return
    # Durable dedupe: ignore a retry whose message id is already stored.
    if wa_message_id and await storage.message_seen(wa_message_id):
        logger.info("Duplicate inbound message %s from %s; ignoring (DB dedupe)",
                     wa_message_id, mask_phone(phone))
        return
    await record_message(
        phone,
        "in",
        msg_type,
        text,
        wa_message_id=wa_message_id,
        language=user.language if user else None,
        media_ref=media_ref,
        case_id=case_id,
        user=user,
    )


def _menu_payload(
    language: str | None,
    name: str | None,
    role: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Service menu list message (farm or trader). English when language unknown."""
    try:
        from app.onboarding.messages import service_menu
        return service_menu(language or "english", name=name, role=role, kind=kind)
    except Exception as error:
        logger.warning("Could not build service menu: %s", error)
        return {
            "type": "text",
            "text": {"body": "AjaTrade here. Send a photo or voice note about your crop problem, ask for a market price, or ask for a vendor."},
        }


def _schedule_wait_notice(user) -> None:
    """Send the pregenerated 'please wait' voice note in the background.

    Fires once per request, right when a photo or voice note arrives and
    processing starts. Fire-and-forget: the send never delays the diagnosis,
    and it is never retried if processing turns out to be slow.
    """
    if user is None:
        return
    try:
        asyncio.create_task(send_wait_notice(user))
    except Exception as error:  # noqa: BLE001 - never block the reply
        logger.warning("Could not schedule wait notice: %s", error)


async def handle_message(message: Message, contact_name: str | None) -> None:
    """Do the real work here for an active, onboarded user."""
    sender = message.from_
    try:
        user = await storage.get_user(sender)
    except StorageUnavailableError as error:
        logger.error(
            "Storage unavailable for %s: %s", mask_phone(sender), error
        )
        user = None
    language_selected = user.language if user else None

    logger.info(
        "Handling message from %s (%s), language=%s",
        mask_phone(sender),
        contact_name,
        language_selected,
    )
    msg_type = message.type
    logger.info("sender info %s", mask_phone(sender))

    if msg_type == "text" and message.text is not None:
        logger.info(
            "Text from %s (%s): %s",
            mask_phone(sender),
            contact_name,
            message.text.body,
        )
        original_text = message.text.body
        # Store inbound text (dedupe + consent checked inside).
        await _save_inbound(
            sender,
            "text",
            original_text,
            wa_message_id=message.id,
            user=user,
        )
        english_text = await _to_english(original_text, language_selected)
        # Route through the brain.
        reply = await _route_text_or_voice(user, english_text)
        await _send_routed_reply(sender, user, reply, language_selected)

    elif msg_type == "audio" and message.audio is not None:
        logger.info(
            "Voice note from %s, media_id=%s", mask_phone(sender), message.audio.id
        )
        # "Please wait" voice note: send it in the background before the slow
        # steps (fetch, STT, translation, model calls).
        _schedule_wait_notice(user)
        # (await is a syntax error; use asyncio or just call directly)
        audio_bytes, mime_type = await fetch_voice_note(message.audio.id)
        logger.info(
            "Fetched voice note media_id=%s from %s: %d bytes (%s)",
            message.audio.id,
            mask_phone(sender),
            len(audio_bytes),
            mime_type,
        )
        transcription_text = await transcribe_voice_note(audio_bytes, mime_type, language_selected)
        logger.info("Transcription: %s", transcription_text)

        # Translate to English for the brain.
        translate_result = await translate_to_english(transcription_text)
        english_text = translate_result.get("english", transcription_text)
        logger.info("Translated text: %s", english_text)

        # Store the transcript, never the audio bytes.
        await _save_inbound(
            sender,
            "voice",
            transcription_text,
            wa_message_id=message.id,
            user=user,
        )

        # Route the English text through the brain.
        reply = await _route_text_or_voice(user, english_text)
        await _send_routed_reply(sender, user, reply, language_selected)

    elif msg_type == "image" and message.image is not None:
        logger.info(
            "Image from %s, media_id=%s", mask_phone(sender), message.image.id
        )
        # "Please wait" voice note: sent in the background as soon as the photo
        # is received, before the diagnosis reply is ready. Once per request.
        _schedule_wait_notice(user)
        mime_type, image_bytes = await fetch_whatsapp_image(message.image.id)
        logger.info(
            "Fetched image media_id=%s from %s: %d bytes (%s)",
            message.image.id,
            mask_phone(sender),
            len(image_bytes),
            mime_type,
        )
        logger.info("Farmer's caption: %s", message.image.caption)

        translated_text = None
        if message.image.caption:
            result = await translate_to_english(message.image.caption)
            translated_text = result.get("english")

        # A farm-advice flow that takes photos (weeds, germination, soil
        # report) gets the photo; anything else is a crop-health photo.
        if user is not None and _flow_takes_photos(user):
            reply = await _route_photo_to_flow(user, translated_text or "", image_bytes, mime_type)
            await _save_inbound(
                sender, "image", f"[photo for {user.flow}]", wa_message_id=message.id,
                media_ref=message.image.id, user=user,
            )
            await _send_routed_reply(sender, user, reply, language_selected)
            return

        diagnosis = await diagnose(image_bytes, mime_type, farmer_text=translated_text)
        logger.info("Diagnosis for media_id=%s: %s", message.image.id, diagnosis)
        # Safety net: the reply builder removes any dose and handles low
        # confidence / bad photos before anything reaches the farmer.
        farmer_message = (
            build_reply(json.dumps(diagnosis)).text_en
            if diagnosis.get("diagnosis")
            else diagnosis.get("farmer_message") or "Please send the photo again."
        )

        # Store the diagnosis text + the WhatsApp media id (temporary), never
        # the photo bytes.
        await _save_inbound(
            sender,
            "image",
            farmer_message,
            wa_message_id=message.id,
            media_ref=message.image.id,
            user=user,
        )
        await _log_photo_diagnosis(user, message.image.id, diagnosis, farmer_message)

        await _send_routed_reply(
            sender,
            user,
            RoutedReply(messages=[{"type": "text", "text": {"body": farmer_message}}], english_text=farmer_message),
            language_selected,
        )

    elif msg_type == "location" and message.location is not None:
        loc = message.location
        place = {
            "label": loc.name or loc.address or "your farm",
            "lat": loc.latitude,
            "lon": loc.longitude,
            "state": find_state(loc.address or loc.name or ""),
            "exact": True,
        }
        logger.info("Location pin from %s", mask_phone(sender))
        await _save_inbound(sender, "location", place["label"], wa_message_id=message.id, user=user)
        if user is None or getattr(user, "consent_status", None) != "granted":
            return
        reply = await route_message(user, "", location=place)
        await _send_routed_reply(sender, user, reply, language_selected)

    elif msg_type == "interactive" and message.interactive is not None:
        choice = message.interactive.list_reply or message.interactive.button_reply
        if choice is None:
            return
        await _save_inbound(sender, "button", choice.title or choice.id, wa_message_id=message.id, user=user)
        if user is None or getattr(user, "consent_status", None) != "granted":
            return
        reply = await _menu_choice(user, choice.id, choice.title or "")
        await _send_routed_reply(sender, user, reply, language_selected)

    else:
        logger.info("Unhandled message type: %s", msg_type)


async def _to_english(text: str, language: str | None) -> str:
    """Typed messages in Yoruba, Hausa, Igbo or Pidgin go to English first."""
    if normalize_language(language) == "english" or not text.strip():
        return text
    result = await translate_to_english(text)
    return result.get("english") or text


def _flow_takes_photos(user) -> bool:
    from app.services.advisor.specs import FLOWS

    spec = FLOWS.get(getattr(user, "flow", None) or "")
    return bool(spec and spec.photo_task and not (user.flow_data or {}).get("stage"))


async def _route_photo_to_flow(user, caption_en: str, image_bytes: bytes, mime_type: str) -> RoutedReply:
    from app.services.advisor import engine as advisor

    if getattr(user, "consent_status", None) != "granted":
        return RoutedReply(messages=[], english_text=None)
    reply = await advisor.continue_flow(user, caption_en, photo=(image_bytes, mime_type))
    return reply or RoutedReply(messages=[], english_text=None)


async def _menu_choice(user, choice_id: str, title: str) -> RoutedReply:
    """A tap on the service menu inside the main pipeline."""
    from app.services.advisor import engine as advisor
    from app.voice.scripts import SCRIPTS

    if choice_id in ADVISOR_MENU_IDS:
        return await advisor.start_flow(user, choice_id)
    from app.trade import flow as trade_flow

    if choice_id in trade_flow.CHOICES:
        return await trade_flow.handle_choice(user, choice_id)
    if choice_id == "crop_detection":
        try:
            await storage.close_case(user.phone)
            user.flow = "crop_detection"
            user.flow_data = {}
            await storage.save_user(user)
        except Exception as error:  # noqa: BLE001
            logger.warning("Could not start crop detection for %s: %s", mask_phone(user.phone), error)
        prompt = (SCRIPTS.get("diagnosis_prompt") or {}).get("english") or (
            "Please send a photo or voice note of your crop problem, and we will diagnose it for you."
        )
        return RoutedReply(messages=[{"type": "text", "text": {"body": prompt}}], english_text=prompt)
    if choice_id in ("market_price", "connect_vendor"):
        text = (SCRIPTS.get("feature_coming_soon") or {}).get("english") or "This feature will be added soon."
        return RoutedReply(messages=[{"type": "text", "text": {"body": text}}], english_text=text, show_menu=True)
    # Any other button: treat its title as a message.
    return await _route_text_or_voice(user, title or choice_id)


async def _log_photo_diagnosis(user, media_id: str, diagnosis: dict, farmer_message: str) -> None:
    """Log the photo diagnosis as labelled data (consented users only)."""
    if user is None or getattr(user, "consent_status", None) != "granted":
        return
    primary = (diagnosis.get("diagnosis") or {}).get("primary") or {}
    try:
        from app.services import natlas
        from app.services.vision import VISION_MODEL_NAME

        await storage.log_diagnosis(
            case_id=None,
            wa_id=user.phone,
            source="photo",
            model_name=f"{VISION_MODEL_NAME} (observe) + {natlas.MODEL_NAME} (diagnose)",
            photo_ref=media_id,
            raw_output=json.dumps(diagnosis, ensure_ascii=False, default=str)[:20000],
            crop=(diagnosis.get("crop") or {}).get("name"),
            diagnosis_name=primary.get("name"),
            category=primary.get("category"),
            confidence=primary.get("confidence"),
            flags=[str(diagnosis.get("image_status") or "")],
            reply_text_en=farmer_message,
        )
    except Exception as error:  # noqa: BLE001
        logger.warning("Could not log photo diagnosis for %s: %s", mask_phone(user.phone), error)


async def _route_text_or_voice(user, english_text: str) -> RoutedReply:
    """Route a text or voice (now in English) message through the brain."""
    if user is None or getattr(user, "consent_status", None) != "granted":
        # Not consented: do not route, do not store beyond dedupe.
        return RoutedReply(messages=[], english_text=None)

    # The "please wait" voice note is sent at the start of photo/voice
    # processing (see _schedule_wait_notice), once per request.

    return await route_message(user, english_text)


_DIGITS = re.compile(r"\d+(?:[.,]\d+)*")


def _same_numbers(english: str, translated: str) -> bool:
    """A translated card must keep every number exactly."""
    norm = lambda t: sorted(n.replace(",", "") for n in _DIGITS.findall(t))  # noqa: E731
    return norm(english) == norm(translated)


_whatsapp_service = None


def _whatsapp():
    global _whatsapp_service
    if _whatsapp_service is None:
        from app.services.whatsapp_onboarding import WhatsAppService

        _whatsapp_service = WhatsAppService(
            access_token=os.getenv("WHATSAPP_ACCESS_TOKEN", ""),
            phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""),
        )
    return _whatsapp_service


async def _send_interactive(phone: str, payload: dict[str, Any]) -> bool:
    from app.onboarding.onboarding_send import validate_message

    return await _whatsapp().safe_send(phone, validate_message(dict(payload)), logger)


async def _send_routed_reply(
    phone: str,
    user,
    reply: RoutedReply,
    language_selected: str | None,
) -> None:
    """Send the router's reply, translate+TTS where needed, and record it."""
    if not reply.messages and not reply.show_menu:
        return
    language = normalize_language(language_selected)

    # If the router signals a flow switch or case close, apply it.
    if reply.close_case:
        try:
            await storage.close_case(phone)
        except Exception as error:
            logger.warning("Could not close case for %s: %s", mask_phone(phone), error)

    if reply.new_flow:
        try:
            user_obj = await storage.get_user(phone)
            if user_obj is not None:
                user_obj.flow = reply.new_flow
                user_obj.step = None
                user_obj.current_case_id = None
                user_obj.case_started_at = None
                user_obj.last_activity_at = None
                user_obj.case_crop = None
                user_obj.case_part = None
                user_obj.case_symptoms = []
                user_obj.questions_asked = 0
                user_obj.diagnoses_in_case = 0
                user_obj.flow_data = {}
                await storage.save_user(user_obj)
        except Exception as error:
            logger.warning("Could not set flow=%s for %s: %s", reply.new_flow, mask_phone(phone), error)

    for msg in reply.messages:
        kind = msg.get("type")
        if kind == "text":
            body = msg.get("text", {}).get("body", "")
            if not body.strip():
                continue
            # Translate the English body into the user's language.
            translated, _ = await translate_for_speech(body, language)
            if msg.get("voice") is False:
                # Text card with exact numbers: never let translation change a
                # number; fall back to the English card if it did.
                if not _same_numbers(body, translated):
                    logger.warning("Translated card changed numbers; sending English card to %s", mask_phone(phone))
                    translated = body
                sent = await send_text(phone, translated)
                logger.info("Sent text card to %s: success=%s", mask_phone(phone), sent)
                continue
            voice_note = await text_to_speech(translated, language)
            if voice_note:
                sent = await send_voice_note(phone, voice_note)
                logger.info(
                    "Sent routed voice reply to %s: success=%s",
                    mask_phone(phone),
                    sent,
                )
            else:
                sent = await send_text(phone, translated)
                logger.info(
                    "TTS unavailable for %s; sent text fallback to %s: success=%s",
                    language,
                    mask_phone(phone),
                    sent,
                )
                await _save_inbound(phone, "text", translated, user=user)

        elif kind == "interactive":
            sent = await _send_interactive(phone, msg)
            logger.info(
                "Sent routed interactive reply to %s: success=%s",
                mask_phone(phone),
                sent,
            )

        elif kind == "document":
            doc = msg.get("document") or {}
            sent = await send_document(
                phone,
                doc.get("bytes") or b"",
                doc.get("filename") or "document.pdf",
                caption=doc.get("caption"),
                mime_type=doc.get("mime_type") or "application/pdf",
            )
            logger.info("Sent document to %s: success=%s", mask_phone(phone), sent)

        else:
            logger.warning("Unhandled routed message type: %s", kind)

    if reply.show_menu:
        name = getattr(user, "preferred_name", None) or getattr(user, "name", None)
        menu = _menu_payload(language, name, role=getattr(user, "role", None), kind=getattr(reply, "menu", None))
        sent = await _send_interactive(phone, menu)
        logger.info("Sent service menu to %s: success=%s", mask_phone(phone), sent)


def _interactive_text(msg: dict[str, Any]) -> str:
    """Best-effort user-visible text from an interactive payload."""
    interactive = msg.get("interactive") or {}
    body = interactive.get("body") or {}
    return body.get("text", "")
