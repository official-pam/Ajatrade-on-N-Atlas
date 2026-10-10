import spitch
from spitch import AsyncSpitch

# One shared client for the whole app. Reads SPITCH_API_KEY from the environment.
spitch_client = AsyncSpitch()

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

async def text_to_speech(text: str, language: str) -> bytes | None:
    """Returns audio bytes, or None if we can't voice this reply."""
    config = VOICES.get(language)
    if not config or not text.strip():
        return None   # caller should send the reply as text instead

    lang_code, voice = config
    try:
        response = await spitch_client.with_options(
            timeout=20.0, max_retries=2
        ).speech.generate(
            text=text,
            language=lang_code,
            voice=voice,
            format="mp3", 
        )
        return await response.read()
    except spitch.APIConnectionError as e:
        print(f"Spitch connection error: {e}")
    except Exception as e:
        print(f"Spitch TTS error: {e}")
    return None