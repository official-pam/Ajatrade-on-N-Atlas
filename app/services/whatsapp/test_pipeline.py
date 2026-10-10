"""WhatsApp pipeline tests: menu taps, location pins, typed local-language
text and reply sending, with every network call faked."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.schemas.memory import User
from app.schemas.webhook import Message
from app.storage import OpenCase


@pytest.fixture()
def pipeline(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "test-id")
    from app.services import brain, natlas
    from app.services.advisor import compose, engine, extract
    from app.services.whatsapp import messages

    user = User(phone="2348000000000", state="active", language="hausa", consent_status="granted", onboarding=True)

    class Store:
        logs: list = []

        async def get_user(self, phone):
            return user

        async def save_user(self, u):
            pass

        async def message_seen(self, mid):
            return False

        async def close_case(self, phone):
            pass

        async def set_user_last_activity(self, phone):
            pass

        async def get_case(self, phone):
            return OpenCase.empty()

        async def log_diagnosis(self, **kw):
            self.logs.append(kw)

    store = Store()
    for module in (messages, brain, engine):
        monkeypatch.setattr(module, "storage", store)

    async def record_message(*args, **kwargs):
        return None

    monkeypatch.setattr(messages, "record_message", record_message)

    sent: list[tuple[str, object]] = []

    async def send_text(to, body):
        sent.append(("text", body))
        return True

    async def send_interactive(to, payload):
        sent.append(("interactive", payload))
        return True

    async def no_tts(text, language):
        return None

    translations: dict[str, str] = {}

    async def translate_for_speech(text, language):
        return translations.get(text, f"[{language}] {text}"), language

    async def translate_to_english(text):
        return {"language": "hausa", "english": "how much fertilizer for my maize", "is_mixed": False, "unclear": False}

    monkeypatch.setattr(messages, "send_text", send_text)
    monkeypatch.setattr(messages, "_send_interactive", send_interactive)
    monkeypatch.setattr(messages, "text_to_speech", no_tts)
    monkeypatch.setattr(messages, "translate_for_speech", translate_for_speech)
    monkeypatch.setattr(messages, "translate_to_english", translate_to_english)

    async def fake_chat(system_prompt, user_text, **kwargs):
        if system_prompt == extract.EXTRACT_SYSTEM:
            return json.dumps({"facts": {}})
        if system_prompt == compose.SYSTEM:
            return "Here is your plan."
        return "{}"

    monkeypatch.setattr(natlas, "chat", fake_chat)
    return messages, user, sent, translations


def _msg(**fields) -> Message:
    return Message.model_validate({"from": "2348000000000", "id": "wamid.1", **fields})


def test_menu_tap_starts_farm_advice_flow(pipeline):
    messages, user, sent, _ = pipeline
    msg = _msg(type="interactive", interactive={"type": "list_reply", "list_reply": {"id": "fertilizer", "title": "Shirin taki"}})
    asyncio.run(messages.handle_message(msg, "Musa"))
    assert user.flow == "fertilizer"
    assert sent[0][0] == "text" and sent[0][1].startswith("[hausa] I will make a fertilizer plan")


def test_typed_hausa_is_translated_then_routed(pipeline):
    messages, user, sent, _ = pipeline
    msg = _msg(type="text", text={"body": "taki nawa zan sa wa masara ta"})
    asyncio.run(messages.handle_message(msg, "Musa"))
    # keyword intent "fertilizer" from the English translation starts the flow
    assert user.flow == "fertilizer"


def test_location_pin_outside_flow_is_saved_and_menu_sent(pipeline):
    messages, user, sent, _ = pipeline
    msg = _msg(type="location", location={"latitude": 12.0, "longitude": 8.5, "name": "Gona", "address": "Kano"})
    asyncio.run(messages.handle_message(msg, "Musa"))
    assert user.farm_profile["location"]["state"] == "kano"
    kinds = [k for k, _ in sent]
    assert kinds == ["text", "interactive"]
    rows = sent[1][1]["interactive"]["action"]["sections"][0]["rows"]
    assert len(rows) == 10 and rows[1]["id"] == "irrigation"
    assert rows[3]["title"] == "Shirin taki"  # Hausa menu


def test_card_keeps_english_when_translation_changes_numbers(pipeline):
    messages, user, sent, translations = pipeline
    from app.services.brain import RoutedReply

    card = "NPK 15-15-15: 400 kg (8 bags)"
    translations[card] = "NPK 15-15-15: 40 kg (8 buhu)"  # a number was changed
    reply = RoutedReply(messages=[{"type": "text", "text": {"body": card}, "voice": False}])
    asyncio.run(messages._send_routed_reply(user.phone, user, reply, "hausa"))
    assert sent == [("text", card)]

    sent.clear()
    translations[card] = "NPK 15-15-15: 400 kg (buhu 8)"
    asyncio.run(messages._send_routed_reply(user.phone, user, reply, "hausa"))
    assert sent == [("text", "NPK 15-15-15: 400 kg (buhu 8)")]


def test_pidgin_menu_and_language_normalised(pipeline):
    messages, user, sent, _ = pipeline
    payload = messages._menu_payload("nigerian pidgin", None)
    assert payload["interactive"]["body"]["text"].startswith("Hello, I be AjaTrade")
