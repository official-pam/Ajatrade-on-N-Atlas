"""User model.

Persistence now lives in :mod:`app.storage`; this module only holds the shape
of a user, matching the ``users`` table columns (SPEC.md section 3, and the
case-state fields from VOICE_NOTE.md section 5).
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass
class User:
    phone: str
    name: str | None = None
    preferred_name: str | None = None
    state: str = "new"
    language: str | None = None
    language_source: str | None = None
    language_attempts: int = 0
    role: str | None = None
    onboarding: bool = False
    consent_status: str = "pending"
    consent_at: datetime | None = None
    consent_version: str | None = None
    created_at: datetime | None = None
    last_seen_at: datetime | None = None

    # ---- crop-diagnosis case state (VOICE_NOTE.md section 5) ----

    flow: str | None = None
    step: str | None = None
    current_case_id: UUID | None = None
    case_started_at: datetime | None = None
    last_activity_at: datetime | None = None
    case_crop: str | None = None
    case_part: str | None = None
    case_symptoms: list[str] = field(default_factory=list)
    questions_asked: int = 0
    diagnoses_in_case: int = 0

    # ---- farm-advice modules (additive, migration c3a7d2f1e9b0) ----

    # Answers collected so far in the open farm-advice flow; cleared when the
    # flow ends.
    flow_data: dict = field(default_factory=dict)
    # Farm facts kept between flows (crop, size, place, slope, soil ratings)
    # so the farmer is not asked twice. Consented users only.
    farm_profile: dict = field(default_factory=dict)
