"""Trader ledger: number reading, parsing, maths, statement and conversations.

N-ATLaS and the database are replaced by fakes, so these run offline.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.schemas.memory import User
from app.services import natlas
from app.trade import flow, ledger, money, parse, statement, strings

D = Decimal


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# money
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, values",
    [
        ("I sold 3 bags of rice at 52,000 naira each", ["3", "52000"]),
        ("sold 3bags rice 52k", ["3", "52000"]),
        ("fifty-two thousand naira", ["52000"]),
        ("one hundred and fifty thousand", ["150000"]),
        ("two and a half bags", ["2.5"]),
        ("I sold two bags at a thousand naira each", ["2", "1000"]),
        ("1.5m", ["1500000"]),
        ("N52,000", ["52000"]),
        ("I sold a bag of rice", []),
        ("twenty five thousand five hundred", ["25500"]),
    ],
)
def test_find_numbers(text, values):
    assert [str(n.value) for n in money.find_numbers(text)] == values


def test_number_words_and_formatting():
    assert money.number_words(152500) == "one hundred and fifty-two thousand five hundred"
    assert money.spoken_naira(1050) == "one thousand and fifty naira"
    assert money.naira(D("1250.5")) == "₦1,250.50"
    assert money.naira(-3000) == "-₦3,000"
    assert money.qty(D("2.500")) == "2.5"
    assert money.spoken_qty(D("2.5")) == "two and a half"


def test_was_said_only_accepts_spoken_numbers():
    text = "I sold three bags for one hundred and fifty thousand"
    assert money.was_said(D("150000"), text)
    assert money.was_said(D("3"), text)
    assert not money.was_said(D("50000"), text)


# ---------------------------------------------------------------------------
# parse (patterns)
# ---------------------------------------------------------------------------


def fields(a: parse.Action) -> dict:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in a.__dict__.items() if v not in (None, [])}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("I sold 3 bags of rice at 52,000 naira each", {"kind": "sale", "product": "rice", "unit": "bag", "qty": "3", "price_each": "52000"}),
        ("I sold three bags of rice for one hundred and fifty thousand naira", {"kind": "sale", "product": "rice", "unit": "bag", "qty": "3", "price_total": "150000"}),
        ("sold 2 cartons indomie 9500", {"kind": "sale", "product": "indomie", "unit": "carton", "qty": "2", "ambiguous_price": "9500"}),
        ("I sold a bag of beans at 45000", {"kind": "sale", "product": "beans", "unit": "bag", "qty": "1", "price_each": "45000"}),
        ("Add 10 bags of rice, cost 45,000, sell 52,000", {"kind": "restock", "product": "rice", "unit": "bag", "qty": "10", "cost_each": "45000", "set_price": "52000"}),
        ("I bought 20 cartons of milk at 12,000 each and I will sell at 14,500", {"kind": "restock", "product": "milk", "unit": "carton", "qty": "20", "cost_each": "12000", "set_price": "14500"}),
        ("restock 5 bags of garri, I paid 100,000 for all", {"kind": "restock", "product": "garri", "unit": "bag", "qty": "5", "cost_each": "20000.00"}),
        ("a customer bought 2 bags of rice at 52000 each", {"kind": "sale", "product": "rice", "unit": "bag", "qty": "2", "price_each": "52000"}),
        ("I sold palm oil 4 gallons 18000 each", {"kind": "sale", "product": "palm oil", "unit": "gallon", "qty": "4", "price_each": "18000"}),
    ],
)
def test_pattern_action(text, expected):
    assert fields(parse.pattern_action(text)) == expected


@pytest.mark.parametrize(
    "text, kind",
    [
        ("How much profit did I make today?", parse.TODAY),
        ("show me this week profit", parse.WEEK),
        ("I want my ledger for a loan", parse.EXPORT),
        ("what do I have in stock", parse.STOCK),
        ("my shop", parse.SHOP_MENU),
        ("my maize leaves are turning yellow", None),
    ],
)
def test_detect_kind(text, kind):
    assert parse.detect_kind(text) == kind


def test_yes_no():
    assert parse.is_yes("Yes") and parse.is_yes("ok save it") and parse.is_yes("e correct")
    assert parse.is_no("no") and parse.is_no("cancel") and not parse.is_yes("no")


def test_ground_drops_numbers_the_trader_did_not_say():
    text = "I sold three bags of rice for one hundred and fifty thousand"
    action = parse.ground(text, {"action": "sale", "product": "rice", "quantity": 3, "price_each": 50000, "price_total": 150000})
    assert action.qty == 3
    assert action.price_each is None  # 50,000 was never said: dropped
    assert action.price_total == 150000
    assert action.product == "rice"


def test_ground_drops_an_item_not_in_the_message():
    action = parse.ground("I sold 3 bags at 5000 each", {"action": "sale", "product": "cement", "quantity": 3})
    assert action.product is None


def test_match_product():
    catalogue = [
        ledger.Product(name="rice", unit="bag", cost_price=D(1), set_price=D(2), stock_qty=D(1), id=1),
        ledger.Product(name="palm oil", unit="gallon", cost_price=D(1), set_price=D(2), stock_qty=D(1), id=2),
        ledger.Product(name="groundnut oil", unit="gallon", cost_price=D(1), set_price=D(2), stock_qty=D(1), id=3),
    ]
    assert parse.match_product("bags of rice", catalogue)[0].id == 1
    assert parse.match_product("palm oil", catalogue)[0].id == 2
    product, candidates = parse.match_product("oil", catalogue)
    assert product is None and {p.id for p in candidates} == {2, 3}
    assert parse.match_product("cement", catalogue) == (None, [])


# ---------------------------------------------------------------------------
# ledger maths
# ---------------------------------------------------------------------------


def rice(stock: str = "10") -> ledger.Product:
    return ledger.Product(name="rice", unit="bag", cost_price=D("45000"), set_price=D("52000"), stock_qty=D(stock), id=1)


def test_sale_at_set_price():
    r = ledger.record_sale(rice(), D(3), D(52000))
    assert r.entry.amount == D("156000.00")
    assert r.entry.profit == D("21000.00")
    assert r.planned_profit == D("21000.00")
    assert r.price_vs_set == "as_set" and r.status == "profit"
    assert r.product.stock_qty == D("7.000")


def test_sale_with_discount():
    r = ledger.record_sale(rice(), D(2), D(50000))
    assert r.entry.profit == D("10000.00")
    assert r.entry.price_difference == D("-4000.00")
    assert r.price_vs_set == "discount"


def test_sale_with_markup():
    r = ledger.record_sale(rice(), D(1), D(53500))
    assert r.entry.price_difference == D("1500.00") and r.price_vs_set == "markup"


def test_sale_at_a_loss_and_low_stock():
    r = ledger.record_sale(rice("4"), D(2), D(44000))
    assert r.status == "loss" and r.entry.profit == D("-2000.00")
    assert r.low_stock and not r.out_of_stock


def test_oversold_never_goes_below_zero():
    r = ledger.record_sale(rice("1"), D(3), D(52000))
    assert r.oversold and r.product.stock_qty == 0 and r.out_of_stock


def test_bad_sale_numbers_rejected():
    with pytest.raises(ValueError):
        ledger.record_sale(rice(), D(0), D(52000))


def test_restock_averages_cost():
    r = ledger.record_restock(rice("10"), name="rice", unit="bag", qty=D(10), unit_cost=D(47000), set_price=None)
    assert r.product.stock_qty == D("20.000")
    assert r.product.cost_price == D("46000.00")  # (10 x 45,000 + 10 x 47,000) / 20
    assert r.cost_changed and r.entry.amount == D("470000.00")


def test_new_item_needs_cost_and_set_price():
    with pytest.raises(ValueError):
        ledger.record_restock(None, name="sugar", unit="bag", qty=D(1), unit_cost=D(70000), set_price=None)
    r = ledger.record_restock(None, name="sugar", unit="bag", qty=D(2), unit_cost=D(70000), set_price=D(78000))
    assert r.new_product and r.product.stock_qty == 2 and r.product.set_price == D("78000.00")


def test_summary():
    p = rice()
    sales = [
        ledger.record_sale(p, D(3), D(52000)).entry,
        ledger.record_sale(p, D(2), D(50000)).entry,
        ledger.record_sale(p, D(1), D(44000)).entry,
    ]
    restock = ledger.record_restock(p, name="rice", unit="bag", qty=D(5), unit_cost=D(45000), set_price=None).entry
    s = ledger.summarize(sales + [restock], [rice("2")], "today")
    assert s.sales_count == 3
    assert s.revenue == D("300000.00")
    assert s.cost_of_goods == D("270000.00")
    assert s.profit == D("30000.00")
    assert s.planned_profit == D("42000.00")
    assert s.gap_to_plan == D("-12000.00")
    assert s.discounts == D("12000.00")  # 2 x 2,000 + 1 x 8,000
    assert s.loss_sales == 1
    assert s.restock_spend == D("225000.00")
    assert [p.name for p in s.low_stock] == ["rice"]
    assert s.margin_percent == D("10.0")


def test_period_bounds_use_lagos_time():
    # 23:30 UTC on a Monday is 00:30 WAT Tuesday: "today" starts Tuesday 00:00 WAT.
    now = datetime(2026, 10, 12, 23, 30, tzinfo=timezone.utc)
    start, end, label = ledger.period_bounds("today", now)
    assert start == datetime(2026, 10, 12, 23, 0, tzinfo=timezone.utc) and label == "today"
    week_start, _, _ = ledger.period_bounds("week", now)
    assert week_start == datetime(2026, 10, 11, 23, 0, tzinfo=timezone.utc)  # Monday 00:00 WAT


# ---------------------------------------------------------------------------
# statement
# ---------------------------------------------------------------------------


def test_statement_pdf_is_well_formed():
    p = rice()
    when = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
    entries = []
    for i in range(60):
        e = ledger.record_sale(p, D(1), D(52000 - (i % 3) * 1000)).entry
        e.created_at = when + timedelta(days=i % 40, hours=i % 5)
        entries.append(e)
    pdf, ref = statement.build_pdf(trader_name="Tọ́lá Adé", phone="2348012345678", entries=entries, products=[p])
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    assert re.fullmatch(r"[0-9A-F]{10}", ref)
    # Cross-reference offsets point at the objects.
    xref_at = int(pdf.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    assert pdf[xref_at:xref_at + 4] == b"xref"
    count = int(pdf[xref_at:].split(b"\n")[1].split()[1])
    offsets = [int(line.split()[0]) for line in pdf[xref_at:].split(b"\n")[3:3 + count - 1]]
    for number, offset in enumerate(offsets, start=1):
        assert pdf[offset:].startswith(f"{number} 0 obj".encode())
    assert b"+234 *** *** 5678" in pdf  # phone is masked
    assert b"2348012345678" not in pdf
    assert b"Tola Ade" in pdf  # accents dropped for the PDF font
    assert b"/Count 2" in pdf or b"/Count 3" in pdf  # more than one page


# ---------------------------------------------------------------------------
# conversations (fake storage and N-ATLaS)
# ---------------------------------------------------------------------------


class FakeStorage:
    def __init__(self) -> None:
        self.products: dict[int, ledger.Product] = {}
        self.entries: list[ledger.Entry] = []
        self.next_id = 1
        self.saves = 0

    async def save_user(self, user: User) -> None:
        json.dumps(user.flow_data)  # state must stay JSON-safe
        self.saves += 1

    async def trade_products(self, wa_id: str) -> list[ledger.Product]:
        return [ledger.Product(**p.__dict__) for p in sorted(self.products.values(), key=lambda p: p.name)]

    async def trade_apply(self, wa_id: str, product: ledger.Product, entry: ledger.Entry) -> ledger.Entry:
        if product.id is None:
            key = parse.product_key(product.name)
            existing = next((p for p in self.products.values() if parse.product_key(p.name) == key), None)
            product.id = existing.id if existing else self.next_id
            if not existing:
                self.next_id += 1
        self.products[product.id] = ledger.Product(**product.__dict__)
        entry.product_id = product.id
        entry.created_at = datetime.now(timezone.utc)
        self.entries.append(entry)
        return entry

    async def trade_entries(self, wa_id: str, start=None, end=None) -> list[ledger.Entry]:
        return [e for e in self.entries if (start is None or e.created_at >= start) and (end is None or e.created_at < end)]


class FakeNatlas:
    def __init__(self) -> None:
        self.replies: list[dict] = []
        self.calls = 0

    async def chat(self, system_prompt: str, user_text: str, **kwargs) -> str:
        assert system_prompt == parse.EXTRACT_SYSTEM
        self.calls += 1
        return json.dumps(self.replies.pop(0) if self.replies else {"action": "other"})


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch):
    store = FakeStorage()
    fake = FakeNatlas()
    monkeypatch.setattr(flow, "storage", store)
    monkeypatch.setattr(natlas, "chat", fake.chat)
    user = User(phone="2348000000001", name="Tolu", state="active", language="english", role="trader", consent_status="granted")
    return store, fake, user


def bodies(reply) -> list[str]:
    out = []
    for m in reply.messages:
        if m.get("type") == "text":
            out.append(m["text"]["body"])
        elif m.get("type") == "interactive":
            out.append("[buttons]")
        elif m.get("type") == "document":
            out.append("[document]")
    return out


def test_add_then_sell_then_summary(env):
    store, fake, user = env
    r = run(flow.start(user, "Add 10 bags of rice, cost 45,000, sell 52,000"))
    assert "[buttons]" in bodies(r) and user.flow_data["awaiting"] == "confirm"
    assert "Cost price: ₦45,000 each" in bodies(r)[1]
    assert not store.entries  # nothing saved before yes
    r = run(flow.continue_flow(user, "yes"))
    assert store.products[1].stock_qty == 10 and len(store.entries) == 1
    assert user.flow is None

    r = run(flow.start(user, "I sold 3 bags of rice at 50,000 naira each"))
    assert "Total: ₦150,000" in bodies(r)[1]
    r = run(flow.handle_choice(user, "trade_yes"))
    spoken, card = bodies(r)
    assert "fifteen thousand naira profit" in spoken
    assert "six thousand naira below your set price" in spoken
    assert "Profit: ₦15,000" in card and "Discount given: ₦6,000" in card
    assert "Stock left: 7 bags" in card
    assert store.products[1].stock_qty == 7
    assert fake.calls == 0  # clear messages need no model call

    r = run(flow.handle_choice(user, "trade_today"))
    spoken, card = bodies(r)
    assert "sold one hundred and fifty thousand naira and made fifteen thousand naira profit" in spoken
    assert "You made ₦6,000 less than planned" in card


def test_no_cancels_and_saves_nothing(env):
    store, _, user = env
    run(flow.start(user, "Add 10 bags of rice, cost 45,000, sell 52,000"))
    r = run(flow.continue_flow(user, "no"))
    assert not store.entries and user.flow is None
    assert "did not save" in bodies(r)[0]


def test_missing_fields_are_asked_one_at_a_time(env):
    store, _, user = env
    store.products[1] = rice()
    store.next_id = 2
    r = run(flow.handle_choice(user, "trade_sale"))
    assert user.flow_data["awaiting"] == "sale_details"
    r = run(flow.continue_flow(user, "rice"))
    assert "How many bags of rice did you sell?" in bodies(r)[0]
    r = run(flow.continue_flow(user, "two"))
    assert "How much did you sell one bag of rice for?" in bodies(r)[0]
    r = run(flow.continue_flow(user, "fifty-two thousand"))
    assert "Total: ₦104,000" in bodies(r)[1]
    run(flow.continue_flow(user, "yes"))
    assert store.entries[-1].amount == D("104000.00")


def test_ambiguous_price_asks_each_or_total(env):
    store, _, user = env
    store.products[1] = rice()
    store.next_id = 2
    r = run(flow.start(user, "sold 3 rice 150000"))
    assert "Is ₦150,000 the price for one, or for all 3?" in bodies(r)[0]
    r = run(flow.continue_flow(user, "for all of them"))
    assert "Price: ₦50,000 each" in bodies(r)[1]


def test_sale_of_a_new_item_asks_its_cost(env):
    store, _, user = env
    r = run(flow.start(user, "I sold 2 cartons of indomie at 9,500 each"))
    assert "I do not have indomie in your goods yet" in bodies(r)[0]
    r = run(flow.continue_flow(user, "8000"))
    assert "New item. Cost: ₦8,000 each" in bodies(r)[1]
    r = run(flow.continue_flow(user, "yes"))
    assert store.entries[-1].profit == D("3000.00")
    assert "sold more indomie than I had" not in bodies(r)[0]


def test_loss_is_flagged(env):
    store, _, user = env
    store.products[1] = rice()
    store.next_id = 2
    run(flow.start(user, "I sold a bag of rice at 40,000"))
    r = run(flow.continue_flow(user, "yes"))
    spoken, card = bodies(r)
    assert "you lost five thousand naira" in spoken
    assert "LOSS: ₦5,000" in card


def test_model_fills_gaps_but_never_invents_numbers(env):
    store, fake, user = env
    store.products[1] = rice()
    store.next_id = 2
    # Long, loosely worded message: patterns miss the price; N-ATLaS reads it,
    # but one value it returns (51,000) was never said and is dropped.
    fake.replies = [{"action": "sale", "product": "rice", "quantity": 2, "price_each": 51000, "price_total": 104000}]
    text = "my customer came this morning and she collected two of the rice and paid me 104,000 altogether for both"
    r = run(flow.start(user, text))
    assert fake.calls == 1
    assert "Price: ₦52,000 each" in bodies(r)[1]  # 104,000 / 2, worked out in code


def test_a_new_instruction_replaces_the_open_question(env):
    store, _, user = env
    store.products[1] = rice()
    store.next_id = 2
    run(flow.start(user, "I sold rice"))
    assert user.flow_data["awaiting"] == "qty"
    r = run(flow.continue_flow(user, "how much profit did I make today"))
    assert user.flow is None
    assert "No sales recorded today yet." in bodies(r)[0]


def test_unrelated_message_is_handed_back(env):
    store, _, user = env
    store.products[1] = rice()
    run(flow.start(user, "I sold 2 bags of rice at 52000 each"))
    assert run(flow.continue_flow(user, "what is the weather")) is not None  # one reminder
    assert run(flow.continue_flow(user, "what is the weather")) is None  # then hand back
    assert not store.entries and user.flow is None


def test_stock_list_and_export(env):
    store, _, user = env
    store.products[1] = rice("2")
    store.next_id = 2
    r = run(flow.handle_choice(user, "trade_stock"))
    assert "Running low or finished: rice" in bodies(r)[0]
    assert "1. rice: 2 bags (low), cost ₦45,000, sell ₦52,000" in bodies(r)[1]

    r = run(flow.handle_choice(user, "trade_export"))
    assert bodies(r) == [strings.EXPORT_EMPTY]
    run(flow.start(user, "I sold 1 bag of rice at 52,000"))
    run(flow.continue_flow(user, "yes"))
    r = run(flow.handle_choice(user, "trade_export"))
    doc = r.messages[1]["document"]
    assert doc["bytes"].startswith(b"%PDF") and doc["filename"].endswith(".pdf")


def test_no_ledger_without_consent(env):
    store, _, user = env
    user.consent_status = "declined"
    r = run(flow.start(user, "I sold 2 bags of rice at 52000 each"))
    assert bodies(r) == [strings.NEED_CONSENT]
    assert not store.entries and store.saves == 0


def test_looks_like_trade_by_role():
    trader = User(phone="1", role="trader")
    farmer = User(phone="2", role="farmer")
    assert flow.looks_like_trade("today's profit", trader)
    assert not flow.looks_like_trade("today's profit", farmer)
    assert flow.looks_like_trade("I sold 5 bags of maize at 30,000 each", farmer)
    assert flow.looks_like_trade("my shop", farmer)
    assert not flow.looks_like_trade("my maize leaves are yellow", trader)
    assert not flow.looks_like_trade("how do I get a loan for fertilizer", farmer)


def test_trader_menu_rows_fit_whatsapp_limits():
    from app.onboarding.messages import TRADER_MENU_MESSAGES, service_menu

    for language, msgs in TRADER_MENU_MESSAGES.items():
        assert len(msgs["rows"]) <= 10
        for row_id, title, description in msgs["rows"]:
            assert row_id in flow.CHOICES
            assert 1 <= len(title) <= 24, (language, title)
            assert len(description) <= 72, (language, description)
        assert len(msgs["button"]) <= 20
    trader_menu = service_menu("hausa", name="Musa", role="trader")
    rows = trader_menu["interactive"]["action"]["sections"][0]["rows"]
    assert rows[0]["id"] == "trade_sale"
    farm_menu = service_menu("hausa", name="Musa", role="farmer")
    assert farm_menu["interactive"]["action"]["sections"][0]["rows"][0]["id"] == "crop_detection"
    assert service_menu("english", role="farmer", kind="trade")["interactive"]["action"]["sections"][0]["rows"][0]["id"] == "trade_sale"


def test_confirm_button_titles_fit():
    for language in ("english", "hausa", "yoruba", "igbo", "pidgin"):
        payload = strings.confirm_buttons(language)
        for button in payload["interactive"]["action"]["buttons"]:
            assert 1 <= len(button["reply"]["title"]) <= 20
