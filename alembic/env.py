"""Alembic environment.

Reads the connection string from ``DATABASE_URL`` (never committed) and runs
migrations against PostgreSQL with the asyncpg driver.
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.storage.models import Base

# Load .env from the project root so local runs pick up DATABASE_URL.
load_dotenv()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    """Return the SQLAlchemy async URL for DATABASE_URL."""
    url = os.getenv("DATABASE_URL", "").strip()
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


_database_url_value = _database_url()
if _database_url_value:
    config.set_main_option("sqlalchemy.url", _database_url_value)

# The SQLAlchemy models are the source of truth for the schema. Migrations
# stay additive only; use ``alembic revision --autogenerate`` to draft new ones.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a DB connection (emit SQL)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live database."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
