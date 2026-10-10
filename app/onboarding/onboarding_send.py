"""Shared helpers for the combined voice + text onboarding flow.

This is the single place that implements the "voice note, then text + buttons"
rule from the combined onboarding spec, so neither the voice nor the text flow
copies that step logic.

It also owns:
- durable dedupe of WhatsApp message ids (Postgres; survives restarts),
- webhook-safe sending through the WhatsApp service,
- safe interactive-title handling via the WhatsApp helpers.
"""

from __future__ import annotations

import logging
from typing import Any

from app.onboarding.messages import (
    LANGUAGES,
    ROLES,
    consent_message,
    create_role_message,
    text_message,
    service_menu
)
from app.services.whatsapp.helpers import (
    MAX_REPLY_BUTTONS,
    safe_title,
    sanitize_list_rows,
    validate_interactive_buttons,
    validate_interactive_payload,
)
from app.services.whatsapp_onboarding import WhatsAppService
from app.storage import storage

logger = logging.getLogger("onboarding.send")


async def mark_seen(message_id: str, wa_id: str | None = None) -> bool:
    """Record a message id as seen, durably, in Postgres.

    Returns True when this is the first time we have seen the id.

    The insert is atomic (primary key + ON CONFLICT DO NOTHING), so the
    database decides the winner when two deliveries of the same id arrive at
    the same instant, and the answer survives a restart. Any database failure
    is logged and treated as "first time", so a user's message is never
    dropped because dedup was unavailable.
    """
    if not message_id:
        return True
    try:
        return await storage.mark_seen(message_id, wa_id)
    except Exception:  # noqa: BLE001 - dedup must never drop a message
        logger.exception("Dedup insert failed for message %s", message_id)
        return True


async def safe_send_whatsapp(
    whatsapp: WhatsAppService,
    phone: str,
    message: dict[str, Any] | list[dict[str, Any]],
    log: logging.Logger,
) -> bool:
    """Send a WhatsApp message and never let a send failure become an exception.

    Returns True on success, False on any failure. The caller decides what to
    do when False is returned.
    """
    try:
        await whatsapp.safe_send(phone, message, log)
        return True
    except Exception as exc:  # noqa: BLE001 - we must not crash the webhook
        log.error(
            "WhatsApp send raised for %s: %s",
            phone,
            exc,
        )
        return False


def sanitize_interactive(message: dict[str, Any]) -> dict[str, Any]:
    """Apply WhatsApp interactive limits to one message payload in place.

    - reply buttons: at most MAX_REPLY_BUTTONS, each title safe_title'd,
    - list rows: capped and row titles safe_title'd,
    - interactive body text: kept under the 1024 limit by truncation with a
      note when needed.
    """
    print("sanitize_interactive: : ", message)
    if message.get("type") != "interactive":
        return message

    interactive = dict(message.get("interactive", {}))
    message_type = interactive.get("type")

    if message_type == "button":
        action = dict(interactive.get("action", {}))
        action["buttons"] = validate_interactive_buttons(action.get("buttons", []))
        interactive = {**interactive, "action": action}

    elif message_type == "list":
        action = dict(interactive.get("action", {}))
        sections = []
        for section in action.get("sections") or []:
            section = dict(section)
            section["rows"] = sanitize_list_rows(section.get("rows") or [])
            sections.append(section)
        if sections:
            action["sections"] = sections
        interactive = {**interactive, "action": action}

    # Body text for interactive messages must not exceed 1024 characters.
    body = interactive.get("body", {})
    text = (body.get("text") or "")
    if len(text) > 1024:
        interactive = {
            **interactive,
            "body": {**body, "text": text[:1024]},
        }

    message = {**message, "interactive": interactive}
    return message


def ensure_text_body_not_empty(message: dict[str, Any]) -> dict[str, Any]:
    """Ensure a plain text message has a non-empty body, or render a safe fallback.

    WhatsApp rejects an empty text body.
    """
    if message.get("type") != "text":
        return message
    text = (message.get("text") or {}).get("body") or ""
    if not text.strip():
        message = {
            "type": "text",
            "text": {"body": "AjaTrade here. Please try again."},
        }
    return message


def validate_message(message: dict[str, Any]) -> dict[str, Any]:
    """Run all pre-send interactive/text safety checks on one message payload."""
    message = sanitize_interactive(message)
    message = ensure_text_body_not_empty(message)
    print("message", message)
    return message


async def send_voice_then_messages(
    whatsapp: WhatsAppService,
    phone: str,
    voice_script_id: str | None,
    messages: list[dict[str, Any]] | dict[str, Any] | None,
    log: logging.Logger,
    *,
    language: str | None = None,
    voice_text_fallback: str | None = None,
) -> list[dict[str, Any]]:
    """Send a voice note first, then the text / interactive messages.

    This is the shared step both onboarding flows should use when they want the
    combined voice + text behaviour from the combined onboarding spec.

    Rules:
    - Await the voice send before sending the text/buttons, unless there is no
      voice script for this step.
    - If the voice send fails, or the script is missing, send the text/buttons
      anyway and send the text fallback of the script when it is available.
    - Every voice send is wrapped in try/except and logged.
    - The returned messages are safe for WhatsApp interactive limits.

    Parameters:
        whatsapp: the WhatsApp service instance.
        phone: recipient phone.
        voice_script_id: onboarding script id for the voice note, or None when
            this step has no voice note.
        messages: the text / interactive messages to send after the voice note.
            A single message dict is normalized to a list.
        log: logger for this send path.
        language: user language used to choose the right voice script variant.
        voice_text_fallback: text to send when the voice note cannot be sent and
            there is no audio file for the script. When None, the audio store's
            own text fallback is used.
    """
    result: list[dict[str, Any]] = []

    messages_list: list[dict[str, Any]]
    if messages is None:
        messages_list = []
    elif isinstance(messages, dict):
        messages_list = [messages]
    else:
        messages_list = list(messages)

    if voice_script_id:
        sent = await send_onboarding_voice_note(
            whatsapp,
            phone,
            voice_script_id,
            log,
            language=language,
            text_fallback=voice_text_fallback,
        )
        if not sent and voice_text_fallback:
            result.append(text_message(voice_text_fallback))

    for msg in messages_list:
        safe = validate_message(dict(msg))
        print("Safe", safe)
        sent = await safe_send_whatsapp(whatsapp, phone, safe, log)
        if not sent:
            log.error(
                "WhatsApp send failed for step %s to %s; message type=%s",
                voice_script_id or "no-voice",
                phone,
                safe.get("type"),
            )

        result.append(safe)

    return result


async def send_onboarding_voice_note(
    whatsapp: WhatsAppService,
    phone: str,
    script_id: str,
    log: logging.Logger,
    *,
    language: str | None = None,
    text_fallback: str | None = None,
) -> bool:
    """Send one pre-generated onboarding voice note, falling back to text.

    The voice side should not talk to the audio store directly for sending;
    this helper centralises that send so the voice/text order rule stays in one
    place. It delegates the actual audio send to the audio store, but guards it
    so a failure is logged and never thrown to the webhook.
    """
    try:
        from app.voice.audio_store import default_store

        lang = (language or "english").strip().lower() or "english"
        await default_store.send_voice_script(phone, script_id, lang)
        log.info(
            "Sent onboarding voice note %s / %s to %s",
            script_id,
            lang,
            phone,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - never block the text path
        log.error(
            "Onboarding voice note %s / %s could not be sent to %s: %s",
            script_id,
            language,
            phone,
            exc,
        )
        if text_fallback:
            await safe_send_whatsapp(whatsapp, phone, text_message(text_fallback), log)
        return False


def role_buttons_message(
    language: str | None,
) -> dict[str, Any]:
    """Return the role selection interactive message for a stored language."""
    return create_role_message(language or LANGUAGES.get("lang_en"))


def startup_menu_message(language: str| None, name: str | None) -> dict[str, Any]: 
    """Return the startup menu interactive message for a stored language and name."""
    return service_menu(language, name)


def consent_buttons_message(
    language: str | None,
) -> dict[str, Any]:
    """Return the consent interactive message for a stored language.

    The text flow owns the exact stored-language mapping. This helper wraps it
    so the voice flow can hand it the same button ids. The shared text assets
    are missing button labels for some stored values (Hausa's key has trailing
    spaces; the stored Pidgin value is "nigerian pidgin" but the key is
    "pidgin"), which raises ``KeyError``; English is the fallback so a user is
    never dropped mid-onboarding. Flagged to the owner rather than fixed in the
    text flow here (combined-onboarding-spec section 8, decision 3).
    """
    lang = (language or LANGUAGES.get("lang_en") or "english").strip().lower()
    try:
        return consent_message(lang)
    except KeyError:
        logger.error(
            "No consent buttons for language=%r; falling back to English",
            language,
        )
        return consent_message("english")


def voice_script_for_step(
    prev_state: str | None,
    answer: str | None,
) -> str | None:
    """Return the onboarding voice script id for the reply being sent.

    Maps the step the state machine just handled to its pre-generated voice
    note (combined-onboarding-spec section 5). Returns ``None`` when the step
    has no voice note. ``prev_state`` is the user's state before the message
    was processed; ``answer`` is the id the state machine received.
    """
    if prev_state in ("new", "awaiting_language") and answer in LANGUAGES:
        return "welcome_consent"
    if prev_state == "awaiting_consent":
        if answer == "consent_read_more":
            return "privacy_notice"
        if answer in ("consent_yes", "consent_no"):
            return "role_ask"
    if prev_state == "awaiting_role": 
            return "service_menu_prompt"
    if prev_state == "active":
        # Main menu choices for an active user: the pre-generated voice note
        # that plays before the text reply (TODO_new_voice_notes.md).
        if answer == "crop_detection":
            return "diagnosis_prompt"
        if answer in ("market_price", "connect_vendor"):
            return "feature_coming_soon"
    return None


def role_buttons_for(
    language: str | None,
    role_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return role reply buttons for the given language, in stored ROLES order.

    When ``role_ids`` is provided, only those role ids are included. This is
    useful for the voice flow when it has mapped an audio answer to a single
    role id.
    """
    lang = (language or LANGUAGES.get("lang_en") or "english").strip().lower()
    all_buttons = create_role_message(lang).get("interactive", {}).get("action", {}).get(
        "buttons", []
    )
    if not role_ids:
        return all_buttons
    id_set = set(role_ids)
    return [b for b in all_buttons if b.get("reply", {}).get("id") in id_set]
