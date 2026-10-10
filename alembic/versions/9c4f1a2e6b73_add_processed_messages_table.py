"""add processed_messages table (webhook dedup)

Revision ID: 9c4f1a2e6b73
Revises: 07dab31de8b7
Create Date: 2026-10-09

Durable dedupe of inbound WhatsApp message ids: the primary key on
``message_id`` is what stops a Meta retry or a replayed backlog from running
the same message twice after a restart (SPEC.md section 6, dedupe rule).
Additive only: one new table plus the index the retention cleanup deletes by.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c4f1a2e6b73"
down_revision: Union[str, None] = "07dab31de8b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "processed_messages",
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("wa_id", sa.Text(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("message_id"),
    )
    op.create_index(
        "ix_processed_messages_received_at", "processed_messages", ["received_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_processed_messages_received_at", table_name="processed_messages")
    op.drop_table("processed_messages")
