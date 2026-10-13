"""add trader ledger tables (trade_products, trade_entries)

Revision ID: d4b8e3a2f1c7
Revises: c3a7d2f1e9b0
Create Date: 2026-10-13 12:00:00

Additive only. Two new tables; no existing table or column changes.

- trade_products: a trader's catalogue (cost price, set selling price,
  units in stock). One row per item per trader.
- trade_entries: the ledger, one row per sale or restock, with the prices at
  that moment copied in so later price changes never rewrite old records.

Rows are written only for users who gave consent (the trade flow runs only
for them).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4b8e3a2f1c7"
down_revision: Union[str, None] = "c3a7d2f1e9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trade_products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("wa_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("cost_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("set_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("stock_qty", sa.Numeric(14, 3), server_default=sa.text("0"), nullable=False),
        sa.Column("low_stock_level", sa.Numeric(14, 3), server_default=sa.text("3"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["wa_id"], ["users.wa_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("wa_id", "name_key", name="uq_trade_products_wa_id_name_key"),
    )
    op.create_index("ix_trade_products_wa_id", "trade_products", ["wa_id"])

    op.create_table(
        "trade_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("wa_id", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("product_name", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("qty", sa.Numeric(14, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("unit_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("set_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("amount", sa.Numeric(16, 2), nullable=False),
        sa.Column("profit", sa.Numeric(16, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("price_difference", sa.Numeric(16, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("stock_after", sa.Numeric(14, 3), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["wa_id"], ["users.wa_id"]),
        sa.ForeignKeyConstraint(["product_id"], ["trade_products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trade_entries_wa_id_created_at", "trade_entries", ["wa_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_trade_entries_wa_id_created_at", table_name="trade_entries")
    op.drop_table("trade_entries")
    op.drop_index("ix_trade_products_wa_id", table_name="trade_products")
    op.drop_table("trade_products")
