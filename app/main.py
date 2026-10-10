"""Application factory and FastAPI instance."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.router import api_router
from app.storage import storage

logging.basicConfig(level=logging.INFO)

# How often old webhook dedup rows are swept (processed_messages).
PURGE_INTERVAL_SECONDS = 24 * 60 * 60


async def _purge_loop() -> None:
    """Delete old webhook dedup rows once a day until the app shuts down."""
    while True:
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)
        await storage.purge_old_processed_messages()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Open the storage pool on startup and close it on shutdown."""
    await storage.connect()
    # Retention sweep for processed_messages: once at startup, then daily.
    # purge_old_processed_messages logs and swallows its own failures, so
    # neither call can stop the app from starting.
    await storage.purge_old_processed_messages()
    purge_task = asyncio.create_task(_purge_loop())
    try:
        yield
    finally:
        purge_task.cancel()
        with suppress(asyncio.CancelledError):
            await purge_task
        await storage.close()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    application = FastAPI(
        title="WhatsApp Webhook",
        version="0.1.0",
        description="Receives and verifies WhatsApp Cloud API webhook events.",
        lifespan=lifespan,
    )
    application.include_router(api_router)
    return application


app = create_app()
