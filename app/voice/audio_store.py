"""Read pre-generated onboarding audio files and send them via WhatsApp.

Provides two helpers:

* ``get_audio_file(script_id, language)`` — returns the path to the mp3 for
  a script and language, falling back to English when the exact language is
  missing, and returning ``None`` when no file exists at all.
* ``send_voice_script(phone, script_id, language)`` — uploads the file to
  WhatsApp once, caches the returned media ID, sends the audio by media ID,
  and falls back to sending the script's text when anything fails.

The media ID cache is in memory only (an ``dict`` on the ``AudioStore``
instance). It is intended to be short-lived per process; the same expiry
logic applies on the next cold start.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

# Load environment variables before importing the WhatsApp send helper,
# which reads env vars at module level.
from dotenv import load_dotenv
from app.services.whatsapp.send_media import upload_media, send_uploaded_media, send_text

load_dotenv()

from app.services.whatsapp.send_media import send_text

logger = logging.getLogger("audio_store")

AUDIO_DIR = Path(__file__).resolve().parent / "audio"
MANIFEST_PATH = AUDIO_DIR / "manifest.json"

# WhatsApp media IDs expire after about 30 days. To be safe, treat any
# cached ID older than this as stale and re-upload before sending.
MEDIA_ID_TTL_SECONDS = 25 * 24 * 60 * 60  # 25 days

# How many times to try uploading a file before falling back to text.
MAX_UPLOAD_ATTEMPTS = 2

# Cap for the generate-on-miss path (TODO_new_voice_notes.md step 4): one TTS
# call is 20 s with two retries, so this covers it and still reaches the text
# fallback quickly enough that the user is never left waiting in silence.
GENERATE_TIMEOUT_SECONDS = 45.0

# Config flag for the onboarding audio format. ``mp3`` for now; switching
# to ``ogg`` later only needs a config change and a regeneration run.
ONBOARDING_AUDIO_FORMAT = os.environ.get("ONBOARDING_AUDIO_FORMAT", "mp3")

# MIME type for the upload, based on the configured format.
MEDIA_TYPE = "audio/mpeg" if ONBOARDING_AUDIO_FORMAT == "mp3" else "audio/ogg"


# The text onboarding stores the full language names from ``LANGUAGES`` (for
# example "nigerian pidgin"), while the audio assets are keyed by short names
# ("pidgin"). Map the stored value to the asset key so the right file is sent
# instead of silently falling back to English.
AUDIO_LANGUAGE_ALIASES: dict[str, str] = {
    "nigerian pidgin": "pidgin",
    "pcm": "pidgin",
}


def audio_language(language: str | None) -> str:
    """Return the audio asset key for a stored language value."""
    lang = (language or "").strip().lower()
    return AUDIO_LANGUAGE_ALIASES.get(lang, lang)


def _load_manifest() -> dict[str, Any]:
    """Return the parsed manifest, or an empty dict on any failure."""
    if not MANIFEST_PATH.exists():
        return {}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))  # type: ignore[attr-defined]
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read manifest: %s", exc)
        return {}


def _save_manifest(manifest: dict[str, Any]) -> None:
    """Write the manifest to disk."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")  # type: ignore[attr-defined]
    tmp.replace(MANIFEST_PATH)


def _dotenv_loaded() -> bool:
    """Best-effort check that the environment is populated for WhatsApp calls."""
    return bool(os.environ.get("WHATSAPP_ACCESS_TOKEN") and os.environ.get("WHATSAPP_PHONE_NUMBER_ID"))





class AudioStore:
    """Holds the manifest, audio files and the in-memory media ID cache."""

    def __init__(self) -> None:
        self._manifest: dict[str, Any] = _load_manifest()
        # media_id_cache[(script_id, language)] = (media_id, uploaded_at_timestamp)
        self._media_id_cache: dict[tuple[str, str], tuple[str, float]] = {}

    def refresh_manifest(self) -> None:
        """Re-read the manifest from disk. Call after a regeneration run."""
        self._manifest = _load_manifest()

    def get_audio_file(self, script_id: str, language: str) -> Path | None:
        """Return the mp3 path for *script_id* and *language*.

        Falls back to English when the exact language is missing. Returns
        ``None`` when the script, language, or file does not exist.
        """
        language = audio_language(language)
        entry = self._manifest.get(script_id, {}).get(language)
        if entry is None:
            # Fallback to English.
            entry = self._manifest.get(script_id, {}).get("english")
        if entry is None:
            return None
        fpath = AUDIO_DIR / entry["file"]
        if not fpath.exists():
            logger.warning(
                "Audio file missing for %s / %s: %s",
                script_id,
                language,
                fpath,
            )
            return None
        return fpath

    def get_script_text(self, script_id: str, language: str) -> str | None:
        """Return the source text for a script and language, or ``None``."""
        from app.voice.scripts import SCRIPTS

        language = audio_language(language)
        entry = SCRIPTS.get(script_id)
        if entry is None:
            return None
        if isinstance(entry, dict):
            return entry.get(language, entry.get("english", ""))
        return entry

    async def send_voice_script(
        self,
        phone: str,
        script_id: str,
        language: str,
    ) -> bool:
        """Send the pre-generated audio for *script_id* and *language*.

        Returns ``True`` when the audio was sent (or the text fallback was
        sent), ``False`` on unrecoverable failure.
        """
        fpath = self.get_audio_file(script_id, language)
        if fpath is None:
            # Cached file missing: log it, generate it once, save it and send
            # it (TODO_new_voice_notes.md step 4). On any failure we fall
            # through to the text fallback below.
            fpath = await self._generate_missing(script_id, language)
        if fpath is None:
            text = self.get_script_text(script_id, language)
            if text:
                logger.error(
                    "No audio file for %s / %s; sending text fallback to %s",
                    script_id,
                    language,
                    phone,
                )
                await send_text(phone, text)
                return True
            logger.error(
                "No audio file and no text for %s / %s; cannot reach %s",
                script_id,
                language,
                phone,
            )
            return False

        audio_bytes = fpath.read_bytes()
        if not audio_bytes:
            logger.error("Audio file is empty: %s", fpath)
            text = self.get_script_text(script_id, language)
            if text:
                await send_text(phone, text)
            return text is not None

        # Attempt to send, re-uploading when the cached media ID is stale
        # or the send fails with an invalid-media error.
        uploaded = False
        for attempt in range(1, MAX_UPLOAD_ATTEMPTS + 1):
            media_id = self._get_cached_media_id(script_id, language)
            if media_id is not None and self._is_media_id_fresh(script_id, language):
                sent = await self._send_audio_by_id(phone, media_id)
                if sent:
                    return True
                # Send failed — assume the ID is stale and re-upload.
                logger.info(
                    "Send by cached media id failed for %s / %s (attempt %d); re-uploading",
                    script_id,
                    language,
                    attempt,
                )
                self._invalidate_cache(script_id, language)

            # Upload the file.
            media_id = await self._upload_audio(fpath, audio_bytes)
            if media_id is None:
                if attempt == MAX_UPLOAD_ATTEMPTS:
                    break
                continue

            # Cache the new media id.
            self._media_id_cache[(script_id, language)] = (media_id, time.time())

            sent = await self._send_audio_by_id(phone, media_id)
            if sent:
                return True
            # Send failed after upload — invalidate and retry once.
            self._invalidate_cache(script_id, language)

        # All attempts failed: send the text fallback.
        text = self.get_script_text(script_id, language)
        if text:
            logger.error(
                "Could not send audio for %s / %s after %d attempts; sending text fallback to %s",
                script_id,
                language,
                MAX_UPLOAD_ATTEMPTS,
                phone,
            )
            await send_text(phone, text)
            return True
        logger.error(
            "Could not send audio for %s / %s and no text fallback; %s is unreachable",
            script_id,
            language,
            phone,
        )
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _generate_missing(self, script_id: str, language: str) -> Path | None:
        """Generate a missing audio file once, save it, and return its path.

        Used when the manifest has no entry for *script_id* / *language* or
        the file it points to is gone. Returns ``None`` on any failure so
        the caller can fall back to text; the user is never left in silence.
        """
        from app.voice.generate_assets import (
            generate_one,
            get_text_for_script,
            load_manifest,
            save_manifest,
            sha256_hex8,
        )

        language = audio_language(language)
        text = get_text_for_script(script_id, language) or self.get_script_text(
            script_id, language
        )
        if not text:
            logger.error(
                "Cannot generate missing audio for %s / %s: no script text",
                script_id,
                language,
            )
            return None

        logger.warning(
            "Missing audio for %s / %s; generating it now", script_id, language
        )
        try:
            result = await asyncio.wait_for(
                generate_one(script_id, language, text, force=False),
                timeout=GENERATE_TIMEOUT_SECONDS,
            )
        except Exception as error:  # noqa: BLE001 - fall back to text
            logger.error(
                "Could not generate missing audio for %s / %s: %s",
                script_id,
                language,
                error,
            )
            return None

        if result.get("status") not in ("generated", "skipped") or not result.get("file"):
            logger.error(
                "Generating missing audio for %s / %s failed: %s",
                script_id,
                language,
                result.get("reason", "unknown"),
            )
            return None

        # Register the file in the manifest and refresh this store's copy.
        try:
            manifest = load_manifest()
            manifest.setdefault(script_id, {})[language] = {
                "file": result["file"],
                "hash": sha256_hex8(text),
            }
            save_manifest(manifest)
            self.refresh_manifest()
        except Exception as error:  # noqa: BLE001 - the file exists regardless
            logger.warning(
                "Could not update manifest for %s / %s: %s",
                script_id,
                language,
                error,
            )
        return self.get_audio_file(script_id, language)

    def _get_cached_media_id(self, script_id: str, language: str) -> str | None:
        cached = self._media_id_cache.get((script_id, language))
        if cached is None:
            return None
        return cached[0]

    def _is_media_id_fresh(self, script_id: str, language: str) -> bool:
        cached = self._media_id_cache.get((script_id, language))
        if cached is None:
            return False
        _, uploaded_at = cached
        return (time.time() - uploaded_at) < MEDIA_ID_TTL_SECONDS

    def _invalidate_cache(self, script_id: str, language: str) -> None:
        self._media_id_cache.pop((script_id, language), None)

    def check_missing(self) -> list[str]:
        """Return a list of \"script_id / language — reason\" strings for
        every onboarding audio that is missing from the manifest or disk.

        Called at startup so the owner can see which files still need to be
        generated.
        """
        missing: list[str] = []
        from app.voice.scripts import SCRIPTS

        for script_id, entry in SCRIPTS.items():
            if not isinstance(entry, dict):
                continue
            for language in entry:
                manifest_entry = self._manifest.get(script_id, {}).get(language)
                if manifest_entry is None:
                    missing.append(f"{script_id} / {language} — not in manifest")
                    continue
                fpath = AUDIO_DIR / manifest_entry["file"]
                if not fpath.exists():
                    missing.append(
                        f"{script_id} / {language} — file missing: {manifest_entry['file']}"
                    )
        return missing

    async def _upload_audio(self, fpath: Path, audio_bytes: bytes) -> str | None:
        """Upload *audio_bytes* to WhatsApp and return the media id, or ``None``."""
        if not _dotenv_loaded():
            logger.error("WhatsApp env vars missing; cannot upload audio")
            return None

        graph_version = "v21.0"
        phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
        token = os.environ["WHATSAPP_ACCESS_TOKEN"]
        graph_url = f"https://graph.facebook.com/{graph_version}/{phone_number_id}"
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(timeout=30.0) as http:
            upload = await http.post(
                f"{graph_url}/media",
                headers=headers,
                data={"messaging_product": "whatsapp", "type": MEDIA_TYPE},
                files={"file": (fpath.name, audio_bytes, MEDIA_TYPE)},
            )
            if upload.status_code != 200:
                logger.error(
                    "Media upload failed for %s: %s %s",
                    fpath.name,
                    upload.status_code,
                    upload.text,
                )
                return None
            try:
                return upload.json()["id"]
            except (ValueError, KeyError) as exc:
                logger.error("Unexpected upload response for %s: %s", fpath.name, exc)
                return None

    async def _send_audio_by_id(self, phone: str, media_id: str) -> bool:
        """Send an audio message referencing *media_id* to *phone*."""
        if not _dotenv_loaded():
            logger.error("WhatsApp env vars missing; cannot send audio")
            return False

        graph_version = "v21.0"
        phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
        token = os.environ["WHATSAPP_ACCESS_TOKEN"]
        graph_url = f"https://graph.facebook.com/{graph_version}/{phone_number_id}"
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(timeout=30.0) as http:
            send = await http.post(
                f"{graph_url}/messages",
                headers=headers,
                json={
                    "messaging_product": "whatsapp",
                    "to": phone,
                    "type": "audio",
                    "audio": {"id": media_id},
                },
            )
            if send.status_code != 200:
                logger.error(
                    "Send by media id failed: %s %s",
                    send.status_code,
                    send.text,
                )
                return False
            return True


# Module-level convenience instance. Most callers use this; consumers that
# need a fresh cache (e.g. per-webhook) can create their own.
default_store = AudioStore()
