"""
Speech-to-text transcription stage.

Takes the raw voice-note bytes fetched by app/services/whatsapp/get_media.py
(fetch_voice_note) and turns them into text with the N-ATLAS speech
recognition models (NCAIR1 Yoruba / Hausa / Igbo / Nigerian-accented English
ASR), chosen by the user's stored language.

ElevenLabs Scribe is kept only as an opt-in emergency fallback
(``STT_FALLBACK=elevenlabs``). It is off by default because the NAIC
voice-first track requires the N-ATLAS ASR.
"""

from __future__ import annotations

import io
import logging
import os

from dotenv import load_dotenv

from app.services import natlas

load_dotenv()

logger = logging.getLogger("speech_to_text")


async def transcribe_voice_note(
    audio_bytes: bytes,
    mime_type: str = "audio/ogg",
    language: str | None = None,
) -> str:
    """Transcribe an inbound WhatsApp voice note to text.

    Args:
        audio_bytes: Raw audio payload (typically OGG/Opus from WhatsApp).
        mime_type: MIME type reported by WhatsApp's media API.
        language: the user's stored language, which picks the ASR model.
            ``None`` (first voice note, language unknown) lets the N-ATLAS
            server try every ASR model and keep the most confident one.

    Returns:
        The transcribed text.
    """
    transcript = await transcribe_voice_note_detailed(audio_bytes, mime_type, language)
    return transcript.text


async def transcribe_voice_note_detailed(
    audio_bytes: bytes,
    mime_type: str = "audio/ogg",
    language: str | None = None,
) -> natlas.Transcript:
    """Like :func:`transcribe_voice_note` but also returns the ASR language."""
    try:
        transcript = await natlas.transcribe(audio_bytes, mime_type, language)
        logger.info("N-ATLAS ASR (%s) transcribed %d chars", transcript.model, len(transcript.text))
        return transcript
    except Exception as error:
        if os.getenv("STT_FALLBACK", "").strip().lower() != "elevenlabs":
            raise
        logger.warning("N-ATLAS ASR failed (%s); using the ElevenLabs fallback", error)
        text = await _elevenlabs_transcribe(audio_bytes, mime_type)
        return natlas.Transcript(text=text, language=None, model="elevenlabs/scribe_v2")


async def _elevenlabs_transcribe(audio_bytes: bytes, mime_type: str) -> str:
    from elevenlabs.client import AsyncElevenLabs

    client = AsyncElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
    extension = mime_type.rsplit("/", 1)[-1].split(";")[0] or "ogg"
    file_tuple = (f"audio.{extension}", io.BytesIO(audio_bytes), mime_type)
    transcription = await client.speech_to_text.convert(file=file_tuple, model_id="scribe_v2")
    text = getattr(transcription, "text", None)
    if text is None and hasattr(transcription, "transcripts"):
        text = " ".join(t.text for t in transcription.transcripts)
    return (text or "").strip()
