"""eval fields: startup relevance judge, draft trace and eval

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("startups", sa.Column("relevance_judge", sa.Float(), nullable=True))
    op.add_column("startups", sa.Column("relevance_judge_reason", sa.Text(), nullable=True))
    op.add_column("contacts", sa.Column("draft_trace_id", sa.String(64), nullable=True))
    op.add_column("contacts", sa.Column("draft_eval", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("contacts", "draft_eval")
    op.drop_column("contacts", "draft_trace_id")
    op.drop_column("startups", "relevance_judge_reason")
    op.drop_column("startups", "relevance_judge")
