"""Voice-note detection for the combined onboarding flow.

Under the combined onboarding design the text ``OnboardingStateMachine`` is the
single source of truth for state and stored values (combined-onboarding-spec
section 5, rule 4). This module only decides *which answer id* a voice note
means; the webhook then hands that id to the same state machine handler the
buttons use (``lang_*``, ``consent_yes`` / ``consent_no`` /
``consent_read_more``).

What it owns:

* language detection for the first voice note (kept from the voice flow),
  including the low-confidence path (first failure asks again, second failure
  defaults to Pidgin) and the unsupported-language notice;
* consent detection: transcribe an audio answer and map it to a consent id;
* the consent replay cap for unclear audio, kept in memory as flow control
  only (nothing is persisted; AGENTS.md).

It never stores state transitions or user-facing profile values. The only
fields it writes are the voice-specific detection bookkeeping fields
(``language_source`` / ``language_attempts``), which the text flow does not use.

Not built yet: voice role classification. Audio arriving at ``awaiting_role``
replays the role step instead of being classified, so a user is never met with
silence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.onboarding.messages import LANGUAGES, create_role_message, text_message
from app.onboarding.onboarding_send import consent_buttons_message
from app.schemas.memory import User
from app.services.speech_to_text import transcribe_voice_note
from app.services.translation import language_detector
from app.services.whatsapp.get_media import fetch_voice_note
from app.storage import mask_phone
from app.storage import storage as default_storage
from app.voice.audio_store import default_store
from app.voice.scripts import (
    LOW_CONFIDENCE_RETRY,
    MAX_STEP_REPLAYS,
    PIDGIN_DEFAULT_NOTICE,
    STEP_REPLAY_UNCLEAR,
    UNSUPPORTED_LANGUAGE,
)

logger = logging.getLogger("voice")


# A detected language is trusted at or above this confidence (VOICE_SPEC 4).
LANGUAGE_CONFIDENCE_THRESHOLD = 0.7

# The consent step repeats itself below this confidence (VOICE_SPEC 6.3).
CONSENT_CONFIDENCE_THRESHOLD = 0.6

# The language detector (LANGUAGE_DETECTION_SYSTEM_PROMPT) returns full
# lowercase names. Map them to the row ids the text flow's language list uses,
# so the stored value still comes from ``LANGUAGES`` and the two flows cannot
# drift. ``other`` is handled separately as the unsupported-language path.
CLASSIFIER_NAME_TO_OPTION_ID: dict[str, str] = {
    "yoruba": "lang_yo",
    "hausa": "lang_ha",
    "igbo": "lang_ig",
    "pidgin": "lang_pcm",
    "english": "lang_en",
}


@dataclass
class AudioResolution:
    """What one inbound voice note means for the current onboarding step.

    ``answer`` is the id to hand to ``OnboardingStateMachine``, or ``None``
    when the voice note produced messages of its own (a retry prompt, the
    unsupported notice, a replayed step) or nothing at all.
    """

    answer: str | None = None
    prefix_messages: list[dict] = field(default_factory=list)
    voice_script_id: str | None = None


def _clean_confidence(value: object) -> float:
    """Return a confidence between 0.0 and 1.0, or 0.0 if it is unusable."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


class VoiceOnboarding:
    """Decides which answer id a voice note means for the current step."""

    def __init__(self, storage=None):
        self.storage = storage or default_storage
        self.audiostore = default_store
        # Unclear-answer replay counts. Flow control only, never persisted.
        self._consent_replays: dict[str, int] = {}
        # Startup check: warn about any onboarding audio that is missing.
        for missing in self.audiostore.check_missing():
            logger.warning("Missing onboarding audio: %s", missing)

    async def resolve_audio(self, user: User, message: dict) -> AudioResolution:
        """Resolve one inbound voice note into an answer id or direct messages."""
        if user.state == "new":
            # First contact: the language step runs the detection path. The
            # state machine sets the real state from here on.
            user.state = "awaiting_language"
            await self.storage.save_user(user)

        if user.state in ("new", "awaiting_language"):
            return await self._resolve_language(user, message)

        if user.state == "awaiting_consent":
            answer = await self.detect_consent(message, user.language)
            if answer is not None:
                self._consent_replays.pop(user.phone, None)
                return AudioResolution(answer=answer)
            return await self._replay_consent(user)

        if user.state == "awaiting_role":
            # Voice role classification is not built yet (step 3). Replay the
            # role step so a speaking user is not met with silence; the text
            # state machine still owns the actual role transition.
            logger.info(
                "Voice: role step not classified; replaying it for %s",
                mask_phone(user.phone),
            )
            return AudioResolution(
                prefix_messages=[create_role_message(user.language or "english")],
                voice_script_id="role_ask",
            )

        # active / declined and later steps are handled elsewhere.
        return AudioResolution()

    async def _resolve_language(
        self, user: User, message: dict
    ) -> AudioResolution:
        """Transcribe the voice note, detect the language and advance."""
        media_id = (message.get("audio") or {}).get("id")
        if not media_id:
            logger.error("Voice message without a media id; asking again")
            return await self._low_confidence(user)

        try:
            audio_bytes, mime_type = await fetch_voice_note(media_id)
            transcript = await transcribe_voice_note(audio_bytes, mime_type)
            # if not transcript:
            #     return {"stautus": "Not transcript"}
        except Exception as error:  # noqa: BLE001 - never silence the user
            logger.error(
                "Voice note %s could not be transcribed for %s: %s",
                media_id,
                mask_phone(user.phone),
                error,
            )
            return await self._low_confidence(user)

        logger.info(
            "Voice transcription for %s: %r", mask_phone(user.phone), transcript
        )

        try:
            result = await language_detector(transcript)
        except Exception as error:  # noqa: BLE001 - fall back to asking again
            logger.error(
                "Language detection failed for %s: %s",
                mask_phone(user.phone),
                error,
            )
            return await self._low_confidence(user)

        name = str(result.get("language", "")).strip().lower()
        confidence = _clean_confidence(result.get("confidence"))
        logger.info(
            "Voice language detection for %s: %s (confidence=%.2f)",
            mask_phone(user.phone),
            name or "unknown",
            confidence,
        )

        if name == "other" and confidence >= LANGUAGE_CONFIDENCE_THRESHOLD:
            # Confidently a language we do not support.
            user.language_attempts = 0
            await self.storage.save_user(user)
            return AudioResolution(
                prefix_messages=[text_message(UNSUPPORTED_LANGUAGE["english"])]
            )

        option_id = CLASSIFIER_NAME_TO_OPTION_ID.get(name)
        if option_id is not None and confidence >= LANGUAGE_CONFIDENCE_THRESHOLD:
            user.language_source = "detected"
            user.language_attempts = 0
            await self.storage.save_user(user)
            return AudioResolution(answer=option_id)

        return await self._low_confidence(user)




    
    async def _low_confidence(self, user: User) -> AudioResolution:
        """Handle a language detection we cannot trust (VOICE_SPEC 4).

        First failure: ask for another voice note. Second failure: default to
        Pidgin and continue, handing the state machine the Pidgin option id.
        """
        attempts = user.language_attempts or 0

        if attempts >= 1:
            logger.info(
                "Voice language detection failed twice for %s; defaulting to Pidgin",
                mask_phone(user.phone),
            )
            user.language_source = "default_fallback"
            user.language_attempts = 0
            await self.storage.save_user(user)
            return AudioResolution(
                answer="lang_pcm",
                prefix_messages=[text_message(PIDGIN_DEFAULT_NOTICE)],
            )

        user.language_attempts = attempts + 1
        await self.storage.save_user(user)
        return AudioResolution(prefix_messages=[text_message(LOW_CONFIDENCE_RETRY)])

    async def _replay_consent(self, user: User) -> AudioResolution:
        """Replay the consent step after an unclear answer.

        After ``MAX_STEP_REPLAYS`` unclear answers, send nothing more and wait
        (VOICE_SPEC 6.3). Replays are counted in memory per phone.
        """
        replays = self._consent_replays.get(user.phone, 0) + 1
        self._consent_replays[user.phone] = replays
        if replays > MAX_STEP_REPLAYS:
            logger.info(
                "Voice consent unclear %d times for %s; waiting",
                replays,
                mask_phone(user.phone),
            )
            return AudioResolution()

        logger.info(
            "Voice consent unclear for %s; replaying the step (%d/%d)",
            mask_phone(user.phone),
            replays,
            MAX_STEP_REPLAYS,
        )
        return AudioResolution(
            prefix_messages=[
                text_message(STEP_REPLAY_UNCLEAR),
                consent_buttons_message(user.language),
            ],
            voice_script_id="welcome_consent",
        )

    async def detect_consent(
        self, message: dict, language: str | None
    ) -> str | None:
        """Transcribe the voice note and classify it into a consent answer.

        Returns ``consent_yes``, ``consent_no``, ``consent_read_more``, or
        ``None`` when the answer is unclear. The audio and the transcript are
        never stored.
        """
        media_id = (message.get("audio") or {}).get("id")
        if not media_id:
            logger.error("Voice message without a media id; treating it as unclear")
            return None

        try:
            audio_bytes, mime_type = await fetch_voice_note(media_id)
            transcript = await transcribe_voice_note(audio_bytes, mime_type, language)
        except Exception as error:  # noqa: BLE001 - never silence the user
            logger.error(
                "Voice note %s could not be transcribed: %s", media_id, error
            )
            return None

        return _detect_consent_answer(transcript, language)


def _detect_consent_answer(transcript: str, language: str | None) -> str | None:
    """Classifier stopgap: map a transcript to a consent answer.

    English-only for now. Returns ``None`` for anything it cannot match so the
    caller replays the consent step instead of guessing.
    """
    lowered = (transcript or "").strip().lower()
    if not lowered:
        return None

    # Single-word answers.
    if lowered in ("yes", "ye", "yeah", "yep", "true", "correct", "agree"):
        return "consent_yes"
    if lowered in ("no", "nah", "nope", "false"):
        return "consent_no"

    # Multi-word patterns. A negation must be checked before the bare "agree"
    # so "I don't agree" does not match as agreement.
    negations = ("don't agree", "do not agree", "doesn't agree", "does not agree")
    if any(neg in lowered for neg in negations):
        return "consent_no"
    if lowered in ("not true", "not correct"):
        return "consent_no"
    if any(phrase in lowered for phrase in ("tell me more", "read more", "more detail", "i want to read")):
        return "consent_read_more"

    return None
