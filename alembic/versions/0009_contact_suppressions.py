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

    # Contacts already flagged do-not-contact need a row too, or a later run would re-propose them.
    from app.suppression import fingerprint

    connection = op.get_bind()
    flagged = connection.execute(sa.text("SELECT linkedin_url FROM contacts WHERE do_not_contact")).fetchall()
    for (linkedin_url,) in flagged:
        connection.execute(
            sa.text(
                "INSERT INTO contact_suppressions (id, linkedin_hash, reason, created_at)"
                " VALUES (gen_random_uuid(), :digest, 'do_not_contact', now())"
                " ON CONFLICT (linkedin_hash) DO NOTHING"
            ),
            {"digest": fingerprint(linkedin_url)},
        )


def downgrade() -> None:
    op.drop_table("contact_suppressions")
