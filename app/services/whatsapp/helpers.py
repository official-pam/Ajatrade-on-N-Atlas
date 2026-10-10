"""WhatsApp interactive-message safety helpers.

WhatsApp rejects interactive payloads with empty or overlong reply-button
titles (error 131009, "Button title length invalid, min 1, max 20"), and it
caps reply buttons per message. This module centralises those guards so the
same checks apply everywhere a WhatsApp interactive payload is sent.

Button and row ids are internal only and are never shown to users.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any

logger = logging.getLogger("whatsapp.helpers")

MAX_BUTTON_TITLE = 20
MAX_REPLY_BUTTONS = 3
MAX_LIST_ROWS = 10
MAX_LIST_ROW_TITLE = 24


def safe_text_body(text: str | None, fallback: str) -> str:
    """Make a WhatsApp text/interactive body safe to send.

    WhatsApp rejects an empty text body. This helper strips, falls back if
    empty, and leaves the length to the caller for the 4096 / 1024 limits.
    """
    body = (text or "").strip()
    if not body:
        body = fallback
    return body


def safe_title(text: str | None, fallback: str) -> str:
    """Normalize a WhatsApp reply-button title and make sure it is sendable.

    WhatsApp rejects empty or overlong reply-button titles (error 131009).
    This helper normalizes combining marks first, then strips, falls back if
    empty, and truncates to the allowed maximum.
    """
    if text is None:
        text = fallback
    title = unicodedata.normalize("NFC", text).strip()
    if not title:
        title = fallback
    if len(title) > MAX_BUTTON_TITLE:
        title = title[:MAX_BUTTON_TITLE]
    return title


def validate_interactive_buttons(
    buttons: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a safe, capped copy of a reply-button list for one interactive message.

    - At most ``MAX_REPLY_BUTTONS`` buttons are kept.
    - Each button title is wrapped with :func:`safe_title`, using the button id
      as the fallback when the title is empty.
    - Button ids are internal only and never shown to users.
    """
    safe: list[dict[str, Any]] = []
    for button in buttons[:MAX_REPLY_BUTTONS]:
        reply = button.get("reply", {})
        title = safe_title(
            reply.get("title"),
            fallback=reply.get("id", "option") or "option",
        )
        safe.append(
            {
                "type": "reply",
                "reply": {
                    "id": reply.get("id"),
                    "title": title,
                },
            }
        )
    return safe


def validate_interactive_payload(interactive: dict[str, Any]) -> dict[str, Any]:
    """Sanitize one interactive payload before sending.

    Currently this protects reply-button titles and caps the button count.
    List messages are guarded separately where rows are built.
    """
    action = interactive.get("action", {})
    buttons = action.get("buttons", [])
    if buttons and isinstance(buttons, list):
        action = {**action, "buttons": validate_interactive_buttons(buttons)}
        interactive = {**interactive, "action": action}
    return interactive


def sanitize_list_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Sanitize list rows before sending: cap count and row-title length.

    Row ids are internal only. Row titles are normalized and truncated to
    ``MAX_LIST_ROW_TITLE``; empty titles fall back to the row id.
    """
    safe: list[dict[str, Any]] = []
    for row in rows[:MAX_LIST_ROWS]:
        title = safe_title(
            row.get("title"),
            fallback=row.get("id", "option") or "option",
        )
        safe.append(
            {
                "id": row.get("id"),
                "title": title,
                "description": (row.get("description") or "").strip() or None,
            }
        )
    return safe
