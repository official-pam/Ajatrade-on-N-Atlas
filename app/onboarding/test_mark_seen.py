"""Tests for durable webhook dedup (``mark_seen``).

The guarantee comes from the primary key on ``processed_messages`` plus
``ON CONFLICT DO NOTHING``, so the real behaviour needs the local Postgres
(``DATABASE_URL`` from ``.env``). Those tests write rows with fake
``wamid_test_...`` ids and delete them again, and are skipped when
``DATABASE_URL`` is not set.

The failure cases stub the storage call instead, so they never need a broken
database. No network access beyond the local database.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Awaitable, TypeVar

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete, select

# The app reads .env from the project root. This has to run before the app
# imports below: PostgresStorage reads DATABASE_URL once, at the moment
# app.storage is first imported. Resolved from this file so it works whatever
# directory pytest was started from.
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

from app.onboarding.onboarding_send import mark_seen  # noqa: E402
from app.storage import StorageUnavailableError, storage  # noqa: E402
from app.storage.models import ProcessedMessageRow  # noqa: E402
from app.storage.postgres import to_async_url  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL is not set; dedup tests need the local Postgres",
)

T = TypeVar("T")

# A number nobody owns: no real user is ever involved in these tests.
TEST_WA_ID = "2348000000000"


def _new_id() -> str:
    """Return a fake WhatsApp message id no other test can collide with."""
    return f"wamid_test_{uuid.uuid4().hex}"


def _run(coro: Awaitable[T]) -> T:
    """Run ``coro`` against the local database inside one event loop."""

    async def runner() -> T:
        if not storage._dsn:
            # Another module can import app.storage before this file's
            # load_dotenv has run (pytest collection order). Give the
            # singleton the DSN the app itself would have used.
            load_dotenv(ROOT / ".env")
            storage._dsn = to_async_url(os.getenv("DATABASE_URL", ""))
        await storage.connect()
        try:
            return await coro
        finally:
            await storage.close()

    return asyncio.run(runner())


async def _fetch(message_id: str) -> ProcessedMessageRow | None:
    """Return the stored dedup row for ``message_id``, or ``None``."""
    factory = storage._session_factory
    assert factory is not None, "storage is not connected"
    async with factory() as session:
        result = await session.execute(
            select(ProcessedMessageRow).where(
                ProcessedMessageRow.message_id == message_id
            )
        )
        return result.scalar_one_or_none()


async def _delete(message_id: str) -> None:
    """Remove one test row so the table stays clean."""
    factory = storage._session_factory
    assert factory is not None, "storage is not connected"
    async with factory() as session:
        await session.execute(
            delete(ProcessedMessageRow).where(
                ProcessedMessageRow.message_id == message_id
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# mark_seen
# ---------------------------------------------------------------------------


def test_first_call_is_new_second_is_duplicate() -> None:
    async def scenario() -> None:
        message_id = _new_id()
        try:
            assert await mark_seen(message_id, TEST_WA_ID) is True
            assert await mark_seen(message_id, TEST_WA_ID) is False
            row = await _fetch(message_id)
            assert row is not None
            # Stored unmasked, in the same format the users table uses.
            assert row.wa_id == TEST_WA_ID
        finally:
            await _delete(message_id)

    _run(scenario())


def test_concurrent_duplicates_let_exactly_one_through() -> None:
    async def scenario() -> None:
        message_id = _new_id()
        try:
            first, second = await asyncio.gather(
                mark_seen(message_id, TEST_WA_ID),
                mark_seen(message_id, TEST_WA_ID),
            )
            results = [first, second]
            assert results.count(True) == 1, f"expected one winner, got {results}"
            assert results.count(False) == 1, f"expected one loser, got {results}"
        finally:
            await _delete(message_id)

    _run(scenario())


def test_empty_id_is_first_time_and_writes_nothing(monkeypatch) -> None:
    async def unexpected(*args: object, **kwargs: object) -> bool:
        raise AssertionError("an empty message id must not touch the database")

    monkeypatch.setattr(storage, "mark_seen", unexpected)
    # "" and a missing id (the webhook's ``message.get("id")``) are both new.
    assert asyncio.run(mark_seen("")) is True
    assert asyncio.run(mark_seen(None)) is True


def test_database_failure_is_processed_anyway(monkeypatch) -> None:
    async def explode(*args: object, **kwargs: object) -> bool:
        raise StorageUnavailableError("database is unreachable")

    monkeypatch.setattr(storage, "mark_seen", explode)
    # No raise, and True so the webhook still processes the message.
    assert asyncio.run(mark_seen("wamid_test_db_down", TEST_WA_ID)) is True
