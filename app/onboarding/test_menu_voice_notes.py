"""Tests for the new voice notes and the active-user dispatch fix.

Covers TODO_new_voice_notes.md step 3 (wiring diagnosis_prompt and
feature_coming_soon into the menu option handlers) and the process()
dispatch fix that lets an open flow reach the main pipeline.

No network access: the audio store is never sent to, and the state machine
runs against a fake storage.
"""

from __future__ import annotations

import asyncio

import pytest

from app.onboarding.onboarding_send import voice_script_for_step
from app.onboarding.state_machine import OnboardingStateMachine, _script_text
from app.schemas.memory import User
from app.voice.scripts import FEATURE_COMING_SOON


@pytest.fixture(autouse=True)
def _whatsapp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # audio_store imports the WhatsApp send helper, which reads env vars at
    # import time; harmless values keep imports (and asserts) deterministic.
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "test-id")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("SPITCH_API_KEY", "test-key")


class FakeStorage:
    """Minimal async storage stand-in for the state machine."""

    def __init__(self, user: User | None) -> None:
        self.user = user
        self.saves = 0

    async def get_user(self, phone: str):
        return self.user

    async def save_user(self, user):
        self.saves += 1
        self.user = user


# ---------------------------------------------------------------------------
# voice_script_for_step: which cached script plays with the menu reply
# ---------------------------------------------------------------------------


def test_voice_script_for_menu_option_1() -> None:
    assert voice_script_for_step("active", "crop_detection") == "diagnosis_prompt"


def test_voice_script_for_unbuilt_options() -> None:
    assert voice_script_for_step("active", "market_price") == "feature_coming_soon"
    assert voice_script_for_step("active", "connect_vendor") == "feature_coming_soon"


def test_voice_script_none_for_other_answers_and_states() -> None:
    assert voice_script_for_step("active", "hello") is None
    assert voice_script_for_step("active", None) is None
    # Not an active-user menu tap: no script from this mapping.
    assert voice_script_for_step("awaiting_language", "crop_detection") is None


# ---------------------------------------------------------------------------
# handle_active: option 1 starts the flow, unbuilt options change nothing
# ---------------------------------------------------------------------------


def test_option_1_starts_crop_detection_flow_and_replies() -> None:
    storage = FakeStorage(None)
    machine = OnboardingStateMachine(storage)
    user = User(phone="123", state="active", language="english")

    reply = asyncio.run(machine.handle_active(user, "crop_detection"))

    assert user.flow == "crop_detection"
    assert storage.saves == 1
    assert reply["type"] == "text"
    assert reply["text"]["body"] == _script_text("diagnosis_prompt", "english")


@pytest.mark.parametrize("answer", ["market_price", "connect_vendor"])
def test_unbuilt_options_change_no_state(answer: str) -> None:
    storage = FakeStorage(None)
    machine = OnboardingStateMachine(storage)
    user = User(phone="123", state="active", language="english")

    reply = asyncio.run(machine.handle_active(user, answer))

    assert user.flow is None
    assert user.state == "active"
    assert storage.saves == 0  # nothing persisted: state unchanged
    assert reply["type"] == "text"
    assert reply["text"]["body"] == FEATURE_COMING_SOON["english"]


def test_ordinary_text_is_left_to_main_pipeline() -> None:
    storage = FakeStorage(None)
    machine = OnboardingStateMachine(storage)
    user = User(phone="123", state="active", language="english")

    reply = asyncio.run(machine.handle_active(user, "my maize is yellowing"))

    assert reply is None
    assert user.flow is None  # raw text must never become a flow
    assert storage.saves == 0


# ---------------------------------------------------------------------------
# please_wait: fired once, in the background, when photo/voice arrives
# ---------------------------------------------------------------------------


def test_schedule_wait_notice_fires_in_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_schedule_wait_notice must not block: the send happens as a task, once
    per request, and carries the user's stored language."""
    import app.voice.audio_store as audio_store_mod
    from app.services.whatsapp.messages import _schedule_wait_notice

    calls: list[tuple[str, str, str]] = []

    class FakeStore:
        async def send_voice_script(self, phone, script_id, language):
            calls.append((phone, script_id, language))
            return True

    monkeypatch.setattr(audio_store_mod, "default_store", FakeStore())

    async def run() -> None:
        user = User(phone="123", language="hausa")
        _schedule_wait_notice(user)
        # Not awaited: the task runs on the next loop tick, after this
        # coroutine yields — the diagnosis work is never delayed by it.
        assert calls == []
        await asyncio.sleep(0)
        assert calls == [("123", "please_wait", "hausa")]

    asyncio.run(run())


def test_schedule_wait_notice_skips_when_user_is_unknown() -> None:
    from app.services.whatsapp.messages import _schedule_wait_notice

    _schedule_wait_notice(None)  # must not raise, must not send


# ---------------------------------------------------------------------------
# process() dispatch: open flow goes to the main pipeline, declined restarts
# ---------------------------------------------------------------------------


def test_active_user_with_open_flow_is_handed_to_main_pipeline() -> None:
    user = User(phone="123", state="active", language="english", flow="crop_detection")
    storage = FakeStorage(user)
    machine = OnboardingStateMachine(storage)

    reply = asyncio.run(
        machine.process("123", {"text": {"body": "the leaves have spots"}})
    )

    assert reply is None  # webhook falls through to handle_message
    assert user.state == "active"  # no onboarding restart
    assert user.flow == "crop_detection"  # flow untouched


def test_menu_tap_through_process_starts_the_flow() -> None:
    user = User(phone="123", state="active", language="english")
    storage = FakeStorage(user)
    machine = OnboardingStateMachine(storage)

    reply = asyncio.run(
        machine.process(
            "123", {"interactive": {"list_reply": {"id": "crop_detection"}}}
        )
    )

    assert user.flow == "crop_detection"
    assert reply is not None and reply["type"] == "text"


def test_declined_user_reaches_the_declined_handler() -> None:
    """A declined user must not be swallowed by handle_active (the bug the
    dispatch fix addresses): their messages reach handle_declined.

    Note: the "yes" branch of handle_declined currently crashes inside
    language_question() (undefined LANGUAGE_OPTIONS) — a pre-existing bug
    reported to the owner, so this asserts the non-crashing branch.
    """
    from app.onboarding.messages import restart_message

    user = User(phone="123", state="declined", consent_status="declined")
    storage = FakeStorage(user)
    machine = OnboardingStateMachine(storage)

    reply = asyncio.run(machine.process("123", {"text": {"body": "hello"}}))

    assert reply == restart_message("en")
    assert user.state == "declined"  # nothing changes until they say yes
