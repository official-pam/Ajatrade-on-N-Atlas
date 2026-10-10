"""Entrypoint: ``uvicorn app.main:app --reload --host 0.0.0.0 --port 8000``."""
# \c ajatrade
# DROP TABLE IF EXISTS users CASCADE;
# netstat -ano | findstr :8000

from app.main import app

__all__ = ["app"]
