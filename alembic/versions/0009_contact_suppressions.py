"""contact suppressions: never contact this person again

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-12

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Outlives the contact row: erasing someone must not lose the fact that they asked not to be
    # emailed, so only a hash of the LinkedIn URL is kept — enough to match, not to identify.
    op.create_table(
        "contact_suppressions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("linkedin_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("contact_suppressions")
