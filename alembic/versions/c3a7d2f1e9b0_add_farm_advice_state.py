"""add farm-advice state to users (flow_data, farm_profile)

Revision ID: c3a7d2f1e9b0
Revises: 9c4f1a2e6b73
Create Date: 2026-10-10 15:00:00

Additive only.

- users.flow_data: answers collected in the open farm-advice flow
  (irrigation, soil, fertilizer, water source, weeds, varieties,
  germination); cleared when the flow ends.
- users.farm_profile: farm facts kept between flows so the farmer is not
  asked twice (crop, farm size, place, slope, soil ratings). Written only for
  consented users.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3a7d2f1e9b0"
down_revision: Union[str, None] = "9c4f1a2e6b73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "flow_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text(" '{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "farm_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text(" '{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "farm_profile")
    op.drop_column("users", "flow_data")
