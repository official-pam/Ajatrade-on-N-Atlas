"""SQLAlchemy models.

The ``users``, ``messages`` and ``conversation_memory`` tables are in scope.
``vendor_requests`` is a later item and must be added additively.

These models are the single source of truth for the schema: Alembic reads
``Base.metadata`` for autogeneration, and ``app.storage`` reads and writes rows
through them.

User state fields (``flow``, ``step``, ``current_case_id``, ...) track an open
crop-diagnosis case. They are cleared when a case closes.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Numeric,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    Boolean,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now_utc() -> datetime:
    """Return the current UTC time."""
    return datetime.now(tz=None)


# ---------------------------------------------------------------------------
# Config / provider constants (read from environment; never hardcoded)
# ---------------------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    """Best-effort environment read used by model helper defaults."""
    import os as _os
    return _os.environ.get(key, default)


# Vision model used by the photo diagnosis path. Read from the same model name
# already used in the existing disease_detection module; do not hardcode a
# different provider here.
VISION_MODEL_NAME = _env("VISION_MODEL_NAME", "") or _env("GROQ_MODEL", "qwen/qwen3.8-27b")

# Text model used for intent routing and the crop-description case flow.
# Groq is already used for translation; use the same provider/model until told
# otherwise (VOICE_NOTE.md section 14, question 1).
TEXT_MODEL_NAME = _env("TEXT_MODEL_NAME", "") or _env("GROQ_MODEL", "qwen/qwen3.8-27b")


class Base(DeclarativeBase):
    """Base class for all models."""


# ---------------------------------------------------------------------------
# User state helpers
# ---------------------------------------------------------------------------

class UserFlow:
    """Allowed values for ``UserRow.flow``."""

    CROP_DETECTION = "crop_detection"
    MARKET_PRICE = "market_price"
    CONNECT_VENDOR = "connect_vendor"

    # Farm-advice modules (precision agriculture problem set).
    IRRIGATION = "irrigation"
    SOIL_HEALTH = "soil_health"
    FERTILIZER = "fertilizer"
    WATER_SOURCE = "water_source"
    WEED_CONTROL = "weed_control"
    VARIETY_CHOICE = "variety_choice"
    GERMINATION = "germination"

    ADVISOR = (
        IRRIGATION,
        SOIL_HEALTH,
        FERTILIZER,
        WATER_SOURCE,
        WEED_CONTROL,
        VARIETY_CHOICE,
        GERMINATION,
    )
    # Trader ledger (sales, restocks, summaries, lender statement).
    TRADE = "trade"

    ALL = (CROP_DETECTION, MARKET_PRICE, CONNECT_VENDOR, *ADVISOR, TRADE)


class UserStep:
    """Allowed values for ``UserRow.step`` during a crop_detection case."""

    AWAITING_CROP = "awaiting_crop"
    AWAITING_DESCRIPTION = "awaiting_description"
    AWAITING_PHOTO_OPTIONAL = "awaiting_photo_optional"

    ALL = (AWAITING_CROP, AWAITING_DESCRIPTION, AWAITING_PHOTO_OPTIONAL)


@dataclass
class OpenCase:
    """Snapshot of the fields that describe an open crop-detection case."""

    flow: str
    step: str | None
    current_case_id: UUID | None
    case_started_at: datetime | None
    last_activity_at: datetime | None
    case_crop: str | None
    case_part: str | None
    case_symptoms: list[str]
    questions_asked: int
    diagnoses_in_case: int

    @classmethod
    def empty(cls) -> "OpenCase":
        return cls(
            flow=None,
            step=None,
            current_case_id=None,
            case_started_at=None,
            last_activity_at=None,
            case_crop=None,
            case_part=None,
            case_symptoms=[],
            questions_asked=0,
            diagnoses_in_case=0,
        )

    @property
    def has_case(self) -> bool:
        return self.current_case_id is not None


def new_case_id() -> UUID:
    return uuid4()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class UserRow(Base):
    """A user, keyed by WhatsApp id (``wa_id``)."""

    __tablename__ = "users"

    wa_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferred_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="new", default="new"
    )
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    language_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    language_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    role: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="pending", default="pending"
    )
    consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    onboarding: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    consent_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # ---- crop-diagnosis case state (VOICE_NOTE.md section 5) ----

    flow: Mapped[str | None] = mapped_column(
        Text, nullable=True, server_default=None
    )
    step: Mapped[str | None] = mapped_column(
        Text, nullable=True, server_default=None
    )
    current_case_id: Mapped[UUID | None] = mapped_column(
        Uuid, nullable=True, server_default=None
    )
    case_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    case_crop: Mapped[str | None] = mapped_column(
        Text, nullable=True, server_default=None
    )
    case_part: Mapped[str | None] = mapped_column(
        Text, nullable=True, server_default=None
    )
    case_symptoms: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text(" '[]'::jsonb"), default=list
    )
    questions_asked: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    diagnoses_in_case: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )

    # ---- farm-advice modules ----
    flow_data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text(" '{}'::jsonb"), default=dict
    )
    farm_profile: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text(" '{}'::jsonb"), default=dict
    )


class MessageRow(Base):
    """One saved incoming or outgoing message (chat history).

    Rows are only written for users whose ``consent_status`` is ``granted``.
    ``text`` holds the message text, the transcript for a voice note, or the
    diagnosis summary for a photo; raw audio and photos are never stored.

    ``media_ref`` is the WhatsApp media id for image messages (temporary —
    WhatsApp media ids expire, so this is not a durable photo store).
    ``case_id`` links a message to an open crop-detection case.
    """

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_wa_id_created_at", "wa_id", "created_at"),
        UniqueConstraint("wa_message_id", name="uq_messages_wa_message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wa_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.wa_id"), nullable=False
    )
    wa_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    msg_type: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_ref: Mapped[str | None] = mapped_column(
        Text, nullable=True, server_default=None
    )
    case_id: Mapped[UUID | None] = mapped_column(
    Uuid, nullable=True, index=True
)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConversationMemoryRow(Base):
    """Short rolling summary of what a consented user has shared.

    One row per user (``wa_id`` is the primary key). Updated once per closed
    case that produced a diagnosis, never per message (VOICE_NOTE.md section 10).
    """

    __tablename__ = "conversation_memory"
    __table_args__ = (
        Index("ix_conversation_memory_wa_id", "wa_id"),
    )

    wa_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.wa_id"), primary_key=True
    )
    summary: Mapped[str | None] = mapped_column(
        Text, nullable=True, server_default=None
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DiagnosisLogRow(Base):
    """One row per diagnosis sent (VOICE_NOTE.md section 6).

    Only users who accepted consent reach this code, so logging is allowed.
    The photo itself is never stored; ``photo_ref`` is the WhatsApp media id
    (temporary — WhatsApp media ids expire).
    """

    __tablename__ = "diagnosis_log"
    __table_args__ = (
        Index("ix_diagnosis_log_case_id", "case_id"),
        Index("ix_diagnosis_log_wa_id", "wa_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[UUID | None] = mapped_column(BigInteger, nullable=True, server_default=None)
    wa_id: Mapped[str] = mapped_column(Text, ForeignKey("users.wa_id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_ref: Mapped[str | None] = mapped_column(Text, nullable=True, server_default=None)
    raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    crop: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnosis_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text(" '[]'::jsonb"), default=list)
    reply_text_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_diagnosis: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProcessedMessageRow(Base):
    """One deduplicated inbound webhook message id.

    WhatsApp message ids only need to be remembered for as long as Meta might
    retry or replay a delivery, so old rows are purged after
    ``PROCESSED_MESSAGE_RETENTION_DAYS`` (see ``app.storage.postgres``).

    ``wa_id`` is the sender's number in the same format the ``users`` table
    uses, with no foreign key: dedup runs before the user row is created.

    ``received_at`` is indexed because the retention cleanup deletes by it.
    """

    __tablename__ = "processed_messages"
    __table_args__ = (
        Index("ix_processed_messages_received_at", "received_at"),
    )

    message_id: Mapped[str] = mapped_column(Text, primary_key=True)
    wa_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Trader ledger (additive, migration d4b8e3a2f1c7)
# ---------------------------------------------------------------------------


class TradeProductRow(Base):
    """One item in a trader's catalogue.

    Written only for consented users (the trade flow only runs for them).
    ``name_key`` is the normalised name used to find the item again
    ("Bags of Rice" -> "rice").
    """

    __tablename__ = "trade_products"
    __table_args__ = (
        UniqueConstraint("wa_id", "name_key", name="uq_trade_products_wa_id_name_key"),
        Index("ix_trade_products_wa_id", "wa_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wa_id: Mapped[str] = mapped_column(Text, ForeignKey("users.wa_id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_key: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_price: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    set_price: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    stock_qty: Mapped[float] = mapped_column(
        Numeric(14, 3), nullable=False, server_default=text("0"), default=0
    )
    low_stock_level: Mapped[float] = mapped_column(
        Numeric(14, 3), nullable=False, server_default=text("3"), default=3
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TradeEntryRow(Base):
    """One ledger line: a sale or a restock, with the prices at that moment.

    Prices are copied onto the row so later price changes never rewrite old
    records. ``profit`` and ``price_difference`` are only set for sales.
    """

    __tablename__ = "trade_entries"
    __table_args__ = (
        Index("ix_trade_entries_wa_id_created_at", "wa_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wa_id: Mapped[str] = mapped_column(Text, ForeignKey("users.wa_id"), nullable=False)
    product_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("trade_products.id"), nullable=True
    )
    product_name: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # sale | restock
    qty: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    unit_cost: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    set_price: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    profit: Mapped[float] = mapped_column(
        Numeric(16, 2), nullable=False, server_default=text("0"), default=0
    )
    price_difference: Mapped[float] = mapped_column(
        Numeric(16, 2), nullable=False, server_default=text("0"), default=0
    )
    stock_after: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)  # voice | text
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
