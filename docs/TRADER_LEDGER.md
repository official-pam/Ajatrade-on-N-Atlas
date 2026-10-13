# Trader ledger (sales assistant for traders)

Traders and small manufacturers keep their sales book on WhatsApp, by voice
or text, in any of the five languages.

## What it does

- **Catalogue:** each item has a cost price, a set selling price and units
  in stock.
- **Record a sale:** "I sold 3 bags of rice at 52,000 each". The bot reads it
  back with Yes / No buttons and saves only after Yes. It then replies with
  the profit, any loss, any discount or markup against the set price, and the
  stock left.
- **Add or restock goods:** "Add 10 bags of rice, cost 45,000, sell 52,000".
  When the cost changes, the cost price becomes the average of the old and
  new stock.
- **Today's profit / This week's profit** (since Monday, Nigeria time): money
  received, cost of goods, profit, margin, planned profit vs actual,
  discounts, sales at a loss, best items, low or finished stock.
- **Record for a loan:** a PDF statement (summary, month by month, current
  stock, transactions) sent as a WhatsApp document. It states that the
  trader entered the records and that they are not audited.

Traders and manufacturers see the trader menu. Farmers keep the farm menu.
Anyone can type "my shop" to open the trader menu, and the trader menu has a
"Farm services" row that opens the farm menu.

## Safety rules it follows

- All money maths is in `app/trade/ledger.py` (Decimal, no floats).
- N-ATLaS is only asked to read a message when the patterns miss something.
  A number it returns is kept only if the trader actually said it
  (`parse.ground`).
- Nothing is saved without a Yes, and nothing is saved for users without
  consent.
- Summaries are sent only when asked (no proactive messages, per AGENTS.md).

## Files

- `app/trade/`: `money.py`, `ledger.py`, `parse.py`, `strings.py`,
  `statement.py`, `flow.py`, and the tests.
- `alembic/versions/d4b8e3a2f1c7_add_trade_ledger.py`: two new tables,
  `trade_products` and `trade_entries`. Additive only.
- Small hooks in `app/services/brain.py` (routing),
  `app/services/whatsapp/messages.py` (menu taps, sending the PDF),
  `app/services/whatsapp/send_media.py` (`send_document`),
  `app/onboarding/messages.py` (trader menu in five languages),
  `app/onboarding/state_machine.py`, and `app/storage/` (tables and queries).

## How to test on the test number

1. `alembic upgrade head` (the app also creates the tables at startup).
2. Start the bot. If needed, type "start over" and pick **Trader** as the role.
3. Tap **Add or restock goods**, then say or type:
   "Add 10 bags of rice, cost 45,000, sell 52,000". You should see a read-back
   card and Yes / No buttons. Tap **Yes**.
4. Say "I sold 3 bags of rice at 50,000 each", then tap **Yes**. You should get
   a profit of ₦15,000, a ₦6,000 discount note, and 7 bags left.
5. Tap **Today's profit**, **My goods and stock**, then **Record for a loan**
   (a PDF arrives).

## Needs review

- Hausa, Yoruba, Igbo and Pidgin menu rows and button labels (`# REVIEW`).
- The PDF masks the phone number except the last four digits. Decide with a
  lender what identity details they need.
