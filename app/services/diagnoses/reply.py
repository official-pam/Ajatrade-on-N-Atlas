"""Pure, testable reply-building logic for crop diagnosis.

No network calls. Converts the vision/text model's raw JSON output into a
safe, dose-free reply and a kind label.

Decision order (VOICE_NOTE.md section 7):
  bad photo -> retake request
  low/unknown confidence -> 'not sure', refer to extension officer
  healthy -> no chemicals
  real diagnosis -> use the model's message only if it has no dose and is not
      too long; append medium-confidence prefix and/or extension-officer line
      and/or closing line as specified.

This module is unit-tested without any network access.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

MSG = {
    "parse_error": "Sorry, I could not look at your photo well. Please send the photo again.",
    "not_plant": "I cannot see a crop in this photo. Please take a photo of the leaf or plant that has the problem.",
    "unclear": (
        "The photo is not clear enough. Stand close to the plant. "
        "Hold the phone steady, use good daylight, and send one clear photo of the sick leaf."
    ),
    "healthy": (
        "Good news. Your crop looks healthy. Keep checking it often. "
        "If you see spots or yellow leaves later, send me another photo."
    ),
    "unsure": (
        "I am not sure what is wrong with this plant. "
        "Please show it to an extension officer or an agro-dealer near you. "
        "You can also send me a photo of another leaf."
    ),
    "extension": "Please also show the plant to an extension officer.",
    "medium_prefix": "I am not fully sure, but this is what I see.",
    "closing": "If you have another problem, send me another photo.",
}

# Safety net: catches doses, mixing amounts and spray schedules.
DOSE_PATTERN = re.compile(
    r"\b\d+(\.\d+)?\s*(ml|millilit\w*|l|litres?|liters?|g|grams?|kg|tablespoons?"
    r"|teaspoons?|tbsp|tsp|cups?|caps?|sachets?)\b"
    r"|\bper\s+(litre|liter|knapsack|hectare|acre)\b"
    r"|\b(every|each)\s+\d+\s+days?\b",
    re.I,
)

MAX_WORDS = 80


@dataclass
class Reply:
    kind: str  # error | retake | unsure | healthy | diagnosis
    text_en: str  # translate, TTS, send
    flags: list[str] = field(default_factory=list)
    parsed: dict[str, Any] | None = None


def parse_model_output(raw: Any) -> dict[str, Any] | None:
    """Parse JSON even if wrapped in code fences or extra text."""
    if not raw or not isinstance(raw, str):
        return None
    text = re.sub(r"```(?:json)?", "", raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clean(text: Any) -> str | None:
    """Return text only if non-empty and free of doses."""
    if not isinstance(text, str) or not text.strip():
        return None
    if DOSE_PATTERN.search(text):
        return None
    return text.strip()


def _build_fallback(data: dict[str, Any], name: str, confidence: str) -> str:
    crop = (data.get("crop") or {}).get("name") or "crop"
    verb = "has" if confidence == "high" else "may have"
    parts = [f"Your {crop} {verb} {name}."]
    actions = data.get("recommended_actions") or []
    safe = [a for a in actions if _clean(a)][:2]
    parts.extend(a.rstrip(".") + "." for a in safe)
    return " ".join(parts)


def build_reply(raw: Any) -> Reply:
    """Build a safe farmer-facing reply from the model's raw output."""
    data = parse_model_output(raw)
    print("Parse_Model_OUTPUT: ",  data)
    if data is None:
        return Reply("error", MSG["parse_error"], ["parse_failed"])

    status = data.get("image_status", "")
    quality = data.get("photo_quality") or {}
    if status != "": 
        if status != "ok" or quality.get("usable") is False:
            fallback = MSG["not_plant"] if status in ("not_a_plant", "no_crop_visible") else MSG["unclear"]
            text = _clean(quality.get("retake_advice")) or fallback
            return Reply("retake", text, [f"bad_photo:{status}"], data)

    primary = (data.get("diagnosis") or {}).get("primary")
    if not isinstance(primary, dict):
        return Reply("unsure", MSG["unsure"], ["no_diagnosis"], data)

    name = str(primary.get("name") or "unknown").strip().lower()
    category = str(primary.get("category") or "unknown").strip().lower()
    confidence = str(primary.get("confidence") or "low").strip().lower()

    if name == "unknown" or category == "unknown" or confidence == "low":
        return Reply("unsure", f"{MSG['unsure']} {MSG['closing']}", ["low_confidence"], data)

    if name == "healthy" or category == "healthy":
        return Reply("healthy", MSG["healthy"], ["healthy"], data)

    flags: list[str] = []
    message = _clean(data.get("farmer_message"))
    if message is None:
        flags.append("message_unusable_or_dose_found")
    elif len(message.split()) > MAX_WORDS:
        flags.append("message_too_long")
        message = None
    if message is None:
        message = _build_fallback(data, name, confidence)

    if confidence == "medium":
        message = f"{MSG['medium_prefix']} {message}"
        flags.append("medium_confidence")

    ext = data.get("see_extension_officer") or {}
    if ext.get("needed") and "extension officer" not in message.lower():
        message = f"{message} {MSG['extension']}"

    message = f"{message} {MSG['closing']}"
    return Reply("diagnosis", message, flags, data)
