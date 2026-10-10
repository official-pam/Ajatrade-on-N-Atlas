"""Top-level router that aggregates all route modules."""

from fastapi import APIRouter

from app.api.routes import webhook

api_router = APIRouter()
api_router.include_router(webhook.router, tags=["webhook"])
