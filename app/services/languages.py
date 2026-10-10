"""One spelling for each language across the app.

Onboarding stores Pidgin as ``"nigerian pidgin"`` while translation, TTS and
the menu strings use ``"pidgin"``; the mismatch made Pidgin users fall back to
English. Everything that maps a stored language goes through
:func:`normalize_language`.
"""

from __future__ import annotations

SUPPORTED = ("english", "yoruba", "hausa", "igbo", "pidgin")

_ALIASES = {
    "en": "english",
    "eng": "english",
    "yo": "yoruba",
    "yor": "yoruba",
    "ha": "hausa",
    "hau": "hausa",
    "ig": "igbo",
    "ibo": "igbo",
    "pcm": "pidgin",
    "nigerian pidgin": "pidgin",
    "naija": "pidgin",
    "pidgin english": "pidgin",
}


def normalize_language(language: str | None, default: str = "english") -> str:
    """Return one of SUPPORTED, falling back to ``default``."""
    value = (language or "").strip().lower()
    value = _ALIASES.get(value, value)
    return value if value in SUPPORTED else default
