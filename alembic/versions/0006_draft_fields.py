"""draft subject, timestamps, edited flag

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("contacts", sa.Column("draft_subject", sa.String(200), nullable=True))
    op.add_column("contacts", sa.Column("draft_generated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("contacts", sa.Column("draft_approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("contacts", sa.Column("draft_edited", sa.Boolean(), server_default="false", nullable=False))


def downgrade() -> None:
    for column in ("draft_edited", "draft_approved_at", "draft_generated_at", "draft_subject"):
        op.drop_column("contacts", column)
