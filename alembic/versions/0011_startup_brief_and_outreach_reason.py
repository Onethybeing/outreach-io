"""startups.brief and contacts.outreach_reason: richer context for drafts

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-12

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # What the company does, its product, stage, recent news and the sources behind them — gathered
    # once during discovery so every draft for that company can be specific instead of generic.
    op.add_column("startups", sa.Column("brief", JSONB(none_as_null=True), nullable=True))
    op.add_column("startups", sa.Column("researched_at", sa.DateTime(timezone=True), nullable=True))
    # Why this particular person was worth contacting, carried over from the candidate.
    op.add_column("contacts", sa.Column("outreach_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("contacts", "outreach_reason")
    op.drop_column("startups", "researched_at")
    op.drop_column("startups", "brief")
