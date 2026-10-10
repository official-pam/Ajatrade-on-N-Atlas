"""create messages table

Revision ID: 0003_create_messages
Revises: 0002_add_voice_fields
Create Date: 2026-10-08

Chat history (SPEC.md section 3). Rows are only written for users who granted
consent. Additive only: a new table, so existing users data is unaffected.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_create_messages"
down_revision: Union[str, None] = "0002_add_voice_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "wa_id",
            sa.Text(),
            sa.ForeignKey("users.wa_id"),
            nullable=False,
        ),
        sa.Column("wa_message_id", sa.Text(), nullable=True),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("msg_type", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("wa_message_id", name="uq_messages_wa_message_id"),
    )
    op.create_index("ix_messages_wa_id", "messages", ["wa_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_wa_id", table_name="messages")
    op.drop_table("messages")
