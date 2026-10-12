"""WhatsApp webhook endpoints: verification and message delivery."""

import logging
import os
from typing import Any

from dotenv import load_dotenv
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import PlainTextResponse

from app.config import Settings, get_settings
from app.core.security import verify_signature
from app.onboarding.messages import (
    GENERIC_ERROR_MESSAGE,
    LANGUAGES,
    STORAGE_FALLBACK_MESSAGE,
    text_message,
)
from app.onboarding.onboarding_send import (
    mark_seen,
    safe_send_whatsapp,
    send_voice_then_messages,
    validate_message,
    voice_script_for_step,
)
from app.onboarding.state_machine import OnboardingStateMachine
from app.schemas.webhook import WebhookPayload
from app.services.chat_history import record_inbound, record_outgoing
from app.services.whatsapp.messages import handle_message
from app.services.whatsapp_onboarding import WhatsAppService
from app.storage import StorageUnavailableError, mask_phone, storage
from app.voice.onboarding import VoiceOnboarding

load_dotenv()


logger = logging.getLogger("webhook")

router = APIRouter()


onboarding = OnboardingStateMachine(storage)

# The text state machine owns every state and stored value. The voice module
# only resolves a voice note into an answer id, which is then handed to the
# same handler (combined-onboarding-spec section 5, rule 4).
voice_onboarding = VoiceOnboarding(storage)


whatsapp = WhatsAppService(
    access_token=os.getenv("WHATSAPP_ACCESS_TOKEN"),
    phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID"),
)


@router.get("/webhook", response_class=PlainTextResponse)
async def verify_webhook(
    mode: str | None = Query(None, alias="hub.mode"),
    token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str | None = Query(None, alias="hub.challenge"),
    settings: Settings = Depends(get_settings),
) -> PlainTextResponse:
    """Meta calls this once when you register the webhook URL."""
    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        # Must echo the challenge back as plain text
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


def _as_list(
    reply: dict[str, Any] | list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Normalize a state machine reply into a list of messages."""
    if reply is None:
        return []
    if isinstance(reply, list):
        return list(reply)
    return [reply]


async def _send_fallback(phone: str, body: str) -> None:
    """Send one plain fallback message and never raise into the webhook."""
    await safe_send_whatsapp(
        whatsapp, phone, validate_message(text_message(body)), logger
    )


@router.post("/webhook")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Accept a webhook delivery, then process it out-of-band."""
    raw_body = await request.body()
    if not verify_signature(
        raw_body,
        request.headers.get("X-Hub-Signature-256"),
        settings.whatsapp_app_secret,
    ):
        raise HTTPException(status_code=403, detail="Invalid signature")
    logger.info("here")
    payload_json = await request.json()
    value = payload_json["entry"][0]["changes"][0]["value"]

    messages = value.get("messages")
    if not messages:
        logger.info("Webhook event has no incoming message. Ignoring it.")
        return {"status": "ignored"}

    message = messages[0]
    phone = message["from"]
    name = value["contacts"][0]["profile"]["name"]
    message_id = message.get("id")
    is_audio = message.get("type") == "audio"

    seen = await mark_seen(message_id, phone)

    # Dedupe by WhatsApp message id so a Meta retry cannot send a voice note
    # twice and cannot re-run onboarding for the same inbound message.
    if not seen:
        logger.info(
            "Duplicate webhook message %s from %s; ignoring",
            message_id,
            mask_phone(phone),
        )
        return {"status": "received"} 


    
    try:
        user = await onboarding.get_or_create_user(phone, name)
    except StorageUnavailableError as error:
        logger.error("Storage unavailable for %s: %s", mask_phone(phone), error)
        await _send_fallback(phone, STORAGE_FALLBACK_MESSAGE)
        return {"status": "received"}

    prev_state = user.state
    

    # Track the onboarding message (text or a button/list reply), but only if
    # the user has consented. Runs after the 200, and a storage failure only
    # logs. An active user's messages are recorded by the main pipeline.
    if prev_state != "active":
        background_tasks.add_task(
            record_inbound, phone, message, wa_message_id=message_id
        )

    # Resolve the inbound message into an answer id the text state machine
    # understands. A voice note is resolved by the voice module; text and
    # button taps are normalized by the state machine itself.
    answer: str | None = None
    prefix: list[dict[str, Any]] = []
    voice_override: str | None = None
    send_direct = False
    try:
        if is_audio:
            resolution = await voice_onboarding.resolve_audio(user, message)
            answer = resolution.answer
            prefix = list(resolution.prefix_messages)
            # A voice step may name its own script (for example a replay).
            voice_override = resolution.voice_script_id
            # With no answer id the voice note produced its own messages (a
            # retry prompt, the unsupported notice, or a replayed step) or
            # nothing at all; never run the text handler with a None answer.
            send_direct = answer is None
        else:
            answer = onboarding.normalize_input(message)

        if send_direct:
            response = prefix
        else:
            if is_audio:
                inbound: dict[str, Any] = {
                    "type": "interactive",
                    "interactive": {"button_reply": {"id": answer}},
                }
            else:
                inbound = message
            reply = await onboarding.process(phone, inbound, name)
            if reply is not None: 
                response = prefix + _as_list(reply)
            else: 
                print("We are here")
                response = None
    except StorageUnavailableError as error:
        logger.error("Storage unavailable for %s: %s", mask_phone(phone), error)
        await _send_fallback(phone, STORAGE_FALLBACK_MESSAGE)
        return {"status": "received"}
    except Exception as error:  # noqa: BLE001 - a failure must not become a 500
        logger.exception("Onboarding failed for %s: %s", mask_phone(phone), error)
        await _send_fallback(phone, GENERIC_ERROR_MESSAGE)
        return {"status": "received"}

    if response:
        # Voice note first, then the text / interactive messages. The helper
        # awaits the voice send and never lets it block the text path.

        language = LANGUAGES.get(answer) or user.language
        script_id = voice_override or voice_script_for_step(prev_state, answer)
        await send_voice_then_messages(
            whatsapp,
            phone,
            script_id,
            response,
            logger,
            language=language,
        )
        # Track the outgoing reply (and the voice script) after the 200, only
        # if the user has consented.
        background_tasks.add_task(
            record_outgoing,
            phone,
            response,
            script_id=script_id,
            language=language,
        )
        return {"status": "received"}

    # An active user's ordinary message has no onboarding reply; hand it to the
    # main AjaTrade pipeline in the background.
    # Re-read the user: process() may have changed and saved it, and the copy
    # loaded at the top of this request is stale.
    try:
        user = await storage.get_user(phone) or user
    except StorageUnavailableError:
        pass
    if user.state == "active":
        logger.info("Active user; handing the message to the main pipeline")
        payload = WebhookPayload.model_validate(payload_json)
        for entry in payload.entry:
            for change in entry.changes:
                change_value = change.value
                contacts = {
                    contact.wa_id: contact.profile.name
                    for contact in change_value.contacts
                }
                for msg in change_value.messages:
                    background_tasks.add_task(
                        handle_message, msg, contacts.get(msg.from_)
                    )
        return {"status": "received"}

    # A voice step that intentionally sends nothing (replays exhausted).
    return {"status": "received"}
