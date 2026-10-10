from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.storage import mask_phone

logger = logging.getLogger("whatsapp")


def _parse_whatsapp_error(payload: dict[str, Any] | str) -> dict[str, Any]:
    """Best-effort extraction of a Meta error block from a failed send response.

    Meta often returns ``{"errors": [{"code": ..., "message": ...}]}``.
    """
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                return {
                    "code": first.get("code"),
                    "message": first.get("message"),
                }
        return {
            "code": payload.get("code"),
            "message": payload.get("message"),
        }
    return {"code": None, "message": str(payload)}


class WhatsAppService:

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        api_version: str = "v23.0",
    ):
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.api_version = api_version

        self.url = (
            f"https://graph.facebook.com/"
            f"{self.api_version}/"
            f"{self.phone_number_id}/messages"
        )

    async def send_message(
        self,
        recipient: str,
        message: dict[str, Any] | list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Send one WhatsApp message payload.

        The caller should guard this with :func:`safe_send` when a failed send
        must not become a 500 (for example inside the webhook response path).

        When this raises, it is because the HTTP call itself failed or Meta
        returned a non-200 response that could not be parsed.
        """
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient,
            **message,
        }

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.url,
                headers=headers,
                json=payload,
            )

            if response.is_error:
                parsed = _parse_whatsapp_error(response.json()) if response.json() else {}
                logger.error(
                    "WhatsApp %s send failed for %s: code=%s message=%s",
                    message.get("type") or "message",
                    mask_phone(recipient),
                    parsed.get("code"),
                    parsed.get("message"),
                )
                if response.status_code == 400:
                    payload_text = (
                        response.text[:500] if isinstance(response.text, str) else str(response.text)
                    )
                    logger.error(
                        "WhatsApp 400 payload for %s: %s",
                        mask_phone(recipient),
                        payload_text,
                    )
                response.raise_for_status()

            return response.json()

    async def safe_send(
        self,
        recipient: str,
        message: dict[str, Any] | list[dict[str, Any]],
        logger_obj: logging.Logger,
    ) -> bool:
        """Send a message and swallow errors so a send failure is never a 500.

        Returns ``True`` when the send succeeded, ``False`` on any failure.
        The payload is logged on failure so the cause is visible in production.

        This is the send to use from the webhook response path, where a send
        error must not become a 500 to Meta.
        """
        try:
            payload_obj = {
                "messaging_product": "whatsapp",
                "to": recipient,
                **message,
            }
            payload_json = json.dumps(payload_obj, ensure_ascii=False)
            await self.send_message(recipient, message)
            return True
        except Exception as error:
            logger_obj.error(
                "WhatsApp send failed for %s: type=%s: %s\nPayload: %s",
                mask_phone(recipient),
                type(error).__name__,
                error,
                payload_json[:2000],
            )
            return False