"""N-ATLAS provider: the single place the bot talks to N-ATLAS.

Every language-model call (translation, language detection, intent routing,
diagnosis reasoning, farm advice, summaries) goes through :func:`chat`, and
every voice note goes through :func:`transcribe`. Both call the N-ATLAS
server in ``natlas_server/`` over HTTPS, so the bot itself loads no model and
needs no new dependency (httpx and tenacity are already used).

Environment variables:

- ``NATLAS_BASE_URL``  URL of the N-ATLAS server, e.g. https://xyz.hf.space
- ``NATLAS_API_KEY``   shared secret (the server's ``NATLAS_SERVER_KEY``)
- ``NATLAS_ASR_URL``   optional, when ASR runs on a different host
- ``NATLAS_MODEL``     model name sent in requests (informational)
- ``NATLAS_TIMEOUT_SECONDS`` default 90 (a cold 8B model can be slow)

The chat endpoint is OpenAI-compatible, so ``NATLAS_BASE_URL`` can also point
at N-ATLaS served by vLLM, TGI or a Hugging Face Inference Endpoint.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.services.languages import normalize_language

load_dotenv()

logger = logging.getLogger("natlas")

MODEL_NAME = os.getenv("NATLAS_MODEL", "NCAIR1/N-ATLaS")
ASR_MODEL_NAMES = {
    "yoruba": "NCAIR1/Yoruba-ASR",
    "hausa": "NCAIR1/Hausa-ASR",
    "igbo": "NCAIR1/Igbo-ASR",
    "english": "NCAIR1/NigerianAccentedEnglish",
    "pidgin": "NCAIR1/NigerianAccentedEnglish",
}


class NatlasError(RuntimeError):
    """N-ATLAS could not answer (not configured, down, or bad reply)."""


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None  # language of the ASR model that produced the text
    model: str | None


def _base_url() -> str:
    url = os.getenv("NATLAS_BASE_URL", "").strip().rstrip("/")
    if not url:
        raise NatlasError("NATLAS_BASE_URL is not set")
    return url


def _asr_url() -> str:
    # os.getenv("NATLAS_ASR_URL", "").strip().rstrip("/") or
    return (_base_url())


def _headers() -> dict[str, str]:
    # The ngrok header skips ngrok's free-plan browser warning page; other
    # hosts ignore it.
    # headers = {"ngrok-skip-browser-warning": "1", "User-Agent": "AjaTrade/1.0"}
    headers = {}
    key = os.getenv("NATLAS_API_KEY", "").strip()
    print("KEY: ", key)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _timeout() -> float:
    try:
        return float(os.getenv("NATLAS_TIMEOUT_SECONDS", "90"))
    except ValueError:
        return 90.0


def _retryable(error: BaseException) -> bool:
    """Retry network errors and 5xx, not 4xx (a bad request stays bad)."""
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code >= 500
    return isinstance(error, (httpx.TransportError, httpx.TimeoutException))


_one_retry = retry(
    stop=stop_after_attempt(2),  # one attempt plus one retry (AGENTS.md)
    wait=wait_exponential(min=1, max=4),
    retry=retry_if_exception(_retryable),
    reraise=True,
)


@_one_retry
async def _post_chat(payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{_base_url()}/v1/chat/completions", json=payload, headers=_headers()
        )
        response.raise_for_status()
        return response.json()


async def chat_messages(
    messages: list[dict[str, str]],
    *,
    json_mode: bool = False,
    temperature: float = 0.1,
    max_tokens: int = 600,
    timeout: float | None = None,
) -> str:
    """One N-ATLaS chat completion. Returns the reply text.

    Raises :class:`NatlasError` (or an httpx error) on failure so callers can
    send their polite fallback.
    """
    payload: dict[str, Any] = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    data = await _post_chat(payload, timeout or _timeout())
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise NatlasError(f"unexpected chat reply shape: {error}") from error
    return (content or "").strip()


async def chat(
    system_prompt: str,
    user_text: str,
    *,
    json_mode: bool = False,
    temperature: float = 0.1,
    max_tokens: int = 600,
    timeout: float | None = None,
) -> str:
    """System + user convenience wrapper around :func:`chat_messages`."""
    return await chat_messages(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        json_mode=json_mode,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )


@_one_retry
async def _post_asr(audio_bytes: bytes, mime_type: str, language: str, timeout: float) -> dict[str, Any]:
    extension = mime_type.rsplit("/", 1)[-1].split(";")[0] or "ogg"
    print("Extention: : ", extension)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{_asr_url()}/asr",
            files={"file": (f"audio.{extension}", audio_bytes, mime_type)},
            data={"language": language},
            headers=_headers(),
        )
        print("Response: : ", response)
        response.raise_for_status()
        return response.json()


async def transcribe(
    audio_bytes: bytes,
    mime_type: str = "audio/ogg",
    language: str | None = None,
) -> Transcript:
    """Transcribe a voice note with the N-ATLAS ASR model for ``language``.

    With no language (first voice note during onboarding) the server runs
    every ASR model and returns the most confident transcript and its
    language.
    """
    lang = normalize_language(language) if language else None
    if lang not in ASR_MODEL_NAMES:
        lang = None
    data = await _post_asr(audio_bytes, mime_type, lang or "auto", _timeout())
    text = str(data.get("text") or "").strip()
    print("TEXT: : ", text)
    detected = normalize_language(data.get("language")) if data.get("language") else lang
    return Transcript(text=text, language=detected, model=data.get("model"))
