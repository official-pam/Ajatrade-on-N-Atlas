"""Photo crop diagnosis: vision observes, N-ATLaS diagnoses and advises.

1. The vision provider (app/services/vision.py) reports what is visible in
   the photo: crop, symptoms, pests, visual look-alikes, photo quality.
2. N-ATLaS reads those observations plus the farmer's note, decides the most
   likely problem and writes the advice.
3. The result has the same JSON shape the reply builder (reply.py) already
   checks for doses, low confidence and bad photos.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.services import natlas, vision
from app.services.diagnoses.reply import parse_model_output

logger = logging.getLogger("disease_detection")

SYSTEM_PROMPT = """
You are a crop health assistant for smallholder farmers in Nigeria. A camera
assistant looked at the farmer's photo and wrote down what it saw. You did not
see the photo yourself. Decide the most likely problem from those
observations and write advice. Return ONE JSON object and nothing else.

RULES
1. Use only the observations and the farmer's note. Do not invent symptoms.
2. Never overstate certainty. If the signs fit more than one cause, lower the
   confidence and list alternatives. "unknown" is acceptable.
3. If the plant looks healthy, use category "healthy". Do not invent a disease.
4. Common problems: cassava (mosaic disease, bacterial blight, brown streak),
   maize (fall armyworm, streak virus, leaf blight), tomato (early blight, late
   blight, Tuta absoluta), rice (blast), cocoa (black pod), yam (anthracnose),
   pepper and cowpea leaf diseases and pests.
5. recommended_actions: low-cost and cultural steps first (remove infected
   plants, clean planting material, spacing, sanitation, rotation). Then, only
   if a chemical is commonly used for this problem, ONE last item naming the
   product type or active ingredient. NEVER give doses, mixing amounts or
   spray schedules; say "Ask your agro-dealer for the right product and follow
   the label." No chemical when healthy or when confidence is low.
6. farmer_message: simple English, short sentences, under 60 words, no jargon.
7. Output ONLY this JSON object:
{
  "crop": {"name": "crop name or null", "confidence": "low | medium | high"},
  "diagnosis": {
    "primary": {"name": "problem name, 'healthy' or 'unknown'",
                "category": "fungal | bacterial | viral | pest | nutrient | abiotic | healthy | unknown",
                "confidence": "low | medium | high"},
    "alternatives": [{"name": "other cause", "category": "same options"}],
    "visible_symptoms": ["copied from the observations"]
  },
  "recommended_actions": ["short action"],
  "see_extension_officer": {"needed": true or false, "reason": "one sentence"},
  "farmer_message": "short plain message for the farmer"
}
""".strip()

REQUIRED_KEYS = ["image_status", "photo_quality", "farmer_message"]

RETAKE_MESSAGE = (
    "I could not read that photo well. Please send a clear, close photo of one "
    "affected leaf in daylight."
)


def _retake(observation: dict[str, Any]) -> dict[str, Any]:
    quality = observation.get("photo_quality") or {}
    return {
        "image_status": observation.get("image_status") or "unclear",
        "photo_quality": {
            "usable": False,
            "issues": quality.get("issues") or [],
            "retake_advice": quality.get("retake_advice") or RETAKE_MESSAGE,
        },
        "farmer_message": quality.get("retake_advice") or RETAKE_MESSAGE,
    }


def _fallback_from_observation(observation: dict[str, Any]) -> dict[str, Any]:
    """Cautious answer when N-ATLaS is unavailable: refer, never guess high."""
    return {
        "image_status": "ok",
        "photo_quality": observation.get("photo_quality") or {"usable": True},
        "crop": observation.get("crop") or {"name": None, "confidence": "low"},
        "diagnosis": {
            "primary": {"name": "unknown", "category": "unknown", "confidence": "low"},
            "alternatives": [],
            "visible_symptoms": observation.get("visible_symptoms") or [],
        },
        "recommended_actions": [],
        "see_extension_officer": {"needed": True, "reason": "Could not finish the diagnosis."},
        "farmer_message": "I am not sure of the problem. Show the plant to an extension officer.",
    }


async def diagnose(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    farmer_text: str | None = None,
) -> dict[str, Any]:
    """Diagnose a crop photo. Never raises; always returns the REQUIRED_KEYS."""
    observation = await vision.perceive(image_bytes, mime_type, "crop_health", farmer_text)
    if not vision.photo_is_usable(observation):
        return _retake(observation)

    observed = {k: v for k, v in observation.items() if k not in ("image_status", "photo_quality")}
    user_text = "Camera observations:\n" + json.dumps(observed, ensure_ascii=False, indent=1)
    if farmer_text and farmer_text.strip():
        user_text += f'\n\nFarmer\'s note (may be incomplete): "{farmer_text.strip()[:500]}"'

    try:
        raw = await natlas.chat(SYSTEM_PROMPT, user_text, json_mode=True, max_tokens=600)
        result = parse_model_output(raw)
    except Exception as error:  # noqa: BLE001 - fall back to a cautious answer
        logger.warning("N-ATLaS diagnosis failed: %s", error)
        result = None
    if not result or "diagnosis" not in result:
        result = _fallback_from_observation(observation)

    result["image_status"] = "ok"
    result["photo_quality"] = observation.get("photo_quality") or {"usable": True}
    result.setdefault("farmer_message", "")
    result["observation"] = observed
    return diagnoses_result_check(result)


def diagnoses_result_check(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("image_status") == "unclear":
        return {
            "image_status": result["image_status"],
            "photo_quality": result.get("photo_quality"),
            "farmer_message": "I could not read that photo well. Please send a clearer photo.",
        }
    primary = (result.get("diagnosis") or {}).get("primary") or {}
    if str(primary.get("confidence") or "low").lower() == "low":
        return {
            "image_status": result.get("image_status"),
            "photo_quality": result.get("photo_quality"),
            "farmer_message": "I am not sure of the diseases present. Show the plant to an extension officer.",
        }
    if not str(result.get("farmer_message") or "").strip():
        from app.services.diagnoses.reply import build_reply

        result["farmer_message"] = build_reply(json.dumps(result)).text_en
    return result
