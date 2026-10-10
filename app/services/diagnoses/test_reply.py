"""Pure unit tests for the diagnosis reply builder.

No network access. These test parse_model_output, build_reply (every branch),
and the dose regex, matching VOICE_NOTE.md section 11.
"""

from __future__ import annotations

import json
import pytest

from app.services.diagnoses.reply import (
    DOSE_PATTERN,
    MSG,
    Reply,
    build_reply,
    parse_model_output,
)


# ---------------------------------------------------------------------------
# parse_model_output
# ---------------------------------------------------------------------------


def test_parse_valid_json() -> None:
    raw = '{"image_status": "ok", "photo_quality": {"usable": true}}'
    data = parse_model_output(raw)
    assert data == {"image_status": "ok", "photo_quality": {"usable": True}}


def test_parse_json_inside_code_fences() -> None:
    raw = "```json\n{\"image_status\": \"ok\", \"photo_quality\": {\"usable\": true}}\n```"
    data = parse_model_output(raw)
    assert data["image_status"] == "ok"


def test_parse_json_with_extra_text() -> None:
    raw = "here is the result:\n{\"image_status\": \"ok\"}\nand done"
    data = parse_model_output(raw)
    assert data["image_status"] == "ok"


def test_parse_garbage_returns_none() -> None:
    assert parse_model_output("not json at all") is None


def test_parse_empty_returns_none() -> None:
    assert parse_model_output("") is None
    assert parse_model_output(None) is None
    assert parse_model_output(123) is None


def test_parse_json_array_returns_none() -> None:
    assert parse_model_output("[1, 2, 3]") is None


# ---------------------------------------------------------------------------
# build_reply branches
# ---------------------------------------------------------------------------


def _ok_photo(
    farmer_message: str = "Your cassava has mosaic disease.",
    confidence: str = "high",
) -> dict:
    return {
        "image_status": "ok",
        "photo_quality": {"usable": True, "issues": []},
        "crop": {"name": "cassava", "confidence": "high"},
        "diagnosis": {
            "primary": {
                "name": "cassava mosaic disease",
                "category": "viral",
                "confidence": confidence,
            }
        },
        "recommended_actions": ["Remove infected plants.", "Use clean cuttings next time."],
        "see_extension_officer": {"needed": True, "reason": "serious outbreak"},
        "farmer_message": farmer_message,
    }


def test_build_reply_parse_error() -> None:
    reply = build_reply("not json")
    assert reply.kind == "error"
    assert MSG["parse_error"] in reply.text_en
    assert "parse_failed" in reply.flags


def test_build_reply_not_a_plant() -> None:
    raw = '{"image_status": "not_a_plant", "photo_quality": {"usable": false}}'
    reply = build_reply(raw)
    assert reply.kind == "retake"
    assert MSG["not_plant"] in reply.text_en
    assert reply.flags[0].startswith("bad_photo:")


def test_build_reply_no_crop_visible() -> None:
    raw = '{"image_status": "no_crop_visible", "photo_quality": {"usable": false}}'
    reply = build_reply(raw)
    assert reply.kind == "retake"
    assert MSG["not_plant"] in reply.text_en


def test_build_reply_unclear_photo() -> None:
    raw = '{"image_status": "unclear", "photo_quality": {"usable": false}}'
    reply = build_reply(raw)
    assert reply.kind == "retake"
    assert MSG["unclear"] in reply.text_en


def test_build_reply_retake_advice_used() -> None:
    raw = '{"image_status": "unclear", "photo_quality": {"usable": false, "retake_advice": "Please move closer."}}'
    reply = build_reply(raw)
    assert reply.kind == "retake"
    assert "move closer" in reply.text_en


def test_build_reply_no_diagnosis_block() -> None:
    raw = '{"image_status": "ok", "photo_quality": {"usable": true}, "diagnosis": null}'
    reply = build_reply(raw)
    assert reply.kind == "unsure"
    assert "not sure" in reply.text_en.lower()


def test_build_reply_low_confidence() -> None:
    data = _ok_photo(confidence="low")
    raw = json.dumps(data)
    reply = build_reply(raw)
    assert reply.kind == "unsure"
    assert "not sure" in reply.text_en.lower()
    assert "low_confidence" in reply.flags


def test_build_reply_unknown_name() -> None:
    data = _ok_photo(farmer_message="unknown")
    data["diagnosis"]["primary"]["name"] = "unknown"
    raw = json.dumps(data)
    reply = build_reply(raw)
    assert reply.kind == "unsure"


def test_build_reply_healthy() -> None:
    data = _ok_photo(farmer_message="Your crop looks healthy.")
    data["diagnosis"]["primary"]["name"] = "healthy"
    data["diagnosis"]["primary"]["category"] = "healthy"
    raw = json.dumps(data)
    reply = build_reply(raw)
    assert reply.kind == "healthy"
    assert MSG["healthy"] in reply.text_en
    assert "healthy" in reply.flags


def test_build_reply_medium_prefix_appended() -> None:
    data = _ok_photo(confidence="medium")
    raw = json.dumps(data)
    reply = build_reply(raw)
    assert reply.kind == "diagnosis"
    assert MSG["medium_prefix"] in reply.text_en
    assert "medium_confidence" in reply.flags


def test_build_reply_dose_in_farmer_message_triggers_fallback() -> None:
    data = _ok_photo(farmer_message="Apply 20 ml per litre of water.")
    raw = json.dumps(data)
    reply = build_reply(raw)
    assert "message_unusable_or_dose_found" in reply.flags
    # fallback builds from crop + diagnosis name + safe actions
    assert "cassava" in reply.text_en
    assert "mosaic" in reply.text_en


def test_build_reply_message_too_long_truncated_to_fallback() -> None:
    long_msg = "word " * 200
    data = _ok_photo(farmer_message=long_msg)
    raw = json.dumps(data)
    reply = build_reply(raw)
    assert "message_too_long" in reply.flags


def test_build_reply_extension_officer_line_appended() -> None:
    data = _ok_photo(confidence="high")
    data["see_extension_officer"] = {"needed": True, "reason": "outbreak"}
    raw = json.dumps(data)
    reply = build_reply(raw)
    assert MSG["extension"] in reply.text_en
    assert MSG["closing"] in reply.text_en


def test_build_reply_closing_line_always_appended() -> None:
    data = _ok_photo(confidence="high")
    raw = json.dumps(data)
    reply = build_reply(raw)
    assert MSG["closing"] in reply.text_en


def test_build_reply_unusable_farmer_message_uses_fallback() -> None:
    data = _ok_photo(farmer_message="")
    raw = json.dumps(data)
    reply = build_reply(raw)
    assert "message_unusable_or_dose_found" in reply.flags
    assert "cassava" in reply.text_en


# ---------------------------------------------------------------------------
# Dose regex
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Apply 20 ml per litre of water.",
    "Use 2 millilitres per plant.",
    "Spray 1 l every 7 days.",
    "Mix 2 g in 5 litres.",
    "Use 1 tablespoons per knapsack.",
    "Apply 3 teaspoons per acre.",
    "Use 2 sachets.",
    "Apply 500 mg per hectare.",
    "Give 1 cup every 3 days.",
])
def test_dose_regex_catches(text: str) -> None:
    assert DOSE_PATTERN.search(text) is not None, f"should catch: {text!r}"


@pytest.mark.parametrize("text", [
    "Remove the infected plants.",
    "Use clean cuttings next time.",
    "The plant looks healthy.",
    "Apply a copper-based fungicide.",
    "Ask your agro-dealer for the right product.",
    "Space the plants well.",
])
def test_dose_regex_passes(text: str) -> None:
    assert DOSE_PATTERN.search(text) is None, f"should not catch: {text!r}"


# ---------------------------------------------------------------------------
# welcome-back text helpers (pure)
# ---------------------------------------------------------------------------


def test_welcome_back_fallback_to_en() -> None:
    from app.services.brain import welcome_back_text
    assert "Welcome back" in welcome_back_text("unknown-language")


def test_send_wait_notice_sends_please_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_wait_notice sends the cached please_wait script for the user's
    stored language, with no real network call."""
    import asyncio

    from app.schemas.memory import User
    from app.services.brain import send_wait_notice

    # audio_store imports the WhatsApp send helper, which reads env vars at
    # import time; give it harmless values so no real API is reachable.
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "test-id")

    import app.voice.audio_store as audio_store_mod

    calls: list[tuple[str, str, str]] = []

    class FakeStore:
        async def send_voice_script(self, phone, script_id, language):
            calls.append((phone, script_id, language))
            return True

    monkeypatch.setattr(audio_store_mod, "default_store", FakeStore())

    user = User(phone="123", language="nigerian pidgin")
    asyncio.run(send_wait_notice(user))
    assert calls == [("123", "please_wait", "nigerian pidgin")]


def test_send_wait_notice_defaults_to_english_and_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing language means English; a failing store must not raise, so
    the diagnosis is never delayed or dropped because of the wait note."""
    import asyncio

    from app.schemas.memory import User
    from app.services.brain import send_wait_notice

    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "test-id")

    import app.voice.audio_store as audio_store_mod

    calls: list[tuple[str, str, str]] = []

    class FakeStore:
        async def send_voice_script(self, phone, script_id, language):
            calls.append((phone, script_id, language))
            raise RuntimeError("send failed")

    monkeypatch.setattr(audio_store_mod, "default_store", FakeStore())

    user = User(phone="123", language=None)
    asyncio.run(send_wait_notice(user))  # must not raise
    assert calls == [("123", "please_wait", "english")]


# ---------------------------------------------------------------------------
# description diagnosis builder (pure part)
# ---------------------------------------------------------------------------


def test_description_confidence_high_downgraded_to_medium() -> None:
    from app.services.brain import _build_description_reply
    data = _ok_photo(confidence="high")
    reply = _build_description_reply(data)
    assert reply.kind == "diagnosis"
    assert MSG["medium_prefix"] in reply.text_en
    assert "medium_confidence" in reply.flags


def test_description_photo_line_appended_by_code() -> None:
    from app.services.brain import _build_description_reply
    data = _ok_photo(confidence="medium")
    reply = _build_description_reply(data)
    assert "To be more sure, please send me a photo of the leaf." in reply.text_en


def test_description_chemical_stripped() -> None:
    from app.services.brain import _build_description_reply
    data = _ok_photo(farmer_message="Use 20 ml of pesticide per litre.")
    reply = _build_description_reply(data)
    assert "message_unusable_or_dose_found" in reply.flags
    assert "ml" not in reply.text_en.lower() or "per litre" not in reply.text_en.lower()
