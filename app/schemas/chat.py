"""Chat-history model.

Persistence lives in :mod:`app.storage`; this module only holds the shape of
one stored message, matching the ``messages`` table columns (SPEC.md section 3,
with the additions from VOICE_NOTE.md section 6).

Raw audio and photos are never stored; ``media_ref`` is the WhatsApp media id
for an image message (temporary, since WhatsApp media ids expire).
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass
class ChatMessage:
    """One incoming or outgoing message held in ``messages``.

    ``text`` is the message text, the transcript for a voice note, or the
    diagnosis summary for a photo. Raw audio and photos are never stored.

    ``media_ref`` is the WhatsApp media id for image messages (temporary).
    ``case_id`` links a message to an open crop-detection case.
    """

    wa_id: str
    direction: str
    msg_type: str
    text: str | None = None
    wa_message_id: str | None = None
    language: str | None = None
    intent: str | None = None
    media_ref: str | None = None
    case_id: UUID | None = None
