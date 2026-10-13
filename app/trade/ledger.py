"""Pure ledger maths for traders: sales, restocks, stock and summaries.

No database, no network, no language model. Every figure a trader sees comes
from these functions. Money is ``Decimal`` (never float) and rounded to
kobo; quantities are ``Decimal`` so half bags work.

Terms used in replies:
- cost price: what one unit cost the trader.
- set price: the selling price the trader planned for one unit.
- actual price: what the trader really sold one unit for.
- profit = (actual price - cost price) x quantity.
- planned profit = (set price - cost price) x quantity.
- price difference = (actual price - set price) x quantity; below zero is a
  discount, above zero a markup.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
KOBO = Decimal("0.01")
QTY_STEP = Decimal("0.001")

# Nigeria is UTC+1 all year (no daylight saving). A fixed offset avoids
# needing the tzdata package on Windows.
LAGOS = timezone(timedelta(hours=1), "WAT")

DEFAULT_LOW_STOCK = Decimal("3")


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(KOBO, rounding=ROUND_HALF_UP)


def quantity(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(QTY_STEP, rounding=ROUND_HALF_UP)


@dataclass
class Product:
    """One item in a trader's catalogue."""

    name: str
    unit: str | None
    cost_price: Decimal
    set_price: Decimal
    stock_qty: Decimal
    low_stock_level: Decimal = DEFAULT_LOW_STOCK
    id: int | None = None


@dataclass
class Entry:
    """One ledger line: a sale or a restock."""

    kind: str  # "sale" | "restock"
    product_name: str
    qty: Decimal
    unit_price: Decimal  # sale: actual selling price; restock: cost per unit
    unit_cost: Decimal  # cost price per unit at the time
    set_price: Decimal  # planned selling price at the time
    amount: Decimal  # money in (sale) or money out (restock)
    profit: Decimal = ZERO  # sale only
    price_difference: Decimal = ZERO  # sale only (actual - set) x qty
    stock_after: Decimal = ZERO
    unit: str | None = None
    product_id: int | None = None
    created_at: datetime | None = None
    source: str | None = None  # "voice" | "text"


@dataclass
class SaleResult:
    entry: Entry
    product: Product  # the product after the sale (stock reduced)
    planned_profit: Decimal
    status: str  # "profit" | "break_even" | "loss"
    price_vs_set: str  # "as_set" | "discount" | "markup"
    oversold: bool  # sold more than the recorded stock
    low_stock: bool
    out_of_stock: bool


@dataclass
class RestockResult:
    entry: Entry
    product: Product  # the product after the restock
    cost_changed: bool
    old_cost: Decimal
    new_product: bool


def record_sale(product: Product, qty: Decimal, unit_price: Decimal, *, source: str | None = None) -> SaleResult:
    """Work out one sale against the product's cost and set price."""
    qty = quantity(qty)
    unit_price = money(unit_price)
    if qty <= 0:
        raise ValueError("quantity must be more than zero")
    if unit_price < 0:
        raise ValueError("price cannot be negative")

    revenue = money(qty * unit_price)
    cost = money(qty * product.cost_price)
    profit = revenue - cost
    planned_profit = money(qty * (product.set_price - product.cost_price))
    difference = money(qty * (unit_price - product.set_price))

    oversold = qty > product.stock_qty
    stock_after = max(ZERO, quantity(product.stock_qty - qty))
    after = replace(product, stock_qty=stock_after)

    status = "profit" if profit > 0 else "loss" if profit < 0 else "break_even"
    price_vs_set = "discount" if unit_price < product.set_price else "markup" if unit_price > product.set_price else "as_set"

    entry = Entry(
        kind="sale",
        product_name=product.name,
        qty=qty,
        unit_price=unit_price,
        unit_cost=money(product.cost_price),
        set_price=money(product.set_price),
        amount=revenue,
        profit=profit,
        price_difference=difference,
        stock_after=stock_after,
        unit=product.unit,
        product_id=product.id,
        source=source,
    )
    return SaleResult(
        entry=entry,
        product=after,
        planned_profit=planned_profit,
        status=status,
        price_vs_set=price_vs_set,
        oversold=oversold,
        low_stock=ZERO < stock_after <= product.low_stock_level,
        out_of_stock=stock_after == 0,
    )


def record_restock(
    product: Product | None,
    *,
    name: str,
    unit: str | None,
    qty: Decimal,
    unit_cost: Decimal | None,
    set_price: Decimal | None,
    source: str | None = None,
) -> RestockResult:
    """Add stock. A new item needs its cost and set price.

    When the cost per unit changes, the cost price becomes the average of the
    old stock and the new stock (weighted by quantity), so profit on later
    sales stays honest.
    """
    qty = quantity(qty)
    if qty < 0:
        raise ValueError("quantity cannot be negative")
    new_product = product is None
    if product is None:
        if unit_cost is None or set_price is None:
            raise ValueError("a new item needs a cost price and a selling price")
        product = Product(name=name, unit=unit, cost_price=money(unit_cost), set_price=money(set_price), stock_qty=ZERO)
        old_cost = product.cost_price
        new_cost = product.cost_price
    else:
        old_cost = product.cost_price
        if unit_cost is None:
            new_cost = old_cost
        elif product.stock_qty > 0 and qty > 0:
            total = product.stock_qty * old_cost + qty * money(unit_cost)
            new_cost = money(total / (product.stock_qty + qty))
        else:
            new_cost = money(unit_cost)

    paid_each = money(unit_cost) if unit_cost is not None else old_cost
    after = replace(
        product,
        unit=product.unit or unit,
        cost_price=new_cost,
        set_price=money(set_price) if set_price is not None else product.set_price,
        stock_qty=quantity(product.stock_qty + qty),
    )
    entry = Entry(
        kind="restock",
        product_name=after.name,
        qty=qty,
        unit_price=paid_each,
        unit_cost=new_cost,
        set_price=after.set_price,
        amount=money(qty * paid_each),
        stock_after=after.stock_qty,
        unit=after.unit,
        product_id=after.id,
        source=source,
    )
    return RestockResult(
        entry=entry,
        product=after,
        cost_changed=(not new_product) and new_cost != old_cost,
        old_cost=old_cost,
        new_product=new_product,
    )


# ---------------------------------------------------------------------------
# Periods and summaries
# ---------------------------------------------------------------------------


def lagos_now(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(LAGOS)


def period_bounds(period: str, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    """Start and end (UTC) of "today", "week" (since Monday) or "month"."""
    local = lagos_now(now)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        start, label = day_start, "today"
    elif period == "week":
        start, label = day_start - timedelta(days=local.weekday()), "this week"
    elif period == "month":
        start, label = day_start.replace(day=1), "this month"
    else:
        raise ValueError(f"unknown period {period!r}")
    return start.astimezone(timezone.utc), local.astimezone(timezone.utc) + timedelta(seconds=1), label


@dataclass
class ProductLine:
    name: str
    qty: Decimal = ZERO
    revenue: Decimal = ZERO
    profit: Decimal = ZERO


@dataclass
class Summary:
    label: str
    sales_count: int = 0
    revenue: Decimal = ZERO
    cost_of_goods: Decimal = ZERO
    profit: Decimal = ZERO
    planned_profit: Decimal = ZERO
    discounts: Decimal = ZERO  # total given below set price (positive number)
    markups: Decimal = ZERO  # total above set price
    loss_sales: int = 0
    restock_spend: Decimal = ZERO
    top: list[ProductLine] = field(default_factory=list)
    low_stock: list[Product] = field(default_factory=list)
    out_of_stock: list[Product] = field(default_factory=list)

    @property
    def margin_percent(self) -> Decimal | None:
        if self.revenue <= 0:
            return None
        return (self.profit / self.revenue * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    @property
    def gap_to_plan(self) -> Decimal:
        """Profit minus planned profit (below zero: earned less than planned)."""
        return self.profit - self.planned_profit


def summarize(entries: list[Entry], products: list[Product], label: str, top_n: int = 3) -> Summary:
    """Totals for the entries of one period, plus current stock warnings."""
    summary = Summary(label=label)
    lines: dict[str, ProductLine] = defaultdict(lambda: ProductLine(name=""))
    for e in entries:
        if e.kind == "sale":
            summary.sales_count += 1
            summary.revenue += e.amount
            summary.cost_of_goods += money(e.qty * e.unit_cost)
            summary.profit += e.profit
            summary.planned_profit += money(e.qty * (e.set_price - e.unit_cost))
            if e.price_difference < 0:
                summary.discounts += -e.price_difference
            elif e.price_difference > 0:
                summary.markups += e.price_difference
            if e.profit < 0:
                summary.loss_sales += 1
            line = lines[e.product_name.lower()]
            line.name = line.name or e.product_name
            line.qty += e.qty
            line.revenue += e.amount
            line.profit += e.profit
        elif e.kind == "restock":
            summary.restock_spend += e.amount
    summary.top = sorted(lines.values(), key=lambda line: line.profit, reverse=True)[:top_n]
    summary.out_of_stock = [p for p in products if p.stock_qty <= 0]
    summary.low_stock = [p for p in products if ZERO < p.stock_qty <= p.low_stock_level]
    return summary


@dataclass
class MonthLine:
    month: str  # "2026-10"
    sales_count: int = 0
    revenue: Decimal = ZERO
    profit: Decimal = ZERO
    restock_spend: Decimal = ZERO


def by_month(entries: list[Entry]) -> list[MonthLine]:
    """Monthly totals (Lagos time), oldest first, for the lender statement."""
    months: dict[str, MonthLine] = {}
    for e in entries:
        when = lagos_now(e.created_at) if e.created_at else lagos_now()
        key = f"{when.year:04d}-{when.month:02d}"
        line = months.setdefault(key, MonthLine(month=key))
        if e.kind == "sale":
            line.sales_count += 1
            line.revenue += e.amount
            line.profit += e.profit
        elif e.kind == "restock":
            line.restock_spend += e.amount
    return [months[k] for k in sorted(months)]


def active_days(entries: list[Entry]) -> int:
    """Number of different days (Lagos time) with at least one entry."""
    days: set[date] = set()
    for e in entries:
        if e.created_at:
            days.add(lagos_now(e.created_at).date())
    return len(days)
