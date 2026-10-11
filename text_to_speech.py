import logging
import os

import spitch
from spitch import AsyncSpitch

logger = logging.getLogger("text_to_speech")

# One shared client for the whole app, created on first use. Reads
# SPITCH_API_KEY from the environment. Without a key the bot still runs and
# replies in text instead of voice (creating the client at import time used to
# crash the whole app when the key was missing).
_spitch_client: AsyncSpitch | None = None

# Map your detected language -> (Spitch language code, voice).
# Only "yo"/"sade" and "en"/"lina" appear in Spitch's own docs examples.
# Fill in the rest from the docs voice list; leave as None if unsure.
VOICES = {
    "yoruba":  ("yo",  "sade"),
    "english": ("en",  "lina"),
    "hausa":   ("ha",  "amina"),
    "igbo":    ("ig",  "ngozi"),
    "pidgin":  ("ng", "boma"),  # REVIEW: confirm 'ng' is the right Spitch code for Nigerian Pidgin
}


def _client() -> AsyncSpitch | None:
    global _spitch_client
    if _spitch_client is None:
        if not os.getenv("SPITCH_API_KEY", "").strip():
            return None
        _spitch_client = AsyncSpitch()
    return _spitch_client


async def text_to_speech(text: str, language: str) -> bytes | None:
    """Returns audio bytes, or None if we can't voice this reply."""
    from app.services.languages import normalize_language

    config = VOICES.get(normalize_language(language))
    client = _client()
    if not config or not text.strip() or client is None:
        return None   # caller should send the reply as text instead

    lang_code, voice = config
    try:
        response = await client.with_options(
            timeout=20.0, max_retries=2
        ).speech.generate(
            text=text,
            language=lang_code,
            voice=voice,
            format="mp3",
        )
        return await response.read()
    except spitch.APIConnectionError as e:
        logger.warning("Spitch connection error: %s", e)
    except Exception as e:
        logger.warning("Spitch TTS error: %s", e)
    return None
