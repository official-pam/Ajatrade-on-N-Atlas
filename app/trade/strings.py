"""User-facing wording for the trader ledger.

Replies are written in English, like the farm-advice replies: the reply
pipeline translates them with N-ATLaS and voices them, and number cards are
sent as text with every number checked after translation. Interactive
messages (buttons) are not translated by the pipeline, so their short labels
are given here in all five languages.

Spoken lines say money in words ("fifty-two thousand naira"); the text card
carries the exact digits.
"""

from __future__ import annotations

from app.services.languages import normalize_language

MENU_HINT = "Say menu to see other services."

# ---------------------------------------------------------------------------
# English replies (translated by the reply pipeline)
# ---------------------------------------------------------------------------

ASK_SALE = (
    "Tell me what you sold, how many, and the price. For example: "
    "I sold three bags of rice at fifty-two thousand naira each."
)
ASK_SALE_CARD = "Example: I sold 3 bags of rice at ₦52,000 each."
ASK_ADD = (
    "Tell me the item, how many, what each one cost you, and your selling price. "
    "For example: add ten bags of rice, cost forty-five thousand, sell fifty-two thousand."
)
ASK_ADD_CARD = "Example: Add 10 bags of rice, cost ₦45,000, sell ₦52,000."

ASK_PRODUCT = "Which item was it?"
ASK_QTY_SALE = "How many {unit} of {product} did you sell?"
ASK_QTY_ADD = "How many {unit} of {product} are you adding?"
ASK_PRICE = "How much did you sell one {unit_one} of {product} for?"
ASK_EACH_OR_TOTAL = "Is {price} the price for one, or for all {qty}?"
ASK_COST_NEW = "I do not have {product} in your goods yet. How much did one {unit_one} cost you?"
ASK_SET_NEW = "What is your normal selling price for one {unit_one} of {product}?"
ASK_WHICH = "Which one do you mean: {options}?"

CONFIRM_SALE_SPOKEN = "Please check. You sold {qty} {unit} of {product} at {price} each. Tap yes to save."
CONFIRM_ADD_SPOKEN = "Please check. You are adding {qty} {unit} of {product}. Tap yes to save."
CONFIRM_NEW_SPOKEN = (
    "Please check. New item {product}: {qty} {unit}, cost {cost} each, selling price {sell} each. Tap yes to save."
)

SAVED_SALE_PROFIT = "Saved. You made {profit} profit on this sale."
SAVED_SALE_BREAK_EVEN = "Saved. You made no profit and no loss on this sale."
SAVED_SALE_LOSS = "Saved. Careful: you lost {loss} on this sale. You sold below what it cost you."
SPOKEN_DISCOUNT = "You sold {gap} below your set price."
SPOKEN_MARKUP = "You sold {gap} above your set price. Well done."
SPOKEN_LOW = "{product} is running low. Only {qty} left."
SPOKEN_OUT = "{product} is finished in your stock."
SPOKEN_OVERSOLD = "You sold more {product} than I had in your stock. Please add your new stock."
SPOKEN_ADD_STOCK = "I added {product} to your goods. Add your {product} stock so I can track what is left."
SAVED_ADD = "Saved. You now have {qty} {unit} of {product}."
SPOKEN_COST_CHANGED = "Your cost price is now the average of old and new stock."

CANCELLED = "Okay, I did not save it. You can send it again with the right details."
NOTHING_PENDING = "There is nothing waiting to be saved."
NOT_UNDERSTOOD = (
    "Sorry, I did not get that. Tell me what you sold or what you are adding, "
    "or say menu to see the options."
)
NEED_CONSENT = (
    "To keep your sales record I need your permission to save your chats. "
    "Say start over to change your choice."
)
SAVE_FAILED = "Sorry, I could not save that right now. Please send it again in a few minutes."
NO_GOODS = "You have not added any goods yet. Tap add or restock goods to start."
STOCK_SPOKEN = "You have {count} items. I sent the list."
NO_SALES = "No sales recorded {label} yet."
SUMMARY_SPOKEN = "{label_cap} you sold {revenue} and made {profit} profit."
SUMMARY_LOSS_SPOKEN = "{label_cap} you sold {revenue} but lost {loss}."
SUMMARY_DISCOUNT_SPOKEN = "Discounts cost you {discounts}."
EXPORT_SPOKEN = (
    "I sent your sales record as a document. You can share it with a bank or lender. "
    "It shows what you recorded with AjaTrade."
)
EXPORT_EMPTY = "There are no sales or stock records yet, so there is nothing to put in the statement."
EXPORT_FAILED = "Sorry, I could not make the document right now. Please try again later."
HELP_SPOKEN = (
    "I am AjaTrade, a computer assistant. I keep your sales book. Tell me each sale, like: "
    "I sold two cartons of noodles at nine thousand five hundred naira each. "
    "I will work out your profit, warn you about losses and discounts, and track your stock. "
    "You can ask for today's profit, this week's profit, or a statement for a loan."
)

# ---------------------------------------------------------------------------
# Short labels for interactive messages (not translated by the pipeline)
# REVIEW: native speakers to check the Hausa, Yoruba, Igbo and Pidgin labels.
# ---------------------------------------------------------------------------

_LABELS: dict[str, dict[str, str]] = {
    "english": {"confirm": "Save this record?", "yes": "Yes, save", "no": "No, cancel", "statement": "AjaTrade sales record"},
    "hausa": {"confirm": "A ajiye wannan?", "yes": "Eh, ajiye", "no": "A'a, soke", "statement": "Bayanan kasuwancin AjaTrade"},  # REVIEW
    "yoruba": {"confirm": "Ṣé kí n fi pamọ́?", "yes": "Bẹ́ẹ̀ ni, fi pamọ́", "no": "Rárá, fagilé", "statement": "Àkọsílẹ̀ ọjà AjaTrade"},  # REVIEW
    "igbo": {"confirm": "Chekwaa ndekọ a?", "yes": "Ee, chekwaa", "no": "Mba, kagbuo", "statement": "Ndekọ ahịa AjaTrade"},  # REVIEW
    "pidgin": {"confirm": "Make I save am?", "yes": "Yes, save am", "no": "No, cancel", "statement": "AjaTrade sales record"},  # REVIEW
}


def label(key: str, language: str | None) -> str:
    lang = normalize_language(language)
    return _LABELS.get(lang, _LABELS["english"]).get(key) or _LABELS["english"][key]


def confirm_buttons(language: str | None) -> dict:
    """Yes / No buttons under a read-back (ids are internal only)."""
    return {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": label("confirm", language)},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "trade_yes", "title": label("yes", language)}},
                    {"type": "reply", "reply": {"id": "trade_no", "title": label("no", language)}},
                ]
            },
        },
    }
