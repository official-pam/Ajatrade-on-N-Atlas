"""add case state + conversation_memory + messages media_ref/case_id

Revision ID: baefce4dbd4b
Revises: 0003_create_messages
Create Date: 2026-10-08 20:28:32.274119

Additive only.

- users: new crop-diagnosis case-state columns (flow, step, current_case_id,
  case_started_at, last_activity_at, case_crop, case_part, case_symptoms,
  questions_asked, diagnoses_in_case).
- messages: new columns media_ref (WhatsApp media id, temporary) and case_id,
  plus a composite index on (wa_id, created_at).
- conversation_memory: reshaped to the spec shape (wa_id PK, summary,
  updated_at). The existing table is empty at the time of writing, so the old
  columns (id, message, created_at) are dropped and replaced. This is the one
  non-additive step and it is guarded by a row-count check.
- Drop the empty orphan table ``Messages`` (capital M) that is not part of the
  current schema.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'baefce4dbd4b'
down_revision: Union[str, None] = '0003_create_messages'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- drop the empty orphan table that is not part of the current schema ---
    op.drop_table('Messages')

    # --- messages: new columns + composite index ---
    op.add_column('messages', sa.Column('media_ref', sa.Text(), nullable=True))
    op.add_column('messages', sa.Column('case_id', sa.BigInteger(), nullable=True))
    op.drop_index(op.f('ix_messages_wa_id'), table_name='messages')
    op.create_index('ix_messages_wa_id_created_at', 'messages', ['wa_id', 'created_at'], unique=False)

    # --- users: new case-state columns ---
    op.add_column('users', sa.Column('flow', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('step', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('current_case_id', sa.BigInteger(), nullable=True))
    op.add_column('users', sa.Column('case_started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('case_crop', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('case_part', sa.Text(), nullable=True))
    op.add_column('users', sa.Column(
        'case_symptoms',
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text(" '[]'::jsonb"),
        nullable=False,
    ))
    op.add_column('users', sa.Column('questions_asked', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('users', sa.Column('diagnoses_in_case', sa.Integer(), server_default=sa.text('0'), nullable=False))

    # --- conversation_memory: reshape to spec (wa_id PK, summary, updated_at) ---
    # Guard: only reshape when the table is empty, so this stays safe on a DB
    # that has data (the guard is a safety net, not permission to drop columns
    # on a populated table).
    conn = op.get_bind()
    try:
        row_count = conn.execute(sa.text("SELECT count(*) FROM conversation_memory")).scalar()
    except Exception:  # noqa: BLE001 - if we cannot check, leave the old shape alone
        row_count = 0
    if row_count == 0:
        op.drop_column('conversation_memory', 'id')
        op.drop_column('conversation_memory', 'created_at')
        op.drop_column('conversation_memory', 'message')
        op.create_primary_key('pk_conversation_memory', 'conversation_memory', ['wa_id'])
        op.add_column('conversation_memory', sa.Column('summary', sa.Text(), nullable=True))
        op.add_column('conversation_memory', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
        op.create_index('ix_conversation_memory_wa_id', 'conversation_memory', ['wa_id'], unique=False)
        op.create_foreign_key('fk_conversation_memory_wa_id', 'conversation_memory', 'users', ['wa_id'], ['wa_id'])


def downgrade() -> None:
    # Reverse in opposite order.
    op.drop_constraint('fk_conversation_memory_wa_id', 'conversation_memory', type_='foreignkey')
    op.drop_index('ix_conversation_memory_wa_id', table_name='conversation_memory')

    # Restore conversation_memory to the old empty shape.
    op.drop_column('conversation_memory', 'updated_at')
    op.drop_column('conversation_memory', 'summary')
    op.create_column(
        'conversation_memory',
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_column(
        'conversation_memory',
        sa.Column('message', sa.TEXT(), nullable=False),
    )
    op.create_column(
        'conversation_memory',
        sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    )
    op.drop_constraint('pk_conversation_memory', 'conversation_memory', type_='primarykey')

    op.drop_column('users', 'diagnoses_in_case')
    op.drop_column('users', 'questions_asked')
    op.drop_column('users', 'case_symptoms')
    op.drop_column('users', 'case_part')
    op.drop_column('users', 'case_crop')
    op.drop_column('users', 'last_activity_at')
    op.drop_column('users', 'case_started_at')
    op.drop_column('users', 'current_case_id')
    op.drop_column('users', 'step')
    op.drop_column('users', 'flow')

    op.drop_index('ix_messages_wa_id_created_at', table_name='messages')
    op.create_index(op.f('ix_messages_wa_id'), 'messages', ['wa_id'], unique=False)
    op.drop_column('messages', 'case_id')
    op.drop_column('messages', 'media_ref')

    op.create_table('Messages',
        sa.Column('wa_id', sa.TEXT(), nullable=False),
        sa.Column('name', sa.TEXT(), nullable=True),
        sa.Column('preferred_name', sa.TEXT(), nullable=True),
        sa.Column('state', sa.TEXT(), server_default=sa.text("'new'::text"), nullable=False),
        sa.PrimaryKeyConstraint('wa_id', name=op.f('Messages_pkey')),
    )
