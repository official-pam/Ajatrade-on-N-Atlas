"""Vision provider: turns a farm photo into structured observations.

N-ATLaS reads text only, so photos need an eyes-only step first. This module
is that step and nothing more: it reports what is visible (crop, symptoms,
weeds, seedlings, the numbers printed on a soil-test report) as JSON. It
does not advise the farmer. All reasoning, recommendations and wording in the
farmer's language are done by N-ATLaS afterwards.

The provider is the Groq-hosted vision model the bot already used
(GROQ_API_KEY, VISION_MODEL_NAME), behind one function so it can be swapped
without touching the flows (AGENTS.md).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Any

from dotenv import load_dotenv

from app.services.diagnoses.reply import parse_model_output

load_dotenv()

logger = logging.getLogger("vision")

VISION_MODEL_NAME = os.getenv("VISION_MODEL_NAME", "") or "qwen/qwen3.8-27b"
VISION_TIMEOUT_SECONDS = 60.0

_COMMON_QUALITY = """
First judge the photo. If it is blurry, too dark, too far away, or does not
show what is asked, set image_status to "unclear" (or "not_a_plant" /
"no_crop_visible") and explain in photo_quality. Never guess from a bad photo.
Report only what you can see. Do not give advice, treatments or doses.
Output ONLY one JSON object, no markdown.
""".strip()

PROMPTS: dict[str, str] = {
    "crop_health": _COMMON_QUALITY + """

TASK: describe the health of the crop in the photo for a plant doctor who
cannot see it.

JSON
{
  "image_status": "ok | unclear | not_a_plant | no_crop_visible",
  "photo_quality": {"usable": true, "issues": [], "retake_advice": null},
  "crop": {"name": "crop name or null", "confidence": "low | medium | high"},
  "plant_part": "leaf | stem | fruit | root | whole plant | null",
  "visible_symptoms": ["one visible sign per item: colour, shape, pattern, where on the plant"],
  "pests_seen": ["insects, larvae, eggs or damage marks seen, or empty"],
  "share_of_plant_affected": "none | under a quarter | about half | most",
  "looks_healthy": true or false,
  "visual_matches": [
    {"name": "disease, pest or disorder this pattern looks like", "how_close": "low | medium | high"}
  ]
}
""",
    "weeds": _COMMON_QUALITY + """

TASK: describe the weeds in this farm photo for a weed-control adviser.

JSON
{
  "image_status": "ok | unclear | not_a_plant | no_crop_visible",
  "photo_quality": {"usable": true, "issues": [], "retake_advice": null},
  "crop": {"name": "the crop in the field or null", "growth_stage": "seedling | young | mid | mature | null"},
  "weeds": [
    {
      "type": "grass | broadleaf | sedge | parasitic | unknown",
      "likely_species": "common or scientific name, or null",
      "how_close": "low | medium | high",
      "growth_stage": "seedling | young | flowering | seeding | null"
    }
  ],
  "weed_cover": "few | moderate | heavy",
  "striga_seen": true or false
}
""",
    "germination": _COMMON_QUALITY + """

TASK: count seeds and seedlings in this nursery tray, seed test or planted row.

JSON
{
  "image_status": "ok | unclear | not_a_plant | no_crop_visible",
  "photo_quality": {"usable": true, "issues": [], "retake_advice": null},
  "setup": "nursery tray | paper or cloth test | field row | other",
  "crop": {"name": "crop or null", "confidence": "low | medium | high"},
  "positions_visible": number of seed positions or planting holes you can see, or null,
  "germinated": number that have sprouted, or null,
  "not_germinated": number with no sprout, or null,
  "count_confidence": "low | medium | high",
  "abnormal_seedlings": number that look weak, twisted, pale or rotten, or 0,
  "visible_problems": ["mould, rot, damping off, insect damage, dry soil, etc."]
}
""",
    "soil_report": _COMMON_QUALITY + """

TASK: read the values printed on this soil-test report or soil-meter screen.
Copy numbers exactly as printed. Use null for anything not on the report.

JSON
{
  "image_status": "ok | unclear | not_a_plant | no_crop_visible",
  "photo_quality": {"usable": true, "issues": [], "retake_advice": null},
  "values": {
    "ph": number or null,
    "ec_ds_per_m": number or null,
    "organic_carbon_percent": number or null,
    "organic_matter_percent": number or null,
    "total_nitrogen_percent": number or null,
    "available_p_mg_per_kg": number or null,
    "exchangeable_k_cmol_per_kg": number or null,
    "moisture_percent": number or null,
    "texture": "sand | loamy sand | sandy loam | loam | clay loam | clay | null"
  },
  "units_seen": ["unit strings exactly as printed"],
  "lab_or_device": "name printed on the report or device, or null"
}
""",
}

# For "not a soil report" the generic statuses still apply.
VALID_STATUSES = {"ok", "unclear", "not_a_plant", "no_crop_visible"}

_client: Any = None


def _groq_client() -> Any:
    global _client
    if _client is None:
        from groq import AsyncGroq

        _client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
    return _client


def unreadable(reason: str = "parse error") -> dict[str, Any]:
    """Observation used when the vision step fails: asks for a new photo."""
    return {
        "image_status": "unclear",
        "photo_quality": {
            "usable": False,
            "issues": [reason],
            "retake_advice": None,
        },
    }


async def perceive(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    task: str = "crop_health",
    farmer_text: str | None = None,
) -> dict[str, Any]:
    """Return what the photo shows for ``task`` as a dict. Never raises."""
    prompt = PROMPTS.get(task)
    if prompt is None:
        raise ValueError(f"unknown vision task {task!r}")
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    instruction = "Describe this photo. Reply with JSON only."
    if farmer_text and farmer_text.strip():
        instruction += (
            "\nThe farmer's note (context only, may be wrong): "
            f'"""{farmer_text.strip()[:500]}"""'
        )
    try:
        completion = await asyncio.wait_for(
            _groq_client().chat.completions.create(
                model=VISION_MODEL_NAME,
                messages=[
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": instruction},
                            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                        ],
                    },
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_completion_tokens=700,
            ),
            timeout=VISION_TIMEOUT_SECONDS,
        )
        data = parse_model_output(completion.choices[0].message.content)
    except Exception as error:  # noqa: BLE001 - a failed photo gets a retake request
        logger.warning("Vision %s failed: %s", task, error)
        return unreadable("vision error")
    if not data:
        return unreadable()
    if data.get("image_status") not in VALID_STATUSES:
        data["image_status"] = "unclear"
    return data


def photo_is_usable(observation: dict[str, Any]) -> bool:
    quality = observation.get("photo_quality") or {}
    return observation.get("image_status") == "ok" and quality.get("usable") is not False
