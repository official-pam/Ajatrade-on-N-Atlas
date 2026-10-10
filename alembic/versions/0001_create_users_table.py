"""create users table

Revision ID: 0001_create_users
Revises:
Create Date: 2026-10-05

Only the users table is in scope for now. messages, conversation_memory,
vendor_requests, the consent flow and retention are later items.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_create_users"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("wa_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("preferred_name", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="new"),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column(
            "consent_status", sa.Text(), nullable=False, server_default="pending"
        ),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("users")
