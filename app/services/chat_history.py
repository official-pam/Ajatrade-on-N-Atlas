"""Chat-history recording.

The single place that decides whether a message may be saved, and what shape
it takes. A message is only written when the user's ``consent_status`` is
``granted`` (SPEC.md section 4). Nothing here ever receives raw audio or photo
bytes: voice is stored as its transcript and photos as their diagnosis text.

These are plain Python helpers with no WhatsApp imports. They are safe to call
from a background task; a storage failure is logged and never raised, so the
webhook can never be taken down by chat-history bookkeeping.
"""

from __future__ import annotations

import logging
from typing import Any

from app.schemas.chat import ChatMessage
from app.schemas.memory import User
from app.storage import StorageUnavailableError, mask_phone, storage

logger = logging.getLogger("chat_history")


def whatsapp_message_type(msg_type: str | None) -> str:
    """Map a WhatsApp message type to the stored ``msg_type``.

    WhatsApp calls voice notes ``audio`` and calls button/list replies
    ``interactive``; the table uses ``voice`` and ``button`` (SPEC.md section 3).
    """
    if msg_type == "audio":
        return "voice"
    if msg_type == "interactive":
        return "button"
    if msg_type in {"text", "image", "location"}:
        return msg_type
    return msg_type or "text"


def inbound_text(message: dict[str, Any]) -> str | None:
    """Return the user-visible text of an incoming WhatsApp message.

    Button and list replies store their title (never the internal reply id, so
    no internal ids reach the stored history). Text and image captions store
    their body.
    """
    interactive = message.get("interactive") or {}
    reply = interactive.get("button_reply") or interactive.get("list_reply")
    if reply:
        return reply.get("title")
    text = (message.get("text") or {}).get("body")
    if text is not None:
        return text
    return (message.get("image") or {}).get("caption")


def outbound_text(message: dict[str, Any]) -> str | None:
    """Return the user-visible text of an outgoing WhatsApp payload."""
    if message.get("type") == "text":
        return (message.get("text") or {}).get("body")
    if message.get("type") == "interactive":
        return ((message.get("interactive") or {}).get("body") or {}).get("text")
    return None


async def record_message(
    phone: str,
    direction: str,
    msg_type: str,
    text: str | None,
    *,
    wa_message_id: str | None = None,
    language: str | None = None,
    intent: str | None = None,
    media_ref: str | None = None,
    case_id: Any = None,
    user: User | None = None,
) -> None:
    """Save one message when the user has consented. Never raises.

    When ``user`` is not supplied it is looked up, so the consent decision is
    always made against the latest stored status rather than a stale object.

    ``media_ref`` is the WhatsApp media id for an image message (temporary).
    ``case_id`` links a message to an open crop-detection case.
    """
    try:
        if user is None:
            user = await storage.get_user(phone)
        if user is None or user.consent_status != "granted":
            return
        await storage.save_message(
            ChatMessage(
                wa_id=phone,
                direction=direction,
                msg_type=msg_type,
                text=text,
                wa_message_id=wa_message_id,
                language=language or user.language,
                intent=intent,
                media_ref=media_ref,
                case_id=case_id,
            )
        )
    except StorageUnavailableError as error:
        logger.error(
            "Could not save %s message for %s: %s",
            direction,
            mask_phone(phone),
            error,
        )
    except Exception:  # noqa: BLE001 - bookkeeping must never crash a flow
        logger.exception(
            "Unexpected error saving %s message for %s",
            direction,
            mask_phone(phone),
        )


async def record_inbound(
    phone: str,
    message: dict[str, Any],
    *,
    wa_message_id: str | None = None,
) -> None:
    """Save an incoming onboarding message (text or a button/list reply).

    Voice notes are skipped here: their transcript only exists inside the main
    pipeline, which records them itself.
    """
    msg_type = whatsapp_message_type(message.get("type"))
    if msg_type == "voice":
        return
    await record_message(
        phone,
        "in",
        msg_type,
        inbound_text(message),
        wa_message_id=wa_message_id,
    )


async def record_outgoing(
    phone: str,
    messages: list[dict[str, Any]] | dict[str, Any] | None,
    *,
    script_id: str | None = None,
    language: str | None = None,
) -> None:
    """Save each outgoing text/interactive message, and the voice script if any."""
    if messages is None:
        messages_list: list[dict[str, Any]] = []
    elif isinstance(messages, dict):
        messages_list = [messages]
    else:
        messages_list = list(messages)

    for message in messages_list:
        await record_message(
            phone,
            "out",
            whatsapp_message_type(message.get("type")),
            outbound_text(message),
            language=language,
        )

    if script_id:
        script_text = _voice_script_text(script_id, language)
        if script_text:
            await record_message(
                phone, "out", "voice", script_text, language=language
            )


def _voice_script_text(script_id: str, language: str | None) -> str | None:
    """Return the source text of a pre-generated onboarding voice script."""
    try:
        from app.voice.audio_store import default_store

        return default_store.get_script_text(script_id, language or "english")
    except Exception:  # noqa: BLE001 - a missing asset must not stop recording
        logger.exception("Could not read voice script text for %s", script_id)
        return None
