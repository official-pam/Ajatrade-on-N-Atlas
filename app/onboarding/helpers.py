from __future__ import annotations

import logging
from typing import Any

from app.onboarding.messages import LANGUAGES

logger = logging.getLogger(__name__)


def language_name(code: str) -> str:
    """Return a human-friendly language name for a stored language code."""
    return LANGUAGES.get(code, code)


def language_label(user: User) -> str:
    """Return a short language label for a user."""
    if user.language is None:
        return "unknown"
    return language_name(user.language)


def role_label(user: User) -> str:
    """Return a short role label for a user."""
    return getattr(user, "role", None) or "unknown"


def consent_label(user: User) -> str:
    """Return a short consent label for a user."""
    return getattr(user, "consent", None) or "pending"


def state_label(user: User) -> str:
    """Return the onboarding state for a user."""
    return getattr(user, "state", None) or "unknown"


def is_onboarding_complete(user: User) -> bool:
    """Return True when a user has completed onboarding."""
    return (
        user.language is not None
        and user.role is not None
        and user.consent in {"granted", "declined"}
    )


def user_summary(user: User) -> dict[str, Any]:
    """Return a summary of a user for logging and debugging."""
    return {
        "phone": mask_phone(user.phone),
        "name": user.name,
        "language": language_label(user),
        "role": role_label(user),
        "consent": consent_label(user),
        "state": state_label(user),
        "onboarding_complete": is_onboarding_complete(user),
    }


logger = logging.getLogger(__name__)
