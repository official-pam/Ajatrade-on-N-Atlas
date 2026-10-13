"""Conversation for the trader ledger.

One open action at a time per user. State lives on the user row:
``flow == "trade"`` and ``flow_data`` holds the action being built
(``pending``), the question just asked (``awaiting``) and how many times it
was asked.

Every sale or restock is read back with Yes / No buttons and saved only
after the trader says yes. The numbers in the read-back are the trader's
own; the profit, discount, stock and summary figures come from
:mod:`app.trade.ledger`.

Brain module: no WhatsApp imports. Replies are English ``RoutedReply``
objects; the reply pipeline translates and voices them.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.schemas.memory import User
from app.storage import mask_phone, storage
from app.trade import ledger, money, parse, strings
from app.trade.parse import Action

logger = logging.getLogger("trade")

FLOW = "trade"
TRADER_ROLES = ("trader", "manufacturer")
MAX_ASKS = 2

CHOICES = (
    "trade_sale",
    "trade_add",
    "trade_stock",
    "trade_today",
    "trade_week",
    "trade_export",
    "trade_help",
    "trade_yes",
    "trade_no",
    "farm_menu",
    "shop_menu",
)


def _reply(*args: Any, **kwargs: Any):
    from app.services.brain import RoutedReply

    return RoutedReply(*args, **kwargs)


def _text(body: str) -> dict[str, Any]:
    return {"type": "text", "text": {"body": body}}


def _card(body: str) -> dict[str, Any]:
    """Text-only message (no voice note): exact numbers, checked after translation."""
    return {"type": "text", "text": {"body": body}, "voice": False}


def is_trade_flow(flow: str | None) -> bool:
    return flow == FLOW


def is_trader(user: User | None) -> bool:
    return (getattr(user, "role", None) or "").lower() in TRADER_ROLES


def looks_like_trade(text: str, user: User | None) -> bool:
    """Should a message with no open flow go to the ledger?

    Clear ledger words ("I sold 3 ...", "restock", "my ledger") count for
    everyone. Traders also get "today's profit", "add 10 bags ...", etc. A
    farmer's "I sold 5 bags of maize at 30,000" is a sale too.
    """
    kind = parse.detect_kind(text)
    if kind is None:
        return False
    if parse.strong_trade_words(text):
        return True
    has_amount = bool(money.find_numbers(text)) or bool(parse._A_UNIT.search(text))
    if is_trader(user):
        # "my customers are not buying" is a chat, not a sale to record.
        return kind not in (parse.SALE, parse.RESTOCK) or has_amount
    return kind == parse.SALE and bool(money.find_numbers(text))


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _plural(unit: str | None, qty: Decimal | None) -> str:
    return money.plural_unit(unit, qty)


def _unit_one(unit: str | None) -> str:
    return unit or "one"


def _spoken_unit(unit: str | None, qty: Decimal) -> str:
    return _plural(unit, qty) if unit else ""


def _clean_name(text: str) -> str | None:
    name, _unit = parse._product_and_unit(text, money.find_numbers(text))
    return name


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def _state(user: User) -> dict[str, Any]:
    if not isinstance(user.flow_data, dict):
        user.flow_data = {}
    return user.flow_data


def _end(user: User) -> None:
    user.flow = None
    user.step = None
    user.flow_data = {}


async def _save(user: User) -> None:
    try:
        await storage.save_user(user)
    except Exception as error:  # noqa: BLE001 - a failed save only costs a repeat question
        logger.warning("Could not save trade state for %s: %s", mask_phone(user.phone), error)


async def _catalogue(user: User) -> list[ledger.Product]:
    try:
        return await storage.trade_products(user.phone)
    except Exception as error:  # noqa: BLE001
        logger.warning("Could not load goods for %s: %s", mask_phone(user.phone), error)
        return []


def _consented(user: User) -> bool:
    return getattr(user, "consent_status", None) == "granted"


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def handle_choice(user: User, choice_id: str) -> Any:
    """A tap on the trader menu or on Yes / No under a read-back."""
    if choice_id in ("farm_menu",):
        _end(user)
        await _save(user)
        return _reply(messages=[], show_menu=True, menu="farm")
    if choice_id == "shop_menu":
        return _reply(messages=[], show_menu=True, menu="trade")
    if not _consented(user):
        return _reply(messages=[_text(strings.NEED_CONSENT)], english_text=strings.NEED_CONSENT)
    if choice_id == "trade_yes":
        return await _commit(user)
    if choice_id == "trade_no":
        return await _cancel(user)
    if choice_id == "trade_sale":
        return await _open(user, "sale_details", strings.ASK_SALE, strings.ASK_SALE_CARD)
    if choice_id == "trade_add":
        return await _open(user, "add_details", strings.ASK_ADD, strings.ASK_ADD_CARD)
    if choice_id == "trade_stock":
        return await _stock(user)
    if choice_id == "trade_today":
        return await _summary(user, "today")
    if choice_id == "trade_week":
        return await _summary(user, "week")
    if choice_id == "trade_export":
        return await _export(user)
    return _help()


async def start(user: User, english_text: str) -> Any:
    """A ledger message with no open trade flow."""
    if not _consented(user):
        return _reply(messages=[_text(strings.NEED_CONSENT)], english_text=strings.NEED_CONSENT)
    action = await parse.read_action(english_text)
    return await _dispatch(user, action, english_text)


async def continue_flow(user: User, english_text: str) -> Any | None:
    """Continue the open trade flow. ``None`` hands the message back to the router."""
    if not is_trade_flow(user.flow):
        return None
    if not _consented(user):
        _end(user)
        await _save(user)
        return None
    state = _state(user)
    awaiting = state.get("awaiting")
    text = (english_text or "").strip()
    pending = Action.from_json(state.get("pending")) if state.get("pending") else None

    if awaiting == "confirm":
        if parse.is_yes(text):
            return await _commit(user)
        if parse.is_no(text):
            return await _cancel(user)

    if parse.is_no(text) and awaiting and awaiting not in ("sale_details", "add_details"):
        return await _cancel(user)

    # A new instruction ("I sold 2 bags of beans at 40,000", "today's
    # profit") replaces whatever was open. Fields it leaves out are taken
    # from the open action when it is the same kind of action.
    kind = parse.detect_kind(text)
    if kind is not None and (kind not in (parse.SALE, parse.RESTOCK) or money.find_numbers(text)):
        action = await parse.read_action(text)
        if action.kind is not None:
            if pending is not None and action.kind == pending.kind and awaiting != "confirm":
                action = parse.merge(action, pending)
            return await _dispatch(user, action, text)

    if awaiting in ("sale_details", "add_details"):
        hint = parse.SALE if awaiting == "sale_details" else parse.RESTOCK
        action = await parse.read_action(text, hint)
        if action.kind in (None, hint) and (action.product or action.qty is not None):
            action.kind = hint
            return await _dispatch(user, action, text)
        return await _reask(user, strings.NOT_UNDERSTOOD)

    if awaiting == "confirm":
        if pending is not None and int(state.get("asks") or 0) < 1:
            state["asks"] = 1
            return await _show_confirm(user, pending, reminder=True)
        # Still no yes or no: leave it unsaved and let the router answer.
        _end(user)
        await _save(user)
        return None

    if pending is not None and awaiting:
        filled = _fill(pending, awaiting, text, state)
        if filled is None:
            return await _reask(user, None)
        return await _dispatch(user, filled, text)

    # Nothing we understand: hand back to the router.
    _end(user)
    await _save(user)
    return None


# ---------------------------------------------------------------------------
# Building an action
# ---------------------------------------------------------------------------


async def _open(user: User, awaiting: str, spoken: str, card: str) -> Any:
    user.flow = FLOW
    user.flow_data = {"pending": None, "awaiting": awaiting, "asks": 1, "question": spoken}
    await _save(user)
    return _reply(messages=[_text(spoken), _card(card)], english_text=spoken)


async def _dispatch(user: User, action: Action, text: str) -> Any:
    kind = action.kind
    if kind == parse.STOCK:
        return await _stock(user)
    if kind == parse.TODAY:
        return await _summary(user, "today")
    if kind == parse.WEEK:
        return await _summary(user, "week")
    if kind == parse.EXPORT:
        return await _export(user)
    if kind == parse.HELP:
        return _help()
    if kind == parse.SHOP_MENU:
        _end(user)
        await _save(user)
        return _reply(messages=[], show_menu=True, menu="trade")
    if kind not in (parse.SALE, parse.RESTOCK):
        return await _reask(user, strings.NOT_UNDERSTOOD)
    return await _next(user, action)


async def _next(user: User, action: Action) -> Any:
    """Ask for the next missing fact, or read the action back for confirmation."""
    state = _state(user)
    user.flow = FLOW

    if not action.product:
        return await _ask(user, action, "product", strings.ASK_PRODUCT)

    catalogue = await _catalogue(user)
    product, candidates = parse.match_product(action.product, catalogue)
    if product is None and candidates:
        names = [p.name for p in candidates]
        state["options"] = names
        return await _ask(user, action, "which", strings.ASK_WHICH.format(options=", ".join(names[:-1]) + (" or " if len(names) > 1 else "") + names[-1]))
    if product is not None:
        action.product = product.name
        action.unit = action.unit or product.unit
    unit = action.unit
    name = action.product

    if action.qty is None or action.qty <= 0:
        question = strings.ASK_QTY_SALE if action.kind == parse.SALE else strings.ASK_QTY_ADD
        return await _ask(user, action, "qty", question.format(unit=_plural(unit, None) if unit else "units", product=name))

    if action.kind == parse.SALE:
        if action.price_each is None and action.price_total is not None:
            action.price_each = ledger.money(action.price_total / action.qty)
            action.price_total = None
        if action.price_each is None and action.ambiguous_price is not None:
            if action.qty == 1:
                action.price_each, action.ambiguous_price = action.ambiguous_price, None
            else:
                return await _ask(
                    user,
                    action,
                    "each_or_total",
                    strings.ASK_EACH_OR_TOTAL.format(price=money.naira(action.ambiguous_price), qty=money.qty(action.qty)),
                )
        if action.price_each is None:
            return await _ask(user, action, "price", strings.ASK_PRICE.format(unit_one=_unit_one(unit), product=name))
        if product is None and action.cost_each is None:
            return await _ask(user, action, "cost", strings.ASK_COST_NEW.format(product=name, unit_one=_unit_one(unit)))
    else:
        if product is None and action.cost_each is None:
            return await _ask(user, action, "cost", strings.ASK_COST_NEW.format(product=name, unit_one=_unit_one(unit)))
        if product is None and action.set_price is None:
            return await _ask(user, action, "set", strings.ASK_SET_NEW.format(product=name, unit_one=_unit_one(unit)))

    return await _show_confirm(user, action, product=product)


async def _ask(user: User, action: Action, key: str, question: str) -> Any:
    state = _state(user)
    if state.get("awaiting") == key and state.get("pending"):
        asks = int(state.get("asks") or 0) + 1
    else:
        asks = 1
    if asks > MAX_ASKS:
        _end(user)
        await _save(user)
        msg = "Sorry, I still did not get that, so I did not save anything. " + strings.MENU_HINT
        return _reply(messages=[_text(msg)], english_text=msg)
    prefix = "Sorry, I did not get that. " if asks > 1 else ""
    user.flow = FLOW
    user.flow_data = {**state, "pending": action.to_json(), "awaiting": key, "asks": asks, "question": question}
    await _save(user)
    body = prefix + question
    return _reply(messages=[_text(body)], english_text=body)


async def _reask(user: User, message: str | None) -> Any:
    state = _state(user)
    asks = int(state.get("asks") or 0) + 1
    if asks > MAX_ASKS:
        _end(user)
        await _save(user)
        msg = "Sorry, I still did not get that, so I did not save anything. " + strings.MENU_HINT
        return _reply(messages=[_text(msg)], english_text=msg, show_menu=False)
    state["asks"] = asks
    user.flow = FLOW
    await _save(user)
    body = message or ("Sorry, I did not get that. " + (state.get("question") or strings.NOT_UNDERSTOOD))
    return _reply(messages=[_text(body)], english_text=body)


_ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2}


def _fill(action: Action, awaiting: str, text: str, state: dict[str, Any]) -> Action | None:
    """Put the answer to the last question into the action. ``None`` if no answer."""
    numbers = money.find_numbers(text)
    first = numbers[0].value if numbers else None
    if awaiting == "product":
        name = _clean_name(text)
        if not name:
            return None
        action.product = name
        return action
    if awaiting == "which":
        options = state.get("options") or []
        low = text.lower()
        for word, index in _ORDINALS.items():
            if re.search(rf"\b{word}\b", low) and index < len(options):
                action.product = options[index]
                return action
        if first is not None and first == first.to_integral_value() and 1 <= int(first) <= len(options):
            action.product = options[int(first) - 1]
            return action
        for option in options:
            if parse.product_key(option) and parse.product_key(option) in parse.product_key(text):
                action.product = option
                return action
        name = _clean_name(text)
        if name:
            action.product = name  # a different, new item
            return action
        return None
    if awaiting == "qty":
        if first is None:
            if parse._A_UNIT.search(text):
                action.qty = Decimal("1")
                return action
            return None
        action.qty = first
        return action
    if awaiting == "each_or_total":
        low = text.lower()
        if re.search(r"\b(each|one|per|single|a piece|apiece)\b", low):
            action.price_each, action.ambiguous_price = action.ambiguous_price, None
            return action
        if re.search(r"\b(all|total|everything|together|the whole|for them)\b", low):
            action.price_total, action.ambiguous_price = action.ambiguous_price, None
            return action
        return None
    if first is None or first <= 0:
        return None
    if awaiting == "price":
        if re.search(r"\b(all|total|altogether|together)\b", text.lower()) and action.qty and action.qty != 1:
            action.price_total = first
        else:
            action.price_each = first
        return action
    if awaiting == "cost":
        action.cost_each = first
        return action
    if awaiting == "set":
        action.set_price = first
        return action
    return None


# ---------------------------------------------------------------------------
# Read-back, save, cancel
# ---------------------------------------------------------------------------


async def _show_confirm(user: User, action: Action, product: ledger.Product | None = None, reminder: bool = False) -> Any:
    if product is None:
        product, _ = parse.match_product(action.product, await _catalogue(user))
    unit = action.unit or (product.unit if product else None)
    qty = action.qty or Decimal("0")
    lines: list[str] = []
    if action.kind == parse.SALE:
        price = action.price_each or Decimal("0")
        spoken = strings.CONFIRM_SALE_SPOKEN.format(
            qty=money.spoken_qty(qty), unit=_spoken_unit(unit, qty) or "of", product=action.product, price=money.spoken_naira(price)
        ).replace(" of of ", " of ")
        lines = [
            f"Sale: {money.qty(qty)} {_plural(unit, qty)} of {action.product}",
            f"Price: {money.naira(price)} each",
            f"Total: {money.naira(ledger.money(price * qty))}",
        ]
        if product is None:
            lines.append(f"New item. Cost: {money.naira(action.cost_each)} each")
    else:
        if product is None:
            spoken = strings.CONFIRM_NEW_SPOKEN.format(
                product=action.product,
                qty=money.spoken_qty(qty),
                unit=_spoken_unit(unit, qty),
                cost=money.spoken_naira(action.cost_each or 0),
                sell=money.spoken_naira(action.set_price or 0),
            )
            lines = [
                f"New item: {action.product}",
                f"Quantity: {money.qty(qty)} {_plural(unit, qty)}",
                f"Cost price: {money.naira(action.cost_each)} each",
                f"Selling price: {money.naira(action.set_price)} each",
            ]
        else:
            spoken = strings.CONFIRM_ADD_SPOKEN.format(qty=money.spoken_qty(qty), unit=_spoken_unit(unit, qty), product=action.product)
            lines = [f"Add stock: {money.qty(qty)} {_plural(unit, qty)} of {action.product}"]
            if action.cost_each is not None:
                lines.append(f"Cost: {money.naira(action.cost_each)} each")
            if action.set_price is not None and action.set_price != product.set_price:
                lines.append(f"New selling price: {money.naira(action.set_price)} each")
    spoken = re.sub(r"\s{2,}", " ", spoken)
    if reminder:
        spoken = "Please tap yes to save it, or no to cancel. " + spoken
    user.flow = FLOW
    user.flow_data = {
        "pending": action.to_json(),
        "awaiting": "confirm",
        "asks": 1 if reminder else 0,
        "product_id": product.id if product else None,
    }
    await _save(user)
    return _reply(
        messages=[_text(spoken), _card("\n".join(lines)), strings.confirm_buttons(user.language)],
        english_text=spoken,
    )


async def _cancel(user: User) -> Any:
    had_pending = bool(_state(user).get("pending"))
    _end(user)
    await _save(user)
    msg = strings.CANCELLED if had_pending else strings.NOTHING_PENDING
    return _reply(messages=[_text(msg + " " + strings.MENU_HINT)], english_text=msg)


async def _commit(user: User) -> Any:
    state = _state(user)
    if state.get("awaiting") != "confirm" or not state.get("pending"):
        _end(user)
        await _save(user)
        return _reply(messages=[_text(strings.NOTHING_PENDING)], english_text=strings.NOTHING_PENDING)
    action = Action.from_json(state["pending"])
    catalogue = await _catalogue(user)
    product = next((p for p in catalogue if state.get("product_id") and p.id == state.get("product_id")), None)
    if product is None:
        product, _ = parse.match_product(action.product, catalogue)

    try:
        if action.kind == parse.SALE:
            if product is None:
                # A sale of an item not in the catalogue yet: create it with
                # the cost the trader gave and this sale price as the set price.
                product = ledger.Product(
                    name=action.product or "item",
                    unit=action.unit,
                    cost_price=ledger.money(action.cost_each or 0),
                    set_price=ledger.money(action.set_price or action.price_each or 0),
                    stock_qty=ledger.quantity(action.qty or 0),
                )
            new_item = product.id is None
            result = ledger.record_sale(product, action.qty, action.price_each)
            await storage.trade_apply(user.phone, result.product, result.entry)
            reply = await _sale_saved(user, result, new_item=new_item)
        else:
            result = ledger.record_restock(
                product,
                name=action.product or "item",
                unit=action.unit,
                qty=action.qty,
                unit_cost=action.cost_each,
                set_price=action.set_price,
            )
            await storage.trade_apply(user.phone, result.product, result.entry)
            reply = _restock_saved(result)
    except ValueError as error:
        logger.info("Trade action rejected for %s: %s", mask_phone(user.phone), error)
        _end(user)
        await _save(user)
        msg = "Sorry, I could not save that, so nothing was saved. Please send it again."
        return _reply(messages=[_text(msg)], english_text=msg)
    except Exception as error:  # noqa: BLE001 - never leave the trader in silence
        logger.exception("Could not save trade entry for %s: %s", mask_phone(user.phone), error)
        return _reply(messages=[_text(strings.SAVE_FAILED)], english_text=strings.SAVE_FAILED)

    _end(user)
    await _save(user)
    return reply


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


async def _sale_saved(user: User, result: ledger.SaleResult, new_item: bool = False) -> Any:
    e = result.entry
    p = result.product
    spoken: list[str] = []
    if result.status == "profit":
        spoken.append(strings.SAVED_SALE_PROFIT.format(profit=money.spoken_naira(e.profit)))
    elif result.status == "loss":
        spoken.append(strings.SAVED_SALE_LOSS.format(loss=money.spoken_naira(-e.profit)))
    else:
        spoken.append(strings.SAVED_SALE_BREAK_EVEN)
    if result.price_vs_set == "discount":
        spoken.append(strings.SPOKEN_DISCOUNT.format(gap=money.spoken_naira(-e.price_difference)))
    elif result.price_vs_set == "markup":
        spoken.append(strings.SPOKEN_MARKUP.format(gap=money.spoken_naira(e.price_difference)))
    if new_item:
        # Its stock was never recorded, so "finished" would be a guess.
        spoken.append(strings.SPOKEN_ADD_STOCK.format(product=p.name))
    elif result.oversold:
        spoken.append(strings.SPOKEN_OVERSOLD.format(product=p.name))
    elif result.out_of_stock:
        spoken.append(_cap(strings.SPOKEN_OUT.format(product=p.name)))
    elif result.low_stock:
        spoken.append(_cap(strings.SPOKEN_LOW.format(product=p.name, qty=money.spoken_qty(p.stock_qty))))

    lines = [
        f"Sale saved: {money.qty(e.qty)} {_plural(e.unit, e.qty)} of {e.product_name}",
        f"Money received: {money.naira(e.amount)} ({money.naira(e.unit_price)} each)",
        f"Cost of goods: {money.naira(ledger.money(e.qty * e.unit_cost))} ({money.naira(e.unit_cost)} each)",
        ("Profit" if e.profit >= 0 else "LOSS") + f": {money.naira(abs(e.profit))}",
    ]
    if result.price_vs_set == "discount":
        lines.append(
            f"Set price {money.naira(e.set_price)} each. Discount given: {money.naira(-e.price_difference)}"
            f" (planned profit was {money.naira(result.planned_profit)})"
        )
    elif result.price_vs_set == "markup":
        lines.append(f"Set price {money.naira(e.set_price)} each. Extra above set price: {money.naira(e.price_difference)}")
    else:
        lines.append(f"Sold at your set price ({money.naira(e.set_price)} each)")
    if new_item:
        lines.append(f"New item added to your goods. Add your {p.name} stock so I can track what is left.")
    else:
        lines.append(f"Stock left: {money.qty(p.stock_qty)} {_plural(p.unit, p.stock_qty)}")
    if result.oversold and not new_item:
        lines.append("You sold more than your recorded stock. Please add your new stock.")

    today = await _today_line(user)
    if today:
        lines.append(today)
    lines.append(strings.MENU_HINT)
    body = " ".join(spoken)
    return _reply(messages=[_text(body), _card("\n".join(lines))], english_text=body)


def _restock_saved(result: ledger.RestockResult) -> Any:
    p = result.product
    spoken = strings.SAVED_ADD.format(qty=money.spoken_qty(p.stock_qty), unit=_spoken_unit(p.unit, p.stock_qty), product=p.name)
    if result.cost_changed:
        spoken += " " + strings.SPOKEN_COST_CHANGED
    spoken = re.sub(r"\s{2,}", " ", spoken)
    e = result.entry
    lines = [
        ("New item saved" if result.new_product else "Stock added") + f": {money.qty(e.qty)} {_plural(p.unit, e.qty)} of {p.name}",
        f"Paid: {money.naira(e.amount)} ({money.naira(e.unit_price)} each)",
        f"In stock now: {money.qty(p.stock_qty)} {_plural(p.unit, p.stock_qty)}",
        f"Cost price: {money.naira(p.cost_price)} each"
        + (f" (was {money.naira(result.old_cost)}; average of old and new stock)" if result.cost_changed else ""),
        f"Selling price: {money.naira(p.set_price)} each",
        f"Profit per {p.unit or 'unit'} at your selling price: {money.naira(p.set_price - p.cost_price)}",
        strings.MENU_HINT,
    ]
    return _reply(messages=[_text(spoken), _card("\n".join(lines))], english_text=spoken)


async def _today_line(user: User) -> str | None:
    try:
        start, end, _label = ledger.period_bounds("today")
        entries = await storage.trade_entries(user.phone, start, end)
    except Exception as error:  # noqa: BLE001
        logger.warning("Could not load today's entries for %s: %s", mask_phone(user.phone), error)
        return None
    s = ledger.summarize(entries, [], "today")
    if s.sales_count == 0:
        return None
    sales = "1 sale" if s.sales_count == 1 else f"{s.sales_count} sales"
    return f"Today so far: {sales}, {money.naira(s.revenue)} received, {money.naira(s.profit)} profit"


# ---------------------------------------------------------------------------
# Stock list, summaries, statement, help
# ---------------------------------------------------------------------------


async def _stock(user: User) -> Any:
    catalogue = await _catalogue(user)
    _end(user)
    await _save(user)
    if not catalogue:
        return _reply(messages=[_text(strings.NO_GOODS)], english_text=strings.NO_GOODS, show_menu=True, menu="trade")
    spoken = strings.STOCK_SPOKEN.format(count=money.number_words(len(catalogue)))
    low = [p for p in catalogue if p.stock_qty <= p.low_stock_level]
    if low:
        names = ", ".join(p.name for p in low[:5])
        spoken += f" Running low or finished: {names}."
    lines = ["Your goods:"]
    for i, p in enumerate(catalogue[:40], start=1):
        flag = " (finished)" if p.stock_qty <= 0 else " (low)" if p.stock_qty <= p.low_stock_level else ""
        lines.append(
            f"{i}. {p.name}: {money.qty(p.stock_qty)} {_plural(p.unit, p.stock_qty)}{flag}, cost {money.naira(p.cost_price)}, sell {money.naira(p.set_price)}"
        )
    value = sum((ledger.money(p.stock_qty * p.cost_price) for p in catalogue), Decimal("0"))
    lines.append(f"Stock value at cost: {money.naira(value)}")
    lines.append(strings.MENU_HINT)
    return _reply(messages=[_text(spoken), _card("\n".join(lines))], english_text=spoken)


async def _summary(user: User, period: str) -> Any:
    _end(user)
    await _save(user)
    start, end, label = ledger.period_bounds(period)
    try:
        entries = await storage.trade_entries(user.phone, start, end)
        catalogue = await storage.trade_products(user.phone)
    except Exception as error:  # noqa: BLE001
        logger.warning("Could not load summary for %s: %s", mask_phone(user.phone), error)
        return _reply(messages=[_text(strings.SAVE_FAILED)], english_text=strings.SAVE_FAILED)
    s = ledger.summarize(entries, catalogue, label)
    if s.sales_count == 0 and s.restock_spend == 0:
        msg = strings.NO_SALES.format(label=label)
        return _reply(messages=[_text(msg)], english_text=msg, show_menu=True, menu="trade")

    label_cap = label[0].upper() + label[1:]
    if s.profit >= 0:
        spoken = strings.SUMMARY_SPOKEN.format(label_cap=label_cap, revenue=money.spoken_naira(s.revenue), profit=money.spoken_naira(s.profit))
    else:
        spoken = strings.SUMMARY_LOSS_SPOKEN.format(label_cap=label_cap, revenue=money.spoken_naira(s.revenue), loss=money.spoken_naira(-s.profit))
    if s.discounts > 0:
        spoken += " " + strings.SUMMARY_DISCOUNT_SPOKEN.format(discounts=money.spoken_naira(s.discounts))
    if s.low_stock or s.out_of_stock:
        names = ", ".join(p.name for p in (s.out_of_stock + s.low_stock)[:4])
        spoken += f" Check your stock of {names}."

    local = ledger.lagos_now()
    heading = f"{label_cap} ({local:%d %b %Y})" if period == "today" else f"{label_cap} (since Monday {ledger.lagos_now(start):%d %b})"
    lines = [
        heading,
        f"Sales: {s.sales_count}",
        f"Money received: {money.naira(s.revenue)}",
        f"Cost of goods sold: {money.naira(s.cost_of_goods)}",
        ("Profit" if s.profit >= 0 else "LOSS") + f": {money.naira(abs(s.profit))}"
        + (f" (margin {s.margin_percent}%)" if s.margin_percent is not None and s.profit >= 0 else ""),
    ]
    if s.sales_count:
        gap = s.gap_to_plan
        if gap < 0:
            lines.append(f"Planned profit: {money.naira(s.planned_profit)}. You made {money.naira(-gap)} less than planned.")
        elif gap > 0:
            lines.append(f"Planned profit: {money.naira(s.planned_profit)}. You made {money.naira(gap)} more than planned.")
        else:
            lines.append("You sold at your set prices.")
    if s.discounts:
        lines.append(f"Discounts given: {money.naira(s.discounts)}")
    if s.markups:
        lines.append(f"Sold above set price: {money.naira(s.markups)}")
    if s.loss_sales:
        lines.append(f"Sales at a loss: {s.loss_sales}")
    if s.restock_spend:
        lines.append(f"Goods bought: {money.naira(s.restock_spend)}")
    if s.top:
        lines.append("Best items: " + "; ".join(f"{t.name} {money.naira(t.profit)} profit" for t in s.top))
    if s.out_of_stock:
        lines.append("Finished: " + ", ".join(p.name for p in s.out_of_stock[:6]))
    if s.low_stock:
        lines.append("Running low: " + ", ".join(f"{p.name} ({money.qty(p.stock_qty)} left)" for p in s.low_stock[:6]))
    lines.append(strings.MENU_HINT)
    return _reply(messages=[_text(spoken), _card("\n".join(lines))], english_text=spoken)


async def _export(user: User) -> Any:
    from app.trade import statement

    _end(user)
    await _save(user)
    try:
        entries = await storage.trade_entries(user.phone)
        catalogue = await storage.trade_products(user.phone)
    except Exception as error:  # noqa: BLE001
        logger.warning("Could not load ledger for %s: %s", mask_phone(user.phone), error)
        return _reply(messages=[_text(strings.EXPORT_FAILED)], english_text=strings.EXPORT_FAILED)
    if not entries:
        return _reply(messages=[_text(strings.EXPORT_EMPTY)], english_text=strings.EXPORT_EMPTY)
    try:
        generated = datetime.now(timezone.utc)
        name = getattr(user, "preferred_name", None) or getattr(user, "name", None)
        pdf, ref = statement.build_pdf(trader_name=name, phone=user.phone, entries=entries, products=catalogue, generated=generated)
    except Exception as error:  # noqa: BLE001
        logger.exception("Could not build statement for %s: %s", mask_phone(user.phone), error)
        return _reply(messages=[_text(strings.EXPORT_FAILED)], english_text=strings.EXPORT_FAILED)
    document = {
        "type": "document",
        "document": {
            "bytes": pdf,
            "filename": statement.filename(name, generated),
            "caption": f"{strings.label('statement', user.language)} ({ref})",
            "mime_type": "application/pdf",
        },
    }
    return _reply(messages=[_text(strings.EXPORT_SPOKEN), document], english_text=strings.EXPORT_SPOKEN)


def _help() -> Any:
    return _reply(messages=[_text(strings.HELP_SPOKEN)], english_text=strings.HELP_SPOKEN, show_menu=True, menu="trade")
