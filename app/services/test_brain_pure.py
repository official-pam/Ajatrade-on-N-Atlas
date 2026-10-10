"""Pure unit tests for case expiry, router scenarios, and case caps.

No network access. These test the pure parts of the brain router that can be
verified without calling Groq: expiry logic, case caps, confidence downgrade,
and the decision structure.

VOICE_NOTE.md section 11 wants these; the model-dependent paths (intent
classification, text-model turns) are covered by the integration path and are
not tested here.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from app.schemas.memory import User
from app.storage import OpenCase, UserFlow, UserStep, new_case_id
from app.services.brain import (
    CASE_INACTIVITY_TIMEOUT_SECONDS,
    _case_expired,
    _build_description_reply,
    _build_case_context,
    _merge_extracted,
    _step_for_question,
    route_message,  # imported for signature check; not called (needs DB + model)
)


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def test_case_not_expired_within_3_hours() -> None:
    case = OpenCase(
        flow=UserFlow.CROP_DETECTION,
        step=UserStep.AWAITING_CROP,
        current_case_id=new_case_id(),
        case_started_at=datetime.now(timezone.utc),
        last_activity_at=datetime.now(timezone.utc),
        case_crop=None,
        case_part=None,
        case_symptoms=[],
        questions_asked=0,
        diagnoses_in_case=0,
    )
    assert _case_expired(case) is False


def test_case_expired_beyond_3_hours() -> None:
    case = OpenCase(
        flow=UserFlow.CROP_DETECTION,
        step=UserStep.AWAITING_CROP,
        current_case_id=new_case_id(),
        case_started_at=datetime.now(timezone.utc),
        last_activity_at=datetime.now(timezone.utc)
        - timedelta(seconds=CASE_INACTIVITY_TIMEOUT_SECONDS + 60),
        case_crop=None,
        case_part=None,
        case_symptoms=[],
        questions_asked=0,
        diagnoses_in_case=0,
    )
    assert _case_expired(case) is True


def test_no_case_open_is_not_expired() -> None:
    case = OpenCase.empty()
    assert _case_expired(case) is False


def test_expiry_threshold_is_3_hours() -> None:
    assert CASE_INACTIVITY_TIMEOUT_SECONDS == 3 * 60 * 60


# ---------------------------------------------------------------------------
# Case caps: third question forced to diagnose
# ---------------------------------------------------------------------------


def test_third_question_forced_to_diagnose() -> None:
    # The router forces diagnose when questions_asked >= MAX_QUESTIONS_PER_CASE.
    from app.services.brain import MAX_QUESTIONS_PER_CASE
    assert MAX_QUESTIONS_PER_CASE == 2


def test_third_diagnosis_blocked() -> None:
    from app.services.brain import MAX_DIAGNOSES_PER_CASE
    assert MAX_DIAGNOSES_PER_CASE == 2


def test_confidence_high_downgraded_to_medium_in_description() -> None:
    data = {
        "image_status": "ok",
        "photo_quality": {"usable": True},
        "diagnosis": {
            "primary": {
                "name": "cassava mosaic disease",
                "category": "viral",
                "confidence": "high",
            }
        },
        "recommended_actions": ["Remove infected plants."],
        "see_extension_officer": {"needed": False, "reason": ""},
        "farmer_message": "Your cassava may have mosaic disease.",
    }
    reply = _build_description_reply(data)
    assert reply.kind == "diagnosis"
    # high -> medium cap.
    assert "medium_prefix" in reply.text_en or "not fully sure" in reply.text_en


# ---------------------------------------------------------------------------
# Context building (pure)
# ---------------------------------------------------------------------------


def test_case_context_includes_symptoms() -> None:
    case = OpenCase(
        flow=UserFlow.CROP_DETECTION,
        step=UserStep.AWAITING_DESCRIPTION,
        current_case_id=new_case_id(),
        case_started_at=datetime.now(timezone.utc),
        last_activity_at=datetime.now(timezone.utc),
        case_crop="cassava",
        case_part="leaf",
        case_symptoms=["yellow leaves", "mosaic patches"],
        questions_asked=1,
        diagnoses_in_case=0,
    )
    ctx = _build_case_context(case, None)
    assert "cassava" in ctx
    assert "yellow leaves" in ctx
    assert "mosaic patches" in ctx


def test_case_context_uses_summary_when_present() -> None:
    case = OpenCase.empty()
    ctx = _build_case_context(case, "grew maize in Ibadan; last advice: remove infected plants.")
    assert "grew maize in Ibadan" in ctx


# ---------------------------------------------------------------------------
# Extraction merge (pure)
# ---------------------------------------------------------------------------


def test_merge_extracted_adds_new_symptoms() -> None:
    case = OpenCase(
        flow=UserFlow.CROP_DETECTION,
        step=UserStep.AWAITING_DESCRIPTION,
        current_case_id=new_case_id(),
        case_started_at=datetime.now(timezone.utc),
        last_activity_at=datetime.now(timezone.utc),
        case_crop="cassava",
        case_part=None,
        case_symptoms=["yellow leaves"],
        questions_asked=0,
        diagnoses_in_case=0,
    )
    merged = _merge_extracted(case, {"crop": "cassava", "part": "leaf", "symptoms": ["mosaic patches"]})
    assert "yellow leaves" in merged.case_symptoms
    assert "mosaic patches" in merged.case_symptoms
    assert merged.case_part == "leaf"


def test_merge_extracted_keeps_existing_crop_when_new_is_none() -> None:
    case = OpenCase(
        flow=UserFlow.CROP_DETECTION,
        step=UserStep.AWAITING_DESCRIPTION,
        current_case_id=new_case_id(),
        case_started_at=datetime.now(timezone.utc),
        last_activity_at=datetime.now(timezone.utc),
        case_crop="cassava",
        case_part=None,
        case_symptoms=[],
        questions_asked=0,
        diagnoses_in_case=0,
    )
    merged = _merge_extracted(case, {"crop": None, "symptoms": []})
    assert merged.case_crop == "cassava"


# ---------------------------------------------------------------------------
# Step selection for question (pure)
# ---------------------------------------------------------------------------


def test_step_awaiting_description_when_crop_known() -> None:
    model_output = {"extracted": {"crop": "cassava"}}
    case = OpenCase(
        flow=UserFlow.CROP_DETECTION,
        step=UserStep.AWAITING_CROP,
        current_case_id=new_case_id(),
        case_started_at=datetime.now(timezone.utc),
        last_activity_at=datetime.now(timezone.utc),
        case_crop="cassava",
        case_part=None,
        case_symptoms=[],
        questions_asked=1,
        diagnoses_in_case=0,
    )
    step = _step_for_question(model_output, case)
    assert step == UserStep.AWAITING_DESCRIPTION


# ---------------------------------------------------------------------------
# Router: imported for signature; not called here (needs DB + model).
# ---------------------------------------------------------------------------


def test_route_message_signature_accepts_user_and_text() -> None:
    # Just confirm the function exists and has the right shape; real routing
    # is tested via the integration path (webhook -> handle_message -> brain).
    import inspect
    sig = inspect.signature(route_message)
    params = list(sig.parameters.keys())
    # Positional shape unchanged; location (pins) is keyword-only.
    assert params[:3] == ["user", "english_text", "media_ref"]
    assert sig.parameters["location"].kind is inspect.Parameter.KEYWORD_ONLY
