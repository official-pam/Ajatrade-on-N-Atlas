"""The trader's sales record as a PDF a lender can read.

Built with plain Python (no PDF library, so no new dependency): A4 pages,
the built-in Helvetica fonts, simple tables. Text is kept to Latin-1, so
accents are dropped from item names and naira is written "NGN".

The statement says clearly that the trader entered the records and that
AjaTrade only added them up; it is not an audited account.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.trade import ledger, money

PAGE_W, PAGE_H = 595, 842  # A4 in points
MARGIN = 40
MAX_TRANSACTIONS = 300

# Helvetica character widths (per 1000 units) for the characters we print
# most; anything else uses an average width. Good enough to right-align
# numbers in a column.
_WIDTHS = {
    **{d: 556 for d in "0123456789"},
    " ": 278, ",": 278, ".": 278, "-": 333, ":": 278, "/": 278, "%": 889, "(": 333, ")": 333,
    "N": 722, "G": 778, "i": 222, "l": 222, "I": 278, "t": 278, "f": 278, "j": 222, "r": 333,
    "m": 833, "w": 722, "M": 833, "W": 944,
}


def _width(text: str, size: float, bold: bool = False) -> float:
    avg = 611 if bold else 556
    return sum(_WIDTHS.get(ch, avg if ch.isupper() else 500) for ch in text) * size / 1000


def _latin(text: object) -> str:
    raw = unicodedata.normalize("NFKD", str(text if text is not None else ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.replace("₦", "NGN ").replace("’", "'").replace("–", "-").replace("—", "-")
    return raw.encode("latin-1", "replace").decode("latin-1")


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class _Canvas:
    """Collects drawing commands per page."""

    def __init__(self) -> None:
        self.pages: list[list[str]] = []
        self.y = 0.0
        self.new_page()

    def new_page(self) -> None:
        self.pages.append([])
        self.y = PAGE_H - MARGIN

    @property
    def ops(self) -> list[str]:
        return self.pages[-1]

    def text(self, x: float, y: float, value: object, size: float = 9, bold: bool = False, align: str = "left") -> None:
        s = _latin(value)
        if align == "right":
            x -= _width(s, size, bold)
        font = "F2" if bold else "F1"
        self.ops.append(f"BT /{font} {size:.1f} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm ({_escape(s)}) Tj ET")

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 0.5) -> None:
        self.ops.append(f"{width:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def ensure(self, height: float) -> bool:
        """Start a new page when fewer than ``height`` points are left."""
        if self.y - height < MARGIN + 30:
            self.new_page()
            return True
        return False


@dataclass
class Column:
    title: str
    x: float  # left edge (left aligned) or right edge (right aligned)
    align: str = "left"


def _table(c: _Canvas, columns: list[Column], rows: list[list[object]], size: float = 8.5) -> None:
    def header() -> None:
        for col in columns:
            c.text(col.x, c.y, col.title, size, bold=True, align=col.align)
        c.y -= 4
        c.line(MARGIN, c.y, PAGE_W - MARGIN, c.y)
        c.y -= size + 4

    header()
    for row in rows:
        if c.ensure(size + 6):
            header()
        for col, value in zip(columns, row, strict=False):
            c.text(col.x, c.y, value, size, align=col.align)
        c.y -= size + 5
    c.y -= 6


def _heading(c: _Canvas, title: str) -> None:
    c.ensure(60)
    c.y -= 6
    c.text(MARGIN, c.y, title, 11.5, bold=True)
    c.y -= 16


def _date(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return ledger.lagos_now(dt).strftime("%d %b %Y")


def reference(wa_id: str, generated: datetime, entries: list[ledger.Entry]) -> str:
    """A short code that ties the document to the records it was made from."""
    digest = hashlib.sha256()
    digest.update(wa_id.encode())
    digest.update(generated.isoformat().encode())
    for e in entries:
        digest.update(f"{e.kind}|{e.product_name}|{e.qty}|{e.amount}|{e.created_at}".encode())
    return digest.hexdigest()[:10].upper()


def masked_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"+{digits[:3]} *** *** {digits[-4:]}"


def build_pdf(
    *,
    trader_name: str | None,
    phone: str,
    entries: list[ledger.Entry],
    products: list[ledger.Product],
    generated: datetime | None = None,
) -> tuple[bytes, str]:
    """Return (pdf bytes, reference code)."""
    generated = generated or datetime.now(timezone.utc)
    ref = reference(phone, generated, entries)
    summary = ledger.summarize(entries, products, "all records")
    months = ledger.by_month(entries)
    days = ledger.active_days(entries)
    first = min((e.created_at for e in entries if e.created_at), default=None)
    last = max((e.created_at for e in entries if e.created_at), default=None)

    c = _Canvas()
    right = PAGE_W - MARGIN

    c.text(MARGIN, c.y, "AjaTrade Sales Record", 18, bold=True)
    c.y -= 22
    c.text(MARGIN, c.y, "Statement of sales and stock recorded on AjaTrade (WhatsApp)", 9.5)
    c.y -= 20
    info = [
        ("Trader", trader_name or "-"),
        ("WhatsApp number", masked_phone(phone)),
        ("Records from", f"{_date(first)} to {_date(last)}"),
        ("Days with records", str(days)),
        ("Generated", ledger.lagos_now(generated).strftime("%d %b %Y, %H:%M WAT")),
        ("Reference", ref),
    ]
    for key, value in info:
        c.text(MARGIN, c.y, key, 9.5, bold=True)
        c.text(MARGIN + 130, c.y, value, 9.5)
        c.y -= 14
    c.y -= 4
    c.line(MARGIN, c.y, right, c.y, 0.8)
    c.y -= 8

    _heading(c, "Summary")
    avg_daily = (summary.revenue / days) if days else Decimal(0)
    margin = summary.margin_percent
    totals = [
        ("Number of sales", f"{summary.sales_count:,}"),
        ("Total sales value", money.plain_naira(summary.revenue)),
        ("Cost of goods sold", money.plain_naira(summary.cost_of_goods)),
        ("Gross profit", money.plain_naira(summary.profit)),
        ("Profit margin", f"{margin}%" if margin is not None else "-"),
        ("Discounts given (below set price)", money.plain_naira(summary.discounts)),
        ("Sales made at a loss", f"{summary.loss_sales:,}"),
        ("Goods bought (restocks)", money.plain_naira(summary.restock_spend)),
        ("Average sales per active day", money.plain_naira(ledger.money(avg_daily))),
    ]
    for key, value in totals:
        c.text(MARGIN, c.y, key, 9.5)
        c.text(MARGIN + 330, c.y, value, 9.5, bold=True, align="right")
        c.y -= 14

    if months:
        _heading(c, "Month by month")
        cols = [
            Column("Month", MARGIN),
            Column("Sales", MARGIN + 150, "right"),
            Column("Sales value", MARGIN + 260, "right"),
            Column("Profit", MARGIN + 370, "right"),
            Column("Goods bought", right, "right"),
        ]
        rows = [
            [
                datetime.strptime(m.month, "%Y-%m").strftime("%B %Y"),
                f"{m.sales_count:,}",
                money.plain_naira(m.revenue),
                money.plain_naira(m.profit),
                money.plain_naira(m.restock_spend),
            ]
            for m in months
        ]
        _table(c, cols, rows)

    if products:
        _heading(c, "Current stock")
        cols = [
            Column("Item", MARGIN),
            Column("In stock", MARGIN + 220, "right"),
            Column("Cost price", MARGIN + 310, "right"),
            Column("Selling price", MARGIN + 410, "right"),
            Column("Stock value at cost", right, "right"),
        ]
        rows = []
        stock_value = Decimal(0)
        for p in products:
            value = ledger.money(p.stock_qty * p.cost_price)
            stock_value += value
            unit = f" {money.plural_unit(p.unit, p.stock_qty)}" if p.unit else ""
            rows.append([p.name, f"{money.qty(p.stock_qty)}{unit}", money.plain_naira(p.cost_price), money.plain_naira(p.set_price), money.plain_naira(value)])
        rows.append(["Total", "", "", "", money.plain_naira(stock_value)])
        _table(c, cols, rows)

    shown = entries[-MAX_TRANSACTIONS:]
    _heading(c, "Transactions" + (f" (latest {len(shown)} of {len(entries)})" if len(shown) < len(entries) else ""))
    cols = [
        Column("Date", MARGIN),
        Column("Type", MARGIN + 62),
        Column("Item", MARGIN + 112),
        Column("Qty", MARGIN + 290, "right"),
        Column("Price each", MARGIN + 365, "right"),
        Column("Amount", MARGIN + 445, "right"),
        Column("Profit", right, "right"),
    ]
    rows = []
    for e in shown:
        rows.append(
            [
                _date(e.created_at),
                "Sale" if e.kind == "sale" else "Restock",
                e.product_name[:32],
                money.qty(e.qty),
                money.plain_naira(e.unit_price),
                money.plain_naira(e.amount),
                money.plain_naira(e.profit) if e.kind == "sale" else "",
            ]
        )
    _table(c, cols, rows, size=8)

    note = [
        "About this statement: the trader entered these sales and stock records through AjaTrade,",
        "an AI assistant on WhatsApp. AjaTrade calculated the totals from those entries. The records",
        "have not been checked or audited by AjaTrade or anyone else. Powered by Awarri N-ATLaS.",
    ]
    c.ensure(len(note) * 12 + 10)
    c.y -= 4
    for line in note:
        c.text(MARGIN, c.y, line, 8)
        c.y -= 11

    return _assemble(c.pages, ref), ref


def _assemble(pages: list[list[str]], ref: str) -> bytes:
    """Write the PDF objects, cross-reference table and trailer."""
    total = len(pages)
    objects: list[bytes] = []

    def add(body: str | bytes) -> int:
        objects.append(body.encode("latin-1") if isinstance(body, str) else body)
        return len(objects)

    catalog = add("")  # filled in below
    pages_obj = add("")
    font1 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    font2 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    page_ids: list[int] = []
    for number, ops in enumerate(pages, start=1):
        footer = (
            f"BT /F1 7.5 Tf 1 0 0 1 {MARGIN} 22 Tm (AjaTrade sales record - reference {ref}) Tj ET\n"
            f"BT /F1 7.5 Tf 1 0 0 1 {PAGE_W - MARGIN - 50} 22 Tm (Page {number} of {total}) Tj ET"
        )
        stream = ("\n".join(ops) + "\n" + footer).encode("latin-1")
        content = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        page_ids.append(
            add(
                f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                f"/Resources << /Font << /F1 {font1} 0 R /F2 {font2} 0 R >> >> /Contents {content} 0 R >>"
            )
        )
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode()
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[pages_obj - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {total} >>".encode()

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R "
        f"/Info << /Title (AjaTrade sales record {ref}) /Producer (AjaTrade) >> >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def filename(trader_name: str | None, generated: datetime | None = None) -> str:
    generated = generated or datetime.now(timezone.utc)
    base = "".join(ch for ch in _latin(trader_name or "trader") if ch.isalnum() or ch in " -").strip().replace(" ", "-")
    return f"AjaTrade-sales-record-{base or 'trader'}-{ledger.lagos_now(generated):%Y-%m-%d}.pdf"
