"""add voice language fields to users

Revision ID: 0002_add_voice_fields
Revises: 0001_create_users
Create Date: 2026-10-07

The voice onboarding flow detects the user's language instead of asking for it,
so it records where the language came from and how many low-confidence attempts
it took. Additive only: both columns are new, so the text flow is unaffected.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_add_voice_fields"
down_revision: Union[str, None] = "0001_create_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("language_source", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "language_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "language_attempts")
    op.drop_column("users", "language_source")
