"""Application configuration loaded from the environment."""

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()  # reads .env from the project root


@dataclass(frozen=True)
class Settings:
    """Runtime settings sourced from environment variables."""

    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""  # from the Meta app dashboard
    whatsapp_api_version: str = "v21.0"


@lru_cache
def get_settings() -> Settings:
    """Return cached settings, suitable for use as a FastAPI dependency."""
    return Settings(
        whatsapp_verify_token=os.getenv("WHATSAPP_VERIFY_TOKEN", ""),
        whatsapp_app_secret=os.getenv("WHATSAPP_APP_SECRET", ""),
        whatsapp_api_version=os.getenv("WHATSAPP_API_VERSION", "v21.0"),
    )
