"""N-ATLAS inference server for AjaTrade.

Runs on a GPU machine (Hugging Face Space, RunPod, Modal, Kaggle or Colab)
and serves the open N-ATLAS models published by NCAIR / Awarri:

- ``NCAIR1/N-ATLaS``                 language model (Llama-3 8B fine-tune)
- ``NCAIR1/Yoruba-ASR``              speech recognition, Yoruba
- ``NCAIR1/Hausa-ASR``               speech recognition, Hausa
- ``NCAIR1/Igbo-ASR``                speech recognition, Igbo
- ``NCAIR1/NigerianAccentedEnglish`` speech recognition, Nigerian English
                                     (also used for Nigerian Pidgin)

The WhatsApp bot never loads a model. It calls this server over HTTPS:

- ``POST /v1/chat/completions``  OpenAI-style chat completion (subset)
- ``POST /asr``                  transcribe a voice note
- ``GET  /health``               which models are loaded

Environment variables:

- ``HF_TOKEN``            Hugging Face token of an account that accepted the
                          N-ATLaS and ASR terms (the models are gated)
- ``NATLAS_SERVER_KEY``   shared secret; the bot sends it as a bearer token
- ``NATLAS_MODEL``        default ``NCAIR1/N-ATLaS``
- ``NATLAS_LOAD_IN_4BIT`` ``1`` to fit a 16 GB GPU (T4); default ``0`` (bf16,
                          needs about 17 GB, e.g. L4 / A10 / 3090 / 4090)
- ``NATLAS_ASR_LANGS``    comma list of ASR languages to load, default all
- ``NATLAS_ASR_DEVICE``   ``cuda`` (default when available) or ``cpu``

Run: ``uvicorn server:app --host 0.0.0.0 --port 8000``
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import subprocess
import time
import uuid
from datetime import datetime
from typing import Any

import numpy as np
import torch
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    WhisperForConditionalGeneration,
    WhisperProcessor,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("natlas_server")

HF_TOKEN = os.getenv("HF_TOKEN") or None
SERVER_KEY = os.getenv("NATLAS_SERVER_KEY", "")
LLM_ID = os.getenv("NATLAS_MODEL", "NCAIR1/N-ATLaS")
LOAD_IN_4BIT = os.getenv("NATLAS_LOAD_IN_4BIT", "0") == "1"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
ASR_DEVICE = os.getenv("NATLAS_ASR_DEVICE", DEVICE)

ASR_MODELS = {
    "yoruba": "NCAIR1/Yoruba-ASR",
    "hausa": "NCAIR1/Hausa-ASR",
    "igbo": "NCAIR1/Igbo-ASR",
    "english": "NCAIR1/NigerianAccentedEnglish",
}
# Pidgin has no dedicated N-ATLAS ASR model; Nigerian-accented English is
# the closest acoustic and lexical match.
ASR_ALIASES = {"pidgin": "english", "nigerian pidgin": "english"}
WHISPER_FALLBACK_PROCESSOR = "openai/whisper-small"

SAMPLE_RATE = 16_000
CHUNK_SECONDS = 28  # the ASR models take at most 30 s per call
MAX_AUDIO_SECONDS = 180

# Generation settings from the N-ATLaS model card.
REPETITION_PENALTY = 1.12

# One generation at a time per GPU; requests queue instead of running out of
# memory.
_llm_lock = asyncio.Lock()
_asr_lock = asyncio.Lock()

_llm: dict[str, Any] = {}
_asr: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def load_llm() -> None:
    logger.info("Loading %s (4-bit=%s) on %s", LLM_ID, LOAD_IN_4BIT, DEVICE)
    tokenizer = AutoTokenizer.from_pretrained(LLM_ID, token=HF_TOKEN)
    kwargs: dict[str, Any] = {"token": HF_TOKEN, "device_map": "auto"}
    if LOAD_IN_4BIT:
        from transformers import BitsAndBytesConfig

        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_quant_type="nf4",
        )
    else:
        kwargs["torch_dtype"] = torch.bfloat16 if DEVICE == "cuda" else torch.float32
    model = AutoModelForCausalLM.from_pretrained(LLM_ID, **kwargs)
    model.eval()
    _llm.update(tokenizer=tokenizer, model=model)
    logger.info("Loaded %s", LLM_ID)


def load_asr(language: str) -> None:
    repo = ASR_MODELS[language]
    logger.info("Loading ASR %s on %s", repo, ASR_DEVICE)
    try:
        processor = WhisperProcessor.from_pretrained(repo, token=HF_TOKEN)
    except Exception as error:  # some repos ship weights only
        logger.warning("No processor in %s (%s); using %s", repo, error, WHISPER_FALLBACK_PROCESSOR)
        processor = WhisperProcessor.from_pretrained(WHISPER_FALLBACK_PROCESSOR)
    dtype = torch.float16 if ASR_DEVICE == "cuda" else torch.float32
    model = WhisperForConditionalGeneration.from_pretrained(repo, token=HF_TOKEN, torch_dtype=dtype)
    model.to(ASR_DEVICE).eval()
    _asr[language] = {"processor": processor, "model": model, "dtype": dtype, "repo": repo}


def _asr_languages_to_load() -> list[str]:
    raw = os.getenv("NATLAS_ASR_LANGS", ",".join(ASR_MODELS))
    return [lang.strip() for lang in raw.split(",") if lang.strip() in ASR_MODELS]


# ---------------------------------------------------------------------------
# App + auth
# ---------------------------------------------------------------------------

app = FastAPI(title="AjaTrade N-ATLAS server", version="1.0.0")


def load_all() -> None:
    """Load every model once (safe to call again)."""
    if not _llm and os.getenv("NATLAS_SKIP_LLM", "0") != "1":
        load_llm()
    for language in _asr_languages_to_load():
        if language not in _asr:
            load_asr(language)


@app.on_event("startup")
async def _startup() -> None:
    await asyncio.to_thread(load_all)


def require_key(authorization: str | None = Header(default=None)) -> None:
    if not SERVER_KEY:
        return  # open server (local testing only)
    expected = f"Bearer {SERVER_KEY}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid key")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "llm": LLM_ID if _llm else None,
        "asr": {lang: info["repo"] for lang, info in _asr.items()},
        "device": DEVICE,
        "four_bit": LOAD_IN_4BIT,
    }


# ---------------------------------------------------------------------------
# Chat completions (OpenAI-style subset)
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    temperature: float = 0.1
    max_tokens: int = Field(default=600, le=2000)
    response_format: dict[str, Any] | None = None


def _build_prompt(messages: list[ChatMessage], json_mode: bool) -> str:
    tokenizer = _llm["tokenizer"]
    chat = [{"role": m.role, "content": m.content} for m in messages]
    prompt = tokenizer.apply_chat_template(
        chat,
        tokenize=False,
        add_generation_prompt=True,
        date_string=datetime.now().strftime("%d %b %Y"),
    )
    if json_mode:
        # Prefill the reply with "{" so the model starts a JSON object.
        prompt += "{"
    return prompt


def _generate(prompt: str, temperature: float, max_tokens: int) -> tuple[str, int, int]:
    tokenizer, model = _llm["tokenizer"], _llm["model"]
    encoded = tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
    inputs = {k: encoded[k].to(model.device) for k in ("input_ids", "attention_mask") if k in encoded}
    eos_ids = [tokenizer.eos_token_id]
    eot = tokenizer.convert_tokens_to_ids("<|eot_id|>")
    if isinstance(eot, int) and eot >= 0:
        eos_ids.append(eot)
    do_sample = temperature > 0.05
    with torch.inference_mode():
        output = model.generate(
            **inputs,
            max_new_tokens=max_tokens,
            do_sample=do_sample,
            temperature=temperature if do_sample else None,
            top_p=0.9 if do_sample else None,
            repetition_penalty=REPETITION_PENALTY,
            eos_token_id=eos_ids,
            pad_token_id=tokenizer.eos_token_id,
            use_cache=True,
        )
    new_tokens = output[0][inputs["input_ids"].shape[1]:]
    text = tokenizer.decode(new_tokens, skip_special_tokens=True)
    return text.strip(), int(inputs["input_ids"].shape[1]), int(new_tokens.shape[0])


@app.post("/v1/chat/completions", dependencies=[Depends(require_key)])
async def chat_completions(request: ChatRequest) -> dict[str, Any]:
    if not _llm:
        raise HTTPException(status_code=503, detail="language model not loaded")
    json_mode = (request.response_format or {}).get("type") == "json_object"
    prompt = _build_prompt(request.messages, json_mode)
    async with _llm_lock:
        started = time.monotonic()
        text, prompt_tokens, completion_tokens = await asyncio.to_thread(
            _generate, prompt, request.temperature, request.max_tokens
        )
    if json_mode:
        text = "{" + text
    logger.info(
        "chat: %d prompt + %d completion tokens in %.1fs",
        prompt_tokens, completion_tokens, time.monotonic() - started,
    )
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": LLM_ID,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


# ---------------------------------------------------------------------------
# Speech recognition
# ---------------------------------------------------------------------------


def decode_audio(data: bytes) -> np.ndarray:
    """Decode any audio (WhatsApp sends OGG/Opus) to 16 kHz mono float32."""
    process = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0", "-t", str(MAX_AUDIO_SECONDS),
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1",
        ],
        input=data,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        raise ValueError(process.stderr.decode("utf-8", "ignore")[:300] or "ffmpeg failed")
    return np.frombuffer(process.stdout, dtype=np.float32)


def _chunks(audio: np.ndarray) -> list[np.ndarray]:
    size = CHUNK_SECONDS * SAMPLE_RATE
    pieces = [audio[i:i + size] for i in range(0, len(audio), size)]
    # Drop a tiny trailing piece (under half a second): it only adds noise.
    return [p for p in pieces if len(p) >= SAMPLE_RATE // 2] or [audio]


def transcribe_with(language: str, audio: np.ndarray) -> tuple[str, float]:
    """Transcribe with one ASR model.

    Returns (text, mean token log-probability). The log-probability lets the
    auto mode pick the model that is most confident about the audio.
    """
    info = _asr[language]
    processor, model = info["processor"], info["model"]
    texts: list[str] = []
    scores: list[float] = []
    for piece in _chunks(audio):
        features = processor(piece, sampling_rate=SAMPLE_RATE, return_tensors="pt").input_features
        features = features.to(ASR_DEVICE, dtype=info["dtype"])
        with torch.inference_mode():
            out = model.generate(
                features,
                max_new_tokens=min(220, model.config.max_target_positions - 8),
                return_dict_in_generate=True,
                output_scores=True,
            )
        transition = model.compute_transition_scores(out.sequences, out.scores, normalize_logits=True)
        valid = transition[0][torch.isfinite(transition[0])]
        if valid.numel():
            scores.append(float(valid.mean()))
        texts.append(processor.batch_decode(out.sequences, skip_special_tokens=True)[0].strip())
    text = " ".join(t for t in texts if t).strip()
    score = sum(scores) / len(scores) if scores else float("-inf")
    return text, score


@app.post("/asr", dependencies=[Depends(require_key)])
async def asr(
    file: UploadFile = File(...),
    language: str = Form("auto"),
) -> dict[str, Any]:
    """Transcribe a voice note.

    ``language`` is yoruba | hausa | igbo | english | pidgin | auto. With
    ``auto`` every loaded ASR model transcribes the audio and the one with the
    highest mean token log-probability wins; its language is returned so the
    bot can use it for onboarding language detection.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty audio")
    try:
        audio = await asyncio.to_thread(decode_audio, data)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=f"could not decode audio: {error}")

    requested = (language or "auto").strip().lower()
    requested = ASR_ALIASES.get(requested, requested)
    if requested != "auto" and requested not in _asr:
        raise HTTPException(status_code=400, detail=f"no ASR model for {language}")
    candidates = list(_asr) if requested == "auto" else [requested]
    if not candidates:
        raise HTTPException(status_code=503, detail="no ASR model loaded")

    started = time.monotonic()
    results: dict[str, tuple[str, float]] = {}
    async with _asr_lock:
        for lang in candidates:
            results[lang] = await asyncio.to_thread(transcribe_with, lang, audio)
    best = max(results, key=lambda lang: results[lang][1])
    logger.info(
        "asr %s -> %s in %.1fs (%.1fs audio)",
        requested, best, time.monotonic() - started, len(audio) / SAMPLE_RATE,
    )
    return {
        "text": results[best][0],
        "language": best,
        "model": _asr[best]["repo"],
        "avg_logprob": results[best][1],
        "duration_seconds": round(len(audio) / SAMPLE_RATE, 2),
        "candidates": {
            lang: {"text": text, "avg_logprob": score} for lang, (text, score) in results.items()
        }
        if requested == "auto"
        else None,
    }
