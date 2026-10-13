"""Trader ledger through the WhatsApp pipeline (handle_message), every
network call and the database faked."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.schemas.memory import User
from app.schemas.webhook import Message
from app.storage import OpenCase
from app.trade.test_trade import FakeStorage


@pytest.fixture()
def pipeline(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "test-id")
    from app.services import brain, natlas
    from app.services.advisor import engine
    from app.services.whatsapp import messages
    from app.trade import flow

    user = User(phone="2348000000000", name="Tolu", state="active", language="pidgin", role="trader", consent_status="granted")

    class Store(FakeStorage):
        async def get_user(self, phone):
            return user

        async def message_seen(self, mid):
            return False

        async def close_case(self, phone):
            pass

        async def set_user_last_activity(self, phone):
            pass

        async def get_case(self, phone):
            return OpenCase.empty()

    store = Store()
    for module in (messages, brain, engine, flow):
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

    async def send_document(to, data, filename, caption=None, mime_type="application/pdf"):
        sent.append(("document", (filename, data[:4], caption)))
        return True

    async def no_tts(text, language):
        return None

    async def translate_for_speech(text, language):
        return f"[{language}] {text}", language

    english: dict[str, str] = {}

    async def translate_to_english(text):
        return {"english": english.get(text, text)}

    async def fake_chat(system_prompt, user_text, **kwargs):
        return json.dumps({"intent": "unclear"})

    monkeypatch.setattr(messages, "send_text", send_text)
    monkeypatch.setattr(messages, "_send_interactive", send_interactive)
    monkeypatch.setattr(messages, "send_document", send_document)
    monkeypatch.setattr(messages, "text_to_speech", no_tts)
    monkeypatch.setattr(messages, "translate_for_speech", translate_for_speech)
    monkeypatch.setattr(messages, "translate_to_english", translate_to_english)
    monkeypatch.setattr(natlas, "chat", fake_chat)
    return messages, user, store, sent, english


def _msg(**fields) -> Message:
    return Message.model_validate({"from": "2348000000000", "id": "wamid.1", **fields})


def _tap(row_id: str, title: str = "x") -> Message:
    return _msg(type="interactive", interactive={"type": "button_reply", "button_reply": {"id": row_id, "title": title}})


def test_pidgin_sale_by_text_is_read_back_then_saved(pipeline):
    messages, user, store, sent, english = pipeline
    asyncio.run(messages.handle_message(_tap("trade_add"), "Tolu"))
    english["I add 10 bag rice, cost 45k, I go sell 52k"] = "Add 10 bags of rice, cost 45k, I will sell at 52k"
    asyncio.run(messages.handle_message(_msg(type="text", text={"body": "I add 10 bag rice, cost 45k, I go sell 52k"}), "Tolu"))
    asyncio.run(messages.handle_message(_tap("trade_yes"), "Tolu"))
    assert store.products[1].stock_qty == 10

    sent.clear()
    english["I don sell 3 bag rice 52k each"] = "I sold 3 bags of rice at 52k each"
    asyncio.run(messages.handle_message(_msg(type="text", text={"body": "I don sell 3 bag rice 52k each"}), "Tolu"))
    kinds = [k for k, _ in sent]
    assert kinds == ["text", "text", "interactive"]  # spoken read-back, card, Yes/No
    assert "Total: ₦156,000" in sent[1][1]
    buttons = sent[2][1]["interactive"]["action"]["buttons"]
    assert [b["reply"]["title"] for b in buttons] == ["Yes, save am", "No, cancel"]

    sent.clear()
    asyncio.run(messages.handle_message(_tap("trade_yes"), "Tolu"))
    assert "Profit: ₦21,000" in sent[1][1]
    assert store.products[1].stock_qty == 7


def test_trader_gets_the_trader_menu(pipeline):
    messages, user, store, sent, _ = pipeline
    asyncio.run(messages.handle_message(_msg(type="text", text={"body": "hello"}), "Tolu"))
    menu = [p for k, p in sent if k == "interactive"][-1]
    rows = menu["interactive"]["action"]["sections"][0]["rows"]
    assert rows[0]["id"] == "trade_sale" and rows[-1]["id"] == "farm_menu"

    sent.clear()
    asyncio.run(messages.handle_message(_tap("farm_menu"), "Tolu"))
    rows = sent[-1][1]["interactive"]["action"]["sections"][0]["rows"]
    assert rows[0]["id"] == "crop_detection"


def test_statement_is_sent_as_a_document(pipeline):
    messages, user, store, sent, english = pipeline
    asyncio.run(messages.handle_message(_msg(type="text", text={"body": "I sold 1 bag of beans at 40,000"}), "Tolu"))
    asyncio.run(messages.handle_message(_msg(type="text", text={"body": "30000"}), "Tolu"))  # its cost
    asyncio.run(messages.handle_message(_tap("trade_yes"), "Tolu"))
    sent.clear()
    asyncio.run(messages.handle_message(_tap("trade_export"), "Tolu"))
    docs = [p for k, p in sent if k == "document"]
    assert docs and docs[0][1] == b"%PDF" and docs[0][0].endswith(".pdf")
    assert docs[0][2].startswith("AjaTrade sales record (")
