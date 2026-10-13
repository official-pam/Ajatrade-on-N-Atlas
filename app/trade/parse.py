"""Turn a trader's message (already in English) into a ledger action.

Two passes, like the farm-advice extractor:

1. Patterns (fast, offline): the action word ("sold", "add", "restock",
   "profit today", "ledger") and numbers with a clear cue ("at 52,000 each",
   "cost 45,000", "sell at 52,000").
2. N-ATLaS, only when a needed field is still missing or the message is long.
   Its answer is grounded: a number is kept only if the trader actually said
   it (digits or words), and the item name must appear in the message.

Nothing here does money maths or touches the database.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from app.trade import money

logger = logging.getLogger("trade")

SALE = "sale"
RESTOCK = "restock"
STOCK = "stock"
TODAY = "summary_today"
WEEK = "summary_week"
EXPORT = "export"
HELP = "help"
SHOP_MENU = "shop_menu"

ACTIONS = (SALE, RESTOCK, STOCK, TODAY, WEEK, EXPORT, HELP, SHOP_MENU)

UNITS = {
    "bag", "carton", "piece", "pcs", "pc", "crate", "tin", "kg", "kilo", "litre", "liter",
    "sachet", "packet", "pack", "dozen", "basket", "mudu", "paint", "tuber", "bunch", "roll",
    "bottle", "yard", "box", "gallon", "keg", "jerrycan", "bundle", "set", "pair", "unit",
    "plate", "cup", "derica", "olodo", "bowl", "sack", "tray", "ream", "wrap", "loaf", "heap",
}

_STOP = {
    "i", "we", "me", "my", "our", "have", "has", "had", "just", "today", "now", "the", "of", "a", "an",
    "and", "at", "for", "to", "each", "per", "one", "naira", "price", "cost", "costs", "is", "it", "them",
    "that", "this", "with", "sell", "sold", "selling", "sale", "sales", "add", "added", "new", "item",
    "product", "goods", "restock", "restocked", "bought", "buy", "stock", "in", "on", "please", "record",
    "set", "will", "would", "paid", "pay", "apiece", "piece", "all", "total", "plus", "more", "some",
    "customer", "customers", "someone", "somebody", "from", "got", "get", "collected", "collect",
    "brought", "came", "units", "quantity", "worth", "amount", "be", "was", "were", "are", "so", "also",
    "then", "yesterday", "morning", "afternoon", "evening", "market", "shop", "store", "ngn", "n",
    "money", "only", "about", "around", "like", "say", "said", "want", "wants", "you", "your", "make",
    "made", "up", "out", "off", "discount", "did", "do", "does", "can", "could", "again", "another",
    "selling_price", "k", "m", "thousand", "million", "hundred", "x", "by", "into", "as", "but",
}

_SALE = re.compile(r"\b(sold|sell(?!ing price)|i sell|sale|sales of|(my )?customers?|buyer|somebody (bought|buy|took|collected)|someone (bought|buy|took|collected)|paid me|gave me)\b", re.I)
_RESTOCK = re.compile(r"\b(add|added|restock\w*|re-stock\w*|bought|buy|new (item|product|goods|stock)|stock up|got new|collected|brought in|put in stock|i get)\b", re.I)
_STOCK = re.compile(r"\b(my (stock|goods|items|products|catalogue|catalog|inventory)|what (do )?i have|stock (left|remaining)|how many .{1,30} (left|remain\w*)|check (my )?stock|inventory)\b", re.I)
_TODAY = re.compile(r"\b((today'?s?|day'?s?) (profit|sales|summary|account|gain)|(profit|sales|summary|gain|account) (for )?today|how (much|far) .{0,20}today|did i make today)\b", re.I)
_WEEK = re.compile(r"\b((this |the )?week'?s? (profit|sales|summary|account)|(profit|sales|summary|account) (for |this )?(this )?week|weekly|this week)\b", re.I)
_EXPORT = re.compile(r"\b(ledger|statement|loan|export|pdf|record book|account book|bank|lender|grant|credit|my records)\b", re.I)
_HELP = re.compile(r"^\s*(help|how (do|does) (this|it) work|what can you do)\s*\??\s*$", re.I)
_SHOP_MENU = re.compile(r"\b(my shop|shop menu|trade menu|trader menu|business menu|sales menu)\b", re.I)

_YES = re.compile(r"^\s*(yes|yeah|yea|yep|ok|okay|correct|confirm\w*|save( it)?|sure|right|that is (right|correct)|it is correct|na so|e correct|true|go ahead|ehen)\b", re.I)
_NO = re.compile(r"^\s*(no|nope|not correct|wrong|cancel|incorrect|that is wrong|it is wrong|don'?t save|do not save|change( it)?|mistake|false)\b", re.I)

_EACH_AFTER = re.compile(r"^\s*(naira\s*)?(each|per|apiece|a piece|for one|for each|one one|per (one|unit|piece|bag|carton|item|\w+))\b", re.I)
_TOTAL_AFTER = re.compile(r"^\s*(naira\s*)?(in total|total|altogether|for all|for everything|all together|for the (lot|whole|all))\b", re.I)
_COST_BEFORE = re.compile(r"(cost(s|ing)?( price)?( me)?|bought (it |them )?(at|for)|buy (it |them )?(at|for)|i paid|paid|purchase(d)? (at|for)|cost price (is|of))\s*(is\s*|was\s*|of\s*)?(₦|n|ngn)?\s*$", re.I)
_SET_BEFORE = re.compile(r"(sell(ing)?( price)?|set price|sell (it |them |each |one )?(at|for)|will sell( it| them)?( at| for)?|selling at|sale price|to sell( at| for)?)\s*(is\s*|of\s*)?(₦|n|ngn)?\s*$", re.I)
_AT_BEFORE = re.compile(r"(\bat|@|\bfor|\bprice( of)?|\bwith)\s*(₦|n|ngn)?\s*$", re.I)
_FOR_BEFORE = re.compile(r"\bfor\s*(₦|n|ngn)?\s*$", re.I)
_MONEY_AFTER = re.compile(r"^\s*(naira|ngn|k\b)", re.I)
_UNIT_AFTER = re.compile(r"^\s*(x\s*)?([a-z]+)", re.I)
_A_UNIT = re.compile(r"\b(a|an|one)\s+(" + "|".join(sorted(UNITS, key=len, reverse=True)) + r")s?\b", re.I)


@dataclass
class Action:
    """What the trader asked for, with only the fields they actually said."""

    kind: str | None = None
    product: str | None = None
    unit: str | None = None
    qty: Decimal | None = None
    price_each: Decimal | None = None  # sale: actual selling price per unit
    price_total: Decimal | None = None  # sale: total received
    cost_each: Decimal | None = None  # restock / new item: cost per unit
    set_price: Decimal | None = None  # restock / new item: planned selling price
    ambiguous_price: Decimal | None = None  # a sale price with no "each" / "total" cue
    from_model: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, Decimal):
                data[key] = str(value)
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> "Action":
        data = dict(data or {})
        out = cls()
        for key in ("kind", "product", "unit"):
            setattr(out, key, data.get(key))
        for key in ("qty", "price_each", "price_total", "cost_each", "set_price", "ambiguous_price"):
            value = data.get(key)
            setattr(out, key, Decimal(value) if value not in (None, "") else None)
        out.from_model = list(data.get("from_model") or [])
        return out


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def singular(word: str) -> str:
    w = word.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("oes", "xes", "ches", "shes", "sses")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def product_key(name: str | None) -> str:
    """Normalised item name for matching: "Bags of Rice" -> "rice"."""
    words = re.findall(r"[a-z0-9']+", (name or "").lower())
    words = [w for w in words if w not in ("of", "the", "a", "an")]
    if words and singular(words[0]) in UNITS and len(words) > 1:
        words = words[1:]
    return " ".join(singular(w) for w in words).strip()


def unit_of(word: str | None) -> str | None:
    if not word:
        return None
    w = singular(word.lower())
    if w in ("kilo", "kilogram"):
        return "kg"
    if w == "liter":
        return "litre"
    if w in ("pcs", "pc"):
        return "piece"
    return w if w in UNITS else None


def is_yes(text: str) -> bool:
    return bool(_YES.match(text or "")) and not _NO.match(text or "")


def is_no(text: str) -> bool:
    return bool(_NO.match(text or ""))


def detect_kind(text: str) -> str | None:
    """The action word, if any (patterns only)."""
    t = text or ""
    if _HELP.match(t):
        return HELP
    if _SHOP_MENU.search(t):
        return SHOP_MENU
    if _TODAY.search(t):
        return TODAY
    if _WEEK.search(t):
        return WEEK
    if _EXPORT.search(t):
        return EXPORT
    sale = _SALE.search(t)
    restock = _RESTOCK.search(t)
    if sale and restock:
        # "Add 10 bags, cost 45,000, sell 52,000": "sell" is the set price.
        # "I sold ... a customer bought": "sold" wins.
        if re.search(r"\b(sold|sale)\b", t, re.I) and not re.search(r"\b(add|added|restock\w*)\b", t, re.I):
            return SALE
        return RESTOCK if restock.start() < sale.start() else SALE
    if sale:
        return SALE
    if _STOCK.search(t) and not money.find_numbers(t):
        return STOCK
    if restock:
        return RESTOCK
    return None


def strong_trade_words(text: str) -> bool:
    """Words that mean a ledger action whatever the user's role.

    "today" or "this week" alone are not enough (a farmer may say them), so
    those only count for traders (see ``flow.looks_like_trade``).
    """
    t = text or ""
    return bool(
        _SHOP_MENU.search(t)
        or re.search(r"\b(i sold|we sold|sold \d|sold (a|an|one|two|three|four|five|six|seven|eight|nine|ten)\b|restock\w*|my ledger|ledger|my stock|my goods|add .{1,30} to my (goods|stock|shop))", t, re.I)
    )


# ---------------------------------------------------------------------------
# Pattern pass
# ---------------------------------------------------------------------------


def _product_and_unit(text: str, numbers: list[money.Number]) -> tuple[str | None, str | None]:
    """Item name and unit from the words that are not numbers or filler."""
    spans = [(n.start, n.end) for n in numbers]
    words: list[tuple[str, int]] = []
    for m in re.finditer(r"[A-Za-z][A-Za-z'\-]*", text):
        if any(s <= m.start() < e for s, e in spans):
            continue
        words.append((m.group(0), m.start()))
    unit: str | None = None
    kept: list[str] = []
    for word, _ in words:
        low = word.lower()
        u = unit_of(low)
        if u and unit is None:
            unit = u
            continue
        if u:
            continue
        if low in _STOP or low in money._WORD or low in ("half", "dozen"):
            continue
        kept.append(low)
    # Item names are short ("rice", "palm oil", "indomie noodles").
    name = " ".join(kept[:4]).strip() or None
    return name, unit


def pattern_action(text: str, kind: str | None = None) -> Action:
    """Fields with a clear cue in the text. Leaves the rest as None."""
    text = text or ""
    kind = kind or detect_kind(text)
    action = Action(kind=kind)
    if kind not in (SALE, RESTOCK):
        return action

    numbers = money.find_numbers(text)
    action.product, action.unit = _product_and_unit(text, numbers)
    quantities: list[Decimal] = []
    loose_money: list[Decimal] = []

    for n in numbers:
        before = text[max(0, n.start - 40):n.start]
        after = text[n.end:n.end + 30]
        each = bool(_EACH_AFTER.match(after))
        total = bool(_TOTAL_AFTER.match(after))
        if _COST_BEFORE.search(before):
            if total and action.qty is None and quantities:
                action.qty = quantities[0]
            action.cost_each = n.value if not total else None
            if total:
                action.price_total = n.value  # resolved to cost below
            continue
        if _SET_BEFORE.search(before) and (kind == RESTOCK or "price" in before.lower()):
            action.set_price = n.value
            continue
        if _AT_BEFORE.search(before) or _MONEY_AFTER.match(after) or each or total or before.rstrip().endswith(("₦", "N")):
            if kind == SALE:
                if each:
                    action.price_each = n.value
                elif total or _FOR_BEFORE.search(before):
                    action.price_total = n.value
                else:
                    loose_money.append(n.value)
            else:
                if total:
                    action.price_total = n.value
                elif action.cost_each is None:
                    action.cost_each = n.value
                elif action.set_price is None:
                    action.set_price = n.value
            continue
        unit_match = _UNIT_AFTER.match(after)
        if unit_match and unit_match.group(2).lower() not in ("naira", "each", "per", "k", "total"):
            quantities.append(n.value)
        else:
            loose_money.append(n.value)

    if quantities:
        action.qty = quantities[0]
        loose_money = quantities[1:] + loose_money
    elif _A_UNIT.search(text):
        action.qty = Decimal("1")

    if kind == SALE:
        if action.qty is None and len(loose_money) >= 2:
            action.qty, loose_money = loose_money[0], loose_money[1:]
        if action.price_each is None and action.price_total is None and loose_money:
            price = loose_money[0]
            if action.qty == 1:
                action.price_each = price
            else:
                action.ambiguous_price = price
    else:
        if action.qty is None and loose_money:
            action.qty, loose_money = loose_money[0], loose_money[1:]
        if action.price_total is not None and action.cost_each is None and action.qty:
            action.cost_each = money_div(action.price_total, action.qty)
            action.price_total = None
        for value in loose_money:
            if action.cost_each is None:
                action.cost_each = value
            elif action.set_price is None:
                action.set_price = value
    return action


def money_div(total: Decimal, qty: Decimal) -> Decimal:
    from app.trade.ledger import money as to_money

    return to_money(total / qty)


# ---------------------------------------------------------------------------
# N-ATLaS pass (grounded)
# ---------------------------------------------------------------------------

EXTRACT_SYSTEM = (
    "You read a Nigerian trader's message (already translated to English) for a sales "
    "record book. Only include facts the trader clearly said. Use digits for numbers. "
    "Return ONE JSON object and nothing else."
)


def _extract_prompt(text: str, kind_hint: str | None, asked: str | None) -> str:
    return (
        f"Message: {text!r}\n"
        f"Current task: {kind_hint or 'unknown'}. Question just asked: {asked or 'none'}.\n"
        "Return {\"action\": one of [sale, restock, stock, summary_today, summary_week, export, "
        "other], \"product\": item name or null, \"unit\": bag/carton/piece/etc or null, "
        "\"quantity\": number or null, \"price_each\": selling price for one unit or null, "
        "\"price_total\": total money received for the sale or null, \"cost_each\": what one "
        "unit cost the trader or null, \"selling_price\": planned selling price for one unit "
        "(when adding goods) or null}. restock means adding goods to stock."
    )


def ground(text: str, data: dict[str, Any]) -> Action:
    """Keep only model fields the trader really said."""
    said = money.said_values(text)
    action = Action()
    kind = str(data.get("action") or "").strip().lower()
    action.kind = kind if kind in ACTIONS else None

    def num(key: str) -> Decimal | None:
        value = money.to_decimal(data.get(key))
        if value is None or value <= 0:
            return None
        if value.normalize() not in said:
            logger.info("Dropped model value %s=%s: not said by the trader", key, value)
            return None
        action.from_model.append(key)
        return value

    action.qty = num("quantity")
    action.price_each = num("price_each")
    action.price_total = num("price_total")
    action.cost_each = num("cost_each")
    action.set_price = num("selling_price")

    product = data.get("product")
    if isinstance(product, str) and product.strip():
        key_words = set(product_key(product).split())
        text_words = {singular(w) for w in re.findall(r"[a-z0-9']+", text.lower())}
        if key_words and key_words <= text_words:
            action.product = product.strip().lower()
            action.from_model.append("product")
    action.unit = unit_of(data.get("unit")) if isinstance(data.get("unit"), str) else None
    return action


async def model_action(text: str, kind_hint: str | None = None, asked: str | None = None) -> Action | None:
    """N-ATLaS reading of the message, grounded. ``None`` if the call fails."""
    from app.services import natlas
    from app.services.diagnoses.reply import parse_model_output

    try:
        raw = await natlas.chat(
            EXTRACT_SYSTEM, _extract_prompt(text, kind_hint, asked), json_mode=True, max_tokens=250, timeout=60.0
        )
    except Exception as error:  # noqa: BLE001 - patterns still work without it
        logger.warning("N-ATLaS trade extraction failed: %s", error)
        return None
    data = parse_model_output(raw) or {}
    return ground(text, data)


def needs_model(action: Action, text: str) -> bool:
    if action.kind is None:
        return True
    if action.kind == SALE:
        return not action.product or action.qty is None or (
            action.price_each is None and action.price_total is None and action.ambiguous_price is None
        ) or len(text.split()) > 16
    if action.kind == RESTOCK:
        return not action.product or action.qty is None or len(text.split()) > 16
    return False


def merge(primary: Action, extra: Action | None) -> Action:
    """Fill gaps in the pattern result with grounded model fields."""
    if extra is None:
        return primary
    out = Action(**{**primary.__dict__, "from_model": list(primary.from_model)})
    if out.kind is None:
        out.kind = extra.kind
    for key in ("product", "unit", "qty", "price_each", "price_total", "cost_each", "set_price"):
        if getattr(out, key) is None and getattr(extra, key) is not None:
            setattr(out, key, getattr(extra, key))
            out.from_model.append(key)
    # The model can settle an ambiguous sale price only by naming it exactly.
    if out.ambiguous_price is not None:
        if extra.price_each == out.ambiguous_price:
            out.price_each, out.ambiguous_price = extra.price_each, None
        elif extra.price_total == out.ambiguous_price:
            out.price_total, out.ambiguous_price = extra.price_total, None
    if out.price_each is not None and out.price_total is not None and out.qty:
        # Both said: keep "each" and drop a total that does not add up.
        from app.trade.ledger import money as to_money

        if to_money(out.price_each * out.qty) != to_money(out.price_total):
            out.price_total = None
    return out


async def read_action(text: str, kind_hint: str | None = None, asked: str | None = None) -> Action:
    """Patterns first, then N-ATLaS for the gaps."""
    action = pattern_action(text, kind_hint if kind_hint in (SALE, RESTOCK) and detect_kind(text) is None else None)
    if needs_model(action, text):
        action = merge(action, await model_action(text, action.kind or kind_hint, asked))
    return action


# ---------------------------------------------------------------------------
# Catalogue matching
# ---------------------------------------------------------------------------


def match_product(name: str | None, catalogue: list[Any]) -> tuple[Any | None, list[Any]]:
    """Find ``name`` in the trader's catalogue.

    Returns (product, []) for one clear match, (None, [candidates]) when
    several items are close, and (None, []) when nothing matches.
    """
    key = product_key(name)
    if not key:
        return None, []
    by_key = {product_key(p.name): p for p in catalogue}
    if key in by_key:
        return by_key[key], []
    contains = [p for k, p in by_key.items() if key in k.split() or k in key.split() or (len(key) > 3 and key in k)]
    if len(contains) == 1:
        return contains[0], []
    close = difflib.get_close_matches(key, list(by_key), n=3, cutoff=0.8)
    if len(close) == 1 and not contains:
        return by_key[close[0]], []
    candidates = contains or [by_key[k] for k in close]
    return None, candidates[:3]
