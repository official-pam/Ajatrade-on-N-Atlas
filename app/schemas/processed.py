"""Processed-message model (webhook dedup).

Persistence lives in :mod:`app.storage`; this module only holds the shape of
one deduplicated webhook delivery, matching the ``processed_messages`` table
columns (SPEC.md section 6, dedupe rule).

Purpose: a Meta retry or a replayed backlog must not run the same inbound
message twice, and the answer must survive a restart, so message ids are kept
in Postgres instead of an in-memory set. ``app.storage.storage.mark_seen`` is
the only writer; rows are deleted after
``PROCESSED_MESSAGE_RETENTION_DAYS`` days because Meta only retries for a
short window.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ProcessedMessage:
    """One inbound WhatsApp message id held in ``processed_messages``.

    ``message_id`` is the primary key (the WhatsApp ``wamid...`` id). The
    primary key plus ``ON CONFLICT DO NOTHING`` is what makes dedup safe when
    two deliveries of the same id arrive at the same instant: the database
    decides, not Python.

    ``wa_id`` is the sender's number in the same format the ``users`` table
    uses, so records correlate. It has no foreign key: dedup runs before the
    user row is created. Never log it unmasked.

    ``received_at`` is when the id was first seen (timezone-aware, server
    default ``now()``). It carries the index ``ix_processed_messages_received_at``,
    which the retention cleanup deletes by.
    """

    message_id: str
    wa_id: str | None = None
    received_at: datetime | None = None
