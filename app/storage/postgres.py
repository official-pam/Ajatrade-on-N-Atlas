"""PostgreSQL-backed user storage built on SQLAlchemy (async).

Same public interface as before: ``get_user``, ``save_user``, ``get_state`` and
``get_role``, all ``async``, returning ``None`` when a user does not exist, so
handlers and the state machine keep calling ``storage.get_user(...)``.

All SQL/SQLAlchemy access lives behind :class:`PostgresStorage`. Every call has
a timeout and one retry, and raises :class:`StorageUnavailableError` if the
database stays unreachable.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Awaitable, Callable, Optional, TypeVar

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.schemas.chat import ChatMessage

if TYPE_CHECKING:
    from app.trade.ledger import Entry, Product
from app.schemas.memory import User
from app.storage.models import (
    Base,
    ConversationMemoryRow,
    DiagnosisLogRow,
    MessageRow,
    ProcessedMessageRow,
    TradeEntryRow,
    TradeProductRow,
    UserRow,
    OpenCase,
)


logger = logging.getLogger("storage")

CONNECT_TIMEOUT_SECONDS = 5.0
QUERY_TIMEOUT_SECONDS = 5.0
RETRY_DELAY_SECONDS = 0.2

# Inbound webhook message ids only need to be remembered for as long as Meta
# might retry or replay a delivery, so older dedup rows are purged.
PROCESSED_MESSAGE_RETENTION_DAYS = 7

T = TypeVar("T")


class StorageUnavailableError(RuntimeError):
    """Raised when the database cannot be reached after one retry."""


def mask_phone(phone: str | None) -> str:
    """Mask a phone number so logs never contain the full number."""
    if not phone:
        return ""
    if len(phone) <= 4:
        return "*" * len(phone)
    return f"{phone[:2]}{'*' * (len(phone) - 4)}{phone[-2:]}"


def to_async_url(url: str) -> str:
    """Return a SQLAlchemy async (asyncpg) URL for a plain Postgres URL.

    Hosted Postgres (Neon, Supabase, Railway) hands out libpq-style URLs with
    ``sslmode`` and ``channel_binding``, which asyncpg rejects. ``sslmode``
    becomes asyncpg's ``ssl`` and ``channel_binding`` is dropped.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    url = (url or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    if not url.startswith("postgresql+asyncpg://") or "?" not in url:
        return url
    parts = urlsplit(url)
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == "channel_binding":
            continue
        if key == "sslmode":
            key = "ssl"
        query.append((key, value))
    return urlunsplit(parts._replace(query=urlencode(query, safe="/:")))


def _row_to_user(row: UserRow) -> User:
    """Convert a database row into the plain ``User`` the rest of the app uses."""
    return User(
        phone=row.wa_id,
        name=row.name,
        preferred_name=row.preferred_name,
        state=row.state,
        language=row.language,
        language_source=row.language_source,
        language_attempts=row.language_attempts or 0,
        role=row.role,
        consent_status=row.consent_status,
        consent_at=row.consent_at,
        consent_version=row.consent_version,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        onboarding=row.onboarding,
        flow=row.flow,
        step=row.step,
        current_case_id=row.current_case_id,
        last_activity_at=row.last_activity_at,
        case_crop=row.case_crop,
        case_part=row.case_part, 
        case_symptoms=row.case_symptoms, 
        questions_asked=row.questions_asked, 
        diagnoses_in_case=row.diagnoses_in_case,
        flow_data=dict(row.flow_data or {}),
        farm_profile=dict(row.farm_profile or {}),
    )


def _row_to_product(row: TradeProductRow) -> "Product":
    from decimal import Decimal

    from app.trade.ledger import Product

    return Product(
        id=row.id,
        name=row.name,
        unit=row.unit,
        cost_price=Decimal(row.cost_price),
        set_price=Decimal(row.set_price),
        stock_qty=Decimal(row.stock_qty),
        low_stock_level=Decimal(row.low_stock_level),
    )


def _row_to_entry(row: TradeEntryRow) -> "Entry":
    from decimal import Decimal

    from app.trade.ledger import Entry

    return Entry(
        kind=row.kind,
        product_name=row.product_name,
        qty=Decimal(row.qty),
        unit_price=Decimal(row.unit_price),
        unit_cost=Decimal(row.unit_cost),
        set_price=Decimal(row.set_price),
        amount=Decimal(row.amount),
        profit=Decimal(row.profit),
        price_difference=Decimal(row.price_difference),
        stock_after=Decimal(row.stock_after),
        unit=row.unit,
        product_id=row.product_id,
        created_at=row.created_at,
        source=row.source,
    )


class PostgresStorage:
    """User storage backed by PostgreSQL through SQLAlchemy."""

    def __init__(self, dsn: str | None = None):
        raw = dsn if dsn is not None else os.getenv("DATABASE_URL", "")
        self._dsn = to_async_url(raw) if raw else ""
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def connect(self) -> None:
        """Create the async engine and verify it once.

        Never raises: if the database is missing or unreachable it is logged
        and the app keeps running (queries then fail into
        :class:`StorageUnavailableError`).
        """
        if not self._dsn:
            logger.error("DATABASE_URL is not set; user storage is disabled")
            return
        self._engine = create_async_engine(
            self._dsn,
            pool_size=5,
            max_overflow=0,
            pool_pre_ping=True,
            connect_args={
                "timeout": CONNECT_TIMEOUT_SECONDS,
                "command_timeout": QUERY_TIMEOUT_SECONDS,
            },
        )
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False
        )
        try:
            await self._ping()
            await self._create_tables()
            logger.info("Connected to PostgreSQL")
        except Exception as error:  # noqa: BLE001 - startup must not crash
            logger.error("Could not reach PostgreSQL at startup: %s", error)

    async def _ping(self) -> None:
        """Run a cheap query to confirm the connection works."""
        if self._session_factory is None:
            raise StorageUnavailableError("database is not connected")
        async with self._session_factory() as session:
            await asyncio.wait_for(
                session.execute(text("SELECT 1")), QUERY_TIMEOUT_SECONDS
            )


    async def _create_tables(self) -> None:
        """Create any missing tables (development convenience)."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Tables ready: %s", list(Base.metadata.tables.keys()))

    async def set_conversation_memory(self, wa_id: str, summary: str) -> None:
        """Insert or replace the rolling summary for a user."""

        async def operation(session: AsyncSession) -> None:
            row = ConversationMemoryRow(wa_id=wa_id, summary=summary)
            await session.merge(row)
            await session.commit()

        await self._execute(operation)

    async def get_conversation_memory(self, wa_id: str) -> str | None:
        """Return the stored summary for a user, or ``None``."""

        async def operation(session: AsyncSession) -> str | None:
            result = await session.execute(
                select(ConversationMemoryRow.summary).where(
                    ConversationMemoryRow.wa_id == wa_id
                )
            )
            return result.scalar_one_or_none()

        return await self._execute(operation)

    async def log_diagnosis(
        self,
        case_id: UUID | None,
        wa_id: str,
        source: str,
        model_name: str | None,
        photo_ref: str | None,
        raw_output: str | None,
        crop: str | None,
        diagnosis_name: str | None,
        category: str | None,
        confidence: str | None,
        flags: list[str],
        reply_text_en: str | None,
        confirmed_diagnosis: str | None = None,
    ) -> None:
        """Insert one diagnosis-log row (only reached for consented users)."""

        async def operation(session: AsyncSession) -> None:
            row = DiagnosisLogRow(
                case_id=case_id,
                wa_id=wa_id,
                source=source,
                model_name=model_name,
                photo_ref=photo_ref,
                raw_output=raw_output,
                crop=crop,
                diagnosis_name=diagnosis_name,
                category=category,
                confidence=confidence,
                flags=list(flags),
                reply_text_en=reply_text_en,
                confirmed_diagnosis=confirmed_diagnosis,
            )
            session.add(row)
            await session.commit()

        await self._execute(operation)

    async def close(self) -> None:
        """Dispose of the engine and its pool."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None

    async def _execute(
        self, operation: Callable[[AsyncSession], Awaitable[T]]
    ) -> T:
        """Run one database operation with a timeout and a single retry."""
        if self._session_factory is None:
            raise StorageUnavailableError("database is not connected")

        last_error: Exception = StorageUnavailableError("unknown database error")
        for attempt in range(2):  # one attempt plus one retry
            try:
                async with self._session_factory() as session:
                    return await asyncio.wait_for(
                        operation(session), QUERY_TIMEOUT_SECONDS
                    )
            except (SQLAlchemyError, asyncio.TimeoutError, OSError) as error:
                last_error = error
                logger.warning(
                    "Database call failed (attempt %d/2): %s", attempt + 1, error
                )
                if attempt == 0:
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
        raise StorageUnavailableError(str(last_error)) from last_error

    async def get_user(self, phone: str) -> Optional[User]:
        """Return the user, or ``None`` if they do not exist yet."""

        async def operation(session: AsyncSession) -> Optional[User]:
            result = await session.execute(
                select(UserRow).where(UserRow.wa_id == phone)
            )
            row = result.scalar_one_or_none()
            return _row_to_user(row) if row is not None else None

        return await self._execute(operation)

    async def save_user(self, user: User) -> None:
        """Insert the user, or update the existing row."""

        async def operation(session: AsyncSession) -> None:
            insert_values = {
                "wa_id": user.phone,
                "name": user.name,
                "preferred_name": user.preferred_name,
                "state": user.state,
                "language": user.language,
                "language_source": user.language_source,
                "language_attempts": user.language_attempts,
                "role": user.role,
                "onboarding": user.onboarding,
                "consent_status": user.consent_status,
                "consent_at": user.consent_at,
                "consent_version": user.consent_version,
                "flow": user.flow, 
                "step": user.step,
                "current_case_id": user.current_case_id, 
                "case_started_at": user.case_started_at, 
                "last_activity_at": user.last_activity_at, 
                "case_crop": user.case_crop, 
                "case_part": user.case_part, 
                "case_symptoms": user.case_symptoms, 
                "questions_asked": user.questions_asked, 
                "diagnoses_in_case": user.diagnoses_in_case,
                "flow_data": user.flow_data or {},
                "farm_profile": user.farm_profile or {},
            }
            update_values = {
                key: value
                for key, value in insert_values.items()
                if key != "wa_id"
            }
            update_values["last_seen_at"] = func.now()

            statement = (
                pg_insert(UserRow)
                .values(**insert_values)
                .on_conflict_do_update(
                    index_elements=[UserRow.wa_id], set_=update_values
                )
            )
            await session.execute(statement)
            await session.commit()

        await self._execute(operation)

    async def mark_seen(self, message_id: str, wa_id: str | None = None) -> bool:
        """Record an inbound WhatsApp message id and report whether it is new.

        Returns ``True`` when this is the first time the id is seen and
        ``False`` for a duplicate. The primary key on ``message_id`` plus
        ``ON CONFLICT DO NOTHING`` is what makes this safe when two deliveries
        of the same id arrive at the same instant: the database decides, not
        Python. Raises :class:`StorageUnavailableError` when the database is
        unreachable; the caller decides what a failure means.
        """
        if not message_id:
            return True

        async def operation(session: AsyncSession) -> bool:
            statement = (
                pg_insert(ProcessedMessageRow)
                .values(message_id=message_id, wa_id=wa_id)
                .on_conflict_do_nothing(index_elements=["message_id"])
                .returning(ProcessedMessageRow.message_id)
            )
            result = await session.execute(statement)
            await session.commit()
            # A returned row means we inserted it: this id is new.
            return result.scalar_one_or_none() is not None

        return await self._execute(operation)

    async def purge_old_processed_messages(self) -> None:
        """Delete dedup rows older than the retention window. Never raises.

        Called once at startup and then once a day (see ``app.main``). A
        cleanup failure is logged and swallowed so it can never stop the app
        from starting or kill the daily sweep.
        """
        try:

            async def operation(session: AsyncSession) -> None:
                await session.execute(
                    text(
                        "DELETE FROM processed_messages "
                        "WHERE received_at < now() - interval "
                        f"'{PROCESSED_MESSAGE_RETENTION_DAYS} days'"
                    )
                )
                await session.commit()

            await self._execute(operation)
        except Exception:  # noqa: BLE001 - cleanup must never break the app
            logger.exception("Could not purge old processed_messages rows")

    async def message_seen(self, wa_message_id: str) -> bool:
        """Return ``True`` when a WhatsApp message id has already been stored.

        This is the durable dedupe check that survives restarts (VOICE_NOTE.md
        section 4, rule 1). The in-memory dedupe in ``onboarding_send`` is still
        the first line of defence inside a single process; this is the backup for
        retries that arrive after a restart or on a different worker.
        """
        if not wa_message_id:
            return False

        async def operation(session: AsyncSession) -> bool:
            result = await session.execute(
                select(MessageRow.id).where(MessageRow.wa_message_id == wa_message_id)
            )
            return result.scalar_one_or_none() is not None

        return await self._execute(operation)

    async def save_message(self, message: ChatMessage) -> None:
        """Insert one chat-history row.

        The caller only reaches here for a consented user, so consent is
        checked before this point. A duplicate ``wa_message_id`` (a webhook
        retry that slipped past dedupe) is ignored rather than raised.
        """

        async def operation(session: AsyncSession) -> None:
            row = MessageRow(
                wa_id=message.wa_id,
                wa_message_id=message.wa_message_id,
                direction=message.direction,
                msg_type=message.msg_type,
                text=message.text,
                language=message.language,
                intent=message.intent,
                media_ref=message.media_ref,
                case_id=message.case_id,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                logger.info("Duplicate message ignored (dedupe)")

        await self._execute(operation)

    async def get_state(self, phone: str) -> str | None:
        """Return the user's current onboarding state."""
        user = await self.get_user(phone)
        return user.state if user else None

    async def get_role(self, phone: str) -> str | None:
        """Return the user's role."""
        user = await self.get_user(phone)
        return user.role if user else None

    async def set_user_last_activity(self, phone: str) -> None:
        """Update ``last_seen_at`` and, when a case is open, ``last_activity_at``."""
        user = await self.get_user(phone)
        if user is None:
            return
        user.last_seen_at = datetime.now(timezone.utc)
        if user.current_case_id is not None:
            user.last_activity_at = datetime.now(timezone.utc)
        await self.save_user(user)

    async def get_case(self, phone: str) -> OpenCase:
        """Return the current crop-diagnosis case state for a user, or an empty one."""
        user = await self.get_user(phone)
        if user is None:
            return OpenCase.empty()
        return OpenCase(
            flow=user.flow,
            step=user.step,
            current_case_id=user.current_case_id,
            case_started_at=user.case_started_at,
            last_activity_at=user.last_activity_at,
            case_crop=user.case_crop,
            case_part=user.case_part,
            case_symptoms=list(user.case_symptoms or []),
            questions_asked=user.questions_asked,
            diagnoses_in_case=user.diagnoses_in_case,
        )

    async def set_case(self, phone: str, case: OpenCase) -> None:
        """Persist the open-case fields back onto the user row."""
        user = await self.get_user(phone)
        if user is None:
            return
        user.flow = case.flow
        user.step = case.step
        user.current_case_id = case.current_case_id
        user.case_started_at = case.case_started_at
        user.last_activity_at = case.last_activity_at
        user.case_crop = case.case_crop
        user.case_part = case.case_part
        user.case_symptoms = list(case.case_symptoms)
        user.questions_asked = case.questions_asked
        user.diagnoses_in_case = case.diagnoses_in_case
        await self.save_user(user)

    async def close_case(self, phone: str) -> None:
        """Clear all open-case fields on the user row."""
        user = await self.get_user(phone)
        if user is None:
            return
        user.flow = None
        user.step = None
        user.current_case_id = None
        user.case_started_at = None
        user.last_activity_at = None
        user.case_crop = None
        user.case_part = None
        user.case_symptoms = []
        user.questions_asked = 0
        user.diagnoses_in_case = 0
        user.flow_data = {}
        await self.save_user(user)


    # ------------------------------------------------------------------
    # Trader ledger (consented users only; the trade flow checks consent)
    # ------------------------------------------------------------------

    async def trade_products(self, wa_id: str) -> list["Product"]:
        """The trader's catalogue, by name."""

        async def operation(session: AsyncSession) -> list[Product]:
            result = await session.execute(
                select(TradeProductRow)
                .where(TradeProductRow.wa_id == wa_id)
                .order_by(TradeProductRow.name)
            )
            return [_row_to_product(row) for row in result.scalars()]

        return await self._execute(operation)

    async def trade_apply(self, wa_id: str, product: "Product", entry: "Entry") -> "Entry":
        """Save the product's new state and its ledger line in one transaction.

        A new product (``product.id is None``) is inserted, or merged into an
        existing row with the same normalised name.
        """
        from app.trade.parse import product_key

        async def operation(session: AsyncSession) -> Entry:
            key = product_key(product.name)
            row = None
            if product.id is not None:
                row = await session.get(TradeProductRow, product.id, with_for_update=True)
                if row is not None and row.wa_id != wa_id:
                    row = None
            if row is None:
                result = await session.execute(
                    select(TradeProductRow)
                    .where(TradeProductRow.wa_id == wa_id, TradeProductRow.name_key == key)
                    .with_for_update()
                )
                row = result.scalar_one_or_none()
                if row is not None and product.id is None:
                    # Worked out as a new item, but the item exists: saving
                    # would overwrite its stock and prices. Refuse instead.
                    raise ValueError(f"item {product.name!r} already exists")
            if row is None:
                row = TradeProductRow(wa_id=wa_id, name=product.name, name_key=key)
                session.add(row)
            row.unit = product.unit
            row.cost_price = product.cost_price
            row.set_price = product.set_price
            row.stock_qty = product.stock_qty
            row.low_stock_level = product.low_stock_level
            row.updated_at = datetime.now(timezone.utc)
            await session.flush()
            created = datetime.now(timezone.utc)
            line = TradeEntryRow(
                created_at=created,
                wa_id=wa_id,
                product_id=row.id,
                product_name=entry.product_name,
                unit=entry.unit,
                kind=entry.kind,
                qty=entry.qty,
                unit_price=entry.unit_price,
                unit_cost=entry.unit_cost,
                set_price=entry.set_price,
                amount=entry.amount,
                profit=entry.profit,
                price_difference=entry.price_difference,
                stock_after=entry.stock_after,
                source=entry.source,
            )
            session.add(line)
            await session.commit()
            entry.product_id = row.id
            entry.created_at = created
            return entry

        return await self._execute(operation)

    async def trade_entries(
        self, wa_id: str, start: datetime | None = None, end: datetime | None = None
    ) -> list["Entry"]:
        """Ledger lines between ``start`` and ``end`` (UTC), oldest first."""

        async def operation(session: AsyncSession) -> list[Entry]:
            query = select(TradeEntryRow).where(TradeEntryRow.wa_id == wa_id)
            if start is not None:
                query = query.where(TradeEntryRow.created_at >= start)
            if end is not None:
                query = query.where(TradeEntryRow.created_at < end)
            result = await session.execute(query.order_by(TradeEntryRow.created_at, TradeEntryRow.id))
            return [_row_to_entry(row) for row in result.scalars()]

        return await self._execute(operation)


storage = PostgresStorage()
