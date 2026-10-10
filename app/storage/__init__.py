"""Storage backends for AjaTrade.

Handlers and the WhatsApp adapter import ``storage`` from here and never
touch SQL. The SQLAlchemy models live in :mod:`app.storage.models`.
"""

from app.storage.models import (
    Base,
    ConversationMemoryRow,
    DiagnosisLogRow,
    MessageRow,
    ProcessedMessageRow,
    UserRow,
    OpenCase,
    UserFlow,
    UserStep,
    new_case_id,
    VISION_MODEL_NAME,
    TEXT_MODEL_NAME,
)
from app.storage.postgres import (
    PROCESSED_MESSAGE_RETENTION_DAYS,
    PostgresStorage,
    StorageUnavailableError,
    mask_phone,
    storage,
)

__all__ = [
    "Base",
    "ConversationMemoryRow",
    "DiagnosisLogRow",
    "MessageRow",
    "ProcessedMessageRow",
    "UserRow",
    "OpenCase",
    "UserFlow",
    "UserStep",
    "new_case_id",
    "VISION_MODEL_NAME",
    "TEXT_MODEL_NAME",
    "PostgresStorage",
    "PROCESSED_MESSAGE_RETENTION_DAYS",
    "StorageUnavailableError",
    "mask_phone",
    "storage",
]
